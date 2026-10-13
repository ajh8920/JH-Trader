# -*- coding: utf-8 -*-
"""75단계 이후(2026-10-14): 지인이 "Elder Exit"가 알렉산더 엘더의 투자관에서
왔다고 직접 확인해준 뒤, 63단계(임펄스 시스템 - 손절 성격, 평균손익 +1.2%로
실패)와 다른 가설로 재시도한다. "Elder Exit"의 평균손익(+18.0%, 전체의 32%)이
뚜렷한 익절 패턴이라는 점에서, 엘더가 실제로 가장 즐겨 쓴 "채널(이동평균
봉투) 익절 - 강세 속에서도 채널 상단에 닿으면 추세반전을 기다리지 않고
판다"는 개념(elder_channel_exit, 신규 엔진 기능)이 더 맞는 방향이라는 가설.

변형: JPEX_V11_PARAMS 베이스로
  elder_channel_atr_mult = 2.0 / 2.5 / 3.0 (ma_period=22 고정)
  elder_channel_ma_period = 13 / 30 (atr_mult=2.5 고정)

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래/elderChannelExit
  발동건수+평균손익, research/jpex/jpex_v63_elder_channel_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v63_elder_channel
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

GRID = {
    "echan_mult2_0": {**vcp.JPEX_V11_PARAMS, "elder_channel_exit": True, "elder_channel_atr_mult": 2.0},
    "echan_mult2_5": {**vcp.JPEX_V11_PARAMS, "elder_channel_exit": True, "elder_channel_atr_mult": 2.5},
    "echan_mult3_0": {**vcp.JPEX_V11_PARAMS, "elder_channel_exit": True, "elder_channel_atr_mult": 3.0},
    "echan_ma13": {**vcp.JPEX_V11_PARAMS, "elder_channel_exit": True, "elder_channel_ma_period": 13},
    "echan_ma30": {**vcp.JPEX_V11_PARAMS, "elder_channel_exit": True, "elder_channel_ma_period": 30},
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
    ec_trades = [t for t in trades if t.get("exitReason") == "elderChannelExit"]
    ec_avg = sum(t["pnlPct"] for t in ec_trades) / len(ec_trades) if ec_trades else 0
    print(f"\n[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 손익비 {r.get('profitLossRatio')} 거래 {len(trades)}건"
          f"(연 {len(trades)/years_span:.1f}건) elderChannelExit건수 {len(ec_trades)} "
          f"평균손익 {ec_avg:.1f}%", flush=True)

print("\n=== 기준(V11, elder_channel_exit 없음): 41.52%/-29.43%/1.411/192건 ===", flush=True)

out_path = Path(__file__).parent / "jpex_v63_elder_channel_trades.json"
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
