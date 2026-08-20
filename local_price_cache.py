"""로컬에서 스크리닝 백테스트를 반복 실행할 때 쓰는 가격 데이터 캐시.

운영 서버(app.py)는 이 모듈을 쓰지 않는다 - 매번 새로 받아오는
trend_screener.fetch_ohlc_history_batches를 그대로 쓴다(메모리 절약을 위해
청크 단위로 스트리밍하는 방식이 운영 서버 메모리 한도에는 맞다, screening_backtest.py
주석 참고). 이 모듈은 로컬에서 같은 유니버스로 파라미터만 바꿔 여러 번
백테스트를 반복할 때, 매번 9분 넘게 걸리는 야후 재조회를 건너뛰기 위한 것이다
(로컬은 메모리 여유가 있어 전종목을 한 번에 캐싱해도 문제없다).

캐시 파일: data/price_cache/{market}.parquet. 부분 갱신은 하지 않는다(전종목을
한 번에 받아야 청크 다운로드가 효율적이라서) - max_age_hours보다 오래되면
통째로 다시 받는다.
"""
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

import trend_screener as ts

CACHE_DIR = Path(__file__).parent / "data" / "price_cache"
DEFAULT_LOOKBACK_DAYS = 8 * 365  # 2020-01-01 시작 백테스트(+450일 워밍업)까지 여유 있게 커버
CHUNK = 25


def _cache_path(market):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{market}.parquet"


def _fetch_full_universe(market, lookback_days=DEFAULT_LOOKBACK_DAYS):
    universe = ts.load_universe(market)
    tickers = [t for _, _, t, _, _ in universe]
    end = datetime.today()
    start = end - timedelta(days=lookback_days)
    start_str, end_str = start.strftime("%Y-%m-%d"), (end + timedelta(days=1)).strftime("%Y-%m-%d")

    rows = []
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        df = None
        for attempt in range(3):
            try:
                # threads=True: 운영 서버는 메모리 한도 때문에 끄지만(trend_screener.py
                # 주석 참고) 로컬은 여유가 있어 켜서 다운로드 자체를 병렬화한다.
                df = yf.download(
                    chunk, start=start_str, end=end_str,
                    progress=False, auto_adjust=False, timeout=30, group_by="ticker", threads=True,
                )
                if df is not None and not df.empty:
                    break
            except Exception:
                df = None
            time.sleep(1.5 * (attempt + 1))
        else:
            continue
        if df is None or df.empty:
            continue
        for t in chunk:
            try:
                sub = df[t]
            except KeyError:
                continue
            sub = sub.dropna(subset=["Close", "High", "Low"])
            has_volume = "Volume" in sub.columns
            for idx, row in sub.iterrows():
                rows.append({
                    "ticker": t, "date": idx.strftime("%Y-%m-%d"),
                    "close": float(row["Close"]), "high": float(row["High"]), "low": float(row["Low"]),
                    "volume": float(row["Volume"]) if has_volume and row["Volume"] == row["Volume"] else None,
                })
        print(f"[local_price_cache] {market} {min(i + CHUNK, len(tickers))}/{len(tickers)}종목 처리")

    df_all = pd.DataFrame(rows)
    df_all.to_parquet(_cache_path(market), engine="pyarrow", compression="zstd", index=False)
    print(f"[local_price_cache] {market} 캐시 저장 완료: {len(df_all)}행 ({df_all['ticker'].nunique()}종목)")
    return df_all


def _load_or_build_cache(market, max_age_hours=12, force_refresh=False):
    path = _cache_path(market)
    if not force_refresh and path.exists():
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours < max_age_hours:
            print(f"[local_price_cache] {market} 캐시 재사용(생성 {age_hours:.1f}시간 전)")
            return pd.read_parquet(path)
        print(f"[local_price_cache] {market} 캐시가 {age_hours:.1f}시간 지나 다시 받습니다")
    return _fetch_full_universe(market)


def cached_fetch_ohlc_history_batches(market, max_age_hours=12, force_refresh=False):
    """screening_backtest.run_screening_backtest의 fetch_fn 인자로 바로 넘길 수
    있는 (tickers, start_date, end_date) -> Iterable[(ticker, bars)] 함수를 반환한다."""
    df = _load_or_build_cache(market, max_age_hours=max_age_hours, force_refresh=force_refresh)

    def _fetch(tickers, start_date, end_date):
        wanted = set(tickers)
        sub = df[df["ticker"].isin(wanted) & (df["date"] >= start_date) & (df["date"] < end_date)]
        for ticker, g in sub.groupby("ticker"):
            g = g.sort_values("date")
            bars = [
                {"date": r.date, "close": r.close, "high": r.high, "low": r.low, "volume": r.volume}
                for r in g.itertuples()
            ]
            if bars:
                yield ticker, bars

    return _fetch
