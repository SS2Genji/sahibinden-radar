"""
Unit tests for FilterEngine, Turkish negative keyword filtering,
and SQLite delta tracking.
"""

import os
import tempfile
import unittest
from pathlib import Path

from filter_engine import FilterEngine, normalize_turkish
from radar_db import init_db, mark_as_notified


class TestFilterEngine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_radar.db"
        init_db(self.db_path)

        self.negative_keywords = [
            "ağır hasar",
            "pert",
            "arızalı",
            "tamirli",
            "anakart arızalı"
        ]

        self.engine = FilterEngine(
            db_path=self.db_path,
            negative_keywords=self.negative_keywords,
            discount_threshold_pct=15.0
        )

        # Pre-seed DB with normal market prices around 800,000 TL
        sample_prices = [790000, 800000, 810000, 795000, 805000, 820000]
        for i, p in enumerate(sample_prices):
            self.engine.evaluate_listing({
                "id": f"seed_{i}",
                "category": "Renault Megane",
                "title": f"Renault Megane {i}",
                "price": p,
                "url": f"https://example.com/{i}",
                "location": "Ankara"
            })

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_turkish_normalization(self):
        self.assertEqual(normalize_turkish("AĞIR HASARLI"), "ağır hasarlı")
        self.assertEqual(normalize_turkish("İSTANBUL"), "istanbul")
        self.assertEqual(normalize_turkish("ŞANZIMAN"), "şanzıman")

    def test_negative_keyword_purge(self):
        # Items with Turkish negative keywords must be rejected immediately
        bad_item = {
            "id": "bad_1",
            "category": "Renault Megane",
            "title": "2020 Megane Ağır Hasar Kayıtlı Acil",
            "price": 500000,
            "url": "https://example.com/bad1",
            "location": "İzmir"
        }
        res = self.engine.evaluate_listing(bad_item)
        self.assertFalse(res["should_notify"])
        self.assertIn("negative_keyword_matched", res["reason"])

    def test_new_genuine_opportunity_alert(self):
        # A clean listing at 650,000 TL (~19% below median of ~800,000 TL)
        good_item = {
            "id": "good_1",
            "category": "Renault Megane",
            "title": "2021 Renault Megane Joy Boyasız Hatasız",
            "price": 650000,
            "url": "https://example.com/good1",
            "location": "İstanbul"
        }
        res = self.engine.evaluate_listing(good_item)
        self.assertTrue(res["should_notify"])
        self.assertEqual(res["alert_type"], "NEW_OPPORTUNITY")
        self.assertGreaterEqual(res["discount_pct"], 15.0)

    def test_deduplication_prevents_duplicate_alert(self):
        item = {
            "id": "dup_1",
            "category": "Renault Megane",
            "title": "2021 Renault Megane Kelepir",
            "price": 660000,
            "url": "https://example.com/dup1",
            "location": "Bursa"
        }
        res1 = self.engine.evaluate_listing(item)
        self.assertTrue(res1["should_notify"])
        mark_as_notified(self.db_path, "dup_1")

        # Second evaluation of the same item must NOT trigger an alert
        res2 = self.engine.evaluate_listing(item)
        self.assertFalse(res2["should_notify"])
        self.assertEqual(res2["reason"], "already_seen_unchanged")

    def test_price_drop_triggers_alert(self):
        item = {
            "id": "drop_1",
            "category": "Renault Megane",
            "title": "2021 Renault Megane Normal",
            "price": 780000,
            "url": "https://example.com/drop1",
            "location": "Ankara"
        }
        # First scan: normal price -> no alert
        self.engine.evaluate_listing(item)

        # Later scan: price drops from 780.000 TL to 700.000 TL
        item["price"] = 700000
        res = self.engine.evaluate_listing(item)
        self.assertTrue(res["should_notify"])
        self.assertEqual(res["alert_type"], "PRICE_DROP")
        self.assertEqual(res["old_price"], 780000)
        self.assertEqual(res["new_price"], 700000)

    def test_city_filtering(self):
        engine_city = FilterEngine(
            db_path=self.db_path,
            discount_threshold_pct=15.0,
            target_cities=["İstanbul", "Ankara"]
        )
        # Listing in İzmir should be rejected by city filter
        izmir_item = {
            "id": "city_1",
            "category": "Renault Megane",
            "title": "2021 Renault Megane Joy",
            "price": 640000,
            "url": "https://example.com/c1",
            "location": "İzmir Konak"
        }
        res = engine_city.evaluate_listing(izmir_item)
        self.assertFalse(res["should_notify"])
        self.assertIn("city_filtered", res["reason"])

        # Listing in İstanbul should pass
        ist_item = {
            "id": "city_2",
            "category": "Renault Megane",
            "title": "2021 Renault Megane Joy",
            "price": 640000,
            "url": "https://example.com/c2",
            "location": "İstanbul Kadıköy"
        }
        res2 = engine_city.evaluate_listing(ist_item)
        self.assertTrue(res2["should_notify"])

    def test_min_profit_threshold(self):
        engine_profit = FilterEngine(
            db_path=self.db_path,
            discount_threshold_pct=10.0,
            min_profit_tl=200000  # Impossibly high profit threshold
        )
        item = {
            "id": "profit_1",
            "category": "Renault Megane",
            "title": "2021 Renault Megane Joy",
            "price": 680000,  # ~120,000 TL profit vs 800,000 median
            "url": "https://example.com/p1",
            "location": "Ankara"
        }
        res = engine_profit.evaluate_listing(item)
        self.assertFalse(res["should_notify"])
        self.assertIn("below_minimum", res["reason"])


if __name__ == "__main__":
    unittest.main()

