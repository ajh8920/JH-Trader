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
from datetime import date, datetime, timedelta

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
    달력일로 센다 - 원문의 "n일차"도 보통 주말 포함 경과일 감각으로 쓰인다.

    start_cycle(cash_override=position.seed)(2026-10-29 추가, 사용자 요청: "이 무한매수는
    단리로 가도록 하세요") - PositionState.start_cycle()의 기본 동작은 새 사이클의
    1회 분할매수금(cycle_seed) 기준을 그 시점의 cash(=시드+그동안 누적 실현손익)로
    잡아 사이클이 끝날 때마다 번 돈이 다음 사이클 분할금액에 자동으로 반영되는
    "복리" 방식이다. 이 실매매 포지션은 그 대신 매 사이클 분할금액을 항상 원래
    시드(position.seed) 고정 기준으로 계산하는 "단리"를 쓴다 - 실현손익은 cash에는
    그대로 누적되지만(현금 잔고 자체는 정확해야 하므로) 다음 사이클 매수 사이즈
    산정에는 반영하지 않는다. 백테스트(backtest.py)·실전현황 수기기록
    (live_tracker.py)은 건드리지 않았다 - 이 결정은 실매매 SOXL 포지션에만
    적용하라는 요청이었다."""
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
                state.start_cycle(cash_override=position.seed)
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
                state.start_cycle(cash_override=position.seed)
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


def current_cycle_orders(position):
    """position.orders(모든 사이클 누적, 상태 무관) 중 "현재 진행 중인 사이클"에
    속하는 것만 돌려준다(2026-10-29 추가, 사용자 요청: "무매탭의 주문 내역은
    현재 진행중인 사이클에 대해서만 관리"). 종료된 사이클은 히스토리 탭
    (list_closed_cycles)이 따로 보여주므로 "주문 내역"에서 또 보일 필요가
    없고, 사이클이 쌓일수록 목록이 한없이 길어지는 것도 막는다.

    기준은 "직전에 끝난 사이클의 마지막 체결 주문 id보다 큰 id 전부"다(현재
    사이클 자체의 체결 여부와 무관) - 그래야 새 사이클을 시작하려다 실패한
    주문(error/rejected, 아직 체결 전이라 build_filled_ledger엔 안 잡힘)도
    "직전 사이클 마감 이후" 시점이면 바로 보인다. 지금까지 한 번도 사이클이
    끝난 적 없는 포지션(체결 이력 전무 포함)이면 기준점이 없으므로 전부
    현재 사이클로 간주해 그대로 돌려준다."""
    state, _ = replay_position_state(position)
    ledger = build_filled_ledger(position)
    prev_ids = [r["orderId"] for r in ledger if r["cycleNo"] != state.cycle_no]
    cutoff_id = (max(prev_ids) + 1) if prev_ids else None
    if cutoff_id is None:
        return list(position.orders)
    return [o for o in position.orders if o.id >= cutoff_id]


def list_closed_cycles(position):
    """이 포지션에서 전량매도로 완결된 사이클만 뽑아 사이클별 요약(매입/매도
    총액·실현손익·수익률·기간)을 돌려준다(2026-10-29 추가, 사용자 요청: 다른
    무한매수 리포트 앱처럼 "종료된 사이클 히스토리"를 보고 싶다는 요청 - 무한매수
    탭 안에 히스토리 소분류 탭으로 추가).

    build_filled_ledger가 체결 하나하나를 재생하며 매긴 cycleNo로 그룹을 묶는다.
    cycle_no는 apply_sell이 보유수량을 0으로 만드는 순간 그 자리에서 +1 되므로
    (PositionState.apply_sell), 현재 진행 중인(아직 전량매도 안 된) 마지막 사이클을
    제외한 모든 cycle_no는 이미 전량매도로 끝난 것이 보장된다 - 별도의 "완료"
    플래그나 DB 컬럼 없이 기존 체결 기록만으로 히스토리를 계산할 수 있는 이유다.

    사이클별 체결 목록(trades)도 build_filled_ledger가 만든 행을 그대로 담아
    돌려준다(2026-10-29 추가, 사용자 요청: 히스토리에서 사이클을 클릭하면
    상세 거래내역이 보이게) - showAutoInfiniteFills가 쓰는 "실제 체결 내역"
    표와 완전히 같은 필드 모양이라 프런트에서 같은 렌더링 코드를 재사용한다."""
    state, _ = replay_position_state(position)
    current_cycle_no = state.cycle_no
    position_open = state.holding_qty > 0
    rows = build_filled_ledger(position)

    by_cycle = {}
    for r in rows:
        by_cycle.setdefault(r["cycleNo"], []).append(r)

    cycles = []
    for cycle_no, crows in sorted(by_cycle.items()):
        is_last = cycle_no == current_cycle_no
        if is_last and position_open:
            continue  # 아직 전량매도 안 된 진행 중인 사이클 - 히스토리 대상 아님
        buys = [r for r in crows if r["action"] == "buy"]
        sells = [r for r in crows if r["action"] == "sell"]
        if not sells:
            continue  # 매도 기록이 없으면(데이터 이상) 완료로 보지 않는다
        buy_total = sum(r["price"] * r["qty"] for r in buys if r["price"])
        sell_total = sum(r["price"] * r["qty"] for r in sells if r["price"])
        pnl = sum(r["realizedPnl"] or 0 for r in sells)
        cycles.append({
            "positionId": position.id,
            "ticker": position.ticker,
            "label": position.label or "",
            "cycleNo": cycle_no,
            "startDate": buys[0]["tradeDate"] if buys else crows[0]["tradeDate"],
            "endDate": sells[-1]["tradeDate"],
            "seed": position.seed,
            "splits": position.splits,
            "targetReturnPct": position.target_return_pct,
            "buyTotal": round(buy_total, 2),
            "sellTotal": round(sell_total, 2),
            "pnl": round(pnl, 2),
            "pnlPct": round(pnl / buy_total * 100, 2) if buy_total else None,
            "seedPnlPct": round(pnl / position.seed * 100, 2) if position.seed else None,
            "trades": crows,
        })
    return cycles


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


def cancel_order(order, client, dry_run=False):
    """AutoInfiniteOrder 한 건을 메리츠에서 실제로 취소하고 status를
    "cancelled"로 갱신한다(2026-11-05 추가, 사용자 요청: "하나라도 실패할
    경우 모든 매수를 취소하고 실패 메일을"). 부분체결된 주문은 남은
    수량(qty - filled_qty)만 취소 요청한다 - 이미 전량 체결됐으면 취소할
    대상이 없으므로 아무 것도 하지 않는다. 이미 거부/취소/체결 확인된
    주문(status != "submitted")은 호출부가 먼저 걸러야 한다 - 이 함수는
    그 상태를 다시 확인하지 않는다."""
    remaining_qty = order.qty - (order.filled_qty or 0)
    if remaining_qty <= 0:
        return
    oder_date = order.trade_date.replace("-", "")
    client.cancel_overseas_order(
        order.order_type, order.broker_order_no, oder_date, remaining_qty, dry_run=dry_run,
    )
    if not dry_run:
        order.status = "cancelled"
        db.session.commit()


def submit_order(position, client, action, order_type, qty, price, label, dry_run=True,
                  notify=True):
    """주문 한 줄(가이드가 만든 것이든, 1일차처럼 사용자가 직접 입력한 것이든)을
    계산된 가격 그대로 메리츠에 제출한다(dry_run=True면 API 호출 없이 미리보기만).
    성공/실패 모두 AutoInfiniteOrder에 기록한다(실패도 감사 로그로 남겨야 "왜
    매수가 안 됐는지" 나중에 추적 가능). cycle_no는 표시용으로 제출 시점 재생
    결과에서 가져온다.

    2026-10-22~11-05까지는 현재가 대비 괴리율이 크면(쿼터손절모드·폭락대비
    사다리 등으로 가격이 멀어짐) 메리츠가 거부하기 전에 미리 허용 한도가로
    당겨서(clamp) 제출했었다. 그런데 clamp된 실제 제출가가 알림 이메일에는
    반영되지 않는 버그가 있었고(이메일은 clamp 전 원래 계산값을 그대로 보여줌 -
    _run_auto_infinite_submissions가 price 지역변수를 재사용하지 않아서),
    실제 메리츠 앱의 미체결 주문과 이메일 내용이 서로 다르게 보이는 문제로
    이어졌다(2026-11-05 사용자 지적). 사용자가 clamp 자체를 없애고 계산된
    값 그대로 제출하되, 거부되면(메리츠 괴리율 하드리밋 20% 등) 그 즉시
    실패 메일을 보내는 쪽을 택했다 - 이제 제출가=이메일가=계산값이 항상
    같으므로 이 불일치가 구조적으로 생기지 않는다.

    notify=False(2026-10-23 추가, 사용자 요청: "매매 주문 메일링은 모든 매매내역을
    취합하여 한번만 보내도록") - 스케줄러가 가이드 10건 가까이 한 번에 제출할 때
    건마다 카카오+이메일이 따로 오면 알림이 연달아 쏟아진다. 스케줄러
    (_run_auto_infinite_submissions)는 notify=False로 호출하고, 루프가 끝난 뒤
    _notify_orders_submitted_batch()로 한 번만 모아 보낸다. 사용자가 가이드에서
    직접 한 줄씩 수동 제출할 때(기본값 notify=True)는 그 자리에서 바로 알림이
    오는 게 맞아 기존 동작을 유지한다. 실패 메일은 notify 설정과 무관하게 항상
    즉시 나간다(2026-11-05 추가, 사용자 요청: "주문에 실패할 경우 실패메일을
    즉각적으로") - 성공 알림은 스팸 방지로 모아 보내지만, 실패는 그날 다시
    시도할 기회가 있을 때 바로 알아야 의미가 있다."""
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
        if position.alert_email:
            import email_utils
            import email_templates
            action_ko = "매도" if action == "sell" else "매수"
            price_text = f"${price:,.2f}" if price else "시장가"
            reason = f'{label}({action_ko} {order_type.upper()} {price_text} x{qty}주) 제출 실패: {e}'
            html, body = email_templates.build_auto_infinite_failure_email(position, reason)
            email_utils.send_email(position.alert_email, f"[JH-Trader] {position.ticker} 주문 실패", body, html=html)
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
    # 2026-11-10부터 당일 조회(orders/today) 대신 기간 조회를 쓴다 - 체결확인이
    # 하루 실패하면 그날 체결이 영영 "submitted"로 남던 문제(실측: 11/09 주문
    # 체결분 누락) 때문. 30일보다 오래된 미체결 대기 건은 이미 만료된 당일
    # 주문이라 조회 범위를 거기까지 넓히지 않는다.
    start = max(min(o.trade_date for o in pending), (date.today() - timedelta(days=30)).isoformat())
    index = _broker_history_index(client, position.ticker, start)

    updated = 0
    newly_rejected = []
    for o in pending:
        key = (o.trade_date.replace("-", ""), o.broker_order_no)
        entry = index.get(key)
        if not entry:
            continue
        filled_qty, filled_price = entry["filled_qty"], entry["filled_price"]
        if entry["rejected"] and not filled_qty:
            o.status = "rejected"
            newly_rejected.append((o, entry["rejected"]))
        elif filled_qty >= o.qty or (filled_qty and key[0] < date.today().strftime("%Y%m%d")):
            # 전량 체결이거나, 지난 날짜의 부분체결(남은 수량은 당일 주문이라 이미 소멸)
            o.status = "filled"
            o.filled_qty = filled_qty
            o.filled_price = filled_price or o.price
        elif entry["cancelled_qty"] and not filled_qty:
            # 전량 취소(사람이 메리츠 앱에서 직접 취소한 경우 포함) - replay에서
            # 완전히 빠지도록 filled이 아닌 별도 상태로 남긴다. 메리츠는 취소를 원
            # 주문 행이 아니라 별도 "취소" 행(orgl_oder_no=원 주문번호)으로 내려준다.
            o.status = "cancelled"
        elif filled_qty:
            o.filled_qty = filled_qty
            o.filled_price = filled_price or o.price
        o.raw_response = json.dumps(entry["row"], ensure_ascii=False)
        updated += 1
    db.session.commit()
    return updated, newly_rejected


# 메리츠 호가유형코드(aspr_type_code) -> AutoInfiniteOrder.order_type
_ASPR_TYPE_TO_ORDER_TYPE = {"1": "market", "2": "limit", "5": "loc", "7": "moc"}


def _broker_history_index(client, ticker, start_date):
    """start_date(YYYY-MM-DD)~내일 기간 주문체결내역에서 이 티커만 골라, 원
    주문 단위로 체결/취소/거부를 모은다. 키: (주문일자 YYYYMMDD, 원 주문번호).

    사람이 메리츠 앱에서 주문을 "정정"하면 체결은 정정 행(oder_rctf_cncl_cls_code
    ="1", orgl_oder_no=원 주문번호)에 찍힌다(실측: 11/04 1831516 행) - 이 체결을
    원 주문 몫으로 돌려야 앱이 낸 주문이 체결됐다고 인식한다."""
    end = (date.today() + timedelta(days=1)).strftime("%Y%m%d")
    rows = client.get_overseas_orders_history(start_date.replace("-", ""), end)
    rows = [r for r in rows if str(r.get("iscd") or "").split(".")[0] == ticker]

    def _no(value):
        return str(int(float(value))) if value not in (None, "") else "0"

    parent = {}
    for r in rows:
        if r.get("oder_rctf_cncl_cls_code") in ("1", "2") and _no(r.get("orgl_oder_no")) != "0":
            parent[(r["oder_date"], _no(r["oder_no"]))] = (r["oder_date"], _no(r["orgl_oder_no"]))

    def _root(key):
        seen = set()
        while key in parent and key not in seen:
            seen.add(key)
            key = parent[key]
        return key

    index = {}
    for r in rows:
        key = _root((r["oder_date"], _no(r["oder_no"])))
        entry = index.setdefault(key, {"filled_qty": 0, "filled_amount": 0.0, "cancelled_qty": 0,
                                        "rejected": "", "row": r})
        code = r.get("oder_rctf_cncl_cls_code")
        if code == "2":
            entry["cancelled_qty"] += int(float(r.get("oder_dqty") or 0))
            continue
        if code == "0":
            entry["row"] = r
            if r.get("ovrs_stck_rfus_reas") or r.get("ovrs_stck_oder_stat_cls_code") in ("1", "2"):
                entry["rejected"] = r.get("ovrs_stck_rfus_reas") or "거부(사유 미기재)"
        qty = int(float(r.get("cntg_dqty") or 0))
        if qty:
            entry["filled_qty"] += qty
            entry["filled_amount"] += qty * float(r.get("cntg_crnc_prc") or 0)
    for entry in index.values():
        entry["filled_price"] = (entry["filled_amount"] / entry["filled_qty"]) if entry["filled_qty"] else None
    return index


def sync_from_broker_history(position, client, dry_run=True):
    """메리츠 기간 주문체결내역으로 이 포지션의 장부를 실계좌와 맞춘다(2026-11-10
    추가, 사용자 요청: "메리츠 api를 이용하여 무한매수 현황을 최신화").

    1) 앱이 낸 주문 중 아직 체결이 반영되지 않은 건을 실제 체결 결과로 갱신하고,
    2) 앱이 모르는 체결(사람이 메리츠 앱에서 직접 낸 주문)을 "filled" 주문으로
       새로 기록한다 - 실측 11/10 기준 앱 장부 19주 vs 실계좌 30주로, 차이 대부분이
       이 직접 주문 체결분이었다.

    2)는 이 티커를 운용하는 활성 포지션이 이 포지션 하나일 때만 한다(여럿이면
    어느 포지션 몫인지 알 수 없음). 조회 시작일은 앱이 처음 메리츠에 주문을 낸
    날이다 - 그 이전 체결은 이미 "실계좌 과거 체결(백필)"로 들어가 있어(브로커
    주문번호 없음) 다시 넣으면 이중 기록이 된다. dry_run=True면 DB를 바꾸지 않고
    바뀔 내용만 돌려준다."""
    dated = [o for o in position.orders if o.broker_order_no]
    if not dated:
        return {"error": "메리츠에 제출한 주문이 하나도 없어 동기화 기준일을 정할 수 없습니다"}
    start = min(o.trade_date for o in dated)
    index = _broker_history_index(client, position.ticker, start)
    by_key = {(o.trade_date.replace("-", ""), o.broker_order_no): o for o in dated}
    sole_position = AutoInfinitePosition.query.filter_by(
        ticker=position.ticker, exchange=position.exchange, is_active=True).count() == 1

    # 주문번호 없이 손으로 넣은 체결(백필 등)이 기준일 이후에도 있다 - 같은 날짜·
    # 구분·수량·체결가의 기록이 이미 있으면 그 체결로 보고 다시 넣지 않는다
    # (실측 11/10: 이걸 안 하면 30주 계좌가 36주로 이중 기록됨).
    unnumbered = [o for o in position.orders
                  if o.status == "filled" and not o.broker_order_no and o.trade_date >= start]

    # 손으로 넣을 때 주문일 대신 체결일(한국시간 다음날 새벽)을 쓰거나, 체결가 대신
    # 주문가를 적은 경우가 실제로 있어(실측: 10/22 주문 $83.33→체결 $82.11을
    # $83.33으로, 10/27 체결을 10/26로) 날짜 ±1일, 가격은 체결가·주문가 둘 다 비교한다.
    def _take_unnumbered(trade_date, action, qty, prices):
        d = date.fromisoformat(trade_date)
        near = {(d + timedelta(days=k)).isoformat() for k in (-1, 0, 1)}
        candidates = [o for o in unnumbered
                      if o.trade_date in near and o.action == action and (o.filled_qty or o.qty) == qty
                      and any(abs((o.filled_price or o.price or 0) - p) < 0.05 for p in prices if p)]
        if not candidates:
            return None
        best = min(candidates, key=lambda o: abs(date.fromisoformat(o.trade_date) - d))
        unnumbered.remove(best)
        return best

    updates, imports, matched_manual = [], [], []
    for key, entry in sorted(index.items()):
        qty = entry["filled_qty"]
        if not qty:
            continue
        o = by_key.get(key)
        price = round(entry["filled_price"], 4)
        row = entry["row"]
        trade_date = f"{key[0][:4]}-{key[0][4:6]}-{key[0][6:]}"
        action = "sell" if row.get("seln_byov_clcd") == "1" else "buy"
        order_price = float(row.get("ovrs_stck_oder_prc") or 0)
        if o:
            if o.status == "filled" and o.filled_qty == qty:
                continue
            # 앱 주문이 "submitted"로 멈춰 있는 동안 사람이 같은 체결을 손으로 넣어둔
            # 경우(실측: 11/04 주문 81 ↔ 백필 90) - 손 기록을 중복으로 빼야 이중 계산이 안 된다.
            dup = _take_unnumbered(trade_date, action, qty, (price, order_price, o.price))
            updates.append({"orderId": o.id, "date": o.trade_date, "label": o.label,
                            "from": o.status, "filledQty": qty, "filledPrice": price,
                            "duplicateManualOrderId": dup.id if dup else None})
            o.status, o.filled_qty, o.filled_price = "filled", qty, price
            if dup:
                dup.status = "duplicate"
            continue
        manual = _take_unnumbered(trade_date, action, qty, (price, order_price))
        if manual:
            matched_manual.append({"orderId": manual.id, "date": trade_date, "brokerOrderNo": key[1]})
            manual.broker_order_no = key[1]  # 다음 동기화부터 번호로 바로 매칭
            continue
        imports.append({"date": trade_date, "action": action, "qty": qty, "price": price,
                        "brokerOrderNo": key[1]})
        if sole_position:
            position.orders.append(AutoInfiniteOrder(
                trade_date=trade_date, action=action,
                order_type=_ASPR_TYPE_TO_ORDER_TYPE.get(row.get("aspr_type_code"), "limit"),
                label="메리츠 직접 주문(동기화)", qty=qty, price=order_price or price,
                status="filled", filled_qty=qty, filled_price=price, broker_order_no=key[1],
                raw_response=json.dumps(row, ensure_ascii=False),
            ))

    # dry_run이어도 실제로 바꿔본 뒤 재생 결과(적용 후 보유수량·평단가)를 계산하고
    # 마지막에 롤백한다 - 그래야 "적용하면 실계좌와 맞는지"를 미리 정확히 본다.
    # 재생은 (trade_date, id)로 정렬하므로 새 주문에 id가 붙도록 먼저 flush한다.
    db.session.flush()
    state, _ = replay_position_state(position)
    ledger = build_filled_ledger(position)
    broker_qty, broker_avg = _broker_holding_for_ticker(client, position.ticker, position.exchange)
    pending_updated = 0
    if dry_run:
        db.session.rollback()
    else:
        db.session.commit()
        pending_updated, _ = refresh_fill_status(position, client)  # 남은 취소/거부 반영
    return {"dryRun": dry_run, "since": start, "solePosition": sole_position,
            "updatedOrders": updates, "matchedManualOrders": matched_manual,
            "unmatchedManualOrders": [o.to_dict() for o in unnumbered],
            "importedFills": imports if sole_position else [],
            "skippedImports": [] if sole_position else imports,
            "pendingUpdated": pending_updated,
            "ledgerQty": state.holding_qty, "ledgerAvgPrice": round(state.avg_price, 4),
            "brokerQty": broker_qty, "brokerAvgPrice": broker_avg, "ledger": ledger}
