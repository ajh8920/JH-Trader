# -*- coding: utf-8 -*-
"""JPEX 111단계(2026-10-22, 사용자 질문: "JPEX도 장기간 종목이 들어오지 않을
경우 유휴 자금을 금리ETF로 돌리면 어떻게 되나요?") - 구현 전 사전 점검으로
JPEX_V11의 유휴 슬롯 빈도를 debug_regime_log로 측정한 결과, 재평가일의
72.4%가 완전히 빈 포지션(0/2슬롯), 평균 보유 슬롯이 0.34/2(유휴 비중 83%)로
확인됐다 - SPEX(7슬롯)보다 훨씬 유휴 현금이 많을 것으로 예상되는 구조라
rate_etf_parking을 시험할 가치가 있다고 판단.

**주의(SPEX_V5 교훈)**: rate_etf_parking은 과거 SPEX에서 채택 후 철회된
전례가 있다 - 처음엔 개선처럼 보였으나 KODEX 단기채권(153130) 가격을 매
실행마다 yfinance로 새로 받아오는 탓에(로컬 parquet 캐시 없음) 재실행마다
결과가 흔들렸다. 그 캐싱 버그는 2026-10-20에 _cached_rate_etf_series
(로컬 20시간 캐싱)로 이미 고쳤지만, 캐시를 고친 뒤에도 SPEX 재검증
4번째 측정은 V4보다 전부 나빴다(총 4번 측정 2승2패로 방향성 불일치) -
"효과 자체가 재현 안 됨"으로 최종 기각됐다. 이번에도 같은 함정을 피하려고
**이 스크립트를 반드시 두 번 이상 별도 프로세스로 실행해 재현성을
확인한 뒤에만** 채택 여부를 판단한다.

변형: JPEX_V11_PARAMS에서 rate_etf_parking만 True로 전환(슬리피지/상한은
기본값 0.1%/100% 그대로).

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/jpex/jpex_v111_rate_etf_parking_trades.json
사용법: python -m research.jpex.test_jpex_v111_rate_etf_parking
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

VARIANTS = {
    "A_rate_etf_parking": {**vcp.JPEX_V11_PARAMS, "rate_etf_parking": True},
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
for key, preset in VARIANTS.items():
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
    print(f"[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} 승률 {wr:.1f}% 거래 {len(trades)}건", flush=True)

print("참고(기준선, JPEX_V11): CAGR 41.52% MDD -29.43% Calmar 1.411 거래 192건", flush=True)

out_path = Path(__file__).parent / "jpex_v111_rate_etf_parking_trades.json"
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
print(f"저장 완료: {out_path}", flush=True)
