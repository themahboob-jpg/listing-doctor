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

# Fallback for listings the Browse API can't resolve (it 400s on some active
# listings). The Shopping API takes the numeric ItemID directly and needs only
# the app key — no OAuth.
SHOPPING_URL = "https://open.api.ebay.com/shopping"

NOT_FOUND_MSG = ("Listing not found on eBay. Double-check the item ID — "
                 "it may be a typo, or the listing may have ended or been removed.")

# ---------------------------------------------------------------- mock data
MOCK_ITEM = {
    "itemId": "v1|394118902100|0",
    "legacyItemId": "394118902100",
    "title": "Apple iPhone 13 128GB Midnight Unlocked Excellent Condition",
    "price": {"value": "429.99", "currency": "USD"},
    "condition": "Used",
    "conditionId": "3000",
    "categoryPath": "Cell Phones & Accessories|Cell Phones & Smartphones",
    "image": {"imageUrl": "https://i.ebayimg.com/images/g/aaa1.jpg"},
    "additionalImages": [
        {"imageUrl": "https://i.ebayimg.com/images/g/aaa2.jpg"},
        {"imageUrl": "https://i.ebayimg.com/images/g/aaa3.jpg"},
    ],
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
    "returnTerms": {"returnsAccepted": True, "returnPeriod": {"value": "30", "unit": "DAY"}},
    "itemLocation": {"city": "Austin", "country": "US"},
    "itemWebUrl": "https://www.ebay.com/itm/394118902100",
    "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK", "estimatedAvailableQuantity": 3}],
}

# competitor prices for the mock item (used for price positioning)
MOCK_SIMILAR_PRICES = [419.0, 435.5, 449.0, 409.99, 459.0, 429.0, 442.5, 415.0,
                        438.0, 425.0, 451.0, 418.5, 433.0, 447.0, 422.0]


def _translate_shopping_item(it):
    """Shopping API GetSingleItem -> Browse-like item dict for the analyzer."""
    def txt(v):
        return str(v).strip() if v is not None else ""

    pics = it.get("PictureURL") or []
    if isinstance(pics, str):
        pics = [pics]
    pics = [p for p in pics if txt(p)]

    specifics = []
    nvl = ((it.get("ItemSpecifics") or {}).get("NameValueList")) or []
    for nv in nvl:
        vals = (nv or {}).get("Value") or []
        if isinstance(vals, str):
            vals = [vals]
        name = txt(nv.get("Name"))
        value = ", ".join(txt(v) for v in vals if txt(v))
        if name:
            specifics.append({"name": name, "value": value})

    seller = it.get("Seller") or {}
    try:
        fb_score = int(float(txt(seller.get("FeedbackScore")) or 0))
    except (TypeError, ValueError):
        fb_score = 0

    ships = []
    for so in ((it.get("ShippingDetails") or {}).get("ShippingServiceOptions")) or []:
        so = so or {}
        cost = (so.get("ShippingServiceCost") or {})
        cval = cost.get("Value", cost.get("_value"))
        ccur = txt(cost.get("CurrencyID", cost.get("_currencyId")) or "USD")
        free = txt(so.get("FreeShipping")).lower() == "true"
        try:
            cval = "0.00" if free else "%.2f" % float(txt(cval))
        except (TypeError, ValueError):
            cval = "0.00" if free else None
        if cval is None:
            continue
        ships.append({"shippingCostType": "FLAT_RATE",
                      "shippingCost": {"value": cval, "currency": ccur}})

    rp = it.get("ReturnPolicy") or {}
    ra = txt(rp.get("ReturnsAccepted")).lower().replace(" ", "")
    accepted = bool(ra) and "notaccepted" not in ra
    pval, punit = None, ""
    m = re.match(r"days?_(\d+)", txt(rp.get("ReturnsWithin")), re.I)
    if m:
        pval, punit = m.group(1), "DAY"
    else:
        m = re.match(r"months?_(\d+)", txt(rp.get("ReturnsWithin")), re.I)
        if m:
            pval, punit = str(int(m.group(1)) * 30), "DAY"

    price = it.get("CurrentPrice") or {}
    pcur = txt(price.get("CurrencyID", price.get("_currencyId")) or "USD")

    return {
        "title": txt(it.get("Title")),
        "price": {"value": txt(price.get("Value", price.get("_value"))),
                  "currency": pcur},
        "condition": txt(it.get("ConditionDisplayName")),
        "image": {"imageUrl": pics[0] if pics else ""},
        "additionalImages": [{"imageUrl": u} for u in pics[1:]],
        "description": it.get("Description") or "",
        "localizedAspects": specifics,
        "seller": {"username": txt(seller.get("UserID")),
                   "feedbackScore": fb_score,
                   "feedbackPercentage": txt(seller.get("PositiveFeedbackPercent"))},
        "shippingOptions": ships,
        "returnTerms": {"returnsAccepted": accepted,
                        "returnPeriod": {"value": pval or "", "unit": punit}},
        "itemLocation": {"city": txt(it.get("Location")),
                         "country": txt(it.get("Country"))},
        "itemWebUrl": txt(it.get("ViewItemURLForNaturalSearch")),
    }


class ListingNotFound(Exception):
    """eBay has no such listing (bad ID, or ended/removed)."""


class EbayClient:
    def __init__(self, client_id=None, client_secret=None):
        self.client_id = client_id or os.getenv("EBAY_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("EBAY_CLIENT_SECRET")
        self.mock = not (self.client_id and self.client_secret)
        self._token = None
        self._token_exp = 0
        # last Shopping-fallback failure detail (for /api/health debugging)
        self.last_shopping_debug = None
        # last Browse API error detail (for /api/health debugging)
        self.last_browse_debug = None

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
    def _browse(self, path, params=None):
        """Browse API GET with 400/404 mapped to ListingNotFound."""
        try:
            return self._get(path, params)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            body = ""
            try:
                body = e.response.text[:300] if e.response is not None else ""
            except Exception:
                pass
            self.last_browse_debug = "http_%s %s %s" % (status, path, body)
            if status in (400, 404):
                raise ListingNotFound(NOT_FOUND_MSG) from e
            raise

    def get_restful_id(self, legacy_id):
        """legacy numeric id -> RESTful item id (v1|..|..)."""
        if self.mock:
            return MOCK_ITEM["itemId"]
        data = self._browse("/item/get_item_by_legacy_id",
                            {"legacy_item_id": legacy_id})
        return data["itemId"]

    def get_item(self, url_or_id):
        """Full item detail for an eBay URL / legacy id / RESTful id.

        Primary: Browse API. Fallback: Shopping API (GetSingleItem), which
        resolves some active listings the Browse API rejects.
        """
        if self.mock:
            item = dict(MOCK_ITEM)
            item["title"] = f"{MOCK_ITEM['title']} (demo data)"
            return {"item": item, "mode": "mock"}
        s = (url_or_id or "").strip()
        try:
            if s.startswith("v1|"):
                rest_id = s
            else:
                rest_id = self.get_restful_id(self.parse_item_id(s))
            # URL-encode the pipes in the RESTful id
            rest_id_enc = rest_id.replace("|", "%7C")
            try:
                item = self._browse(f"/item/{rest_id_enc}")
            except requests.HTTPError:
                # fallback: some ids work directly
                item = self._browse(f"/item/{self.parse_item_id(s)}")
            return {"item": item, "mode": "live"}
        except ListingNotFound:
            pass  # try the Shopping API below
        # ---- Shopping API fallback --------------------------------------
        if s.startswith("v1|"):
            legacy_id = s.split("|")[1]
        else:
            legacy_id = self.parse_item_id(s)
        item = self._shopping_item(legacy_id)
        return {"item": item, "mode": "live"}

    # -------------------------------------------------- shopping fallback
    def _shopping_item(self, legacy_id):
        """GetSingleItem via the Shopping API; translated to Browse-like shape."""
        params = {
            "callname": "GetSingleItem",
            "responseencoding": "JSON",
            "appid": self.client_id,
            "siteid": "0",
            "version": "967",
            "ItemID": legacy_id,
            "IncludeSelector": "Description,Details,ItemSpecifics,ShippingCosts",
        }

        def fail(reason):
            self.last_shopping_debug = reason[:300]
            raise ListingNotFound(NOT_FOUND_MSG)

        try:
            r = requests.get(SHOPPING_URL, params=params, timeout=20)
        except requests.RequestException as e:
            fail("connection_error: %s" % type(e).__name__)
        if r.status_code != 200:
            fail("http_%d: %s" % (r.status_code, r.text[:120]))
        try:
            data = r.json()
        except ValueError:
            fail("bad_json: %s" % r.text[:120])
        if data.get("Ack") not in ("Success", "Warning") or not data.get("Item"):
            errs = data.get("Errors") or [{}]
            e0 = errs[0] if isinstance(errs, list) else errs
            fail("ack_%s code=%s msg=%s" % (
                data.get("Ack"),
                e0.get("ErrorCode"),
                str(e0.get("LongMessage") or e0.get("Message"))[:160]))
        self.last_shopping_debug = "ok"
        return _translate_shopping_item(data["Item"])

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
