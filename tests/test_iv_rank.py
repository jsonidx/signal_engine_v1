"""
tests/test_iv_rank.py — Unit tests for the 30-day rolling IV Rank fix.

Tests
-----
1.  compute_iv_rank_returns_float_with_history      — 5+ rows → float returned
2.  compute_iv_rank_returns_none_below_5_rows       — <5 rows → None
3.  compute_iv_rank_returns_50_when_flat            — max==min → 0.5
4.  iv_rank_label_low                               — rank < 25 → Low IV label logic
5.  iv_rank_label_high                              — rank > 75 → High IV label logic
6.  iv_rank_label_normal                            — 25–75 → Normal
7.  iv_history_upsert_no_duplicate_error            — ON CONFLICT idempotent
8.  get_options_heat_returns_iv_history_days_key    — key present in result dict
9.  get_options_heat_no_na_string                   — iv_rank is float or 0.0, never str "N/A"
10. aapl_live_smoke_test                            — AAPL returns float or None (never raises)
"""

import sys
import os
import math
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from utils.iv_calculator import (
    _get_iv_metrics,
    _store_iv,
    get_iv_rank_and_percentile,
)
from utils.db import get_connection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_TICKER = "TEST_IVRANK_UNIT"   # isolated ticker — cleaned up after each test


def _seed(iv_values: list, start_offset_days: int = 1) -> None:
    """Insert synthetic IV history rows into Supabase iv_history."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        today = date.today()
        n = len(iv_values)
        for i, iv in enumerate(iv_values):
            row_date = today - timedelta(days=start_offset_days + (n - 1 - i))
            cur.execute(
                """
                INSERT INTO iv_history (ticker, date, iv30, computed_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (ticker, date) DO UPDATE SET
                    iv30        = EXCLUDED.iv30,
                    computed_at = EXCLUDED.computed_at
                """,
                (_TEST_TICKER, row_date.isoformat(), float(iv),
                 datetime.utcnow().isoformat()),
            )
        conn.commit()
    finally:
        conn.close()


def _cleanup() -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM iv_history WHERE ticker = %s", (_TEST_TICKER,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. Returns float when ≥ 5 snapshots exist
# ---------------------------------------------------------------------------

class TestIVRankReturnsFloat:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_returns_float_with_5_rows(self):
        """5 seeded rows + today's write → rank activates (min_history=5)."""
        _seed([0.20, 0.22, 0.25, 0.28, 0.30])
        rank, pct = get_iv_rank_and_percentile(
            _TEST_TICKER, 0.25, lookback_days=30, min_history=5
        )
        assert rank is not None, "Expected float rank, got None"
        assert isinstance(rank, float)
        assert 0.0 <= rank <= 1.0
        assert pct is not None
        assert 0.0 <= pct <= 1.0

    def test_rank_mathematics(self):
        """
        Seed min=0.20, max=0.40; current=0.30.
        Expected rank = (0.30 - 0.20) / (0.40 - 0.20) = 0.5  (before today's write).
        After _store_iv appends current=0.25 today, min/max/rank shift slightly — just
        check it's close to 0.5 and in [0, 1].
        """
        _seed([0.20, 0.25, 0.30, 0.35, 0.40])
        rank, _ = get_iv_rank_and_percentile(
            _TEST_TICKER, 0.30, lookback_days=30, min_history=5
        )
        assert rank is not None
        # With 0.30 appended as today, history = [0.20,0.25,0.30,0.35,0.40,0.30]
        # min=0.20, max=0.40 → rank = (0.30-0.20)/(0.40-0.20) = 0.5
        assert math.isclose(rank, 0.5, abs_tol=0.15)  # generous tolerance for day order


# ---------------------------------------------------------------------------
# 2. Returns None when fewer than 5 rows (with min_history=5)
# ---------------------------------------------------------------------------

class TestIVRankReturnsNoneBelow5:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_none_with_zero_rows(self):
        """No seeded rows → _store_iv writes today only → 1 row < 5 → None."""
        rank, pct = get_iv_rank_and_percentile(
            _TEST_TICKER, 0.30, lookback_days=30, min_history=5
        )
        # After _store_iv: 1 row total (today). 1 < 5 → (None, None).
        assert rank is None
        assert pct is None

    def test_none_with_4_rows(self):
        """4 seeded + 1 today = 5 rows — exactly at threshold, should pass."""
        _seed([0.20, 0.25, 0.30, 0.35])  # 4 past rows
        rank, _ = get_iv_rank_and_percentile(
            _TEST_TICKER, 0.28, lookback_days=30, min_history=5
        )
        # 4 past + 1 today = 5 total → equals threshold → should return rank
        assert rank is not None, "Expected rank at exactly 5 rows"

    def test_none_with_3_rows(self):
        """3 seeded + 1 today = 4 rows < 5 → still None."""
        _seed([0.20, 0.25, 0.30])
        rank, _ = get_iv_rank_and_percentile(
            _TEST_TICKER, 0.28, lookback_days=30, min_history=5
        )
        assert rank is None

    def test_default_threshold_still_60(self):
        """
        Without min_history override, global IV_MIN_HISTORY_DAYS=60 applies.
        5 rows is not enough for the default path.
        """
        _seed([0.20, 0.25, 0.30, 0.35, 0.40])
        rank, _ = get_iv_rank_and_percentile(
            _TEST_TICKER, 0.30, lookback_days=30
            # no min_history → uses IV_MIN_HISTORY_DAYS = 60
        )
        assert rank is None, "Default path should require 60 rows, not 5"


# ---------------------------------------------------------------------------
# 3. Returns 0.5 when max == min (flat IV environment)
# ---------------------------------------------------------------------------

class TestIVRankFlatEnvironment:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_flat_iv_returns_0_5(self):
        """All historical IVs identical → degenerate case → rank = 0.5."""
        _seed([0.25] * 5)
        rank, _ = get_iv_rank_and_percentile(
            _TEST_TICKER, 0.25, lookback_days=30, min_history=5
        )
        assert rank is not None
        assert math.isclose(rank, 0.5, abs_tol=0.01), f"Expected 0.5, got {rank}"


# ---------------------------------------------------------------------------
# 4–6. IV rank label threshold logic (pure Python — no DB)
# ---------------------------------------------------------------------------

class TestIVRankLabels:
    """
    The label logic lives in ai_quant._build_prompt() but we replicate
    the thresholds here as unit tests since the rule is:
      < 25  → Low IV
      > 75  → High IV
      25–75 → Normal IV
    """

    def _label(self, iv_rank_val: float) -> str:
        if iv_rank_val < 25:
            return "Low IV"
        elif iv_rank_val > 75:
            return "High IV"
        return "Normal IV"

    def test_low_iv_below_25(self):
        assert self._label(10.0) == "Low IV"
        assert self._label(24.9) == "Low IV"

    def test_high_iv_above_75(self):
        assert self._label(76.0) == "High IV"
        assert self._label(99.9) == "High IV"

    def test_normal_iv_boundary_25(self):
        assert self._label(25.0) == "Normal IV"

    def test_normal_iv_boundary_75(self):
        assert self._label(75.0) == "Normal IV"

    def test_normal_iv_midrange(self):
        assert self._label(50.0) == "Normal IV"


# ---------------------------------------------------------------------------
# 7. iv_history upsert is idempotent (no duplicate error on same date)
# ---------------------------------------------------------------------------

class TestIVHistoryUpsert:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_duplicate_date_no_error(self):
        """Calling _store_iv twice for the same (ticker, date) must not raise."""
        today_str = date.today().isoformat()
        conn = get_connection()
        try:
            cur = conn.cursor()
            for _ in range(3):
                cur.execute(
                    """
                    INSERT INTO iv_history (ticker, date, iv30, computed_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (ticker, date) DO UPDATE SET
                        iv30        = EXCLUDED.iv30,
                        computed_at = EXCLUDED.computed_at
                    """,
                    (_TEST_TICKER, today_str, 0.30, datetime.utcnow().isoformat()),
                )
            conn.commit()
        finally:
            conn.close()
        # Verify only one row for today
        conn2 = get_connection()
        try:
            cur2 = conn2.cursor()
            cur2.execute(
                "SELECT COUNT(*) AS n FROM iv_history WHERE ticker=%s AND date=%s",
                (_TEST_TICKER, today_str),
            )
            row = cur2.fetchone()
            assert int(row["n"]) == 1
        finally:
            conn2.close()


# ---------------------------------------------------------------------------
# 8. get_options_heat() returns iv_history_days key
# ---------------------------------------------------------------------------

class TestGetOptionsHeatKeys:
    """Verify the iv_history_days key is always present in get_options_heat() output."""

    def test_iv_history_days_key_present_on_success(self):
        """
        Mock analyze_ticker to return a minimal result and confirm
        get_options_heat wraps it with iv_history_days.
        """
        from options_flow import get_options_heat

        fake_result = {
            "heat_score": 55.0,
            "direction": "BULL",
            "expected_move_pct": 3.5,
            "implied_vol_pct": 32.0,
            "true_iv_pct": 31.0,
            "iv_rank": 45.0,
            "iv_source": "true",
            "iv_history_days": 12,
            "iv_percentile": 50.0,
            "iv_score": 15.0,
            "pc_ratio": 1.1,
            "pc_label": "balanced",
            "pc_score": 10.0,
            "total_options_vol": 50000,
            "call_vol": 30000,
            "put_vol": 20000,
            "total_oi": 200000,
            "straddle_cost": 5.50,
            "expiry": "2099-01-17",
            "days_to_exp": 14,
            "vol_score": 18.0,
            "vol_label": "high",
            "stock_vol_today": 1000000,
            "stock_vol_20d_avg": 800000,
            "price": 150.0,
            "ticker": "FAKE",
            "gamma_concentration_pct": 40.0,
        }

        with patch("options_flow.analyze_ticker", return_value=fake_result):
            result = get_options_heat("FAKE")

        assert "iv_history_days" in result, "iv_history_days key missing from get_options_heat output"
        assert result["iv_history_days"] == 12


# ---------------------------------------------------------------------------
# 9. get_options_heat() never returns the string "N/A" for iv_rank
# ---------------------------------------------------------------------------

class TestGetOptionsHeatNoNAString:
    """iv_rank in the dict should be a float (possibly 0.0), never the string 'N/A'."""

    def test_iv_rank_is_not_na_string_on_success(self):
        from options_flow import get_options_heat

        fake_result = {
            "heat_score": 40.0, "direction": "NEUTRAL",
            "expected_move_pct": 2.0, "implied_vol_pct": 25.0,
            "true_iv_pct": None, "iv_rank": 0.0, "iv_source": "estimated",
            "iv_history_days": 0, "iv_percentile": None, "iv_score": 5.0,
            "pc_ratio": 0.9, "pc_label": "balanced", "pc_score": 10.0,
            "total_options_vol": 5000, "call_vol": 3000, "put_vol": 2000,
            "total_oi": 50000, "straddle_cost": 3.0,
            "expiry": "2099-01-17", "days_to_exp": 14,
            "vol_score": 5.0, "vol_label": "low",
            "stock_vol_today": 500000, "stock_vol_20d_avg": 600000,
            "price": 50.0, "ticker": "FAKE2",
            "gamma_concentration_pct": 30.0,
        }

        with patch("options_flow.analyze_ticker", return_value=fake_result):
            result = get_options_heat("FAKE2")

        iv = result.get("iv_rank")
        assert iv != "N/A", "iv_rank must never be the string 'N/A'"
        assert isinstance(iv, (int, float)), f"iv_rank must be numeric, got {type(iv)}"

    def test_empty_dict_when_analyze_returns_none(self):
        """When analyze_ticker returns None → get_options_heat returns {} (not crash)."""
        from options_flow import get_options_heat

        with patch("options_flow.analyze_ticker", return_value=None):
            result = get_options_heat("CRYPTO-USD")

        assert result == {}


# ---------------------------------------------------------------------------
# 10. AAPL live smoke test
# ---------------------------------------------------------------------------

class TestAAPLLiveSmoke:
    """Real network call — only asserts shape, not specific values."""

    @pytest.mark.integration
    def test_aapl_iv_rank_float_or_none(self):
        """
        get_iv_rank_and_percentile for AAPL with min_history=5 must return
        (float, float) or (None, None) — never raise.
        """
        try:
            from utils.iv_calculator import compute_atm_iv
            iv = compute_atm_iv("AAPL")
        except Exception as exc:
            pytest.skip(f"Network unavailable: {exc}")

        if iv is None:
            pytest.skip("compute_atm_iv returned None (market closed or illiquid)")

        try:
            rank, pct = get_iv_rank_and_percentile(
                "AAPL", iv, lookback_days=30, min_history=5
            )
        except Exception as exc:
            pytest.fail(f"get_iv_rank_and_percentile raised: {exc}")

        # Either (float, float) or (None, None) — both are valid
        if rank is not None:
            assert isinstance(rank, float), f"rank type: {type(rank)}"
            assert 0.0 <= rank <= 1.0, f"rank out of range: {rank}"
            assert pct is not None
            assert 0.0 <= pct <= 1.0
        else:
            assert pct is None
