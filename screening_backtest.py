"""스크리닝(트렌드 템플릿/미너비니 단계) 전략을 과거 구간에 대해 워크포워드로
시뮬레이션한다 - "이 전략으로 스크리닝했다면 그동안 어떤 종목을 언제 사서
언제 팔았을지, 손익이 얼마였을지"를 보여주는 것이 목적이다.

## 룩어헤드 편향 방지 (RULES.md R3)
각 재평가 시점의 트렌드 템플릿 조건·RS 등급·단계는 그 시점까지의 가격만으로
매번 다시 계산한다 - trend_screener.evaluate_trend_template/compute_universe_screen을
그대로 재사용한다(실제 스크리닝 화면과 다른 판정 로직을 쓰면 백테스트가 화면과
다른 결과를 보여주는 문제가 생기고, 미래 가격을 참조해 "그때도 조건을
만족했었다"고 판정하면 실제로는 알 수 없었던 정보로 매수한 것이 된다).

## 지원 전략 범위
가격 데이터만으로 계산 가능한 전략만 지원한다(트렌드 템플릿 8/8, 단계 N 이상).
CANSLIM/딥밸류처럼 PER·ROE 등 재무 지표에 의존하는 전략 프리셋은, 이 화면이
과거 시점의 재무 스냅샷을 시계열로 갖고 있지 않아(현재 값만 캐시) 여기서는
지원하지 않는다.

## 매매 규칙
- 재평가(리밸런싱) 주기: 주 단위. 매일 전종목을 재계산하면 계산량이 지나치게
  커진다(국내 전종목 1회 평가에 로컬 기준 몇 분 단위) - 주 단위면 그 배수로
  늘어난다. 스윙성 전략에는 충분한 감도라고 판단했다.
- 매도: 손절(-N%, 기본 -8%) 또는 조건 이탈(그 재평가 시점에 더 이상 전략
  조건을 만족하지 않음) 중 먼저 오는 재평가 시점에 청산한다. 고정 익절 상한은
  두지 않는다 - kr_swing.py의 combo 전략과 같은 이유(강한 추세장에서 상승분을
  다 못 먹는 문제)로 조건 이탈 자체를 청산 신호로 쓴다.
- 손절은 직전~이번 재평가 시점 사이의 일별 저가를 스캔해 손절선에 처음 닿은
  날 그 손절가에 청산된 것으로 본다(재평가 시점 종가만 보면 그 사이 급락분을
  다 뒤집어써 실제 손절폭보다 훨씬 큰 손실로 잡히는 문제가 있었다). 시가
  데이터가 없어 갭하락으로 손절가 아래에서 시작한 경우까지는 반영하지 못한다.
- 신규 매수: 이번 재평가에 조건을 만족하는데 아직 보유하지 않은 종목 중,
  RS 등급이 높은 순으로 비어 있는 슬롯 수만큼 채운다.
- 포지션 크기: 슬롯당 동일 비중(시드 ÷ 최대 보유 종목 수)으로 고정한다.

## 성과 지표
알파(초과수익률)는 같은 기간 지수(국내=코스피 ^KS11, 미국=S&P500 ^GSPC)를
첫 재평가일에 매수해 그대로 보유했을 때와 비교한 단순 차이다(전략 수익률 -
벤치마크 수익률) - kr_quant.py 리밸런싱 백테스트의 벤치마크 계산과 같은
방식이다. 손익비는 이긴 거래의 평균 수익률 ÷ 진 거래의 평균 손실률(절대값).
"""

import bisect
from datetime import datetime, timedelta

import yfinance as yf

import trend_screener as ts

DEFAULT_STOP_LOSS_PCT = -8.0
DEFAULT_MAX_POSITIONS = 10
RESCAN_INTERVAL_DAYS = 7
WARMUP_DAYS = 450  # MIN_BARS(200일선) + RS 계산용 12개월 수익률 확보
BENCHMARK_TICKER = {"KR": "^KS11", "US": "^GSPC"}  # KOSPI / S&P 500
BENCHMARK_LABEL = {"KR": "KOSPI Buy & Hold", "US": "S&P 500 Buy & Hold"}


def _profit_loss_ratio(trades):
    # 손익비 = 이긴 거래의 평균 수익률 ÷ 진 거래의 평균 손실률(절대값) - kr_swing.py의
    # 같은 지표와 동일한 정의. 승률과 함께 봐야 전략의 기대값을 판단할 수 있다.
    wins = [t["pnlPct"] for t in trades if t["pnlPct"] > 0]
    losses = [t["pnlPct"] for t in trades if t["pnlPct"] < 0]
    if not losses:
        return None
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses))
    if avg_loss == 0:
        return None
    return round(avg_win / avg_loss, 2)


def _fetch_benchmark_curve(market, dates, seed):
    """지수(코스피/S&P500)를 첫 재평가일에 매수해 그대로 보유했을 때의 평가액
    곡선 - kr_quant.py의 벤치마크 계산과 같은 방식이다. 실패해도 백테스트
    자체는 계속 진행해야 하므로 예외는 조용히 삼키고 빈 결과를 돌려준다."""
    if not dates:
        return [], None
    ticker = BENCHMARK_TICKER.get(market)
    if not ticker:
        return [], None
    try:
        idx = yf.download(ticker, start=dates[0], end=dates[-1], progress=False, timeout=15)
        if idx is None or idx.empty:
            return [], None
        if hasattr(idx.columns, "get_level_values") and idx.columns.nlevels > 1:
            idx.columns = idx.columns.get_level_values(0)
        closes = idx["Close"].dropna()
        first_close = float(closes.iloc[0])
        curve = []
        for d in dates:
            window = closes.loc[:d]
            if window.empty:
                continue
            px = float(window.iloc[-1])
            curve.append({"date": d, "value": round(seed * (px / first_close), 2)})
        if not curve:
            return [], None
        return_pct = round((curve[-1]["value"] - seed) / seed * 100, 2)
        return curve, return_pct
    except Exception:
        return [], None


def _fetch_index_series(market, fetch_start, fetch_end):
    """시장 레짐 필터용 - 지수 자체의 종가 시계열을 받아온다(개별 종목과 같은
    워밍업 구간을 써서 재평가 시점마다 그 지수의 200일선을 계산할 수 있게 한다).
    실패하면 (빈 리스트, 빈 리스트)를 돌려주고, 호출부에서 레짐 필터를 그냥
    끄는 것으로 처리한다(필터를 켰다고 백테스트 자체가 죽으면 안 된다)."""
    ticker = BENCHMARK_TICKER.get(market)
    if not ticker:
        return [], []
    try:
        idx = yf.download(ticker, start=fetch_start, end=fetch_end, progress=False, timeout=15)
        if idx is None or idx.empty:
            return [], []
        if hasattr(idx.columns, "get_level_values") and idx.columns.nlevels > 1:
            idx.columns = idx.columns.get_level_values(0)
        closes = idx["Close"].dropna()
        return [d.strftime("%Y-%m-%d") for d in closes.index], [float(v) for v in closes.values]
    except Exception:
        return [], []


def _strategy_predicate(strategy):
    """strategy: 'trendTemplate' 또는 'stageN'(N=1~4)."""
    if strategy and strategy.startswith("stage"):
        try:
            n = int(strategy[len("stage"):])
        except ValueError:
            n = 2
        return lambda e: (e.get("stage") or 0) >= n
    return lambda e: bool(e.get("allPass"))


def _strategy_label(strategy):
    if strategy and strategy.startswith("stage"):
        try:
            n = int(strategy[len("stage"):])
            return f"Stage {n}+"
        except ValueError:
            pass
    return "Trend Template 8/8"


def _kr_quant_latest_fundamentals_as_of(rebalance_date_str, fundamentals_rows):
    """kr_quant.latest_fundamentals_as_of를 그대로 재사용(rcept_date 기준 룩어헤드
    편향 방지 로직 중복 구현 방지). 모듈 최상단에서 import하면 kr_quant.py가
    끌고 오는 models.py(Flask-SQLAlchemy) 의존을 이 순수 계산 모듈에 항상
    강제하게 되므로, 가치/퀄리티 팩터를 실제로 쓸 때만 지연 import한다."""
    from kr_quant import latest_fundamentals_as_of
    return latest_fundamentals_as_of(rebalance_date_str, fundamentals_rows)


def _realized_vol(closes, i, window=60):
    """i번 인덱스까지 최근 window거래일 일간수익률의 연환산 변동성(%) - 저변동성
    팩터용. 데이터가 부족하면 None(호출부에서 이 팩터 필터를 통과 못 시킴)."""
    if i < window:
        return None
    rets = []
    for k in range(i - window + 1, i + 1):
        if closes[k - 1] and closes[k - 1] > 0 and closes[k] is not None:
            rets.append(closes[k] / closes[k - 1] - 1)
    if len(rets) < window // 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / len(rets)
    return (var ** 0.5) * (252 ** 0.5) * 100


def _avg_trade_value(closes, volumes, i, window=20):
    """i번 인덱스까지 최근 window거래일 평균 거래대금(원) - 유동성 필터용."""
    if i < window - 1:
        return None
    vals = [closes[k] * volumes[k] for k in range(i - window + 1, i + 1)
            if volumes[k] is not None and closes[k] is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _atr(highs, lows, closes, i, period=14):
    """i번 인덱스(포함)까지의 ATR(period) - 트루레인지의 단순평균. 데이터가
    부족하면 None(호출부에서 해당 종목은 이번 재평가에 진입 후보에서 제외)."""
    if i < period:
        return None
    total = 0.0
    for k in range(i - period + 1, i + 1):
        prev_close = closes[k - 1] if k > 0 else closes[k]
        total += max(highs[k] - lows[k], abs(highs[k] - prev_close), abs(lows[k] - prev_close))
    return total / period


DEFAULT_RISK_PCT = 1.5
DEFAULT_ATR_PERIOD = 14
DEFAULT_ATR_MULT = 2.0
DEFAULT_BREAKEVEN_R = 1.0
DEFAULT_TRAIL_START_R = 2.0
DEFAULT_TIME_STOP_DAYS = 7
DEFAULT_DD_HALT_PCT = 10.0

EXIT_REASON_LABEL = {
    "initialStop": "초기 손절(2×ATR)", "breakevenStop": "본전 손절", "trailingStop": "트레일링 손절",
    "timeStop": "시간 손절", "periodEnd": "기간 종료",
}

# "미너비니 v2" 전략 프리셋 - 스크리닝 백테스트 웹 화면과 모의투자 탭(paper_trading.py)이
# 정확히 같은 규칙을 쓰도록 파라미터를 한 곳에서만 정의한다(따로 정의하면 둘 사이에
# 파라미터가 슬금슬금 달라질 위험이 있다). 8종 팩터 조합 스윕에서 수익률·알파·손익비
# 전부 1위였던 "유동성만"(모멘텀+유동성, 가치/퀄리티/저변동성 없음) 조합 그대로다.
MINERVINI_V2_PARAMS = {
    "market": "KR", "strategy": "trendTemplate",
    "risk_pct": DEFAULT_RISK_PCT, "atr_period": DEFAULT_ATR_PERIOD, "atr_mult": DEFAULT_ATR_MULT,
    "breakeven_r": DEFAULT_BREAKEVEN_R, "trail_start_r": DEFAULT_TRAIL_START_R,
    "time_stop_days": DEFAULT_TIME_STOP_DAYS, "dd_halt_pct": DEFAULT_DD_HALT_PCT,
    "max_positions": DEFAULT_MAX_POSITIONS, "min_avg_trade_value": 300_000_000,
}


def run_risk_managed_backtest(market, strategy, start_date, end_date,
                               risk_pct=DEFAULT_RISK_PCT, atr_period=DEFAULT_ATR_PERIOD, atr_mult=DEFAULT_ATR_MULT,
                               breakeven_r=DEFAULT_BREAKEVEN_R, trail_start_r=DEFAULT_TRAIL_START_R,
                               time_stop_days=DEFAULT_TIME_STOP_DAYS, dd_halt_pct=DEFAULT_DD_HALT_PCT,
                               max_positions=DEFAULT_MAX_POSITIONS, seed=10_000_000, fetch_fn=None,
                               min_rs=None, min_rel_volume=None, market_regime_filter=False,
                               min_avg_trade_value=None,
                               use_value=False, use_quality=False, use_low_vol=False,
                               value_percentile=50, quality_percentile=50, low_vol_percentile=50,
                               fundamentals_rows=None, shares_map=None):
    """run_screening_backtest과 진입 신호(트렌드 템플릿/단계)는 동일하지만,
    청산 로직을 고정 % 손절 대신 다음 리스크 관리 규칙으로 완전히 대체한 버전:

    - 포지션 크기: 트레이드당 리스크(계좌 평가액의 risk_pct%) ÷ 손절폭(2×ATR)으로
      역산한 주식수. 단, 슬롯당 최대 비중(seed/max_positions)과 보유 현금을
      넘지 않게 캡을 둔다(ATR이 매우 작은 저변동성 종목에 과도하게 몰빵되는
      것을 막기 위함 - 순수 리스크 역산만 쓰면 이론상 슬롯 하나가 계좌 전체를
      먹을 수 있다).
    - 손절: 진입가 - atr_mult×ATR(entry_atr_period). 이후 가격이 유리하게
      움직이면 손절선을 다음 순서로만 올린다(내리지 않음):
        1) +breakeven_r×R 도달 시 손절선을 진입가(본전)로 이동
        2) +trail_start_r×R 도달 이후로는 (그 시점까지의 고가 - atr_mult×ATR)로
           트레일링(고가 갱신될 때마다 재계산, entry_atr은 고정값을 계속 사용 -
           포지션마다 매일 ATR을 재계산하지 않는다는 단순화)
      R(1R)은 진입가-초기손절가(진입 시점 리스크폭)로 고정.
    - 시간 손절: 진입 후 time_stop_days 거래일이 지났는데 아직 종가가 진입가
      이상으로 오르지 못했으면("무진전") 그날 종가로 청산.
    - 손절/시간손절 판정은 직전~이번 재평가 사이 일별 저가/종가를 스캔해
      실제 그 손절선에 처음 닿은 날 기준으로 청산한다(run_screening_backtest와
      같은 이유 - 재평가 시점 종가만 보면 그 사이 급락분을 실제보다 과장/축소
      해서 잡는다).
    - 신규 진입 중단: 이 시점까지의 최고 평가액 대비 낙폭이 dd_halt_pct% 이상이면
      신규 매수를 쉰다(보유 종목 청산 판정은 평소대로 진행). 낙폭은 재평가
      주기(주 단위) 스냅샷 기준으로 판정한다(일별 낙폭까지는 추적하지 않는다 -
      이 엔진 전체가 주 단위 재평가 설계라 다른 지표들과 일관되게 맞췄다).
      기준 고점(peak_equity)은 보유 종목이 하나도 없는(완전 청산된) 시점마다
      그 시점 평가액으로 리셋한다 - 리셋하지 않으면 급락장에서 전종목이 한꺼번에
      손절되어 현금 100%가 된 이후 평가액이 다시는 움직이지 않아(포지션이 없으니
      마크투마켓 대상이 없다) 옛 고점을 영원히 회복하지 못해 신규 매수가 남은
      기간 내내 영구 정지되는 결함이 있었다(최초 구현에서 실제로 발생 확인 -
      2020년 3월 코로나 급락 이후 2026년까지 거래가 완전히 멈췄다). 완전 청산
      시점엔 더 지킬 미실현 이익이 없으므로 그 시점을 새 기준으로 삼는 것이
      타당하다 - 이 낙폭 한도는 "보유 중인 포지션이 이미 크게 물려 있을 때
      더 태우지 않는다"는 취지로 재해석한 것이다.
    - 물타기 없음: 이미 보유 중인 종목은 재평가 시점에 후보로 다시 고려하지
      않는다(진입은 종목당 한 번뿐, 추가매수 로직 자체가 없다).
    - 팩터 결합 필터(전부 선택적, AND로 결합 - 트렌드템플릿/RS/거래량/레짐 필터를
      전부 통과한 후보군에 추가로 덧붙는 필터라 서로 배타적이지 않다):
      - min_avg_trade_value: 최근 20일 평균 거래대금(원)이 이 값 미만인 종목 제외
        (유동성 확보 - 값이 작아도 실제로 사고팔기 어려운 종목을 걸러낸다).
      - use_value: PER(시가총액÷당기순이익, 흑자기업만)이 그 시점 후보군 내
        낮은 쪽 value_percentile% 안에 드는 종목만 남긴다(저평가).
      - use_quality: ROE(당기순이익÷자본총계, 100% 초과는 자본잠식 회복형 왜곡으로
        제외)가 그 시점 후보군 내 높은 쪽 quality_percentile% 안에 드는 종목만
        남긴다(우량기업).
      - use_low_vol: 최근 60거래일 일간수익률 변동성(연환산)이 그 시점 후보군 내
        낮은 쪽 low_vol_percentile% 안에 드는 종목만 남긴다(저변동성).
      가치/퀄리티는 kr_quant.py와 같은 방식(rcept_date 기준 그 시점까지 실제
      공시된 가장 최근 연도 재무데이터만 사용 - 룩어헤드 편향 방지)이며, 호출부가
      fundamentals_rows(KrFundamental.query 결과 리스트)와 shares_map(종목코드→
      상장주식수)을 미리 로드해 넘겨야 한다(이 함수 자체는 DB에 접근하지 않는다 -
      fetch_fn 주입과 같은 이유로 순수 계산 함수로 유지). 각 팩터의 percentile은
      독립적으로 같은 기준 후보군(유동성 필터까지 통과한 후보군) 대비 계산한 뒤
      교집합을 취한다(필터를 순서대로 적용해 점점 줄여나가면 앞선 필터의 통과율에
      따라 뒤 필터의 컷오프가 왜곡되는 문제가 있다).
    - 최소 손익비 1:2는 별도 필터가 아니라 위 청산 구조(본전 이동 +1R, 트레일링
      +2R) 자체로 구현한다 - 스크리너 기반 전략은 진입 시점에 확정적인 목표가를
      알 수 없어(개별 종목 저항선 등을 미리 계산하지 않음), "최소 1R은 손실
      없이, 2R부터는 추세를 최대한 따라간다"는 구조로 손익비 하한을 확보한다.
    """
    if market not in ("KR", "US"):
        return {"error": "market은 KR 또는 US여야 합니다"}
    if fetch_fn is None:
        fetch_fn = ts.fetch_ohlc_history_batches

    universe = ts.load_universe(market)
    tickers = [t for _, _, t, _, _ in universe]
    info_by_ticker = {t: (code, name, industry, sector) for code, name, t, industry, sector in universe}

    fetch_start = (datetime.fromisoformat(start_date) - timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
    fetch_end = (datetime.fromisoformat(end_date) + timedelta(days=1)).strftime("%Y-%m-%d")

    regime_dates, regime_closes = ([], [])
    if market_regime_filter:
        regime_dates, regime_closes = _fetch_index_series(market, fetch_start, fetch_end)

    series = {}
    for ticker, bars in fetch_fn(tickers, fetch_start, fetch_end):
        series[ticker] = (
            [b["date"] for b in bars],
            [b["close"] for b in bars],
            [b["high"] for b in bars],
            [b["low"] for b in bars],
            [b.get("volume") for b in bars],
        )

    all_dates = sorted({d for dates, *_ in series.values() for d in dates if start_date <= d <= end_date})
    if not all_dates:
        return {"error": "해당 기간에 사용 가능한 가격 데이터가 없습니다"}

    rebalance_dates = all_dates[::RESCAN_INTERVAL_DAYS]
    if rebalance_dates[-1] != all_dates[-1]:
        rebalance_dates.append(all_dates[-1])

    predicate = _strategy_predicate(strategy)

    cash = float(seed)
    positions = {}  # ticker -> dict(code,name,entryDate,entryPrice,entryIdx,shares,entryAtr,stopPrice,stopState,riskPerShare,highestHigh)
    trades = []
    equity_curve = []
    peak_equity = float(seed)
    prev_rd = None
    dd_halt_periods = 0

    for rd in rebalance_dates:
        evaluated = []
        idx_at_rd = {}
        for ticker, (dates, closes, highs, lows, volumes) in series.items():
            i = bisect.bisect_right(dates, rd) - 1
            if i < 0:
                continue
            idx_at_rd[ticker] = i
            if i + 1 < ts.MIN_BARS:
                continue
            code, name, industry, sector = info_by_ticker.get(ticker, (ticker, ticker, None, None))
            bars_slice = [
                {"date": dates[k], "close": closes[k], "high": highs[k], "low": lows[k], "volume": volumes[k]}
                for k in range(i + 1)
            ]
            try:
                result = ts.evaluate_trend_template(code, name, bars_slice, industry, sector)
            except Exception:
                continue
            if result:
                result["_ticker"] = ticker
                evaluated.append(result)
        ts.compute_universe_screen(evaluated)
        by_ticker = {e["_ticker"]: e for e in evaluated}
        qualifying = {t: e for t, e in by_ticker.items() if predicate(e)}

        # 1) 보유 종목 청산 판정 - 일별로 스캔하며 손절선 히트 > 시간손절 > 본전/트레일링 갱신 순으로 확인
        for ticker in list(positions.keys()):
            pos = positions[ticker]
            dates, closes, highs, lows, _volumes = series[ticker]
            i = idx_at_rd.get(ticker)
            if i is None:
                continue
            start_i = bisect.bisect_right(dates, prev_rd) if prev_rd else pos["entryIdx"] + 1
            start_i = max(start_i, pos["entryIdx"] + 1)
            exit_price = exit_date = reason = None
            for j in range(start_i, i + 1):
                if lows[j] <= pos["stopPrice"]:
                    exit_price, exit_date, reason = pos["stopPrice"], dates[j], pos["stopState"]
                    break
                held_bars = j - pos["entryIdx"]
                if held_bars >= time_stop_days and closes[j] <= pos["entryPrice"]:
                    exit_price, exit_date, reason = closes[j], dates[j], "timeStop"
                    break
                pos["highestHigh"] = max(pos["highestHigh"], highs[j])
                r_reached = ((pos["highestHigh"] - pos["entryPrice"]) / pos["riskPerShare"]
                             if pos["riskPerShare"] > 0 else 0)
                if r_reached >= trail_start_r:
                    new_stop = pos["highestHigh"] - atr_mult * pos["entryAtr"]
                    if new_stop > pos["stopPrice"]:
                        pos["stopPrice"] = new_stop
                        pos["stopState"] = "trailingStop"
                elif r_reached >= breakeven_r and pos["entryPrice"] > pos["stopPrice"]:
                    pos["stopPrice"] = pos["entryPrice"]
                    pos["stopState"] = "breakevenStop"
            if reason:
                shares = pos["shares"]
                pnl_pct = round((exit_price - pos["entryPrice"]) / pos["entryPrice"] * 100, 2)
                cash += shares * exit_price
                trades.append({
                    "code": pos["code"], "name": pos["name"],
                    "entryDate": pos["entryDate"], "entryPrice": round(pos["entryPrice"], 2),
                    "exitDate": exit_date, "exitPrice": round(exit_price, 2),
                    "pnlPct": pnl_pct, "exitReason": reason, "shares": shares,
                    "holdDays": (datetime.fromisoformat(exit_date) - datetime.fromisoformat(pos["entryDate"])).days,
                })
                del positions[ticker]
        prev_rd = rd

        # 낙폭 한도 판정 (신규 진입 전 시점 평가액 기준)
        held_value = 0.0
        for ticker, pos in positions.items():
            i = idx_at_rd.get(ticker)
            price = series[ticker][1][i] if i is not None else pos["entryPrice"]
            held_value += pos["shares"] * price
        equity_now = cash + held_value
        if not positions:
            peak_equity = equity_now  # 완전 청산 시점엔 그 평가액을 새 고점 기준으로 리셋(위 docstring 참고)
        else:
            peak_equity = max(peak_equity, equity_now)
        drawdown_pct = (peak_equity - equity_now) / peak_equity * 100 if peak_equity > 0 else 0.0
        dd_halt = drawdown_pct >= dd_halt_pct
        if dd_halt:
            dd_halt_periods += 1

        # 2) 시장 레짐 + 낙폭 한도 모두 통과해야 신규 매수. 빈 슬롯을 RS 등급 높은 순으로 채운다.
        regime_ok = True
        if market_regime_filter and regime_dates:
            ri = bisect.bisect_right(regime_dates, rd) - 1
            if ri + 1 >= 200:
                regime_ma200 = sum(regime_closes[ri - 199:ri + 1]) / 200
                regime_ok = regime_closes[ri] >= regime_ma200
            else:
                regime_ok = False

        open_slots = max_positions - len(positions) if (regime_ok and not dd_halt) else 0
        if open_slots > 0:
            candidates = [
                (t, e) for t, e in qualifying.items()
                if t not in positions
                and (min_rs is None or (e.get("rsRating") or 0) >= min_rs)
                and (min_rel_volume is None or (e.get("relVolume") or 0) >= min_rel_volume)
            ]
            if min_avg_trade_value:
                kept = []
                for t, e in candidates:
                    dates, closes, highs, lows, volumes = series[t]
                    avg_val = _avg_trade_value(closes, volumes, idx_at_rd[t])
                    if avg_val is not None and avg_val >= min_avg_trade_value:
                        kept.append((t, e))
                candidates = kept

            if use_value or use_quality or use_low_vol:
                fundamentals_now = (
                    _kr_quant_latest_fundamentals_as_of(rd, fundamentals_rows)
                    if (use_value or use_quality) else {}
                )
                enriched = []
                for t, e in candidates:
                    i = idx_at_rd[t]
                    dates, closes, highs, lows, volumes = series[t]
                    row = {"ticker": t}
                    if use_value or use_quality:
                        f = fundamentals_now.get(e["code"])
                        shares = shares_map.get(e["code"]) if shares_map else None
                        price = closes[i]
                        if f and shares and f.net_income and f.net_income > 0 \
                                and f.total_equity and f.total_equity > 0 and price:
                            row["per"] = price * shares / f.net_income
                            roe = f.net_income / f.total_equity * 100
                            row["roe"] = roe if roe <= 100 else None
                        else:
                            row["per"] = None
                            row["roe"] = None
                    if use_low_vol:
                        row["vol"] = _realized_vol(closes, i)
                    enriched.append(row)

                def _percentile_keep_set(key, reverse, pct):
                    valid = [r for r in enriched if r.get(key) is not None]
                    valid.sort(key=lambda r: r[key], reverse=reverse)
                    cutoff = max(1, round(len(valid) * pct / 100))
                    return {r["ticker"] for r in valid[:cutoff]}

                keep = None
                if use_value:
                    keep = _percentile_keep_set("per", False, value_percentile)
                if use_quality:
                    s = _percentile_keep_set("roe", True, quality_percentile)
                    keep = s if keep is None else (keep & s)
                if use_low_vol:
                    s = _percentile_keep_set("vol", False, low_vol_percentile)
                    keep = s if keep is None else (keep & s)
                if keep is not None:
                    candidates = [(t, e) for t, e in candidates if t in keep]

            candidates.sort(key=lambda te: -(te[1].get("rsRating") or 0))
            max_position_value = seed / max_positions
            for ticker, e in candidates[:open_slots]:
                i = idx_at_rd.get(ticker)
                if i is None:
                    continue
                dates, closes, highs, lows, _v = series[ticker]
                price = closes[i]
                if price is None or price <= 0:
                    continue
                atr = _atr(highs, lows, closes, i, atr_period)
                if not atr or atr <= 0:
                    continue
                stop_price = price - atr_mult * atr
                risk_per_share = price - stop_price
                if risk_per_share <= 0:
                    continue
                risk_amount = equity_now * risk_pct / 100
                shares = int(risk_amount // risk_per_share)
                cap_shares = int(min(cash, max_position_value) // price)
                shares = min(shares, cap_shares)
                if shares <= 0:
                    continue
                position_value = shares * price
                cash -= position_value
                held_value += position_value
                positions[ticker] = {
                    "code": e["code"], "name": e["name"], "entryDate": rd, "entryPrice": price,
                    "entryIdx": i, "shares": shares, "entryAtr": atr,
                    "stopPrice": stop_price, "stopState": "initialStop",
                    "riskPerShare": risk_per_share, "highestHigh": price,
                }

        equity_curve.append({"date": rd, "value": round(cash + held_value, 2)})

    # 종료 시점까지 남은 보유 종목은 마지막 가격으로 강제 청산해 손익을 확정한다.
    last_rd = rebalance_dates[-1]
    for ticker, pos in list(positions.items()):
        i = bisect.bisect_right(series[ticker][0], last_rd) - 1
        if i < 0:
            continue
        price = series[ticker][1][i]
        pnl_pct = round((price - pos["entryPrice"]) / pos["entryPrice"] * 100, 2)
        cash += pos["shares"] * price
        trades.append({
            "code": pos["code"], "name": pos["name"],
            "entryDate": pos["entryDate"], "entryPrice": round(pos["entryPrice"], 2),
            "exitDate": last_rd, "exitPrice": round(price, 2),
            "pnlPct": pnl_pct, "exitReason": "periodEnd", "shares": pos["shares"],
            "holdDays": (datetime.fromisoformat(last_rd) - datetime.fromisoformat(pos["entryDate"])).days,
        })
    positions.clear()

    trades.sort(key=lambda t: t["entryDate"])

    final_value = equity_curve[-1]["value"] if equity_curve else seed
    return_pct = round((final_value / seed - 1) * 100, 2) if seed > 0 else 0.0
    win_trades = [t for t in trades if t["pnlPct"] > 0]
    win_rate_pct = round(len(win_trades) / len(trades) * 100, 1) if trades else None
    avg_hold_days = round(sum(t["holdDays"] for t in trades) / len(trades), 1) if trades else None

    peak = seed
    mdd_pct = 0.0
    for pt in equity_curve:
        peak = max(peak, pt["value"])
        if peak > 0:
            mdd_pct = max(mdd_pct, (peak - pt["value"]) / peak * 100)

    profit_loss_ratio = _profit_loss_ratio(trades)
    benchmark_curve, benchmark_return_pct = _fetch_benchmark_curve(
        market, [pt["date"] for pt in equity_curve], seed)
    alpha_pct = round(return_pct - benchmark_return_pct, 2) if benchmark_return_pct is not None else None

    exit_reason_counts = {}
    for t in trades:
        exit_reason_counts[t["exitReason"]] = exit_reason_counts.get(t["exitReason"], 0) + 1

    return {
        "market": market, "strategy": strategy, "strategyLabel": _strategy_label(strategy),
        "start": start_date, "end": end_date, "seed": seed, "mode": "riskManaged",
        "riskPct": risk_pct, "atrPeriod": atr_period, "atrMult": atr_mult,
        "breakevenR": breakeven_r, "trailStartR": trail_start_r,
        "timeStopDays": time_stop_days, "ddHaltPct": dd_halt_pct,
        "maxPositions": max_positions,
        "minRs": min_rs, "minRelVolume": min_rel_volume, "marketRegimeFilter": market_regime_filter,
        "minAvgTradeValue": min_avg_trade_value,
        "useValue": use_value, "useQuality": use_quality, "useLowVol": use_low_vol,
        "valuePercentile": value_percentile if use_value else None,
        "qualityPercentile": quality_percentile if use_quality else None,
        "lowVolPercentile": low_vol_percentile if use_low_vol else None,
        "returnPct": return_pct, "finalValue": round(final_value, 2),
        "tradeCount": len(trades), "winCount": len(win_trades), "winRatePct": win_rate_pct,
        "avgHoldDays": avg_hold_days, "mddPct": round(mdd_pct, 2),
        "profitLossRatio": profit_loss_ratio, "alphaPct": alpha_pct,
        "exitReasonCounts": exit_reason_counts, "ddHaltPeriods": dd_halt_periods,
        "benchmark": {"label": BENCHMARK_LABEL.get(market, "Benchmark"), "returnPct": benchmark_return_pct,
                      "equityCurve": benchmark_curve},
        "equityCurve": equity_curve, "trades": trades,
    }


def run_screening_backtest(market, strategy, start_date, end_date,
                            stop_loss_pct=DEFAULT_STOP_LOSS_PCT, max_positions=DEFAULT_MAX_POSITIONS,
                            seed=10_000_000, fetch_fn=None,
                            min_rs=None, min_rel_volume=None, market_regime_filter=False):
    """fetch_fn: (tickers, start_date, end_date) -> Iterable[(ticker, bars)] 시그니처를
    맞추면 가격 조회 방식을 바꿔 끼울 수 있다. 기본값(운영 서버에서 쓰는 경로)은
    매번 야후에서 새로 받아오는 trend_screener.fetch_ohlc_history_batches이지만,
    같은 파라미터를 여러 번 반복 실행하는 로컬 스크립트(screening_backtest_cli.py)는
    local_price_cache의 캐싱 버전을 넘겨 매번 몇 분씩 걸리는 재조회를 건너뛴다.

    세 가지 매수 진입 필터를 추가로 걸 수 있다(전부 기존 전략 조건 위에 AND로
    덧붙는 추가 확인이며, 이미 보유 중인 포지션의 청산 판정에는 영향을 주지
    않는다 - 매수 시점만 더 깐깐하게 고른다는 뜻이다):
    - min_rs: RS 등급이 이 값 이상인 후보만 신규 매수(예: 85) - 트렌드 템플릿
      기본 조건(RS>=70)보다 더 강한 모멘텀만 남긴다.
    - min_rel_volume: 최근 거래량/직전 20일 평균거래량 비율이 이 값 이상인
      후보만 신규 매수(예: 1.5) - 미너비니 원 방법론의 "돌파 거래량" 확인을
      근사한다(trend_screener.py의 정량 조건에는 원래 빠져 있는 항목).
    - market_regime_filter: 지수(코스피/S&P500) 종가가 그 지수의 200일선보다
      낮은 재평가 시점에는 신규 매수를 쉰다(보유 종목의 손절/조건이탈 청산은
      평소대로 진행) - 전체 시장이 눌려 있는 국면에 새로 사는 것 자체를 줄여
      낙폭을 완화하려는 필터다.
    """
    if market not in ("KR", "US"):
        return {"error": "market은 KR 또는 US여야 합니다"}
    if fetch_fn is None:
        fetch_fn = ts.fetch_ohlc_history_batches

    universe = ts.load_universe(market)
    tickers = [t for _, _, t, _, _ in universe]
    info_by_ticker = {t: (code, name, industry, sector) for code, name, t, industry, sector in universe}

    fetch_start = (datetime.fromisoformat(start_date) - timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
    fetch_end = (datetime.fromisoformat(end_date) + timedelta(days=1)).strftime("%Y-%m-%d")

    regime_dates, regime_closes = ([], [])
    if market_regime_filter:
        regime_dates, regime_closes = _fetch_index_series(market, fetch_start, fetch_end)

    # 종목별 전체 기간 가격을 한 번만 받아, 재평가 시점마다 그 시점까지의
    # 구간만 슬라이스해서 재사용한다(API를 매번 다시 부르지 않는다). 원본
    # bars(딕셔너리 리스트)를 그대로 들고 있지 않고 필드별 배열로만 저장한다 -
    # evaluate_trend_template이 받는 딕셔너리 리스트 형태로 매번 다시 감싸는
    # 대신 두 형태를 동시에 들고 있으면(예전 방식) 국내 전체 유니버스×수년치
    # 데이터가 메모리에 두 배로 쌓여 Render 무료 티어 메모리 한도에서 프로세스가
    # 죽는 원인이 됐다(trend_screener.fetch_ohlc_history_batch를 제너레이터로
    # 바꾼 것과 같은 이유). 매 재평가 시점마다 필요한 구간만 잠깐 딕셔너리로
    # 재구성해 쓰고 곧바로 버린다 - 메모리 대신 약간의 CPU를 더 쓰는 쪽을 택했다.
    series = {}  # ticker -> (dates, closes, highs, lows, volumes)
    for ticker, bars in fetch_fn(tickers, fetch_start, fetch_end):
        series[ticker] = (
            [b["date"] for b in bars],
            [b["close"] for b in bars],
            [b["high"] for b in bars],
            [b["low"] for b in bars],
            [b.get("volume") for b in bars],
        )

    all_dates = sorted({d for dates, *_ in series.values() for d in dates if start_date <= d <= end_date})
    if not all_dates:
        return {"error": "해당 기간에 사용 가능한 가격 데이터가 없습니다"}

    rebalance_dates = all_dates[::RESCAN_INTERVAL_DAYS]
    if rebalance_dates[-1] != all_dates[-1]:
        rebalance_dates.append(all_dates[-1])

    predicate = _strategy_predicate(strategy)

    positions = {}  # ticker -> {code, name, entryDate, entryPrice}
    trades = []
    equity_curve = []
    slot_capital = seed / max_positions
    prev_rd = None

    for rd in rebalance_dates:
        evaluated = []
        idx_at_rd = {}
        for ticker, (dates, closes, highs, lows, volumes) in series.items():
            i = bisect.bisect_right(dates, rd) - 1
            if i < 0:
                continue
            idx_at_rd[ticker] = i
            if i + 1 < ts.MIN_BARS:
                continue
            code, name, industry, sector = info_by_ticker.get(ticker, (ticker, ticker, None, None))
            bars_slice = [
                {"date": dates[k], "close": closes[k], "high": highs[k], "low": lows[k], "volume": volumes[k]}
                for k in range(i + 1)
            ]
            try:
                result = ts.evaluate_trend_template(code, name, bars_slice, industry, sector)
            except Exception:
                continue
            if result:
                result["_ticker"] = ticker
                evaluated.append(result)
        ts.compute_universe_screen(evaluated)
        by_ticker = {e["_ticker"]: e for e in evaluated}
        qualifying = {t: e for t, e in by_ticker.items() if predicate(e)}

        # 1) 보유 종목 청산 판정. 손절은 재평가 시점 종가만 보면 그 사이 급락분을
        #    다 뒤집어써 실제 손절폭보다 훨씬 큰 손실로 잡힌다(직전 재평가~이번
        #    재평가 사이에 -8% 손절선을 진작 스쳤는데 그 주 종가가 -19%까지 더
        #    떨어진 걸 그대로 청산가로 쓰는 식) - 그 구간의 일별 저가를 스캔해
        #    손절선에 처음 닿은 날, 그 손절가에 청산된 것으로 근사한다(시가
        #    데이터가 없어 갭하락은 반영하지 못한다).
        for ticker in list(positions.keys()):
            i = idx_at_rd.get(ticker)
            if i is None:
                continue
            pos = positions[ticker]
            dates, closes, highs, lows, _volumes = series[ticker]
            stop_price = pos["entryPrice"] * (1 + stop_loss_pct / 100)
            start_i = bisect.bisect_right(dates, prev_rd) if prev_rd else 0
            exit_price = None
            exit_date = None
            reason = None
            for j in range(start_i, i + 1):
                if lows[j] <= stop_price:
                    exit_price, exit_date, reason = stop_price, dates[j], "stopLoss"
                    break
            if exit_price is None:
                price = closes[i]
                if ticker not in qualifying:
                    exit_price, exit_date, reason = price, rd, "conditionExit"
            if reason:
                pnl_pct = round((exit_price - pos["entryPrice"]) / pos["entryPrice"] * 100, 2)
                trades.append({
                    "code": pos["code"], "name": pos["name"],
                    "entryDate": pos["entryDate"], "entryPrice": round(pos["entryPrice"], 2),
                    "exitDate": exit_date, "exitPrice": round(exit_price, 2),
                    "pnlPct": pnl_pct, "exitReason": reason,
                    "holdDays": (datetime.fromisoformat(exit_date) - datetime.fromisoformat(pos["entryDate"])).days,
                })
                del positions[ticker]
        prev_rd = rd

        # 2) 빈 슬롯을 RS 등급 높은 순으로 채운다 - 시장 레짐 필터가 켜져 있고
        #    이 시점에 지수가 자기 200일선 아래면 신규 매수 자체를 쉰다(보유
        #    종목의 청산 판정은 위에서 이미 평소대로 끝났다).
        regime_ok = True
        if market_regime_filter and regime_dates:
            ri = bisect.bisect_right(regime_dates, rd) - 1
            if ri + 1 >= 200:
                regime_ma200 = sum(regime_closes[ri - 199:ri + 1]) / 200
                regime_ok = regime_closes[ri] >= regime_ma200
            else:
                regime_ok = False  # 200일선을 계산할 데이터가 아직 없으면 보수적으로 매수를 쉰다

        open_slots = max_positions - len(positions) if regime_ok else 0
        if open_slots > 0:
            candidates = [
                (t, e) for t, e in qualifying.items()
                if t not in positions
                and (min_rs is None or (e.get("rsRating") or 0) >= min_rs)
                and (min_rel_volume is None or (e.get("relVolume") or 0) >= min_rel_volume)
            ]
            candidates.sort(key=lambda te: -(te[1].get("rsRating") or 0))
            for ticker, e in candidates[:open_slots]:
                i = idx_at_rd.get(ticker)
                if i is None:
                    continue
                price = series[ticker][1][i]
                if price is None or price <= 0:
                    continue
                positions[ticker] = {
                    "code": e["code"], "name": e["name"], "entryDate": rd, "entryPrice": price,
                }

        # 3) 이 시점 포트폴리오 평가액(보유 슬롯은 현재가 기준, 빈 슬롯은 현금 그대로)
        held_value = 0.0
        for ticker, pos in positions.items():
            i = idx_at_rd.get(ticker)
            price = series[ticker][1][i] if i is not None else pos["entryPrice"]
            held_value += slot_capital * (price / pos["entryPrice"])
        idle_value = slot_capital * (max_positions - len(positions))
        equity_curve.append({"date": rd, "value": round(held_value + idle_value, 2)})

    # 종료 시점까지 남은 보유 종목은 마지막 가격으로 강제 청산해 손익을 확정한다.
    last_rd = rebalance_dates[-1]
    for ticker, pos in positions.items():
        i = bisect.bisect_right(series[ticker][0], last_rd) - 1
        if i < 0:
            continue
        price = series[ticker][1][i]
        pnl_pct = round((price - pos["entryPrice"]) / pos["entryPrice"] * 100, 2)
        trades.append({
            "code": pos["code"], "name": pos["name"],
            "entryDate": pos["entryDate"], "entryPrice": round(pos["entryPrice"], 2),
            "exitDate": last_rd, "exitPrice": round(price, 2),
            "pnlPct": pnl_pct, "exitReason": "periodEnd",
            "holdDays": (datetime.fromisoformat(last_rd) - datetime.fromisoformat(pos["entryDate"])).days,
        })

    trades.sort(key=lambda t: t["entryDate"])

    final_value = equity_curve[-1]["value"] if equity_curve else seed
    return_pct = round((final_value / seed - 1) * 100, 2) if seed > 0 else 0.0
    win_trades = [t for t in trades if t["pnlPct"] > 0]
    win_rate_pct = round(len(win_trades) / len(trades) * 100, 1) if trades else None
    avg_hold_days = round(sum(t["holdDays"] for t in trades) / len(trades), 1) if trades else None

    peak = seed
    mdd_pct = 0.0
    for pt in equity_curve:
        peak = max(peak, pt["value"])
        if peak > 0:
            mdd_pct = max(mdd_pct, (peak - pt["value"]) / peak * 100)

    profit_loss_ratio = _profit_loss_ratio(trades)
    benchmark_curve, benchmark_return_pct = _fetch_benchmark_curve(
        market, [pt["date"] for pt in equity_curve], seed)
    alpha_pct = round(return_pct - benchmark_return_pct, 2) if benchmark_return_pct is not None else None

    return {
        "market": market, "strategy": strategy, "strategyLabel": _strategy_label(strategy),
        "start": start_date, "end": end_date, "seed": seed,
        "stopLossPct": stop_loss_pct, "maxPositions": max_positions,
        "minRs": min_rs, "minRelVolume": min_rel_volume, "marketRegimeFilter": market_regime_filter,
        "returnPct": return_pct, "finalValue": round(final_value, 2),
        "tradeCount": len(trades), "winCount": len(win_trades), "winRatePct": win_rate_pct,
        "avgHoldDays": avg_hold_days, "mddPct": round(mdd_pct, 2),
        "profitLossRatio": profit_loss_ratio, "alphaPct": alpha_pct,
        "benchmark": {"label": BENCHMARK_LABEL.get(market, "Benchmark"), "returnPct": benchmark_return_pct,
                      "equityCurve": benchmark_curve},
        "equityCurve": equity_curve, "trades": trades,
    }
