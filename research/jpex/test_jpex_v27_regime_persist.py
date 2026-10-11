# -*- coding: utf-8 -*-
"""43단계: jpex_v7.json 매매내역(종목명/진입가/청산가/청산이유를 이번에 막
채워넣은 참)을 exitReason별로 집계해보니 뜻밖의 분포가 나왔다 -

  trailingStop  32건  합계+1321.0%p  승률96.9%  (수익의 거의 전부)
  overheat       3건  합계 +193.8%p  승률100%
  breakevenStop  9건  합계  -46.1%p  승률22.2%
  regimeExit   126건  합계  -69.8%p  승률36.5%  (전체 231건의 54.5%!)
  initialStop   61건  합계 -431.4%p  승률0%(정의상)

regimeExit가 거래 "건수"로는 전체의 과반(54.5%)을 차지하는데, 날짜를
세어보니 10.75년 동안 119개의 서로 다른 날짜에 걸쳐 있고 2021/2024처럼
전반적으로 양호했던 해에도 몇 건씩 나왔다 - 코스피 종가가 200일선을
하루이틀 넘나드는 노이즈에도 매번 미성숙 포지션(regime_exit_min_r=1.25
미달)이 바로 끊기는 것으로 보인다. "진짜 하락장 진입"과 "하루짜리
휩쏘"를 구분하지 못하는 신호라는 가설을 세우고, 엔진에
`regime_loss_persist_days`(국면이 N일 연속 weak여야 강제청산 발동,
기본 1=기존과 동일) 파라미터를 신규 추가해 시험한다(vcp_strategy.py 수정 -
기본값에서 기존 결과와 완전히 동일한 하위호환성 확인 완료).

변형: JPEX_V7_PARAMS 베이스로 regime_loss_persist_days = 2 / 3 / 5

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래 + exitReason별 집계 +
  2019/2022/2025 요약, research/jpex/jpex_v27_regime_persist_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v27_regime_persist
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
    "persist2": {**vcp.JPEX_V7_PARAMS, "regime_loss_persist_days": 2},
    "persist3": {**vcp.JPEX_V7_PARAMS, "regime_loss_persist_days": 3},
    "persist5": {**vcp.JPEX_V7_PARAMS, "regime_loss_persist_days": 5},
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


def exit_reason_breakdown(trades):
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t.get("exitReason") or "None"].append(t["pnlPct"])
    for reason, pnls in sorted(by_reason.items(), key=lambda kv: -sum(kv[1])):
        n = len(pnls)
        wins = [p for p in pnls if p > 0]
        print(f"    {reason:16s} n={n:4d} 합계={sum(pnls):8.1f}%p 평균={sum(pnls)/n:6.2f}% "
              f"승률={len(wins)/n*100:5.1f}%")


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
    exit_reason_breakdown(trades)
    year_breakdown(trades, ["2019", "2022", "2025"])

print("\n=== 기준(V7 그대로, CAGR 40.04%/MDD -32.61%/Calmar 1.228/승률35.5%/거래231건, regimeExit 126건) ===", flush=True)

out_path = Path(__file__).parent / "jpex_v27_regime_persist_trades.json"
out_data = {"seed": SEED}
for key, r in results.items():
    out_data[f"{key}_curve"] = r["equityCurve"]
    out_data[f"{key}_trades"] = [
        {"code": t["code"], "entryDate": t["entryDate"], "exitDate": t["exitDate"],
         "pnlPct": t["pnlPct"], "exitReason": t.get("exitReason")}
        for t in r["trades"]
    ]
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out_data, f)
print(f"\n저장 완료: {out_path}", flush=True)
