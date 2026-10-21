"""Listing Doctor — Phase 1: eBay listing audit (Flask).

Run:  python3 app.py   ->  http://localhost:5000
Env:  EBAY_CLIENT_ID / EBAY_CLIENT_SECRET  (without them the app runs in mock/demo mode)
"""
import os
import time

from flask import Flask, request, jsonify, render_template, make_response, redirect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from urllib.parse import urlencode

from ebay_client import (EbayClient, ListingNotFound, MOCK_ITEM, MOCK_SIMILAR_PRICES,
                         MOCK_CATEGORY_ASPECTS)
from analyzer import analyze
from generator import generate_listing
from share_card import make_score_card

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
APP_VERSION = "2026-10-21-a"

# Public base URL of the deployed app (for social share previews).
# Change if the subdomain/name differs.
SITE_URL = os.getenv("SITE_URL", "https://tool.mappackfix.com")


def _item_brief(item):
    """Verified product facts used to prefill the listing generator."""
    aspects = []
    by_name = {}
    for a in item.get("localizedAspects") or []:
        name = (a.get("name") or "").strip()
        value = (a.get("value") or "").strip()
        if name and value:
            aspects.append({"name": name, "value": value})
            by_name.setdefault(name.lower(), value)

    def pick(*names):
        for name in names:
            if by_name.get(name):
                return by_name[name]
        return ""

    price = ""
    price_currency = ""
    try:
        price_data = item.get("price") or {}
        price = str(price_data.get("value") or "")
        price_currency = str(price_data.get("currency") or "")
    except (TypeError, ValueError):
        pass

    shipping_type, shipping_cost, shipping_currency = "", "", ""
    shipping = (item.get("shippingOptions") or [])
    if shipping:
        option = shipping[0] or {}
        cost = (option.get("shippingCost") or {})
        shipping_currency = str(cost.get("currency") or "")
        try:
            amount = float(cost.get("value"))
            shipping_cost = str(cost.get("value") or "")
            shipping_type = "free" if amount == 0 else "flat"
        except (TypeError, ValueError):
            cost_type = (option.get("shippingCostType") or "").lower()
            if "calculated" in cost_type:
                shipping_type = "calculated"
            elif "flat" in cost_type:
                shipping_type = "flat"

    returns_option = ""
    return_terms = item.get("returnTerms") or {}
    if return_terms.get("returnsAccepted") is True:
        period = return_terms.get("returnPeriod") or {}
        try:
            days = int(period.get("value"))
            if "DAY" in str(period.get("unit") or "").upper():
                returns_option = str(days) if days in (14, 30) else ""
        except (TypeError, ValueError):
            pass
    elif return_terms.get("returnsAccepted") is False:
        returns_option = "none"

    location = item.get("itemLocation") or {}
    ships_from = ", ".join(str(v).strip() for v in
                           (location.get("city"), location.get("country")) if v)

    return {
        "title": item.get("title") or "",
        "brand": pick("brand"),
        "model": pick("model", "mpn"),
        "color": pick("color", "colour"),
        "size": pick("storage capacity", "storage", "capacity", "size"),
        "condition": item.get("condition") or "",
        "price": price,
        "price_currency": price_currency,
        "specifics": aspects,
        "category": item.get("categoryPath") or "",
        "shipping_type": shipping_type,
        "shipping_cost": shipping_cost,
        "shipping_currency": shipping_currency,
        "handling_time": str(item.get("handlingTimeDays")) if item.get("handlingTimeDays") is not None else "",
        "returns_option": returns_option,
        "ships_from": ships_from,
    }

@app.route("/")
def index():
    return render_template("index.html", mock_mode=client.mock, site_url=SITE_URL)


@app.route("/about")
def about():
    return render_template("about.html", site_url=SITE_URL)


@app.route("/privacy")
def privacy():
    return render_template("privacy.html", site_url=SITE_URL)


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
        # Optional enrichment must never make a valid listing audit fail.
        # Comparable prices are active asking prices, not sold-item values.
        notes = [got["note"]] if got.get("note") else []
        query = " ".join((item.get("title") or "").split()[:6])
        try:
            item_currency = (item.get("price") or {}).get("currency")
            sim = (client.similar_prices(query, condition=item.get("condition"),
                                         currency=item_currency)
                   if query else {"prices": [], "count": 0})
        except Exception:  # noqa: BLE001
            app.logger.warning("price comparison unavailable for item %s", item_id,
                               exc_info=True)
            sim = {"prices": [], "count": 0}
            notes.append("Comparable active asking prices could not be retrieved; price positioning was not assessed.")
        try:
            aspects = client.category_aspects(item.get("categoryId"))
        except Exception:  # noqa: BLE001
            app.logger.warning("category aspects unavailable for item %s", item_id,
                               exc_info=True)
            aspects = {}
            notes.append("Category-specific item requirements were unavailable; item-specific checks may be less complete.")
        report = analyze(item, sim["prices"], aspects)
        report["mode"] = got["mode"]
        report["comparables"] = sim["count"]
        report["item_brief"] = _item_brief(item)
        if notes:
            report["note"] = " ".join(notes)
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


@app.route("/contact")
def contact():
    return render_template("contact.html", site_url=SITE_URL)


@app.route("/share")
def share():
    """Shareable score page — OG tags point at a generated score card image,
    so the link unfurls with the score on WhatsApp/Facebook/X."""
    try:
        score = int(request.args.get("s", ""))
    except (TypeError, ValueError):
        return redirect("/", 302)
    if not 0 <= score <= 100:
        return redirect("/", 302)
    title = (request.args.get("t") or "").strip()[:70]
    q = urlencode({"s": score, "t": title})
    return render_template("share.html", score=score, title=title,
                           img_url=f"{SITE_URL}/share-img?{q}",
                           page_url=f"{SITE_URL}/share?{q}", site_url=SITE_URL)


@app.route("/share-img")
@limiter.limit("30 per minute")
def share_img():
    """Render the 1200x630 score card PNG on the fly (nothing stored)."""
    try:
        score = int(request.args.get("s", ""))
    except (TypeError, ValueError):
        return jsonify({"error": "bad score"}), 400
    if not 0 <= score <= 100:
        return jsonify({"error": "bad score"}), 400
    title = (request.args.get("t") or "").strip()[:70]
    resp = make_response(make_score_card(score, title))
    resp.headers["Content-Type"] = "image/png"
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "mode": "mock" if client.mock else "live",
                    "version": APP_VERSION})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
