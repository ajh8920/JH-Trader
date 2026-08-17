"""데이터 수집 스크립트(fetch_prices.py, fetch_fundamentals_dart.py)가 공유하는
경로/종목리스트 로더. 앱(app.py)과 독립적으로 동작한다 - Flask나 DB에 의존하지 않는다.

저장 위치는 이 저장소 밖(C:\\Users\\ajh89\\Desktop\\AI 공부\\000.Data)이다. 로컬 백테스트용
원본 데이터를 프로젝트 git 저장소에 커밋하지 않기 위해 의도적으로 분리했다. 다른 PC에서
쓸 경우 STOCK_DATA_ROOT 환경변수로 위치를 바꿀 수 있다.
"""

import json
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

DATA_ROOT = Path(os.environ.get(
    "STOCK_DATA_ROOT",
    r"C:\Users\ajh89\Desktop\AI 공부\000.Data",
))
PRICE_DIR = DATA_ROOT / "prices"
PRICE_KR_DIR = PRICE_DIR / "kr"
PRICE_US_DIR = PRICE_DIR / "us"
FUND_DIR = DATA_ROOT / "fundamentals"
FUND_KR_DIR = FUND_DIR / "kr"  # DART(전자공시) 출처
FUND_US_DIR = FUND_DIR / "us"  # Finnhub 등 향후 수집 예정, 폴더만 미리 만들어둠


def ensure_dirs():
    for d in (PRICE_KR_DIR, PRICE_US_DIR, FUND_KR_DIR, FUND_US_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_kr_stocks():
    with open(PROJECT_DIR / "kr_stocks.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_us_stocks():
    with open(PROJECT_DIR / "us_stocks.json", "r", encoding="utf-8") as f:
        return json.load(f)


def kr_yf_symbol(stock):
    """국내 종목의 yfinance 심볼. KOSPI -> .KS, KOSDAQ -> .KQ."""
    suffix = ".KS" if stock["market"] == "KOSPI" else ".KQ"
    return stock["code"] + suffix


def us_yf_symbol(stock):
    # yfinance는 티커의 '.'를 '-'로 써야 한다(예: BRK.B -> BRK-B).
    # 현재 us_stocks.json에는 해당 케이스가 없지만 방어적으로 처리한다.
    return stock["code"].replace(".", "-")
