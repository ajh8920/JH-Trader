# -*- coding: utf-8 -*-
"""마지막 두 미시험 축 - 유동성 문턱(min_avg_trade_value) 민감도와, 국면
판정 지수를 코스피 대신 코스닥으로 바꿔보는 실험.

목적: "다 밀어붙이는" 요청에 따라 지금까지 손대지 않은 두 축을 마무리한다.
JPEX는 min_avg_trade_value=1억원(평균 일 거래대금 하한)을 쓰는데 이
문턱의 민감도를 본 적이 없다. 국면 판정(regime_ok)은 항상 코스피
(^KS11)만 쓰는데, JPEX가 실제로 사고파는 종목은 코스피/코스닥 섞여
있으므로 코스닥 지수로 국면을 판정하면 다를 수 있다는 가설(가벼운
monkey-patch로 BENCHMARK_TICKER["KR"]을 임시로 바꿔 테스트 - 엔진에
영구 파라미터를 추가하지 않고 이 스크립트 안에서만 전역을 바꾸고
되돌린다).

변형: JPEX_V7_PARAMS 베이스로
  - liquidity_half: min_avg_trade_value 1억->5천만(완화)
  - liquidity_double: min_avg_trade_value 1억->2억(강화)
  - kosdaq_regime: 국면 판정 지수를 코스피(^KS11)->코스닥(^KQ11)

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v21_liquidity_regime_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v21_liquidity_regime
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
import screening_backtest as sb
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
    "liquidity_half": ({**vcp.JPEX_V7_PARAMS, "min_avg_trade_value": 50_000_000}, None),
    "liquidity_double": ({**vcp.JPEX_V7_PARAMS, "min_avg_trade_value": 200_000_000}, None),
    "kosdaq_regime": ({**vcp.JPEX_V7_PARAMS}, "^KQ11"),
}


def run(preset, label, regime_ticker_override=None):
    kw = dict(preset)
    kw.pop("market", None)
    kw.pop("default_seed", None)
    kw["quarterly_rows_by_code"] = quarterly_rows
    vcp.MAX_POSITION_WEIGHT_PCT = kw.get("max_position_weight_pct", 30.0)
    # _fetch_index_series_local(국면 판정용 지수 시계열)은 vcp_strategy.py가
    # screening_backtest.py에서 그대로 가져와 쓰는 함수라, 그 함수 본문이 참조하는
    # BENCHMARK_TICKER는 screening_backtest 모듈 쪽 전역이다(vcp_strategy.py도
    # 같은 이름의 딕셔너리를 따로 갖고 있지만 알파 계산에만 쓰여 서로 다른 객체 -
    # 거길 바꾸면 이 함수에는 영향이 없다).
    original_ticker = sb.BENCHMARK_TICKER.get("KR")
    if regime_ticker_override:
        sb.BENCHMARK_TICKER["KR"] = regime_ticker_override
    print(f"=== {label} 실행 (국면지수={sb.BENCHMARK_TICKER.get('KR')}) ===", flush=True)
    t0 = time.time()
    try:
        r = vcp.run_vcp_backtest(
            "KR", START, END, seed=SEED, fetch_fn=fetch_fn, shares_map=shares_map,
            shareholder_rows_by_code=shareholder_rows, fundamentals_rows_by_code=fundamentals_rows, **kw)
    finally:
        sb.BENCHMARK_TICKER["KR"] = original_ticker
    print(f"{label} 완료 ({round(time.time() - t0)}s) trades={len(r.get('trades', []))}", flush=True)
    return r


years_span = (date.today() - date(2016, 1, 1)).days / 365.25
results = {}
for key, (preset, regime_override) in GRID.items():
    r = run(preset, key, regime_override)
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

out_path = Path(__file__).parent / "jpex_v21_liquidity_regime_trades.json"
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
