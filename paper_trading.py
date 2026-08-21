"""모의투자 탭 - "미너비니 v2"/"v2.1" 전략을 실제 돈 없이 매일 자동으로 그대로
따라가며 시뮬레이션한다. screening_backtest.py의 run_risk_managed_backtest와
정확히 같은 매매 규칙을 쓰지만, 저건 "과거 구간을 워크포워드로 재생"하는
배치 계산이고 이 모듈은 "오늘 실제로 확정된 가격을 하루치씩 실시간으로
누적 반영"한다는 점이 다르다 - 그래서 별도 모듈로 분리했다(백테스트 코드와
결과가 갈라지지 않도록 파라미터는 STRATEGY_PRESETS를 통해 screening_backtest.py의
MINERVINI_V2_PARAMS/MINERVINI_V21_PARAMS를 그대로 공유한다).

## 하루 처리 흐름 (run_daily_step)
1. 보유 포지션 + 신규 진입 후보(트렌드템플릿 통과 + 유동성, 이미 계산되어
   있는 TrendScreenCache를 그대로 읽는다 - 재계산하지 않음)의 최근 며칠치
   OHLC를 받는다.
2. 마지막 처리일(last_processed_date) 다음 날부터 오늘까지, 보유 포지션마다
   하루씩 순서대로 반영한다(스레드가 며칠 쉬었다 깨어나도 밀린 날짜를 전부
   따라잡는다) - 손절선 히트 → 시간손절 → 본전/트레일링 갱신 순으로 확인하는
   건 백테스트 엔진과 동일한 로직이다.
3. 그 시점 평가액으로 계좌 낙폭을 갱신한다(완전 청산 상태면 고점을 리셋 -
   screening_backtest.py에서 발견한 "영구 정지" 버그와 같은 이유).
4. 마지막 재평가일로부터 7일 이상 지났으면(주 단위 재평가), 빈 슬롯을
   RS 등급 높은 순으로 채운다(리스크 기반 사이징 - ATR은 방금 받은 최근
   OHLC로 그 종목 진입 시점 기준 새로 계산).
"""
from datetime import datetime, timedelta

import yfinance as yf

import screening_backtest as sb
from kr_quant import _load_market_map, _yf_ticker
from models import PaperPosition, PaperStrategyAccount, PaperTrade, TrendScreenCache, db

STRATEGY_PRESETS = {"minervini_v2": sb.MINERVINI_V2_PARAMS, "minervini_v21": sb.MINERVINI_V21_PARAMS}
RESCAN_INTERVAL_DAYS = 7
FETCH_LOOKBACK_DAYS = 40  # last_processed_date 이후 밀린 날짜 + ATR(14) 계산에 충분한 여유


def _bars_from_df(sub):
    sub = sub.dropna(subset=["Close", "High", "Low"])
    has_volume = "Volume" in sub.columns
    bars = []
    for idx, row in sub.iterrows():
        bars.append({
            "date": idx.strftime("%Y-%m-%d"), "close": float(row["Close"]),
            "high": float(row["High"]), "low": float(row["Low"]),
            "volume": float(row["Volume"]) if has_volume and row["Volume"] == row["Volume"] else None,
        })
    return bars


def fetch_recent_bars(codes, market, lookback_days=FETCH_LOOKBACK_DAYS):
    """보유 종목 + 신규 진입 후보의 최근 OHLC를 받는다. 대상이 많아야 수십 개
    수준이라(전종목 재조회가 아님) 요청 경로가 아닌 백그라운드 스레드에서만
    호출해도 충분히 빠르다."""
    if not codes:
        return {}
    market_map = _load_market_map() if market == "KR" else {}
    tickers = [_yf_ticker(c, market_map) if market == "KR" else c for c in codes]
    code_by_ticker = dict(zip(tickers, codes))
    end = datetime.today()
    start = end - timedelta(days=lookback_days)
    start_str, end_str = start.strftime("%Y-%m-%d"), (end + timedelta(days=1)).strftime("%Y-%m-%d")

    bars_by_code = {}
    CHUNK = 25
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        df = None
        for attempt in range(3):
            try:
                df = yf.download(
                    chunk, start=start_str, end=end_str,
                    progress=False, auto_adjust=False, timeout=25, group_by="ticker", threads=False,
                )
                if df is not None and not df.empty:
                    break
            except Exception:
                df = None
        if df is None or df.empty:
            continue
        if len(chunk) == 1:
            bars_by_code[code_by_ticker[chunk[0]]] = _bars_from_df(df)
            continue
        for tkr in chunk:
            try:
                sub = df[tkr]
            except KeyError:
                continue
            bars_by_code[code_by_ticker[tkr]] = _bars_from_df(sub)
    return bars_by_code


def _process_position_day(pos, bar, preset):
    """포지션 하루치 봉을 반영한다. 청산되면 (exit_price, exit_date, reason)을
    돌려주고, 아니면 None을 돌려주며 pos의 상태(stop_price/stop_state/
    highest_high/bars_held)를 그 자리에서 갱신한다. 손절가 히트는 실제 저가가
    아니라 손절가 그 자체에 체결된 것으로 본다(screening_backtest.py와 같은
    단순화 - 시가 데이터가 없어 갭하락까지는 반영 못 함)."""
    if bar["low"] <= pos.stop_price:
        return pos.stop_price, bar["date"], pos.stop_state

    pos.bars_held += 1
    if pos.bars_held >= preset["time_stop_days"] and bar["close"] <= pos.entry_price:
        return bar["close"], bar["date"], "timeStop"

    pos.highest_high = max(pos.highest_high, bar["high"])
    r_reached = (pos.highest_high - pos.entry_price) / pos.risk_per_share if pos.risk_per_share > 0 else 0
    if r_reached >= preset["trail_start_r"]:
        new_stop = pos.highest_high - preset["atr_mult"] * pos.entry_atr
        if new_stop > pos.stop_price:
            pos.stop_price = new_stop
            pos.stop_state = "trailingStop"
    elif r_reached >= preset["breakeven_r"]:
        locked_stop = pos.entry_price + preset.get("breakeven_lock_r", 0.0) * pos.risk_per_share
        if locked_stop > pos.stop_price:
            pos.stop_price = locked_stop
            pos.stop_state = "breakevenStop"
    return None


def _regime_ok(preset):
    """market_regime_filter가 꺼진 프리셋(v2 등)은 항상 True. 켜진 프리셋(v2.1)은
    지수(코스피)가 자기 200일선 위에 있을 때만 신규 매수를 허용한다 -
    screening_backtest.py의 레짐필터와 같은 조건. 지수 데이터를 못 받으면
    보수적으로 매수를 쉰다(신규 매수를 잘못 여는 것보다 안전한 쪽)."""
    if not preset.get("market_regime_filter"):
        return True
    ticker = "^KS11" if preset["market"] == "KR" else "^GSPC"
    end = datetime.today()
    start = end - timedelta(days=320)  # 200일선 계산에 필요한 여유
    try:
        df = yf.download(
            ticker, start=start.strftime("%Y-%m-%d"), end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            progress=False, timeout=20,
        )
    except Exception:
        return False
    if df is None or df.empty:
        return False
    if hasattr(df.columns, "get_level_values") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    closes = df["Close"].dropna()
    if len(closes) < 200:
        return False
    ma200 = float(closes.iloc[-200:].mean())
    return float(closes.iloc[-1]) >= ma200


def run_daily_step(account):
    """계좌 하나를 최신 거래일까지 진행시킨다. 이미 최신 거래일까지 처리된
    상태면 아무 것도 하지 않고 조용히 반환한다(하루에 여러 번 깨어나는
    리프레셔가 중복 처리하지 않도록)."""
    preset = STRATEGY_PRESETS.get(account.strategy)
    if not preset:
        return

    held_positions = list(account.positions)
    held_codes = [p.code for p in held_positions]

    candidate_rows = []
    if len(held_codes) < preset["max_positions"]:
        candidate_rows = (
            TrendScreenCache.query.filter_by(market=preset["market"], all_pass=True)
            .filter(TrendScreenCache.avg_trade_value.isnot(None))
            .filter(TrendScreenCache.avg_trade_value >= preset["min_avg_trade_value"])
            .order_by(TrendScreenCache.rs_rating.desc())
            .limit(preset["max_positions"] * 3)  # 보유종목 제외 후에도 슬롯을 채우기 충분한 여유
            .all()
        )
    candidate_codes = [r.code for r in candidate_rows if r.code not in held_codes]

    fetch_codes = list(dict.fromkeys(held_codes + candidate_codes))
    bars_by_code = fetch_recent_bars(fetch_codes, preset["market"])
    if not bars_by_code:
        return

    latest_date = max((bars[-1]["date"] for bars in bars_by_code.values() if bars), default=None)
    if latest_date is None or (account.last_processed_date and latest_date <= account.last_processed_date):
        return

    # 1) 보유 포지션 - 마지막 처리일 다음 날부터 오늘까지 하루씩 순서대로 반영
    for pos in held_positions:
        bars = bars_by_code.get(pos.code)
        if not bars:
            continue
        for bar in bars:
            if account.last_processed_date and bar["date"] <= account.last_processed_date:
                continue
            if bar["date"] > latest_date:
                break
            result = _process_position_day(pos, bar, preset)
            if result:
                exit_price, exit_date, reason = result
                pnl_pct = round((exit_price - pos.entry_price) / pos.entry_price * 100, 2)
                account.cash += pos.shares * exit_price
                db.session.add(PaperTrade(
                    account_id=account.id, code=pos.code, name=pos.name,
                    entry_date=pos.entry_date, entry_price=round(pos.entry_price, 2),
                    exit_date=exit_date, exit_price=round(exit_price, 2), shares=pos.shares,
                    pnl_pct=pnl_pct, exit_reason=reason,
                    hold_days=(datetime.fromisoformat(exit_date) - datetime.fromisoformat(pos.entry_date)).days,
                ))
                db.session.delete(pos)
                break  # 이 포지션은 청산됐으니 이후 날짜는 더 볼 필요 없음

    db.session.flush()

    # 2) 이 시점 평가액으로 낙폭 판정 (완전 청산 상태면 고점 리셋 -
    #    screening_backtest.py의 영구정지 버그 수정과 같은 이유)
    remaining = PaperPosition.query.filter_by(account_id=account.id).all()
    held_value = 0.0
    for pos in remaining:
        bars = bars_by_code.get(pos.code)
        price = bars[-1]["close"] if bars else pos.entry_price
        held_value += pos.shares * price
    equity_now = account.cash + held_value
    if not remaining:
        account.peak_equity = equity_now
    else:
        account.peak_equity = max(account.peak_equity, equity_now)
    drawdown_pct = (account.peak_equity - equity_now) / account.peak_equity * 100 if account.peak_equity > 0 else 0.0
    dd_halt = drawdown_pct >= preset["dd_halt_pct"]

    # 3) 주 단위 재평가 - 빈 슬롯을 RS 등급 높은 순으로 채운다
    should_rescan = account.last_rescan_date is None or (
        datetime.fromisoformat(latest_date) - datetime.fromisoformat(account.last_rescan_date)
    ).days >= RESCAN_INTERVAL_DAYS
    if should_rescan and not dd_halt and _regime_ok(preset):
        open_slots = preset["max_positions"] - len(remaining)
        if open_slots > 0:
            max_position_value = account.seed / preset["max_positions"]
            filled = 0
            for row in candidate_rows:
                if filled >= open_slots:
                    break
                if row.code in held_codes:
                    continue
                bars = bars_by_code.get(row.code)
                if not bars or len(bars) < preset["atr_period"] + 1:
                    continue
                closes = [b["close"] for b in bars]
                highs = [b["high"] for b in bars]
                lows = [b["low"] for b in bars]
                i = len(bars) - 1
                atr = sb._atr(highs, lows, closes, i, preset["atr_period"])
                price = closes[i]
                if not atr or atr <= 0 or not price or price <= 0:
                    continue
                stop_price = price - preset["atr_mult"] * atr
                risk_per_share = price - stop_price
                if risk_per_share <= 0:
                    continue
                risk_amount = equity_now * preset["risk_pct"] / 100
                shares = int(risk_amount // risk_per_share)
                cap_shares = int(min(account.cash, max_position_value) // price)
                shares = min(shares, cap_shares)
                if shares <= 0:
                    continue
                position_value = shares * price
                account.cash -= position_value
                db.session.add(PaperPosition(
                    account_id=account.id, code=row.code, name=row.name,
                    entry_date=latest_date, entry_price=price, shares=shares,
                    entry_atr=atr, risk_per_share=risk_per_share,
                    stop_price=stop_price, stop_state="initialStop", highest_high=price, bars_held=0,
                ))
                filled += 1
        account.last_rescan_date = latest_date

    account.last_processed_date = latest_date
    db.session.commit()


def run_all_accounts():
    """활성 계좌 전부를 최신 거래일까지 진행시킨다. 계좌 하나가 실패해도
    (예: 그 계좌가 들고 있는 특정 종목의 야후 조회 오류) 다른 계좌 처리에
    영향을 주지 않도록 계좌 단위로 예외를 격리한다."""
    accounts = PaperStrategyAccount.query.filter_by(is_active=True).all()
    for account in accounts:
        try:
            run_daily_step(account)
        except Exception as e:
            db.session.rollback()
            print(f"[모의투자] 계좌 {account.id}({account.strategy}) 처리 오류: {e}")
