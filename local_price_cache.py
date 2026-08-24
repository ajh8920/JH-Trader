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


def _cache_path(market, include_delisted=False):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # include_delisted 여부에 따라 유니버스 구성이 달라지므로 캐시 파일도 분리한다 -
    # 같은 파일을 공유하면 "생성 후 N시간 이내면 재사용" 판단이 유니버스 차이를
    # 구분 못 해 종목 상장폐지 포함 여부가 뒤섞인 캐시를 잘못 재사용할 수 있다.
    suffix = "_with_delisted" if include_delisted else ""
    return CACHE_DIR / f"{market}{suffix}.parquet"


def _load_delisted_rows(market):
    """data_pipeline.fetch_delisted_kr가 미리 받아둔 상장폐지 종목 parquet들을
    합쳐 _fetch_full_universe와 같은 행 형식(dict list)으로 돌려준다. KR 전용."""
    if market != "KR":
        return []
    from data_pipeline.common import PRICE_KR_DELISTED_DIR

    rows = []
    if not PRICE_KR_DELISTED_DIR.exists():
        return rows
    for path in PRICE_KR_DELISTED_DIR.glob("*.parquet"):
        code = path.stem
        df = pd.read_parquet(path)
        ticker = f"{code}.DL"
        for r in df.itertuples():
            rows.append({
                "ticker": ticker, "date": r.date,
                "close": float(r.close), "high": float(r.high), "low": float(r.low),
                "open": float(r.open) if r.open == r.open else float(r.close),
                "volume": float(r.volume) if r.volume == r.volume else None,
            })
    print(f"[local_price_cache] 상장폐지 종목 {len(list(PRICE_KR_DELISTED_DIR.glob('*.parquet')))}개 로컬 캐시에서 로드")
    return rows


def _fetch_full_universe(market, lookback_days=DEFAULT_LOOKBACK_DAYS, include_delisted=False):
    universe = ts.load_universe(market, include_delisted=include_delisted)
    tickers = [t for _, _, t, _, _ in universe if not t.endswith(".DL")]
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
            has_open = "Open" in sub.columns
            for idx, row in sub.iterrows():
                rows.append({
                    "ticker": t, "date": idx.strftime("%Y-%m-%d"),
                    "close": float(row["Close"]), "high": float(row["High"]), "low": float(row["Low"]),
                    "open": float(row["Open"]) if has_open and row["Open"] == row["Open"] else float(row["Close"]),
                    "volume": float(row["Volume"]) if has_volume and row["Volume"] == row["Volume"] else None,
                })
        print(f"[local_price_cache] {market} {min(i + CHUNK, len(tickers))}/{len(tickers)}종목 처리")

    if include_delisted:
        rows += _load_delisted_rows(market)

    df_all = pd.DataFrame(rows)
    df_all.to_parquet(_cache_path(market, include_delisted), engine="pyarrow", compression="zstd", index=False)
    print(f"[local_price_cache] {market} 캐시 저장 완료: {len(df_all)}행 ({df_all['ticker'].nunique()}종목)")
    return df_all


def _load_or_build_cache(market, max_age_hours=12, force_refresh=False, include_delisted=False):
    path = _cache_path(market, include_delisted)
    if not force_refresh and path.exists():
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours < max_age_hours:
            print(f"[local_price_cache] {market} 캐시 재사용(생성 {age_hours:.1f}시간 전)")
            return pd.read_parquet(path)
        print(f"[local_price_cache] {market} 캐시가 {age_hours:.1f}시간 지나 다시 받습니다")
    return _fetch_full_universe(market, include_delisted=include_delisted)


def cached_fetch_ohlc_history_batches(market, max_age_hours=12, force_refresh=False, include_delisted=False):
    """screening_backtest.run_screening_backtest의 fetch_fn 인자로 바로 넘길 수
    있는 (tickers, start_date, end_date) -> Iterable[(ticker, bars)] 함수를 반환한다.
    include_delisted=True면 data_pipeline.fetch_delisted_kr로 미리 받아둔 상장폐지
    종목 시세도 함께 캐시에 포함한다(생존편향 제거용 - vcp_strategy.run_vcp_backtest에
    include_delisted=True와 함께 넘겨야 실제로 유니버스에도 반영된다)."""
    df = _load_or_build_cache(
        market, max_age_hours=max_age_hours, force_refresh=force_refresh, include_delisted=include_delisted)

    def _fetch(tickers, start_date, end_date):
        wanted = set(tickers)
        sub = df[df["ticker"].isin(wanted) & (df["date"] >= start_date) & (df["date"] < end_date)]
        for ticker, g in sub.groupby("ticker"):
            g = g.sort_values("date")
            bars = []
            for r in g.itertuples():
                # 캐시 파일이 open 컬럼 추가 전에 만들어졌을 수 있어(구버전 캐시)
                # 없거나 NaN이면 종가로 대신한다.
                o = getattr(r, "open", None)
                if o is None or o != o:
                    o = r.close
                bars.append({"date": r.date, "close": r.close, "high": r.high, "low": r.low, "open": o, "volume": r.volume})
            if bars:
                yield ticker, bars

    return _fetch
