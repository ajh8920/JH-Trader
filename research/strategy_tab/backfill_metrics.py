# -*- coding: utf-8 -*-
"""관리자 "전략" 탭(strategy_specs.py)의 각 전략별 결과 항목에서 빠져 있던 지표
(CAGR/MDD, 손익비, 알파, 평균보유일)를 현재 엔진으로 재실행해 채우고, 전략탭에서
실제 매매 내역을 조회할 수 있게 거래 단위 데이터도 strategy_trades/<key>.json에
저장한다.

목적:
  사용자 요청: "전략탭의 모든 전략들은 CAGR, MDD, CAGR/MDD, 승률, 손익비, 알파,
  거래량이 포함되어야 한다. 빠진 데이터가 있다면 채워 놓으십쇼." + 2026-10-10
  추가 요청 두 건: "평균 보유일도 포함해주세요." / "전략 탭에서 실제 매매 내역도
  확인할 수 있게 추가해 주세요." strategy_specs.py의 기존 결과 항목들을 점검한
  결과, 거의 전부 CAGR/MDD(Calmar)와 알파(benchmark 대비 초과수익)가 비어
  있었다. CAGR/MDD는 이미 있는 CAGR·MDD로 바로 나눌 수 있어 재실행 없이
  채웠지만, 알파·손익비·평균보유일·매매내역은 벤치마크 곡선과 거래 로그가
  필요해 재실행이 불가피하다 - avgHoldDays는 엔진이 이미 내부적으로 계산해서
  결과 dict에 돌려주는데, 1차 백필 때는 출력/저장을 안 해둬서 이번에 다시
  돈다(엔진을 다시 돌리는 게 아니라 "캡처를 놓쳤던 필드를 추가로 저장"하는
  것 - 재발 방지 차원에서 이 스크립트 자체에 영구히 추가).

  매매내역은 strategy_trades/(저장소 루트, git 추적 - data/는 .gitignore 대상
  이라 여기 두면 프로덕션에 배포되지 않는다)에 전략별로 JSON 파일 하나씩
  저장한다. app.py의 신규 라우트(/api/admin/strategy-trades/<key>)가 이
  파일을 그대로 읽어 돌려준다 - DB에 넣지 않은 이유는 이 데이터가 "그 시점
  백테스트의 스냅샷"이라 수정/갱신이 거의 없고(재실행하면 파일을 다시
  덮어쓰면 됨), 매매 건수가 전략당 많아야 수백 건이라 정적 파일로도 충분히
  가볍기 때문.

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

산출(결과물): 콘솔에 전략별 CAGR/MDD/CAGR-MDD/승률/손익비/알파/평균보유일/거래
  전체 출력, research/strategy_tab/backfill_results.json에 저장(이후
  strategy_specs.py 수정 시 참고).

사용법: python -m research.strategy_tab.backfill_metrics
"""
import io
import json
import sys
import time
from datetime import date, datetime
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
TRADES_DIR = PROJECT_DIR / "strategy_trades"
TRADES_DIR.mkdir(exist_ok=True)


def save_trades(key, trades):
    """code/name/entryDate/entryPrice/exitDate/exitPrice/pnlPct/exitReason/
    holdDays만 추려 저장한다(shares/pyramidCount 등 UI에 안 쓰는 필드는 뺐다 -
    파일 용량과 화면에 보여줄 열 수를 맞추기 위함). 날짜 오름차순으로 정렬해
    "매매 내역" 표가 시간순으로 보이게 한다."""
    rows = sorted(trades, key=lambda t: t["entryDate"])
    out = [{
        "code": t["code"], "name": t.get("name"), "entryDate": t["entryDate"],
        "entryPrice": t.get("entryPrice"), "exitDate": t["exitDate"], "exitPrice": t.get("exitPrice"),
        "pnlPct": t["pnlPct"], "exitReason": t.get("exitReason"),
        "holdDays": t.get("holdDays") or (datetime.fromisoformat(t["exitDate"])
                                           - datetime.fromisoformat(t["entryDate"])).days,
    } for t in rows]
    with open(TRADES_DIR / f"{key}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"  매매내역 {len(out)}건 저장: strategy_trades/{key}.json", flush=True)

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
    avg_hold = r.get("avgHoldDays")
    out = {
        "CAGR": round(cagr, 2), "MDD": mdd, "CAGR_MDD": round(calmar, 3),
        "winRatePct": wr, "profitLossRatio": plr, "alphaPct": alpha, "avgHoldDays": avg_hold,
        "tradeCount": n, "tradesPerYear": round(n / years_span(start), 1),
        "period": f"{start}~{TODAY}",
    }
    results[key] = out
    print(f"[{key}] CAGR {out['CAGR']}% MDD {mdd}% CAGR/MDD {out['CAGR_MDD']} "
          f"승률 {wr}% 손익비 {plr} 알파 {alpha}%p 평균보유 {avg_hold}일 거래 {n}건(연 {out['tradesPerYear']}건)", flush=True)
    save_trades(key, trades)


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
        holds = [(datetime.fromisoformat(t["exitDate"]) - datetime.fromisoformat(t["entryDate"])).days
                 for t in trades]
        avg_hold = round(sum(holds) / len(holds), 1) if holds else None
        return alpha, plr, avg_hold

    alpha_v64, plr_v64, hold_v64 = jpex_alpha_and_plr(v64_data["jpex"], v64_data["jpex_trades"], v64_data["seed"])
    print(f"[jpex V64] 알파 {alpha_v64}%p 손익비 {plr_v64} 평균보유 {hold_v64}일", flush=True)
    results["jpex_v64"] = {"alphaPct": alpha_v64, "profitLossRatio": plr_v64, "avgHoldDays": hold_v64}
    save_trades("jpex_v64", v64_data["jpex_trades"])

    alpha_v5, plr_v5, hold_v5 = jpex_alpha_and_plr(v5_data["B_curve"], v5_data["B_trades"], v5_data["seed"])
    print(f"[jpex V5(최종채택)] 알파 {alpha_v5}%p 손익비 {plr_v5} 평균보유 {hold_v5}일", flush=True)
    results["jpex_v5"] = {"alphaPct": alpha_v5, "profitLossRatio": plr_v5, "avgHoldDays": hold_v5}
    save_trades("jpex_v5", v5_data["B_trades"])
except FileNotFoundError as e:
    print("JPEX 캐시 파일(V64/V5) 없음 - 건너뜀:", e, flush=True)

# JPEX V7(현재 최종 채택, quality_rank_weight=0.3)/V6(7라운드 이전) - 각각
# 다른 세션 결과 파일에 캐시돼 있다(둘 다 research/jpex/에 있어 경로가 안정적).
try:
    v7_data = json.load(open(jpex_cache_dir / "jpex_v13_quality_blend_trades.json", encoding="utf-8"))
    v6_data = json.load(open(jpex_cache_dir / "jpex_v7_regime_adaptive_trades.json", encoding="utf-8"))

    alpha_v7, plr_v7, hold_v7 = jpex_alpha_and_plr(
        v7_data["quality30_curve"], v7_data["quality30_trades"], v7_data["seed"])
    print(f"[jpex V7(현재 최종채택)] 알파 {alpha_v7}%p 손익비 {plr_v7} 평균보유 {hold_v7}일", flush=True)
    results["jpex_v7"] = {"alphaPct": alpha_v7, "profitLossRatio": plr_v7, "avgHoldDays": hold_v7}
    save_trades("jpex_v7", v7_data["quality30_trades"])

    alpha_v6, plr_v6, hold_v6 = jpex_alpha_and_plr(
        v6_data["baseline_curve"], v6_data["baseline_trades"], v6_data["seed"])
    print(f"[jpex V6] 알파 {alpha_v6}%p 손익비 {plr_v6} 평균보유 {hold_v6}일", flush=True)
    results["jpex_v6"] = {"alphaPct": alpha_v6, "profitLossRatio": plr_v6, "avgHoldDays": hold_v6}
    save_trades("jpex_v6", v6_data["baseline_trades"])
except FileNotFoundError as e:
    print("JPEX 캐시 파일(V7/V6) 없음 - 건너뜀:", e, flush=True)

out_path = Path(__file__).parent / "backfill_results.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n저장 완료: {out_path}", flush=True)
