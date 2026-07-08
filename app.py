import os
import re
import secrets
import time
import threading
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

from models import Alert, PortfolioItem, User, db

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)

app = Flask(__name__)


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
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", f"sqlite:///{DATA_DIR / 'app.db'}"
)
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
    save_key, delete_key, get_stock, get_quote,
    get_portfolio, add_portfolio, remove_portfolio, refresh_portfolio,
    get_alerts, add_alert, remove_alert,
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


# ─── 실행 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    threading.Thread(target=alert_checker, daemon=True).start()

    print("=" * 50)
    print("  미국 주식 목표가 트래커")
    print("  http://localhost:5000 으로 접속하세요")
    print("=" * 50)
    app.run(debug=False, port=5000)
