# -*- coding: utf-8 -*-
"""JPEX_V6_PARAMS의 월별 분해에서 발견한 "집중 베팅(2슬롯+피라미딩)이 대형 승자를
만드는 동시에 급락 월의 원인이기도 하다"는 패턴을 근거로, 대형 승자를 더 크게/더
오래 태우는 방향(피라미딩 한도·포지션 비중 상한 확대)이 CAGR을 50%대로 밀어올릴 수
있는지 검증한다.

목적:
  사용자 요청: "월단위로 분석하여 결과값이 미흡한 부분을 완화하고 CAGR을 50%
  이상으로 올려보라." JPEX_V6_PARAMS(research/jpex/test_jpex_v5_grid.py, "
  rank40_r1.25")의 월별 자산곡선을 뜯어본 결과, 최악의 달들(2019-12 -18.39%,
  2023-09 -11.19%)이 전부 "피라미딩으로 크게 불린 단일 포지션"과 관련돼
  있었다 - 2019-12은 그 포지션이 급락 갭으로 손절되며 하루에 포트폴리오
  -16.9%, 2023-09은 336570(+67.81%, 4개월 보유) 거대 승자의 미실현 수익
  반납이었다. 같은 구조(2슬롯 집중+피라미딩)가 2024-05(+82.19%)·2026-01
  (+55.58%) 같은 CAGR 견인 월의 원천이기도 하므로, "나쁜 달 방어"보다
  "대형 승자를 더 키우는" 쪽이 CAGR 50% 목표에 더 직접적인 레버로 보인다.

배경:
  JPEX_V6_PARAMS는 pyramid_max_count=4, max_position_weight_pct=50.0이다.
  둘 다 올리면 한 종목에 더 많이, 더 크게 태울 수 있다 - 당연히 MDD도
  커질 위험이 있어 Calmar 트레이드오프를 같이 본다.

변형: JPEX_V6_PARAMS 베이스로 pyramid_max_count x max_position_weight_pct
  2x2 격자(4x50=V6 기준점, 재실행 안 함):
  - pyramid6_w65: pyramid_max_count=6, max_position_weight_pct=65.0
  - pyramid6_w50: pyramid_max_count=6, max_position_weight_pct=50.0(그대로)
  - pyramid4_w65: pyramid_max_count=4(그대로), max_position_weight_pct=65.0

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/거래 + 월별 최악 5개월 요약,
  research/jpex/jpex_v6_concentration_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v6_concentration
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
    "pyramid6_w65": {**vcp.JPEX_V6_PARAMS, "pyramid_max_count": 6, "max_position_weight_pct": 65.0},
    "pyramid6_w50": {**vcp.JPEX_V6_PARAMS, "pyramid_max_count": 6},
    "pyramid4_w65": {**vcp.JPEX_V6_PARAMS, "max_position_weight_pct": 65.0},
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

    by_month = {}
    for p in r["equityCurve"]:
        by_month[p["date"][:7]] = p["value"]
    months = sorted(by_month)
    prev = SEED
    month_rets = []
    for ym in months:
        v = by_month[ym]
        month_rets.append((ym, (v / prev - 1) * 100))
        prev = v
    worst5 = sorted(month_rets, key=lambda x: x[1])[:5]

    print(f"\n[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 거래 {len(trades)}건(연 {len(trades)/years_span:.1f}건)", flush=True)
    print("최악 5개월:", ", ".join(f"{ym}({r_:.1f}%)" for ym, r_ in worst5), flush=True)

out_path = Path(__file__).parent / "jpex_v6_concentration_trades.json"
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
