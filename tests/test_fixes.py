import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer import analyze
from generator import generate_listing
from ebay_client import MOCK_ITEM, MOCK_SIMILAR_PRICES, MOCK_CATEGORY_ASPECTS
import app as app_module


def check(report, cid):
    return next(c for c in report["checks"] if c["id"] == cid)


def item(**over):
    it = copy.deepcopy(MOCK_ITEM)
    it.update(over)
    return it


class AuditFixTests(unittest.TestCase):
    def test_specs_and_units_are_not_shouting(self):
        r = analyze(item(title="Apple iPhone 13 Mini 5G 128GB Blue Unlocked iOS26 BH86% USB"))
        self.assertNotIn("ALL-CAPS", check(r, "title")["fix"])

    def test_real_shouting_still_flagged(self):
        r = analyze(item(title="AMAZING Apple iPhone 13 128GB Blue Unlocked Smartphone"))
        self.assertIn("AMAZING", check(r, "title")["fix"])

    def test_separators_not_counted_as_repeats(self):
        r = analyze(item(title="Apple iPhone 13 - Like New - Unlocked - Bundle"))
        self.assertNotIn("repeated", check(r, "title")["fix"])

    def test_colour_counts_as_color(self):
        asp = [a if a["name"] != "Color" else {"name": "Colour", "value": "Blue"}
               for a in MOCK_ITEM["localizedAspects"]]
        aspects = {"required": ["Brand", "Model", "Color"], "recommended": []}
        r = analyze(item(localizedAspects=asp), MOCK_SIMILAR_PRICES, aspects)
        self.assertNotEqual(check(r, "specifics")["status"], "fail")
        self.assertEqual(r["missing_specifics"], [])

    def test_german_specifics_count(self):
        asp = [{"name": "Marke", "value": "Apple"}, {"name": "Modell", "value": "iPhone 13"},
               {"name": "Farbe", "value": "Rosa"}, {"name": "Speicherkapazität", "value": "128 GB"},
               {"name": "Netzwerk", "value": "Ohne Simlock"}]
        aspects = {"required": ["Brand", "Model", "Color", "Storage Capacity"], "recommended": []}
        r = analyze(item(localizedAspects=asp), MOCK_SIMILAR_PRICES, aspects)
        self.assertEqual(r["missing_specifics"], [])

    def test_price_includes_shipping(self):
        ship = [{"shippingCostType": "FIXED", "shippingCost": {"value": "100.00", "currency": "USD"}}]
        r = analyze(item(shippingOptions=ship), MOCK_SIMILAR_PRICES)
        c = check(r, "price")
        self.assertEqual(c["status"], "warn")
        self.assertIn("shipping", c["detail"])

    def test_small_feedback_not_called_trusted(self):
        r = analyze(item(seller={"username": "x", "feedbackScore": 26, "feedbackPercentage": "100.0"}))
        self.assertNotIn("trusted", check(r, "seller")["fix"])
        self.assertIn("26 ratings", check(r, "seller")["fix"])


class GeneratorFixTests(unittest.TestCase):
    def test_emoji_and_separators_removed(self):
        r = generate_listing({"product_name": "Apple iPhone 13 128GB - Like New - 100% 🔋 - Unlocked",
                              "condition": "used"})
        self.assertEqual(r["title"], "Apple iPhone 13 128GB Like New 100% Battery Unlocked")
        self.assertTrue(r["title_changed"])

    def test_no_conflicting_condition_phrase(self):
        r = generate_listing({"product_name": "Sony Headphones Like New", "condition": "used_good"})
        self.assertNotIn("Good Condition", r["title"])

    def test_plain_used_not_upgraded(self):
        r = generate_listing({"product_name": "Sony Headphones", "condition": "used"})
        self.assertIn("Condition: Used.", r["description"])

    def test_no_false_trim_tip(self):
        r = generate_listing({"product_name": "A" * 10 + " phone " + "word " * 11, "condition": "used_good"})
        self.assertFalse(any("cut" in t or "trimmed" in t for t in r["tips"]))

    def test_negative_price_rejected(self):
        r = generate_listing({"product_name": "Phone", "price": "-5"})
        price = next(x for x in r["readiness"] if x["label"] == "Price")
        self.assertEqual(price["status"], "missing")

    def test_description_uses_specifics(self):
        r = generate_listing({"product_name": "Phone", "existing_specifics": [
            {"name": "Farbe", "value": "Rosa"}, {"name": "Colour", "value": "Pink"}],
            "color": "Pink"})
        self.assertIn("Specifications:", r["description"])
        self.assertEqual(sum(1 for s in r["specifics"] if s["name"] in ("Color", "Colour", "Farbe")), 1)

    def test_missing_specifics_flagged(self):
        r = generate_listing({"product_name": "Phone", "missing_specifics": ["Brand"]})
        spec = next(x for x in r["readiness"] if x["label"] == "Item specifics")
        self.assertEqual(spec["status"], "missing")


class ShareTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_demo_has_signed_share_url(self):
        url = self.client.post("/api/demo", json={}).get_json()["share_url"]
        path = url.split(app_module.SITE_URL, 1)[1]
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"/100", r.data)

    def test_forged_share_redirects(self):
        r = self.client.get("/share?s=100&t=fake&k=deadbeef")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/share-img?s=100&t=fake").status_code, 400)

    def test_robots_and_headers(self):
        r = self.client.get("/robots.txt")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Sitemap:", r.get_data(as_text=True))
        self.assertIn("Content-Security-Policy", r.headers)
        self.assertIn("Strict-Transport-Security", r.headers)
        self.assertEqual(self.client.get("/favicon.ico").status_code, 200)

    def test_analytics_waits_for_consent(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('<script async src="https://www.googletagmanager.com', html)
        self.assertIn('id="consent"', html)


if __name__ == "__main__":
    unittest.main()
