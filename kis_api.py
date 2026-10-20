# -*- coding: utf-8 -*-
"""한국투자증권(KIS) Open API 클라이언트 - JPEX 실전 자동매매 연동용.

엔드포인트/tr_id/필드명은 모두 공식 저장소(github.com/koreainvestment/
open-trading-api, 2026-10-20 확인)의 실제 코드에서 그대로 가져왔다 -
추측으로 작성하지 않았다. 신버전 현금주문 TR(TTTC0011U/0012U)을 쓴다 -
학습 자료에 구버전(TTTC0801U/0802U)이 많이 남아있어 생성형 AI가 구버전을
내놓는 사례가 많다는 걸 확인해 의도적으로 피했다(EXCG_ID_DVSN_CD 필드도
신버전에서만 요구됨).

안전 설계(실제 돈이 걸린 모듈이라 전부 의도적인 선택):
  - place_order()의 dry_run 기본값은 True다 - 호출부가 명시적으로
    dry_run=False를 넘기지 않으면 실제 주문이 절대 나가지 않는다.
  - 신용/공매도/대주 관련 TR은 이 파일에 아예 구현하지 않는다(현금
    매수/매도만) - "레버리지 영구 금지" 방침과 일치.
  - APP_KEY/APP_SECRET/계좌번호는 전부 .env에서만 읽는다(코드에 하드코딩
    금지, RULES.md R10과 동일 원칙).
  - 접근토큰은 메모리에만 캐싱한다(디스크에 평문 저장 안 함) - 공식
    예제(kis_auth.py)는 ~/KIS/config/에 파일로 저장하지만, 이 프로젝트는
    gunicorn 멀티워커 환경(RULES.md)이라 파일 캐싱은 워커 간 경쟁 문제를
    새로 만들 뿐이라 그 방식을 따르지 않았다 - 토큰은 길어야 24시간
    유효하므로 워커별로 각자 발급받아도 비용이 작다.

주의(2026-10-20 실측 확인) - 시세/잔고조회(GET)는 24시간 되지만,
**주문(order_cash)은 KRX 정규장 시간(평일 09:00~15:30)에만 접수된다.**
장외 시간에 주문을 넣으면 msg_cd="40580000"("모의투자 불가 종목입니다")
를 돌려주는데, 이 메시지가 종목/계정 문제처럼 보여 혼동하기 쉽다 -
실제로는 "지금 주문을 받을 수 없다"(장 마감)는 뜻이었다(한투 앱에서
직접 시도하니 "모의투자 장 종료"라는 더 명확한 메시지가 떴다). 같은
종목(005930)이든 다른 종목(000660)이든 장외 시간엔 전부 이 에러가
난다 - 종목을 바꿔도 해결 안 됨.

사용법(실전 투자 전 반드시 모의투자 서버로 먼저 검증):
    from kis_api import KisClient
    client = KisClient(env="demo")  # 모의투자. 실전은 env="real"
    price = client.get_price("005930")
    balance = client.get_balance()
    result = client.place_order("005930", qty=1, side="buy", dry_run=True)
"""
import json
import os
import threading
import time
from dataclasses import dataclass

import requests

# 실전/모의 서버 주소 - 공식 kis_auth.py의 _setTRENV 주석에서 그대로 확인.
_BASE_URL = {
    "real": "https://openapi.koreainvestment.com:9443",
    "demo": "https://openapivts.koreainvestment.com:29443",
}

# 현금 매수/매도 tr_id(신버전, 2026년 기준 공식 저장소 코드 확인) - 매도가
# 0011, 매수가 0012라는 점이 직관과 반대라 주석으로 못박아둔다.
_ORDER_TR_ID = {
    ("real", "sell"): "TTTC0011U", ("real", "buy"): "TTTC0012U",
    ("demo", "sell"): "VTTC0011U", ("demo", "buy"): "VTTC0012U",
}
_BALANCE_TR_ID = {"real": "TTTC8434R", "demo": "VTTC8434R"}
_PRICE_TR_ID = "FHKST01010100"  # 실전/모의 공통(공식 예제 확인)

# 주문구분(ORD_DVSN) - 00=지정가, 01=시장가, 02=조건부지정가(공식 문서/
# 공개 코드 공통 확인). 이 모듈은 00(지정가)만 쓴다 - 시장가는 체결가를
# 통제할 수 없어 실전 자동매매 1차 단계에서는 의도적으로 막는다.
ORD_DVSN_LIMIT = "00"


class KisApiError(Exception):
    """KIS API가 에러를 돌려줬을 때(주문 거부 등) - 호출부가 반드시
    처리해야 한다. 절대 조용히 삼키거나 자동 재시도하지 않는다(RULES.md
    원칙과 같음 - 실제 돈이 걸린 호출은 사람이 확인하는 게 기본값)."""


@dataclass
class KisCredentials:
    app_key: str
    app_secret: str
    account_no: str  # CANO(8자리 계좌번호 앞부분)
    account_product_code: str  # ACNT_PRDT_CD(2자리, 보통 "01")

    @classmethod
    def from_env(cls, env: str):
        """env("real"/"demo")별로 별도 .env 키를 쓴다 - 실전/모의 키를
        혼동해서 잘못된 서버에 실전 키를 보내는 사고를 막기 위함."""
        prefix = "KIS_REAL" if env == "real" else "KIS_DEMO"
        app_key = os.environ.get(f"{prefix}_APP_KEY")
        app_secret = os.environ.get(f"{prefix}_APP_SECRET")
        account = os.environ.get(f"{prefix}_ACCOUNT_NO")
        if not (app_key and app_secret and account):
            raise KisApiError(
                f".env에 {prefix}_APP_KEY/{prefix}_APP_SECRET/{prefix}_ACCOUNT_NO가 "
                f"전부 설정돼야 합니다 (env={env})")
        if "-" in account:
            cano, prdt = account.split("-", 1)
        else:
            cano, prdt = account[:8], account[8:] or "01"
        return cls(app_key=app_key, app_secret=app_secret,
                    account_no=cano, account_product_code=prdt)


class KisClient:
    """env="demo"(모의투자)/"real"(실전투자). 실전 키로 호출할 때마다
    이 클래스가 명시적으로 env="real"을 요구하므로, 기본값으로 실수로
    실전에 주문이 나가는 경로는 없다."""

    def __init__(self, env="demo", creds: KisCredentials | None = None, timeout=10):
        if env not in ("real", "demo"):
            raise ValueError('env는 "real" 또는 "demo"여야 합니다')
        self.env = env
        self.base_url = _BASE_URL[env]
        self.creds = creds or KisCredentials.from_env(env)
        self.timeout = timeout
        self._lock = threading.Lock()
        self._token = None
        self._token_expires_at = 0.0

    # ── 인증 ──────────────────────────────────────────────────────────
    def _ensure_token(self):
        """토큰은 메모리에만 캐싱, 만료 10분 전부터 선제 재발급(공식
        예제는 1일 유효라고 하지만 여유를 둔다). 한투는 토큰 발급 자체에
        분당 호출 제한이 있다는 공식 안내가 있어 락으로 동시 재발급을
        막는다(gunicorn 멀티워커라도 프로세스별로 각자 캐싱하므로 워커
        간 경쟁은 없음 - 같은 워커 내 스레드 경쟁만 방지)."""
        with self._lock:
            if self._token and time.time() < self._token_expires_at - 600:
                return self._token
            resp = requests.post(
                f"{self.base_url}/oauth2/tokenP",
                headers={"Content-Type": "application/json"},
                data=json.dumps({
                    "grant_type": "client_credentials",
                    "appkey": self.creds.app_key,
                    "appsecret": self.creds.app_secret,
                }),
                timeout=self.timeout,
            )
            body = resp.json()
            if resp.status_code != 200 or "access_token" not in body:
                raise KisApiError(f"토큰 발급 실패: {resp.status_code} {body}")
            self._token = body["access_token"]
            # expires_in(초) 응답 필드 - 없으면 보수적으로 12시간만 신뢰.
            self._token_expires_at = time.time() + int(body.get("expires_in", 43200))
            return self._token

    def _headers(self, tr_id, extra=None):
        h = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "text/plain",
            "authorization": f"Bearer {self._ensure_token()}",
            "appkey": self.creds.app_key,
            "appsecret": self.creds.app_secret,
            "tr_id": tr_id,
            "custtype": "P",  # 개인
        }
        if extra:
            h.update(extra)
        return h

    def _get(self, path, tr_id, params):
        # EGW00201(초당 거래건수 초과)만 짧게 재시도한다 - 모의투자 서버가
        # 실전보다 훨씬 낮은 호출 제한을 둬서(공식 저장소 주석 확인) 연달아
        # 여러 조회를 하면 쉽게 걸린다. 그 외 에러는 절대 재시도하지 않고
        # 바로 올린다(주문 실패 등을 조용히 숨기지 않는다는 원칙).
        for attempt in range(3):
            resp = requests.get(f"{self.base_url}{path}", headers=self._headers(tr_id),
                                 params=params, timeout=self.timeout)
            body = resp.json()
            if resp.status_code == 200 and body.get("rt_cd") == "0":
                return body
            if body.get("msg_cd") == "EGW00201" and attempt < 2:
                time.sleep(1.0)
                continue
            raise KisApiError(f"{path} 조회 실패: {resp.status_code} {body}")

    # ── 시세조회 ──────────────────────────────────────────────────────
    def get_price(self, code: str) -> float:
        """code: 6자리 종목코드. 현재가(원)를 float로 돌려준다."""
        body = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price", _PRICE_TR_ID,
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
        return float(body["output"]["stck_prpr"])

    # ── 잔고조회 ──────────────────────────────────────────────────────
    def get_balance(self):
        """현금 잔고 + 보유 종목 리스트를 돌려준다. (공식 예제 inquire_balance
        파라미터를 그대로 따름 - 조회구분(INQR_DVSN)은 "02"(종목별)를 쓴다.)"""
        params = {
            "CANO": self.creds.account_no, "ACNT_PRDT_CD": self.creds.account_product_code,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        body = self._get("/uapi/domestic-stock/v1/trading/inquire-balance",
                          _BALANCE_TR_ID[self.env], params)
        positions = body.get("output1", [])
        cash_summary = (body.get("output2") or [{}])[0]
        return {
            "cash": float(cash_summary.get("dnca_tot_amt", 0)),  # 예수금총금액
            "positions": [
                {"code": p["pdno"], "name": p["prdt_name"], "shares": int(p["hldg_qty"]),
                 "avg_price": float(p["pchs_avg_pric"]), "current_price": float(p["prpr"])}
                for p in positions if int(p.get("hldg_qty") or 0) > 0
            ],
        }

    # ── 주문(현금 매수/매도만 - 신용/공매도 미구현) ───────────────────
    def place_order(self, code: str, qty: int, side: str, price: int | None = None,
                     dry_run: bool = True):
        """code: 6자리 종목코드. side: "buy"/"sell". price: 지정가(원,
        정수) - None이면 get_price()로 현재가를 조회해 그 가격으로 지정가
        주문(시장가 주문은 이 모듈에서 아예 막아둠 - ORD_DVSN_LIMIT 고정).

        dry_run=True(기본값)면 실제 API 호출을 하지 않고 "이렇게 나갈
        주문"만 돌려준다 - 실전 반영 전 반드시 이 기본값으로 먼저 여러
        거래일 검증할 것. 명시적으로 dry_run=False를 넘겨야 실제 주문이
        나간다."""
        if side not in ("buy", "sell"):
            raise ValueError('side는 "buy" 또는 "sell"이어야 합니다')
        if qty <= 0:
            raise ValueError("qty는 1 이상이어야 합니다")
        if price is None:
            price = round(self.get_price(code))

        order_body = {
            "CANO": self.creds.account_no, "ACNT_PRDT_CD": self.creds.account_product_code,
            "PDNO": code, "ORD_DVSN": ORD_DVSN_LIMIT, "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
            "EXCG_ID_DVSN_CD": "KRX",  # 신버전 TR 필수 필드 - 거래소 분산원장 중 기본 KRX
            "SLL_TYPE": "01" if side == "sell" else "",  # 매도유형(일반매도) - 매수는 공란
            "CNDT_PRIC": "",
        }
        plan = {"env": self.env, "side": side, "code": code, "qty": qty,
                "price": price, "tr_id": _ORDER_TR_ID[(self.env, side)], "dry_run": dry_run}
        if dry_run:
            return {**plan, "executed": False,
                    "message": "dry_run=True - 실제 주문 미실행(검증 모드)"}

        tr_id = _ORDER_TR_ID[(self.env, side)]
        # EGW00201(초당 거래건수 초과)만 재시도한다 - 이 에러는 "주문이
        # 아예 접수되기 전에" 거부되는 것이라 재시도해도 중복 주문 위험이
        # 없다(_get과 같은 원칙). 그 외 에러는 절대 재시도하지 않는다 -
        # 주문 거부/부분 실패를 자동으로 다시 쏘면 안 된다.
        for attempt in range(3):
            resp = requests.post(
                f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash",
                headers=self._headers(tr_id), data=json.dumps(order_body), timeout=self.timeout,
            )
            body = resp.json()
            if resp.status_code == 200 and body.get("rt_cd") == "0":
                return {**plan, "executed": True, "order_no": body.get("output", {}).get("ODNO"),
                        "raw": body}
            if body.get("msg_cd") == "EGW00201" and attempt < 2:
                time.sleep(1.0)
                continue
            raise KisApiError(f"주문 실패: {resp.status_code} {body}")
