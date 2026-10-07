"""eBay Browse API client with mock fallback.

Real mode: OAuth2 client-credentials -> Browse API (getItemByLegacyId, getItem, search).
Mock mode (no keys in env): realistic sample data so the app + UI work end-to-end.
Docs: https://developer.ebay.com/api-docs/buy/browse/overview.html
"""
import base64
import os
import re
import time
import requests

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_BASE = "https://api.ebay.com/buy/browse/v1"
OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
# ---------------------------------------------------------------- mock data
MOCK_ITEM = {
    "itemId": "v1|394118902100|0",
    "legacyItemId": "394118902100",
    "title": "Apple iPhone 13 128GB Midnight Unlocked Excellent Condition",
    "price": {"value": "429.99", "currency": "USD"},
    "condition": "Used",
    "conditionId": "3000",
    "categoryPath": "Cell Phones & Accessories|Cell Phones & Smartphones",
    "image": {"imageUrl": [
        "https://i.ebayimg.com/images/g/aaa1.jpg",
        "https://i.ebayimg.com/images/g/aaa2.jpg",
        "https://i.ebayimg.com/images/g/aaa3.jpg",
    ]},
    "shortDescription": "Apple iPhone 13, 128GB, Midnight. Fully unlocked, battery health 89%.",
    "description": "<p>Apple iPhone 13 128GB in Midnight. Phone is in excellent condition with "
                   "only light micro-scratches. Battery health 89%. Fully unlocked for all carriers. "
                   "Includes original box. Ships within 1 business day with tracking.</p>",
    "localizedAspects": [
        {"name": "Brand", "value": "Apple"},
        {"name": "Model", "value": "iPhone 13"},
        {"name": "Storage Capacity", "value": "128 GB"},
        {"name": "Color", "value": "Midnight"},
        {"name": "Network", "value": "Unlocked"},
    ],
    "seller": {"username": "techdeals_usa", "feedbackScore": 12480, "feedbackPercentage": "99.2"},
    "shippingOptions": [
        {"shippingCostType": "FLAT_RATE", "shippingCost": {"value": "0.00", "currency": "USD"},
         "maxEstimatedDeliveryDate": "2026-10-10", "minEstimatedDeliveryDate": "2026-10-08"}
    ],
    "itemWebUrl": "https://www.ebay.com/itm/394118902100",
    "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK", "estimatedAvailableQuantity": 3}],
}

# competitor prices for the mock item (used for price positioning)
MOCK_SIMILAR_PRICES = [419.0, 435.5, 449.0, 409.99, 459.0, 429.0, 442.5, 415.0,
                        438.0, 425.0, 451.0, 418.5, 433.0, 447.0, 422.0]


class EbayClient:
    def __init__(self, client_id=None, client_secret=None):
        self.client_id = client_id or os.getenv("EBAY_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("EBAY_CLIENT_SECRET")
        self.mock = not (self.client_id and self.client_secret)
        self._token = None
        self._token_exp = 0

    # ------------------------------------------------------------- helpers
    @staticmethod
    def parse_item_id(url_or_id):
        """Extract numeric legacy item id from an eBay URL or bare id."""
        s = (url_or_id or "").strip()
        m = re.search(r"/itm/(?:[^/]+/)?(\d{9,})", s)
        if m:
            return m.group(1)
        m = re.search(r"(\d{9,})", s)
        if m:
            return m.group(1)
        raise ValueError("Could not find an eBay item id in the input.")

    def _app_token(self):
        if time.time() < self._token_exp - 60:
            return self._token
        basic = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()).decode()
        r = requests.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": OAUTH_SCOPE},
            timeout=20)
        if r.status_code != 200:
            try:
                detail = r.json()
                reason = detail.get("error_description") or detail.get("error") or r.text[:200]
            except Exception:
                reason = r.text[:200]
            raise RuntimeError(f"eBay token rejected (HTTP {r.status_code}): {reason}")
        data = r.json()
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 7200))
        return self._token

    def _get(self, path, params=None):
        r = requests.get(
            BROWSE_BASE + path,
            headers={"Authorization": f"Bearer {self._app_token()}",
                     "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                     "Content-Type": "application/json"},
            params=params or {}, timeout=20)
        r.raise_for_status()
        return r.json()

    # ----------------------------------------------------------------- api
    def get_restful_id(self, legacy_id):
        """legacy numeric id -> RESTful item id (v1|..|..)."""
        if self.mock:
            return MOCK_ITEM["itemId"]
        data = self._get("/item/get_item_by_legacy_id",
                         {"legacy_item_id": legacy_id})
        return data["itemId"]

    def get_item(self, url_or_id):
        """Full item detail for an eBay URL / legacy id / RESTful id."""
        if self.mock:
            item = dict(MOCK_ITEM)
            item["title"] = f"{MOCK_ITEM['title']} (demo data)"
            return {"item": item, "mode": "mock"}
        s = (url_or_id or "").strip()
        if s.startswith("v1|"):
            rest_id = s
        else:
            rest_id = self.get_restful_id(self.parse_item_id(s))
        # URL-encode the pipes in the RESTful id
        rest_id_enc = rest_id.replace("|", "%7C")
        try:
            item = self._get(f"/item/{rest_id_enc}")
        except requests.HTTPError:
            # fallback: some ids work directly
            item = self._get(f"/item/{self.parse_item_id(s)}")
        return {"item": item, "mode": "live"}

    def similar_prices(self, query, limit=50):
        """Competitor prices for price positioning (search API)."""
        if self.mock:
            return {"prices": list(MOCK_SIMILAR_PRICES), "mode": "mock",
                    "count": len(MOCK_SIMILAR_PRICES)}
        data = self._get("/item_summary/search",
                         {"q": query, "limit": min(limit, 200),
                          "filter": "buyingOptions:{FIXED_PRICE}"})
        prices = []
        for it in data.get("itemSummaries", []):
            p = (it.get("price") or {}).get("value")
            try:
                prices.append(float(p))
            except (TypeError, ValueError):
                pass
        return {"prices": prices, "mode": "live", "count": len(prices)}
