# -*- coding: utf-8 -*-
"""JPEX_V6_PARAMS에 신규 regime_adaptive_params(국면별로 피라미딩/챈들리어/
초기리스크/비중상한/진입순위를 다르게 쓰는 기능)를 적용해 CAGR 50%를
레버리지 없이 달성할 수 있는지 검증한다. 0번째 변형(baseline)은
regime_adaptive_params=None으로 돌려 기존 V6와 완전히 동일한 결과가
나오는지 하위호환성부터 확인한다.

목적:
  "레버리지는 절대 쓰지 않는다. 월별 분석으로 특정 레짐에서 어떤 대응을
  해야 할지 분석·개발하라. 목표는 CAGR 50% 이상"이라는 지시에 따라, 피라미딩
  한도/챈들리어 폭을 전체 기간에 똑같이 키우면 역효과였던 것
  (test_jpex_v6_concentration.py, research/jpex/RESULTS.md 20단계)을
  "국면이 좋을 때만 선택적으로" 적용하는 신규 엔진 기능
  (regime_adaptive_params, vcp_strategy.run_vcp_backtest)으로 재시도한다.

배경:
  국면은 재평가일마다 3단계로 분류한다(run_vcp_backtest의 regime_adaptive_params
  docstring 참고) - weak(국면상실, 코스피<200일선 또는 200일선 비상승),
  neutral(국면 양호하지만 200일선 대비 10% 미만 상승), strong(200일선 대비
  10%+ 위, 뚜렷한 강세). "weak"에는 방어(피라미딩 축소·챈들리어 타이트·
  리스크 축소), "strong"에는 공격(피라미딩 확대·챈들리어 와이드)을 동시에
  적용한다 - 정적으로 둘 다 키운 건 실패했지만, weak 구간의 손실을 같이
  줄이면서 strong 구간만 선택적으로 키우면 순효과가 날 수 있다는 가설.

변형: JPEX_V6_PARAMS 베이스로
  0. baseline: regime_adaptive_params=None(하위호환 확인용, 기존 V6와 동일해야 함)
  1. defense_only: weak에서만 방어(피라미딩2, 챈들리어2.0, 리스크2.5%), strong은
     그대로(V6 기본값)
  2. offense_only: strong에서만 공격(피라미딩6, 챈들리어4.5), weak는 그대로
  3. both: weak 방어 + strong 공격 동시 적용

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/거래 + 연도별(2019/2022 포함) 요약,
  research/jpex/jpex_v7_regime_adaptive_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v7_regime_adaptive
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

DEFENSE = {"pyramid_max_count": 2, "chandelier_atr_mult": 2.0, "max_initial_risk_pct": 2.5}
OFFENSE = {"pyramid_max_count": 6, "chandelier_atr_mult": 4.5}

GRID = {
    "baseline": {**vcp.JPEX_V6_PARAMS},
    "defense_only": {**vcp.JPEX_V6_PARAMS, "regime_adaptive_params": {"weak": DEFENSE}},
    "offense_only": {**vcp.JPEX_V6_PARAMS, "regime_adaptive_params": {"strong": OFFENSE}},
    "both": {**vcp.JPEX_V6_PARAMS, "regime_adaptive_params": {"weak": DEFENSE, "strong": OFFENSE}},
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

out_path = Path(__file__).parent / "jpex_v7_regime_adaptive_trades.json"
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
