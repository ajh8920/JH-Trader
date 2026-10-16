# -*- coding: utf-8 -*-
"""SPEX 10단계(9단계 후속, "야근ㄱㄱ") - 9단계에서 뜻밖의 발견: 비중=20%
고정일 때 슬롯7이 슬롯8(8단계, MDD -22.50% 위반)보다 오히려 MDD가 더
낮았다(-19.50%, 통과) - "슬롯 수가 늘면 분산되어 MDD가 준다"는 직관과
반대 방향. 슬롯8 축에서는 비중 18.5~20% 사이가 Calmar>=1/MDD<=-20%
경계였는데(8단계), 슬롯7에서는 이 경계가 어디인지 아직 모른다 - 슬롯7을
고정하고 비중 전구간을 다시 스캔해 이 새 국소최적점 주변의 진짜 경계를
찾는다(목표: Calmar>=1 AND MDD<=-20%를 지키면서 CAGR을 19.95%보다 더
끌어올릴 수 있는 비중이 있는지).

변형: max_positions=7 고정, entry_rank_top_n=80/rank_exit_top_n=100 고정,
  max_position_weight_pct = 18 / 19 / 20(9단계 재확인) / 21 / 22 / 23 / 24

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v10_slot7_weight_trades.json (equity curve + 거래 로그)
사용법: python -m research.spex.test_spex_v10_slot7_weight
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

BASE = {**vcp.SPEX_V1_PARAMS, "max_positions": 7, "entry_rank_top_n": 80, "rank_exit_top_n": 100}
GRID = {
    "w18": {**BASE, "max_position_weight_pct": 18},
    "w19": {**BASE, "max_position_weight_pct": 19},
    "w20": {**BASE, "max_position_weight_pct": 20},
    "w21": {**BASE, "max_position_weight_pct": 21},
    "w22": {**BASE, "max_position_weight_pct": 22},
    "w23": {**BASE, "max_position_weight_pct": 23},
    "w24": {**BASE, "max_position_weight_pct": 24},
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

print("\n=== 참고(슬롯7/w20, 9단계): 19.95%/-19.50%/1.023/354건 ===", flush=True)
print("=== 참고(슬롯8/w18.5, SPEX_V1): 17.55%/-17.40%/1.009/357건 ===", flush=True)

out_path = Path(__file__).parent / "spex_v10_slot7_weight_trades.json"
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
