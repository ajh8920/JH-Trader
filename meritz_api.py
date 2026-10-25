# -*- coding: utf-8 -*-
"""메리츠증권(Meritz) Open API 클라이언트 - JPEX 실전 자동매매 연동용.

엔드포인트/필드명은 모두 공식 저장소(github.com/meritz-securities/open-api,
openapi.json 및 meritz/data/catalog.json, 2026-10-21 확인)에서 그대로
가져왔다 - 추측으로 작성하지 않았다.

**kis_api.py와의 핵심 차이(반드시 인지할 것) - 메리츠는 모의투자 서버가
없다.** 앱키에 연결된 계좌가 곧 실계좌다(공식 저장소 README에 "모의계좌는
없습니다"라고 명시). 즉 이 클라이언트로 하는 모든 조회/주문은 처음부터
실제 계좌를 대상으로 한다 - 한투처럼 env="demo"로 가짜 돈 테스트를 할
방법이 아예 없다. 그래서 안전장치를 kis_api.py보다 한 단계 더 강하게
뒀다(아래 place_order 참고).

안전 설계:
  - place_order()의 dry_run 기본값은 True다(kis_api.py와 동일 원칙).
  - 신용(credit_orders_*)·공매도 관련 엔드포인트는 이 파일에 구현하지
    않는다(현금 매수/매도만) - "레버리지 영구 금지" 방침과 일치.
  - APP_KEY/APP_SECRET/계좌번호는 .env에서만 읽는다.
  - mac_address 헤더는 공식 client.py와 동일하게 uuid.getnode()로
    로컬 기기의 실제 MAC을 자동 계산한다(설정값이 아니다 - 없으면
    게이트웨이가 IGW50024로 거절한다고 공식 코드 주석에 명시돼 있음).
  - 토큰은 메모리에만 캐싱(kis_api.py와 같은 이유 - gunicorn 멀티워커
    환경에서 파일 캐싱의 워커 간 경쟁을 피함).

사용법(모의투자가 없으므로 조회 전용으로 충분히 익힌 뒤 dry_run으로
주문 미리보기까지 확인하고, 실제 주문은 반드시 소액으로 시작):
    from meritz_api import MeritzClient
    client = MeritzClient()
    price = client.get_price("005930")
    balance = client.get_holdings()
    result = client.place_order("005930", qty=1, side="buy", dry_run=True)
"""
import json
import os
import threading
import time
import uuid

import requests

_BASE_URL = "https://openapi.imeritz.com:9443"

# 주문구분(oder_cls_code) - 01=지정가, 05=시장가(공식 catalog.json 확인).
# 이 모듈은 01(지정가)만 쓴다 - kis_api.py와 같은 이유로 시장가를 막는다.
ODER_CLS_LIMIT = "01"

# 해외주식 주문구분코드(공식 catalog.json의 /trading/v1/overseas/orders/buy 확인) -
# 무한매수법이 요구하는 LOC/MOC를 메리츠가 네이티브로 지원한다(국내주식과 달리
# 워크어라운드가 필요 없다). oder_cond_cls_code는 이 표와 1:1로 고정되어 있다
# (01/05=0 조건없음, 31/33=2 개장전, 32/34=7 종가) - 호출부가 따로 넣지 않는다.
OVERSEAS_ORDER_TYPES = {
    "limit": {"oder_cls_code": "01", "oder_cond_cls_code": "0"},   # 지정가
    "market": {"oder_cls_code": "05", "oder_cond_cls_code": "0"},  # 시장가
    "loc": {"oder_cls_code": "32", "oder_cond_cls_code": "7"},     # LOC(장마감 지정가) - 무한매수 매수/매도 주력
    "moc": {"oder_cls_code": "34", "oder_cond_cls_code": "7"},     # MOC(장마감 시장가) - 손절모드 강제매도
}

# 해외거래소 코드 - 시세조회(mrkt_cls_code)와 주문(ovrs_exch_vndr_idno)이 서로
# 다른 코드체계를 쓴다(공식 catalog.json 확인) - 섞어 쓰면 안 된다. 무한매수법
# 대상 종목(SOXL/TQQQ/KORU 등)은 모두 NYSE Arca 상장이지만 메리츠 코드 목록에는
# Arca가 따로 없어 뉴욕(NYSE) 코드로 조회/주문되는지 실제 응답으로 확인 후 썼다
# (check_meritz_overseas_connection.py 참고).
OVERSEAS_EXCHANGES = {
    "NASDAQ": {"price": "OQ", "order": "0537"},
    "NYSE": {"price": "NY", "order": "0321"},
    "AMEX": {"price": "AX", "order": "0066"},
}


class MeritzApiError(Exception):
    """메리츠 API가 에러를 돌려줬을 때 - 호출부가 반드시 처리해야 한다.
    절대 조용히 삼키거나 자동 재시도하지 않는다(모의투자가 없어 실수의
    대가가 kis_api.py보다 크므로 더욱 엄격하게 적용)."""


def _mac_address() -> str:
    """공식 meritz/core/client.py의 mac_address() 구현과 동일 - 로컬
    기기의 실제 MAC을 uuid.getnode()로 얻어 12자리 대문자 16진수로
    변환한다(설정 불가 - 게이트웨이 필수 헤더)."""
    return "%012X" % uuid.getnode()


class MeritzCredentials:
    """env_prefix(기본 "MERITZ")로 어느 계좌의 키를 읽을지 고른다 - 메리츠
    Open API는 APP_KEY 하나당 계좌 하나만 연결되는 구조라(공식 문서: "접근
    토큰에 매핑된 계좌"), 계좌를 여러 개 쓰려면 계좌마다 별도로 발급받은
    APP_KEY/APP_SECRET이 필요하다(2026-10-25 확인 - VR(TQQQ) 전용 계좌
    추가하며 발견). 예: env_prefix="MERITZ_VR" -> MERITZ_VR_APP_KEY 등을 읽음."""

    def __init__(self, env_prefix="MERITZ"):
        self.app_key = os.environ.get(f"{env_prefix}_APP_KEY")
        self.app_secret = os.environ.get(f"{env_prefix}_APP_SECRET")
        self.account_no = os.environ.get(f"{env_prefix}_ACCOUNT_NO")
        if not (self.app_key and self.app_secret and self.account_no):
            raise MeritzApiError(
                f".env에 {env_prefix}_APP_KEY/{env_prefix}_APP_SECRET/"
                f"{env_prefix}_ACCOUNT_NO가 전부 설정돼야 합니다")


class MeritzClient:
    """모의투자가 없어 이 클라이언트를 만드는 순간부터 실제 계좌를 쓴다는
    점을 호출부가 항상 의식해야 한다. env_prefix는 MeritzCredentials로
    그대로 전달한다(여러 계좌 지원, 위 설명 참고)."""

    def __init__(self, timeout=15, env_prefix="MERITZ"):
        self.creds = MeritzCredentials(env_prefix=env_prefix)
        self.timeout = timeout
        self._lock = threading.Lock()
        self._token = None
        self._token_expires_at = 0.0
        self._mac = _mac_address()

    def _ensure_token(self):
        with self._lock:
            if self._token and time.time() < self._token_expires_at - 600:
                return self._token
            resp = requests.post(
                f"{_BASE_URL}/oauth2/token",
                headers={"content-type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials", "client_id": self.creds.app_key,
                      "client_secret": self.creds.app_secret, "scope": "login"},
                timeout=self.timeout,
            )
            body = resp.json()
            if resp.status_code != 200 or "access_token" not in body:
                raise MeritzApiError(f"토큰 발급 실패: {resp.status_code} {body}")
            self._token = body["access_token"]
            # expires_in이 명세상 문자열이지만 예제 응답은 숫자(604800=7일)로
            # 내려오기도 한다고 공식 문서에 적혀 있어 양쪽 다 처리한다.
            self._token_expires_at = time.time() + int(body.get("expires_in", 86400))
            return self._token

    def _headers(self, content_type="application/json; charset=utf-8"):
        return {
            "authorization": f"Bearer {self._ensure_token()}",
            "mac_address": self._mac,
            "content-type": content_type,
        }

    def _get(self, path, params):
        # EGW00201류 속도제한 재시도는 kis_api.py와 같은 원칙으로 둔다 -
        # 다만 메리츠 쪽 정확한 제한 코드는 아직 실측하지 않았으므로
        # (모의투자가 없어 함부로 연속 호출해 실측하지 않음) 재시도 없이
        # 바로 올린다 - 필요해지면 실측 후 추가한다.
        resp = requests.get(f"{_BASE_URL}{path}", headers=self._headers(),
                             params=params, timeout=self.timeout)
        body = resp.json()
        if resp.status_code != 200:
            raise MeritzApiError(f"{path} 조회 실패: {resp.status_code} {body}")
        return body

    # ── 시세조회 ──────────────────────────────────────────────────────
    def get_price(self, code: str) -> float:
        """code: 6자리 종목코드(접두 없음 - 주문 때만 'A' 접두가 붙는다)."""
        body = self._get("/market/v1/prices", {"mrkt_div_code": "J", "iscd": code})
        return float(body["psnt_prc"])

    # ── 잔고조회 ──────────────────────────────────────────────────────
    def get_holdings(self):
        """보유종목 평가 조회(/accounts/v1/me/holdings). 응답의 "data"
        배열을 그대로 돌려준다(필드: iscd/isnm/rmnd_qty/byng_unpr/psnt_prc
        등, 공식 catalog.json 응답 스키마 확인) - 국내(pros_trgt_cls_code
        ="1")/해외("J") 종목이 섞여 내려올 수 있다."""
        body = self._get("/accounts/v1/me/holdings", {"nthn_rmnd_incu_yn": "N"})
        return body.get("data") or []

    def get_deposit(self):
        """예수금 조회(/accounts/v1/me/deposit)."""
        return self._get("/accounts/v1/me/deposit", {})

    # ── 주문(현금 매수/매도만 - 신용/공매도 미구현) ───────────────────
    def place_order(self, code: str, qty: int, side: str, price: int | None = None,
                     dry_run: bool = True):
        """code: 6자리 종목코드. side: "buy"/"sell". price: 지정가(원,
        정수) - None이면 get_price()로 현재가 조회.

        dry_run=True(기본값)면 실제 API 호출을 하지 않는다. **메리츠는
        모의투자가 없으므로 dry_run=False는 반드시 실제 계좌·실제 돈으로
        나간다는 걸 호출부가 명확히 인지해야 한다** - kis_api.py의
        env="demo"처럼 안전하게 연습할 방법이 없다."""
        if side not in ("buy", "sell"):
            raise ValueError('side는 "buy" 또는 "sell"이어야 합니다')
        if qty <= 0:
            raise ValueError("qty는 1 이상이어야 합니다")
        if price is None:
            price = round(self.get_price(code))

        order_body = {
            "iscd": f"A{code}",  # 주문 API는 종목코드에 'A' 접두 필요(시세조회와 다름)
            "oder_unpr": price, "odqt": qty, "oder_cls_code": ODER_CLS_LIMIT,
            "oder_cond_cls_code": "0",  # 일반
            "orgl_oder_no": 0, "whol_rctf_cncl_yn": "N",  # 신규 주문 고정값
            "warn_cnfr_yn": "N", "exch_kind_code": "01",  # KRX
        }
        plan = {"side": side, "code": code, "qty": qty, "price": price, "dry_run": dry_run}
        if dry_run:
            return {**plan, "executed": False,
                    "message": "dry_run=True - 실제 주문 미실행(검증 모드)"}

        path = f"/trading/v1/orders/{side}"
        resp = requests.post(f"{_BASE_URL}{path}", headers=self._headers(),
                              data=json.dumps(order_body), timeout=self.timeout)
        body = resp.json()
        if resp.status_code != 200:
            raise MeritzApiError(f"주문 실패: {resp.status_code} {body}")
        return {**plan, "executed": True, "raw": body}

    # ── 해외주식(무한매수법 2.2버전 실매매용) ───────────────────────────
    def get_overseas_price(self, symbol: str, exchange: str) -> float:
        """symbol: 해외주식 심볼(접두 없음, 예: "SOXL"). exchange: OVERSEAS_EXCHANGES의
        키("NASDAQ"/"NYSE"/"AMEX"). 종목코드가 틀린 거래소로 조회되면 공식 문서에
        명시된 대로 오류 없이 iscd가 빈 값·가격이 0으로 내려오므로 그 경우를
        명시적으로 걸러낸다(조용히 가격 0을 반환하면 더 위험하다)."""
        mrkt_cls_code = OVERSEAS_EXCHANGES[exchange]["price"]
        body = self._get("/market/v1/overseas/prices",
                          {"mrkt_div_code": "OV", "mrkt_cls_code": mrkt_cls_code,
                           "iscd": symbol, "dely_rltm_cls_code": "0"})
        data = body.get("data") or {}
        if not data.get("iscd"):
            raise MeritzApiError(
                f"{symbol}을 거래소 {exchange}({mrkt_cls_code})로 조회했지만 "
                f"종목코드가 빈 값으로 내려왔습니다 - 거래소가 틀렸을 가능성이 높습니다 "
                f"(실측 확인: SOXL/KORU는 AMEX, TQQQ는 NASDAQ)")
        return float(data["prpr"])

    def get_overseas_holdings(self):
        """해외주식 잔고 조회(/accounts/v1/me/overseas/holdings). 필드: iscd/isnm/
        rmnd_dqty(보유수량)/avrg_unpr(평균단가)/psnt_prc(현재가)/byov_camt(매수금액) 등."""
        body = self._get("/accounts/v1/me/overseas/holdings", {})
        return body.get("data") or []

    def get_overseas_currencies(self):
        """해외주식 외화 예수금 조회(/accounts/v1/me/overseas/currencies). USD 행의
        frcr_amt가 해외주식 주문 가능 현금(달러)이다."""
        body = self._get("/accounts/v1/me/overseas/currencies", {})
        return body.get("data") or []

    def get_overseas_orders_today(self, cntg_cls_code="0"):
        """금일 해외주식 주문체결내역 조회(/trading/v1/overseas/orders/today).
        cntg_cls_code: "0"전체/"1"체결/"2"미체결. 체결수량(cntg_qty)·미체결수량
        (ncnt_qty)·주문상태구분코드(ovrs_stck_oder_stat_cls_code)로 체결 여부를
        판단한다 - place_overseas_order() 제출 후 체결 확인에 쓴다."""
        body = self._get("/trading/v1/overseas/orders/today",
                          {"cntg_cls_code": cntg_cls_code, "seln_byov_clcd": "0"})
        return body.get("data") or []

    def place_overseas_order(self, symbol: str, exchange: str, qty: int, side: str,
                              order_type: str, price: float | None = None,
                              dry_run: bool = True):
        """symbol/exchange: get_overseas_price()와 동일. side: "buy"/"sell".
        order_type: OVERSEAS_ORDER_TYPES의 키("limit"/"market"/"loc"/"moc") -
        무한매수법은 "loc"(매수·매도 기본)과 "moc"(손절모드 강제매도)만 쓴다.
        price: 지정가 계열(limit/loc)에는 필수, 시장가 계열(market/moc)에는
        무시하고 0을 보낸다(공식 문서 명시).

        dry_run=True(기본값)면 실제 API 호출을 하지 않는다 - 모의투자가 없으므로
        kis_api.py의 env="demo"처럼 안전하게 연습할 방법이 없다는 점을 호출부가
        반드시 인지해야 한다."""
        if side not in ("buy", "sell"):
            raise ValueError('side는 "buy" 또는 "sell"이어야 합니다')
        if order_type not in OVERSEAS_ORDER_TYPES:
            raise ValueError(f'order_type은 {list(OVERSEAS_ORDER_TYPES)} 중 하나여야 합니다')
        if qty <= 0:
            raise ValueError("qty는 1 이상이어야 합니다")

        order_cls = OVERSEAS_ORDER_TYPES[order_type]
        is_limit_family = order_cls["oder_cls_code"] in ("01", "32")
        if is_limit_family and price is None:
            raise ValueError(f'order_type="{order_type}"은 price가 필수입니다')

        order_body = {
            "ovrs_stck_symb_iscd": symbol,
            "ovrs_exch_vndr_idno": OVERSEAS_EXCHANGES[exchange]["order"],
            "oder_cls_code": order_cls["oder_cls_code"],
            "oder_cond_cls_code": order_cls["oder_cond_cls_code"],
            "odqt": qty,
            # round(price, 4)였다가 2026-10-22 실제 제출에서 전부 거부되는 걸 발견 -
            # 메리츠 해외주식 호가단위가 $0.01라 소수점 2자리를 넘는 가격은 전부
            # rsp_cd 8337("호가 단위를 확인해주세요")로 거부된다(실측). 무한매수법
            # 공식이 1/N 분할 등으로 계산 과정에서 소수점이 길어지는데, 그 값을
            # 그대로 보내면 안 되고 여기서 최종적으로 2자리로 반올림해야 한다.
            "ovrs_stck_oder_prc": round(price, 2) if is_limit_family else 0,
        }
        plan = {"side": side, "symbol": symbol, "exchange": exchange, "qty": qty,
                "order_type": order_type, "price": price, "dry_run": dry_run}
        if dry_run:
            return {**plan, "executed": False,
                    "message": "dry_run=True - 실제 주문 미실행(검증 모드)"}

        path = f"/trading/v1/overseas/orders/{side}"
        resp = requests.post(f"{_BASE_URL}{path}", headers=self._headers(),
                              data=json.dumps(order_body), timeout=self.timeout)
        body = resp.json()
        # rsp_cd 성공값은 엔드포인트마다 달라 일반화할 수 없다(실측: 조회 성공인데도
        # "5766"이 내려온 적 있음) - kis_api.py/meritz_api.py 전체와 같은 원칙으로
        # HTTP 상태코드만 성공 판정 기준으로 쓴다.
        if resp.status_code != 200:
            raise MeritzApiError(f"해외주식 주문 실패: {resp.status_code} {body}")
        return {**plan, "executed": True, "raw": body,
                "orderNo": (body.get("data") or {}).get("oder_no")}

    # ── 해외주식 예약주문(VR 전략 연동용, 2026-10-25 추가) ─────────────────────
    # place_overseas_order()는 당일 장중에만 유효한 일반 주문이다 - VR 전략처럼
    # "오늘부터 N일간, 가격이 닿으면 체결"되는 사다리 주문은 별도의 예약주문
    # API(공식 catalog.json 확인, tr_id=obtro_oder_112a)를 써야 한다.
    RESERVED_PRICE_TYPES = {
        "limit": "1", "market": "2", "loo": "4", "loc": "5",
        "moo": "6", "moc": "7",
    }
    # 예약방식(oder_rsev_cls_code) - VR 사다리는 "기간잔량"(기간 내 미체결 수량이
    # 계속 유효, 체결되면 그 줄만 소진) 방식을 쓴다 - 시트 원문의 "지정가 잔량
    # 주문"과 정확히 일치.
    RESERVED_METHOD_PERIOD_REMAINING = "2"

    def place_overseas_reserved_order(self, symbol: str, exchange: str, qty: int, side: str,
                                       price: float, start_date: str, end_date: str,
                                       price_type: str = "limit",
                                       method_code: str = RESERVED_METHOD_PERIOD_REMAINING,
                                       order_hour_code: str = "0",
                                       dry_run: bool = True):
        """해외주식 예약주문 등록(/trading/v1/overseas/reserved-orders/{buy|sell}).
        symbol/exchange/qty/side: place_overseas_order()와 동일. price_type:
        RESERVED_PRICE_TYPES의 키(기본 "limit"=지정가). start_date/end_date:
        "YYYYMMDD" 8자리(시트의 예약 시작일/종료일). order_hour_code
        (oder_rsev_hour_clcd): "0"장개시/"1"장마감10분전/"2"장마감30분전/
        "3"장개시30분후 - 매수는 공식 문서상 필수라 기본값 "0"을 쓴다(매도는
        선택이지만 동일하게 보내 일관성을 둠). dry_run=True(기본값)면 실제
        API 호출을 하지 않는다."""
        if side not in ("buy", "sell"):
            raise ValueError('side는 "buy" 또는 "sell"이어야 합니다')
        if price_type not in self.RESERVED_PRICE_TYPES:
            raise ValueError(f'price_type은 {list(self.RESERVED_PRICE_TYPES)} 중 하나여야 합니다')
        if qty <= 0:
            raise ValueError("qty는 1 이상이어야 합니다")
        if len(start_date) != 8 or len(end_date) != 8:
            raise ValueError('start_date/end_date는 "YYYYMMDD" 8자리여야 합니다')

        order_body = {
            "ovrs_stck_symb_iscd": symbol,
            "ovrs_exch_vndr_idno": OVERSEAS_EXCHANGES[exchange]["order"],
            "rsev_aspr_tpcd": self.RESERVED_PRICE_TYPES[price_type],
            "odqt": qty,
            # place_overseas_order()와 같은 이유(실측 거부 사례) - 호가단위 $0.01
            # 초과분은 전부 2자리로 반올림해서 보낸다.
            "ovrs_stck_oder_prc": round(price, 2),
            "oder_rsev_cls_code": method_code,
            "oder_rsev_hour_clcd": order_hour_code,
            "oder_sta_date": start_date,
            "oder_fin_date": end_date,
        }
        plan = {"side": side, "symbol": symbol, "exchange": exchange, "qty": qty,
                "price": price, "start_date": start_date, "end_date": end_date, "dry_run": dry_run}
        if dry_run:
            return {**plan, "executed": False,
                    "message": "dry_run=True - 실제 주문 미실행(검증 모드)"}

        path = f"/trading/v1/overseas/reserved-orders/{side}"
        resp = requests.post(f"{_BASE_URL}{path}", headers=self._headers(),
                              data=json.dumps(order_body), timeout=self.timeout)
        body = resp.json()
        if resp.status_code != 200:
            raise MeritzApiError(f"해외주식 예약주문 실패: {resp.status_code} {body}")
        data = body.get("data") or {}
        # 취소(cancel_overseas_reserved_order)에 필요한 두 값을 여기서 바로
        # 돌려준다 - 응답에 oder_rsev_mst_no는 포함되지만(공식 문서 확인)
        # oder_rsev_mst_date(예약 접수 일자)는 포함되지 않아, 접수 시점인
        # "지금"을 그대로 기록해 둔다(제출과 접수가 같은 날인 API 호출이라
        # 오차가 없다).
        return {**plan, "executed": True, "raw": body,
                "oderRsevMstNo": data.get("oder_rsev_mst_no"),
                "oderRsevMstDate": time.strftime("%Y%m%d")}

    def get_overseas_reserved_orders(self, start_date: str, end_date: str, cntg_cls_code="0"):
        """예약주문 조회(/trading/v1/overseas/reserved-orders, GET). start_date/
        end_date: "YYYYMMDD". 제출된 예약주문 목록과 상태를 확인할 때 쓴다.
        각 행의 acml_cntg_qty(누적체결수량)를 rsev_qty(예약수량)와 비교하면
        체결 완료 여부를, cncl_able_yn/rsev_cncl_yn으로 취소 가능/이미 취소
        여부를 알 수 있다(공식 문서 확인, 2026-10-25)."""
        body = self._get("/trading/v1/overseas/reserved-orders",
                          {"iqry_sta_date": start_date, "iqry_fin_date": end_date,
                           "seln_byov_clcd": "0"})
        return body.get("data") or []

    def cancel_overseas_reserved_order(self, oder_rsev_mst_date: str, oder_rsev_mst_no,
                                        dry_run: bool = True):
        """해외주식 예약주문 취소(/trading/v1/overseas/reserved-orders/cancel,
        tr_id=obtro_oder_113a, 공식 문서 확인 2026-10-25).

        "기간잔량"(RESERVED_METHOD_PERIOD_REMAINING) 방식 예약주문은 1주가
        체결돼도 메리츠 쪽에서 그 줄을 자동으로 완전히 끝내지 않고 예약기간이
        끝날 때까지 남아있을 수 있다(사용자 지적: "한번 매매가 이뤄진 금액으로
        다시 재매매 되지 않기 위함" - 체결된 가격에서 또 체결될 위험). 체결
        확인 후에는 이 함수로 반드시 명시적으로 취소해야 한다.

        oder_rsev_mst_date/oder_rsev_mst_no: place_overseas_reserved_order()의
        반환값(oderRsevMstDate/oderRsevMstNo)을 그대로 넘긴다."""
        plan = {"oderRsevMstDate": oder_rsev_mst_date, "oderRsevMstNo": str(oder_rsev_mst_no)}
        if dry_run:
            return {**plan, "executed": False,
                    "message": "dry_run=True - 실제 취소 미실행(검증 모드)"}
        cancel_body = {"oder_rsev_mst_date": oder_rsev_mst_date,
                        "oder_rsev_mst_no": str(oder_rsev_mst_no)}
        resp = requests.post(f"{_BASE_URL}/trading/v1/overseas/reserved-orders/cancel",
                              headers=self._headers(), data=json.dumps(cancel_body),
                              timeout=self.timeout)
        body = resp.json()
        if resp.status_code != 200:
            raise MeritzApiError(f"해외주식 예약주문 취소 실패: {resp.status_code} {body}")
        return {**plan, "executed": True, "raw": body}
