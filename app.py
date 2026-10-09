"""Listing Doctor — Phase 1: eBay listing audit (Flask).

Run:  python3 app.py   ->  http://localhost:5000
Env:  EBAY_CLIENT_ID / EBAY_CLIENT_SECRET  (without them the app runs in mock/demo mode)
"""
import os
import time

from flask import Flask, request, jsonify, render_template
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

from ebay_client import (EbayClient, ListingNotFound, MOCK_ITEM, MOCK_SIMILAR_PRICES,
                         MOCK_CATEGORY_ASPECTS)
from analyzer import analyze
from generator import generate_listing

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
limiter = Limiter(get_remote_address, app=app,
                  default_limits=["300 per day", "60 per hour"],
                  storage_uri="memory://")

_CACHE = {}
_CACHE_TTL = 600
_CACHE_MAX = 500


def _cache_get(key):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    _CACHE.pop(key, None)
    return None


def _cache_set(key, value):
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (time.time(), value)


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp
client = EbayClient()  # reads keys from env; mock mode if absent

# Bump on every deploy — lets us verify which build is live via /api/health.
APP_VERSION = "2026-10-08-e"

# Public base URL of the deployed app (for social share previews).
# Change if the subdomain/name differs.
SITE_URL = os.getenv("SITE_URL", "https://tool.mappackfix.com")


def _item_brief(item):
    """Small product-facts payload so 'Fix My Listing' can pre-fill the generator."""
    aspects = {}
    for a in item.get("localizedAspects") or []:
        n = (a.get("name") or "").strip().lower()
        if n and n not in aspects:
            aspects[n] = a.get("value") or ""

    def pick(*names):
        for n in names:
            if aspects.get(n):
                return aspects[n]
        return ""

    price = ""
    try:
        price = str((item.get("price") or {}).get("value") or "")
    except (TypeError, ValueError):
        pass
    return {
        "title": item.get("title") or "",
        "brand": pick("brand"),
        "model": pick("model", "mpn"),
        "color": pick("color", "colour"),
        "size": pick("storage", "capacity", "size"),
        "condition": item.get("condition") or "",
        "price": price,
    }


@app.route("/")
def index():
    return render_template("index.html", mock_mode=client.mock, site_url=SITE_URL)


@app.route("/api/audit", methods=["POST"])
@limiter.limit("10 per minute")
def audit():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Paste an eBay item URL or item ID."}), 400
    try:
        item_id = EbayClient.parse_item_id(url)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    cached = _cache_get(item_id)
    if cached is not None:
        return jsonify(cached)

    try:
        got = client.get_item(url)
        item = got["item"]
        # price positioning: search by first ~6 title words
        query = " ".join((item.get("title") or "").split()[:6])
        sim = (client.similar_prices(query, condition=item.get("condition"))
               if query else {"prices": [], "count": 0})
        aspects = client.category_aspects(item.get("categoryId"))
        report = analyze(item, sim["prices"], aspects)
        report["mode"] = got["mode"]
        report["comparables"] = sim["count"]
        report["item_brief"] = _item_brief(item)
        if got.get("note"):
            report["note"] = got["note"]
        _cache_set(item_id, report)
        return jsonify(report)
    except ListingNotFound as e:
        return jsonify({"error": str(e)}), 404
    except Exception:  # noqa: BLE001
        app.logger.exception("audit failed")
        return jsonify({"error": "eBay lookup failed. Please try again in a minute."}), 502


@app.route("/api/demo", methods=["GET", "POST"])
def demo():
    """One-tap sample audit — always uses mock data, works in live mode too."""
    item = dict(MOCK_ITEM)
    item["title"] = f"{MOCK_ITEM['title']} (demo listing)"
    report = analyze(item, list(MOCK_SIMILAR_PRICES), dict(MOCK_CATEGORY_ASPECTS))
    report["mode"] = "demo"
    report["comparables"] = len(MOCK_SIMILAR_PRICES)
    report["item_brief"] = _item_brief(item)
    return jsonify(report)


@app.route("/api/generate", methods=["POST"])
@limiter.limit("20 per minute")
def generate():
    """Build an optimized title, specifics and description from product details."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(generate_listing(data))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "mode": "mock" if client.mock else "live",
                    "version": APP_VERSION})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
