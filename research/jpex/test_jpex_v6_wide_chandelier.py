# -*- coding: utf-8 -*-
"""JPEX_V6_PARAMS에 "거대 승자 전용 와이드 챈들리어"(신규 엔진 파라미터
chandelier_wide_r/chandelier_atr_mult_wide)를 추가했을 때 CAGR을 더 올릴 수
있는지 검증한다.

목적:
  월별 분해(research/jpex/RESULTS.md 20단계)에서 2023-09의 -11.19%가 손실이
  아니라 336570(+67.81%, 4개월 보유) 같은 거대 승자가 미실현 수익을 서서히
  반납한 결과였음을 확인했다. 피라미딩/비중상한을 키워 승자를 더 크게 태우는
  시도(test_jpex_v6_concentration.py)는 전부 역효과였으므로, "더 크게"가 아니라
  "덜 반납하게"(거대 승자에게만 더 넓은 트레일링을 줘서 일반적인 되돌림에
  조기 청산되지 않게) 하는 방향을 대신 시험한다. vcp_strategy.py에
  chandelier_wide_r/chandelier_atr_mult_wide를 신규 추가(이 스크립트가 첫 실사용
  검증) - 둘 다 None이면 기존 동작과 완전히 동일(하위호환).

배경:
  JPEX_V6_PARAMS는 chandelier_atr_mult=3.0 하나로 모든 R구간을 커버한다.
  R배수가 chandelier_wide_r 이상인 포지션만 chandelier_atr_mult_wide(더 큰 값)를
  써서 트레일링 폭을 넓힌다 - 일반 승자는 기존 그대로, 그야말로 "몇 배씩
  번" 극소수 포지션만 영향을 받는다.

변형: JPEX_V6_PARAMS 베이스로 2개 (wide_r=5.0/mult_wide=5.0),
  (wide_r=8.0/mult_wide=6.0).

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/거래 + 월별 최악/최고 5개월,
  research/jpex/jpex_v6_wide_chandelier_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v6_wide_chandelier
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
    "wide_r5_m5": {**vcp.JPEX_V6_PARAMS, "chandelier_wide_r": 5.0, "chandelier_atr_mult_wide": 5.0},
    "wide_r8_m6": {**vcp.JPEX_V6_PARAMS, "chandelier_wide_r": 8.0, "chandelier_atr_mult_wide": 6.0},
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
    best_trade = max(trades, key=lambda t: t["pnlPct"]) if trades else None
    print(f"\n[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 거래 {len(trades)}건(연 {len(trades)/years_span:.1f}건)", flush=True)
    if best_trade:
        print(f"  최고 거래: {best_trade['code']} {best_trade['pnlPct']}% "
              f"({best_trade['entryDate']}~{best_trade['exitDate']})", flush=True)

out_path = Path(__file__).parent / "jpex_v6_wide_chandelier_trades.json"
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
