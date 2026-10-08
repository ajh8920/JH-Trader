# -*- coding: utf-8 -*-
"""JPEX 변형 백테스트 실행기

목적:
  "JPEX" 전략(APEX Stage3 68차 + 시장국면 게이팅/국면상실청산 + 피라미딩 최적화)을
  만드는 과정에서 시험한 변형들을 재현 가능한 형태로 보관하고, 새 변형을 추가로
  시험할 때도 같은 스크립트를 재사용한다.

배경:
  2026-10-08 밤~아침 세션에서 scratchpad(세션 임시 폴더)에 변형마다 70줄짜리
  파일을 복제해가며 돌렸다(vcp_run_jpex_v1.py ~ v30.py). 임시 폴더라 세션이
  끝나면 사라지므로, 변형을 딕셔너리(VARIANTS)로 추출해 하나의 스크립트로
  합쳤다. 지인 목표(CAGR 68.9%/MDD -24.2%/거래 487건)에 근접시키려는 탐색이
  계속 이어질 예정이라 재사용 가능한 형태가 필요했다.

변형:
  VARIANTS 딕셔너리 참고(`--list`로 조회). v64 = 최종 채택(`vcp_strategy.JPEX_PARAMS`
  와 동일, 4라운드: max_initial_risk_pct 4.0 적용). v49는 2라운드 최종(=JPEX_V3_PARAMS)
  이었으나 v64로 대체됨. v6은 1라운드 최종(`JPEX_V2_PARAMS`)이었으나 v49로 대체됨.
  PERIOD_OVERRIDES로 구간검증(하락장/상승장/코로나 급락)도 가능.

데이터:
  로컬 전용. data/price_cache/*.parquet(가격 캐시, 2018년~), data/app.db(재무
  일부), 000.Data/(이 저장소 밖 - 대량보유·공시·분기재무, STOCK_DATA_ROOT 환경변수로
  경로 변경 가능) 전부 있어야 실행된다. 전부 상장폐지 종목 포함(include_delisted=True).

산출(결과물):
  콘솔에 CAGR/총수익/최종자산/MDD/CAGR-MDD/알파/승률/손익비/거래수/평균보유/고유종목수
  출력. 숫자를 비교·누적 기록하는 곳은 이 스크립트가 아니라 RESULTS.md다 - 새
  변형을 추가했으면 VARIANTS에 넣고 RESULTS.md 표에도 같이 추가할 것.

사용법:
  python -m research.jpex.run_jpex_variant v64                   # 최종 채택
  python -m research.jpex.run_jpex_variant v25                   # 거래량 확대 대안
  python -m research.jpex.run_jpex_variant v64 --period covid    # 코로나 급락 구간검증
  python -m research.jpex.run_jpex_variant --list                # 변형 목록만 출력
"""
import argparse
import io
import json
import sys
import time
from datetime import date
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR))

# JPEX_V1_PARAMS(=APEX_STAGE3_BEST68_PARAMS + 국면게이팅 + 국면상실청산)에 더한
# 오버라이드만 적어둔다 - 전부 vcp_strategy.JPEX_V1_PARAMS를 베이스로 한다.
# v1~v30은 2026-10-08 새벽(1라운드, entry_rank_top_n 동순위 비결정성 버그
# 수정 전) 측정값이라 재실행하면 숫자가 달라질 수 있다 - research/jpex/
# RESULTS.md 7단계 참고. v31부터는 버그 수정 + 성능 최적화 이후(2라운드)다.
# v64 = 최종 채택(vcp_strategy.JPEX_PARAMS와 동일, 4라운드). v49(=JPEX_V3_PARAMS)는
# 2라운드 최종이었지만 v64로 대체됐다. v6(=JPEX_V2_PARAMS)은 1라운드 최종이었지만 v49로 대체됐다.
# _V64 = 4라운드 변형들이 기준으로 삼는 베이스 오버라이드.
_V64 = {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0,
        "chandelier_atr_mult": 3.0, "breakeven_r": 3.0, "max_initial_risk_pct": 4.0}

VARIANTS = {
    "v1": {},  # 국면게이팅+국면상실청산만 추가 (베이스라인)
    "v2a": {"max_positions": 4, "max_position_weight_pct": 30.0},
    "v2b": {"entry_rank_top_n": 80},
    "v3": {"max_positions": 4, "max_position_weight_pct": 40.0, "entry_rank_top_n": 80},
    "v4": {"max_positions": 3, "max_position_weight_pct": 35.0, "entry_rank_top_n": 60},
    "v5": {"max_positions": 3, "max_position_weight_pct": 35.0},
    "v6": {"pyramid_max_count": 6},  # 1라운드 최종(JPEX_V2_PARAMS와 동일) - v49로 대체됨
    "v7": {"pyramid_max_count": 8},
    "v8": {"pyramid_max_count": 6, "max_positions": 4, "max_position_weight_pct": 30.0},
    "v9": {"pyramid_max_count": 5},
    "v10": {"pyramid_max_count": 7},
    "v11": {"pyramid_max_count": 6, "max_position_weight_pct": 70.0},
    "v12": {"pyramid_max_count": 6, "chandelier_atr_mult": 4.0},
    "v13": {"pyramid_max_count": 6, "chandelier_atr_mult": 3.0},
    "v14": {"pyramid_max_count": 6, "initial_stop_atr_mult": 0.7, "max_initial_risk_pct": 2.0},
    "v15": {"pyramid_max_count": 6, "breakeven_r": 3.0},
    "v16": {"pyramid_max_count": 6, "breakeven_r": 5.0},
    "v17": {"pyramid_max_count": 6, "time_stop_days": 15},
    "v18": {"pyramid_max_count": 6, "trail_activate_r": 1.5},
    "v19": {"pyramid_max_count": 6, "min_market_cap": 150_000_000_000},
    "v20": {"pyramid_max_count": 6, "min_eps_growth_pct": 10.0},
    "v21": {"pyramid_max_count": 6, "partial_profit_fraction": 0.25, "partial_profit_r": 3.0},
    "v22": {"pyramid_max_count": 6, "early_stop_days": 3, "early_stop_pct": -8.0},
    "v23": {"pyramid_max_count": 6, "entry_rank_top_n": 50},
    "v24": {"pyramid_max_count": 6, "min_pullback_pct": 2.0, "max_pullback_pct": 10.0},
    "v25": {"pyramid_max_count": 6, "max_positions": 3, "max_position_weight_pct": 35.0},
    "v26": {"pyramid_max_count": 6, "entry_rank_top_n": 60},
    "v27": {"pyramid_max_count": 6, "max_positions": 4, "max_position_weight_pct": 30.0, "entry_rank_top_n": 60},
    "v28": {"pyramid_max_count": 6, "max_positions": 3, "max_position_weight_pct": 35.0, "entry_rank_top_n": 60},
    "v29": {"pyramid_max_count": 6, "max_positions": 3, "max_position_weight_pct": 35.0, "entry_rank_top_n": 50},
    "v30": {"pyramid_max_count": 6, "max_positions": 3, "max_position_weight_pct": 35.0,
            "min_eps_growth_pct": 10.0, "min_revenue_growth": 10.0},
    "ceiling": {"pyramid_max_count": 6, "max_positions": 30, "max_position_weight_pct": 8.0,
                "entry_rank_top_n": 1000},  # 진단용 - 신호 천장 확인(1라운드)

    # --- 2라운드(2026-10-08 아침, 결정성 버그 수정 + 성능 최적화 이후) ---
    "v31": {"pyramid_max_count": 6, "require_profitable": True},
    "v32": {"pyramid_max_count": 6, "evan_params": {"min_rs": 70.0}},
    "v33": {"pyramid_max_count": 6, "require_catalyst": True},  # 무효 시험 - catalyst_dates_by_code 미전달
    "v34": {"pyramid_max_count": 6, "overheat_days": 5, "overheat_gain_pct": 50.0},
    "v35": {"pyramid_max_count": 6, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0},
    "v36": {"pyramid_max_count": 6, "overheat_days": 5, "overheat_gain_pct": 50.0, "breakeven_r": 3.0},
    "v37": {"pyramid_max_count": 6, "overheat_days": 7, "overheat_gain_pct": 40.0},
    "v38": {"pyramid_max_count": 6, "overheat_days": 5, "overheat_gain_pct": 50.0, "trail_activate_r": 1.5},
    "v39": {"pyramid_max_count": 6, "overheat_days": 5, "overheat_gain_pct": 50.0,
            "chandelier_atr_mult": 3.0, "breakeven_r": 3.0},
    "v40": {"pyramid_max_count": 6, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 2.5},
    "v41": {"pyramid_max_count": 6, "overheat_days": 5, "overheat_gain_pct": 50.0,
            "chandelier_atr_mult": 3.0, "trail_activate_r": 1.5},
    "v42": {"pyramid_max_count": 5, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0},
    "v43": {"pyramid_max_count": 5, "overheat_days": 5, "overheat_gain_pct": 50.0,
            "chandelier_atr_mult": 3.0, "breakeven_r": 3.0},
    "v44": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0},
    "v45": {"pyramid_max_count": 5, "overheat_days": 5, "overheat_gain_pct": 40.0, "chandelier_atr_mult": 3.0},
    "v46": {"pyramid_max_count": 5, "overheat_days": 3, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0},
    "v47": {"pyramid_max_count": 3, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0},
    "v48": {"pyramid_max_count": 2, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0},
    "v49": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0,
            "chandelier_atr_mult": 3.0, "breakeven_r": 3.0},  # === 2라운드 최종(JPEX_V3_PARAMS와 동일) - v64로 대체됨 ===
    "v50": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 2.5},
    "v51": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0,
            "chandelier_atr_mult": 3.0, "breakeven_r": 3.0, "trail_activate_r": 1.5},
    "v52": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0,
            "chandelier_atr_mult": 3.0, "breakeven_r": 2.5},
    "v53": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "initial_stop_atr_mult": 0.7, "max_initial_risk_pct": 2.0},

    # --- 3라운드(2026-10-08 밤, 거래량 확대 재시도 + CAGR 50% 목표) ---
    "v55": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "max_positions": 3, "max_position_weight_pct": 35.0},
    "v56": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "max_positions": 4, "max_position_weight_pct": 30.0},
    "v57": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "evan_params": {"min_rs": 55.0}},
    "v58": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "entry_rank_top_n": 60},
    "v59": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "max_positions": 3, "max_position_weight_pct": 50.0},
    "v60": {"pyramid_max_count": 6, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "max_positions": 3, "max_position_weight_pct": 45.0},
    "v61": {"pyramid_max_count": 6, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "max_positions": 4, "max_position_weight_pct": 45.0},
    "v62": {"pyramid_max_count": 8, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "max_positions": 4, "max_position_weight_pct": 40.0},
    "v63": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "max_position_weight_pct": 65.0},
    "v64": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "max_initial_risk_pct": 4.0},  # === 2026-10-08 밤 최고 ===
    "v65": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "exit_on_regime_loss": False},
    "v66": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "max_position_weight_pct": 65.0, "max_initial_risk_pct": 4.0},
    "v67": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "max_initial_risk_pct": 5.0},
    "v68": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "max_initial_risk_pct": 6.0},
    "v69": {"pyramid_max_count": 4, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "max_initial_risk_pct": 4.0, "initial_stop_atr_mult": 1.5},
    "v70": {"pyramid_max_count": 5, "overheat_days": 5, "overheat_gain_pct": 50.0, "chandelier_atr_mult": 3.0,
            "breakeven_r": 3.0, "max_initial_risk_pct": 4.0},

    # --- 4라운드(2026-10-09 새벽, CAGR 50% 목표 - 시총 밴드/소형주 탐색) ---
    "v71": {**_V64, "min_market_cap": 150_000_000_000},
    "v72": {**_V64, "min_eps_growth_pct": 10.0},
    "v73": {**_V64, "min_revenue_growth": 10.0},
    "v74": {**_V64, "min_pullback_pct": 1.0},
    "v75": {**_V64, "min_market_cap": 30_000_000_000, "max_market_cap": 300_000_000_000},
    "v76": {**_V64, "min_market_cap": 50_000_000_000, "max_market_cap": 200_000_000_000},
    "v77": {**_V64, "min_market_cap": 100_000_000_000, "max_market_cap": 500_000_000_000},
    "v78": {**_V64, "min_market_cap": 10_000_000_000, "max_market_cap": 100_000_000_000},
    "v79": {**_V64, "min_market_cap": 30_000_000_000, "max_market_cap": 300_000_000_000,
            "min_eps_growth_pct": 0.0, "min_revenue_growth": 0.0},
    "v80": {**_V64, "min_market_cap": 10_000_000_000, "max_market_cap": 100_000_000_000,
            "min_eps_growth_pct": 0.0, "min_revenue_growth": 0.0},
    "v81": {**_V64, "min_market_cap": 30_000_000_000, "max_market_cap": 300_000_000_000,
            "min_eps_growth_pct": 0.0, "min_revenue_growth": 0.0, "min_avg_trade_value": 50_000_000},
    "v82": {**_V64, "min_market_cap": 50_000_000_000, "max_market_cap": 500_000_000_000,
            "min_eps_growth_pct": 0.0, "min_revenue_growth": 0.0},
    "v83": {**_V64, "min_market_cap": 50_000_000_000, "max_market_cap": 500_000_000_000,
            "min_eps_growth_pct": 0.0, "min_revenue_growth": 0.0,
            "evan_params": {"min_rs": 40.0}, "max_pct_of_avg_trade_value": 20},
    "v84": {**_V64, "min_market_cap": 50_000_000_000, "max_market_cap": 500_000_000_000,
            "min_eps_growth_pct": 0.0, "min_revenue_growth": 0.0, "evan_params": {"min_rs": 40.0}},
    "v85": {**_V64, "min_market_cap": 50_000_000_000, "max_market_cap": 500_000_000_000,
            "min_eps_growth_pct": 0.0, "min_revenue_growth": 0.0,
            "min_pullback_pct": 1.0, "max_pullback_pct": 12.0},
    "v86": {**_V64, "min_market_cap": 50_000_000_000, "max_market_cap": 500_000_000_000,
            "min_eps_growth_pct": 0.0, "min_revenue_growth": 0.0, "chandelier_atr_mult": 5.0},
    "v87": {**_V64, "min_market_cap": 50_000_000_000, "max_market_cap": 500_000_000_000,
            "min_eps_growth_pct": 0.0, "min_revenue_growth": 0.0, "require_evan_stage2": False},
    "v88": {**_V64, "min_market_cap": 50_000_000_000, "max_market_cap": 500_000_000_000,
            "min_eps_growth_pct": 0.0, "min_revenue_growth": 0.0, "gate_entries_on_regime": False},
    "v89": {**_V64, "min_market_cap": 50_000_000_000, "max_market_cap": 500_000_000_000,
            "min_eps_growth_pct": 0.0, "min_revenue_growth": 0.0, "position_sizing_mode": "equal_weight"},
    "v90": {**_V64, "min_market_cap": 50_000_000_000, "max_market_cap": 500_000_000_000,
            "min_eps_growth_pct": 0.0, "min_revenue_growth": 0.0,
            "require_evan_stage2": False, "gate_entries_on_regime": False},
    "v91": {**_V64, "min_market_cap": 500_000_000_000},
    "v92": {**_V64, "min_market_cap": 1_000_000_000_000},
    "v93": {**_V64, "entry_rank_top_n": 20},
    "v94": {**_V64, "entry_rank_top_n": 10},
    "v95": {**_V64, "time_stop_days": 15},
    "v96": {**_V64, "time_stop_days": 5},
}
del _V64

# 구간검증(강건성 확인)용 - 변형은 기본 v64(JPEX_PARAMS) 기준으로 쓴다
PERIOD_OVERRIDES = {
    "full": ("2016-01-01", None),  # None이면 오늘 날짜
    "down": ("2016-01-01", "2019-12-31"),  # 2017-2019 하락장
    "up": ("2019-06-01", None),  # 2020-2026 상승장
    "covid": ("2019-06-01", "2020-06-30"),  # 코로나 급락 스트레스테스트
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant", nargs="?", default="v64", help="VARIANTS 키 (기본 v64=최종 채택)")
    parser.add_argument("--period", default="full", choices=list(PERIOD_OVERRIDES), help="구간검증용")
    parser.add_argument("--list", action="store_true", help="변형 목록만 출력하고 종료")
    args = parser.parse_args()

    if args.list:
        for k, v in VARIANTS.items():
            print(f"{k}: {v}")
        return

    if args.variant not in VARIANTS:
        print(f"알 수 없는 변형: {args.variant} (--list로 목록 확인)")
        sys.exit(1)

    from flask import Flask
    from models import db, KrFundamental
    import vcp_strategy as vcp
    from local_price_cache import cached_fetch_ohlc_history_batches
    from data_pipeline.common import SHAREHOLDER_KR_DIR, FUND_KR_DIR

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{PROJECT_DIR / 'data' / 'app.db'}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    with open(PROJECT_DIR / "kr_stocks.json", encoding="utf-8") as f:
        kr_stocks = json.load(f)
    shares_map = {s["code"]: s["shares"] for s in kr_stocks if s.get("shares")}
    shareholder_rows = vcp.load_shareholder_rows([
        SHAREHOLDER_KR_DIR / "majorstock_1of2.parquet", SHAREHOLDER_KR_DIR / "majorstock_2of2.parquet",
    ])
    with app.app_context():
        fundamentals_rows = vcp.load_fundamentals_rows(KrFundamental)
    disclosure_paths = sorted((FUND_KR_DIR.parent / "disclosures_kr").glob("*.parquet"))
    dividend_dates = vcp.load_dividend_dates(disclosure_paths)
    quarter_paths = sorted((FUND_KR_DIR.parent / "kr_quarter").glob("*.parquet"))
    quarterly_rows = vcp.load_quarterly_rows(quarter_paths)

    fetch_fn = cached_fetch_ohlc_history_batches("KR", include_delisted=True, max_age_hours=99999)
    start, end = PERIOD_OVERRIDES[args.period]
    end = end or date.today().isoformat()
    years = (date.fromisoformat(end) - date.fromisoformat(start)).days / 365.25
    SEED = 500_000_000

    kw = {**vcp.JPEX_V1_PARAMS, **VARIANTS[args.variant]}
    kw.pop("market", None)
    kw.pop("default_seed", None)
    kw["quarterly_rows_by_code"] = quarterly_rows
    vcp.MAX_POSITION_WEIGHT_PCT = kw.get("max_position_weight_pct", 30.0)

    print(f"=== JPEX {args.variant} ({args.period}: {start}~{end}) ===", flush=True)
    t0 = time.time()
    r = vcp.run_vcp_backtest(
        "KR", start, end, seed=SEED, fetch_fn=fetch_fn, shares_map=shares_map,
        shareholder_rows_by_code=shareholder_rows, fundamentals_rows_by_code=fundamentals_rows,
        dividend_dates_by_code=dividend_dates, **kw,
    )
    el = round(time.time() - t0)
    if "error" in r:
        print(f"오류: {r['error']} ({el}s)", flush=True)
        return

    cagr = round(((r["finalValue"] / r["seed"]) ** (1 / years) - 1) * 100, 2)
    tr = r["trades"]
    wins = [t for t in tr if t["pnlPct"] > 0]
    losses = [t for t in tr if t["pnlPct"] <= 0]
    aw = round(sum(t["pnlPct"] for t in wins) / len(wins), 2) if wins else 0
    al = round(sum(t["pnlPct"] for t in losses) / len(losses), 2) if losses else 0
    py = round(r["tradeCount"] / years, 1) if years else 0
    mdd = r["mddPct"]
    cagr_mdd = round(cagr / mdd, 3) if mdd else None
    print(f"({el}s)", flush=True)
    print(f"CAGR={cagr}%  총수익={r['returnPct']:.1f}%  최종자산={r['finalValue']:,.0f}원  "
          f"MDD=-{mdd}%  CAGR/MDD={cagr_mdd}  알파={r.get('alphaPct')}%", flush=True)
    print(f"승률={r['winRatePct']}%  손익비={r['profitLossRatio']}  평균수익={aw}%  평균손실={al}%  "
          f"거래={r['tradeCount']}건(연{py}건)", flush=True)
    print(f"고유종목수: {len(set(t['code'] for t in tr))}개", flush=True)


if __name__ == "__main__":
    main()
