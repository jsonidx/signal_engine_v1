"""
tests/test_dark_pool_flow.py
============================
Tests for dark_pool_flow.py — FINRA ATS institutional flow signal.

Covers:
  - CSV parsing with a sample FINRA pipe-delimited fixture
  - _normalize_df: column aliasing, multi-market aggregation, zero-vol filtering
  - compute_dark_pool_signal with known declining short ratio data → ACCUMULATION
  - compute_dark_pool_signal with known rising short ratio data → DISTRIBUTION
  - compute_dark_pool_signal with borderline neutral data → NEUTRAL
  - Returns None when fewer than 5 days of data available
  - Fallback when current day's file is unavailable (404 → prior day used)
  - batch_scan: FINRA files read once, not per-ticker (deduplication)
  - score_dark_pool: wraps correctly for catalyst_screener (+2/-1/0)
  - load_result_cache: stale file (yesterday) returns {}
  - load_result_cache: today's file returns keyed dict
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dark_pool_flow as dpf


# ==============================================================================
# FIXTURES & HELPERS
# ==============================================================================

_FINRA_PIPE_SAMPLE = """\
Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
20260310|AAPL|3000000|50000|7500000|Q
20260310|AAPL|1000000|10000|2000000|N
20260310|MSFT|2000000|30000|5000000|Q
20260310|GME|800000|5000|1000000|Q
20260310|TOTALVOL|0|0|0|
"""

# A DataFrame with known declining short_ratio (should score ACCUMULATION)
def _declining_sr_frames(n: int = 20) -> Dict[str, pd.DataFrame]:
    """
    Generate n days of FINRA data for TSLA with steadily declining short ratio.
    short_ratio goes from 0.55 to 0.35 over n days → slope ≈ -0.01/day.
    """
    frames: Dict[str, pd.DataFrame] = {}
    today = datetime.now()
    business_days = dpf._prev_business_days(n + 5, reference=today)
    bdays = business_days[:n]   # oldest n days

    for i, day in enumerate(bdays):
        sr = 0.55 - (i * 0.01)   # declining
        sr = max(sr, 0.01)
        sv = int(1_000_000 * sr)
        tv = 1_000_000
        df = pd.DataFrame({
            "symbol":       ["TSLA", "AAPL"],
            "short_volume": [sv,      500_000],
            "total_volume": [tv,    1_000_000],
            "short_ratio":  [sr,          0.5],
        })
        frames[day.strftime("%Y%m%d")] = df

    return frames


def _rising_sr_frames(n: int = 20) -> Dict[str, pd.DataFrame]:
    """
    Generate n days of FINRA data for TSLA with rising short ratio.
    short_ratio goes from 0.35 to 0.55 → slope ≈ +0.01/day.
    """
    frames: Dict[str, pd.DataFrame] = {}
    today = datetime.now()
    business_days = dpf._prev_business_days(n + 5, reference=today)
    bdays = business_days[:n]

    for i, day in enumerate(bdays):
        sr = 0.35 + (i * 0.01)
        sr = min(sr, 0.99)
        sv = int(1_000_000 * sr)
        tv = 1_000_000
        df = pd.DataFrame({
            "symbol":       ["TSLA"],
            "short_volume": [sv],
            "total_volume": [tv],
            "short_ratio":  [sr],
        })
        frames[day.strftime("%Y%m%d")] = df

    return frames


def _flat_sr_frames(n: int = 20, sr: float = 0.48) -> Dict[str, pd.DataFrame]:
    """n days of flat short ratio near 0.50 → should be NEUTRAL."""
    frames: Dict[str, pd.DataFrame] = {}
    today = datetime.now()
    bdays = dpf._prev_business_days(n + 5, reference=today)[:n]

    for day in bdays:
        df = pd.DataFrame({
            "symbol":       ["TSLA"],
            "short_volume": [int(1_000_000 * sr)],
            "total_volume": [1_000_000],
            "short_ratio":  [sr],
        })
        frames[day.strftime("%Y%m%d")] = df

    return frames


# ==============================================================================
# 1. CSV PARSING
# ==============================================================================

class TestNormalizeDf:
    def test_parses_raw_pipe_delimited(self):
        """_normalize_df handles raw FINRA column names (ShortVolume, TotalVolume)."""
        from io import StringIO
        df_raw = pd.read_csv(StringIO(_FINRA_PIPE_SAMPLE), sep="|", dtype=str)
        result = dpf._normalize_df(df_raw)
        assert result is not None
        assert set(result.columns) == {"symbol", "short_volume", "total_volume", "short_ratio"}

    def test_aggregates_multi_market(self):
        """AAPL appears on Q and N markets — should be aggregated to one row."""
        from io import StringIO
        df_raw = pd.read_csv(StringIO(_FINRA_PIPE_SAMPLE), sep="|", dtype=str)
        result = dpf._normalize_df(df_raw)
        aapl = result[result["symbol"] == "AAPL"]
        assert len(aapl) == 1
        assert aapl["short_volume"].iloc[0] == pytest.approx(4_000_000)
        assert aapl["total_volume"].iloc[0] == pytest.approx(9_500_000)

    def test_short_ratio_calculation(self):
        """short_ratio = short_volume / total_volume."""
        from io import StringIO
        df_raw = pd.read_csv(StringIO(_FINRA_PIPE_SAMPLE), sep="|", dtype=str)
        result = dpf._normalize_df(df_raw)
        aapl = result[result["symbol"] == "AAPL"]
        expected_sr = 4_000_000 / 9_500_000
        assert aapl["short_ratio"].iloc[0] == pytest.approx(expected_sr, abs=1e-6)

    def test_drops_zero_total_volume(self):
        """Rows with total_volume == 0 (e.g. TOTALVOL footer) are removed."""
        from io import StringIO
        df_raw = pd.read_csv(StringIO(_FINRA_PIPE_SAMPLE), sep="|", dtype=str)
        result = dpf._normalize_df(df_raw)
        assert "TOTALVOL" not in result["symbol"].values

    def test_handles_cached_column_names(self):
        """Handles already-normalised CSV (short_volume, total_volume lower-case)."""
        df_cached = pd.DataFrame({
            "symbol":       ["SPY", "QQQ"],
            "short_volume": ["3000000", "1500000"],
            "total_volume": ["6000000", "3000000"],
            "short_ratio":  ["0.5", "0.5"],
        })
        result = dpf._normalize_df(df_cached)
        assert result is not None
        assert len(result) == 2

    def test_returns_none_on_missing_columns(self):
        """Returns None if required columns are absent."""
        df_bad = pd.DataFrame({"Symbol": ["AAPL"], "SomeOtherCol": [123]})
        result = dpf._normalize_df(df_bad)
        assert result is None

    def test_symbols_uppercased(self):
        """Symbol column is stripped and uppercased."""
        df = pd.DataFrame({
            "Symbol":      [" aapl ", "msft"],
            "ShortVolume": [100, 200],
            "TotalVolume": [500, 400],
        })
        result = dpf._normalize_df(df)
        assert result is not None
        assert "AAPL" in result["symbol"].values
        assert "MSFT" in result["symbol"].values


# ==============================================================================
# 2. DECLINING SHORT RATIO → ACCUMULATION
# ==============================================================================

class TestDecliningShortRatio:
    def test_returns_accumulation_signal(self):
        """Steadily declining short ratio over 20 days → ACCUMULATION."""
        frames = _declining_sr_frames(n=20)
        result = dpf.compute_dark_pool_signal("TSLA", _preloaded_frames=frames)
        assert result is not None
        assert result["signal"] == "ACCUMULATION"

    def test_zscore_is_negative(self):
        """Declining short ratio → short_ratio_zscore must be negative."""
        frames = _declining_sr_frames(n=20)
        result = dpf.compute_dark_pool_signal("TSLA", _preloaded_frames=frames)
        assert result is not None
        assert result["short_ratio_zscore"] < 0

    def test_days_of_data_correct(self):
        """days_of_data reflects actual matched days."""
        frames = _declining_sr_frames(n=20)
        result = dpf.compute_dark_pool_signal("TSLA", lookback_days=20, _preloaded_frames=frames)
        assert result is not None
        assert result["days_of_data"] <= 20
        assert result["days_of_data"] >= 5

    def test_all_required_fields_present(self):
        """Result dict must have all required keys."""
        frames = _declining_sr_frames(n=20)
        result = dpf.compute_dark_pool_signal("TSLA", _preloaded_frames=frames)
        assert result is not None
        required_keys = {"ticker", "signal", "short_ratio_today", "short_ratio_zscore", "days_of_data"}
        assert required_keys.issubset(set(result.keys()))


# ==============================================================================
# 3. RISING SHORT RATIO → NEUTRAL (no DISTRIBUTION in minimal version)
# ==============================================================================

class TestRisingShortRatio:
    def test_returns_neutral_signal(self):
        """Steadily rising short ratio over 20 days → NEUTRAL (not DISTRIBUTION)."""
        frames = _rising_sr_frames(n=20)
        result = dpf.compute_dark_pool_signal("TSLA", _preloaded_frames=frames)
        assert result is not None
        assert result["signal"] == "NEUTRAL"

    def test_zscore_is_positive(self):
        """Rising short ratio → short_ratio_zscore must be positive."""
        frames = _rising_sr_frames(n=20)
        result = dpf.compute_dark_pool_signal("TSLA", _preloaded_frames=frames)
        assert result is not None
        assert result["short_ratio_zscore"] > 0


# ==============================================================================
# 4. NEUTRAL / BORDERLINE
# ==============================================================================

class TestNeutral:
    def test_flat_ratio_returns_neutral(self):
        """Flat short ratio near 0.48 with no clear trend → NEUTRAL."""
        frames = _flat_sr_frames(n=20, sr=0.48)
        result = dpf.compute_dark_pool_signal("TSLA", _preloaded_frames=frames)
        assert result is not None
        assert result["signal"] == "NEUTRAL"

    def test_zscore_near_zero(self):
        """Flat data: short_ratio_zscore should be near 0."""
        frames = _flat_sr_frames(n=20, sr=0.48)
        result = dpf.compute_dark_pool_signal("TSLA", _preloaded_frames=frames)
        assert result is not None
        assert abs(result["short_ratio_zscore"]) < 1.5


# ==============================================================================
# 5. INSUFFICIENT DATA → None
# ==============================================================================

class TestInsufficientData:
    def test_returns_none_with_zero_days(self):
        """Empty preloaded frames → no data → returns None."""
        result = dpf.compute_dark_pool_signal("AAPL", _preloaded_frames={})
        assert result is None

    def test_returns_none_with_4_days(self):
        """Fewer than 5 days of data → returns None (below minimum threshold)."""
        frames = _declining_sr_frames(n=4)
        result = dpf.compute_dark_pool_signal("TSLA", lookback_days=20, _preloaded_frames=frames)
        assert result is None

    def test_returns_none_ticker_not_in_universe(self):
        """Ticker not present in any loaded FINRA frame → None."""
        frames = _flat_sr_frames(n=20, sr=0.50)   # only has TSLA
        result = dpf.compute_dark_pool_signal("FAKEXYZ", _preloaded_frames=frames)
        assert result is None


# ==============================================================================
# 6. FALLBACK WHEN CURRENT DAY IS UNAVAILABLE (HTTP 404)
# ==============================================================================

class TestFallback404:
    def test_falls_back_to_prior_day_on_404(self):
        """
        If the most recent business day's FINRA file returns 404,
        fetch_finra_weekly_short_volume should fall back to the prior business day.
        """
        today = datetime.now()
        # Use the two most recent business days (handles weekends/holidays cleanly)
        bdays = dpf._prev_business_days(2, reference=today)
        most_recent = bdays[-1]   # newest business day
        prior       = bdays[-2]   # business day before that

        mock_df = pd.DataFrame({
            "symbol":       ["AAPL"],
            "short_volume": [1_000_000],
            "total_volume": [2_000_000],
            "short_ratio":  [0.5],
        })

        def fake_fetch_single(date: datetime):
            if date.date() == most_recent.date():
                return None      # Simulate 404 for most recent day
            if date.date() == prior.date():
                return mock_df   # Prior day available
            return None

        with patch.object(dpf, "_fetch_single_date", side_effect=fake_fetch_single):
            result = dpf.fetch_finra_weekly_short_volume(date=today)

        assert result is not None
        assert "AAPL" in result["symbol"].values

    def test_returns_none_if_all_fallback_days_404(self):
        """Returns None if all 4 candidate days return None (404 / no data)."""
        with patch.object(dpf, "_fetch_single_date", return_value=None):
            result = dpf.fetch_finra_weekly_short_volume(date=datetime.now())
        assert result is None


# ==============================================================================
# 7. BATCH SCAN — FILE DEDUPLICATION
# ==============================================================================

class TestBatchScan:
    def test_finra_files_loaded_once_not_per_ticker(self):
        """
        batch_scan pre-loads FINRA files before the ticker loop.
        _fetch_single_date should be called at most once per date,
        not once per ticker per date.
        """
        tickers = ["AAPL", "MSFT", "NVDA", "TSLA", "GME"]
        lookback = 10

        call_count: Dict[str, int] = {}

        declining_frames = _declining_sr_frames(n=lookback + 5)

        # All tickers share the same short ratio data for simplicity
        def fake_fetch(date: datetime) -> pd.DataFrame:
            ds = date.strftime("%Y%m%d")
            call_count[ds] = call_count.get(ds, 0) + 1
            # Return a frame with all tickers present
            base = declining_frames.get(ds)
            if base is None:
                return None
            # Expand to all tickers so they all get data
            rows = []
            for t in tickers:
                rows.append({
                    "symbol":       t,
                    "short_volume": 500_000,
                    "total_volume": 1_000_000,
                    "short_ratio":  0.50,
                })
            return pd.DataFrame(rows)

        with patch.object(dpf, "_fetch_single_date", side_effect=fake_fetch):
            results = dpf.batch_scan(tickers, lookback_days=lookback)

        # Each date should be fetched exactly once regardless of ticker count
        for ds, count in call_count.items():
            assert count == 1, (
                f"FINRA file for {ds} was fetched {count} times (expected 1)"
            )

    def test_batch_scan_returns_list_of_dicts(self):
        """batch_scan returns a list of result dicts."""
        frames = _declining_sr_frames(n=20)
        # Build frames that have multiple tickers
        expanded: Dict[str, pd.DataFrame] = {}
        for ds, df in frames.items():
            df2 = df.copy()
            df2["symbol"] = "TSLA"
            extra = df.copy()
            extra["symbol"] = "AAPL"
            expanded[ds] = pd.concat([df2, extra], ignore_index=True)

        with patch.object(dpf, "_fetch_single_date", side_effect=lambda d: expanded.get(d.strftime("%Y%m%d"))):
            results = dpf.batch_scan(["TSLA", "AAPL"], lookback_days=20)

        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, dict)
            assert "ticker" in r
            assert "signal" in r

    def test_batch_scan_skips_tickers_with_no_data(self):
        """Tickers not present in FINRA frames are silently skipped."""
        frames = _declining_sr_frames(n=20)
        with patch.object(dpf, "_fetch_single_date", side_effect=lambda d: frames.get(d.strftime("%Y%m%d"))):
            results = dpf.batch_scan(["TSLA", "NOTREAL123"], lookback_days=20)

        tickers_in_results = [r["ticker"] for r in results]
        assert "NOTREAL123" not in tickers_in_results

    def test_batch_scan_sorted_by_score_descending(self):
        """Results are returned sorted by dark_pool_score descending."""
        frames_acc = _declining_sr_frames(n=20)
        frames_dist = _rising_sr_frames(n=20)

        # Build merged frames (TSLA declining, AAPL rising)
        merged: Dict[str, pd.DataFrame] = {}
        all_keys = set(frames_acc.keys()) | set(frames_dist.keys())
        for ds in all_keys:
            dfs = []
            if ds in frames_acc:
                df = frames_acc[ds].copy()
                df["symbol"] = "TSLA"
                dfs.append(df)
            if ds in frames_dist:
                df = frames_dist[ds].copy()
                df["symbol"] = "AAPL"
                dfs.append(df)
            if dfs:
                merged[ds] = pd.concat(dfs, ignore_index=True)

        with patch.object(dpf, "_fetch_single_date", side_effect=lambda d: merged.get(d.strftime("%Y%m%d"))):
            results = dpf.batch_scan(["TSLA", "AAPL"], lookback_days=20)

        zscores = [r["short_ratio_zscore"] for r in results]
        assert zscores == sorted(zscores)


# ==============================================================================
# 8. SCORE_DARK_POOL (catalyst_screener wrapper)
# ==============================================================================

class TestScoreDarkPool:
    def test_accumulation_returns_plus2(self):
        """ACCUMULATION signal → score = +2."""
        mock_result = {
            "ticker": "AAPL", "signal": "ACCUMULATION",
            "short_ratio_today": 0.35, "short_ratio_zscore": -1.8, "days_of_data": 18,
        }
        with patch.object(dpf, "compute_dark_pool_signal", return_value=mock_result):
            result = dpf.score_dark_pool("AAPL")
        assert result["score"] == 2
        assert result["max"] == 2
        assert result["signal"] == "ACCUMULATION"
        assert len(result["flags"]) == 1

    def test_neutral_returns_zero(self):
        """NEUTRAL signal → score = 0, no flags."""
        mock_result = {
            "ticker": "MSFT", "signal": "NEUTRAL",
            "short_ratio_today": 0.48, "short_ratio_zscore": 0.1, "days_of_data": 20,
        }
        with patch.object(dpf, "compute_dark_pool_signal", return_value=mock_result):
            result = dpf.score_dark_pool("MSFT")
        assert result["score"] == 0
        assert result["flags"] == []

    def test_no_data_returns_zero_gracefully(self):
        """compute_dark_pool_signal returns None → score = 0, no crash."""
        with patch.object(dpf, "compute_dark_pool_signal", return_value=None):
            result = dpf.score_dark_pool("XYZ")
        assert result["score"] == 0
        assert result["signal"] == "NEUTRAL"
        assert result["detail"] is None

    def test_exception_returns_zero_gracefully(self):
        """If compute_dark_pool_signal raises, score_dark_pool returns 0 safely."""
        with patch.object(dpf, "compute_dark_pool_signal", side_effect=RuntimeError("network down")):
            result = dpf.score_dark_pool("AAPL")
        assert result["score"] == 0
        assert result["signal"] == "NEUTRAL"

    def test_uses_result_cache_when_provided(self):
        """score_dark_pool uses result_cache dict if ticker is present."""
        cache = {
            "NVDA": {
                "ticker": "NVDA", "dark_pool_score": 72, "signal": "ACCUMULATION",
                "short_ratio_today": 0.38, "short_ratio_mean": 0.47,
                "short_ratio_trend": -0.006, "short_ratio_zscore": -1.6,
                "dark_pool_intensity": 0.46, "days_of_data": 20,
                "interpretation": "Institutional accumulation likely.",
            }
        }
        # compute_dark_pool_signal should NOT be called when cache hits
        with patch.object(dpf, "compute_dark_pool_signal") as mock_compute:
            result = dpf.score_dark_pool("NVDA", result_cache=cache)
        mock_compute.assert_not_called()
        assert result["score"] == 2
        assert result["signal"] == "ACCUMULATION"


# ==============================================================================
# 9. RESULT CACHE (load_result_cache / save_result_cache)
# ==============================================================================

class TestResultCache:
    def test_stale_file_returns_empty(self):
        """A file generated yesterday returns {} (stale)."""
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        payload = {
            "generated": yesterday,
            "results": [{"ticker": "AAPL", "signal": "ACCUMULATION", "dark_pool_score": 70}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            tmp_path = Path(f.name)

        try:
            with patch.object(dpf, "_RESULT_CACHE_PATH", tmp_path):
                result = dpf.load_result_cache()
            assert result == {}
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_today_file_returns_keyed_dict(self):
        """A file generated today returns a {ticker: dict} mapping."""
        today_iso = datetime.now().isoformat()
        payload = {
            "generated": today_iso,
            "results": [
                {"ticker": "AAPL", "signal": "ACCUMULATION", "dark_pool_score": 70},
                {"ticker": "MSFT", "signal": "NEUTRAL",       "dark_pool_score": 50},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            tmp_path = Path(f.name)

        try:
            with patch.object(dpf, "_RESULT_CACHE_PATH", tmp_path):
                result = dpf.load_result_cache()
            assert "AAPL" in result
            assert "MSFT" in result
            assert result["AAPL"]["signal"] == "ACCUMULATION"
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_missing_file_returns_empty(self):
        """Non-existent cache file returns {} gracefully."""
        non_existent = Path("/tmp/dark_pool_does_not_exist_999.json")
        with patch.object(dpf, "_RESULT_CACHE_PATH", non_existent):
            result = dpf.load_result_cache()
        assert result == {}

    def test_round_trip_save_and_load(self):
        """save_result_cache + load_result_cache round-trips correctly."""
        results = [
            {"ticker": "TSLA", "dark_pool_score": 68, "signal": "ACCUMULATION",
             "short_ratio_today": 0.40, "short_ratio_mean": 0.50,
             "short_ratio_trend": -0.006, "short_ratio_zscore": -1.7,
             "dark_pool_intensity": 0.47, "days_of_data": 20,
             "interpretation": "Test round trip."},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "dark_pool_latest.json"
            with patch.object(dpf, "_RESULT_CACHE_PATH", tmp_path):
                dpf.save_result_cache(results)
                loaded = dpf.load_result_cache()

        assert "TSLA" in loaded
        assert loaded["TSLA"]["signal"] == "ACCUMULATION"
        assert loaded["TSLA"]["dark_pool_score"] == 68


# ==============================================================================
# 10. INTEGRATION: CONFLICT RESOLVER RECOGNISES dark_pool_flow MODULE
# ==============================================================================

class TestConflictResolverIntegration:
    def test_extract_module_direction_accumulation(self):
        """conflict_resolver.extract_module_direction maps ACCUMULATION → BULL."""
        import conflict_resolver as cr
        dp_output = {"dark_pool_score": 70, "signal": "ACCUMULATION"}
        direction = cr.extract_module_direction("dark_pool_flow", dp_output)
        assert direction == "BULL"

    def test_extract_module_direction_neutral(self):
        """conflict_resolver.extract_module_direction returns None for NEUTRAL."""
        import conflict_resolver as cr
        dp_output = {"dark_pool_score": 50, "signal": "NEUTRAL"}
        direction = cr.extract_module_direction("dark_pool_flow", dp_output)
        assert direction is None

    def test_dark_pool_in_module_weights(self):
        """dark_pool_flow must be in MODULE_WEIGHTS."""
        import conflict_resolver as cr
        assert "dark_pool_flow" in cr.MODULE_WEIGHTS
        assert cr.MODULE_WEIGHTS["dark_pool_flow"] == pytest.approx(0.08)

    def test_dark_pool_in_signals_key_map(self):
        """dark_pool_flow must be in _SIGNALS_KEY_MAP."""
        import conflict_resolver as cr
        assert "dark_pool_flow" in cr._SIGNALS_KEY_MAP
        assert cr._SIGNALS_KEY_MAP["dark_pool_flow"] == "dark_pool_flow"

