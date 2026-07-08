# AS-IS / TO-BE 정리 — 사용자 관리 및 보안 강화

작성일: 2026-07-08
대상 커밋: `5f9c225`(최초) → `2cdccf4`(사용자 인증 추가) → 본 커밋(보안 강화)

## 1. 배경

원래 `stock-tracker-py`는 로컬에서 혼자 쓰는 단일 사용자용 Flask 앱이었다.
계정 개념과 권한 분리가 필요해져 사용자 관리 기능을 추가했고, 뒤이어
전체적인 보안 점검(`study/보안점검_정리.txt` 참고)을 거쳐 취약점을 보강했다.

---

## 2. AS-IS (변경 전, 커밋 `5f9c225`)

### 구조
- 앱 전체가 `app.py` 하나 + `templates/index.html` + `static/`로 구성.
- 인증/계정 개념 자체가 없음. 접속하는 모든 사람이 같은 데이터를 봄.

### 데이터 저장
- `data/config.json` — Finnhub API 키 **하나**를 전역으로 저장.
- `data/portfolio.json` — 포트폴리오 목록을 파일 하나에 배열로 저장 (사용자 구분 없음).
- `data/alerts.json` — 알림 목록도 동일하게 전역 배열.
- 동시 쓰기 시 파일 통째로 덮어쓰는 구조라 다중 사용자 확장이 불가능.

### 보안
- 인증 없음 → 권한 구분 없음 → CSRF/세션 개념 자체가 없음.
- API 키는 웹 UI에서 입력하면 평문으로 `config.json`에 저장.
- 로컬(localhost) 단독 사용을 전제로 설계되어 위 사항들이 문제되지 않았음.
- 형상관리(git) 없음.

---

## 3. TO-BE (변경 후)

### 3.1 형상관리
- git 저장소 초기화, GitHub 원격 `ajh8920/JH-Trader` 연결.

### 3.2 데이터 계층 — JSON 파일 → SQLAlchemy ORM
- `models.py` 신설: `User`, `PortfolioItem`, `Alert` 3개 테이블.
- 기본은 SQLite(`data/app.db`)이지만 `DATABASE_URL` 환경변수만 설정하면
  코드 변경 없이 PostgreSQL 등으로 전환 가능 (SQLAlchemy가 방언 차이를 흡수).
- 포트폴리오/알림 모두 `user_id` 외래키로 연결 — 계정별 데이터 완전 격리.

### 3.3 계정 및 권한 관리
- 회원가입(`/register`) · 로그인(`/login`) · 로그아웃(`/logout`) 추가 (Flask-Login).
- 비밀번호는 werkzeug `generate_password_hash`로 해시 저장 (평문 저장 안 함).
- `role` 컬럼으로 `admin` / `user` 구분. **최초 가입자는 자동으로 admin.**
- 관리자 전용 페이지 `/admin`: 사용자 목록 조회, 권한 변경, 계정 삭제.
- 안전장치: 자기 자신의 admin 권한 해제 불가, 자기 계정 삭제 불가
  (관리자가 실수로 스스로 잠기는 것 방지).

### 3.4 API 키 관리
- 계정별로 Finnhub API 키를 별도 저장 (`User.api_key`).
- `.env` 파일(git 미포함)에 `FINNHUB_API_KEY` 기본값을 두고,
  계정에 개별 키가 없으면 이 기본값으로 자동 폴백 → 로그인할 때마다
  키를 다시 입력할 필요 없음.
- `SECRET_KEY`도 `.env`에 없으면 서버가 안전한 랜덤 값을 자동 생성해
  같은 파일에 저장 → 재시작해도 기존 로그인 세션 유지.

### 3.5 보안 강화
전체 취약점 점검(`study/보안점검_정리.txt`) 결과를 반영해 아래 항목을 수정했다.

| 항목 | AS-IS | TO-BE |
|---|---|---|
| SECRET_KEY | 코드에 고정 문자열(`dev-secret-change-in-production`) 하드코딩 | `.env` 미설정 시 랜덤 생성 후 자동 저장 |
| 저장형 XSS | 아이디 3자 이상만 검사, `admin.html`/`app.js`에서 사용자 입력을 `innerHTML`로 그대로 출력 | 아이디/티커에 서버측 정규식 검증(`USERNAME_RE`, `TICKER_RE`) + 클라이언트에서 `escapeHtml()`로 이스케이프 후 출력 |
| 로그인 무차별 대입 | 시도 횟수 제한 없음 | Flask-Limiter로 로그인/회원가입 분당 10회 제한 (429 응답 확인) |
| CSRF | 토큰 없음, 세션 쿠키만으로 인증 | Flask-WTF CSRFProtect 적용 (로그인/회원가입 폼), JSON API는 same-origin + Content-Type 프리플라이트로 보호되므로 명시적으로 예외 처리 |
| 보안 헤더 | 없음 | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` 추가 |
| Finnhub 요청 URL 조립 | 티커를 f-string으로 쿼리스트링에 직접 삽입 (`&` 등 특수문자 주입 가능) | `requests`의 `params=`로 전달해 URL 인코딩 위임, 경로 파라미터 라우트(`/api/stock/<ticker>`, `/api/quote/<ticker>`)에도 `TICKER_RE` 검증 추가 |
| 숫자 입력 처리 | `float()` 변환 실패 시 500 에러 | try/except로 감싸 400 + 에러 메시지 반환 |
| 세션 쿠키 | 기본값 그대로 | `HttpOnly`, `SameSite=Lax` 명시, `COOKIE_SECURE` 환경변수로 HTTPS 배포 시 `Secure` 옵션 제어 |

### 3.6 검증
- 실제 서버에 대해 회원가입/로그인/로그아웃, 관리자 페이지 권한 체크(403),
  계정 간 데이터 격리, rate limit(429), CSRF 토큰 누락 시 차단(400),
  잘못된 티커 형식 차단(400), 숫자 아닌 입력 처리(400), 기본 API 키
  폴백 동작을 curl/Python 스크립트로 직접 확인함.

---

## 4. 남은 과제 / 권장 사항

1. **[조치 필요] Finnhub API 키 재발급 권장** — 현재 키(`d8rrgi...`)가
   대화 중 평문으로 노출된 적이 있음. `.env`는 git에 포함되지 않아
   저장소 유출 위험은 없지만, 대화 로그 등을 통한 노출 가능성을
   배제할 수 없으므로 [finnhub.io/dashboard](https://finnhub.io/dashboard)에서
   재발급하는 것을 권장.
2. 계정별 API 키가 DB에 평문 저장됨 — 로컬 전용이라 당장은 감수 가능하지만,
   외부 배포 시에는 암호화 저장 고려.
3. `pyjwt`가 `requirements.txt`에 추가되어 있으나 현재 코드에서는 미사용.
   향후 토큰 기반 API 인증이 필요할 때 사용 예정.
4. 외부(공개 인터넷) 배포 시 `COOKIE_SECURE=true` + HTTPS 필수, 현재는
   로컬 개발 전제로 `false` 기본값.

---

## 5. 변경 파일

```
app.py                    수정 — 인증/권한/보안 로직 전면 추가
models.py                 신설 — User/PortfolioItem/Alert ORM 모델
templates/login.html      신설 — 로그인 폼 (CSRF 토큰 포함)
templates/register.html   신설 — 회원가입 폼 (CSRF 토큰 포함)
templates/admin.html      신설 — 관리자 사용자 관리 페이지
templates/index.html      수정 — 로그인 사용자 정보, 로그아웃, 관리자 링크 추가
static/app.js             수정 — escapeHtml 적용, 401 처리, 알림 삭제 id 기반으로 변경
requirements.txt          수정 — flask-sqlalchemy, flask-login, flask-wtf, flask-limiter, python-dotenv 추가
.gitignore                수정 — data/, .env 제외
.env.example              신설 — 환경변수 템플릿 (커밋 대상, 실제 값 없음)
.env                       신설 — 실제 키 저장 (gitignore 처리, 커밋 안 됨)
```
