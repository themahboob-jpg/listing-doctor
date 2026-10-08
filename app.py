"""Listing Doctor — Phase 1: eBay listing audit (Flask).

Run:  python3 app.py   ->  http://localhost:5000
Env:  EBAY_CLIENT_ID / EBAY_CLIENT_SECRET  (without them the app runs in mock/demo mode)
"""
import os
from flask import Flask, request, jsonify, render_template

from ebay_client import EbayClient, ListingNotFound, MOCK_ITEM, MOCK_SIMILAR_PRICES
from analyzer import analyze
from generator import generate_listing

app = Flask(__name__)
client = EbayClient()  # reads keys from env; mock mode if absent

# Bump on every deploy — lets us verify which build is live via /api/health.
APP_VERSION = "2026-10-08-c"

# Public base URL of the deployed app (for social share previews).
# Change if the subdomain/name differs.
SITE_URL = os.getenv("SITE_URL", "https://tool.mappackfix.com")


@app.route("/")
def index():
    return render_template("index.html", mock_mode=client.mock, site_url=SITE_URL)


@app.route("/api/audit", methods=["POST"])
def audit():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Paste an eBay item URL or item ID."}), 400
    try:
        item_id = EbayClient.parse_item_id(url)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    try:
        got = client.get_item(url)
        item = got["item"]
        # price positioning: search by first ~6 title words
        query = " ".join((item.get("title") or "").split()[:6])
        sim = client.similar_prices(query) if query else {"prices": []}
        report = analyze(item, sim["prices"])
        report["mode"] = got["mode"]
        report["comparables"] = sim["count"]
        if got.get("note"):
            report["note"] = got["note"]
        return jsonify(report)
    except ListingNotFound as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:  # noqa: BLE001 - surface API errors cleanly
        return jsonify({"error": f"eBay lookup failed: {e}"}), 502


@app.route("/api/demo", methods=["GET", "POST"])
def demo():
    """One-tap sample audit — always uses mock data, works in live mode too."""
    item = dict(MOCK_ITEM)
    item["title"] = f"{MOCK_ITEM['title']} (demo listing)"
    report = analyze(item, list(MOCK_SIMILAR_PRICES))
    report["mode"] = "demo"
    report["comparables"] = len(MOCK_SIMILAR_PRICES)
    return jsonify(report)


@app.route("/api/generate", methods=["POST"])
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
                    "version": APP_VERSION,
                    "shopping_debug": client.last_shopping_debug,
                    "browse_debug": client.last_browse_debug})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
