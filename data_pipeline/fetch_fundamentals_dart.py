"""DART에서 국내 전종목의 N년치 재무제표를 받아 연도별 parquet 파일로 저장한다
(fundamentals/kr/{연도}.parquet, 한 파일에 그 해 전종목이 행으로 들어간다 - 가격 데이터의
prices/kr, prices/us 구조와 맞춰 fundamentals도 kr/us로 나눈다. us는 향후 Finnhub 등으로
채울 자리만 미리 만들어둔다).

DART는 계정(API 키)당 일일 요청 한도(기본 20,000회)가 있고 종목당 1~2회를 쓰기 때문에
국내 전종목(약 2,686개) x 10년치는 하루에 못 끝난다. 그래서:

- **연도를 최신순으로 먼저 통째로 채우고, 그 안에서 종목을 순회한다.** 종목 순서로
  돌리면(기존 dart_fetch.py 방식) 중간에 멈췄을 때 "일부 종목만 전체 연도 완료, 나머지는
  0건"이 되어 스크리닝 백테스트를 시작할 수 없다. 연도 우선으로 돌리면 하루치만 받아도
  최근 연도 하나는 전종목이 채워져 바로 쓸 수 있다.
- 이미 받은 (종목, 연도)는 건너뛰어 재실행하면 이어받는다 - 성공분은 해당 연도
  parquet에 이미 있는지로, 데이터없음(no_data)은 체크포인트 JSON으로 판단한다.
- 요청 한도 근처에서 스스로 멈추고, 다음날 다시 실행하면 이어받는다(DART 한도는
  자정 기준으로 초기화된다).
- 실제 재무제표 계산 로직(_extract_fields 등)은 dart_fetch.py 것을 그대로 재사용한다 -
  Flask 앱이 KrFundamental 테이블에 캐시하는 로직과 이 스크립트가 파일로 저장하는 로직이
  서로 달라지지 않게 하기 위함.

사용 예:
    python -m data_pipeline.fetch_fundamentals_dart --limit 10 --max-requests 100   # 소규모 테스트
    python -m data_pipeline.fetch_fundamentals_dart                                  # 본 수집(하루치)
    python -m data_pipeline.fetch_fundamentals_dart --start-year 2016 --end-year 2025
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from dart_fetch import fetch_corp_code_map, fetch_company_year, EXTRA_FIELDS  # noqa: E402

from data_pipeline.common import FUND_KR_DIR, ensure_dirs, load_kr_stocks  # noqa: E402

CHECKPOINT_PATH = FUND_KR_DIR / "_checkpoint.json"
OUTPUT_COLUMNS = (
    ["stock_code", "corp_code", "corp_name", "bsns_year", "fs_div", "rcept_no",
     "total_equity", "net_income", "revenue"]
    + list(EXTRA_FIELDS)
    + ["fetched_at"]
)


def load_checkpoint():
    if CHECKPOINT_PATH.exists():
        with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_checkpoint(cp):
    with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
        json.dump(cp, f, ensure_ascii=False)


def load_year_df(year):
    path = FUND_KR_DIR / f"{year}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def save_year_df(year, df):
    path = FUND_KR_DIR / f"{year}.parquet"
    df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)


def run(start_year, end_year, max_requests, limit, flush_every=300):
    ensure_dirs()
    years = list(range(end_year, start_year - 1, -1))  # 최신순
    stocks = load_kr_stocks()
    if limit:
        stocks = stocks[:limit]

    print(f"[fetch_fundamentals_dart] 연도 {years}, 종목 {len(stocks)}개, "
          f"요청 한도 {max_requests}건")

    corp_map = fetch_corp_code_map()
    checkpoint = load_checkpoint()

    requests_used = 0
    total_stored, total_no_data, total_no_corp = 0, 0, 0
    started = time.time()
    stop = False

    for year in years:
        if stop:
            break
        year_key = str(year)
        no_data_set = set(checkpoint.get(year_key, {}).get("no_data", []))
        df = load_year_df(year)
        have_set = set(df["stock_code"]) if not df.empty else set()

        buffer = []
        processed_since_flush = 0

        def flush():
            nonlocal df, buffer, processed_since_flush
            if buffer:
                new_rows = pd.DataFrame(buffer, columns=OUTPUT_COLUMNS)
                df = new_rows if df.empty else pd.concat([df, new_rows], ignore_index=True)
                save_year_df(year, df)
                buffer = []
            save_checkpoint(checkpoint)
            processed_since_flush = 0

        for stock in stocks:
            code = stock["code"]
            if code in have_set or code in no_data_set:
                continue

            info = corp_map.get(code)
            if not info:
                total_no_corp += 1
                no_data_set.add(code)
                checkpoint[year_key] = {"no_data": sorted(no_data_set)}
                continue

            if requests_used >= max_requests:
                print(f"[fetch_fundamentals_dart] 요청 한도({max_requests}) 도달, 중단합니다. "
                      f"저장 {total_stored}건, 경과 {time.time() - started:.0f}초")
                stop = True
                break

            result = fetch_company_year(info["corp_code"], year)
            requests_used += 1 if result and result.get("fs_div") == "CFS" else 2

            if result:
                row = {
                    "stock_code": code, "corp_code": info["corp_code"],
                    "corp_name": info["corp_name"], "bsns_year": year_key,
                    "fs_div": result["fs_div"], "rcept_no": result.get("rcept_no", ""),
                    "total_equity": result["total_equity"], "net_income": result["net_income"],
                    "revenue": result.get("revenue"),
                    "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                for f in EXTRA_FIELDS:
                    row[f] = result.get(f)
                buffer.append(row)
                total_stored += 1
            else:
                total_no_data += 1
                no_data_set.add(code)
                checkpoint[year_key] = {"no_data": sorted(no_data_set)}

            processed_since_flush += 1
            if processed_since_flush >= flush_every:
                flush()
                elapsed = time.time() - started
                print(f"[fetch_fundamentals_dart] {year}년 진행중: 저장 {total_stored}건, "
                      f"데이터없음 {total_no_data}건, 요청 {requests_used}건, 경과 {elapsed:.0f}초")

        flush()
        if not stop:
            print(f"[fetch_fundamentals_dart] {year}년 완료: 누적 저장 {total_stored}건")

    print(f"[fetch_fundamentals_dart] 종료: 저장 {total_stored}건, 데이터없음 {total_no_data}건, "
          f"corp_code 없음 {total_no_corp}건, 요청 {requests_used}건, "
          f"총 소요 {time.time() - started:.0f}초, 중단여부={stop}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    # DART fnlttSinglAcntAll API는 bsns_year 2015 미만은 "조회된 데이타가 없습니다"를
    # 반환한다(직접 확인함) - 그 이전으로는 --start-year를 낮춰도 데이터를 못 받는다.
    DART_EARLIEST_YEAR = 2015

    parser = argparse.ArgumentParser(description="DART 국내 전종목 재무제표 수집(2015년~전년도)")
    parser.add_argument("--start-year", type=int, default=DART_EARLIEST_YEAR)
    parser.add_argument("--end-year", type=int, default=datetime.today().year - 1)
    parser.add_argument("--max-requests", type=int, default=19000, help="이번 실행 최대 요청 수(일일 한도 20,000 이하로)")
    parser.add_argument("--limit", type=int, default=0, help="테스트용: 앞에서 N종목만 처리(0=전체)")
    args = parser.parse_args()

    run(args.start_year, args.end_year, args.max_requests, args.limit or None)
