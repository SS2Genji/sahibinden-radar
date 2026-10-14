"""
tests/test_radar_db.py: Unit tests for SQLite database operations,
opportunity recording, dashboard statistics, and listing exploration.
"""

import tempfile
import unittest
from pathlib import Path

from radar_db import (
    get_dashboard_stats,
    get_opportunities,
    get_recent_listings,
    init_db,
    process_listing_delta,
    record_opportunity,
)


class TestRadarDB(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_radar.db"
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_record_and_get_opportunities(self):
        item = {
            "id": "1001",
            "category": "smartphones",
            "title": "iPhone 15 Pro 128GB Tertemiz",
            "price": 45000,
            "url": "https://sahibinden.com/ilan/1001",
            "location": "İstanbul Kadıköy",
        }
        process_listing_delta(self.db_path, item)

        decision = {
            "clean_median": 60000,
            "price_advantage": 15000,
            "discount_pct": 25.0,
            "detected_variant": "15 Pro 128GB",
            "hardware_score": 95,
            "cosmetic_score": 90,
            "detected_flaws": ["kılcal_çizik"],
            "positive_traits": ["pil_orijinal", "kutu_fatura_var"],
            "expert_verdict": "CLEAN",
            "liquidity_rating": "HOT_FLIP",
            "turnover_days": "under_24_hours",
            "quick_flip_price": 54000,
            "alert_type": "NEW_OPPORTUNITY",
        }

        opp_id = record_opportunity(self.db_path, item, decision)
        self.assertGreater(opp_id, 0)

        # Retrieve opportunities
        opps = get_opportunities(self.db_path)
        self.assertEqual(len(opps), 1)
        first = opps[0]
        self.assertEqual(first["title"], item["title"])
        self.assertEqual(first["profit_margin"], 15000)
        self.assertEqual(first["detected_flaws"], ["kılcal_çizik"])
        self.assertEqual(first["liquidity_rating"], "HOT_FLIP")

    def test_dashboard_stats(self):
        item1 = {
            "id": "2001",
            "category": "gaming",
            "title": "PlayStation 5 Slim Dijital",
            "price": 16000,
            "url": "https://sahibinden.com/ilan/2001",
            "location": "Ankara Çankaya",
        }
        item2 = {
            "id": "2002",
            "category": "smartphones",
            "title": "iPhone 14 Pro 128GB",
            "price": 40000,
            "url": "https://sahibinden.com/ilan/2002",
            "location": "İzmir Bornova",
        }
        process_listing_delta(self.db_path, item1)
        process_listing_delta(self.db_path, item2)

        decision = {
            "clean_median": 22000,
            "price_advantage": 6000,
            "discount_pct": 27.2,
            "detected_variant": "Slim Digital",
            "hardware_score": 100,
            "cosmetic_score": 100,
            "detected_flaws": [],
            "positive_traits": ["kutu_fatura_var"],
            "expert_verdict": "CLEAN",
            "liquidity_rating": "HOT_FLIP",
            "turnover_days": "under_24_hours",
            "quick_flip_price": 19500,
            "alert_type": "NEW_OPPORTUNITY",
        }
        record_opportunity(self.db_path, item1, decision)

        stats = get_dashboard_stats(self.db_path)
        self.assertEqual(stats["total_listings"], 2)
        self.assertEqual(stats["total_opportunities"], 1)
        self.assertIn("high_profit_count", stats)
        self.assertIn("active_targets_count", stats)
        self.assertEqual(stats["avg_profit"], 6000)
        self.assertIn("gaming", stats["categories_tracked"])
        self.assertIn("smartphones", stats["categories_tracked"])

    def test_get_recent_listings(self):
        item = {
            "id": "3001",
            "category": "laptops",
            "title": "MacBook Air M2 16GB",
            "price": 32000,
            "url": "https://sahibinden.com/ilan/3001",
            "location": "Bursa Nilüfer",
        }
        process_listing_delta(self.db_path, item)

        recent = get_recent_listings(self.db_path, limit=10)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["title"], item["title"])

        search_res = get_recent_listings(self.db_path, search="MacBook")
        self.assertEqual(len(search_res), 1)
        search_empty = get_recent_listings(self.db_path, search="Dyson")
        self.assertEqual(len(search_empty), 0)

    def test_custom_targets_crud(self):
        from radar_db import add_custom_target, get_custom_targets, toggle_custom_target, delete_custom_target

        target_id = add_custom_target(
            self.db_path,
            name="RTX 4080",
            url="https://www.sahibinden.com/ekran-karti?query_text=rtx+4080",
            min_profit_tl=4000,
            negative_keywords=["kırık", "kutu"],
            target_cities=["İstanbul", "Ankara"]
        )
        self.assertGreater(target_id, 0)

        targets = get_custom_targets(self.db_path)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["name"], "RTX 4080")
        self.assertEqual(targets[0]["min_profit_tl"], 4000)
        self.assertEqual(targets[0]["negative_keywords"], ["kırık", "kutu"])
        self.assertEqual(targets[0]["target_cities"], ["İstanbul", "Ankara"])
        self.assertEqual(targets[0]["enabled"], 1)

        # Toggle state
        toggle_custom_target(self.db_path, target_id)
        targets_disabled = get_custom_targets(self.db_path, enabled_only=True)
        self.assertEqual(len(targets_disabled), 0)

        # Delete target
        deleted = delete_custom_target(self.db_path, target_id)
        self.assertTrue(deleted)
        self.assertEqual(len(get_custom_targets(self.db_path)), 0)


if __name__ == "__main__":
    unittest.main()
