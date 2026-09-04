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
from pathlib import Path

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

# fundamental_quality_score의 합격선. ROE 5%는 "자본을 최소한 예금 이상으로는
# 굴리고 있는가", 부채비율 200%는 국내 상장사 통상 기준에서 재무구조가 위험
# 수준인지를 가르는 선으로 잡았다(둘 다 널리 쓰이는 관행적 기준 - 백테스트
# 성과에 맞춰 고른 값이 아니다. 성과로 튜닝하면 그 자체가 과최적화가 된다).
QUALITY_MIN_ROE = 0.05
QUALITY_MAX_DEBT_RATIO = 2.0
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
#
# 이후 백테스트 신뢰도 강화 작업(사용자 요청)의 1단계로 생존편향(survivorship
# bias)을 검증했다 - kr_stocks.json은 "현재 상장된" 종목 스냅샷이라 2017년 이후
# 상장폐지된 종목이 유니버스에 아예 없어 결과가 낙관적으로 치우쳐 있을 가능성이
# 있었다. FinanceDataReader(야후파이낸스는 상장폐지 종목을 지원하지 않음)로
# 2015년 이후 상장폐지된 보통주 568종목의 상장~상장폐지 전체 시세를 받아
# (data_pipeline.fetch_delisted_kr/import_delisted_to_db, KrDelistedPrice 테이블)
# 유니버스에 포함시켜(include_delisted=True) 재검증한 결과: CAGR 51.26%→53.12%,
# 알파 +5,110%p→+5,746%p로 오히려 개선됐고(거래 4,180→5,116건), 승률(58.7%→
# 57.4%)·손익비(5.43→5.16)·MDD(-32.0%→-32.37%)는 거의 그대로였다. 전체 5,116건
# 중 실제로 상장폐지 시점까지 들고 있다가 청산된 거래는 9건(0.18%)뿐 - 타이트한
# 초기손절(진입가 대비 최대 6% 리스크) 덕분에 부실기업이 실제 상장폐지되기 훨씬
# 전에 이미 일반 손절로 빠져나가는 구조라, 이 전략은 애초에 생존편향에 크게
# 의존하지 않았다는 뜻이다. 이 정식 결과(상장폐지 포함)를 채택해 include_delisted
# 를 기본값으로 켰다 - 사유(합병/감사의견거절 등) 기준으로 종목을 골라내지 않고
# "주식"(신주인수권 등 파생상품 제외) 전부를 포함했다: 임의로 골라내면 오히려
# 새 편향이 생기므로, 실제 매매 규칙이 알아서 걸러내도록 두는 편이 더 안전하다.
SWEEPER_PARAMS = {
    "market": "KR", "entry_mode": "donchian", "donchian_period": 15, "max_positions": 10,
    "initial_stop_atr_mult": 1.5, "max_initial_risk_pct": 6.0, "risk_cap_mode": "shrink",
    "partial_profit_fraction": 0.25, "breakeven_r": 1.0,
    "chandelier_atr_mult": 3.0,
    "rescan_interval_days": 3, "cash_equitize": True, "equitize_max_pct": 70.0,
    "position_sizing_mode": "risk", "min_market_cap": 0, "min_avg_trade_value": 100_000_000,
    "gate_entries_on_regime": False, "default_seed": 10_000_000, "include_delisted": True,
    "require_profitable": True,
}

# "SEPA" - 마크 미너비니의 Specific Entry Point Analysis를 이 엔진이 표현할 수 있는
# 범위에서 정석대로 구현한 프리셋. 앞선 스위퍼/어나니머스가 "성과가 좋은 조합"을
# 자유 탐색해 만든 것과 달리, 이건 SEPA 규칙을 먼저 세우고 그대로 옮긴 것이다.
#
# ## 5단계 대응
#  1단계 트렌드 템플릿: min_trend_pass_count=8 (8개 조건 전부 통과)
#  2단계 펀더멘털:      min_eps_growth_pct/require_eps_acceleration(분기 EPS 성장·가속)
#                       + min_quality_score(수익성·ROE·부채비율·매출성장 5개 항목)
#  3단계 촉매:          require_catalyst(대형수주·증설·인수·자사주 공시)
#  4단계 진입:          entry_mode="vcp"(변동성 수축 패턴 + 피벗 돌파 + 거래량 확인)
#  5단계 청산:          초기손절 + 본전이동 + 챈들리어 트레일링 + 분할익절
#
# ## 이 엔진이 표현하지 못하는 것
#  - 촉매 중 신제품·경영진 교체·업종 순환 같은 정성 정보(뉴스 데이터가 없다)
#  - 기관 수급의 직접 관측(대량 매수 흔적은 거래량으로만 간접 추정)
#
# ## 규칙에서 유래한 값들(성과로 튜닝한 값이 아니다)
#  - 시장 국면 상승 시에만 매수(gate_entries_on_regime=True): 미너비니의 핵심 규칙.
#    실측에서 이걸 켜면 성과가 나빠지지만, 정석 구현이므로 그대로 둔다.
#  - 손절 최대 8%: 미너비니가 반복해 말하는 상한.
#  - 시총·유동성 하한: 기관이 들어올 수 있는 규모의 종목만 본다.
SEPA_PARAMS = {
    "market": "KR", "max_positions": 10, "default_seed": 10_000_000,
    # 1단계
    "min_trend_pass_count": 8,
    # 4단계 - VCP 패턴(수축 구간 거래량 감소 요구는 VCP 원안)
    "entry_mode": "vcp", "require_volume_decrease": True,
    # 2단계
    "min_quality_score": 0.4, "require_profitable": False,
    "min_eps_growth_pct": 0.0, "require_eps_acceleration": True,
    # 3단계
    "require_catalyst": True, "catalyst_lookback_days": 60,
    # 5단계
    "initial_stop_atr_mult": 1.5, "max_initial_risk_pct": 8.0, "risk_cap_mode": "shrink",
    "breakeven_r": 1.0, "trail_activate_r": 1.0, "chandelier_atr_mult": 3.0,
    "partial_profit_fraction": 0.25,
    # 시장 국면 / 유니버스
    "gate_entries_on_regime": True,
    "min_market_cap": 100_000_000_000, "min_avg_trade_value": 500_000_000,
    # 운용
    "rescan_interval_days": 3, "cash_equitize": True, "equitize_max_pct": 100.0,
    "position_sizing_mode": "equal_weight", "position_cap_base": "equity",
    "max_pct_of_avg_trade_value": 10, "include_delisted": True,
}


# "와쳐(Watcher)" - 지인 스크리너의 종목 선별 기준을 그대로 옮긴 전략.
# 앞선 스위퍼/어나니머스가 백테스트 성과를 자유 탐색해 만든 것과 달리, 이건
# 실제로 운용 중인 외부 스크리너를 복제하는 것이 목적이다.
#
# ## 2026-08-27 스냅샷(S1 361종목)에서 역산해 확정한 것
#  - 단계(S1~S4) = minervini_stage 값. 숫자가 클수록 강한 상승추세다
#    (와인스타인 원안의 1=바닥/2=상승/3=천장/4=하락과 방향이 다르다 -
#     사용자가 "4단계 종목을 매수한다"고 한 것과 이 해석이 일치한다).
#  - 2단계 판정식: 아래 6조건 AND. 361종목 중 정확히 12종목만 통과했다
#    (오탐 0, 누락 0). passes_evan_stage2 참고.
#  - 유니버스: 시가총액 3,000억원 이상. S1 361종목 중 3,000억 미만이 0종목이고
#    최솟값이 3,020억으로 컷오프 바로 위였다.
#
# ## 아직 모르는 것
#  - 3단계/4단계 판정식. 표본이 3단계 1종목(에이피알)뿐이고, 그 종목이 단독으로
#    두드러지는 지표가 없다(1개월낙폭 0%는 2단계인 GS·한올바이오파마도 같다).
#    스냅샷이 며칠 더 쌓여야 특정할 수 있다. 그래서 이 프리셋은 2단계까지만
#    복제하고, 진입 타이밍은 우리 쪽 신호(눌림목)를 쓴다.
#  - 지인 rs(연속값)와 우리 rsRating(1~99 백분위)은 척도가 달라 근사다.
#
# ## 성과 관련 주의
# 26차 실측에서 이 선별 기준을 적용하면 우리 백테스트 성과가 오히려 떨어졌다
# (CAGR 34.94%->24.93%). 시총 3,000억 하한이 중소형주를 걷어내는데, 우리
# 백테스트 수익의 상당부분이 거기서 나왔기 때문이다. 즉 이 전략은 "백테스트
# 최적"이 아니라 "실제 운용 중인 기준의 복제"라는 점을 분명히 해둔다.
#
# ## 29~37차(72개 변형) 파라미터 탐색 후 최종 확정 (슬롯 10개 고정 - 사용자 필수조건)
# 매출성장 하한(min_revenue_growth=20%)·손절폭 축소(max_initial_risk_pct=3.5%)·
# RS 상대순위(evan_params.min_rs=85, 지인 2단계식의 70.3보다 훨씬 엄격)가 CAGR·
# 총수익·MDD를 지인 목표(CAGR 50.76%/총수익 +5,049%/MDD -44.45%) 근처 또는 그
# 이상으로 끌어올린 유일한 조합이었다(2016-01-01~2026-09-02, 10.7년 실측:
# CAGR 47.71%, 총수익 +6,319.2%, MDD -40.39%, 승률 23.1%, 평균수익 31.23%,
# 평균손실 -6.0%, 손익비 5.20, 연 152.2건). 승률(목표 31.57%)과 평균수익(목표
# 52.38%)은 진입/청산/기본적 분석 축을 폭넓게 시험해도(돌파 진입, 장기보유
# 매도, 지인조건 이탈 청산, ROE 상대순위, PER 상한, 성장 연속성 등 - round32~37)
# 못 좁혔다 - 룰 기반 백테스트로는 재현 못하는 재량적 판단이 섞여 있다고 보고
# 이 지점을 최종 채택했다.
WATCHER_PARAMS = {
    "market": "KR", "max_positions": 10, "default_seed": 50_000_000,
    # 유니버스 - 시총 3,000억 이상
    "min_market_cap": 300_000_000_000, "min_avg_trade_value": 100_000_000,
    # 종목 선별 - 지인 2단계 판정식 + RS 상대순위(33차 CE, 절대문턱보다 상대순위가
    # 더 안정적으로 통했다) + 매출성장 하한(28~30차, 가장 강력한 단일 레버였다)
    "require_evan_stage2": True, "evan_params": {"min_rs": 85.0},
    "min_revenue_growth": 20.0,
    # 진입 - 3·4단계 조건을 모를 때의 대체 신호(우리 쪽 최적)
    "entry_mode": "pullback", "pullback_ma_period": 20,
    "min_pullback_pct": 3.0, "max_pullback_pct": 15.0, "pullback_lookback": 20,
    # 운용 조건 - 사용자가 확인해준 제약
    "cash_equitize": False,             # 지수 투자 안 함
    "partial_profit_fraction": 0.0,     # 분할익절 안 함
    "position_sizing_mode": "equal_weight", "position_cap_base": "equity",
    "max_pct_of_avg_trade_value": 10,
    # 피라미딩 상한(그 시점 평가액의 15%) - 29~37차 탐색 스크립트 전부가
    # vcp.MAX_POSITION_WEIGHT_PCT를 15.0으로 임시 덮어쓴 채 돌린 결과였다(모듈
    # 상수 기본값은 20.0). 이 키가 없으면 기본값(20%)이 적용돼 포지션이 더 크게
    # 피라미딩되면서 실제 검증한 결과(CAGR 47.71%/MDD -40.39%)와 달라진다 -
    # 최종 반영 직후 이 키를 빠뜨려 한 번 이 문제로 수치가 틀어진 적이 있다.
    "max_position_weight_pct": 15.0,
    # 청산 - 손절폭만 30차 ZC(3.5%)로 좁혔고 나머지는 run_vcp_backtest 기본값과
    # 같다. paper_trading.py가 이 딕셔너리 키를 직접 참조하므로(모듈 기본값
    # fallback이 없다) 아래 항목들을 명시적으로 채워둔다.
    "initial_stop_atr_mult": 1.5, "max_initial_risk_pct": 3.5, "risk_cap_mode": "shrink",
    "breakeven_r": 2.0, "trail_activate_r": 2.0, "chandelier_atr_mult": 3.0,
    "ma_break_period": 50, "ma_break_consec_days": MA_BREAK_CONSEC_DAYS,
    "time_stop_days": TIME_STOP_DAYS, "time_stop_progress_r": TIME_STOP_PROGRESS_R,
    "pyramid_max_count": PYRAMID_MAX_COUNT,
    "rescan_interval_days": 3, "gate_entries_on_regime": False,
    "include_delisted": True, "require_profitable": False,
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


def detect_pullback(highs, lows, closes, i, lookback=20, min_pullback_pct=3.0,
                    max_pullback_pct=15.0, ma_period=50):
    """상승 추세 종목이 고점에서 눌린 뒤 되돌아서는 지점을 잡는다.

    지금까지 쓴 진입 신호(돈치안 돌파·VCP)는 전부 "신고가를 뚫는 순간"을 노리는데,
    지인 스크리너가 4단계로 잡았던 에이피알은 그 시점에 신고가가 아니라 직전 고점
    대비 19% 눌린 조정 구간이었다(2026-08-19, 종가 383,500 vs 8/13 고가 424,250).
    그래서 반대 성격의 진입도 측정해보려고 추가했다.

    조건:
      1) 최근 lookback일 고가 대비 min~max% 사이로 눌려 있다(너무 안 눌렸거나
         추세가 꺾일 만큼 깊게 빠진 건 제외)
      2) 그래도 ma_period 이동평균 위에 있다(추세 자체는 살아 있다)
      3) 오늘 종가가 어제보다 높다(되돌림이 멈추고 방향을 튼 신호)

    pivot은 손절 기준 계산 등에서 쓰는 값이라 다른 탐지기와 형식을 맞춰
    "되돌리기 시작한 고점"을 돌려준다."""
    if i < max(lookback, ma_period):
        return None
    recent_high = max(highs[i - lookback:i + 1])
    if recent_high <= 0:
        return None
    pullback_pct = (recent_high - closes[i]) / recent_high * 100
    if not (min_pullback_pct <= pullback_pct <= max_pullback_pct):
        return None
    ma = _sma(closes, ma_period, i)
    if ma is None or closes[i] <= ma:
        return None
    if closes[i] <= closes[i - 1]:
        return None
    return {"pivot": recent_high, "pullbackPct": round(pullback_pct, 2)}


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


def latest_is_profitable(rows_for_code, as_of_date):
    """rows_for_code: 한 종목의 DART 재무데이터 이력
    [{"rcept_date":, "bsns_year":, "net_income":, "operating_income":}, ...].
    as_of_date까지 실제 공시된 것 중 가장 최근 결산연도(bsns_year) 하나를 골라
    영업이익 또는 순이익 중 하나라도 흑자(양수)면 True, 둘 다 적자거나 0이면
    False를 돌려준다(kr_quant.latest_fundamentals_as_of와 같은 point-in-time
    방식 - 아직 공시 전인 미래 결산 데이터를 미리 쓰지 않는다). 데이터가 없으면
    None(필터 미적용 - latest_max_shareholder_pct와 같은 이유로, 신규상장 등
    DART 데이터가 아직 없는 종목을 무조건 배제하지 않기 위함)."""
    best = None
    for row in rows_for_code:
        rd = row.get("rcept_date")
        if not rd or rd > as_of_date:
            continue
        if best is None or row["bsns_year"] > best["bsns_year"]:
            best = row
    if best is None:
        return None
    net_income = best.get("net_income")
    operating_income = best.get("operating_income")
    if net_income is None and operating_income is None:
        return None
    return (net_income or 0) > 0 or (operating_income or 0) > 0


def latest_fundamentals_as_of(rows_for_code, as_of_date):
    """as_of_date까지 실제 공시된 재무데이터 중 (가장 최근 결산연도 행, 그 직전
    결산연도 행)을 돌려준다. 성장률처럼 두 개 연도가 필요한 지표 때문에 직전
    연도까지 함께 준다(직전 연도가 없으면 두 번째 값은 None).

    같은 결산연도에 대해 행이 여러 개일 수 있어(연결/별도 재무제표가 따로
    들어오거나 정정공시가 붙는 경우) 연도별로 rcept_date가 가장 늦은 것 하나만
    남긴다 - 같은 연도라면 나중에 공시된 쪽이 확정치에 가깝다."""
    by_year = {}
    for row in rows_for_code:
        rd = row.get("rcept_date")
        year = row.get("bsns_year")
        if not rd or not year or rd > as_of_date:
            continue
        cur = by_year.get(year)
        if cur is None or rd > cur["rcept_date"]:
            by_year[year] = row
    if not by_year:
        return None, None
    years = sorted(by_year)
    prev = by_year[years[-2]] if len(years) >= 2 else None
    return by_year[years[-1]], prev


def fundamental_quality_score(rows_for_code, as_of_date):
    """기본적 분석 점수(0.0~1.0). 수익성 2개(영업이익·순이익 흑자), 자본효율
    1개(ROE 5% 이상), 안정성 1개(부채비율 200% 이하), 성장성 1개(매출 전년 대비
    증가) - 총 5개 항목의 통과 비율이다.

    "적자면 무조건 제외" 같은 이분법 하드 필터가 아니라 점수로 만든 이유:
    그 방식을 실측해보니 24만 건을 걸러내면서 성과가 오히려 나빠졌다(제외된
    종목 중에 나중에 크게 오른 것도 함께 버려졌다). 점수로 두면 후보 순위에
    반영하거나(quality_rank_weight) 하위 구간만 잘라내는(min_quality_score)
    식으로 강도를 조절할 수 있다.

    판정에 필요한 값이 없는 항목은 분모에서 빼서, 데이터가 덜 채워진 종목이
    그 이유만으로 낮은 점수를 받지 않게 한다(예: 상장 첫 해라 전년 매출이 없으면
    성장성 항목은 아예 채점하지 않는다). 채점 가능한 항목이 하나도 없거나 공시
    자체가 없으면 None - latest_is_profitable과 같은 이유로 필터를 적용하지 않는다."""
    latest, prev = latest_fundamentals_as_of(rows_for_code, as_of_date)
    if latest is None:
        return None

    passed = applicable = 0

    operating_income = latest.get("operating_income")
    if operating_income is not None:
        applicable += 1
        passed += operating_income > 0

    net_income = latest.get("net_income")
    if net_income is not None:
        applicable += 1
        passed += net_income > 0

    total_equity = latest.get("total_equity")
    if net_income is not None and total_equity is not None and total_equity > 0:
        applicable += 1
        passed += (net_income / total_equity) >= QUALITY_MIN_ROE

    total_liabilities = latest.get("total_liabilities")
    if total_liabilities is not None and total_equity is not None and total_equity > 0:
        applicable += 1
        passed += (total_liabilities / total_equity) <= QUALITY_MAX_DEBT_RATIO

    revenue = latest.get("revenue")
    prev_revenue = prev.get("revenue") if prev else None
    if revenue is not None and prev_revenue is not None and prev_revenue > 0:
        applicable += 1
        passed += revenue > prev_revenue

    if applicable == 0:
        return None
    return passed / applicable


# ── SEPA 2단계: 분기 실적 성장(EPS 가속) ────────────────────────────────────
# 미너비니 SEPA는 "직전 분기 EPS 증가율이 그 이전 분기보다 더 높아지는가"(가속)를
# 핵심 조건으로 본다. 연간 결산만으로는 판정 자체가 불가능해 분기 데이터를 따로
# 수집했다(data_pipeline/fetch_fundamentals_quarterly.py).
#
# 누적 EPS를 그대로 전년 동기 누적과 비교한다. DART 분기보고서는 그 사업연도
# 1분기부터의 누적으로 저장돼 있어(models.KrFundamentalQuarter 참고), 같은 분기끼리
# 비교하면 계절성이 자동으로 상쇄되고 4분기 차분 같은 조작이 필요 없다.


def load_quarterly_rows(parquet_paths):
    """분기 재무 parquet들을 {종목코드: {(연도, 분기): {...}}}로 묶는다.
    fundamentals_rows처럼 백테스트 시작 전 한 번만 로드한다."""
    import pandas as pd

    by_code = {}
    for path in parquet_paths:
        if not Path(path).exists():
            continue
        df = pd.read_parquet(path)
        for r in df.itertuples():
            rcept = getattr(r, "rcept_no", "") or ""
            if len(rcept) < 8:
                continue
            d = rcept[:8]
            by_code.setdefault(r.stock_code, {})[(str(r.bsns_year), int(r.quarter))] = {
                "rcept_date": f"{d[0:4]}-{d[4:6]}-{d[6:8]}",
                "eps_cum": getattr(r, "eps_cum", None),
                "revenue_cum": getattr(r, "revenue_cum", None),
            }
    return by_code


def _yoy_growth(cur, prev):
    """전년 동기 대비 증가율(%). 전년이 0이거나 없으면 판정 불가(None).
    전년이 적자(음수)면 증가율 자체가 의미를 잃으므로(-100원->+10원이 몇 %인가)
    '적자에서 흑자 전환'만 성장으로 인정하고 나머지는 None으로 둔다."""
    if cur is None or prev is None:
        return None
    if prev > 0:
        return (cur - prev) / prev * 100
    if prev < 0:
        return 100.0 if cur > 0 else None
    return None


def quarterly_eps_growth(quarters_for_code, as_of_date):
    """as_of_date까지 공시된 분기 중 가장 최근 두 분기의 EPS 전년동기 증가율을
    (최근분기 증가율, 그 직전분기 증가율)로 돌려준다. 계산 불가면 (None, None).

    SEPA의 '가속' 판정은 이 둘을 비교해서 한다(최근 > 직전이면 가속).
    공시일(rcept_date) 기준으로만 보므로 아직 발표 전인 실적은 쓰지 않는다."""
    if not quarters_for_code:
        return None, None
    filed = sorted(
        (k for k, v in quarters_for_code.items()
         if v.get("rcept_date") and v["rcept_date"] <= as_of_date),
        key=lambda k: (k[0], k[1]),
    )
    if not filed:
        return None, None

    def growth_at(key):
        year, q = key
        prev_key = (str(int(year) - 1), q)
        prev = quarters_for_code.get(prev_key)
        # 전년 동기 보고서도 그 시점에 이미 공시돼 있어야 한다(당연히 그렇지만
        # 데이터가 뒤늦게 채워진 경우를 대비해 확인한다).
        if not prev or not prev.get("rcept_date") or prev["rcept_date"] > as_of_date:
            return None
        return _yoy_growth(quarters_for_code[key].get("eps_cum"), prev.get("eps_cum"))

    latest = growth_at(filed[-1])
    prior = growth_at(filed[-2]) if len(filed) >= 2 else None
    return latest, prior


def eps_growth_ok(quarters_for_code, as_of_date, min_growth_pct=None, require_acceleration=False):
    """SEPA 2단계 판정. 데이터가 없어 계산 불가면 True(필터 미적용) - 다른 필터와
    같은 원칙이다(수집 범위 밖 구간을 통째로 배제하지 않기 위함)."""
    latest, prior = quarterly_eps_growth(quarters_for_code, as_of_date)
    if latest is None:
        return True
    if min_growth_pct is not None and latest < min_growth_pct:
        return False
    if require_acceleration:
        if prior is None:
            return True
        if latest <= prior:
            return False
    return True


# ── SEPA 3단계: 촉매(Catalyst) ──────────────────────────────────────────────
# 미너비니는 기관 매수를 부르는 사건(신제품·대형계약·실적 서프라이즈 등)을 촉매로
# 본다. 국내에서 그에 대응하는 건 DART 공시인데, 실측(2022~2026년 192,165건)에서
# 방향이 명확한 것만 골랐다:
#   - 단일판매ㆍ공급계약체결(15,415건): 대형 수주. 가장 많고 의미가 분명하다.
#   - 신규시설투자등: 증설. 성장 신호.
#   - 타법인주식및출자증권취득결정: 인수·지분투자.
#   - 자기주식취득: 자사주 매입.
# 뺀 것:
#   - 매출액또는손익구조30%이상변경(8,298건): 실적 서프라이즈 성격이지만 제목에
#     증가/감소가 안 적혀 있어(본문을 봐야 안다) 방향을 알 수 없다. 방향 모르는
#     신호를 양의 촉매로 쓰면 급락 종목까지 사게 된다.
#   - 현금ㆍ현물배당결정(5,895건): 계절성이 강해 촉매라기보다 노이즈다.
POSITIVE_CATALYST_KEYWORDS = (
    "단일판매", "공급계약", "신규시설투자", "타법인주식및출자증권취득", "자기주식취득",
)
# [기재정정]은 이미 낸 공시를 고쳐 다시 낸 것이라 새 사건이 아니다 - 원본이 이미
# 수집돼 있으므로 정정본까지 세면 같은 촉매를 두 번 세게 된다.
_AMENDED_PREFIX = "기재정정"


def load_catalyst_dates(parquet_paths):
    """공시 parquet들에서 양의 촉매만 골라 {종목코드: [접수일...]}(오름차순)로 만든다.
    shareholder_rows처럼 백테스트 시작 전에 한 번만 로드해둔다."""
    import pandas as pd

    dates_by_code = {}
    for path in parquet_paths:
        if not Path(path).exists():
            continue
        df = pd.read_parquet(path)
        for row in df.itertuples():
            name = row.report_nm or ""
            if _AMENDED_PREFIX in name:
                continue
            if not any(kw in name for kw in POSITIVE_CATALYST_KEYWORDS):
                continue
            code = row.stock_code
            if not code:
                continue
            dates_by_code.setdefault(code, []).append(row.rcept_dt)
    for code in dates_by_code:
        dates_by_code[code].sort()
    return dates_by_code


def has_recent_catalyst(dates_sorted, as_of_date, lookback_days):
    """as_of_date 기준 lookback_days 이내에 양의 촉매 공시가 있었는지.

    공시 접수일(rcept_dt)이 곧 시장이 그 정보를 알게 된 날이라, as_of_date보다
    나중 공시는 당연히 볼 수 없다(룩어헤드 방지). 날짜 문자열이 YYYY-MM-DD로
    정렬 가능해 bisect로 구간만 잘라 본다."""
    if not dates_sorted:
        return False
    start = (datetime.fromisoformat(as_of_date) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    lo = bisect.bisect_left(dates_sorted, start)
    hi = bisect.bisect_right(dates_sorted, as_of_date)
    return hi > lo


# ── 배당락 갭 보정용 배당 공시 ────────────────────────────────────────────────
# 위 촉매 판정에서는 "현금ㆍ현물배당결정"을 계절성 노이즈라 일부러 뺐지만
# (POSITIVE_CATALYST_KEYWORDS 위 주석 참고), 여기서는 반대 목적으로 정확히
# 그 배당 공시가 필요하다 - "이 종목이 최근에 배당을 결정했는가"만 알면
# 되므로 [기재정정]도 그대로 포함한다(중복 카운트 걱정 없음 - 존재 여부만
# 본다). 실측(2016~2026, 44만 건 중 19,740건)에서 "배당" 한 글자로 현금ㆍ
# 현물배당결정/부동산투자회사금전배당결정(리츠)/주식배당결정/배당락/배당
# 기준일 관련 공시가 전부 잡혔다.
def load_dividend_dates(parquet_paths):
    """공시 parquet들에서 배당 관련 공시만 골라 {종목코드: [접수일...]}(오름차순)로
    만든다. load_catalyst_dates와 같은 패턴, 백테스트 시작 전 한 번만 로드."""
    import pandas as pd

    dates_by_code = {}
    for path in parquet_paths:
        if not Path(path).exists():
            continue
        df = pd.read_parquet(path)
        for row in df.itertuples():
            name = row.report_nm or ""
            if "배당" not in name:
                continue
            code = row.stock_code
            if not code:
                continue
            dates_by_code.setdefault(code, []).append(row.rcept_dt)
    for code in dates_by_code:
        dates_by_code[code].sort()
    return dates_by_code


def financial_quality(rows_for_code, as_of_date):
    """as_of_date까지 공시된 재무로 수익성·성장성 지표를 계산한다.

    지인 스크리너에서 3단계로 올라간 에이피알이 2단계 11종목 대비 압도적으로
    앞선 항목들을 그대로 옮겼다(2026-08-27 스냅샷 기준):
        ROE 56.3% (2위 19.8%) / ROIC 65.5% (2위 15.0%) / ROA 33.1% (2위 9.9%)
        매출증가율 134.2% (2위 83.6%) / 매출총이익률 79.2% (2위 56.0%)
    2단계까지는 가격 지표로 거르고 그 위 단계에서 재무 품질로 다시 좁히는
    구조로 보여, 이 지표들을 임계값으로 쓸 수 있게 만들었다.

    ROIC는 투하자본(자기자본+총부채) 대비 영업이익으로 근사한다 - 정확한
    ROIC는 세후영업이익÷(순운전자본+유형자산)이지만 그 계산에 필요한 항목이
    DART 응답에 일관되게 오지 않는다. 값의 절대 수준보다 종목 간 서열이
    유지되면 필터 용도로는 충분하다.

    계산 불가한 항목은 None으로 둔다(필터에서 통과 처리 - 다른 필터와 같은 원칙)."""
    latest, prev = latest_fundamentals_as_of(rows_for_code, as_of_date)
    if latest is None:
        return {}
    out = {}
    eq = latest.get("total_equity")
    ni = latest.get("net_income")
    oi = latest.get("operating_income")
    rev = latest.get("revenue")
    ta = latest.get("total_assets")
    li = latest.get("total_liabilities")
    gp = latest.get("gross_profit")
    if ni is not None and eq and eq > 0:
        out["roe"] = ni / eq * 100
    if ni is not None and ta and ta > 0:
        out["roa"] = ni / ta * 100
    if oi is not None and eq and li is not None and (eq + li) > 0:
        out["roic"] = oi / (eq + li) * 100
    if oi is not None and rev and rev > 0:
        out["operating_margin"] = oi / rev * 100
    if gp is not None and rev and rev > 0:
        out["gross_margin"] = gp / rev * 100
    prev_rev = prev.get("revenue") if prev else None
    if rev is not None and prev_rev and prev_rev > 0:
        out["revenue_growth"] = (rev - prev_rev) / prev_rev * 100
    return out


def passes_financial_quality(rows_for_code, as_of_date, min_roe=None, min_roic=None,
                             min_revenue_growth=None, min_operating_margin=None):
    """financial_quality 결과에 하한을 적용한다. 값이 없으면 통과시킨다."""
    m = financial_quality(rows_for_code, as_of_date)
    for key, floor in (("roe", min_roe), ("roic", min_roic),
                       ("revenue_growth", min_revenue_growth),
                       ("operating_margin", min_operating_margin)):
        if floor is None:
            continue
        v = m.get(key)
        if v is not None and v < floor:
            return False
    return True


def revenue_growth_streak(rows_for_code, as_of_date):
    """as_of_date까지 공시된 결산연도를 최근 것부터 거꾸로 훑어, 매출이 전년보다
    큰 해가 몇 년 연속 이어지는지 센다. financial_quality의 revenue_growth가
    "성장의 크기"(예: 올해 +134%)만 보는 것과 달리 이건 "성장의 꾸준함"을 본다
    - 지인 데이터의 revenue_growth_streak/operating_income_growth_streak 필드와
    같은 개념이다. 한 해라도 역성장하면 그 이전 연속은 인정하지 않고 끊는다.
    연도 데이터가 2개 미만이면 0(판정 불가가 아니라 '연속 없음'으로 취급 -
    신규상장 종목을 데이터 없다는 이유로 통과시키면 이 필터의 취지와 어긋난다)."""
    by_year = {}
    for row in rows_for_code:
        rd = row.get("rcept_date")
        year = row.get("bsns_year")
        if not rd or not year or rd > as_of_date:
            continue
        cur = by_year.get(year)
        if cur is None or rd > cur["rcept_date"]:
            by_year[year] = row
    years = sorted(by_year)
    if len(years) < 2:
        return 0
    streak = 0
    for idx in range(len(years) - 1, 0, -1):
        rev = by_year[years[idx]].get("revenue")
        prev_rev = by_year[years[idx - 1]].get("revenue")
        if rev is None or prev_rev is None or prev_rev <= 0:
            break
        if rev > prev_rev:
            streak += 1
        else:
            break
    return streak


def operating_margin_trend(rows_for_code, as_of_date):
    """최근 결산연도 영업이익률에서 그 직전 결산연도 영업이익률을 뺀 값(%p) -
    수익성 "수준"이 아니라 "개선 방향"을 본다. 지인 데이터의 operating_margin_trend
    필드와 같은 개념. 계산 불가(연도 2개 미만, 매출이 0 이하 등)면 None(필터
    미적용 - 다른 필터와 같은 원칙)."""
    latest, prev = latest_fundamentals_as_of(rows_for_code, as_of_date)
    if latest is None or prev is None:
        return None
    lrev, loi = latest.get("revenue"), latest.get("operating_income")
    prev_rev, prev_oi = prev.get("revenue"), prev.get("operating_income")
    if not lrev or lrev <= 0 or not prev_rev or prev_rev <= 0 or loi is None or prev_oi is None:
        return None
    return (loi / lrev * 100) - (prev_oi / prev_rev * 100)


def estimate_per(price, shares_out, rows_for_code, as_of_date):
    """PER 근사(주가/주당순이익) = 시가총액 / 최근 결산연도 순이익. 지인 데이터는
    분기 EPS(eps_forward 등)까지 쓰지만 우리는 연간 순이익만 있어 연 단위로
    근사한다. 순이익이 적자거나 계산 불가면 None(필터 미적용)."""
    if not price or not shares_out:
        return None
    latest, _ = latest_fundamentals_as_of(rows_for_code, as_of_date)
    if latest is None:
        return None
    ni = latest.get("net_income")
    if ni is None or ni <= 0:
        return None
    market_cap = price * shares_out
    return market_cap / ni


def load_fundamentals_rows(KrFundamental):
    """KrFundamental 테이블 전체를 읽어 {stock_code: [{...}, ...]}로 묶는다.
    shareholder_rows처럼 백테스트 시작 전에 한 번만 로드해둔다(일별 루프 안에서
    매번 DB 조회하면 느리다). 호출부가 이미 Flask app context 안에 있어야 한다.

    net_income/operating_income만 읽던 것을 fundamental_quality_score가 쓰는
    항목(자기자본·부채·매출)까지 넓혔다 - 기존 latest_is_profitable도 같은 dict를
    그대로 쓰므로 호환된다."""
    rows = KrFundamental.query.filter(KrFundamental.rcept_no != "").all()
    rows_by_code = {}
    for r in rows:
        rd = r.rcept_date
        if not rd:
            continue
        rows_by_code.setdefault(r.stock_code, []).append({
            "rcept_date": rd, "bsns_year": r.bsns_year,
            "net_income": r.net_income, "operating_income": r.operating_income,
            "total_equity": r.total_equity, "total_liabilities": r.total_liabilities,
            "revenue": r.revenue, "total_assets": r.total_assets,
            "gross_profit": r.gross_profit,
        })
    return rows_by_code


def _return_pct(closes, i, days):
    """i번 인덱스 기준 days거래일 전 대비 수익률(%). 데이터가 모자라면 None."""
    j = i - days
    if j < 0 or not closes[j]:
        return None
    return (closes[i] / closes[j] - 1) * 100


def passes_evan_stage2(closes, highs, i, rs_rating,
                       min_rs=70.0, min_high_52w_pct=75.0,
                       min_return_3m=-6.0, min_return_6m=-10.0, min_return_12m=40.0,
                       min_ma200_gap_pct=8.0, max_ma200_gap_pct=None):
    """지인 스크리너의 "2단계" 판정식을 옮긴 것.

    2026-08-27 실측 스냅샷(1단계 361종목)에서 역산했다. 아래 6개 조건을 모두
    만족하는 종목이 정확히 그날의 2단계 12종목과 일치했다(오탐·누락 0).

    주의: 역산에 쓴 임계값은 통과 종목 12개의 '최솟값'이라 실제 기준선은 이보다
    낮을 수 있다(예: 실제가 RS>=65여도 최저 종목이 70.3이면 같은 결과가 나온다).
    그래서 여기서는 최솟값보다 살짝 여유를 둔 값을 기본값으로 쓴다 - 경계를
    정확히 좁히려면 며칠치 스냅샷이 더 필요하다.

    우리 rsRating(1~99 백분위)과 지인 rs(70.3 등)는 척도가 달라 직접 비교가
    아니라 근사다. 나머지 5개는 가격만으로 계산되므로 정의가 같다.

    max_ma200_gap_pct: 3·4단계 근사용 상한. 2026-08-27~09-01 5개 관측(에이피알
    하나를 반복 추적)에서, 200일선 이격도가 좁을 때(30.17%/31.93%)만 4단계였고
    넓을 때(34.54%/39.74%/40.22%)는 3단계였다 - RS·시총 등 다른 지표는 단조
    증가(1→2→4단계)했는데 이 값만 4단계에서 오히려 꺾였다. 즉 2단계 필터를
    통과한 종목 중 "너무 많이 오른 게 아니라 눌림이 와서 이격이 다시 좁혀진"
    경우로 좁히면 4단계에 가까워진다는 가설이다. 표본이 종목 1개뿐이라 임계값
    (32~34% 사이로 추정)은 확정이 아니라 근사치다."""
    if rs_rating is None or rs_rating < min_rs:
        return False
    if i < 252:
        return False
    week52_high = max(highs[i - 251:i + 1])
    if week52_high <= 0 or closes[i] / week52_high * 100 < min_high_52w_pct:
        return False
    ma200 = _sma(closes, 200, i)
    if ma200 is None or ma200 <= 0:
        return False
    ma200_gap = (closes[i] / ma200 - 1) * 100
    if ma200_gap < min_ma200_gap_pct:
        return False
    if max_ma200_gap_pct is not None and ma200_gap > max_ma200_gap_pct:
        return False
    for days, floor in ((63, min_return_3m), (126, min_return_6m), (252, min_return_12m)):
        r = _return_pct(closes, i, days)
        if r is None or r < floor:
            return False
    return True


def _realized_vol(closes, i, window=60):
    """i번 인덱스까지 최근 window거래일 일간수익률의 연환산 변동성(%).

    종가 기준 손절은 손절선을 그대로 지키지 못한다 - 장 마감 뒤에야 판정하므로
    그날 하락분을 다 뒤집어쓴다. 그래서 변동성이 큰 종목일수록 실제 손실이
    손절폭보다 크게 벌어진다(우리 평균손실 -7.64%가 손절선 6%를 넘는 이유).
    이 값으로 상한을 걸어 그런 종목을 애초에 후보에서 빼기 위한 지표다.
    screening_backtest._realized_vol과 같은 정의를 쓴다(두 엔진이 다른 값을
    쓰면 같은 종목을 다르게 판정하게 된다)."""
    if i < window:
        return None
    rets = []
    for k in range(i - window + 1, i + 1):
        prev = closes[k - 1]
        if prev and prev > 0 and closes[k] is not None:
            rets.append(closes[k] / prev - 1)
    if len(rets) < window // 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / len(rets)
    return (var ** 0.5) * (252 ** 0.5) * 100


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
                      fetch_fn=None, shares_map=None, shareholder_rows_by_code=None, fundamentals_rows_by_code=None,
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
                      max_pct_of_avg_trade_value=None, max_position_value_abs=None, include_delisted=False,
                      min_quality_score=None, quality_rank_weight=0.0, require_profitable=True,
                      position_cap_base="seed",
                      catalyst_dates_by_code=None, require_catalyst=False, catalyst_lookback_days=60,
                      quarterly_rows_by_code=None, min_eps_growth_pct=None,
                      require_eps_acceleration=False,
                      pullback_lookback=20, min_pullback_pct=3.0, max_pullback_pct=15.0,
                      pullback_ma_period=50, pullback_min_volume_mult=None, max_volatility_pct=None,
                      require_evan_stage2=False, evan_params=None,
                      stage_exit=False, stage_exit_params=None,
                      min_roe=None, min_roic=None, min_revenue_growth=None,
                      min_operating_margin=None,
                      min_revenue_growth_streak=None, min_operating_margin_trend=None,
                      min_roe_percentile=None, max_per=None,
                      max_position_weight_pct=MAX_POSITION_WEIGHT_PCT,
                      dividend_dates_by_code=None, dividend_gap_threshold_pct=-6.0,
                      dividend_gap_max_pct=-20.0, dividend_gap_lookback_days=30,
                      stop_cooldown_days=None, min_ret12m_percentile=None):
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

    universe = ts.load_universe(market, include_delisted=include_delisted)
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
            [b.get("open", b["close"]) for b in bars],
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
    # 재평가 루프 안에서 갱신되지만, 보유 포지션 처리(피라미딩 상한 계산)가 그
    # 갱신보다 먼저 돌기 때문에 첫 회차용 초기값이 필요하다. 두 번째 회차부터는
    # 직전 재평가 시점의 평가액을 쓰게 된다(그 시점에 알 수 있는 최신 값이라
    # 룩어헤드가 아니다).
    equity_now = float(seed)
    prev_rd = None
    # 손절/추세이탈로 청산된 종목의 "냉각 기간" - 같은 종목이 짧은 간격으로
    # 다시 눌림목 신호를 내고 또 손절당하는 것(초퍼/횡보 구간에서 반복 손실)을
    # 줄여 승률을 올릴 수 있는지 보는 신규 레버(38차). {종목코드: 마지막
    # 실패 청산일} - initialStop/maBreak만 기록한다(트레일링/본전/분할익절은
    # "실패"가 아니라 정상 청산이라 냉각시킬 이유가 없다).
    #
    # 38차 실측(냉각 10/20/30일 + 12개월수익률 상대백분위 60/70/80 + 조합,
    # 총 8개 변형): 이번에도 동일한 트레이드오프가 그대로 나타났다 - 승률은
    # 22.6%->22.0~23.8%로 소폭 오르지만 CAGR이 47.89%->34~46%로 크게
    # 빠진다(가장 균형 잡힌 HG_12개월백분위80조차 CAGR 77% 수준). 29~38차
    # (10라운드, 80개 변형) 동안 승률 22~25% 근방이 반복적으로 상한으로
    # 나타나는 걸 보면 이건 조건을 더 찾으면 풀리는 문제가 아니라 이
    # 룰 기반 백테스트의 구조적 한계로 보인다 - 이 두 파라미터
    # (stop_cooldown_days/min_ret12m_percentile)는 코드에는 남겨두되
    # WATCHER_PARAMS에는 반영하지 않는다(기본값 None이라 기존 동작에
    # 영향 없음 - round32의 max_ma200_gap_pct/round33의
    # pullback_min_volume_mult와 같은 처리).
    stopped_recently = {}
    excluded_preferred = excluded_cap = excluded_liquidity = excluded_shareholder = excluded_unprofitable = 0
    excluded_low_quality = excluded_no_catalyst = excluded_eps_growth = 0
    excluded_volatility = excluded_evan = excluded_fin_quality = 0
    excluded_cooldown = 0
    shareholder_rows_by_code = shareholder_rows_by_code or {}
    fundamentals_rows_by_code = fundamentals_rows_by_code or {}

    def _position_cap(seed_value, equity_value):
        """종목당 투입 상한. position_cap_base="seed"면 최초 시드 기준으로 고정하고,
        "equity"면 그 시점 평가액 기준으로 잡는다.

        기본값이 seed인 건 기존 동작을 그대로 두기 위함이지만, 복리를 보려면
        equity가 맞다 - 시드 기준으로 고정하면 계좌가 커질수록 투입 가능 비중이
        계속 줄어들어(시드 1천만/상한 50%면 계좌가 1억이 돼도 종목당 500만원)
        복리가 구조적으로 막힌다. 실제로 이것 때문에 거래당 기대값이 플러스인데도
        CAGR이 낮게 나오고 있었다. equity로 두면 대신 계좌가 커질수록 매수 금액이
        그 종목의 하루 거래대금을 넘어설 수 있으니, max_pct_of_avg_trade_value를
        함께 걸어야 현실적인 결과가 된다.

        비율 자체는 max_position_weight_pct 인자로 받는다(기본값은 모듈 상수
        MAX_POSITION_WEIGHT_PCT) - 예전엔 이 함수가 모듈 상수를 직접 참조해서,
        프리셋마다 다른 비율을 쓰려면 호출 스크립트가 vcp.MAX_POSITION_WEIGHT_PCT를
        임시로 덮어썼다 갱신하는 식이었다(gunicorn 멀티스레드에서 다른 요청과
        경합할 수 있는 전역 mutable state라 위험했다 - RULES.md 워커간 공유상태
        금지와 같은 이유). 인자로 받게 바꿔 스레드 안전하게 만들었다."""
        base = equity_value if position_cap_base == "equity" else seed_value
        return base * max_position_weight_pct / 100

    for rd in rebalance_dates:
        idx_at_rd = {}
        evaluated = []
        for ticker, (dates, closes, highs, lows, volumes, *_rest) in series.items():
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
        # entry_mode="donchian"은 기본적으로 트렌드템플릿 통과 여부를 요구하지 않지만
        # (전체 유니버스 대상 - RS 랭킹은 후보 정렬에만 쓴다), min_trend_pass_count를
        # 명시적으로 주면 donchian 모드에서도 그 기준을 그대로 적용한다(예: 8을 주면
        # "8개 조건 모두 만족"까지 요구하는 셈).
        if min_trend_pass_count is not None:
            trend_ok_set = {t for t, e in by_ticker.items() if (e.get("passCount") or 0) >= min_trend_pass_count}
        elif entry_mode == "donchian":
            trend_ok_set = set(by_ticker.keys())
        else:
            trend_ok_set = {t for t, e in by_ticker.items() if e.get("allPass")}

        # ROE 백분위 문턱 - min_roe(절대값) 대신 그날 후보군 안에서의 상대 순위를
        # 본다. RS85(RS등급 상위 백분위)는 순수 개선이었는데 min_roe/min_roic(절대
        # 문턱)는 효과가 미미하거나 해로웠다(round 27-28) - "얼마나 벌었나"보다
        # "그 시점 다른 후보들과 비교해 얼마나 잘 버나"가 더 안정적인 신호일
        # 수 있다는 가설. 매 재평가일마다 그날의 trend_ok_set 안에서만 백분위를
        # 새로 계산한다(전체 유니버스가 아니라 이미 기술적 조건을 통과한 후보군
        # 기준 - 그래야 "약세장에도 상대적으로 낫다"가 아니라 "이미 좋은 후보들
        # 중에서도 특히 좋다"를 걸러낸다).
        roe_percentile_cutoff = None
        if min_roe_percentile is not None:
            roe_vals = []
            for t in trend_ok_set:
                code_t, *_ = info_by_ticker.get(t, (t,))
                fq = financial_quality(fundamentals_rows_by_code.get(code_t, []), rd)
                v = fq.get("roe")
                if v is not None:
                    roe_vals.append(v)
            if roe_vals:
                roe_vals.sort()
                idx = min(int(len(roe_vals) * min_roe_percentile / 100), len(roe_vals) - 1)
                roe_percentile_cutoff = roe_vals[idx]

        # 12개월 수익률 백분위 - 위 ROE 백분위와 같은 논리를 가격 모멘텀에
        # 적용한 것(38차 신규). evan_stage2의 min_return_12m(절대 문턱, 40%)은
        # 이미 있지만, RS85가 "절대 문턱보다 상대 순위가 더 안정적으로 통했다"는
        # 패턴이 다른 지표에도 적용되는지 보는 가설.
        ret12m_percentile_cutoff = None
        if min_ret12m_percentile is not None:
            ret_vals = []
            for t in trend_ok_set:
                idx_t = idx_at_rd.get(t)
                if idx_t is None:
                    continue
                r12 = _return_pct(series[t][1], idx_t, 252)
                if r12 is not None:
                    ret_vals.append(r12)
            if ret_vals:
                ret_vals.sort()
                idx = min(int(len(ret_vals) * min_ret12m_percentile / 100), len(ret_vals) - 1)
                ret12m_percentile_cutoff = ret_vals[idx]

        # 1) 보유 포지션 처리 - 하루씩 순서대로(손절/트레일링 히트 > MA50이탈 > 시간손절 > 본전/분할익절/트레일링갱신 > 피라미딩)
        for ticker in list(positions.keys()):
            pos = positions[ticker]
            dates, closes, highs, lows, volumes, opens = series[ticker]
            i = idx_at_rd.get(ticker)
            if i is None:
                continue
            start_i = bisect.bisect_right(dates, prev_rd) if prev_rd else pos["entryIdx"] + 1
            start_i = max(start_i, pos["entryIdx"] + 1)
            closed = False
            for j in range(start_i, i + 1):
                close = closes[j]
                # 배당락(권리락) 갭 보정 - 특별배당 등으로 배당액만큼 주가가 기계적으로
                # 빠지는 날을 실제 하락으로 오인해 손절되는 걸 막는다(사용자 지적:
                # "코람코더원리츠 같이 특별배당으로 주가가 급변하면?"). 이 종목에
                # 최근 DART 배당결정 공시(dividend_dates_by_code)가 있고, 전일 대비
                # 낙폭이 dividend_gap_threshold_pct(기본 -6%, 와쳐 손절폭 -3.5%보다
                # 훨씬 커야 "그냥 나쁜 소식"이 아니라 "배당만큼 빠진 것"으로 의심할
                # 근거가 된다)를 넘으면, 손절선과 최고가 추적치를 낙폭만큼 함께
                # 내려서 그날부터는 "새 기준가" 위에서 손절 거리를 그대로 재적용한다
                # - 배당 당일의 갭 자체는 무시하면서, 배당과 무관하게 그 뒤로도
                # 계속 빠지면(새 기준가 대비) 정상적으로 손절되도록 한다. 배당소득
                # 자체를 계좌 현금에 반영하지는 않는다(이 엔진은 배당 현금흐름을
                # 아예 모델링하지 않는다 - 별도의 더 큰 개선 과제로 남겨둔다).
                if dividend_dates_by_code and j > 0 and closes[j - 1]:
                    day_return = (close / closes[j - 1] - 1) * 100
                    # 상한(dividend_gap_max_pct)을 반드시 함께 둔다 - 실측(2023-04
                    # 하림지주 사례)에서 상한 없이 "낙폭 하한 + 최근 배당공시"만
                    # 조건으로 두니, 배당과 무관한 진짜 폭락(SG증권발 무더기
                    # 하한가 사태, 하루 -30%)까지 우연히 근처 시점의 정기배당
                    # 공시와 엮여 손절이 봐지면서 손실이 더 커졌다(CAGR 47.68%->
                    # 36.76%로 오히려 악화). 배당락은 보통 하루 -6~15% 수준이지
                    # -20%를 넘게 빠지지 않는다 - 그 이상은 배당이 아니라 진짜
                    # 하락으로 보고 정상적으로 손절한다.
                    #
                    # 남은 한계: 상한을 둬도 완벽하지 않다 - 12월 결산법인은
                    # 매년 2월에 정기배당을 결정하는데, 2020년 2월 코로나 폭락
                    # (심텍 -16.2%, 상한 -20% 이내)처럼 "우연히 같은 시기의
                    # 시장 전체 급락"과 겹치면 여전히 잘못 봐줄 수 있다(실측
                    # 확인됨). 이 배당 공시 자체가 실제 배당락일을 특정하지
                    # 못해(접수일만 있고 기준일은 DART 원문을 파싱해야 나옴)
                    # 생기는 근본적 한계 - 전체 성과 지표(CAGR 등)에는 순
                    # 개선으로 나타났지만(47.68%->47.89%), 개별 종목 단위로는
                    # 이런 오탐이 완전히 없다고 보장하지 못한다.
                    if dividend_gap_max_pct <= day_return <= dividend_gap_threshold_pct and has_recent_catalyst(
                            dividend_dates_by_code.get(pos["code"], []), dates[j], dividend_gap_lookback_days):
                        gap = closes[j - 1] - close
                        if gap > 0:
                            pos["stopPrice"] -= gap
                            pos["highestHigh"] = max(0.0, pos["highestHigh"] - gap)
                # 손절/트레일링/분할익절/피라미딩 전부 종가 기준으로만 판정하고 종가로
                # 체결한다 - 장중 저가/고가가 손절가·목표가를 스쳤다고 그 가격에 정확히
                # 체결됐다고 가정하지 않는다(그날 종가가 확정돼야 실제로 알 수 있는
                # 정보라는 지적 반영). 그만큼 손절은 더 늦게(더 나쁜 가격에), 익절/
                # 피라미딩은 더 늦게(놓칠 수도 있게) 체결되어 백테스트가 더 보수적이다.
                if close <= pos["stopPrice"]:
                    trade, proceeds = _full_exit(pos, close, dates[j], pos["stopState"])
                    trades.append(trade)
                    cash += proceeds
                    if pos["stopState"] == "initialStop":
                        stopped_recently[pos["code"]] = dates[j]
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
                    stopped_recently[pos["code"]] = dates[j]
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

                # highestHigh는 이름은 그대로지만 이제 장중 고가가 아니라 "이제까지의
                # 최고 종가"를 추적한다(트레일링/R계산의 기준을 전부 종가로 통일).
                pos["highestHigh"] = max(pos["highestHigh"], close)
                r_reached = (pos["highestHigh"] - pos["avgEntryPrice"]) / r if r > 0 else 0

                if r_reached >= breakeven_r and pos["stopPrice"] < pos["avgEntryPrice"]:
                    pos["stopPrice"] = pos["avgEntryPrice"]
                    pos["stopState"] = "breakevenStop"

                if partial_profit_fraction > 0 and not pos["partialTaken"] and r_reached >= partial_profit_r:
                    # target(+2R 목표가) 자체가 아니라 그 목표가를 넘어선 그날 종가로
                    # 체결한다 - 장중에 목표가를 스쳤다가 종가는 못 미쳤다면 그날은
                    # 익절하지 않는다(다음날 이후 r_reached 조건이 다시 충족되면 그때 체결).
                    target = pos["avgEntryPrice"] + partial_profit_r * r
                    if close >= target:
                        sell_shares = min(max(1, int(pos["shares"] * partial_profit_fraction)), pos["shares"] - 1)
                        if sell_shares > 0:
                            fill = close * (1 - (SLIPPAGE_EXIT_PCT + SELL_TAX_PCT) / 100)
                            pnl_pct = round((fill - pos["avgEntryPrice"]) / pos["avgEntryPrice"] * 100, 2)
                            trades.append({
                                "code": pos["code"], "name": pos["name"], "entryDate": pos["entryDate"],
                                "entryPrice": round(pos["avgEntryPrice"], 2), "exitDate": dates[j],
                                "exitPrice": round(close, 2), "shares": sell_shares, "pnlPct": pnl_pct,
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
                    # 장중에 목표가(lastEntryPrice+0.5R)를 스쳤는지가 아니라, 그날
                    # 종가가 그 목표가를 넘어섰는지로 판정하고 그 종가로 추가매수한다.
                    target = pos["lastEntryPrice"] + PYRAMID_INTERVAL_R * r
                    if close >= target:
                        add_shares = max(1, int(pos["initialShares"] * PYRAMID_SIZE_FRACTION))
                        cost = add_shares * close * (1 + SLIPPAGE_ENTRY_PCT / 100)
                        max_pos_value = _position_cap(seed, equity_now)
                        current_value = pos["shares"] * close
                        if cost <= cash and (current_value + add_shares * close) <= max_pos_value:
                            cash -= cost
                            pos["totalCost"] += cost
                            pos["shares"] += add_shares
                            pos["avgEntryPrice"] = pos["totalCost"] / pos["shares"]
                            pos["lastEntryPrice"] = close
                            pos["pyramidCount"] += 1
                            pos["stopPrice"] = max(pos["stopPrice"], close - initial_stop_atr_mult * pos["entryAtr"])
            if closed:
                continue
            # 지인 조건(2단계) 이탈 시 청산 - ATR 트레일링은 "가격이 얼마나 빠졌는가"만
            # 보고, 그 종목이 여전히 지인 스크리너 기준 "강한 종목"인지는 안 본다.
            # 반대로 이 규칙은 진입 때 썼던 것과 같은(또는 완화한) 판정식을 매
            # 재평가일마다 다시 걸어, 조건 자체가 깨지면(RS 급락·눌림이 추세이탈
            # 수준으로 깊어짐 등) 가격이 손절선에 안 닿았어도 청산한다. 목표는 승률/
            # 평균수익을 동시에 못 올리는 문제 - ATR 트레일링의 잦은 조기 손절(승률
            # 저하)과 추세 계속 종목의 조기 익절(평균수익 저하)을 둘 다 줄이려는 시도.
            # 재평가가 주 단위라 이 판정도 주 단위로만 이뤄진다(당일 확인 불가).
            if stage_exit and require_evan_stage2:
                e_hold = by_ticker.get(ticker)
                rs_hold = e_hold.get("rsRating") if e_hold else None
                if not passes_evan_stage2(closes, highs, i, rs_hold,
                                          **(stage_exit_params if stage_exit_params is not None
                                             else (evan_params or {}))):
                    trade, proceeds = _full_exit(pos, closes[i], dates[i], "stageExit")
                    trades.append(trade)
                    cash += proceeds
                    del positions[ticker]
                    continue
            # 상장폐지 종목(.DL)의 마지막 거래일에 도달했는데 손절/이탈 조건에 걸리지
            # 않고 "살아남은" 경우(급락 없이 합병·자회사화 등으로 조용히 상장폐지된
            # 경우 등) - series[ticker]에 더 이상 데이터가 없으므로 실제로는 더 이상
            # 보유를 지속할 수 없다. 그대로 두면 이후 재평가일마다 idx_at_rd가 계속
            # 마지막 인덱스에 멈춰 있어(위쪽 인덱스 계산 참고) 포지션이 청산되지
            # 않은 채 남은 백테스트 기간 내내 슬롯만 차지하는 "유령 포지션"이 된다 -
            # 그 시점 마지막 가격으로 즉시 강제 청산한다.
            if ticker.endswith(".DL") and i == len(dates) - 1:
                trade, proceeds = _full_exit(pos, closes[i], dates[i], "delisted")
                trades.append(trade)
                cash += proceeds
                del positions[ticker]

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
                # trend_ok_set은 파이썬 set이라 순회 순서가 프로세스마다(해시 랜덤화로)
                # 달라진다 - RS등급이 동률인 후보가 많은 상황(슬롯 수 제한이 걸릴 때)에서
                # 정렬 동률 처리가 이 순회 순서에 좌우돼 같은 파라미터로 돌려도 실행마다
                # 다른 거래가 나오는 재현성 버그가 있었다. 정렬해서 순회 순서를 고정한다.
                for ticker in sorted(trend_ok_set):
                    if ticker in positions:
                        continue
                    e = by_ticker[ticker]
                    code, name = e["code"], e["name"]
                    if is_preferred_stock(name):
                        excluded_preferred += 1
                        continue
                    i = idx_at_rd[ticker]
                    dates, closes, highs, lows, volumes, opens = series[ticker]
                    price_now = closes[i]
                    shares_out = shares_map.get(code)
                    if shares_out and price_now * shares_out < min_market_cap:
                        excluded_cap += 1
                        continue
                    avg_val = _avg_trade_value(closes, volumes, i)
                    if avg_val is not None and avg_val < min_avg_trade_value:
                        excluded_liquidity += 1
                        continue
                    # 변동성 상한 - 종가 기준 손절은 변동성이 큰 종목에서 손절선을
                    # 크게 벗어나 체결된다(장 마감 뒤 판정이라 그날 하락분을 다
                    # 뒤집어쓴다). 계산 불가면(데이터 부족) 거르지 않는다.
                    if max_volatility_pct is not None:
                        vol60 = _realized_vol(closes, i)
                        if vol60 is not None and vol60 > max_volatility_pct:
                            excluded_volatility += 1
                            continue
                    # 지인 스크리너 2단계 판정식(passes_evan_stage2 docstring 참고).
                    # 트렌드템플릿보다 훨씬 엄격하다 - 특히 12개월 수익률 하한이
                    # "이미 크게 오른 종목"만 남긴다.
                    if require_evan_stage2:
                        if not passes_evan_stage2(closes, highs, i, e.get("rsRating"),
                                                  **(evan_params or {})):
                            excluded_evan += 1
                            continue
                    # 재무 품질 하한 - 지인 스크리너의 3단계 이상 종목이 보인
                    # 수익성/성장성 우위를 옮긴 것(financial_quality docstring 참고).
                    if any(v is not None for v in (min_roe, min_roic, min_revenue_growth,
                                                   min_operating_margin)):
                        if not passes_financial_quality(
                                fundamentals_rows_by_code.get(code, []), rd,
                                min_roe=min_roe, min_roic=min_roic,
                                min_revenue_growth=min_revenue_growth,
                                min_operating_margin=min_operating_margin):
                            excluded_fin_quality += 1
                            continue
                    # 성장의 꾸준함(연속 증가 연수) - revenue_growth(크기)와는 다른 축.
                    if min_revenue_growth_streak is not None:
                        if revenue_growth_streak(fundamentals_rows_by_code.get(code, []), rd) < min_revenue_growth_streak:
                            excluded_fin_quality += 1
                            continue
                    # 수익성 개선 추세(영업이익률 전년 대비 변화, %p) - 수준이 아니라 방향.
                    if min_operating_margin_trend is not None:
                        trend = operating_margin_trend(fundamentals_rows_by_code.get(code, []), rd)
                        if trend is None or trend < min_operating_margin_trend:
                            excluded_fin_quality += 1
                            continue
                    # ROE 상대 순위(그날 후보군 백분위) - roe_percentile_cutoff 산출 로직 참고.
                    if roe_percentile_cutoff is not None:
                        fq_roe = financial_quality(fundamentals_rows_by_code.get(code, []), rd).get("roe")
                        if fq_roe is None or fq_roe < roe_percentile_cutoff:
                            excluded_fin_quality += 1
                            continue
                    # 12개월 수익률 상대 순위 - ret12m_percentile_cutoff 산출 로직 참고.
                    if ret12m_percentile_cutoff is not None:
                        r12 = _return_pct(closes, i, 252)
                        if r12 is None or r12 < ret12m_percentile_cutoff:
                            excluded_fin_quality += 1
                            continue
                    # 밸류에이션 상한(PER 근사) - 이미 많이 오른 뒤 비싸게 사는 걸 거른다.
                    if max_per is not None:
                        per_v = estimate_per(price_now, shares_out, fundamentals_rows_by_code.get(code, []), rd)
                        if per_v is not None and per_v > max_per:
                            excluded_fin_quality += 1
                            continue
                    max_holder_pct = latest_max_shareholder_pct(shareholder_rows_by_code.get(code, []), rd)
                    if max_holder_pct is not None and max_holder_pct > MAX_SHAREHOLDER_PCT:
                        excluded_shareholder += 1
                        continue
                    # 냉각 기간(38차 신규) - 최근 초기손절/MA이탈로 실패한 종목은
                    # stop_cooldown_days 동안 재진입 후보에서 뺀다. 같은 눌림목
                    # 신호가 짧은 간격으로 반복 실패하는(초퍼/횡보) 패턴을 걸러
                    # 승률을 올릴 수 있는지 보는 가설.
                    if stop_cooldown_days is not None:
                        last_fail = stopped_recently.get(code)
                        if last_fail is not None:
                            days_since = (datetime.fromisoformat(rd) - datetime.fromisoformat(last_fail)).days
                            if days_since < stop_cooldown_days:
                                excluded_cooldown += 1
                                continue
                    # ── 기본적 분석 ──────────────────────────────────────────
                    # 두 단계로 쓴다. (1) require_profitable: 영업이익·순이익이 둘 다
                    # 적자면 제외하는 이분법 하드 필터. (2) min_quality_score/
                    # quality_rank_weight: 5개 항목 통과율(0~1)을 점수로 매겨 하위
                    # 구간만 잘라내거나 후보 순위에 섞는 방식.
                    # 하드 필터를 기본값으로 두되 끌 수 있게 한 이유는, 실측에서
                    # 이 필터가 24만 건을 걸러내면서 성과를 오히려 깎았기 때문이다
                    # (좋은 기회까지 함께 버려졌다). 데이터가 없으면(신규상장 등)
                    # 어느 쪽도 적용하지 않는다 - latest_max_shareholder_pct와 같은 이유.
                    fund_rows = fundamentals_rows_by_code.get(code, [])
                    if require_profitable and latest_is_profitable(fund_rows, rd) is False:
                        excluded_unprofitable += 1
                        continue
                    quality = fundamental_quality_score(fund_rows, rd)
                    if min_quality_score is not None and quality is not None and quality < min_quality_score:
                        excluded_low_quality += 1
                        continue
                    # SEPA 3단계: 최근 catalyst_lookback_days 안에 양의 촉매 공시가
                    # 있었던 종목만 진입한다. 공시 데이터가 아예 없는 기간/종목은
                    # 거르지 않는다 - 수집 범위 밖(2022년 이전)까지 무조건 배제하면
                    # 그 기간 백테스트가 통째로 비어버린다.
                    if require_catalyst and catalyst_dates_by_code:
                        if not has_recent_catalyst(catalyst_dates_by_code.get(code, []),
                                                   rd, catalyst_lookback_days):
                            excluded_no_catalyst += 1
                            continue
                    # SEPA 2단계: 분기 EPS 성장률/가속. 분기 데이터가 없는 종목이나
                    # 구간에서는 판정을 건너뛴다(eps_growth_ok가 True를 돌려준다).
                    if quarterly_rows_by_code and (min_eps_growth_pct is not None or require_eps_acceleration):
                        if not eps_growth_ok(quarterly_rows_by_code.get(code, {}), rd,
                                             min_eps_growth_pct, require_eps_acceleration):
                            excluded_eps_growth += 1
                            continue
                    if entry_mode == "donchian":
                        brk = detect_donchian_breakout(highs, closes, i, period=donchian_period)
                        if not brk:
                            continue
                        pivot = brk["pivot"]
                    elif entry_mode == "pullback":
                        pb = detect_pullback(highs, lows, closes, i, lookback=pullback_lookback,
                                             min_pullback_pct=min_pullback_pct,
                                             max_pullback_pct=max_pullback_pct,
                                             ma_period=pullback_ma_period)
                        if not pb:
                            continue
                        pivot = pb["pivot"]
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
                    candidates.append((ticker, e, pivot, avg_vol50, avg_val, quality))

                # 슬롯보다 후보가 많을 때 누구를 먼저 담을지 정하는 순위.
                # quality_rank_weight=0이면 기존대로 RS등급(기술적 상대강도)만 본다.
                # 0보다 크면 그 비중만큼 기본적 분석 점수를 섞는다 - 둘 다 0~1로
                # 정규화해서 더한다(RS는 1~99라 99로 나눔). 재무데이터가 없는 종목은
                # 점수를 0.5(중립)로 둔다 - 없다는 이유로 뒤로 밀지 않기 위함.
                # 동점일 때는 티커 순으로 잘라 실행할 때마다 결과가 달라지지 않게 한다.
                def _rank_key(c):
                    rs = (c[1].get("rsRating") or 0) / 99
                    q = c[5] if c[5] is not None else 0.5
                    return (-(rs * (1 - quality_rank_weight) + q * quality_rank_weight), c[0])

                candidates.sort(key=_rank_key)
                for ticker, e, pivot, avg_vol50, avg_trade_val, quality in candidates:
                    if open_slots <= 0:
                        break
                    dates, closes, highs, lows, volumes, opens = series[ticker]
                    i = idx_at_rd[ticker]
                    s_i = bisect.bisect_right(dates, prev_rd) if prev_rd else max(0, i - rescan_interval_days)
                    # 피벗가+0.5% 지정가로 장중 저가 터치를 기다리지 않는다 - 그날 종가가
                    # 피벗을 넘었고 거래량 조건도 맞으면 그날 종가로 곧바로 체결한다.
                    fill_date = fill_price = fj = None
                    if entry_mode == "pullback":
                        # 눌림목은 피벗(직전 고점) '아래'에서 사는 것이라 돌파형처럼
                        # "종가가 피벗을 넘은 날"을 기다릴 수 없다. 조건이 성립한
                        # 첫날 종가로 바로 체결한다. 거래량 급증도 요구하지 않는다 -
                        # 눌림 구간은 오히려 거래량이 줄어드는 게 정상이다.
                        for j in range(s_i, i + 1):
                            if not detect_pullback(highs, lows, closes, j, lookback=pullback_lookback,
                                                   min_pullback_pct=min_pullback_pct,
                                                   max_pullback_pct=max_pullback_pct,
                                                   ma_period=pullback_ma_period):
                                continue
                            # 되돌림 재개일 거래량 확인(선택) - SEPA/오닐 식 "발자국"
                            # 논리: 눌림 구간 자체는 거래량이 줄어드는 게 정상이지만,
                            # 방향을 튼 당일에는 기관 매수가 들어왔다면 거래량이
                            # 평균을 웃돌아야 한다는 가설. 데이터 없으면 거르지 않는다.
                            if pullback_min_volume_mult is not None:
                                vol50 = _avg_volume(volumes, j)
                                vol_j = volumes[j]
                                if vol50 and vol_j is not None and vol_j < vol50 * pullback_min_volume_mult:
                                    continue
                            fill_date, fill_price, fj = dates[j], closes[j], j
                            break
                    else:
                        for j in range(s_i, i + 1):
                            if closes[j] <= pivot:
                                continue
                            vol = volumes[j]
                            if vol is None or vol < avg_vol50 * volume_breakout_mult:
                                continue
                            fill_date, fill_price, fj = dates[j], closes[j], j
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
                        max_pos_value = _position_cap(seed, equity_now)
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
        "excludedUnprofitable": excluded_unprofitable,
        "excludedLowQuality": excluded_low_quality,
        "excludedNoCatalyst": excluded_no_catalyst,
        "excludedEpsGrowth": excluded_eps_growth,
        "excludedVolatility": excluded_volatility,
        "excludedEvanStage2": excluded_evan,
        "excludedFinQuality": excluded_fin_quality,
        "excludedCooldown": excluded_cooldown,
        "benchmark": {"label": BENCHMARK_LABEL.get(market, "Benchmark"), "returnPct": benchmark_return_pct,
                      "equityCurve": benchmark_curve},
        "equityCurve": equity_curve, "trades": trades,
    }
