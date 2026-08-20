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

import screening_backtest as sb
from local_price_cache import cached_fetch_ohlc_history_batches


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--market", default="KR", choices=["KR", "US"])
    p.add_argument("--strategy", default="trendTemplate", help="trendTemplate 또는 stage1~stage4")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--stop-loss", type=float, default=sb.DEFAULT_STOP_LOSS_PCT, dest="stop_loss")
    p.add_argument("--max-positions", type=int, default=sb.DEFAULT_MAX_POSITIONS, dest="max_positions")
    p.add_argument("--seed", type=float, default=10_000_000)
    p.add_argument("--refresh-cache", action="store_true", dest="refresh_cache")
    p.add_argument("--out", default=None, help="전체 결과(거래 내역 포함)를 저장할 JSON 파일 경로")
    args = p.parse_args()

    fetch_fn = cached_fetch_ohlc_history_batches(args.market, force_refresh=args.refresh_cache)
    result = sb.run_screening_backtest(
        args.market, args.strategy, args.start, args.end,
        stop_loss_pct=args.stop_loss, max_positions=args.max_positions, seed=args.seed,
        fetch_fn=fetch_fn,
    )

    if "error" in result:
        print("오류:", result["error"])
        return

    print()
    print(f"기간: {result['start']} ~ {result['end']} | 전략: {result['strategyLabel']} | 손절: {result['stopLossPct']}%")
    print(f"수익률: {result['returnPct']:+.2f}% | 최종평가액: {result['finalValue']:,.0f}")
    print(f"거래수: {result['tradeCount']} | 승률: {result['winRatePct']}% | 평균보유일: {result['avgHoldDays']}")
    print(f"MDD: -{result['mddPct']:.2f}% | 손익비: {result['profitLossRatio']}")
    if result["alphaPct"] is not None:
        print(f"알파(초과수익률): {result['alphaPct']:+.2f}%p | 벤치마크({result['benchmark']['label']}): "
              f"{result['benchmark']['returnPct']:+.2f}%")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n전체 결과(거래 내역 포함) 저장: {args.out}")


if __name__ == "__main__":
    main()
