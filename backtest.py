"""라오어의 무한매수법 백테스트 엔진 (v2.2 / v3.0 / v4.0).

이 구현은 라오어 네이버 카페의 "SOXL 무한매수법 V2.2 방법론" 원문(2023.07.16)을
기준으로 삼는다. 세 버전 모두 아래 매수·매도·쿼터손절 골격을 공유하고, 분할수·
목표수익률 기본값과 복리(수익 재투자) 반영 시점만 다르다. 일봉 시가/종가/고가만
으로 시뮬레이션하며, LOC(지정가-종가) 주문은 "종가가 조건을 만족해야 그날 종가에
체결"로, 일반 지정가 주문은 "고가가 지정가 이상이면 그 지정가에 체결"로 근사한다.

공통 공식 (원문 기준, 실사용자 실거래 데이터로 검증됨)
--------------------------------------------------------
- 1회 매수금 = 이번 사이클 시드 / 분할수(N)  (사이클 동안 고정)
- 사용한 시드(순 투입액) = 이번 사이클 누적 매수액 − 누적 매도액
    · 매도를 하면 회수한 자금만큼 "사용한 시드"가 곧바로 줄어든다. 매도가
      있어도 줄지 않는 단순 누적매수액이 아니다 — 실제 사용자 데이터(시드/
      1회투자금/T값/☆% 전부 공개된 값)로 이 공식을 정확히 검증했다.
- T값(진행률) = 사용한 시드 / 1회 매수금
- ☆%(임계값) = 목표수익률 × (1 − 2T/N)
    · 원문의 SOXL 예시(N=40, 목표 12%)는 "12 − T×0.6"이고, 분할수가 a로 바뀌면
      "12 − T×0.6×(40/a)"가 된다는 것이 원문에 명시되어 있다. 이는 정확히
      목표수익률×(1−2T/a)와 동치이므로(T=0→+목표%, T=N→−목표%) 이 공식을
      그대로 일반화해 사용한다.
- 1일차(보유수량 0): "전일 종가×1.12"(=+목표%) 에 LOC 매수를 걸어 사실상 항상
  체결되도록 하는 것이 원문 설명이나, LOC는 체결가가 항상 그날 종가이므로 결과는
  "그날 종가로 매수"와 동일하다(급등으로 갭이 목표%를 초과하는 극단적 경우만 예외이며
  이 구현에서는 그 예외를 반영하지 않는다).
- 화면에 표시되는 날짜는 실제 미국 거래일 종가 기준 +1일(한국 투자자가 다음날 새벽에
  체결을 확인하는 관행)로 보정해 보여준다. 내부 계산은 실제 미국 거래일 기준으로 진행된다.

매일의 매수·매도 (원문 "매수편"/"매도편")
-----------------------------------------
1) 매도(전후반 공통): 누적수량의 1/4은 "평단×(1+☆%)"에 LOC 매도, 나머지 3/4은
   "평단×(1+목표수익률%)"에 지정가 매도. 두 조건이 모두 맞으면 전량 매도 후
   사이클 종료(다음 거래일부터 1일차 재시작). 하나만 맞으면 그 비율만 매도하고
   남은 수량은 같은 평단을 유지하며, 매도된 금액만큼 "사용한 시드"(T값)가 준다.
2) 매도가 없었다면 매수: T < N/2(전반전)이면 1회 매수금의 절반은 "평단가(0%)"에,
   절반은 "평단×(1+☆%)"에 각각 LOC 매수. T ≥ N/2(후반전)이면 1회 매수금 전체를
   "평단×(1+☆%)"에 LOC 매수.
3) 손절모드(분할소진, 원문 "39<T≤40"을 T>N−1로 일반화): 그날 매수 대신 보유수량의
   1/4을 종가로 무조건(MOC) 매도한다. 매도로 "사용한 시드"가 줄어들어 T가
   자연히 N−1 이하로 내려가면 다음날부터 정상 매수·매도로 복귀하고, 그래도
   여전히 T>N−1이면 다음날 또 1/4을 MOC 매도한다(자기 조정적이라 별도의
   10회 복구 사이클을 시뮬레이션하지 않는다).

버전별 차이
-----------
- v2.2: 40분할 / 목표수익률은 종목별 기본값(SOXL 12%, KORU 20%, 그 외 10%)을 쓴다.
        사이클이 완전히 끝나야만(전량 매도) 복리 반영.
- v3.0: 20분할 / 쿼터(1/4) 매도 등으로 중간에 이익이 나면 그 이익의 1/40을 즉시
        이후 1회 매수금에 더해 반영(복리를 더 빨리 태움). 손실이면 1회 매수금은 유지.
- v4.0: 20/30/40분할 중 선택. 매수·매도 공식은 v3.0과 동일하고 중간 복리도 반영한다.

실매매 전 반드시 결과를 직접 검증하세요.

지수(벤치마크) 비교
--------------------
같은 종목을 첫날 종가에 전량 매수해 그대로 보유했을 경우(매수후보유)를 벤치마크로
삼아 MDD(최대낙폭)와 알파(전략 수익률 − 벤치마크 수익률)를 함께 계산한다.
"""

import math
from datetime import datetime, timedelta

# 미국 거래일 종가는 한국 투자자 기준으로 보통 다음날 새벽에 확정되므로,
# 화면에 보여줄 날짜는 실제 미국 거래일 + 1일(한국 리포트 관행)로 표시한다.
def _display_date(us_trading_date_str):
    dt = datetime.strptime(us_trading_date_str, "%Y-%m-%d") + timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


VERSION_DEFAULTS = {
    "v2": {"splits": 40, "compound_mid_cycle": False},
    "v3": {"splits": 20, "compound_mid_cycle": True},
    "v4": {"splits": 20, "compound_mid_cycle": True},
}

# 종목별 목표수익률 고정값 (라오어 SOXL V2.2 원문 기준 12%, KORU는 변동성이 커 20%,
# 그 외 종목은 TQQQ 등 기존 무한매수법 표준값인 10%)
TICKER_TARGET_DEFAULTS = {
    "SOXL": 12.0,
    "KORU": 20.0,
}
DEFAULT_TARGET_RETURN = 10.0


def get_version_defaults(version):
    return VERSION_DEFAULTS.get(version, VERSION_DEFAULTS["v2"])


def get_target_default(ticker):
    return TICKER_TARGET_DEFAULTS.get((ticker or "").upper(), DEFAULT_TARGET_RETURN)


def fetch_daily_prices(ticker, start, end):
    import yfinance as yf

    end_inclusive = (datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    df = yf.download(ticker, start=start, end=end_inclusive, progress=False, auto_adjust=False)
    if df is None or df.empty:
        return []

    if hasattr(df.columns, "get_level_values") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)

    bars = []
    for idx, row in df.iterrows():
        try:
            o, c, h = float(row["Open"]), float(row["Close"]), float(row["High"])
        except (TypeError, ValueError):
            continue
        if o <= 0 or c <= 0 or h <= 0:
            continue
        bars.append({"date": idx.strftime("%Y-%m-%d"), "open": o, "close": c, "high": h})
    return bars


def run_infinite_buying(ticker, start, end, seed, splits, target_return_pct, version="v2"):
    bars = fetch_daily_prices(ticker, start, end)
    if not bars:
        return {"error": f'"{ticker}"의 시세 데이터를 가져올 수 없습니다 (티커 또는 기간을 확인하세요)'}

    defaults = get_version_defaults(version)
    compound_mid_cycle = defaults["compound_mid_cycle"]

    cash = seed
    cycle_seed = seed
    bonus_per_split = 0.0
    net_deployed = 0.0
    holding_qty = 0
    avg_price = 0.0
    t_value = 0.0
    cycle_no = 1

    trades = []
    equity_curve = []

    def split_amount():
        return cycle_seed / splits + bonus_per_split

    def threshold_pct(t):
        return target_return_pct * (1 - 2 * t / splits)

    def reset_cycle():
        nonlocal cycle_no, avg_price, t_value, net_deployed
        cycle_no += 1
        avg_price = 0.0
        t_value = 0.0
        net_deployed = 0.0

    for bar in bars:
        date, o, c, h = bar["date"], bar["open"], bar["close"], bar["high"]

        if holding_qty == 0:
            cycle_seed = cash
            bonus_per_split = 0.0
            net_deployed = 0.0
            amount = min(split_amount(), cash)
            qty = math.floor(amount / c)
            if qty > 0:
                spend = qty * c
                cash -= spend
                holding_qty = qty
                avg_price = c
                net_deployed = spend
                t_value = net_deployed / split_amount() if split_amount() > 0 else 0.0
                trades.append({
                    "cycle": cycle_no, "date": _display_date(date), "action": "buy",
                    "price": round(c, 4), "qty": qty, "note": "1일차 종가 매수",
                })
            equity_curve.append({"date": _display_date(date), "value": round(cash + holding_qty * c, 2)})
            continue

        # 1) 매도 확인 (T값은 원문 정의대로 "순 투입액(매수누적액-매도누적액)/1회매수액"
        # 이므로 매도할 때마다 곧바로 줄어든다)
        quarter_limit = avg_price * (1 + threshold_pct(t_value) / 100)
        target_limit = avg_price * (1 + target_return_pct / 100)
        c_fill = c >= quarter_limit
        d_fill = h >= target_limit
        sold_today = False

        if c_fill or d_fill:
            qty_before = holding_qty
            proceeds = 0.0
            cost_of_sold = 0.0

            if c_fill and d_fill:
                qty_c = math.floor(qty_before * 0.25)
                qty_d = qty_before - qty_c
                proceeds = qty_c * c + qty_d * target_limit
                cost_of_sold = qty_before * avg_price
                trades.append({
                    "cycle": cycle_no, "date": _display_date(date), "action": "sell",
                    "price": round(proceeds / qty_before, 4), "qty": qty_before,
                    "note": "전량매도(쿼터+목표가 동시 체결) 후 재시작",
                })
                holding_qty = 0
            elif c_fill:
                qty_c = math.floor(qty_before * 0.25)
                if qty_c > 0:
                    proceeds = qty_c * c
                    cost_of_sold = qty_c * avg_price
                    holding_qty = qty_before - qty_c
                    trades.append({
                        "cycle": cycle_no, "date": _display_date(date), "action": "sell",
                        "price": round(c, 4), "qty": qty_c, "note": "쿼터(1/4) LOC 매도",
                    })
            else:
                qty_d = math.floor(qty_before * 0.75)
                if qty_d > 0:
                    proceeds = qty_d * target_limit
                    cost_of_sold = qty_d * avg_price
                    holding_qty = qty_before - qty_d
                    trades.append({
                        "cycle": cycle_no, "date": _display_date(date), "action": "sell",
                        "price": round(target_limit, 4), "qty": qty_d, "note": "목표수익률 지정가 매도(3/4)",
                    })

            if proceeds > 0:
                cash += proceeds
                sold_today = True
                profit = proceeds - cost_of_sold
                if holding_qty == 0:
                    reset_cycle()
                else:
                    net_deployed -= proceeds
                    amt = split_amount()
                    t_value = net_deployed / amt if amt > 0 else 0.0
                    if compound_mid_cycle and profit > 0:
                        bonus_per_split += profit / 40

        # 2) 매수/손절 진행 (오늘 매도가 없었고, 아직 포지션이 남아있을 때)
        if not sold_today and holding_qty > 0:
            loss_cut_mode = t_value > splits - 1
            if loss_cut_mode:
                # 분할 소진(T > N-1): 매수 대신 보유수량의 1/4을 그날 종가로 무조건(MOC) 매도
                qty_moc = math.floor(holding_qty * 0.25)
                if qty_moc > 0:
                    proceeds = qty_moc * c
                    cash += proceeds
                    holding_qty -= qty_moc
                    net_deployed -= proceeds
                    trades.append({
                        "cycle": cycle_no, "date": _display_date(date), "action": "sell",
                        "price": round(c, 4), "qty": qty_moc, "note": "손절모드(분할소진) MOC 매도(1/4)",
                    })
                if holding_qty == 0:
                    reset_cycle()
                else:
                    amt = split_amount()
                    t_value = net_deployed / amt if amt > 0 else 0.0
            else:
                half_point = splits / 2
                amt = split_amount()
                if t_value < half_point:
                    for limit_price, note in (
                        (avg_price, "전반전 매수(평단가 LOC)"),
                        (avg_price * (1 + threshold_pct(t_value) / 100), "전반전 매수(임계값 LOC)"),
                    ):
                        if c <= limit_price:
                            order_amt = min(amt / 2, cash)
                            qty = math.floor(order_amt / c)
                            if qty > 0:
                                spend = qty * c
                                cash -= spend
                                avg_price = (avg_price * holding_qty + spend) / (holding_qty + qty)
                                holding_qty += qty
                                net_deployed += spend
                                t_value = net_deployed / amt if amt > 0 else 0.0
                                trades.append({
                                    "cycle": cycle_no, "date": _display_date(date), "action": "buy",
                                    "price": round(c, 4), "qty": qty, "note": note,
                                })
                else:
                    limit_price = avg_price * (1 + threshold_pct(t_value) / 100)
                    if c <= limit_price:
                        order_amt = min(amt, cash)
                        qty = math.floor(order_amt / c)
                        if qty > 0:
                            spend = qty * c
                            cash -= spend
                            avg_price = (avg_price * holding_qty + spend) / (holding_qty + qty)
                            holding_qty += qty
                            net_deployed += spend
                            t_value = net_deployed / amt if amt > 0 else 0.0
                            trades.append({
                                "cycle": cycle_no, "date": _display_date(date), "action": "buy",
                                "price": round(c, 4), "qty": qty, "note": "후반전 매수(임계값 LOC)",
                            })

        equity_curve.append({"date": _display_date(date), "value": round(cash + holding_qty * c, 2)})

    last_close = bars[-1]["close"]
    total_buy = sum(t["price"] * t["qty"] for t in trades if t["action"] == "buy")
    total_sell = sum(t["price"] * t["qty"] for t in trades if t["action"] == "sell")
    holding_value = round(holding_qty * last_close, 2)
    eval_pnl = round((total_sell + holding_value) - total_buy, 2)
    return_pct = round(eval_pnl / total_buy * 100, 2) if total_buy > 0 else 0.0
    seed_return_pct = round(eval_pnl / seed * 100, 2) if seed > 0 else 0.0
    completed_cycles = cycle_no - 1

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
        "evalPnl": eval_pnl,
        "returnPct": return_pct,
        "seedReturnPct": seed_return_pct,
        "completedCycles": completed_cycles,
        "holding": {
            "qty": holding_qty,
            "avgPrice": round(avg_price, 4) if holding_qty else None,
            "currentPrice": last_close,
            "value": holding_value,
            "tValue": round(t_value, 2),
            "lossCutMode": holding_qty > 0 and t_value > splits - 1,
        },
        "mddPct": strategy_mdd,
        "alphaPct": alpha_pct,
        "benchmark": {
            "label": f"{ticker} 매수후보유",
            "returnPct": bh_return_pct,
            "mddPct": bh_mdd,
            "equityCurve": bh_curve,
        },
        "trades": list(reversed(trades)),
        "equityCurve": equity_curve,
    }
