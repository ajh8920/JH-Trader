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
import vcp_strategy as vcp
from kr_quant import _load_market_map, _yf_ticker
from models import PaperPosition, PaperStrategyAccount, PaperTrade, TrendScreenCache, db

STRATEGY_PRESETS = {"minervini_v2": sb.MINERVINI_V2_PARAMS, "minervini_v21": sb.MINERVINI_V21_PARAMS}
RESCAN_INTERVAL_DAYS = 7
FETCH_LOOKBACK_DAYS = 40  # last_processed_date 이후 밀린 날짜 + ATR(14) 계산에 충분한 여유

# "어나니머스"(vcp_strategy.ANONYMOUS_PARAMS) 전용 상수. 이 전략은 트렌드템플릿
# 단일 손절 모델(위 STRATEGY_PRESETS 계열)과 포지션 구조가 완전히 달라서(피라미딩,
# 챈들리어 트레일링, 250일선 이탈, 현금 유휴화 방지) 아래에 별도 경로로 구현한다.
ANON_HELD_LOOKBACK_DAYS = 400  # 보유 종목(최대 10개)만 받으므로 250일선 계산에 필요한
# 긴 구간을 받아도 비용이 적다 - 전체 유니버스를 매일 다시 훑는 게 아니기 때문.
ANON_CANDIDATE_ATR_LOOKBACK_DAYS = 40  # 신규 후보의 ATR(20) 계산용(기존 FETCH_LOOKBACK_DAYS와 같은 목적)
ANON_INDEX_SLIPPAGE_PCT = 0.1  # vcp_strategy.run_vcp_backtest의 index_slippage_pct 기본값과 동일


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
            # group_by="ticker"는 종목 1개짜리 리스트를 넘겨도 MultiIndex 컬럼
            # (티커, 필드)을 그대로 돌려준다 - df를 바로 안 쓰고 다른 분기처럼
            # df[티커]로 한 번 골라내야 한다(안 그러면 "Close" 등 컬럼명이
            # (티커,"Close") 튜플이라 dropna(subset=["Close",...])가 KeyError).
            try:
                sub = df[chunk[0]]
            except KeyError:
                continue
            bars_by_code[code_by_ticker[chunk[0]]] = _bars_from_df(sub)
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


def get_watchlist(account, limit=15):
    """"슬롯이 꽉 차 대기 중인 종목" 조회용 - run_daily_step/run_anonymous_daily_step의
    후보 조회 조건(트렌드템플릿+유동성 또는 돈치안+유동성, RS 랭킹 순)과 동일하게
    계산하되, 슬롯 여유 여부와 무관하게 항상 계산하고 실제 매매에는 관여하지 않는다
    (조회 전용 - 실제 진입 여부는 여전히 다음 재평가일에 run_daily_step이 결정한다)."""
    held_codes = {p.code for p in account.positions}
    if account.strategy in ("anonymous", "sweeper"):
        params = vcp.ANONYMOUS_PARAMS if account.strategy == "anonymous" else vcp.SWEEPER_PARAMS
        query = (
            TrendScreenCache.query.filter_by(market=params["market"])
            .filter(TrendScreenCache.donchian_high_15.isnot(None))
            .filter(TrendScreenCache.price > TrendScreenCache.donchian_high_15)
        )
        min_avg_trade_value = params.get("min_avg_trade_value", vcp.MIN_AVG_TRADE_VALUE)
        if min_avg_trade_value:
            query = query.filter(TrendScreenCache.avg_trade_value.isnot(None)) \
                .filter(TrendScreenCache.avg_trade_value >= min_avg_trade_value)
        min_market_cap = params.get("min_market_cap", vcp.MIN_MARKET_CAP)
        if min_market_cap:
            query = query.filter(TrendScreenCache.market_cap.isnot(None)) \
                .filter(TrendScreenCache.market_cap >= min_market_cap)
        rows = query.order_by(TrendScreenCache.rs_rating.desc()).limit((limit + len(held_codes)) * 3).all()
        rows = [r for r in rows if not vcp.is_preferred_stock(r.name)]
    else:
        preset = STRATEGY_PRESETS.get(account.strategy)
        if not preset:
            return []
        rows = (
            TrendScreenCache.query.filter_by(market=preset["market"], all_pass=True)
            .filter(TrendScreenCache.avg_trade_value.isnot(None))
            .filter(TrendScreenCache.avg_trade_value >= preset["min_avg_trade_value"])
            .order_by(TrendScreenCache.rs_rating.desc())
            .limit((limit + len(held_codes)) * 3)
            .all()
        )
    rows = [r for r in rows if r.code not in held_codes][:limit]
    return [
        {"code": r.code, "name": r.name, "price": r.price, "rsRating": r.rs_rating,
         "avgTradeValue": r.avg_trade_value}
        for r in rows
    ]


def run_daily_step(account):
    """계좌 하나를 최신 거래일까지 진행시킨다. 이미 최신 거래일까지 처리된
    상태면 아무 것도 하지 않고 조용히 반환한다(하루에 여러 번 깨어나는
    리프레셔가 중복 처리하지 않도록)."""
    if account.strategy in ("anonymous", "sweeper"):
        return run_anonymous_daily_step(account)

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


# ─── "어나니머스"/"스위퍼"(vcp_strategy.ANONYMOUS_PARAMS/SWEEPER_PARAMS) 공용 실시간 진행 로직 ──
# 트렌드템플릿 단일손절 모델(위)과 포지션 구조가 완전히 달라(피라미딩·챈들리어
# 트레일링·250일선 이탈·현금 유휴화 방지) 별도 함수로 분리했다. vcp_strategy.
# run_vcp_backtest와 최대한 같은 계산식을 쓰되, "과거 구간 워크포워드 재생"이
# 아니라 "오늘 실제로 확정된 가격을 하루치씩 누적 반영"한다는 점만 다르다.


def _anon_market_index_and_regime():
    """코스피 종가와 국면 판정(가격>200일선 and 200일선 20일 기울기>0)을 함께
    돌려준다 - vcp_strategy.run_vcp_backtest의 regime_ok와 동일 조건. 신규진입
    게이팅과 현금 유휴화 방지(지수 대납) 양쪽에 공통으로 쓴다. 지수 데이터를
    못 받으면 (None, False)로 보수적으로 처리한다."""
    end = datetime.today()
    start = end - timedelta(days=340)
    try:
        df = yf.download(
            "^KS11", start=start.strftime("%Y-%m-%d"), end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            progress=False, timeout=20,
        )
    except Exception:
        return None, False
    if df is None or df.empty:
        return None, False
    if hasattr(df.columns, "get_level_values") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    closes = df["Close"].dropna()
    if len(closes) < 220:
        return None, False
    price = float(closes.iloc[-1])
    ma200 = float(closes.iloc[-200:].mean())
    ma200_20d_ago = float(closes.iloc[-220:-20].mean())
    regime_ok = price >= ma200 and ma200 > ma200_20d_ago
    return price, regime_ok


def _process_anon_position_day(pos, closes, highs, lows, j, params):
    """"어나니머스" 포지션 하루치(인덱스 j)를 반영한다 - vcp_strategy.
    run_vcp_backtest의 포지션 관리 블록과 동일한 규칙(손절 > MA이탈 > 시간손절 >
    트레일링 갱신 순). 청산되면 (청산가, 사유)를, 아니면 None을 돌려주며 pos를
    그 자리에서 갱신한다."""
    low, high, close = lows[j], highs[j], closes[j]
    if low <= pos["stopPrice"]:
        return pos["stopPrice"], pos["stopState"]

    pos["barsHeld"] += 1
    ma = vcp._sma(closes, params["ma_break_period"], j)
    if ma is not None and close < ma:
        pos["maBelowCount"] += 1
    else:
        pos["maBelowCount"] = 0
    if pos["maBelowCount"] >= params["ma_break_consec_days"]:
        return close, "maBreak"

    r = pos["riskPerShare"]
    highest_r = (pos["highestHigh"] - pos["avgEntryPrice"]) / r if r > 0 else 0
    if pos["barsHeld"] >= params["time_stop_days"] and highest_r < params["time_stop_progress_r"]:
        return close, "timeStop"

    # max_hold_days: 추세와 무관하게 이 날짜수를 넘기면 강제 청산(슬롯 회전율 확보용)
    max_hold_days = params.get("max_hold_days")
    if max_hold_days is not None and pos["barsHeld"] >= max_hold_days:
        return close, "maxHold"

    pos["highestHigh"] = max(pos["highestHigh"], high)
    r_reached = (pos["highestHigh"] - pos["avgEntryPrice"]) / r if r > 0 else 0

    if r_reached >= params["breakeven_r"] and pos["stopPrice"] < pos["avgEntryPrice"]:
        pos["stopPrice"] = pos["avgEntryPrice"]
        pos["stopState"] = "breakevenStop"

    if r_reached >= params["trail_activate_r"]:
        chandelier = pos["highestHigh"] - params["chandelier_atr_mult"] * pos["entryAtr"]
        if chandelier > pos["stopPrice"]:
            pos["stopPrice"] = chandelier
            pos["stopState"] = "trailingStop"
    return None


def run_anonymous_daily_step(account):
    """"어나니머스"/"스위퍼" 계좌를 최신 거래일까지 진행시킨다(둘 다 vcp_strategy.
    run_vcp_backtest의 돈치안 브레이크아웃+포지션 관리 규칙을 그대로 쓰되 파라미터
    딕셔너리만 다르다 - 어나니머스는 균등배분+고정상한, 스위퍼는 리스크기반
    사이징+타이트 트레일링). run_daily_step과 같은 "이미 처리된 상태면 조용히
    반환" 규칙을 따른다."""
    params = vcp.ANONYMOUS_PARAMS if account.strategy == "anonymous" else vcp.SWEEPER_PARAMS
    held_positions = list(account.positions)
    held_codes = [p.code for p in held_positions]

    candidate_rows = []
    if len(held_codes) < params["max_positions"]:
        query = (
            TrendScreenCache.query.filter_by(market=params["market"])
            .filter(TrendScreenCache.donchian_high_15.isnot(None))
            .filter(TrendScreenCache.price > TrendScreenCache.donchian_high_15)
        )
        min_avg_trade_value = params.get("min_avg_trade_value", vcp.MIN_AVG_TRADE_VALUE)
        if min_avg_trade_value:
            query = query.filter(TrendScreenCache.avg_trade_value.isnot(None)) \
                .filter(TrendScreenCache.avg_trade_value >= min_avg_trade_value)
        min_market_cap = params.get("min_market_cap", vcp.MIN_MARKET_CAP)
        if min_market_cap:
            query = query.filter(TrendScreenCache.market_cap.isnot(None)) \
                .filter(TrendScreenCache.market_cap >= min_market_cap)
        candidate_rows = query.order_by(TrendScreenCache.rs_rating.desc()) \
            .limit(params["max_positions"] * 5).all()
    candidate_rows = [r for r in candidate_rows if not vcp.is_preferred_stock(r.name)]
    candidate_codes = [r.code for r in candidate_rows if r.code not in held_codes]

    held_bars = fetch_recent_bars(held_codes, params["market"], lookback_days=ANON_HELD_LOOKBACK_DAYS)
    candidate_bars = fetch_recent_bars(
        candidate_codes, params["market"], lookback_days=ANON_CANDIDATE_ATR_LOOKBACK_DAYS)

    latest_date = max(
        [bars[-1]["date"] for bars in held_bars.values() if bars]
        + [bars[-1]["date"] for bars in candidate_bars.values() if bars], default=None,
    )
    if latest_date is None or (account.last_processed_date and latest_date <= account.last_processed_date):
        return

    # 1) 보유 포지션 - 마지막 처리일 다음 날부터 오늘까지 하루씩 순서대로 반영
    for pos_row in held_positions:
        bars = held_bars.get(pos_row.code)
        if not bars:
            continue
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        dates = [b["date"] for b in bars]
        pos = {
            "avgEntryPrice": pos_row.entry_price, "shares": pos_row.shares, "entryAtr": pos_row.entry_atr,
            "riskPerShare": pos_row.risk_per_share, "stopPrice": pos_row.stop_price,
            "stopState": pos_row.stop_state, "highestHigh": pos_row.highest_high, "barsHeld": pos_row.bars_held,
            "maBelowCount": 0, "totalCost": pos_row.total_cost or (pos_row.entry_price * pos_row.shares),
            "lastEntryPrice": pos_row.last_entry_price or pos_row.entry_price, "pyramidCount": pos_row.pyramid_count,
            "initialShares": pos_row.initial_shares or pos_row.shares, "partialTaken": pos_row.partial_taken,
        }
        closed = False
        last_pyramid_date, last_pyramid_shares = None, None
        for j, d in enumerate(dates):
            if account.last_processed_date and d <= account.last_processed_date:
                continue
            if d > latest_date:
                break
            result = _process_anon_position_day(pos, closes, highs, lows, j, params)
            if result:
                exit_price, reason = result
                proceeds = pos["shares"] * exit_price * (1 - (vcp.SLIPPAGE_EXIT_PCT + vcp.SELL_TAX_PCT) / 100)
                pnl_pct = round((proceeds / pos["shares"] - pos["avgEntryPrice"]) / pos["avgEntryPrice"] * 100, 2)
                account.cash += proceeds
                db.session.add(PaperTrade(
                    account_id=account.id, code=pos_row.code, name=pos_row.name,
                    entry_date=pos_row.entry_date, entry_price=round(pos["avgEntryPrice"], 2),
                    exit_date=d, exit_price=round(exit_price, 2), shares=pos["shares"], pnl_pct=pnl_pct,
                    exit_reason=reason,
                    hold_days=(datetime.fromisoformat(d) - datetime.fromisoformat(pos_row.entry_date)).days,
                ))
                db.session.delete(pos_row)
                closed = True
                break
            # 분할익절 - vcp_strategy.run_vcp_backtest의 분할익절 블록과 동일 규칙(+2R에
            # partial_profit_fraction만큼 매도, 포지션은 남은 수량으로 계속 유지). 어나니머스는
            # partial_profit_fraction=0이라 조건이 항상 거짓이 되어 기존 동작에 영향이 없다.
            partial_profit_fraction = params.get("partial_profit_fraction", 0.0)
            if partial_profit_fraction > 0 and not pos["partialTaken"]:
                partial_profit_r = params.get("partial_profit_r", vcp.PARTIAL_PROFIT_R)
                r = pos["riskPerShare"]
                r_reached = (pos["highestHigh"] - pos["avgEntryPrice"]) / r if r > 0 else 0
                if r_reached >= partial_profit_r:
                    target = pos["avgEntryPrice"] + partial_profit_r * r
                    if highs[j] >= target:
                        sell_shares = min(max(1, int(pos["shares"] * partial_profit_fraction)), pos["shares"] - 1)
                        if sell_shares > 0:
                            fill = target * (1 - (vcp.SLIPPAGE_EXIT_PCT + vcp.SELL_TAX_PCT) / 100)
                            pnl_pct = round((fill - pos["avgEntryPrice"]) / pos["avgEntryPrice"] * 100, 2)
                            account.cash += sell_shares * fill
                            db.session.add(PaperTrade(
                                account_id=account.id, code=pos_row.code, name=pos_row.name,
                                entry_date=pos_row.entry_date, entry_price=round(pos["avgEntryPrice"], 2),
                                exit_date=d, exit_price=round(target, 2), shares=sell_shares, pnl_pct=pnl_pct,
                                exit_reason="partialProfit",
                                hold_days=(datetime.fromisoformat(d) - datetime.fromisoformat(pos_row.entry_date)).days,
                            ))
                            pos["totalCost"] -= sell_shares * pos["avgEntryPrice"]
                            pos["shares"] -= sell_shares
                        pos["partialTaken"] = True
            # 피라미딩 - vcp_strategy.run_vcp_backtest의 신규 진입 후 피라미딩 블록과 동일 규칙
            if pos["pyramidCount"] < params["pyramid_max_count"]:
                target = pos["lastEntryPrice"] + vcp.PYRAMID_INTERVAL_R * pos["riskPerShare"]
                if highs[j] >= target and lows[j] <= target:
                    add_shares = max(1, int(pos["initialShares"] * vcp.PYRAMID_SIZE_FRACTION))
                    cost = add_shares * target * (1 + vcp.SLIPPAGE_ENTRY_PCT / 100)
                    max_pos_value = account.seed * vcp.MAX_POSITION_WEIGHT_PCT / 100
                    current_value = pos["shares"] * target
                    if cost <= account.cash and (current_value + add_shares * target) <= max_pos_value:
                        account.cash -= cost
                        pos["totalCost"] += cost
                        pos["shares"] += add_shares
                        pos["avgEntryPrice"] = pos["totalCost"] / pos["shares"]
                        pos["lastEntryPrice"] = target
                        pos["pyramidCount"] += 1
                        pos["stopPrice"] = max(pos["stopPrice"], target - params["initial_stop_atr_mult"] * pos["entryAtr"])
                        # 매매 알림 메일용 - 이 행 갱신만으로는 "언제 얼마나" 추가매수했는지
                        # 남지 않아 별도로 기록해둔다(피라미딩은 이력 테이블이 없다).
                        last_pyramid_date, last_pyramid_shares = d, add_shares
        if not closed:
            pos_row.entry_price, pos_row.shares = pos["avgEntryPrice"], pos["shares"]
            pos_row.total_cost, pos_row.last_entry_price = pos["totalCost"], pos["lastEntryPrice"]
            pos_row.pyramid_count, pos_row.initial_shares = pos["pyramidCount"], pos["initialShares"]
            pos_row.stop_price, pos_row.stop_state = pos["stopPrice"], pos["stopState"]
            pos_row.highest_high, pos_row.bars_held = pos["highestHigh"], pos["barsHeld"]
            pos_row.partial_taken = pos["partialTaken"]
            if last_pyramid_date:
                pos_row.last_pyramid_date, pos_row.last_pyramid_shares = last_pyramid_date, last_pyramid_shares

    db.session.flush()

    # 2) 이 시점 평가액(현금+보유주식+지수 대납분)으로 국면·현금운용 판정
    remaining = PaperPosition.query.filter_by(account_id=account.id).all()
    held_value = 0.0
    for pos_row in remaining:
        bars = held_bars.get(pos_row.code)
        price = bars[-1]["close"] if bars else pos_row.entry_price
        held_value += pos_row.shares * price
    index_price, regime_ok = _anon_market_index_and_regime()
    index_value = account.index_units * index_price if index_price else 0.0
    equity_now = account.cash + held_value + index_value
    if not remaining and account.index_units <= 0:
        account.peak_equity = equity_now
    else:
        account.peak_equity = max(account.peak_equity, equity_now)

    # 3) 현금 유휴화 방지 - 국면이 꺼지면 지수 보유분 전량 현금화(방어)
    if not regime_ok and account.index_units > 0 and index_price:
        account.cash += account.index_units * index_price * (1 - ANON_INDEX_SLIPPAGE_PCT / 100)
        account.index_units = 0.0

    # 4) 재평가(신규진입) - vcp_strategy와 같은 간격(기본 3일)
    should_rescan = account.last_rescan_date is None or (
        datetime.fromisoformat(latest_date) - datetime.fromisoformat(account.last_rescan_date)
    ).days >= params["rescan_interval_days"]
    if should_rescan and (regime_ok or not params.get("gate_entries_on_regime", True)):
        open_slots = params["max_positions"] - len(remaining)
        if open_slots > 0:
            for row in candidate_rows:
                if open_slots <= 0:
                    break
                if row.code in held_codes:
                    continue
                bars = candidate_bars.get(row.code)
                if not bars or len(bars) < vcp.ATR_PERIOD + 1:
                    continue
                closes = [b["close"] for b in bars]
                highs = [b["high"] for b in bars]
                lows = [b["low"] for b in bars]
                j = len(bars) - 1
                atr20 = vcp._atr(highs, lows, closes, j, vcp.ATR_PERIOD)
                price = closes[j]
                if not atr20 or atr20 <= 0 or not price or price <= 0:
                    continue
                raw_risk = params["initial_stop_atr_mult"] * atr20
                risk_per_share = min(raw_risk, price * params["max_initial_risk_pct"] / 100) \
                    if params["risk_cap_mode"] == "shrink" else raw_risk
                if params["risk_cap_mode"] != "shrink" and (risk_per_share / price * 100) > params["max_initial_risk_pct"]:
                    continue
                if risk_per_share <= 0:
                    continue
                entry_fill = price * (1 + vcp.SLIPPAGE_ENTRY_PCT / 100)
                if params.get("position_sizing_mode") == "equal_weight":
                    # 슬롯당 동일 금액 배분 - vcp_strategy.run_vcp_backtest와 동일한 방식
                    target_value = equity_now / params["max_positions"]
                    max_position_value_abs = params.get("max_position_value_abs")
                    if max_position_value_abs is not None:
                        # 계좌가 커져도 포지션 금액이 무한정 커지지 않도록 고정 상한
                        # ("전략 용량" 가정 - vcp_strategy.run_vcp_backtest와 동일)
                        target_value = min(target_value, max_position_value_abs)
                    shares = int(target_value // entry_fill)
                    max_pos_value = target_value
                else:
                    risk_amount = equity_now * vcp.RISK_PCT_PER_TRADE / 100
                    shares = int(risk_amount // risk_per_share)
                    max_pos_value = account.seed * vcp.MAX_POSITION_WEIGHT_PCT / 100
                max_pct_of_avg_trade_value = params.get("max_pct_of_avg_trade_value")
                if max_pct_of_avg_trade_value is not None and row.avg_trade_value:
                    # 종목 자신의 평균거래대금 대비 비율로 추가 제한(시장충격 근사)
                    liquidity_cap_value = row.avg_trade_value * max_pct_of_avg_trade_value / 100
                    shares = min(shares, int(liquidity_cap_value // entry_fill))
                cap_shares = int(min(account.cash, max_pos_value) // entry_fill)
                shares = min(shares, cap_shares)
                if shares <= 0:
                    continue
                cost = shares * entry_fill
                if cost > account.cash:
                    continue
                account.cash -= cost
                db.session.add(PaperPosition(
                    account_id=account.id, code=row.code, name=row.name,
                    entry_date=latest_date, entry_price=entry_fill, shares=shares,
                    entry_atr=atr20, risk_per_share=risk_per_share,
                    stop_price=entry_fill - risk_per_share, stop_state="initialStop",
                    highest_high=entry_fill, bars_held=0, pyramid_count=0,
                    last_entry_price=entry_fill, total_cost=cost, initial_shares=shares,
                ))
                open_slots -= 1
        account.last_rescan_date = latest_date

    # 5) 현금 유휴화 방지 - 국면 OK인데 남은 유휴 현금을 지수 프록시에 태운다
    if regime_ok and index_price and account.cash > 0:
        cap_value = equity_now * params["equitize_max_pct"] / 100
        current_index_value = account.index_units * index_price
        room = max(0.0, cap_value - current_index_value)
        buy_cash = min(account.cash, room)
        if buy_cash > 0:
            account.index_units += buy_cash * (1 - ANON_INDEX_SLIPPAGE_PCT / 100) / index_price
            account.cash -= buy_cash

    account.last_processed_date = latest_date
    db.session.commit()


# ─── "스위퍼" 매매 알림 메일 ──────────────────────────────────────────────────
# 사용자가 실전 계좌에서 같은 매매를 직접 따라 하려면 장 마감(15:30) 전에
# 오늘 무엇을 사고팔지 알아야 한다 - app.py의 paper_trading_scheduler가 매일
# 한국시간 14:30에 모든 전략을 run_all_accounts()로 먼저 계산한 뒤에야 이
# send_sweeper_trade_alerts()를 호출한다(계산 -> 메일 순서를 그 시각에 보장하는
# 것이 핵심). 신규진입/청산(분할익절 포함)은 PaperPosition.entry_date/
# PaperTrade.exit_date로 "오늘 반영된 거래일"을 그대로 조회할 수 있지만,
# 피라미딩(추가매수)은 기존 행을 갱신할 뿐이라 last_pyramid_date/
# last_pyramid_shares(run_anonymous_daily_step에서 기록)가 없으면 "오늘 있었는지"를
# 알 방법이 없다.

_SWEEPER_ALERT_EXIT_REASON_LABEL = {
    "initialStop": "초기손절", "breakevenStop": "본전손절", "trailingStop": "트레일링손절",
    "timeStop": "시간손절", "maBreak": "이평선이탈", "maxHold": "최대보유도달",
    "partialProfit": "분할익절", "periodEnd": "기간종료",
}


def send_sweeper_trade_alerts():
    """알림 이메일이 설정된 활성 스위퍼 계좌를 전부 찾아 계좌별로 오늘의 매매를
    보낸다. run_all_accounts와 같은 이유로 계좌 하나의 실패가 나머지를
    막지 않도록 계좌별로 예외를 격리한다."""
    account_ids = [
        row.id for row in PaperStrategyAccount.query.filter_by(strategy="sweeper", is_active=True)
        .filter(PaperStrategyAccount.alert_email.isnot(None))
        .filter(PaperStrategyAccount.alert_email != "").all()
    ]
    for account_id in account_ids:
        try:
            _send_one_sweeper_alert(account_id)
        except Exception as e:
            db.session.rollback()
            print(f"[스위퍼 매매알림] 계좌 {account_id} 발송 오류: {e}")


def _send_one_sweeper_alert(account_id):
    import email_utils

    # gunicorn 워커 2개가 같은 시각(14:30)에 동시에 깨어날 수 있어, 중복 발송을
    # 막으려고 행 잠금을 걸고 나서야 last_alert_sent_date를 확인한다(RULES.md의
    # "워커 간 공유 상태" 문제와 같은 이유 - SQLite에서는 이 잠금이 사실상
    # 무시되지만 로컬은 어차피 프로세스가 하나뿐이라 문제가 안 된다).
    account = PaperStrategyAccount.query.filter_by(id=account_id).with_for_update().first()
    if not account or not account.alert_email:
        return
    target_date = account.last_processed_date
    if not target_date or account.last_alert_sent_date == target_date:
        db.session.commit()
        return

    buys = PaperPosition.query.filter_by(account_id=account.id, entry_date=target_date).all()
    pyramids = PaperPosition.query.filter_by(account_id=account.id, last_pyramid_date=target_date).all()
    sells = PaperTrade.query.filter_by(account_id=account.id, exit_date=target_date).all()

    lines = []
    if buys:
        lines.append("[신규 매수]")
        lines += [f"- {p.name}({p.code}): {p.shares:,}주 @ {round(p.entry_price):,}원" for p in buys]
    if pyramids:
        if lines:
            lines.append("")
        lines.append("[추가 매수(피라미딩)]")
        lines += [
            f"- {p.name}({p.code}): {(p.last_pyramid_shares or 0):,}주 추가 @ {round(p.last_entry_price):,}원"
            for p in pyramids
        ]
    if sells:
        if lines:
            lines.append("")
        lines.append("[매도]")
        lines += [
            f"- {t.name}({t.code}): {t.shares:,}주 @ {round(t.exit_price):,}원 "
            f"(사유: {_SWEEPER_ALERT_EXIT_REASON_LABEL.get(t.exit_reason, t.exit_reason)}, 손익 {t.pnl_pct:+.1f}%)"
            for t in sells
        ]

    if not lines:
        body = f"{target_date} 기준, 스위퍼 전략에서 오늘 실행할 매매가 없습니다."
    else:
        body = (
            f"{target_date} 확정 종가 기준, 스위퍼 전략의 오늘 매매 내역입니다.\n\n"
            + "\n".join(lines)
            + "\n\n※ 위 가격은 계산 기준가이며, 실제 체결가는 다를 수 있습니다."
        )

    subject = f"[JH-Trader] 스위퍼 오늘의 매매 ({target_date})"
    if email_utils.send_email(account.alert_email, subject, body):
        account.last_alert_sent_date = target_date
        db.session.commit()
    else:
        db.session.rollback()
