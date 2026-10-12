# -*- coding: utf-8 -*-
"""61단계: 지인이 채팅에서 직접 공개한 전략 로직("대형주편입 시기 체크해서
조건맞으면 시총1,2위 중 한두개 매수해... 코스피가 코스닥보다 우위일 때
대형주 편입해")과 "지인의 슬롯은 4개"라는 정보를 그대로 구현한
`large_cap_overlay_*`(신규)를 V10 위에서 시험한다 - 메인 Top40 RS 눌림목
후보 선정과 완전히 별개의 슬롯(최대 2개 추가)으로, 코스피 N일 수익률이
코스닥보다 높을 때만 삼성전자/SK하이닉스를 그날 종가로 매수한다.

변형: JPEX_V10_PARAMS 베이스로 large_cap_overlay_tickers=["005930","000660"],
large_cap_overlay_slots=2, lookback_days = 60(기본) / 20 / 120

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부
필요. 코스닥 지수는 매 실행마다 야후에서 새로 받는다(네트워크 필요).

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v45_large_cap_overlay_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v45_large_cap_overlay
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

OVERLAY_TICKERS = ["005930", "000660"]
# 쿨다운만으로는(5/10일) 여전히 MDD -44~49%로 심한 휩쏘였다 - 61단계에서
# "국면전환 청산"(largeCapRegimeExit, 진입조건이 꺼지면 손절 도달 여부와
# 무관하게 즉시 청산)을 신규 추가했다. 이제 쿨다운은 작게/0으로 두고
# 국면전환 청산이 주된 리스크 관리가 되게 한다.
GRID = {
    "overlay_lb60_regimeexit": {**vcp.JPEX_V10_PARAMS, "large_cap_overlay_tickers": OVERLAY_TICKERS,
                                 "large_cap_overlay_slots": 2, "large_cap_overlay_lookback_days": 60,
                                 "large_cap_overlay_cooldown_days": 0},
    "overlay_lb120_regimeexit": {**vcp.JPEX_V10_PARAMS, "large_cap_overlay_tickers": OVERLAY_TICKERS,
                                  "large_cap_overlay_slots": 2, "large_cap_overlay_lookback_days": 120,
                                  "large_cap_overlay_cooldown_days": 0},
    "overlay_lb60_regimeexit_cd3": {**vcp.JPEX_V10_PARAMS, "large_cap_overlay_tickers": OVERLAY_TICKERS,
                                     "large_cap_overlay_slots": 2, "large_cap_overlay_lookback_days": 60,
                                     "large_cap_overlay_cooldown_days": 3},
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
    overlay_trades = [t for t in trades if t["code"] in OVERLAY_TICKERS]
    print(f"\n[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 손익비 {r.get('profitLossRatio')} 거래 {len(trades)}건"
          f"(연 {len(trades)/years_span:.1f}건) 오버레이거래 {len(overlay_trades)}건", flush=True)

print("\n=== 기준(V10, 오버레이 없음): 41.31%/-29.43%/1.404/191건 ===", flush=True)

out_path = Path(__file__).parent / "jpex_v45_large_cap_overlay_trades.json"
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
