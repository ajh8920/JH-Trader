# -*- coding: utf-8 -*-
"""JPEX + 미너비니 v2.1을 각각 한 번 돌리고 원본 자산곡선/거래를 JSON으로 저장한다.
이후 블렌드 비율 탐색은 이 파일을 읽어 재실행 없이 빠르게 한다."""
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
import screening_backtest as sb
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

fetch_jpex = cached_fetch_ohlc_history_batches("KR", include_delisted=True, max_age_hours=99999)
kw = dict(vcp.JPEX_PARAMS)
kw.pop("market", None); kw.pop("default_seed", None)
kw["quarterly_rows_by_code"] = quarterly_rows
vcp.MAX_POSITION_WEIGHT_PCT = kw.get("max_position_weight_pct", 30.0)

print("=== JPEX 실행 ===", flush=True)
t0 = time.time()
r1 = vcp.run_vcp_backtest("KR", START, END, seed=SEED, fetch_fn=fetch_jpex, shares_map=shares_map,
    shareholder_rows_by_code=shareholder_rows, fundamentals_rows_by_code=fundamentals_rows, **kw)
print(f"JPEX 완료 ({round(time.time()-t0)}s) trades={len(r1.get('trades', []))}", flush=True)

print("=== 미너비니 v2.1 실행 ===", flush=True)
p = sb.MINERVINI_V21_PARAMS
fetch_mv = cached_fetch_ohlc_history_batches(p["market"], include_delisted=True, max_age_hours=99999)
t0 = time.time()
r2 = sb.run_risk_managed_backtest(
    p["market"], p["strategy"], START, END,
    risk_pct=p["risk_pct"], atr_period=p["atr_period"], atr_mult=p["atr_mult"],
    breakeven_r=p["breakeven_r"], breakeven_lock_r=p.get("breakeven_lock_r", 0.0),
    trail_start_r=p["trail_start_r"], time_stop_days=p["time_stop_days"], dd_halt_pct=p["dd_halt_pct"],
    max_positions=p["max_positions"], seed=SEED, min_avg_trade_value=p["min_avg_trade_value"],
    market_regime_filter=p.get("market_regime_filter", False), fetch_fn=fetch_mv,
)
print(f"미너비니 v2.1 완료 ({round(time.time()-t0)}s) trades={len(r2.get('trades', []))}", flush=True)

if "error" in r1 or "error" in r2:
    print("오류:", r1.get("error"), r2.get("error"), flush=True)
    sys.exit(1)

out_path = Path(__file__).parent / "blend_curves.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({
        "seed": SEED,
        "jpex": r1["equityCurve"], "mv21": r2["equityCurve"],
        "jpex_trades": [{"pnlPct": t["pnlPct"]} for t in r1["trades"]],
        "mv21_trades": [{"pnlPct": t["pnlPct"]} for t in r2["trades"]],
    }, f)
print(f"저장 완료: {out_path}", flush=True)
