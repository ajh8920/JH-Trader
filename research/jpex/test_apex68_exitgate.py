# -*- coding: utf-8 -*-
"""APEX68(블렌드의 고CAGR 축)에 '국면상실청산(exit_on_regime_loss)'만 추가했을 때
연도별 손익비/기대값이 개선되는지 단독 검증한다.

목적:
  JPEX(v64)+APEX68 블렌드의 연도별 분해에서, 2019/2022년이 "승률도 낮고
  수익률도 낮은" 구간으로 확인됐다(RESULTS.md 16단계). 두 해를 JPEX/APEX68로
  쪼개보면 APEX68(국면 필터 전혀 없음)이 주범이다 - 2022년 APEX68 단독
  기대값 -3.78%/건(21건), 2019년 -5.19%/건(12건, 평균승리가 겨우 0.53%).
  반면 JPEX는 같은 두 해에 2022년 거래 0건(국면게이팅으로 전부 회피),
  2019년 기대값 +0.71%(소폭이지만 양수)로 선방했다 - 국면 필터가 실제로
  작동한다는 뜻.

배경:
  JPEX_V1_PARAMS(=APEX68 + gate_entries_on_regime + exit_on_regime_loss 둘
  다 켠 버전)은 이미 검증됐지만 신규진입까지 막아 거래량이 크게 줄었다
  (1차 실측 연 7.4건). 블렌드에서 APEX68의 역할은 "고CAGR/고거래량" 축이므로
  신규진입을 막는 건 그 역할을 없애버린다. 그래서 진입은 그대로 열어두고
  "청산만" 국면상실 시 강제로 끊는 쪽(exit_on_regime_loss만 True)을 따로
  검증해 거래량을 희생하지 않으면서 손실을 억제할 수 있는지 본다.

변형:
  APEX68_EXITGATE_PARAMS = {**APEX_STAGE3_BEST68_PARAMS, "exit_on_regime_loss": True}
  (gate_entries_on_regime은 건드리지 않음 - 기본 False 그대로, 신규진입 자유).

데이터:
  로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/(대량보유·
  재무, STOCK_DATA_ROOT 환경변수) 전부 필요. include_delisted=True.

산출(결과물):
  콘솔에 전체 CAGR/MDD/Calmar/승률/거래수 + 연도별(승률/평균승/평균패/
  기대값) 출력, 비교 대상(순수 APEX68) 수치도 같이 찍는다. 거래 전체를
  research/jpex/apex68_exitgate_trades.json에 저장(entryDate/exitDate/
  pnlPct 포함) - 이후 블렌드 재계산에 재사용.

사용법:
  python -m research.jpex.test_apex68_exitgate
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

APEX68_EXITGATE_PARAMS = {**vcp.APEX_STAGE3_BEST68_PARAMS, "exit_on_regime_loss": True}


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


def days(a, b):
    ay, am, ad = map(int, a.split("-"))
    by, bm, bd = map(int, b.split("-"))
    return (date(by, bm, bd) - date(ay, am, ad)).days


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


r1 = run(APEX68_EXITGATE_PARAMS, "APEX68+국면상실청산")
r2 = run(vcp.APEX_STAGE3_BEST68_PARAMS, "APEX68(원본, 비교용)")

if "error" in r1 or "error" in r2:
    print("오류:", r1.get("error"), r2.get("error"), flush=True)
    sys.exit(1)

years_span = (date.today() - date(2016, 1, 1)).days / 365.25
for label, r in [("APEX68+국면상실청산", r1), ("APEX68(원본)", r2)]:
    final_val = r["equityCurve"][-1]["value"]
    cagr = ((final_val / SEED) ** (1 / years_span) - 1) * 100
    mdd = r.get("mddPct", 0)
    calmar = cagr / abs(mdd) if mdd else float("inf")
    trades = r["trades"]
    wins = [t for t in trades if t["pnlPct"] > 0]
    wr = len(wins) / len(trades) * 100 if trades else 0
    print(f"\n[{label}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 거래 {len(trades)}건")
    print("연도별(2016/2019/2022 - 문제 구간):")
    year_breakdown(trades, ["2016", "2019", "2022"])

out_path = Path(__file__).parent / "apex68_exitgate_trades.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({
        "seed": SEED,
        "apex68_exitgate": r1["equityCurve"],
        "apex68_exitgate_trades": [
            {"code": t["code"], "entryDate": t["entryDate"], "exitDate": t["exitDate"], "pnlPct": t["pnlPct"]}
            for t in r1["trades"]
        ],
    }, f)
print(f"\n저장 완료: {out_path}", flush=True)
