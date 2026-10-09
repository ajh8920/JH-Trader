# -*- coding: utf-8 -*-
"""블렌드(JPEX+APEX68 두 엔진 결과를 섞는 방식) 대신, JPEX 하나의 파라미터만
조정해 거래량·CAGR을 올리는 단일 전략 버전을 찾는다.

목적:
  16~17단계(research/jpex/RESULTS.md)에서 JPEX+APEX68 블렌드로 거래량/CAGR을
  올리는 길을 찾았지만, 사용자가 "섞지 말고 하나로 완벽한 JPEX를 구현해달라"고
  명시적으로 요청했다. 블렌드는 두 개의 서로 다른 신호체계(진입조건이 다름)를
  자산 비중으로 합치는 것이라 "하나의 전략"이 아니다 - 이 스크립트는 JPEX_PARAMS
  (v64) 자체의 파라미터만 바꿔 같은 효과(거래량 상향 + 하락 방어)를 단일
  엔진 실행으로 낼 수 있는지 검증한다.

배경:
  JPEX(v64)는 거래가 10.75년간 84건(연 7.8건)뿐이다 - gate_entries_on_regime=True
  가 약한 국면에서 신규진입 자체를 막아버리기 때문(국면방어 효과는 확실하지만
  거래 기회를 과도하게 줄인다). 17단계에서 추가한 regime_exit_min_r(이미 1R
  이상 번 포지션은 국면상실청산에서 봐줌)을 쓰면, 진입 게이트를 풀어도
  (gate_entries_on_regime=False) 청산 쪽에서 하락장 손실을 억제할 수 있을
  것이라는 가설.

변형(모두 JPEX_PARAMS 베이스, 여러 인자를 동시에 바꿔 비교):
  A: gate_entries_on_regime=False, exit_on_regime_loss=True, regime_exit_min_r=1.0
     (진입 게이트만 풀고 나머지는 v64 그대로)
  B: A + regime_exit_min_r=1.5 (청산 보호 기준을 더 느슨하게)
  C: A + entry_rank_top_n=60 (진입 게이트를 풀어 거래가 늘면 후보 풀도 같이 넓힘)

데이터:
  로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물):
  콘솔에 CAGR/MDD/Calmar/승률/거래수 + 2016/2019/2022 연도별(승률/평균승/
  평균패/기대값) 출력, v64(원본, 비교용)도 같이 돌려서 나란히 찍는다. 거래
  전체를 research/jpex/jpex_unified_trades.json에 저장.

사용법:
  python -m research.jpex.test_jpex_unified
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

JPEX_UNIFIED_A = {**vcp.JPEX_PARAMS, "gate_entries_on_regime": False, "regime_exit_min_r": 1.0}
JPEX_UNIFIED_B = {**JPEX_UNIFIED_A, "regime_exit_min_r": 1.5}
JPEX_UNIFIED_C = {**JPEX_UNIFIED_A, "entry_rank_top_n": 60}

VARIANTS = {
    "B(게이트해제+r1.5)": JPEX_UNIFIED_B,
    "C(A+Top60)": JPEX_UNIFIED_C,
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
        losses = [t for t in ts if t["pnlPct"] <= 0]
        wr = len(wins) / n * 100
        aw = sum(t["pnlPct"] for t in wins) / len(wins) if wins else 0
        al = sum(t["pnlPct"] for t in losses) / len(losses) if losses else 0
        exp = sum(t["pnlPct"] for t in ts) / n
        print(f"  {y}: {n}건 승률{wr:.1f}% 평균승{aw:.2f}% 평균패{al:.2f}% 기대값{exp:.2f}%/건")


years_span = (date.today() - date(2016, 1, 1)).days / 365.25
results = {}
for key, preset in VARIANTS.items():
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
          f"승률 {wr:.1f}% 거래 {len(trades)}건 (연 {len(trades)/years_span:.1f}건)")
    print("연도별(2016/2019/2022 - 문제 구간):")
    year_breakdown(trades, ["2016", "2019", "2022"])

out_path = Path(__file__).parent / "jpex_unified_trades.json"
out_data = {"seed": SEED}
for key, r in results.items():
    safe_key = key.split("(")[0]
    out_data[f"{safe_key}_curve"] = r["equityCurve"]
    out_data[f"{safe_key}_trades"] = [
        {"code": t["code"], "entryDate": t["entryDate"], "exitDate": t["exitDate"], "pnlPct": t["pnlPct"]}
        for t in r["trades"]
    ]
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out_data, f)
print(f"\n저장 완료: {out_path}", flush=True)
