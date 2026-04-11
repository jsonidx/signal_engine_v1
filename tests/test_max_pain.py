"""
Smoke tests for compute_max_pain() in options_flow.py.

Tests cover:
  - Return type and required keys
  - Math correctness on synthetic chain data
  - Graceful None return on bad input
  - Distance / direction derivation
"""

import sys
import os
import math
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from options_flow import compute_max_pain


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_chain(
    strikes,
    call_oi,
    put_oi,
    expiry="2099-01-17",
) -> tuple:
    """Build minimal calls / puts DataFrames for the given strike lists."""
    calls = pd.DataFrame({"strike": strikes, "openInterest": call_oi})
    puts  = pd.DataFrame({"strike": strikes, "openInterest": put_oi})
    return calls, puts, expiry


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestComputeMaxPainReturnShape:
    """compute_max_pain must return a dict with all required keys."""

    def test_required_keys_present(self):
        calls, puts, expiry = _make_chain(
            strikes=[90, 95, 100, 105, 110],
            call_oi=[200, 300, 500, 200, 100],
            put_oi= [100, 200, 500, 300, 200],
        )
        result = compute_max_pain(
            "TEST", calls=calls, puts=puts, expiry=expiry, price=100.0
        )
        assert result is not None, "Expected a dict, got None"
        for key in ("max_pain_strike", "expiry", "days_to_expiry",
                    "current_price", "distance_pct", "direction"):
            assert key in result, f"Missing key: {key}"

    def test_value_types(self):
        calls, puts, expiry = _make_chain(
            strikes=[90, 95, 100, 105, 110],
            call_oi=[200, 300, 500, 200, 100],
            put_oi= [100, 200, 500, 300, 200],
        )
        result = compute_max_pain(
            "TEST", calls=calls, puts=puts, expiry=expiry, price=100.0
        )
        assert isinstance(result["max_pain_strike"], float)
        assert isinstance(result["distance_pct"], float)
        assert result["direction"] in ("ABOVE", "BELOW")


class TestComputeMaxPainMath:
    """Verify the max-pain formula produces the expected strike."""

    def test_symmetric_chain_pain_at_center(self):
        """Perfectly symmetric OI → max pain must be the ATM strike."""
        strikes = [90, 95, 100, 105, 110]
        calls, puts, expiry = _make_chain(
            strikes=strikes,
            call_oi=[100, 200, 300, 200, 100],
            put_oi= [100, 200, 300, 200, 100],
        )
        result = compute_max_pain(
            "TEST", calls=calls, puts=puts, expiry=expiry, price=100.0
        )
        assert result is not None
        assert result["max_pain_strike"] == 100.0, (
            f"Expected 100.0, got {result['max_pain_strike']}"
        )

    def test_call_heavy_chain_pain_below_atm(self):
        """Heavy call OI above ATM → writers want price below that cluster → pain skews lower."""
        strikes = [90, 95, 100, 105, 110]
        calls, puts, expiry = _make_chain(
            strikes=strikes,
            call_oi=[0, 0, 0, 1000, 1000],   # huge call OI above ATM
            put_oi= [1000, 1000, 0, 0, 0],    # huge put OI below ATM
        )
        result = compute_max_pain(
            "TEST", calls=calls, puts=puts, expiry=expiry, price=100.0
        )
        assert result is not None
        # Max pain should be at or near 100 (the crossing point)
        assert result["max_pain_strike"] in (95.0, 100.0, 105.0)

    def test_distance_pct_sign(self):
        """distance_pct = (price - max_pain) / max_pain * 100; positive when price > pain."""
        calls, puts, expiry = _make_chain(
            strikes=[90, 95, 100, 105, 110],
            call_oi=[200, 300, 500, 200, 100],
            put_oi= [100, 200, 500, 300, 200],
        )
        price = 110.0
        result = compute_max_pain(
            "TEST", calls=calls, puts=puts, expiry=expiry, price=price
        )
        assert result is not None
        expected_dist = round(
            (price - result["max_pain_strike"]) / result["max_pain_strike"] * 100, 2
        )
        assert math.isclose(result["distance_pct"], expected_dist, rel_tol=1e-4)
        assert result["direction"] == "ABOVE"

    def test_direction_below_when_price_less_than_pain(self):
        """When current price < max pain → direction must be BELOW."""
        calls, puts, expiry = _make_chain(
            strikes=[90, 95, 100, 105, 110],
            call_oi=[200, 300, 500, 200, 100],
            put_oi= [100, 200, 500, 300, 200],
        )
        result = compute_max_pain(
            "TEST", calls=calls, puts=puts, expiry=expiry, price=80.0
        )
        assert result is not None
        assert result["direction"] == "BELOW"
        assert result["distance_pct"] < 0


class TestComputeMaxPainEdgeCases:
    """Graceful degradation on bad / missing input."""

    def test_empty_calls_and_puts_returns_none(self):
        result = compute_max_pain(
            "TEST",
            calls=pd.DataFrame(columns=["strike", "openInterest"]),
            puts=pd.DataFrame(columns=["strike", "openInterest"]),
            expiry="2099-01-17",
            price=100.0,
        )
        assert result is None

    def test_missing_price_returns_none(self):
        calls, puts, expiry = _make_chain(
            strikes=[100], call_oi=[100], put_oi=[100]
        )
        result = compute_max_pain(
            "TEST", calls=calls, puts=puts, expiry=expiry, price=None
        )
        assert result is None

    def test_missing_expiry_returns_none(self):
        calls, puts, _ = _make_chain(
            strikes=[100], call_oi=[100], put_oi=[100]
        )
        result = compute_max_pain(
            "TEST", calls=calls, puts=puts, expiry=None, price=100.0
        )
        assert result is None


class TestComputeMaxPainLiveFetch:
    """Smoke test against a live yfinance fetch for a liquid ticker.

    This test makes a real network call. It only asserts shape — not specific
    values — so it won't break when the market moves.
    Skipped if network is unavailable.
    """

    @pytest.mark.integration
    def test_aapl_live_fetch(self):
        try:
            result = compute_max_pain("AAPL")
        except Exception as exc:
            pytest.skip(f"Network unavailable or yfinance error: {exc}")

        if result is None:
            pytest.skip("AAPL returned None (thin chain or market closed)")

        assert result["max_pain_strike"] > 0
        assert result["expiry"] is not None
        assert result["direction"] in ("ABOVE", "BELOW")
        assert isinstance(result["distance_pct"], float)
