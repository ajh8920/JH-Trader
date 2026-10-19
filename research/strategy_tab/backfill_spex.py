# -*- coding: utf-8 -*-
"""전략탭("전략" 탭, strategy_specs.py)에 SPEX(V1~V4)만 "매매내역"
(tradesKey)과 "알파"가 빠져 있던 것을 발견해 채우기 위한 백테스트.

배경: backfill_new_strategies.py와 동일한 방식(run_vcp_backtest 한 번으로
CAGR/MDD/승률/손익비/알파/거래 전부 획득 - alphaPct는 run_vcp_backtest가
이미 계산해 돌려주는 값이라 따로 구현할 필요 없음) - JPEX 계열은 이미
tradesKey가 있는데 SPEX는 strategy_specs.py에 결과만 수기로 옮겨 적고
tradesKey/알파/총수익률/평균보유일을 빠뜻린 상태였다.

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/CAGR-MDD/승률/손익비/알파/평균보유일/거래 출력,
  research/strategy_tab/backfill_spex_results.json에 저장,
  strategy_trades/spex_v1~v4.json에 매매내역 저장.

사용법: python -m research.strategy_tab.backfill_spex
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


def save_trades(key, trades):
    rows = sorted(trades, key=lambda t: t["entryDate"])
    out = [{
        "code": t["code"], "name": t.get("name"), "entryDate": t["entryDate"],
        "entryPrice": t.get("entryPrice"), "exitDate": t["exitDate"], "exitPrice": t.get("exitPrice"),
        "pnlPct": t["pnlPct"], "exitReason": t.get("exitReason"), "holdDays": t.get("holdDays"),
    } for t in rows]
    with open(TRADES_DIR / f"{key}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"  매매내역 {len(out)}건 저장: strategy_trades/{key}.json", flush=True)


RERUNS = {
    "spex_v1": vcp.SPEX_V1_PARAMS,
    "spex_v2": vcp.SPEX_V2_PARAMS,
    "spex_v3": vcp.SPEX_V3_PARAMS,
    "spex_v4": vcp.SPEX_V4_PARAMS,
}

years = (date.today() - date(2016, 1, 1)).days / 365.25
results = {}
for key, preset in RERUNS.items():
    kw = dict(preset)
    kw.pop("market", None)
    kw.pop("default_seed", None)
    kw["quarterly_rows_by_code"] = quarterly_rows
    vcp.MAX_POSITION_WEIGHT_PCT = kw.get("max_position_weight_pct", 30.0)
    print(f"=== {key} 실행 ({START}~{TODAY}, 시드 {SEED:,}) ===", flush=True)
    t0 = time.time()
    r = vcp.run_vcp_backtest(
        "KR", START, TODAY, seed=SEED, fetch_fn=fetch_fn, shares_map=shares_map,
        shareholder_rows_by_code=shareholder_rows, fundamentals_rows_by_code=fundamentals_rows, **kw)
    print(f"{key} 완료 ({round(time.time() - t0)}s)", flush=True)
    if "error" in r:
        print("오류:", r.get("error"), flush=True)
        continue
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
    results[key] = out
    print(f"[{key}] {out}", flush=True)
    save_trades(key, trades)

out_path = Path(__file__).parent / "backfill_spex_results.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n저장 완료: {out_path}", flush=True)
