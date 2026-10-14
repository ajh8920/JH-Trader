"""pykrx로 국내 상장사 전체의 외국인 보유비율(지분율) 일별 데이터를 받아
로컬 parquet에 캐시한다. fetch_investor_flows_kr.py와 같은 패턴 - "외국인
순매수 거래대금"(돈 기준)과는 다른 축으로, "외국인이 전체 주식 중 몇 %를
들고 있는가"(지분 기준) 자체의 추세(특히 감소 추세)가 거래대금 변화보다
더 느리지만 더 "확신 있는" 이탈/진입 신호일 수 있다는 가설(2026-10-14,
사용자 제안)을 검증하기 위한 신규 피처.

fetch_investor_flows_kr.py와 달리 이 데이터는 날짜 범위만으로는 못 받고
종목별로 받아야 한다(KRX API 제약) - 2,600여 종목 x 1콜(종목당 전체
기간을 한 번에 받음, 날짜별로 나눠 부를 필요 없음)이라 총 호출 수는
investor_flows_kr.py와 비슷한 규모(수천 회 아님, 종목 수만큼).

KRX가 이 데이터 카테고리를 data.krx.co.kr 회원 로그인 없이는 막아놔서,
.env의 KRX_ID/KRX_PW로 로그인한 세션이 있어야 동작한다. 세션 만료(약
1시간)는 pykrx의 get_auth_session()이 자동 감지해 재로그인하므로 여기서
따로 처리할 필요 없다 - 일시적 네트워크 오류 등 다른 실패만 1회 재시도.

사용법:
  python -m data_pipeline.fetch_foreign_ownership_kr
  python -m data_pipeline.fetch_foreign_ownership_kr --start 2016-01-01 --end 2026-10-02
"""
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import pandas as pd
from dotenv import load_dotenv

load_dotenv(PROJECT_DIR / ".env")

from data_pipeline.common import DATA_ROOT, load_kr_stocks

FOREIGN_KR_DIR = DATA_ROOT / "foreign_ownership" / "kr"
RATE_LIMIT_SLEEP = 0.3


def fetch_one(ticker, start, end):
    from pykrx import stock
    df = stock.get_exhaustion_rates_of_foreign_investment_by_date(start, end, ticker)
    if df is None or df.empty:
        return None
    df = df.reset_index()
    df = df.rename(columns={df.columns[0]: "date", "지분율": "foreign_pct"})
    return df[["date", "foreign_pct"]]


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default=None)
    args = parser.parse_args()

    end = args.end or time.strftime("%Y-%m-%d")
    start_ymd = args.start.replace("-", "")
    end_ymd = end.replace("-", "")

    FOREIGN_KR_DIR.mkdir(parents=True, exist_ok=True)
    stocks = load_kr_stocks()
    codes = [s["code"] for s in stocks]

    out_path = FOREIGN_KR_DIR / "foreign_ownership.parquet"
    existing_codes = set()
    rows = []
    if out_path.exists():
        existing = pd.read_parquet(out_path)
        existing_codes = set(existing["code"].unique())
        rows = existing.to_dict("records")
        print(f"[foreign_ownership] 기존 파일에 {len(existing_codes)}종목 이미 있음 - 이어서 진행", flush=True)

    todo = [c for c in codes if c not in existing_codes]
    print(f"[foreign_ownership] 전체 {len(codes)}종목 중 {len(todo)}종목 남음", flush=True)

    for n, code in enumerate(todo, 1):
        try:
            df = fetch_one(code, start_ymd, end_ymd)
        except Exception:
            time.sleep(2)
            try:
                df = fetch_one(code, start_ymd, end_ymd)
            except Exception as e2:
                print(f"[foreign_ownership] {code} 실패(재시도 포함): {e2}", flush=True)
                df = None
        if df is not None and not df.empty:
            df["code"] = code
            rows.extend(df.to_dict("records"))
        if n % 50 == 0 or n == len(todo):
            pd.DataFrame(rows).to_parquet(out_path, index=False)
            print(f"[foreign_ownership] {n}/{len(todo)} 종목 처리, 누적 {len(rows)}행 저장", flush=True)
        time.sleep(RATE_LIMIT_SLEEP)

    pd.DataFrame(rows).to_parquet(out_path, index=False)
    print(f"[foreign_ownership] 완료 - 총 {len(rows)}행, {out_path}", flush=True)


if __name__ == "__main__":
    main()
