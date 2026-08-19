import json
import os
import re
import secrets
import time
import threading
from datetime import datetime, timedelta, timezone
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
from kr_swing import STRATEGIES as KR_SWING_STRATEGIES, run_kr_swing_backtest
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
# 커넥션 풀에 남아있던 죽은 연결(DB 재시작, idle timeout 등으로 끊긴 연결)을 쓰려다
# 요청이 그대로 실패하는 걸 막는다 - 매 체크아웃 시 가벼운 SELECT 1로 살아있는지
# 확인하고, 죽어있으면 자동으로 새 연결을 만든다.
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

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
    return render_template(
        "index.html",
        has_key=bool(get_effective_api_key(current_user)),
        show_all_tabs=current_user.is_admin,
    )


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
    {"ticker": "^GSPC", "name": "S&P 500", "group": "Indices", "unit": "pt"},
    {"ticker": "^NDX", "name": "Nasdaq 100", "group": "Indices", "unit": "pt"},
    {"ticker": "^DJI", "name": "Dow Jones", "group": "Indices", "unit": "pt"},
    {"ticker": "^RUT", "name": "Russell 2000", "group": "Indices", "unit": "pt"},
    {"ticker": "^VIX", "name": "VIX", "group": "Volatility", "unit": "pt"},
    # 국채 금리: 2년물은 CBOE 지수 티커가 따로 없어 ICE 2년물 금리 선물(2YY=F)로 대체한다
    # (실제 트레이더들도 흔히 쓰는 대체 지표). 5/10/30년물은 CBOE 금리 지수를 그대로 쓴다.
    {"ticker": "2YY=F", "name": "US 2Y Treasury Yield", "group": "Rates", "unit": "pct"},
    {"ticker": "^FVX", "name": "US 5Y Treasury Yield", "group": "Rates", "unit": "pct"},
    {"ticker": "^TNX", "name": "US 10Y Treasury Yield", "group": "Rates", "unit": "pct"},
    {"ticker": "^TYX", "name": "US 30Y Treasury Yield", "group": "Rates", "unit": "pct"},
    {"ticker": "DX-Y.NYB", "name": "Dollar Index", "group": "FX", "unit": "pt"},
    {"ticker": "GC=F", "name": "Gold Futures", "group": "Commodities", "unit": "usd"},
    {"ticker": "CL=F", "name": "WTI Crude Futures", "group": "Commodities", "unit": "usd"},
]


def _fetch_macro_quote(ticker, attempts=3):
    """최근가/전일가와 함께 최근 1개월 종가 시계열(미니차트용)을 반환한다."""
    import yfinance as yf

    for attempt in range(attempts):
        try:
            df = yf.download(ticker, period="1mo", interval="1d", progress=False, auto_adjust=False, timeout=8)
        except Exception:
            df = None
        if df is not None and not df.empty:
            if hasattr(df.columns, "get_level_values") and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
            df = df.dropna(subset=["Close"])
            if not df.empty:
                series = [round(float(c), 4) for c in df["Close"].tolist()]
                last = series[-1]
                prev = series[-2] if len(series) > 1 else last
                return last, prev, series
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
                "unit": item["unit"], "price": None, "change": None, "changePct": None, "series": [],
            })
            continue
        last, prev, series = q
        out.append({
            "ticker": item["ticker"],
            "name": item["name"],
            "group": item["group"],
            "unit": item["unit"],
            "price": round(last, 2),
            "change": round(last - prev, 4),
            "changePct": round((last - prev) / prev * 100, 2) if prev else 0.0,
            "series": series,
        })

    result = sanitize_json({
        "instruments": out, "fearGreed": fear_greed,
        "asOf": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })

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


# ─── 국내(KRX) 스윙 백테스트 API ─────────────────────────────────────────────
# 6자리 종목코드(예: 005930) 또는 명시적으로 .KS/.KQ가 붙은 코드를 받는다.
KR_CODE_RE = re.compile(r"^\d{6}(\.K[SQ])?$", re.IGNORECASE)

# 종목명 검색용 코스피/코스닥 전체 종목 코드-이름 매핑(정적 스냅샷, FinanceDataReader로
# 생성). 상장폐지·사명변경 등으로 시간이 지나면 조금씩 어긋날 수 있으나, 검색 자동완성
# 용도로는 충분하다.
KR_STOCKS_PATH = BASE_DIR / "kr_stocks.json"
try:
    with open(KR_STOCKS_PATH, "r", encoding="utf-8") as _f:
        KR_STOCKS = json.load(_f)
except (OSError, ValueError):
    KR_STOCKS = []


@app.route("/api/kr-swing/search-stocks")
@login_required
def search_kr_stocks():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": []})

    q_lower = q.lower()
    starts, contains = [], []
    for item in KR_STOCKS:
        name_lower = item["name"].lower()
        if name_lower.startswith(q_lower) or item["code"].startswith(q):
            starts.append(item)
        elif q_lower in name_lower:
            contains.append(item)

    return jsonify({"results": (starts + contains)[:20]})


@app.route("/api/kr-swing/backtest", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def kr_swing_backtest():
    body = request.json or {}
    strategy = body.get("strategy", "")
    code = str(body.get("code", "")).strip()

    if strategy not in KR_SWING_STRATEGIES:
        return jsonify({"error": "지원하지 않는 전략입니다"}), 400
    if not KR_CODE_RE.match(code):
        return jsonify({"error": "종목 코드 형식이 올바르지 않습니다 (예: 005930)"}), 400

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
    except (TypeError, ValueError):
        return jsonify({"error": "시드는 숫자여야 합니다"}), 400
    if seed <= 0:
        return jsonify({"error": "시드는 0보다 커야 합니다"}), 400

    raw_params = body.get("params") or {}
    if not isinstance(raw_params, dict):
        return jsonify({"error": "전략 파라미터 형식이 올바르지 않습니다"}), 400
    params = {}
    for key, value in raw_params.items():
        try:
            params[key] = float(value)
        except (TypeError, ValueError):
            return jsonify({"error": "전략 파라미터는 숫자여야 합니다"}), 400

    result = run_kr_swing_backtest(strategy, code, start, end, seed, params)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(sanitize_json(result))


# ─── 국내 퀀트(재무지표 팩터) 스크리닝/백테스트 API ───────────────────────────
# DART 재무데이터 백필 진행 상황도 함께 노출한다(전체 백필은 시간이 걸리므로
# 화면에서 "지금 몇 종목까지 준비됐는지"를 보여주기 위함).

@app.route("/api/kr-quant/status")
@login_required
def kr_quant_status():
    from models import KrFundamental
    import kr_quant

    total = KrFundamental.query.count()
    codes = db.session.query(KrFundamental.stock_code).distinct().count()
    age = kr_quant.price_cache_age_seconds()
    return jsonify({
        "fundamentalRows": total, "stockCount": codes,
        "priceCacheReady": age is not None,
        "priceCacheAgeSeconds": round(age) if age is not None else None,
        "priceCacheCount": len(kr_quant.get_cached_prices()),
    })


@app.route("/api/kr-quant/screen")
@login_required
@limiter.limit("10 per minute")
def kr_quant_screen():
    from kr_quant import _load_market_map, get_cached_prices, get_shares_outstanding_map, rank_candidates
    from models import KrFundamental

    try:
        top_n = int(request.args.get("topN", 20))
        min_market_cap = float(request.args.get("minMarketCap", 50_000_000_000))
    except (TypeError, ValueError):
        return jsonify({"error": "파라미터가 올바르지 않습니다"}), 400
    if not (1 <= top_n <= 100):
        return jsonify({"error": "종목 수는 1~100 사이여야 합니다"}), 400

    rows = KrFundamental.query.filter(KrFundamental.rcept_no != "").all()
    if not rows:
        return jsonify({"error": "재무데이터가 아직 준비되지 않았습니다. 잠시 후 다시 시도해주세요."}), 400

    cached_prices = get_cached_prices()
    if not cached_prices:
        return jsonify({
            "error": "현재가 데이터를 아직 준비 중입니다(서버 시작 후 몇 분 정도 걸립니다). 잠시 후 다시 시도해주세요."
        }), 503

    today = datetime.today().strftime("%Y-%m-%d")
    market_map = _load_market_map()
    shares_map = get_shares_outstanding_map()
    picks = rank_candidates(today, market_map, shares_map, rows, min_market_cap, top_n, prices=cached_prices)
    return jsonify(sanitize_json({"date": today, "picks": picks}))


# 리밸런싱 백테스트는 과거 여러 시점의 전체 시장 가격을 실시간으로 조회해야 해서
# 몇 분씩 걸릴 수 있다 - Render 요청 타임아웃(30초) 안에 못 끝나 요청이 죽는
# 문제가 있었다. 그래서 POST는 계산을 시작만 시키고 바로 job id를 돌려주고,
# 실제 계산은 백그라운드 스레드에서 돌려 QuantBacktestJob에 결과를 저장한다.
# 프런트는 GET으로 그 job이 끝날 때까지 주기적으로 상태를 확인한다.

def _run_quant_backtest_job(job_id, start_year, end_year, seed, top_n, min_market_cap):
    from kr_quant import run_quant_backtest
    from models import QuantBacktestJob

    with app.app_context():
        job = db.session.get(QuantBacktestJob, job_id)
        if not job:
            return
        job.status = "running"
        db.session.commit()
        try:
            result = run_quant_backtest(start_year, end_year, seed, top_n, min_market_cap)
        except Exception as e:
            app.logger.exception("퀀트 백테스트 작업 실패: job=%s", job_id)
            job = db.session.get(QuantBacktestJob, job_id)
            job.status = "error"
            job.error = str(e)
            db.session.commit()
            return

        job = db.session.get(QuantBacktestJob, job_id)
        if "error" in result:
            job.status = "error"
            job.error = result["error"]
        else:
            job.status = "done"
            job.result_json = json.dumps(sanitize_json(result))
        db.session.commit()


@app.route("/api/kr-quant/backtest", methods=["POST"])
@login_required
@limiter.limit("5 per minute")
def kr_quant_backtest():
    from models import QuantBacktestJob

    body = request.json or {}
    try:
        start_year = int(body.get("startYear"))
        end_year = int(body.get("endYear"))
        seed = float(body.get("seed", 0))
        top_n = int(body.get("topN", 20))
        min_market_cap = float(body.get("minMarketCap", 50_000_000_000))
    except (TypeError, ValueError):
        return jsonify({"error": "입력값이 올바르지 않습니다"}), 400

    if seed <= 0:
        return jsonify({"error": "시드는 0보다 커야 합니다"}), 400
    if not (1 <= top_n <= 100):
        return jsonify({"error": "종목 수는 1~100 사이여야 합니다"}), 400
    if not (2015 <= start_year < end_year <= datetime.today().year):
        return jsonify({"error": "연도 범위가 올바르지 않습니다"}), 400

    job = QuantBacktestJob(user_id=current_user.id, status="pending")
    db.session.add(job)
    db.session.commit()

    threading.Thread(
        target=_run_quant_backtest_job,
        args=(job.id, start_year, end_year, seed, top_n, min_market_cap),
        daemon=True,
    ).start()

    return jsonify({"jobId": job.id})


@app.route("/api/kr-quant/backtest/<int:job_id>")
@login_required
def kr_quant_backtest_status(job_id):
    from models import QuantBacktestJob

    job = QuantBacktestJob.query.filter_by(id=job_id, user_id=current_user.id).first()
    if not job:
        return jsonify({"error": "작업을 찾을 수 없습니다"}), 404

    if job.status == "done":
        return jsonify({"status": "done", "result": json.loads(job.result_json)})
    if job.status == "error":
        return jsonify({"status": "error", "error": job.error})
    return jsonify({"status": job.status})


# ─── 트렌드 템플릿 스크리닝 API ────────────────────────────────────────────────
# 미너비니류 트렌드 템플릿(8개 조건)으로 국내/미국 종목을 스크리닝한다. 결과는
# 백그라운드 스레드(trend_screen_refresher)가 미리 계산해둔 캐시를 읽기만 해서
# 항상 빠르게 응답한다.

@app.route("/api/screener/status")
@login_required
def screener_status():
    from models import TrendScreenCache

    out = {}
    for market in ("KR", "US"):
        latest = (
            TrendScreenCache.query.filter_by(market=market)
            .order_by(TrendScreenCache.updated_at.desc()).first()
        )
        count = TrendScreenCache.query.filter_by(market=market).count()
        age = (datetime.utcnow() - latest.updated_at).total_seconds() if latest and latest.updated_at else None
        as_of = latest.updated_at.replace(tzinfo=timezone.utc).isoformat() if latest and latest.updated_at else None
        out[market] = {"ready": latest is not None, "count": count, "ageSeconds": round(age) if age else None, "asOf": as_of}
    return jsonify(out)


@app.route("/api/screener/results")
@login_required
def screener_results():
    from models import TrendScreenCache

    market = request.args.get("market", "KR").upper()
    if market not in ("KR", "US"):
        return jsonify({"error": "market은 KR 또는 US여야 합니다"}), 400
    only_pass = request.args.get("onlyPass", "true").lower() != "false"

    q = TrendScreenCache.query.filter_by(market=market)
    if only_pass:
        q = q.filter_by(all_pass=True)
    # 예전에는 300개 제한이 있었다 - "8개 조건 모두 충족" 종목만 보여주던 시절엔
    # 국내 기준 보통 100개 미만이라 실제로 걸릴 일이 없었지만, 기본값이 전체 조회로
    # 바뀌면서 조건을 적게 만족하는 종목(특히 4단계·하락 추세)이 정렬 순서상 밀려
    # 300개 안에 아예 들어오지 못하는 문제가 생겨 제거했다. 유니버스 전체(국내
    # 최대 2,536 / 미국 최대 592)를 그대로 반환한다.
    rows = q.order_by(TrendScreenCache.pass_count.desc(), TrendScreenCache.rs_rating.desc()).all()

    if not rows and TrendScreenCache.query.filter_by(market=market).count() == 0:
        return jsonify({
            "error": "스크리닝 데이터를 아직 준비 중입니다(서버 시작 후 최대 몇 분에서 십수 분 걸릴 수 있습니다). 잠시 후 다시 시도해주세요."
        }), 503

    results = [{
        "code": r.code, "name": r.name, "industry": r.industry, "sector": r.sector, "price": r.price,
        "ma50": r.ma50, "ma150": r.ma150, "ma200": r.ma200,
        "week52High": r.week52_high, "week52Low": r.week52_low,
        "pctAbove52wLow": r.pct_above_52w_low, "pctBelow52wHigh": r.pct_below_52w_high,
        "rsRating": r.rs_rating, "passCount": r.pass_count, "allPass": r.all_pass, "stage": r.stage,
        "conditions": json.loads(r.conditions_json) if r.conditions_json else {},
        "volume": r.volume, "relVolume": r.rel_volume,
        "marketCap": r.market_cap, "peRatio": r.pe_ratio, "epsGrowth": r.eps_growth,
        "dividendYield": r.dividend_yield, "analystRating": r.analyst_rating,
        "metrics": json.loads(r.metrics_json) if r.metrics_json else {},
    } for r in rows]
    return jsonify(sanitize_json({"market": market, "results": results}))


@app.route("/api/screener/detail")
@login_required
@limiter.limit("30 per minute")
def screener_detail():
    from models import KrFundamental, TrendScreenCache
    import trend_screener as ts

    market = request.args.get("market", "KR").upper()
    code = request.args.get("code", "").strip()
    if market not in ("KR", "US") or not code:
        return jsonify({"error": "잘못된 요청입니다"}), 400

    row = TrendScreenCache.query.filter_by(market=market, code=code).first()
    if not row:
        return jsonify({"error": "스크리닝 결과에서 해당 종목을 찾을 수 없습니다"}), 404

    if market == "KR":
        import kr_quant
        market_map = kr_quant._load_market_map()
        ticker = f"{code}.KQ" if market_map.get(code) == "KOSDAQ" else f"{code}.KS"
    else:
        ticker = code
    end = datetime.today()
    start = end - timedelta(days=450)
    # 사용자가 클릭해서 기다리는 요청이라 재시도를 가볍게(최악의 경우에도 30초
    # 요청 타임아웃 안에 끝나도록) 해서, 못 받아오면 그냥 차트만 빈 채로 나머지
    # 정보(재무/조건 등)는 보여준다 - 요청 자체가 죽는 것보다는 낫다.
    history = ts.fetch_ohlc_history_batch(
        [ticker], start.strftime("%Y-%m-%d"), (end + timedelta(days=1)).strftime("%Y-%m-%d"),
        max_attempts=2, dl_timeout=8, backoff_base=1.0,
    )
    bars = history.get(ticker, [])

    result = {
        "code": row.code, "name": row.name, "industry": row.industry, "sector": row.sector,
        "market": market, "ticker": ticker,
        "price": row.price, "ma50": row.ma50, "ma150": row.ma150, "ma200": row.ma200,
        "week52High": row.week52_high, "week52Low": row.week52_low,
        "pctAbove52wLow": row.pct_above_52w_low, "pctBelow52wHigh": row.pct_below_52w_high,
        "rsRating": row.rs_rating, "passCount": row.pass_count, "allPass": row.all_pass, "stage": row.stage,
        "conditions": json.loads(row.conditions_json) if row.conditions_json else {},
        "priceCurve": [{"date": b["date"], "close": b["close"], "volume": b.get("volume")} for b in bars],
        "volume": row.volume, "relVolume": row.rel_volume,
        "marketCap": row.market_cap, "peRatio": row.pe_ratio, "epsGrowth": row.eps_growth,
        "dividendYield": row.dividend_yield, "analystRating": row.analyst_rating,
        "metrics": json.loads(row.metrics_json) if row.metrics_json else {},
        "financials": None, "target": None,
    }

    if market == "KR":
        fundamentals = (
            KrFundamental.query.filter_by(stock_code=code)
            .order_by(KrFundamental.bsns_year.desc()).first()
        )
        if fundamentals:
            import kr_quant
            shares = kr_quant.get_shares_outstanding_map().get(code)
            market_cap = row.price * shares if shares and row.price else None
            per = (
                round(market_cap / fundamentals.net_income, 2)
                if market_cap and fundamentals.net_income and fundamentals.net_income > 0 else None
            )
            roe = (
                round(fundamentals.net_income / fundamentals.total_equity * 100, 2)
                if fundamentals.total_equity else None
            )
            result["financials"] = {
                "bsnsYear": fundamentals.bsns_year, "totalEquity": fundamentals.total_equity,
                "netIncome": fundamentals.net_income, "revenue": fundamentals.revenue,
                "marketCap": market_cap, "per": per, "roe": roe,
            }
    else:
        key = get_effective_api_key(current_user)
        if key:
            import concurrent.futures
            endpoints = {"target": "/stock/price-target", "rec": "/stock/recommendation", "profile": "/stock/profile2"}
            fh_results = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
                futures = {ex.submit(fh_get, path, key, {"symbol": code}): name for name, path in endpoints.items()}
                for fut in concurrent.futures.as_completed(futures):
                    name = futures[fut]
                    data, _err = fut.result()
                    fh_results[name] = data if data is not None else {}
            rec_list = fh_results.get("rec", [])
            latest_rec = rec_list[0] if isinstance(rec_list, list) and rec_list else {}
            target = fh_results.get("target", {})
            profile = fh_results.get("profile", {})
            # Finnhub 무료 플랜은 목표가(price-target) 엔드포인트가 막혀 있어(403)
            # targetMean은 거의 항상 비어 있다. 그래도 추천의견(recommendation)
            # 엔드포인트는 무료로 되므로 목표가와 별개로 항상 채워둔다.
            has_target = bool(target.get("targetMean"))
            has_rec = bool(latest_rec)
            if has_target or has_rec:
                result["target"] = {
                    "targetMean": target.get("targetMean"), "targetHigh": target.get("targetHigh"),
                    "targetLow": target.get("targetLow"), "targetUpdated": target.get("lastUpdated", ""),
                    "recBuy": latest_rec.get("strongBuy", 0) + latest_rec.get("buy", 0),
                    "recHold": latest_rec.get("hold", 0),
                    "recSell": latest_rec.get("strongSell", 0) + latest_rec.get("sell", 0),
                }
            if profile.get("finnhubIndustry") or profile.get("marketCapitalization"):
                result["financials"] = {
                    "industry": profile.get("finnhubIndustry", ""),
                    "marketCap": profile.get("marketCapitalization"),  # 단위: 백만 달러
                }

    return jsonify(sanitize_json(result))


@app.route("/api/screener/watchlist", methods=["GET"])
@login_required
def list_screener_watchlist():
    from models import ScreenerWatchlist

    rows = ScreenerWatchlist.query.filter_by(user_id=current_user.id).order_by(ScreenerWatchlist.created_at.desc()).all()
    return jsonify({"items": [{"market": r.market, "code": r.code, "name": r.name} for r in rows]})


@app.route("/api/screener/watchlist", methods=["POST"])
@login_required
def add_screener_watchlist():
    from models import ScreenerWatchlist

    body = request.json or {}
    market = str(body.get("market", "")).upper()
    code = str(body.get("code", "")).strip()
    name = str(body.get("name", "")).strip()
    if market not in ("KR", "US") or not code:
        return jsonify({"error": "잘못된 요청입니다"}), 400

    existing = ScreenerWatchlist.query.filter_by(user_id=current_user.id, market=market, code=code).first()
    if not existing:
        db.session.add(ScreenerWatchlist(user_id=current_user.id, market=market, code=code, name=name))
        db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/screener/watchlist", methods=["DELETE"])
@login_required
def remove_screener_watchlist():
    from models import ScreenerWatchlist

    market = str(request.args.get("market", "")).upper()
    code = str(request.args.get("code", "")).strip()
    ScreenerWatchlist.query.filter_by(user_id=current_user.id, market=market, code=code).delete()
    db.session.commit()
    return jsonify({"ok": True})


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
    backtest_infinite_buying, kr_swing_backtest, search_kr_stocks,
    kr_quant_status, kr_quant_screen, kr_quant_backtest, kr_quant_backtest_status,
    screener_status, screener_results, screener_detail,
    list_screener_watchlist, add_screener_watchlist, remove_screener_watchlist,
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


# ─── 국내 퀀트 가격 캐시 갱신 ──────────────────────────────────────────────────
# 재무데이터가 있는 종목 전체(1,500개+)의 현재가를 매 스크리닝 요청마다 실시간
# 조회하면 Render의 요청 타임아웃(30초)을 훌쩍 넘겨 요청이 죽는다(500 응답).
# 그래서 이 스레드가 백그라운드에서 주기적으로 미리 받아 kr_quant의 캐시를
# 채워두고, /api/kr-quant/screen은 그 캐시만 읽는다.

def kr_quant_price_cache_refresher():
    import random
    import kr_quant

    # gunicorn 워커가 여러 개면 배포 직후 다 같이 시작되는데, 캐시가 DB 공유라
    # "최근에 이미 갱신됨"으로 서로를 건너뛰긴 해도 맨 처음엔 그 판단 기준이 될
    # 데이터 자체가 없어 동시에 시작할 수 있다. 워커마다 시작 시점을 무작위로
    # 흩어 그 순간이 겹칠 확률을 낮추고, 배포 직후 헬스체크와도 덜 겹치게 한다.
    time.sleep(random.uniform(20, 90))
    while True:
        with app.app_context():
            try:
                n = kr_quant.warm_current_price_cache()
                if n:
                    print(f"[퀀트 가격 캐시] {n}개 종목 현재가 갱신 완료")
            except Exception as e:
                print(f"[퀀트 가격 캐시 갱신 오류] {e}")
        time.sleep(6 * 3600)  # 6시간마다 갱신 (가격이 실시간일 필요는 없는 용도)


# ─── 트렌드 템플릿 스크리닝 캐시 갱신 ──────────────────────────────────────────
# 국내 퀀트와 같은 이유로(유니버스 전체 가격 히스토리 조회가 몇 분씩 걸림),
# 요청 중이 아니라 이 스레드가 미리 계산해 DB에 저장해두고 API는 결과만 읽는다.
# 일봉 기준 지표라 하루 한두 번이면 충분해 12시간 주기로 돌린다.

TREND_SCREEN_MIN_INTERVAL_SECONDS = 12 * 3600
# 미국 재무 보강(시가총액/PE/EPS성장률/배당수익률/애널리스트 레이팅/재무제표
# 절대금액)은 Finnhub 무료 API 호출 한도(분당 60회) 때문에 종목당 3회 호출×약
# 600종목이면 한 바퀴에 30분 넘게 걸린다 - 가격 갱신(12시간)보다 더 느슨한 주기로 돈다.
US_FUND_MIN_INTERVAL_SECONDS = 20 * 3600


def _enrich_kr_fundamentals(results):
    """국내 종목의 시가총액/PE/EPS성장률을 로컬 데이터(DART 재무 캐시 + kr_stocks.json의
    발행주식수 스냅샷)만으로 계산한다. 외부 API 호출이 없어 12시간 주기 스크리닝
    갱신에 그대로 끼워 넣어도 부담이 없다(미국처럼 별도 느린 주기가 필요 없음).
    EPS 자체가 아니라 최근 2개 연도의 순이익 증감률로 EPS 성장률을 근사한다
    (발행주식수가 두 해 사이 크게 바뀌지 않는다는 전제 - kr_quant 백테스트와 같은 근사 방식).
    """
    import kr_quant
    from models import KrFundamental

    shares_map = kr_quant.get_shares_outstanding_map()
    fund_by_code = {}
    for fr in KrFundamental.query.all():
        fund_by_code.setdefault(fr.stock_code, []).append(fr)

    for r in results:
        shares = shares_map.get(r["code"])
        market_cap = r["price"] * shares if shares and r["price"] else None
        r["marketCap"] = market_cap

        frs = sorted(fund_by_code.get(r["code"], []), key=lambda x: x.bsns_year, reverse=True)
        latest_f = frs[0] if frs else None
        prev_f = frs[1] if len(frs) > 1 else None

        r["peRatio"] = (
            round(market_cap / latest_f.net_income, 2)
            if latest_f and market_cap and latest_f.net_income and latest_f.net_income > 0 else None
        )
        r["epsGrowth"] = (
            round((latest_f.net_income - prev_f.net_income) / abs(prev_f.net_income) * 100, 1)
            if latest_f and prev_f and latest_f.net_income is not None and prev_f.net_income not in (None, 0)
            else None
        )
        r["dividendYield"] = None  # 국내는 배당 데이터 소스가 없어 항상 비움
        r["analystRating"] = None  # 국내는 애널리스트 레이팅 소스가 없어 항상 비움

        # 상세 모달 아코디언·필터용 확장 지표. DART 확장 필드(재무상태표/손익계산서)가
        # 채워진 종목은 비율까지 전부 계산하고, 아직 옛 3개 필드만 있는 종목(재조회
        # 전이거나 DART에 해당 계정이 없는 업종)은 계산 가능한 것만 채운다.
        metrics = {}
        if latest_f and latest_f.revenue and latest_f.net_income is not None:
            metrics["netMargin"] = round(latest_f.net_income / latest_f.revenue * 100, 1)
        if latest_f and latest_f.total_equity:
            if latest_f.net_income is not None:
                metrics["roe"] = round(latest_f.net_income / latest_f.total_equity * 100, 1)
            if market_cap:
                metrics["pbr"] = round(market_cap / latest_f.total_equity, 2)
        if latest_f and market_cap and latest_f.revenue:
            metrics["psr"] = round(market_cap / latest_f.revenue, 2)
        if latest_f and prev_f and latest_f.revenue and prev_f.revenue:
            metrics["revenueGrowth"] = round((latest_f.revenue - prev_f.revenue) / abs(prev_f.revenue) * 100, 1)

        if latest_f and latest_f.revenue and latest_f.operating_income is not None:
            metrics["operatingMargin"] = round(latest_f.operating_income / latest_f.revenue * 100, 1)
        if latest_f and latest_f.revenue and latest_f.gross_profit is not None:
            metrics["grossMargin"] = round(latest_f.gross_profit / latest_f.revenue * 100, 1)
        if latest_f and latest_f.total_assets and latest_f.net_income is not None:
            metrics["roa"] = round(latest_f.net_income / latest_f.total_assets * 100, 1)
        if latest_f and latest_f.current_liabilities:
            if latest_f.current_assets is not None:
                metrics["currentRatio"] = round(latest_f.current_assets / latest_f.current_liabilities * 100, 1)
            if latest_f.current_assets is not None and latest_f.inventories is not None:
                metrics["quickRatio"] = round(
                    (latest_f.current_assets - latest_f.inventories) / latest_f.current_liabilities * 100, 1
                )
        if latest_f and latest_f.total_equity and latest_f.total_liabilities is not None:
            metrics["debtRatio"] = round(latest_f.total_liabilities / latest_f.total_equity * 100, 1)
            if latest_f.cash_and_equivalents is not None:
                net_debt = latest_f.total_liabilities - latest_f.cash_and_equivalents
                metrics["netDebtRatio"] = round(net_debt / latest_f.total_equity * 100, 1)
        if latest_f and prev_f and latest_f.operating_income is not None and prev_f.operating_income:
            metrics["opIncomeGrowth"] = round(
                (latest_f.operating_income - prev_f.operating_income) / abs(prev_f.operating_income) * 100, 1
            )

        # 재무상태표/포괄손익계산서 카테고리용 절대금액(원 단위 그대로).
        if latest_f:
            for key, val in (
                ("totalAssets", latest_f.total_assets), ("currentAssets", latest_f.current_assets),
                ("totalLiabilities", latest_f.total_liabilities), ("currentLiabilities", latest_f.current_liabilities),
                ("totalEquity", latest_f.total_equity), ("equityAttributable", latest_f.equity_attributable),
                ("issuedCapital", latest_f.issued_capital), ("revenue", latest_f.revenue),
                ("grossProfit", latest_f.gross_profit), ("operatingIncome", latest_f.operating_income),
                ("profitBeforeTax", latest_f.profit_before_tax), ("netIncome", latest_f.net_income),
                ("netIncomeAttributable", latest_f.net_income_attributable),
            ):
                if val is not None:
                    metrics[key] = val

        r["metrics"] = metrics


def trend_screen_refresher():
    import random
    from models import TrendScreenCache
    import trend_screener

    time.sleep(random.uniform(30, 120))
    while True:
        for market in ("KR", "US"):
            with app.app_context():
                try:
                    latest = (
                        TrendScreenCache.query.filter_by(market=market)
                        .order_by(TrendScreenCache.updated_at.desc()).first()
                    )
                    if latest and latest.updated_at:
                        age = (datetime.utcnow() - latest.updated_at).total_seconds()
                        if age < TREND_SCREEN_MIN_INTERVAL_SECONDS:
                            continue

                    results = trend_screener.run_screen(market)
                    if market == "KR":
                        _enrich_kr_fundamentals(results)

                    now = datetime.utcnow()
                    # delete-all 후 재삽입하면 미국 종목의 Finnhub 재무 보강 결과가
                    # 이 12시간 주기 갱신마다 통째로 날아간다(재무 보강은 훨씬 느린
                    # 별도 주기로 돈다) - 종목별 upsert로 바꿔 market_cap/pe_ratio 등
                    # 재무 보강 컬럼은 그대로 보존한다.
                    existing = {row.code: row for row in TrendScreenCache.query.filter_by(market=market).all()}
                    seen = set()
                    for r in results:
                        seen.add(r["code"])
                        row = existing.get(r["code"])
                        if row is None:
                            row = TrendScreenCache(market=market, code=r["code"])
                            db.session.add(row)
                        row.name = r["name"]
                        row.industry = r.get("industry")
                        row.sector = r.get("sector")
                        row.price = r["price"]
                        row.ma50 = r["ma50"]
                        row.ma150 = r["ma150"]
                        row.ma200 = r["ma200"]
                        row.week52_high = r["week52High"]
                        row.week52_low = r["week52Low"]
                        row.pct_above_52w_low = r["pctAbove52wLow"]
                        row.pct_below_52w_high = r["pctBelow52wHigh"]
                        row.rs_rating = r.get("rsRating")
                        row.pass_count = r["passCount"]
                        row.all_pass = r["allPass"]
                        row.stage = r.get("stage")
                        row.conditions_json = json.dumps(r["conditions"])
                        row.volume = r.get("volume")
                        row.rel_volume = r.get("relVolume")
                        if market == "KR":
                            row.market_cap = r.get("marketCap")
                            row.pe_ratio = r.get("peRatio")
                            row.eps_growth = r.get("epsGrowth")
                            row.dividend_yield = r.get("dividendYield")
                            row.analyst_rating = r.get("analystRating")
                            row.metrics_json = json.dumps(r.get("metrics") or {})
                        row.updated_at = now
                    for code, row in existing.items():
                        if code not in seen:
                            db.session.delete(row)
                    db.session.commit()
                    print(f"[트렌드 스크리닝] {market} {len(results)}개 종목 평가 완료 "
                          f"({sum(1 for r in results if r['allPass'])}개 전 조건 통과)")
                except Exception as e:
                    app.logger.exception("트렌드 스크리닝 갱신 오류: %s", market)
        time.sleep(1800)  # 30분마다 깨어나 시장별로 갱신 필요 여부를 확인


def _extract_us_metrics(m):
    """Finnhub /stock/metric 응답(m)에서 종목 상세 모달 아코디언에 쓸 확장 지표만
    골라낸다. 이미 받아오는 응답 안에 다 들어있어 추가 API 호출은 없다."""
    def pct(key):
        v = m.get(key)
        return round(v, 1) if isinstance(v, (int, float)) else None

    def ratio(key):
        v = m.get(key)
        return round(v * 100, 1) if isinstance(v, (int, float)) else None

    def mult(key):
        v = m.get(key)
        return round(v, 2) if isinstance(v, (int, float)) else None

    metrics = {
        "grossMargin": pct("grossMarginTTM"),
        "operatingMargin": pct("operatingMarginTTM"),
        "netMargin": pct("netProfitMarginTTM"),
        "roe": pct("roeTTM"),
        "roa": pct("roaTTM"),
        "revenueGrowth": pct("revenueGrowthTTMYoy"),
        "currentRatio": ratio("currentRatioQuarterly"),
        "quickRatio": ratio("quickRatioQuarterly"),
        "debtRatio": ratio("totalDebt/totalEquityQuarterly"),
        "pbr": mult("pbQuarterly"),
        "psr": mult("psTTM"),
        "evEbitda": mult("evEbitdaTTM"),
    }
    return {k: v for k, v in metrics.items() if v is not None}


# Finnhub /stock/financials-reported는 SEC XBRL 원문을 그대로 넘겨주는데, 같은
# 항목이라도 회사마다 태그(concept)가 조금씩 다르게 붙는다(예: 매출액이
# RevenueFromContractWithCustomerExcludingAssessedTax vs ...IncludingAssessedTax).
# 흔한 변형을 우선순위 순으로 나열해 첫 매칭을 쓴다. 은행/금융업은 재무제표
# 구조 자체가 달라(매출/매출원가 개념이 없음) 상당수 항목이 비게 되는데,
# 이건 데이터 자체의 한계라 국내 종목처럼 "-"로 표시된다.
US_CONCEPT_CANDIDATES = {
    "totalAssets": ["us-gaap_Assets"],
    "currentAssets": ["us-gaap_AssetsCurrent"],
    "totalLiabilities": ["us-gaap_Liabilities"],
    "currentLiabilities": ["us-gaap_LiabilitiesCurrent"],
    "totalEquity": ["us-gaap_StockholdersEquity", "us-gaap_StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "inventories": ["us-gaap_InventoryNet"],
    "cash": ["us-gaap_CashAndCashEquivalentsAtCarryingValue", "us-gaap_CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "revenue": [
        "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap_RevenueFromContractWithCustomerIncludingAssessedTax",
        "us-gaap_Revenues", "us-gaap_RevenuesNetOfInterestExpense",
    ],
    "grossProfit": ["us-gaap_GrossProfit"],
    "operatingIncome": ["us-gaap_OperatingIncomeLoss"],
    "profitBeforeTax": [
        "us-gaap_IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "us-gaap_IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    "netIncome": ["us-gaap_NetIncomeLoss", "us-gaap_ProfitLoss"],
}


def _extract_us_financials(financials_data):
    """Finnhub /stock/financials-reported 응답에서 재무상태표(bs)·손익계산서(ic)
    절대금액을 뽑는다. 가장 최근 연간 보고서 하나만 쓴다."""
    data = (financials_data or {}).get("data") or []
    if not data:
        return {}
    report = data[0].get("report") or {}
    by_concept = {}
    for section in ("bs", "ic"):
        for item in report.get(section, []) or []:
            c = item.get("concept")
            v = item.get("value")
            if c and c not in by_concept and isinstance(v, (int, float)):
                by_concept[c] = v

    out = {}
    for field, candidates in US_CONCEPT_CANDIDATES.items():
        for c in candidates:
            if c in by_concept:
                out[field] = by_concept[c]
                break
    return out


def _analyst_rating_label(rec):
    if not rec:
        return None
    buy = (rec.get("strongBuy", 0) or 0) + (rec.get("buy", 0) or 0)
    hold = rec.get("hold", 0) or 0
    sell = (rec.get("strongSell", 0) or 0) + (rec.get("sell", 0) or 0)
    total = buy + hold + sell
    if total == 0:
        return None
    if buy >= total * 0.6:
        return "Buy"
    if sell >= total * 0.6:
        return "Sell"
    return "Hold"


def us_fundamentals_refresher():
    """미국 스크리닝 종목의 시가총액/PE/EPS성장률/배당수익률/애널리스트 레이팅/
    재무제표 절대금액을 Finnhub(서버 공용 기본 키)로 보강한다. 무료 API 호출
    한도(분당 60회) 안에서 종목당 3회(metric, recommendation, financials-reported)
    호출하며 천천히 도는 별도 백그라운드 작업이라 가격/조건 갱신(trend_screen_refresher)과
    주기·리듬을 분리했다.

    gunicorn --workers 2라 이 스레드가 워커마다 하나씩 독립적으로 돈다(의도된
    설계). 종목 하나씩 "SELECT ... FOR UPDATE SKIP LOCKED"로 원자적으로 선점한
    뒤 아주 짧은 트랜잭션으로 커밋하고 놓아준다 - 배치 전체를 한 세션에 20~30분
    물고 있으면 두 워커의 UPDATE 순서가 꼬여 데드락이 난다(실제로 한 번 겪었다:
    가격 재스캔 수동 스크립트가 app.py를 임포트하며 이 스레드까지 같이 띄워
    운영 서버의 진짜 스레드와 동시에 같은 행을 건드렸다). SKIP LOCKED 덕분에
    다른 워커가 이미 잠근 행은 건너뛰고 다음 종목으로 넘어가 중복 작업도 피한다."""
    import random
    from models import TrendScreenCache

    time.sleep(random.uniform(60, 180))
    while True:
        try:
            key = DEFAULT_FINNHUB_KEY
            if not key:
                time.sleep(1800)
                continue

            cutoff = datetime.utcnow() - timedelta(seconds=US_FUND_MIN_INTERVAL_SECONDS)
            processed = 0
            while True:
                # 종목 하나 처리 중 생기는 예외(잘못된 응답 형식 등)를 여기서 잡아야
                # 한다 - 이 안에서 처리 안 하면 바깥쪽 try/except까지 튀어서 남은
                # 수백 종목을 통째로 포기하고 30분을 쉬어버린다(실제로 겪은 버그:
                # 종목 하나가 실패하자 나머지 400여 종목이 전혀 처리되지 않았다).
                code = None
                try:
                    with app.app_context():
                        row = (
                            TrendScreenCache.query.filter_by(market="US")
                            .filter(db.or_(TrendScreenCache.fund_updated_at.is_(None), TrendScreenCache.fund_updated_at < cutoff))
                            .order_by(TrendScreenCache.fund_updated_at.is_(None).desc(), TrendScreenCache.fund_updated_at.asc())
                            .with_for_update(skip_locked=True)
                            .first()
                        )
                        if row is None:
                            break
                        row.fund_updated_at = datetime.utcnow()  # 먼저 선점 표시부터 남겨 다른 워커가 못 집어가게 함
                        db.session.commit()
                        code, row_id = row.code, row.id

                    metric_data, _ = fh_get("/stock/metric", key, {"symbol": code, "metric": "all"})
                    time.sleep(1.1)
                    rec_data, _ = fh_get("/stock/recommendation", key, {"symbol": code})
                    time.sleep(1.1)
                    financials_data, _ = fh_get("/stock/financials-reported", key, {"symbol": code, "freq": "annual"})
                    time.sleep(1.1)

                    with app.app_context():
                        row = TrendScreenCache.query.get(row_id)
                        if row is not None:
                            m = (metric_data or {}).get("metric") or {}
                            row.market_cap = m.get("marketCapitalization")
                            row.pe_ratio = m.get("peTTM")
                            row.eps_growth = m.get("epsGrowthTTMYoy")
                            row.dividend_yield = m.get("dividendYieldIndicatedAnnual")
                            latest_rec = rec_data[0] if isinstance(rec_data, list) and rec_data else {}
                            row.analyst_rating = _analyst_rating_label(latest_rec)
                            metrics = _extract_us_metrics(m)
                            metrics.update(_extract_us_financials(financials_data))
                            if metrics.get("totalLiabilities") is not None and metrics.get("cash") is not None and metrics.get("totalEquity"):
                                metrics["netDebtRatio"] = round(
                                    (metrics["totalLiabilities"] - metrics["cash"]) / metrics["totalEquity"] * 100, 1
                                )
                            row.metrics_json = json.dumps(metrics)
                            row.fund_updated_at = datetime.utcnow()
                            db.session.commit()
                    processed += 1
                except Exception:
                    app.logger.exception(f"미국 재무 보강 - 종목 처리 실패, 다음 종목으로 계속 진행: {code}")
                    # DB 오류로 예외가 났으면 세션이 "실패한 트랜잭션" 상태로 남아,
                    # 롤백하지 않으면 이후 커밋이 전부 같은 이유로 계속 실패해
                    # 사실상 멈춰버린다(실제로 이 문제로 441개에서 멈췄었다).
                    try:
                        db.session.rollback()
                    except Exception:
                        pass
                    time.sleep(1)
            if processed:
                print(f"[미국 재무 보강] {processed}개 종목 갱신 완료")
        except Exception:
            app.logger.exception("미국 스크리닝 재무 보강 오류")
            try:
                db.session.rollback()
            except Exception:
                pass
        time.sleep(1800)


# ─── 초기화 ──────────────────────────────────────────────────────────────────
# gunicorn 등 WSGI 서버로 구동해도(=__name__ != "__main__") DB 테이블 생성과 알림
# 체크 스레드가 항상 시작되도록 모듈 임포트 시점에 실행한다.

def _ensure_column(table_name, column_name, ddl_type):
    """마이그레이션 도구가 없어(RULES.md R18) db.create_all()이 이미 존재하는
    프로덕션 테이블에는 새 컬럼을 추가해주지 못하는 문제를 보완한다. gunicorn
    워커 2개가 기동 시점에 동시에 시도할 수 있어, 다른 워커가 먼저 추가해서
    나는 "컬럼 중복" 에러는 무시한다."""
    inspector = db.inspect(db.engine)
    if table_name not in inspector.get_table_names():
        return
    existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
    if column_name in existing_cols:
        return
    try:
        with db.engine.begin() as conn:
            conn.execute(db.text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_type}"))
    except Exception:
        pass


def _backfill_stage_column():
    """stage 컬럼을 새로 추가하면서 기존 캐시 행은 전부 NULL이 된다. 배경 리프레셔는
    12시간 주기(TREND_SCREEN_MIN_INTERVAL_SECONDS)라 그때까지 기다리면 배포 직후
    단계 배지가 한동안 전부 비어 보인다 - 이미 저장된 이평선/52주 데이터만으로
    순수 계산이라 외부 API 호출 없이 즉시 채울 수 있어 시작 시 한 번 수행한다."""
    from models import TrendScreenCache
    import trend_screener as ts

    rows = TrendScreenCache.query.filter(TrendScreenCache.stage.is_(None)).all()
    if not rows:
        return
    for row in rows:
        if None in (row.price, row.ma50, row.ma150, row.ma200, row.week52_high, row.week52_low):
            continue
        conditions = json.loads(row.conditions_json) if row.conditions_json else {}
        ma200_rising = conditions.get("ma200Rising", True)
        row.stage = ts.classify_stage(row.price, row.ma50, row.ma150, row.ma200, ma200_rising, row.week52_high, row.week52_low)
    db.session.commit()


with app.app_context():
    db.create_all()
    _ensure_column("trend_screen_cache", "stage", "INTEGER")
    _backfill_stage_column()

threading.Thread(target=alert_checker, daemon=True).start()
threading.Thread(target=kr_quant_price_cache_refresher, daemon=True).start()
threading.Thread(target=trend_screen_refresher, daemon=True).start()
threading.Thread(target=us_fundamentals_refresher, daemon=True).start()


# ─── 실행 (로컬 개발 서버) ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  JH-Trader")
    print("  http://localhost:3000 으로 접속하세요")
    print("=" * 50)
    app.run(debug=False, port=3000)
