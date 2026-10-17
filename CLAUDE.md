# CLAUDE.md — stock-tracker-py 프로젝트 가이드

> ⚠️ **모든 대화는 반드시 한국어로 진행한다.** 사용자에게 노출되는 텍스트는 예외 없이
> 전부 한국어로 작성한다 — 여기에는 다음이 전부 포함된다:
> - 최종 답변, 요약, 질문
> - 도구 호출 직전에 붙이는 짧은 진행 안내 문장("Now let's...", "Let's check..." 같은 것)
> - **Bash/PowerShell 등 도구 호출의 `description`(작업 설명) 파라미터** — 이 자리를
>   "사용자에게 보이는 텍스트가 아니라 내부 로그"로 착각해서 영어로 쓰는 실수가
>   실제로 반복됐다. `description`도 화면에 그대로 노출되는 문구이니 반드시 한국어로.
> - `AskUserQuestion`의 question/header/option label 등 UI에 렌더링되는 모든 문자열
>
> "이건 짧은 상태 메모/파라미터니까 괜찮겠지"라는 예외를 절대 두지 않는다 — 도구
> 호출과 관련된 사용자 노출 필드는 전부 한국어 대상이라고 기본 가정한다. 코드
> 주석/커밋 메시지도 한국어로 작성한다. 이 규칙은 세션이 요약(compact)되거나 새로
> 시작되어도 항상 최우선으로 적용한다.

> 이 문서는 Claude Code(및 이 저장소를 처음 보는 엔지니어)가 **아무 사전 지식 없이도**
> 이 프로젝트에서 기획/설계 → 개발 → 테스트 → 배포를 수행할 수 있도록 만든 진입점 문서다.

이 저장소는 세 개의 문서로 "하네스"를 구성한다. 역할이 겹치지 않으니 아래 기준으로 참조한다.

| 문서 | 역할 | 언제 보는가 |
|---|---|---|
| **CLAUDE.md** (본 문서) | 프로젝트가 무엇이고 어떻게 생겼는지 — 컨텍스트/아키텍처 | 항상 자동 로드됨. 작업 시작 전 오리엔테이션 |
| **[RULES.md](RULES.md)** | 절대 어기면 안 되는 제약사항 — 왜 그런지 포함 | 코드를 작성/수정/배포하기 직전 반드시 재확인 |
| **[.claude/skills/feature-workflow/SKILL.md](.claude/skills/feature-workflow/SKILL.md)** | 기능 추가/버그 수정을 4단계로 실행하는 실행형 체크리스트 | 실제 작업을 시작할 때 그대로 따라감 |

---

## 1. 프로젝트가 무엇인가 (기획/설계 컨텍스트)

**"JH-Trader"(옛 이름: 미국 주식 목표가 트래커)** — 미국/한국 주식을 추적하고, 여러 매매 전략을 백테스트하고,
시장 상황을 한눈에 보는 개인용 Flask 웹앱. 여러 사용자가 동시에 쓸 수 있도록 계정/권한 시스템이 붙어 있다.

- GitHub: `ajh8920/JH-Trader`
- 배포: Render (`https://stock-tracker-5gy3.onrender.com`), 무료 웹서비스 + 무료 관리형 Postgres
- 실행: `gunicorn app:app --workers 2 --timeout 30 --graceful-timeout 20` — **워커 2개, 요청당 30초 제한**.
  이 두 숫자가 이 코드베이스 설계 전반을 지배한다. 자세한 내용은 [RULES.md](RULES.md)의 개발 규칙 참고.

**왜 이렇게 생겼나:** 원래는 로컬에서 혼자 쓰는 단일 사용자 스크립트였다가, 여러 사람이 동시에 쓰도록
배포하면서 JSON 파일 저장 → SQLAlchemy(Postgres) 전환, 계정/권한 분리, gunicorn 멀티워커 대응이 순서대로
추가됐다. 이 히스토리는 [docs/AS-IS_TO-BE.md](docs/AS-IS_TO-BE.md)에 정리되어 있다.

---

## 2. 아키텍처 한눈에 보기

```
templates/*.html + static/app.js, style.css   ← 프런트엔드 (서버사이드 렌더 + 바닐라 JS SPA-ish)
        │  fetch() → /api/*
        ▼
app.py (단일 Flask 앱, ~1,800줄)               ← 라우트, 인증/권한, 백그라운드 스레드 기동
        │
        ├─ models.py            SQLAlchemy ORM 모델 (User, PortfolioItem, Alert, InfinitePosition/Trade,
        │                       KrFundamental, QuantBacktestJob, KrPriceCache, TrendScreenCache, ScreenerWatchlist)
        ├─ backtest.py          공용 가격 조회(fetch_daily_prices) + 지표 계산 유틸
        ├─ infinite_buying.py   라오어 무한매수법 전략 로직 (백테스트 탭 + 실전현황 탭 공유)
        ├─ kr_swing.py          국내 스윙 전략 4종 (volatility_breakout/box_breakout/ma_pullback/combo)
        ├─ kr_quant.py          국내 퀀트(PER/ROE) 스크리닝 + 리밸런싱 백테스트 엔진
        ├─ dart_fetch.py        DART(전자공시) Open API 클라이언트, 재무데이터 백필
        ├─ trend_screener.py    미너비니 트렌드 템플릿(8조건) 스크리너 (KR+US)
        └─ live_tracker.py      실전현황 탭 보조 유틸

data/app.db (로컬 SQLite, git 미포함) / Render Postgres (프로덕션, DATABASE_URL로 전환)
kr_stocks.json / us_stocks.json / dart_corp_codes.json   ← 정적 스냅샷 데이터 (매 요청마다 외부 API 안 씀)
```

**단일 프로세스가 아니다 — 항상 gunicorn 워커 2개 + 백그라운드 스레드 6개가 함께 돈다:**
`app.py` 하단에서 모듈 임포트 시점에 `alert_checker`, `kr_quant_price_cache_refresher`,
`trend_screen_refresher`, `us_fundamentals_refresher`, `dart_fundamentals_refresher`,
`paper_trading_scheduler` 스레드가 항상 자동 기동한다(daemon thread).
이 때문에 "프로세스 메모리에 캐시를 두면 안 된다"는 제약이 나온다 — 자세한 이유는 RULES.md 참고.
각 스레드의 생존 여부는 `/ops-dashboard`(관리자 전용, 2026-10-17 신설)에서 실시간으로
확인할 수 있다 — OpsHeartbeat 테이블 기반, 자세한 내용은 app.py의 `ops_status`/
`_record_heartbeat` 주석 참고.

---

## 3. 탭(기능) ↔ 파일 매핑

| 탭 (nav 순서) | 설명 | 주요 파일 |
|---|---|---|
| 매크로 (기본 탭) | 주요 지수/금리/원자재 + CNN Fear&Greed, yfinance, 90초 서버 캐시 | `app.py` `get_macro`, `_macro_cache` |
| 종목검색/포트폴리오/알림 | **숨김 처리됨**(`display:none`) — 기능은 살아있음, 죽은 코드 아님. 삭제 금지 | `app.py` `/api/portfolio*`, `/api/alerts*` |
| 백테스트 | 라오어 무한매수법 백테스터 (미국 레버리지 ETF) | `infinite_buying.py`, `backtest.py` |
| 실전 현황 | 무한매수법 실전 포지션 수기 기록 + 오늘의 매수/매도 가이드 | `live_tracker.py`, `InfinitePosition`/`InfiniteTrade` 모델 |
| 실험실 | 다종목/지수 비교 차트, 사용자 정의 조건 구간 하이라이트 | `app.py` `/api/lab/series` |
| 국내 스윙 | 국내(KRX) 단기/스윙 백테스터, 전략 4종 | `kr_swing.py`, `kr_stocks.json` |
| 국내 퀀트 | 국내 저PER+고ROE 팩터 스크리닝 + 연간 리밸런싱 백테스트 | `kr_quant.py`, `dart_fetch.py`, `KrFundamental` |
| 스크리닝 | 미너비니 8조건 트렌드 템플릿 스크리너 (KR+US) + 종목 상세(재무 아코디언) | `trend_screener.py`, `TrendScreenCache`, `us_stocks.json` |

각 기능의 세부 설계 이유(왜 이렇게 캐시했는지, 왜 이 지표를 썼는지 등)는 코드 내 한글 주석과
클래스 docstring(특히 `models.py`)에 충실히 남겨져 있다 — 새 기능을 설계하기 전에 가장 가까운
기존 기능의 주석을 먼저 읽는 것이 가장 빠른 온보딩 경로다.

---

## 4. 로컬 개발 빠른 시작 (개발 단계 진입점)

```bash
pip install -r requirements.txt
cp .env.example .env      # 필요한 값만 채움 (다 비워도 SQLite + 랜덤 SECRET_KEY로 동작함)
python app.py             # http://localhost:3000
```

- DB: `.env`에 `DATABASE_URL` 없으면 로컬 SQLite(`data/app.db`) 자동 사용, 앱 기동 시 `db.create_all()`로 테이블 자동 생성.
- 회원가입 시 **최초 가입자가 자동으로 admin**이 된다.
- 자동화 테스트 스위트는 없다(pytest 등 미도입) — 검증 방법은 [RULES.md](RULES.md) 테스트 섹션 참고.

---

## 5. 참고 문서

- [docs/AS-IS_TO-BE.md](docs/AS-IS_TO-BE.md) — 단일 사용자 스크립트 → 다중 사용자 인증/보안 강화 전환 기록. 보안 관련 설계 결정(CSRF, XSS, rate limit, 세션 쿠키 등)의 근거가 여기 있다.
- [study/보안점검_정리.txt](study/보안점검_정리.txt) — 위 보안 강화의 근거가 된 취약점 점검 원본 메모.
- [RULES.md](RULES.md) / [.claude/skills/feature-workflow/SKILL.md](.claude/skills/feature-workflow/SKILL.md) — 실제 작업 규칙과 절차.
