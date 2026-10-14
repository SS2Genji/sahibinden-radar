"""
Unit tests for MarketAnomalyDetector.
Verifies statistical quantile calculations, scam floor rejections, and arbitrage detection.
"""

import unittest
from anomaly_detector import MarketAnomalyDetector


class TestMarketAnomalyDetector(unittest.TestCase):

    def setUp(self):
        self.detector = MarketAnomalyDetector(
            min_sample_size=5,
            discount_threshold_pct=12.0,
            scam_floor_pct=35.0
        )
        # Baseline prices around 50,000 TL (e.g. iPhone 15 Pro)
        self.market_prices = [
            48000, 49000, 50000, 51000, 52000, 50500, 49500, 53000
        ]

    def test_insufficient_data(self):
        sparse_prices = [50000, 51000]
        res = self.detector.evaluate_opportunity(40000, sparse_prices)
        self.assertFalse(res["is_opportunity"])
        self.assertEqual(res["reason"], "insufficient_data")

    def test_scam_floor_rejection(self):
        # 1 TL, 500 TL, or 5000 TL joke prices on a 50.000 TL item
        res_one = self.detector.evaluate_opportunity(1, self.market_prices)
        self.assertTrue(res_one["is_scam"])
        self.assertFalse(res_one["is_opportunity"])

        res_joke = self.detector.evaluate_opportunity(5000, self.market_prices)
        self.assertTrue(res_joke["is_scam"])
        self.assertFalse(res_joke["is_opportunity"])
        self.assertEqual(res_joke["reason"], "price_below_scam_floor")

    def test_genuine_opportunity(self):
        # Market median is ~50,250 TL.
        # A 40,000 TL listing is ~20.4% below median (exceeds 12% threshold).
        res = self.detector.evaluate_opportunity(40000, self.market_prices)
        self.assertTrue(res["is_opportunity"])
        self.assertFalse(res["is_scam"])
        self.assertGreaterEqual(res["discount_pct"], 12.0)
        self.assertGreater(res["price_advantage"], 9000)

    def test_normal_market_price_rejection(self):
        # 49,000 TL is only ~2.5% below median -> Not an arbitrage opportunity
        res = self.detector.evaluate_opportunity(49000, self.market_prices)
        self.assertFalse(res["is_opportunity"])
        self.assertFalse(res["is_scam"])
        self.assertEqual(res["reason"], "discount_below_threshold")

    def test_outlier_resistance(self):
        # Include crazy high outliers (e.g. 150.000 TL) and low placeholders
        polluted = self.market_prices + [1, 150000, 200000]
        metrics = self.detector.calculate_market_metrics(polluted)
        # Clean median should remain stable near 50,000 TL
        self.assertTrue(48000 <= metrics["clean_median"] <= 53000)


if __name__ == "__main__":
    unittest.main()
