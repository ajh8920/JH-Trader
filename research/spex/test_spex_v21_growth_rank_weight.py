# -*- coding: utf-8 -*-
"""SPEX 21단계("야근ㄱㄱ", 2026-10-18) - growth_rank_weight(분기 EPS 성장률
순위 블렌드, vcp_strategy.py 신규 구현) 최초 시험.

배경: min_eps_growth_pct(하드 필터)는 JPEX 23/24단계에서 후보를 너무 많이
걸러내 성과를 깎는 것으로 이미 확인됐지만, quality_rank_weight가
"하드필터(min_quality_score) 대신 블렌드"로 성공한 것과 같은 논리의
소프트 버전은 이번이 처음(완전히 새로운 신호 - 사용자 지시 "완전히 새로운
신호를 도입해야 할 가능성"에 해당). flow_rank_weight와 같은 구조로
quarterly_eps_growth(최근 분기 YoY EPS 성장률)를 그날 트렌드템플릿 통과
종목 전체의 percentile로 변환해 순위에 섞는다.

변형: SPEX_V4 기준 growth_rank_weight = {0.1, 0.2, 0.3}
  (0.3은 quality_rank_weight와 동시에 적용 시 rs_w가 1-0.3-0.3=0.4로
  과도하게 줄어들 수 있어 quality_rank_weight는 0.3→0.2로 함께 낮춰본다)

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v21_growth_rank_weight_trades.json
사용법: python -m research.spex.test_spex_v21_growth_rank_weight
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

BASE = {**vcp.SPEX_V4_PARAMS}
VARIANTS = {
    "growth0.1": {"growth_rank_weight": 0.1},
    "growth0.2": {"growth_rank_weight": 0.2},
    "growth0.3_q0.2": {"growth_rank_weight": 0.3, "quality_rank_weight": 0.2},
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
for key, overrides in VARIANTS.items():
    preset = {**BASE, **overrides}
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
    print(f"[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} 승률 {wr:.1f}% 거래 {len(trades)}건", flush=True)

print("참고(SPEX_V4=기준): 23.14%/-19.89%/1.164/347건", flush=True)

out_path = Path(__file__).parent / "spex_v21_growth_rank_weight_trades.json"
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
print(f"저장 완료: {out_path}", flush=True)
