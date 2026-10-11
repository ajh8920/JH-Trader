# -*- coding: utf-8 -*-
"""51단계: "CAGR 50% 이상이 되도록 계속 진행하라"는 요청에 V8(breadth_min_pct=30,
CAGR 40.86%) 위에서 추가 개선 여지를 찾는다. breadth가 국면 신호 자체를
바꿨으니, 그 위에서 regime_exit_min_r(국면상실청산 R배수 문턱)의 최적값이
달라졌을 수 있다(46~48단계의 r_min 재그리드는 breadth 적용 전 기준이었다).
또한 breadth_min_pct=30이 20/25/30/35/40/50 6점 격자에서 나온 값이라 28/32로
더 세밀하게 확인한다.

변형: JPEX_V8_PARAMS 베이스로
  - v8_rmin1.1 / v8_rmin1.15 / v8_rmin1.35: r_min 재튜닝
  - breadth28 / breadth32: breadth 문턱 미세조정

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v35_v8_combos_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v35_v8_combos
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
    "v8_rmin1.1": {**vcp.JPEX_V8_PARAMS, "regime_exit_min_r": 1.1},
    "v8_rmin1.15": {**vcp.JPEX_V8_PARAMS, "regime_exit_min_r": 1.15},
    "v8_rmin1.35": {**vcp.JPEX_V8_PARAMS, "regime_exit_min_r": 1.35},
    "breadth28": {**vcp.JPEX_V7_PARAMS, "breadth_min_pct": 28.0},
    "breadth32": {**vcp.JPEX_V7_PARAMS, "breadth_min_pct": 32.0},
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

print("\n=== 기준(V8): 40.86%/-32.47%/1.258/239건 ===", flush=True)

out_path = Path(__file__).parent / "jpex_v35_v8_combos_trades.json"
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
