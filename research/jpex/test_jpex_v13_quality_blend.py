# -*- coding: utf-8 -*-
"""26단계까지 포지션 사이징/국면적응형/진입 엄격도/진입 방식/출구 타이밍/눌림
밴드를 전부 시험했지만 CAGR을 못 올렸다. 마지막 미시험 축 - entry_rank_top_n
순위 계산에 기본적 분석 품질점수를 섞는 quality_rank_weight, 그리고
밸류에이션/수익성 필터(max_per/min_roe) 추가를 시험한다.

목적:
  지금까지는 "그날 RS 순위 Top40 이내"(순수 기술적 모멘텀)로만 후보를
  줄 세웠다. quality_rank_weight(기존 엔진 파라미터, 지금까지 JPEX에서는
  쓴 적 없음 - 0~1 사이, RS와 기본적 분석 품질점수를 섞는 비율)를 쓰면
  "모멘텀은 있지만 재무도 받쳐주는" 종목을 우선시해 257720류 대형 승자
  (지속적으로 우상향하는 실적 기반 종목)를 더 잘 걸러낼 수 있다는 가설.
  max_per/min_roe는 엔진에 이미 있는 밸류에이션/수익성 필터 - "너무 비싸게
  거래되는" 또는 "수익성이 받쳐주지 않는" 모멘텀 종목을 배제한다.

변형: JPEX_V6_PARAMS 베이스로
  - quality30: quality_rank_weight=0.3
  - quality50: quality_rank_weight=0.5
  - max_per30: max_per=30.0(밸류에이션 상한)
  - min_roe10: min_roe=10.0(수익성 하한)

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v13_quality_blend_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v13_quality_blend
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
    "quality30": {**vcp.JPEX_V6_PARAMS, "quality_rank_weight": 0.3},
    "quality50": {**vcp.JPEX_V6_PARAMS, "quality_rank_weight": 0.5},
    "max_per30": {**vcp.JPEX_V6_PARAMS, "max_per": 30.0},
    "min_roe10": {**vcp.JPEX_V6_PARAMS, "min_roe": 10.0},
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

out_path = Path(__file__).parent / "jpex_v13_quality_blend_trades.json"
out_data = {"seed": SEED}
for key, r in results.items():
    out_data[f"{key}_curve"] = r["equityCurve"]
    out_data[f"{key}_trades"] = [
        {"code": t["code"], "entryDate": t["entryDate"], "exitDate": t["exitDate"], "pnlPct": t["pnlPct"]}
        for t in r["trades"]
    ]
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out_data, f)
print(f"\n저장 완료: {out_path}", flush=True)
