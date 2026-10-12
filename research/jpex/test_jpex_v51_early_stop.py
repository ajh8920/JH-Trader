# -*- coding: utf-8 -*-
"""63단계: early_stop_days/early_stop_pct(진입 후 N거래일 내 손실이 문턱 이하면
즉시 청산 - "Early Stop" 재현)는 과거 APEX_STAGE3_EXIT_FRIEND_PARAMS(73차,
지인 청산 이력 76건 기준)에서 이미 검증됐던 파라미터인데 JPEX_V10_PARAMS에는
빠져 있다(overheat_days만 남아있고 early_stop_days=None). 이번 513건짜리 더 큰
표본에서는 "Early Stop" 평균손익이 -6.2%로 과거 가정한 -8.0%보다 타이트하다 -
V10 위에서 재시험한다.

변형: JPEX_V10_PARAMS 베이스로
  early_stop_days=3, early_stop_pct=-8.0(과거 APEX 값 그대로)
  early_stop_days=3, early_stop_pct=-6.2(지인 평균손익에 맞춤)
  early_stop_days=2, early_stop_pct=-6.2
  early_stop_days=4, early_stop_pct=-6.2

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래/earlyStop 발동건수+평균손익,
  research/jpex/jpex_v51_early_stop_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v51_early_stop
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
    "early3_neg8": {**vcp.JPEX_V10_PARAMS, "early_stop_days": 3, "early_stop_pct": -8.0},
    "early3_neg62": {**vcp.JPEX_V10_PARAMS, "early_stop_days": 3, "early_stop_pct": -6.2},
    "early2_neg62": {**vcp.JPEX_V10_PARAMS, "early_stop_days": 2, "early_stop_pct": -6.2},
    "early4_neg62": {**vcp.JPEX_V10_PARAMS, "early_stop_days": 4, "early_stop_pct": -6.2},
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
    es_trades = [t for t in trades if t.get("exitReason") == "earlyStop"]
    es_avg = sum(t["pnlPct"] for t in es_trades) / len(es_trades) if es_trades else 0
    print(f"\n[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 손익비 {r.get('profitLossRatio')} 거래 {len(trades)}건"
          f"(연 {len(trades)/years_span:.1f}건) earlyStop건수 {len(es_trades)} "
          f"평균손익 {es_avg:.1f}%", flush=True)

print("\n=== 기준(V10, early_stop 없음): 41.31%/-29.43%/1.404/191건 ===", flush=True)

out_path = Path(__file__).parent / "jpex_v51_early_stop_trades.json"
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
print(f"\n저장 완료: {out_path}", flush=True)
