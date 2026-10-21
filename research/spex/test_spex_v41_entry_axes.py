# -*- coding: utf-8 -*-
"""SPEX 41단계(APEX 베이스, 2026-10-22 야근ㄱㄱ) - 40단계에서 청산/리스크관리측
레버 8종이 전부 기준선(SPEX_V6: 25.97%/-19.91%/1.304/360건)을 못 넘기거나
완전 무효과였다. 37~39단계의 핵심 발견("RS문턱 강화"가 유일한 PASS)도 진입측
레버였으므로, 이번엔 청산측을 더 건드리지 않고 진입측(손절폭·리스크비중·
EPS성장 문턱·반등확인 문턱·자금흐름 랭크)으로 축을 옮겨 단일 레버씩 테스트한다.

변형(전부 BASE7=SPEX_V6에서 한 레버만 변경):
  A) initial_stop_atr_mult=1.4 (기본 1.1보다 손절폭을 넓혀 휩소 감소 기대)
  B) initial_stop_atr_mult=0.9 (37단계 A=0.8은 대실패였으니 더 완만하게 재시도)
  C) max_initial_risk_pct=2.2 (기본 2.8%보다 보수적 - MDD 개선 기대)
  D) max_initial_risk_pct=3.5 (기본보다 공격적 - CAGR 개선 기대)
  E) min_eps_growth_pct=25.0 (기본 20%보다 엄격한 성장률 하드필터)
  F) min_eps_growth_pct=15.0 (기본보다 완화 - 후보군 확대)
  G) pullback_min_uptick_pct=2.0 (기본 1.0%보다 반등 확인을 더 요구)
  H) flow_rank_weight=0.2 (외국인+기관 순매수 랭크 블렌드 - APEX_STAGE3에서
     하드필터(min_flow_percentile)는 이미 실패 확정이지만(27단계 패턴과 같은
     이유로) "하드필터보다 순위 블렌드가 낫다"는 JPEX 67단계 발견을 이 운영점
     에서도 재확인)

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v41_entry_axes_trades.json
사용법: python -m research.spex.test_spex_v41_entry_axes
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
from data_pipeline.common import SHAREHOLDER_KR_DIR, FUND_KR_DIR, DATA_ROOT

with open(PROJECT_DIR / "kr_stocks.json", encoding="utf-8") as f:
    kr_stocks = json.load(f)
shares_map = {s["code"]: s["shares"] for s in kr_stocks if s.get("shares")}
shareholder_rows = vcp.load_shareholder_rows(
    [SHAREHOLDER_KR_DIR / "majorstock_1of2.parquet", SHAREHOLDER_KR_DIR / "majorstock_2of2.parquet"])
with app.app_context():
    fundamentals_rows = vcp.load_fundamentals_rows(KrFundamental)
quarter_paths = sorted((FUND_KR_DIR.parent / "kr_quarter").glob("*.parquet"))
quarterly_rows = vcp.load_quarterly_rows(quarter_paths)
_flow_path = DATA_ROOT / "flows" / "kr" / "investor_flows.parquet"
flow_rows = vcp.load_investor_flow_rows(_flow_path) if _flow_path.exists() else None

START, END = "2016-01-01", date.today().isoformat()
SEED = 500_000_000
fetch_fn = cached_fetch_ohlc_history_batches("KR", include_delisted=True, max_age_hours=99999)

BASE7 = {**vcp.APEX_V2_PARAMS, "max_positions": 7, "max_position_weight_pct": 20.0,
         "breadth_min_pct": 30.0, "gate_entries_breadth_min_pct": 25.0,
         "evan_params": {"min_rs": 90.0}}

VARIANTS = {
    "A_stop_1.4": {**BASE7, "initial_stop_atr_mult": 1.4},
    "B_stop_0.9": {**BASE7, "initial_stop_atr_mult": 0.9},
    "C_risk_2.2": {**BASE7, "max_initial_risk_pct": 2.2},
    "D_risk_3.5": {**BASE7, "max_initial_risk_pct": 3.5},
    "E_eps_growth_25": {**BASE7, "min_eps_growth_pct": 25.0},
    "F_eps_growth_15": {**BASE7, "min_eps_growth_pct": 15.0},
    "G_uptick_2.0": {**BASE7, "pullback_min_uptick_pct": 2.0},
}
if flow_rows:
    VARIANTS["H_flow_rank_0.2"] = {**BASE7, "flow_rows_by_code": flow_rows, "flow_rank_weight": 0.2}


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
    pass_ok = "PASS" if (cagr >= 25 and abs(mdd) <= 20) else ""
    print(f"[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} 승률 {wr:.1f}% 거래 {len(trades)}건 {pass_ok}", flush=True)

print("참고(기준선, SPEX_V6): 25.97%/-19.91%/1.304/360건 PASS", flush=True)

out_path = Path(__file__).parent / "spex_v41_entry_axes_trades.json"
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
