# -*- coding: utf-8 -*-
"""진입 방식 전환(돈치안)이 재앙이었으므로(24단계), 눌림목 진입은 그대로 두고
"출구 타이밍"의 세부 축(시간손절 제거, 본전이동/트레일링 시작을 더 이르게)을
시험한다.

목적:
  20~24단계에서 포지션 관리(피라미딩/챈들리어 폭/슬롯수/국면적응형), 진입
  필터 엄격도(RS/촉매/성장률), 진입 방식(돈치안) 전부 CAGR을 못 올렸다.
  아직 안 건드린 축: trail_activate_r(현재 2.0, 챈들리어가 언제부터
  활성화되는지)·breakeven_r(현재 3.0, trail_activate_r보다 커서 거의
  무의미하게 묻혀있던 값)·time_stop_days(현재 10일, 진행 안 되는 트레이드를
  며칠째 끊는지)를 동시에 재탐색한다.

배경:
  JPEX_V6_PARAMS는 breakeven_r=3.0 > trail_activate_r=2.0이라 챈들리어가
  먼저 활성화돼 breakeven_r 로직이 거의 죽은 코드처럼 묻혀 있다(2R에서
  이미 챈들리어가 본전보다 위에 있을 가능성이 높음). trail_activate_r을
  낮추면(더 일찍 트레일링 시작) 승리 포지션을 더 빨리 보호하면서 회전율도
  오를 수 있고, time_stop_days를 늘리거나 없애면 느리게 터지는 승자
  (257720처럼 큰 수익을 내는 종목이 초반엔 느릴 수 있음)를 조기에 끊지
  않을 수 있다.

변형: JPEX_V6_PARAMS 베이스로
  - trail_early: trail_activate_r 2.0->1.0, breakeven_r 3.0->1.5(순서를
    정상화 - breakeven이 먼저, 트레일링이 나중)
  - time_stop_none: time_stop_days=None(시간손절 비활성)
  - time_stop20: time_stop_days 10->20(두 배로 느슨하게)
  - combo: trail_early + time_stop20 동시 적용

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v11_exit_timing_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v11_exit_timing
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

TRAIL_EARLY = {"trail_activate_r": 1.0, "breakeven_r": 1.5}
GRID = {
    "trail_early": {**vcp.JPEX_V6_PARAMS, **TRAIL_EARLY},
    "time_stop_none": {**vcp.JPEX_V6_PARAMS, "time_stop_days": None},
    "time_stop20": {**vcp.JPEX_V6_PARAMS, "time_stop_days": 20},
    "combo": {**vcp.JPEX_V6_PARAMS, **TRAIL_EARLY, "time_stop_days": 20},
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

out_path = Path(__file__).parent / "jpex_v11_exit_timing_trades.json"
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
