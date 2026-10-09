# -*- coding: utf-8 -*-
"""quality_rank_weight=0.3(27단계 첫 성공)을 다른 이전 실험들과 결합해 추가
개선을 찾는다 - 특히 entry_rank_top_n을 넓혀도(이전엔 품질 가중 없이
Top60이 실패) 품질 가중이 후보 풀의 junk를 걸러줘 이번엔 다를 수 있다는
가설.

목적:
  27단계에서 quality_rank_weight=0.3이 baseline을 CAGR 39.07%->40.04%,
  Calmar 1.195->1.228로 올렸다(기본적 품질점수를 순위에 섞어 "모멘텀은
  있지만 재무도 받쳐주는" 종목을 우선). entry_rank_top_n=60(14단계/5라운드
  에서 품질 가중 없이 시험 - Calmar 0.690으로 최악)과 min_revenue_growth
  강화(24단계에서 CAGR 하락)가 전부 품질 가중 없이 실패했던 축인데, 이제
  품질 가중이 후보 풀의 junk를 미리 걸러주는 상태에서 재시험하면 다른
  결과가 나올 수 있다.

변형: quality_rank_weight=0.3을 공통으로 깔고 JPEX_V6_PARAMS 베이스로
  - top60: entry_rank_top_n 40->60(후보 확대)
  - top25: entry_rank_top_n 40->25(후보 축소)
  - rs75: evan_params min_rs 62->75
  - revenue25: min_revenue_growth 15->25, min_eps_growth_pct 20->30

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v15_quality_combo_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v15_quality_combo
"""
import io
import json
import sys
import time
from datetime import date
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from flask import Flask
from models import db, KrFundamental
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{PROJECT_DIR / 'data' / 'app.db'}"
db.init_app(app)

import vcp_strategy as vcp
from local_price_cache import cached_fetch_ohlc_history_batches
from data_pipeline.common import SHAREHOLDER_KR_DIR, FUND_KR_DIR

with open(PROJECT_DIR / "kr_stocks.json", encoding="utf-8") as f:
    kr_stocks = json.load(f)
shares_map = {s["code"]: s["shares"] for s in kr_stocks if s.get("shares")}
shareholder_rows = vcp.load_shareholder_rows(
    [SHAREHOLDER_KR_DIR / "majorstock_1of2.parquet", SHAREHOLDER_KR_DIR / "majorstock_2of2.parquet"])
with app.app_context():
    fundamentals_rows = vcp.load_fundamentals_rows(KrFundamental)
quarter_paths = sorted((FUND_KR_DIR.parent / "kr_quarter").glob("*.parquet"))
quarterly_rows = vcp.load_quarterly_rows(quarter_paths)

START, END = "2016-01-01", date.today().isoformat()
SEED = 500_000_000
fetch_fn = cached_fetch_ohlc_history_batches("KR", include_delisted=True, max_age_hours=99999)

BASE = {**vcp.JPEX_V6_PARAMS, "quality_rank_weight": 0.3}
GRID = {
    "top60": {**BASE, "entry_rank_top_n": 60},
    "top25": {**BASE, "entry_rank_top_n": 25},
    "rs75": {**BASE, "evan_params": {"min_rs": 75.0}},
    "revenue25": {**BASE, "min_revenue_growth": 25.0, "min_eps_growth_pct": 30.0},
}


def run(preset, label):
    kw = dict(preset)
    kw.pop("market", None)
    kw.pop("default_seed", None)
    kw["quarterly_rows_by_code"] = quarterly_rows
    vcp.MAX_POSITION_WEIGHT_PCT = kw.get("max_position_weight_pct", 30.0)
    print(f"=== {label} 실행 ===", flush=True)
    t0 = time.time()
    r = vcp.run_vcp_backtest(
        "KR", START, END, seed=SEED, fetch_fn=fetch_fn, shares_map=shares_map,
        shareholder_rows_by_code=shareholder_rows, fundamentals_rows_by_code=fundamentals_rows, **kw)
    print(f"{label} 완료 ({round(time.time() - t0)}s) trades={len(r.get('trades', []))}", flush=True)
    return r


years_span = (date.today() - date(2016, 1, 1)).days / 365.25
results = {}
for key, preset in GRID.items():
    r = run(preset, key)
    if "error" in r:
        print("오류:", r.get("error"), flush=True)
        continue
    results[key] = r
    final_val = r["equityCurve"][-1]["value"]
    cagr = ((final_val / SEED) ** (1 / years_span) - 1) * 100
    mdd = r.get("mddPct", 0)
    calmar = cagr / abs(mdd) if mdd else float("inf")
    trades = r["trades"]
    wins = [t for t in trades if t["pnlPct"] > 0]
    wr = len(wins) / len(trades) * 100 if trades else 0
    print(f"\n[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 손익비 {r.get('profitLossRatio')} 거래 {len(trades)}건"
          f"(연 {len(trades)/years_span:.1f}건)", flush=True)

out_path = Path(__file__).parent / "jpex_v15_quality_combo_trades.json"
out_data = {"seed": SEED}
for key, r in results.items():
    out_data[f"{key}_curve"] = r["equityCurve"]
    out_data[f"{key}_trades"] = [
        {"code": t["code"], "entryDate": t["entryDate"], "exitDate": t["exitDate"], "pnlPct": t["pnlPct"]}
        for t in r["trades"]
    ]
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out_data, f)
print(f"\n저장 완료: {out_path}", flush=True)
