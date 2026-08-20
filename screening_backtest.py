"""스크리닝(트렌드 템플릿/미너비니 단계) 전략을 과거 구간에 대해 워크포워드로
시뮬레이션한다 - "이 전략으로 스크리닝했다면 그동안 어떤 종목을 언제 사서
언제 팔았을지, 손익이 얼마였을지"를 보여주는 것이 목적이다.

## 룩어헤드 편향 방지 (RULES.md R3)
각 재평가 시점의 트렌드 템플릿 조건·RS 등급·단계는 그 시점까지의 가격만으로
매번 다시 계산한다 - trend_screener.evaluate_trend_template/compute_universe_screen을
그대로 재사용한다(실제 스크리닝 화면과 다른 판정 로직을 쓰면 백테스트가 화면과
다른 결과를 보여주는 문제가 생기고, 미래 가격을 참조해 "그때도 조건을
만족했었다"고 판정하면 실제로는 알 수 없었던 정보로 매수한 것이 된다).

## 지원 전략 범위
가격 데이터만으로 계산 가능한 전략만 지원한다(트렌드 템플릿 8/8, 단계 N 이상).
CANSLIM/딥밸류처럼 PER·ROE 등 재무 지표에 의존하는 전략 프리셋은, 이 화면이
과거 시점의 재무 스냅샷을 시계열로 갖고 있지 않아(현재 값만 캐시) 여기서는
지원하지 않는다.

## 매매 규칙
- 재평가(리밸런싱) 주기: 주 단위. 매일 전종목을 재계산하면 계산량이 지나치게
  커진다(국내 전종목 1회 평가에 로컬 기준 몇 분 단위) - 주 단위면 그 배수로
  늘어난다. 스윙성 전략에는 충분한 감도라고 판단했다.
- 매도: 손절(-N%, 기본 -8%) 또는 조건 이탈(그 재평가 시점에 더 이상 전략
  조건을 만족하지 않음) 중 먼저 오는 재평가 시점에 청산한다. 고정 익절 상한은
  두지 않는다 - kr_swing.py의 combo 전략과 같은 이유(강한 추세장에서 상승분을
  다 못 먹는 문제)로 조건 이탈 자체를 청산 신호로 쓴다.
- 손절은 직전~이번 재평가 시점 사이의 일별 저가를 스캔해 손절선에 처음 닿은
  날 그 손절가에 청산된 것으로 본다(재평가 시점 종가만 보면 그 사이 급락분을
  다 뒤집어써 실제 손절폭보다 훨씬 큰 손실로 잡히는 문제가 있었다). 시가
  데이터가 없어 갭하락으로 손절가 아래에서 시작한 경우까지는 반영하지 못한다.
- 신규 매수: 이번 재평가에 조건을 만족하는데 아직 보유하지 않은 종목 중,
  RS 등급이 높은 순으로 비어 있는 슬롯 수만큼 채운다.
- 포지션 크기: 슬롯당 동일 비중(시드 ÷ 최대 보유 종목 수)으로 고정한다.
"""

import bisect
from datetime import datetime, timedelta

import trend_screener as ts

DEFAULT_STOP_LOSS_PCT = -8.0
DEFAULT_MAX_POSITIONS = 10
RESCAN_INTERVAL_DAYS = 7
WARMUP_DAYS = 450  # MIN_BARS(200일선) + RS 계산용 12개월 수익률 확보


def _strategy_predicate(strategy):
    """strategy: 'trendTemplate' 또는 'stageN'(N=1~4)."""
    if strategy and strategy.startswith("stage"):
        try:
            n = int(strategy[len("stage"):])
        except ValueError:
            n = 2
        return lambda e: (e.get("stage") or 0) >= n
    return lambda e: bool(e.get("allPass"))


def _strategy_label(strategy):
    if strategy and strategy.startswith("stage"):
        try:
            n = int(strategy[len("stage"):])
            return f"Stage {n}+"
        except ValueError:
            pass
    return "Trend Template 8/8"


def run_screening_backtest(market, strategy, start_date, end_date,
                            stop_loss_pct=DEFAULT_STOP_LOSS_PCT, max_positions=DEFAULT_MAX_POSITIONS,
                            seed=10_000_000):
    if market not in ("KR", "US"):
        return {"error": "market은 KR 또는 US여야 합니다"}

    universe = ts.load_universe(market)
    tickers = [t for _, _, t, _, _ in universe]
    info_by_ticker = {t: (code, name, industry, sector) for code, name, t, industry, sector in universe}

    fetch_start = (datetime.fromisoformat(start_date) - timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
    fetch_end = (datetime.fromisoformat(end_date) + timedelta(days=1)).strftime("%Y-%m-%d")

    # 종목별 전체 기간 가격을 한 번만 받아, 재평가 시점마다 그 시점까지의
    # 구간만 슬라이스해서 재사용한다(API를 매번 다시 부르지 않는다).
    history = {}
    series = {}  # ticker -> (dates, closes, highs, lows)
    for ticker, bars in ts.fetch_ohlc_history_batches(tickers, fetch_start, fetch_end):
        history[ticker] = bars
        series[ticker] = (
            [b["date"] for b in bars],
            [b["close"] for b in bars],
            [b["high"] for b in bars],
            [b["low"] for b in bars],
        )

    all_dates = sorted({d for dates, *_ in series.values() for d in dates if start_date <= d <= end_date})
    if not all_dates:
        return {"error": "해당 기간에 사용 가능한 가격 데이터가 없습니다"}

    rebalance_dates = all_dates[::RESCAN_INTERVAL_DAYS]
    if rebalance_dates[-1] != all_dates[-1]:
        rebalance_dates.append(all_dates[-1])

    predicate = _strategy_predicate(strategy)

    positions = {}  # ticker -> {code, name, entryDate, entryPrice}
    trades = []
    equity_curve = []
    slot_capital = seed / max_positions
    prev_rd = None

    for rd in rebalance_dates:
        evaluated = []
        idx_at_rd = {}
        for ticker, (dates, closes, highs, lows) in series.items():
            i = bisect.bisect_right(dates, rd) - 1
            if i < 0:
                continue
            idx_at_rd[ticker] = i
            if i + 1 < ts.MIN_BARS:
                continue
            code, name, industry, sector = info_by_ticker.get(ticker, (ticker, ticker, None, None))
            try:
                result = ts.evaluate_trend_template(code, name, history[ticker][:i + 1], industry, sector)
            except Exception:
                continue
            if result:
                result["_ticker"] = ticker
                evaluated.append(result)
        ts.compute_universe_screen(evaluated)
        by_ticker = {e["_ticker"]: e for e in evaluated}
        qualifying = {t: e for t, e in by_ticker.items() if predicate(e)}

        # 1) 보유 종목 청산 판정. 손절은 재평가 시점 종가만 보면 그 사이 급락분을
        #    다 뒤집어써 실제 손절폭보다 훨씬 큰 손실로 잡힌다(직전 재평가~이번
        #    재평가 사이에 -8% 손절선을 진작 스쳤는데 그 주 종가가 -19%까지 더
        #    떨어진 걸 그대로 청산가로 쓰는 식) - 그 구간의 일별 저가를 스캔해
        #    손절선에 처음 닿은 날, 그 손절가에 청산된 것으로 근사한다(시가
        #    데이터가 없어 갭하락은 반영하지 못한다).
        for ticker in list(positions.keys()):
            i = idx_at_rd.get(ticker)
            if i is None:
                continue
            pos = positions[ticker]
            dates, closes, highs, lows = series[ticker]
            stop_price = pos["entryPrice"] * (1 + stop_loss_pct / 100)
            start_i = bisect.bisect_right(dates, prev_rd) if prev_rd else 0
            exit_price = None
            exit_date = None
            reason = None
            for j in range(start_i, i + 1):
                if lows[j] <= stop_price:
                    exit_price, exit_date, reason = stop_price, dates[j], "stopLoss"
                    break
            if exit_price is None:
                price = closes[i]
                if ticker not in qualifying:
                    exit_price, exit_date, reason = price, rd, "conditionExit"
            if reason:
                pnl_pct = round((exit_price - pos["entryPrice"]) / pos["entryPrice"] * 100, 2)
                trades.append({
                    "code": pos["code"], "name": pos["name"],
                    "entryDate": pos["entryDate"], "entryPrice": round(pos["entryPrice"], 2),
                    "exitDate": exit_date, "exitPrice": round(exit_price, 2),
                    "pnlPct": pnl_pct, "exitReason": reason,
                    "holdDays": (datetime.fromisoformat(exit_date) - datetime.fromisoformat(pos["entryDate"])).days,
                })
                del positions[ticker]
        prev_rd = rd

        # 2) 빈 슬롯을 RS 등급 높은 순으로 채운다
        open_slots = max_positions - len(positions)
        if open_slots > 0:
            candidates = [
                (t, e) for t, e in qualifying.items()
                if t not in positions
            ]
            candidates.sort(key=lambda te: -(te[1].get("rsRating") or 0))
            for ticker, e in candidates[:open_slots]:
                i = idx_at_rd.get(ticker)
                if i is None:
                    continue
                price = series[ticker][1][i]
                if price is None or price <= 0:
                    continue
                positions[ticker] = {
                    "code": e["code"], "name": e["name"], "entryDate": rd, "entryPrice": price,
                }

        # 3) 이 시점 포트폴리오 평가액(보유 슬롯은 현재가 기준, 빈 슬롯은 현금 그대로)
        held_value = 0.0
        for ticker, pos in positions.items():
            i = idx_at_rd.get(ticker)
            price = series[ticker][1][i] if i is not None else pos["entryPrice"]
            held_value += slot_capital * (price / pos["entryPrice"])
        idle_value = slot_capital * (max_positions - len(positions))
        equity_curve.append({"date": rd, "value": round(held_value + idle_value, 2)})

    # 종료 시점까지 남은 보유 종목은 마지막 가격으로 강제 청산해 손익을 확정한다.
    last_rd = rebalance_dates[-1]
    for ticker, pos in positions.items():
        i = bisect.bisect_right(series[ticker][0], last_rd) - 1
        if i < 0:
            continue
        price = series[ticker][1][i]
        pnl_pct = round((price - pos["entryPrice"]) / pos["entryPrice"] * 100, 2)
        trades.append({
            "code": pos["code"], "name": pos["name"],
            "entryDate": pos["entryDate"], "entryPrice": round(pos["entryPrice"], 2),
            "exitDate": last_rd, "exitPrice": round(price, 2),
            "pnlPct": pnl_pct, "exitReason": "periodEnd",
            "holdDays": (datetime.fromisoformat(last_rd) - datetime.fromisoformat(pos["entryDate"])).days,
        })

    trades.sort(key=lambda t: t["entryDate"])

    final_value = equity_curve[-1]["value"] if equity_curve else seed
    return_pct = round((final_value / seed - 1) * 100, 2) if seed > 0 else 0.0
    win_trades = [t for t in trades if t["pnlPct"] > 0]
    win_rate_pct = round(len(win_trades) / len(trades) * 100, 1) if trades else None
    avg_hold_days = round(sum(t["holdDays"] for t in trades) / len(trades), 1) if trades else None

    peak = seed
    mdd_pct = 0.0
    for pt in equity_curve:
        peak = max(peak, pt["value"])
        if peak > 0:
            mdd_pct = max(mdd_pct, (peak - pt["value"]) / peak * 100)

    return {
        "market": market, "strategy": strategy, "strategyLabel": _strategy_label(strategy),
        "start": start_date, "end": end_date, "seed": seed,
        "stopLossPct": stop_loss_pct, "maxPositions": max_positions,
        "returnPct": return_pct, "finalValue": round(final_value, 2),
        "tradeCount": len(trades), "winCount": len(win_trades), "winRatePct": win_rate_pct,
        "avgHoldDays": avg_hold_days, "mddPct": round(mdd_pct, 2),
        "equityCurve": equity_curve, "trades": trades,
    }
