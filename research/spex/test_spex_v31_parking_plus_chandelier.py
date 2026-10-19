# -*- coding: utf-8 -*-
"""SPEX 31단계("무조건 성능 향상 야근", 2026-10-19 밤~) - rate_etf_parking
(28단계, MDD -19.89%->-17.53%로 약 2.4%p 여유 확보, 단 CAGR은 23.14%->
22.6%로 소폭 희생)과 chandelier_atr_mult(14단계에서 MDD<=-20% 제약에
막혀 3.6에서 멈췄던 레버 - 4.0은 CAGR 22.94%였지만 MDD -20.29%로 위반)
을 **조합**하면, parking이 벌어준 MDD 여유분을 chandelier를 넓혀 CAGR로
다시 바꿀 수 있는지 확인. 지금까지 이 두 레버를 따로따로만 봤지 함께
쓴 적은 없다 - "사이징 유연화는 전부 실패"(23·29단계)와는 다른 종류의
조합(파킹은 사이징이 아니라 유휴기간 보너스라 그 결론과 무관).

변형: rate_etf_parking=True 고정, chandelier_atr_mult = {3.6(기준), 3.8, 4.0, 4.2}

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v31_parking_plus_chandelier_trades.json
사용법: python -m research.spex.test_spex_v31_parking_plus_chandelier
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

BASE = {**vcp.SPEX_V4_PARAMS, "rate_etf_parking": True}
VARIANTS = {
    "c3.6(기준)": {"chandelier_atr_mult": 3.6},
    "c3.8": {"chandelier_atr_mult": 3.8},
    "c4.0": {"chandelier_atr_mult": 4.0},
    "c4.2": {"chandelier_atr_mult": 4.2},
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

print("참고(SPEX_V4, 파킹 없음, c3.6): 23.14%/-19.89%/1.164/347건", flush=True)
print("참고(28단계, 파킹만 c3.6): ~22.6%/-17.53%/~1.29", flush=True)

out_path = Path(__file__).parent / "spex_v31_parking_plus_chandelier_trades.json"
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
