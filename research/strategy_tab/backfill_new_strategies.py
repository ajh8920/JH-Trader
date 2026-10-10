# -*- coding: utf-8 -*-
"""전략탭에 빠져 있던 "APEX"(기본형)와 "와쳐 2.1"을 처음으로 측정해 strategy_specs.py에
새 항목으로 추가하기 위한 백테스트.

목적:
  사용자 요청("전략탭의 모든 전략들은 CAGR/MDD/CAGR-MDD/승률/손익비/알파/거래량을
  포함해야 한다")을 점검하던 중, 모의투자 탭에서는 선택 가능한 전략인데 전략탭에는
  항목 자체가 없는 것을 발견했다(APEX_PARAMS, WATCHER_V21_PARAMS). "앞으로 추가되는
  전략도 포함돼야 한다"는 요청과 같은 맥락이라 사용자에게 확인 후("추가 권장" 선택)
  이번에 같이 추가한다.

배경:
  backfill_metrics.py와 동일한 방식(run_vcp_backtest 한 번으로 CAGR/MDD/승률/
  손익비/알파/거래 전부 획득) - APEX 계열(apex_v2/apex_v3)과 같은 기간(2016-01-01,
  시드 5억원), 와쳐 계열(watcher)과 같은 기간(2017-01-01, 시드 5천만원)을 쓴다.

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/CAGR-MDD/승률/손익비/알파/평균보유일/거래 출력,
  research/strategy_tab/backfill_new_strategies_results.json에 저장.

사용법: python -m research.strategy_tab.backfill_new_strategies
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
# APEX_PARAMS(require_catalyst=True)가 실제로 작동하려면 이 데이터를 반드시
# 로드해서 넘겨야 한다 - 안 그러면 run_vcp_backtest가 require_catalyst를
# 조용히 무시한다(strategy_specs.py에 이미 기록된 바로 그 버그, 2026-10-09에
# 한 번 잡았지만 이 스크립트에서 재발했다 - 2026-10-10 재수정).
catalyst_dates_by_code = vcp.load_catalyst_dates(sorted((FUND_KR_DIR.parent / "disclosures_kr").glob("*.parquet")))
print(f"공시 촉매 보유 종목수: {len(catalyst_dates_by_code)}", flush=True)

TODAY = date.today().isoformat()
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
    "apex": (vcp.APEX_PARAMS, "2016-01-01", 500_000_000),
    "watcher_v21": (vcp.WATCHER_V21_PARAMS, "2017-01-01", 50_000_000),
}

results = {}
for key, (preset, start, seed) in RERUNS.items():
    kw = dict(preset)
    kw.pop("market", None)
    kw.pop("default_seed", None)
    kw["quarterly_rows_by_code"] = quarterly_rows
    vcp.MAX_POSITION_WEIGHT_PCT = kw.get("max_position_weight_pct", 30.0)
    print(f"=== {key} 실행 ({start}~{TODAY}, 시드 {seed:,}) ===", flush=True)
    t0 = time.time()
    r = vcp.run_vcp_backtest(
        "KR", start, TODAY, seed=seed, fetch_fn=fetch_fn, shares_map=shares_map,
        shareholder_rows_by_code=shareholder_rows, fundamentals_rows_by_code=fundamentals_rows,
        catalyst_dates_by_code=catalyst_dates_by_code, **kw)
    print(f"{key} 완료 ({round(time.time() - t0)}s)", flush=True)
    if "error" in r:
        print("오류:", r.get("error"), flush=True)
        continue
    years = (date.today() - date(*map(int, start.split("-")))).days / 365.25
    final_val = r["equityCurve"][-1]["value"]
    cagr = ((final_val / seed) ** (1 / years) - 1) * 100
    mdd = r.get("mddPct", 0)
    calmar = cagr / abs(mdd) if mdd else float("inf")
    trades = r.get("trades") or []
    out = {
        "CAGR": round(cagr, 2), "MDD": mdd, "CAGR_MDD": round(calmar, 3),
        "winRatePct": r.get("winRatePct"), "profitLossRatio": r.get("profitLossRatio"),
        "alphaPct": r.get("alphaPct"), "avgHoldDays": r.get("avgHoldDays"), "tradeCount": len(trades),
        "tradesPerYear": round(len(trades) / years, 1), "period": f"{start}~{TODAY}",
    }
    results[key] = out
    print(f"[{key}] {out}", flush=True)
    save_trades(key, trades)

out_path = Path(__file__).parent / "backfill_new_strategies_results.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n저장 완료: {out_path}", flush=True)
