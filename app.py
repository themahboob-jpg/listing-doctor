"""Listing Doctor — Phase 1: eBay listing audit (Flask).

Run:  python3 app.py   ->  http://localhost:5000
Env:  EBAY_CLIENT_ID / EBAY_CLIENT_SECRET  (without them the app runs in mock/demo mode)
"""
import hashlib
import hmac
import os
import time

from flask import Flask, request, jsonify, render_template, make_response, redirect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from urllib.parse import urlencode
from xml.sax.saxutils import escape

from ebay_client import (EbayClient, ListingNotFound, MOCK_ITEM, MOCK_SIMILAR_PRICES,
                         MOCK_CATEGORY_ASPECTS)
from analyzer import analyze
from generator import generate_listing
from share_card import make_score_card

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 7 * 24 * 3600  # static URLs carry ?v=
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)


def _client_ip():
    """Visitor IP behind Cloudflare -> Render. The rightmost X-Forwarded-For hop
    is a Cloudflare edge shared by many visitors, so prefer CF-Connecting-IP."""
    return request.headers.get("CF-Connecting-IP") or get_remote_address()


limiter = Limiter(_client_ip, app=app,
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
    resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    resp.headers.setdefault("Content-Security-Policy", CSP)
    return resp


CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://www.googletagmanager.com",
    "connect-src 'self' https://*.google-analytics.com https://*.analytics.google.com https://www.googletagmanager.com",
    "img-src 'self' data: https://*.google-analytics.com https://www.googletagmanager.com",
    "style-src 'self' 'unsafe-inline'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])
client = EbayClient()  # reads keys from env; mock mode if absent

# Bump on every deploy — lets us verify which build is live via /api/health.
APP_VERSION = "2026-10-31-a"

# Public base URL of the deployed app (for social share previews).
# Change if the subdomain/name differs.
SITE_URL = os.getenv("SITE_URL", "https://tool.mappackfix.com")

# Share links are signed so a score card can't be forged with any number/text.
SHARE_SECRET = (os.getenv("SHARE_SECRET") or os.getenv("EBAY_CLIENT_SECRET")
                or "listing-doctor-dev").encode()


def _share_sig(score, title):
    msg = f"{score}|{title}".encode()
    return hmac.new(SHARE_SECRET, msg, hashlib.sha256).hexdigest()[:20]


def _share_query(score, title):
    title = (title or "").strip()[:70]
    return urlencode({"s": score, "t": title, "k": _share_sig(score, title)})


def _signed_share_args():
    """(score, title) from a share URL, or None if missing/forged."""
    try:
        score = int(request.args.get("s", ""))
    except (TypeError, ValueError):
        return None
    title = (request.args.get("t") or "").strip()[:70]
    sig = request.args.get("k") or ""
    if not 0 <= score <= 100 or not hmac.compare_digest(sig, _share_sig(score, title)):
        return None
    return score, title


@app.context_processor
def _template_globals():
    return {"asset_v": APP_VERSION}


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
        cost_type = (option.get("shippingCostType") or "").lower()
        if "calculated" in cost_type:
            shipping_type = "calculated"
        else:
            try:
                amount = float(cost.get("value"))
                shipping_cost = str(cost.get("value") or "")
                shipping_type = "free" if amount == 0 else "flat"
            except (TypeError, ValueError):
                if "flat" in cost_type:
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
        "brand": pick("brand", "marke", "marque", "marca"),
        "model": pick("model", "modell", "modèle", "modello", "modelo", "mpn", "herstellernummer"),
        "color": pick("color", "colour", "farbe", "couleur", "colore"),
        "size": pick("storage capacity", "speicherkapazität", "storage", "capacity", "size"),
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


@app.route("/sitemap.xml")
def sitemap():
    base_url = request.url_root.rstrip("/")
    paths = ("", "about", "privacy", "contact")
    entries = "".join(
        f"  <url><loc>{escape(base_url + '/' + path)}</loc></url>\n"
        for path in paths
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}"
        "</urlset>\n"
    )
    response = make_response(xml)
    response.headers["Content-Type"] = "application/xml; charset=utf-8"
    return response


@app.route("/robots.txt")
def robots():
    body = ("User-agent: *\n"
            "Disallow: /api/\n"
            "Disallow: /share\n"
            "\n"
            "Sitemap: %s/sitemap.xml\n" % SITE_URL)
    return body, 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/favicon.ico")
def favicon():
    return app.send_static_file("favicon.png")


@app.route("/google50ad9445f7f230d7.html")
def google_site_verification():
    return "google-site-verification: google50ad9445f7f230d7.html", 200, {"Content-Type": "text/html; charset=utf-8"}


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
            aspects = client.category_aspects(item.get("categoryId"),
                                             item.get("listingMarketplaceId"))
        except Exception:  # noqa: BLE001
            app.logger.warning("category aspects unavailable for item %s", item_id,
                               exc_info=True)
            aspects = {}
            notes.append("Category-specific item requirements were unavailable; item-specific checks may be less complete.")
        report = analyze(item, sim["prices"], aspects)
        report["mode"] = got["mode"]
        report["comparables"] = sim["count"]
        report["item_brief"] = _item_brief(item)
        report["share_url"] = f"{SITE_URL}/share?{_share_query(report['score'], report['title'])}"
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
    report["share_url"] = f"{SITE_URL}/share?{_share_query(report['score'], report['title'])}"
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
    args = _signed_share_args()
    if args is None:
        return redirect("/", 302)
    score, title = args
    q = _share_query(score, title)
    return render_template("share.html", score=score, title=title,
                           img_url=f"{SITE_URL}/share-img?{q}",
                           page_url=f"{SITE_URL}/share?{q}", site_url=SITE_URL)


@app.route("/share-img")
@limiter.limit("30 per minute")
def share_img():
    """Render the 1200x630 score card PNG on the fly (nothing stored)."""
    args = _signed_share_args()
    if args is None:
        return jsonify({"error": "bad share link"}), 400
    score, title = args
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
