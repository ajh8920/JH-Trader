# -*- coding: utf-8 -*-
"""89단계: 사용자 제안("종목별로 외국인 비율 감소율 같은 데이터 사용은
어떤가요?") - 외국인 지분율이 lookback_days 전보다 문턱(%p) 이상 빠진 채
N일 연속 유지되면 청산하는 foreign_exit_* 가설(rank_exit_top_n과 완전히
같은 구조, "RS 순위가 식으면 끊는다" -> "외국인이 발을 빼면 끊는다").

데이터: data_pipeline/fetch_foreign_ownership_kr.py로 수집한
  000.Data/foreign_ownership/kr/foreign_ownership.parquet. 전체 2,686종목은
  종목당 ~39초가 걸려 전체 수집에 ~29시간이 필요해서, 1차 검증은 V11이
  실제로 보유했던 120개 종목(strategy_trades/jpex_v11.json)만 먼저 받아
  진행한다 - foreignExit은 보유 중인 포지션에만 적용되는 청산 신호라
  V11 기준 거래 재현에는 이 120종목 데이터만으로 충분하다(이 가설이
  유효하다고 확인되면 전체 유니버스 수집으로 넘어가 신규 진입 종목까지
  반영한다).

변형: JPEX_V11_PARAMS 베이스로
  decline_pct(lookback=20, persist=2 고정) = -2 / -3 / -5 / -8
  persist_days(decline_pct=-3 고정, lookback=20) = 1 / 3
  lookback_days(decline_pct=-3, persist=2 고정) = 10 / 30

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v66_foreign_exit_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v66_foreign_exit
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
foreign_rows = vcp.load_foreign_ownership_rows(DATA_ROOT / "foreign_ownership" / "kr" / "foreign_ownership.parquet")
print(f"[foreign_ownership] {len(foreign_rows)}종목 데이터 로드", flush=True)

START, END = "2016-01-01", date.today().isoformat()
SEED = 500_000_000
fetch_fn = cached_fetch_ohlc_history_batches("KR", include_delisted=True, max_age_hours=99999)

GRID = {
    "decline_m2":  {**vcp.JPEX_V11_PARAMS, "foreign_exit_decline_pct": -2, "foreign_exit_lookback_days": 20, "foreign_exit_persist_days": 2},
    "decline_m3":  {**vcp.JPEX_V11_PARAMS, "foreign_exit_decline_pct": -3, "foreign_exit_lookback_days": 20, "foreign_exit_persist_days": 2},
    "decline_m5":  {**vcp.JPEX_V11_PARAMS, "foreign_exit_decline_pct": -5, "foreign_exit_lookback_days": 20, "foreign_exit_persist_days": 2},
    "decline_m8":  {**vcp.JPEX_V11_PARAMS, "foreign_exit_decline_pct": -8, "foreign_exit_lookback_days": 20, "foreign_exit_persist_days": 2},
    "m3_persist1": {**vcp.JPEX_V11_PARAMS, "foreign_exit_decline_pct": -3, "foreign_exit_lookback_days": 20, "foreign_exit_persist_days": 1},
    "m3_persist3": {**vcp.JPEX_V11_PARAMS, "foreign_exit_decline_pct": -3, "foreign_exit_lookback_days": 20, "foreign_exit_persist_days": 3},
    "m3_lb10":     {**vcp.JPEX_V11_PARAMS, "foreign_exit_decline_pct": -3, "foreign_exit_lookback_days": 10, "foreign_exit_persist_days": 2},
    "m3_lb30":     {**vcp.JPEX_V11_PARAMS, "foreign_exit_decline_pct": -3, "foreign_exit_lookback_days": 30, "foreign_exit_persist_days": 2},
}


def run(preset, label):
    kw = dict(preset)
    kw.pop("market", None)
    kw.pop("default_seed", None)
    kw["quarterly_rows_by_code"] = quarterly_rows
    kw["foreign_ownership_by_code"] = foreign_rows
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
    fx = sum(1 for t in trades if t.get("exitReason") == "foreignExit")
    print(f"\n[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 손익비 {r.get('profitLossRatio')} 거래 {len(trades)}건"
          f"(연 {len(trades)/years_span:.1f}건) foreignExit청산 {fx}건", flush=True)

print("\n=== 기준(V11, foreign_exit 비활성): 41.52%/-29.43%/1.411/192건 ===", flush=True)

out_path = Path(__file__).parent / "jpex_v66_foreign_exit_trades.json"
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
