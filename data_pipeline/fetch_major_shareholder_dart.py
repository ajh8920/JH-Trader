"""DART 대량보유 상황보고(majorstock.json)를 국내 상장사 전체에 대해 받아
로컬 parquet에 캐시한다. VCP 백테스트의 "최대주주지분율 > 70% 제외" 필터용
- 종목 하나당 요청 한 번으로 그 회사가 DART에 보유하고 있는 전체 보고 이력을
돌려준다(연도별로 나눠 부를 필요가 없다는 점이 fetch_fundamentals_dart.py와 다름).

한계: 실제로 받아보니 이 API가 반환하는 이력이 최근 1~2년치로 제한되는 경우가
많다(2020년 이전 보고는 조회되지 않는 종목이 다수) - DART 서버 쪽 제약으로 보이며
우리가 조정할 수 있는 부분이 아니다. 그래서 이 데이터로 만드는 "최대주주지분율"
필터는 최근 구간(대략 2024년 이후)에만 실질적으로 적용되고, 그 이전 구간은
데이터가 없어 필터가 사실상 통과 처리된다 - vcp_strategy.py의 필터 적용부에
같은 내용을 주석으로 남겨둔다.

사용법:
  python -m data_pipeline.fetch_major_shareholder_dart
  python -m data_pipeline.fetch_major_shareholder_dart --api-key-env DART_API_KEY_2 --shard 1/2
"""
import argparse
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv(PROJECT_DIR / ".env")

import dart_fetch as df
from data_pipeline.common import SHAREHOLDER_KR_DIR, ensure_dirs, load_kr_stocks

DART_BASE = "https://opendart.fss.or.kr/api"
RATE_LIMIT_SLEEP = 0.2


def fetch_one(corp_code, api_key):
    try:
        res = requests.get(
            f"{DART_BASE}/majorstock.json", params={"crtfc_key": api_key, "corp_code": corp_code}, timeout=15,
        )
        data = res.json()
    except Exception:
        return None
    if data.get("status") != "000":
        return None
    return data.get("list", [])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key-env", default="DART_API_KEY")
    parser.add_argument("--shard", default=None, help="예: 1/2 (전체를 2등분해 그 중 1번째만 처리)")
    args = parser.parse_args()

    import os
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        print(f"{args.api_key_env} 환경변수가 없습니다")
        return

    ensure_dirs()
    corp_map = df.fetch_corp_code_map()
    stocks = load_kr_stocks()
    codes = [s["code"] for s in stocks]

    if args.shard:
        idx, total = (int(x) for x in args.shard.split("/"))
        codes = codes[idx - 1::total]

    out_path = SHAREHOLDER_KR_DIR / f"majorstock{'_' + args.shard.replace('/', 'of') if args.shard else ''}.parquet"
    existing_codes = set()
    if out_path.exists():
        existing_codes = set(pd.read_parquet(out_path, columns=["stock_code"])["stock_code"].unique())
        print(f"[major_shareholder] 기존 파일에 {len(existing_codes)}종목 이미 있음 - 이어서 진행")

    rows = []
    if out_path.exists():
        rows = pd.read_parquet(out_path).to_dict("records")

    processed = 0
    for code in codes:
        if code in existing_codes:
            continue
        info = corp_map.get(code)
        if not info:
            continue
        items = fetch_one(info["corp_code"], api_key)
        time.sleep(RATE_LIMIT_SLEEP)
        if items:
            for it in items:
                try:
                    stkrt = float(str(it.get("stkrt", "")).replace(",", ""))
                except (TypeError, ValueError):
                    continue
                rows.append({
                    "stock_code": code, "rcept_no": it.get("rcept_no", ""), "rcept_dt": it.get("rcept_dt", ""),
                    "repror": it.get("repror", ""), "stkrt": stkrt,
                })
        processed += 1
        if processed % 100 == 0:
            print(f"[major_shareholder] {processed}/{len(codes)}종목 처리, 누적 {len(rows)}행")
            pd.DataFrame(rows).to_parquet(out_path, engine="pyarrow", compression="zstd", index=False)

    pd.DataFrame(rows).to_parquet(out_path, engine="pyarrow", compression="zstd", index=False)
    print(f"[major_shareholder] 완료: {len(rows)}행, {out_path}")


if __name__ == "__main__":
    main()
