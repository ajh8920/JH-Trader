# -*- coding: utf-8 -*-
"""SPEX 41-b단계(APEX 베이스, 2026-10-22 야근ㄱㄱ) - 41단계에서 min_eps_growth_pct를
20%(APEX 기본)에서 15%로 완화한 F 변형이 27.88%/-19.91%/Calmar 1.400/362건으로
기준선(SPEX_V6: 25.97%/-19.91%/1.304/360건)을 CAGR·Calmar 모두에서 뚜렷하게
앞섰다. 채택 전 과최적화 검증(트레이드 diff) - 기준선과 F를 같은 실행에서
나란히 돌려 공통/배제/신규 거래를 비교한다.

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v41b_eps_growth15_diff_trades.json
사용법: python -m research.spex.test_spex_v41b_eps_growth15_diff
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

BASE7 = {**vcp.APEX_V2_PARAMS, "max_positions": 7, "max_position_weight_pct": 20.0,
         "breadth_min_pct": 30.0, "gate_entries_breadth_min_pct": 25.0,
         "evan_params": {"min_rs": 90.0}}

VARIANTS = {
    "baseline_SPEX_V6": BASE7,
    "F_eps_growth_15": {**BASE7, "min_eps_growth_pct": 15.0},
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

# ─── 트레이드 diff ──────────────────────────────────────────────────────────
if "baseline_SPEX_V6" in results and "F_eps_growth_15" in results:
    def key_of(t):
        return (t["code"], t["entryDate"])

    base_trades = {key_of(t): t for t in results["baseline_SPEX_V6"]["trades"]}
    new_trades = {key_of(t): t for t in results["F_eps_growth_15"]["trades"]}
    common = set(base_trades) & set(new_trades)
    excluded = set(base_trades) - set(new_trades)  # 기준선엔 있었는데 F에선 사라진 거래
    added = set(new_trades) - set(base_trades)      # F에서만 새로 생긴 거래

    excluded_pnl = sum(base_trades[k]["pnlPct"] for k in excluded)
    added_pnl = sum(new_trades[k]["pnlPct"] for k in added)
    added_years = sorted({k[1][:4] for k in added})
    excluded_years = sorted({k[1][:4] for k in excluded})

    print(f"\n=== 트레이드 diff(기준선 vs F_eps_growth_15) ===", flush=True)
    print(f"공통진입 {len(common)}건", flush=True)
    print(f"기준선전용(배제) {len(excluded)}건, pnl합 {excluded_pnl:+.1f}%p, 연도분포 {excluded_years}", flush=True)
    print(f"F전용(신규) {len(added)}건, pnl합 {added_pnl:+.1f}%p, 연도분포 {added_years}", flush=True)
    if added:
        top_added = sorted(new_trades[k] for k in added) if False else sorted(
            (new_trades[k] for k in added), key=lambda t: t["pnlPct"], reverse=True)[:5]
        print("신규 거래 중 pnl 상위 5건:", flush=True)
        for t in top_added:
            print(f"  {t['code']} {t['entryDate']}~{t['exitDate']} {t['pnlPct']:+.2f}% ({t.get('exitReason')})", flush=True)

out_path = Path(__file__).parent / "spex_v41b_eps_growth15_diff_trades.json"
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
