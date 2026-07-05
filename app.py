import json
import os
import threading
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

CONFIG_FILE = DATA_DIR / "config.json"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"
ALERTS_FILE = DATA_DIR / "alerts.json"

FH_BASE = "https://finnhub.io/api/v1"


# ─── 데이터 저장/불러오기 ────────────────────────────────────────────────────

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_api_key():
    cfg = load_json(CONFIG_FILE, {})
    return cfg.get("api_key", "")


# ─── Finnhub API 호출 ────────────────────────────────────────────────────────

def fh_get(path, api_key=None):
    key = api_key or get_api_key()
    if not key:
        return None, "API 키가 설정되지 않았습니다"
    try:
        res = requests.get(
            f"{FH_BASE}{path}&token={key}",
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


# ─── 페이지 ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    has_key = bool(get_api_key())
    return render_template("index.html", has_key=has_key)


# ─── 설정 API ────────────────────────────────────────────────────────────────

@app.route("/api/settings/key", methods=["POST"])
def save_key():
    key = request.json.get("key", "").strip()
    if not key:
        return jsonify({"error": "키를 입력하세요"}), 400

    # 키 유효성 확인
    data, err = fh_get("/quote?symbol=AAPL", api_key=key)
    if err:
        return jsonify({"error": err}), 400

    save_json(CONFIG_FILE, {"api_key": key})
    return jsonify({"ok": True})


@app.route("/api/settings/key", methods=["DELETE"])
def delete_key():
    save_json(CONFIG_FILE, {})
    return jsonify({"ok": True})


# ─── 종목 데이터 API ─────────────────────────────────────────────────────────

@app.route("/api/stock/<ticker>")
def get_stock(ticker):
    ticker = ticker.upper()
    key = get_api_key()
    if not key:
        return jsonify({"error": "API 키가 설정되지 않았습니다"}), 401

    import concurrent.futures
    endpoints = {
        "quote": f"/quote?symbol={ticker}",
        "target": f"/stock/price-target?symbol={ticker}",
        "rec": f"/stock/recommendation?symbol={ticker}",
        "profile": f"/stock/profile2?symbol={ticker}",
    }

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(fh_get, path, key): name for name, path in endpoints.items()}
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
def get_quote(ticker):
    ticker = ticker.upper()
    data, err = fh_get(f"/quote?symbol={ticker}")
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"ticker": ticker, "price": data.get("c", 0), "changePct": data.get("dp", 0)})


# ─── 포트폴리오 API ──────────────────────────────────────────────────────────

@app.route("/api/portfolio", methods=["GET"])
def get_portfolio():
    return jsonify(load_json(PORTFOLIO_FILE, []))


@app.route("/api/portfolio", methods=["POST"])
def add_portfolio():
    body = request.json
    ticker = body.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "티커를 입력하세요"}), 400

    portfolio = load_json(PORTFOLIO_FILE, [])
    if any(p["ticker"] == ticker for p in portfolio):
        return jsonify({"error": f"{ticker}은(는) 이미 포트폴리오에 있습니다"}), 409

    portfolio.append({
        "ticker": ticker,
        "qty": float(body.get("qty", 0)),
        "buyPrice": float(body.get("buyPrice", 0)),
        "name": ticker,
        "currentPrice": None,
        "targetPrice": None,
        "changePct": 0,
    })
    save_json(PORTFOLIO_FILE, portfolio)
    return jsonify({"ok": True})


@app.route("/api/portfolio/<ticker>", methods=["DELETE"])
def remove_portfolio(ticker):
    portfolio = load_json(PORTFOLIO_FILE, [])
    portfolio = [p for p in portfolio if p["ticker"] != ticker.upper()]
    save_json(PORTFOLIO_FILE, portfolio)
    return jsonify({"ok": True})


@app.route("/api/portfolio/refresh", methods=["POST"])
def refresh_portfolio():
    portfolio = load_json(PORTFOLIO_FILE, [])
    if not portfolio:
        return jsonify([])

    import concurrent.futures
    key = get_api_key()
    if not key:
        return jsonify({"error": "API 키가 설정되지 않았습니다"}), 401

    def update_item(item):
        q, _ = fh_get(f"/quote?symbol={item['ticker']}", key)
        t, _ = fh_get(f"/stock/price-target?symbol={item['ticker']}", key)
        p, _ = fh_get(f"/stock/profile2?symbol={item['ticker']}", key)
        if q and q.get("c"):
            item["currentPrice"] = q["c"]
            item["changePct"] = q.get("dp", 0)
        if t and t.get("targetMean"):
            item["targetPrice"] = t["targetMean"]
        if p and p.get("name"):
            item["name"] = p["name"]
        return item

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        portfolio = list(ex.map(update_item, portfolio))

    save_json(PORTFOLIO_FILE, portfolio)
    return jsonify(portfolio)


# ─── 알림 API ────────────────────────────────────────────────────────────────

@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    return jsonify(load_json(ALERTS_FILE, []))


@app.route("/api/alerts", methods=["POST"])
def add_alert():
    body = request.json
    ticker = body.get("ticker", "").strip().upper()
    price = body.get("price")
    kind = body.get("type", "above")

    if not ticker or price is None:
        return jsonify({"error": "티커와 가격을 입력하세요"}), 400

    alerts = load_json(ALERTS_FILE, [])
    alerts.append({
        "ticker": ticker,
        "price": float(price),
        "type": kind,
        "triggered": False,
        "triggeredAt": None,
        "created": time.strftime("%Y-%m-%d"),
    })
    save_json(ALERTS_FILE, alerts)
    return jsonify({"ok": True})


@app.route("/api/alerts/<int:idx>", methods=["DELETE"])
def remove_alert(idx):
    alerts = load_json(ALERTS_FILE, [])
    if 0 <= idx < len(alerts):
        alerts.pop(idx)
        save_json(ALERTS_FILE, alerts)
    return jsonify({"ok": True})


# ─── 백그라운드 알림 체크 ────────────────────────────────────────────────────

def alert_checker():
    while True:
        time.sleep(30)
        try:
            alerts = load_json(ALERTS_FILE, [])
            changed = False
            for i, a in enumerate(alerts):
                if a.get("triggered"):
                    continue
                data, _ = fh_get(f"/quote?symbol={a['ticker']}")
                if not data:
                    continue
                cur = data.get("c", 0)
                hit = (a["type"] == "above" and cur >= a["price"]) or \
                      (a["type"] == "below" and cur <= a["price"])
                if hit:
                    alerts[i]["triggered"] = True
                    alerts[i]["triggeredAt"] = time.strftime("%H:%M:%S")
                    changed = True
                    print(f"[알림] {a['ticker']} ${cur:.2f} — 목표 ${a['price']:.2f} 달성!")
            if changed:
                save_json(ALERTS_FILE, alerts)
        except Exception as e:
            print(f"[알림 체크 오류] {e}")


threading.Thread(target=alert_checker, daemon=True).start()


# ─── 실행 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  미국 주식 목표가 트래커")
    print("  http://localhost:5000 으로 접속하세요")
    print("=" * 50)
    app.run(debug=False, port=5000)
