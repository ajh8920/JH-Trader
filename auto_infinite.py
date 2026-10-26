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
from datetime import date, datetime

from infinite_buying import PositionState, build_guide
from meritz_api import MeritzApiError, OVERSEAS_EXCHANGES
from models import AutoInfiniteOrder, AutoInfinitePosition, db

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


def _broker_holding_for_ticker(client, ticker, exchange):
    """실계좌에서 이 티커의 실제 보유수량·평단가를 조회한다(reconcile_holdings
    전용). 보유가 없으면 (0, None)."""
    order_vendor_id = OVERSEAS_EXCHANGES[exchange]["order"]
    for row in client.get_overseas_holdings():
        if row.get("ovrs_stck_symb_iscd") == ticker and row.get("ovrs_exch_vndr_idno") == order_vendor_id:
            return int(float(row["rmnd_dqty"])), float(row.get("avrg_unpr") or 0) or None
    return 0, None


def _broker_qty_for_ticker(client, ticker, exchange):
    """실계좌에서 이 티커의 실제 총 보유수량을 조회한다(reconcile_holdings 전용)."""
    return _broker_holding_for_ticker(client, ticker, exchange)[0]


def replay_position_state(position):
    """이 포지션에 체결된 주문을 시간순으로 재생해 현재 PositionState를 만든다
    (live_tracker.compute_position_status와 같은 패턴). 반환값: (state, 체결건수).

    state.cycle_start_date/cycle_day(2026-10-22 추가, 사용자 요청: "1회의 몇일차인지
    정보가 추가되었음 함") - 현재 사이클(아직 전량매도 안 된 회차)의 첫 매수
    체결일부터 오늘까지 경과한 "달력일" 수다. 거래일 캘린더 데이터가 없어
    영업일 기준으로는 못 세고(CLAUDE.md missing_filters와 같은 종류의 한계),
    달력일로 센다 - 원문의 "n일차"도 보통 주말 포함 경과일 감각으로 쓰인다."""
    state = PositionState(position.seed, position.splits, position.target_return_pct,
                           compound_mid_cycle=False)
    filled = sorted(
        (o for o in position.orders if o.status == "filled"),
        key=lambda o: (o.trade_date, o.id),
    )
    cycle_start_date = None
    for o in filled:
        qty = o.filled_qty if o.filled_qty else o.qty
        price = o.filled_price if o.filled_price else o.price
        if o.action == "buy":
            if state.holding_qty == 0:
                state.start_cycle()
                cycle_start_date = o.trade_date
            state.apply_buy(price, qty)
        else:
            state.apply_sell(price or 0.0, qty)
            if state.holding_qty == 0:
                cycle_start_date = None

    state.cycle_start_date = cycle_start_date
    state.cycle_day = None
    if cycle_start_date and state.holding_qty > 0:
        try:
            start_d = datetime.strptime(cycle_start_date[:10], "%Y-%m-%d").date()
            state.cycle_day = (date.today() - start_d).days + 1
        except ValueError:
            pass
    return state, len(filled)


def build_filled_ledger(position):
    """실제 체결(status="filled")된 주문만 시간순으로 재생해, 한 줄마다 그
    체결 직후의 보유수량·평단가·사이클을 같이 보여주는 "실제 매수/매도 내역"
    표를 만든다(2026-10-23 추가, 사용자 요청: "주문 내역과 반대로 실제로
    매수된 내역을 볼 수 있는 기능"). list_auto_infinite_orders가 보여주는
    "주문 내역"은 submitted/error/rejected/cancelled까지 전부 섞인 제출
    로그라 "결국 무엇이 실제로 체결됐는지"를 한눈에 보기 어렵다 - 이 함수는
    replay_position_state와 완전히 같은 재생 로직을 쓰되, 최종 상태 하나만
    돌려주는 대신 매 체결마다의 스냅샷을 쌓아 돌려준다(로직이 갈라지면 두
    화면의 숫자가 어긋나므로 반드시 같은 전이 규칙을 공유해야 한다)."""
    state = PositionState(position.seed, position.splits, position.target_return_pct,
                           compound_mid_cycle=False)
    filled = sorted(
        (o for o in position.orders if o.status == "filled"),
        key=lambda o: (o.trade_date, o.id),
    )
    rows = []
    for o in filled:
        qty = o.filled_qty if o.filled_qty else o.qty
        price = o.filled_price if o.filled_price else o.price
        realized_pnl = None
        # 이 거래가 "속한" 회차는 적용 전 cycle_no다 - apply_sell이 보유수량을
        # 0으로 만들면 그 안에서 바로 cycle_no를 올려버려서, 적용 후 값을 쓰면
        # "1회차를 마감시킨 매도"가 2회차로 표시되는 혼란이 생긴다.
        cycle_no = state.cycle_no
        if o.action == "buy":
            if state.holding_qty == 0:
                state.start_cycle()
            state.apply_buy(price, qty)
        else:
            _, realized_pnl = state.apply_sell(price or 0.0, qty)
        rows.append({
            "orderId": o.id, "tradeDate": o.trade_date, "cycleNo": cycle_no,
            "action": o.action, "qty": qty, "price": round(price, 2) if price else None,
            "realizedPnl": round(realized_pnl, 2) if realized_pnl is not None else None,
            "holdingQtyAfter": state.holding_qty,
            "avgPriceAfter": round(state.avg_price, 4) if state.holding_qty else None,
            "tValueAfter": round(state.t_value, 2),
        })
    return rows


def _mark_already_submitted_today(position, recommendation):
    """가이드 주문 중 오늘 이미 메리츠에 보낸(체결 여부 무관) 줄이 있으면
    `alreadySubmitted: True`를 달아 프런트가 다시 제출 못 하게 막는다
    (2026-10-22 추가 - 실사용 중 중복 매도 발견). replay_position_state는
    status="filled"만 반영해 "제출됨(미체결)" 주문은 모르기 때문에, 체결 전
    화면을 새로고침하면 방금 낸 주문과 똑같은 가이드 줄이 다시 "제출 가능"으로
    보이는 게 근본 원인 - label(가이드 줄마다 고유: "Buy (Avg Price)"/
    "Sell (Quarter 1/4)" 등)로 오늘자 주문을 대조한다. error/rejected/cancelled는
    실제로 안 나갔거나 취소된 것이므로 재시도 가능하게 제외한다."""
    today = date.today().isoformat()
    sent_labels = [
        o.label or "" for o in position.orders
        if o.trade_date == today and o.status in ("submitted", "filled")
    ]
    for order in recommendation.get("orders", []):
        label = order.get("label") or ""
        # 수동 제출은 가이드 label을 그대로 저장하지만, 스케줄러는
        # f"자동매매({label})"로 감싸서 저장한다(_run_auto_infinite_submissions) -
        # 부분 포함으로 양쪽 다 잡는다.
        if label and any(label in sent for sent in sent_labels):
            order["alreadySubmitted"] = True


def _overlay_broker_avg_price(position, client, state):
    """포지션이 이 (티커, 거래소)에 단 하나뿐이면, 평단가를 재생(replay) 계산
    대신 메리츠 실계좌의 avrg_unpr로 덮어쓴다(2026-10-23 추가, 사용자 질문:
    "현재 평단 정보를 api로 못가져오나요?"). 과거 수기 백필 체결가 하나하나의
    정확도에 기대지 않고 항상 실제 평단과 일치시키는 게 목적 - 평단가가
    틀리면 가이드의 모든 매수/매도 가격(avg_price 기반)이 같이 틀어지므로
    단순 표시 문제가 아니다. 포지션이 2개 이상 공유하면(동시에 여러
    사이클 운용) 브로커의 블렌디드 평단을 특정 포지션에 1:1로 못 당겨와
    건너뛴다 - 그 경우 reconcile_holdings()의 평단 대조 경고로만 알린다.
    보유수량이 서로 다르면(체결확인 지연 등) 신뢰할 수 없으니 역시 건너뛴다."""
    try:
        siblings = AutoInfinitePosition.query.filter_by(
            ticker=position.ticker, exchange=position.exchange, is_active=True,
        ).count()
        if siblings != 1 or state.holding_qty <= 0:
            return
        broker_qty, broker_avg = _broker_holding_for_ticker(client, position.ticker, position.exchange)
        if broker_qty == state.holding_qty and broker_avg:
            state.avg_price = broker_avg
    except MeritzApiError:
        pass


def compute_live_status(position, client):
    """체결 이력 재생 + 실시간 시세로 포지션 현황과 오늘의 가이드를 계산한다.
    (매수 가능 현금은 이 포지션 자체의 재생된 가상 잔고를 쓴다 - 계좌 공용 현금이
    아니다. 계좌 전체 잔고는 compute_account_overview()에서 별도로 보여준다.)"""
    state, filled_count = replay_position_state(position)
    _overlay_broker_avg_price(position, client, state)

    current_price = None
    try:
        current_price = client.get_overseas_price(position.ticker, position.exchange)
    except MeritzApiError:
        pass

    recommendation = build_guide(state, current_price=current_price)
    _mark_already_submitted_today(position, recommendation)
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
        "cycleDay": state.cycle_day,
        "cycleStartDate": state.cycle_start_date,
        "splitAmount": round(state.split_amount, 2),
        "ledgerCashUsd": round(state.cash, 2),
        "holdingQty": state.holding_qty,
        "avgPrice": round(state.avg_price, 4) if state.holding_qty else None,
        "buyAmount": round(state.holding_qty * state.avg_price, 2) if state.holding_qty else 0.0,
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
        "autoTradingEnabled": position.auto_trading_enabled,
        "autoLastRunDate": position.auto_last_run_date,
        "autoLastRunHadError": position.auto_last_run_had_error,
        "alertEmail": position.alert_email or "",
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
        # 평단가 대조는 더 이상 여기서 경고하지 않는다(2026-10-24) - 포지션이
        # 티커당 1개뿐인 흔한 경우는 compute_live_status의
        # _overlay_broker_avg_price가 화면에 보여줄 평단가를 이미 실계좌
        # 값으로 덮어써서 표시값 자체는 항상 정확하다. 그런데도 여기서 "재생
        # (replay) 계산값이 다르다"고 계속 경고하면, 정작 화면엔 맞는 숫자가
        # 떠 있는데 바로 위에 "다릅니다" 경고가 떠서 사용자가 혼란스러워했다
        # (실사용 피드백). 과거 백필 체결가 오차 자체는 사이클/T값 계산에는
        # 영향이 없고(avg_price에 의존하지 않음) 평단가는 이미 보정되므로,
        # 수량 불일치(ledger_total != broker_qty, 위 분기)만 실질적인 문제로
        # 남아 그대로 경고한다.
    return warnings


def compute_account_overview(client, positions):
    cash = get_available_cash_usd(client)
    warnings = reconcile_holdings(client, positions)
    return {"availableCashUsd": round(cash, 2), "warnings": warnings}


def _notify_order_submitted(position, action, order_type, qty, price, state=None):
    """실제(dry_run=False) 주문이 메리츠에 제출된 직후 이메일로 알린다 - 실제
    돈이 나가는 이벤트라 즉시성이 중요하다(paper_trading.py의 일일 요약 알림과
    달리 매 건마다 보낸다). 발송 실패를 내부에서 삼키므로 여기서 예외 처리를
    따로 할 필요가 없다 - 주문 자체는 이미 끝난 뒤라 알림 실패가 주문 결과에
    영향을 주지 않는다. 카카오 알림은 2026-10-23 사용자 요청으로 껐다(기능
    자체(kakao_notify.py)는 남겨둠 - 다시 켜고 싶으면 알려달라고 할 것).
    state는 호출부(submit_order)가 주문 직전에 이미 replay_position_state로 계산해둔
    것을 그대로 재사용한다(API를 다시 부르지 않음)."""
    action_ko = "매도" if action == "sell" else "매수"
    price_text = f"${price:,.2f}" if price else "시장가(장마감)"
    title = f"[JH-Trader] {position.ticker} {action_ko} 주문 제출"

    if position.alert_email:
        import email_utils
        import email_templates
        html, body = email_templates.build_auto_infinite_order_email(
            position, action, order_type, qty, price, state=state,
        )
        email_utils.send_email(position.alert_email, title, body, html=html)


def _notify_orders_submitted_batch(position, submitted_orders):
    """오늘 스케줄러가 제출한 주문 전체를 한 번에 모아 이메일로 알린다
    (2026-10-23 추가, 사용자 요청: "매매 주문 메일링은 모든 매매내역을 취합하여
    한번만 보내도록"). submitted_orders: [{"action","orderType","qty","price",
    "label"}...] - _run_auto_infinite_submissions가 notify=False로 제출하며
    쌓아둔 목록을 그대로 받는다. 수동 단건 제출(submit_auto_infinite_order
    라우트)은 이 함수를 쓰지 않고 기존처럼 _notify_order_submitted로 즉시 알림
    - 사용자가 그 자리에서 한 줄만 직접 눌러 보낸 거라 바로 확인받는 게 맞다.
    카카오 알림은 2026-10-23 사용자 요청으로 껐다(기능 자체는 남겨둠)."""
    if not submitted_orders:
        return
    title = f"[JH-Trader] {position.ticker} 자동매매 주문 제출 ({len(submitted_orders)}건)"

    if position.alert_email:
        import email_utils
        import email_templates
        state, _ = replay_position_state(position)
        html, body = email_templates.build_auto_infinite_batch_order_email(
            position, submitted_orders, state=state,
        )
        email_utils.send_email(position.alert_email, title, body, html=html)


# 현재가 대비 지정가 괴리율 상한(2026-10-22 추가) - 지인 실사용 팁: "무매 앱에
# 나온 금액이 T값 기준으로 계산되는데, T값이 높아지면(쿼터손절모드·폭락대비
# 사다리 등) 그 가격이 현재가에서 너무 멀어져 증권사가 'LOC 주문을 현재가
# 대비 일정% 이상 벗어난 가격으로는 못 걸게' 거부한다 - 그래서 걸 수 있는
# 최대치로 대신 주문을 건다"는 실전 대응을 그대로 반영했다.
#
# 2026-10-27 실측치로 교체 - 처음엔 공개 문서로 정확한 괴리율을 확인하지
# 못해 보수적으로 25%를 썼는데, 그 25%가 메리츠의 실제 한도보다 느슨해서
# "Buy (Threshold)" 주문이 10/23·10/26 두 번 거부당했다(메리츠 거부사유
# 원문: "LimitPrice {101.65 > 23.32% Limit 20.00% thru nbbo}" - 즉 NBBO
# 대비 20.00%가 메리츠의 실제 하드 리밋). 20%에 딱 맞추면 우리가 주문을
# 계산한 시점과 메리츠가 밤새 NBBO로 재검사하는 시점 사이에 가격이 조금만
# 움직여도 다시 걸릴 수 있어, 18%로 여유를 둔다.
MAX_PRICE_DEVIATION_PCT = 18.0


def _clamp_price_to_broker_band(client, position, action, order_type, price, current_price=None):
    """지정가/LOC 주문 가격이 현재가 대비 MAX_PRICE_DEVIATION_PCT를 넘게
    벗어나 있으면, 증권사가 거부할 가능성이 높으므로 걸 수 있는 최대치(현재가
    기준 한도)로 당겨서 반환한다. (조정됨 여부, 조정된 가격)을 돌려준다 -
    시세 조회 자체가 실패하면 조정하지 않고 원래 가격을 그대로 쓴다(주문
    제출 자체를 막지 않기 위함 - 실패하면 기존처럼 MeritzApiError로 남는다).

    current_price를 호출부가 이미 알고 있으면(예: _run_auto_infinite_submissions가
    가이드 전체를 계산할 때 한 번 조회해둔 값) 그대로 넘겨 받아 재조회하지
    않는다 - 가이드 10건을 매번 각각 시세 조회하면 불필요하게 API 호출이 늘어난다."""
    if order_type not in ("loc", "limit") or not price or price <= 0:
        return False, price
    if current_price is None:
        try:
            current_price = client.get_overseas_price(position.ticker, position.exchange)
        except MeritzApiError:
            return False, price
    if not current_price or current_price <= 0:
        return False, price

    # 2026-10-27 수정 - 원래는 "매수는 너무 쌀 때만, 매도는 너무 비쌀 때만"
    # 한쪽만 막았는데, 실제 거부 사례(메리츠 원문: "LimitPrice {101.65 > 23.32%
    # Limit 20.00% thru nbbo}")를 보면 메리츠의 호가 제한은 매수/매도 구분 없이
    # NBBO 기준 양방향으로 걸린다 - "임계값 매수"(Buy Threshold)처럼 현재가보다
    # 훨씬 높은 가격으로 거는 매수 주문(돌파 매수)이 바로 이 상한에 걸려
    # 거부됐다. 그래서 매수/매도 둘 다 상/하한을 같이 확인한다.
    floor_price = round(current_price * (1 - MAX_PRICE_DEVIATION_PCT / 100), 4)
    ceiling_price = round(current_price * (1 + MAX_PRICE_DEVIATION_PCT / 100), 4)
    if price < floor_price:
        return True, floor_price
    if price > ceiling_price:
        return True, ceiling_price
    return False, price


def submit_order(position, client, action, order_type, qty, price, label, dry_run=True,
                  current_price=None, skip_if_out_of_band=False, notify=True):
    """주문 한 줄(가이드가 만든 것이든, 1일차처럼 사용자가 직접 입력한 것이든)을
    실제로 메리츠에 제출한다(dry_run=True면 API 호출 없이 미리보기만). 성공/실패
    모두 AutoInfiniteOrder에 기록한다(실패도 감사 로그로 남겨야 "왜 매수가 안
    됐는지" 나중에 추적 가능). cycle_no는 표시용으로 제출 시점 재생 결과에서
    가져온다. current_price는 _clamp_price_to_broker_band로 그대로 전달한다.

    skip_if_out_of_band=True(폭락장 대비 추가매수 사다리 전용, 2026-10-22 추가) -
    사다리는 한 번에 여러 레벨(최대 30개)을 동시에 제출하는데, 밴드 밖인 레벨을
    전부 허용 한도가로 clamp하면 서로 다른 레벨들이 같은 가격 하나로 뭉쳐 중복
    주문이 된다(실측: 현재가 $92 기준 25% 밴드 $69.10 아래 레벨 5개가 전부
    $69.10 하나로 뭉침). 그래서 사다리 레벨은 clamp 대신 그날은 아예 건너뛴다 -
    LOC는 하루짜리 주문이라 다음날 그날의 새 현재가로 다시 계산되므로, 밴드
    안으로 들어오면 자연스럽게 다시 걸린다. 메인 주문(평단/목표%, 사다리가
    아닌 단일 주문)은 원래대로 clamp를 쓴다(중복 위험이 없음).

    notify=False(2026-10-23 추가, 사용자 요청: "매매 주문 메일링은 모든 매매내역을
    취합하여 한번만 보내도록") - 스케줄러가 가이드 10건 가까이 한 번에 제출할 때
    건마다 카카오+이메일이 따로 오면 알림이 연달아 쏟아진다. 스케줄러
    (_run_auto_infinite_submissions)는 notify=False로 호출하고, 루프가 끝난 뒤
    _notify_orders_submitted_batch()로 한 번만 모아 보낸다. 사용자가 가이드에서
    직접 한 줄씩 수동 제출할 때(기본값 notify=True)는 그 자리에서 바로 알림이
    오는 게 맞아 기존 동작을 유지한다."""
    today = date.today().isoformat()
    state, _ = replay_position_state(position)
    cycle_no = state.cycle_no

    clamped, adj_price = _clamp_price_to_broker_band(
        client, position, action, order_type, price, current_price=current_price)
    if clamped and skip_if_out_of_band:
        return {"dryRun": dry_run, "skipped": True,
                "reason": f"현재가 대비 {MAX_PRICE_DEVIATION_PCT}% 밴드 밖(${price:,.2f}) - 오늘은 건너뜀"}
    if clamped:
        label = f"{label} (현재가 제한으로 ${price:,.2f}→${adj_price:,.2f} 보정)"
        price = adj_price

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

    if notify:
        _notify_order_submitted(position, action, order_type, qty, price, state=state)
    return {"dryRun": False, "order": order.to_dict()}


def refresh_fill_status(position, client):
    """금일 주문체결내역 조회로 이 포지션의 "submitted" 주문들을 체결/거부/취소/
    미체결로 갱신한다. 체결수량이 주문수량보다 적으면(부분체결) filled_qty만
    반영하고 status는 체결수량>0이면 "filled", 0이면 "submitted"(아직 미체결)
    그대로 둔다.

    취소 인식(2026-10-22 추가, 실사용 중 중복매도 1건을 앱에서 직접 취소한 뒤
    발견) - 메리츠는 취소를 원 주문 행 자체에 플래그로 남기지 않고, 별도
    행(orgl_oder_no가 원 주문의 oder_no를 가리키고 cncl_cnfr_qty>0인 "취소
    확인" 행)으로 내려준다(실측: oder_rctf_cncl_cls_code="2"). 이 별도 행을
    못 알아보면 취소된 주문이 영원히 "submitted"(미체결 대기)로 남아 다음
    replay_position_state 때 실제로는 없는 주문을 있는 것처럼 계산하게 된다.

    반환값은 (갱신건수, 새로 거부된 주문 리스트)다(2026-10-27 추가) - 제출
    시점(17시)엔 멀쩡히 접수됐다가 메리츠가 밤새 호가 재검사해서 거부하는
    경우(실측 사례: "Buy (Threshold)" 주문이 NBBO 대비 20% 호가제한에 걸려
    거부)가 있는데, 지금까지는 이 거부가 다음날 체결확인(06~08시)에서야
    드러나는데도 그 시점에 알림이 전혀 안 나가서 사용자가 직접 메리츠 앱을
    보기 전까진 아무도 몰랐다. 호출부가 이 리스트로 바로 알림을 보낼 수
    있게 한다."""
    pending = [o for o in position.orders if o.status == "submitted" and o.broker_order_no]
    if not pending:
        return 0, []
    rows = client.get_overseas_orders_today()
    by_order_no = {str(row.get("oder_no")): row for row in rows}
    cancelled_qty_by_orgl = {}
    for row in rows:
        orgl = row.get("orgl_oder_no")
        cncl_cnfr_qty = int(float(row.get("cncl_cnfr_qty") or 0))
        if orgl and str(orgl) != "0" and cncl_cnfr_qty > 0:
            cancelled_qty_by_orgl[str(orgl)] = cancelled_qty_by_orgl.get(str(orgl), 0) + cncl_cnfr_qty

    updated = 0
    newly_rejected = []
    for o in pending:
        row = by_order_no.get(o.broker_order_no)
        if not row:
            continue
        cntg_qty = int(float(row.get("cntg_qty") or 0))
        ncnt_qty = int(float(row.get("ncnt_qty") or 0))
        rfus_reas = row.get("ovrs_stck_rfus_reas") or ""
        cancelled_qty = cancelled_qty_by_orgl.get(o.broker_order_no, 0)
        if rfus_reas:
            o.status = "rejected"
            newly_rejected.append((o, rfus_reas))
        elif cntg_qty > 0 and ncnt_qty == 0:
            o.status = "filled"
            o.filled_qty = cntg_qty
            o.filled_price = float(row.get("cntg_crnc_prc") or 0) or o.price
        elif cancelled_qty > 0 and cntg_qty == 0:
            # 전량 취소(사람이 메리츠 앱에서 직접 취소한 경우 포함) - replay에서
            # 완전히 빠지도록 filled이 아닌 별도 상태로 남긴다.
            o.status = "cancelled"
        elif cntg_qty > 0:
            o.filled_qty = cntg_qty
            o.filled_price = float(row.get("cntg_crnc_prc") or 0) or o.price
        o.raw_response = json.dumps(row, ensure_ascii=False)
        updated += 1
    db.session.commit()
    return updated, newly_rejected
