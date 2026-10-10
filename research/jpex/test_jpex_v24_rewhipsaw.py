# -*- coding: utf-8 -*-
"""40단계: "수익률이 떨어지는 연도들을 월단위로 분석해 수익률을 높이라"는
요청(31~33단계)에서 2019/2022/2025의 공통 증상을 "몬스터 트레이드는 이미 잘
잡히는데 작은 손실이 반복돼 수익을 깎아먹는다"로 결론지었다. strategy_trades/
jpex_v7.json을 code별로 묶어 다시 살펴보니, 그 "작은 손실 반복"의 상당수가
같은 종목을 거의 같은 자리에서 반복 재진입하다 반복 손절당하는 휩쏘(whipsaw)
패턴이었다 - 예: 2022년 085370은 4/15~4/21 사이 4번 재진입(+3.72%→-0.8%→
+1.33%→-1.14%), 004690은 5~12월 사이 5번 재진입(대부분 -0.17%~-1.73% 소액
손실). quality_rank_weight(27단계)처럼 "후보 품질"을 건드리는 축은 이미 많이
탐색됐지만, "같은 피벗에서의 반복 재진입 자체를 막는" 축은 JPEX에 한 번도
적용된 적이 없다 - require_new_pivot(39~40차에 다른 전략 문제로 추가된 기존
엔진 파라미터, 직전 진입 피벗보다 더 높은 새 고점이 아니면 재진입 금지)이
정확히 이 패턴을 겨냥한다. 추가로 분기 EPS "가속"을 요구하는
require_eps_acceleration(기존 엔진 파라미터, 지금까지 JPEX는 min_eps_growth_pct
=20%만 쓰고 가속 조건은 켠 적이 없음)도 "성장은 있지만 식어가는" 종목을
걸러내 같은 방향으로 작용할 수 있어 같이 시험한다.

변형: JPEX_V7_PARAMS 베이스로
  - new_pivot: require_new_pivot=True (여유 마진 0%)
  - new_pivot_margin2: require_new_pivot=True, new_pivot_margin_pct=2.0(더 엄격)
  - eps_accel: require_eps_acceleration=True
  - combo: new_pivot + eps_accel 둘 다

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래 + 2019/2022/2025 요약,
  research/jpex/jpex_v24_rewhipsaw_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v24_rewhipsaw
"""
import io
import json
import sys
import time
from collections import defaultdict
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
    "new_pivot": {**vcp.JPEX_V7_PARAMS, "require_new_pivot": True},
    "new_pivot_margin2": {**vcp.JPEX_V7_PARAMS, "require_new_pivot": True, "new_pivot_margin_pct": 2.0},
    "eps_accel": {**vcp.JPEX_V7_PARAMS, "require_eps_acceleration": True},
    "combo": {**vcp.JPEX_V7_PARAMS, "require_new_pivot": True, "require_eps_acceleration": True},
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


def year_breakdown(trades, years_of_interest):
    by_year = defaultdict(list)
    for t in trades:
        by_year[t["exitDate"][:4]].append(t)
    for y in years_of_interest:
        ts = by_year.get(y, [])
        n = len(ts)
        if n == 0:
            print(f"  {y}: 거래 0건")
            continue
        wins = [t for t in ts if t["pnlPct"] > 0]
        exp = sum(t["pnlPct"] for t in ts) / n
        print(f"  {y}: {n}건 승률{len(wins)/n*100:.1f}% 기대값{exp:.2f}%/건")


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
    year_breakdown(trades, ["2019", "2022", "2025"])

print("\n=== 기준(V7 그대로, CAGR 40.04%/MDD -32.61%/Calmar 1.228/승률35.5%/거래231건) ===", flush=True)

out_path = Path(__file__).parent / "jpex_v24_rewhipsaw_trades.json"
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
