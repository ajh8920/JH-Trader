# -*- coding: utf-8 -*-
"""25단계까지 소진하지 않은 네 가지 축(과열청산 제거, 리스크기반 사이징,
눌림 깊이 band 양방향)을 동시에 시험한다.

목적:
  overheat_days/overheat_gain_pct(진입 후 5일 내 +50%면 즉시 청산)가 혹시
  257720처럼 짧은 기간에 크게 튀는 대형 승자를 초반에 끊어버리고 있는 건
  아닌지 의심된다 - 꺼서 비교한다. position_sizing_mode="equal_weight"
  (현재)가 아니라 "risk"(변동성 역산 사이징)가 더 좋은 자본 배분을 낼
  수도 있다. 눌림 깊이(min/max_pullback_pct, 현재 3~8%)도 양방향으로
  재탐색한다 - 이미 1~12%대 밴드가 다른 라운드에서 실패했었지만, V6의
  최종 출구 조합 기준으로는 재검증한 적이 없다.

변형: JPEX_V6_PARAMS 베이스로
  - no_overheat: overheat_days=None, overheat_gain_pct=None
  - risk_sizing: position_sizing_mode="risk"
  - pullback_deep: min_pullback_pct=5.0, max_pullback_pct=15.0
  - pullback_shallow: min_pullback_pct=1.0, max_pullback_pct=5.0

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v12_misc_axes_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v12_misc_axes
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
    "no_overheat": {**vcp.JPEX_V6_PARAMS, "overheat_days": None, "overheat_gain_pct": None},
    "risk_sizing": {**vcp.JPEX_V6_PARAMS, "position_sizing_mode": "risk"},
    "pullback_deep": {**vcp.JPEX_V6_PARAMS, "min_pullback_pct": 5.0, "max_pullback_pct": 15.0},
    "pullback_shallow": {**vcp.JPEX_V6_PARAMS, "min_pullback_pct": 1.0, "max_pullback_pct": 5.0},
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

out_path = Path(__file__).parent / "jpex_v12_misc_axes_trades.json"
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
