# -*- coding: utf-8 -*-
"""110단계(사용자 지시 갱신: "1. MDD -20% 이하가 최우선. 2. CAGR은 그
안에서 최대한 유지(무조건 유지 아님)") - 89~109단계의 "조건부" 개입
(서킷브레이커·비중축소·필터)이 전부 MDD를 거의 못 줄인 것(최선
-27.95%)과 달리, 조건 없이 매 진입의 체결 수량을 균일하게 줄이는
`position_size_scale`(신규)로 포트폴리오 집중도 자체를 낮춘다 - 이건
조건부가 아니라 항상 적용되므로 MDD가 산술적으로 보장되어 줄어든다.
freed-up 현금을 지수에 태우는 기존 엔진 기능 cash_equitize(JPEX는
한 번도 안 써봄, APEX 계열에서는 이미 검증됨)를 같이 켜서 CAGR 손실을
얼마나 상쇄할 수 있는지도 같이 본다.

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

변형: JPEX_V11_PARAMS 베이스로
  scale = 0.5 / 0.6 / 0.7 (cash_equitize 없음)
  scale = 0.5 / 0.6 / 0.7 + cash_equitize=True, equitize_max_pct=100

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v83_position_scale_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v83_position_scale
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
    "s05":    {**vcp.JPEX_V11_PARAMS, "position_size_scale": 0.5},
    "s06":    {**vcp.JPEX_V11_PARAMS, "position_size_scale": 0.6},
    "s07":    {**vcp.JPEX_V11_PARAMS, "position_size_scale": 0.7},
    "s05_eq": {**vcp.JPEX_V11_PARAMS, "position_size_scale": 0.5, "cash_equitize": True, "equitize_max_pct": 100.0},
    "s06_eq": {**vcp.JPEX_V11_PARAMS, "position_size_scale": 0.6, "cash_equitize": True, "equitize_max_pct": 100.0},
    "s07_eq": {**vcp.JPEX_V11_PARAMS, "position_size_scale": 0.7, "cash_equitize": True, "equitize_max_pct": 100.0},
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

print("\n=== 기준(V11, scale=1.0): 41.52%/-29.43%/1.411/192건 ===", flush=True)

out_path = Path(__file__).parent / "jpex_v83_position_scale_trades.json"
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
