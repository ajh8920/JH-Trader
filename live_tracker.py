"""실전 무한매수법 포지션의 현황(평단가·T값·사이클·평가손익·다음 매매 추천)을
매매 기록에서 계산한다.

매수·매도·T값 공식은 `infinite_buying.py`(백테스트와 공유하는 공통 모듈)의
`PositionState`를 그대로 쓴다. 백테스트와 달리 여기서는 사용자가 직접 체결한
매수/매도를 수동으로 입력하므로, LOC 체결 시뮬레이션 없이 입력된 거래를
시간순으로 그대로 `PositionState`에 적용(재생)해 현재 상태를 파생한다. 편집·
삭제 후에도 항상 일관되도록 매번 전체 거래 내역에서 처음부터 다시 계산한다.
"""

from infinite_buying import PositionState, build_guide, get_version_defaults


def compute_position_status(position, current_price=None):
    splits = position.splits or 1
    target = position.target_return_pct
    seed = position.seed
    compound_mid_cycle = get_version_defaults(position.version)["compound_mid_cycle"]

    state = PositionState(seed, splits, target, compound_mid_cycle=compound_mid_cycle)
    total_buy = 0.0
    total_sell = 0.0

    trades = sorted(position.trades, key=lambda t: (t.trade_date, t.id))

    for t in trades:
        if t.action == "buy":
            if state.holding_qty == 0:
                state.start_cycle()
            state.apply_buy(t.price, t.qty)
            total_buy += t.price * t.qty
        else:
            state.apply_sell(t.price, t.qty)
            total_sell += t.price * t.qty

    used_seed = state.net_deployed if state.holding_qty > 0 else 0.0
    split_amount = state.split_amount

    holding_value = round(state.holding_qty * current_price, 2) if (current_price and state.holding_qty) else None
    unrealized = (holding_value - state.holding_qty * state.avg_price) if (holding_value is not None) else None
    eval_pnl = round(
        total_sell - total_buy + (holding_value if holding_value is not None else state.holding_qty * state.avg_price),
        2,
    )
    return_pct = round(eval_pnl / total_buy * 100, 2) if total_buy > 0 else 0.0
    seed_return_pct = round(eval_pnl / seed * 100, 2) if seed > 0 else 0.0
    target_price = round(state.avg_price * (1 + target / 100), 4) if state.holding_qty else None
    star_pct = round(state.star_pct, 2) if state.holding_qty else None

    recommendation = build_guide(state)

    return {
        "id": position.id,
        "ticker": position.ticker,
        "version": position.version,
        "splits": splits,
        "targetReturnPct": target,
        "seed": seed,
        "usedSeed": round(used_seed, 2),
        "splitAmount": round(split_amount, 2),
        "cash": round(state.cash, 2),
        "holdingQty": state.holding_qty,
        "avgPrice": round(state.avg_price, 4) if state.holding_qty else None,
        "buyAmount": round(state.holding_qty * state.avg_price, 2) if state.holding_qty else 0.0,
        "targetPrice": target_price,
        "starPct": star_pct,
        "currentPrice": current_price,
        "holdingValue": holding_value,
        "unrealizedPnl": round(unrealized, 2) if unrealized is not None else None,
        "cycle": state.cycle_no,
        "tValue": round(state.t_value, 2),
        "lossCutMode": state.loss_cut_mode,
        "totalBuyAmount": round(total_buy, 2),
        "totalSellAmount": round(total_sell, 2),
        "evalPnl": eval_pnl,
        "returnPct": return_pct,
        "seedReturnPct": seed_return_pct,
        "tradeCount": len(trades),
        "recommendation": recommendation,
    }
