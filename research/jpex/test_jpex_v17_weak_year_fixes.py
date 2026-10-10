# -*- coding: utf-8 -*-
"""JPEX_V7_PARAMS의 약세 연도(2019/2022/2025) 월별 거래 로그를 종목·날짜
단위로 뜯어본 결과(사용자 요청: "수익률이 떨어지는 연도들을 월단위로 분석해
어떤 부분에서 큰 수익을 얻어내지 못했는지 분석하라"), 세 해가 서로 다른
이유로 부진했다:

  - 2019: 11월에 056000이 크게 올라 포트폴리오가 그 달 +16.54%까지 뛰었지만,
    12월 2일->3일 단 하루에 갭하락으로 급락해 최종 -9.8% 손실로 청산 -
    11월에 포착한 대형 미실현 수익을 12월 초 갭 전에 잠그지 못함.
  - 2022: 거래 32건 중 대부분이 작은 손실(-0.4~-5.8%) - 8월 한 달만도 5건
    연속 손절로 -6.84%. 대형 승자를 "놓친" 게 아니라 약세장에서 작은
    손절이 반복 누적된 문제(4월에 336570을 두 번 조기 정리한 건 합리적
    판단 - 그 종목은 2023년에야 대박이 났다).
  - 2025: 127120(+125.2%)·458870(+37.4%+37.2%) 두 개의 대형 승자를 실제로
    잡았는데도, 그 사이 15건 이상의 소액 손실(-0.8~-9.6%)이 쌓여 연간
    수익을 거의 0%로 깎아먹었다 - "놓친 큰 수익"이 아니라 "큰 수익을
    잡고도 주변 소액 손실이 갚아먹은" 문제.

목적: 2019/12월형(미실현 수익 반납)에는 분할익절(partial_profit_fraction,
현재 0=비활성)로 일부 수익을 R배수 도달 시 미리 현금화해 대응하고,
2025년형(소액 손실 반복)에는 정체청산(flat_halt_days, 현재 None=비활성)
으로 진행 안 되는 포지션을 더 빨리 정리해 회전율을 높이는 두 가지를
시험한다.

변형: JPEX_V7_PARAMS 베이스로
  - partial30: partial_profit_fraction=0.3(partial_profit_r=2.0 도달 시 30% 익절)
  - partial50: partial_profit_fraction=0.5
  - flat_halt5: flat_halt_days=5(5거래일간 ±0.5% 이내면 정체청산)
  - combo: partial30 + flat_halt5 동시 적용

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래 + 2019/2022/2025
  연도별 요약, research/jpex/jpex_v17_weak_year_fixes_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v17_weak_year_fixes
"""
import io
import json
import sys
import time
from collections import defaultdict
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

GRID = {
    "partial30": {**vcp.JPEX_V7_PARAMS, "partial_profit_fraction": 0.3},
    "partial50": {**vcp.JPEX_V7_PARAMS, "partial_profit_fraction": 0.5},
    "flat_halt5": {**vcp.JPEX_V7_PARAMS, "flat_halt_days": 5, "flat_halt_threshold_pct": 0.5},
    "combo": {**vcp.JPEX_V7_PARAMS, "partial_profit_fraction": 0.3,
              "flat_halt_days": 5, "flat_halt_threshold_pct": 0.5},
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


def year_breakdown(trades, years_of_interest):
    by_year = defaultdict(list)
    for t in trades:
        by_year[t["exitDate"][:4]].append(t)
    for y in years_of_interest:
        ts = by_year.get(y, [])
        n = len(ts)
        if n == 0:
            print(f"  {y}: 거래 0건")
            continue
        wins = [t for t in ts if t["pnlPct"] > 0]
        exp = sum(t["pnlPct"] for t in ts) / n
        print(f"  {y}: {n}건 승률{len(wins)/n*100:.1f}% 기대값{exp:.2f}%/건")


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
    year_breakdown(trades, ["2019", "2022", "2025"])

out_path = Path(__file__).parent / "jpex_v17_weak_year_fixes_trades.json"
out_data = {"seed": SEED}
for key, r in results.items():
    out_data[f"{key}_curve"] = r["equityCurve"]
    out_data[f"{key}_trades"] = [
        {"code": t["code"], "entryDate": t["entryDate"], "exitDate": t["exitDate"], "pnlPct": t["pnlPct"]}
        for t in r["trades"]
    ]
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out_data, f)
print(f"\n저장 완료: {out_path}", flush=True)
