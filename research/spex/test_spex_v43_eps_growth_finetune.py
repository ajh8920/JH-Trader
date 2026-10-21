# -*- coding: utf-8 -*-
"""SPEX 43단계(APEX 베이스, 2026-10-22 야근ㄱㄱ) - 41단계에서 min_eps_growth_pct가
비단조(15%·25% 둘 다 기준 20%보다 낫고, 15%는 과최적화로 기각돼 25%를
SPEX_V7로 채택)임을 확인했다. 25% 근방을 더 촘촘히 훑어 진짜 국소최적점을
찾는다 - 18%(15%와 20% 사이, 15%가 기각된 이유인 쏠림 없이도 완화 효과를
볼 수 있는지)·22%(20%와 25% 사이)·28%/30%(25%보다 더 엄격).

변형(전부 SPEX_V7_PARAMS=min_eps_growth_pct 25에서 이 값만 재변경):
  A) min_eps_growth_pct=18.0
  B) min_eps_growth_pct=22.0
  C) min_eps_growth_pct=28.0
  D) min_eps_growth_pct=30.0

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v43_eps_growth_finetune_trades.json
사용법: python -m research.spex.test_spex_v43_eps_growth_finetune
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
    "A_eps_18": {**BASE7, "min_eps_growth_pct": 18.0},
    "B_eps_22": {**BASE7, "min_eps_growth_pct": 22.0},
    "C_eps_28": {**BASE7, "min_eps_growth_pct": 28.0},
    "D_eps_30": {**BASE7, "min_eps_growth_pct": 30.0},
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

print("참고(기준선, SPEX_V7=eps25): 26.01%/-19.37%/1.343/354건 PASS", flush=True)
print("참고(41단계 eps20=SPEX_V6): 25.97%/-19.91%/1.304/360건 PASS", flush=True)
print("참고(41단계 eps15=F, 과최적화로 기각): 27.88%/-19.91%/1.400/362건", flush=True)

out_path = Path(__file__).parent / "spex_v43_eps_growth_finetune_trades.json"
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
