# -*- coding: utf-8 -*-
"""SPEX 13단계(12단계 후속, "야근ㄱㄱ") - 12단계에서 rank_exit_top_n을
85~115 전구간 바꿔도 MDD가 -19.50%로 단 한 번도 안 움직였다(CAGR만
변했다). 이는 MDD가 슬롯7·비중21.2%라는 "구조"에만 좌우되고, 개별
트레이드 단위 실행 파라미터(청산 타이밍)와는 독립적임을 시사한다 -
그렇다면 아직 안 건드린 다른 실행 파라미터(트레일링스탑 폭, 피라미딩
횟수, 초기손절폭)도 MDD는 그대로 둔 채 CAGR만 올려줄 가능성이 있다.
이 파라미터들은 전부 JPEX(2슬롯·슬롯당 50% - 고집중 고확신 베팅)에
맞춰 튜닝된 값을 SPEX(7슬롯·슬롯당 21.2% - 진짜 분산)가 그대로
물려받은 것이라, SPEX 구조에서는 안 맞을 수 있다.

변형(슬롯7/비중21.2%/entry80/rank_exit95 고정, 하나씩만 바꿈):
  chandelier_atr_mult(트레일링스탑 ATR배수, 기본 3.0) = 2.5 / 3.5 / 4.0
  pyramid_max_count(피라미딩 최대 횟수, 기본 4) = 2 / 6
  initial_stop_atr_mult(초기손절 ATR배수, 기본 1.1) = 1.5

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v13_exec_params_trades.json (equity curve + 거래 로그)
사용법: python -m research.spex.test_spex_v13_exec_params
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

BASE = {**vcp.SPEX_V1_PARAMS, "max_positions": 7, "entry_rank_top_n": 80,
        "max_position_weight_pct": 21.2, "rank_exit_top_n": 95}
GRID = {
    "chand25": {**BASE, "chandelier_atr_mult": 2.5},
    "chand35": {**BASE, "chandelier_atr_mult": 3.5},
    "chand40": {**BASE, "chandelier_atr_mult": 4.0},
    "pyr2": {**BASE, "pyramid_max_count": 2},
    "pyr6": {**BASE, "pyramid_max_count": 6},
    "stop15": {**BASE, "initial_stop_atr_mult": 1.5},
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

print("\n=== 참고(슬롯7/w21.2/re95, 12단계 최고): 20.66%/-19.50%/1.059/353건 ===", flush=True)

out_path = Path(__file__).parent / "spex_v13_exec_params_trades.json"
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
