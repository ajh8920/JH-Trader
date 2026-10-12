# -*- coding: utf-8 -*-
"""68단계 이후: 지인(연 57건)과 V10(연 17.7건)의 CAGR 격차가 "거래 품질"이
아니라 "자본회전율"에서 난다는 진단(손익비는 V10이 오히려 2배 더 좋음,
슬롯 확대/필터 완화는 전부 품질 희석으로 역효과)에 따라, 진입 품질은 전혀
안 건드리고 "얼마나 오래 들고 있는가"만 바꾸는 두 축을 JPEX에서 처음
시험한다.

max_hold_days(보유일수 상한, 도달 시 수익 진행과 무관하게 강제청산) -
현재 평균보유 8.4일보다 긴 15/20/30일로 "지지부진한 포지션"만 잘라내고
트레일링이 큰 승자를 키울 여지는 보존하려는 시도.

rank_exit_top_n(RS 순위가 Top N 밖으로 밀려나면 청산) - 손절/트레일링과
무관하게 "상대강도 자체가 식은" 포지션을 더 빨리 정리.

변형: JPEX_V10_PARAMS 베이스로
  max_hold_days = 15 / 20 / 30
  rank_exit_top_n = 60 / 80 (entry_rank_top_n=40보다 넉넉하게 - 진입
    문턱보다 청산 문턱을 더 느슨하게 둬서 노이즈성 휩쏘를 줄인다)

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v55_turnover_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v55_turnover
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
    "hold15": {**vcp.JPEX_V10_PARAMS, "max_hold_days": 15},
    "hold20": {**vcp.JPEX_V10_PARAMS, "max_hold_days": 20},
    "hold30": {**vcp.JPEX_V10_PARAMS, "max_hold_days": 30},
    "rankexit60": {**vcp.JPEX_V10_PARAMS, "rank_exit_top_n": 60},
    "rankexit80": {**vcp.JPEX_V10_PARAMS, "rank_exit_top_n": 80},
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
    avg_hold = sum(t["holdDays"] for t in trades) / len(trades) if trades else 0
    print(f"\n[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 손익비 {r.get('profitLossRatio')} 거래 {len(trades)}건"
          f"(연 {len(trades)/years_span:.1f}건) 평균보유 {avg_hold:.1f}일", flush=True)

print("\n=== 기준(V10): 41.31%/-29.43%/1.404/191건(연 17.7건, 평균보유 8.4일) ===", flush=True)

out_path = Path(__file__).parent / "jpex_v55_turnover_trades.json"
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
print(f"\n저장 완료: {out_path}", flush=True)
