"""
Pro-Mode Dynamic Arbitrage & Honest Defect Engine (pro_target_engine.py).

A zero-dependency, production-grade analytical module for evaluating arbitrary
second-hand marketplace watch targets using adaptive order statistics (IQR/MAD)
and Turkish clause-predicate defect extraction.
"""

from __future__ import annotations

import math
import re
import unittest
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Iterator, Mapping, Sequence


# ============================================================================
# Domain Exceptions & Enumerations
# ============================================================================


class ProTargetEngineError(Exception):
    """Base exception for all pro_target_engine domain errors."""


class InvalidMarketInputError(ProTargetEngineError, ValueError):
    """Raised when market pricing inputs or thresholds violate domain invariants."""


class InvalidDefectInputError(ProTargetEngineError, ValueError):
    """Raised when listing text inputs violate type invariants."""


class ConditionLabel(str, Enum):
    """Binary honest condition labels for classified listings."""

    CLEAN = "Temiz"
    DEFECTIVE = "Kusurlu / İşlemli"
    SCAM_RISK = "Dolandırıcılık Riski"


# ============================================================================
# Immutable Data Contracts
# ============================================================================


@dataclass(frozen=True, slots=True)
class MarketEvaluation(Mapping[str, Any]):
    """
    Immutable output contract for AdaptiveMarketEngine.evaluate.
    Supports both attribute access (ev.is_arbitrage) and Mapping access (ev["is_arbitrage"]).
    """

    is_arbitrage: bool
    is_scam_or_placeholder: bool
    fair_market_price: float
    mad: float
    iqr: float
    dynamic_floor: float
    discount_pct: float
    profit_margin_tl: int
    confidence: float
    sample_size: int
    reason: str

    _FIELDS: Final[tuple[str, ...]] = (
        "is_arbitrage",
        "is_scam_or_placeholder",
        "fair_market_price",
        "mad",
        "iqr",
        "dynamic_floor",
        "discount_pct",
        "profit_margin_tl",
        "confidence",
        "sample_size",
        "reason",
    )

    @property
    def is_arbitrage_opportunity(self) -> bool:
        """Compatibility alias for is_arbitrage."""
        return self.is_arbitrage

    @property
    def confidence_score(self) -> float:
        """Compatibility alias for confidence."""
        return self.confidence

    def __getitem__(self, key: str) -> Any:
        if key in self._FIELDS:
            return getattr(self, key)
        if key in ("is_arbitrage_opportunity", "confidence_score"):
            return getattr(self, key)
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._FIELDS)

    def __len__(self) -> int:
        return len(self._FIELDS)


@dataclass(frozen=True, slots=True)
class DefectReport(Mapping[str, Any]):
    """
    Immutable output contract for HonestDefectExtractor.extract.
    Provides binary clarity without speculative percentage scores.
    """

    is_clean: bool
    status_label: str
    defects: list[str]
    is_scam_risk: bool
    scam_indicators: list[str]

    _FIELDS: Final[tuple[str, ...]] = (
        "is_clean",
        "status_label",
        "defects",
        "is_scam_risk",
        "scam_indicators",
    )

    @property
    def label(self) -> str:
        """Alias for status_label ('Temiz', 'Kusurlu / İşlemli', or 'Dolandırıcılık Riski')."""
        return self.status_label

    def __getitem__(self, key: str) -> Any:
        if key in self._FIELDS:
            return getattr(self, key)
        if key == "label":
            return self.status_label
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._FIELDS)

    def __len__(self) -> int:
        return len(self._FIELDS)


# ============================================================================
# 1. Dynamic Market Arbitrage Engine (AdaptiveMarketEngine)
# ============================================================================


class AdaptiveMarketEngine:
    """
    Adaptive order-statistic arbitrage and scam evaluation engine.

    Handles sparse (N = 3..5) to abundant (N >= 50) price streams in O(N log N)
    time and O(1) auxiliary space beyond the single sorted sample buffer via
    index-bounded binary search trimming and two-pointer streaming MAD.
    """

    ABSOLUTE_MIN_PRICE_FLOOR: Final[float] = 100.0
    SPARSE_MEDIAN_FLOOR_RATIO: Final[float] = 0.35
    IQR_FENCE_MULTIPLIER: Final[float] = 1.5
    MIN_IQR_COHORT_SIZE: Final[int] = 4
    MAD_NORMAL_CONSISTENCY: Final[float] = 1.4826
    MIN_EFFECTIVE_IQR_RATIO: Final[float] = 0.25

    # Log-sigmoid parameters: S_N(N) = 1 / (1 + exp(-k * (ln(N) - ln(N0))))
    # Reaches > 0.80 confidence at N = 15 while allowing low-variance N = 3 cohorts >= 0.50.
    _SIGMOID_K: Final[float] = 1.35
    _SIGMOID_LN_N0: Final[float] = math.log(2.5)

    def evaluate(
        self,
        candidate_price: float,
        historical_prices: Sequence[float],
        discount_threshold_pct: float = 12.0,
        min_profit_tl: int = 3000,
        min_confidence: float = 0.50,
    ) -> MarketEvaluation:
        """
        Evaluate a candidate listing price against a historical price stream.

        Time Complexity: O(N log N) for initial Timsort; O(log N) for index-bounded
                         outlier trimming; O(N) for two-pointer streaming MAD.
        Space Complexity: O(1) auxiliary heap space beyond the single sorted buffer of size N.

        Args:
            candidate_price: Listing price under evaluation (must be finite and >= 0).
            historical_prices: Sequence of historical listing prices for the target.
            discount_threshold_pct: Minimum percentage discount required (default 12.0).
            min_profit_tl: Minimum absolute TL profit required (default 3000).
            min_confidence: Minimum confidence score in [0.0, 1.0] required (default 0.50).

        Returns:
            MarketEvaluation dataclass instance.

        Raises:
            InvalidMarketInputError: If candidate_price or thresholds are invalid.
        """
        self._validate_parameters(
            candidate_price=candidate_price,
            historical_prices=historical_prices,
            discount_threshold_pct=discount_threshold_pct,
            min_profit_tl=min_profit_tl,
            min_confidence=min_confidence,
        )

        cand = float(candidate_price)

        # Single O(N) sanitization allocation followed by in-place O(N log N) Timsort.
        sorted_buf: list[float] = [
            float(p)
            for p in historical_prices
            if isinstance(p, (int, float))
            and not isinstance(p, bool)
            and math.isfinite(p)
            and p > 0.0
        ]
        sorted_buf.sort()

        raw_n = len(sorted_buf)
        if raw_n == 0:
            return MarketEvaluation(
                is_arbitrage=False,
                is_scam_or_placeholder=cand <= self.ABSOLUTE_MIN_PRICE_FLOOR,
                fair_market_price=0.0,
                mad=0.0,
                iqr=0.0,
                dynamic_floor=self.ABSOLUTE_MIN_PRICE_FLOOR,
                discount_pct=0.0,
                profit_margin_tl=0,
                confidence=0.0,
                sample_size=0,
                reason="No valid historical prices available.",
            )

        # Stage 1: Coarse placeholder pre-trim in O(log N) via binary search bounds [lo, hi)
        # Prevents multiple 1 TL / 50 TL ads in historical_prices from corrupting Q1.
        raw_med = self._median_in_range(sorted_buf, 0, raw_n)
        coarse_floor = (
            max(self.ABSOLUTE_MIN_PRICE_FLOOR, self.SPARSE_MEDIAN_FLOOR_RATIO * raw_med)
            if raw_med > self.ABSOLUTE_MIN_PRICE_FLOOR * 2.0
            else self.SPARSE_MEDIAN_FLOOR_RATIO * raw_med
        )
        lo = self._bisect_left(sorted_buf, coarse_floor, 0, raw_n)
        hi = raw_n
        if lo >= hi:
            lo, hi = 0, raw_n

        # Stage 2: Adaptive IQR trimming when cohort size >= 4 (O(log N) time, O(1) space)
        plausible_n = hi - lo
        med_plausible = self._median_in_range(sorted_buf, lo, hi)

        if plausible_n >= self.MIN_IQR_COHORT_SIZE:
            q1_p, q3_p = self._quartiles_in_range(sorted_buf, lo, hi)
            iqr_p = q3_p - q1_p
            hist_low_fence = max(
                self.ABSOLUTE_MIN_PRICE_FLOOR,
                self.SPARSE_MEDIAN_FLOOR_RATIO * med_plausible,
                q1_p - self.IQR_FENCE_MULTIPLIER * iqr_p,
            )
            hist_high_fence = (
                q3_p + self.IQR_FENCE_MULTIPLIER * iqr_p
                if iqr_p > 0.0
                else 1.85 * med_plausible
            )
            new_lo = self._bisect_left(sorted_buf, hist_low_fence, lo, hi)
            new_hi = self._bisect_right(sorted_buf, hist_high_fence, new_lo, hi)
            if new_lo < new_hi:
                lo, hi = new_lo, new_hi
        else:
            # For sparse cohorts (N < 4), still trim extreme upper troll outliers (> 1.85x median)
            sparse_high_fence = 1.85 * med_plausible
            new_hi = self._bisect_right(sorted_buf, sparse_high_fence, lo, hi)
            if lo < new_hi:
                hi = new_hi

        # Stage 3: Final clean statistics over [lo, hi) in O(N) time and O(1) auxiliary space
        clean_n = hi - lo
        fair_price = self._median_in_range(sorted_buf, lo, hi)
        clean_q1, clean_q3 = self._quartiles_in_range(sorted_buf, lo, hi)
        clean_iqr = clean_q3 - clean_q1
        clean_mad = self._streaming_mad_in_range(sorted_buf, lo, hi, fair_price)

        # Dynamic floor calculation:
        # - N >= 4: max(100.0, Q1 - 1.5 * IQR), with effective IQR floor stabilization
        #   so zero-variance cohorts (IQR == 0) do not reject valid 12%-35% discounts as scams.
        # - N < 4:  max(100.0, 0.35 * median)
        if clean_n >= self.MIN_IQR_COHORT_SIZE:
            effective_iqr = max(clean_iqr, self.MIN_EFFECTIVE_IQR_RATIO * fair_price)
            dynamic_floor = max(
                self.ABSOLUTE_MIN_PRICE_FLOOR,
                self.SPARSE_MEDIAN_FLOOR_RATIO * fair_price,
                clean_q1 - self.IQR_FENCE_MULTIPLIER * effective_iqr,
            )
        else:
            dynamic_floor = max(
                self.ABSOLUTE_MIN_PRICE_FLOOR,
                self.SPARSE_MEDIAN_FLOOR_RATIO * fair_price,
            )

        is_scam = (
            cand <= 0.0
            or (fair_price > self.ABSOLUTE_MIN_PRICE_FLOOR * 2.0 and cand <= self.ABSOLUTE_MIN_PRICE_FLOOR)
            or cand < dynamic_floor
        )

        discount_pct = ((fair_price - cand) / fair_price) * 100.0
        profit_margin_tl = int(round(fair_price - cand))
        confidence = self._compute_confidence(clean_n, fair_price, clean_mad)

        is_arbitrage = (
            not is_scam
            and discount_pct >= discount_threshold_pct
            and profit_margin_tl >= min_profit_tl
            and confidence >= min_confidence
        )

        reason = self._build_reason(
            is_arbitrage=is_arbitrage,
            is_scam=is_scam,
            discount_pct=discount_pct,
            discount_threshold_pct=discount_threshold_pct,
            profit_margin_tl=profit_margin_tl,
            min_profit_tl=min_profit_tl,
            confidence=confidence,
            min_confidence=min_confidence,
            sample_size=clean_n,
        )

        return MarketEvaluation(
            is_arbitrage=is_arbitrage,
            is_scam_or_placeholder=is_scam,
            fair_market_price=round(fair_price, 2),
            mad=round(clean_mad, 2),
            iqr=round(clean_iqr, 2),
            dynamic_floor=round(dynamic_floor, 2),
            discount_pct=round(discount_pct, 2),
            profit_margin_tl=profit_margin_tl,
            confidence=round(confidence, 4),
            sample_size=clean_n,
            reason=reason,
        )

    @classmethod
    def _compute_confidence(cls, n: int, median: float, mad: float) -> float:
        """
        Compute dynamic confidence score in [0.0, 1.0] using log-sigmoid sample
        saturation (reaching > 0.80 at N = 15) and MAD stability.
        """
        if n < 2 or median <= 0.0:
            return 0.0

        # Log-sigmoid sample saturation: 1 / (1 + exp(-k * (ln(N) - ln(N0))))
        z = cls._SIGMOID_K * (math.log(n) - cls._SIGMOID_LN_N0)
        sample_saturation = 1.0 / (1.0 + math.exp(-z))

        # Normal-consistent robust coefficient of variation
        cv_mad = (cls.MAD_NORMAL_CONSISTENCY * mad) / median
        mad_stability = math.exp(-1.8 * cv_mad)

        score = sample_saturation * (0.35 + 0.65 * mad_stability)
        return max(0.0, min(1.0, score))

    @staticmethod
    def _bisect_left(arr: Sequence[float], target: float, lo: int, hi: int) -> int:
        """O(log N) binary search for the first index in [lo, hi) where arr[idx] >= target."""
        while lo < hi:
            mid = (lo + hi) >> 1
            if arr[mid] < target:
                lo = mid + 1
            else:
                hi = mid
        return lo

    @staticmethod
    def _bisect_right(arr: Sequence[float], target: float, lo: int, hi: int) -> int:
        """O(log N) binary search for the first index in [lo, hi) where arr[idx] > target."""
        while lo < hi:
            mid = (lo + hi) >> 1
            if arr[mid] <= target:
                lo = mid + 1
            else:
                hi = mid
        return lo

    @staticmethod
    def _median_in_range(arr: Sequence[float], lo: int, hi: int) -> float:
        """O(1) time, O(1) space exact median over sorted slice arr[lo:hi]."""
        length = hi - lo
        mid = lo + (length >> 1)
        if length & 1:
            return arr[mid]
        return 0.5 * (arr[mid - 1] + arr[mid])

    @classmethod
    def _quartiles_in_range(cls, arr: Sequence[float], lo: int, hi: int) -> tuple[float, float]:
        """O(1) time, O(1) space Tukey hinges (Q1, Q3) over sorted slice arr[lo:hi]."""
        length = hi - lo
        if length <= 1:
            return arr[lo], arr[lo]
        mid = lo + (length >> 1)
        q1 = cls._median_in_range(arr, lo, mid)
        q3 = cls._median_in_range(arr, mid + (length & 1), hi)
        return q1, q3

    @classmethod
    def _streaming_mad_in_range(
        cls, arr: Sequence[float], lo: int, hi: int, median: float
    ) -> float:
        """
        Compute Median Absolute Deviation (MAD) over arr[lo:hi] in O(N) time
        and O(1) auxiliary space via two-pointer outward merge from the median.
        """
        length = hi - lo
        if length <= 1:
            return 0.0

        split = cls._bisect_left(arr, median, lo, hi)
        left = split - 1
        right = split
        steps = (length >> 1) + 1
        prev_dev = 0.0
        curr_dev = 0.0

        for _ in range(steps):
            prev_dev = curr_dev
            if left >= lo and right < hi:
                left_dev = median - arr[left]
                right_dev = arr[right] - median
                if left_dev <= right_dev:
                    curr_dev = left_dev
                    left -= 1
                else:
                    curr_dev = right_dev
                    right += 1
            elif left >= lo:
                curr_dev = median - arr[left]
                left -= 1
            else:
                curr_dev = arr[right] - median
                right += 1

        if length & 1:
            return curr_dev
        return 0.5 * (prev_dev + curr_dev)

    @staticmethod
    def _validate_parameters(
        *,
        candidate_price: float,
        historical_prices: Sequence[float],
        discount_threshold_pct: float,
        min_profit_tl: int,
        min_confidence: float,
    ) -> None:
        if (
            isinstance(candidate_price, bool)
            or not isinstance(candidate_price, (int, float))
            or not math.isfinite(candidate_price)
            or candidate_price < 0.0
        ):
            raise InvalidMarketInputError(
                "candidate_price must be a finite non-negative number."
            )
        if not isinstance(historical_prices, Sequence) or isinstance(
            historical_prices, (str, bytes)
        ):
            raise InvalidMarketInputError(
                "historical_prices must be a sequence of numeric values."
            )
        if (
            isinstance(discount_threshold_pct, bool)
            or not isinstance(discount_threshold_pct, (int, float))
            or not (0.0 < discount_threshold_pct < 100.0)
        ):
            raise InvalidMarketInputError(
                "discount_threshold_pct must be strictly between 0.0 and 100.0."
            )
        if (
            isinstance(min_profit_tl, bool)
            or not isinstance(min_profit_tl, (int, float))
            or not math.isfinite(min_profit_tl)
            or min_profit_tl < 0
        ):
            raise InvalidMarketInputError("min_profit_tl must be a non-negative integer.")
        if (
            isinstance(min_confidence, bool)
            or not isinstance(min_confidence, (int, float))
            or not (0.0 <= min_confidence <= 1.0)
        ):
            raise InvalidMarketInputError("min_confidence must be in [0.0, 1.0].")

    @staticmethod
    def _build_reason(
        *,
        is_arbitrage: bool,
        is_scam: bool,
        discount_pct: float,
        discount_threshold_pct: float,
        profit_margin_tl: int,
        min_profit_tl: int,
        confidence: float,
        min_confidence: float,
        sample_size: int,
    ) -> str:
        if is_scam:
            return (
                f"Rejected as scam/placeholder: candidate price falls below the "
                f"adaptive market floor (N={sample_size})."
            )
        if is_arbitrage:
            return (
                f"Arbitrage confirmed: {discount_pct:.1f}% discount (+{profit_margin_tl} TL "
                f"margin) with {confidence:.2f} confidence (N={sample_size})."
            )
        if discount_pct < discount_threshold_pct:
            return (
                f"Insufficient discount ({discount_pct:.1f}% < {discount_threshold_pct:.1f}% "
                f"threshold)."
            )
        if profit_margin_tl < min_profit_tl:
            return (
                f"Insufficient profit margin ({profit_margin_tl} TL < {min_profit_tl} TL "
                f"minimum)."
            )
        return (
            f"Confidence score ({confidence:.2f}) below required threshold "
            f"({min_confidence:.2f}) for N={sample_size}."
        )


# ============================================================================
# 2. Honest Defect Extractor (HonestDefectExtractor)
# ============================================================================


@dataclass(frozen=True, slots=True)
class _FlawPatternRule:
    """Internal pre-compiled rule mapping Turkish defect patterns to canonical tags."""

    tag: str
    pattern: re.Pattern[str]
    uses_clause_negation: bool


class HonestDefectExtractor:
    """
    Turkish-aware semantic defect and scam risk extractor.

    Avoids speculative percentage scores in favor of deterministic, honest binary
    clarity:
      - Extracts human-readable Turkish defect tags when flaws/repairs exist.
      - Flags `is_clean = True` with label `"Temiz"` when no defects or scam signals exist.
      - Distinguishes coordinate comma lists ("Kasada ezik, çizik yoktur" -> CLEAN)
        from clause stops using First-Governing-Predicate resolution in O(1) window space.

    Time Complexity: O(L) where L is the character length of the input text.
    Space Complexity: O(1) auxiliary space beyond the O(L) normalized string.
    """

    # C-level character translation table for exact Turkish lowercase folding.
    _TR_LOWER_MAP: Final[dict[int, str]] = str.maketrans(
        {
            "İ": "i",
            "I": "ı",
            "Ğ": "ğ",
            "Ü": "ü",
            "Ş": "ş",
            "Ö": "ö",
            "Ç": "ç",
            "\u0307": "",
        }
    )

    # Hard clause boundary stops. Commas (,) are intentionally excluded so coordinate
    # noun lists ("ezik, vuruk, çizik yoktur") remain bound to their shared predicate.
    _HARD_CLAUSE_STOP_RE: Final[re.Pattern[str]] = re.compile(
        r"[.;!?\n\r]+|\b(?:ama|fakat|lakin|ancak|yaln[ıi]z|haric[iı](?:nde)?|hari[cç]|ve|ayr[ıi]ca)\b"
    )

    # Governing negative predicates inside a clause.
    _NEGATION_PREDICATE_RE: Final[re.Pattern[str]] = re.compile(
        r"\b(?:yok|yoktur|de[gğ][iı]l|de[gğ][iı]ld[iı]r|bulunmaz|bulunmuyor|"
        r"bulunmamaktad[ıi]r|olmay[ıi]p|g[oö]rmed[iı]|g[oö]rmem[iı][sş](?:t[iı]r)?|"
        r"de[gğ][iı][sş]med[iı]|de[gğ][iı][sş]mem[iı][sş](?:t[iı]r)?|mevcut\s+de[gğ][iı]l)\b"
    )

    # Governing affirmative predicates inside a clause (e.g. "ezik var, çizik yok").
    _AFFIRMATIVE_PREDICATE_RE: Final[re.Pattern[str]] = re.compile(
        r"\b(?:var|vard[ıi]r|mevcut(?:tur)?(?!\s*de[gğ][iı]l)|bulunuyor|"
        r"bulunmaktad[ıi]r|oldu|olu[sş]tu)\b"
    )

    # Pre-nominal privative modifiers within the same clause.
    _PRE_PRIVATIVE_RE: Final[re.Pattern[str]] = re.compile(
        r"\b(?:hi[cç]b[iı]r|asla|kes[iı]nl[iı]kle)\b"
    )

    # Pre-compiled Turkish defect extraction rules.
    _FLAW_RULES: Final[tuple[_FlawPatternRule, ...]] = (
        _FlawPatternRule(
            tag="Ekran Değişmiş",
            pattern=re.compile(
                r"\b(?:"
                r"ekran[ıi]?\s+(?:serv[iı]ste\s+|d[ıi][sş]ar[ıi]da\s+|taraf[ıi]mca\s+|yeni\s+)?"
                r"(?:de[gğ][iı][sş](?:t[iı]|m[iı][sş](?:t[iı]r)?|[iı]m[iı]\s+yap[ıi]ld[ıi])|"
                r"yen[iı]lend[iı]|tak[ıi]ld[ıi])"
                r"|(?:yan\s+sanayi|gx|oled\s+revize|[cç][ıi]kma)\s+ekran"
                r"|ekran\s+(?:yan\s+sanayi|de[gğ][iı][sş]en)"
                r")\b"
            ),
            uses_clause_negation=True,
        ),
        _FlawPatternRule(
            tag="True Tone Yok",
            pattern=re.compile(
                r"\btrue\s*tone\s+(?:[cç]al[ıi][sş]m[ıi]yor|yok(?:tur)?|pas[iı]f|"
                r"akt[iı]f\s+de[gğ][iı]l|kay[ıi]p|ar[ıi]zal[ıi]|bozuk|devre\s+d[ıi][sş][ıi])\b"
            ),
            uses_clause_negation=False,
        ),
        _FlawPatternRule(
            tag="Face ID Arızalı",
            pattern=re.compile(
                r"\bface\s*[iı]d\s+(?:ar[ıi]zal[ıi]|[cç]al[ıi][sş]m[ıi]yor|yok(?:tur)?|"
                r"pas[iı]f|akt[iı]f\s+de[gğ][iı]l|bozuk|okumuyor|devre\s+d[ıi][sş][ıi])\b"
            ),
            uses_clause_negation=False,
        ),
        _FlawPatternRule(
            tag="Pil Değişmiş / Servis",
            pattern=re.compile(
                r"\b(?:"
                r"(?:p[iı]l[iı]?|batarya(?:s[ıi])?)\s+"
                r"(?:serv[iı]ste\s+|yeni\s+|taraf[ıi]mca\s+)?"
                r"(?:de[gğ][iı][sş](?:t[iı]|m[iı][sş](?:t[iı]r)?|[iı]m[iı]\s+yap[ıi]ld[ıi])|"
                r"yen[iı]lend[iı]|tak[ıi]ld[ıi]|serv[iı]s(?:te)?\s+uyar[ıi]s[ıi]|serv[iı]s\b)"
                r"|(?:deji|rova|baseus|g[uü][cç]lend[iı]r[iı]lm[iı][sş]|yan\s+sanayi)\s+"
                r"(?:p[iı]l|batarya|tak[ıi]ld[ıi])"
                r"|deji\s+tak[ıi]ld[ıi]"
                r")\b"
            ),
            uses_clause_negation=True,
        ),
        _FlawPatternRule(
            tag="Kasada Ezik/Darbe",
            pattern=re.compile(
                r"\b(?:ez[iı]k(?:ler)?|vuruk(?:lar)?|darbe(?:l[iı])?|g[oö][cç][uü]k)\b"
            ),
            uses_clause_negation=True,
        ),
        _FlawPatternRule(
            tag="Çizik Mevcut",
            pattern=re.compile(
                r"\b(?:k[ıi]lcal\s+)?[cç][iı]z[iı]k(?:ler)?(?!\s*s[iı]z)\b"
            ),
            uses_clause_negation=True,
        ),
        _FlawPatternRule(
            tag="Kırık / Çatlak",
            pattern=re.compile(
                r"\b(?:[cç]atlak(?:lar)?|k[ıi]r[ıi]k(?:lar)?)\b"
            ),
            uses_clause_negation=True,
        ),
        _FlawPatternRule(
            tag="Parmak İzi Arızalı",
            pattern=re.compile(
                r"\b(?:parmak\s+[iı]z[iı]|touch\s*id)\s+"
                r"(?:okumuyor|[cç]al[ıi][sş]m[ıi]yor|ar[ıi]zal[ıi]|bozuk|pas[iı]f|"
                r"akt[iı]f\s+de[gğ][iı]l|yok(?:tur)?)\b"
            ),
            uses_clause_negation=False,
        ),
        _FlawPatternRule(
            tag="Anakart / Tamir İşlemli",
            pattern=re.compile(
                r"\b(?:anakart[ıi]?\s+(?:tam[iı]rl[iı]|[iı][sş]lem\s+g[oö]rm[uü][sş]|onard[ıi])|"
                r"entegre\s+de[gğ][iı][sş]t[iı]|tam[iı]r\s+g[oö]rd[uü])\b"
            ),
            uses_clause_negation=True,
        ),
        _FlawPatternRule(
            tag="Ekran Lekesi / Ghost Screen",
            pattern=re.compile(
                r"\b(?:hayalet\s+ekran|ghost\s+screen|[oö]l[uü]\s+p[iı]ksel|"
                r"ekranda\s+leke|m[uü]rekkep\s+akmas[ıi])\b"
            ),
            uses_clause_negation=True,
        ),
    )

    # Pre-compiled scam risk indicator rules.
    _SCAM_RULES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
        (
            "Kapora Talebi",
            re.compile(
                r"\b(?:kapora\s+(?:[sş]art(?:t[ıi]r)?|zorunlu|[iı]st[iı]yorum|al[ıi]n[ıi]r)|"
                r"kaporas[ıi]z\s+(?:g[oö]ndermem|ay[ıi]rmam|sat[ıi][sş]\s+yok|i[sş]lem\s+yapmam)|"
                r"[oö]nce\s+kapora)\b"
            ),
        ),
        (
            "Önce Havale Şartı",
            re.compile(
                r"\b(?:[oö]nce\s+(?:havale|eft|[uü]cret|[oö]deme)(?:\s+sonra\s+kargo)?|"
                r"havale\s+sonras[ıi]\s+kargo|[uü]cret\s+pe[sş][iı]n\s+kargo)\b"
            ),
        ),
        (
            "Elden Teslim Yok / Sadece Kargo",
            re.compile(
                r"\b(?:elden\s+(?:tesl[iı]m(?:at)?|sat[ıi][sş][ıi]?m?)\s+"
                r"(?:yok(?:tur)?|olmaz|yap[ıi]lmaz|m[uü]mk[uü]n\s+de[gğ][iı]l)|"
                r"sadece\s+kargo(?:\s+[iı]le\s+g[oö]nder[iı]m)?)\b"
            ),
        ),
    )

    @classmethod
    def turkish_lower(cls, text: str) -> str:
        """
        Fold string to lowercase preserving Turkish dotted/dotless I semantics
        ('İ' -> 'i', 'I' -> 'ı', 'Ş' -> 'ş', 'Ğ' -> 'ğ', 'Ü' -> 'ü', 'Ö' -> 'ö', 'Ç' -> 'ç')
        in O(L) time via C-level str.translate.
        """
        if not isinstance(text, str):
            raise InvalidDefectInputError("Input text must be a string.")
        return text.translate(cls._TR_LOWER_MAP).lower()

    def extract(self, title: str, description: str = "") -> DefectReport:
        """
        Extract honest defect tags, scam risk flags, and binary cleanliness status
        from a listing's title and description.

        Time Complexity: O(L) where L = len(title) + len(description).
        Space Complexity: O(1) auxiliary memory beyond the O(L) folded text buffer.

        Args:
            title: Listing title or primary text.
            description: Optional listing description body.

        Returns:
            Immutable DefectReport instance.

        Raises:
            InvalidDefectInputError: If inputs are not strings or combined text is empty.
        """
        if not isinstance(title, str) or not isinstance(description, str):
            raise InvalidDefectInputError("Both 'title' and 'description' must be strings.")

        combined = f"{title} . {description}".strip(" .") if description.strip() else title.strip()
        if not combined:
            raise InvalidDefectInputError("Listing text cannot be empty or whitespace only.")

        normalized = self.turkish_lower(combined)

        defects: list[str] = []
        seen_defects: set[str] = set()

        for rule in self._FLAW_RULES:
            has_unnegated_flaw = False
            for match in rule.pattern.finditer(normalized):
                if rule.uses_clause_negation and self._is_negated_in_clause(
                    normalized, match.start(), match.end()
                ):
                    continue
                has_unnegated_flaw = True
                break

            if has_unnegated_flaw and rule.tag not in seen_defects:
                seen_defects.add(rule.tag)
                defects.append(rule.tag)

        scam_indicators: list[str] = []
        for scam_label, scam_pattern in self._SCAM_RULES:
            if scam_pattern.search(normalized):
                scam_indicators.append(scam_label)

        is_scam_risk = len(scam_indicators) > 0
        is_clean = (len(defects) == 0) and (not is_scam_risk)

        if is_scam_risk:
            status_label = ConditionLabel.SCAM_RISK.value
        elif is_clean:
            status_label = ConditionLabel.CLEAN.value
        else:
            status_label = ConditionLabel.DEFECTIVE.value

        return DefectReport(
            is_clean=is_clean,
            status_label=status_label,
            defects=defects,
            is_scam_risk=is_scam_risk,
            scam_indicators=scam_indicators,
        )

    # Alias for callers expecting .inspect(...)
    inspect = extract

    @classmethod
    def _is_negated_in_clause(cls, text: str, start: int, end: int) -> bool:
        """
        First-Governing-Predicate negation resolution in O(1) time and O(1) space.

        Slices the forward clause window up to the first hard clause stop (excluding
        commas so coordinate lists like 'Kasada ezik, çizik yoktur' stay unified) and
        compares the position of the first negative predicate against the first
        affirmative predicate.
        """
        post_window = text[end : min(len(text), end + 65)]
        stop_match = cls._HARD_CLAUSE_STOP_RE.search(post_window)
        if stop_match is not None:
            post_window = post_window[: stop_match.start()]

        neg_match = cls._NEGATION_PREDICATE_RE.search(post_window)
        aff_match = cls._AFFIRMATIVE_PREDICATE_RE.search(post_window)

        if neg_match is not None:
            if aff_match is None or neg_match.start() < aff_match.start():
                return True
            return False

        if aff_match is not None:
            return False

        # Check pre-nominal privative modifier within the same clause
        pre_window = text[max(0, start - 25) : start]
        for prev_stop in cls._HARD_CLAUSE_STOP_RE.finditer(pre_window):
            pre_window = pre_window[prev_stop.end() :]

        return bool(cls._PRE_PRIVATIVE_RE.search(pre_window))


# ============================================================================
# 3. Exhaustive Verification & Unit Test Suite
# ============================================================================


class TestProTargetEngine(unittest.TestCase):
    """Exhaustive TDD verification suite for AdaptiveMarketEngine and HonestDefectExtractor."""

    def setUp(self) -> None:
        self.market_engine = AdaptiveMarketEngine()
        self.defect_extractor = HonestDefectExtractor()

    # --- 1. Sparse Historical Cohorts (N = 3 and N = 5) ---

    def test_sparse_cohorts_n3_and_n5(self) -> None:
        # N = 3 cohort: dynamic floor uses 0.35 * median
        hist_n3 = [30_000.0, 29_500.0, 30_500.0]
        eval_n3 = self.market_engine.evaluate(
            candidate_price=25_000.0,
            historical_prices=hist_n3,
            discount_threshold_pct=12.0,
            min_profit_tl=3_000,
            min_confidence=0.50,
        )
        self.assertTrue(eval_n3.is_arbitrage)
        self.assertFalse(eval_n3.is_scam_or_placeholder)
        self.assertEqual(eval_n3.sample_size, 3)
        self.assertAlmostEqual(eval_n3.fair_market_price, 30_000.0)
        self.assertAlmostEqual(eval_n3.dynamic_floor, 10_500.0)  # 0.35 * 30,000
        self.assertAlmostEqual(eval_n3.discount_pct, 16.67, places=2)
        self.assertEqual(eval_n3.profit_margin_tl, 5_000)
        self.assertGreaterEqual(eval_n3.confidence, 0.50)

        # N = 5 cohort
        hist_n5 = [29_000.0, 29_500.0, 30_000.0, 30_500.0, 31_000.0]
        eval_n5 = self.market_engine.evaluate(
            candidate_price=25_500.0,
            historical_prices=hist_n5,
            discount_threshold_pct=12.0,
            min_profit_tl=3_000,
            min_confidence=0.50,
        )
        self.assertTrue(eval_n5.is_arbitrage)
        self.assertFalse(eval_n5.is_scam_or_placeholder)
        self.assertEqual(eval_n5.sample_size, 5)
        self.assertAlmostEqual(eval_n5.fair_market_price, 30_000.0)
        self.assertEqual(eval_n5.profit_margin_tl, 4_500)
        self.assertGreater(eval_n5.confidence, eval_n3.confidence)

        # Abundant N = 15 cohort must exceed 0.80 confidence per specification
        hist_n15 = [30_000.0 + (i % 3 - 1) * 500.0 for i in range(15)]
        eval_n15 = self.market_engine.evaluate(
            candidate_price=25_000.0,
            historical_prices=hist_n15,
        )
        self.assertGreater(eval_n15.confidence, 0.80)
        self.assertTrue(eval_n15.is_arbitrage)

    # --- 2. Outlier & Scam Rejection (1 TL Placeholders & Down Payments) ---

    def test_outlier_and_scam_rejection(self) -> None:
        # Polluted stream containing 1 TL, 50 TL placeholders and a 250,000 TL troll listing
        polluted_hist = [
            1.0,
            50.0,
            38_000.0,
            39_500.0,
            40_000.0,
            40_000.0,
            40_500.0,
            42_000.0,
            250_000.0,
        ]

        for scam_candidate in (1.0, 99.0, 100.0, 4_500.0):
            with self.subTest(scam_candidate=scam_candidate):
                ev = self.market_engine.evaluate(
                    candidate_price=scam_candidate,
                    historical_prices=polluted_hist,
                )
                self.assertTrue(ev.is_scam_or_placeholder)
                self.assertFalse(ev.is_arbitrage)
                self.assertAlmostEqual(ev.fair_market_price, 40_000.0)
                self.assertEqual(ev.sample_size, 6)

    # --- 3. Zero-Variance Cohort Robustness ---

    def test_zero_variance_cohort_allows_valid_arbitrage_and_blocks_scams(self) -> None:
        zero_var_hist = [50_000.0] * 8

        # Valid 16% discount (+8,000 TL profit) on a zero-variance cohort
        ev_valid = self.market_engine.evaluate(
            candidate_price=42_000.0,
            historical_prices=zero_var_hist,
        )
        self.assertEqual(ev_valid.mad, 0.0)
        self.assertEqual(ev_valid.iqr, 0.0)
        self.assertFalse(ev_valid.is_scam_or_placeholder)
        self.assertTrue(ev_valid.is_arbitrage)
        self.assertAlmostEqual(ev_valid.discount_pct, 16.0)
        self.assertEqual(ev_valid.profit_margin_tl, 8_000)

        # 1 TL placeholder on the same zero-variance cohort must still be rejected
        ev_scam = self.market_engine.evaluate(
            candidate_price=1.0,
            historical_prices=zero_var_hist,
        )
        self.assertTrue(ev_scam.is_scam_or_placeholder)
        self.assertFalse(ev_scam.is_arbitrage)

    # --- 4. Arbitrage Decision Boundary Conditions ---

    def test_arbitrage_profit_and_discount_boundaries(self) -> None:
        # Low-priced item (e.g. 10,000 TL console): 15% discount is only 1,500 TL (< 3,000 TL min_profit_tl)
        hist_console = [10_000.0, 9_800.0, 10_200.0, 10_000.0, 10_000.0]
        ev_low_profit = self.market_engine.evaluate(
            candidate_price=8_500.0,
            historical_prices=hist_console,
            discount_threshold_pct=12.0,
            min_profit_tl=3_000,
        )
        self.assertFalse(ev_low_profit.is_arbitrage)
        self.assertAlmostEqual(ev_low_profit.discount_pct, 15.0)
        self.assertEqual(ev_low_profit.profit_margin_tl, 1_500)
        self.assertIn("profit margin", ev_low_profit.reason.lower())

    # --- 5. HonestDefectExtractor: Turkish Negation & Coordinate Comma Lists ---

    def test_honest_defect_extractor_clean_vs_flawed(self) -> None:
        # Coordinate comma list with shared trailing negation -> CLEAN ("Temiz")
        for clean_text in (
            "Kasada ezik, çizik yoktur",
            "Ekran değişmedi, tamamen orijinal ekran",
            "True tone aktif, Face ID sorunsuz, kutu fatura tam",
            "Ezik, vuruk, kırık, kılcal çizik dahi kesinlikle yoktur",
        ):
            with self.subTest(clean_text=clean_text):
                rep = self.defect_extractor.extract(clean_text)
                self.assertTrue(rep.is_clean)
                self.assertEqual(rep.status_label, "Temiz")
                self.assertEqual(rep.defects, [])
                self.assertFalse(rep.is_scam_risk)

        # Individual flaw detections matching specification tags
        flaw_cases = [
            ("iPhone 13 Ekran değişti diğer her şey aktif", ["Ekran Değişmiş"]),
            ("Kasada ezik var kullanıma engel değil", ["Kasada Ezik/Darbe"]),
            ("True tone çalışmıyor, ekran orijinal", ["True Tone Yok"]),
            ("True tone yok fiyat uygun", ["True Tone Yok"]),
            ("Geçen hafta pil değişti, deji takıldı", ["Pil Değişmiş / Servis"]),
            ("Pil servis uyarısı veriyor", ["Pil Değişmiş / Servis"]),
            ("Cihazda Face ID arızalı", ["Face ID Arızalı"]),
            ("Ekran değişti ve True Tone yok", ["Ekran Değişmiş", "True Tone Yok"]),
        ]
        for text, expected_tags in flaw_cases:
            with self.subTest(text=text):
                rep = self.defect_extractor.extract(text)
                self.assertFalse(rep.is_clean)
                self.assertEqual(rep.defects, expected_tags)
                self.assertEqual(rep.status_label, "Kusurlu / İşlemli")

    def test_mixed_comma_clauses_affirmative_and_negative(self) -> None:
        # First noun affirmed ("ezik var"), second noun negated ("çizik yok")
        rep1 = self.defect_extractor.extract("Kasada ezik var, ekranda çizik yok")
        self.assertFalse(rep1.is_clean)
        self.assertEqual(rep1.defects, ["Kasada Ezik/Darbe"])

        # First noun negated ("çizik yok"), second noun affirmed ("ezik var")
        rep2 = self.defect_extractor.extract("Ekranda çizik yok, kasada ezik var")
        self.assertFalse(rep2.is_clean)
        self.assertEqual(rep2.defects, ["Kasada Ezik/Darbe"])

    def test_scam_risk_extraction(self) -> None:
        for scam_text in (
            "Ciddi alıcılar yazsın kapora şart",
            "Şehir dışına önce havale sonra kargo",
            "Yoğunluktan dolayı elden teslim yok, sadece kargo",
        ):
            with self.subTest(scam_text=scam_text):
                rep = self.defect_extractor.extract(scam_text)
                self.assertTrue(rep.is_scam_risk)
                self.assertFalse(rep.is_clean)
                self.assertEqual(rep.status_label, "Dolandırıcılık Riski")
                self.assertGreaterEqual(len(rep.scam_indicators), 1)

    # --- 6. Defensive Input Validation ---

    def test_defensive_error_boundaries(self) -> None:
        with self.assertRaises(InvalidMarketInputError):
            self.market_engine.evaluate(-50.0, [10_000.0, 11_000.0, 12_000.0])

        with self.assertRaises(InvalidMarketInputError):
            self.market_engine.evaluate(float("nan"), [10_000.0, 11_000.0, 12_000.0])

        with self.assertRaises(InvalidMarketInputError):
            self.market_engine.evaluate(8_000.0, "invalid_sequence")  # type: ignore[arg-type]

        with self.assertRaises(InvalidDefectInputError):
            self.defect_extractor.extract("   ")

        with self.assertRaises(InvalidDefectInputError):
            self.defect_extractor.extract(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main(verbosity=2)