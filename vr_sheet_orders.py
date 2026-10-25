# -*- coding: utf-8 -*-
"""구글 스프레드시트("VR 5.0.4 by.실력공식" 템플릿) 연동 - TQQQ 예약주문 사다리를
시트에서 읽어 메리츠 VR 전용 계좌(3059-8695-01, MERITZ_VR_* 키)로 실제 제출하고,
결과를 다시 시트에 반영한다(2026-10-25 추가, 사용자 요청: "엑셀 스프레드시트에
명령대로 예약 주문 및 시트에 기록을 진행할 수 있습니까?").

무한매수법(auto_infinite.py)과 완전히 별개의 전략·계좌·시트다 - VR(Value
Rebalancing)은 주차마다 "목표 자산가치(V)"와 실제 평가금의 차이를 사고팔아
맞추는 방식으로, 이 시트는 그 차이를 이미 "1주씩, N단계 지정가 사다리"로
환산해 "주문" 탭에 계산해둔다(원본 템플릿이 사람이 수동으로 보고 입력하도록
설계된 것을, 여기서는 API로 그대로 읽어 자동 제출한다).

시트 구조(사용자 확인, 2026-10-25):
  - "주문" 탭: B10:D45가 매수 사다리(수량/단가/Pool), I10:K45가 매도 사다리
    (순번/매수단가/매도단가) - 매수는 Pool 75% 한도로 보통 매도보다 적은
    줄 수만 채워진다(이 시트 기준 매수 12줄/매도 36줄).
  - "적립식" 탭 9행부터: Done(Y/N)/Week/Start/End/V Target/V min/V max/
    잔고 수량/마감 종가/마감 평가금/필요 거래/필요 거래액/거래액/적립금/
    Pool/P&L/P&L% - Done="N"인(아직 안 끝난) 행이 이번 주차, 주문 체결 후
    이 행의 "잔고 수량"·"거래액" 두 칸만 실제 값으로 갱신한다(사용자 지정).
"""
import json
import os

import gspread

from meritz_api import MeritzApiError, MeritzClient, OVERSEAS_EXCHANGES

TICKER = "TQQQ"
EXCHANGE = "NASDAQ"
ORDER_SHEET_NAME = "주문"
TRACKING_SHEET_NAME = "적립식"

# 제출한 예약주문의 oder_rsev_mst_no/date를 여기에 기록해둔다 - 예약주문
# 매수/매도 응답에는 이 둘이 포함되지만 예약주문 조회 응답에는 포함되지
# 않아(공식 문서 확인, 2026-10-25), 나중에 "이 줄이 체결됐으니 취소해야
# 한다"고 판단했을 때 취소 API에 넘길 식별자를 스스로 들고 있어야 한다.
RESERVED_ORDERS_LOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "vr_reserved_orders_log.json")


def _get_gspread_client():
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_JSON")
    if not creds_json:
        raise RuntimeError(".env에 GOOGLE_SHEETS_CREDENTIALS_JSON이 설정돼야 합니다")
    creds_dict = json.loads(creds_json)
    return gspread.service_account_from_dict(creds_dict)


def _open_spreadsheet():
    gc = _get_gspread_client()
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID")
    if not spreadsheet_id:
        raise RuntimeError(".env에 GOOGLE_SHEETS_SPREADSHEET_ID가 설정돼야 합니다")
    return gc.open_by_key(spreadsheet_id)


def read_order_ladder():
    """"주문" 탭에서 이번 주차의 매수/매도 사다리와 예약 시작일/종료일을 읽는다.
    반환: {"buys":[{"qty":1,"price":66.76}, ...], "sells":[...], "cycleLabel":
    "4주차", "startDate":"20261025", "endDate":"20261103"} - 날짜는 시트 6행의
    "예약 시작일"(J6)/"예약 종료일"(K6, "YYYY-MM-DD")을 그대로 메리츠 예약주문
    API가 요구하는 "YYYYMMDD"로 변환한다.

    B10:D45(매수: 수량/단가/Pool)는 "주문 순번"(I열)이 채워진 행까지만 유효한
    줄로 본다(그 아래는 다음 사이클들을 미리 보여주는 참고용 확장표라 이번
    주차 대상이 아님 - I열이 비면 멈춘다)."""
    sh = _open_spreadsheet()
    ws = sh.worksheet(ORDER_SHEET_NAME)
    values = ws.get_all_values()

    cycle_label = values[1][2] if len(values) > 1 and len(values[1]) > 2 else ""  # C2: "4주차 예약 주문"
    start_date_raw = values[5][9] if len(values) > 5 and len(values[5]) > 9 else ""   # J6
    end_date_raw = values[5][10] if len(values) > 5 and len(values[5]) > 10 else ""   # K6
    start_date = start_date_raw.replace("-", "")
    end_date = end_date_raw.replace("-", "")

    buys, sells = [], []
    # 10행(인덱스 9)부터 I열(인덱스 8, "주문 순번")이 빈 값이 나올 때까지.
    for row in values[9:]:
        if len(row) <= 8 or not row[8].strip():
            break
        buy_price = row[9].strip() if len(row) > 9 else ""   # J: 매수
        sell_price = row[10].strip() if len(row) > 10 else ""  # K: 매도
        if buy_price:
            buys.append({"qty": 1, "price": float(buy_price.replace(",", ""))})
        if sell_price:
            sells.append({"qty": 1, "price": float(sell_price.replace(",", ""))})
    return {"buys": buys, "sells": sells, "cycleLabel": cycle_label,
            "startDate": start_date, "endDate": end_date}


def preview_orders():
    """실제 제출 전 검토용 - 사다리를 읽기만 하고 아무것도 제출하지 않는다."""
    ladder = read_order_ladder()
    print(f"=== {ladder['cycleLabel']} 미리보기 ({ladder['startDate']}~{ladder['endDate']}) ===")
    print(f"매수 {len(ladder['buys'])}건:")
    for o in ladder["buys"]:
        print(f"  지정가 예약매수(기간잔량) {o['qty']}주 @ ${o['price']:.2f}")
    print(f"매도 {len(ladder['sells'])}건:")
    for o in ladder["sells"]:
        print(f"  지정가 예약매도(기간잔량) {o['qty']}주 @ ${o['price']:.2f}")
    return ladder


def _load_reserved_orders_log():
    if not os.path.exists(RESERVED_ORDERS_LOG_PATH):
        return []
    with open(RESERVED_ORDERS_LOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_reserved_orders_log(entries):
    os.makedirs(os.path.dirname(RESERVED_ORDERS_LOG_PATH), exist_ok=True)
    with open(RESERVED_ORDERS_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def submit_ladder(dry_run=True):
    """사다리 전체(매수+매도)를 VR 전용 메리츠 계좌로 해외주식 예약주문으로
    제출한다(일반 주문이 아니라 place_overseas_reserved_order - 시트의 "지정가
    잔량 주문"·예약 시작일/종료일을 그대로 oder_rsev_cls_code=기간잔량·
    oder_sta_date/oder_fin_date에 매핑). 각 건의 결과(성공/실패, 주문번호)를
    리스트로 돌려준다 - 중간에 한 건이 실패해도 나머지는 계속 제출한다(개별
    주문이라 서로 독립적).

    실제 제출(dry_run=False) 성공 건은 oder_rsev_mst_no/date를
    RESERVED_ORDERS_LOG_PATH에 같이 적어둔다 - 체결 후 cancel_filled_reservations가
    취소 대상을 찾을 때 이 로그로 역추적한다."""
    ladder = read_order_ladder()
    if not ladder["startDate"] or not ladder["endDate"]:
        raise RuntimeError("시트에서 예약 시작일/종료일을 읽지 못했습니다 - 제출 중단")
    client = MeritzClient(env_prefix="MERITZ_VR")
    results = []
    log_entries = _load_reserved_orders_log() if not dry_run else None
    for action, orders in (("buy", ladder["buys"]), ("sell", ladder["sells"])):
        for o in orders:
            try:
                res = client.place_overseas_reserved_order(
                    TICKER, EXCHANGE, o["qty"], action, o["price"],
                    ladder["startDate"], ladder["endDate"],
                    price_type="limit", dry_run=dry_run,
                )
                results.append({"action": action, "qty": o["qty"], "price": o["price"],
                                 "ok": True, "result": res})
                if not dry_run and res.get("oderRsevMstNo"):
                    log_entries.append({
                        "action": action, "qty": o["qty"], "price": o["price"],
                        "oderRsevMstNo": res["oderRsevMstNo"],
                        "oderRsevMstDate": res["oderRsevMstDate"],
                        "cancelled": False,
                    })
            except MeritzApiError as e:
                results.append({"action": action, "qty": o["qty"], "price": o["price"],
                                 "ok": False, "error": str(e)})
    if not dry_run:
        _save_reserved_orders_log(log_entries)
    return results


def cancel_filled_reservations(dry_run=True):
    """체결이 끝났는데도 아직 살아있는(취소되지 않은) 예약주문을 찾아 취소한다.

    "기간잔량" 방식 예약주문은 1주가 체결돼도 메리츠가 그 줄을 자동으로
    완전히 끝내지 않을 수 있어, 예약기간 내에 같은 가격을 다시 지나가면
    또 체결될 위험이 있다(2026-10-25 사용자 지적: "한번 매매가 이뤄진 금액으로
    다시 재매매 되지 않기 위함"). submit_ladder가 남긴 로그의 각 건을 예약주문
    조회 결과와 가격·수량·매수매도 구분으로 대조해, 누적체결수량(acml_cntg_qty)이
    예약수량(rsev_qty) 이상인데 아직 취소되지 않은 건(rsev_cncl_yn!='Y')만
    cancel_overseas_reserved_order로 취소한다. 로그에 없는 주문(예: 사람이
    메리츠 앱에서 직접 넣은 주문)은 대상으로 보지 않는다 - 우리가 낸 주문만
    책임진다."""
    log_entries = _load_reserved_orders_log()
    pending = [e for e in log_entries if not e.get("cancelled")]
    if not pending:
        return []

    ladder = read_order_ladder()
    client = MeritzClient(env_prefix="MERITZ_VR")
    rows = client.get_overseas_reserved_orders(ladder["startDate"], ladder["endDate"])

    side_code = {"buy": "2", "sell": "1"}  # 조회 응답의 seln_byov_clcd(매도1/매수2)

    results = []
    for entry in pending:
        match = next((
            r for r in rows
            if r.get("seln_byov_clcd") == side_code[entry["action"]]
            and abs(float(r.get("ovrs_stck_oder_prc") or 0) - entry["price"]) < 0.005
            and int(float(r.get("rsev_qty") or 0)) == entry["qty"]
        ), None)
        if not match:
            continue
        acml = int(float(match.get("acml_cntg_qty") or 0))
        rsev_qty = int(float(match.get("rsev_qty") or 0))
        already_cancelled = match.get("rsev_cncl_yn") == "Y"
        if acml < rsev_qty or already_cancelled:
            continue
        try:
            res = client.cancel_overseas_reserved_order(
                entry["oderRsevMstDate"], entry["oderRsevMstNo"], dry_run=dry_run,
            )
            results.append({**entry, "ok": True, "result": res})
            if not dry_run:
                entry["cancelled"] = True
        except MeritzApiError as e:
            results.append({**entry, "ok": False, "error": str(e)})
    if not dry_run:
        _save_reserved_orders_log(log_entries)
    return results


def update_tracking_row():
    """"적립식" 탭에서 Done="N"인(아직 안 끝난) 행을 찾아, 실계좌 현재
    잔고수량과 이번 주차 누적 거래액(현재 평가금 - 시작일 평가금 기준이
    아니라, 시작일 Pool과 현재 Pool의 차액 - 원문 "거래액" 정의를 그대로
    따름: 시작일 대비 실제로 오간 금액)을 갱신한다. 계산 공식은 시트
    자체의 "거래액" 컬럼이 이미 쓰고 있는 정의(적립금 + Pool 변화량)를
    역산하지 않고, 가장 직접적인 값인 "현재 잔고수량"만 실계좌에서 읽어오고
    거래액은 사용자가 시트에서 눈으로 검산할 수 있도록 호출부가 명시적으로
    넘기게 한다(자동 추정하면 템플릿의 복잡한 Pool 공식과 어긋날 위험)."""
    raise NotImplementedError(
        "거래액 계산식을 사용자와 먼저 확정해야 한다 - 아직 호출하지 말 것")


if __name__ == "__main__":
    preview_orders()
