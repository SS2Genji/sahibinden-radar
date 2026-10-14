"""
anomaly_detector.py: Statistical Arbitrage & Market Anomaly Detection Engine.
Calculates market price medians and IQR (Interquartile Range) to dynamically
detect legitimate discount opportunities while filtering scams and extreme outliers.
Uses Python standard library only (statistics, math, typing).
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List


class MarketAnomalyDetector:
    """
    Evaluates listing prices against historical category observations using
    robust non-parametric statistics (Median & Interquartile Range).
    """

    def __init__(
        self,
        min_sample_size: int = 5,
        discount_threshold_pct: float = 12.0,
        scam_floor_pct: float = 35.0,
    ) -> None:
        """
        :param min_sample_size: Minimum number of historical prices required for evaluation.
        :param discount_threshold_pct: Percentage below clean median required to flag opportunity.
        :param scam_floor_pct: Minimum percentage of clean median below which a listing is flagged as scam/fake.
        """
        self.min_sample_size = max(3, min_sample_size)
        self.discount_threshold_pct = discount_threshold_pct
        self.scam_floor_pct = scam_floor_pct

    def calculate_market_metrics(self, prices: List[int]) -> Dict[str, Any]:
        """
        Computes robust central tendencies and dispersion metrics.
        Filters out low-price placeholders (e.g. 1 TL) and absurd high outliers.
        """
        raw_prices = sorted(p for p in prices if p > 0)
        total_count = len(raw_prices)

        if total_count < self.min_sample_size:
            median_val = float(statistics.median(raw_prices)) if raw_prices else 0.0
            return {
                "sample_count": total_count,
                "raw_median": median_val,
                "clean_median": median_val,
                "q1": median_val,
                "q3": median_val,
                "iqr": 0.0,
                "valid_price_count": total_count,
            }

        # Step 1: Compute baseline quantiles
        raw_median = float(statistics.median(raw_prices))
        mid = total_count // 2
        if total_count % 2 == 0:
            q1 = float(statistics.median(raw_prices[:mid]))
            q3 = float(statistics.median(raw_prices[mid:]))
        else:
            q1 = float(statistics.median(raw_prices[:mid]))
            q3 = float(statistics.median(raw_prices[mid + 1:]))

        iqr = max(0.0, q3 - q1)

        # Step 2: Establish robust bounds
        lower_bound = max(
            raw_median * (self.scam_floor_pct / 100.0),
            q1 - 1.5 * iqr if iqr > 0 else raw_median * 0.4
        )
        upper_bound = q3 + 2.0 * iqr if iqr > 0 else raw_median * 2.5

        # Step 3: Filter clean distribution
        clean_prices = [p for p in raw_prices if lower_bound <= p <= upper_bound]
        clean_median = float(statistics.median(clean_prices)) if clean_prices else raw_median

        return {
            "sample_count": total_count,
            "raw_median": raw_median,
            "clean_median": clean_median,
            "q1": q1,
            "q3": q3,
            "iqr": iqr,
            "valid_price_count": len(clean_prices),
        }

    def evaluate_opportunity(
        self,
        target_price: int,
        historical_prices: List[int]
    ) -> Dict[str, Any]:
        """
        Determines if a target listing price represents a genuine arbitrage opportunity.
        """
        if target_price <= 0:
            return {
                "is_opportunity": False,
                "is_scam": True,
                "clean_median": 0.0,
                "discount_pct": 0.0,
                "price_advantage": 0,
                "reason": "invalid_or_zero_price",
            }

        metrics = self.calculate_market_metrics(historical_prices)
        clean_median = metrics["clean_median"]

        # Guard: Insufficient baseline data
        if metrics["valid_price_count"] < self.min_sample_size:
            return {
                "is_opportunity": False,
                "is_scam": False,
                "clean_median": clean_median,
                "discount_pct": 0.0,
                "price_advantage": 0,
                "reason": "insufficient_data",
            }

        # Guard: Scam floor check (e.g. 500 TL on a 50.000 TL phone)
        scam_threshold = clean_median * (self.scam_floor_pct / 100.0)
        if target_price < scam_threshold:
            return {
                "is_opportunity": False,
                "is_scam": True,
                "clean_median": clean_median,
                "discount_pct": round(((clean_median - target_price) / clean_median) * 100.0, 1),
                "price_advantage": 0,
                "reason": "price_below_scam_floor",
            }

        # Calculate discount vs clean median
        discount_amount = clean_median - target_price
        discount_pct = (discount_amount / clean_median) * 100.0

        if discount_pct >= self.discount_threshold_pct:
            return {
                "is_opportunity": True,
                "is_scam": False,
                "clean_median": clean_median,
                "discount_pct": round(discount_pct, 1),
                "price_advantage": int(round(discount_amount)),
                "reason": f"discount_{discount_pct:.1f}%_exceeds_threshold",
            }

        return {
            "is_opportunity": False,
            "is_scam": False,
            "clean_median": clean_median,
            "discount_pct": round(max(0.0, discount_pct), 1),
            "price_advantage": max(0, int(round(discount_amount))),
            "reason": "discount_below_threshold",
        }
