"""야후파이낸스에서 국내/미국 전종목의 N년치 일봉을 받아 종목별 parquet 파일로 저장한다.

한 번에 한 종목씩 요청하면 종목 수(국내 2,686 + 미국 606)만큼 HTTP 요청이 나가 느리고
차단 위험도 커서, 여러 티커를 묶어 yf.download() 배치 호출로 받는다.

이미 parquet 파일이 있는 종목은 건너뛴다(--force로 재수집 가능) - 중간에 멈춰도
재실행하면 이어받는다.

사용 예:
    python -m data_pipeline.fetch_prices --market kr --limit 5      # 소규모 테스트
    python -m data_pipeline.fetch_prices --market all               # 전종목 수집
    python -m data_pipeline.fetch_prices --market kr --force        # 국내 전종목 재수집
"""

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from data_pipeline.common import (
    PRICE_DIR, PRICE_KR_DIR, PRICE_US_DIR,
    ensure_dirs, load_kr_stocks, load_us_stocks, kr_yf_symbol, us_yf_symbol,
)

COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]


def _extract_one(df, symbol, is_batch):
    """yf.download 결과에서 티커 하나의 OHLCV만 뽑아 표준 컬럼의 DataFrame으로 반환한다."""
    if is_batch:
        if symbol not in df.columns.get_level_values(0):
            return None
        sub = df[symbol]
    else:
        sub = df
    sub = sub.dropna(how="all")
    if sub.empty:
        return None

    out = pd.DataFrame({
        "date": sub.index.strftime("%Y-%m-%d"),
        "open": sub["Open"].astype(float),
        "high": sub["High"].astype(float),
        "low": sub["Low"].astype(float),
        "close": sub["Close"].astype(float),
        "adj_close": sub["Adj Close"].astype(float) if "Adj Close" in sub.columns else sub["Close"].astype(float),
        "volume": sub["Volume"].fillna(0).astype("int64"),
    }).reset_index(drop=True)
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out[(out["open"] > 0) & (out["high"] > 0) & (out["low"] > 0) & (out["close"] > 0)]
    return out if not out.empty else None


def fetch_batch(symbols, start, end, retries=2):
    """티커 목록 하나를 한 번의 yf.download 호출로 받는다. 실패 시 재시도."""
    for attempt in range(retries + 1):
        try:
            df = yf.download(
                tickers=symbols, start=start, end=end, group_by="ticker",
                auto_adjust=False, threads=True, progress=False, timeout=20,
            )
            return df
        except Exception as e:
            if attempt == retries:
                print(f"[fetch_prices] 배치 실패(포기): {symbols[:3]}... - {e}")
                return None
            print(f"[fetch_prices] 배치 실패, 재시도 {attempt + 1}/{retries}: {e}")
            time.sleep(5)
    return None


def run(market, years, batch_size, sleep_sec, force, limit, start_date=None):
    ensure_dirs()
    end = datetime.today().strftime("%Y-%m-%d")
    start = start_date or (datetime.today() - timedelta(days=365 * years + 5)).strftime("%Y-%m-%d")

    targets = []  # (out_dir, code, yf_symbol)
    if market in ("kr", "all"):
        for s in load_kr_stocks():
            targets.append((PRICE_KR_DIR, s["code"], kr_yf_symbol(s)))
    if market in ("us", "all"):
        for s in load_us_stocks():
            targets.append((PRICE_US_DIR, s["code"], us_yf_symbol(s)))

    if not force:
        targets = [t for t in targets if not (t[0] / f"{t[1]}.parquet").exists()]
    if limit:
        targets = targets[:limit]

    print(f"[fetch_prices] 대상 {len(targets)}종목, 기간 {start}~{end}, 배치 {batch_size}개씩")
    if not targets:
        print("[fetch_prices] 수집할 종목이 없습니다(이미 전부 완료됨). --force로 재수집 가능")
        return

    manifest_rows = []
    ok, empty, failed = 0, 0, 0
    started = time.time()

    for i in range(0, len(targets), batch_size):
        chunk = targets[i:i + batch_size]
        symbols = [t[2] for t in chunk]
        is_batch = len(symbols) > 1
        df = fetch_batch(symbols, start, end)
        if df is None or df.empty:
            failed += len(chunk)
            continue

        for out_dir, code, symbol in chunk:
            one = _extract_one(df, symbol, is_batch)
            if one is None:
                empty += 1
                continue
            path = out_dir / f"{code}.parquet"
            one.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
            ok += 1
            manifest_rows.append({
                "code": code, "rows": len(one),
                "start_date": one["date"].iloc[0], "end_date": one["date"].iloc[-1],
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })

        done = min(i + batch_size, len(targets))
        elapsed = time.time() - started
        print(f"[fetch_prices] {done}/{len(targets)} 처리 (성공 {ok}, 빈데이터 {empty}, 실패 {failed}), "
              f"경과 {elapsed:.0f}초")
        time.sleep(sleep_sec)

    if manifest_rows:
        manifest_path = PRICE_DIR / f"_manifest_{market}.parquet"
        new_df = pd.DataFrame(manifest_rows)
        if manifest_path.exists() and not force:
            old_df = pd.read_parquet(manifest_path)
            new_df = pd.concat([old_df[~old_df["code"].isin(new_df["code"])], new_df], ignore_index=True)
        new_df.to_parquet(manifest_path, engine="pyarrow", compression="zstd", index=False)

    print(f"[fetch_prices] 완료: 성공 {ok}, 빈데이터 {empty}, 실패 {failed}, 총 {time.time() - started:.0f}초")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    # fundamentals(DART)의 실제 하한이 2015년이라(그 이전은 API가 데이터를 안 줌) 가격
    # 데이터도 맞춰서 2015년부터 받는다. --start를 명시하면 그쪽이 우선한다.
    parser = argparse.ArgumentParser(description="야후파이낸스 국내/미국 주가 수집(기본 2015년~)")
    parser.add_argument("--market", choices=["kr", "us", "all"], default="all")
    parser.add_argument("--start", type=str, default="2015-01-01", help="수집 시작일(YYYY-MM-DD)")
    parser.add_argument("--years", type=int, default=10, help="--start 대신 '오늘로부터 N년 전'으로 지정하려면 --start ''와 함께 사용")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--sleep", type=float, default=1.5, help="배치 사이 대기(초)")
    parser.add_argument("--force", action="store_true", help="이미 있는 종목도 재수집")
    parser.add_argument("--limit", type=int, default=0, help="테스트용: 앞에서 N종목만 처리(0=전체)")
    args = parser.parse_args()

    run(args.market, args.years, args.batch_size, args.sleep, args.force, args.limit or None,
        start_date=args.start or None)
