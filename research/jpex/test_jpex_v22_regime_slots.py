# -*- coding: utf-8 -*-
"""사용자 제안(2026-10-10): "슬롯 2개"는 정적으로는 집중/분산 양방향 모두
최적점이었지만(22단계), 시장 레짐에 따라 슬롯 수 자체를 유동적으로 조절하면
다를 수 있는가? regime_adaptive_params에 max_positions을 신규 지원(이
스크립트가 첫 실사용 검증)해 테스트한다.

목적: 22단계(슬롯 정적 확대/축소)는 전부 실패했고, 21단계(피라미딩/챈들리어/
리스크 국면조건부)도 실패했다. 하지만 "슬롯수를 국면조건부로" 바꾸는 건
아직 안 해봤다 - 피라미딩/챈들리어는 "보유 포지션을 얼마나 키우는가"를
다루는 반면 슬롯수는 "동시에 몇 개의 서로 다른 아이디어에 걸 것인가"를
다루는 별개의 축이라, 다른 결과가 나올 수 있다.

변형: JPEX_V7_PARAMS 베이스로
  - weak_diversify: weak 국면에 슬롯 4개(분산 방어), strong/neutral은 그대로(2개)
  - strong_concentrate: strong 국면에 슬롯 1개+비중100%(최대 집중), weak/neutral은
    그대로(2개)
  - reverse: weak 국면에 슬롯 1개+비중100%(확신 있는 한 종목에만 집중 - 약세장
    눌림목 신호는 대부분 가짜라는 가정), strong 국면에 슬롯 3개(분산해서 여러
    승자를 동시에 포착)

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래 + 2019/2022 요약,
  research/jpex/jpex_v22_regime_slots_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v22_regime_slots
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
    "weak_diversify": {**vcp.JPEX_V7_PARAMS,
                        "regime_adaptive_params": {"weak": {"max_positions": 4}}},
    "strong_concentrate": {**vcp.JPEX_V7_PARAMS,
                            "regime_adaptive_params": {"strong": {"max_positions": 1, "max_position_weight_pct": 100.0}}},
    "reverse": {**vcp.JPEX_V7_PARAMS,
                "regime_adaptive_params": {
                    "weak": {"max_positions": 1, "max_position_weight_pct": 100.0},
                    "strong": {"max_positions": 3},
                }},
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
    year_breakdown(trades, ["2019", "2022"])

out_path = Path(__file__).parent / "jpex_v22_regime_slots_trades.json"
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
