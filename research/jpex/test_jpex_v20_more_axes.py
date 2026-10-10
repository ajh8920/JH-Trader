# -*- coding: utf-8 -*-
""""다 밀어붙이는 방향으로" 요청에 따라 아직 안 건드린 네 가지 축을 동시에
시험한다 - 성장률 하드 필터 제거(품질 블렌드로 대체), MA이탈 청산 타이밍
양방향, 눌림목 기준 이동평균 기간.

목적: quality_rank_weight(27단계 성공)가 재무 품질을 "순위"로 반영하는데,
min_revenue_growth=15%/min_eps_growth_pct=20%는 여전히 "하드 필터"로
같이 걸려 있다 - 이제 와서 보면 중복/과잉 제약일 수 있다(29단계에서
성장률을 "강화"하는 건 실패했지만 "제거"는 아직 안 해봤다). ma_break_
period/consec_days(현재 50일/4일)·pullback_ma_period(현재 20일)도 V7
최종 조합 기준으로는 재탐색한 적이 없다.

변형: JPEX_V7_PARAMS 베이스로
  - no_growth_filter: min_revenue_growth=None, min_eps_growth_pct=None
    (하드 필터 제거, quality_rank_weight=0.3가 이미 매출성장을 품질점수
    5개 항목 중 하나로 보고 있어 완전히 필터가 없어지는 건 아님)
  - ma_break_tight: ma_break_period=30, ma_break_consec_days=3(더 빨리 끊음)
  - ma_break_loose: ma_break_period=100, ma_break_consec_days=6(더 오래 버팀)
  - pullback_ma10: pullback_ma_period=10(눌림목 기준 이동평균을 20일->10일)

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v20_more_axes_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v20_more_axes
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
    "no_growth_filter": {**vcp.JPEX_V7_PARAMS, "min_revenue_growth": None, "min_eps_growth_pct": None},
    "ma_break_tight": {**vcp.JPEX_V7_PARAMS, "ma_break_period": 30, "ma_break_consec_days": 3},
    "ma_break_loose": {**vcp.JPEX_V7_PARAMS, "ma_break_period": 100, "ma_break_consec_days": 6},
    "pullback_ma10": {**vcp.JPEX_V7_PARAMS, "pullback_ma_period": 10},
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

out_path = Path(__file__).parent / "jpex_v20_more_axes_trades.json"
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
