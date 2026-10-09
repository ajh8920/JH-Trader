# -*- coding: utf-8 -*-
"""APEX68에 국면상실청산(exit_on_regime_loss)을 걸되, 이미 R배수 기준으로 어느 정도
번 포지션은 봐주는(regime_exit_min_r) 새 엔진 파라미터의 효과를 검증한다.

목적:
  test_apex68_exitgate.py에서 exit_on_regime_loss=True 단독으로는 2019/2022(승률·
  손익비 붕괴 구간)의 기대값을 양수 근처로 되돌렸지만, 국면이 잠깐 흔들리는
  틈에 2016의 대박 거래(평균승리 44.61%)까지 같이 끊어버려 그 해 기대값이
  +1.95%->-0.24%로 뒤집히고 블렌드 전체 CAGR도 34.38%->30.58%로 깎였다
  (research/jpex/RESULTS.md 16단계). "국면 신호는 이 거래가 이길지 모르고
  시장 전체만 본다"는 게 원인이므로, 이미 R배수로 유의미하게 번 포지션은
  국면과 무관하게 원래 트레일링에 맡기고, 아직 확신이 안 선 포지션만
  즉시 끊는 regime_exit_min_r을 vcp_strategy.py에 추가했다(이 스크립트가
  그 신규 파라미터의 첫 실사용 검증).

배경:
  vcp_strategy.py의 "시장국면 상실 시 보유 포지션 강제청산" 블록(_full_exit
  "regimeExit" 호출부) 바로 앞에서, current_r = (종가-평단)/riskPerShare가
  regime_exit_min_r보다 작을 때만 강제청산하도록 조건을 추가했다. None(기본값)
  이면 기존 동작(무조건 청산)과 완전히 동일 - 하위호환.

변형:
  APEX68_REGIMEEXIT_R{N} = {**APEX_STAGE3_BEST68_PARAMS, "exit_on_regime_loss": True,
  "regime_exit_min_r": N} - N=1.0(1R, 즉 최초 리스크만큼 번 지점)과 N=2.0(2R) 두
  값을 비교한다. exit_on_regime_loss만 켠 버전(N 없음, 이미 측정됨: CAGR 30.19%/
  MDD -30.87%)과 원본(CAGR 38.12%/MDD -51.87%)도 같이 표에 넣어 4원 비교.

데이터:
  로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물):
  콘솔에 CAGR/MDD/Calmar/승률/거래수 + 2016/2019/2022 연도별 분해(승률/평균승/
  평균패/기대값). 거래 전체를 research/jpex/apex68_regime_exit_minr_trades.json에
  저장(entryDate/exitDate/pnlPct, 변형별 키 구분) - 이후 JPEX(v64)와 재블렌드할 때
  재사용.

사용법:
  python -m research.jpex.test_apex68_regime_exit_minr
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

VARIANTS = {
    "r1.0": {**vcp.APEX_STAGE3_BEST68_PARAMS, "exit_on_regime_loss": True, "regime_exit_min_r": 1.0},
    "r2.0": {**vcp.APEX_STAGE3_BEST68_PARAMS, "exit_on_regime_loss": True, "regime_exit_min_r": 2.0},
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
        losses = [t for t in ts if t["pnlPct"] <= 0]
        wr = len(wins) / n * 100
        aw = sum(t["pnlPct"] for t in wins) / len(wins) if wins else 0
        al = sum(t["pnlPct"] for t in losses) / len(losses) if losses else 0
        exp = sum(t["pnlPct"] for t in ts) / n
        print(f"  {y}: {n}건 승률{wr:.1f}% 평균승{aw:.2f}% 평균패{al:.2f}% 기대값{exp:.2f}%/건")


years_span = (date.today() - date(2016, 1, 1)).days / 365.25
results = {}
for key, preset in VARIANTS.items():
    r = run(preset, f"APEX68+국면상실청산(regime_exit_min_r={preset['regime_exit_min_r']})")
    if "error" in r:
        print("오류:", r.get("error"), flush=True)
        sys.exit(1)
    results[key] = r
    final_val = r["equityCurve"][-1]["value"]
    cagr = ((final_val / SEED) ** (1 / years_span) - 1) * 100
    mdd = r.get("mddPct", 0)
    calmar = cagr / abs(mdd) if mdd else float("inf")
    trades = r["trades"]
    wins = [t for t in trades if t["pnlPct"] > 0]
    wr = len(wins) / len(trades) * 100 if trades else 0
    print(f"\n[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 거래 {len(trades)}건")
    print("연도별(2016/2019/2022 - 문제 구간):")
    year_breakdown(trades, ["2016", "2019", "2022"])

out_path = Path(__file__).parent / "apex68_regime_exit_minr_trades.json"
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
