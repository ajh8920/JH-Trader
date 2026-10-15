# -*- coding: utf-8 -*-
"""104단계(사용자 지시: "CAGR은 최대한 유지하면서 MDD를 -20% 이하로 낮추는
방향 찾기") - V11 실측 MDD(-29.43%)가 전부 2016년, 그중에서도 7~10월
initialStop 6연속(-4.5~-7.9%)에서 나왔다(102단계 연도별 분해에서 확인).
섹터상한(92단계)·어닝블랙아웃(93단계)처럼 미리 정한 달력/카테고리 규칙은
전부 역효과였지만, 이번엔 "실제로 연속 손실이 발생했을 때만" 반응하는
서킷브레이커(loss_streak_halt_count/days, 신규)를 시험한다.

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

변형: JPEX_V11_PARAMS 베이스로
  halt_count=3, halt_days = 5/10/20
  halt_count=4, halt_days = 10/20
  halt_count=2, halt_days = 10

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v79_loss_streak_halt_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v79_loss_streak_halt
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
    "c3_h5":  {**vcp.JPEX_V11_PARAMS, "loss_streak_halt_count": 3, "loss_streak_halt_days": 5},
    "c3_h10": {**vcp.JPEX_V11_PARAMS, "loss_streak_halt_count": 3, "loss_streak_halt_days": 10},
    "c3_h20": {**vcp.JPEX_V11_PARAMS, "loss_streak_halt_count": 3, "loss_streak_halt_days": 20},
    "c4_h10": {**vcp.JPEX_V11_PARAMS, "loss_streak_halt_count": 4, "loss_streak_halt_days": 10},
    "c4_h20": {**vcp.JPEX_V11_PARAMS, "loss_streak_halt_count": 4, "loss_streak_halt_days": 20},
    "c2_h10": {**vcp.JPEX_V11_PARAMS, "loss_streak_halt_count": 2, "loss_streak_halt_days": 10},
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

print("\n=== 기준(V11, 서킷브레이커 비활성): 41.52%/-29.43%/1.411/192건 ===", flush=True)

out_path = Path(__file__).parent / "jpex_v79_loss_streak_halt_trades.json"
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
