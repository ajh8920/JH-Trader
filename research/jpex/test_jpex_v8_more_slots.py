# -*- coding: utf-8 -*-
"""JPEX_V6_PARAMS의 슬롯 수를 늘려(비중은 총 노출을 유지하도록 반비례 축소)
여러 대형 승자를 "순차적으로"가 아니라 "동시에" 잡을 수 있는지 검증한다.

목적:
  20~21단계에서 "승자를 더 크게 태우기"(피라미딩 확대/와이드 트레일링, 정적
  이든 국면조건부든) 전부 실패했다. 월별 분해에서 257720(+263.11%)·336570
  (+67.81%) 같은 대형 승자가 실제로 존재하는 걸 확인했으므로, "한 포지션을
  더 키우기"가 아니라 "슬롯을 늘려 이런 대형 승자를 여러 개 동시에 보유"하는
  쪽으로 축을 바꾼다. 2슬롯 구조에서는 새 셋업이 나와도 기존 2개가 꽉 차
  있으면 놓칠 수 있다 - 슬롯을 늘리면 그런 손실을 줄일 수 있다는 가설.

배경:
  슬롯을 늘리면서 슬롯당 비중(max_position_weight_pct)을 그만큼 줄여 "총
  노출"은 거의 동일하게 유지한다(2슬롯x50%=100% 상당 -> 4슬롯x25%=100%
  상당) - 이러면 "한 포지션에 더 많이 거는" 효과(이미 실패 확인)가 아니라
  순수하게 "동시에 보유 가능한 종목 수"만 바뀐다.

변형: JPEX_V6_PARAMS 베이스로 max_positions x max_position_weight_pct
  - slots3: max_positions=3, max_position_weight_pct=33.0
  - slots4: max_positions=4, max_position_weight_pct=25.0
  - slots6: max_positions=6, max_position_weight_pct=17.0

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/거래 + 2019/2022 요약,
  research/jpex/jpex_v8_more_slots_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v8_more_slots
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
    "slots3": {**vcp.JPEX_V6_PARAMS, "max_positions": 3, "max_position_weight_pct": 33.0},
    "slots4": {**vcp.JPEX_V6_PARAMS, "max_positions": 4, "max_position_weight_pct": 25.0},
    "slots6": {**vcp.JPEX_V6_PARAMS, "max_positions": 6, "max_position_weight_pct": 17.0},
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
          f"승률 {wr:.1f}% 거래 {len(trades)}건(연 {len(trades)/years_span:.1f}건)", flush=True)
    year_breakdown(trades, ["2019", "2022"])

out_path = Path(__file__).parent / "jpex_v8_more_slots_trades.json"
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
