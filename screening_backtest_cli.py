"""스크리닝 백테스트를 로컬에서 빠르게 반복 실행하기 위한 CLI.

가격 데이터를 로컬에 캐싱해두고(local_price_cache.py) 파라미터만 바꿔 여러
번 돌릴 때 매번 몇 분씩 걸리는 야후 재조회를 건너뛴다. 운영 서버(웹 화면)의
백테스트 경로와는 완전히 별개다 - 이 스크립트의 전종목 캐싱 방식은 로컬
전용이며, 운영 서버는 메모리 한도 때문에 절대 이렇게 하지 않는다.

사용법:
  python screening_backtest_cli.py --start 2025-01-01 --end 2026-08-20
  python screening_backtest_cli.py --start 2025-01-01 --end 2026-08-20 --stop-loss -5
  python screening_backtest_cli.py --start 2025-01-01 --end 2026-08-20 --strategy stage2 --max-positions 15

  # 가격 캐시를 강제로 새로 받고 싶으면:
  python screening_backtest_cli.py --start ... --end ... --refresh-cache
"""
import argparse
import json
import os
from datetime import date
from pathlib import Path

import screening_backtest as sb
from local_price_cache import cached_fetch_ohlc_history_batches


DEFAULT_START = "2020-01-01"
PROJECT_DIR = Path(__file__).resolve().parent


def _load_fundamentals_and_shares():
    """가치/퀄리티 팩터용 - app.py를 통째로 import하면 백그라운드 스레드가 같이
    떠서(data_pipeline/import_fundamentals_to_db.py와 같은 이유) DB 접속 설정만
    복제한 최소 Flask 앱으로 KrFundamental을 읽는다. shares_map은 DB 없이
    kr_stocks.json에서 바로 읽는다."""
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
    from flask import Flask
    from models import KrFundamental, db
    from kr_quant import get_shares_outstanding_map

    app = Flask(__name__)
    database_url = os.environ.get("DATABASE_URL", f"sqlite:///{PROJECT_DIR / 'data' / 'app.db'}")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        fundamentals_rows = KrFundamental.query.filter(KrFundamental.rcept_no != "").all()
    shares_map = get_shares_outstanding_map()
    return fundamentals_rows, shares_map


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--market", default="KR", choices=["KR", "US"])
    p.add_argument("--strategy", default="trendTemplate", help="trendTemplate 또는 stage1~stage4")
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=None, help="생략하면 오늘")
    p.add_argument("--stop-loss", type=float, default=sb.DEFAULT_STOP_LOSS_PCT, dest="stop_loss")
    p.add_argument("--max-positions", type=int, default=sb.DEFAULT_MAX_POSITIONS, dest="max_positions")
    p.add_argument("--seed", type=float, default=10_000_000)
    p.add_argument("--min-rs", type=float, default=None, dest="min_rs", help="RS 등급 상향 진입 필터(예: 85)")
    p.add_argument("--min-rel-volume", type=float, default=None, dest="min_rel_volume",
                    help="거래량(직전 20일 평균 대비 배수) 진입 필터(예: 1.5)")
    p.add_argument("--regime-filter", action="store_true", dest="regime_filter",
                    help="지수가 200일선 아래인 시점엔 신규 매수를 쉰다")
    p.add_argument("--refresh-cache", action="store_true", dest="refresh_cache")
    p.add_argument("--out", default=None, help="전체 결과(거래 내역 포함)를 저장할 JSON 파일 경로")

    p.add_argument("--risk-managed", action="store_true", dest="risk_managed",
                    help="고정 % 손절 대신 ATR 기반 리스크관리 청산 규칙 사용(본전이동/트레일링/시간손절/낙폭중단)")
    p.add_argument("--risk-pct", type=float, default=sb.DEFAULT_RISK_PCT, dest="risk_pct",
                    help="트레이드당 리스크(계좌 평가액 대비 %%, risk-managed 전용)")
    p.add_argument("--atr-period", type=int, default=sb.DEFAULT_ATR_PERIOD, dest="atr_period")
    p.add_argument("--atr-mult", type=float, default=sb.DEFAULT_ATR_MULT, dest="atr_mult")
    p.add_argument("--breakeven-r", type=float, default=sb.DEFAULT_BREAKEVEN_R, dest="breakeven_r")
    p.add_argument("--trail-start-r", type=float, default=sb.DEFAULT_TRAIL_START_R, dest="trail_start_r")
    p.add_argument("--time-stop-days", type=int, default=sb.DEFAULT_TIME_STOP_DAYS, dest="time_stop_days")
    p.add_argument("--dd-halt-pct", type=float, default=sb.DEFAULT_DD_HALT_PCT, dest="dd_halt_pct")

    p.add_argument("--min-avg-trade-value", type=float, default=None, dest="min_avg_trade_value",
                    help="최근 20일 평균 거래대금(원) 하한선 - 유동성 필터(예: 300000000)")
    p.add_argument("--use-value", action="store_true", dest="use_value", help="PER 저평가 팩터(상위 --value-pct%%)")
    p.add_argument("--use-quality", action="store_true", dest="use_quality", help="ROE 우량 팩터(상위 --quality-pct%%)")
    p.add_argument("--use-low-vol", action="store_true", dest="use_low_vol", help="저변동성 팩터(하위 --lowvol-pct%%)")
    p.add_argument("--value-pct", type=float, default=50, dest="value_percentile")
    p.add_argument("--quality-pct", type=float, default=50, dest="quality_percentile")
    p.add_argument("--lowvol-pct", type=float, default=50, dest="low_vol_percentile")
    args = p.parse_args()

    end = args.end or date.today().isoformat()

    fetch_fn = cached_fetch_ohlc_history_batches(args.market, force_refresh=args.refresh_cache)

    fundamentals_rows, shares_map = (None, None)
    if args.risk_managed and (args.use_value or args.use_quality):
        fundamentals_rows, shares_map = _load_fundamentals_and_shares()

    if args.risk_managed:
        result = sb.run_risk_managed_backtest(
            args.market, args.strategy, args.start, end,
            risk_pct=args.risk_pct, atr_period=args.atr_period, atr_mult=args.atr_mult,
            breakeven_r=args.breakeven_r, trail_start_r=args.trail_start_r,
            time_stop_days=args.time_stop_days, dd_halt_pct=args.dd_halt_pct,
            max_positions=args.max_positions, seed=args.seed,
            fetch_fn=fetch_fn, min_rs=args.min_rs, min_rel_volume=args.min_rel_volume,
            market_regime_filter=args.regime_filter,
            min_avg_trade_value=args.min_avg_trade_value,
            use_value=args.use_value, use_quality=args.use_quality, use_low_vol=args.use_low_vol,
            value_percentile=args.value_percentile, quality_percentile=args.quality_percentile,
            low_vol_percentile=args.low_vol_percentile,
            fundamentals_rows=fundamentals_rows, shares_map=shares_map,
        )
    else:
        result = sb.run_screening_backtest(
            args.market, args.strategy, args.start, end,
            stop_loss_pct=args.stop_loss, max_positions=args.max_positions, seed=args.seed,
            fetch_fn=fetch_fn, min_rs=args.min_rs, min_rel_volume=args.min_rel_volume,
            market_regime_filter=args.regime_filter,
        )

    if "error" in result:
        print("오류:", result["error"])
        return

    print()
    if args.risk_managed:
        print(f"기간: {result['start']} ~ {result['end']} | 전략: {result['strategyLabel']} | 모드: 리스크관리"
              f" | 리스크 {result['riskPct']}%/트레이드 | 손절 {result['atrMult']}xATR({result['atrPeriod']})"
              f" | 본전 {result['breakevenR']}R | 트레일링 {result['trailStartR']}R+ | 시간손절 {result['timeStopDays']}일"
              f" | 낙폭중단 {result['ddHaltPct']}%")
        factors = []
        if result.get("minAvgTradeValue"):
            factors.append(f"유동성>={result['minAvgTradeValue']:,.0f}원/일")
        if result.get("useValue"):
            factors.append(f"가치(PER 상위{result['valuePercentile']}%)")
        if result.get("useQuality"):
            factors.append(f"퀄리티(ROE 상위{result['qualityPercentile']}%)")
        if result.get("useLowVol"):
            factors.append(f"저변동성(상위{result['lowVolPercentile']}%)")
        print("적용 팩터: " + (", ".join(factors) if factors else "없음(모멘텀만)"))
    else:
        print(f"기간: {result['start']} ~ {result['end']} | 전략: {result['strategyLabel']} | 손절: {result['stopLossPct']}%"
              f" | RS>= {result['minRs']} | 거래량>= {result['minRelVolume']}x | 레짐필터: {result['marketRegimeFilter']}")
    print(f"수익률: {result['returnPct']:+.2f}% | 최종평가액: {result['finalValue']:,.0f}")
    print(f"거래수: {result['tradeCount']} | 승률: {result['winRatePct']}% | 평균보유일: {result['avgHoldDays']}")
    print(f"MDD: -{result['mddPct']:.2f}% | 손익비: {result['profitLossRatio']}")
    if result["alphaPct"] is not None:
        print(f"알파(초과수익률): {result['alphaPct']:+.2f}%p | 벤치마크({result['benchmark']['label']}): "
              f"{result['benchmark']['returnPct']:+.2f}%")
    if args.risk_managed:
        reasons = result.get("exitReasonCounts", {})
        label = sb.EXIT_REASON_LABEL
        print("청산사유: " + ", ".join(f"{label.get(k, k)} {v}건" for k, v in reasons.items()))
        print(f"낙폭중단 발동 횟수(신규매수 정지된 재평가 시점 수): {result.get('ddHaltPeriods', 0)}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n전체 결과(거래 내역 포함) 저장: {args.out}")


if __name__ == "__main__":
    main()
