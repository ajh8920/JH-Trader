"""KRX 상장폐지 종목(생존편향 제거용) 리스트 + 과거 시세를 FinanceDataReader(FDR)로
받아 저장한다. yfinance는 상장폐지 종목 데이터를 제공하지 않아 별도 소스가 필요했다.

사유(합병/감사의견거절/해산 등) 기준으로 종목을 골라내지 않는다 - 신주인수권증서·
수익증권·신형우선주 같은 "주식 자체가 아닌" 상품만 제외한다(SecuGroup=주권,
Kind=보통주). 합병/자회사화처럼 손실이 아닌 케이스까지 임의로 빼면 오히려 새로운
편향이 생긴다 - 실제 매매 규칙(돈치안 브레이크아웃 등)이 알아서 걸러내도록 둔다.

사용 예:
    python -m data_pipeline.fetch_delisted_kr --limit 5   # 소규모 테스트
    python -m data_pipeline.fetch_delisted_kr             # 전종목 수집(재실행하면 이어받음)
"""
import argparse
import json
import sys
import time

import FinanceDataReader as fdr
import pandas as pd

from data_pipeline.common import PRICE_KR_DELISTED_DIR, PROJECT_DIR, ensure_dirs

# 2017년 시작 백테스트 + 워밍업(450일)을 커버하는 하한. 이보다 오래 전에 상장폐지된
# 종목은 어차피 백테스트 기간에 걸리지 않는다.
CUTOFF_DATE = "2015-01-01"


def build_delisted_list():
    df = fdr.StockListing("KRX-DELISTING")
    df["DelistingDate"] = pd.to_datetime(df["DelistingDate"], errors="coerce")
    df = df[
        (df["SecuGroup"] == "주권") & (df["Kind"] == "보통주")
        & (df["DelistingDate"] >= CUTOFF_DATE)
    ].copy()
    df = df.dropna(subset=["Symbol", "DelistingDate"])
    return df


def run(limit, force, sleep_sec):
    ensure_dirs()
    delisted = build_delisted_list()
    print(f"[fetch_delisted_kr] 대상 {len(delisted)}종목(2015-01-01 이후 상장폐지, 보통주만)")

    rows = delisted.to_dict("records")
    if limit:
        rows = rows[:limit]
    if not force:
        rows = [r for r in rows if not (PRICE_KR_DELISTED_DIR / f"{r['Symbol']}.parquet").exists()]
        print(f"[fetch_delisted_kr] 이미 받은 종목 제외 후 {len(rows)}종목")

    manifest_rows = []
    ok, empty, failed = 0, 0, 0
    started = time.time()
    for i, row in enumerate(rows, 1):
        code = row["Symbol"]
        try:
            df = fdr.DataReader(code, exchange="KRX-DELISTING")
        except Exception as e:
            print(f"[fetch_delisted_kr] {code}({row['Name']}) 실패: {e}")
            failed += 1
            time.sleep(sleep_sec)
            continue
        if df is None or df.empty:
            empty += 1
            time.sleep(sleep_sec)
            continue
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        df = df[(df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0)]
        if df.empty:
            empty += 1
            time.sleep(sleep_sec)
            continue
        out = pd.DataFrame({
            "date": df.index.strftime("%Y-%m-%d"),
            "open": df["Open"].astype(float), "high": df["High"].astype(float),
            "low": df["Low"].astype(float), "close": df["Close"].astype(float),
            "adj_close": df["Close"].astype(float),
            "volume": df["Volume"].fillna(0).astype("int64"),
        }).reset_index(drop=True)
        out.to_parquet(PRICE_KR_DELISTED_DIR / f"{code}.parquet", engine="pyarrow", compression="zstd", index=False)
        ok += 1
        delisting_date = row["DelistingDate"]
        manifest_rows.append({
            "code": code, "name": row["Name"], "market": row["Market"],
            "delistingDate": delisting_date.strftime("%Y-%m-%d") if pd.notna(delisting_date) else None,
            "reason": row.get("Reason") or "", "rows": len(out),
            "startDate": out["date"].iloc[0], "endDate": out["date"].iloc[-1],
        })
        if i % 25 == 0 or i == len(rows):
            elapsed = time.time() - started
            print(f"[fetch_delisted_kr] {i}/{len(rows)} 처리(성공 {ok}, 빈데이터 {empty}, 실패 {failed}), 경과 {elapsed:.0f}초")
        time.sleep(sleep_sec)

    if manifest_rows:
        meta_path = PROJECT_DIR / "kr_delisted_stocks.json"
        existing = []
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        existing_codes = {e["code"] for e in existing}
        merged = existing + [m for m in manifest_rows if m["code"] not in existing_codes]
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f"[fetch_delisted_kr] kr_delisted_stocks.json 저장: {len(merged)}종목")

    print(f"[fetch_delisted_kr] 완료: 성공 {ok}, 빈데이터 {empty}, 실패 {failed}, 총 {time.time() - started:.0f}초")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="KRX 상장폐지 종목 시세 수집(FinanceDataReader)")
    parser.add_argument("--limit", type=int, default=0, help="테스트용: 앞에서 N종목만 처리(0=전체)")
    parser.add_argument("--force", action="store_true", help="이미 받은 종목도 재수집")
    parser.add_argument("--sleep", type=float, default=0.3, help="요청 사이 대기(초)")
    args = parser.parse_args()

    run(args.limit or None, args.force, args.sleep)
