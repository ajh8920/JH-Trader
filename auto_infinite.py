# -*- coding: utf-8 -*-
"""무한매수법 2.2버전 실매매 자동화 - 메리츠증권 해외주식 계좌 연동.

infinite_buying.py(PositionState/build_guide, 백테스트·수기 실전현황과 공유하는
공식 모듈 - 쿼터손절모드 전체 상태기계 포함)를 그대로 재사용한다.

**상태 계산 방식(2026-10-22 설계) - live_tracker.py와 동일하게 "재생(replay)"한다.**
이 포지션에 실제로 체결(status="filled")된 AutoInfiniteOrder 기록을 시간순으로
처음부터 PositionState에 적용해(apply_buy/apply_sell) 현재 평단가·보유수량·
사이클·쿼터손절회차를 구한다 - 메리츠 실계좌 잔고를 그대로 믿지 않는다.

이전 버전은 실계좌 잔고를 직접 신뢰했었다(그게 "항상 진실의 원천"이라는 설계
원칙이었다). **그 원칙을 바꾼 이유**: 같은 티커로 여러 사이클/포지션을 동시에
운용할 수 있게 해달라는 요청을 반영하면서, 브로커 잔고는 "이 종목의 계좌 전체
합계"만 알려줄 뿐 그 중 어느 주문이 어느 논리적 포지션(사이클) 몫인지 구분하지
못한다는 한계가 드러났기 때문이다. 대신 여러 포지션의 재생 결과 합계를 실계좌
보유량과 주기적으로 대조하는 reconcile_holdings()로 안전장치를 둔다 - 앱 밖에서
수동 거래가 있었거나 체결 기록이 누락되면 여기서 경고로 걸러진다.

1일차(새 사이클 시작)는 시스템이 가격/수량을 추측하지 않는다 - 원문의 "+목표%
LOC"가 정확히 무엇 기준인지 불명확하고, 애초에 시작 여부·금액은 사용자가 정할
문제라서 사용자가 직접 입력해 제출하면 그 이후 회차부터 자동으로 이어진다
(infinite_buying.build_guide의 holding_qty==0 분기가 빈 주문 목록을 반환하는 것과
짝을 이룬다).
"""
import json
from datetime import date

from infinite_buying import PositionState, build_guide
from meritz_api import MeritzApiError, OVERSEAS_EXCHANGES
from models import AutoInfiniteOrder, db

# 실측 확인(2026-10-21, check_meritz_overseas 스크립트) - SOXL/KORU는 메리츠 코드상
# AMEX(아멕스, NYSE Arca 통합분류)로, TQQQ는 NASDAQ으로 조회/주문된다. 목록에
# 없는 티커는 add_position()에서 세 거래소를 순서대로 조회해 자동 감지한다.
TICKER_EXCHANGE_DEFAULTS = {"SOXL": "AMEX", "TQQQ": "NASDAQ", "KORU": "AMEX"}


def detect_exchange(client, ticker):
    """티커의 거래소를 안다면 바로 쓰고, 모르면 AMEX/NASDAQ/NYSE 순서로 실제
    시세조회를 날려 종목코드가 빈 값이 아닌 응답이 오는 거래소를 찾는다."""
    known = TICKER_EXCHANGE_DEFAULTS.get(ticker)
    if known:
        client.get_overseas_price(ticker, known)  # 틀렸으면 여기서 MeritzApiError
        return known
    for exchange in OVERSEAS_EXCHANGES:
        try:
            client.get_overseas_price(ticker, exchange)
            return exchange
        except MeritzApiError:
            continue
    raise MeritzApiError(f"{ticker}의 거래소를 AMEX/NASDAQ/NYSE 어디서도 찾지 못했습니다")


def get_available_cash_usd(client):
    """메리츠 해외주식 USD 예수금(계좌 전체 주문가능금액, 표시/대조용) -
    estm_frcr_amt(추정 외화예수금)를 쓴다. **실측(2026-10-21)으로 frcr_amt(단순
    외화예수금)가 아니라 이쪽이 실제 주문가능금액과 일치함을 확인했다** -
    frcr_amt=195.10이었지만 실제 주문가능금액은 estm_frcr_amt=2183.53이었다
    (최근 매매의 미결제 정산분이 frcr_amt에는 반영 안 되는 것으로 보임).

    주의: 이 값은 "계좌 전체" 기준이다. 포지션별 매수 한도는 이 값이 아니라
    replay_position_state()가 돌려주는 state.cash(그 포지션 자체의 시드 기준
    가상 잔고)를 쓴다 - 여러 포지션이 동시에 계좌 현금을 나눠 쓸 수 있어서다."""
    for row in client.get_overseas_currencies():
        if row.get("crcd") == "USD":
            return float(row["estm_frcr_amt"])
    return 0.0


def _broker_qty_for_ticker(client, ticker, exchange):
    """실계좌에서 이 티커의 실제 총 보유수량을 조회한다(reconcile_holdings 전용)."""
    order_vendor_id = OVERSEAS_EXCHANGES[exchange]["order"]
    for row in client.get_overseas_holdings():
        if row.get("ovrs_stck_symb_iscd") == ticker and row.get("ovrs_exch_vndr_idno") == order_vendor_id:
            return int(float(row["rmnd_dqty"]))
    return 0


def replay_position_state(position):
    """이 포지션에 체결된 주문을 시간순으로 재생해 현재 PositionState를 만든다
    (live_tracker.compute_position_status와 같은 패턴). 반환값: (state, 체결건수)."""
    state = PositionState(position.seed, position.splits, position.target_return_pct,
                           compound_mid_cycle=False)
    filled = sorted(
        (o for o in position.orders if o.status == "filled"),
        key=lambda o: (o.trade_date, o.id),
    )
    for o in filled:
        qty = o.filled_qty if o.filled_qty else o.qty
        price = o.filled_price if o.filled_price else o.price
        if o.action == "buy":
            if state.holding_qty == 0:
                state.start_cycle()
            state.apply_buy(price, qty)
        else:
            state.apply_sell(price or 0.0, qty)
    return state, len(filled)


def compute_live_status(position, client):
    """체결 이력 재생 + 실시간 시세로 포지션 현황과 오늘의 가이드를 계산한다.
    (매수 가능 현금은 이 포지션 자체의 재생된 가상 잔고를 쓴다 - 계좌 공용 현금이
    아니다. 계좌 전체 잔고는 compute_account_overview()에서 별도로 보여준다.)"""
    state, filled_count = replay_position_state(position)

    current_price = None
    try:
        current_price = client.get_overseas_price(position.ticker, position.exchange)
    except MeritzApiError:
        pass

    recommendation = build_guide(state)
    holding_value = round(state.holding_qty * current_price, 2) if (current_price and state.holding_qty) else None
    unrealized = round(holding_value - state.holding_qty * state.avg_price, 2) if holding_value is not None else None

    return {
        "id": position.id,
        "ticker": position.ticker,
        "exchange": position.exchange,
        "label": position.label or "",
        "splits": state.splits,
        "targetReturnPct": state.target_return_pct,
        "seed": position.seed,
        "cycleSeed": round(state.cycle_seed, 2),
        "cycle": state.cycle_no,
        "splitAmount": round(state.split_amount, 2),
        "ledgerCashUsd": round(state.cash, 2),
        "holdingQty": state.holding_qty,
        "avgPrice": round(state.avg_price, 4) if state.holding_qty else None,
        "currentPrice": current_price,
        "holdingValue": holding_value,
        "unrealizedPnl": unrealized,
        "tValue": round(state.t_value, 2),
        "starPct": round(state.star_pct, 2) if state.holding_qty else None,
        "lossCutMode": state.loss_cut_mode,
        "quarterCutRound": state.quarter_cut_round,
        "recommendation": recommendation,
        "isActive": position.is_active,
        "filledOrderCount": filled_count,
    }


def reconcile_holdings(client, positions):
    """같은 (티커, 거래소)를 쓰는 활성 포지션들의 재생 결과 합계를 실계좌 실제
    보유량과 대조한다. 여러 포지션이 한 종목을 동시에 운용할 때, 앱 밖에서 수동
    거래가 있었거나 체결 기록이 누락됐을 경우 여기서 걸러진다. 포지션이 없는
    티커는 비교하지 않는다(그 종목은 이 기능과 무관한 보유분일 수 있음)."""
    by_ticker = {}
    for p in positions:
        if not p.is_active:
            continue
        by_ticker.setdefault((p.ticker, p.exchange), []).append(p)

    warnings = []
    for (ticker, exchange), group in by_ticker.items():
        ledger_total = 0
        for p in group:
            state, _ = replay_position_state(p)
            ledger_total += state.holding_qty
        try:
            broker_qty = _broker_qty_for_ticker(client, ticker, exchange)
        except MeritzApiError:
            continue
        if ledger_total != broker_qty:
            warnings.append(
                f"{ticker}: 앱에 기록된 {len(group)}개 포지션의 합계 보유수량({ledger_total}주)이 "
                f"메리츠 실계좌 보유량({broker_qty}주)과 다릅니다 - 계좌에서 수동 거래가 있었는지, "
                f"또는 체결 확인이 누락된 주문이 있는지 확인하세요"
            )
    return warnings


def compute_account_overview(client, positions):
    cash = get_available_cash_usd(client)
    warnings = reconcile_holdings(client, positions)
    return {"availableCashUsd": round(cash, 2), "warnings": warnings}


def _notify_order_submitted(position, action, order_type, qty, price):
    """실제(dry_run=False) 주문이 메리츠에 제출된 직후 카카오톡으로 알린다 - 실제
    돈이 나가는 이벤트라 즉시성이 중요하다(paper_trading.py의 일일 요약 알림과
    달리 매 건마다 보낸다). 발송 실패는 notify()가 내부에서 삼키므로 여기서
    예외 처리를 따로 할 필요가 없다 - 주문 자체는 이미 끝난 뒤라 알림 실패가
    주문 결과에 영향을 주지 않는다."""
    import kakao_notify

    action_ko = "매도" if action == "sell" else "매수"
    price_text = f"${price:,.2f}" if price else "시장가(장마감)"
    title = f"[JH-Trader] {position.ticker} {action_ko} 주문 제출"
    lines = [
        f"{position.ticker}{(' · ' + position.label) if position.label else ''} - {action_ko} {qty}주",
        f"주문구분: {order_type.upper()} / 가격: {price_text}",
    ]
    kakao_notify.notify(title, lines, url=f"{kakao_notify.APP_BASE_URL}/")


def submit_order(position, client, action, order_type, qty, price, label, dry_run=True):
    """주문 한 줄(가이드가 만든 것이든, 1일차처럼 사용자가 직접 입력한 것이든)을
    실제로 메리츠에 제출한다(dry_run=True면 API 호출 없이 미리보기만). 성공/실패
    모두 AutoInfiniteOrder에 기록한다(실패도 감사 로그로 남겨야 "왜 매수가 안
    됐는지" 나중에 추적 가능). cycle_no는 표시용으로 제출 시점 재생 결과에서
    가져온다."""
    today = date.today().isoformat()
    state, _ = replay_position_state(position)
    cycle_no = state.cycle_no

    try:
        result = client.place_overseas_order(
            position.ticker, position.exchange, qty, action, order_type,
            price=price, dry_run=dry_run,
        )
    except MeritzApiError as e:
        order = AutoInfiniteOrder(
            position_id=position.id, cycle_no=cycle_no, trade_date=today,
            action=action, order_type=order_type, label=label, qty=qty, price=price,
            status="error", raw_response=str(e),
        )
        db.session.add(order)
        db.session.commit()
        raise

    if dry_run:
        return {"dryRun": True, "plan": result}

    order = AutoInfiniteOrder(
        position_id=position.id, cycle_no=cycle_no, trade_date=today,
        action=action, order_type=order_type, label=label, qty=qty, price=price,
        status="submitted", broker_order_no=str(result.get("orderNo") or ""),
        raw_response=json.dumps(result.get("raw"), ensure_ascii=False),
    )
    db.session.add(order)
    db.session.commit()

    _notify_order_submitted(position, action, order_type, qty, price)
    return {"dryRun": False, "order": order.to_dict()}


def refresh_fill_status(position, client):
    """금일 주문체결내역 조회로 이 포지션의 "submitted" 주문들을 체결/거부/미체결로
    갱신한다. 체결수량이 주문수량보다 적으면(부분체결) filled_qty만 반영하고
    status는 체결수량>0이면 "filled", 0이면 "submitted"(아직 미체결) 그대로 둔다."""
    pending = [o for o in position.orders if o.status == "submitted" and o.broker_order_no]
    if not pending:
        return 0
    by_order_no = {str(row.get("oder_no")): row for row in client.get_overseas_orders_today()}
    updated = 0
    for o in pending:
        row = by_order_no.get(o.broker_order_no)
        if not row:
            continue
        cntg_qty = int(float(row.get("cntg_qty") or 0))
        ncnt_qty = int(float(row.get("ncnt_qty") or 0))
        rfus_reas = row.get("ovrs_stck_rfus_reas") or ""
        if rfus_reas:
            o.status = "rejected"
        elif cntg_qty > 0 and ncnt_qty == 0:
            o.status = "filled"
            o.filled_qty = cntg_qty
            o.filled_price = float(row.get("cntg_crnc_prc") or 0) or o.price
        elif cntg_qty > 0:
            o.filled_qty = cntg_qty
            o.filled_price = float(row.get("cntg_crnc_prc") or 0) or o.price
        o.raw_response = json.dumps(row, ensure_ascii=False)
        updated += 1
    db.session.commit()
    return updated
