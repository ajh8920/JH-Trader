# -*- coding: utf-8 -*-
"""SPEX 22단계("야근ㄱㄱ" 계속, 2026-10-18) - entry_mode(셋업 탐지 방식)
자체를 바꿔보는 첫 시도. SPEX_V1~V4는 전부 JPEX_V11(entry_mode="pullback",
눌림목 재진입)을 그대로 상속했는데, 이는 JPEX의 2슬롯/농축 구조에서
튜닝된 선택이지 SPEX의 7슬롯/분산 구조에서 "pullback이 vcp/donchian보다
낫다"고 직접 비교된 적은 없다(entry_mode는 지금까지 늘 서로 다른 전략
계열을 "정의"하는 용도로만 쓰였지, 같은 운영점에서 맞바꿔본 축이 아니다).

변형: SPEX_V4 기준(슬롯7/비중21.2%/entry80/rank_exit95/chandelier3.6/
breakeven2.0) 그대로 두고 entry_mode만 교체
  A) entry_mode="vcp"(고전 VCP, adx_threshold 기본값 사용)
  B) entry_mode="donchian"(돈치안 20일 브레이크아웃)
  C(기준) entry_mode="pullback"(SPEX_V4 그대로)

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v22_entry_mode_trades.json
사용법: python -m research.spex.test_spex_v22_entry_mode
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
    "A_vcp": {"entry_mode": "vcp", "adx_threshold": vcp.ADX_THRESHOLD},
    "B_donchian": {"entry_mode": "donchian", "donchian_period": 20},
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

print("참고(SPEX_V4=pullback 기준): 23.14%/-19.89%/1.164/347건", flush=True)

out_path = Path(__file__).parent / "spex_v22_entry_mode_trades.json"
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
