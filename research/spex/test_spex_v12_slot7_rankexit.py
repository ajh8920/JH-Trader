# -*- coding: utf-8 -*-
"""SPEX 12단계(11단계 후속, "야근ㄱㄱ") - 10~11단계에서 슬롯7·비중21.2%가
CAGR 20.55%/MDD -19.50%/Calmar 1.054로 지금까지 최고 후보가 됐지만,
비중 축은 21.2%에서 벽에 막혔다(21.4%부터 MDD 즉시 위반). rank_exit_top_n
=100은 6~7단계에서 슬롯8·비중18.5% 기준으로 튜닝한 값이라, 슬롯7·비중
21%라는 완전히 다른 운영점에서도 여전히 최적인지 확인한 적이 없다 -
이 축을 슬롯7 기준으로 재탐색해 벽을 넘을 여지가 있는지 본다.

변형: max_positions=7, entry_rank_top_n=80, max_position_weight_pct=21.2 고정,
  rank_exit_top_n = 85 / 90 / 95 / 105 / 110 / 115

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v12_slot7_rankexit_trades.json (equity curve + 거래 로그)
사용법: python -m research.spex.test_spex_v12_slot7_rankexit
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

BASE = {**vcp.SPEX_V1_PARAMS, "max_positions": 7, "entry_rank_top_n": 80, "max_position_weight_pct": 21.2}
GRID = {
    "re85": {**BASE, "rank_exit_top_n": 85},
    "re90": {**BASE, "rank_exit_top_n": 90},
    "re95": {**BASE, "rank_exit_top_n": 95},
    "re105": {**BASE, "rank_exit_top_n": 105},
    "re110": {**BASE, "rank_exit_top_n": 110},
    "re115": {**BASE, "rank_exit_top_n": 115},
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

print("\n=== 참고(슬롯7/w21.2/re100, 11단계 최고): 20.55%/-19.50%/1.054/353건 ===", flush=True)

out_path = Path(__file__).parent / "spex_v12_slot7_rankexit_trades.json"
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
