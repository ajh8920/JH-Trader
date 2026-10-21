# -*- coding: utf-8 -*-
"""SPEX 42단계(APEX 베이스, 2026-10-22 야근ㄱㄱ) - 41단계에서 min_eps_growth_pct
(20%→25%)를 재튜닝해 SPEX_V7(26.01%/-19.37%/Calmar 1.343/354건)로 채택했다.
그 교훈("구 APEX 설계에서 그대로 넘어와 RS90 운영점에서 한 번도 재튜닝한 적
없는 상수")을 같은 계열의 다른 APEX_PARAMS 하드코딩 값에도 적용한다 -
rescan_interval_days/pullback_min_persist_days/max_pullback_pct/
ma_break_consec_days/pullback_min_uptick_pct 전부 4슬롯·RS85 시절 APEX 1차
설계에서 그대로 상속됐을 뿐, 7슬롯·RS90 SPEX 운영점에서 재검증된 적이 없다.

변형(전부 SPEX_V7_PARAMS에서 한 레버만 변경):
  A) rescan_interval_days=3 (기본 1일보다 덜 자주 재평가)
  B) pullback_min_persist_days=5 (기본 3일보다 더 긴 눌림목 지속 요구)
  C) pullback_min_persist_days=1 (기본보다 완화)
  D) max_pullback_pct=12.0 (기본 8%보다 더 깊은 눌림목 허용)
  E) max_pullback_pct=5.0 (기본보다 얕은 눌림목만 허용)
  F) ma_break_consec_days=6 (기본 4일보다 이평선이탈 확정을 더 오래 요구)
  G) ma_break_consec_days=2 (기본보다 빠르게 이평선이탈 확정)
  H) pullback_min_uptick_pct=0.5 (41단계 G=2.0은 대실패였으니 반대 방향인
     완화로 재시도, 기본 1.0%)

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v42_apex_legacy_axes_trades.json
사용법: python -m research.spex.test_spex_v42_apex_legacy_axes
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

BASE7 = vcp.SPEX_V7_PARAMS

VARIANTS = {
    "A_rescan_3": {**BASE7, "rescan_interval_days": 3},
    "B_persist_5": {**BASE7, "pullback_min_persist_days": 5},
    "C_persist_1": {**BASE7, "pullback_min_persist_days": 1},
    "D_maxpullback_12": {**BASE7, "max_pullback_pct": 12.0},
    "E_maxpullback_5": {**BASE7, "max_pullback_pct": 5.0},
    "F_mabreak_6": {**BASE7, "ma_break_consec_days": 6},
    "G_mabreak_2": {**BASE7, "ma_break_consec_days": 2},
    "H_uptick_0.5": {**BASE7, "pullback_min_uptick_pct": 0.5},
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

print("참고(기준선, SPEX_V7): 26.01%/-19.37%/1.343/354건 PASS", flush=True)

out_path = Path(__file__).parent / "spex_v42_apex_legacy_axes_trades.json"
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
