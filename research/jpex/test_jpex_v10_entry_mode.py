# -*- coding: utf-8 -*-
"""진입 "방식" 자체를 눌림목(pullback)에서 돈치안 신고가 돌파(donchian)로
바꿔봤을 때 JPEX의 나머지 필터·출구와 결합해 CAGR을 올릴 수 있는지 검증한다.
성장률 문턱 강화도 같이 시험한다.

목적:
  20~23단계에서 포지션 관리 축(피라미딩/챈들리어/리스크/슬롯/국면적응형)과
  진입 필터 엄격도(RS/촉매) 축이 전부 막혔다. 남은 큰 축은 진입 "방식"
  자체다 - 눌림목(현재 고점 대비 3~8% 되돌림에서 매수, 평균회귀 성향)
  대신 신고가 돌파(돈치안 채널, 추세추종 성향)로 바꾸면 완전히 다른 매매
  시점/다른 종목 집합을 잡는다. require_evan_stage2·min_revenue_growth·
  entry_rank_top_n 등 나머지 필터는 entry_mode와 독립적으로 동작하므로
  (vcp_strategy.py 진입 로직 참고) 그대로 유지하면서 "트리거"만 바꾸는
  깨끗한 비교가 가능하다.

배경:
  vcp_strategy.py 73차 주석("지인 진입 이력... 52주 신고가 돌파")에서 이미
  52주 돌파 아이디어가 제시된 적 있다(APEX_STAGE3_NEWHIGH52_PARAMS, 68차
  기준 - 미검증 상태로 남아있었음). JPEX_V6 기준으로 처음 검증한다.

변형: JPEX_V6_PARAMS 베이스로
  - donchian20: entry_mode="donchian", donchian_period=20
  - donchian50: donchian_period=50
  - donchian252: donchian_period=252(52주 신고가)
  - revenue25: entry_mode는 유지(pullback), min_revenue_growth 15->25,
    min_eps_growth_pct 20->30(성장률 문턱만 강화)

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v10_entry_mode_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v10_entry_mode
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
    "donchian20": {**vcp.JPEX_V6_PARAMS, "entry_mode": "donchian", "donchian_period": 20},
    "donchian50": {**vcp.JPEX_V6_PARAMS, "entry_mode": "donchian", "donchian_period": 50},
    "donchian252": {**vcp.JPEX_V6_PARAMS, "entry_mode": "donchian", "donchian_period": 252},
    "revenue25": {**vcp.JPEX_V6_PARAMS, "min_revenue_growth": 25.0, "min_eps_growth_pct": 30.0},
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

out_path = Path(__file__).parent / "jpex_v10_entry_mode_trades.json"
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
