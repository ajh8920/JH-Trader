"""국내(KRX) 주식 단기/스윙 매매 백테스트 엔진.

시드가 작을 때(5천만원 미만)를 가정해 신용·미수 없이 종목당 전액매수/전량매도만
하는 단일 포지션 전략 3가지를 규칙 기반으로 구현한다. 야후 파이낸스 일봉(시가·
고가·저가·종가)만으로 "그 규칙이 그날 체결됐는지"를 근사하므로, 장중 실제 체결가와는
차이가 있을 수 있다. 슬리피지·거래세·수수료는 반영하지 않으므로 실매매 전 반드시
직접 검증하세요.

전략 3가지:
- volatility_breakout: 변동성 돌파(래리 윌리엄스 응용) — 시가+전일 변동폭×K를
  당일 고가가 넘으면 그 가격에 매수, 지정 보유일 경과 시 시가에 매도.
- box_breakout: 박스권 돌파(돈치안 채널) — N일 신고가 갱신 시 종가 매수, M일
  신저가 이탈 시 종가 매도(추세추종).
- ma_pullback: 이동평균 눌림목 — 장기 이평선 위 상승추세에서 단기 이평선까지
  눌린 뒤 재반등할 때 매수, 목표수익률·단기선 이탈 시 매도.

공통으로 손절율(%)을 적용해 저가가 손절가 아래로 내려가면 그날 청산한다(시가가
이미 손절가 아래로 갭하락했다면 시가로 체결).
"""

import math
from datetime import date

from backtest import fetch_daily_prices


def _lookup_kr_market(ticker):
    # 야후는 .KS로 물어봐도 실제로는 코스닥 종목의 데이터를 그대로 돌려주는
    # 경우가 있어(내부적으로 코드만 보고 종목을 찾는 듯), 어떤 접미사로 성공했는지로
    # 시장을 추측하면 틀릴 수 있다. 실제 거래소 메타데이터로 한 번 더 확인한다.
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).get_info()
        exch = (info.get("fullExchangeName") or "").upper()
        if "KOSDAQ" in exch:
            return "KOSDAQ"
        if "KSE" in exch or "KOSPI" in exch:
            return "KOSPI"
    except Exception:
        pass
    return None


def fetch_kr_bars(code, start, end):
    """6자리 종목코드만 주어지면 코스피(.KS)/코스닥(.KQ)을 순서대로 시도한다."""
    code = code.strip().upper()
    candidates = [code] if "." in code else [f"{code}.KS", f"{code}.KQ"]
    for cand in candidates:
        bars = fetch_daily_prices(cand, start, end)
        if bars:
            fallback = "KOSPI" if cand.endswith(".KS") else ("KOSDAQ" if cand.endswith(".KQ") else "-")
            market = _lookup_kr_market(cand) or fallback
            return cand, market, bars
    return None, None, []


def _max_drawdown_pct(values):
    if not values:
        return 0.0
    peak = values[0]
    mdd = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak * 100
            if dd > mdd:
                mdd = dd
    return round(mdd, 2)


def _cagr_pct(seed, final_value, start_date_str, end_date_str):
    if seed <= 0:
        return 0.0
    start_d = date.fromisoformat(start_date_str)
    end_d = date.fromisoformat(end_date_str)
    years = (end_d - start_d).days / 365.25
    if years <= 0:
        return 0.0
    if final_value <= 0:
        return -100.0
    return round(((final_value / seed) ** (1 / years) - 1) * 100, 2)


def _calmar_ratio(cagr_pct, mdd_pct):
    if not mdd_pct:
        return None
    return round(cagr_pct / mdd_pct, 2)


def _profit_loss_ratio(sell_trades):
    # 손익비 = 이긴 거래의 평균 수익률 ÷ 진 거래의 평균 손실률(절대값). 승률과 함께
    # 봐야 전략의 기대값을 판단할 수 있는 짝꿍 지표라 승률 옆에 같이 보여준다.
    wins = [t["pnlPct"] for t in sell_trades if t.get("pnlPct", 0) > 0]
    losses = [t["pnlPct"] for t in sell_trades if t.get("pnlPct", 0) < 0]
    if not losses:
        return None
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses))
    if avg_loss == 0:
        return None
    return round(avg_win / avg_loss, 2)


def _sma(closes, period, i):
    if i + 1 < period:
        return None
    window = closes[i + 1 - period: i + 1]
    return sum(window) / period


def _run_volatility_breakout(bars, seed, params):
    k = float(params.get("k", 0.5))
    hold_days = max(1, int(params.get("holdDays", 1)))
    stop_loss_pct = float(params.get("stopLossPct", -5))

    cash = seed
    qty = 0
    entry_price = 0.0
    entry_idx = None
    trades = []
    equity_curve = []

    for i, bar in enumerate(bars):
        date, o, h, l, c = bar["date"], bar["open"], bar["high"], bar["low"], bar["close"]

        if qty == 0:
            if i > 0:
                prev = bars[i - 1]
                breakout_price = o + (prev["high"] - prev["low"]) * k
                if breakout_price > 0 and h >= breakout_price:
                    fill = max(breakout_price, o)
                    buy_qty = math.floor(cash / fill)
                    if buy_qty > 0:
                        cash -= buy_qty * fill
                        qty = buy_qty
                        entry_price = fill
                        entry_idx = i
                        trades.append({
                            "date": date, "action": "buy", "price": round(fill, 2), "qty": buy_qty,
                            "note": f"Volatility breakout (K={k}) buy",
                        })
        else:
            stop_price = entry_price * (1 + stop_loss_pct / 100)
            if l <= stop_price:
                sell_price = o if o <= stop_price else stop_price
                cash += qty * sell_price
                trades.append({
                    "date": date, "action": "sell", "price": round(sell_price, 2), "qty": qty,
                    "note": f"Stop-loss sell ({stop_loss_pct}%)",
                    "pnlPct": round((sell_price - entry_price) / entry_price * 100, 2),
                    "holdDays": i - entry_idx,
                })
                qty = 0
                entry_idx = None
            elif (i - entry_idx) >= hold_days:
                cash += qty * o
                trades.append({
                    "date": date, "action": "sell", "price": round(o, 2), "qty": qty,
                    "note": f"Holding period ({hold_days}d) reached, open sell",
                    "pnlPct": round((o - entry_price) / entry_price * 100, 2),
                    "holdDays": i - entry_idx,
                })
                qty = 0
                entry_idx = None

        equity_curve.append({"date": date, "value": round(cash + qty * c, 2)})

    return trades, equity_curve, cash, qty, entry_price


def _run_box_breakout(bars, seed, params):
    entry_n = max(2, int(params.get("entryN", 20)))
    exit_n = max(2, int(params.get("exitN", 10)))
    stop_loss_pct = float(params.get("stopLossPct", -7))

    cash = seed
    qty = 0
    entry_price = 0.0
    entry_idx = None
    trades = []
    equity_curve = []

    for i, bar in enumerate(bars):
        date, o, h, l, c = bar["date"], bar["open"], bar["high"], bar["low"], bar["close"]

        if qty == 0:
            if i >= entry_n:
                highest = max(b["high"] for b in bars[i - entry_n:i])
                if c > highest:
                    buy_qty = math.floor(cash / c)
                    if buy_qty > 0:
                        cash -= buy_qty * c
                        qty = buy_qty
                        entry_price = c
                        entry_idx = i
                        trades.append({
                            "date": date, "action": "buy", "price": round(c, 2), "qty": buy_qty,
                            "note": f"{entry_n}-day high breakout buy",
                        })
        else:
            stop_price = entry_price * (1 + stop_loss_pct / 100)
            if l <= stop_price:
                sell_price = o if o <= stop_price else stop_price
                cash += qty * sell_price
                trades.append({
                    "date": date, "action": "sell", "price": round(sell_price, 2), "qty": qty,
                    "note": f"Stop-loss sell ({stop_loss_pct}%)",
                    "pnlPct": round((sell_price - entry_price) / entry_price * 100, 2),
                    "holdDays": i - entry_idx,
                })
                qty = 0
                entry_idx = None
            elif i >= exit_n:
                lowest = min(b["low"] for b in bars[max(0, i - exit_n):i])
                if c < lowest:
                    cash += qty * c
                    trades.append({
                        "date": date, "action": "sell", "price": round(c, 2), "qty": qty,
                        "note": f"{exit_n}-day low breakdown sell",
                        "pnlPct": round((c - entry_price) / entry_price * 100, 2),
                        "holdDays": i - entry_idx,
                    })
                    qty = 0
                    entry_idx = None

        equity_curve.append({"date": date, "value": round(cash + qty * c, 2)})

    return trades, equity_curve, cash, qty, entry_price


def _run_ma_pullback(bars, seed, params):
    long_period = max(2, int(params.get("longMa", 20)))
    short_period = max(2, int(params.get("shortMa", 5)))
    stop_loss_pct = float(params.get("stopLossPct", -5))
    target_pct = float(params.get("targetPct", 10))

    closes = [b["close"] for b in bars]
    long_ma = [_sma(closes, long_period, i) for i in range(len(bars))]
    short_ma = [_sma(closes, short_period, i) for i in range(len(bars))]

    cash = seed
    qty = 0
    entry_price = 0.0
    entry_idx = None
    trades = []
    equity_curve = []

    for i, bar in enumerate(bars):
        date, o, h, l, c = bar["date"], bar["open"], bar["high"], bar["low"], bar["close"]
        lma, sma = long_ma[i], short_ma[i]

        if qty == 0:
            if i > 0 and lma is not None and sma is not None and long_ma[i - 1] is not None:
                uptrend = c > lma and lma > long_ma[i - 1]
                pulled_back = l <= sma * 1.01
                rebounding = c > bars[i - 1]["close"] and c > sma
                if uptrend and pulled_back and rebounding:
                    buy_qty = math.floor(cash / c)
                    if buy_qty > 0:
                        cash -= buy_qty * c
                        qty = buy_qty
                        entry_price = c
                        entry_idx = i
                        trades.append({
                            "date": date, "action": "buy", "price": round(c, 2), "qty": buy_qty,
                            "note": f"MA{long_period} uptrend pullback to MA{short_period}, rebound buy",
                        })
        else:
            stop_price = entry_price * (1 + stop_loss_pct / 100)
            target_price = entry_price * (1 + target_pct / 100)
            if l <= stop_price:
                sell_price = o if o <= stop_price else stop_price
                cash += qty * sell_price
                trades.append({
                    "date": date, "action": "sell", "price": round(sell_price, 2), "qty": qty,
                    "note": f"Stop-loss sell ({stop_loss_pct}%)",
                    "pnlPct": round((sell_price - entry_price) / entry_price * 100, 2),
                    "holdDays": i - entry_idx,
                })
                qty = 0
                entry_idx = None
            elif h >= target_price:
                sell_price = o if o >= target_price else target_price
                cash += qty * sell_price
                trades.append({
                    "date": date, "action": "sell", "price": round(sell_price, 2), "qty": qty,
                    "note": f"Target return ({target_pct}%) reached, sell",
                    "pnlPct": round((sell_price - entry_price) / entry_price * 100, 2),
                    "holdDays": i - entry_idx,
                })
                qty = 0
                entry_idx = None
            elif sma is not None and c < sma:
                cash += qty * c
                trades.append({
                    "date": date, "action": "sell", "price": round(c, 2), "qty": qty,
                    "note": f"MA{short_period} breakdown sell",
                    "pnlPct": round((c - entry_price) / entry_price * 100, 2),
                    "holdDays": i - entry_idx,
                })
                qty = 0
                entry_idx = None

        equity_curve.append({"date": date, "value": round(cash + qty * c, 2)})

    return trades, equity_curve, cash, qty, entry_price


def _run_combo(bars, seed, params):
    """세 전략의 강점을 순서대로 결합한 복합 전략.

    1) 추세 필터(박스권 돌파의 강점): 장기 이평선 위 + 그 이평선이 상승 중일 때만
       매매 후보로 삼아, 하락·횡보 구간의 잦은 손절을 피한다.
    2) 눌림목 대기(이동평균 눌림목의 강점): 추세가 살아있는 채로 단기 이평선까지
       조정을 받아야만 진입을 고려해, 고점 추격매수보다 유리한 평단가를 노린다.
    3) 모멘텀 진입 확인(변동성 돌파의 강점): "눌림목"이라고 막연히 판단하지 않고,
       그날 시가+전일 변동폭×K를 넘는 실제 반등이 나온 날에만 그 가격으로 진입한다.
    4) 청산은 손절로 하방만 제한하고, 고정 익절 상한은 두지 않는다(박스권 돌파의
       강점 그대로). 초기 버전은 고정 목표수익률 도달 시 익절했었는데, 강한
       추세에서 일찍 팔고 재진입 조건(추세+눌림목+모멘텀 재확인)을 다시 기다리다
       추세 대부분을 놓치는 경우가 많아 제거했다 — 실데이터 검증(005930, 2025-08~
       2026-08, 250%대 상승 구간)에서 목표수익률 15%를 없애자 수익률이 19%→121%로
       뛰었다. N일 신저가 이탈(추세 종료) 전까지는 계속 보유해 수익을 태운다.
    """
    trend_period = max(2, int(params.get("trendMa", 60)))
    pullback_period = max(2, int(params.get("pullbackMa", 20)))
    breakout_k = float(params.get("breakoutK", 0.3))
    stop_loss_pct = float(params.get("stopLossPct", -6))
    trailing_exit_n = max(2, int(params.get("trailingExitN", 10)))

    closes = [b["close"] for b in bars]
    trend_ma = [_sma(closes, trend_period, i) for i in range(len(bars))]
    pullback_ma = [_sma(closes, pullback_period, i) for i in range(len(bars))]

    cash = seed
    qty = 0
    entry_price = 0.0
    entry_idx = None
    trades = []
    equity_curve = []

    for i, bar in enumerate(bars):
        date, o, h, l, c = bar["date"], bar["open"], bar["high"], bar["low"], bar["close"]
        tma, pma = trend_ma[i], pullback_ma[i]

        if qty == 0:
            if i >= 5 and tma is not None and pma is not None and trend_ma[i - 5] is not None:
                uptrend = c > tma and tma > trend_ma[i - 5]
                pulled_back = uptrend and l <= pma * 1.015
                if pulled_back:
                    prev = bars[i - 1]
                    breakout_price = o + (prev["high"] - prev["low"]) * breakout_k
                    if breakout_price > 0 and h >= breakout_price:
                        fill = max(breakout_price, o)
                        buy_qty = math.floor(cash / fill)
                        if buy_qty > 0:
                            cash -= buy_qty * fill
                            qty = buy_qty
                            entry_price = fill
                            entry_idx = i
                            trades.append({
                                "date": date, "action": "buy", "price": round(fill, 2), "qty": buy_qty,
                                "note": f"Trend(MA{trend_period})+pullback(MA{pullback_period})+momentum(K={breakout_k}) confirmed buy",
                            })
        else:
            stop_price = entry_price * (1 + stop_loss_pct / 100)
            if l <= stop_price:
                sell_price = o if o <= stop_price else stop_price
                cash += qty * sell_price
                trades.append({
                    "date": date, "action": "sell", "price": round(sell_price, 2), "qty": qty,
                    "note": f"Stop-loss sell ({stop_loss_pct}%)",
                    "pnlPct": round((sell_price - entry_price) / entry_price * 100, 2),
                    "holdDays": i - entry_idx,
                })
                qty = 0
                entry_idx = None
            elif i >= trailing_exit_n:
                lowest = min(b["low"] for b in bars[max(0, i - trailing_exit_n):i])
                if c < lowest:
                    cash += qty * c
                    trades.append({
                        "date": date, "action": "sell", "price": round(c, 2), "qty": qty,
                        "note": f"{trailing_exit_n}-day low breakdown (trend end) sell",
                        "pnlPct": round((c - entry_price) / entry_price * 100, 2),
                        "holdDays": i - entry_idx,
                    })
                    qty = 0
                    entry_idx = None

        equity_curve.append({"date": date, "value": round(cash + qty * c, 2)})

    return trades, equity_curve, cash, qty, entry_price


STRATEGIES = {
    "volatility_breakout": {
        "label": "변동성 돌파",
        "run": _run_volatility_breakout,
        "defaults": {"k": 0.5, "holdDays": 1, "stopLossPct": -5},
    },
    "box_breakout": {
        "label": "박스권 돌파",
        "run": _run_box_breakout,
        "defaults": {"entryN": 20, "exitN": 10, "stopLossPct": -7},
    },
    "ma_pullback": {
        "label": "이동평균 눌림목",
        "run": _run_ma_pullback,
        "defaults": {"longMa": 20, "shortMa": 5, "stopLossPct": -5, "targetPct": 10},
    },
    "combo": {
        "label": "복합전략(추세+눌림목+모멘텀)",
        "run": _run_combo,
        "defaults": {"trendMa": 60, "pullbackMa": 20, "breakoutK": 0.3, "stopLossPct": -6, "trailingExitN": 10},
    },
}


def run_kr_swing_backtest(strategy, code, start, end, seed, params):
    if strategy not in STRATEGIES:
        return {"error": "지원하지 않는 전략입니다"}

    resolved_ticker, market, bars = fetch_kr_bars(code, start, end)
    if not bars:
        return {"error": f'"{code}"의 시세 데이터를 가져올 수 없습니다 (종목 코드 또는 기간을 확인하세요)'}
    if len(bars) < 5:
        return {"error": "데이터가 너무 적어 백테스트할 수 없습니다 (기간을 늘려주세요)"}

    spec = STRATEGIES[strategy]
    merged_params = dict(spec["defaults"])
    merged_params.update({k: v for k, v in params.items() if v is not None})

    trades, equity_curve, cash, qty, entry_price = spec["run"](bars, seed, merged_params)

    last_close = bars[-1]["close"]
    total_buy = sum(t["price"] * t["qty"] for t in trades if t["action"] == "buy")
    total_sell = sum(t["price"] * t["qty"] for t in trades if t["action"] == "sell")
    total_buy_qty = sum(t["qty"] for t in trades if t["action"] == "buy")
    total_sell_qty = sum(t["qty"] for t in trades if t["action"] == "sell")
    holding_value = round(qty * last_close, 2)
    eval_pnl = round((total_sell + holding_value) - total_buy, 2)
    return_pct = round(eval_pnl / total_buy * 100, 2) if total_buy > 0 else 0.0
    seed_return_pct = round(eval_pnl / seed * 100, 2) if seed > 0 else 0.0

    sell_trades = [t for t in trades if t["action"] == "sell"]
    win_trades = [t for t in sell_trades if t.get("pnlPct", 0) > 0]
    win_rate_pct = round(len(win_trades) / len(sell_trades) * 100, 1) if sell_trades else 0.0
    avg_hold_days = round(sum(t.get("holdDays", 0) for t in sell_trades) / len(sell_trades), 1) if sell_trades else 0.0

    # 지수(벤치마크) 비교: 같은 종목을 첫날 종가에 전량 매수해 그대로 보유했을 경우
    first_close = bars[0]["close"]
    bh_shares = math.floor(seed / first_close) if first_close > 0 else 0
    bh_leftover = seed - bh_shares * first_close
    bh_curve = [
        {"date": eq["date"], "value": round(bh_leftover + bh_shares * bar["close"], 2)}
        for eq, bar in zip(equity_curve, bars)
    ]
    bh_final_value = bh_curve[-1]["value"] if bh_curve else seed
    bh_return_pct = round((bh_final_value - seed) / seed * 100, 2) if seed > 0 else 0.0

    strategy_mdd = _max_drawdown_pct([e["value"] for e in equity_curve])
    bh_mdd = _max_drawdown_pct([e["value"] for e in bh_curve])
    alpha_pct = round(seed_return_pct - bh_return_pct, 2)

    final_value = seed + eval_pnl
    cagr_pct = _cagr_pct(seed, final_value, bars[0]["date"], bars[-1]["date"])
    calmar_ratio = _calmar_ratio(cagr_pct, strategy_mdd)
    profit_loss_ratio = _profit_loss_ratio(sell_trades)

    # 단일 포지션 전략이라 매수 직후엔 그 수량이, 매도 직후엔 0이 스냅샷이 된다.
    running_qty = 0
    running_avg = 0.0
    for t in trades:
        if t["action"] == "buy":
            running_qty = t["qty"]
            running_avg = t["price"]
        else:
            running_qty = 0
            running_avg = 0.0
        t["qtyAfter"] = running_qty
        t["avgPriceAfter"] = round(running_avg, 2) if running_qty > 0 else None

    return {
        "code": code,
        "ticker": resolved_ticker,
        "market": market,
        "strategy": strategy,
        "strategyLabel": spec["label"],
        "params": merged_params,
        "start": bars[0]["date"],
        "end": bars[-1]["date"],
        "seed": seed,
        "totalBuyAmount": round(total_buy, 2),
        "totalSellAmount": round(total_sell, 2),
        "totalBuyQty": total_buy_qty,
        "totalSellQty": total_sell_qty,
        "evalPnl": eval_pnl,
        "returnPct": return_pct,
        "seedReturnPct": seed_return_pct,
        "tradeCount": len(sell_trades),
        "winCount": len(win_trades),
        "winRatePct": win_rate_pct,
        "avgHoldDays": avg_hold_days,
        "holding": {
            "qty": qty,
            "avgPrice": round(entry_price, 2) if qty > 0 else None,
            "currentPrice": last_close,
            "value": holding_value,
        },
        "mddPct": strategy_mdd,
        "alphaPct": alpha_pct,
        "cagrPct": cagr_pct,
        "calmarRatio": calmar_ratio,
        "profitLossRatio": profit_loss_ratio,
        "benchmark": {
            "label": f"{resolved_ticker} Buy & Hold",
            "returnPct": bh_return_pct,
            "mddPct": bh_mdd,
            "equityCurve": bh_curve,
        },
        "trades": list(reversed(trades)),
        "equityCurve": equity_curve,
        "priceCurve": [{"date": b["date"], "close": round(b["close"], 2)} for b in bars],
    }
