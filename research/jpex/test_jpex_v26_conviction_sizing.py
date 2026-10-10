# -*- coding: utf-8 -*-
"""42단계: "계속해서 CAGR을 높이도록 하세요"라는 요청에, 40~41단계(진입/재진입을
필터로 더 거르는 축)가 연속 실패한 뒤 완전히 다른 종류의 축을 시도한다.

지금까지 "2슬롯/50%씩 균등배분"이라는 포지션 사이징 구조 자체를 바꾸는
시도(슬롯수 정적/국면조건부 증감, 38~39단계)는 전부 실패했다 - 어느 방향으로
구조를 바꿔도 나빴다. 하지만 그 실험들은 전부 "모든 포지션에 똑같은 새
규칙"을 적용했다(예: 약세장엔 전부 1슬롯). 이번엔 구조(슬롯수 2개, 전체
비중상한)는 그대로 두고, 개별 후보의 신호 강도(quality_rank_weight와 같은
RS+품질 블렌드 점수)에 따라서만 배분을 달리하는 "신호강도 비례 비중
배분"(conviction-weighted sizing)을 엔진에 새로 추가해 시험한다
(vcp_strategy.py에 conviction_weight_high_pct/low_pct/conviction_score_threshold
파라미터 신규 추가, run_vcp_backtest 자체 수정 - 기존 JPEX V5/V6/V7/V64
결과와 완전히 동일한 하위호환성을 별도로 확인했다).

변형: JPEX_V7_PARAMS 베이스로
  - hi65lo35_th70: 확신 종목 65%, 약한 종목 35%, 문턱 0.7
  - hi70lo30_th70: 확신 종목 70%, 약한 종목 30%, 문턱 0.7
  - hi65lo35_th60: 확신 종목 65%, 약한 종목 35%, 문턱 0.6(더 많은 후보가 "확신"으로 분류)
  - hi60lo40_th70: 완만한 버전(60/40)

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래 + 2019/2022/2025 요약,
  research/jpex/jpex_v26_conviction_sizing_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v26_conviction_sizing
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
    "hi65lo35_th70": {**vcp.JPEX_V7_PARAMS, "conviction_weight_high_pct": 65.0,
                       "conviction_weight_low_pct": 35.0, "conviction_score_threshold": 0.7},
    "hi70lo30_th70": {**vcp.JPEX_V7_PARAMS, "conviction_weight_high_pct": 70.0,
                       "conviction_weight_low_pct": 30.0, "conviction_score_threshold": 0.7},
    "hi65lo35_th60": {**vcp.JPEX_V7_PARAMS, "conviction_weight_high_pct": 65.0,
                       "conviction_weight_low_pct": 35.0, "conviction_score_threshold": 0.6},
    "hi60lo40_th70": {**vcp.JPEX_V7_PARAMS, "conviction_weight_high_pct": 60.0,
                       "conviction_weight_low_pct": 40.0, "conviction_score_threshold": 0.7},
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

out_path = Path(__file__).parent / "jpex_v26_conviction_sizing_trades.json"
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
