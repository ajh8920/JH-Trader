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
    def __init__(self):
        self.app_key = os.environ.get("MERITZ_APP_KEY")
        self.app_secret = os.environ.get("MERITZ_APP_SECRET")
        self.account_no = os.environ.get("MERITZ_ACCOUNT_NO")
        if not (self.app_key and self.app_secret and self.account_no):
            raise MeritzApiError(
                ".env에 MERITZ_APP_KEY/MERITZ_APP_SECRET/MERITZ_ACCOUNT_NO가 "
                "전부 설정돼야 합니다")


class MeritzClient:
    """모의투자가 없어 이 클라이언트를 만드는 순간부터 실제 계좌를 쓴다는
    점을 호출부가 항상 의식해야 한다."""

    def __init__(self, timeout=15):
        self.creds = MeritzCredentials()
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
