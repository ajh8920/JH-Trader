# -*- coding: utf-8 -*-
"""전략탭에 SPEX_V5(31단계, rate_etf_parking 채택) 등록용 지표/매매내역
백필 - backfill_spex.py와 동일한 방식.

사용법: python -m research.strategy_tab.backfill_spex_v5
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

TODAY = date.today().isoformat()
START = "2016-01-01"
SEED = 500_000_000
fetch_fn = cached_fetch_ohlc_history_batches("KR", include_delisted=True, max_age_hours=99999)
TRADES_DIR = PROJECT_DIR / "strategy_trades"
TRADES_DIR.mkdir(exist_ok=True)

kw = dict(vcp.SPEX_V5_PARAMS)
kw.pop("market", None)
kw.pop("default_seed", None)
kw["quarterly_rows_by_code"] = quarterly_rows
vcp.MAX_POSITION_WEIGHT_PCT = kw.get("max_position_weight_pct", 30.0)

years = (date.today() - date(2016, 1, 1)).days / 365.25
t0 = time.time()
r = vcp.run_vcp_backtest("KR", START, TODAY, seed=SEED, fetch_fn=fetch_fn, shares_map=shares_map,
                          shareholder_rows_by_code=shareholder_rows, fundamentals_rows_by_code=fundamentals_rows, **kw)
print(f"완료 ({round(time.time() - t0)}s)", flush=True)
if "error" in r:
    print("오류:", r["error"], flush=True)
else:
    final_val = r["equityCurve"][-1]["value"]
    cagr = ((final_val / SEED) ** (1 / years) - 1) * 100
    total_return = (final_val / SEED - 1) * 100
    mdd = r.get("mddPct", 0)
    calmar = cagr / abs(mdd) if mdd else float("inf")
    trades = r.get("trades") or []
    out = {
        "CAGR": round(cagr, 2), "totalReturnPct": round(total_return, 2), "MDD": mdd,
        "CAGR_MDD": round(calmar, 3), "winRatePct": r.get("winRatePct"),
        "profitLossRatio": r.get("profitLossRatio"), "alphaPct": r.get("alphaPct"),
        "avgHoldDays": r.get("avgHoldDays"), "tradeCount": len(trades),
        "tradesPerYear": round(len(trades) / years, 1), "period": f"{START}~{TODAY}",
    }
    print("[spex_v5]", out, flush=True)
    rows = sorted(trades, key=lambda t: t["entryDate"])
    trades_out = [{
        "code": t["code"], "name": t.get("name"), "entryDate": t["entryDate"],
        "entryPrice": t.get("entryPrice"), "exitDate": t["exitDate"], "exitPrice": t.get("exitPrice"),
        "pnlPct": t["pnlPct"], "exitReason": t.get("exitReason"), "holdDays": t.get("holdDays"),
    } for t in rows]
    with open(TRADES_DIR / "spex_v5.json", "w", encoding="utf-8") as f:
        json.dump(trades_out, f, ensure_ascii=False)
    print(f"매매내역 {len(trades_out)}건 저장: strategy_trades/spex_v5.json", flush=True)
    with open(Path(__file__).parent / "backfill_spex_v5_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
