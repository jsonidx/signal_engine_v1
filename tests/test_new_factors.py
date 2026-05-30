"""
Tests for new signal_engine factors (2026-03-22):
  - compute_earnings_revision: EPS proxy revision, winsorization, caching
  - compute_ivol: OLS residual std, sign convention, minimum-bars guard
  - compute_52wk_high_proximity: George-Hwang factor, edge cases
  - compute_equity_composite: graceful degradation, weight renormalization
  - config.EQUITY_FACTORS: weights sum to 1.0
"""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_engine import (
    compute_earnings_revision,
    compute_ivol,
    compute_52wk_high_proximity,
    compute_equity_composite,
)


# ── Synthetic data helpers ─────────────────────────────────────────────────────

def _price_series(n: int = 300, drift: float = 0.0003, vol: float = 0.012,
                  seed: int = 42) -> pd.Series:
    """Geometric random-walk price series, business-day indexed."""
    rng = np.random.default_rng(seed)
    log_rets = rng.normal(drift, vol, n)
    prices = 100.0 * np.exp(np.cumsum(log_rets))
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.Series(prices, index=dates, name="px")


def _price_df(n_tickers: int = 5, n_days: int = 300, seed: int = 0) -> pd.DataFrame:
    """Multi-ticker price DataFrame for composite tests."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B")
    data = {}
    for i in range(n_tickers):
        log_rets = rng.normal(0.0003, 0.012, n_days)
        data[f"T{i}"] = 100.0 * np.exp(np.cumsum(log_rets))
    return pd.DataFrame(data, index=dates)


# ==============================================================================
# Tests: compute_earnings_revision
# ==============================================================================

class TestEarningsRevision:

    def _mock_ticker(self, info_dict: dict):
        """Patch yfinance.Ticker so .info returns info_dict."""
        mock_inst = MagicMock()
        mock_inst.info = info_dict
        return patch("yfinance.Ticker", return_value=mock_inst)

    def _no_cache(self):
        """Patch fundamentals_cache so every call is a cache miss."""
        return (
            patch("signal_engine.get_cached", return_value=None),
            patch("signal_engine.save_to_cache"),
        )

    # ── Positive revision ────────────────────────────────────────────────────

    def test_positive_revision(self):
        """epsForward > epsCurrentYear → positive score."""
        info = {"epsForward": 5.0, "epsCurrentYear": 4.0}
        nc1, nc2 = self._no_cache()
        with self._mock_ticker(info), nc1, nc2:
            result = compute_earnings_revision("AAPL")

        assert result is not None
        assert result > 0.0
        # proxy = (5 - 4) / 4 = 0.25
        assert result == pytest.approx(0.25)

    # ── Negative revision ────────────────────────────────────────────────────

    def test_negative_revision(self):
        """epsForward < epsCurrentYear → negative score."""
        info = {"epsForward": 3.0, "epsCurrentYear": 4.0}
        nc1, nc2 = self._no_cache()
        with self._mock_ticker(info), nc1, nc2:
            result = compute_earnings_revision("MSFT")

        assert result is not None
        assert result < 0.0
        # proxy = (3 - 4) / 4 = -0.25
        assert result == pytest.approx(-0.25)

    # ── Winsorization ────────────────────────────────────────────────────────

    def test_winsorized_at_plus_50pct(self):
        """Extreme positive revision (>50%) is capped at +0.50."""
        info = {"epsForward": 10.0, "epsCurrentYear": 1.0}  # +900%
        nc1, nc2 = self._no_cache()
        with self._mock_ticker(info), nc1, nc2:
            result = compute_earnings_revision("GME")

        assert result == pytest.approx(0.50)

    def test_winsorized_at_minus_50pct(self):
        """Extreme negative revision (<-50%) is capped at -0.50."""
        info = {"epsForward": -5.0, "epsCurrentYear": 1.0}  # -600%
        nc1, nc2 = self._no_cache()
        with self._mock_ticker(info), nc1, nc2:
            result = compute_earnings_revision("BBBY")

        assert result == pytest.approx(-0.50)

    # ── Missing data → None ──────────────────────────────────────────────────

    def test_returns_none_when_no_eps_data(self):
        """Returns None (not NaN) when EPS fields are missing."""
        nc1, nc2 = self._no_cache()
        with self._mock_ticker({}), nc1, nc2:
            result = compute_earnings_revision("UNKNOWN")

        assert result is None

    def test_returns_none_when_eps_current_is_zero(self):
        """Returns None when epsCurrentYear == 0 (division by zero guard)."""
        info = {"epsForward": 1.0, "epsCurrentYear": 0.0}
        nc1, nc2 = self._no_cache()
        with self._mock_ticker(info), nc1, nc2:
            result = compute_earnings_revision("ZERO")

        assert result is None

    def test_returns_none_on_yfinance_exception(self):
        """Returns None when yfinance raises an exception."""
        nc1, nc2 = self._no_cache()
        with patch("yfinance.Ticker", side_effect=RuntimeError("network error")), \
             nc1, nc2:
            result = compute_earnings_revision("ERR")

        assert result is None

    # ── Cache behaviour ──────────────────────────────────────────────────────

    def test_cache_hit_skips_yfinance(self):
        """Cache hit returns stored value without calling yfinance."""
        cached_payload = {"eps_revision": 0.17}
        with patch("signal_engine.get_cached", return_value=cached_payload) as mc, \
             patch("yfinance.Ticker") as myt:
            result = compute_earnings_revision("AAPL")

        assert result == pytest.approx(0.17)
        myt.assert_not_called()  # yfinance never touched

    def test_cache_miss_saves_result(self):
        """After a cache miss, the computed value is saved to the cache."""
        info = {"epsForward": 5.0, "epsCurrentYear": 4.0}
        nc1, _ = self._no_cache()
        with self._mock_ticker(info), nc1, \
             patch("signal_engine.save_to_cache") as mock_save:
            compute_earnings_revision("AAPL")

        mock_save.assert_called_once()
        _, kwargs_or_pos = mock_save.call_args[0], mock_save.call_args
        saved_dict = mock_save.call_args[0][1]
        assert "eps_revision" in saved_dict
        assert saved_dict["eps_revision"] == pytest.approx(0.25)


# ==============================================================================
# Tests: compute_ivol
# ==============================================================================

class TestIvol:

    def _build_spy_and_ticker(self, n: int, spy_vol: float = 0.01,
                               noise_vol: float = 0.0, seed: int = 0):
        """
        Build aligned SPY and ticker price series.
        ticker = SPY + noise.  When noise_vol=0, residuals should be ~0.
        """
        rng = np.random.default_rng(seed)
        spy_ret = rng.normal(0.0004, spy_vol, n)
        noise   = rng.normal(0.0, noise_vol, n)
        tkr_ret = spy_ret + noise
        dates   = pd.date_range("2024-01-01", periods=n, freq="B")
        spy_px  = pd.Series(100.0 * np.exp(np.cumsum(spy_ret)), index=dates)
        tkr_px  = pd.Series(100.0 * np.exp(np.cumsum(tkr_ret)), index=dates)
        return tkr_px, spy_px

    # ── Basic sign and magnitude ─────────────────────────────────────────────

    def test_returns_negative_value(self):
        """IVOL is always returned as a negative number (quality convention)."""
        tkr_px, spy_px = self._build_spy_and_ticker(100, noise_vol=0.01)
        result = compute_ivol(tkr_px, spy_px, lookback=63)
        assert result is not None
        assert result < 0.0, f"Expected negative IVOL, got {result}"

    def test_low_noise_near_zero_ivol(self):
        """
        When ticker = SPY + constant drift (zero random noise),
        residuals are near zero → |IVOL| should be small (< 0.05 annualized).
        """
        n = 100
        rng = np.random.default_rng(7)
        spy_ret = rng.normal(0.0004, 0.01, n)
        # Deterministic offset — std of residuals should be essentially 0
        tkr_ret = spy_ret + 0.0005
        dates   = pd.date_range("2024-01-01", periods=n, freq="B")
        spy_px  = pd.Series(100.0 * np.exp(np.cumsum(spy_ret)), index=dates)
        tkr_px  = pd.Series(100.0 * np.exp(np.cumsum(tkr_ret)), index=dates)

        result = compute_ivol(tkr_px, spy_px, lookback=63)
        assert result is not None
        assert abs(result) < 0.05, f"Near-zero IVOL expected, got {result}"

    def test_high_noise_large_ivol(self):
        """Ticker with large idiosyncratic noise has more negative IVOL score."""
        tkr_high, spy = self._build_spy_and_ticker(100, noise_vol=0.03, seed=1)
        tkr_low,  _   = self._build_spy_and_ticker(100, noise_vol=0.002, seed=2)

        ivol_high = compute_ivol(tkr_high, spy, lookback=63)
        ivol_low  = compute_ivol(tkr_low,  spy, lookback=63)

        assert ivol_high is not None and ivol_low is not None
        # More noise → more negative (worse quality score)
        assert ivol_high < ivol_low, (
            f"High-noise IVOL ({ivol_high:.4f}) should be < low-noise ({ivol_low:.4f})"
        )

    # ── Residual std matches manual calculation ───────────────────────────────

    def test_ivol_matches_manual_ols(self):
        """
        Manually compute OLS residuals; verify compute_ivol returns
        -(std(residuals) * sqrt(252)) within floating-point tolerance.
        """
        rng = np.random.default_rng(99)
        n = 80
        spy_ret = rng.normal(0.0003, 0.01, n)
        noise   = rng.normal(0.0, 0.015, n)
        tkr_ret = spy_ret + noise
        dates   = pd.date_range("2024-01-01", periods=n, freq="B")
        spy_px  = pd.Series(100.0 * np.exp(np.cumsum(spy_ret)), index=dates)
        tkr_px  = pd.Series(100.0 * np.exp(np.cumsum(tkr_ret)), index=dates)

        result = compute_ivol(tkr_px, spy_px, lookback=n)

        # Manual replication
        log_spy = np.diff(np.log(spy_px.values))
        log_tkr = np.diff(np.log(tkr_px.values))
        var_spy  = np.var(log_spy, ddof=1)
        beta     = np.cov(log_tkr, log_spy, ddof=1)[0, 1] / var_spy
        alpha    = log_tkr.mean() - beta * log_spy.mean()
        eps      = log_tkr - (alpha + beta * log_spy)
        expected = -(np.std(eps, ddof=1) * np.sqrt(252))

        assert result == pytest.approx(expected, rel=1e-6)

    # ── Insufficient data ────────────────────────────────────────────────────

    def test_returns_none_when_fewer_than_40_bars(self):
        """Returns None when window has fewer than 40 aligned bars."""
        tkr_px, spy_px = self._build_spy_and_ticker(30)
        result = compute_ivol(tkr_px, spy_px, lookback=63)
        assert result is None

    def test_returns_none_when_lookback_leaves_under_40(self):
        """Even with 100 bars, a lookback that yields <40 after alignment → None."""
        tkr_px, spy_px = self._build_spy_and_ticker(100)
        # lookback=63 with 100 bars should give 63 aligned — OK
        # Force a mismatch so window < 40 after alignment
        result = compute_ivol(tkr_px.iloc[:25], spy_px, lookback=63)
        assert result is None

    def test_handles_misaligned_indices(self):
        """Tickers with different date ranges are aligned on overlap only."""
        rng = np.random.default_rng(5)
        dates_spy = pd.date_range("2024-01-01", periods=100, freq="B")
        dates_tkr = pd.date_range("2024-03-01", periods=70, freq="B")   # shorter/offset
        spy_px = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 100))), index=dates_spy)
        tkr_px = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.015, 70))), index=dates_tkr)
        result = compute_ivol(tkr_px, spy_px, lookback=63)
        # Should not raise; may be None if overlap < 40
        assert result is None or result < 0.0


# ==============================================================================
# Tests: compute_52wk_high_proximity
# ==============================================================================

class TestProximity52Wk:

    def test_price_at_52wk_high_gives_one(self):
        """Price ending exactly at its 252-bar max → proximity = 1.0."""
        px = _price_series(300)
        px.iloc[-1] = px.tail(252).max() + 1.0   # Ensure current IS the max
        result = compute_52wk_high_proximity(px)
        assert result == pytest.approx(1.0)

    def test_price_below_high_gives_lt_one(self):
        """Price below the 52-week high → proximity strictly < 1.0."""
        px = _price_series(300)
        px.iloc[-1] = px.tail(252).max() * 0.80
        result = compute_52wk_high_proximity(px)
        assert result is not None
        assert result < 1.0

    def test_proximity_in_unit_interval(self):
        """Proximity is always in (0, 1]."""
        for seed in range(10):
            px = _price_series(300, seed=seed)
            result = compute_52wk_high_proximity(px)
            assert result is not None
            assert 0.0 < result <= 1.0, f"seed={seed}: proximity={result}"

    def test_returns_none_when_fewer_than_126_bars(self):
        """Returns None when price series has < 126 bars."""
        px = _price_series(100)
        assert compute_52wk_high_proximity(px) is None

    def test_returns_none_on_exactly_125_bars(self):
        """Boundary: exactly 125 bars → None."""
        px = _price_series(125)
        assert compute_52wk_high_proximity(px) is None

    def test_works_on_exactly_126_bars(self):
        """Boundary: exactly 126 bars → value returned (not None)."""
        px = _price_series(126)
        result = compute_52wk_high_proximity(px)
        assert result is not None
        assert 0.0 < result <= 1.0

    def test_downtrending_stock_has_low_proximity(self):
        """Steadily falling prices → proximity well below 1.0."""
        n = 300
        # Monotonically decreasing prices
        px = pd.Series(
            np.linspace(200, 50, n),
            index=pd.date_range("2023-01-01", periods=n, freq="B"),
        )
        result = compute_52wk_high_proximity(px)
        assert result is not None
        assert result < 0.5, f"Downtrend should give low proximity, got {result}"

    def test_uses_only_last_252_bars_for_high(self):
        """
        A spike older than 252 bars must not inflate the 52-week high.
        After 252 bars, the spike falls out of window → proximity near 1.0.
        """
        n = 600
        px = _price_series(n)
        # Inject a massive spike more than 252 bars in the past
        px.iloc[0] = 1_000_000.0
        result = compute_52wk_high_proximity(px)
        # The old spike is out of the 252-bar window; proximity should be > 0.5
        assert result is not None
        assert result > 0.5, (
            f"Old spike should not suppress proximity; got {result}"
        )


# ==============================================================================
# Tests: compute_equity_composite — graceful degradation
# ==============================================================================

class TestCompositeGracefulDegradation:

    def _build_prices(self, n_tickers=5, n_days=300, seed=0):
        return _price_df(n_tickers, n_days, seed)

    def _mock_spy_download(self, prices_df):
        """Return a spy DataFrame covering the same date range."""
        rng = np.random.default_rng(999)
        spy_ret = rng.normal(0.0004, 0.01, len(prices_df))
        spy_px  = 100.0 * np.exp(np.cumsum(spy_ret))
        spy_df  = pd.DataFrame(
            {"Close": spy_px},
            index=prices_df.index,
        )
        return spy_df

    def test_composite_computed_without_earnings_revision(self):
        """
        When earnings_revision returns None for all tickers, composite_z
        is still non-NaN, renormalized from the remaining 6 factors.
        """
        prices = self._build_prices()
        spy_df = self._mock_spy_download(prices)

        with patch("signal_engine.compute_earnings_revision", return_value=None), \
             patch("signal_engine.yf.download", return_value=spy_df):
            result = compute_equity_composite(prices)

        assert not result.empty
        assert result["composite_z"].notna().all(), (
            "composite_z should be non-NaN even without eps_rev"
        )

    def test_composite_computed_without_ivol(self):
        """
        When SPY download fails (IVOL = None for all), composite_z is still
        computed from the remaining 6 factors.
        """
        prices = self._build_prices()

        # Simulate SPY download failure by returning empty DataFrame
        with patch("signal_engine.compute_earnings_revision", return_value=None), \
             patch("signal_engine.yf.download", return_value=pd.DataFrame()):
            result = compute_equity_composite(prices)

        assert not result.empty
        assert result["composite_z"].notna().all()

    def test_factors_used_column_present(self):
        """factors_used column is present and non-empty for all rows."""
        prices = self._build_prices()
        spy_df = self._mock_spy_download(prices)

        with patch("signal_engine.compute_earnings_revision", return_value=None), \
             patch("signal_engine.yf.download", return_value=spy_df):
            result = compute_equity_composite(prices)

        assert "factors_used" in result.columns
        assert (result["factors_used"].str.len() > 0).all(), (
            "factors_used should be non-empty for all tickers"
        )

    def test_factors_used_excludes_missing_factors(self):
        """
        When eps_rev is None, 'eps_rev' must NOT appear in factors_used.
        """
        prices = self._build_prices()
        spy_df = self._mock_spy_download(prices)

        with patch("signal_engine.compute_earnings_revision", return_value=None), \
             patch("signal_engine.yf.download", return_value=spy_df):
            result = compute_equity_composite(prices)

        for fu in result["factors_used"]:
            assert "eps_rev" not in fu, (
                f"eps_rev should be excluded when None, got factors_used='{fu}'"
            )

    def test_new_output_columns_present(self):
        """All new CSV columns are present in the output DataFrame."""
        prices = self._build_prices()
        spy_df = self._mock_spy_download(prices)

        with patch("signal_engine.compute_earnings_revision", return_value=None), \
             patch("signal_engine.yf.download", return_value=spy_df):
            result = compute_equity_composite(prices)

        expected_cols = {
            "earnings_revision_raw",
            "ivol_raw",
            "proximity_52wk",
            "factors_used",
            "momentum_skip_21d_correct",
        }
        missing = expected_cols - set(result.columns)
        assert not missing, f"Missing output columns: {missing}"

    def test_momentum_skip_21d_correct_is_true(self):
        """Sentinel column confirms trading-day-correct skip period."""
        prices = self._build_prices()
        spy_df = self._mock_spy_download(prices)

        with patch("signal_engine.compute_earnings_revision", return_value=None), \
             patch("signal_engine.yf.download", return_value=spy_df):
            result = compute_equity_composite(prices)

        assert result["momentum_skip_21d_correct"].all()

    def test_renormalized_weights_still_rank_tickers(self):
        """
        With a subset of factors, tickers are still ranked 1…N without ties
        on composite_z (i.e., no NaN contamination from missing factors).
        """
        prices = self._build_prices(n_tickers=10)
        spy_df = self._mock_spy_download(prices)

        with patch("signal_engine.compute_earnings_revision", return_value=None), \
             patch("signal_engine.yf.download", return_value=pd.DataFrame()):
            result = compute_equity_composite(prices)

        assert len(result) == 10
        assert result["rank"].nunique() == 10   # No ties in ranks


# ==============================================================================
# Tests: config.EQUITY_FACTORS weight integrity
# ==============================================================================

class TestConfigWeights:

    def test_equity_factor_weights_sum_to_one(self):
        """All EQUITY_FACTORS weights must sum to exactly 1.0."""
        from config import EQUITY_FACTORS
        total = sum(v["weight"] for v in EQUITY_FACTORS.values())
        assert abs(total - 1.0) < 1e-9, (
            f"EQUITY_FACTORS weights sum to {total:.10f}, expected 1.0"
        )

    def test_new_factors_present_in_config(self):
        """New factors are present in EQUITY_FACTORS."""
        from config import EQUITY_FACTORS
        assert "earnings_revision"   in EQUITY_FACTORS
        assert "ivol"                in EQUITY_FACTORS
        assert "52wk_high_proximity" in EQUITY_FACTORS

    def test_risk_adjusted_momentum_removed(self):
        """risk_adjusted_momentum has been replaced by new factors."""
        from config import EQUITY_FACTORS
        assert "risk_adjusted_momentum" not in EQUITY_FACTORS

    def test_new_factor_weights_nonzero(self):
        """Each new factor has a positive, nonzero weight."""
        from config import EQUITY_FACTORS
        for key in ("earnings_revision", "ivol", "52wk_high_proximity"):
            assert EQUITY_FACTORS[key]["weight"] > 0.0, (
                f"{key} has zero/negative weight"
            )


# ==============================================================================
# TRD-001 — Pre-Earnings Breakout Detector
# ==============================================================================

class TestPreEarningsBreakoutDetector:
    """
    Unit tests for catalyst_screener.score_pre_earnings_breakout.

    All inputs are static dicts — no live yfinance, Supabase, or SEC calls.
    """

    def _call(self, **kwargs):
        from catalyst_screener import score_pre_earnings_breakout
        return score_pre_earnings_breakout(kwargs)

    # ── Bullish case (SNOW-like) ──────────────────────────────────────────────

    def test_snow_like_setup_fires(self):
        """SNOW-like inputs (beat streak ≥3, momentum ≥5%, volume+options ≥3) must fire."""
        result = self._call(
            days_to_earnings=2,
            earnings_beat_streak=4,
            earnings_surprise_avg_pct=8.5,
            momentum_1m_pct=9.2,
            volume_score=4.0,
            options_score=4.0,
            dark_pool_signal="NEUTRAL",
            short_pct_float=5.1,
        )
        assert result["pre_earnings_breakout"] is True
        assert result["confidence"] in ("medium", "high")
        assert result["requires_high_short_interest"] is False

    def test_high_confidence_all_conditions(self):
        """Four conditions met → high confidence."""
        result = self._call(
            days_to_earnings=5,
            earnings_beat_streak=5,
            earnings_surprise_avg_pct=12.0,
            momentum_1m_pct=10.0,
            volume_score=4.0,
            options_score=4.0,
            dark_pool_signal="ACCUMULATION",
            short_pct_float=4.0,
        )
        assert result["pre_earnings_breakout"] is True
        assert result["confidence"] == "high"

    # ── Neutral case ─────────────────────────────────────────────────────────

    def test_only_one_condition_does_not_fire(self):
        """Single condition (beat streak only, weak momentum, no volume/options) → no flag."""
        result = self._call(
            days_to_earnings=10,
            earnings_beat_streak=3,
            earnings_surprise_avg_pct=5.0,
            momentum_1m_pct=1.0,    # below threshold
            volume_score=1.0,       # below threshold
            options_score=1.0,      # below threshold
            dark_pool_signal="NEUTRAL",
            short_pct_float=6.0,
        )
        assert result["pre_earnings_breakout"] is False

    def test_no_earnings_window_does_not_fire(self):
        """Outside earnings window (days_to_earnings=None or >30) → no flag."""
        result_none = self._call(
            days_to_earnings=None,
            earnings_beat_streak=5,
            earnings_surprise_avg_pct=15.0,
            momentum_1m_pct=12.0,
            volume_score=5.0,
            options_score=5.0,
            dark_pool_signal="ACCUMULATION",
            short_pct_float=5.0,
        )
        assert result_none["pre_earnings_breakout"] is False

        result_far = self._call(
            days_to_earnings=45,
            earnings_beat_streak=5,
            earnings_surprise_avg_pct=15.0,
            momentum_1m_pct=12.0,
            volume_score=5.0,
            options_score=5.0,
            dark_pool_signal="ACCUMULATION",
            short_pct_float=5.0,
        )
        assert result_far["pre_earnings_breakout"] is False

    # ── False-positive guard ──────────────────────────────────────────────────

    def test_no_beat_streak_does_not_fire(self):
        """Even with strong momentum/volume/options, zero beat streak → no flag."""
        result = self._call(
            days_to_earnings=3,
            earnings_beat_streak=0,  # no history
            earnings_surprise_avg_pct=0.0,
            momentum_1m_pct=12.0,
            volume_score=5.0,
            options_score=5.0,
            dark_pool_signal="ACCUMULATION",
            short_pct_float=5.0,
        )
        assert result["pre_earnings_breakout"] is False, (
            "Beat streak is required — momentum alone is not enough"
        )

    def test_negative_momentum_does_not_fire(self):
        """Beat streak + negative momentum → no flag (price says market disagrees)."""
        result = self._call(
            days_to_earnings=5,
            earnings_beat_streak=4,
            earnings_surprise_avg_pct=8.0,
            momentum_1m_pct=-3.0,   # negative
            volume_score=4.0,
            options_score=4.0,
            dark_pool_signal="NEUTRAL",
            short_pct_float=5.0,
        )
        assert result["pre_earnings_breakout"] is False, (
            "Negative momentum contradicts re-rating thesis"
        )

    # ── Squeeze independence ──────────────────────────────────────────────────

    def test_does_not_require_high_short_interest(self):
        """Detector must work regardless of short interest level."""
        for si in (1.0, 5.0, 10.0, 30.0, 50.0):
            result = self._call(
                days_to_earnings=7,
                earnings_beat_streak=3,
                earnings_surprise_avg_pct=6.0,
                momentum_1m_pct=6.0,
                volume_score=3.5,
                options_score=3.5,
                dark_pool_signal="NEUTRAL",
                short_pct_float=si,
            )
            assert result["requires_high_short_interest"] is False, (
                f"requires_high_short_interest must always be False (SI={si})"
            )

    def test_score_max_field_always_5(self):
        """score_pre_earnings_breakout always returns max=5."""
        result = self._call(
            days_to_earnings=5,
            earnings_beat_streak=3,
            earnings_surprise_avg_pct=6.0,
            momentum_1m_pct=6.0,
            volume_score=3.0,
            options_score=3.0,
            dark_pool_signal="NEUTRAL",
            short_pct_float=5.0,
        )
        assert result["max"] == 5

    def test_derive_earnings_beat_stats_from_yfinance_history(self):
        """Beat stats should be derived from earnings_history when info keys are absent."""
        from catalyst_screener import _derive_earnings_beat_stats

        mock_stock = MagicMock()
        mock_stock.earnings_history = pd.DataFrame(
            {"Surprise(%)": [0.10, 0.08, -0.02]},
            index=pd.to_datetime(["2026-05-01", "2026-02-01", "2025-11-01"]),
        )

        result = _derive_earnings_beat_stats({"info": {}, "stock_obj": mock_stock})

        assert result["earnings_beat_streak"] == 2
        assert result["earnings_surprise_avg_pct"] == pytest.approx((10 + 8 - 2) / 3)

    def test_derive_earnings_beat_stats_preserves_explicit_info_keys(self):
        """Fixture/vendor info keys should remain supported."""
        from catalyst_screener import _derive_earnings_beat_stats

        result = _derive_earnings_beat_stats({
            "info": {"earningsBeatStreak": 4, "earningsSurpriseAvgPct": 18.7},
            "stock_obj": None,
        })

        assert result == {
            "earnings_beat_streak": 4,
            "earnings_surprise_avg_pct": 18.7,
        }


# ==============================================================================
# TRD-001 hardening — _derive_earnings_beat_stats parser
# ==============================================================================

class TestDeriveEarningsBeatStats:
    """
    Unit tests for catalyst_screener._derive_earnings_beat_stats.
    All inputs are static dicts/DataFrames — no live yfinance calls.
    """

    def _call(self, data: dict):
        from catalyst_screener import _derive_earnings_beat_stats
        return _derive_earnings_beat_stats(data)

    # ── Direct info-key path ──────────────────────────────────────────────────

    def test_info_keys_take_priority(self):
        """When info has earningsBeatStreak, use it without touching stock_obj."""
        result = self._call({
            "info": {"earningsBeatStreak": 4, "earningsSurpriseAvgPct": 8.5},
            "stock_obj": None,
        })
        assert result["earnings_beat_streak"] == 4
        assert result["earnings_surprise_avg_pct"] == 8.5

    def test_info_key_partial_uses_default_for_missing(self):
        """Only one info key present → other defaults to 0."""
        result = self._call({"info": {"earningsBeatStreak": 3}, "stock_obj": None})
        assert result["earnings_beat_streak"] == 3
        assert result["earnings_surprise_avg_pct"] == 0.0

    # ── earnings_history path ─────────────────────────────────────────────────

    def test_surprise_col_detected(self):
        """Columns with 'surprise' in name are used directly.

        yfinance indexes earnings_history by date ascending; the code
        sorts descending so the newest quarter ends up first.  Construct
        the fixture with ascending dates so the intended newest value
        (first beat) becomes first after the descending sort.
        """
        import pandas as pd

        # Ascending-date order (oldest→newest): -0.02, 0.05, 0.12, 0.08
        # After sort_index descending (newest first): 0.08, 0.12, 0.05, -0.02
        # Values ≤1 → ×100: [8, 12, 5, -2]
        # Beat streak: 3 (8, 12, 5 are beats; -2 breaks)
        dates = pd.date_range("2025-06-01", periods=4, freq="QE")
        hist = pd.DataFrame(
            {"epsSurprisePct": [-0.02, 0.05, 0.12, 0.08]},
            index=dates,
        )
        mock_obj = type("T", (), {"earnings_history": hist})()
        result = self._call({"info": {}, "stock_obj": mock_obj})
        assert result["earnings_beat_streak"] == 3
        assert result["earnings_surprise_avg_pct"] > 0

    def test_estimate_actual_cols_fallback(self):
        """When no 'surprise' col, derive from estimate + actual."""
        import pandas as pd

        # All quarters beat: actual > estimate
        dates = pd.date_range("2025-06-01", periods=4, freq="QE")
        hist = pd.DataFrame({
            "epsEstimate": [1.0, 1.2, 0.9, 1.1],
            "epsActual":   [1.1, 1.3, 1.0, 1.2],  # all beat
        }, index=dates)
        mock_obj = type("T", (), {"earnings_history": hist})()
        result = self._call({"info": {}, "stock_obj": mock_obj})
        assert result["earnings_beat_streak"] >= 1

    def test_non_dataframe_hist_returns_zeros(self):
        """If earnings_history is a dict or string, return safe zeros."""
        mock_obj = type("T", (), {"earnings_history": {"bad": "data"}})()
        result = self._call({"info": {}, "stock_obj": mock_obj})
        assert result["earnings_beat_streak"] == 0
        assert result["earnings_surprise_avg_pct"] == 0.0

    def test_empty_dataframe_returns_zeros(self):
        """Empty DataFrame → zeros."""
        import pandas as pd
        mock_obj = type("T", (), {"earnings_history": pd.DataFrame()})()
        result = self._call({"info": {}, "stock_obj": mock_obj})
        assert result["earnings_beat_streak"] == 0

    def test_no_stock_obj_returns_zeros(self):
        """No stock_obj and no info keys → zeros (not exception)."""
        result = self._call({"info": {}, "stock_obj": None})
        assert result["earnings_beat_streak"] == 0
        assert result["earnings_surprise_avg_pct"] == 0.0

    def test_exception_in_earnings_history_returns_zeros(self):
        """If earnings_history raises → return zeros safely."""
        class BadObj:
            @property
            def earnings_history(self):
                raise RuntimeError("yfinance broken")
        result = self._call({"info": {}, "stock_obj": BadObj()})
        assert result["earnings_beat_streak"] == 0

    def test_all_misses_streak_is_zero(self):
        """All negative surprises → beat streak = 0."""
        import pandas as pd
        dates = pd.date_range("2025-06-01", periods=4, freq="QE")
        hist = pd.DataFrame(
            {"epsSurprisePct": [-0.05, -0.10, -0.02, -0.08]},
            index=dates,
        )
        mock_obj = type("T", (), {"earnings_history": hist})()
        result = self._call({"info": {}, "stock_obj": mock_obj})
        assert result["earnings_beat_streak"] == 0

    def test_percentage_surprises_not_multiplied(self):
        """Values >1 in surprise col are treated as percentage already.

        Construct with ascending dates so after descending sort newest=8.0:
        ascending: [3.0, -2.0, 5.0, 8.0] → descending: [8.0, 5.0, -2.0, 3.0]
        Beats: 8 then 5; -2 breaks → streak = 2.
        """
        import pandas as pd
        dates = pd.date_range("2025-06-01", periods=4, freq="QE")
        hist = pd.DataFrame(
            {"epsSurprisePct": [3.0, -2.0, 5.0, 8.0]},
            index=dates,
        )
        mock_obj = type("T", (), {"earnings_history": hist})()
        result = self._call({"info": {}, "stock_obj": mock_obj})
        # Values >1 → no ×100; newest first after sort: [8, 5, -2, 3]
        # Streak: 8 (beat), 5 (beat), -2 (miss) → 2
        assert result["earnings_beat_streak"] == 2


# ==============================================================================
# TRD-001 hardening — momentum_1m_pct from score_technical_setup
# ==============================================================================

class TestTechnicalSetupMomentum:
    """Verify score_technical_setup now returns momentum_1m_pct."""

    def test_momentum_key_present(self):
        """score_technical_setup must return momentum_1m_pct for sufficient history."""
        import numpy as np
        import pandas as pd
        from catalyst_screener import score_technical_setup

        n = 60
        prices = pd.Series(
            100.0 * np.exp(np.cumsum(np.random.default_rng(42).normal(0.001, 0.01, n))),
            index=pd.date_range("2026-01-01", periods=n, freq="B"),
            name="Close",
        )
        hist = pd.DataFrame({"Close": prices, "Volume": 1e6})
        data = {"history": hist}
        result = score_technical_setup(data)
        assert "momentum_1m_pct" in result, (
            "score_technical_setup must return momentum_1m_pct for pre-earnings detector"
        )
        assert isinstance(result["momentum_1m_pct"], float)

    def test_momentum_matches_22day_return(self):
        """momentum_1m_pct should equal (close[-1]/close[-22] - 1) * 100."""
        import numpy as np
        import pandas as pd
        from catalyst_screener import score_technical_setup

        n = 60
        prices = pd.Series(
            100.0 * np.exp(np.cumsum(np.random.default_rng(7).normal(0.002, 0.01, n))),
            index=pd.date_range("2026-01-01", periods=n, freq="B"),
            name="Close",
        )
        hist = pd.DataFrame({"Close": prices, "Volume": 1e6})
        data = {"history": hist}
        result = score_technical_setup(data)
        expected = (prices.iloc[-1] / prices.iloc[-22] - 1) * 100
        assert abs(result["momentum_1m_pct"] - expected) < 1e-6
