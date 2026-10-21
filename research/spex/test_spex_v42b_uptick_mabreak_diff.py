# -*- coding: utf-8 -*-
"""SPEX 42-b단계 - 42단계에서 pullback_min_uptick_pct=0.5(기본 1.0%보다 완화)가
28.85%/-19.78%/Calmar 1.458/419건으로 SPEX_V7(26.01%/-19.37%/1.343/354건) 대비
가장 큰 폭(+2.84%p CAGR, 거래 65건 증가)으로 앞섰다. 거래 수 증가폭이 커서
(41단계 F가 쏠림으로 기각된 사례처럼) 소수 거래 의존 여부를 반드시 확인해야
한다. ma_break_consec_days=2(G, +0.08%p 소폭 개선)도 같이 대조한다.

기준선(SPEX_V7) 트레이드는 41-c에서 이미 저장한 것을 재사용(결정적 재현
2회 확인됨 - 26.01%/-19.37%/1.343/354건 바이트 단위 동일).

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v42b_uptick_mabreak_diff_trades.json
사용법: python -m research.spex.test_spex_v42b_uptick_mabreak_diff
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

BASE7 = vcp.SPEX_V7_PARAMS
VARIANTS = {
    "H_uptick_0.5": {**BASE7, "pullback_min_uptick_pct": 0.5},
    "G_mabreak_2": {**BASE7, "ma_break_consec_days": 2},
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

with open(Path(__file__).parent / "spex_v41c_eps_growth25_diff_trades.json", encoding="utf-8") as f:
    prev = json.load(f)
base_trades_list = prev["E_eps_growth_25_trades"]


def key_of(t):
    return (t["code"], t["entryDate"])


base_trades = {key_of(t): t for t in base_trades_list}

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

    new_trades = {key_of(t): t for t in trades}
    common = set(base_trades) & set(new_trades)
    excluded = set(base_trades) - set(new_trades)
    added = set(new_trades) - set(base_trades)
    excluded_pnl = sum(base_trades[k]["pnlPct"] for k in excluded)
    added_pnl = sum(new_trades[k]["pnlPct"] for k in added)
    added_years = sorted({k[1][:4] for k in added})
    excluded_years = sorted({k[1][:4] for k in excluded})
    print(f"  diff(vs SPEX_V7): 공통 {len(common)}건, 배제 {len(excluded)}건(pnl {excluded_pnl:+.1f}%p, {excluded_years}), "
          f"신규 {len(added)}건(pnl {added_pnl:+.1f}%p, {added_years})", flush=True)
    if added:
        top_added = sorted((new_trades[k] for k in added), key=lambda t: t["pnlPct"], reverse=True)[:8]
        top_sum = sum(t["pnlPct"] for t in top_added[:3])
        print(f"  신규 상위3건 합 {top_sum:+.1f}%p (신규 순효과 {added_pnl:+.1f}%p 대비 비중 "
              f"{(top_sum/added_pnl*100 if added_pnl else float('nan')):.0f}%)", flush=True)
        for t in top_added:
            print(f"    {t['code']} {t['entryDate']}~{t['exitDate']} {t['pnlPct']:+.2f}% ({t.get('exitReason')})", flush=True)

out_path = Path(__file__).parent / "spex_v42b_uptick_mabreak_diff_trades.json"
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
