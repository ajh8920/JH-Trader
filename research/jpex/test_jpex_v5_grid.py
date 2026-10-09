# -*- coding: utf-8 -*-
"""JPEX_V5_PARAMS(국면게이트 해제+regime_exit_min_r) 확정 이후, 그 두 인자를 더
세밀하게·동시에 격자탐색해 CAGR/Calmar를 추가로 개선할 수 있는지 찾는다.

목적:
  18단계에서 JPEX_V5_PARAMS(regime_exit_min_r=1.5, entry_rank_top_n=40 그대로)를
  채택했지만, regime_exit_min_r는 {1.0, 1.5}만 비교했고 entry_rank_top_n은
  {40, 60}만 비교했다(60은 후보 풀 희석으로 대실패). "한 번에 한 인자만 바꾸지
  말고 여러 인자를 동시에 바꿔 최적을 찾으라"는 프로젝트 표준 방침에 따라, 이번엔
  regime_exit_min_r(1.25/1.5/1.75)과 entry_rank_top_n(25/40, 40은 이미 아는 V5
  기준점) 두 축을 3×2 격자로 동시에 바꿔 더 좋은 조합이 있는지 확인한다.
  entry_rank_top_n=25는 아직 안 시험한 "더 타이트하게" 방향 - 60(후보 확대)이
  품질 희석으로 실패했으니 반대 방향(후보를 더 좁혀 품질을 높임)이 유효한지 본다.

배경:
  vcp_strategy.JPEX_V5_PARAMS = {**JPEX_PARAMS, "gate_entries_on_regime": False,
  "regime_exit_min_r": 1.5}가 현재 "최종 채택" 단일전략이다(CAGR 34.60%/MDD
  -30.02%/Calmar 1.153/거래 223건). research/jpex/test_jpex_unified.py의 A/B/C
  3변형 결과가 이 선택의 근거였다.

변형: JPEX_PARAMS를 베이스로 entry_rank_top_n×regime_exit_min_r 조합 6개.
  (25, 1.25) (25, 1.5) (25, 1.75) (40, 1.25) (40, 1.75) - 5개 신규 실행.
  (40, 1.5)는 이미 아는 JPEX_V5_PARAMS 그 자체라 재실행하지 않는다.

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 각 조합의 CAGR/MDD/Calmar/승률/거래수(연간) 출력,
  research/jpex/jpex_v5_grid_trades.json에 조합별 equityCurve+trades(날짜 포함) 저장.

사용법: python -m research.jpex.test_jpex_v5_grid
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

BASE = {**vcp.JPEX_PARAMS, "gate_entries_on_regime": False}
GRID = {
    "rank25_r1.25": {**BASE, "entry_rank_top_n": 25, "regime_exit_min_r": 1.25},
    "rank25_r1.5": {**BASE, "entry_rank_top_n": 25, "regime_exit_min_r": 1.5},
    "rank25_r1.75": {**BASE, "entry_rank_top_n": 25, "regime_exit_min_r": 1.75},
    "rank40_r1.25": {**BASE, "entry_rank_top_n": 40, "regime_exit_min_r": 1.25},
    "rank40_r1.75": {**BASE, "entry_rank_top_n": 40, "regime_exit_min_r": 1.75},
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
    print(f"[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 거래 {len(trades)}건(연 {len(trades)/years_span:.1f}건)", flush=True)

# 기준점(이미 아는 값, 재실행 안 함)과 나란히 비교
print("\n[rank40_r1.5(=JPEX_V5_PARAMS, 기준점, 재실행 안 함)] CAGR 34.60% MDD -30.02% "
      "Calmar 1.153 승률 34.5% 거래 223건(연 20.7건)")

out_path = Path(__file__).parent / "jpex_v5_grid_trades.json"
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
