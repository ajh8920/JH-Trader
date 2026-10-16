# -*- coding: utf-8 -*-
"""SPEX 15단계(14단계 후속, "야근ㄱㄱ" - 성능이 아니라 견고성 검증) -
14단계에서 SPEX_V3(chandelier_atr_mult=3.6)이 세 조건을 전부 만족했지만,
개선분의 대부분이 거래 1건(033100 제룡전기, 2024-03-26 진입)에서 나와
단일거래 의존도가 이전 단계들보다 높다는 게 확인됐다(RESULTS.md 14단계).
RULES.md R13(강한 추세장 구간 + 횡보/하락장 구간, 최소 두 구간 확인)
방법론대로, 이 거래가 아예 없는 기간만 떼서 chandelier=3.0(SPEX_V2)과
3.6(SPEX_V3)을 다시 비교한다 - 033100 없이도 3.6이 여전히 3.0보다
나은지가 핵심 질문(나으면 "진짜 레버", 역전되면 "그 거래 하나에만
기댄 착시").

변형:
  A. END=2023-12-31(033100 진입일 이전, 2024년 전체 제외)로 chand=3.0
     vs chand=3.6 비교
  B. START=2024-01-01(033100이 포함된 최근 구간만)로 chand=3.0 vs 3.6
     비교 - 혹시 최근 구간 자체가 구조적으로 달라졌는지 참고용

데이터: KR, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v15_robustness_trades.json
사용법: python -m research.spex.test_spex_v15_robustness
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

SEED = 500_000_000
fetch_fn = cached_fetch_ohlc_history_batches("KR", include_delisted=True, max_age_hours=99999)

BASE = {**vcp.SPEX_V2_PARAMS}  # 슬롯7/비중21.2/entry80/rank_exit95, chandelier은 아래서 지정
PERIODS = {
    "pre2024": ("2016-01-01", "2023-12-31"),
    "post2024": ("2024-01-01", date.today().isoformat()),
}
CHAND = {"c30": 3.0, "c36": 3.6}


def run(preset, label, start, end):
    kw = dict(preset)
    kw.pop("market", None)
    kw.pop("default_seed", None)
    kw["quarterly_rows_by_code"] = quarterly_rows
    vcp.MAX_POSITION_WEIGHT_PCT = kw.get("max_position_weight_pct", 30.0)
    print(f"=== {label} ({start}~{end}) 실행 ===", flush=True)
    t0 = time.time()
    r = vcp.run_vcp_backtest(
        "KR", start, end, seed=SEED, fetch_fn=fetch_fn, shares_map=shares_map,
        shareholder_rows_by_code=shareholder_rows, fundamentals_rows_by_code=fundamentals_rows, **kw)
    print(f"{label} 완료 ({round(time.time() - t0)}s) trades={len(r.get('trades', []))}", flush=True)
    return r


results = {}
for pname, (start, end) in PERIODS.items():
    years_span = (date.fromisoformat(end) - date.fromisoformat(start)).days / 365.25
    for cname, mult in CHAND.items():
        key = f"{pname}_{cname}"
        preset = {**BASE, "chandelier_atr_mult": mult}
        r = run(preset, key, start, end)
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
              f"승률 {wr:.1f}% 손익비 {r.get('profitLossRatio')} 거래 {len(trades)}건", flush=True)

out_path = Path(__file__).parent / "spex_v15_robustness_trades.json"
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
