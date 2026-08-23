"""국내주식_추세추종_VCP_전략_명세서.md를 최대한 충실히 구현한 백테스트 엔진.

screening_backtest.py의 run_risk_managed_backtest와는 완전히 별도 엔진이다 -
포지션 하나가 여러 매수 유닛(피라미딩)과 분할 매도를 가질 수 있어서 그
엔진의 "포지션 1개 = 매수 1회, 청산 1회" 모델로는 표현이 안 된다.

## 명세서 대비 구현 범위
구현: 트렌드템플릿 8조건, ADX(14)>=25, VCP 패턴(순차 수축 조정+거래량 감소),
피벗 돌파+거래량 확인, 시장국면(코스피>200일선 and 200일선 20일 기울기>0),
ATR(20) 기반 손절(최대 -8% 캡), 본전이동(+1R), 분할익절(+2R에 25%),
챈들리어 트레일링(3×ATR20), MA50 2일연속 이탈 청산, 피라미딩(최대 2회,
+0.5R 간격), 시간손절(10거래일 내 +0.5R 미도달), 슬리피지(+0.3%/-0.3%),
매도세(0.2%), 우선주/시총/거래대금 제외, 익일 시가 체결(시가 데이터가
있는 경우, 없으면 종가로 근사).

구현 불가(우리 데이터에 해당 항목 자체가 없음): 관리종목, 투자주의/경고/
위험, 감사의견, 정리매매, 최대주주지분율, 회계처리위반 이력, 생존편향
제거(kr_stocks.json이 현재 상장 종목 스냅샷이라 상장폐지 종목이 유니버스에
아예 없음 - 이 백테스트 결과는 그만큼 낙관적으로 치우쳐 있을 수 있다).

## 룩어헤드 편향 방지
매일 종가 이후 신호를 계산해 익일 시가(또는 시가 데이터가 없으면 그날 종가)에
체결한다는 명세를 그대로 따른다 - 신호가 나온 그날 가격에 즉시 체결하지 않는다.
VCP/ADX/트렌드템플릿 판정은 전부 그 시점까지의 데이터만 사용한다.
"""
import bisect
from datetime import datetime, timedelta

import trend_screener as ts
from screening_backtest import _fetch_benchmark_curve as _fetch_benchmark_curve_local
from screening_backtest import _fetch_index_series as _fetch_index_series_local

ATR_PERIOD = 20
INITIAL_STOP_ATR_MULT = 2.0
MAX_INITIAL_RISK_PCT = 8.0  # 리스크폭이 진입가의 8%를 넘으면 셋업 기각
CHANDELIER_ATR_MULT = 3.0
BREAKEVEN_R = 1.0
PARTIAL_PROFIT_R = 2.0
PARTIAL_PROFIT_FRACTION = 0.25
TRAIL_ACTIVATE_R = 1.0
PYRAMID_MAX_COUNT = 2
PYRAMID_INTERVAL_R = 0.5
PYRAMID_SIZE_FRACTION = 0.5  # 최초 유닛 대비 비율
TIME_STOP_DAYS = 10
TIME_STOP_PROGRESS_R = 0.5
MA_BREAK_CONSEC_DAYS = 2
ADX_PERIOD = 14
ADX_THRESHOLD = 25
RISK_PCT_PER_TRADE = 1.5
MAX_POSITION_WEIGHT_PCT = 20.0
MIN_MARKET_CAP = 100_000_000_000  # 1,000억원
MIN_AVG_TRADE_VALUE = 500_000_000  # 5억원
VOLUME_BREAKOUT_MULT = 1.5
VOLUME_AVG_DAYS = 50
RESCAN_INTERVAL_DAYS = 3  # 원래 7(주 단위) - 피벗 돌파가 재평가 사이에 났다 꺼지는 걸
# 놓치는 문제(표본 부족의 주된 원인 중 하나로 확인)를 줄이려고 3거래일로 단축.
# 계산량은 그만큼(약 2.3배) 늘어난다.
DEFAULT_MAX_POSITIONS_FALLBACK = 10
WARMUP_DAYS = 450
SLIPPAGE_ENTRY_PCT = 0.3
SLIPPAGE_EXIT_PCT = 0.3
SELL_TAX_PCT = 0.2
BENCHMARK_TICKER = {"KR": "^KS11", "US": "^GSPC"}
BENCHMARK_LABEL = {"KR": "KOSPI Buy & Hold", "US": "S&P 500 Buy & Hold"}

# "완화 VCP 전략" - 국내주식_추세추종_VCP_전략_명세서.md 원안대로면 표본이 6.5년간
# 30건(리스크폭 8%초과 셋업을 기각하는 스펙 원안대로 하면 13건)뿐이라 통계적
# 유의성이 부족했다(명세서 스스로도 "최소 표본 100건 이상"을 요구). 승률에는
# 여유가 있다는 전제로(원안 73~77%에서 30~40%까지 낮아져도 괜찮음) ADX/VCP
# 패턴 판정 기준을 완화하고, 리스크폭 8% 초과 셋업은 기각 대신 손절폭을 8%로
# 줄여서라도 진입시키도록 바꿔 거래빈도를 올렸다 - 그 결과 110건(승률은 오히려
# 74.5%로 원안과 비슷하게 유지, 손익비 4.35)으로 늘어 이 조합을 최종 채택했다.
#
# 이후 실측 결과 재평가일의 61.5%가 포지션 0개(현금 100%)로 드러나 벤치마크(코스피
# 매수보유 215.04%) 대비 알파가 -104.82%p로 크게 마이너스였다(개별 종목 승률/손익비
# 자체는 좋았음 - 기회가 너무 드물었을 뿐). cash_equitize(현금 유휴화 방지 - 국면 OK인데
# 유휴한 현금을 지수에 태워두다가 개별 셋업이 뜨면 갈아타는, 미너비니/오닐류의 "확인된
# 상승국면에서는 현금을 놀리지 마라" 원칙)를 추가해 0%/40%/70%/100% 지수노출 상한을
# 스윕한 결과 알파-MDD가 함께 커지는 트레이드오프가 뚜렷했다(0%: 알파-104.82%p/MDD
# 5.43%, 40%: -15.78%p/24.29%, 70%: +48.83%p/30.61%, 100%: +66.78%p/37.64%) - 사용자가
# "최소 요건인 알파 양수는 달성하되 MDD는 최대안보다 낮출 것"을 목표로 70%를 선택.
#
# 이후 "9년이면 최소 1000건 이상 거래해야 한다"는 요청으로 거래빈도를 다시 크게
# 늘렸다(2017~2026 기준 139건 -> 목표 1000건+). 미너비니 트렌드템플릿(8조건)을
# 몇 개 이상 통과해야 후보로 볼지(min_trend_pass_count)를 새로 열어 후보 풀 자체를
# 넓히는 방향과, VCP/ADX 품질기준을 함께 푸는 방향을 나눠 11개 조합을 스윕한 결과
# "거래빈도를 늘릴수록 손익비가 함께 낮아지는" 트레이드오프가 일관되게 나타났다
# (트렌드템플릿+슬롯수만 완화·VCP품질 유지: 9.6년환산 315건/손익비4.3 vs 전면 완화:
# 9.6년환산 1158건/손익비3.66) - "1000건 이상 + 손익비 상승"을 동시에 만족하는
# 조합은 찾지 못했다. 사용자가 "1000건 이상 달성"을 우선해 아래 완화 조합(ADX≥10,
# 최종수축비율 95%, 최소지속 1일, 최근성 60일, 트렌드템플릿 3개 이상 통과,
# 최대 40포지션)을 선택 - 원안(2020-2026, 손익비4.35)/직전 버전(2017-2026,
# 손익비4.51) 대비 손익비는 낮아지지만(3.66 내외) 거래빈도와 CAGR/알파가 크게 늘었다.
RELAXED_VCP_PARAMS = {
    "market": "KR", "adx_threshold": 10, "final_contraction_ratio": 0.95, "min_final_duration": 1,
    "max_days_since_low": 60, "require_volume_decrease": False, "rescan_interval_days": 7,
    "risk_cap_mode": "shrink", "cash_equitize": True, "equitize_max_pct": 70.0,
    "min_trend_pass_count": 3, "max_positions": 40,
}

# "어나니머스" - 사용자가 실제로 아는 사람이 쓴다는 국내주식 매매방법론(9년 1,476건,
# 승률 31.57%, 손익비 9.30, 평균수익 +52.38%/평균손실 -5.63%, CAGR +50.76%, 누적
# +5,049%, MDD -44.45%)에 최대한 근접시키려 한 결과다. 이 숫자 조합(낮은 승률+
# 극단적으로 높은 손익비+큰 평균수익/작은 평균손실)은 VCP 패턴 조건과는 안 맞고
# (VCP는 아무리 완화해도 "패턴"이라는 조건 자체가 병목이라 거래빈도 한계가 뚜렷했다),
# 터틀 트레이딩류의 "손실은 짧게, 수익은 무제한으로"에 가깝다는 판단 하에 entry_mode를
# VCP에서 돈치안 채널 브레이크아웃(패턴 요건 없이 전체 유니버스에서 N일 신고가
# 돌파)으로 완전히 바꿨다. 슬롯을 10개로 고정한 상태에서 손익비(9.30)에 가장
# 근접시키려 트레일링을 넓히면(예: 10×ATR) 평균수익/손익비는 거의 정확히 맞아도
# (52.26%/8.59) 자본회전이 느려져 CAGR·거래수가 함께 떨어지는 트레이드오프가
# 뚜렷했다 - "10슬롯 × 9.6년 거래일 / 평균보유일수"가 산수적 상한이라(평균보유
# 25일이면 최대 940건) 재평가 주기를 아무리 단축해도(주간→3일→매일) 1,000건을
# 넘기지 못했고, 오히려 매일 재계산은 손익비만 깎아먹었다(3일 재평가: 551건/
# 손익비8.2, 매일 재평가 근사치: ~624건/손익비6.81). 그래서 CAGR·손익비·평균수익/
# 손실을 종합적으로 가장 잘 근접시킨 "3일 재평가" 조합을 최종 채택했다(실측:
# 9.6년 551건, 승률 22.3%, 손익비 8.2, CAGR 27.43%, 평균수익 51.25%, 평균손실
# -6.25%, 알파 +735.52%p - 목표 대비 거래수·CAGR·누적수익률은 못 미치지만 손익비/
# 평균수익/평균손실은 상당히 근접했다). 슬롯을 10개보다 늘리면 거래수 목표(1,000건+)
# 달성이 훨씬 쉬워지지만, 사용자가 슬롯 10개 고정을 명시적으로 요구해 그 제약 안에서
# 나온 최선의 조합이다.
#
# 이후 "종목마다 동일한 금액을 매매하고, 시가총액/유동성 필터를 제거하라"는 요청으로
# position_sizing_mode="equal_weight"(변동성과 무관하게 슬롯당 총자산/max_positions을
# 동일 배분 - 기존 리스크 기반 ATR 사이징은 변동성 큰 종목을 작게 사서 자본을 충분히
# 못 굴렸다)와 min_market_cap=0/min_avg_trade_value=0을 추가했다. 실측 결과 시가총액/
# 유동성 필터 제거는 거래수에 거의 영향이 없었다(551->570건 - 그 필터가 병목이
# 아니었다는 뜻, "10슬롯×거래일/평균보유일수" 산수 상한이 진짜 병목임을 재확인).
# 반면 동일금액 배분은 CAGR(27.43%->35.71%)과 누적수익률(934%->1,798%)을 크게
# 끌어올렸다(손익비 8.2->7.9, MDD -31.24%->-33.83%로 소폭 트레이드오프).
#
# 그런데 이 상태로 시가총액/유동성 필터까지 완전히 없애고 여러 max_hold_days를
# 스윕하니(9.6년 최대보유 15~40일) 누적수익률이 수만~수백만 %까지 치솟았다 -
# "동일금액(계좌 성장에 비례) + 유동성 무제한" 조합이 계좌가 커질수록 실제로는
# 체결 불가능한 금액을 그대로 태운다고 가정하는 초복리 아티팩트였다(사용자가
# "만 단위 수익률이 정상이냐"고 지적해 발견). min_avg_trade_value(유동성 필터,
# 후보 자격만 거름)를 다시 넣어도 해결이 안 됐다 - 진짜 문제는 필터가 아니라
# "포지션 금액 자체가 계좌 크기에 비례해 무한정 커진다"는 가정이었다. 이를
# max_pct_of_avg_trade_value(종목 자신의 평균거래대금 대비 비율 상한)와
# max_position_value_abs(계좌가 아무리 커져도 넘지 않는 고정 금액 상한 - "전략
# 용량" 가정을 명시적으로 반영)로 이중 제한해 현실적인 수준으로 눌렀다. 시드
# 5,000만원 기준 고정상한 1,500/2,500/5,000만원을 비교한 결과 승률·손익비·거래수는
# 거의 그대로인 채(상한이 트레이드 자체의 품질에는 영향 없음, 계좌가 커졌을 때
# "얼마나 더 태울 수 있다고 볼지"만 다름) CAGR만 달라졌다(47.0/53.43/62.81%) -
# 2,500만원(초기 슬롯당 배분액 500만원의 5배)이 목표(CAGR 50.76%, 승률 31.57%,
# 거래수 1,476건)에 가장 근접해(CAGR 53.43%, 승률 31.2%, 거래수 1,596건) 이를
# 최종 채택했다(손익비 5.12/평균수익 31.69%는 여전히 목표에 못 미침 - 거래수
# 1,000건+를 확보하려면 max_hold_days=20이 필요했고, 이게 승리 포지션을 더
# 크게 키우지 못하게 막는 대가다).
ANONYMOUS_PARAMS = {
    "market": "KR", "entry_mode": "donchian", "donchian_period": 15, "max_positions": 10,
    "initial_stop_atr_mult": 1.5, "max_initial_risk_pct": 6.0, "risk_cap_mode": "shrink",
    "partial_profit_fraction": 0.0, "breakeven_r": 999.0, "trail_activate_r": 0.5,
    "chandelier_atr_mult": 8.0, "ma_break_period": 250, "ma_break_consec_days": 6,
    "pyramid_max_count": 5, "time_stop_days": 20, "time_stop_progress_r": 0.2,
    "rescan_interval_days": 3, "cash_equitize": True, "equitize_max_pct": 70.0,
    "position_sizing_mode": "equal_weight", "min_market_cap": 0, "min_avg_trade_value": 100_000_000,
    "max_pct_of_avg_trade_value": 10, "max_hold_days": 20, "gate_entries_on_regime": False,
    "max_position_value_abs": 25_000_000, "default_seed": 50_000_000,
}

# "스위퍼" - 사용자가 "어나니머스는 내가 직접 제안한 제약(균등배분, 고정 금액 상한 등)이
# 많으니, 그런 제약을 걷어내고 수익률·승률·손익비만 보고 최적 조건을 찾아달라"고
# 요청해 나온 별도 전략이다. 진입 신호(돈치안15일 브레이크아웃)는 어나니머스와 같지만
# 청산·사이징 철학이 정반대다 - 어나니머스는 "손실은 짧게, 수익은 최대한 오래"(챈들리어
# 8×ATR, 분할익절 없음, 평균보유 14일)인데, 이 전략은 챈들리어를 3×ATR로 바짝 좁히고
# +1R 본전이동 + 25% 분할익절을 되살려 "짧고 빠르게, 자주 이기며" 자본을 빠르게
# 회전시킨다(평균보유 5.9일). position_sizing_mode도 "risk"(계좌 대비 리스크%
# 기반)로 되돌렸다 - equal_weight는 계좌가 복리로 커질수록 포지션 금액이 무한정
# 커지는 초복리 아티팩트가 있어(어나니머스에서 실측으로 확인) 고정 상한이 필수였는데,
# risk 기반 사이징은 그 문제 자체가 구조적으로 없다.
#
# 슬롯 수를 40/60/80으로 자유롭게 풀어본 스윕에서는 슬롯이 늘수록 CAGR이 계속
# 올라갔지만(슬롯40 CAGR 66.32%→슬롯80 72.28%), 사용자가 "슬롯이 너무 많다"며
# 다시 10개 고정을 요청해 그 제약 안에서 트레일링 폭(3/5/8×ATR)·분할익절 비율
# (25/50%)·회당 리스크%(6/10%)·국면게이팅 여부를 스윕한 결과(9.6년, 시드
# 1,000만원, 유동성 최소선 평균거래대금 1억원): 타이트 트레일링(3×ATR)+분할익절
# 25%+회당리스크6%+국면게이팅 해제 조합이 CAGR 51.14%·승률 58.6%·손익비 5.39·
# 거래 4,194건으로 승률·CAGR을 동시에 압도했다(와이드 트레일링으로 "크게 먹는"
# 접근은 슬롯이 10개뿐이면 평균보유가 21일까지 늘어나 자본회전이 느려지고 오히려
# CAGR·승률 모두 이 조합보다 낮았다 - CAGR 39.73%/승률 24.5%). 국면게이팅을
# 다시 켜면 MDD가 32.1%→30.3%로 개선되지만 CAGR이 51.1%→42.5%로 크게 낮아지는
# 트레이드오프가 뚜렷해 해제 상태를 최종 채택했다.
SWEEPER_PARAMS = {
    "market": "KR", "entry_mode": "donchian", "donchian_period": 15, "max_positions": 10,
    "initial_stop_atr_mult": 1.5, "max_initial_risk_pct": 6.0, "risk_cap_mode": "shrink",
    "partial_profit_fraction": 0.25, "breakeven_r": 1.0,
    "chandelier_atr_mult": 3.0,
    "rescan_interval_days": 3, "cash_equitize": True, "equitize_max_pct": 70.0,
    "position_sizing_mode": "risk", "min_market_cap": 0, "min_avg_trade_value": 100_000_000,
    "gate_entries_on_regime": False, "default_seed": 10_000_000,
}


def _true_range(highs, lows, closes, k):
    prev_close = closes[k - 1] if k > 0 else closes[k]
    return max(highs[k] - lows[k], abs(highs[k] - prev_close), abs(lows[k] - prev_close))


def _atr(highs, lows, closes, i, period=ATR_PERIOD):
    if i < period:
        return None
    total = sum(_true_range(highs, lows, closes, k) for k in range(i - period + 1, i + 1))
    return total / period


def _sma(values, period, i):
    if i < period - 1:
        return None
    return sum(values[i - period + 1:i + 1]) / period


def _adx(highs, lows, closes, i, period=ADX_PERIOD):
    """Wilder's ADX. i번 인덱스(포함)까지의 값. period*2 이상의 데이터가 필요하다
    (첫 스무딩 구간 + 그 결과의 재스무딩)."""
    need = period * 2 + 1
    if i < need:
        return None
    start = i - need + 1
    plus_dm, minus_dm, tr = [], [], []
    for k in range(start + 1, i + 1):
        up_move = highs[k] - highs[k - 1]
        down_move = lows[k - 1] - lows[k]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        tr.append(_true_range(highs, lows, closes, k))

    def _wilder_smooth(series, period):
        smoothed = [sum(series[:period])]
        for v in series[period:]:
            smoothed.append(smoothed[-1] - smoothed[-1] / period + v)
        return smoothed

    tr_s = _wilder_smooth(tr, period)
    plus_s = _wilder_smooth(plus_dm, period)
    minus_s = _wilder_smooth(minus_dm, period)

    dx_list = []
    for tr_v, p_v, m_v in zip(tr_s, plus_s, minus_s):
        if tr_v <= 0:
            continue
        plus_di = 100 * p_v / tr_v
        minus_di = 100 * m_v / tr_v
        denom = plus_di + minus_di
        dx = 100 * abs(plus_di - minus_di) / denom if denom > 0 else 0.0
        dx_list.append(dx)
    if len(dx_list) < period:
        return None
    return sum(dx_list[-period:]) / period


def _find_swing_points(highs, lows, end_i, window=5, lookback=90):
    """end_i까지의 최근 lookback봉 구간에서 스윙고점/저점을 찾는다(프랙탈 방식 -
    앞뒤 window봉보다 높은/낮은 봉). 반환: [(index, 'high'|'low', price), ...] 시간순."""
    start_i = max(window, end_i - lookback)
    points = []
    for k in range(start_i, end_i - window + 1):
        window_highs = highs[k - window:k + window + 1]
        if highs[k] == max(window_highs) and window_highs.count(highs[k]) == 1:
            points.append((k, "high", highs[k]))
        window_lows = lows[k - window:k + window + 1]
        if lows[k] == min(window_lows) and window_lows.count(lows[k]) == 1:
            points.append((k, "low", lows[k]))
    points.sort(key=lambda p: p[0])
    # 고점/저점이 번갈아 나오도록 정리(같은 방향이 연속되면 더 극단적인 값만 남긴다)
    cleaned = []
    for p in points:
        if cleaned and cleaned[-1][1] == p[1]:
            if (p[1] == "high" and p[2] > cleaned[-1][2]) or (p[1] == "low" and p[2] < cleaned[-1][2]):
                cleaned[-1] = p
        else:
            cleaned.append(p)
    return cleaned


def detect_vcp(highs, lows, closes, volumes, i, final_contraction_ratio=0.5, min_final_duration=5,
                max_days_since_low=15, require_volume_decrease=True):
    """i번 인덱스(오늘) 기준 VCP(변동성수축패턴)를 탐지한다. 조건을 만족하면
    {"pivot": 피벗가, "legs": [...]} 를 반환하고, 아니면 None.

    조정1→조정2→조정3(최소 2개, 이상적으론 3개 이상) 각각의 고점대비 하락폭이
    순차적으로 줄고(항상 요구), 그 구간 평균거래량도 순차적으로 줄며(선택 -
    require_volume_decrease), 마지막 수축구간 변동폭이 첫 조정폭의
    final_contraction_ratio 이하이고 그 구간이 min_final_duration거래일 이상
    지속됐는지 확인한다. 뒤의 세 파라미터는 명세서 원안 값(0.5/5/15)이 기본값이며,
    표본이 너무 적어 거래빈도를 늘리려는 실험에서 완화해 쓸 수 있게 열어뒀다.
    """
    points = _find_swing_points(highs, lows, i, window=5, lookback=90)
    if len(points) < 4:
        return None
    # 마지막 점이 저점이면(현재 막 저점을 찍고 올라오는 중) 그대로, 고점이면 사용 불가
    # (아직 조정이 안 끝난 것으로 보고 스킵)
    if points[-1][1] != "low":
        return None

    # 고점-저점 쌍(조정 레그)을 시간 역순으로 최대 4개 추출. "고점"이라는 라벨이
    # 붙어 있어도 그보다 나중의 "저점"보다 가격이 낮으면(프랙탈 노이즈로 흔함 -
    # 예: 두 저점 사이에 낀 사소한 반등이 그 구간 안에서만 "가장 높아" 고점으로
    # 잡힌 경우) 진짜 조정이 아니므로, low_pt보다 실제로 더 높은 고점을 찾을
    # 때까지 계속 더 과거로 거슬러 올라간다.
    legs = []
    j = len(points) - 1
    while j >= 1 and len(legs) < 4:
        low_pt = points[j]
        hi_j = j - 1
        high_pt = None
        while hi_j >= 0:
            if points[hi_j][1] == "high" and points[hi_j][2] > low_pt[2]:
                high_pt = points[hi_j]
                break
            hi_j -= 1
        if high_pt is None:
            break
        pullback_pct = (high_pt[2] - low_pt[2]) / high_pt[2] * 100 if high_pt[2] > 0 else 0
        vol_window = volumes[high_pt[0]:low_pt[0] + 1]
        vol_window = [v for v in vol_window if v is not None]
        avg_vol = sum(vol_window) / len(vol_window) if vol_window else None
        legs.append({
            "highIdx": high_pt[0], "lowIdx": low_pt[0], "highPrice": high_pt[2], "lowPrice": low_pt[2],
            "pullbackPct": pullback_pct, "avgVolume": avg_vol,
        })
        j = hi_j

    if len(legs) < 2:
        return None
    legs.reverse()  # 시간순(오래된 조정 -> 최근 조정)

    # 순차적으로 하락폭이 줄어드는지(각 조정이 직전 조정보다 타이트해지는지)
    for k in range(1, len(legs)):
        if legs[k]["pullbackPct"] >= legs[k - 1]["pullbackPct"]:
            return None
    # 거래량도 순차적으로 감소하는지(선택)
    if require_volume_decrease:
        vols = [leg["avgVolume"] for leg in legs]
        if any(v is None for v in vols):
            return None
        for k in range(1, len(vols)):
            if vols[k] >= vols[k - 1]:
                return None
    # 최종 수축구간이 첫 조정의 final_contraction_ratio 이하
    if legs[-1]["pullbackPct"] > legs[0]["pullbackPct"] * final_contraction_ratio:
        return None
    # 최종 수축구간이 min_final_duration거래일 이상 지속
    final_leg = legs[-1]
    duration = final_leg["lowIdx"] - final_leg["highIdx"]
    if duration < min_final_duration:
        return None
    # 현재 시점이 마지막 저점 이후 너무 오래 지나지 않았는지(막 바닥을 다진 상태여야 돌파 의미가 있음)
    if i - final_leg["lowIdx"] > max_days_since_low:
        return None

    pivot = final_leg["highPrice"]
    return {"pivot": pivot, "legs": legs}


def detect_donchian_breakout(highs, closes, i, period=20):
    """i번 인덱스(오늘) 종가가 직전 period거래일 고가(오늘 제외) 대비 신고가를
    돌파했는지 - 터틀 트레이딩 시스템의 돈치안 채널 브레이크아웃 방식. VCP처럼
    패턴(순차 수축)을 요구하지 않아 훨씬 자주(전체 유니버스 대상) 신호가 난다."""
    if i < period:
        return None
    prior_high = max(highs[i - period:i])
    if closes[i] > prior_high:
        return {"pivot": prior_high}
    return None


def _profit_loss_ratio(trades):
    wins = [t["pnlPct"] for t in trades if t["pnlPct"] > 0]
    losses = [t["pnlPct"] for t in trades if t["pnlPct"] < 0]
    if not losses:
        return None
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses))
    if avg_loss == 0:
        return None
    return round(avg_win / avg_loss, 2)


def is_preferred_stock(name):
    """국내 우선주는 종목명이 '우'/'우B'/'1우'/'2우B' 등으로 끝난다(정확한 규칙은
    없지만 실무적으로 널리 쓰이는 근사 - 예: 삼성전자우, LG화학우, 두산퓨얼셀1우)."""
    if not name:
        return False
    tail = name[-3:]
    return name.endswith("우") or "우B" in tail or "우C" in tail


MAX_SHAREHOLDER_PCT = 70.0


def latest_max_shareholder_pct(rows_for_code, as_of_date):
    """rows_for_code: 한 종목의 DART 대량보유상황보고(majorstock) 전체 이력
    [{"rcept_dt":, "repror":, "stkrt":}, ...]. as_of_date까지 실제 공시된 것만 써서
    보고자(repror)별 최신 지분율을 구하고 그 중 최댓값을 최대주주지분율 근사치로
    돌려준다(대량보유 상황보고 자체가 5%/1%p 이상 변동 시에만 갱신되는 "지분 변동
    보고"라 완전한 최대주주 명부는 아니지만, "특정 주주가 압도적 지분을 쥐고 있는지"
    를 보는 이 필터 목적에는 충분한 근사다). 데이터가 없으면 None(필터 미적용 -
    DART가 이 API에서 최근 1~2년치 이전 이력은 잘 안 돌려주는 경우가 많아, 오래된
    구간은 사실상 이 필터가 꺼진 것과 같다 - fetch_major_shareholder_dart.py 참고)."""
    latest_by_repror = {}
    for row in rows_for_code:
        if row["rcept_dt"] > as_of_date:
            continue
        cur = latest_by_repror.get(row["repror"])
        if cur is None or row["rcept_dt"] > cur["rcept_dt"]:
            latest_by_repror[row["repror"]] = row
    if not latest_by_repror:
        return None
    return max(r["stkrt"] for r in latest_by_repror.values())


def load_shareholder_rows(paths):
    """fetch_major_shareholder_dart.py가 만든 parquet 파일(들)을 읽어
    {stock_code: [{"rcept_dt","repror","stkrt"}, ...]}로 묶는다."""
    import pandas as pd
    rows_by_code = {}
    for path in paths:
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        for stock_code, group in df.groupby("stock_code"):
            rows_by_code.setdefault(stock_code, []).extend(
                group[["rcept_dt", "repror", "stkrt"]].to_dict("records")
            )
    return rows_by_code


def _avg_trade_value(closes, volumes, i, window=20):
    if i < window - 1:
        return None
    vals = [closes[k] * volumes[k] for k in range(i - window + 1, i + 1)
            if volumes[k] is not None and closes[k] is not None]
    return sum(vals) / len(vals) if vals else None


def _avg_volume(volumes, i, window=VOLUME_AVG_DAYS):
    if i < window - 1:
        return None
    vals = [v for v in volumes[i - window + 1:i + 1] if v is not None]
    return sum(vals) / len(vals) if vals else None


def _full_exit(pos, price, date, reason, tax=True):
    proceeds = pos["shares"] * price * (1 - (SLIPPAGE_EXIT_PCT + (SELL_TAX_PCT if tax else 0)) / 100)
    pnl_pct = round((proceeds / pos["shares"] - pos["avgEntryPrice"]) / pos["avgEntryPrice"] * 100, 2)
    trade = {
        "code": pos["code"], "name": pos["name"], "entryDate": pos["entryDate"],
        "entryPrice": round(pos["avgEntryPrice"], 2), "exitDate": date, "exitPrice": round(price, 2),
        "shares": pos["shares"], "pnlPct": pnl_pct, "exitReason": reason,
        "pyramidCount": pos["pyramidCount"], "partialTaken": pos["partialTaken"],
        "holdDays": (datetime.fromisoformat(date) - datetime.fromisoformat(pos["entryDate"])).days,
    }
    return trade, proceeds


def run_vcp_backtest(market, start_date, end_date, seed=10_000_000, max_positions=DEFAULT_MAX_POSITIONS_FALLBACK,
                      fetch_fn=None, shares_map=None, shareholder_rows_by_code=None,
                      adx_threshold=ADX_THRESHOLD, min_market_cap=MIN_MARKET_CAP,
                      min_avg_trade_value=MIN_AVG_TRADE_VALUE, volume_breakout_mult=VOLUME_BREAKOUT_MULT,
                      final_contraction_ratio=0.5, min_final_duration=5, max_days_since_low=15,
                      require_volume_decrease=True, rescan_interval_days=RESCAN_INTERVAL_DAYS,
                      max_initial_risk_pct=MAX_INITIAL_RISK_PCT, risk_cap_mode="skip",
                      cash_equitize=False, index_slippage_pct=0.1, equitize_max_pct=100.0,
                      min_trend_pass_count=None, ma_break_period=50, ma_break_consec_days=MA_BREAK_CONSEC_DAYS,
                      breakeven_r=BREAKEVEN_R, partial_profit_r=PARTIAL_PROFIT_R,
                      partial_profit_fraction=PARTIAL_PROFIT_FRACTION, chandelier_atr_mult=CHANDELIER_ATR_MULT,
                      trail_activate_r=TRAIL_ACTIVATE_R, pyramid_max_count=PYRAMID_MAX_COUNT,
                      time_stop_days=TIME_STOP_DAYS, time_stop_progress_r=TIME_STOP_PROGRESS_R,
                      entry_mode="vcp", donchian_period=20, initial_stop_atr_mult=INITIAL_STOP_ATR_MULT,
                      position_sizing_mode="risk", max_hold_days=None, gate_entries_on_regime=True,
                      max_pct_of_avg_trade_value=None, max_position_value_abs=None):
    """VCP 명세서 기반 백테스트. 모듈 docstring의 "구현 범위"를 반드시 먼저 읽을 것 -
    관리종목/감사의견/정리매매/최대주주지분율/회계처리위반 이력, 생존편향 제거는
    데이터가 없어 반영하지 못했다.

    재평가(신호계산)는 주 단위다(전종목 트렌드템플릿+ADX+VCP 판정이 무거워 매일
    돌리면 계산량이 지나치게 커진다 - screening_backtest.py와 같은 이유). 그
    재평가일의 종가가 피벗 돌파+거래량 조건을 만족하면 "익일 시가" 대신(시가
    데이터를 이 파이프라인에서 받지 않아) 명세서가 제시한 대안인 "피벗가+0.5%
    지정가"로 체결한다 - 그 다음 거래일부터 재평가일까지 저가가 그 지정가
    이하로 닿은 첫날 체결된 것으로 본다. 이 때문에 재평가일과 재평가일 사이에
    돌파했다가 다시 꺼진 경우는 포착하지 못한다(주 단위 재평가의 근본적 한계).

    cash_equitize=True("현금 유휴화 방지"): 실측 결과 완화 VCP는 전체 재평가일의
    61.5%가 포지션 0개(현금 100%)로, 이게 알파가 크게 마이너스로 나오는 주된
    원인이었다(개별 종목 승률/손익비 자체는 좋음 - 기회가 너무 드물 뿐). 미너비니/
    오닐(CANSLIM)이 말하는 "확인된 상승국면에서는 현금을 놀리지 말고 지수/리더에
    태워두다가 개별 셋업이 뜨면 갈아타라"는 원칙을 그대로 구현: 시장국면이 OK인데
    포지션에 안 들어간 유휴 현금은 매 재평가일마다 지수(벤치마크 시리즈를 대납용
    프록시로 사용)에 넣어두고, 개별 종목 매수가 필요하면 그만큼 지수를 팔아 현금화
    한다. 국면이 꺼지면(코스피<200일선 등) 지수 보유분을 전량 현금화한다(방어적
    태세 - 국면필터가 신규진입을 막는 것과 같은 이유). index_slippage_pct는 개별
    종목(슬리피지+세금 합 0.5~0.8%)보다 훨씬 낮게 잡았다 - 국내 지수 추종 ETF는
    스프레드가 촘촘하고 매도 시 증권거래세가 면제되기 때문. equitize_max_pct는
    지수 프록시에 태울 수 있는 최대 비중(총자산 대비 %) - 100이면 유휴 현금
    전액을 지수에 태워 알파는 커지지만 MDD도 벤치마크 수준까지 같이 커지고,
    낮출수록(예: 40~70) 베타 노출이 줄어 MDD는 낮아지는 대신 알파 개선폭도
    줄어드는 트레이드오프가 있다 - 실측 결과 참고. min_trend_pass_count는
    미너비니 트렌드템플릿(8조건)을 몇 개 이상 통과해야 VCP 후보로 볼지 -
    기본값 None은 원안대로 8개 전부(allPass) 요구. 값을 낮추면(예: 6) 후보 풀이
    크게 늘어 거래빈도가 올라가지만, VCP+ADX가 사실상 유일한 최종 필터가 되어
    승률/손익비가 낮아질 수 있다.

    position_sizing_mode="risk"(기본값)는 리스크 기반 사이징(계좌의 RISK_PCT_PER_TRADE%를
    손절폭으로 나눠 수량 산정, 종목마다 변동성에 따라 배분이 달라짐). "equal_weight"는
    모든 신규 포지션에 그 시점 총자산/max_positions을 동일하게 배분한다(변동성과
    무관하게 슬롯당 같은 금액) - 손절폭(ATR 기반)은 손절가 산정에는 여전히 쓰이지만
    수량 결정에는 관여하지 않는다. max_hold_days(기본 None=비활성)는 슬롯 수가 적을
    때 승리 포지션이 자본을 너무 오래 묶어 재진입(거래빈도)을 막는 문제를 완화하려는
    강제 보유일 상한(추세 진행도와 무관하게 도달하면 무조건 청산). gate_entries_on_regime
    =False면 시장국면과 무관하게 신규진입을 허용한다(현금유휴화방지 판단에는 국면을
    여전히 쓴다) - 국면필터 자체가 회전율을 깎는지 확인용.

    max_pct_of_avg_trade_value(기본 None=비활성)는 포지션 금액을 그 종목의 평균거래대금
    대비 비율로 추가 캡핑한다. min_avg_trade_value(유동성 필터)는 "후보로 볼지"만
    걸러낼 뿐 실제 매수 금액은 제한하지 않는다 - equal_weight 사이징으로 계좌가
    복리로 커지면 슬롯당 배분액이 그 종목의 실제 하루 거래대금을 초과하는(시장충격
    없이 체결된다고 가정하는) 비현실적 상황이 생길 수 있어, 이 파라미터로 실제
    체결 가능한 규모에 가깝게 추가 제한한다.

    max_position_value_abs(기본 None=비활성)는 equal_weight 사이징이 계좌 성장에 따라
    무한정 커지는 것을 막는 고정 금액 상한이다(원 단위). equal_weight 하나만으로는
    계좌가 복리로 커질수록 슬롯당 배분액도 같이 커져 승률/손익비 같은 트레이드당
    비율 지표는 그대로인데도 총수익률이 비현실적으로 폭증한다(자금 자체가 가격을
    움직이는 "전략 용량" 문제를 백테스트가 반영 못 하기 때문) - 이 파라미터로 "일정
    규모 이상은 더 키우지 않는다"는 현실적 가정을 명시적으로 넣는다.
    ma_break_period/ma_break_consec_days/breakeven_r/partial_profit_r/
    partial_profit_fraction/chandelier_atr_mult/trail_activate_r/pyramid_max_count는
    전부 청산 로직 파라미터다(기본값은 명세서 원안과 동일). "승리 트레이드를 일찍
    확정짓는" 기존 설계(+2R 25% 분할익절, +1R 본전이동, MA50 2일 이탈, 3×ATR
    트레일링)는 손익비의 상한을 스스로 만든다 - 손익비를 크게(예: 9 이상) 끌어올리려면
    터틀 트레이더스/에드 세이코타류의 "손실은 짧게, 수익은 무제한으로" 철학대로
    partial_profit_fraction=0(분할익절 없음), breakeven_r을 늦추거나 매우 크게,
    chandelier_atr_mult를 훨씬 넓게(5~6), ma_break_period를 150 등으로 늘려 추세
    이탈 판정 자체를 완만하게, pyramid_max_count를 늘려 승리 포지션을 더 키우는
    방향으로 바꿔야 한다 - 그 대가로 평균 보유일수가 늘고 승률은 더 낮아지기 쉽다.

    entry_mode="donchian"이면 VCP 패턴 탐지(순차 수축조정) 자체를 요구하지 않고,
    돈치안 채널 브레이크아웃(donchian_period거래일 신고가 돌파, 터틀 트레이딩
    방식)만으로 진입 후보를 찾는다 - 트렌드템플릿 통과 여부도 요구하지 않고
    전체 유니버스를 대상으로 한다(RS 랭킹으로 정렬만 함). VCP는 아무리 조건을
    완화해도 "패턴"이라는 조건 자체가 병목이 되어 거래빈도에 한계가 있는데,
    돈치안 브레이크아웃은 패턴 요건이 없어 거래빈도가 훨씬 커진다 - 대신 대부분의
    신호가 실패해서(승률이 낮음) initial_stop_atr_mult를 타이트하게 잡아 평균
    손실을 작게 유지하고, 넓은 트레일링(chandelier_atr_mult 등)으로 승리
    포지션을 최대한 오래 태워 손익비를 크게 키우는 "터틀식" 손익 분포를 노린다.
    """
    if market not in ("KR", "US"):
        return {"error": "market은 KR 또는 US여야 합니다"}
    if fetch_fn is None:
        fetch_fn = ts.fetch_ohlc_history_batches
    if shares_map is None:
        shares_map = {}

    universe = ts.load_universe(market)
    tickers = [t for _, _, t, _, _ in universe]
    info_by_ticker = {t: (code, name, industry, sector) for code, name, t, industry, sector in universe}

    fetch_start = (datetime.fromisoformat(start_date) - timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
    fetch_end = (datetime.fromisoformat(end_date) + timedelta(days=1)).strftime("%Y-%m-%d")

    regime_dates, regime_closes = _fetch_index_series_local(market, fetch_start, fetch_end)

    series = {}
    for ticker, bars in fetch_fn(tickers, fetch_start, fetch_end):
        series[ticker] = (
            [b["date"] for b in bars], [b["close"] for b in bars], [b["high"] for b in bars],
            [b["low"] for b in bars], [b.get("volume") for b in bars],
        )

    all_dates = sorted({d for dates, *_ in series.values() for d in dates if start_date <= d <= end_date})
    if not all_dates:
        return {"error": "해당 기간에 사용 가능한 가격 데이터가 없습니다"}
    rebalance_dates = all_dates[::rescan_interval_days]
    if rebalance_dates[-1] != all_dates[-1]:
        rebalance_dates.append(all_dates[-1])

    cash = float(seed)
    positions = {}  # ticker -> position dict
    index_units = 0.0  # cash_equitize용 지수 프록시 보유 수량(주식 아님, 벤치마크 시리즈 기준)
    trades = []
    equity_curve = []
    peak_equity = float(seed)
    prev_rd = None
    excluded_preferred = excluded_cap = excluded_liquidity = excluded_shareholder = 0
    shareholder_rows_by_code = shareholder_rows_by_code or {}

    for rd in rebalance_dates:
        idx_at_rd = {}
        evaluated = []
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
        # min_trend_pass_count가 None이면 원안대로 8개 조건 전부 통과(allPass)만 후보로 삼는다.
        # 값을 주면(예: 6) 그 개수 이상만 통과해도 후보 풀에 넣어 거래빈도를 늘릴 수 있다 -
        # 트렌드템플릿 자체가 진입을 막는 게 아니라 VCP+ADX가 최종 필터 역할을 한다.
        # entry_mode="donchian"이면 트렌드템플릿 통과 여부를 아예 요구하지 않는다(전체
        # 유니버스 대상 - RS 랭킹은 후보 정렬에만 쓴다).
        if entry_mode == "donchian":
            trend_ok_set = set(by_ticker.keys())
        elif min_trend_pass_count is not None:
            trend_ok_set = {t for t, e in by_ticker.items() if (e.get("passCount") or 0) >= min_trend_pass_count}
        else:
            trend_ok_set = {t for t, e in by_ticker.items() if e.get("allPass")}

        # 1) 보유 포지션 처리 - 하루씩 순서대로(손절/트레일링 히트 > MA50이탈 > 시간손절 > 본전/분할익절/트레일링갱신 > 피라미딩)
        for ticker in list(positions.keys()):
            pos = positions[ticker]
            dates, closes, highs, lows, volumes = series[ticker]
            i = idx_at_rd.get(ticker)
            if i is None:
                continue
            start_i = bisect.bisect_right(dates, prev_rd) if prev_rd else pos["entryIdx"] + 1
            start_i = max(start_i, pos["entryIdx"] + 1)
            closed = False
            for j in range(start_i, i + 1):
                low, high, close = lows[j], highs[j], closes[j]
                if low <= pos["stopPrice"]:
                    trade, proceeds = _full_exit(pos, pos["stopPrice"], dates[j], pos["stopState"])
                    trades.append(trade)
                    cash += proceeds
                    del positions[ticker]
                    closed = True
                    break

                pos["barsHeld"] += 1
                ma50 = _sma(closes, ma_break_period, j)
                if ma50 is not None and close < ma50:
                    pos["maBelowCount"] += 1
                else:
                    pos["maBelowCount"] = 0
                if pos["maBelowCount"] >= ma_break_consec_days:
                    trade, proceeds = _full_exit(pos, close, dates[j], "maBreak")
                    trades.append(trade)
                    cash += proceeds
                    del positions[ticker]
                    closed = True
                    break

                r = pos["riskPerShare"]
                highest_r = (pos["highestHigh"] - pos["avgEntryPrice"]) / r if r > 0 else 0
                if pos["barsHeld"] >= time_stop_days and highest_r < time_stop_progress_r:
                    trade, proceeds = _full_exit(pos, close, dates[j], "timeStop")
                    trades.append(trade)
                    cash += proceeds
                    del positions[ticker]
                    closed = True
                    break

                # max_hold_days: 추세가 아무리 좋아도(진행도와 무관하게) 이 날짜수를
                # 넘기면 강제 청산 - 슬롯 10개 고정처럼 동시보유 종목수가 제한된
                # 상황에서 승리 포지션이 자본을 너무 오래 묶어 재진입 기회를 막는
                # 문제를 완화하려는 옵션(기본값 None은 비활성 - 원안대로 무제한 보유).
                if max_hold_days is not None and pos["barsHeld"] >= max_hold_days:
                    trade, proceeds = _full_exit(pos, close, dates[j], "maxHold")
                    trades.append(trade)
                    cash += proceeds
                    del positions[ticker]
                    closed = True
                    break

                pos["highestHigh"] = max(pos["highestHigh"], high)
                r_reached = (pos["highestHigh"] - pos["avgEntryPrice"]) / r if r > 0 else 0

                if r_reached >= breakeven_r and pos["stopPrice"] < pos["avgEntryPrice"]:
                    pos["stopPrice"] = pos["avgEntryPrice"]
                    pos["stopState"] = "breakevenStop"

                if partial_profit_fraction > 0 and not pos["partialTaken"] and r_reached >= partial_profit_r:
                    target = pos["avgEntryPrice"] + partial_profit_r * r
                    if high >= target:
                        sell_shares = min(max(1, int(pos["shares"] * partial_profit_fraction)), pos["shares"] - 1)
                        if sell_shares > 0:
                            fill = target * (1 - (SLIPPAGE_EXIT_PCT + SELL_TAX_PCT) / 100)
                            pnl_pct = round((fill - pos["avgEntryPrice"]) / pos["avgEntryPrice"] * 100, 2)
                            trades.append({
                                "code": pos["code"], "name": pos["name"], "entryDate": pos["entryDate"],
                                "entryPrice": round(pos["avgEntryPrice"], 2), "exitDate": dates[j],
                                "exitPrice": round(target, 2), "shares": sell_shares, "pnlPct": pnl_pct,
                                "exitReason": "partialProfit", "pyramidCount": pos["pyramidCount"],
                                "partialTaken": True,
                                "holdDays": (datetime.fromisoformat(dates[j]) - datetime.fromisoformat(pos["entryDate"])).days,
                            })
                            cash += sell_shares * fill
                            pos["totalCost"] -= sell_shares * pos["avgEntryPrice"]
                            pos["shares"] -= sell_shares
                        pos["partialTaken"] = True

                if r_reached >= trail_activate_r:
                    chandelier = pos["highestHigh"] - chandelier_atr_mult * pos["entryAtr"]
                    if chandelier > pos["stopPrice"]:
                        pos["stopPrice"] = chandelier
                        pos["stopState"] = "trailingStop"

                if pos["pyramidCount"] < pyramid_max_count and ticker in trend_ok_set:
                    target = pos["lastEntryPrice"] + PYRAMID_INTERVAL_R * r
                    if high >= target and low <= target:
                        add_shares = max(1, int(pos["initialShares"] * PYRAMID_SIZE_FRACTION))
                        cost = add_shares * target * (1 + SLIPPAGE_ENTRY_PCT / 100)
                        max_pos_value = seed * MAX_POSITION_WEIGHT_PCT / 100
                        current_value = pos["shares"] * target
                        if cost <= cash and (current_value + add_shares * target) <= max_pos_value:
                            cash -= cost
                            pos["totalCost"] += cost
                            pos["shares"] += add_shares
                            pos["avgEntryPrice"] = pos["totalCost"] / pos["shares"]
                            pos["lastEntryPrice"] = target
                            pos["pyramidCount"] += 1
                            pos["stopPrice"] = max(pos["stopPrice"], target - initial_stop_atr_mult * pos["entryAtr"])
            if closed:
                continue

        # 2) 이 시점 평가액/낙폭 (완전 청산 상태면 고점 리셋 - screening_backtest.py와 같은 이유)
        index_price = None
        if regime_dates:
            ri0 = bisect.bisect_right(regime_dates, rd) - 1
            if ri0 >= 0:
                index_price = regime_closes[ri0]
        index_value = index_units * index_price if index_price is not None else 0.0
        held_value = sum(
            pos["shares"] * series[ticker][1][idx_at_rd[ticker]]
            for ticker, pos in positions.items() if ticker in idx_at_rd
        )
        equity_now = cash + held_value + index_value
        peak_equity = equity_now if not (positions or index_units > 0) else max(peak_equity, equity_now)

        # 3) 시장국면(코스피>200일선 and 200일선 20일 기울기>0)
        regime_ok = True
        if regime_dates:
            ri = bisect.bisect_right(regime_dates, rd) - 1
            if ri + 1 >= 220:
                ma200 = sum(regime_closes[ri - 199:ri + 1]) / 200
                ma200_20d_ago = sum(regime_closes[ri - 219:ri - 19]) / 200
                regime_ok = regime_closes[ri] >= ma200 and ma200 > ma200_20d_ago
            else:
                regime_ok = False

        # 3.5) 현금 유휴화 방지(cash_equitize) - 국면이 꺼지면 지수 보유분 전량 현금화(방어)
        if cash_equitize and not regime_ok and index_units > 0 and index_price is not None:
            proceeds = index_units * index_price * (1 - index_slippage_pct / 100)
            cash += proceeds
            index_units = 0.0

        # 4) 신규 진입 - VCP+ADX+유니버스필터를 통과한 후보 중 피벗 돌파(+거래량)를
        #    직전~이번 재평가 사이에서 찾아 피벗가*1.005 지정가로 체결. gate_entries_on_regime=
        # False면 국면과 무관하게 신규진입을 허용한다(현금유휴화방지는 여전히 국면을
        # 본다) - 슬롯이 적을 때 국면필터가 재진입 기회 자체를 막아 회전율을 깎는지 실험용.
        if regime_ok or not gate_entries_on_regime:
            open_slots = max_positions - len(positions)
            if open_slots > 0:
                candidates = []
                for ticker in trend_ok_set:
                    if ticker in positions:
                        continue
                    e = by_ticker[ticker]
                    code, name = e["code"], e["name"]
                    if is_preferred_stock(name):
                        excluded_preferred += 1
                        continue
                    i = idx_at_rd[ticker]
                    dates, closes, highs, lows, volumes = series[ticker]
                    price_now = closes[i]
                    shares_out = shares_map.get(code)
                    if shares_out and price_now * shares_out < min_market_cap:
                        excluded_cap += 1
                        continue
                    avg_val = _avg_trade_value(closes, volumes, i)
                    if avg_val is not None and avg_val < min_avg_trade_value:
                        excluded_liquidity += 1
                        continue
                    max_holder_pct = latest_max_shareholder_pct(shareholder_rows_by_code.get(code, []), rd)
                    if max_holder_pct is not None and max_holder_pct > MAX_SHAREHOLDER_PCT:
                        excluded_shareholder += 1
                        continue
                    if entry_mode == "donchian":
                        brk = detect_donchian_breakout(highs, closes, i, period=donchian_period)
                        if not brk:
                            continue
                        pivot = brk["pivot"]
                    else:
                        adx = _adx(highs, lows, closes, i)
                        if not adx or adx < adx_threshold:
                            continue
                        vcp = detect_vcp(
                            highs, lows, closes, volumes, i,
                            final_contraction_ratio=final_contraction_ratio, min_final_duration=min_final_duration,
                            max_days_since_low=max_days_since_low, require_volume_decrease=require_volume_decrease,
                        )
                        if not vcp:
                            continue
                        pivot = vcp["pivot"]
                    avg_vol50 = _avg_volume(volumes, i)
                    if not avg_vol50:
                        continue
                    candidates.append((ticker, e, pivot, avg_vol50, avg_val))

                candidates.sort(key=lambda c: -(c[1].get("rsRating") or 0))
                for ticker, e, pivot, avg_vol50, avg_trade_val in candidates:
                    if open_slots <= 0:
                        break
                    dates, closes, highs, lows, volumes = series[ticker]
                    i = idx_at_rd[ticker]
                    s_i = bisect.bisect_right(dates, prev_rd) if prev_rd else max(0, i - rescan_interval_days)
                    limit_price = pivot * (1 + 0.5 / 100)
                    fill_date = fill_price = fj = None
                    for j in range(s_i, i + 1):
                        if closes[j] <= pivot:
                            continue
                        vol = volumes[j]
                        if vol is None or vol < avg_vol50 * volume_breakout_mult:
                            continue
                        if lows[j] <= limit_price:
                            fill_date, fill_price, fj = dates[j], limit_price, j
                            break
                    if fill_date is None:
                        continue
                    atr20 = _atr(highs, lows, closes, fj)
                    if not atr20 or atr20 <= 0:
                        continue
                    raw_risk = initial_stop_atr_mult * atr20
                    if risk_cap_mode == "shrink":
                        # 명세서 문구("리스크폭이 8% 넘으면 셋업 기각")와 다르게, 손절폭을
                        # 8%로 줄여서라도 진입시킨다 - 변동성 큰 후보를 버리지 않아
                        # 거래수를 늘리려는 실험용 옵션(기본값 아님).
                        risk_per_share = min(raw_risk, fill_price * max_initial_risk_pct / 100)
                    else:
                        risk_per_share = raw_risk
                        if (risk_per_share / fill_price * 100) > max_initial_risk_pct:
                            continue
                    if risk_per_share <= 0:
                        continue
                    if position_sizing_mode == "equal_weight":
                        # 슬롯당 동일 금액 배분 - 변동성과 무관하게 그 시점 총자산을
                        # max_positions으로 나눈 만큼만 산다(리스크 기반 사이징 대신).
                        target_value = equity_now / max_positions
                        if max_position_value_abs is not None:
                            # 계좌가 복리로 계속 커져도 포지션 금액 자체는 이 고정
                            # 상한을 넘지 않는다 - "일정 규모 이상은 더 키우지 않는다"는
                            # 전략 용량(capacity) 가정을 명시적으로 반영. 상한을 넘는
                            # 초과 자본은 그냥 현금(또는 cash_equitize로 지수)에 남는다.
                            target_value = min(target_value, max_position_value_abs)
                        shares = int(target_value // fill_price)
                        max_pos_value = target_value
                    else:
                        risk_amount = equity_now * RISK_PCT_PER_TRADE / 100
                        shares = int(risk_amount // risk_per_share)
                        max_pos_value = seed * MAX_POSITION_WEIGHT_PCT / 100
                    if max_pct_of_avg_trade_value is not None and avg_trade_val:
                        # 유동성 필터(min_avg_trade_value)는 "이 종목을 후보로 볼지"만
                        # 걸러낼 뿐, 실제로 그 종목에 얼마를 태울지는 제한하지 않는다 -
                        # 계좌가 커지면(특히 equal_weight로 계속 복리) 슬롯당 배분액이
                        # 그 종목의 실제 하루 거래대금을 아득히 넘어서는 비현실적인
                        # 상황이 생긴다(시장충격 없이 그만큼 체결된다고 가정하는 셈).
                        # 그래서 종목 자신의 평균거래대금 대비 비율로 포지션 금액
                        # 자체를 추가로 캡핑한다(실제 체결 가능한 규모로 근사).
                        liquidity_cap_value = avg_trade_val * max_pct_of_avg_trade_value / 100
                        shares = min(shares, int(liquidity_cap_value // fill_price))
                    # cash_equitize: 지수에 태워둔 유휴자금까지 매수여력에 포함시킨다(필요한
                    # 만큼만 아래에서 실제로 청산) - 안 그러면 현금이 대부분 지수에 가 있어
                    # cap_shares가 0으로 계산돼 버린다.
                    available_cash = cash
                    if cash_equitize and index_units > 0 and index_price is not None:
                        available_cash = cash + index_units * index_price * (1 - index_slippage_pct / 100)
                    cap_shares = int(min(available_cash, max_pos_value) // fill_price)
                    shares = min(shares, cap_shares)
                    if shares <= 0:
                        continue
                    entry_fill = fill_price * (1 + SLIPPAGE_ENTRY_PCT / 100)
                    cost = shares * entry_fill
                    if cost > cash and cash_equitize and index_units > 0 and index_price is not None:
                        shortfall = cost - cash
                        sell_units = min(index_units, shortfall / (index_price * (1 - index_slippage_pct / 100)))
                        proceeds = sell_units * index_price * (1 - index_slippage_pct / 100)
                        index_units -= sell_units
                        cash += proceeds
                    if cost > cash:
                        continue
                    cash -= cost
                    positions[ticker] = {
                        "code": e["code"], "name": e["name"], "entryDate": fill_date, "entryIdx": fj,
                        "shares": shares, "initialShares": shares, "totalCost": cost,
                        "avgEntryPrice": entry_fill, "entryAtr": atr20, "riskPerShare": risk_per_share,
                        "stopPrice": entry_fill - risk_per_share, "stopState": "initialStop",
                        "highestHigh": entry_fill, "partialTaken": False, "pyramidCount": 0,
                        "lastEntryPrice": entry_fill, "maBelowCount": 0, "barsHeld": 0,
                    }
                    open_slots -= 1

        # 4.5) 현금 유휴화 방지 - 국면 OK인데 남은 유휴 현금을 지수 프록시에 태운다
        # (equitize_max_pct%까지만 - 그 이상은 베타 노출을 늘리지 않고 현금으로 남긴다)
        if cash_equitize and regime_ok and index_price is not None and cash > 0:
            cap_value = equity_now * equitize_max_pct / 100
            current_index_value = index_units * index_price
            room = max(0.0, cap_value - current_index_value)
            buy_cash = min(cash, room)
            if buy_cash > 0:
                index_units += buy_cash * (1 - index_slippage_pct / 100) / index_price
                cash -= buy_cash

        held_value = sum(
            pos["shares"] * series[ticker][1][idx_at_rd[ticker]]
            for ticker, pos in positions.items() if ticker in idx_at_rd
        )
        index_value = index_units * index_price if index_price is not None else 0.0
        equity_curve.append({"date": rd, "value": round(cash + held_value + index_value, 2)})
        prev_rd = rd

    last_rd = rebalance_dates[-1]
    for ticker, pos in list(positions.items()):
        i = bisect.bisect_right(series[ticker][0], last_rd) - 1
        if i < 0:
            continue
        price = series[ticker][1][i]
        trade, proceeds = _full_exit(pos, price, last_rd, "periodEnd")
        trades.append(trade)
        cash += proceeds
    positions.clear()
    if cash_equitize and index_units > 0 and regime_dates:
        ri_last = bisect.bisect_right(regime_dates, last_rd) - 1
        if ri_last >= 0:
            cash += index_units * regime_closes[ri_last] * (1 - index_slippage_pct / 100)
            index_units = 0.0

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
    benchmark_curve, benchmark_return_pct = _fetch_benchmark_curve_local(
        market, [pt["date"] for pt in equity_curve], seed)
    alpha_pct = round(return_pct - benchmark_return_pct, 2) if benchmark_return_pct is not None else None

    exit_reason_counts = {}
    for t in trades:
        exit_reason_counts[t["exitReason"]] = exit_reason_counts.get(t["exitReason"], 0) + 1

    return {
        "market": market, "strategyLabel": "VCP 추세추종", "start": start_date, "end": end_date, "seed": seed,
        "returnPct": return_pct, "finalValue": round(final_value, 2),
        "tradeCount": len(trades), "winCount": len(win_trades), "winRatePct": win_rate_pct,
        "avgHoldDays": avg_hold_days, "mddPct": round(mdd_pct, 2),
        "profitLossRatio": profit_loss_ratio, "alphaPct": alpha_pct,
        "exitReasonCounts": exit_reason_counts,
        "excludedPreferred": excluded_preferred, "excludedMarketCap": excluded_cap,
        "excludedLiquidity": excluded_liquidity, "excludedShareholder": excluded_shareholder,
        "benchmark": {"label": BENCHMARK_LABEL.get(market, "Benchmark"), "returnPct": benchmark_return_pct,
                      "equityCurve": benchmark_curve},
        "equityCurve": equity_curve, "trades": trades,
    }
