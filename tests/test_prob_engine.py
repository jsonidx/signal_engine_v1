"""
tests/test_prob_engine.py — Unit tests for utils/prob_engine.py

Tests
-----
 1. normalize_signal — basic scaling
 2. normalize_signal — clips below min
 3. normalize_signal — clips above max
 4. normalize_signal — None returns 0.50
 5. normalize_signal — NaN returns 0.50
 6. normalize_signal — degenerate range (min==max) returns 0.50
 7. compute_prob_combined — returns float in [0.30, 0.90]
 8. compute_prob_combined — all-None inputs returns 0.50 (neutral, no crash)
 9. compute_prob_combined — all-maximum inputs returns value near 0.90
10. compute_prob_combined — all-minimum inputs returns value near 0.30
11. compute_prob_combined — prob_technical, prob_options, prob_catalyst in output
12. compute_prob_combined — data_quality HIGH/MEDIUM/LOW logic
13. compute_prob_combined — data_quality LOW when no real inputs
14. compute_prob_combined — data_quality HIGH when all inputs real
15. compute_prob_combined — iv_contribution inverts iv_rank
16. compute_prob_combined — beat_rate_4q None defaults to 0.50 catalyst
17. compute_prob_combined — news None defaults to neutral
18. compute_prob_combined — prob_combined clips at 0.30 floor
19. compute_prob_combined — prob_combined clips at 0.90 ceiling
20. Kelly sanity check: prob=0.65, rr=2.5 → positive fraction
21. Both pipelines produce same prob_combined for same inputs
"""

import sys
import os
import math

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.prob_engine import (
    normalize_signal,
    compute_prob_combined,
    _assess_data_quality,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _full_signals(
    rsi=70.0,
    heat=75.0,
    fund=72.0,
    beat_rate=0.75,
    avg_sentiment=0.3,
    agreement=0.80,
    iv_rank=25.0,
) -> dict:
    return {
        "technical":             {"rsi_14": rsi},
        "options_flow":          {"heat_score": heat, "iv_rank": iv_rank},
        "fundamentals":          {"fundamental_score_pct": fund},
        "earnings_event":        {"beat_rate_4q": beat_rate},
        "news_sentiment":        {"avg_sentiment": avg_sentiment, "source": "marketaux",
                                  "articles_found": 3},
        "signal_agreement_score": agreement,
    }


def _empty_signals() -> dict:
    return {}


def _min_signals() -> dict:
    return {
        "technical":             {"rsi_14": 0.0},
        "options_flow":          {"heat_score": 0.0, "iv_rank": 100.0},
        "fundamentals":          {"fundamental_score_pct": 0.0},
        "earnings_event":        {"beat_rate_4q": 0.0},
        "news_sentiment":        {"avg_sentiment": -1.0, "source": "marketaux",
                                  "articles_found": 5},
        "signal_agreement_score": 0.0,
    }


def _max_signals() -> dict:
    return {
        "technical":             {"rsi_14": 100.0},
        "options_flow":          {"heat_score": 100.0, "iv_rank": 0.0},
        "fundamentals":          {"fundamental_score_pct": 100.0},
        "earnings_event":        {"beat_rate_4q": 1.0},
        "news_sentiment":        {"avg_sentiment": 1.0, "source": "marketaux",
                                  "articles_found": 5},
        "signal_agreement_score": 1.0,
    }


# ---------------------------------------------------------------------------
# 1–6. normalize_signal
# ---------------------------------------------------------------------------

class TestNormalizeSignal:
    def test_basic_scaling(self):
        assert math.isclose(normalize_signal(50.0, 0, 100), 0.50, abs_tol=1e-9)
        assert math.isclose(normalize_signal(80.0, 0, 100), 0.80, abs_tol=1e-9)
        assert math.isclose(normalize_signal(0.3, -1.0, 1.0), 0.65, abs_tol=1e-9)

    def test_clips_below_min(self):
        result = normalize_signal(-50.0, 0, 100)
        assert math.isclose(result, 0.0, abs_tol=1e-9)

    def test_clips_above_max(self):
        result = normalize_signal(150.0, 0, 100)
        assert math.isclose(result, 1.0, abs_tol=1e-9)

    def test_none_returns_half(self):
        assert normalize_signal(None, 0, 100) == 0.50

    def test_nan_returns_half(self):
        assert normalize_signal(float("nan"), 0, 100) == 0.50

    def test_degenerate_range_returns_half(self):
        assert normalize_signal(5.0, 5.0, 5.0) == 0.50


# ---------------------------------------------------------------------------
# 7–10. compute_prob_combined — range + boundary
# ---------------------------------------------------------------------------

class TestProbCombinedRange:
    def test_returns_float_in_range(self):
        result = compute_prob_combined(_full_signals())
        p = result["prob_combined"]
        assert isinstance(p, float), f"Expected float, got {type(p)}"
        assert 0.30 <= p <= 0.90, f"Out of range: {p}"

    def test_all_none_inputs_returns_near_neutral(self):
        result = compute_prob_combined(_empty_signals())
        p = result["prob_combined"]
        # All inputs default to 0.50 → weighted sum = 0.50 → clipped to 0.50
        assert isinstance(p, float)
        assert math.isclose(p, 0.50, abs_tol=0.01), f"Expected ~0.50, got {p}"

    def test_maximum_inputs_near_ceiling(self):
        result = compute_prob_combined(_max_signals())
        p = result["prob_combined"]
        assert p >= 0.85, f"Max signals should be near ceiling, got {p}"

    def test_minimum_inputs_at_floor(self):
        result = compute_prob_combined(_min_signals())
        p = result["prob_combined"]
        assert p <= 0.35, f"Min signals should be near floor, got {p}"


# ---------------------------------------------------------------------------
# 11. Output keys
# ---------------------------------------------------------------------------

class TestProbCombinedOutputKeys:
    def test_required_keys_present(self):
        result = compute_prob_combined(_full_signals())
        required = {
            "prob_combined", "prob_technical", "prob_options",
            "prob_catalyst", "prob_news", "iv_contribution", "data_quality",
        }
        missing = required - set(result.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_all_component_probs_in_0_1(self):
        result = compute_prob_combined(_full_signals())
        for key in ("prob_technical", "prob_options", "prob_catalyst",
                    "prob_news", "iv_contribution"):
            v = result[key]
            assert 0.0 <= v <= 1.0, f"{key} out of range: {v}"


# ---------------------------------------------------------------------------
# 12–14. data_quality
# ---------------------------------------------------------------------------

class TestDataQuality:
    def test_high_quality_with_all_real_inputs(self):
        assert _assess_data_quality(_full_signals()) == "HIGH"

    def test_low_quality_with_empty_signals(self):
        assert _assess_data_quality(_empty_signals()) == "LOW"

    def test_medium_quality_partial(self):
        signals = {
            "technical":  {"rsi_14": 55.0},
            "options_flow": {"heat_score": 60.0, "iv_rank": None},
            "fundamentals": {"fundamental_score_pct": 70.0},
            "earnings_event": {"beat_rate_4q": None},
            "news_sentiment": {"avg_sentiment": 0.0, "source": "fallback_neutral",
                               "articles_found": 0},
            "signal_agreement_score": 0.75,
        }
        quality = _assess_data_quality(signals)
        assert quality in ("MEDIUM", "HIGH")


# ---------------------------------------------------------------------------
# 15. IV contribution is inverted
# ---------------------------------------------------------------------------

class TestIVContribution:
    def test_low_iv_rank_gives_high_contribution(self):
        low_iv  = _full_signals(iv_rank=10.0)
        high_iv = _full_signals(iv_rank=90.0)
        r_low  = compute_prob_combined(low_iv)
        r_high = compute_prob_combined(high_iv)
        assert r_low["iv_contribution"] > r_high["iv_contribution"], (
            "Low IV rank should produce higher iv_contribution than high IV rank"
        )

    def test_iv_none_gives_neutral_contribution(self):
        signals = _full_signals()
        signals["options_flow"]["iv_rank"] = None
        result = compute_prob_combined(signals)
        assert math.isclose(result["iv_contribution"], 0.50, abs_tol=0.01)


# ---------------------------------------------------------------------------
# 16. beat_rate_4q None defaults to 0.50
# ---------------------------------------------------------------------------

class TestBeatRateDefault:
    def test_none_beat_rate_defaults_neutral(self):
        signals = _full_signals(beat_rate=None)
        signals["earnings_event"]["beat_rate_4q"] = None
        result = compute_prob_combined(signals)
        assert math.isclose(result["prob_catalyst"], 0.50, abs_tol=0.01)


# ---------------------------------------------------------------------------
# 17. news None / missing defaults to neutral
# ---------------------------------------------------------------------------

class TestNewsSentimentDefault:
    def test_missing_news_defaults_neutral(self):
        signals = _full_signals()
        signals.pop("news_sentiment", None)
        result = compute_prob_combined(signals)
        assert math.isclose(result["prob_news"], 0.50, abs_tol=0.01)

    def test_fallback_neutral_source_treated_as_neutral(self):
        signals = _full_signals()
        signals["news_sentiment"] = {
            "avg_sentiment": 0.0,
            "source": "fallback_neutral",
            "articles_found": 0,
        }
        result = compute_prob_combined(signals)
        assert math.isclose(result["prob_news"], 0.50, abs_tol=0.01)


# ---------------------------------------------------------------------------
# 18–19. Clipping at floor and ceiling
# ---------------------------------------------------------------------------

class TestClipping:
    def test_extreme_bearish_clamps_to_floor(self):
        # Even with all-0 inputs the weighted sum = 0.0, clipped to 0.30
        result = compute_prob_combined(_min_signals())
        assert result["prob_combined"] >= 0.30

    def test_extreme_bullish_clamps_to_ceiling(self):
        result = compute_prob_combined(_max_signals())
        assert result["prob_combined"] <= 0.90


# ---------------------------------------------------------------------------
# 20. Kelly sanity check
# ---------------------------------------------------------------------------

class TestKellySanity:
    def test_positive_kelly_for_edge_setup(self):
        """
        Kelly formula: f = (p*(b+1) - 1) / b
        prob=0.65, rr=2.5 → f = (0.65*3.5 - 1) / 2.5 = (2.275 - 1) / 2.5 = 0.51
        Quarter-Kelly cap → 0.1275, then regime multiply down.
        Just verify it's positive.
        """
        prob = 0.65
        rr   = 2.5
        kelly_raw = (prob * (rr + 1) - 1) / rr
        assert kelly_raw > 0, f"Kelly should be positive for prob=0.65, rr=2.5; got {kelly_raw}"

    def test_negative_kelly_for_no_edge(self):
        prob = 0.30
        rr   = 2.0
        kelly_raw = (prob * (rr + 1) - 1) / rr
        assert kelly_raw < 0, f"Kelly should be negative for prob=0.30, rr=2.0"


# ---------------------------------------------------------------------------
# 21. Same inputs produce same output from both pipeline contexts
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_output(self):
        s1 = _full_signals()
        s2 = _full_signals()
        r1 = compute_prob_combined(s1)
        r2 = compute_prob_combined(s2)
        assert r1["prob_combined"] == r2["prob_combined"]
        assert r1["data_quality"]  == r2["data_quality"]

    def test_partial_signals_no_crash(self):
        """Pipeline must never raise for any subset of signals."""
        partial_cases = [
            {},
            {"technical": {}},
            {"technical": {"rsi_14": None}},
            {"options_flow": {"heat_score": 55}},
            {"signal_agreement_score": 0.70},
            {"earnings_event": {"beat_rate_4q": None}},
            {"news_sentiment": None},
        ]
        for signals in partial_cases:
            result = compute_prob_combined(signals)
            p = result["prob_combined"]
            assert isinstance(p, float), f"Expected float for input {signals}, got {type(p)}"
            assert 0.30 <= p <= 0.90,   f"Out of range for input {signals}: {p}"
