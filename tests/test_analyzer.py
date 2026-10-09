import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer import analyze
from ebay_client import EbayClient, MOCK_ITEM, MOCK_SIMILAR_PRICES, MOCK_CATEGORY_ASPECTS


def check(report, cid):
    return next(c for c in report["checks"] if c["id"] == cid)


def item(**over):
    it = copy.deepcopy(MOCK_ITEM)
    it.update(over)
    return it


class AnalyzerTests(unittest.TestCase):
    def test_weights_sum_to_100_when_everything_known(self):
        r = analyze(item(handlingTimeDays=1), MOCK_SIMILAR_PRICES, MOCK_CATEGORY_ASPECTS)
        self.assertEqual(sum(c["weight"] for c in r["checks"]), 100)

    def test_score_in_range(self):
        r = analyze(item(), MOCK_SIMILAR_PRICES, MOCK_CATEGORY_ASPECTS)
        self.assertTrue(0 <= r["score"] <= 100)

    def test_missing_required_specific_fails(self):
        asp = [a for a in MOCK_ITEM["localizedAspects"] if a["name"] != "Model"]
        r = analyze(item(localizedAspects=asp), MOCK_SIMILAR_PRICES, MOCK_CATEGORY_ASPECTS)
        c = check(r, "specifics")
        self.assertEqual(c["status"], "fail")
        self.assertIn("Model", c["fix"])

    def test_recommended_specifics_warn(self):
        r = analyze(item(), MOCK_SIMILAR_PRICES, MOCK_CATEGORY_ASPECTS)
        self.assertEqual(check(r, "specifics")["status"], "warn")

    def test_specifics_without_taxonomy_falls_back_to_count(self):
        r = analyze(item(), MOCK_SIMILAR_PRICES, None)
        self.assertEqual(check(r, "specifics")["status"], "pass")

    def test_handling_unknown_is_info_and_unscored(self):
        r = analyze(item(), MOCK_SIMILAR_PRICES)
        c = check(r, "handling")
        self.assertEqual((c["status"], c["weight"]), ("info", 0))
        self.assertNotIn("Handling time", [f["label"] for f in r["fixes"]])

    def test_handling_values(self):
        self.assertEqual(check(analyze(item(handlingTimeDays=1)), "handling")["status"], "pass")
        self.assertEqual(check(analyze(item(handlingTimeDays=3)), "handling")["status"], "warn")
        self.assertEqual(check(analyze(item(handlingTimeDays=5)), "handling")["status"], "fail")

    def test_photo_size(self):
        many = [{"imageUrl": "u%d" % i} for i in range(9)]
        big = item(additionalImages=many, image={"imageUrl": "m", "width": 1800, "height": 1200})
        mid = item(additionalImages=many, image={"imageUrl": "m", "width": 1000, "height": 800})
        tiny = item(additionalImages=many, image={"imageUrl": "m", "width": 300, "height": 300})
        self.assertEqual(check(analyze(big), "photos")["status"], "pass")
        self.assertEqual(check(analyze(mid), "photos")["status"], "warn")
        self.assertEqual(check(analyze(tiny), "photos")["status"], "fail")

    def test_identifiers(self):
        self.assertEqual(check(analyze(item()), "identifiers")["status"], "warn")
        with_upc = item(localizedAspects=MOCK_ITEM["localizedAspects"] + [{"name": "UPC", "value": "190199"}])
        self.assertEqual(check(analyze(with_upc), "identifiers")["status"], "pass")
        na = item(localizedAspects=MOCK_ITEM["localizedAspects"] + [{"name": "UPC", "value": "Does not apply"}])
        self.assertEqual(check(analyze(na), "identifiers")["status"], "warn")
        self.assertEqual(check(analyze(item(gtin="0190199123456")), "identifiers")["status"], "pass")

    def test_no_comparables_not_scored(self):
        self.assertEqual(check(analyze(item(), []), "price")["weight"], 0)


class ClientTests(unittest.TestCase):
    def test_parse_aspects(self):
        data = {"aspects": [
            {"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True, "aspectUsage": "RECOMMENDED"}},
            {"localizedAspectName": "Color", "aspectConstraint": {"aspectRequired": False, "aspectUsage": "RECOMMENDED"}},
            {"localizedAspectName": "Notes", "aspectConstraint": {"aspectUsage": "OPTIONAL"}},
            {"aspectConstraint": {}},
        ]}
        self.assertEqual(EbayClient._parse_aspects(data),
                         {"required": ["Brand"], "recommended": ["Color"]})
        self.assertEqual(EbayClient._parse_aspects(None), {"required": [], "recommended": []})

    def test_condition_filter_and_outliers(self):
        self.assertEqual(EbayClient._condition_filter("Used"), "USED")
        self.assertEqual(EbayClient._condition_filter("New"), "NEW")
        self.assertEqual(EbayClient._condition_filter("Open box"), "")
        self.assertEqual(EbayClient._trim_outliers([100, 105, 110, 95, 102, 5, 900]),
                         [95, 100, 102, 105, 110])


if __name__ == "__main__":
    unittest.main()
