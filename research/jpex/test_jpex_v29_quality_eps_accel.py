# -*- coding: utf-8 -*-
"""45단계: 40단계에서 분기 EPS 가속을 하드 필터(require_eps_acceleration)로
걸었을 때 JPEX CAGR이 40.04%->29.05%로 붕괴했다 - 좋은 후보까지 같이
잘려나간 전형적인 하드 필터 역효과였다. 반면 quality_rank_weight(27단계,
기본적 분석 5개 항목 통과율을 순위에 "섞는" 소프트 블렌드)는 이 세션에서
유일하게 성공한 레버였다. 같은 EPS 가속 신호를 하드 필터가 아니라
fundamental_quality_score의 6번째 항목(소프트 블렌드)으로 녹이면 다를
수 있다는 가설을 시험한다 - vcp_strategy.py의 fundamental_quality_score에
quarterly_rows_for_code 선택 인자를 추가하고, run_vcp_backtest에
quality_use_eps_accel(기본 False=기존 5항목 그대로, 하위호환 확인 완료)
플래그를 신규 추가했다.

변형: JPEX_V7_PARAMS 베이스로 quality_use_eps_accel=True (quality_rank_weight=0.3은
그대로 유지 - EPS 가속이 섞인 6항목 품질점수가 기존 30% 가중치 안에 녹아든다)

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래 + 2019/2022/2025 요약,
  research/jpex/jpex_v29_quality_eps_accel_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v29_quality_eps_accel
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
    "quality_eps_accel": {**vcp.JPEX_V7_PARAMS, "quality_use_eps_accel": True},
    # quality_rank_weight을 조금 더 줘서(0.3->0.4) 6항목으로 늘어난 품질점수의
    # 비중을 더 반영했을 때도 시험(과최적화 위험이 있지만 방향성만 확인).
    "quality_eps_accel_w40": {**vcp.JPEX_V7_PARAMS, "quality_use_eps_accel": True, "quality_rank_weight": 0.4},
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

out_path = Path(__file__).parent / "jpex_v29_quality_eps_accel_trades.json"
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
