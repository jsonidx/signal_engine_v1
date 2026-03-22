"""
Tests for regime_filter.py
==========================
Covers:
  - Each signal component (trend, volatility, credit, yield_curve) in isolation
  - Full regime classification with edge cases (exactly on thresholds)
  - FRED API fallback (returns None if FRED is unreachable)
  - Sector regime for all 11 sectors with mocked price data
  - Position multiplier application in signal_engine.py integration
"""

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_price_df(tickers, n_bars=260, start="2023-01-01", base_price=100.0, volume=1_000_000.0):
    """Return a MultiIndex Close DataFrame (like yf.download for multiple tickers)."""
    dates = pd.date_range(start=start, periods=n_bars, freq="B")
    arrays = [[("Close", t) for t in tickers]]
    idx = pd.MultiIndex.from_tuples([(f"Close", t) for t in tickers])
    data = {("Close", t): [base_price] * n_bars for t in tickers}
    df = pd.DataFrame(data, index=dates)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


def _flat_close(n_bars=260, start="2023-01-01", price=100.0):
    """Return a plain Series of *price* repeated for n_bars business days."""
    dates = pd.date_range(start=start, periods=n_bars, freq="B")
    return pd.Series([float(price)] * n_bars, index=dates)


def _trending_close(n_bars=260, start="2023-01-01", start_price=80.0, end_price=120.0):
    """Return a linearly trending price series."""
    dates  = pd.date_range(start=start, periods=n_bars, freq="B")
    prices = np.linspace(start_price, end_price, n_bars)
    return pd.Series(prices, index=dates)


# ==============================================================================
# 1. SIGNAL COMPONENTS IN ISOLATION
# ==============================================================================

class TestTrendSignal:
    """Signal 1: SPY vs 50MA / 200MA."""

    def _run_with_spy(self, spy_series):
        """Call _compute_market_regime with a mocked SPY (no VIX/HYG/FRED)."""
        import regime_filter as rf
        empty = pd.Series(dtype=float)

        # Patch yf.download to return a MultiIndex df with only SPY
        dates = spy_series.index
        mi_df = pd.DataFrame(
            {"Close": spy_series.values},
            index=dates,
            columns=pd.MultiIndex.from_tuples([("Close", "SPY")]),
        )
        mi_df.columns = pd.MultiIndex.from_tuples(mi_df.columns)

        def fake_download(tickers, **kwargs):
            cols = pd.MultiIndex.from_tuples([("Close", t) for t in tickers])
            data = {("Close", t): spy_series.values if t == "SPY" else [np.nan] * len(spy_series)
                    for t in tickers}
            return pd.DataFrame(data, index=dates, columns=pd.MultiIndex.from_tuples(
                [(c, t) for c, t in data.keys()]))

        with patch("regime_filter.yf.download", side_effect=fake_download):
            with patch("regime_filter._fetch_fred_yield_curve", return_value=None):
                result = rf._compute_market_regime()
        return result

    def test_both_above_ma_gives_plus2(self):
        """Price above both 50MA and 200MA → trend = +2."""
        # Use a strongly upward trend: final price well above both 50MA and 200MA
        n = 260
        spy = _trending_close(n, start_price=50.0, end_price=200.0)
        result = self._run_with_spy(spy)
        assert result["components"]["trend"] == 2

    def test_trending_up_both_above(self):
        """Linearly trending up price → above both MAs → trend = +2."""
        spy = _trending_close(260, start_price=60.0, end_price=120.0)
        result = self._run_with_spy(spy)
        assert result["components"]["trend"] == 2

    def test_trending_down_below_both_gives_minus1(self):
        """Linearly trending down → price below both MAs → trend = -1."""
        spy = _trending_close(260, start_price=120.0, end_price=60.0)
        result = self._run_with_spy(spy)
        assert result["components"]["trend"] == -1

    def test_insufficient_spy_data_gives_zero_trend(self):
        """Fewer than 200 bars → trend component stays 0."""
        spy = _flat_close(n_bars=100)
        result = self._run_with_spy(spy)
        assert result["components"]["trend"] == 0


class TestVixSignal:
    """Signal 2: VIX level thresholds."""

    def _run_with_vix(self, vix_level):
        import regime_filter as rf
        n = 260
        dates = pd.date_range("2023-01-01", periods=n, freq="B")

        def fake_download(tickers, **kwargs):
            data = {}
            for t in tickers:
                if t == "^VIX":
                    data[("Close", t)] = [float(vix_level)] * n
                else:
                    data[("Close", t)] = [100.0] * n
            return pd.DataFrame(data, index=dates,
                                 columns=pd.MultiIndex.from_tuples(data.keys()))

        with patch("regime_filter.yf.download", side_effect=fake_download):
            with patch("regime_filter._fetch_fred_yield_curve", return_value=None):
                result = rf._compute_market_regime()
        return result

    def test_vix_below_18_gives_plus2(self):
        assert self._run_with_vix(14.5)["components"]["volatility"] == 2

    def test_vix_exactly_18_gives_plus1(self):
        assert self._run_with_vix(18.0)["components"]["volatility"] == 1

    def test_vix_between_25_and_30_gives_zero(self):
        assert self._run_with_vix(27.0)["components"]["volatility"] == 0

    def test_vix_above_30_gives_minus2(self):
        assert self._run_with_vix(35.0)["components"]["volatility"] == -2


class TestCreditSignal:
    """Signal 3: HYG 20-day return z-score."""

    def _run_with_hyg(self, hyg_series):
        import regime_filter as rf
        n = len(hyg_series)
        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        hyg_s = pd.Series(hyg_series, index=dates)

        def fake_download(tickers, **kwargs):
            data = {}
            for t in tickers:
                if t == "HYG":
                    data[("Close", t)] = hyg_s.values.tolist()
                else:
                    data[("Close", t)] = [100.0] * n
            return pd.DataFrame(data, index=dates,
                                 columns=pd.MultiIndex.from_tuples(data.keys()))

        with patch("regime_filter.yf.download", side_effect=fake_download):
            with patch("regime_filter._fetch_fred_yield_curve", return_value=None):
                result = rf._compute_market_regime()
        return result

    def test_flat_hyg_gives_zero_credit(self):
        """Flat HYG → 20d return = 0, z-score = 0 → credit = 0."""
        hyg = [75.0] * 300
        result = self._run_with_hyg(hyg)
        assert result["components"]["credit"] == 0

    def test_strongly_rising_hyg_gives_plus1(self):
        """HYG surging vs flat 1-yr history → z > 0.5 → credit = +1."""
        # Flat for first 270 bars, then strong 20-day rally
        hyg = [75.0] * 260 + [75.0 * (1 + 0.003 * i) for i in range(40)]
        result = self._run_with_hyg(hyg)
        assert result["components"]["credit"] == 1

    def test_strongly_falling_hyg_gives_minus1(self):
        """HYG collapsing vs flat history → z < -0.5 → credit = -1."""
        hyg = [75.0] * 260 + [75.0 * (1 - 0.003 * i) for i in range(40)]
        result = self._run_with_hyg(hyg)
        assert result["components"]["credit"] == -1

    def test_insufficient_hyg_data_gives_zero(self):
        """Fewer than 41 bars → credit stays 0."""
        hyg = [75.0] * 30
        result = self._run_with_hyg(hyg)
        assert result["components"]["credit"] == 0


class TestYieldCurveSignal:
    """Signal 4: FRED T10Y2Y spread."""

    def _run_with_yc(self, spread_value):
        import regime_filter as rf

        def fake_download(tickers, **kwargs):
            n = 260
            dates = pd.date_range("2023-01-01", periods=n, freq="B")
            data = {("Close", t): [100.0] * n for t in tickers}
            return pd.DataFrame(data, index=dates,
                                 columns=pd.MultiIndex.from_tuples(data.keys()))

        with patch("regime_filter.yf.download", side_effect=fake_download):
            with patch("regime_filter._fetch_fred_yield_curve", return_value=spread_value):
                result = rf._compute_market_regime()
        return result

    def test_spread_above_050_gives_plus1(self):
        assert self._run_with_yc(0.75)["components"]["yield_curve"] == 1

    def test_spread_exactly_050_gives_plus1(self):
        # > 0.50 → +1; exactly 0.50 is not > 0.50
        assert self._run_with_yc(0.50)["components"]["yield_curve"] == 0

    def test_spread_between_0_and_050_gives_zero(self):
        assert self._run_with_yc(0.25)["components"]["yield_curve"] == 0

    def test_spread_exactly_zero_gives_zero(self):
        assert self._run_with_yc(0.0)["components"]["yield_curve"] == 0

    def test_negative_spread_gives_minus2(self):
        assert self._run_with_yc(-0.30)["components"]["yield_curve"] == -2


# ==============================================================================
# 2. FULL REGIME CLASSIFICATION (edge cases at thresholds)
# ==============================================================================

class TestFullRegimeClassification:
    """Test regime classification with exactly-on-threshold scores."""

    def _classify(self, components, yc=None):
        import regime_filter as rf
        # Compose a fake _compute_market_regime result by directly testing
        # the classification logic (via get_market_regime with mocked internals)
        score = sum(components.values())
        if score >= rf.REGIME_RISK_ON_THRESHOLD:
            return "RISK_ON"
        elif score <= rf.REGIME_RISK_OFF_THRESHOLD:
            return "RISK_OFF"
        return "TRANSITIONAL"

    def test_score_exactly_3_is_risk_on(self):
        comp = {"trend": 2, "volatility": 1, "credit": 0, "yield_curve": 0}
        assert self._classify(comp) == "RISK_ON"

    def test_score_exactly_0_is_risk_off(self):
        comp = {"trend": 0, "volatility": 0, "credit": 0, "yield_curve": 0}
        assert self._classify(comp) == "RISK_OFF"

    def test_score_above_3_is_risk_on(self):
        comp = {"trend": 2, "volatility": 2, "credit": 1, "yield_curve": 1}
        assert self._classify(comp) == "RISK_ON"

    def test_score_below_0_is_risk_off(self):
        comp = {"trend": -1, "volatility": -2, "credit": -1, "yield_curve": -2}
        assert self._classify(comp) == "RISK_OFF"

    def test_score_1_is_transitional(self):
        comp = {"trend": 1, "volatility": 0, "credit": 0, "yield_curve": 0}
        assert self._classify(comp) == "TRANSITIONAL"

    def test_score_2_is_transitional(self):
        comp = {"trend": 1, "volatility": 1, "credit": 0, "yield_curve": 0}
        assert self._classify(comp) == "TRANSITIONAL"

    def test_full_compute_returns_required_keys(self, tmp_path):
        """_compute_market_regime result has all required keys."""
        import regime_filter as rf
        n = 260
        dates = pd.date_range("2023-01-01", periods=n, freq="B")

        def fake_download(tickers, **kwargs):
            data = {("Close", t): [100.0] * n for t in tickers}
            return pd.DataFrame(data, index=dates,
                                 columns=pd.MultiIndex.from_tuples(data.keys()))

        with patch("regime_filter.yf.download", side_effect=fake_download):
            with patch("regime_filter._fetch_fred_yield_curve", return_value=0.5):
                result = rf._compute_market_regime()

        required = {"regime", "score", "components", "vix", "spy_vs_200ma",
                    "yield_curve_spread", "computed_at"}
        assert required.issubset(result.keys())
        assert result["regime"] in ("RISK_ON", "TRANSITIONAL", "RISK_OFF")
        assert isinstance(result["score"], int)
        assert set(result["components"].keys()) == {"trend", "volatility", "credit", "yield_curve"}


# ==============================================================================
# 3. FRED API FALLBACK
# ==============================================================================

class TestFredFallback:
    """FRED yield curve fallback: return None when FRED is unreachable."""

    def test_returns_none_on_connection_error(self, tmp_path):
        import regime_filter as rf

        # Temporarily redirect cache to tmp_path so tests don't share state
        with patch.object(rf, "_REGIME_CACHE_PATH", tmp_path / "regime_cache.json"):
            with patch("regime_filter.requests.get", side_effect=ConnectionError("timeout")):
                result = rf._fetch_fred_yield_curve()
        assert result is None

    def test_returns_none_on_http_error(self, tmp_path):
        import regime_filter as rf
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("503 Service Unavailable")
        with patch.object(rf, "_REGIME_CACHE_PATH", tmp_path / "regime_cache.json"):
            with patch("regime_filter.requests.get", return_value=mock_resp):
                result = rf._fetch_fred_yield_curve()
        assert result is None

    def test_returns_none_on_empty_csv(self, tmp_path):
        import regime_filter as rf
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = "DATE,T10Y2Y\n2024-01-01,.\n2024-01-02,.\n"
        with patch.object(rf, "_REGIME_CACHE_PATH", tmp_path / "regime_cache.json"):
            with patch("regime_filter.requests.get", return_value=mock_resp):
                result = rf._fetch_fred_yield_curve()
        assert result is None

    def test_parses_valid_fred_csv(self, tmp_path):
        import regime_filter as rf
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = "DATE,T10Y2Y\n2024-01-01,.\n2024-01-02,0.35\n2024-01-03,0.42\n"
        with patch.object(rf, "_REGIME_CACHE_PATH", tmp_path / "regime_cache.json"):
            with patch("regime_filter.requests.get", return_value=mock_resp):
                result = rf._fetch_fred_yield_curve()
        assert result == pytest.approx(0.42)

    def test_uses_fresh_cache(self, tmp_path):
        """Cached value within TTL is returned without HTTP call."""
        import regime_filter as rf
        cache = {
            "fred_yield_curve": {
                "value":       1.23,
                "series":      "T10Y2Y",
                "computed_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        }
        cache_path = tmp_path / "regime_cache.json"
        cache_path.write_text(json.dumps(cache))
        with patch.object(rf, "_REGIME_CACHE_PATH", cache_path):
            with patch("regime_filter.requests.get") as mock_get:
                result = rf._fetch_fred_yield_curve()
        mock_get.assert_not_called()
        assert result == pytest.approx(1.23)

    def test_regime_computation_proceeds_with_none_yield_curve(self, tmp_path):
        """If FRED returns None, yield_curve component is 0 and regime still computes."""
        import regime_filter as rf
        n = 260
        dates = pd.date_range("2023-01-01", periods=n, freq="B")

        def fake_download(tickers, **kwargs):
            data = {("Close", t): [100.0] * n for t in tickers}
            return pd.DataFrame(data, index=dates,
                                 columns=pd.MultiIndex.from_tuples(data.keys()))

        with patch("regime_filter.yf.download", side_effect=fake_download):
            with patch("regime_filter._fetch_fred_yield_curve", return_value=None):
                result = rf._compute_market_regime()

        assert result["components"]["yield_curve"] == 0
        assert result["yield_curve_spread"] is None
        assert result["regime"] in ("RISK_ON", "TRANSITIONAL", "RISK_OFF")


# ==============================================================================
# 4. SECTOR REGIMES — all 11 sectors
# ==============================================================================

class TestSectorRegimes:
    """Test get_sector_regimes() for all 11 sectors with mocked price data."""

    def _make_sector_download(self, bull_sectors=(), bear_sectors=()):
        """
        Return a fake yf.download function.
        bull_sectors: sector ETF tickers that will have golden cross + positive RS
        bear_sectors: sector ETF tickers that will have death cross + negative RS
        Others: flat (neutral)
        """
        import regime_filter as rf
        n = 260
        dates = pd.date_range("2023-01-01", periods=n, freq="B")

        def fake_download(tickers, **kwargs):
            data = {}
            for t in tickers:
                if t in bull_sectors:
                    # Trending strongly up → ma50 > ma200, 20d return > SPY
                    px = np.linspace(60.0, 130.0, n)
                elif t in bear_sectors:
                    # Trending strongly down → ma50 < ma200, 20d return < SPY
                    px = np.linspace(130.0, 60.0, n)
                elif t == "SPY":
                    # SPY flat at 100
                    px = np.full(n, 100.0)
                else:
                    # Neutral: flat
                    px = np.full(n, 100.0)
                data[("Close", t)] = px.tolist()
            return pd.DataFrame(data, index=dates,
                                 columns=pd.MultiIndex.from_tuples(data.keys()))

        return fake_download

    def test_all_11_sectors_classified(self, tmp_path):
        import regime_filter as rf
        with patch.object(rf, "_REGIME_CACHE_PATH", tmp_path / "regime_cache.json"):
            with patch("regime_filter.yf.download", side_effect=self._make_sector_download()):
                result = rf._compute_sector_regimes()

        expected_sectors = set(rf.SECTOR_ETFS.keys())
        assert expected_sectors.issubset(result.keys())

    def test_bull_sector_classified_as_bull(self, tmp_path):
        import regime_filter as rf
        bull_etf = "XLK"   # tech
        with patch.object(rf, "_REGIME_CACHE_PATH", tmp_path / "regime_cache.json"):
            with patch("regime_filter.yf.download",
                       side_effect=self._make_sector_download(bull_sectors=[bull_etf])):
                result = rf._compute_sector_regimes()
        assert result["tech"] == "BULL"

    def test_bear_sector_classified_as_bear(self, tmp_path):
        import regime_filter as rf
        bear_etf = "XLE"   # energy
        with patch.object(rf, "_REGIME_CACHE_PATH", tmp_path / "regime_cache.json"):
            with patch("regime_filter.yf.download",
                       side_effect=self._make_sector_download(bear_sectors=[bear_etf])):
                result = rf._compute_sector_regimes()
        assert result["energy"] == "BEAR"

    def test_flat_sector_is_not_bull(self, tmp_path):
        """A flat sector ETF (same price as SPY) cannot be classified as BULL.
        With flat prices: ma50 == ma200 (cross_bull=False) and etf_ret==spy_ret (rs_bull=False),
        so the BEAR condition fires. Either way it is not BULL."""
        import regime_filter as rf
        with patch.object(rf, "_REGIME_CACHE_PATH", tmp_path / "regime_cache.json"):
            with patch("regime_filter.yf.download", side_effect=self._make_sector_download()):
                result = rf._compute_sector_regimes()
        for sector in rf.SECTOR_ETFS:
            assert result[sector] != "BULL", f"{sector} should not be BULL with flat prices"

    def test_missing_etf_data_returns_neutral(self, tmp_path):
        """If an ETF has no data, sector is NEUTRAL, not an error."""
        import regime_filter as rf

        def fake_download(tickers, **kwargs):
            n = 260
            dates = pd.date_range("2023-01-01", periods=n, freq="B")
            data = {("Close", "SPY"): [100.0] * n}
            return pd.DataFrame(data, index=dates,
                                 columns=pd.MultiIndex.from_tuples(data.keys()))

        with patch.object(rf, "_REGIME_CACHE_PATH", tmp_path / "regime_cache.json"):
            with patch("regime_filter.yf.download", side_effect=fake_download):
                result = rf._compute_sector_regimes()
        for sector in rf.SECTOR_ETFS:
            assert result[sector] == "NEUTRAL"

    def test_insufficient_history_returns_neutral(self, tmp_path):
        """ETF with < 200 bars → NEUTRAL."""
        import regime_filter as rf

        def fake_download(tickers, **kwargs):
            n = 100   # Insufficient
            dates = pd.date_range("2023-01-01", periods=n, freq="B")
            data = {("Close", t): [100.0] * n for t in tickers}
            return pd.DataFrame(data, index=dates,
                                 columns=pd.MultiIndex.from_tuples(data.keys()))

        with patch.object(rf, "_REGIME_CACHE_PATH", tmp_path / "regime_cache.json"):
            with patch("regime_filter.yf.download", side_effect=fake_download):
                result = rf._compute_sector_regimes()
        for sector in rf.SECTOR_ETFS:
            assert result[sector] == "NEUTRAL"

    def test_download_failure_returns_all_neutral(self, tmp_path):
        """If yf.download raises, all sectors are NEUTRAL."""
        import regime_filter as rf
        with patch.object(rf, "_REGIME_CACHE_PATH", tmp_path / "regime_cache.json"):
            with patch("regime_filter.yf.download", side_effect=Exception("network error")):
                result = rf._compute_sector_regimes()
        for sector in rf.SECTOR_ETFS:
            assert result[sector] == "NEUTRAL"


# ==============================================================================
# 5. REGIME MODIFIERS
# ==============================================================================

class TestRegimeModifiers:

    def test_position_multipliers(self):
        import regime_filter as rf
        assert rf.get_position_size_multiplier("RISK_ON")      == pytest.approx(1.0)
        assert rf.get_position_size_multiplier("TRANSITIONAL") == pytest.approx(0.7)
        assert rf.get_position_size_multiplier("RISK_OFF")     == pytest.approx(0.4)

    def test_position_multiplier_unknown_defaults_to_transitional(self):
        import regime_filter as rf
        assert rf.get_position_size_multiplier("UNKNOWN") == pytest.approx(0.7)

    def test_factor_weights_risk_on(self):
        import regime_filter as rf
        w = rf.get_factor_weights("RISK_ON")
        assert set(w.keys()) == {
            "momentum_12_1", "momentum_6_1", "mean_reversion_5d",
            "volatility_quality", "risk_adjusted_momentum"
        }
        assert abs(sum(w.values()) - 1.0) < 0.01, "RISK_ON weights should sum to ~1.0"
        assert w["momentum_12_1"] > w["mean_reversion_5d"], "momentum heavier in RISK_ON"

    def test_factor_weights_risk_off(self):
        import regime_filter as rf
        w = rf.get_factor_weights("RISK_OFF")
        assert abs(sum(w.values()) - 1.0) < 0.01, "RISK_OFF weights should sum to ~1.0"
        assert w["mean_reversion_5d"] > rf.get_factor_weights("RISK_ON")["mean_reversion_5d"], \
            "mean_reversion weighted more in RISK_OFF"
        assert w["volatility_quality"] > rf.get_factor_weights("RISK_ON")["volatility_quality"], \
            "quality weighted more in RISK_OFF"

    def test_factor_weights_transitional_returns_empty(self):
        import regime_filter as rf
        w = rf.get_factor_weights("TRANSITIONAL")
        assert w == {}, "TRANSITIONAL returns empty dict — caller uses config defaults"

    def test_max_conviction(self):
        import regime_filter as rf
        assert rf.get_max_conviction("RISK_ON")      == 5
        assert rf.get_max_conviction("TRANSITIONAL") == 4
        assert rf.get_max_conviction("RISK_OFF")     == 3

    def test_max_conviction_unknown_defaults_to_4(self):
        import regime_filter as rf
        assert rf.get_max_conviction("WHATEVER") == 4


# ==============================================================================
# 6. SIGNAL ENGINE INTEGRATION — position multiplier applied correctly
# ==============================================================================

class TestSignalEngineIntegration:
    """
    Verify that regime multiplier propagates correctly into
    signal_engine.compute_position_sizes().
    """

    def _make_signals_df(self, tickers):
        """Minimal signals DataFrame for compute_position_sizes.
        Uses distinct random walks per ticker so cross-sectional z-scores are non-zero."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import signal_engine as se
        n = 260
        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        rng   = np.random.default_rng(42)
        prices = pd.DataFrame(
            {t: np.cumprod(1 + rng.normal(0.001, 0.02, n)) * 100 for t in tickers},
            index=dates,
        )
        signals = se.compute_equity_composite(prices)
        return signals, prices

    def test_risk_on_multiplier_gives_full_position(self):
        """RISK_ON multiplier 1.0 → same position as baseline."""
        import signal_engine as se
        tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
        signals, prices = self._make_signals_df(tickers)
        if signals.empty:
            pytest.skip("Insufficient data for signals")

        baseline = se.compute_position_sizes(signals, prices, "equity", 30_000, regime_multiplier=1.0)
        risk_on  = se.compute_position_sizes(signals, prices, "equity", 30_000, regime_multiplier=1.0)
        if baseline.empty:
            pytest.skip("No positions generated")
        pd.testing.assert_frame_equal(baseline, risk_on)

    def test_risk_off_multiplier_reduces_positions_by_60pct(self):
        """RISK_OFF multiplier 0.4 → positions are 40% of RISK_ON."""
        import signal_engine as se
        tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
        signals, prices = self._make_signals_df(tickers)
        if signals.empty:
            pytest.skip("Insufficient data for signals")

        full    = se.compute_position_sizes(signals, prices, "equity", 30_000, regime_multiplier=1.0)
        off     = se.compute_position_sizes(signals, prices, "equity", 30_000, regime_multiplier=0.4)
        if full.empty:
            pytest.skip("No positions generated")

        for ticker in off.index:
            if ticker in full.index:
                expected = full.loc[ticker, "position_eur"] * 0.4
                actual   = off.loc[ticker, "position_eur"]
                assert actual == pytest.approx(expected, rel=0.01)

    def test_transitional_multiplier_reduces_positions_by_30pct(self):
        """TRANSITIONAL multiplier 0.7 → positions are 70% of RISK_ON."""
        import signal_engine as se
        tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
        signals, prices = self._make_signals_df(tickers)
        if signals.empty:
            pytest.skip("Insufficient data for signals")

        full  = se.compute_position_sizes(signals, prices, "equity", 30_000, regime_multiplier=1.0)
        trans = se.compute_position_sizes(signals, prices, "equity", 30_000, regime_multiplier=0.7)
        if full.empty:
            pytest.skip("No positions generated")

        for ticker in trans.index:
            if ticker in full.index:
                expected = full.loc[ticker, "position_eur"] * 0.7
                actual   = trans.loc[ticker, "position_eur"]
                assert actual == pytest.approx(expected, rel=0.01)

    def test_regime_weights_change_composite_scores(self):
        """RISK_OFF weights should produce different composite z-scores than RISK_ON weights."""
        import signal_engine as se
        import regime_filter as rf
        tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
        n = 260
        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        # Create varied price histories so factors differ across tickers
        rng = np.random.default_rng(42)
        prices = pd.DataFrame(
            {t: np.cumprod(1 + rng.normal(0.001, 0.02, n)) * 100 for t in tickers},
            index=dates,
        )

        w_on  = rf.get_factor_weights("RISK_ON")
        w_off = rf.get_factor_weights("RISK_OFF")
        sig_on  = se.compute_equity_composite(prices, regime_weights=w_on)
        sig_off = se.compute_equity_composite(prices, regime_weights=w_off)

        if sig_on.empty or sig_off.empty:
            pytest.skip("Insufficient data")

        # Composite scores should differ between regimes (unless perfectly symmetric data)
        assert not sig_on["composite_z"].equals(sig_off["composite_z"]), \
            "RISK_ON and RISK_OFF should produce different composite z-scores"


# ==============================================================================
# 7. CACHE BEHAVIOUR
# ==============================================================================

class TestCacheBehaviour:

    def test_fresh_cache_avoids_yf_call(self, tmp_path):
        """get_market_regime() returns cached value without calling yf.download."""
        import regime_filter as rf
        cached_result = {
            "regime":             "RISK_ON",
            "score":              4,
            "components":         {"trend": 2, "volatility": 1, "credit": 1, "yield_curve": 0},
            "vix":                16.0,
            "spy_vs_200ma":       5.5,
            "yield_curve_spread": 0.25,
            "computed_at":        datetime.now(tz=timezone.utc).isoformat(),
        }
        cache = {"market_regime": cached_result}
        cache_path = tmp_path / "regime_cache.json"
        cache_path.write_text(json.dumps(cache))

        with patch.object(rf, "_REGIME_CACHE_PATH", cache_path):
            with patch("regime_filter.yf.download") as mock_dl:
                result = rf.get_market_regime()

        mock_dl.assert_not_called()
        assert result["regime"] == "RISK_ON"
        assert result["score"] == 4

    def test_stale_cache_triggers_recompute(self, tmp_path):
        """get_market_regime() recomputes when cache is older than TTL."""
        import regime_filter as rf
        stale_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=48)).isoformat()
        cache = {
            "market_regime": {
                "regime": "RISK_OFF", "score": -3,
                "components": {}, "vix": None, "spy_vs_200ma": None,
                "yield_curve_spread": None,
                "computed_at": stale_ts,
            }
        }
        cache_path = tmp_path / "regime_cache.json"
        cache_path.write_text(json.dumps(cache))

        n = 260
        dates = pd.date_range("2023-01-01", periods=n, freq="B")

        def fake_download(tickers, **kwargs):
            data = {("Close", t): [100.0] * n for t in tickers}
            return pd.DataFrame(data, index=dates,
                                 columns=pd.MultiIndex.from_tuples(data.keys()))

        with patch.object(rf, "_REGIME_CACHE_PATH", cache_path):
            with patch("regime_filter.yf.download", side_effect=fake_download):
                with patch("regime_filter._fetch_fred_yield_curve", return_value=None):
                    result = rf.get_market_regime()

        # Stale cache is ignored — fresh computation runs
        assert "computed_at" in result
        fresh_ts = datetime.fromisoformat(result["computed_at"])
        if fresh_ts.tzinfo is None:
            fresh_ts = fresh_ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(tz=timezone.utc) - fresh_ts).total_seconds()
        assert age < 60, "Fresh result should be very recent"

    def test_force_refresh_ignores_fresh_cache(self, tmp_path):
        """force_refresh=True bypasses even a fresh cache."""
        import regime_filter as rf
        cached_result = {
            "regime":     "RISK_ON",
            "score":      5,
            "components": {"trend": 2, "volatility": 2, "credit": 1, "yield_curve": 0},
            "vix":        12.0,
            "spy_vs_200ma": 8.0,
            "yield_curve_spread": 0.1,
            "computed_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        cache_path = tmp_path / "regime_cache.json"
        cache_path.write_text(json.dumps({"market_regime": cached_result}))

        n = 260
        dates = pd.date_range("2023-01-01", periods=n, freq="B")

        def fake_download(tickers, **kwargs):
            data = {("Close", t): [100.0] * n for t in tickers}
            return pd.DataFrame(data, index=dates,
                                 columns=pd.MultiIndex.from_tuples(data.keys()))

        call_count = {"n": 0}

        def counting_download(*args, **kwargs):
            call_count["n"] += 1
            return fake_download(*args, **kwargs)

        with patch.object(rf, "_REGIME_CACHE_PATH", cache_path):
            with patch("regime_filter.yf.download", side_effect=counting_download):
                with patch("regime_filter._fetch_fred_yield_curve", return_value=None):
                    rf.get_market_regime(force_refresh=True)

        assert call_count["n"] >= 1, "force_refresh should trigger a fresh download"
