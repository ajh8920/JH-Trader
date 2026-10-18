# -*- coding: utf-8 -*-
"""SPEX 26단계("야근ㄱㄱ" 계속, 2026-10-19) - dividend_dates_by_code(배당락
갭 보정, 손절 오판 방지)를 SPEX 백테스트에 최초로 연결.

배경: 전체 run_vcp_backtest 파라미터 174개를 JPEX/SPEX RESULTS.md 전체와
대조해 "언급 0건"인 축을 추렸다. 대부분은 이미 죽은/기각된 기능의
세부옵션이었지만 dividend_dates_by_code 계열(배당락으로 전일대비
-6%~-20% 갭하락한 날, 실제 악재가 아니라 배당락 기계적 하락이면 손절을
보류)은 **production(paper_trading.py run_anonymous_daily_step)에는 이미
연결돼 실전에서 매일 쓰이고 있는데, SPEX/JPEX 백테스트 연구 스크립트
전부가 이 인자를 빼놓고 돌렸다** - 즉 지금까지의 모든 SPEX_V1~V25
백테스트 숫자는 실전과 다른 조건(배당락 보정 꺼짐)으로 계산된 것.
이건 "새 레버 발굴"이 아니라 "백테스트-실전 정합성 확인"이 주목적.

변형: SPEX_V4 기준 dividend_dates_by_code 연결 여부
  A) 연결(실전과 동일 조건)
  B(기준) 미연결(SPEX_V1~V25 지금까지 전부)

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원,
disclosures_kr parquet(2016~현재) 전체 로드.
산출: research/spex/spex_v26_dividend_gap_trades.json
사용법: python -m research.spex.test_spex_v26_dividend_gap
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
dividend_paths = sorted((FUND_KR_DIR.parent / "disclosures_kr").glob("*.parquet"))
dividend_dates_by_code = vcp.load_dividend_dates(dividend_paths)
print(f"배당 공시 로드: {len(dividend_dates_by_code)}종목", flush=True)

START, END = "2016-01-01", date.today().isoformat()
SEED = 500_000_000
fetch_fn = cached_fetch_ohlc_history_batches("KR", include_delisted=True, max_age_hours=99999)

BASE = {**vcp.SPEX_V4_PARAMS}
VARIANTS = {
    "A_with_dividend": {"dividend_dates_by_code": dividend_dates_by_code},
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

print("참고(SPEX_V4=기준, 배당보정 미연결): 23.14%/-19.89%/1.164/347건", flush=True)

out_path = Path(__file__).parent / "spex_v26_dividend_gap_trades.json"
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
