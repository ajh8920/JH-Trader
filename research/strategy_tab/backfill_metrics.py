# -*- coding: utf-8 -*-
"""관리자 "전략" 탭(strategy_specs.py)의 각 전략별 결과 항목에서 빠져 있던 지표
(CAGR/MDD, 손익비, 알파)를 현재 엔진으로 재실행해 채운다.

목적:
  사용자 요청: "전략탭의 모든 전략들은 CAGR, MDD, CAGR/MDD, 승률, 손익비, 알파,
  거래량이 포함되어야 한다. 빠진 데이터가 있다면 채워 놓으십쇼." strategy_specs.py의
  기존 결과 항목들을 점검한 결과, 거의 전부 CAGR/MDD(Calmar)와 알파(benchmark 대비
  초과수익)가 비어 있었다. CAGR/MDD는 이미 있는 CAGR·MDD로 바로 나눌 수 있어
  재실행 없이 채웠지만(strategy_specs.py를 직접 수정), 알파·손익비는 벤치마크
  곡선과 거래 로그가 필요해 재실행이 불가피하다.

배경:
  vcp_strategy.run_vcp_backtest / screening_backtest.run_risk_managed_backtest
  둘 다 결과 dict에 이미 alphaPct·profitLossRatio를 계산해서 돌려준다(벤치마크
  곡선을 자체적으로 받아온다) - 그래서 "현재 엔진으로 재현 가능한" 항목들은
  한 번씩만 다시 돌리면 CAGR/MDD/승률/손익비/알파/거래 6개를 한 번에 얻는다.
  "UNVERIFIED"(구버전 기록, 재현 불가로 이미 확인됨) 항목은 재실행해도 다른
  숫자가 나올 뿐 그 기록을 "채우는" 게 아니므로 대상에서 제외했다 - 대신
  strategy_specs.py에는 "데이터 없음(구버전 기록, 재현 불가)"로 명시한다.

  JPEX(V5=최종채택, V64=이전 설정)는 이미 이번 세션에서 저장해 둔 캐시
  (research/jpex/jpex_unified_trades.json의 B_curve/B_trades, research/jpex/
  blend_curves_68cha_full.json의 jpex_curve/jpex_trades)가 있어 재실행 없이
  벤치마크만 따로 불러와 알파를 계산한다(손익비는 캐시된 거래로 직접 계산).

  apex_stage3_best68은 두 기록값(run68.log 실측, 2026-10-07 재실행) 모두
  entry_rank_top_n 동순위 비결정성 버그(2026-10-08 수정) 영향을 받았을 수 있다는
  주의문이 이미 달려 있었다 - 재실행해도 "그 기록을 채우는" 게 아니라 새로운
  신뢰 가능한 기록을 하나 추가하는 것이므로, strategy_specs.py에 세 번째 결과
  항목으로 별도 추가한다(기존 두 항목은 보존).

변형: 재실행 대상(전부 preset 그대로, 문서화된 기간/시드 사용) -
  ANONYMOUS_PARAMS, SWEEPER_PARAMS, WATCHER_PARAMS, APEX_V2_PARAMS,
  APEX_V3_PARAMS, APEX_STAGE3_BEST68_PARAMS(vcp_strategy 엔진),
  MINERVINI_V2_PARAMS, MINERVINI_V21_PARAMS(screening_backtest 엔진).

데이터: 로컬 전용. data/price_cache/*.parquet, data/app.db, 000.Data/ 전부 필요.

산출(결과물): 콘솔에 전략별 CAGR/MDD/CAGR-MDD/승률/손익비/알파/거래 전체 출력,
  research/strategy_tab/backfill_results.json에 저장(이후 strategy_specs.py
  수정 시 참고).

사용법: python -m research.strategy_tab.backfill_metrics
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
import screening_backtest as sb
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

TODAY = date.today().isoformat()
fetch_fn = cached_fetch_ohlc_history_batches("KR", include_delisted=True, max_age_hours=99999)

VCP_RERUNS = {
    "anonymous": (vcp.ANONYMOUS_PARAMS, "2017-01-01", vcp.ANONYMOUS_PARAMS["default_seed"]),
    "sweeper": (vcp.SWEEPER_PARAMS, "2017-01-01", vcp.SWEEPER_PARAMS["default_seed"]),
    "watcher": (vcp.WATCHER_PARAMS, "2017-01-01", vcp.WATCHER_PARAMS["default_seed"]),
    "apex_v2": (vcp.APEX_V2_PARAMS, "2016-01-01", 500_000_000),
    "apex_v3": (vcp.APEX_V3_PARAMS, "2016-01-01", 500_000_000),
    "apex_stage3_best68": (vcp.APEX_STAGE3_BEST68_PARAMS, "2016-01-01", 500_000_000),
}

results = {}
years_cache = {}


def years_span(start):
    if start not in years_cache:
        y, m, d = map(int, start.split("-"))
        years_cache[start] = (date.today() - date(y, m, d)).days / 365.25
    return years_cache[start]


def summarize(key, r, start, seed):
    final_val = r["equityCurve"][-1]["value"]
    cagr = ((final_val / seed) ** (1 / years_span(start)) - 1) * 100
    mdd = r.get("mddPct", 0)
    calmar = cagr / abs(mdd) if mdd else float("inf")
    trades = r.get("trades") or []
    n = len(trades)
    wr = r.get("winRatePct")
    plr = r.get("profitLossRatio")
    alpha = r.get("alphaPct")
    out = {
        "CAGR": round(cagr, 2), "MDD": mdd, "CAGR_MDD": round(calmar, 3),
        "winRatePct": wr, "profitLossRatio": plr, "alphaPct": alpha,
        "tradeCount": n, "tradesPerYear": round(n / years_span(start), 1),
        "period": f"{start}~{TODAY}",
    }
    results[key] = out
    print(f"[{key}] CAGR {out['CAGR']}% MDD {mdd}% CAGR/MDD {out['CAGR_MDD']} "
          f"승률 {wr}% 손익비 {plr} 알파 {alpha}%p 거래 {n}건(연 {out['tradesPerYear']}건)", flush=True)


for key, (preset, start, seed) in VCP_RERUNS.items():
    kw = dict(preset)
    kw.pop("market", None)
    kw.pop("default_seed", None)
    kw["quarterly_rows_by_code"] = quarterly_rows
    vcp.MAX_POSITION_WEIGHT_PCT = kw.get("max_position_weight_pct", 30.0)
    print(f"=== {key} 실행 ({start}~{TODAY}, 시드 {seed:,}) ===", flush=True)
    t0 = time.time()
    r = vcp.run_vcp_backtest(
        "KR", start, TODAY, seed=seed, fetch_fn=fetch_fn, shares_map=shares_map,
        shareholder_rows_by_code=shareholder_rows, fundamentals_rows_by_code=fundamentals_rows, **kw)
    print(f"{key} 완료 ({round(time.time() - t0)}s)", flush=True)
    if "error" in r:
        print("오류:", r.get("error"), flush=True)
        continue
    summarize(key, r, start, seed)

MINERVINI_RERUNS = {
    "minervini_v2": (sb.MINERVINI_V2_PARAMS, "2017-01-01", 10_000_000),
    "minervini_v21": (sb.MINERVINI_V21_PARAMS, "2017-01-01", 10_000_000),
}
sb_fetch_fn = cached_fetch_ohlc_history_batches("KR", include_delisted=False, max_age_hours=99999)
for key, (p, start, seed) in MINERVINI_RERUNS.items():
    print(f"=== {key} 실행 ({start}~{TODAY}, 시드 {seed:,}) ===", flush=True)
    t0 = time.time()
    r = sb.run_risk_managed_backtest(
        p["market"], p["strategy"], start, TODAY,
        risk_pct=p["risk_pct"], atr_period=p["atr_period"], atr_mult=p["atr_mult"],
        breakeven_r=p["breakeven_r"], breakeven_lock_r=p.get("breakeven_lock_r", 0.0),
        trail_start_r=p["trail_start_r"],
        time_stop_days=p["time_stop_days"], dd_halt_pct=p["dd_halt_pct"],
        max_positions=p["max_positions"], seed=seed,
        min_avg_trade_value=p["min_avg_trade_value"],
        market_regime_filter=p.get("market_regime_filter", False),
        fetch_fn=sb_fetch_fn,
    )
    print(f"{key} 완료 ({round(time.time() - t0)}s)", flush=True)
    if "error" in r:
        print("오류:", r.get("error"), flush=True)
        continue
    summarize(key, r, start, seed)

# JPEX - 이미 캐시된 자산곡선/거래 로그 재사용(재실행 안 함), 벤치마크만 새로 받아 알파 계산.
# blend_curves_68cha_full.json(V64용, entryDate/exitDate 포함판)은 세션 스크래치패드에만
# 있다(research/jpex/에는 pnlPct만 있는 구버전이 없음 - 애초에 커밋한 적이 없다) -
# 경로가 없으면 조용히 건너뛴다(아래 except).
JPEX_SCRATCHPAD = Path(
    r"C:\Users\ajh89\AppData\Local\Temp\claude\c--Users-ajh89-Desktop-AI----000-Project-stock-tracker-py"
    r"\350d4739-3991-4bfa-b894-2b3bbc491e6c\scratchpad"
)
jpex_cache_dir = PROJECT_DIR / "research" / "jpex"
try:
    v64_data = json.load(open(JPEX_SCRATCHPAD / "blend_curves_68cha_full.json", encoding="utf-8"))
    v5_data = json.load(open(jpex_cache_dir / "jpex_unified_trades.json", encoding="utf-8"))

    def jpex_alpha_and_plr(curve, trades, seed):
        dates = [p["date"] for p in curve]
        final_val = curve[-1]["value"]
        return_pct = (final_val / seed - 1) * 100
        _, bench_return = sb._fetch_benchmark_curve("KR", dates, seed)
        alpha = round(return_pct - bench_return, 2) if bench_return is not None else None
        plr = sb._profit_loss_ratio(trades)
        return alpha, plr

    alpha_v64, plr_v64 = jpex_alpha_and_plr(v64_data["jpex"], v64_data["jpex_trades"], v64_data["seed"])
    print(f"[jpex V64] 알파 {alpha_v64}%p 손익비 {plr_v64}", flush=True)
    results["jpex_v64"] = {"alphaPct": alpha_v64, "profitLossRatio": plr_v64}

    alpha_v5, plr_v5 = jpex_alpha_and_plr(v5_data["B_curve"], v5_data["B_trades"], v5_data["seed"])
    print(f"[jpex V5(최종채택)] 알파 {alpha_v5}%p 손익비 {plr_v5}", flush=True)
    results["jpex_v5"] = {"alphaPct": alpha_v5, "profitLossRatio": plr_v5}
except FileNotFoundError as e:
    print("JPEX 캐시 파일 없음 - 건너뜀:", e, flush=True)

out_path = Path(__file__).parent / "backfill_results.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n저장 완료: {out_path}", flush=True)
