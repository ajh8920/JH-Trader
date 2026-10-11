# -*- coding: utf-8 -*-
"""46단계: "다른 여러가지의 조건을 조합하여 결과를 개선해보라"는 요청에 따라,
지금까지 각각 단독으로만 시험했던 축들을 서로 조합해본다 - 40~45단계는
전부 V7(quality_rank_weight=0.3 고정) 위에 축 하나씩만 추가했을 뿐, 그
축들끼리는 한 번도 섞어보지 않았다.

조합:
  - combo_a: new_pivot(40단계, 재진입 차단) + min_blend_score=0.75(44단계,
    블렌드점수 하위 필터) - "같은 피벗 반복 재진입"과 "애초에 신호가 약한
    후보"는 서로 다른 문제라 두 필터가 겹치지 않는 거래를 걸러낼 수도
    있다는 가설.
  - combo_b: regime_exit_min_r=1.0(6라운드 이전 그리드의 값, quality_rank_weight
    없이 측정됐었음) - quality 블렌드가 후보 자체를 더 좋게 솎아낸 지금은
    국면상실청산을 더 엄격하게(r_min을 낮춰 더 많이 끊게) 해도 다를 수
    있다는 가설.
  - combo_c: regime_exit_min_r=1.5(같은 이유, 반대 방향 - 더 느슨하게)
  - combo_d: trail_early(25/28단계 - trail_activate_r 2.0->1.0, breakeven_r
    3.0->1.5, 이 세션 전체 최고 Calmar 1.296을 냈던 조합) + min_blend_score=0.75
    - Calmar 최고 조합에 44단계의 품질 하한까지 더하면 CAGR도 같이
    올라가는지.

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래 + 2019/2022/2025 요약,
  research/jpex/jpex_v30_combos_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v30_combos
"""
import io
import json
import sys
import time
from collections import defaultdict
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

GRID = {
    "combo_a_newpivot_minblend": {**vcp.JPEX_V7_PARAMS, "require_new_pivot": True, "min_blend_score": 0.75},
    "combo_b_rmin1.0": {**vcp.JPEX_V7_PARAMS, "regime_exit_min_r": 1.0},
    "combo_c_rmin1.5": {**vcp.JPEX_V7_PARAMS, "regime_exit_min_r": 1.5},
    "combo_d_trailearly_minblend": {**vcp.JPEX_V7_PARAMS, "trail_activate_r": 1.0, "breakeven_r": 1.5,
                                     "min_blend_score": 0.75},
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


def year_breakdown(trades, years_of_interest):
    by_year = defaultdict(list)
    for t in trades:
        by_year[t["exitDate"][:4]].append(t)
    for y in years_of_interest:
        ts = by_year.get(y, [])
        n = len(ts)
        if n == 0:
            print(f"  {y}: 거래 0건")
            continue
        wins = [t for t in ts if t["pnlPct"] > 0]
        exp = sum(t["pnlPct"] for t in ts) / n
        print(f"  {y}: {n}건 승률{len(wins)/n*100:.1f}% 기대값{exp:.2f}%/건")


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
    year_breakdown(trades, ["2019", "2022", "2025"])

print("\n=== 기준(V7 그대로, CAGR 40.04%/MDD -32.61%/Calmar 1.228/승률35.5%/거래231건) ===", flush=True)

out_path = Path(__file__).parent / "jpex_v30_combos_trades.json"
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
