# -*- coding: utf-8 -*-
"""65단계: trailingStop(챈들리어 트레일링)이 V10에서 가장 큰 평균수익(+43.3%,
strategy_trades/jpex_v10.json 실측)을 내는 청산 메커니즘인데,
chandelier_atr_mult(현재 3.0)/trail_activate_r(현재 2.0)가 breadth
게이트(49~50단계)·피라미딩게이트(53단계)·entries breadth 게이트(10라운드)
도입 이후 V10 기준으로 재튜닝된 적이 없다 - 후보 풀 자체가 그때그때
달라졌으니 트레일링 폭의 최적점도 달라졌을 수 있다.

변형: JPEX_V10_PARAMS 베이스로
  chandelier_atr_mult = 2.5 / 2.75 / 3.25 / 3.5 (trail_activate_r=2.0 고정)
  trail_activate_r = 1.5 / 2.5 (chandelier_atr_mult=3.0 고정)

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v53_chandelier_recheck_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v53_chandelier_recheck
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

GRID = {
    "chand2_5": {**vcp.JPEX_V10_PARAMS, "chandelier_atr_mult": 2.5},
    "chand2_75": {**vcp.JPEX_V10_PARAMS, "chandelier_atr_mult": 2.75},
    "chand3_25": {**vcp.JPEX_V10_PARAMS, "chandelier_atr_mult": 3.25},
    "chand3_5": {**vcp.JPEX_V10_PARAMS, "chandelier_atr_mult": 3.5},
    "trailact1_5": {**vcp.JPEX_V10_PARAMS, "trail_activate_r": 1.5},
    "trailact2_5": {**vcp.JPEX_V10_PARAMS, "trail_activate_r": 2.5},
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

print("\n=== 기준(V10, chand=3.0/trailact=2.0): 41.31%/-29.43%/1.404/191건 ===", flush=True)

out_path = Path(__file__).parent / "jpex_v53_chandelier_recheck_trades.json"
out_data = {"seed": SEED}
for key, r in results.items():
    out_data[f"{key}_curve"] = r["equityCurve"]
    out_data[f"{key}_trades"] = [
        {"code": t["code"], "entryDate": t["entryDate"], "exitDate": t["exitDate"], "pnlPct": t["pnlPct"],
         "exitReason": t.get("exitReason")}
        for t in r["trades"]
    ]
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out_data, f)
print(f"\n저장 완료: {out_path}", flush=True)
