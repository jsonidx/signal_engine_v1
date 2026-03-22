"""
tests/test_iv_calculator.py — Unit tests for utils/iv_calculator.py

Tests
-----
1.  test_bs_call_known_value          — textbook Black-Scholes ATM call price
2.  test_bs_call_intrinsic_deep_itm   — deep ITM: call price ≥ intrinsic value
3.  test_bs_call_zero_time            — T=0: returns intrinsic only
4.  test_bs_vega_positive             — vega > 0 for valid ATM option
5.  test_bs_vega_zero_time            — T=0: vega = 0
6.  test_implied_vol_round_trip       — compute call price then recover sigma
7.  test_implied_vol_round_trip_otm   — round-trip for OTM call
8.  test_implied_vol_invalid_inputs   — degenerate inputs return None
9.  test_implied_vol_below_intrinsic  — sub-intrinsic price returns None
10. test_iv_rank_known_values         — synthetic 100-day history, known min/max
11. test_iv_rank_returns_none_below_min_history — fewer than 60 days → None
12. test_iv_rank_clamped_at_extremes  — IV above max → rank = 1.0
13. test_iv_percentile_uniform        — uniform distribution → ~0.50 at median
14. test_iv_percentile_returns_none_below_min_history — fewer than 60 days → None
15. test_get_iv_rank_and_percentile   — combined function returns both values
16. test_store_then_retrieve          — _store_iv writes; subsequent call reads it
17. test_illiquid_returns_none        — bid=0 on ATM options → compute_atm_iv None
18. test_collect_and_store_iv_filters_crypto — crypto tickers skipped
"""

import math
import os
import sys
import tempfile
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.iv_calculator import (
    _get_iv_metrics,
    _store_iv,
    _ensure_db,
    bs_call,
    bs_vega,
    collect_and_store_iv,
    get_iv_rank,
    get_iv_rank_and_percentile,
    get_iv_percentile,
    implied_vol,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_temp_db():
    """Return path to a temporary SQLite file (caller must clean up)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _cleanup_db(path: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        p = path + suffix
        if os.path.exists(p):
            os.unlink(p)


def _seed_iv_history(db_path: str, ticker: str, iv_values: list, start_offset_days: int = 1) -> None:
    """
    Insert synthetic IV history rows into db_path.

    iv_values[0] maps to (today - start_offset_days - len + 1), so the most
    recent artificial entry is (today - start_offset_days).  This ensures none
    of the seeded rows fall on today's date (avoiding INSERT OR REPLACE
    collisions with the row _get_iv_metrics writes for current_iv).
    """
    _ensure_db(db_path)
    from utils.db import managed_connection
    from datetime import datetime

    today = date.today()
    n = len(iv_values)
    with managed_connection(db_path) as conn:
        for i, iv in enumerate(iv_values):
            row_date = today - timedelta(days=start_offset_days + (n - 1 - i))
            conn.execute(
                """
                INSERT OR REPLACE INTO iv_history
                    (ticker, date, iv30, computed_at)
                VALUES (?, ?, ?, ?)
                """,
                (ticker, row_date.isoformat(), iv, datetime.utcnow().isoformat()),
            )
        conn.commit()


# ===========================================================================
# 1-3: Black-Scholes call price
# ===========================================================================

class TestBsCall:
    def test_bs_call_known_value(self):
        """
        Textbook ATM call: S=100, K=100, T=1yr, r=5%, σ=20%.
        Standard result ≈ 10.45.  Verified against Hull "Options, Futures..."
        """
        result = bs_call(S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.2)
        assert abs(result - 10.45) < 0.05, f"Expected ~10.45, got {result:.4f}"

    def test_bs_call_intrinsic_deep_itm(self):
        """Deep ITM call price must be at least intrinsic (S - K * e^{-rT})."""
        result = bs_call(S=150.0, K=100.0, T=0.25, r=0.05, sigma=0.3)
        intrinsic = 150.0 - 100.0 * math.exp(-0.05 * 0.25)
        assert result >= intrinsic - 1e-6

    def test_bs_call_zero_time(self):
        """At expiry (T=0): call price == max(S - K, 0)."""
        assert bs_call(120.0, 100.0, 0.0, 0.05, 0.3) == pytest.approx(20.0)
        assert bs_call(80.0, 100.0, 0.0, 0.05, 0.3) == pytest.approx(0.0)


# ===========================================================================
# 4-5: Black-Scholes vega
# ===========================================================================

class TestBsVega:
    def test_bs_vega_positive(self):
        """Vega must be strictly positive for a live option (T > 0, sigma > 0)."""
        v = bs_vega(S=100.0, K=100.0, T=0.25, r=0.05, sigma=0.2)
        assert v > 0

    def test_bs_vega_zero_time(self):
        """At expiry (T=0): vega = 0 (no sensitivity to vol change)."""
        assert bs_vega(100.0, 100.0, 0.0, 0.05, 0.2) == pytest.approx(0.0)

    def test_bs_vega_atm_maximum(self):
        """ATM vega is always greater than deep ITM or OTM vega at same T."""
        v_atm = bs_vega(100.0, 100.0, 0.25, 0.05, 0.3)
        v_itm = bs_vega(100.0, 70.0, 0.25, 0.05, 0.3)
        v_otm = bs_vega(100.0, 130.0, 0.25, 0.05, 0.3)
        assert v_atm > v_itm
        assert v_atm > v_otm


# ===========================================================================
# 6-9: Newton-Raphson implied vol solver
# ===========================================================================

class TestImpliedVol:
    def test_implied_vol_round_trip(self):
        """
        Compute a call price then recover the original sigma.
        Tests that the Newton-Raphson solver inverts bs_call correctly.
        """
        S, K, T, r, true_sigma = 100.0, 100.0, 1.0, 0.05, 0.25
        call_price = bs_call(S, K, T, r, true_sigma)
        recovered = implied_vol(call_price, S, K, T, r)
        assert recovered is not None, "Solver returned None on valid round-trip"
        assert abs(recovered - true_sigma) < 1e-5, (
            f"Expected σ≈{true_sigma}, got {recovered:.8f}"
        )

    def test_implied_vol_round_trip_otm(self):
        """Round-trip for an out-of-the-money call (K > S)."""
        S, K, T, r, true_sigma = 100.0, 110.0, 0.5, 0.05, 0.30
        call_price = bs_call(S, K, T, r, true_sigma)
        recovered = implied_vol(call_price, S, K, T, r)
        assert recovered is not None
        assert abs(recovered - true_sigma) < 1e-5

    def test_implied_vol_round_trip_high_vol(self):
        """Round-trip for high-vol small-cap scenario (σ = 1.20)."""
        S, K, T, r, true_sigma = 15.0, 15.0, 30 / 365.0, 0.05, 1.20
        call_price = bs_call(S, K, T, r, true_sigma)
        recovered = implied_vol(call_price, S, K, T, r, initial_sigma=0.5)
        assert recovered is not None
        assert abs(recovered - true_sigma) < 1e-3  # looser tol for high vol

    def test_implied_vol_invalid_inputs(self):
        """Degenerate / invalid inputs must return None without raising."""
        assert implied_vol(-1.0, 100.0, 100.0, 1.0, 0.05) is None   # negative price
        assert implied_vol(0.0, 100.0, 100.0, 1.0, 0.05) is None    # zero price
        assert implied_vol(5.0, 100.0, 100.0, 0.0, 0.05) is None    # T=0
        assert implied_vol(5.0, 0.0, 100.0, 1.0, 0.05) is None      # S=0
        assert implied_vol(5.0, 100.0, 0.0, 1.0, 0.05) is None      # K=0

    def test_implied_vol_below_intrinsic(self):
        """
        A market price below intrinsic value is arbitrage-free impossible;
        the solver must return None rather than produce a garbage sigma.
        """
        S, K, T, r = 120.0, 100.0, 1.0, 0.05
        intrinsic = S - K * math.exp(-r * T)  # ≈ 24.76
        sub_intrinsic_price = intrinsic - 1.0
        assert implied_vol(sub_intrinsic_price, S, K, T, r) is None


# ===========================================================================
# 10-13: IV rank and percentile with synthetic history
# ===========================================================================

class TestIvRank:
    def test_iv_rank_known_values(self):
        """
        Insert 100 synthetic IV days (range 0.10 to 0.50).
        current_iv=0.30 → rank = (0.30-0.10)/(0.50-0.10) = 0.50 exactly.
        """
        db = _make_temp_db()
        try:
            ticker = "TESTRANK"
            # 100 evenly spaced values: 0.10, 0.1040, ..., 0.50
            values = [0.10 + 0.40 * i / 99 for i in range(100)]
            _seed_iv_history(db, ticker, values)

            rank = get_iv_rank(ticker, current_iv=0.30, lookback_days=252, db_path=db)
            assert rank is not None
            assert abs(rank - 0.50) < 0.02, f"Expected rank≈0.50, got {rank:.4f}"
        finally:
            _cleanup_db(db)

    def test_iv_rank_returns_none_below_min_history(self):
        """
        With only 10 days of history (< IV_MIN_HISTORY_DAYS=60), rank must be None.
        """
        db = _make_temp_db()
        try:
            _seed_iv_history(db, "THINHISTORY", [0.25] * 10)
            rank = get_iv_rank("THINHISTORY", current_iv=0.25, lookback_days=252, db_path=db)
            assert rank is None, f"Expected None for thin history, got {rank}"
        finally:
            _cleanup_db(db)

    def test_iv_rank_clamped_at_extremes(self):
        """
        IV above the historical max → rank clamped to 1.0.
        IV below the historical min → rank clamped to 0.0.
        """
        db = _make_temp_db()
        try:
            values = [0.20 + 0.30 * i / 99 for i in range(100)]  # 0.20 → 0.50
            _seed_iv_history(db, "CLAMP", values)

            rank_high = get_iv_rank("CLAMP", current_iv=0.80, lookback_days=252, db_path=db)
            rank_low = get_iv_rank("CLAMP", current_iv=0.01, lookback_days=252, db_path=db)
            assert rank_high == pytest.approx(1.0)
            assert rank_low == pytest.approx(0.0)
        finally:
            _cleanup_db(db)


class TestIvPercentile:
    def test_iv_percentile_uniform(self):
        """
        Uniform distribution 0.01..1.00 in 1-cent steps (100 values).
        current_iv=0.50 → percentile ≈ 0.49-0.51.
        """
        db = _make_temp_db()
        try:
            values = [round(0.01 * (i + 1), 4) for i in range(100)]  # 0.01 to 1.00
            _seed_iv_history(db, "UNIFORM", values)

            pct = get_iv_percentile("UNIFORM", current_iv=0.50, lookback_days=252, db_path=db)
            assert pct is not None
            assert abs(pct - 0.50) < 0.05, f"Expected percentile≈0.50, got {pct:.4f}"
        finally:
            _cleanup_db(db)

    def test_iv_percentile_returns_none_below_min_history(self):
        """< 60 days of data → percentile returns None."""
        db = _make_temp_db()
        try:
            _seed_iv_history(db, "THIN2", [0.30] * 10)
            pct = get_iv_percentile("THIN2", current_iv=0.30, lookback_days=252, db_path=db)
            assert pct is None
        finally:
            _cleanup_db(db)

    def test_iv_percentile_all_below(self):
        """IV above all history → percentile should be ~1.0."""
        db = _make_temp_db()
        try:
            values = [0.20] * 100
            _seed_iv_history(db, "ALLBELOW", values)
            pct = get_iv_percentile("ALLBELOW", current_iv=0.99, lookback_days=252, db_path=db)
            # After storing 0.99 for today, we have 101 rows; 100 are below 0.99
            assert pct is not None
            assert pct > 0.95
        finally:
            _cleanup_db(db)


# ===========================================================================
# 14-15: Combined get_iv_rank_and_percentile
# ===========================================================================

class TestGetIvRankAndPercentile:
    def test_returns_both_values(self):
        """Combined call returns (rank, percentile) both non-None when history exists."""
        db = _make_temp_db()
        try:
            values = [0.15 + 0.40 * i / 99 for i in range(100)]
            _seed_iv_history(db, "COMBO", values)
            rank, pct = get_iv_rank_and_percentile(
                "COMBO", current_iv=0.35, lookback_days=252, db_path=db
            )
            assert rank is not None
            assert pct is not None
            assert 0.0 <= rank <= 1.0
            assert 0.0 <= pct <= 1.0
        finally:
            _cleanup_db(db)

    def test_returns_none_none_for_thin_history(self):
        """(None, None) when fewer than IV_MIN_HISTORY_DAYS rows exist."""
        db = _make_temp_db()
        try:
            _seed_iv_history(db, "THIN3", [0.25] * 5)
            rank, pct = get_iv_rank_and_percentile(
                "THIN3", current_iv=0.25, lookback_days=252, db_path=db
            )
            assert rank is None
            assert pct is None
        finally:
            _cleanup_db(db)


# ===========================================================================
# 16: _store_iv / persistence
# ===========================================================================

class TestStoreIv:
    def test_store_then_retrieve(self):
        """
        _store_iv writes a row; reading the table directly should return it.
        """
        db = _make_temp_db()
        try:
            _ensure_db(db)
            _store_iv("SPY", 0.175, db)

            from utils.db import managed_connection
            with managed_connection(db) as conn:
                rows = conn.execute(
                    "SELECT iv30 FROM iv_history WHERE ticker='SPY'"
                ).fetchall()
            assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
            assert abs(rows[0][0] - 0.175) < 1e-9
        finally:
            _cleanup_db(db)

    def test_upsert_idempotent(self):
        """
        Two consecutive _store_iv calls on the same (ticker, date) must result
        in exactly one row (INSERT OR REPLACE behaviour).
        """
        db = _make_temp_db()
        try:
            _ensure_db(db)
            _store_iv("AAPL", 0.20, db)
            _store_iv("AAPL", 0.22, db)  # Same date → replace

            from utils.db import managed_connection
            with managed_connection(db) as conn:
                rows = conn.execute(
                    "SELECT iv30 FROM iv_history WHERE ticker='AAPL'"
                ).fetchall()
            assert len(rows) == 1, "Upsert must yield exactly one row per date"
            assert abs(rows[0][0] - 0.22) < 1e-9, "Second write must overwrite first"
        finally:
            _cleanup_db(db)


# ===========================================================================
# 17: compute_atm_iv — illiquid options (bid = 0) → None
# ===========================================================================

class TestComputeAtmIv:
    def _build_mock_chain(self, price: float, strike: float, call_bid: float,
                           call_ask: float, put_bid: float, put_ask: float):
        """Build a minimal mock yfinance options chain."""
        import pandas as pd

        calls_df = pd.DataFrame({
            "strike": [strike],
            "bid": [call_bid],
            "ask": [call_ask],
            "lastPrice": [call_ask],
        })
        puts_df = pd.DataFrame({
            "strike": [strike],
            "bid": [put_bid],
            "ask": [put_ask],
            "lastPrice": [put_ask],
        })

        mock_chain = MagicMock()
        mock_chain.calls = calls_df
        mock_chain.puts = puts_df
        return mock_chain

    def test_illiquid_call_bid_zero_returns_none(self):
        """
        When the ATM call has bid=0 (no active market), compute_atm_iv must
        return None rather than computing a meaningless IV.
        """
        from utils.iv_calculator import _compute_atm_iv_for_expiry

        mock_ticker = MagicMock()
        price = 100.0
        expiry_str = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        mock_ticker.option_chain.return_value = self._build_mock_chain(
            price=price,
            strike=100.0,
            call_bid=0.0,   # illiquid
            call_ask=1.5,
            put_bid=1.2,
            put_ask=1.5,
        )

        iv, _ = _compute_atm_iv_for_expiry(mock_ticker, expiry_str, price, r=0.05)
        assert iv is None, "bid=0 on call leg must return None"

    def test_illiquid_put_bid_zero_returns_none(self):
        """bid=0 on the put leg must also return None."""
        from utils.iv_calculator import _compute_atm_iv_for_expiry

        mock_ticker = MagicMock()
        price = 100.0
        expiry_str = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        mock_ticker.option_chain.return_value = self._build_mock_chain(
            price=price,
            strike=100.0,
            call_bid=1.2,
            call_ask=1.5,
            put_bid=0.0,   # illiquid put
            put_ask=1.5,
        )

        iv, _ = _compute_atm_iv_for_expiry(mock_ticker, expiry_str, price, r=0.05)
        assert iv is None, "bid=0 on put leg must return None"

    def test_liquid_chain_returns_float(self):
        """
        A liquid ATM straddle with valid bid/ask should yield a non-None,
        positive, plausible IV (between 5% and 500%).
        """
        from utils.iv_calculator import _compute_atm_iv_for_expiry

        mock_ticker = MagicMock()
        price = 100.0
        expiry_str = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        # Straddle mid = 1.5 + 1.5 = 3.0 → half = 1.50
        # ATM BS: σ ≈ straddle / (S * sqrt(2T/π)) ≈ 3.0 / (100 * sqrt(2*30/365/π)) ≈ 0.27
        mock_ticker.option_chain.return_value = self._build_mock_chain(
            price=price,
            strike=100.0,
            call_bid=1.40,
            call_ask=1.60,
            put_bid=1.40,
            put_ask=1.60,
        )

        iv, atm_strike = _compute_atm_iv_for_expiry(mock_ticker, expiry_str, price, r=0.05)
        assert iv is not None, "Liquid chain must return a non-None IV"
        assert 0.05 <= iv <= 5.0, f"IV {iv:.4f} outside plausible range [0.05, 5.0]"
        assert atm_strike == pytest.approx(100.0)

    def test_fewer_than_two_expirations_returns_none(self):
        """
        compute_atm_iv requires ≥ 2 expirations; with only one available
        it must return None without crashing.
        """
        from utils.iv_calculator import compute_atm_iv
        import pandas as pd

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = MagicMock(
            empty=False,
            __getitem__=lambda s, k: MagicMock(iloc=MagicMock(__getitem__=lambda s, i: 100.0)),
        )
        mock_ticker.options = ("2025-04-18",)  # Only one expiry

        with patch("utils.iv_calculator.yf.Ticker", return_value=mock_ticker):
            result = compute_atm_iv("ILLIQ", target_dte=30)
        assert result is None


# ===========================================================================
# 18: collect_and_store_iv — crypto filter
# ===========================================================================

class TestCollectAndStoreIv:
    def test_filters_crypto_tickers(self):
        """
        Crypto tickers (ending in -USD) must be silently skipped.
        No yfinance call should be attempted for them.
        """
        db = _make_temp_db()
        try:
            with patch("utils.iv_calculator.compute_atm_iv", return_value=None) as mock_compute:
                result = collect_and_store_iv(
                    ["AAPL", "BTC-USD", "ETH-USD", "NVDA"],
                    db_path=db,
                )
            # compute_atm_iv should be called for AAPL and NVDA only
            called_tickers = [call.args[0] for call in mock_compute.call_args_list]
            assert "BTC-USD" not in called_tickers, "BTC-USD must be filtered out"
            assert "ETH-USD" not in called_tickers, "ETH-USD must be filtered out"
            assert "AAPL" in called_tickers
            assert "NVDA" in called_tickers
        finally:
            _cleanup_db(db)

    def test_stores_successful_results(self):
        """
        collect_and_store_iv stores IVs for tickers that compute_atm_iv succeeds.
        """
        db = _make_temp_db()
        try:
            def fake_compute_atm_iv(ticker, target_dte=None):
                return {"AAPL": 0.22, "MSFT": 0.19}.get(ticker)

            with patch("utils.iv_calculator.compute_atm_iv", side_effect=fake_compute_atm_iv):
                result = collect_and_store_iv(["AAPL", "MSFT", "THIN"], db_path=db)

            assert result == {"AAPL": 0.22, "MSFT": 0.19}

            from utils.db import managed_connection
            with managed_connection(db) as conn:
                rows = conn.execute(
                    "SELECT ticker, iv30 FROM iv_history ORDER BY ticker"
                ).fetchall()
            tickers_stored = [r[0] for r in rows]
            assert "AAPL" in tickers_stored
            assert "MSFT" in tickers_stored
            assert "THIN" not in tickers_stored
        finally:
            _cleanup_db(db)
