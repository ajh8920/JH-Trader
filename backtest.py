"""라오어의 무한매수법 백테스트 엔진 (v2.2 / v3.0 / v4.0).

매수·매도·T값 공식 자체는 `infinite_buying.py`(백테스트와 실전 현황이 공유하는
공통 모듈)에 있고, 이 파일은 일봉 시가/종가/고가로 "그 공식에 따른 주문이 그날
체결됐는지"를 시뮬레이션하는 역할만 한다. LOC(지정가-종가) 주문은 "종가가 조건을
만족해야 그날 종가에 체결"로, 일반 지정가 주문은 "고가가 지정가 이상이면 그
지정가에 체결"로 근사한다.

1일차(보유수량 0)는 "전일 종가×1.12"(=+목표%)에 LOC 매수를 걸어 사실상 항상
체결되도록 하는 것이 원문 설명이나, LOC는 체결가가 항상 그날 종가이므로 결과는
"그날 종가로 매수"와 동일하다(급등으로 갭이 목표%를 초과하는 극단적 경우만 예외이며
이 구현에서는 그 예외를 반영하지 않는다).

화면에 표시되는 날짜는 실제 미국 거래일 종가 기준 +1일(한국 투자자가 다음날 새벽에
체결을 확인하는 관행)로 보정해 보여준다. 내부 계산은 실제 미국 거래일 기준으로 진행된다.

지수(벤치마크) 비교: 같은 종목을 첫날 종가에 전량 매수해 그대로 보유했을 경우
(매수후보유)를 벤치마크로 삼아 MDD(최대낙폭)와 알파(전략 수익률 − 벤치마크 수익률)를
함께 계산한다.

실매매 전 반드시 결과를 직접 검증하세요.
"""

import math
from datetime import datetime, timedelta

from infinite_buying import PositionState, get_target_default, get_version_defaults

# 미국 거래일 종가는 한국 투자자 기준으로 보통 다음날 새벽에 확정되므로,
# 화면에 보여줄 날짜는 실제 미국 거래일 + 1일(한국 리포트 관행)로 표시한다.
def _display_date(us_trading_date_str):
    dt = datetime.strptime(us_trading_date_str, "%Y-%m-%d") + timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def fetch_daily_prices(ticker, start, end):
    import yfinance as yf

    end_inclusive = (datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    df = yf.download(ticker, start=start, end=end_inclusive, progress=False, auto_adjust=False, timeout=8)
    if df is None or df.empty:
        return []

    if hasattr(df.columns, "get_level_values") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)

    bars = []
    for idx, row in df.iterrows():
        try:
            o, c, h, l = float(row["Open"]), float(row["Close"]), float(row["High"]), float(row["Low"])
        except (TypeError, ValueError):
            continue
        # NaN과의 <= 비교는 항상 False라서 그냥 두면 결측치 행이 필터를 통과해버린다.
        if math.isnan(o) or math.isnan(c) or math.isnan(h) or math.isnan(l):
            continue
        if o <= 0 or c <= 0 or h <= 0 or l <= 0:
            continue
        bars.append({"date": idx.strftime("%Y-%m-%d"), "open": o, "close": c, "high": h, "low": l})
    return bars


def run_infinite_buying(ticker, start, end, seed, splits, target_return_pct, version="v2"):
    bars = fetch_daily_prices(ticker, start, end)
    if not bars:
        return {"error": f'"{ticker}"의 시세 데이터를 가져올 수 없습니다 (티커 또는 기간을 확인하세요)'}

    defaults = get_version_defaults(version)
    state = PositionState(seed, splits, target_return_pct, compound_mid_cycle=defaults["compound_mid_cycle"])

    trades = []
    equity_curve = []
    price_curve = []

    for bar in bars:
        date, c, h = bar["date"], bar["close"], bar["high"]
        display_date = _display_date(date)
        price_curve.append({"date": display_date, "close": round(c, 4)})

        if state.holding_qty == 0:
            state.start_cycle()
            amount = min(state.split_amount, state.cash)
            qty = math.floor(amount / c)
            if qty > 0:
                state.apply_buy(c, qty)
                trades.append({
                    "cycle": state.cycle_no, "date": display_date, "action": "buy",
                    "price": round(c, 4), "qty": qty, "note": "Day 1 close buy",
                })
            equity_curve.append({"date": display_date, "value": round(state.cash + state.holding_qty * c, 2)})
            continue

        # 1) 매도 확인
        star = state.star_pct
        quarter_limit = state.avg_price * (1 + star / 100)
        target_limit = state.avg_price * (1 + target_return_pct / 100)
        c_fill = c >= quarter_limit
        d_fill = h >= target_limit
        sold_today = False

        if c_fill or d_fill:
            qty_before = state.holding_qty
            sold_today = True

            if c_fill and d_fill:
                cycle_before = state.cycle_no
                qty_c = math.floor(qty_before * 0.25)
                qty_d = qty_before - qty_c
                total_proceeds = 0.0
                if qty_c > 0:
                    p, _ = state.apply_sell(c, qty_c)
                    total_proceeds += p
                if qty_d > 0:
                    p, _ = state.apply_sell(target_limit, qty_d)
                    total_proceeds += p
                trades.append({
                    "cycle": cycle_before, "date": display_date, "action": "sell",
                    "price": round(total_proceeds / qty_before, 4), "qty": qty_before,
                    "note": f"Full sell (quarter {qty_c} sh + target {qty_d} sh), restart",
                })
            elif c_fill:
                qty_c = math.floor(qty_before * 0.25)
                if qty_c > 0:
                    cycle_before = state.cycle_no
                    state.apply_sell(c, qty_c)
                    trades.append({
                        "cycle": cycle_before, "date": display_date, "action": "sell",
                        "price": round(c, 4), "qty": qty_c, "note": "Quarter (1/4) LOC sell",
                    })
                else:
                    sold_today = False
            else:
                qty_d = math.floor(qty_before * 0.75)
                if qty_d > 0:
                    cycle_before = state.cycle_no
                    state.apply_sell(target_limit, qty_d)
                    trades.append({
                        "cycle": cycle_before, "date": display_date, "action": "sell",
                        "price": round(target_limit, 4), "qty": qty_d, "note": "Target return limit sell (3/4)",
                    })
                else:
                    sold_today = False

        # 2) 매수/손절 진행 (오늘 매도가 없었고, 아직 포지션이 남아있을 때)
        if not sold_today and state.holding_qty > 0:
            if state.loss_cut_mode:
                # 분할 소진(T > N-1): 매수 대신 보유수량의 1/4을 그날 종가로 무조건(MOC) 매도
                qty_moc = math.floor(state.holding_qty * 0.25)
                if qty_moc > 0:
                    cycle_before = state.cycle_no
                    state.apply_sell(c, qty_moc)
                    trades.append({
                        "cycle": cycle_before, "date": display_date, "action": "sell",
                        "price": round(c, 4), "qty": qty_moc, "note": "Stop-loss (splits exhausted) MOC sell (1/4)",
                    })
            else:
                half_point = splits / 2
                amt = state.split_amount
                if state.t_value < half_point:
                    limit_a = state.avg_price
                    limit_b = state.avg_price * (1 + star / 100)
                    qty_a = 0
                    qty_b = 0
                    if c <= limit_a:
                        order_amt = min(amt / 2, state.cash)
                        qty_a = math.floor(order_amt / c)
                        if qty_a > 0:
                            state.apply_buy(c, qty_a)
                    if c <= limit_b:
                        order_amt = min(amt / 2, state.cash)
                        qty_b = math.floor(order_amt / c)
                        if qty_b > 0:
                            state.apply_buy(c, qty_b)
                    total_qty = qty_a + qty_b
                    if total_qty > 0:
                        if qty_a > 0 and qty_b > 0:
                            note = f"First-half buy (avg price {qty_a} sh + threshold {qty_b} sh)"
                        elif qty_a > 0:
                            note = f"First-half buy (avg price {qty_a} sh)"
                        else:
                            note = f"First-half buy (threshold {qty_b} sh)"
                        trades.append({
                            "cycle": state.cycle_no, "date": display_date, "action": "buy",
                            "price": round(c, 4), "qty": total_qty, "note": note,
                        })
                else:
                    limit_price = state.avg_price * (1 + star / 100)
                    if c <= limit_price:
                        order_amt = min(amt, state.cash)
                        qty = math.floor(order_amt / c)
                        if qty > 0:
                            state.apply_buy(c, qty)
                            trades.append({
                                "cycle": state.cycle_no, "date": display_date, "action": "buy",
                                "price": round(c, 4), "qty": qty, "note": "Second-half buy (threshold LOC)",
                            })

        equity_curve.append({"date": display_date, "value": round(state.cash + state.holding_qty * c, 2)})

    # 거래내역 각 행에 그 시점까지의 누적수량/평단가/수익률을 함께 표시하기 위해
    # trades 리스트(시간순)를 replay하며 스냅샷을 붙인다.
    running_qty = 0
    running_avg = 0.0
    running_buy = 0.0
    running_sell = 0.0
    for t in trades:
        if t["action"] == "buy":
            spend = t["price"] * t["qty"]
            running_avg = (running_avg * running_qty + spend) / (running_qty + t["qty"])
            running_qty += t["qty"]
            running_buy += spend
        else:
            running_sell += t["price"] * t["qty"]
            running_qty -= t["qty"]
            if running_qty <= 0:
                running_qty = 0
                running_avg = 0.0
        holding_value_now = running_qty * t["price"]
        t["qtyAfter"] = running_qty
        t["avgPriceAfter"] = round(running_avg, 4) if running_qty > 0 else None
        t["returnPctAfter"] = (
            round((running_sell + holding_value_now - running_buy) / running_buy * 100, 2)
            if running_buy > 0 else 0.0
        )

    last_close = bars[-1]["close"]
    total_buy = sum(t["price"] * t["qty"] for t in trades if t["action"] == "buy")
    total_sell = sum(t["price"] * t["qty"] for t in trades if t["action"] == "sell")
    total_buy_qty = sum(t["qty"] for t in trades if t["action"] == "buy")
    total_sell_qty = sum(t["qty"] for t in trades if t["action"] == "sell")
    holding_value = round(state.holding_qty * last_close, 2)
    eval_pnl = round((total_sell + holding_value) - total_buy, 2)
    return_pct = round(eval_pnl / total_buy * 100, 2) if total_buy > 0 else 0.0
    seed_return_pct = round(eval_pnl / seed * 100, 2) if seed > 0 else 0.0
    completed_cycles = state.cycle_no - 1

    # 지수 대비 비교: 같은 종목을 첫날 종가에 전량 매수해 그대로 들고 있었을 경우(매수 후 보유)
    first_close = bars[0]["close"]
    bh_shares = math.floor(seed / first_close) if first_close > 0 else 0
    bh_leftover = seed - bh_shares * first_close
    bh_curve = [
        {"date": eq["date"], "value": round(bh_leftover + bh_shares * bar["close"], 2)}
        for eq, bar in zip(equity_curve, bars)
    ]
    bh_final_value = bh_curve[-1]["value"] if bh_curve else seed
    bh_return_pct = round((bh_final_value - seed) / seed * 100, 2) if seed > 0 else 0.0

    def max_drawdown_pct(values):
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

    strategy_mdd = max_drawdown_pct([e["value"] for e in equity_curve])
    bh_mdd = max_drawdown_pct([e["value"] for e in bh_curve])
    alpha_pct = round(seed_return_pct - bh_return_pct, 2)

    return {
        "ticker": ticker,
        "version": version,
        "start": _display_date(bars[0]["date"]),
        "end": _display_date(bars[-1]["date"]),
        "seed": seed,
        "splits": splits,
        "targetReturnPct": target_return_pct,
        "totalBuyAmount": round(total_buy, 2),
        "totalSellAmount": round(total_sell, 2),
        "totalBuyQty": total_buy_qty,
        "totalSellQty": total_sell_qty,
        "evalPnl": eval_pnl,
        "returnPct": return_pct,
        "seedReturnPct": seed_return_pct,
        "completedCycles": completed_cycles,
        "holding": {
            "qty": state.holding_qty,
            "avgPrice": round(state.avg_price, 4) if state.holding_qty else None,
            "currentPrice": last_close,
            "value": holding_value,
            "tValue": round(state.t_value, 2),
            "lossCutMode": state.loss_cut_mode,
        },
        "mddPct": strategy_mdd,
        "alphaPct": alpha_pct,
        "benchmark": {
            "label": f"{ticker} Buy & Hold",
            "returnPct": bh_return_pct,
            "mddPct": bh_mdd,
            "equityCurve": bh_curve,
        },
        "trades": list(reversed(trades)),
        "equityCurve": equity_curve,
        "priceCurve": price_curve,
    }
