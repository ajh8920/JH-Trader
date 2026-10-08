# -*- coding: utf-8 -*-
"""JPEX 전략을 만들며 시험한 30여 개 변형을 하나의 스크립트로 합친 것.

2026-10-08 밤~아침 세션에서 scratchpad에 변형마다 70줄짜리 파일을 복제해가며
돌렸던 것(vcp_run_jpex_v1.py ~ v30.py)을 다시 쓸 수 있게 정리했다 - 그 파일들은
세션 임시 폴더에만 있어서 영구 보관되지 않았다. 결과 숫자는 RESULTS.md 참고.

사용법:
  python -m research.jpex.run_jpex_variant v6          # 최종 채택(JPEX_PARAMS)
  python -m research.jpex.run_jpex_variant v25          # 거래량 확대 대안
  python -m research.jpex.run_jpex_variant --list        # 변형 목록만 출력

주의: 로컬 전용이다. data/price_cache(가격 캐시)와 000.Data(재무·대량보유·분기
데이터, 이 저장소 밖)가 있어야 실행된다 - RULES.md와 CLAUDE.md 참고.
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
# v6이 최종 채택(JPEX_PARAMS)이고, 그 뒤는 v6 기준 추가 오버라이드다.
VARIANTS = {
    "v1": {},  # 국면게이팅+국면상실청산만 추가 (베이스라인)
    "v2a": {"max_positions": 4, "max_position_weight_pct": 30.0},
    "v2b": {"entry_rank_top_n": 80},
    "v3": {"max_positions": 4, "max_position_weight_pct": 40.0, "entry_rank_top_n": 80},
    "v4": {"max_positions": 3, "max_position_weight_pct": 35.0, "entry_rank_top_n": 60},
    "v5": {"max_positions": 3, "max_position_weight_pct": 35.0},
    "v6": {"pyramid_max_count": 6},  # === 최종 채택: JPEX_PARAMS와 동일 ===
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
                "entry_rank_top_n": 1000},  # 진단용 - 슬롯/순위게이트를 사실상 해제해 신호 천장 확인
}

# 구간검증(강건성 확인)용 - 변형은 항상 v6(JPEX_PARAMS) 기준
PERIOD_OVERRIDES = {
    "full": ("2016-01-01", None),  # None이면 오늘 날짜
    "down": ("2016-01-01", "2019-12-31"),  # 2017-2019 하락장
    "up": ("2019-06-01", None),  # 2020-2026 상승장
    "covid": ("2019-06-01", "2020-06-30"),  # 코로나 급락 스트레스테스트
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant", nargs="?", default="v6", help="VARIANTS 키 (기본 v6=최종 채택)")
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
