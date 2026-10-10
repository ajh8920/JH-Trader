# -*- coding: utf-8 -*-
"""41단계: 40단계에서 `require_new_pivot`(가격 기준 재진입 차단)이 휩쏘 거래를
줄이긴 했지만 좋은 재진입 거래까지 같이 막아 CAGR이 소폭 하락했다. 가격만으로
"이 재진입이 진짜인지 가짜인지" 가리는 대신, 그날의 거래량(기관 매수 확인)으로
가려보면 다를 수 있다는 가설을 시험한다.

엔진에 이미 있는 `pullback_min_volume_mult`(눌림 재개일 거래량이 50일 평균의
N배 이상이어야 체결 - WATCHER_PARAMS 계열에서 33차에 시도됐지만
stop_cooldown_days/min_ret12m_percentile과 함께 "승률 22~25% 근방이
구조적 상한"이라는 결론으로 기본값 None 유지됨, vcp_strategy.py 2251~2254줄
주석 참고)을 JPEX에 처음 적용해본다 - WATCHER는 돈치안 계열(진입이 훨씬
빈번, 연 156.8건)이라 구조적 한계가 있었을 수 있지만, JPEX는 애초에 거래가
훨씬 적고(연 21.4건) RS+품질 블렌드로 후보가 이미 엄선돼 있어 같은 결론이
안 나올 수도 있다. require_new_pivot처럼 "전부 막기"가 아니라 "오늘
거래량이 확인되는 재진입만 통과"라는 점에서 더 정교한 필터다.

변형: JPEX_V7_PARAMS 베이스로 pullback_min_volume_mult = 1.0 / 1.2 / 1.5

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래 + 2019/2022/2025 요약,
  research/jpex/jpex_v25_volume_confirm_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v25_volume_confirm
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
    "vol1.0": {**vcp.JPEX_V7_PARAMS, "pullback_min_volume_mult": 1.0},
    "vol1.2": {**vcp.JPEX_V7_PARAMS, "pullback_min_volume_mult": 1.2},
    "vol1.5": {**vcp.JPEX_V7_PARAMS, "pullback_min_volume_mult": 1.5},
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

out_path = Path(__file__).parent / "jpex_v25_volume_confirm_trades.json"
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
