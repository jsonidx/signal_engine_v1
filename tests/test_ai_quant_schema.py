"""
tests/test_ai_quant_schema.py
=============================================================================
Tests for:
  1. compute_signal_agreement() — deterministic score for known signals dicts
  2. _validate_probabilities()  — warns when bull+bear+neutral ≠ ~1.0
  3. _parse_response()          — correctly extracts JSON from a mock Claude reply
=============================================================================
"""

import json
import sys
import os
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_quant import compute_signal_agreement, _validate_probabilities, _parse_response


# ---------------------------------------------------------------------------
# compute_signal_agreement tests
# ---------------------------------------------------------------------------

def test_agreement_all_bull():
    """All modules firing BULL → score = 1.0."""
    signals = {
        "signal_engine":  {"composite_z": 1.2},          # BULL
        "squeeze":        {"squeeze_score_100": 72},      # BULL
        "options_flow":   {"heat_score": 75},             # BULL
        "cross_asset":    {"signal": "BOTTOM"},           # BULL
        "fundamentals":   {"fundamental_score_pct": 78},  # BULL
        "polymarket":     {"polymarket_probability": 0.80},  # BULL
    }
    score = compute_signal_agreement(signals)
    assert score == 1.0, f"Expected 1.0, got {score}"


def test_agreement_all_bear():
    """All modules firing BEAR → score = 1.0 (plurality is BEAR)."""
    signals = {
        "signal_engine":  {"composite_z": -1.5},           # BEAR
        "cross_asset":    {"signal": "TOP"},                # BEAR
        "fundamentals":   {"fundamental_score_pct": 25},   # BEAR
        "polymarket":     {"polymarket_probability": 0.20}, # BEAR
    }
    score = compute_signal_agreement(signals)
    assert score == 1.0, f"Expected 1.0, got {score}"


def test_agreement_mixed_majority_bull():
    """4 BULL vs 1 BEAR → score = 4/5 = 0.8."""
    signals = {
        "signal_engine":  {"composite_z": 0.8},            # BULL
        "squeeze":        {"squeeze_score_100": 60},        # BULL
        "options_flow":   {"heat_score": 65},               # BULL
        "cross_asset":    {"signal": "TOP"},                # BEAR
        "fundamentals":   {"fundamental_score_pct": 70},   # BULL
        # polymarket absent → no vote
    }
    score = compute_signal_agreement(signals)
    assert score == 0.8, f"Expected 0.8, got {score}"


def test_agreement_no_modules():
    """Empty signals dict → score = 0.0."""
    score = compute_signal_agreement({})
    assert score == 0.0, f"Expected 0.0, got {score}"


def test_agreement_neutral_modules_excluded():
    """Modules below threshold cast no vote and are excluded from denominator."""
    signals = {
        "signal_engine": {"composite_z": 0.2},          # between -0.5 and 0.5 → no vote
        "squeeze":       {"squeeze_score_100": 45},      # ≤50 → no vote
        "options_flow":  {"heat_score": 55},             # ≤60 → no vote
        "fundamentals":  {"fundamental_score_pct": 50},  # between 40-60 → no vote
        "polymarket":    {"polymarket_probability": 0.50},  # between 0.35-0.65 → no vote
    }
    score = compute_signal_agreement(signals)
    assert score == 0.0, f"Expected 0.0 (no valid votes), got {score}"


def test_agreement_single_module_fires():
    """Only one module fires → score = 1.0 (100% agreement with itself)."""
    signals = {
        "squeeze": {"squeeze_score_100": 80},  # BULL
    }
    score = compute_signal_agreement(signals)
    assert score == 1.0, f"Expected 1.0, got {score}"


def test_agreement_tie_breaks_to_plurality_count():
    """2 BULL vs 2 BEAR → plurality = 2/4 = 0.5."""
    signals = {
        "signal_engine": {"composite_z": 0.8},             # BULL
        "squeeze":       {"squeeze_score_100": 60},         # BULL
        "cross_asset":   {"signal": "TOP"},                 # BEAR
        "polymarket":    {"polymarket_probability": 0.20},  # BEAR
    }
    score = compute_signal_agreement(signals)
    assert score == 0.5, f"Expected 0.5, got {score}"


def test_agreement_polymarket_zero_probability_ignored():
    """polymarket_probability == 0 → not counted (avoids false BEAR votes)."""
    signals = {
        "polymarket": {"polymarket_probability": 0},
        "squeeze":    {"squeeze_score_100": 60},  # BULL
    }
    score = compute_signal_agreement(signals)
    assert score == 1.0, f"Expected 1.0 (polymarket=0 should be ignored), got {score}"


# ---------------------------------------------------------------------------
# _validate_probabilities tests
# ---------------------------------------------------------------------------

def test_probabilities_valid_sum_no_warning():
    """Probabilities summing to 1.0 should NOT emit a warning."""
    thesis = {
        "ticker": "TEST",
        "bull_probability": 0.6,
        "bear_probability": 0.3,
        "neutral_probability": 0.1,
    }
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _validate_probabilities(thesis)
    assert len(w) == 0, f"Expected no warnings, got: {[str(x.message) for x in w]}"


def test_probabilities_sum_off_warns():
    """Probabilities summing to 0.80 (off by >0.05) must trigger a UserWarning."""
    thesis = {
        "ticker": "TEST",
        "bull_probability": 0.5,
        "bear_probability": 0.2,
        "neutral_probability": 0.1,   # sum = 0.80 → off by 0.20
    }
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _validate_probabilities(thesis)
    assert len(w) == 1, f"Expected 1 warning, got {len(w)}"
    assert issubclass(w[0].category, UserWarning)
    assert "0.800" in str(w[0].message) or "sum" in str(w[0].message).lower()


def test_probabilities_within_tolerance_no_warning():
    """Sum of 0.97 is within ±0.05 of 1.0 → no warning."""
    thesis = {
        "ticker": "TEST",
        "bull_probability": 0.60,
        "bear_probability": 0.30,
        "neutral_probability": 0.07,  # sum = 0.97
    }
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _validate_probabilities(thesis)
    assert len(w) == 0, f"Expected no warnings, got: {[str(x.message) for x in w]}"


def test_probabilities_all_absent_no_warning():
    """When no probability keys are present, no warning should fire."""
    thesis = {"ticker": "TEST", "direction": "BULL"}
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _validate_probabilities(thesis)
    assert len(w) == 0


# ---------------------------------------------------------------------------
# _parse_response tests (mocked Claude output)
# ---------------------------------------------------------------------------

SAMPLE_CLAUDE_JSON = {
    "ticker": "COIN",
    "direction": "BULL",
    "bull_probability": 0.65,
    "bear_probability": 0.20,
    "neutral_probability": 0.15,
    "conviction": 4,
    "time_horizon": "weeks",
    "primary_scenario": "COIN breaks above $280 on strong crypto market tailwinds.",
    "bear_scenario": "Regulatory crackdown causes a pullback below $230 support.",
    "key_invalidation": "Close below $228",
    "entry_low": 245.0,
    "entry_high": 255.0,
    "stop_loss": 228.0,
    "target_1": 285.0,
    "target_2": 310.0,
    "position_size_pct": 8,
    "signal_agreement_score": 0.8,
    "catalysts": ["BTC recovery above 200MA", "Coinbase earnings beat"],
    "risks": ["SEC enforcement action", "Broad risk-off event"],
    "thesis": "COIN shows strong momentum alignment with crypto market. Options flow is bullish.",
    "data_quality": "HIGH",
    "notes": "",
}


def test_parse_response_direct_json():
    """_parse_response handles a raw JSON string correctly."""
    raw = json.dumps(SAMPLE_CLAUDE_JSON)
    result = _parse_response(raw)
    assert result is not None
    assert result["ticker"] == "COIN"
    assert result["direction"] == "BULL"
    assert result["bull_probability"] == 0.65
    assert result["key_invalidation"] == "Close below $228"
    assert result["primary_scenario"].startswith("COIN breaks")
    assert result["bear_scenario"].startswith("Regulatory")
    assert result["signal_agreement_score"] == 0.8


def test_parse_response_wrapped_in_code_fence():
    """_parse_response correctly strips ```json ... ``` fences."""
    raw = f"Here is the analysis:\n```json\n{json.dumps(SAMPLE_CLAUDE_JSON)}\n```"
    result = _parse_response(raw)
    assert result is not None
    assert result["direction"] == "BULL"
    assert result["bear_probability"] == 0.20


def test_parse_response_returns_none_on_invalid():
    """_parse_response returns None when the response has no valid JSON."""
    result = _parse_response("Sorry, I cannot analyze this ticker.")
    assert result is None


def test_parse_response_probabilities_sum():
    """Parsed SAMPLE_CLAUDE_JSON probabilities sum to 1.0."""
    result = _parse_response(json.dumps(SAMPLE_CLAUDE_JSON))
    total = (result["bull_probability"] + result["bear_probability"]
             + result["neutral_probability"])
    assert abs(total - 1.0) < 0.001, f"Probabilities sum to {total}, expected 1.0"
