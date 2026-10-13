# -*- coding: utf-8 -*-
"""76단계 이후: elder_channel_exit(전량청산)는 교차점 없이 전부 실패했다
(research/jpex/RESULTS.md 76단계 - 배수를 넓혀도 V11에 수렴만 할 뿐 못 넘고,
극단에서는 거대 단일 승자를 깎아 오히려 더 나쁨). 엘더가 실제로 더 자주
권하는 "채널에서 일부만 익절, 나머지는 트레일링으로 계속 보유"(elder_channel_
partial_fraction, 신규)로 재시도한다 - 전량청산 대신 부분익절이면 "진짜 거대
승자"를 깎지 않고도 평균 승률/수익을 조금씩 올릴 수 있다는 가설.

변형: JPEX_V11_PARAMS 베이스로 elder_channel_atr_mult=2.5(가장 자주
발동했던 기본 배수) + elder_channel_partial_fraction = 0.25 / 0.4.

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v64_elder_channel_partial_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v64_elder_channel_partial
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
    "echanP_25_mult2_5": {**vcp.JPEX_V11_PARAMS, "elder_channel_exit": True,
                           "elder_channel_atr_mult": 2.5, "elder_channel_partial_fraction": 0.25},
    "echanP_40_mult2_5": {**vcp.JPEX_V11_PARAMS, "elder_channel_exit": True,
                           "elder_channel_atr_mult": 2.5, "elder_channel_partial_fraction": 0.4},
    "echanP_25_mult3_5": {**vcp.JPEX_V11_PARAMS, "elder_channel_exit": True,
                           "elder_channel_atr_mult": 3.5, "elder_channel_partial_fraction": 0.25},
    "echanP_40_mult3_5": {**vcp.JPEX_V11_PARAMS, "elder_channel_exit": True,
                           "elder_channel_atr_mult": 3.5, "elder_channel_partial_fraction": 0.4},
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
    ec_trades = [t for t in trades if t.get("exitReason") == "elderChannelPartial"]
    print(f"\n[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 손익비 {r.get('profitLossRatio')} 거래 {len(trades)}건"
          f"(연 {len(trades)/years_span:.1f}건) elderChannelPartial건수 {len(ec_trades)}", flush=True)

print("\n=== 기준(V11): 41.52%/-29.43%/1.411/192건 ===", flush=True)

out_path = Path(__file__).parent / "jpex_v64_elder_channel_partial_trades.json"
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
