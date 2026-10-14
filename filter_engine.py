"""
filter_engine.py: 3-Tier Noise Reduction & Opportunity Filter Pipeline.
1. Tier 1: SQLite Delta Engine (Identifies new listings and price drops).
2. Tier 2: Turkish Negative Keyword Purge (Regex with Turkish character folding).
3. Tier 3: Statistical Arbitrage Evaluation (Interquartile Range & Median Comparison).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from market_arbitrage_engine import MarketArbitrageEngine
from pro_target_engine import AdaptiveMarketEngine, HonestDefectExtractor
from radar_db import (
    get_category_historical_items,
    is_notified,
    process_listing_delta,
)


def normalize_turkish(text: str) -> str:
    """Normalizes Turkish characters and lowercases text for reliable keyword matching."""
    mapping = {
        "I": "ı", "İ": "i",
        "Ç": "ç", "Ş": "ş",
        "Ğ": "ğ", "Ü": "ü",
        "Ö": "ö"
    }
    for upper, lower in mapping.items():
        text = text.replace(upper, lower)
    return text.lower()


class FilterEngine:
    """Coordinates the 3-tier filtering pipeline powered by Argon ProTargetEngine."""

    def __init__(
        self,
        db_path: str | Path = "radar.db",
        negative_keywords: Optional[List[str]] = None,
        discount_threshold_pct: float = 12.0,
        min_confidence: float = 0.50,
        min_profit_tl: int = 0,
        target_cities: Optional[List[str]] = None,
    ) -> None:
        self.db_path = db_path
        self.negative_keywords = [normalize_turkish(k) for k in (negative_keywords or [])]
        self.discount_threshold_pct = float(discount_threshold_pct)
        self.min_confidence = float(min_confidence)
        self.min_profit_tl = int(min_profit_tl)
        self.target_cities = [normalize_turkish(c) for c in (target_cities or [])]
        self.arbitrage_engine = MarketArbitrageEngine(
            discount_threshold_pct=discount_threshold_pct,
            min_confidence=min_confidence,
        )
        self.adaptive_engine = AdaptiveMarketEngine()
        self.defect_extractor = HonestDefectExtractor()

    def check_negative_keywords(
        self,
        title: str,
        extra_keywords: Optional[List[str]] = None
    ) -> Optional[str]:
        """Returns the matched negative keyword if found, else None."""
        normalized_title = normalize_turkish(title)
        all_kw = self.negative_keywords + [normalize_turkish(k) for k in (extra_keywords or [])]
        for kw in all_kw:
            # Word boundary or clean substring match
            pattern = r"(?:^|\W)" + re.escape(kw) + r"(?:$|\W)"
            if re.search(pattern, normalized_title):
                return kw
        return None

    def evaluate_listing(
        self,
        item: Dict[str, Any],
        extra_negative_keywords: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Executes the full 3-tier filter on an incoming listing item.
        Returns evaluation result with notification decision.
        """
        listing_id = str(item["id"])
        title = item.get("title", "")
        price = int(item.get("price", 0))
        category = item.get("category", "default")
        location = item.get("location", "")

        # ---------------- Tier 0: City Filter ----------------
        if self.target_cities and location:
            norm_loc = normalize_turkish(location)
            if not any(city in norm_loc for city in self.target_cities):
                return {
                    "should_notify": False,
                    "reason": f"city_filtered: '{location}' not in target cities",
                    "alert_type": "NONE"
                }

        # ---------------- Tier 1: Delta Check ----------------
        is_new, is_price_drop, old_price = process_listing_delta(self.db_path, item)

        if not is_new and not is_price_drop:
            return {
                "should_notify": False,
                "reason": "already_seen_unchanged",
                "alert_type": "NONE"
            }

        if is_notified(self.db_path, listing_id) and not is_price_drop:
            return {
                "should_notify": False,
                "reason": "already_notified",
                "alert_type": "NONE"
            }

        # ------------ Tier 2: Negative Keyword Purge -----------
        matched_kw = self.check_negative_keywords(title, extra_negative_keywords)
        if matched_kw:
            return {
                "should_notify": False,
                "reason": f"negative_keyword_matched: '{matched_kw}'",
                "alert_type": "NONE"
            }

        # --------- Tier 3: Statistical Arbitrage / Anomaly -----
        history_items = get_category_historical_items(self.db_path, category, limit=150)
        # Exclude current item's own id if already recorded in history
        eval_history = [
            h for h in history_items
            if str(h.get("id")) != listing_id and h.get("price", 0) > 0
        ]

        eval_result = self.arbitrage_engine.evaluate_listing(
            {"title": title, "price": price},
            eval_history
        )

        fair_price = eval_result.get("fair_market_price", 0.0)
        disc_pct = eval_result.get("discount_pct", 0.0)
        profit_margin = eval_result.get("profit_margin_tl", 0)
        variant = eval_result.get("detected_variant", "Standard")
        battery = eval_result.get("battery_health")
        confidence = round(eval_result.get("confidence_score", 0.0) * 100.0, 1)

        # Fallback to AdaptiveMarketEngine if variant model is sparse or for custom targets
        if fair_price <= 0 and eval_history:
            adaptive_res = self.adaptive_engine.evaluate(
                candidate_price=float(price),
                historical_prices=[float(h["price"]) for h in eval_history if h.get("price", 0) > 0],
                discount_threshold_pct=self.discount_threshold_pct,
                min_profit_tl=self.min_profit_tl,
                min_confidence=self.min_confidence,
            )
            fair_price = adaptive_res.fair_market_price
            disc_pct = adaptive_res.discount_pct
            profit_margin = adaptive_res.profit_margin_tl
            confidence = round(adaptive_res.confidence_score * 100.0, 1)
            is_arb = adaptive_res.is_arbitrage_opportunity
            is_scam_price = adaptive_res.is_scam_or_placeholder
        else:
            is_arb = eval_result.get("is_arbitrage_opportunity", False)
            is_scam_price = eval_result.get("is_scam_or_placeholder", False)

        # Run Honest Defect Extractor
        description = item.get("description", "")
        defect_report = self.defect_extractor.extract(title, description=description)

        if defect_report.is_scam_risk:
            return {
                "should_notify": False,
                "alert_type": "NONE",
                "price": price,
                "clean_median": fair_price,
                "detected_variant": variant,
                "reason": "scam_risk_detected"
            }

        # Price drop alert
        if is_price_drop and not is_scam_price and fair_price > 0:
            drop_amount = old_price - price
            drop_pct = round((drop_amount / old_price) * 100.0, 1)
            return {
                "should_notify": True,
                "alert_type": "PRICE_DROP",
                "old_price": old_price,
                "new_price": price,
                "drop_amount": drop_amount,
                "drop_pct": drop_pct,
                "clean_median": fair_price,
                "detected_variant": variant,
                "battery_health": battery,
                "confidence_score": confidence,
                "is_clean": defect_report.is_clean,
                "detected_flaws": defect_report.defects,
                "expert_verdict": defect_report.status_label,
                "reason": f"price_dropped_{drop_pct}%"
            }

        # New listing opportunity alert
        if is_arb and not is_scam_price:
            if profit_margin < self.min_profit_tl:
                return {
                    "should_notify": False,
                    "alert_type": "NONE",
                    "price": price,
                    "clean_median": fair_price,
                    "detected_variant": variant,
                    "reason": f"profit_{profit_margin}_below_minimum_{self.min_profit_tl}"
                }

            return {
                "should_notify": True,
                "alert_type": "NEW_OPPORTUNITY",
                "price": price,
                "clean_median": fair_price,
                "discount_pct": round(disc_pct, 1),
                "price_advantage": profit_margin,
                "detected_variant": variant,
                "battery_health": battery,
                "confidence_score": confidence,
                "is_clean": defect_report.is_clean,
                "detected_flaws": defect_report.defects,
                "expert_verdict": defect_report.status_label,
                "reason": eval_result.get("reason", "arbitrage_opportunity")
            }

        return {
            "should_notify": False,
            "alert_type": "NONE",
            "price": price,
            "clean_median": fair_price,
            "detected_variant": variant,
            "reason": eval_result.get("reason", "no_opportunity")
        }
