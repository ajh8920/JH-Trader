# -*- coding: utf-8 -*-
"""90단계: 89단계(외국인 지분율 감소 청산, 단일거래 과최적화로 기각)와 같은
"보유 중 이탈 신호" 계열의 다음 시도 - DART 공시 데이터(이미 로컬에 수집
완료, 새 fetch 불필요)에서 악재성 공시(유상증자/전환사채/관리종목지정/
횡령배임/자본잠식/불성실공시)가 보유 종목에 뜨면 즉시 청산하는
negative_catalyst_exit_lookback_days를 신규 구현·시험한다.

75단계 "소형 헬스케어 촉매 오버레이"(진입 신호, 신호 품질 자체가 낮아 전부
기각)와는 반대 방향(청산 신호)이고 키워드도 다르다(유상증자/전환사채는
75단계의 POSITIVE_CATALYST_KEYWORDS에서 "방향 혼재"로 일부러 뺐던 것을
여기서는 역방향으로 재사용).

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부
  필요(disclosures_kr/*.parquet 포함 - 이미 수집됨, 외부 호출 없음).

변형: JPEX_V11_PARAMS 베이스로
  negative_catalyst_exit_lookback_days = 1 / 3 / 5 / 10

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_v67_negative_catalyst_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v67_negative_catalyst
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
from data_pipeline.fetch_disclosures_dart import DISCLOSURE_DIR

with open(PROJECT_DIR / "kr_stocks.json", encoding="utf-8") as f:
    kr_stocks = json.load(f)
shares_map = {s["code"]: s["shares"] for s in kr_stocks if s.get("shares")}
shareholder_rows = vcp.load_shareholder_rows(
    [SHAREHOLDER_KR_DIR / "majorstock_1of2.parquet", SHAREHOLDER_KR_DIR / "majorstock_2of2.parquet"])
with app.app_context():
    fundamentals_rows = vcp.load_fundamentals_rows(KrFundamental)
quarter_paths = sorted((FUND_KR_DIR.parent / "kr_quarter").glob("*.parquet"))
quarterly_rows = vcp.load_quarterly_rows(quarter_paths)
disclosure_paths = sorted(DISCLOSURE_DIR.glob("*.parquet"))
negative_catalyst_rows = vcp.load_negative_catalyst_dates(disclosure_paths)
total_events = sum(len(v) for v in negative_catalyst_rows.values())
print(f"[negative_catalyst] {len(negative_catalyst_rows)}종목, 총 {total_events}건 공시 로드", flush=True)

START, END = "2016-01-01", date.today().isoformat()
SEED = 500_000_000
fetch_fn = cached_fetch_ohlc_history_batches("KR", include_delisted=True, max_age_hours=99999)

GRID = {
    "lb1":  {**vcp.JPEX_V11_PARAMS, "negative_catalyst_exit_lookback_days": 1},
    "lb3":  {**vcp.JPEX_V11_PARAMS, "negative_catalyst_exit_lookback_days": 3},
    "lb5":  {**vcp.JPEX_V11_PARAMS, "negative_catalyst_exit_lookback_days": 5},
    "lb10": {**vcp.JPEX_V11_PARAMS, "negative_catalyst_exit_lookback_days": 10},
}


def run(preset, label):
    kw = dict(preset)
    kw.pop("market", None)
    kw.pop("default_seed", None)
    kw["quarterly_rows_by_code"] = quarterly_rows
    kw["negative_catalyst_by_code"] = negative_catalyst_rows
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
    nc = sum(1 for t in trades if t.get("exitReason") == "negativeCatalyst")
    print(f"\n[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 손익비 {r.get('profitLossRatio')} 거래 {len(trades)}건"
          f"(연 {len(trades)/years_span:.1f}건) negativeCatalyst청산 {nc}건", flush=True)

print("\n=== 기준(V11, negative_catalyst 비활성): 41.52%/-29.43%/1.411/192건 ===", flush=True)

out_path = Path(__file__).parent / "jpex_v67_negative_catalyst_trades.json"
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
