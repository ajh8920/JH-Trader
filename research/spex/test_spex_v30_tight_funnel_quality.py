# -*- coding: utf-8 -*-
"""SPEX 30단계("시도 안 해본 아이디어로 계속 개선", 2026-10-19) - 21단계
결론("SPEX 7슬롯 구조에서는 rank_weight 블렌드가 구조적으로 무력하다 -
슬롯이 후보 수보다 적게 남는 날이 드물어서")이 "entry_rank_top_n=80이라는
넓은 후보풀" 때문이라면, 후보풀을 크게 좁혀(entry_rank_top_n=40) 슬롯
경쟁을 실제로 발생시키면 quality_rank_weight가 다시 "살아날" 수 있는지
확인. 2단계에서 entry_rank_top_n=40 단독은 이미 나쁘다고 나왔지만
(CAGR 11.31%) quality_rank_weight와 짝지어 시험한 적은 없다.

변형(entry_rank_top_n=40 고정):
  A) quality_rank_weight=0.3(기준과 동일 가중치)
  B) quality_rank_weight=0.6(가중치 2배)
  C) quality_rank_weight=0.0(끔 - 좁은 후보풀에서 순수 RS만)
기준: entry_rank_top_n=80(SPEX_V4 그대로)

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v30_tight_funnel_quality_trades.json
사용법: python -m research.spex.test_spex_v30_tight_funnel_quality
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
    "A_tight_q0.3": {"entry_rank_top_n": 40, "quality_rank_weight": 0.3},
    "B_tight_q0.6": {"entry_rank_top_n": 40, "quality_rank_weight": 0.6},
    "C_tight_q0.0": {"entry_rank_top_n": 40, "quality_rank_weight": 0.0},
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

print("참고(SPEX_V4=entry80/q0.3 기준): 23.14%/-19.89%/1.164/347건", flush=True)

out_path = Path(__file__).parent / "spex_v30_tight_funnel_quality_trades.json"
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
