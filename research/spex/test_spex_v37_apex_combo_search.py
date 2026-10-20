# -*- coding: utf-8 -*-
"""SPEX 37단계("타협 없음 - 모든 경우의수 복합 탐색" 사용자 지시, 2026-10-20)

34~36단계에서 슬롯×비중×chandelier 세 축만으로는 "CAGR>=25% AND
MDD<=-20%"를 동시에 만족하는 지점이 없음을 확인했다(슬롯7: 27.87%/
-24.89%, 슬롯9: 22.26%/-20.11%, 그 사이 교차점 없음). 이번엔 MDD에
영향을 주는 완전히 다른 종류의 축들을 "동시에" 조합해 간극을 좁힐 수
있는지 확인한다 - 사용자 지시대로 한두 개가 아니라 여러 조건을 복합
변경.

분석(가설):
  - initial_stop_atr_mult(현재 1.1배): 좁히면 손절당 손실 크기가 줄어
    MDD를 낮출 수 있다(단, 휩소가 늘면 역효과 가능 - 36단계 chandelier
    좁히기에서 이미 본 패턴).
  - pyramid_max_count(현재 2): 줄이면 승리 포지션 한 종목에 더 몰아
    담지 않아 집중도가 낮아짐 - MDD 완화 가능성.
  - breadth 방어 강도(현재 30/25): 더 타이트하게(35/30) 하면 약세장
    진입을 더 일찍 차단해 MDD를 줄일 수 있다.
  - equity_dd_derisk(현재 비활성): 드로다운 중 신규진입 비중을 줄이는
    "사후 반응형" 보호 - JPEX 2슬롯 농축 구조에서는 약했지만(기존 연구)
    APEX 베이스의 7슬롯 분산 구조에서는 다르게 작동할 수 있다.
  - require_profitable(현재 False): 적자기업을 걸러내면 변동성이 큰
    후보가 줄어 MDD가 완화될 수 있다.
  - min_rs(현재 85): 더 높이면(90) 후보가 줄지만 품질이 오를 수 있다.

변형(전부 슬롯7·비중20%·시장폭방어30/25 공통 기반 위에 추가):
  A) initial_stop=0.8
  B) pyramid_max_count=1
  C) breadth 강화(35/30)
  D) require_profitable=True
  E) min_rs=90
  F) equity_dd_derisk(-10%/0.5)
  G) 복합1: A+B+C(초기손절+피라미딩+시장폭 동시)
  H) 복합2: A+B+C+D(복합1+수익성 필터)
  I) 복합3(슬롯8로 복귀): 슬롯8·비중17% 기반 + 복합1(A+B+C) - CAGR을
     25%로 다시 밀어올리면서 MDD도 같이 낮출 수 있는지

데이터: KR 2016-01-01~오늘, 상장폐지 포함, 시드 5억원.
산출: research/spex/spex_v37_apex_combo_search_trades.json
사용법: python -m research.spex.test_spex_v37_apex_combo_search
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

BASE7 = {**vcp.APEX_V2_PARAMS, "max_positions": 7, "max_position_weight_pct": 20.0,
         "breadth_min_pct": 30.0, "gate_entries_breadth_min_pct": 25.0}
BASE8 = {**vcp.APEX_V2_PARAMS, "max_positions": 8, "max_position_weight_pct": 17.0,
         "breadth_min_pct": 30.0, "gate_entries_breadth_min_pct": 25.0}

COMBO1 = {"initial_stop_atr_mult": 0.8, "pyramid_max_count": 1,
          "breadth_min_pct": 35.0, "gate_entries_breadth_min_pct": 30.0}

VARIANTS = {
    "A_tightstop": ({**BASE7, "initial_stop_atr_mult": 0.8}, None),
    "B_pyramid1": ({**BASE7, "pyramid_max_count": 1}, None),
    "C_breadth_strong": ({**BASE7, "breadth_min_pct": 35.0, "gate_entries_breadth_min_pct": 30.0}, None),
    "D_profitable": ({**BASE7, "require_profitable": True}, None),
    "E_rs90": ({**BASE7, "evan_params": {"min_rs": 90.0}}, None),
    "F_ddderisk": ({**BASE7, "equity_dd_derisk_threshold_pct": -10.0, "equity_dd_derisk_factor": 0.5}, None),
    "G_combo1_slot7": ({**BASE7, **COMBO1}, None),
    "H_combo1_profit_slot7": ({**BASE7, **COMBO1, "require_profitable": True}, None),
    "I_combo1_slot8": ({**BASE8, **COMBO1}, None),
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
for key, (preset, _unused) in VARIANTS.items():
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
    pass_ok = "PASS" if (cagr >= 25 and abs(mdd) <= 20) else ""
    print(f"[{key}] CAGR {cagr:.2f}% MDD {mdd:.2f}% Calmar {calmar:.3f} 승률 {wr:.1f}% 거래 {len(trades)}건 {pass_ok}", flush=True)

print("참고(슬롯7·비중20%+방어, 34단계): 27.87%/-24.89%/1.120/425건", flush=True)
print("참고(슬롯8·비중17%+방어, 34단계): 24.69%/-22.31%/1.107/452건", flush=True)
print("참고(SPEX_V4, JPEX베이스): 23.14%/-19.89%/1.164/347건", flush=True)

out_path = Path(__file__).parent / "spex_v37_apex_combo_search_trades.json"
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
