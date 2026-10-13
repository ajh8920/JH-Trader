# -*- coding: utf-8 -*-
"""75단계: 지인 실거래 표본에 보인 소형 바이오텍(헬릭스미스/앱클론/와이바이오로직스/
온코닉테라퓨틱스/한스바이오메드/씨어스/에이비엘바이오 등)이 우리 시총 하한
(min_market_cap=3,000억)과 성장률 필터 때문에 메인 Top40 풀에서 구조적으로
배제된다는 걸 확인했다(research/jpex/RESULTS.md 75단계). 실제 DART 공시
데이터(445,849건, 2016~2026 전부 로컬 parquet)에서 에이비엘바이오의 "기술이전
및 공동개발 계약 체결" 같은 진짜 촉매가 확인돼, 메인 풀과 완전히 별개인
소형 헬스케어 촉매 오버레이(catalyst_overlay_*, 신규 엔진 기능)를 V11 위에서
시험한다. 대형주 오버레이(60~62단계)와 같은 설계(별도 슬롯/쿨다운)지만 신호가
"국면"이 아니라 "개별 종목 공시"라는 점이 다르다.

변형: JPEX_V11_PARAMS 베이스로 catalyst_overlay_codes=헬스케어 섹터 전체
(306종목), catalyst_overlay_slots=1/2, catalyst_overlay_lookback_days=10/20,
catalyst_overlay_cooldown_days=10(고정).

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부
필요(000.Data/fundamentals/disclosures_kr/*.parquet 포함 - 이미 수집됨).

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래/오버레이거래건수,
  research/jpex/jpex_v62_catalyst_overlay_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_v62_catalyst_overlay
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
healthcare_codes = [s["code"] for s in kr_stocks if s.get("sector") == "헬스케어"]
print(f"[healthcare] {len(healthcare_codes)}종목", flush=True)

shareholder_rows = vcp.load_shareholder_rows(
    [SHAREHOLDER_KR_DIR / "majorstock_1of2.parquet", SHAREHOLDER_KR_DIR / "majorstock_2of2.parquet"])
with app.app_context():
    fundamentals_rows = vcp.load_fundamentals_rows(KrFundamental)
quarter_paths = sorted((FUND_KR_DIR.parent / "kr_quarter").glob("*.parquet"))
quarterly_rows = vcp.load_quarterly_rows(quarter_paths)
disclosure_paths = sorted(DISCLOSURE_DIR.glob("*.parquet"))
catalyst_dates = vcp.load_catalyst_dates(disclosure_paths)
print(f"[catalyst] {len(catalyst_dates)}종목에 촉매 공시 존재", flush=True)

START, END = "2016-01-01", date.today().isoformat()
SEED = 500_000_000
fetch_fn = cached_fetch_ohlc_history_batches("KR", include_delisted=True, max_age_hours=99999)

# 1차 실측(모멘텀 확인 없음) 전부 재앙 - 오버레이거래 711~1081건, CAGR
# -6.56~16.70%, MDD -60~94%(손익비 2대로 무너짐 - 306종목 전체에 "최근
# 공시 있음"만으로 거르니 후보가 너무 많았다). catalyst_overlay_require_uptrend
# (20일선 위 종가만, 신규)로 "시장이 진짜 반응한 촉매"만 추가로 거른다.
GRID = {
    "cat_s2_lb20_uptrend": {**vcp.JPEX_V11_PARAMS, "catalyst_overlay_codes": healthcare_codes,
                             "catalyst_overlay_slots": 2, "catalyst_overlay_lookback_days": 20,
                             "catalyst_overlay_require_uptrend": True},
    "cat_s2_lb10_uptrend": {**vcp.JPEX_V11_PARAMS, "catalyst_overlay_codes": healthcare_codes,
                             "catalyst_overlay_slots": 2, "catalyst_overlay_lookback_days": 10,
                             "catalyst_overlay_require_uptrend": True},
    "cat_s1_lb10_uptrend": {**vcp.JPEX_V11_PARAMS, "catalyst_overlay_codes": healthcare_codes,
                             "catalyst_overlay_slots": 1, "catalyst_overlay_lookback_days": 10,
                             "catalyst_overlay_require_uptrend": True},
}


def run(preset, label):
    kw = dict(preset)
    kw.pop("market", None)
    kw.pop("default_seed", None)
    kw["quarterly_rows_by_code"] = quarterly_rows
    kw["catalyst_dates_by_code"] = catalyst_dates
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
    overlay_trades = [t for t in trades if t["code"] in healthcare_codes]
    print(f"\n[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
          f"승률 {wr:.1f}% 손익비 {r.get('profitLossRatio')} 거래 {len(trades)}건"
          f"(연 {len(trades)/years_span:.1f}건) 오버레이거래 {len(overlay_trades)}건", flush=True)

print("\n=== 기준(V11, 오버레이 없음): 41.52%/-29.43%/1.411/192건 ===", flush=True)

out_path = Path(__file__).parent / "jpex_v62_catalyst_overlay_trades.json"
out_data = {"seed": SEED}
for key, r in results.items():
    out_data[f"{key}_curve"] = r["equityCurve"]
    out_data[f"{key}_trades"] = [
        {"code": t["code"], "name": t["name"], "entryDate": t["entryDate"], "exitDate": t["exitDate"],
         "pnlPct": t["pnlPct"], "exitReason": t.get("exitReason")}
        for t in r["trades"]
    ]
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out_data, f, ensure_ascii=False)
print(f"\n저장 완료: {out_path}", flush=True)
