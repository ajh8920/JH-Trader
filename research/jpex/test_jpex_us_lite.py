# -*- coding: utf-8 -*-
"""JPEX의 가격 행동 엔진(Stage2+RS랭킹+눌림목+챈들리어/피라미딩/국면방어)이
미국 시장(S&P500 상당 606종목)에서도 통하는지 1차 근사로 검증한다.

목적: "다 밀어붙이는 방향으로" 요청에 따라 지금까지 미시험으로 남겨둔
미국 시장 축을 실제로 시도한다. 완전한 미국 적용은 과거 시점별 재무
데이터(분기 EPS/매출성장률, DART 공시 상당 데이터)가 없어 막혀 있었다
(13단계) - 이 스크립트는 그 제약을 명시하고 "재무 필터를 뺀 순수 가격
행동 버전"으로 1차 근사 테스트한다.

배경: data/price_cache/US.parquet에 2018-08~현재 가격 데이터가 이미
캐시돼 있다(1,153,402행, 606종목). 상장주식수는 과거 시계열이 없어
build_us_shares_map.py로 "현재" 값을 받아 전체 구간에 고정 근사로 쓴다
(바이백/증자가 컸던 종목은 과거 시가총액이 왜곡될 수 있음 - 1차 근사의
한계). require_evan_stage2(passes_evan_stage2)는 종가/고가/RS만 쓰는
순수 가격 지표라 재무데이터 없이도 정확히 동작한다 - 재무 의존 필터
(min_revenue_growth/min_eps_growth_pct/quality_rank_weight)만 비활성화
하면 나머지(눌림목·챈들리어·피라미딩·국면방어·과열청산·진입순위게이트)는
KR과 동일한 로직을 그대로 쓸 수 있다.

변형: JPEX_V7_PARAMS에서 재무 의존 필터만 비활성화
  - us_lite: market="US", min_revenue_growth=None, min_eps_growth_pct=None,
    quality_rank_weight=0, min_market_cap=20억달러, min_avg_trade_value=
    500만달러(미국 시장 스케일로 재조정), include_delisted=False(미국
    상장폐지 목록 인프라 없음 - 생존편향 존재, 결과 해석 시 감안할 것)

데이터: 로컬 전용. data/price_cache/US.parquet, research/jpex/
  us_shares_cache.json(build_us_shares_map.py로 미리 생성) 필요.

산출(결과물): 콘솔에 CAGR/MDD/Calmar/승률/손익비/거래,
  research/jpex/jpex_us_lite_trades.json에 저장.

사용법: python -m research.jpex.test_jpex_us_lite
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

import vcp_strategy as vcp
from local_price_cache import cached_fetch_ohlc_history_batches

us_shares_path = Path(__file__).parent / "us_shares_cache.json"
if not us_shares_path.exists():
    print("us_shares_cache.json이 없습니다 - 먼저 build_us_shares_map.py를 실행하세요.", flush=True)
    sys.exit(1)
shares_map = {k: int(v) for k, v in json.load(open(us_shares_path, encoding="utf-8")).items()}
print(f"미국 상장주식수 캐시 {len(shares_map)}건 로드", flush=True)

START, END = "2018-08-23", date.today().isoformat()  # US.parquet 캐시 시작일부터
SEED = 500_000_000  # 원화 기준(엔진이 market 무관하게 통화 단위를 따지지 않음 - 상대 수익률만 의미있음)
fetch_fn = cached_fetch_ohlc_history_batches("US", include_delisted=False, max_age_hours=999999)

US_LITE_PARAMS = {**vcp.JPEX_V7_PARAMS,
    "market": "US",
    "min_revenue_growth": None,
    "min_eps_growth_pct": None,
    "quality_rank_weight": 0.0,
    "min_market_cap": 2_000_000_000,
    "min_avg_trade_value": 5_000_000,
    "include_delisted": False,
}

kw = dict(US_LITE_PARAMS)
kw.pop("market", None)
kw.pop("default_seed", None)
vcp.MAX_POSITION_WEIGHT_PCT = kw.get("max_position_weight_pct", 30.0)
print("=== JPEX US-lite 실행 ===", flush=True)
t0 = time.time()
r = vcp.run_vcp_backtest(
    "US", START, END, seed=SEED, fetch_fn=fetch_fn, shares_map=shares_map, **kw)
print(f"완료 ({round(time.time() - t0)}s)", flush=True)

if "error" in r:
    print("오류:", r.get("error"), flush=True)
    sys.exit(1)

years_span = (date.today() - date(2018, 8, 23)).days / 365.25
final_val = r["equityCurve"][-1]["value"]
cagr = ((final_val / SEED) ** (1 / years_span) - 1) * 100
mdd = r.get("mddPct", 0)
calmar = cagr / abs(mdd) if mdd else float("inf")
trades = r["trades"]
wins = [t for t in trades if t["pnlPct"] > 0]
wr = len(wins) / len(trades) * 100 if trades else 0
print(f"\n[US-lite] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} "
      f"승률 {wr:.1f}% 손익비 {r.get('profitLossRatio')} 거래 {len(trades)}건"
      f"(연 {len(trades)/years_span:.1f}건)", flush=True)
print(f"고유종목수: {len({t['code'] for t in trades})}", flush=True)

out_path = Path(__file__).parent / "jpex_us_lite_trades.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({
        "seed": SEED,
        "us_lite_curve": r["equityCurve"],
        "us_lite_trades": [
            {"code": t["code"], "entryDate": t["entryDate"], "exitDate": t["exitDate"], "pnlPct": t["pnlPct"]}
            for t in trades
        ],
    }, f)
print(f"저장 완료: {out_path}", flush=True)
