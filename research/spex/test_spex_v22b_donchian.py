# -*- coding: utf-8 -*-
"""SPEX 22단계 후속 - entry_mode="donchian" 단독 재실행(22단계 세션 중단으로
미완료 부분만). test_spex_v22_entry_mode.py와 동일 조건.
사용법: python -m research.spex.test_spex_v22b_donchian
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

BASE = {**vcp.SPEX_V4_PARAMS}
kw = dict(BASE)
kw.pop("market", None)
kw.pop("default_seed", None)
kw["entry_mode"] = "donchian"
kw["donchian_period"] = 20
kw["quarterly_rows_by_code"] = quarterly_rows
vcp.MAX_POSITION_WEIGHT_PCT = kw.get("max_position_weight_pct", 30.0)

years_span = (date.today() - date(2016, 1, 1)).days / 365.25
t0 = time.time()
r = vcp.run_vcp_backtest("KR", START, END, seed=SEED, fetch_fn=fetch_fn, shares_map=shares_map,
                          shareholder_rows_by_code=shareholder_rows, fundamentals_rows_by_code=fundamentals_rows, **kw)
print(f"완료 ({round(time.time() - t0)}s)", flush=True)
if "error" in r:
    print("오류:", r["error"], flush=True)
else:
    final_val = r["equityCurve"][-1]["value"]
    cagr = ((final_val / SEED) ** (1 / years_span) - 1) * 100
    mdd = r.get("mddPct", 0)
    calmar = cagr / abs(mdd) if mdd else float("inf")
    trades = r["trades"]
    wins = [t for t in trades if t["pnlPct"] > 0]
    wr = len(wins) / len(trades) * 100 if trades else 0
    print(f"[B_donchian] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} 승률 {wr:.1f}% 거래 {len(trades)}건", flush=True)
print("참고(SPEX_V4=pullback 기준): 23.14%/-19.89%/1.164/347건", flush=True)
