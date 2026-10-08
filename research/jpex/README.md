# JPEX 전략 연구

2026-10-08 세션에서 지금까지 점검한 전략(APEX 68차, 지인 필터30 실험 등)의
장점을 결합해 만든 신규 전략 "JPEX"의 백테스트 기록이다.

- `run_jpex_variant.py` — 탐색 과정에서 시험한 30여 개 변형을 하나로 합친
  실행 스크립트. `python -m research.jpex.run_jpex_variant v6`처럼 실행한다.
  `vcp_strategy.JPEX_PARAMS`(최종 채택) = 이 스크립트의 `v6`과 동일하다.
- `RESULTS.md` — 전체 변형의 결과표와 결론, 강건성 검증(하락장/상승장/코로나
  급락 구간), 되돌린 시도(엔진 성능 최적화) 기록.

## 실행 전제 조건

로컬 전용이다. 다음이 모두 있어야 실행된다:
- `data/price_cache/*.parquet` (가격 캐시)
- `data/app.db` (SQLite, 재무데이터 일부)
- `000.Data/`(이 저장소 밖, `data_pipeline/common.py`의 `STOCK_DATA_ROOT` 참고) -
  대량보유·공시·분기재무

프로덕션(Render)이나 git 클론만 한 새 환경에는 위 데이터가 없어 바로 실행되지
않는다 - `CLAUDE.md`/`RULES.md`의 데이터 소스 설명 참고.

## 요약

| 항목 | 값 |
|---|---|
| 최종 설정 | `vcp_strategy.JPEX_PARAMS` (= `JPEX_V1_PARAMS` + `pyramid_max_count=6`) |
| 실측(2016~2026, 10.75년) | CAGR 31.67%, MDD -23.56%, CAGR/MDD 1.344, 거래 84건 |
| 상태 | 백테스트 전용 (`entry_rank_top_n`/`exit_on_regime_loss` 미이식으로 실시간 모의투자 미등록) |
| 남은 과제 | 거래량 확대(목표 400건+) 시 Calmar 1 유지 - 아직 미해결 |

자세한 수치는 `RESULTS.md` 참고.
