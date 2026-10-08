# -*- coding: utf-8 -*-
"""JPEX(v64) + APEX 68차(고CAGR/저Calmar)를 각각 한 번 돌리고 자산곡선을 저장한다.
CAGR을 끌어올리는 블렌드 비율 탐색용(거래량 블렌드와 같은 기법, 대상만 교체)."""
import io, json, sys, time
from datetime import date
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
PROJECT_DIR = Path(r"c:\Users\ajh89\Desktop\AI 공부\000.Project\stock-tracker-py")
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
shareholder_rows = vcp.load_shareholder_rows([SHAREHOLDER_KR_DIR / "majorstock_1of2.parquet", SHAREHOLDER_KR_DIR / "majorstock_2of2.parquet"])
with app.app_context():
    fundamentals_rows = vcp.load_fundamentals_rows(KrFundamental)
quarter_paths = sorted((FUND_KR_DIR.parent / "kr_quarter").glob("*.parquet"))
quarterly_rows = vcp.load_quarterly_rows(quarter_paths)

START, END = "2016-01-01", date.today().isoformat()
SEED = 500_000_000
fetch_fn = cached_fetch_ohlc_history_batches("KR", include_delisted=True, max_age_hours=99999)

def run(preset, label):
    kw = dict(preset)
    kw.pop("market", None); kw.pop("default_seed", None)
    kw["quarterly_rows_by_code"] = quarterly_rows
    vcp.MAX_POSITION_WEIGHT_PCT = kw.get("max_position_weight_pct", 30.0)
    print(f"=== {label} 실행 ===", flush=True)
    t0 = time.time()
    r = vcp.run_vcp_backtest("KR", START, END, seed=SEED, fetch_fn=fetch_fn, shares_map=shares_map,
        shareholder_rows_by_code=shareholder_rows, fundamentals_rows_by_code=fundamentals_rows, **kw)
    print(f"{label} 완료 ({round(time.time()-t0)}s) trades={len(r.get('trades', []))}", flush=True)
    return r

r1 = run(vcp.JPEX_PARAMS, "JPEX(v64)")
r2 = run(vcp.APEX_STAGE3_BEST68_PARAMS, "APEX 68차")

if "error" in r1 or "error" in r2:
    print("오류:", r1.get("error"), r2.get("error"), flush=True)
    sys.exit(1)

out_path = Path(__file__).parent / "blend_curves_68cha.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({
        "seed": SEED,
        "jpex": r1["equityCurve"], "apex68": r2["equityCurve"],
        "jpex_trades": [{"pnlPct": t["pnlPct"]} for t in r1["trades"]],
        "apex68_trades": [{"pnlPct": t["pnlPct"]} for t in r2["trades"]],
    }, f)
print(f"저장 완료: {out_path}", flush=True)
