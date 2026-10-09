# -*- coding: utf-8 -*-
"""JPEX_V6_PARAMS의 포지션 사이징/피라미딩/트레일링 축은 양방향으로 전부
막혔다(20~22단계). 이번엔 "진입 신호 자체의 질"을 높여 더 좋은(더 큰 승자가
될 가능성이 높은) 종목만 거르는 축을 시험한다.

목적:
  20단계(승자를 더 크게/덜 반납), 21단계(국면 적응형), 22단계(슬롯 확대)
  전부 CAGR을 못 올렸다 - 2슬롯/50%비중/피라미딩4/챈들리어3.0이 이미
  다차원 로컬 최적점임이 재확인됐다. 포지션 관리 축을 전부 소진했으므로
  "어떤 종목을 들어가는가"(진입 신호) 축으로 전환한다 - RS 하한을 올려
  모멘텀이 더 강한 종목만 거르거나, 공시 촉매(require_catalyst)를 추가해
  "지금 당장 시장이 주목하는" 종목만 거른다.

배경:
  JPEX는 evan_params={"min_rs": 62.0}를 쓴다(APEX 기본형은 85.0 - 더 엄격).
  require_catalyst는 APEX 기본형에서 승률을 23.0%->30.3%로 올렸지만 거래를
  382->119건으로 크게 줄였다(catalyst_dates_by_code를 제대로 로드해야
  작동함 - strategy_specs.py 커밋 참고, 이번에도 제대로 로드한다). JPEX의
  더 나은 출구(피라미딩/챈들리어/과열청산/국면방어)와 결합하면 다른 결과가
  나올 수 있다는 가설.

변형: JPEX_V6_PARAMS 베이스로
  - rs75: evan_params min_rs 62->75
  - rs85: evan_params min_rs 62->85(APEX 기본형과 동일 엄격도)
  - catalyst: require_catalyst=True 추가(min_rs 62 유지)
  - catalyst_rs75: require_catalyst=True + min_rs 75

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부
  필요. require_catalyst 변형은 disclosures_kr parquet도 필요(공시 데이터).

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v9_entry_quality_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v9_entry_quality
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
catalyst_dates_by_code = vcp.load_catalyst_dates(sorted((FUND_KR_DIR.parent / "disclosures_kr").glob("*.parquet")))
print(f"공시 촉매 보유 종목수: {len(catalyst_dates_by_code)}", flush=True)

START, END = "2016-01-01", date.today().isoformat()
SEED = 500_000_000
fetch_fn = cached_fetch_ohlc_history_batches("KR", include_delisted=True, max_age_hours=99999)

GRID = {
    "rs75": {**vcp.JPEX_V6_PARAMS, "evan_params": {"min_rs": 75.0}},
    "rs85": {**vcp.JPEX_V6_PARAMS, "evan_params": {"min_rs": 85.0}},
    "catalyst": {**vcp.JPEX_V6_PARAMS, "require_catalyst": True,
                 "catalyst_dates_by_code": catalyst_dates_by_code},
    "catalyst_rs75": {**vcp.JPEX_V6_PARAMS, "require_catalyst": True,
                       "catalyst_dates_by_code": catalyst_dates_by_code, "evan_params": {"min_rs": 75.0}},
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

out_path = Path(__file__).parent / "jpex_v9_entry_quality_trades.json"
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
