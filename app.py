import os
import re
import secrets
import time
import threading
from datetime import datetime
from functools import wraps
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, redirect, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_wtf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash

from backtest import run_infinite_buying
from infinite_buying import get_target_default, get_version_defaults
from live_tracker import compute_position_status
from models import Alert, InfinitePosition, InfiniteTrade, PortfolioItem, User, db

def sanitize_json(value):
    """NaN/Infinity는 표준 JSON에 없는 값이라 브라우저의 JSON.parse가 깨진다.
    jsonify로 내보내기 전에 재귀적으로 None으로 바꿔 방어한다."""
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # value != value → NaN
            return None
        return value
    if isinstance(value, dict):
        return {k: sanitize_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_json(v) for v in value]
    return value


BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)

app = Flask(__name__)


def _asset_version(filename):
    try:
        return int(os.path.getmtime(BASE_DIR / "static" / filename))
    except OSError:
        return 1


@app.context_processor
def inject_asset_version():
    # 정적 파일 URL에 수정 시각을 쿼리 파라미터로 붙여, 배포 후 브라우저가
    # 이전 버전의 app.js/style.css를 캐시에서 그대로 쓰는 문제를 방지한다.
    return {"asset_version": _asset_version}


def _load_or_create_secret_key():
    key = os.environ.get("SECRET_KEY")
    if key:
        return key

    key = secrets.token_hex(32)
    try:
        if ENV_FILE.exists():
            text = ENV_FILE.read_text(encoding="utf-8")
            if "SECRET_KEY=" in text:
                text = re.sub(r"^SECRET_KEY=.*$", f"SECRET_KEY={key}", text, flags=re.MULTILINE)
            else:
                text += f"\nSECRET_KEY={key}\n"
            ENV_FILE.write_text(text, encoding="utf-8")
        else:
            ENV_FILE.write_text(f"SECRET_KEY={key}\n", encoding="utf-8")
        print("[안내] SECRET_KEY를 새로 생성해 .env에 저장했습니다.")
    except OSError as e:
        print(f"[경고] SECRET_KEY를 .env에 저장하지 못했습니다 ({e}). 재시작 시 세션이 풀립니다.")
    return key


app.config["SECRET_KEY"] = _load_or_create_secret_key()

# 개발 중엔 SQLite 파일을 사용하고, 운영 전환 시 DATABASE_URL 환경변수만 설정하면
# (예: postgresql://user:pw@host/dbname) 코드 변경 없이 PostgreSQL로 옮길 수 있습니다.
# Render 등 일부 호스팅은 예전 스킴인 postgres://로 URL을 주는데, SQLAlchemy 1.4+는
# postgresql://만 인식하므로 여기서 보정한다.
_database_url = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'app.db'}")
if _database_url.startswith("postgres://"):
    _database_url = _database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "false").lower() == "true"

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"

csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per hour"])

# 개인 Finnhub 키를 등록하지 않은 계정을 위한 서버 공용 기본 키 (.env, git에 커밋되지 않음)
DEFAULT_FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")

FH_BASE = "https://finnhub.io/api/v1"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"error": "로그인이 필요합니다"}), 401
    return redirect(url_for("login"))


def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            return jsonify({"error": "관리자 권한이 필요합니다"}), 403
        return f(*args, **kwargs)

    return wrapper


def get_effective_api_key(user):
    return user.api_key or DEFAULT_FINNHUB_KEY


@app.after_request
def set_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


# /api/ 요청은 항상 JSON으로 응답해야 프런트의 res.json()이 깨지지 않는다.
# 처리되지 않은 예외가 나면 Flask/Werkzeug가 기본 HTML 에러 페이지를 돌려주는데,
# 그러면 브라우저가 "Unexpected token '<'"로 파싱에 실패하므로 여기서 가로챈다.
@app.errorhandler(Exception)
def handle_uncaught_exception(e):
    from werkzeug.exceptions import HTTPException

    if not request.path.startswith("/api/"):
        # 페이지 경로는 Flask/Werkzeug 기본 처리에 맡긴다.
        if isinstance(e, HTTPException):
            return e
        app.logger.exception("처리 중 예외 발생: %s", request.path)
        return "Internal Server Error", 500

    if isinstance(e, HTTPException):
        return jsonify({"error": e.description or "요청을 처리할 수 없습니다"}), e.code or 500
    app.logger.exception("API 처리 중 예외 발생: %s", request.path)
    return jsonify({"error": "서버에서 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요."}), 500


# ─── Finnhub API 호출 ────────────────────────────────────────────────────────

def fh_get(path, api_key, params=None):
    if not api_key:
        return None, "API 키가 설정되지 않았습니다"
    try:
        res = requests.get(
            f"{FH_BASE}{path}",
            params={**(params or {}), "token": api_key},
            timeout=10,
            headers={"User-Agent": "StockTracker/1.0"},
        )
        if res.status_code in (401, 403):
            return None, f"API 키가 올바르지 않습니다 ({res.status_code})"
        if not res.ok:
            return None, f"요청 실패 ({res.status_code})"
        return res.json(), None
    except requests.Timeout:
        return None, "요청 시간 초과"
    except Exception as e:
        return None, str(e)


# ─── 인증 ────────────────────────────────────────────────────────────────────

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "GET":
        return render_template("register.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not USERNAME_RE.match(username):
        return render_template(
            "register.html", error="아이디는 영문/숫자/밑줄만 사용해 3~20자로 입력하세요"
        )
    if len(password) < 6:
        return render_template("register.html", error="비밀번호는 6자 이상이어야 합니다")

    if User.query.filter_by(username=username).first():
        return render_template("register.html", error="이미 사용 중인 아이디입니다")

    # 최초 가입자는 자동으로 관리자 권한을 갖습니다.
    is_first_user = User.query.count() == 0
    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        role="admin" if is_first_user else "user",
    )
    db.session.add(user)
    db.session.commit()

    login_user(user)
    return redirect(url_for("index"))


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "GET":
        return render_template("login.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    user = User.query.filter_by(username=username).first()

    if not user or not check_password_hash(user.password_hash, password):
        return render_template("login.html", error="아이디 또는 비밀번호가 올바르지 않습니다")

    login_user(user)
    return redirect(url_for("index"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ─── 페이지 ─────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html", has_key=bool(get_effective_api_key(current_user)))


@app.route("/admin")
def admin_page():
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    if not current_user.is_admin:
        return redirect(url_for("index"))
    return render_template("admin.html")


# ─── 설정 API ────────────────────────────────────────────────────────────────

@app.route("/api/settings/key", methods=["POST"])
@login_required
def save_key():
    key = request.json.get("key", "").strip()
    if not key:
        return jsonify({"error": "키를 입력하세요"}), 400

    data, err = fh_get("/quote", key, params={"symbol": "AAPL"})
    if err:
        return jsonify({"error": err}), 400

    current_user.api_key = key
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/settings/key", methods=["DELETE"])
@login_required
def delete_key():
    current_user.api_key = ""
    db.session.commit()
    return jsonify({"ok": True})


# ─── 매크로(주요 시황) API ────────────────────────────────────────────────────
# Finnhub 무료 티어는 실제 지수(^GSPC 등) 조회를 지원하지 않아(구독 필요),
# 백테스트와 동일하게 yfinance로 실제 지수 포인트/금리(%)를 가져온다. API 키가
# 없어도 누구나 볼 수 있다.

MACRO_INSTRUMENTS = [
    {"ticker": "^GSPC", "name": "S&P 500", "group": "지수", "unit": "pt"},
    {"ticker": "^NDX", "name": "나스닥 100", "group": "지수", "unit": "pt"},
    {"ticker": "^DJI", "name": "다우존스", "group": "지수", "unit": "pt"},
    {"ticker": "^RUT", "name": "러셀 2000", "group": "지수", "unit": "pt"},
    {"ticker": "^VIX", "name": "변동성지수(VIX)", "group": "변동성", "unit": "pt"},
    {"ticker": "^TNX", "name": "美 10년물 국채금리", "group": "금리", "unit": "pct"},
    {"ticker": "DX-Y.NYB", "name": "달러 인덱스", "group": "환율", "unit": "pt"},
    {"ticker": "GC=F", "name": "금 선물", "group": "원자재", "unit": "usd"},
    {"ticker": "CL=F", "name": "WTI 원유 선물", "group": "원자재", "unit": "usd"},
]


def _fetch_macro_quote(ticker, attempts=3):
    import yfinance as yf

    for attempt in range(attempts):
        try:
            df = yf.download(ticker, period="5d", interval="1d", progress=False, auto_adjust=False, timeout=8)
        except Exception:
            df = None
        if df is not None and not df.empty:
            if hasattr(df.columns, "get_level_values") and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
            df = df.dropna(subset=["Close"])
            if not df.empty:
                last = float(df.iloc[-1]["Close"])
                prev = float(df.iloc[-2]["Close"]) if len(df) > 1 else last
                return last, prev
        if attempt < attempts - 1:
            time.sleep(0.5)
    return None


# CNN의 비공식(문서화되지 않은) 내부 API 엔드포인트입니다. 로그인/인증이 필요
# 없고 널리 쓰이는 방식이지만, CNN이 예고 없이 바꾸거나 막을 수 있어 실패해도
# 매크로 탭 전체가 죽지 않도록 예외를 잡아 None을 반환한다.
def _fetch_fear_greed(attempts=3):
    for attempt in range(attempts):
        try:
            res = requests.get(
                "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                    ),
                    "Accept": "application/json",
                    "Referer": "https://www.cnn.com/markets/fear-and-greed",
                },
                timeout=10,
            )
            if res.ok:
                fg = res.json().get("fear_and_greed", {})
                return {
                    "score": round(fg["score"], 1),
                    "rating": fg["rating"],
                    "previousClose": round(fg["previous_close"], 1),
                    "previousWeek": round(fg["previous_1_week"], 1),
                    "previousMonth": round(fg["previous_1_month"], 1),
                    "previousYear": round(fg["previous_1_year"], 1),
                }
        except Exception:
            pass
        if attempt < attempts - 1:
            time.sleep(0.5)
    return None


# 지수/금리/공포탐욕지수는 초 단위로 바뀔 필요가 없는 값이라, 외부 API를 매번
# 다시 호출하지 않도록 짧게 캐시해 체감 속도를 크게 올린다(워커 프로세스별 캐시).
_macro_cache = {"data": None, "at": 0.0}
MACRO_CACHE_TTL_SECONDS = 90


@app.route("/api/macro")
@login_required
def get_macro():
    now = time.time()
    if _macro_cache["data"] is not None and (now - _macro_cache["at"]) < MACRO_CACHE_TTL_SECONDS:
        return jsonify(_macro_cache["data"])

    import concurrent.futures

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(MACRO_INSTRUMENTS) + 1) as ex:
        futures = {
            ex.submit(_fetch_macro_quote, item["ticker"]): item["ticker"]
            for item in MACRO_INSTRUMENTS
        }
        fg_future = ex.submit(_fetch_fear_greed)
        for fut in concurrent.futures.as_completed(futures):
            results[futures[fut]] = fut.result()
        fear_greed = fg_future.result()

    out = []
    for item in MACRO_INSTRUMENTS:
        q = results.get(item["ticker"])
        if q is None:
            out.append({
                "ticker": item["ticker"], "name": item["name"], "group": item["group"],
                "unit": item["unit"], "price": None, "change": None, "changePct": None,
            })
            continue
        last, prev = q
        out.append({
            "ticker": item["ticker"],
            "name": item["name"],
            "group": item["group"],
            "unit": item["unit"],
            "price": round(last, 2),
            "change": round(last - prev, 4),
            "changePct": round((last - prev) / prev * 100, 2) if prev else 0.0,
        })

    result = sanitize_json({"instruments": out, "fearGreed": fear_greed})

    # 일부 항목이 실패한 결과를 캐시해버리면 그 실패가 최대 90초 동안 그대로
    # 재노출된다. 전체가 성공했을 때만 캐시하고, 일부 실패 시에는 다음 요청이
    # 바로 재시도할 수 있도록 캐시를 비워둔다.
    all_ok = fear_greed is not None and all(item["price"] is not None for item in out)
    if all_ok:
        _macro_cache["data"] = result
        _macro_cache["at"] = now
    else:
        _macro_cache["data"] = None
        _macro_cache["at"] = 0.0
    return jsonify(result)


# ─── 실험실(종목/지수 비교) API ───────────────────────────────────────────────
# 지수(^GSPC), 선물(GC=F), 배당조정 등 yfinance 표기를 그대로 받아야 해서
# 일반 주식 티커보다 느슨한 정규식을 쓴다.
LAB_TICKER_RE = re.compile(r"^[A-Za-z0-9^.=\-]{1,15}$")


@app.route("/api/lab/series", methods=["POST"])
@login_required
def get_lab_series():
    body = request.json or {}
    tickers = body.get("tickers", [])
    start = body.get("start", "")
    end = body.get("end", "")

    if not isinstance(tickers, list) or not (1 <= len(tickers) <= 8):
        return jsonify({"error": "티커는 1~8개 사이로 입력하세요"}), 400
    tickers = [t.strip().upper() for t in tickers if t and t.strip()]
    for t in tickers:
        if not LAB_TICKER_RE.match(t):
            return jsonify({"error": f'"{t}" 티커 형식이 올바르지 않습니다'}), 400

    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)"}), 400
    if start_dt >= end_dt:
        return jsonify({"error": "종료일은 시작일보다 이후여야 합니다"}), 400

    from backtest import fetch_daily_prices
    import concurrent.futures

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tickers)) as ex:
        futures = {ex.submit(fetch_daily_prices, t, start, end): t for t in tickers}
        for fut in concurrent.futures.as_completed(futures):
            results[futures[fut]] = fut.result()

    all_dates = sorted({bar["date"] for bars in results.values() for bar in bars})
    if not all_dates:
        return jsonify({"error": "해당 기간의 시세 데이터를 찾을 수 없습니다"}), 400

    series = []
    invalid_tickers = []
    for t in tickers:
        bars = results.get(t) or []
        if not bars:
            invalid_tickers.append(t)
        by_date = {bar["date"]: bar["close"] for bar in bars}
        series.append({
            "ticker": t,
            "closes": [by_date.get(d) for d in all_dates],
        })

    return jsonify(sanitize_json({"dates": all_dates, "series": series, "invalidTickers": invalid_tickers}))


# ─── 종목 데이터 API ─────────────────────────────────────────────────────────

@app.route("/api/stock/<ticker>")
@login_required
def get_stock(ticker):
    ticker = ticker.upper()
    if not TICKER_RE.match(ticker):
        return jsonify({"error": "티커 형식이 올바르지 않습니다"}), 400
    key = get_effective_api_key(current_user)
    if not key:
        return jsonify({"error": "API 키가 설정되지 않았습니다"}), 401

    import concurrent.futures
    endpoints = {
        "quote": "/quote",
        "target": "/stock/price-target",
        "rec": "/stock/recommendation",
        "profile": "/stock/profile2",
    }

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {
            ex.submit(fh_get, path, key, {"symbol": ticker}): name
            for name, path in endpoints.items()
        }
        for fut in concurrent.futures.as_completed(futures):
            name = futures[fut]
            data, err = fut.result()
            results[name] = data if data is not None else {}

    quote = results.get("quote", {})
    if not quote.get("c"):
        return jsonify({"error": f'"{ticker}" 종목을 찾을 수 없습니다'}), 404

    rec_list = results.get("rec", [])
    latest_rec = rec_list[0] if isinstance(rec_list, list) and rec_list else {}

    return jsonify({
        "ticker": ticker,
        "name": results.get("profile", {}).get("name", ticker),
        "industry": results.get("profile", {}).get("finnhubIndustry", ""),
        "price": quote.get("c", 0),
        "change": quote.get("d", 0),
        "changePct": quote.get("dp", 0),
        "high": quote.get("h", 0),
        "low": quote.get("l", 0),
        "open": quote.get("o", 0),
        "prevClose": quote.get("pc", 0),
        "targetMean": results.get("target", {}).get("targetMean"),
        "targetHigh": results.get("target", {}).get("targetHigh"),
        "targetLow": results.get("target", {}).get("targetLow"),
        "targetUpdated": results.get("target", {}).get("lastUpdated", ""),
        "recBuy": (latest_rec.get("strongBuy", 0) + latest_rec.get("buy", 0)),
        "recHold": latest_rec.get("hold", 0),
        "recSell": (latest_rec.get("strongSell", 0) + latest_rec.get("sell", 0)),
        "recPeriod": latest_rec.get("period", ""),
    })


@app.route("/api/quote/<ticker>")
@login_required
def get_quote(ticker):
    ticker = ticker.upper()
    if not TICKER_RE.match(ticker):
        return jsonify({"error": "티커 형식이 올바르지 않습니다"}), 400
    data, err = fh_get("/quote", get_effective_api_key(current_user), {"symbol": ticker})
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"ticker": ticker, "price": data.get("c", 0), "changePct": data.get("dp", 0)})


# ─── 포트폴리오 API ──────────────────────────────────────────────────────────

@app.route("/api/portfolio", methods=["GET"])
@login_required
def get_portfolio():
    items = PortfolioItem.query.filter_by(user_id=current_user.id).all()
    return jsonify([i.to_dict() for i in items])


@app.route("/api/portfolio", methods=["POST"])
@login_required
def add_portfolio():
    body = request.json
    ticker = body.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "티커를 입력하세요"}), 400
    if not TICKER_RE.match(ticker):
        return jsonify({"error": "티커 형식이 올바르지 않습니다"}), 400

    try:
        qty = float(body.get("qty", 0))
        buy_price = float(body.get("buyPrice", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "수량과 매입가는 숫자여야 합니다"}), 400

    exists = PortfolioItem.query.filter_by(user_id=current_user.id, ticker=ticker).first()
    if exists:
        return jsonify({"error": f"{ticker}은(는) 이미 포트폴리오에 있습니다"}), 409

    item = PortfolioItem(
        user_id=current_user.id,
        ticker=ticker,
        qty=qty,
        buy_price=buy_price,
        name=ticker,
    )
    db.session.add(item)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/portfolio/<ticker>", methods=["DELETE"])
@login_required
def remove_portfolio(ticker):
    PortfolioItem.query.filter_by(user_id=current_user.id, ticker=ticker.upper()).delete()
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/portfolio/refresh", methods=["POST"])
@login_required
def refresh_portfolio():
    items = PortfolioItem.query.filter_by(user_id=current_user.id).all()
    if not items:
        return jsonify([])

    key = get_effective_api_key(current_user)
    if not key:
        return jsonify({"error": "API 키가 설정되지 않았습니다"}), 401

    import concurrent.futures

    def update_item(item):
        params = {"symbol": item.ticker}
        q, _ = fh_get("/quote", key, params)
        t, _ = fh_get("/stock/price-target", key, params)
        p, _ = fh_get("/stock/profile2", key, params)
        if q and q.get("c"):
            item.current_price = q["c"]
            item.change_pct = q.get("dp", 0)
        if t and t.get("targetMean"):
            item.target_price = t["targetMean"]
        if p and p.get("name"):
            item.name = p["name"]
        return item

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        items = list(ex.map(update_item, items))

    db.session.commit()
    return jsonify([i.to_dict() for i in items])


# ─── 알림 API ────────────────────────────────────────────────────────────────

@app.route("/api/alerts", methods=["GET"])
@login_required
def get_alerts():
    alerts = Alert.query.filter_by(user_id=current_user.id).order_by(Alert.id).all()
    return jsonify([a.to_dict() for a in alerts])


@app.route("/api/alerts", methods=["POST"])
@login_required
def add_alert():
    body = request.json
    ticker = body.get("ticker", "").strip().upper()
    price = body.get("price")
    kind = body.get("type", "above")

    if not ticker or price is None:
        return jsonify({"error": "티커와 가격을 입력하세요"}), 400
    if not TICKER_RE.match(ticker):
        return jsonify({"error": "티커 형식이 올바르지 않습니다"}), 400
    if kind not in ("above", "below"):
        return jsonify({"error": "type은 above 또는 below여야 합니다"}), 400
    try:
        price = float(price)
    except (TypeError, ValueError):
        return jsonify({"error": "가격은 숫자여야 합니다"}), 400

    alert = Alert(
        user_id=current_user.id,
        ticker=ticker,
        price=price,
        type=kind,
        created=time.strftime("%Y-%m-%d"),
    )
    db.session.add(alert)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/alerts/<int:alert_id>", methods=["DELETE"])
@login_required
def remove_alert(alert_id):
    Alert.query.filter_by(id=alert_id, user_id=current_user.id).delete()
    db.session.commit()
    return jsonify({"ok": True})


# ─── 무한매수법 백테스트 API ─────────────────────────────────────────────────

@app.route("/api/backtest/infinite-buying", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def backtest_infinite_buying():
    body = request.json or {}
    ticker = body.get("ticker", "").strip().upper()
    if not TICKER_RE.match(ticker):
        return jsonify({"error": "티커 형식이 올바르지 않습니다"}), 400

    version = body.get("version", "v2")
    if version not in ("v2", "v3", "v4"):
        return jsonify({"error": "버전은 v2, v3, v4 중 하나여야 합니다"}), 400
    defaults = get_version_defaults(version)

    start = body.get("start", "")
    end = body.get("end", "")
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)"}), 400
    if start_dt >= end_dt:
        return jsonify({"error": "종료일은 시작일보다 이후여야 합니다"}), 400

    try:
        seed = float(body.get("seed", 0))
        splits = int(body.get("splits", defaults["splits"]))
        target_return = float(body.get("targetReturn", get_target_default(ticker)))
    except (TypeError, ValueError):
        return jsonify({"error": "시드/분할수/목표수익률은 숫자여야 합니다"}), 400

    if seed <= 0:
        return jsonify({"error": "시드는 0보다 커야 합니다"}), 400
    if not (2 <= splits <= 100):
        return jsonify({"error": "분할수는 2~100 사이여야 합니다"}), 400
    if not (0 < target_return <= 200):
        return jsonify({"error": "목표수익률은 0~200 사이여야 합니다"}), 400

    result = run_infinite_buying(ticker, start, end, seed, splits, target_return, version)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(sanitize_json(result))


# ─── 무한매수법 실전 현황 API ─────────────────────────────────────────────────

@app.route("/api/infinite/positions", methods=["GET"])
@login_required
def list_infinite_positions():
    positions = InfinitePosition.query.filter_by(user_id=current_user.id).order_by(InfinitePosition.id).all()
    key = get_effective_api_key(current_user)
    results = []
    for p in positions:
        current_price = None
        if key:
            data, err = fh_get("/quote", key, {"symbol": p.ticker})
            if data and data.get("c"):
                current_price = data["c"]
        results.append(compute_position_status(p, current_price))
    return jsonify(sanitize_json(results))


@app.route("/api/infinite/positions", methods=["POST"])
@login_required
def add_infinite_position():
    body = request.json or {}
    ticker = body.get("ticker", "").strip().upper()
    if not TICKER_RE.match(ticker):
        return jsonify({"error": "티커 형식이 올바르지 않습니다"}), 400

    version = body.get("version", "v2")
    if version not in ("v2", "v3", "v4"):
        return jsonify({"error": "버전은 v2, v3, v4 중 하나여야 합니다"}), 400

    try:
        seed = float(body.get("seed", 0))
        splits = int(body.get("splits", 40))
        target_return = float(body.get("targetReturn", get_target_default(ticker)))
    except (TypeError, ValueError):
        return jsonify({"error": "시드/분할수/목표수익률은 숫자여야 합니다"}), 400

    if seed <= 0:
        return jsonify({"error": "시드는 0보다 커야 합니다"}), 400
    if not (2 <= splits <= 100):
        return jsonify({"error": "분할수는 2~100 사이여야 합니다"}), 400
    if not (0 < target_return <= 200):
        return jsonify({"error": "목표수익률은 0~200 사이여야 합니다"}), 400

    position = InfinitePosition(
        user_id=current_user.id, ticker=ticker, version=version,
        splits=splits, target_return_pct=target_return, seed=seed,
    )
    db.session.add(position)
    db.session.commit()
    return jsonify(sanitize_json(compute_position_status(position)))


@app.route("/api/infinite/positions/<int:position_id>", methods=["DELETE"])
@login_required
def delete_infinite_position(position_id):
    position = InfinitePosition.query.filter_by(id=position_id, user_id=current_user.id).first()
    if not position:
        return jsonify({"error": "포지션을 찾을 수 없습니다"}), 404
    db.session.delete(position)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/infinite/positions/<int:position_id>/trades", methods=["POST"])
@login_required
def add_infinite_trade(position_id):
    position = InfinitePosition.query.filter_by(id=position_id, user_id=current_user.id).first()
    if not position:
        return jsonify({"error": "포지션을 찾을 수 없습니다"}), 404

    body = request.json or {}
    trade_date = body.get("date", "").strip()
    action = body.get("action", "").strip().lower()
    if action not in ("buy", "sell"):
        return jsonify({"error": "구분은 buy 또는 sell이어야 합니다"}), 400
    try:
        datetime.strptime(trade_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)"}), 400
    try:
        price = float(body.get("price", 0))
        qty = int(body.get("qty", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "가격과 수량은 숫자여야 합니다"}), 400
    if price <= 0 or qty <= 0:
        return jsonify({"error": "가격과 수량은 0보다 커야 합니다"}), 400

    trade = InfiniteTrade(
        position_id=position.id, trade_date=trade_date, action=action,
        price=price, qty=qty, note=(body.get("note", "") or "").strip()[:255],
    )
    db.session.add(trade)
    db.session.commit()
    return jsonify(sanitize_json(compute_position_status(position)))


@app.route("/api/infinite/positions/<int:position_id>/trades/<int:trade_id>", methods=["DELETE"])
@login_required
def delete_infinite_trade(position_id, trade_id):
    position = InfinitePosition.query.filter_by(id=position_id, user_id=current_user.id).first()
    if not position:
        return jsonify({"error": "포지션을 찾을 수 없습니다"}), 404
    InfiniteTrade.query.filter_by(id=trade_id, position_id=position.id).delete()
    db.session.commit()
    return jsonify(sanitize_json(compute_position_status(position)))


@app.route("/api/infinite/positions/<int:position_id>/trades", methods=["GET"])
@login_required
def get_infinite_trades(position_id):
    position = InfinitePosition.query.filter_by(id=position_id, user_id=current_user.id).first()
    if not position:
        return jsonify({"error": "포지션을 찾을 수 없습니다"}), 404
    return jsonify([t.to_dict() for t in position.trades])


# ─── 관리자 API ──────────────────────────────────────────────────────────────

@app.route("/api/admin/users", methods=["GET"])
@admin_required
def list_users():
    users = User.query.order_by(User.id).all()
    return jsonify([
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "createdAt": u.created_at.strftime("%Y-%m-%d") if u.created_at else "",
        }
        for u in users
    ])


@app.route("/api/admin/users/<int:user_id>/role", methods=["PATCH"])
@admin_required
def update_user_role(user_id):
    role = request.json.get("role")
    if role not in ("admin", "user"):
        return jsonify({"error": "role은 admin 또는 user여야 합니다"}), 400

    if user_id == current_user.id and role != "admin":
        return jsonify({"error": "자기 자신의 관리자 권한은 해제할 수 없습니다"}), 400

    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "사용자를 찾을 수 없습니다"}), 404

    user.role = role
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id):
    if user_id == current_user.id:
        return jsonify({"error": "자기 자신은 삭제할 수 없습니다"}), 400

    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "사용자를 찾을 수 없습니다"}), 404

    db.session.delete(user)
    db.session.commit()
    return jsonify({"ok": True})


# JSON API는 CSRF 토큰 대신 로그인 세션 + JSON Content-Type(교차 출처 요청 시
# 브라우저 프리플라이트로 차단됨) 조합으로 보호하므로 폼 기반 CSRF 검사에서 제외합니다.
for _view in (
    save_key, delete_key, get_stock, get_quote, get_macro, get_lab_series,
    get_portfolio, add_portfolio, remove_portfolio, refresh_portfolio,
    get_alerts, add_alert, remove_alert,
    backtest_infinite_buying,
    list_infinite_positions, add_infinite_position, delete_infinite_position,
    add_infinite_trade, delete_infinite_trade, get_infinite_trades,
    list_users, update_user_role, delete_user,
):
    csrf.exempt(_view)


# ─── 백그라운드 알림 체크 ────────────────────────────────────────────────────

def alert_checker():
    while True:
        time.sleep(30)
        with app.app_context():
            try:
                pending = Alert.query.filter_by(triggered=False).all()
                changed = False
                for a in pending:
                    user = db.session.get(User, a.user_id)
                    if not user:
                        continue
                    key = get_effective_api_key(user)
                    if not key:
                        continue
                    data, _ = fh_get("/quote", key, {"symbol": a.ticker})
                    if not data:
                        continue
                    cur = data.get("c", 0)
                    hit = (a.type == "above" and cur >= a.price) or \
                          (a.type == "below" and cur <= a.price)
                    if hit:
                        a.triggered = True
                        a.triggered_at = time.strftime("%H:%M:%S")
                        changed = True
                        print(f"[알림] {user.username} - {a.ticker} ${cur:.2f} — 목표 ${a.price:.2f} 달성!")
                if changed:
                    db.session.commit()
            except Exception as e:
                print(f"[알림 체크 오류] {e}")


# ─── 초기화 ──────────────────────────────────────────────────────────────────
# gunicorn 등 WSGI 서버로 구동해도(=__name__ != "__main__") DB 테이블 생성과 알림
# 체크 스레드가 항상 시작되도록 모듈 임포트 시점에 실행한다.

with app.app_context():
    db.create_all()

threading.Thread(target=alert_checker, daemon=True).start()


# ─── 실행 (로컬 개발 서버) ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  미국 주식 목표가 트래커")
    print("  http://localhost:3000 으로 접속하세요")
    print("=" * 50)
    app.run(debug=False, port=3000)
