"""마크 미너비니류 '트렌드 템플레이트' 스크리닝 엔진.

가격·이동평균선·52주 고저가만으로 판정하는 7개 정량 조건 + IBD 상대강도(RS)를
직접 근사 계산한 조건 8, 총 8개 조건을 모두 만족하는 종목을 찾는다.

1. 현재가 > 150일선 및 200일선
2. 150일선 > 200일선
3. 200일선이 최근 1개월 이상 상승 추세
4. 50일선 > 150일선 및 200일선
5. 현재가 > 50일선
6. 현재가가 52주 신저가보다 30% 이상 위
7. 현재가가 52주 신고가의 25% 이내
8. RS Rating(상대강도) >= 임계값(기본 70)

RS Rating 근사: IBD의 정확한 산출식은 비공개이지만, 최근 분기에 가중치를 더 준
가중평균 수익률(최근 3개월 40% + 6/9/12개월 각 20%)로 전체 스크리닝 대상 종목의
순위를 매겨 1~99 백분위로 환산하는 방식이 널리 쓰이는 근사법이라 이를 사용한다.
전체 시장이 아니라 이 스크리너가 다루는 유니버스(코스피/코스닥 전체 또는
S&P500+나스닥 상위) 안에서의 상대 순위라는 한계가 있다.

VCP(변동성 축소) 패턴이나 손절 규칙 등은 시각적/재량적 판단이 필요한 영역이라
정량 스크리닝 대상에서 제외했다 - 8개 조건은 "추세 템플릿" 통과 여부만 판정한다.

stage(1~4): 스탠 와인스타인/미너비니류 4단계 사이클 근사 분류(classify_stage 참고).
이평선 정배열·역배열 여부와 52주 레인지 내 위치만으로 판정하는 스냅샷 근사치라,
실제 판단에 중요한 돌파 거래량이나 추세 지속 기간은 반영하지 않는다.
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf

BASE_DIR = Path(__file__).parent
MIN_BARS = 260  # 200일선 + 최소 1개월 추세 확인에 필요한 최소 거래일 수
DEFAULT_RS_THRESHOLD = 70


def load_universe(market):
    """반환: [(code, name, yf_ticker, industry, sector), ...]"""
    filename = "kr_stocks.json" if market == "KR" else "us_stocks.json"
    with open(BASE_DIR / filename, "r", encoding="utf-8") as f:
        stocks = json.load(f)
    if market == "KR":
        tickers = [
            (s["code"], s["name"], f"{s['code']}.KQ" if s.get("market") == "KOSDAQ" else f"{s['code']}.KS",
             s.get("industry"), s.get("sector"))
            for s in stocks
        ]
    else:
        tickers = [(s["code"], s["name"], s["code"], s.get("industry"), s.get("sector")) for s in stocks]
    return tickers


def fetch_ohlc_history_batch(tickers, start_date, end_date, max_attempts=3, dl_timeout=30, backoff_base=1.5):
    """여러 종목의 전체 기간 OHLC 히스토리를 배치로 가져온다.

    max_attempts/dl_timeout/backoff_base로 재시도 강도를 조절할 수 있다 - 백그라운드
    스크리닝(전체 유니버스)처럼 실패해도 다음 주기에 다시 채우면 되는 경우는 기본값
    (넉넉한 재시도)을 쓰고, 종목 상세 모달처럼 사용자가 요청 중에 기다리는 경우는
    호출부에서 더 가벼운 값을 넘겨 Render 요청 타임아웃(30초)에 걸리지 않게 한다
    (재시도 3회×30초 타임아웃이면 최악의 경우 요청 하나가 90초 넘게 걸려 그 자체로
    요청이 죽는 문제가 있었다).

    반환: {ticker: [{"date":..., "close":..., "high":..., "low":...}, ...]} (날짜 오름차순)
    """
    if not tickers:
        return {}
    # 청크를 작게, threads=False로 순간 메모리/CPU 사용량을 낮춘다(운영 서버 메모리
    # 한도에서 여러 워커가 동시에 큰 배치를 처리하다 죽는 문제를 피하기 위함).
    CHUNK = 25
    history = {}

    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        df = None
        for attempt in range(max_attempts):
            try:
                df = yf.download(
                    chunk, start=start_date, end=end_date,
                    progress=False, auto_adjust=False, timeout=dl_timeout, group_by="ticker", threads=False,
                )
                if df is not None and not df.empty:
                    break
            except Exception:
                df = None
            time.sleep(backoff_base * (attempt + 1))
        else:
            continue
        time.sleep(0.5)
        if df is None or df.empty:
            continue

        # yfinance는 티커를 리스트로 넘기면 원소가 1개여도(group_by="ticker") 항상
        # (티커, 필드) 멀티인덱스 컬럼을 돌려준다 - 리스트가 아니라 문자열 하나만
        # 넘길 때만 컬럼이 flat이 된다. 그래서 청크 크기와 무관하게 항상 df[t]로
        # 접근해야 한다(예전에 len(chunk)==1일 때 flat이라고 잘못 가정해 단일 종목
        # 조회가 항상 빈 결과를 반환하던 버그가 있었다).
        for t in chunk:
            try:
                sub = df[t]
            except KeyError:
                continue
            bars = _extract_bars(sub)
            if bars:
                history[t] = bars

    return history


def _extract_bars(df):
    needed = ("Close", "High", "Low")
    if not all(c in df.columns for c in needed):
        return []
    sub = df.dropna(subset=list(needed))
    has_volume = "Volume" in sub.columns
    return [
        {"date": idx.strftime("%Y-%m-%d"), "close": float(row["Close"]),
         "high": float(row["High"]), "low": float(row["Low"]),
         "volume": float(row["Volume"]) if has_volume and row["Volume"] == row["Volume"] else None}
        for idx, row in sub.iterrows()
    ]


def _sma(closes, period, idx):
    if idx + 1 < period:
        return None
    window = closes[idx + 1 - period: idx + 1]
    return sum(window) / period


def _weighted_return_score(closes):
    n = len(closes)
    if n < 252:
        return None
    last = closes[-1]

    def ret(days_ago):
        idx = n - 1 - days_ago
        if idx < 0:
            return None
        base = closes[idx]
        if base <= 0:
            return None
        return last / base - 1

    r3, r6, r9, r12 = ret(63), ret(126), ret(189), ret(252)
    if None in (r3, r6, r9, r12):
        return None
    return r3 * 0.4 + r6 * 0.2 + r9 * 0.2 + r12 * 0.2


def _volume_stats(bars):
    """최근 거래일 거래량과 상대거래량(최근 거래량 / 직전 20거래일 평균거래량)을 계산한다.
    일봉 데이터만 있어 TradingView의 장중 상대볼륨과는 다르게, 전일 마감 기준 근사치다."""
    recent = [b.get("volume") for b in bars[-21:]]
    recent = [v for v in recent if v is not None]
    if not recent:
        return None, None
    volume = recent[-1]
    prior = recent[:-1]
    if not prior:
        return volume, None
    avg_prior = sum(prior) / len(prior)
    rel_volume = round(volume / avg_prior, 2) if avg_prior > 0 else None
    return volume, rel_volume


def classify_stage(price, ma50, ma150, ma200, ma200_rising, week52_high, week52_low):
    """와인스타인/미너비니류 4단계 사이클 근사 분류.

    정확한 판정에는 몇 주에 걸친 추세 지속성과 돌파 시점의 거래량까지 봐야 하지만,
    이 함수는 배치 스크리닝 시점의 스냅샷(이평선 배열 + 52주 레인지 내 위치)만으로
    근사한다:
    - 2단계(상승): 정배열(가격>50일선>150일선>200일선)이고 200일선도 상승 중.
    - 4단계(하락): 역배열(가격<50일선<150일선<200일선)이고 200일선도 하락/횡보.
    - 그 외(이평선이 뒤섞여 방향이 뚜렷하지 않음)는 52주 레인지 내 가격 위치로
      1단계(저점권 횡보 = 바닥 다지기)/3단계(고점권 횡보 = 천장)를 구분한다.
      직전 단계 이력이 있으면 더 정확히 구분할 수 있지만 배치 스냅샷에는 없다.
    """
    if price > ma50 > ma150 > ma200 and ma200_rising:
        return 2
    if price < ma50 < ma150 < ma200 and not ma200_rising:
        return 4
    range_pos = (price - week52_low) / (week52_high - week52_low) if week52_high > week52_low else 0.5
    return 1 if range_pos < 0.5 else 3


def evaluate_trend_template(code, name, bars, industry=None, sector=None):
    """bars: 날짜 오름차순 OHLC 리스트. 조건1~7과 RS 계산용 가중수익률을 반환한다
    (RS 백분위 자체는 유니버스 전체를 모아야 계산 가능해 compute_universe_screen에서 처리)."""
    if len(bars) < MIN_BARS:
        return None

    # 거래정지/상장폐지 임박 종목은 야후가 마지막 시세를 그대로 반복해 보여주는
    # 경우가 있는데(거래량 0), 이 "평평한 정지 구간"이 급등처럼 보여 트렌드
    # 템플릿을 통과하는 오탐이 있었다(실제 사례: 031860, 거래량 0인 채로 가격이
    # 10배로 튐 - 실제 매매가 아니라 액면병합 등 표시상의 변화). 최근 10거래일
    # 중 거래량이 있는 날이 없으면 후보에서 제외한다.
    recent_volumes = [b.get("volume") for b in bars[-10:]]
    known_volumes = [v for v in recent_volumes if v is not None]
    if known_volumes and all(v == 0 for v in known_volumes):
        return None

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    idx = len(closes) - 1
    price = closes[idx]

    ma50 = _sma(closes, 50, idx)
    ma150 = _sma(closes, 150, idx)
    ma200 = _sma(closes, 200, idx)
    ma200_1m_ago = _sma(closes, 200, idx - 21) if idx - 21 >= 0 else None

    if None in (ma50, ma150, ma200, ma200_1m_ago):
        return None

    week52_high = max(highs[-252:])
    week52_low = min(lows[-252:])
    weighted_return = _weighted_return_score(closes)
    volume, rel_volume = _volume_stats(bars)

    conditions = {
        "priceAboveMa150And200": price > ma150 and price > ma200,
        "ma150AboveMa200": ma150 > ma200,
        "ma200Rising": ma200 > ma200_1m_ago,
        "ma50AboveMa150And200": ma50 > ma150 and ma50 > ma200,
        "priceAboveMa50": price > ma50,
        "priceAbove52wLowBy30pct": week52_low > 0 and price >= week52_low * 1.30,
        "priceWithin25pctOf52wHigh": week52_high > 0 and price >= week52_high * 0.75,
    }

    stage = classify_stage(price, ma50, ma150, ma200, conditions["ma200Rising"], week52_high, week52_low)

    return {
        "code": code, "name": name, "industry": industry, "sector": sector, "price": round(price, 2),
        "ma50": round(ma50, 2), "ma150": round(ma150, 2), "ma200": round(ma200, 2),
        "week52High": round(week52_high, 2), "week52Low": round(week52_low, 2),
        "pctAbove52wLow": round((price / week52_low - 1) * 100, 1) if week52_low > 0 else None,
        "pctBelow52wHigh": round((1 - price / week52_high) * 100, 1) if week52_high > 0 else None,
        "conditions": conditions,
        "stage": stage,
        "weightedReturn": weighted_return,
        "volume": volume,
        "relVolume": rel_volume,
    }


def compute_universe_screen(evaluated_list, rs_threshold=DEFAULT_RS_THRESHOLD):
    """RS 백분위(1~99)를 유니버스 내 상대 순위로 계산해 조건8을 채우고, 8개 조건을
    모두 만족하는 종목을 판정한다. evaluated_list를 그대로 수정해 반환한다."""
    with_return = [e for e in evaluated_list if e["weightedReturn"] is not None]
    with_return.sort(key=lambda e: e["weightedReturn"])
    total = len(with_return)
    for i, e in enumerate(with_return):
        percentile = (i + 1) / total * 98 + 1  # 최하위=1, 최상위=99
        e["rsRating"] = round(percentile)

    for e in evaluated_list:
        rs = e.get("rsRating")
        e["conditions"]["rsAboveThreshold"] = rs is not None and rs >= rs_threshold
        e["passCount"] = sum(1 for v in e["conditions"].values() if v)
        e["allPass"] = e["passCount"] == len(e["conditions"])
        e.pop("weightedReturn", None)

    evaluated_list.sort(key=lambda e: (-e["passCount"], -(e.get("rsRating") or 0)))
    return evaluated_list


def run_screen(market, rs_threshold=DEFAULT_RS_THRESHOLD):
    """market: "KR" 또는 "US". 유니버스 전체를 스크리닝해 평가 결과 리스트를 반환한다."""
    universe = load_universe(market)
    tickers = [t for _, _, t, _, _ in universe]
    info_by_ticker = {t: (code, name, industry, sector) for code, name, t, industry, sector in universe}

    end = datetime.today()
    start = end - timedelta(days=450)
    history = fetch_ohlc_history_batch(tickers, start.strftime("%Y-%m-%d"), (end + timedelta(days=1)).strftime("%Y-%m-%d"))

    evaluated = []
    for ticker, bars in history.items():
        code, name, industry, sector = info_by_ticker.get(ticker, (ticker, ticker, None, None))
        result = evaluate_trend_template(code, name, bars, industry, sector)
        if result:
            evaluated.append(result)

    return compute_universe_screen(evaluated, rs_threshold)
