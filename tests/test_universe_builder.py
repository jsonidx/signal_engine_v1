"""
tests/test_universe_builder.py
=============================================================================
Tests for universe_builder.py (v2 — 7-index global universe, 5-factor prescreen,
quality gate, persistent favourites).

Test groups:
  1.  fetch_index_constituents()      — CSV parsing, caching, fallback chain
  2.  _apply_liquidity_filter()       — price, dollar-volume, history filters
  3.  fast_momentum_prescreen()       — top-N, Tier 1 preservation, quality gate
  4.  _get_tier1_watchlist()          — Tier 1 extraction from watchlist.txt
  5.  build_master_universe()         — dedup, dot-ticker exclusion, ADR injection
  6.  _apply_quality_gate()           — ATR% and beta drop logic
  7.  _get_persistent_favorites()     — top-50 streak tracking
  8.  _save_top50_history() /
      _load_top50_history()           — JSON persistence round-trip
  9.  New index names present in      — iefa / iemg / acwi in _INDEX_URLS
      _INDEX_URLS
=============================================================================
"""

import io
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from unittest.mock import MagicMock, patch, patch as mock_patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import universe_builder as ub


# ---------------------------------------------------------------------------
# Helpers — sample data factories
# ---------------------------------------------------------------------------

def _make_ishares_csv(tickers: list, sectors: dict = None, n_metadata_rows: int = 9) -> str:
    """
    Build a minimal iShares-style CSV:
      * n_metadata_rows rows of filler (skipped by skiprows=9)
      * 1 header row with Ticker, Name, Sector, Asset Class, Weight columns
      * one data row per ticker
      * one cash/placeholder row (should be filtered out)
    """
    lines = [f"MetadataRow{i}" for i in range(n_metadata_rows)]
    lines.append("Ticker,Name,Sector,Asset Class,Weight (%)")
    for t in tickers:
        sec = (sectors or {}).get(t, "Technology")
        lines.append(f"{t},{t} Inc,{sec},Equity,1.0")
    lines.append("-,CASH USD,,Cash,0.5")
    return "\n".join(lines)


def _make_price_df(tickers: list, n_bars: int = 200, price: float = 10.0,
                   volume: float = 1_000_000.0) -> pd.DataFrame:
    """
    Build a yf.download-style MultiIndex DataFrame for *tickers*.
    All prices = *price*, all volumes = *volume*.
    """
    dates = pd.date_range(start="2024-01-01", periods=n_bars, freq="B")
    metrics = ["Open", "High", "Low", "Close", "Volume"]
    arrays = {}
    for m in metrics:
        val = volume if m == "Volume" else price
        arrays[m] = pd.DataFrame(
            np.full((n_bars, len(tickers)), val, dtype=float),
            index=dates,
            columns=tickers,
        )
    return pd.concat(arrays, axis=1)


def _make_price_df_single(ticker: str, n_bars: int = 200, price: float = 10.0,
                           volume: float = 1_000_000.0) -> pd.DataFrame:
    """Build a single-ticker yf.download-style DataFrame (no MultiIndex)."""
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
        assert "NVDA" in result or "AAPL" in result

    def test_raises_on_unknown_index(self):
        """Unknown index name should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown index"):
            ub.fetch_index_constituents("fake_index_xyz")

    def test_parses_sector_map_from_csv(self):
        """Sector column in iShares CSV must be parsed into _SECTOR_MAP."""
        sectors = {"AAPL": "Technology", "XOM": "Energy"}
        csv_text = _make_ishares_csv(list(sectors.keys()), sectors=sectors)
        mock_resp = MagicMock()
        mock_resp.text = csv_text
        mock_resp.raise_for_status = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("universe_builder.requests.get", return_value=mock_resp),
                patch("universe_builder._CACHE_DIR", Path(tmpdir)),
            ):
                ub.fetch_index_constituents("sp500")

        assert ub._SECTOR_MAP.get("AAPL") == "Technology"
        assert ub._SECTOR_MAP.get("XOM") == "Energy"


# ===========================================================================
# 2. New international index names
# ===========================================================================

class TestNewIndexNames:

    def test_iefa_in_index_urls(self):
        assert "iefa" in ub._INDEX_URLS

    def test_iemg_in_index_urls(self):
        assert "iemg" in ub._INDEX_URLS

    def test_acwi_in_index_urls(self):
        assert "acwi" in ub._INDEX_URLS

    def test_all_original_indices_still_present(self):
        for idx in ("russell1000", "russell2000", "sp500", "sp400"):
            assert idx in ub._INDEX_URLS

    def test_fetch_iefa_returns_tickers(self):
        """fetch_index_constituents works for 'iefa' index."""
        csv_text = _make_ishares_csv(["ASML", "NVO"])
        mock_resp = MagicMock()
        mock_resp.text = csv_text
        mock_resp.raise_for_status = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("universe_builder.requests.get", return_value=mock_resp),
                patch("universe_builder._CACHE_DIR", Path(tmpdir)),
            ):
                result = ub.fetch_index_constituents("iefa")

        assert set(result) == {"ASML", "NVO"}

    def test_fetch_iemg_returns_tickers(self):
        csv_text = _make_ishares_csv(["TSM", "SE"])
        mock_resp = MagicMock()
        mock_resp.text = csv_text
        mock_resp.raise_for_status = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("universe_builder.requests.get", return_value=mock_resp),
                patch("universe_builder._CACHE_DIR", Path(tmpdir)),
            ):
                result = ub.fetch_index_constituents("iemg")

        assert set(result) == {"TSM", "SE"}


# ===========================================================================
# 3. _apply_liquidity_filter — price / dollar-volume / history filters
# ===========================================================================

class TestLiquidityFilter:

    def test_passes_tickers_meeting_all_thresholds(self):
        """Tickers with adequate price, volume, and history all pass."""
        tickers = ["AAPL", "MSFT"]
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
        dates = pd.date_range(start="2024-01-01", periods=200, freq="B")
        close  = pd.DataFrame({"PENNY": np.full(200, 1.0), "SOLID": np.full(200, 10.0)}, index=dates)
        volume = pd.DataFrame({"PENNY": np.full(200, 1e6), "SOLID": np.full(200, 1e6)},  index=dates)
        df = pd.concat({"Close": close, "Volume": volume, "High": close, "Low": close}, axis=1)

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
        close  = pd.DataFrame({"ILLIQ": np.full(200, 10.0), "LIQUID": np.full(200, 10.0)},  index=dates)
        volume = pd.DataFrame({"ILLIQ": np.full(200, 10_000.), "LIQUID": np.full(200, 1e6)}, index=dates)
        df = pd.concat({"Close": close, "Volume": volume, "High": close, "Low": close}, axis=1)

        with (
            patch("universe_builder.yf.download", return_value=df),
            patch("universe_builder.UNIVERSE_MIN_PRICE", 2.0),
            patch("universe_builder.UNIVERSE_MIN_DOLLAR_VOLUME", 3_000_000),
        ):
            result = ub._apply_liquidity_filter(tickers)

        assert "ILLIQ" not in result
        assert "LIQUID" in result

    def test_filters_insufficient_history(self):
        """Tickers with fewer than 20 bars must be excluded."""
        tickers = ["SHORT", "LONG"]
        n_bars = 200
        dates = pd.date_range(start="2024-01-01", periods=n_bars, freq="B")

        close  = pd.DataFrame(index=dates, columns=tickers, dtype=float)
        volume = pd.DataFrame(index=dates, columns=tickers, dtype=float)
        close["LONG"] = 10.0
        volume["LONG"] = 1_000_000.0
        # SHORT only has 15 valid bars — below the 20-bar minimum
        close.loc[close.index[-15:],   "SHORT"] = 10.0
        volume.loc[volume.index[-15:], "SHORT"] = 1_000_000.0
        df = pd.concat({"Close": close, "Volume": volume, "High": close, "Low": close}, axis=1)

        with (
            patch("universe_builder.yf.download", return_value=df),
            patch("universe_builder.UNIVERSE_MIN_PRICE", 2.0),
            patch("universe_builder.UNIVERSE_MIN_DOLLAR_VOLUME", 3_000_000),
        ):
            result = ub._apply_liquidity_filter(tickers)

        assert "SHORT" not in result
        assert "LONG" in result


# ===========================================================================
# 4. fast_momentum_prescreen — top-N, Tier 1 preservation, quality gate
# ===========================================================================

class TestFastMomentumPrescreen:
    """
    All tests mock _compute_prescreen_scores, _get_tier1_watchlist,
    _get_persistent_favorites, and _save_top50_history to keep them unit-level.
    """

    def _patches(self, scores: dict, tier1=None, persistent=None):
        return [
            patch("universe_builder._compute_prescreen_scores", return_value=scores),
            patch("universe_builder._get_tier1_watchlist",      return_value=tier1 or []),
            patch("universe_builder._get_persistent_favorites", return_value=persistent or []),
            patch("universe_builder._save_top50_history"),
        ]

    def test_returns_top_n_tickers(self):
        """Result must contain at most top_n tickers from scored universe."""
        tickers = [f"T{i:03d}" for i in range(50)]
        scores  = {t: float(i) / 50 for i, t in enumerate(tickers)}

        with (
            patch("universe_builder._compute_prescreen_scores", return_value=scores),
            patch("universe_builder._get_tier1_watchlist",      return_value=[]),
            patch("universe_builder._get_persistent_favorites", return_value=[]),
            patch("universe_builder._save_top50_history"),
        ):
            result = ub.fast_momentum_prescreen(tickers, top_n=10)

        assert len(result) <= 10

    def test_top_n_are_highest_scored(self):
        """Returned tickers should be the highest-scored ones."""
        tickers = ["LOW", "MID", "HIGH"]
        scores  = {"LOW": 0.1, "MID": 0.5, "HIGH": 0.9}

        with (
            patch("universe_builder._compute_prescreen_scores", return_value=scores),
            patch("universe_builder._get_tier1_watchlist",      return_value=[]),
            patch("universe_builder._get_persistent_favorites", return_value=[]),
            patch("universe_builder._save_top50_history"),
        ):
            result = ub.fast_momentum_prescreen(tickers, top_n=2)

        assert "HIGH" in result
        assert "MID"  in result
        assert "LOW"  not in result

    def test_tier1_tickers_always_preserved(self):
        """A Tier 1 ticker that scores outside top_n must still appear in result."""
        tickers = [f"T{i:02d}" for i in range(20)]
        scores  = {t: float(i + 1) / 20 for i, t in enumerate(tickers)}
        scores["T00"] = 0.0  # lowest score, would be cut at top_n=5

        with (
            patch("universe_builder._compute_prescreen_scores", return_value=scores),
            patch("universe_builder._get_tier1_watchlist",      return_value=["T00"]),
            patch("universe_builder._get_persistent_favorites", return_value=[]),
            patch("universe_builder._save_top50_history"),
        ):
            result = ub.fast_momentum_prescreen(tickers, top_n=5)

        assert "T00" in result

    def test_persistent_favorites_preserved(self):
        """A persistent favourite (3-day streak) must survive even if outside top_n."""
        tickers = [f"T{i:02d}" for i in range(20)]
        scores  = {t: float(i + 1) / 20 for i, t in enumerate(tickers)}
        scores["T00"] = 0.0

        with (
            patch("universe_builder._compute_prescreen_scores", return_value=scores),
            patch("universe_builder._get_tier1_watchlist",      return_value=[]),
            patch("universe_builder._get_persistent_favorites", return_value=["T00"]),
            patch("universe_builder._save_top50_history"),
        ):
            result = ub.fast_momentum_prescreen(tickers, top_n=5)

        assert "T00" in result

    def test_empty_tickers_returns_tier1_only(self):
        """With no scored tickers, only Tier 1 watchlist tickers are returned."""
        with (
            patch("universe_builder._compute_prescreen_scores", return_value={}),
            patch("universe_builder._get_tier1_watchlist",      return_value=["ANCHOR"]),
            patch("universe_builder._get_persistent_favorites", return_value=[]),
            patch("universe_builder._save_top50_history"),
        ):
            result = ub.fast_momentum_prescreen([], top_n=10)

        assert "ANCHOR" in result

    def test_quality_gate_applied_and_drops_flagged_ticker(self):
        """Tickers in _QUALITY_CACHE that fail ATR/beta thresholds are dropped."""
        tickers = ["SAFE", "VOLAT"]
        scores  = {"SAFE": 0.9, "VOLAT": 0.8}  # VOLAT scores higher but should be dropped

        quality = {"VOLAT": {"atr_pct": 9.0, "beta": 1.0}}  # ATR% > 6 → drop

        with (
            patch("universe_builder._compute_prescreen_scores", return_value=scores),
            patch("universe_builder._get_tier1_watchlist",      return_value=[]),
            patch("universe_builder._get_persistent_favorites", return_value=[]),
            patch("universe_builder._save_top50_history"),
            patch.dict("universe_builder._QUALITY_CACHE", quality, clear=True),
        ):
            result = ub.fast_momentum_prescreen(tickers, top_n=5)

        assert "SAFE"  in result
        assert "VOLAT" not in result

    def test_quality_gate_spares_protected_ticker(self):
        """A Tier 1 ticker that fails quality gate must still appear in result."""
        tickers = ["SAFE", "FAV"]
        scores  = {"SAFE": 0.9, "FAV": 0.1}

        quality = {"FAV": {"atr_pct": 10.0, "beta": 3.0}}  # would normally be dropped

        with (
            patch("universe_builder._compute_prescreen_scores", return_value=scores),
            patch("universe_builder._get_tier1_watchlist",      return_value=["FAV"]),
            patch("universe_builder._get_persistent_favorites", return_value=[]),
            patch("universe_builder._save_top50_history"),
            patch.dict("universe_builder._QUALITY_CACHE", quality, clear=True),
        ):
            result = ub.fast_momentum_prescreen(tickers, top_n=5)

        assert "FAV" in result


# ===========================================================================
# 5. _get_tier1_watchlist — watchlist.txt parsing
# ===========================================================================

class TestGetTier1Watchlist:

    def _write_watchlist(self, content: str) -> Path:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        f.write(content)
        f.close()
        return Path(f.name)

    def teardown_method(self):
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

    def test_stops_at_universe_auto_block(self):
        """Should stop at '# ── UNIVERSE (auto)' marker."""
        content = """\
# TIER 1
PINNED

# ── UNIVERSE (auto) — ...
DYNAMIC
"""
        path = self._write_watchlist(content)
        result = ub._get_tier1_watchlist(path)
        assert result == ["PINNED"]
        assert "DYNAMIC" not in result


# ===========================================================================
# 6. build_master_universe — dedup, dot-ticker exclusion, ADR injection
# ===========================================================================

class TestBuildMasterUniverse:

    def test_deduplicates_across_indices(self):
        """Tickers appearing in multiple indices should appear only once."""
        shared = ["AAPL", "MSFT"]
        idx1   = shared + ["ONLY1"]
        idx2   = shared + ["ONLY2"]

        def fake_fetch(index):
            return idx1 if index == "sp500" else idx2

        df = _make_price_df(shared + ["ONLY1", "ONLY2"], n_bars=200)

        with (
            patch("universe_builder.fetch_index_constituents", side_effect=fake_fetch),
            patch("universe_builder.yf.download", return_value=df),
            patch("universe_builder.UNIVERSE_MIN_PRICE", 0.0),
            patch("universe_builder.UNIVERSE_MIN_DOLLAR_VOLUME", 0),
            patch("universe_builder.LIQUID_ADRS", []),   # isolate dedup test
        ):
            result = ub.build_master_universe(["sp500", "nasdaq100"])

        assert result.count("AAPL") == 1
        assert result.count("MSFT") == 1

    def test_excludes_dot_tickers(self):
        """US junk dot-tickers (preferreds/units) must be excluded; international kept."""
        # XYZ.PR = US preferred (junk), 2330.TW = Taiwan exchange suffix (keep), CLEAN = plain
        tickers_mixed = ["XYZ.PR", "ABC.WS", "2330.TW", "CLEAN"]

        with (
            patch("universe_builder.fetch_index_constituents", return_value=tickers_mixed),
            patch("universe_builder._apply_liquidity_filter", side_effect=lambda t, **kw: t),
            patch("universe_builder.LIQUID_ADRS", []),
        ):
            result = ub.build_master_universe(["sp500"])

        assert "XYZ.PR"  not in result, "US preferred should be dropped"
        assert "ABC.WS"  not in result, "US warrant should be dropped"
        assert "2330.TW" in result,     "International exchange suffix should be kept"
        assert "CLEAN"   in result,     "Plain US ticker should be kept"

    def test_liquid_adrs_injected_into_universe(self):
        """LIQUID_ADRS should be included in the raw universe before filtering."""
        fake_adrs = ["TSM", "BABA"]
        df = _make_price_df(fake_adrs, n_bars=200)

        with (
            patch("universe_builder.fetch_index_constituents", return_value=[]),
            patch("universe_builder.yf.download", return_value=df),
            patch("universe_builder.UNIVERSE_MIN_PRICE", 0.0),
            patch("universe_builder.UNIVERSE_MIN_DOLLAR_VOLUME", 0),
            patch("universe_builder.LIQUID_ADRS", fake_adrs),
        ):
            result = ub.build_master_universe(["sp500"])

        assert "TSM"  in result
        assert "BABA" in result

    def test_fallback_when_all_indices_fail(self):
        """
        If every index falls back to the dynamic fallback, build_master_universe
        still returns a non-empty list.
        """
        dummy_tickers = ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA"]
        with (
            patch("universe_builder.fetch_index_constituents",
                  return_value=dummy_tickers),
            patch("universe_builder._apply_liquidity_filter",
                  side_effect=lambda t, **kw: t),
            patch("universe_builder.LIQUID_ADRS", []),
        ):
            result = ub.build_master_universe(["sp500"])

        assert len(result) > 0


# ===========================================================================
# 7. _apply_quality_gate — ATR% and beta filtering
# ===========================================================================

class TestApplyQualityGate:

    def test_drops_high_atr_ticker(self):
        """Ticker with ATR% > UNIVERSE_ATR_PCT_MAX must be dropped."""
        quality = {"VOLAT": {"atr_pct": 8.0, "beta": 1.0}}
        with (
            patch.dict("universe_builder._QUALITY_CACHE", quality, clear=True),
            patch("universe_builder.UNIVERSE_ATR_PCT_MAX", 6.0),
            patch("universe_builder.UNIVERSE_BETA_MAX", 2.0),
        ):
            dropped = ub._apply_quality_gate({"VOLAT", "STABLE"}, protected=set())

        assert "VOLAT"  in dropped
        assert "STABLE" not in dropped

    def test_drops_high_beta_ticker(self):
        """Ticker with beta > UNIVERSE_BETA_MAX must be dropped."""
        quality = {"RISKY": {"atr_pct": 2.0, "beta": 2.5}}
        with (
            patch.dict("universe_builder._QUALITY_CACHE", quality, clear=True),
            patch("universe_builder.UNIVERSE_ATR_PCT_MAX", 6.0),
            patch("universe_builder.UNIVERSE_BETA_MAX", 2.0),
        ):
            dropped = ub._apply_quality_gate({"RISKY"}, protected=set())

        assert "RISKY" in dropped

    def test_spares_protected_ticker_with_high_atr(self):
        """A protected (Tier 1 / persistent) ticker must survive even if ATR% > max."""
        quality = {"FAV": {"atr_pct": 10.0, "beta": 3.0}}
        with (
            patch.dict("universe_builder._QUALITY_CACHE", quality, clear=True),
            patch("universe_builder.UNIVERSE_ATR_PCT_MAX", 6.0),
            patch("universe_builder.UNIVERSE_BETA_MAX", 2.0),
        ):
            dropped = ub._apply_quality_gate({"FAV"}, protected={"FAV"})

        assert "FAV" not in dropped

    def test_passes_ticker_without_quality_data(self):
        """Ticker absent from _QUALITY_CACHE gets benefit of doubt (not dropped)."""
        with patch.dict("universe_builder._QUALITY_CACHE", {}, clear=True):
            dropped = ub._apply_quality_gate({"UNKNOWN"}, protected=set())

        assert "UNKNOWN" not in dropped

    def test_both_thresholds_independently(self):
        """ATR% threshold and beta threshold each independently cause drops."""
        quality = {
            "OK":        {"atr_pct": 3.0, "beta": 1.5},  # passes both
            "HIGH_ATR":  {"atr_pct": 7.0, "beta": 1.0},  # fails ATR only
            "HIGH_BETA": {"atr_pct": 2.0, "beta": 2.1},  # fails beta only
        }
        with (
            patch.dict("universe_builder._QUALITY_CACHE", quality, clear=True),
            patch("universe_builder.UNIVERSE_ATR_PCT_MAX", 6.0),
            patch("universe_builder.UNIVERSE_BETA_MAX", 2.0),
        ):
            dropped = ub._apply_quality_gate(set(quality.keys()), protected=set())

        assert "OK"        not in dropped
        assert "HIGH_ATR"  in dropped
        assert "HIGH_BETA" in dropped


# ===========================================================================
# 8. Persistent favourites — streak tracking
# ===========================================================================

class TestPersistentFavorites:

    def test_streak_tickers_returned(self):
        """Tickers in top-50 for 3+ consecutive days must be returned."""
        history = [
            {"date": "2026-03-26", "tickers": ["STREAK", "AAPL", "X1"]},
            {"date": "2026-03-25", "tickers": ["STREAK", "AAPL", "X2"]},
            {"date": "2026-03-24", "tickers": ["STREAK", "X3",   "X4"]},
        ]
        with patch("universe_builder._load_top50_history", return_value=history):
            result = ub._get_persistent_favorites()

        assert "STREAK" in result

    def test_non_consecutive_ticker_not_returned(self):
        """
        Ticker in all 3 consecutive days → returned.
        Ticker missing from at least one day → NOT returned.
        """
        history = [
            {"date": "2026-03-26", "tickers": ["STREAK", "AAPL"]},
            {"date": "2026-03-25", "tickers": ["STREAK", "X2"]},
            {"date": "2026-03-24", "tickers": ["STREAK", "X3", "AAPL"]},
        ]
        with patch("universe_builder._load_top50_history", return_value=history):
            result = ub._get_persistent_favorites()

        # STREAK is in all 3 days → should appear
        assert "STREAK" in result
        # AAPL is in 2026-03-26 and 2026-03-24 but NOT 2026-03-25 → not in all 3
        assert "AAPL" not in result

    def test_insufficient_history_returns_empty(self):
        """Less than TOP50_STREAK_MIN days of history → return []."""
        history = [
            {"date": "2026-03-26", "tickers": ["AAPL"]},
        ]
        with patch("universe_builder._load_top50_history", return_value=history):
            result = ub._get_persistent_favorites()

        assert result == []

    def test_empty_history_returns_empty(self):
        with patch("universe_builder._load_top50_history", return_value=[]):
            result = ub._get_persistent_favorites()
        assert result == []


# ===========================================================================
# 9. _save_top50_history / _load_top50_history — JSON round-trip
# ===========================================================================

class TestTop50HistoryPersistence:

    def test_save_and_reload(self):
        """Saved history must be readable and contain today's entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "top50_history.json"
            with (
                patch("universe_builder._TOP50_HIST_PATH", path),
                patch("universe_builder.TOP50_HISTORY_DAYS", 5),
            ):
                ub._save_top50_history(["AAPL", "MSFT", "NVDA"])
                history = ub._load_top50_history()

        assert len(history) == 1
        entry = history[0]
        assert entry["date"] == date.today().isoformat()
        assert "AAPL" in entry["tickers"]
        assert "MSFT" in entry["tickers"]

    def test_prunes_to_history_days(self):
        """History must be pruned to TOP50_HISTORY_DAYS entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "top50_history.json"

            # Pre-populate with 4 old entries
            old_entries = [
                {"date": f"2026-03-{20+i:02d}", "tickers": [f"T{i}"]}
                for i in range(4)
            ]
            path.write_text(json.dumps(old_entries))

            with (
                patch("universe_builder._TOP50_HIST_PATH", path),
                patch("universe_builder.TOP50_HISTORY_DAYS", 3),
            ):
                ub._save_top50_history(["NEW"])
                history = ub._load_top50_history()

        assert len(history) <= 3

    def test_replaces_todays_entry_on_re_run(self):
        """Running twice on the same day must not create duplicate date entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "top50_history.json"
            with (
                patch("universe_builder._TOP50_HIST_PATH", path),
                patch("universe_builder.TOP50_HISTORY_DAYS", 5),
            ):
                ub._save_top50_history(["FIRST"])
                ub._save_top50_history(["SECOND"])
                history = ub._load_top50_history()

        assert len(history) == 1
        assert "SECOND" in history[0]["tickers"]
        assert "FIRST"  not in history[0]["tickers"]

    def test_load_missing_file_returns_empty(self):
        """_load_top50_history returns [] when file does not exist."""
        with patch("universe_builder._TOP50_HIST_PATH", Path("/nonexistent/top50.json")):
            result = ub._load_top50_history()
        assert result == []
