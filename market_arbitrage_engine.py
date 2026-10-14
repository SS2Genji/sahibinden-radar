"""
Production-grade Market Arbitrage & Anomaly Detection Engine for second-hand classifieds.

Provides deterministic variant extraction, order-statistic outlier filtering (IQR/MAD),
cross-variant normalized fallback for sparse cohorts, and arbitrage evaluation using
only the Python standard library.
"""

from __future__ import annotations

import math
import re
import unittest
from dataclasses import dataclass
from typing import Any, Final, Mapping, Sequence, TypedDict


# ============================================================================
# Custom Exceptions & Public Type Contracts
# ============================================================================


class ArbitrageEngineError(Exception):
    """Base exception for all MarketArbitrageEngine domain errors."""


class InvalidListingError(ArbitrageEngineError):
    """Raised when a listing payload or configuration violates domain invariants."""


class EvaluationResult(TypedDict):
    """Strict output contract for MarketArbitrageEngine.evaluate_listing."""

    is_arbitrage_opportunity: bool
    is_scam_or_placeholder: bool
    detected_variant: str
    battery_health: int | None
    fair_market_price: float
    discount_pct: float
    profit_margin_tl: int
    confidence_score: float
    reason: str


@dataclass(frozen=True, slots=True)
class VariantProfile:
    """Immutable value object representing parsed listing attributes."""

    base_model: str
    sub_model: str
    capacity_gb: int | None
    battery_health: int | None

    @property
    def variant_key(self) -> tuple[str, str, int | None]:
        """Normalized key used for cohort partitioning."""
        return (self.base_model.lower(), self.sub_model, self.capacity_gb)

    @property
    def display_name(self) -> str:
        """Human-readable canonical variant label (e.g. 'iPhone 15 Pro 256GB')."""
        parts: list[str] = []
        if self.base_model:
            parts.append(self.base_model)
        if self.sub_model != "Standard" and not (
            self.base_model.lower().endswith(self.sub_model.lower())
        ):
            parts.append(self.sub_model)
        if self.capacity_gb is not None:
            cap_str = "1TB" if self.capacity_gb == 1024 else f"{self.capacity_gb}GB"
            parts.append(cap_str)
        return " ".join(parts) if parts else "Unknown Variant"


@dataclass(frozen=True, slots=True)
class RobustStats:
    """Immutable container for robust location and scale statistics."""

    median: float
    q1: float
    q3: float
    iqr: float
    mad: float
    clean_count: int
    raw_count: int


# ============================================================================
# 1. Variant & Attribute Extractor
# ============================================================================


class VariantExtractor:
    """
    Stateless, regex-driven extractor for device model, sub-model tier,
    storage capacity, and battery health percentage.

    Time Complexity: O(L) where L is the character length of the title.
    Space Complexity: O(1) auxiliary heap memory (pre-compiled patterns).
    """

    # Storage capacity pattern: matches 64GB, 128GB, 256GB, 512GB, 1024GB, 1TB, 2TB.
    _CAPACITY_RE: Final[re.Pattern[str]] = re.compile(
        r"\b(?P<val>64|128|256|512|1024|1|2)\s*(?P<unit>GB|TB)\b",
        re.IGNORECASE,
    )

    # Sub-model tier pattern: ordered longest-match first so 'Pro Max' precedes 'Pro'.
    _SUBMODEL_RE: Final[re.Pattern[str]] = re.compile(
        r"\b(?P<tier>Pro\s+Max|Pro|Plus|Ultra|Mini)\b",
        re.IGNORECASE,
    )

    # Canonical base product family recognizer (e.g., 'iPhone 15', 'Galaxy S24', 'iPad Pro').
    _BASE_MODEL_RE: Final[re.Pattern[str]] = re.compile(
        r"\b(?P<brand>iPhone\s*\d{1,2}|Galaxy\s*S\d{1,2}|Redmi\s*Note\s*\d{1,2}|"
        r"MacBook\s*(?:Air|Pro)?|iPad(?:\s*Air|\s*Mini)?|Pixel\s*\d{1,2})\b",
        re.IGNORECASE,
    )

    # Battery health patterns supporting Turkish and English classified conventions:
    # e.g. "Pil 93", "Pil: %89", "%100 pil", "Batarya 91", "Battery Health %88".
    _BATTERY_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
        r"\b(?:pil(?:\s*sa[gğ]l[ıi][gğ][ıi])?|batarya|battery(?:\s*health)?)"
        r"\s*[:=%-]?\s*%?\s*(?P<pct>\d{2,3})\b",
        re.IGNORECASE,
    )
    _BATTERY_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(
        r"(?:%\s*(?P<pct1>\d{2,3})\s*(?:pil|batarya|battery))"
        r"|(?:\b(?P<pct2>\d{2,3})\s*%\s*(?:pil|batarya|battery)\b)",
        re.IGNORECASE,
    )

    _CANONICAL_BRANDS: Final[dict[str, str]] = {
        "iphone": "iPhone",
        "galaxy": "Galaxy",
        "redmi": "Redmi",
        "macbook": "MacBook",
        "ipad": "iPad",
        "pixel": "Pixel",
    }

    @classmethod
    def extract(cls, title: str) -> VariantProfile:
        """
        Parse a raw listing title into a structured VariantProfile.

        Args:
            title: Raw listing title string.

        Returns:
            VariantProfile containing normalized base model, sub-model, capacity,
            and battery health.

        Raises:
            InvalidListingError: If title is not a string or is empty/whitespace.
        """
        if not isinstance(title, str) or not title.strip():
            raise InvalidListingError("Listing title must be a non-empty string.")

        capacity_gb = cls._extract_capacity_gb(title)
        sub_model = cls._extract_sub_model(title)
        base_model = cls._extract_base_model(title, sub_model)
        battery_health = cls._extract_battery_health(title)

        return VariantProfile(
            base_model=base_model,
            sub_model=sub_model,
            capacity_gb=capacity_gb,
            battery_health=battery_health,
        )

    @classmethod
    def _extract_capacity_gb(cls, title: str) -> int | None:
        match = cls._CAPACITY_RE.search(title)
        if not match:
            return None
        value = int(match.group("val"))
        unit = match.group("unit").upper()
        if unit == "TB":
            return value * 1024
        # Guard against matching "1 GB" or "2 GB" unless unit is TB
        if value in (1, 2):
            return None
        return value

    @classmethod
    def _extract_sub_model(cls, title: str) -> str:
        match = cls._SUBMODEL_RE.search(title)
        if not match:
            return "Standard"
        raw_tier = " ".join(match.group("tier").split()).lower()
        if raw_tier == "pro max":
            return "Pro Max"
        return raw_tier.capitalize()

    @classmethod
    def _extract_base_model(cls, title: str, sub_model: str) -> str:
        match = cls._BASE_MODEL_RE.search(title)
        if match:
            tokens = match.group("brand").split()
            first = cls._CANONICAL_BRANDS.get(tokens[0].lower(), tokens[0].capitalize())
            rest = " ".join(t.upper() if len(t) <= 2 else t.capitalize() for t in tokens[1:])
            return f"{first} {rest}".strip()

        # Fallback: strip capacity, battery phrases, and sub-model tokens to isolate core name
        cleaned = cls._CAPACITY_RE.sub("", title)
        cleaned = cls._SUBMODEL_RE.sub("", cleaned)
        cleaned = cls._BATTERY_PREFIX_RE.sub("", cleaned)
        cleaned = cls._BATTERY_SUFFIX_RE.sub("", cleaned)
        tokens = [
            tok for tok in re.split(r"[^\w]+", cleaned)
            if tok and not (len(tok) == 4 and tok.isdigit() and 1980 <= int(tok) <= 2035)
            and tok.lower() not in {"temiz", "acil", "satilik", "satılık", "kutulu", "hatasiz", "hatasız", "tr", "garantili", "pil", "batarya"}
        ]
        if not tokens:
            return "Generic Device" if sub_model == "Standard" else ""
        return " ".join(tokens[:2])

    @classmethod
    def _extract_battery_health(cls, title: str) -> int | None:
        for pattern in (cls._BATTERY_PREFIX_RE, cls._BATTERY_SUFFIX_RE):
            for match in pattern.finditer(title):
                raw_pct = next((v for v in match.groupdict().values() if v is not None), None)
                if raw_pct is not None:
                    pct = int(raw_pct)
                    if 50 <= pct <= 100:
                        return pct
        return None


# ============================================================================
# 2. Robust Statistical Market Model
# ============================================================================


class RobustMarketModel:
    """
    Order-statistic estimator utilizing Median, Interquartile Range (IQR),
    and Median Absolute Deviation (MAD) for scam-resistant pricing models.

    Time Complexity: O(N log N) dominated by a single initial Timsort.
                     Outlier filtering and MAD calculation execute in O(N).
    Space Complexity: O(N) for the sorted price buffer; O(1) auxiliary space for MAD.
    """

    SCAM_MEDIAN_FLOOR_RATIO: Final[float] = 0.35
    IQR_MULTIPLIER: Final[float] = 1.5
    MAD_NORMAL_SCALE: Final[float] = 1.4826
    MIN_VARIANT_SAMPLE_SIZE: Final[int] = 4

    # Relative valuation multipliers anchored to ('Pro', 128GB) = 1.00
    SUBMODEL_MULTIPLIERS: Final[dict[str, float]] = {
        "Mini": 0.75,
        "Standard": 0.82,
        "Plus": 0.90,
        "Pro": 1.00,
        "Pro Max": 1.18,
        "Ultra": 1.18,
    }

    CAPACITY_MULTIPLIERS: Final[dict[int | None, float]] = {
        64: 0.92,
        None: 1.00,
        128: 1.00,
        256: 1.08,
        512: 1.18,
        1024: 1.30,
        2048: 1.45,
    }

    @classmethod
    def get_variant_multiplier(cls, sub_model: str, capacity_gb: int | None) -> float:
        """Return the composite relative price multiplier for a (sub_model, capacity) pair."""
        sub_mult = cls.SUBMODEL_MULTIPLIERS.get(sub_model, 1.00)
        cap_mult = cls.CAPACITY_MULTIPLIERS.get(capacity_gb, 1.00)
        return sub_mult * cap_mult

    @classmethod
    def compute_robust_stats(cls, prices: Sequence[float]) -> RobustStats | None:
        """
        Compute robust location and scale statistics after filtering scam floors
        and extreme Tukey/MAD outliers.

        Args:
            prices: Sequence of raw candidate market prices.

        Returns:
            RobustStats instance, or None if no valid finite positive prices exist.
        """
        valid_sorted = sorted(
            float(p) for p in prices
            if isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p) and p > 0.0
        )
        raw_n = len(valid_sorted)
        if raw_n == 0:
            return None

        # Stage 1: Coarse plausibility filter to prevent >25% scam placeholders (1 TL, 100 TL)
        # from corrupting Q1 and artificially widening IQR.
        raw_median = cls._median_from_sorted(valid_sorted)
        coarse_floor = cls.SCAM_MEDIAN_FLOOR_RATIO * raw_median
        plausible_sorted = [p for p in valid_sorted if p >= coarse_floor]
        if not plausible_sorted:
            plausible_sorted = valid_sorted

        # Stage 2: Quartile and IQR fence calculation on plausible sorted sample.
        med_plausible = cls._median_from_sorted(plausible_sorted)
        q1, q3 = cls._quartiles_from_sorted(plausible_sorted)
        iqr = q3 - q1

        lower_fence = max(
            cls.SCAM_MEDIAN_FLOOR_RATIO * med_plausible,
            q1 - cls.IQR_MULTIPLIER * iqr,
        )
        # When IQR == 0 (identical prices), cap upper fence at 1.85x median rather than
        # rejecting slight upward deviations while still discarding extreme troll prices.
        upper_fence = (
            q3 + cls.IQR_MULTIPLIER * iqr if iqr > 0.0 else 1.85 * med_plausible
        )

        # Filtering a sorted list preserves ascending sort order in O(N) time.
        clean_sorted = [p for p in plausible_sorted if lower_fence <= p <= upper_fence]
        if not clean_sorted:
            clean_sorted = plausible_sorted

        clean_med = cls._median_from_sorted(clean_sorted)
        clean_q1, clean_q3 = cls._quartiles_from_sorted(clean_sorted)
        clean_iqr = clean_q3 - clean_q1
        clean_mad = cls._streaming_mad_from_sorted(clean_sorted, clean_med)

        return RobustStats(
            median=clean_med,
            q1=clean_q1,
            q3=clean_q3,
            iqr=clean_iqr,
            mad=clean_mad,
            clean_count=len(clean_sorted),
            raw_count=raw_n,
        )

    @staticmethod
    def _median_from_sorted(sorted_vals: Sequence[float]) -> float:
        """Compute exact median from a pre-sorted non-empty sequence in O(1) time."""
        n = len(sorted_vals)
        mid = n >> 1
        if n & 1:
            return sorted_vals[mid]
        return 0.5 * (sorted_vals[mid - 1] + sorted_vals[mid])

    @classmethod
    def _quartiles_from_sorted(cls, sorted_vals: Sequence[float]) -> tuple[float, float]:
        """Compute Tukey hinges (Q1, Q3) in O(1) time via index slicing bounds."""
        n = len(sorted_vals)
        if n == 1:
            return sorted_vals[0], sorted_vals[0]
        mid = n >> 1
        q1 = cls._median_via_bounds(sorted_vals, 0, mid)
        q3 = cls._median_via_bounds(sorted_vals, mid + (n & 1), n)
        return q1, q3

    @staticmethod
    def _median_via_bounds(sorted_vals: Sequence[float], start: int, end: int) -> float:
        """Zero-copy median of slice sorted_vals[start:end] in O(1) time and O(1) space."""
        length = end - start
        mid = start + (length >> 1)
        if length & 1:
            return sorted_vals[mid]
        return 0.5 * (sorted_vals[mid - 1] + sorted_vals[mid])

    @staticmethod
    def _streaming_mad_from_sorted(sorted_vals: Sequence[float], median: float) -> float:
        """
        Compute Median Absolute Deviation (MAD) in O(N) time and O(1) auxiliary space.

        Because sorted_vals is sorted ascending:
          - Elements left of the median yield deviations (median - x_i) in ascending
            order as the index moves from the center toward 0.
          - Elements right of the median yield deviations (x_i - median) in ascending
            order as the index moves from the center toward N - 1.
        Merging the two implicit streams for (N // 2) + 1 steps yields the exact median
        deviation without allocating a deviation array or sorting a second time.
        """
        n = len(sorted_vals)
        if n <= 1:
            return 0.0

        # Binary search split point: first index where sorted_vals[idx] >= median
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) >> 1
            if sorted_vals[mid] < median:
                lo = mid + 1
            else:
                hi = mid

        left = lo - 1
        right = lo
        target_idx = n >> 1
        prev_dev = 0.0
        curr_dev = 0.0

        for _ in range(target_idx + 1):
            prev_dev = curr_dev
            if left >= 0 and right < n:
                left_dev = median - sorted_vals[left]
                right_dev = sorted_vals[right] - median
                if left_dev <= right_dev:
                    curr_dev = left_dev
                    left -= 1
                else:
                    curr_dev = right_dev
                    right += 1
            elif left >= 0:
                curr_dev = median - sorted_vals[left]
                left -= 1
            else:
                curr_dev = sorted_vals[right] - median
                right += 1

        if n & 1:
            return curr_dev
        return 0.5 * (prev_dev + curr_dev)


# ============================================================================
# 3. Market Arbitrage & Anomaly Engine
# ============================================================================


class MarketArbitrageEngine:
    """
    Real-time arbitrage and scam detection engine for heterogeneous classified listings.
    """

    def __init__(
        self,
        discount_threshold_pct: float = 12.0,
        min_confidence: float = 0.55,
        min_variant_samples: int = RobustMarketModel.MIN_VARIANT_SAMPLE_SIZE,
    ) -> None:
        """
        Initialize the arbitrage engine with decision thresholds.

        Args:
            discount_threshold_pct: Minimum percentage discount below fair market price
                                    required to flag an arbitrage opportunity (default 12.0%).
            min_confidence: Minimum confidence score in [0.0, 1.0] required to trigger
                            an arbitrage signal (default 0.55).
            min_variant_samples: Minimum clean historical listings required to use a direct
                                 variant median before falling back to scaled category stats.

        Raises:
            InvalidListingError: If thresholds are outside valid mathematical domains.
        """
        if not (0.0 < discount_threshold_pct < 100.0):
            raise InvalidListingError("discount_threshold_pct must be strictly between 0 and 100.")
        if not (0.0 <= min_confidence <= 1.0):
            raise InvalidListingError("min_confidence must be between 0.0 and 1.0.")
        if min_variant_samples < 1:
            raise InvalidListingError("min_variant_samples must be at least 1.")

        self.discount_threshold_pct: Final[float] = float(discount_threshold_pct)
        self.min_confidence: Final[float] = float(min_confidence)
        self.min_variant_samples: Final[int] = int(min_variant_samples)

    def evaluate_listing(
        self,
        item: Mapping[str, Any],
        historical_items: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """
        Evaluate a candidate classified listing against historical market data.

        Time Complexity: O(N log N) where N = len(historical_items).
        Space Complexity: O(N) to store variant and normalized price buffers.

        Args:
            item: Candidate listing mapping containing at least 'title' (str) and 'price' (float|int).
            historical_items: Sequence of historical listing mappings with 'title' and 'price'.

        Returns:
            Dictionary adhering to the EvaluationResult schema.

        Raises:
            InvalidListingError: If the candidate `item` is missing required keys or has
                                 invalid types/non-finite price.
        """
        title, candidate_price = self._validate_candidate_item(item)
        if not isinstance(historical_items, Sequence) or isinstance(historical_items, (str, bytes)):
            raise InvalidListingError("historical_items must be a sequence of listing dictionaries.")

        target_profile = VariantExtractor.extract(title)
        target_mult = RobustMarketModel.get_variant_multiplier(
            target_profile.sub_model, target_profile.capacity_gb
        )

        # Single-pass extraction over historical listings
        exact_variant_prices: list[float] = []
        normalized_category_prices: list[float] = []

        for hist in historical_items:
            if not isinstance(hist, Mapping):
                continue
            h_title = hist.get("title")
            h_price = hist.get("price")
            if (
                not isinstance(h_title, str)
                or not h_title.strip()
                or not isinstance(h_price, (int, float))
                or isinstance(h_price, bool)
                or not math.isfinite(h_price)
                or h_price <= 0.0
            ):
                continue

            h_profile = VariantExtractor.extract(h_title)
            price_val = float(h_price)

            if h_profile.variant_key == target_profile.variant_key:
                exact_variant_prices.append(price_val)

            # Only include items from the same base product family (or generic fallback)
            base_match = (
                not target_profile.base_model
                or not h_profile.base_model
                or h_profile.base_model.lower() == target_profile.base_model.lower()
                or target_profile.base_model.lower() in h_profile.base_model.lower()
                or h_profile.base_model.lower() in target_profile.base_model.lower()
            )
            if base_match:
                h_mult = RobustMarketModel.get_variant_multiplier(
                    h_profile.sub_model, h_profile.capacity_gb
                )
                normalized_category_prices.append(price_val / h_mult)

        variant_stats = RobustMarketModel.compute_robust_stats(exact_variant_prices)

        used_fallback = False
        active_stats: RobustStats | None = None
        fair_market_price = 0.0

        if variant_stats is not None and variant_stats.clean_count >= self.min_variant_samples:
            active_stats = variant_stats
            fair_market_price = variant_stats.median
        else:
            used_fallback = True
            category_stats = RobustMarketModel.compute_robust_stats(normalized_category_prices)
            if category_stats is not None:
                # Project normalized category stats onto the target variant's scale
                active_stats = RobustStats(
                    median=category_stats.median * target_mult,
                    q1=category_stats.q1 * target_mult,
                    q3=category_stats.q3 * target_mult,
                    iqr=category_stats.iqr * target_mult,
                    mad=category_stats.mad * target_mult,
                    clean_count=category_stats.clean_count,
                    raw_count=category_stats.raw_count,
                )
                fair_market_price = active_stats.median

        if active_stats is None or fair_market_price <= 0.0:
            return dict(
                EvaluationResult(
                    is_arbitrage_opportunity=False,
                    is_scam_or_placeholder=candidate_price <= 0.0,
                    detected_variant=target_profile.display_name,
                    battery_health=target_profile.battery_health,
                    fair_market_price=0.0,
                    discount_pct=0.0,
                    profit_margin_tl=0,
                    confidence_score=0.0,
                    reason="Insufficient valid historical market data to establish fair price.",
                )
            )

        is_scam = self._is_scam_or_placeholder(candidate_price, active_stats)
        discount_pct = ((fair_market_price - candidate_price) / fair_market_price) * 100.0
        profit_margin_tl = int(round(fair_market_price - candidate_price))
        confidence_score = self._compute_confidence(active_stats, used_fallback)

        is_arbitrage = (
            not is_scam
            and discount_pct >= self.discount_threshold_pct
            and confidence_score >= self.min_confidence
        )

        reason = self._build_reason(
            is_arbitrage=is_arbitrage,
            is_scam=is_scam,
            used_fallback=used_fallback,
            discount_pct=discount_pct,
            confidence_score=confidence_score,
            clean_count=active_stats.clean_count,
        )

        return dict(
            EvaluationResult(
                is_arbitrage_opportunity=is_arbitrage,
                is_scam_or_placeholder=is_scam,
                detected_variant=target_profile.display_name,
                battery_health=target_profile.battery_health,
                fair_market_price=round(fair_market_price, 2),
                discount_pct=round(discount_pct, 2),
                profit_margin_tl=profit_margin_tl,
                confidence_score=round(confidence_score, 4),
                reason=reason,
            )
        )

    @staticmethod
    def _validate_candidate_item(item: Mapping[str, Any]) -> tuple[str, float]:
        if not isinstance(item, Mapping):
            raise InvalidListingError("Candidate item must be a mapping/dict.")
        if "title" not in item or "price" not in item:
            raise InvalidListingError("Candidate item must contain 'title' and 'price' keys.")

        title = item["title"]
        price = item["price"]

        if not isinstance(title, str) or not title.strip():
            raise InvalidListingError("Candidate 'title' must be a non-empty string.")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price):
            raise InvalidListingError("Candidate 'price' must be a finite numeric value.")
        if price < 0.0:
            raise InvalidListingError("Candidate 'price' cannot be negative.")

        return title, float(price)

    @staticmethod
    def _is_scam_or_placeholder(candidate_price: float, stats: RobustStats) -> bool:
        """
        Identify scam/placeholder prices (e.g. 1 TL, 100 TL, down-payments) using
        the 35% median floor and a scale-stabilized IQR lower fence.
        """
        if candidate_price <= 0.0:
            return True

        hard_floor = RobustMarketModel.SCAM_MEDIAN_FLOOR_RATIO * stats.median
        if candidate_price < hard_floor:
            return True

        # Enforce a minimum effective IQR scale of 22% of median when evaluating a single
        # candidate listing so tightly clustered historical data (IQR -> 0) does not falsely
        # classify genuine 12%-35% arbitrage discounts as scams, while still catching deep
        # down-payment anomalies via Q1 - 1.5 * IQR when market spread is normal.
        effective_iqr = max(stats.iqr, 0.22 * stats.median)
        iqr_scam_fence = stats.q1 - (RobustMarketModel.IQR_MULTIPLIER * effective_iqr)
        return candidate_price < iqr_scam_fence

    @staticmethod
    def _compute_confidence(stats: RobustStats, used_fallback: bool) -> float:
        """
        Compute confidence score in [0.0, 1.0] from clean sample size, MAD-based
        coefficient of variation, and hierarchy fallback mode.
        """
        if stats.clean_count < 2 or stats.median <= 0.0:
            return 0.0

        # Sample size saturation: n / (n + 3) -> n=4 gives 0.571, n=12 gives 0.800
        sample_factor = stats.clean_count / (stats.clean_count + 3.0)

        # Robust coefficient of variation via normal-consistent MAD
        cv_mad = (RobustMarketModel.MAD_NORMAL_SCALE * stats.mad) / stats.median
        dispersion_factor = math.exp(-2.0 * cv_mad)

        mode_penalty = 0.85 if used_fallback else 1.0
        raw_score = mode_penalty * (0.60 * sample_factor + 0.40 * dispersion_factor)
        return max(0.0, min(1.0, raw_score))

    def _build_reason(
        self,
        *,
        is_arbitrage: bool,
        is_scam: bool,
        used_fallback: bool,
        discount_pct: float,
        confidence_score: float,
        clean_count: int,
    ) -> str:
        basis = (
            f"category fallback ({clean_count} normalized samples)"
            if used_fallback
            else f"exact variant cohort ({clean_count} clean samples)"
        )
        if is_scam:
            return (
                f"Rejected as scam/placeholder: listing price is anomalously below "
                f"the robust market floor based on {basis}."
            )
        if is_arbitrage:
            return (
                f"Arbitrage opportunity detected: {discount_pct:.1f}% discount below fair "
                f"market price via {basis} (confidence {confidence_score:.2f})."
            )
        if discount_pct >= self.discount_threshold_pct and confidence_score < self.min_confidence:
            return (
                f"Discount of {discount_pct:.1f}% meets threshold, but confidence "
                f"({confidence_score:.2f} < {self.min_confidence:.2f}) is too low via {basis}."
            )
        if discount_pct > 0.0:
            return (
                f"Below-threshold discount ({discount_pct:.1f}% < "
                f"{self.discount_threshold_pct:.1f}%) relative to {basis}."
            )
        return (
            f"Listing is priced at or above fair market value "
            f"({abs(discount_pct):.1f}% premium) relative to {basis}."
        )


# ============================================================================
# 4. Exhaustive Verification & Unit Test Suite
# ============================================================================


class TestMarketArbitrageEngine(unittest.TestCase):
    """Comprehensive TDD test suite covering nominal, boundary, and error paths."""

    def setUp(self) -> None:
        self.engine = MarketArbitrageEngine(discount_threshold_pct=12.0, min_confidence=0.55)

    # --- 1. VariantExtractor Tests ---

    def test_variant_extractor_capacities_and_submodels(self) -> None:
        cases = [
            ("Temiz iPhone 15 Pro 128GB Hatasız", "iPhone 15 Pro 128GB", "Pro", 128, None),
            ("Acil iPhone 15 Pro Max 1TB %98 Pil", "iPhone 15 Pro Max 1TB", "Pro Max", 1024, 98),
            ("iPhone 15 Plus 256 GB Kutulu Pil: 89", "iPhone 15 Plus 256GB", "Plus", 256, 89),
            ("Satılık iPhone 15 512gb pil 100", "iPhone 15 512GB", "Standard", 512, 100),
            ("Galaxy S24 Ultra 1024GB Batarya %94", "Galaxy S24 Ultra 1TB", "Ultra", 1024, 94),
        ]
        for title, expected_name, expected_sub, expected_cap, expected_bat in cases:
            with self.subTest(title=title):
                profile = VariantExtractor.extract(title)
                self.assertEqual(profile.display_name, expected_name)
                self.assertEqual(profile.sub_model, expected_sub)
                self.assertEqual(profile.capacity_gb, expected_cap)
                self.assertEqual(profile.battery_health, expected_bat)

    def test_battery_health_edge_cases(self) -> None:
        # Valid battery expressions
        self.assertEqual(VariantExtractor.extract("iPhone 15 Pro %100 pil").battery_health, 100)
        self.assertEqual(VariantExtractor.extract("iPhone 15 Pro Pil 93").battery_health, 93)
        self.assertEqual(VariantExtractor.extract("iPhone 15 Pro Pil: 85").battery_health, 85)
        self.assertEqual(VariantExtractor.extract("iPhone 15 Pro 91% battery").battery_health, 91)

        # Invalid/unrelated percentages must be ignored (outside [50, 100] or unrelated to battery)
        self.assertIsNone(VariantExtractor.extract("iPhone 15 Pro %100 orijinal").battery_health)
        self.assertIsNone(VariantExtractor.extract("iPhone 15 Pro Pil 120").battery_health)
        self.assertIsNone(VariantExtractor.extract("iPhone 15 Pro Pil 15").battery_health)

    # --- 2. RobustMarketModel Statistical Tests ---

    def test_robust_stats_scam_and_outlier_rejection(self) -> None:
        # Clean core around 60,000 TL polluted by 1 TL, 100 TL, 5,000 TL scams and 250,000 TL outlier
        polluted_prices = [
            1.0,
            100.0,
            5_000.0,
            58_000.0,
            59_500.0,
            60_000.0,
            60_500.0,
            61_000.0,
            62_000.0,
            250_000.0,
        ]
        stats = RobustMarketModel.compute_robust_stats(polluted_prices)
        self.assertIsNotNone(stats)
        assert stats is not None
        self.assertEqual(stats.clean_count, 6)
        self.assertAlmostEqual(stats.median, 60_250.0)
        self.assertAlmostEqual(stats.mad, 750.0)

    def test_streaming_mad_matches_explicit_definition(self) -> None:
        # Verify O(N) O(1)-space two-pointer MAD against explicit reference calculation
        for sample in (
            [10.0, 20.0, 30.0, 40.0, 50.0],
            [12.0, 15.0, 15.0, 18.0, 22.0, 29.0, 35.0, 40.0],
            [50_000.0, 50_000.0, 50_000.0, 50_000.0],
            [42_000.0],
        ):
            sorted_sample = sorted(sample)
            med = RobustMarketModel._median_from_sorted(sorted_sample)
            explicit_devs = sorted(abs(x - med) for x in sorted_sample)
            expected_mad = RobustMarketModel._median_from_sorted(explicit_devs)
            actual_mad = RobustMarketModel._streaming_mad_from_sorted(sorted_sample, med)
            self.assertAlmostEqual(actual_mad, expected_mad, places=9)

    # --- 3. Variant Isolation & Arbitrage Detection ---

    def test_variant_isolation_prevents_false_1tb_arbitrage_on_128gb(self) -> None:
        # Historical feed has both 128GB (~60,000 TL) and 1TB (~78,000 TL) + Pro Max (~85,000 TL)
        historical = [
            {"title": "iPhone 15 Pro 128GB Temiz", "price": 59_000},
            {"title": "iPhone 15 Pro 128GB Kutulu", "price": 60_000},
            {"title": "iPhone 15 Pro 128GB Hatasız", "price": 60_000},
            {"title": "iPhone 15 Pro 128GB TR", "price": 61_000},
            {"title": "iPhone 15 Pro 1TB Kutulu", "price": 77_000},
            {"title": "iPhone 15 Pro 1TB Hatasız", "price": 78_000},
            {"title": "iPhone 15 Pro 1TB Garantili", "price": 78_500},
            {"title": "iPhone 15 Pro 1TB Sıfır Gibi", "price": 79_000},
            {"title": "iPhone 15 Pro Max 512GB", "price": 84_000},
            {"title": "iPhone 15 Pro Max 1TB", "price": 90_000},
        ]

        # A 128GB model listed at 58,000 TL would look like a >25% discount against the unsegmented
        # global median (~77,500 TL), but is only a 3.33% discount against the 128GB cohort (60,000 TL).
        candidate_normal = {"title": "iPhone 15 Pro 128GB Pil 91", "price": 58_000}
        res_normal = self.engine.evaluate_listing(candidate_normal, historical)

        self.assertFalse(res_normal["is_arbitrage_opportunity"])
        self.assertFalse(res_normal["is_scam_or_placeholder"])
        self.assertEqual(res_normal["detected_variant"], "iPhone 15 Pro 128GB")
        self.assertEqual(res_normal["battery_health"], 91)
        self.assertAlmostEqual(res_normal["fair_market_price"], 60_000.0)
        self.assertAlmostEqual(res_normal["discount_pct"], 3.33, places=2)
        self.assertEqual(res_normal["profit_margin_tl"], 2_000)

        # Now evaluate a genuine 128GB arbitrage deal at 51,000 TL (15.0% discount)
        candidate_arb = {"title": "Acil iPhone 15 Pro 128GB %93 pil", "price": 51_000}
        res_arb = self.engine.evaluate_listing(candidate_arb, historical)

        self.assertTrue(res_arb["is_arbitrage_opportunity"])
        self.assertFalse(res_arb["is_scam_or_placeholder"])
        self.assertAlmostEqual(res_arb["fair_market_price"], 60_000.0)
        self.assertAlmostEqual(res_arb["discount_pct"], 15.0, places=2)
        self.assertEqual(res_arb["profit_margin_tl"], 9_000)
        self.assertGreaterEqual(res_arb["confidence_score"], 0.55)

    # --- 4. Scam & Down-Payment Placeholder Filtering ---

    def test_scam_and_placeholder_detection(self) -> None:
        historical = [
            {"title": "iPhone 15 Pro 256GB", "price": 64_000},
            {"title": "iPhone 15 Pro 256GB", "price": 65_000},
            {"title": "iPhone 15 Pro 256GB", "price": 65_000},
            {"title": "iPhone 15 Pro 256GB", "price": 66_000},
            {"title": "iPhone 15 Pro 256GB Peşinat", "price": 100},
            {"title": "iPhone 15 Pro 256GB Taksitli", "price": 5_000},
        ]

        for scam_price in (1, 100, 5_000, 18_000):
            with self.subTest(scam_price=scam_price):
                candidate = {"title": "iPhone 15 Pro 256GB Acil", "price": scam_price}
                res = self.engine.evaluate_listing(candidate, historical)
                self.assertTrue(res["is_scam_or_placeholder"])
                self.assertFalse(res["is_arbitrage_opportunity"])
                self.assertAlmostEqual(res["fair_market_price"], 65_000.0)

    # --- 5. Sparse Variant Hierarchical Fallback ---

    def test_sparse_variant_hierarchical_fallback(self) -> None:
        # Only 2 listings for 512GB (N < 4), but rich 128GB cohort at 60,000 TL (multiplier 1.00).
        # 512GB multiplier is 1.18 -> expected fallback fair price = 60,000 * 1.18 = 70,800 TL.
        historical = [
            {"title": "iPhone 15 Pro 128GB", "price": 59_500},
            {"title": "iPhone 15 Pro 128GB", "price": 60_000},
            {"title": "iPhone 15 Pro 128GB", "price": 60_000},
            {"title": "iPhone 15 Pro 128GB", "price": 60_500},
            {"title": "iPhone 15 Pro 128GB", "price": 60_000},
            {"title": "iPhone 15 Pro 512GB", "price": 70_800},
            {"title": "iPhone 15 Pro 512GB", "price": 70_800},
        ]
        candidate = {"title": "iPhone 15 Pro 512GB Pil: 95", "price": 59_000}
        res = self.engine.evaluate_listing(candidate, historical)

        self.assertTrue(res["is_arbitrage_opportunity"])
        self.assertFalse(res["is_scam_or_placeholder"])
        self.assertEqual(res["detected_variant"], "iPhone 15 Pro 512GB")
        self.assertEqual(res["battery_health"], 95)
        self.assertAlmostEqual(res["fair_market_price"], 70_800.0, places=1)
        self.assertIn("category fallback", res["reason"])

    # --- 6. Pathological & Defensive Edge Cases ---

    def test_zero_variance_historical_cohort_allows_valid_arbitrage(self) -> None:
        # All historical items have the exact same price (IQR == 0, MAD == 0)
        historical = [{"title": "iPhone 15 Pro 256GB", "price": 65_000} for _ in range(6)]
        candidate = {"title": "iPhone 15 Pro 256GB", "price": 55_000}  # 15.38% discount
        res = self.engine.evaluate_listing(candidate, historical)

        self.assertTrue(res["is_arbitrage_opportunity"])
        self.assertFalse(res["is_scam_or_placeholder"])
        self.assertAlmostEqual(res["discount_pct"], 15.38, places=2)

    def test_empty_or_single_element_historical_data(self) -> None:
        candidate = {"title": "iPhone 15 Pro 256GB", "price": 50_000}

        # Empty historical list
        res_empty = self.engine.evaluate_listing(candidate, [])
        self.assertFalse(res_empty["is_arbitrage_opportunity"])
        self.assertEqual(res_empty["confidence_score"], 0.0)
        self.assertEqual(res_empty["fair_market_price"], 0.0)

        # Single historical item -> insufficient confidence (confidence == 0.0)
        res_single = self.engine.evaluate_listing(
            candidate, [{"title": "iPhone 15 Pro 256GB", "price": 65_000}]
        )
        self.assertFalse(res_single["is_arbitrage_opportunity"])
        self.assertEqual(res_single["confidence_score"], 0.0)

    def test_invalid_inputs_raise_explicit_domain_errors(self) -> None:
        valid_hist = [{"title": "iPhone 15 Pro 128GB", "price": 60_000}]

        with self.assertRaises(InvalidListingError):
            self.engine.evaluate_listing({}, valid_hist)

        with self.assertRaises(InvalidListingError):
            self.engine.evaluate_listing({"title": "   ", "price": 50_000}, valid_hist)

        with self.assertRaises(InvalidListingError):
            self.engine.evaluate_listing({"title": "iPhone 15 Pro", "price": -100}, valid_hist)

        with self.assertRaises(InvalidListingError):
            self.engine.evaluate_listing({"title": "iPhone 15 Pro", "price": float("nan")}, valid_hist)

        with self.assertRaises(InvalidListingError):
            self.engine.evaluate_listing({"title": "iPhone 15 Pro", "price": True}, valid_hist)

        with self.assertRaises(InvalidListingError):
            MarketArbitrageEngine(discount_threshold_pct=0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)