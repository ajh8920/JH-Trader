# -*- coding: utf-8 -*-
"""SPEX 9단계(8단계 후속) - 비중상한(max_position_weight_pct)만 올리는
축으로는 CAGR>=23%에 못 미치고(최고 20.95%, 그마저 MDD -20% 위반) 비중
경계(18.5~20% 사이) 자체는 6~7단계의 더 나은 청산 타이밍으로도 안 바뀌었다
(8단계 결론). "다음 단계에서 이어간다"던 두 미탐색 축을 이번에 확인한다.

변형 A - 슬롯 수 x 비중상한 조인트: 슬롯이 늘면 같은 비중상한에서도 실제
  동시 보유 종목이 늘어 전체 포트폴리오 집중도가 낮아질 수 있다(8단계는
  슬롯=8 고정이었다) - slots=7/9/10 x weight=20 조합이 MDD를 다시
  -20% 이내로 끌어내리는지 확인.
변형 B - entry_rank_top_n 재탐색(비중=20% 고정): 진입 후보풀을 좁히면(60)
  더 강한 신호만 걸러져 같은 비중에서도 손실이 줄 수 있고, 넓히면(120)
  기회가 늘어 CAGR이 더 오를 수 있다 - 5단계는 이 변수를 비중=18.5%
  기준으로만 탐색했었다(80이 최적).

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v9_slots_entry_joint_trades.json (equity curve + 거래 로그)
사용법: python -m research.spex.test_spex_v9_slots_entry_joint
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

BASE = {**vcp.SPEX_V1_PARAMS, "entry_rank_top_n": 80, "rank_exit_top_n": 100}
GRID = {
    # 변형 A - 슬롯 x 비중(20%)
    "s7w20": {**BASE, "max_positions": 7, "max_position_weight_pct": 20},
    "s9w20": {**BASE, "max_positions": 9, "max_position_weight_pct": 20},
    "s10w20": {**BASE, "max_positions": 10, "max_position_weight_pct": 20},
    # 변형 B - entry_rank_top_n x 비중(20%), 슬롯은 8 고정
    "e60w20": {**BASE, "max_position_weight_pct": 20, "entry_rank_top_n": 60},
    "e120w20": {**BASE, "max_position_weight_pct": 20, "entry_rank_top_n": 120},
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
    print(f"\n[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 손익비 {r.get('profitLossRatio')} 거래 {len(trades)}건"
          f"(연 {len(trades)/years_span:.1f}건)", flush=True)

print("\n=== 참고(w18.5/re100/slots8, SPEX_V1): 17.55%/-17.40%/1.009/357건 ===", flush=True)
print("=== 참고(w20/slots8, 8단계): 19.15%/-22.50%/0.851 ===", flush=True)

out_path = Path(__file__).parent / "spex_v9_slots_entry_joint_trades.json"
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
