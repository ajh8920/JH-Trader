"""DART(전자공시시스템) Open API로 국내 상장사 재무제표를 받아 KrFundamental에 캐시한다.

DART는 KRX와 달리 "전종목 재무데이터 일괄 조회" API가 없고 종목 하나·연도 하나당
한 번씩 요청해야 하며, 계정(API 키)당 일일 요청 한도(기본 20,000회)가 있다. 그래서:
- 이미 DB에 있는 (종목코드, 연도) 조합은 건너뛰어, 중단 후 재실행해도 이어서 채워진다.
- 연결재무제표(CFS)를 먼저 시도하고 없으면 별도재무제표(OFS)로 재시도한다(자회사가
  없는 대다수 중소형사는 CFS가 없어 OFS로 떨어진다 - 종목당 최대 2회 소비).
- 요청 사이에 짧게 쉬어(RATE_LIMIT_SLEEP) 초당 요청 수를 낮게 유지한다.
- 각 (연도) 값은 그 연도를 "당기(thstrm)"로 조회한 응답에서만 뽑는다. 같은 응답에
  같이 오는 전기/전전기 금액은 쓰지 않는데, 그 금액을 실제로 처음 공시한 접수번호를
  알 수 없어(이번 조회의 접수번호를 쓰면 실제보다 훨씬 늦게 공개된 것처럼 잘못
  기록되어, 시점별 백테스트에서 과거 시점 판단이 부정확해진다) 정확한 공시일을
  보장할 수 있는 당기 금액만 저장한다. 대신 그만큼 요청 수가 늘어난다.
"""

import io
import json
import os
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
CORP_CODE_CACHE_PATH = BASE_DIR / "dart_corp_codes.json"
DART_BASE = "https://opendart.fss.or.kr/api"
ANNUAL_REPRT_CODE = "11011"  # 사업보고서(연간)
RATE_LIMIT_SLEEP = 0.2  # 요청 사이 대기(초) - 초당 약 5건으로 제한


def _api_key():
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        raise RuntimeError("DART_API_KEY 환경변수가 설정되지 않았습니다")
    return key


def fetch_corp_code_map(force=False):
    """종목코드 -> {corp_code, corp_name} 매핑을 받아 로컬 JSON에 캐시한다."""
    if not force and CORP_CODE_CACHE_PATH.exists():
        with open(CORP_CODE_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    res = requests.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": _api_key()}, timeout=30)
    res.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    root = ET.fromstring(zf.read("CORPCODE.xml"))

    mapping = {}
    for child in root.findall("list"):
        stock_code = (child.findtext("stock_code") or "").strip()
        if not stock_code:
            continue
        mapping[stock_code] = {
            "corp_code": child.findtext("corp_code"),
            "corp_name": (child.findtext("corp_name") or "").strip(),
        }

    with open(CORP_CODE_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False)
    return mapping


def _with_legacy_ifrs_prefix(mapping):
    # DART는 bsns_year 2019(2020년 접수분)부터 계정 ID를 "ifrs-full_" 접두사로 태깅한다.
    # 그 이전(2015~2018, fnlttSinglAcntAll이 실제로 데이터를 주는 가장 이른 시점이 2015다 -
    # 그 이전은 API가 아예 "조회된 데이타가 없습니다"를 반환한다) 연도는 구버전 K-IFRS
    # 분류체계라 접두사가 "ifrs_"뿐이고 나머지 계정명(suffix)은 동일하다. 이 매핑을 빼먹으면
    # 2015~2018년 재무데이터가 있는데도 전부 "데이터없음"으로 잘못 처리된다.
    extra = {
        "ifrs_" + k[len("ifrs-full_"):]: v
        for k, v in mapping.items() if k.startswith("ifrs-full_")
    }
    return {**mapping, **extra}


def _extract_fields(items):
    # 계정명(account_nm)은 "당기순이익" vs "당기순이익(손실)"처럼 같은 회사도 연도마다
    # 표기가 달라질 수 있어 매칭 기준으로 쓰기엔 불안정하다. 대신 DART가 부여하는
    # IFRS 표준 계정 ID(account_id)로 매칭한다 - 이건 표기와 무관하게 항상 고정이다.
    # 재무상태표 항목: sj_nm이 "재무상태표"인 것만 취급한다(같은 account_id가 다른
    # 표(자본변동표 등)에도 나올 수 있어 값이 뒤섞이는 걸 막기 위함).
    BS_ACCOUNT_ID_MAP = _with_legacy_ifrs_prefix({
        "ifrs-full_Equity": "total_equity",
        "ifrs-full_Assets": "total_assets",
        "ifrs-full_CurrentAssets": "current_assets",
        "ifrs-full_CurrentLiabilities": "current_liabilities",
        "ifrs-full_Liabilities": "total_liabilities",
        "ifrs-full_EquityAttributableToOwnersOfParent": "equity_attributable",
        "ifrs-full_IssuedCapital": "issued_capital",
        "ifrs-full_Inventories": "inventories",
        "ifrs-full_CashAndCashEquivalents": "cash_and_equivalents",
    })
    # 손익계산서 항목: "당기순이익" 등은 현금흐름표·자본변동표에도 같은 account_id로
    # 나오는데 그쪽 금액은 지배/비지배 배분이 다르게 섞여 있어 반드시 손익계산서·
    # 포괄손익계산서 표에서만 취급한다(기존에도 이 이유로 이렇게 되어 있었음).
    IC_ACCOUNT_ID_MAP = _with_legacy_ifrs_prefix({
        "ifrs-full_ProfitLoss": "net_income",
        "ifrs-full_Revenue": "revenue",
        "dart_OperatingIncomeLoss": "operating_income",
        "ifrs-full_ProfitLossAttributableToOwnersOfParent": "net_income_attributable",
        "ifrs-full_ProfitLossBeforeTax": "profit_before_tax",
        "ifrs-full_GrossProfit": "gross_profit",
    })
    result = {}
    for it in items:
        sj_nm = it.get("sj_nm")
        account_id = it.get("account_id")
        field = None
        if sj_nm == "재무상태표":
            field = BS_ACCOUNT_ID_MAP.get(account_id)
        elif sj_nm in ("손익계산서", "포괄손익계산서"):
            field = IC_ACCOUNT_ID_MAP.get(account_id)
        if not field or field in result:
            continue
        raw = (it.get("thstrm_amount") or "").replace(",", "").strip()
        if not raw:
            continue
        try:
            result[field] = float(raw)
        except ValueError:
            continue
    return result


def fetch_company_year(corp_code, bsns_year, api_key=None):
    """회사 하나·연도 하나의 당기 자본총계/당기순이익/매출액을 조회한다.

    연결재무제표(CFS)를 먼저 시도하고, 응답이 없으면 별도재무제표(OFS)로 재시도한다.
    api_key를 넘기지 않으면 DART_API_KEY 환경변수를 쓴다 - 여러 DART 계정 키를 나눠
    써서 일일 요청 한도를 여러 배로 늘려 병렬 수집할 때(data_pipeline/fetch_fundamentals_dart.py
    의 --api-key-env 참고) 호출자가 명시적으로 넘긴다.
    반환: {"total_equity", "net_income", "revenue"(optional), "rcept_no", "fs_div"} 또는
    데이터가 전혀 없으면 None.
    """
    key = api_key or _api_key()
    for fs_div in ("CFS", "OFS"):
        params = {
            "crtfc_key": key,
            "corp_code": corp_code,
            "bsns_year": str(bsns_year),
            "reprt_code": ANNUAL_REPRT_CODE,
            "fs_div": fs_div,
        }
        try:
            res = requests.get(f"{DART_BASE}/fnlttSinglAcntAll.json", params=params, timeout=15)
            data = res.json()
        except Exception:
            time.sleep(RATE_LIMIT_SLEEP)
            continue
        time.sleep(RATE_LIMIT_SLEEP)

        if data.get("status") != "000":
            continue
        items = data.get("list", [])
        if not items:
            continue

        fields = _extract_fields(items)
        if "total_equity" in fields and "net_income" in fields:
            fields["rcept_no"] = items[0].get("rcept_no", "")
            fields["fs_div"] = fs_div
            return fields
    return None


EXTRA_FIELDS = (
    "total_assets", "current_assets", "current_liabilities", "total_liabilities",
    "equity_attributable", "issued_capital", "inventories", "cash_and_equivalents",
    "operating_income", "net_income_attributable", "profit_before_tax", "gross_profit",
)


def backfill(app, db, KrFundamental, stock_codes, years, max_requests=18000, progress_every=200):
    """주어진 종목코드 목록 × 연도 목록에 대해 아직 없거나 확장 필드(EXTRA_FIELDS)가
    비어 있는 조합만 채워 넣는다 - 기존에 기본 3개 필드만으로 저장해둔 행도 이번에
    확장 필드까지 채워 업데이트한다(재무상태표/포괄손익계산서 확장분).

    max_requests: 이번 실행에서 소비할 최대 API 요청 수(대략치, 회사당 1~2회이므로
    실제 소비량은 이보다 적을 수 있음) - 일일 한도 근처에서 스스로 멈춰 다음 번
    재실행에서 이어받을 수 있게 한다.
    """
    corp_map = fetch_corp_code_map()

    with app.app_context():
        rows = KrFundamental.query.with_entities(
            KrFundamental.id, KrFundamental.stock_code, KrFundamental.bsns_year,
            KrFundamental.operating_income,
        ).all()
        # operating_income은 이번에 새로 추가한 필드라 값이 있으면 이미 확장 필드까지
        # 채워진 행이라는 뜻이다(재무제표에 영업이익 자체가 없는 극소수 업종은
        # 매번 재조회하게 되지만 안전한 쪽으로 판단하는 게 낫다).
        existing = {(r.stock_code, r.bsns_year): (r.id, r.operating_income) for r in rows}

    requests_used = 0
    done = 0
    skipped_no_corp = 0
    stored = 0
    no_data = 0
    started = time.time()

    for code in stock_codes:
        info = corp_map.get(code)
        if not info:
            skipped_no_corp += 1
            continue

        for year in years:
            key = (code, str(year))
            existing_id, existing_op_income = existing.get(key, (None, None))
            if existing_id is not None and existing_op_income is not None:
                continue
            if requests_used >= max_requests:
                print(f"[dart_fetch] 요청 한도({max_requests})에 도달해 중단합니다. "
                      f"저장 {stored}건, 소요 {time.time() - started:.0f}초")
                return {"stored": stored, "no_data": no_data, "skipped_no_corp": skipped_no_corp,
                        "requests_used": requests_used, "reached_limit": True}

            result = fetch_company_year(info["corp_code"], year)
            requests_used += 1 if result and result.get("fs_div") == "CFS" else 2

            if result:
                with app.app_context():
                    if existing_id is not None:
                        row = KrFundamental.query.get(existing_id)
                    else:
                        row = KrFundamental(stock_code=code, corp_code=info["corp_code"],
                                             corp_name=info["corp_name"], bsns_year=str(year))
                        db.session.add(row)
                    row.fs_div = result["fs_div"]
                    row.rcept_no = result.get("rcept_no", "")
                    row.total_equity = result["total_equity"]
                    row.net_income = result["net_income"]
                    row.revenue = result.get("revenue")
                    for f in EXTRA_FIELDS:
                        setattr(row, f, result.get(f))
                    row.fetched_at = datetime.utcnow()
                    db.session.commit()
                stored += 1
            else:
                no_data += 1

            done += 1
            if done % progress_every == 0:
                elapsed = time.time() - started
                print(f"[dart_fetch] 진행 {done}건 처리 (저장 {stored}, 데이터없음 {no_data}), "
                      f"요청 {requests_used}건 사용, 경과 {elapsed:.0f}초")

    print(f"[dart_fetch] 완료: 저장 {stored}건, 데이터없음 {no_data}건, "
          f"corp_code 없음 {skipped_no_corp}건, 요청 {requests_used}건, "
          f"소요 {time.time() - started:.0f}초")
    return {"stored": stored, "no_data": no_data, "skipped_no_corp": skipped_no_corp,
            "requests_used": requests_used, "reached_limit": False}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")

    from app import app
    from models import db, KrFundamental

    with open(BASE_DIR / "kr_stocks.json", "r", encoding="utf-8") as f:
        stocks = json.load(f)
    codes = [s["code"] for s in stocks]

    years = [2024, 2023, 2022]
    backfill(app, db, KrFundamental, codes, years, max_requests=18000)
