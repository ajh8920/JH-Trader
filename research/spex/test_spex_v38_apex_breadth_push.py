# -*- coding: utf-8 -*-
"""SPEX 38단계(APEX 베이스, 2026-10-20) - 37단계에서 breadth_min_pct=35/
gate_entries_breadth_min_pct=30(기존 30/25보다 강화)이 유일하게 CAGR·
MDD 둘 다 개선시켰다(27.87/-24.89 -> 26.07/-21.70, Calmar 1.120->1.201).
가장 강력한 단일 레버로 확인됐으니 더 세게 밀어붙여 MDD<=-20%를 넘길
수 있는지, 그리고 슬롯8과 조합하면 CAGR을 25% 위로 되돌리면서도
MDD<=-20%를 유지할 수 있는지 확인.

변형:
  A) 슬롯7·비중20%, breadth 40/35(더 강화)
  B) 슬롯7·비중20%, breadth 45/40(훨씬 강화)
  C) 슬롯8·비중17%, breadth 35/30(37단계 최선 조합을 슬롯8에)
  D) 슬롯8·비중17%, breadth 40/35

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v38_apex_breadth_push_trades.json
사용법: python -m research.spex.test_spex_v38_apex_breadth_push
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

BASE7 = {**vcp.APEX_V2_PARAMS, "max_positions": 7, "max_position_weight_pct": 20.0}
BASE8 = {**vcp.APEX_V2_PARAMS, "max_positions": 8, "max_position_weight_pct": 17.0}

VARIANTS = {
    "A_slot7_breadth40_35": {**BASE7, "breadth_min_pct": 40.0, "gate_entries_breadth_min_pct": 35.0},
    "B_slot7_breadth45_40": {**BASE7, "breadth_min_pct": 45.0, "gate_entries_breadth_min_pct": 40.0},
    "C_slot8_breadth35_30": {**BASE8, "breadth_min_pct": 35.0, "gate_entries_breadth_min_pct": 30.0},
    "D_slot8_breadth40_35": {**BASE8, "breadth_min_pct": 40.0, "gate_entries_breadth_min_pct": 35.0},
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
for key, preset in VARIANTS.items():
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
    pass_ok = "PASS" if (cagr >= 25 and abs(mdd) <= 20) else ""
    print(f"[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} 승률 {wr:.1f}% 거래 {len(trades)}건 {pass_ok}", flush=True)

print("참고(37단계 C, 슬롯7·breadth35/30): 26.07%/-21.70%/1.201", flush=True)

out_path = Path(__file__).parent / "spex_v38_apex_breadth_push_trades.json"
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
print(f"저장 완료: {out_path}", flush=True)
