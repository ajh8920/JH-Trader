# -*- coding: utf-8 -*-
"""SPEX 34단계("틀에서 벗어나 CAGR 25%+" 사용자 지시, 2026-10-20) - SPEX는
지금까지 전부 JPEX_V11_PARAMS 상속(RS62+quality_rank_weight+rank_exit)
에서만 나왔다. APEX_V2_PARAMS는 WATCHER/JPEX 계열과 "완전히 별도로
설계된"(strategy_specs.py 설명 그대로) 전략으로, RS>=85 절대문턱(랭킹
기반 funnel 없음)+EPS/매출성장 20%+ 하드필터+국면/시장폭 방어 전혀 없음
+4슬롯 집중(30% 캡)이 특징 - 현재 엔진 재측정 CAGR 31.65%/MDD -39.0%로
JPEX(41.52%/-29.43%)보다도 베이스 자체가 다르다. SPEX가 JPEX를
"분산으로 안정화"했던 것과 같은 방법을 APEX_V2에 적용하면, JPEX 경유
없이도 더 높은 CAGR에서 출발해 분산으로 깎아도 25% 이상이 남을 수
있는지 확인 - 이 세션에서 한 번도 안 해본 "다른 베이스" 시도.

변형: APEX_V2_PARAMS 기준으로
  A) 슬롯7·비중20%, 시장폭 방어 추가(JPEX의 breadth_min_pct=30/
     gate_entries_breadth_min_pct=25 차용)
  B) 슬롯8·비중17%, 시장폭 방어 추가
  C) 슬롯6·비중23%, 시장폭 방어 추가
  D) 슬롯7·비중20%, 시장폭 방어 없음(APEX 원래 그대로, 분산 효과만 순수 측정)

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v34_apex_base_trades.json
사용법: python -m research.spex.test_spex_v34_apex_base
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

BASE = {**vcp.APEX_V2_PARAMS}
DEFENSE = {"breadth_min_pct": 30.0, "gate_entries_breadth_min_pct": 25.0}
VARIANTS = {
    "A_slot7_w20_defense": {"max_positions": 7, "max_position_weight_pct": 20.0, **DEFENSE},
    "B_slot8_w17_defense": {"max_positions": 8, "max_position_weight_pct": 17.0, **DEFENSE},
    "C_slot6_w23_defense": {"max_positions": 6, "max_position_weight_pct": 23.0, **DEFENSE},
    "D_slot7_w20_nodefense": {"max_positions": 7, "max_position_weight_pct": 20.0},
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

print("참고(SPEX_V4, JPEX베이스): 23.14%/-19.89%/1.164/347건", flush=True)
print("참고(APEX_V2 원본, 4슬롯/30%): 31.65%/-39.0%/0.812/382건", flush=True)

out_path = Path(__file__).parent / "spex_v34_apex_base_trades.json"
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
