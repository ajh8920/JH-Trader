# -*- coding: utf-8 -*-
"""SPEX 40단계(APEX 베이스, 2026-10-22 야근ㄱㄱ) - 37~39단계에서 확정된
SPEX_V6(슬롯7·비중20%·breadth30/25·RS90, 25.97%/-19.91%/1.304/360건)를
기준선으로, 34~39단계에서 아직 건드리지 않은 "청산/리스크 관리" 축을
단일 레버씩 테스트한다. 34~39단계는 진입 필터(초기손절·피라미딩·
시장폭·수익성·RS문턱)만 다뤘고, breakeven_r/partial_profit/
max_hold_days/loss_streak_halt/equity_dd_derisk/overheat는 APEX 계열
기본값(또는 미설정=off) 그대로 한 번도 이 운영점에서 변경해본 적이 없다.

참고: breakeven_r·chandelier_atr_mult는 JPEX_V11 라인(18~19단계)에서
이미 테스트됐지만 그건 JPEX_V11_PARAMS 기준선(트레이드 성격이 다름)
이었다 - APEX_V2+RS90 기준선에서 같은 레버가 같은 방향으로 작동하는지
재확인할 필요가 있다.

변형(전부 BASE7=SPEX_V6에서 한 레버만 변경):
  A) breakeven_r=0.5 (기본 1.0보다 더 빨리 손익분기로 끌어올림 - MDD 개선 기대)
  B) breakeven_r=2.0 (JPEX 라인 최적값과 동일 - CAGR 개선 기대, MDD 악화 위험)
  C) partial_profit_r=1.5, partial_profit_fraction=0.35 (기본 2.0/0.25보다
     더 일찍·더 많이 분할 익절 - MDD 개선 기대)
  D) partial_profit_r=3.0, partial_profit_fraction=0.15 (분할 익절을 줄여
     승자를 더 오래 태움 - CAGR 개선 기대)
  E) max_hold_days=180 (장기 정체 포지션 강제 정리)
  F) loss_streak_halt_count=3, loss_streak_halt_days=15 (연속 3손실 시
     15일 신규진입 중단 - MDD 개선 기대)
  G) equity_dd_derisk_threshold_pct=-15.0, equity_dd_derisk_factor=0.6
     (전체 자산 -15% 이상 하락 시 신규 포지션 크기를 60%로 축소)
  H) overheat_days=8, overheat_gain_pct=20.0 (8일 내 +20% 급등 시 일부 익절)

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v40_new_exit_axes_trades.json
사용법: python -m research.spex.test_spex_v40_new_exit_axes
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

BASE7 = {**vcp.APEX_V2_PARAMS, "max_positions": 7, "max_position_weight_pct": 20.0,
         "breadth_min_pct": 30.0, "gate_entries_breadth_min_pct": 25.0,
         "evan_params": {"min_rs": 90.0}}

VARIANTS = {
    "A_breakeven_0.5": {**BASE7, "breakeven_r": 0.5},
    "B_breakeven_2.0": {**BASE7, "breakeven_r": 2.0},
    "C_partial_early_1.5_0.35": {**BASE7, "partial_profit_r": 1.5, "partial_profit_fraction": 0.35},
    "D_partial_late_3.0_0.15": {**BASE7, "partial_profit_r": 3.0, "partial_profit_fraction": 0.15},
    "E_max_hold_180": {**BASE7, "max_hold_days": 180},
    "F_loss_streak_halt_3_15": {**BASE7, "loss_streak_halt_count": 3, "loss_streak_halt_days": 15},
    "G_equity_dd_derisk_15_0.6": {**BASE7, "equity_dd_derisk_threshold_pct": -15.0,
                                  "equity_dd_derisk_factor": 0.6},
    "H_overheat_8_20": {**BASE7, "overheat_days": 8, "overheat_gain_pct": 20.0},
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

print("참고(기준선, 37단계 E/SPEX_V6): 25.97%/-19.91%/1.304/360건 PASS", flush=True)

out_path = Path(__file__).parent / "spex_v40_new_exit_axes_trades.json"
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
