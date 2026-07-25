"""실전 무한매수법 포지션의 현황(평단가·T값·사이클·평가손익·다음 매매 추천)을
매매 기록에서 계산한다.

백테스트(backtest.py)와 달리 여기서는 사용자가 직접 체결한 매수/매도를 수동으로
입력하므로, LOC 체결 시뮬레이션 없이 입력된 거래를 시간순으로 재생(replay)해
평단가/보유수량/T값/사이클 상태를 파생한다. 편집·삭제 후에도 항상 일관되도록
매번 전체 거래 내역에서 처음부터 다시 계산한다.

T값 공식
--------
T = 사용한 시드 ÷ 1회 투자금
사용한 시드 = 이번 사이클 누적 매수액 − 누적 매도액 (순수 net 투입 자금).
즉 매도를 하면 그만큼 T가 곧바로 줄어든다(매도로 회수한 자금은 더 이상
"사용 중"이 아니므로). 사이클이 완전히 끝날 때(보유수량 0)만 0으로 리셋된다.
이 공식은 사용자가 제공한 실제 앱 화면(사용한 시드/1회 투자금/T/Star값)과
대조해 검증했다.

쿼터손절(분할소진) 트리거는 원문 "39<T≤40"(40분할 기준)을 일반화해
`T > 분할수 − 1`일 때로 판단한다.
"""

import math


def _threshold_pct(target_return_pct, splits, t_value):
    return target_return_pct * (1 - 2 * t_value / splits) if splits else 0.0


def compute_position_status(position, current_price=None):
    splits = position.splits or 1
    target = position.target_return_pct
    seed = position.seed

    cash = seed
    cycle_seed = seed
    net_deployed = 0.0
    holding_qty = 0
    avg_price = 0.0
    t_value = 0.0
    cycle_no = 1
    total_buy = 0.0
    total_sell = 0.0

    trades = sorted(position.trades, key=lambda t: (t.trade_date, t.id))

    for t in trades:
        split_amt = cycle_seed / splits if splits else 0

        if t.action == "buy":
            if holding_qty == 0:
                cycle_seed = cash
                net_deployed = 0.0
                split_amt = cycle_seed / splits if splits else 0

            spend = t.price * t.qty
            cash -= spend
            avg_price = (avg_price * holding_qty + spend) / (holding_qty + t.qty)
            holding_qty += t.qty
            total_buy += spend
            net_deployed += spend
        else:
            proceeds = t.price * t.qty
            cash += proceeds
            holding_qty = max(0, holding_qty - t.qty)
            total_sell += proceeds
            net_deployed -= proceeds

            if holding_qty == 0:
                cycle_no += 1
                avg_price = 0.0
                net_deployed = 0.0

        t_value = (net_deployed / split_amt) if (split_amt > 0 and holding_qty > 0) else 0.0

    split_amount = (cycle_seed / splits) if splits else 0.0
    used_seed = net_deployed if holding_qty > 0 else 0.0
    loss_cut_mode = holding_qty > 0 and t_value > (splits - 1)

    holding_value = round(holding_qty * current_price, 2) if (current_price and holding_qty) else None
    unrealized = (holding_value - holding_qty * avg_price) if (holding_value is not None) else None
    eval_pnl = round(total_sell - total_buy + (holding_value if holding_value is not None else holding_qty * avg_price), 2)
    return_pct = round(eval_pnl / total_buy * 100, 2) if total_buy > 0 else 0.0
    seed_return_pct = round(eval_pnl / seed * 100, 2) if seed > 0 else 0.0
    target_price = round(avg_price * (1 + target / 100), 4) if holding_qty else None
    star_pct = round(_threshold_pct(target, splits, t_value), 2) if holding_qty else None

    recommendation = _build_recommendation(splits, target, holding_qty, avg_price, t_value, loss_cut_mode)

    return {
        "id": position.id,
        "ticker": position.ticker,
        "version": position.version,
        "splits": splits,
        "targetReturnPct": target,
        "seed": seed,
        "usedSeed": round(used_seed, 2),
        "splitAmount": round(split_amount, 2),
        "cash": round(cash, 2),
        "holdingQty": holding_qty,
        "avgPrice": round(avg_price, 4) if holding_qty else None,
        "buyAmount": round(holding_qty * avg_price, 2) if holding_qty else 0.0,
        "targetPrice": target_price,
        "starPct": star_pct,
        "currentPrice": current_price,
        "holdingValue": holding_value,
        "unrealizedPnl": round(unrealized, 2) if unrealized is not None else None,
        "cycle": cycle_no,
        "tValue": round(t_value, 2),
        "lossCutMode": loss_cut_mode,
        "totalBuyAmount": round(total_buy, 2),
        "totalSellAmount": round(total_sell, 2),
        "evalPnl": eval_pnl,
        "returnPct": return_pct,
        "seedReturnPct": seed_return_pct,
        "tradeCount": len(trades),
        "recommendation": recommendation,
    }


def _build_recommendation(splits, target, holding_qty, avg_price, t_value, loss_cut_mode):
    if holding_qty == 0:
        return {
            "type": "start",
            "action": "buy",
            "orderType": "MOC",
            "note": "보유 중인 수량이 없습니다 — 새 사이클 1일차(원금/분할수 만큼 종가 매수)를 시작하세요",
        }

    if loss_cut_mode:
        qty_moc = math.floor(holding_qty * 0.25)
        return {
            "type": "loss_cut_moc_sell",
            "action": "sell",
            "orderType": "MOC",
            "qty": qty_moc,
            "note": (
                f"분할을 모두 소진했습니다(T={t_value:.2f}/{splits}) — "
                f"보유수량의 1/4인 {qty_moc}주를 오늘 종가로 무조건(MOC) 매도하는 것을 검토하세요"
            ),
            "targetSellPrice": round(avg_price * (1 + target / 100), 4),
        }

    threshold = _threshold_pct(target, splits, t_value)
    quarter_sell_price = round(avg_price * (1 + threshold / 100), 4)
    target_sell_price = round(avg_price * (1 + target / 100), 4)
    half_point = splits / 2

    if t_value < half_point:
        buy_price_a = round(avg_price, 4)
        buy_price_b = round(avg_price * (1 + threshold / 100), 4)
        return {
            "type": "normal_buy_dual",
            "action": "buy",
            "orderType": "LOC",
            "buyPriceA": buy_price_a,
            "buyPriceB": buy_price_b,
            "quarterSellPrice": quarter_sell_price,
            "targetSellPrice": target_sell_price,
            "note": (
                f"전반전(T={t_value:.2f}/{splits}) — 1회 매수금의 절반은 평단가 ${buy_price_a} 이하, "
                f"나머지 절반은 ${buy_price_b} 이하로 종가가 마감되면 매수(LOC)하세요"
            ),
        }

    buy_price = round(avg_price * (1 + threshold / 100), 4)
    return {
        "type": "normal_buy_single",
        "action": "buy",
        "orderType": "LOC",
        "buyPrice": buy_price,
        "quarterSellPrice": quarter_sell_price,
        "targetSellPrice": target_sell_price,
        "note": (
            f"후반전(T={t_value:.2f}/{splits}) — 1회 매수금 전액을 ${buy_price} 이하로 "
            f"종가가 마감되면 매수(LOC)하세요"
        ),
    }
