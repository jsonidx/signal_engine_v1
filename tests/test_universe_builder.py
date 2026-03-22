"""
tests/test_universe_builder.py
=============================================================================
Tests for universe_builder.py:

  1. fetch_index_constituents() — CSV parsing, caching, fallback chain
  2. _apply_liquidity_filter()  — price, dollar-volume, and history filters
  3. fast_momentum_prescreen()  — top-N selection, Tier 1 preservation
  4. _get_tier1_watchlist()     — correct Tier 1 extraction from watchlist.txt
  5. build_master_universe()    — deduplication and dot-ticker exclusion
=============================================================================
"""

import io
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import universe_builder as ub


# ---------------------------------------------------------------------------
# Helpers — sample data factories
# ---------------------------------------------------------------------------

def _make_ishares_csv(tickers: list, n_metadata_rows: int = 9) -> str:
    """
    Build a minimal iShares-style CSV:
      * n_metadata_rows rows of filler (skipped by skiprows=9)
      * 1 header row with a 'Ticker' column
      * one data row per ticker
      * one cash/placeholder row (should be filtered out)
    """
    lines = [f"MetadataRow{i}" for i in range(n_metadata_rows)]
    lines.append("Ticker,Name,Sector,Asset Class,Weight (%)")
    for t in tickers:
        lines.append(f"{t},{t} Inc,Technology,Equity,1.0")
    lines.append("-,CASH USD,,Cash,0.5")
    return "\n".join(lines)


def _make_price_df(tickers: list, n_bars: int = 200, price: float = 10.0,
                   volume: float = 1_000_000.0) -> pd.DataFrame:
    """
    Build a yf.download-style MultiIndex DataFrame for *tickers*.
    All prices = *price*, all volumes = *volume*.
    Uses a fixed start date to guarantee exactly n_bars business-day entries.
    """
    dates = pd.date_range(start="2024-01-01", periods=n_bars, freq="B")
    metrics = ["Open", "High", "Low", "Close", "Volume"]
    arrays = {}
    for m in metrics:
        val = volume if m == "Volume" else price
        arrays[m] = pd.DataFrame(
            np.full((n_bars, len(tickers)), val),
            index=dates,
            columns=tickers,
        )
    return pd.concat(arrays, axis=1)


def _make_price_df_single(ticker: str, n_bars: int = 200, price: float = 10.0,
                           volume: float = 1_000_000.0) -> pd.DataFrame:
    """
    Build a single-ticker yf.download-style DataFrame (no MultiIndex).
    Uses a fixed start date to guarantee exactly n_bars entries.
    """
    dates = pd.date_range(start="2024-01-01", periods=n_bars, freq="B")
    return pd.DataFrame(
        {
            "Open":   np.full(n_bars, price),
            "High":   np.full(n_bars, price * 1.01),
            "Low":    np.full(n_bars, price * 0.99),
            "Close":  np.full(n_bars, price),
            "Volume": np.full(n_bars, volume),
        },
        index=dates,
    )


# ===========================================================================
# 1. fetch_index_constituents — CSV parsing and caching
# ===========================================================================

class TestFetchIndexConstituents:

    def test_parses_valid_csv(self):
        """Successful HTTP fetch → correct ticker list returned and cached."""
        tickers = ["AAPL", "MSFT", "GOOGL"]
        csv_text = _make_ishares_csv(tickers)

        mock_resp = MagicMock()
        mock_resp.text = csv_text
        mock_resp.raise_for_status = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("universe_builder.requests.get", return_value=mock_resp),
                patch("universe_builder._CACHE_DIR", Path(tmpdir)),
            ):
                result = ub.fetch_index_constituents("sp500")

        assert set(result) == set(tickers)

    def test_filters_cash_and_placeholder_rows(self):
        """'-' and CASH rows must not appear in returned list."""
        csv_text = _make_ishares_csv(["AAPL", "NVDA"])

        mock_resp = MagicMock()
        mock_resp.text = csv_text
        mock_resp.raise_for_status = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("universe_builder.requests.get", return_value=mock_resp),
                patch("universe_builder._CACHE_DIR", Path(tmpdir)),
            ):
                result = ub.fetch_index_constituents("sp500")

        assert "-" not in result
        assert not any("CASH" in t.upper() for t in result)

    def test_uses_fresh_cache_without_http(self):
        """If cache is fresh (< TTL), no HTTP request should be made."""
        cached_tickers = ["CACHED1", "CACHED2"]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "sp500_constituents.json"
            cache_path.write_text(
                json.dumps({
                    "cached_at": datetime.now().isoformat(),
                    "tickers": cached_tickers,
                })
            )

            with (
                patch("universe_builder.requests.get") as mock_get,
                patch("universe_builder._CACHE_DIR", Path(tmpdir)),
                patch("universe_builder.UNIVERSE_CACHE_TTL_HOURS", 24),
            ):
                result = ub.fetch_index_constituents("sp500")
                mock_get.assert_not_called()

        assert result == cached_tickers

    def test_network_fail_uses_stale_cache(self):
        """If HTTP fails, fall back to cached version even if it's > TTL old."""
        stale_tickers = ["STALE1", "STALE2"]
        # Cache written 30 hours ago — beyond 24hr TTL but within 7-day fallback
        stale_time = (datetime.now() - timedelta(hours=30)).isoformat()

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "sp500_constituents.json"
            cache_path.write_text(
                json.dumps({"cached_at": stale_time, "tickers": stale_tickers})
            )

            with (
                patch("universe_builder.requests.get", side_effect=Exception("timeout")),
                patch("universe_builder._CACHE_DIR", Path(tmpdir)),
                patch("universe_builder.UNIVERSE_CACHE_TTL_HOURS", 24),
            ):
                result = ub.fetch_index_constituents("sp500")

        assert result == stale_tickers

    def test_no_cache_and_network_fail_returns_hardcoded(self):
        """With no cache and no network, must return the hardcoded fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("universe_builder.requests.get", side_effect=Exception("timeout")),
                patch("universe_builder._CACHE_DIR", Path(tmpdir)),
            ):
                result = ub.fetch_index_constituents("russell2000")

        assert len(result) > 0
        assert "NVDA" in result or "AAPL" in result  # hardcoded fallback contains these

    def test_raises_on_unknown_index(self):
        """Unknown index name should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown index"):
            ub.fetch_index_constituents("fake_index_xyz")


# ===========================================================================
# 2. _apply_liquidity_filter — price / dollar-volume / history filters
# ===========================================================================

class TestLiquidityFilter:

    def test_passes_tickers_meeting_all_thresholds(self):
        """Tickers with adequate price, volume, and history all pass."""
        tickers = ["AAPL", "MSFT"]
        # price=20, volume=1M → dollar vol = $20M/day >> $3M threshold
        df = _make_price_df(tickers, n_bars=200, price=20.0, volume=1_000_000)

        with (
            patch("universe_builder.yf.download", return_value=df),
            patch("universe_builder.UNIVERSE_MIN_PRICE", 2.0),
            patch("universe_builder.UNIVERSE_MIN_DOLLAR_VOLUME", 3_000_000),
        ):
            result = ub._apply_liquidity_filter(tickers)

        assert set(result) == {"AAPL", "MSFT"}

    def test_filters_low_price_tickers(self):
        """Tickers below UNIVERSE_MIN_PRICE must be excluded."""
        tickers = ["PENNY", "SOLID"]
        # PENNY: price $1 (below $2 threshold), SOLID: price $10 (passes)
        dates = pd.date_range(start="2024-01-01", periods=200, freq="B")
        close = pd.DataFrame(
            {"PENNY": np.full(200, 1.0), "SOLID": np.full(200, 10.0)},
            index=dates,
        )
        volume = pd.DataFrame(
            {"PENNY": np.full(200, 1_000_000.0), "SOLID": np.full(200, 1_000_000.0)},
            index=dates,
        )
        df = pd.concat({"Close": close, "Volume": volume}, axis=1)

        with (
            patch("universe_builder.yf.download", return_value=df),
            patch("universe_builder.UNIVERSE_MIN_PRICE", 2.0),
            patch("universe_builder.UNIVERSE_MIN_DOLLAR_VOLUME", 3_000_000),
        ):
            result = ub._apply_liquidity_filter(tickers)

        assert "PENNY" not in result
        assert "SOLID" in result

    def test_filters_low_dollar_volume_tickers(self):
        """Tickers below UNIVERSE_MIN_DOLLAR_VOLUME must be excluded."""
        tickers = ["ILLIQ", "LIQUID"]
        dates = pd.date_range(start="2024-01-01", periods=200, freq="B")
        close = pd.DataFrame(
            {"ILLIQ": np.full(200, 10.0), "LIQUID": np.full(200, 10.0)},
            index=dates,
        )
        # ILLIQ: volume 10k → dv=$100k < $3M; LIQUID: volume 1M → dv=$10M > $3M
        volume = pd.DataFrame(
            {"ILLIQ": np.full(200, 10_000.0), "LIQUID": np.full(200, 1_000_000.0)},
            index=dates,
        )
        df = pd.concat({"Close": close, "Volume": volume}, axis=1)

        with (
            patch("universe_builder.yf.download", return_value=df),
            patch("universe_builder.UNIVERSE_MIN_PRICE", 2.0),
            patch("universe_builder.UNIVERSE_MIN_DOLLAR_VOLUME", 3_000_000),
        ):
            result = ub._apply_liquidity_filter(tickers)

        assert "ILLIQ" not in result
        assert "LIQUID" in result

    def test_filters_insufficient_history(self):
        """Tickers with fewer than 126 bars must be excluded."""
        tickers = ["SHORT", "LONG"]
        n_bars = 200
        dates = pd.date_range(start="2024-01-01", periods=n_bars, freq="B")

        # Simulate multi-ticker download: SHORT has NaNs for the first 150 rows
        close = pd.DataFrame(index=dates, columns=tickers, dtype=float)
        volume = pd.DataFrame(index=dates, columns=tickers, dtype=float)
        close["LONG"] = 10.0
        volume["LONG"] = 1_000_000.0
        # SHORT only has last 50 bars (< 126 minimum)
        close.loc[close.index[-50:], "SHORT"] = 10.0
        volume.loc[volume.index[-50:], "SHORT"] = 1_000_000.0
        df = pd.concat({"Close": close, "Volume": volume}, axis=1)

        with (
            patch("universe_builder.yf.download", return_value=df),
            patch("universe_builder.UNIVERSE_MIN_PRICE", 2.0),
            patch("universe_builder.UNIVERSE_MIN_DOLLAR_VOLUME", 3_000_000),
        ):
            result = ub._apply_liquidity_filter(tickers)

        assert "SHORT" not in result
        assert "LONG" in result


# ===========================================================================
# 3. fast_momentum_prescreen — top-N and Tier 1 preservation
# ===========================================================================

class TestFastMomentumPrescreen:

    def _make_score_patch(self, tickers: list, scores: dict):
        """Returns a mock for _compute_prescreen_scores returning *scores*."""
        return patch(
            "universe_builder._compute_prescreen_scores",
            return_value={t: scores.get(t, 0.0) for t in tickers if t in scores},
        )

    def test_returns_top_n_tickers(self):
        """Result must contain at most top_n tickers from scored universe."""
        tickers = [f"T{i:03d}" for i in range(50)]
        scores = {t: float(i) / 50 for i, t in enumerate(tickers)}

        with (
            self._make_score_patch(tickers, scores),
            patch("universe_builder._get_tier1_watchlist", return_value=[]),
        ):
            result = ub.fast_momentum_prescreen(tickers, top_n=10)

        assert len(result) <= 10

    def test_top_n_are_highest_scored(self):
        """Returned tickers should be the highest-scored ones."""
        tickers = ["LOW", "MID", "HIGH"]
        scores = {"LOW": 0.1, "MID": 0.5, "HIGH": 0.9}

        with (
            self._make_score_patch(tickers, scores),
            patch("universe_builder._get_tier1_watchlist", return_value=[]),
        ):
            result = ub.fast_momentum_prescreen(tickers, top_n=2)

        assert "HIGH" in result
        assert "MID" in result
        assert "LOW" not in result

    def test_tier1_tickers_always_preserved(self):
        """A Tier 1 ticker that scores outside top_n must still appear in result."""
        tickers = [f"T{i:02d}" for i in range(20)]
        # T00 has the lowest score but is Tier 1
        scores = {t: float(i + 1) / 20 for i, t in enumerate(tickers)}
        scores["T00"] = 0.0  # would be cut at top_n=5

        with (
            self._make_score_patch(tickers, scores),
            patch("universe_builder._get_tier1_watchlist", return_value=["T00"]),
        ):
            result = ub.fast_momentum_prescreen(tickers, top_n=5)

        assert "T00" in result

    def test_empty_tickers_returns_tier1_only(self):
        """With no scored tickers, only Tier 1 watchlist tickers are returned."""
        with (
            patch("universe_builder._compute_prescreen_scores", return_value={}),
            patch("universe_builder._get_tier1_watchlist", return_value=["ANCHOR"]),
        ):
            result = ub.fast_momentum_prescreen([], top_n=10)

        assert result == ["ANCHOR"]


# ===========================================================================
# 4. _get_tier1_watchlist — watchlist.txt parsing
# ===========================================================================

class TestGetTier1Watchlist:

    def _write_watchlist(self, content: str) -> Path:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        f.write(content)
        f.close()
        return Path(f.name)

    def teardown_method(self):
        # Clean up temp files
        import glob
        for f in glob.glob("/tmp/tmp*.txt"):
            try:
                os.unlink(f)
            except OSError:
                pass

    def test_extracts_tier1_tickers(self):
        """Should return only tickers under the TIER 1 section."""
        content = """\
# TIER 1 — High conviction
ROCKET    # 25/25
MOON      # 22/25

# TIER 2 — Monitor
EARTH     # 15/25
"""
        path = self._write_watchlist(content)
        result = ub._get_tier1_watchlist(path)
        assert result == ["ROCKET", "MOON"]

    def test_ignores_comment_lines_in_tier1(self):
        """Comment lines inside TIER 1 block are skipped."""
        content = """\
# TIER 1
# This is a comment
ALPHA    # score
# Another comment
BETA
# TIER 2
GAMMA
"""
        path = self._write_watchlist(content)
        result = ub._get_tier1_watchlist(path)
        assert result == ["ALPHA", "BETA"]

    def test_stops_at_tier2(self):
        """Should not include tickers from TIER 2 or below."""
        content = """\
# TIER 1
ONLY_T1

# TIER 2
NOT_T1
"""
        path = self._write_watchlist(content)
        result = ub._get_tier1_watchlist(path)
        assert result == ["ONLY_T1"]
        assert "NOT_T1" not in result

    def test_empty_tier1_returns_empty_list(self):
        """If TIER 1 section has no tickers, return []."""
        content = """\
# TIER 1

# TIER 2
SOME_TICKER
"""
        path = self._write_watchlist(content)
        result = ub._get_tier1_watchlist(path)
        assert result == []

    def test_missing_watchlist_returns_empty_list(self):
        """Non-existent file returns [] without raising."""
        result = ub._get_tier1_watchlist(Path("/nonexistent/path/watchlist.txt"))
        assert result == []

    def test_tickers_uppercased(self):
        """Returned tickers should always be upper-case."""
        content = """\
# TIER 1
lowercase
MixedCase
"""
        path = self._write_watchlist(content)
        result = ub._get_tier1_watchlist(path)
        assert result == ["LOWERCASE", "MIXEDCASE"]


# ===========================================================================
# 5. build_master_universe — deduplication and dot-ticker exclusion
# ===========================================================================

class TestBuildMasterUniverse:

    def test_deduplicates_across_indices(self):
        """Tickers appearing in multiple indices should appear only once."""
        shared = ["AAPL", "MSFT"]
        idx1 = shared + ["ONLY1"]
        idx2 = shared + ["ONLY2"]

        def fake_fetch(index):
            return idx1 if index == "sp500" else idx2

        df = _make_price_df(shared + ["ONLY1", "ONLY2"], n_bars=200)

        with (
            patch("universe_builder.fetch_index_constituents", side_effect=fake_fetch),
            patch("universe_builder.yf.download", return_value=df),
            patch("universe_builder.UNIVERSE_MIN_PRICE", 0.0),
            patch("universe_builder.UNIVERSE_MIN_DOLLAR_VOLUME", 0),
        ):
            result = ub.build_master_universe(["sp500", "nasdaq100"])

        assert result.count("AAPL") == 1
        assert result.count("MSFT") == 1

    def test_excludes_dot_tickers(self):
        """Tickers containing '.' (ADRs / preferred) must be excluded."""
        tickers_with_dot = ["BRK.B", "CLEAN"]

        with (
            patch(
                "universe_builder.fetch_index_constituents",
                return_value=tickers_with_dot,
            ),
            patch(
                "universe_builder._apply_liquidity_filter",
                side_effect=lambda t, **kw: t,
            ),
        ):
            result = ub.build_master_universe(["sp500"])

        assert "BRK.B" not in result
        assert "CLEAN" in result

    def test_fallback_when_all_indices_fail(self):
        """
        If fetch_index_constituents falls back to hardcoded for every index,
        build_master_universe must still return a non-empty list.
        """
        with (
            patch(
                "universe_builder.fetch_index_constituents",
                return_value=list(ub._HARDCODED_FALLBACK),
            ),
            patch(
                "universe_builder._apply_liquidity_filter",
                side_effect=lambda t, **kw: t,
            ),
        ):
            result = ub.build_master_universe(["sp500"])

        assert len(result) > 0
