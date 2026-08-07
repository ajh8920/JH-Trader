"""국내 퀀트(재무지표 팩터) 스크리닝 및 연간 리밸런싱 백테스트 엔진.

DART에서 미리 받아 KrFundamental에 캐시해둔 연간 재무데이터(자본총계/당기순이익)를
바탕으로, 매 리밸런싱 시점마다 "그 시점까지 실제로 공시되어 있던"(rcept_date 기준)
가장 최근 연도 데이터로 저PER+고ROE 콤보 순위를 매겨 상위 N종목을 동일비중으로
매수·보유하고, 다음 리밸런싱 때 전량 재편성한다(강환국류 '마법공식' 스타일).

한계(정확도에 영향):
- 시가총액은 "현재 상장주식수 × 그 시점 종가"로 근사한다. 상장주식수 변동(유상증자,
  자사주 소각, 액면분할 등) 이력은 추적하지 않으므로 과거로 갈수록 부정확해질 수 있다.
- 리밸런싱은 연 1회(기본 4월 1일) 고정이며, 그 사이 일별 가격은 추적하지 않고
  리밸런싱 시점 간 스냅샷으로만 수익률을 계산한다.
- 상장폐지/거래정지 종목은 가격 조회가 실패하면 그냥 후보에서 빠진다(생존 편향
  가능성 - 폐지 직전 부실기업이 화면에서 조용히 사라짐).
"""

import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf

from models import KrFundamental

BASE_DIR = Path(__file__).parent
MIN_MARKET_CAP_DEFAULT = 50_000_000_000  # 500억원

# 현재가 캐시: Render의 gunicorn 요청 타임아웃(30초)보다 "재무데이터 있는 종목
# 전체(1,500개+)의 현재가를 매 요청마다 실시간 조회"가 훨씬 오래 걸려 요청이
# 통째로 죽는 문제가 있었다. 그래서 스크리닝(오늘 기준)은 요청 처리 중이 아니라
# 백그라운드 스레드가 미리 채워둔 이 캐시를 읽기만 한다(app.py에서 주기적으로
# warm_current_price_cache()를 호출).
_price_cache = {"prices": {}, "at": 0.0}


def get_cached_prices():
    return _price_cache["prices"]


def price_cache_age_seconds():
    if not _price_cache["prices"]:
        return None
    return time.time() - _price_cache["at"]


def warm_current_price_cache():
    """재무데이터가 있는 전 종목의 현재가를 받아 캐시에 채운다(수 분 걸릴 수 있음 -
    반드시 백그라운드 스레드에서만 호출해야 한다)."""
    market_map = _load_market_map()
    codes = [
        row.stock_code for row in
        KrFundamental.query.with_entities(KrFundamental.stock_code).distinct().all()
    ]
    today = datetime.today().strftime("%Y-%m-%d")
    prices = fetch_prices_near_date(codes, market_map, today, window_days=10)
    _price_cache["prices"] = prices
    _price_cache["at"] = time.time()
    return len(prices)


def _load_kr_stocks():
    with open(BASE_DIR / "kr_stocks.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _load_market_map():
    return {s["code"]: s["market"] for s in _load_kr_stocks()}


def get_shares_outstanding_map():
    """kr_stocks.json에 함께 저장해둔 상장주식수 스냅샷을 쓴다(정적 근사치).

    매 요청마다 FinanceDataReader를 살아있는 상태로 호출하면 무겁고(운영 서버에
    matplotlib 등 큰 의존성이 추가됨) 굳이 실시간일 필요도 없는 값이라, kr_stocks.json
    생성 시점에 함께 받아둔 값을 그대로 쓴다. 최신화가 필요하면 kr_stocks.json을
    다시 생성하면 된다.
    """
    return {s["code"]: s["shares"] for s in _load_kr_stocks() if s.get("shares")}


def latest_fundamentals_as_of(rebalance_date_str, rows=None):
    """리밸런싱 시점까지 실제로 공시되어 있던(rcept_date <= 시점) 가장 최근 연도의
    재무데이터를 종목별로 골라 {stock_code: KrFundamental} 형태로 반환한다."""
    if rows is None:
        rows = KrFundamental.query.filter(KrFundamental.rcept_no != "").all()
    best = {}
    for r in rows:
        rd = r.rcept_date
        if not rd or rd > rebalance_date_str:
            continue
        cur = best.get(r.stock_code)
        if cur is None or r.bsns_year > cur.bsns_year:
            best[r.stock_code] = r
    return best


def _yf_ticker(code, market_map):
    return f"{code}.KQ" if market_map.get(code) == "KOSDAQ" else f"{code}.KS"


def fetch_prices_near_date(codes, market_map, date_str, window_days=10):
    """여러 종목의 특정 날짜 또는 그 직전 가장 가까운 거래일 종가를 배치로 가져온다."""
    if not codes:
        return {}
    end = datetime.strptime(date_str, "%Y-%m-%d")
    start = end - timedelta(days=window_days)
    prices = {}
    CHUNK = 40  # 청크를 너무 크게/자주 돌리면 야후 쪽 요청 제한(YFRateLimitError)에 걸린다
    tickers_all = [_yf_ticker(c, market_map) for c in codes]
    code_by_ticker = dict(zip(tickers_all, codes))

    for i in range(0, len(tickers_all), CHUNK):
        chunk = tickers_all[i:i + CHUNK]
        df = None
        for attempt in range(3):
            try:
                df = yf.download(
                    chunk, start=start.strftime("%Y-%m-%d"), end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                    progress=False, auto_adjust=False, timeout=25, group_by="ticker", threads=True,
                )
                if df is not None and not df.empty:
                    break
            except Exception:
                df = None
            time.sleep(1.5 * (attempt + 1))  # 요청 제한 걸렸을 때 점점 더 오래 쉬고 재시도
        else:
            continue
        time.sleep(0.3)  # 청크 사이에도 짧게 쉬어 다음 청크가 제한에 덜 걸리게 한다
        if df is None or df.empty:
            continue

        if len(chunk) == 1:
            closes = df["Close"].dropna() if "Close" in df.columns else None
            if closes is not None and not closes.empty:
                prices[code_by_ticker[chunk[0]]] = float(closes.iloc[-1])
            continue

        for t in chunk:
            try:
                sub = df[t]
            except KeyError:
                continue
            closes = sub["Close"].dropna() if "Close" in sub.columns else None
            if closes is not None and not closes.empty:
                prices[code_by_ticker[t]] = float(closes.iloc[-1])

    return prices


def rank_candidates(rebalance_date_str, market_map, shares_map, fundamentals_rows, min_market_cap, top_n,
                     prices=None):
    """prices를 넘기지 않으면 그 시점 가격을 실시간으로 조회한다(과거 리밸런싱 시점용,
    느림 - 반드시 백그라운드 작업에서만 호출). 오늘 기준 스크리닝은 미리 채워둔
    get_cached_prices()를 prices로 넘겨 요청 중 실시간 조회를 피한다."""
    fundamentals = latest_fundamentals_as_of(rebalance_date_str, fundamentals_rows)
    candidates = {
        code: f for code, f in fundamentals.items()
        if f.net_income and f.net_income > 0 and f.total_equity and f.total_equity > 0
    }
    if not candidates:
        return []

    if prices is None:
        codes = list(candidates.keys())
        prices = fetch_prices_near_date(codes, market_map, rebalance_date_str)

    rows = []
    for code, f in candidates.items():
        price = prices.get(code)
        shares = shares_map.get(code)
        if not price or not shares:
            continue
        market_cap = price * shares
        if market_cap < min_market_cap:
            continue
        roe = f.net_income / f.total_equity * 100
        # 직전 연도에 자본잠식(자본총계가 마이너스)이었다가 일회성 이익으로 자본이
        # 아주 작은 플러스로 돌아선 경우 ROE가 수백 %로 튀는 통계적 왜곡이 생긴다
        # (예: 자본 130억원에 일회성 이익 500억원 → ROE 387%). 지속 가능한 수익성을
        # 보려는 지표이니 이런 극단치는 걸러낸다.
        if roe > 100:
            continue
        rows.append({
            "code": code, "name": f.corp_name, "price": round(price, 2),
            "marketCap": round(market_cap, 0), "per": round(market_cap / f.net_income, 2),
            "roe": round(roe, 2), "bsnsYear": f.bsns_year,
        })

    rows.sort(key=lambda r: r["per"])
    for i, r in enumerate(rows):
        r["perRank"] = i + 1
    rows.sort(key=lambda r: -r["roe"])
    for i, r in enumerate(rows):
        r["roeRank"] = i + 1
    for r in rows:
        r["combinedRank"] = r["perRank"] + r["roeRank"]
    rows.sort(key=lambda r: (r["combinedRank"], r["per"]))
    return rows[:top_n]


def _max_drawdown_pct(values):
    if not values:
        return 0.0
    peak = values[0]
    mdd = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak * 100
            if dd > mdd:
                mdd = dd
    return round(mdd, 2)


def run_quant_backtest(start_year, end_year, seed, top_n=20, min_market_cap=MIN_MARKET_CAP_DEFAULT,
                        rebalance_month_day="04-01"):
    if start_year >= end_year:
        return {"error": "종료연도는 시작연도보다 이후여야 합니다"}

    fundamentals_rows = KrFundamental.query.filter(KrFundamental.rcept_no != "").all()
    if not fundamentals_rows:
        return {"error": "재무데이터가 아직 준비되지 않았습니다. 잠시 후 다시 시도해주세요."}

    market_map = _load_market_map()
    shares_map = get_shares_outstanding_map()

    rebalance_dates = [f"{y}-{rebalance_month_day}" for y in range(start_year, end_year + 1)]
    today_str = datetime.today().strftime("%Y-%m-%d")
    rebalance_dates = [d for d in rebalance_dates if d <= today_str]
    if len(rebalance_dates) < 2:
        return {"error": "리밸런싱 시점이 2회 미만입니다(기간을 늘려주세요)"}

    portfolio_value = seed
    holdings = {}
    equity_curve = []
    trades = []
    picks_log = []

    for date_str in rebalance_dates:
        picks = rank_candidates(date_str, market_map, shares_map, fundamentals_rows, min_market_cap, top_n)

        if holdings:
            exit_prices = fetch_prices_near_date(list(holdings.keys()), market_map, date_str)
            new_value = 0.0
            for code, h in holdings.items():
                px = exit_prices.get(code, h["entryPrice"])
                proceeds = h["shares"] * px
                new_value += proceeds
                trades.append({
                    "date": date_str, "action": "sell", "code": code, "name": h["name"],
                    "price": round(px, 2), "qty": h["shares"],
                    "pnlPct": round((px - h["entryPrice"]) / h["entryPrice"] * 100, 2),
                })
            portfolio_value = new_value

        equity_curve.append({"date": date_str, "value": round(portfolio_value, 2)})

        if not picks:
            picks_log.append({"date": date_str, "picks": []})
            holdings = {}
            continue

        picks_log.append({"date": date_str, "picks": picks})
        per_stock_budget = portfolio_value / len(picks)
        holdings = {}
        for p in picks:
            qty = math.floor(per_stock_budget / p["price"])
            if qty <= 0:
                continue
            holdings[p["code"]] = {"shares": qty, "entryPrice": p["price"], "name": p["name"]}
            trades.append({
                "date": date_str, "action": "buy", "code": p["code"], "name": p["name"],
                "price": p["price"], "qty": qty, "per": p["per"], "roe": p["roe"],
            })

    if holdings:
        final_prices = fetch_prices_near_date(list(holdings.keys()), market_map, today_str)
        final_value = 0.0
        for code, h in holdings.items():
            px = final_prices.get(code, h["entryPrice"])
            final_value += h["shares"] * px
        portfolio_value = final_value
        equity_curve.append({"date": today_str, "value": round(portfolio_value, 2)})

    total_return_pct = round((portfolio_value - seed) / seed * 100, 2) if seed > 0 else 0.0
    mdd = _max_drawdown_pct([e["value"] for e in equity_curve])

    # 벤치마크: 코스피 지수(^KS11)를 첫 리밸런싱일에 매수해 그대로 보유
    benchmark_curve = []
    try:
        kospi = yf.download("^KS11", start=rebalance_dates[0], end=today_str, progress=False, timeout=15)
        if kospi is not None and not kospi.empty:
            if hasattr(kospi.columns, "get_level_values") and kospi.columns.nlevels > 1:
                kospi.columns = kospi.columns.get_level_values(0)
            first_close = float(kospi["Close"].dropna().iloc[0])
            for e in equity_curve:
                d = datetime.strptime(e["date"], "%Y-%m-%d")
                window = kospi.loc[:e["date"]]
                if window.empty:
                    continue
                px = float(window["Close"].dropna().iloc[-1])
                benchmark_curve.append({"date": e["date"], "value": round(seed * (px / first_close), 2)})
    except Exception:
        benchmark_curve = []

    bh_return_pct = (
        round((benchmark_curve[-1]["value"] - seed) / seed * 100, 2) if benchmark_curve else None
    )

    return {
        "startYear": start_year, "endYear": end_year, "seed": seed, "topN": top_n,
        "minMarketCap": min_market_cap, "rebalanceDates": rebalance_dates,
        "finalValue": round(portfolio_value, 2), "totalReturnPct": total_return_pct,
        "mddPct": mdd,
        "benchmark": {"label": "코스피 매수후보유", "returnPct": bh_return_pct, "equityCurve": benchmark_curve},
        "equityCurve": equity_curve, "trades": list(reversed(trades)), "picksLog": list(reversed(picks_log)),
    }
