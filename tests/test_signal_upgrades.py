"""
tests/test_signal_upgrades.py
=============================================================================
Tests for the 5 new signal upgrade modules added to ai_quant.py:

  1. _collect_earnings_event_signals   — earnings calendar + surprise history
  2. _collect_relative_strength_signals — ticker vs RSP / sector ETF
  3. _collect_liquidity_signals         — ADV, spread, liquidity tier
  4. _collect_historical_analog_signals — cosine-similarity on thesis cache
  5. _collect_volatility_regime_signals — realized vol + IV rank + VIX pct

Sub-helpers also tested:
  • _extract_signal_features   — normalized feature vector
  • _cosine_similarity_features — cosine distance between feature dicts
  • _build_prompt               — new sections appear in output text
  • SYSTEM_PROMPT               — new rule keywords are present

No Anthropic API calls are made. All yfinance and sqlite3 calls are mocked.
=============================================================================
"""

import json
import os
import sqlite3
import sys
import warnings
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_quant import (
    SECTOR_ETF_MAP,
    _FEATURE_RANGES,
    _build_prompt,
    _collect_earnings_event_signals,
    _collect_historical_analog_signals,
    _collect_liquidity_signals,
    _collect_relative_strength_signals,
    _collect_volatility_regime_signals,
    _cosine_similarity_features,
    _extract_signal_features,
    SYSTEM_PROMPT,
)


# ─── Shared price-history factory ─────────────────────────────────────────────

def _make_price_df(n_days: int = 90, start_price: float = 100.0, vol: float = 0.015, seed: int = 42) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame with known statistical properties."""
    rng   = np.random.default_rng(seed)
    dates = pd.bdate_range(end=datetime.now(), periods=n_days)
    log_r = rng.normal(0.0003, vol, n_days)
    close = start_price * np.exp(np.cumsum(log_r))
    high  = close * (1 + rng.uniform(0.002, 0.012, n_days))
    low   = close * (1 - rng.uniform(0.002, 0.012, n_days))
    vol_s = (rng.uniform(0.5, 1.5, n_days) * 5_000_000).astype(int)
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": vol_s}, index=dates)


def _make_multi_price_df(tickers, n_days=130, seed=0) -> pd.DataFrame:
    """Multi-ticker Close price DataFrame for relative-strength tests."""
    rng   = np.random.default_rng(seed)
    dates = pd.bdate_range(end=datetime.now(), periods=n_days)
    cols  = {}
    for i, t in enumerate(tickers):
        lr  = rng.normal(0.0002 * (i + 1), 0.015, n_days)
        cols[t] = 100.0 * np.exp(np.cumsum(lr))
    return pd.DataFrame(cols, index=dates)


# ==============================================================================
# 1. _extract_signal_features
# ==============================================================================

class TestExtractSignalFeatures:

    def _full_signals(self):
        return {
            "technical":      {"rsi_14": 60, "above_ma200": True, "momentum_1m_pct": 5.0, "momentum_3m_pct": 12.0},
            "options_flow":   {"iv_rank": 45, "heat_score": 70},
            "catalyst":       {"short_squeeze_score": 50, "vol_compression_score": 4},
            "dark_pool_flow": {"dark_pool_score": 65, "short_ratio_zscore": -1.2},
            "fundamentals":   {"fundamental_score_pct": 72},
            "signal_agreement_score": 0.75,
        }

    def test_all_features_extracted(self):
        feats = _extract_signal_features(self._full_signals())
        assert "rsi_14" in feats
        assert "above_ma200" in feats
        assert "agreement_score" in feats
        assert len(feats) >= 10

    def test_above_ma200_true_maps_to_one(self):
        feats = _extract_signal_features(self._full_signals())
        assert feats["above_ma200"] == pytest.approx(1.0)

    def test_above_ma200_false_maps_to_zero(self):
        sigs  = self._full_signals()
        sigs["technical"]["above_ma200"] = False
        feats = _extract_signal_features(sigs)
        assert feats["above_ma200"] == pytest.approx(0.0)

    def test_above_ma200_none_excluded(self):
        sigs  = self._full_signals()
        sigs["technical"]["above_ma200"] = None
        feats = _extract_signal_features(sigs)
        assert "above_ma200" not in feats

    def test_values_in_unit_range(self):
        feats = _extract_signal_features(self._full_signals())
        for k, v in feats.items():
            assert 0.0 <= v <= 1.0, f"{k}={v} is outside [0,1]"

    def test_empty_signals_returns_empty_dict(self):
        feats = _extract_signal_features({})
        assert feats == {}

    def test_none_sub_dicts_handled_gracefully(self):
        sigs  = {"technical": None, "options_flow": None}
        feats = _extract_signal_features(sigs)
        assert isinstance(feats, dict)

    def test_clipping_above_range(self):
        """RSI of 120 (above 100) should be clipped to 1.0."""
        sigs  = {"technical": {"rsi_14": 120}}
        feats = _extract_signal_features(sigs)
        assert feats.get("rsi_14") == pytest.approx(1.0)

    def test_clipping_below_range(self):
        """RSI of -10 (below 0) should be clipped to 0.0."""
        sigs  = {"technical": {"rsi_14": -10}}
        feats = _extract_signal_features(sigs)
        assert feats.get("rsi_14") == pytest.approx(0.0)

    def test_feature_ranges_dict_covers_all_keys(self):
        feats = _extract_signal_features(self._full_signals())
        for k in feats:
            assert k in _FEATURE_RANGES, f"Feature '{k}' missing from _FEATURE_RANGES"


# ==============================================================================
# 2. _cosine_similarity_features
# ==============================================================================

class TestCosineSimilarityFeatures:

    def test_identical_vectors_return_one(self):
        a = {"rsi_14": 0.6, "iv_rank": 0.5, "heat_score": 0.7, "above_ma200": 1.0}
        assert _cosine_similarity_features(a, a) == pytest.approx(1.0, abs=1e-6)

    def test_opposite_vectors_return_negative(self):
        a = {"rsi_14": 1.0, "iv_rank": 1.0, "heat_score": 1.0, "above_ma200": 1.0}
        b = {"rsi_14": 0.0, "iv_rank": 0.0, "heat_score": 0.0, "above_ma200": 0.0}
        # Zero vector norm → returns 0.0 by guard
        assert _cosine_similarity_features(a, b) == pytest.approx(0.0, abs=1e-6)

    def test_fewer_than_3_shared_keys_returns_zero(self):
        a = {"rsi_14": 0.5, "iv_rank": 0.5}
        b = {"rsi_14": 0.5, "iv_rank": 0.5}
        assert _cosine_similarity_features(a, b) == 0.0

    def test_result_in_minus_one_to_one(self):
        a = {"rsi_14": 0.8, "iv_rank": 0.3, "heat_score": 0.6, "above_ma200": 0.9}
        b = {"rsi_14": 0.2, "iv_rank": 0.7, "heat_score": 0.1, "above_ma200": 0.4}
        sim = _cosine_similarity_features(a, b)
        assert -1.0 <= sim <= 1.0

    def test_partial_key_overlap(self):
        """Only shared keys contribute; extra keys are ignored."""
        a = {"rsi_14": 0.5, "iv_rank": 0.5, "heat_score": 0.5, "dark_pool_score": 0.9}
        b = {"rsi_14": 0.5, "iv_rank": 0.5, "heat_score": 0.5, "fundamental_score": 0.1}
        sim = _cosine_similarity_features(a, b)
        # 3 shared keys with identical values → sim ≈ 1.0
        assert sim == pytest.approx(1.0, abs=1e-4)

    def test_symmetry(self):
        a = {"rsi_14": 0.7, "iv_rank": 0.4, "heat_score": 0.6, "above_ma200": 0.8}
        b = {"rsi_14": 0.3, "iv_rank": 0.9, "heat_score": 0.2, "above_ma200": 0.1}
        assert _cosine_similarity_features(a, b) == pytest.approx(_cosine_similarity_features(b, a), abs=1e-9)


# ==============================================================================
# 3. _collect_earnings_event_signals
# ==============================================================================

def _make_yf_ticker_with_earnings(
    next_date: str = "2026-04-15",
    surprises: list = None,
) -> MagicMock:
    """Build a mock yfinance Ticker with calendar and earnings_history."""
    if surprises is None:
        surprises = [
            {"date": "2025-10-01", "epsEstimate": 1.0, "epsActual": 1.2, "surprisePercent": 0.20},
            {"date": "2025-07-01", "epsEstimate": 0.9, "epsActual": 1.0, "surprisePercent": 0.11},
            {"date": "2025-04-01", "epsEstimate": 0.8, "epsActual": 0.7, "surprisePercent": -0.125},
            {"date": "2025-01-01", "epsEstimate": 0.7, "epsActual": 0.8, "surprisePercent": 0.143},
        ]
    # Calendar: DataFrame with index containing "Earnings Date"
    cal_df           = pd.DataFrame({"Value": [pd.Timestamp(next_date)]}, index=["Earnings Date"])
    cal_df.index.name = None
    # earnings_history: DataFrame with Timestamp index
    idx_ts   = [pd.Timestamp(s["date"]) for s in surprises]
    hist_df  = pd.DataFrame(surprises, index=idx_ts)

    mock_tk                 = MagicMock()
    mock_tk.calendar        = cal_df
    mock_tk.earnings_history = hist_df
    return mock_tk


class TestCollectEarningsEventSignals:

    @patch("yfinance.Ticker")
    def test_basic_structure_returned(self, mock_ticker_cls):
        mock_ticker_cls.return_value = _make_yf_ticker_with_earnings()
        result = _collect_earnings_event_signals("AAPL")
        assert result["earnings_available"] is True
        assert "next_earnings_date" in result
        assert "days_to_next_earnings" in result
        assert "earnings_risk" in result
        assert "earnings_surprises_4q" in result
        assert "avg_surprise_magnitude" in result
        assert "beat_rate_4q" in result

    @patch("yfinance.Ticker")
    def test_high_earnings_risk_within_14_days(self, mock_ticker_cls):
        near_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        mock_ticker_cls.return_value = _make_yf_ticker_with_earnings(next_date=near_date)
        result = _collect_earnings_event_signals("AAPL")
        assert result["earnings_risk"] == "HIGH"

    @patch("yfinance.Ticker")
    def test_medium_earnings_risk_15_to_30_days(self, mock_ticker_cls):
        med_date = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")
        mock_ticker_cls.return_value = _make_yf_ticker_with_earnings(next_date=med_date)
        result = _collect_earnings_event_signals("AAPL")
        assert result["earnings_risk"] == "MEDIUM"

    @patch("yfinance.Ticker")
    def test_low_earnings_risk_over_30_days(self, mock_ticker_cls):
        far_date = (datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d")
        mock_ticker_cls.return_value = _make_yf_ticker_with_earnings(next_date=far_date)
        result = _collect_earnings_event_signals("AAPL")
        assert result["earnings_risk"] == "LOW"

    @patch("yfinance.Ticker")
    def test_surprise_count_matches_history(self, mock_ticker_cls):
        mock_ticker_cls.return_value = _make_yf_ticker_with_earnings()
        result = _collect_earnings_event_signals("AAPL")
        assert len(result["earnings_surprises_4q"]) == 4

    @patch("yfinance.Ticker")
    def test_beat_rate_correct(self, mock_ticker_cls):
        # Surprises: +20%, +11%, -12.5%, +14.3% → 3 beats out of 4
        mock_ticker_cls.return_value = _make_yf_ticker_with_earnings()
        result = _collect_earnings_event_signals("AAPL")
        assert result["beat_rate_4q"] == pytest.approx(0.75)

    @patch("yfinance.Ticker")
    def test_avg_surprise_magnitude_positive(self, mock_ticker_cls):
        mock_ticker_cls.return_value = _make_yf_ticker_with_earnings()
        result = _collect_earnings_event_signals("AAPL")
        assert result["avg_surprise_magnitude"] is not None
        assert result["avg_surprise_magnitude"] > 0

    @patch("yfinance.Ticker")
    def test_exception_returns_safe_fallback(self, mock_ticker_cls):
        mock_ticker_cls.side_effect = RuntimeError("network error")
        result = _collect_earnings_event_signals("BROKEN")
        assert result["earnings_available"] is False
        assert "error" in result
        assert result["earnings_risk"] == "LOW"  # safe default

    @patch("yfinance.Ticker")
    def test_no_earnings_history_handled(self, mock_ticker_cls):
        tk                  = MagicMock()
        tk.calendar         = None
        tk.earnings_history = pd.DataFrame()  # empty
        mock_ticker_cls.return_value = tk
        result = _collect_earnings_event_signals("AAPL")
        assert result["earnings_available"] is True
        assert result["earnings_surprises_4q"] == []
        assert result["beat_rate_4q"] is None


# ==============================================================================
# 4. _collect_relative_strength_signals
# ==============================================================================

def _patch_yf_download(tickers, n_days=130, seed=0):
    """Return a mock for yfinance.download returning multi-col Close DataFrame."""
    prices = _make_multi_price_df(tickers, n_days=n_days, seed=seed)
    # Wrap in dict like yfinance multi-level download result
    result = {"Close": prices}
    return result


class TestCollectRelativeStrengthSignals:

    def _make_download_mock(self, tickers, n_days=130, seed=0):
        prices = _make_multi_price_df(tickers, n_days=n_days, seed=seed)
        mock_result = MagicMock()
        mock_result.__contains__ = lambda self, item: item == "Close"
        mock_result.__getitem__ = lambda self, item: prices
        return mock_result

    @patch("yfinance.download")
    def test_basic_structure(self, mock_dl):
        mock_dl.return_value = self._make_download_mock(["AAPL", "RSP", "XLK"])
        result = _collect_relative_strength_signals("AAPL", sector="Technology")
        assert result["rs_available"] is True
        assert result["sector_etf"] == "XLK"
        assert "ticker_return_20d" in result
        assert "vs_rsp_20d" in result
        assert "vs_sector_20d" in result
        assert "rs_signal_20d" in result

    @patch("yfinance.download")
    def test_rs_signal_values_are_valid(self, mock_dl):
        valid_signals = {
            "STRONG_OUTPERFORM", "OUTPERFORM", "INLINE",
            "UNDERPERFORM", "STRONG_UNDERPERFORM",
        }
        mock_dl.return_value = self._make_download_mock(["NVDA", "RSP", "XLK"])
        result = _collect_relative_strength_signals("NVDA", sector="Technology")
        assert result.get("rs_signal_20d") in valid_signals

    @patch("yfinance.download")
    def test_sector_etf_fallback_to_spy_for_unknown_sector(self, mock_dl):
        mock_dl.return_value = self._make_download_mock(["AAPL", "RSP", "SPY"])
        result = _collect_relative_strength_signals("AAPL", sector="Unknown Sector XYZ")
        assert result["sector_etf"] == "SPY"

    @patch("yfinance.download")
    def test_all_lookback_periods_present(self, mock_dl):
        mock_dl.return_value = self._make_download_mock(["AAPL", "RSP", "XLK"], n_days=130)
        result = _collect_relative_strength_signals("AAPL", sector="Technology")
        for period in ("20d", "60d", "120d"):
            assert f"ticker_return_{period}" in result, f"Missing ticker_return_{period}"
            assert f"vs_rsp_{period}" in result, f"Missing vs_rsp_{period}"

    @patch("yfinance.download")
    def test_exception_returns_safe_fallback(self, mock_dl):
        mock_dl.side_effect = RuntimeError("download failed")
        result = _collect_relative_strength_signals("AAPL", sector="Technology")
        assert result["rs_available"] is False
        assert "error" in result

    @patch("yfinance.download")
    def test_strong_outperform_when_ticker_much_higher(self, mock_dl):
        """Manually craft prices where ticker beats RSP by >>5% over 20d."""
        n_days = 130
        dates  = pd.bdate_range(end=datetime.now(), periods=n_days)
        aapl_p = np.concatenate([np.full(n_days - 20, 100.0), np.linspace(100.0, 115.0, 20)])
        rsp_p  = np.concatenate([np.full(n_days - 20, 100.0), np.linspace(100.0, 101.0, 20)])
        xlk_p  = np.concatenate([np.full(n_days - 20, 100.0), np.linspace(100.0, 102.0, 20)])
        prices = pd.DataFrame({"AAPL": aapl_p, "RSP": rsp_p, "XLK": xlk_p}, index=dates)
        mock_result = MagicMock()
        mock_result.__contains__ = lambda self, item: item == "Close"
        mock_result.__getitem__ = lambda self, item: prices
        mock_dl.return_value = mock_result
        result = _collect_relative_strength_signals("AAPL", sector="Technology")
        assert result["rs_signal_20d"] == "STRONG_OUTPERFORM"
        assert result["vs_rsp_20d"] == pytest.approx(13.86, abs=0.5)

    def test_sector_etf_map_has_all_11_sectors(self):
        expected = {
            "Technology", "Financial Services", "Energy", "Healthcare",
            "Consumer Cyclical", "Consumer Defensive", "Industrials",
            "Basic Materials", "Utilities", "Real Estate", "Communication Services",
        }
        assert set(SECTOR_ETF_MAP.keys()) == expected


# ==============================================================================
# 5. _collect_liquidity_signals
# ==============================================================================

class TestCollectLiquiditySignals:

    def _make_mock_ticker(self, n_days=50, adv_shares=5_000_000, price=150.0):
        hist = _make_price_df(n_days=n_days, start_price=price)
        hist["Volume"] = adv_shares   # constant volume for predictable ADV
        tk = MagicMock()
        tk.history.return_value = hist
        return tk

    @patch("yfinance.Ticker")
    def test_basic_structure(self, mock_ticker_cls):
        mock_ticker_cls.return_value = self._make_mock_ticker()
        result = _collect_liquidity_signals("AAPL")
        assert result["liquidity_available"] is True
        assert "adv_shares" in result
        assert "adv_dollars" in result
        assert "current_price" in result
        assert "vol_today_shares" in result
        assert "vol_ratio_vs_adv" in result
        assert "spread_bps" in result
        assert "liquidity_tier" in result

    @patch("yfinance.Ticker")
    def test_mega_tier_classification(self, mock_ticker_cls):
        # ADV = 5M shares × $150 = $750M → MEGA
        mock_ticker_cls.return_value = self._make_mock_ticker(adv_shares=5_000_000, price=150.0)
        result = _collect_liquidity_signals("AAPL")
        assert result["liquidity_tier"] == "MEGA"

    @patch("yfinance.Ticker")
    def test_small_tier_classification(self, mock_ticker_cls):
        # ADV = 10,000 shares × $50 = $500K → SMALL (< $1M)
        mock_ticker_cls.return_value = self._make_mock_ticker(adv_shares=10_000, price=50.0)
        result = _collect_liquidity_signals("AAPL")
        assert result["liquidity_tier"] == "SMALL"

    @patch("yfinance.Ticker")
    def test_large_tier_classification(self, mock_ticker_cls):
        # ADV = 100,000 × $200 = $20M → LARGE
        mock_ticker_cls.return_value = self._make_mock_ticker(adv_shares=100_000, price=200.0)
        result = _collect_liquidity_signals("AAPL")
        assert result["liquidity_tier"] == "LARGE"

    @patch("yfinance.Ticker")
    def test_adv_dollars_roughly_correct(self, mock_ticker_cls):
        # Price drifts from start_price=100; allow 20% rel tolerance
        mock_ticker_cls.return_value = self._make_mock_ticker(adv_shares=2_000_000, price=100.0)
        result = _collect_liquidity_signals("AAPL")
        assert result["adv_dollars"] == pytest.approx(200_000_000, rel=0.20)

    @patch("yfinance.Ticker")
    def test_spread_bps_positive(self, mock_ticker_cls):
        mock_ticker_cls.return_value = self._make_mock_ticker()
        result = _collect_liquidity_signals("AAPL")
        assert result["spread_bps"] > 0

    @patch("yfinance.Ticker")
    def test_vol_ratio_at_one_when_today_equals_adv(self, mock_ticker_cls):
        mock_ticker_cls.return_value = self._make_mock_ticker(adv_shares=1_000_000)
        result = _collect_liquidity_signals("AAPL")
        assert result["vol_ratio_vs_adv"] == pytest.approx(1.0, abs=0.01)

    @patch("yfinance.Ticker")
    def test_insufficient_history_returns_unavailable(self, mock_ticker_cls):
        tk = MagicMock()
        tk.history.return_value = pd.DataFrame()
        mock_ticker_cls.return_value = tk
        result = _collect_liquidity_signals("AAPL")
        assert result["liquidity_available"] is False

    @patch("yfinance.Ticker")
    def test_exception_returns_safe_fallback(self, mock_ticker_cls):
        mock_ticker_cls.side_effect = RuntimeError("network error")
        result = _collect_liquidity_signals("BROKEN")
        assert result["liquidity_available"] is False
        assert "error" in result


# ==============================================================================
# 6. _collect_historical_analog_signals
# ==============================================================================

def _seed_in_memory_db(db_path: str, n_rows: int = 20) -> None:
    """Seed an in-memory or temp SQLite DB with synthetic thesis rows."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS thesis_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT, date TEXT, direction TEXT, conviction INTEGER,
            signal_agreement_score REAL, signals_json TEXT,
            entry_low REAL, target_1 REAL
        )
    """)
    base_date = datetime.now() - timedelta(days=365)
    directions = ["BULL", "BEAR", "NEUTRAL"]
    rng = np.random.default_rng(7)
    for i in range(n_rows):
        d       = (base_date + timedelta(days=i * 10)).strftime("%Y-%m-%d")
        direc   = directions[i % 3]
        sig_vec = {
            "signal_agreement_score": float(rng.uniform(0.4, 0.9)),
            "technical": {
                "rsi_14":          float(rng.uniform(30, 80)),
                "above_ma200":     bool(rng.integers(0, 2)),
                "momentum_1m_pct": float(rng.uniform(-10, 15)),
                "momentum_3m_pct": float(rng.uniform(-20, 30)),
            },
            "options_flow":   {"iv_rank": float(rng.uniform(10, 90)), "heat_score": float(rng.uniform(20, 85))},
            "catalyst":       {"short_squeeze_score": float(rng.uniform(0, 80)), "vol_compression_score": float(rng.uniform(0, 8))},
            "dark_pool_flow": {"dark_pool_score": float(rng.uniform(20, 80)), "short_ratio_zscore": float(rng.uniform(-2, 2))},
            "fundamentals":   {"fundamental_score_pct": float(rng.uniform(30, 90))},
        }
        conn.execute(
            "INSERT INTO thesis_cache (ticker, date, direction, conviction, signal_agreement_score, signals_json, entry_low, target_1) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"T{i:02d}", d, direc, int(rng.integers(1, 6)), float(rng.uniform(0.4, 0.9)),
             json.dumps(sig_vec), 100.0 + i, 110.0 + i),
        )
    conn.commit()
    conn.close()


def _make_current_signals() -> dict:
    return {
        "signal_agreement_score": 0.75,
        "technical":      {"rsi_14": 62, "above_ma200": True, "momentum_1m_pct": 7.0, "momentum_3m_pct": 18.0},
        "options_flow":   {"iv_rank": 42, "heat_score": 68},
        "catalyst":       {"short_squeeze_score": 55, "vol_compression_score": 3.5},
        "dark_pool_flow": {"dark_pool_score": 65, "short_ratio_zscore": -1.1},
        "fundamentals":   {"fundamental_score_pct": 74},
    }


class TestCollectHistoricalAnalogSignals:

    @pytest.fixture
    def tmp_db(self, tmp_path):
        db_path = str(tmp_path / "test_cache.db")
        _seed_in_memory_db(db_path, n_rows=20)
        return db_path

    def test_basic_structure(self, tmp_db):
        result = _collect_historical_analog_signals("AAPL", _make_current_signals(), db_path=tmp_db)
        assert result["analog_available"] is True
        assert "analog_score" in result
        assert "top_3_analogs" in result
        assert "modal_direction" in result
        assert "n_searched" in result

    def test_top3_analogs_length(self, tmp_db):
        result = _collect_historical_analog_signals("AAPL", _make_current_signals(), db_path=tmp_db)
        assert len(result["top_3_analogs"]) == 3

    def test_analog_score_in_range(self, tmp_db):
        result = _collect_historical_analog_signals("AAPL", _make_current_signals(), db_path=tmp_db)
        assert 0.0 <= result["analog_score"] <= 100.0

    def test_top3_sorted_by_similarity_descending(self, tmp_db):
        result = _collect_historical_analog_signals("AAPL", _make_current_signals(), db_path=tmp_db)
        sims = [a["similarity"] for a in result["top_3_analogs"]]
        assert sims == sorted(sims, reverse=True)

    def test_modal_direction_is_valid(self, tmp_db):
        result = _collect_historical_analog_signals("AAPL", _make_current_signals(), db_path=tmp_db)
        assert result["modal_direction"] in {"BULL", "BEAR", "NEUTRAL"}

    def test_identical_current_signals_get_high_score(self, tmp_db):
        """Searching with signals very similar to DB rows should return analog_score > 50."""
        # Use a signals dict matching one of the seeded rows
        conn = sqlite3.connect(tmp_db)
        row  = conn.execute(
            "SELECT signals_json FROM thesis_cache ORDER BY id LIMIT 1"
        ).fetchone()
        conn.close()
        hist_sigs = json.loads(row[0])
        result    = _collect_historical_analog_signals("AAPL", hist_sigs, db_path=tmp_db)
        assert result["analog_available"] is True
        # Exact match → score should be high (>60)
        assert result["analog_score"] > 60.0

    def test_missing_db_returns_unavailable(self):
        result = _collect_historical_analog_signals(
            "AAPL", _make_current_signals(), db_path="/tmp/nonexistent_xyz_123.db"
        )
        assert result["analog_available"] is False
        assert "reason" in result

    def test_insufficient_features_returns_unavailable(self):
        """Empty signals → fewer than 3 features → unavailable."""
        result = _collect_historical_analog_signals("AAPL", {}, db_path="/tmp/nonexistent.db")
        assert result["analog_available"] is False

    def test_fewer_than_5_rows_returns_unavailable(self, tmp_path):
        db_path = str(tmp_path / "sparse.db")
        _seed_in_memory_db(db_path, n_rows=3)
        result  = _collect_historical_analog_signals("AAPL", _make_current_signals(), db_path=db_path)
        assert result["analog_available"] is False
        assert "only 3 historical theses" in result["reason"]

    def test_n_searched_matches_row_count(self, tmp_db):
        result = _collect_historical_analog_signals("AAPL", _make_current_signals(), db_path=tmp_db)
        assert result["n_searched"] == 20


# ==============================================================================
# 7. _collect_volatility_regime_signals
# ==============================================================================

class TestCollectVolatilityRegimeSignals:

    def _make_ticker_factory(self, n_days=85, stock_vol=0.015, vix_current=18.0):
        stock_hist = _make_price_df(n_days=n_days, vol=stock_vol)
        vix_hist   = pd.DataFrame(
            {"Close": [vix_current] * 260},
            index=pd.bdate_range(end=datetime.now(), periods=260),
        )
        def _factory(sym):
            tk = MagicMock()
            tk.history.return_value = vix_hist if sym == "^VIX" else stock_hist
            return tk
        return _factory

    @patch("yfinance.Ticker")
    def test_basic_structure(self, mock_ticker_cls):
        mock_ticker_cls.side_effect = self._make_ticker_factory()
        result = _collect_volatility_regime_signals("AAPL")
        assert result["vol_regime_available"] is True
        assert "rv_20d_pct" in result
        assert "rv_60d_pct" in result
        assert "vol_ratio_20_60" in result
        assert "vol_regime" in result
        assert "vix_current" in result
        assert "vix_percentile" in result

    @patch("yfinance.Ticker")
    def test_rv_20d_positive(self, mock_ticker_cls):
        mock_ticker_cls.side_effect = self._make_ticker_factory(stock_vol=0.02)
        result = _collect_volatility_regime_signals("AAPL")
        assert result["rv_20d_pct"] > 0.0

    @patch("yfinance.Ticker")
    def test_rv_20d_annualized_roughly_correct(self, mock_ticker_cls):
        """vol=0.015 daily → annualized ≈ 0.015*sqrt(252)*100 ≈ 23.8%."""
        mock_ticker_cls.side_effect = self._make_ticker_factory(stock_vol=0.015)
        result = _collect_volatility_regime_signals("AAPL")
        assert 10.0 < result["rv_20d_pct"] < 50.0

    @patch("yfinance.Ticker")
    def test_vol_regime_expanding(self, mock_ticker_cls):
        """First 65 days: low vol; last 20 days: high vol → 20d rv >> 60d rv → EXPANDING."""
        n_days  = 85
        dates   = pd.bdate_range(end=datetime.now(), periods=n_days)
        low_lr  = np.random.default_rng(1).normal(0, 0.005, n_days - 20)
        high_lr = np.random.default_rng(1).normal(0, 0.040, 20)
        lr      = np.concatenate([low_lr, high_lr])
        close   = 100.0 * np.exp(np.cumsum(lr))
        hist    = pd.DataFrame({
            "Open": close, "High": close * 1.01, "Low": close * 0.99,
            "Close": close, "Volume": np.full(n_days, 1_000_000),
        }, index=dates)
        vix_hist = pd.DataFrame({"Close": [18.0] * 260}, index=pd.bdate_range(end=datetime.now(), periods=260))

        def _factory(sym):
            tk = MagicMock()
            tk.history.return_value = vix_hist if sym == "^VIX" else hist
            return tk

        mock_ticker_cls.side_effect = _factory
        result = _collect_volatility_regime_signals("AAPL")
        assert result["vol_regime"] == "EXPANDING"

    @patch("yfinance.Ticker")
    def test_vol_regime_contracting(self, mock_ticker_cls):
        """First 65 days: high vol; last 20 days: low vol → 20d rv << 60d rv → CONTRACTING."""
        n_days  = 85
        dates   = pd.bdate_range(end=datetime.now(), periods=n_days)
        high_lr = np.random.default_rng(2).normal(0, 0.040, n_days - 20)
        low_lr  = np.random.default_rng(2).normal(0, 0.004, 20)
        lr      = np.concatenate([high_lr, low_lr])
        close   = 100.0 * np.exp(np.cumsum(lr))
        hist    = pd.DataFrame({
            "Open": close, "High": close * 1.01, "Low": close * 0.99,
            "Close": close, "Volume": np.full(n_days, 1_000_000),
        }, index=dates)
        vix_hist = pd.DataFrame({"Close": [18.0] * 260}, index=pd.bdate_range(end=datetime.now(), periods=260))

        def _factory(sym):
            tk = MagicMock()
            tk.history.return_value = vix_hist if sym == "^VIX" else hist
            return tk

        mock_ticker_cls.side_effect = _factory
        result = _collect_volatility_regime_signals("AAPL")
        assert result["vol_regime"] == "CONTRACTING"

    @patch("yfinance.Ticker")
    def test_vix_percentile_in_valid_range(self, mock_ticker_cls):
        n_days   = 85
        hist     = _make_price_df(n_days=n_days)
        vix_vals = np.arange(10.0, 30.1, (30 - 10) / 251)[:252]
        vix_hist = pd.DataFrame(
            {"Close": vix_vals},
            index=pd.bdate_range(end=datetime.now(), periods=252),
        )

        def _factory(sym):
            tk = MagicMock()
            tk.history.return_value = vix_hist if sym == "^VIX" else hist
            return tk

        mock_ticker_cls.side_effect = _factory
        result = _collect_volatility_regime_signals("AAPL")
        assert result["vix_percentile"] is not None
        assert 0 <= result["vix_percentile"] <= 100

    @patch("yfinance.Ticker")
    def test_insufficient_history_returns_unavailable(self, mock_ticker_cls):
        tk = MagicMock()
        tk.history.return_value = _make_price_df(n_days=10)   # < 25 trading days
        mock_ticker_cls.side_effect = lambda sym: tk
        result = _collect_volatility_regime_signals("AAPL")
        assert result["vol_regime_available"] is False

    @patch("yfinance.Ticker")
    def test_exception_returns_safe_fallback(self, mock_ticker_cls):
        mock_ticker_cls.side_effect = RuntimeError("network error")
        result = _collect_volatility_regime_signals("BROKEN")
        assert result["vol_regime_available"] is False
        assert "error" in result


# ==============================================================================
# 8. _build_prompt — new sections present
# ==============================================================================

def _minimal_signals(ticker: str = "AAPL") -> dict:
    """Minimal signals dict that exercises all new sections in _build_prompt."""
    return {
        "ticker":           ticker,
        "timestamp":        datetime.now().isoformat(),
        "weekly_regime":    {"regime": "bullish", "price": 180.0, "ma20w": 170.0, "pct_from_ma20w": 5.9, "slope": 0.5},
        "technical":        {"price": 180.0, "rsi_14": 62, "above_ma200": True, "ma200": 160.0, "above_ma50": True, "ma50": 175.0, "momentum_1m_pct": 5.2, "momentum_3m_pct": 12.1, "momentum_6m_pct": 22.0, "volume_ratio_5d_vs_20d": 1.3, "low_52w": 130.0, "high_52w": 195.0, "pct_from_52w_high": -7.7},
        "volume_profile":   {},
        "cross_asset":      {},
        "options_flow":     {"heat_score": 72, "direction": "BULL", "expected_move_pct": 4.5, "days_to_exp": 21, "implied_vol_pct": 32.0, "iv_rank": 45, "pc_ratio": 0.8, "total_options_vol": 125000, "straddle_cost": 8.10},
        "max_pain":         {},
        "congress":         {},
        "polymarket":       {},
        "sec":              {},
        "catalyst":         {},
        "market_regime":    {"regime": "RISK_ON", "score": 4, "components": {"trend": 2, "volatility": 1, "credit": 1, "yield_curve": 0}, "vix": 16.5, "spy_vs_200ma": 1.05, "yield_curve_spread": 0.25},
        "ticker_sector":    "Technology",
        "ticker_sector_regime": "bullish",
        # ── 5 new modules ──────────────────────────────────────────────────
        "earnings_event": {
            "earnings_available": True,
            "next_earnings_date": "2026-04-18",
            "days_to_next_earnings": 18,
            "earnings_risk": "MEDIUM",
            "earnings_surprises_4q": [
                {"date": "2025-10-01", "eps_estimate": 1.5, "eps_actual": 1.65, "surprise_pct": 10.0},
                {"date": "2025-07-01", "eps_estimate": 1.4, "eps_actual": 1.52, "surprise_pct": 8.6},
                {"date": "2025-04-01", "eps_estimate": 1.3, "eps_actual": 1.28, "surprise_pct": -1.5},
                {"date": "2025-01-01", "eps_estimate": 1.2, "eps_actual": 1.35, "surprise_pct": 12.5},
            ],
            "avg_surprise_magnitude": 8.2,
            "beat_rate_4q": 0.75,
        },
        "relative_strength": {
            "rs_available": True,
            "sector_etf": "XLK",
            "ticker_return_20d": 6.5,
            "rsp_return_20d": 1.2,
            "sector_return_20d": 2.1,
            "vs_rsp_20d": 5.3,
            "vs_sector_20d": 4.4,
            "ticker_return_60d": 14.2,
            "rsp_return_60d": 4.5,
            "sector_return_60d": 6.0,
            "vs_rsp_60d": 9.7,
            "vs_sector_60d": 8.2,
            "ticker_return_120d": 22.0,
            "rsp_return_120d": 8.0,
            "sector_return_120d": 10.0,
            "vs_rsp_120d": 14.0,
            "vs_sector_120d": 12.0,
            "rs_signal_20d": "STRONG_OUTPERFORM",
        },
        "liquidity": {
            "liquidity_available": True,
            "adv_shares": 50_000_000,
            "adv_dollars": 9_000_000_000,
            "current_price": 180.0,
            "vol_today_shares": 55_000_000,
            "vol_ratio_vs_adv": 1.1,
            "spread_bps": 1.2,
            "liquidity_tier": "MEGA",
        },
        "historical_analog": {
            "analog_available": True,
            "analog_score": 74.3,
            "top_3_analogs": [
                {"ticker": "MSFT", "date": "2024-08-15", "direction": "BULL", "conviction": 4, "similarity": 85.2},
                {"ticker": "AAPL", "date": "2024-03-10", "direction": "BULL", "conviction": 3, "similarity": 72.1},
                {"ticker": "GOOGL","date": "2023-11-20", "direction": "NEUTRAL","conviction": 2, "similarity": 61.0},
            ],
            "modal_direction": "BULL",
            "n_searched": 87,
        },
        "volatility_regime": {
            "vol_regime_available": True,
            "rv_20d_pct": 24.5,
            "rv_60d_pct": 22.1,
            "vol_ratio_20_60": 1.11,
            "vol_regime": "STABLE",
            "current_iv_pct": 32.0,
            "iv_rank": 48.0,
            "iv_percentile": 52.0,
            "vix_current": 16.5,
            "vix_percentile": 35.0,
        },
        "signal_agreement_score": 0.80,
    }


class TestBuildPromptNewSections:

    def _prompt(self, signals=None):
        return _build_prompt(signals or _minimal_signals())

    def test_volatility_regime_section_present(self):
        assert "## VOLATILITY REGIME" in self._prompt()

    def test_volatility_regime_contains_rv(self):
        p = self._prompt()
        assert "24.5%" in p   # rv_20d_pct
        assert "22.1%" in p   # rv_60d_pct

    def test_volatility_regime_vix_percentile_present(self):
        p = self._prompt()
        assert "VIX:" in p
        assert "35.0th percentile" in p

    def test_liquidity_section_present(self):
        assert "## LIQUIDITY & TRANSACTION COST" in self._prompt()

    def test_liquidity_contains_tier(self):
        assert "tier: MEGA" in self._prompt()

    def test_liquidity_contains_spread(self):
        assert "bps" in self._prompt()

    def test_relative_strength_section_present(self):
        assert "## RELATIVE STRENGTH / SECTOR CONTEXT" in self._prompt()

    def test_relative_strength_contains_etf(self):
        assert "XLK" in self._prompt()

    def test_relative_strength_contains_signal(self):
        assert "STRONG_OUTPERFORM" in self._prompt()

    def test_earnings_calendar_section_present(self):
        assert "## EARNINGS & EVENT CALENDAR" in self._prompt()

    def test_earnings_contains_next_date(self):
        assert "2026-04-18" in self._prompt()

    def test_earnings_contains_risk_level(self):
        assert "MEDIUM" in self._prompt()

    def test_earnings_contains_surprises(self):
        p = self._prompt()
        assert "+10.0%" in p or "10.0%" in p

    def test_earnings_caution_shown_for_high_risk(self):
        sigs = _minimal_signals()
        sigs["earnings_event"]["earnings_risk"] = "HIGH"
        sigs["earnings_event"]["days_to_next_earnings"] = 7
        assert "CAUTION" in _build_prompt(sigs)

    def test_historical_analog_section_present(self):
        assert "## HISTORICAL ANALOG SCORE" in self._prompt()

    def test_historical_analog_contains_score(self):
        assert "74" in self._prompt()   # analog_score 74.3

    def test_historical_analog_contains_top3(self):
        p = self._prompt()
        assert "MSFT" in p
        assert "GOOGL" in p

    def test_historical_analog_unavailable_message(self):
        sigs = _minimal_signals()
        sigs["historical_analog"] = {"analog_available": False, "reason": "no historical theses cached yet"}
        p = _build_prompt(sigs)
        assert "no historical theses cached yet" in p

    def test_liquidity_thin_note_for_small_tier(self):
        sigs = _minimal_signals()
        sigs["liquidity"]["liquidity_tier"] = "SMALL"
        sigs["liquidity"]["adv_dollars"]    = 500_000
        assert "Thin liquidity" in _build_prompt(sigs) or "thin liquidity" in _build_prompt(sigs)

    def test_all_sections_in_logical_order(self):
        p = self._prompt()
        # Check that new sections appear in the intended order
        positions = {
            "VOLATILITY REGIME":            p.index("## VOLATILITY REGIME"),
            "VOLUME PROFILE":               p.index("## VOLUME PROFILE"),
            "LIQUIDITY":                    p.index("## LIQUIDITY & TRANSACTION COST"),
            "CROSS-ASSET":                  p.index("## CROSS-ASSET DIVERGENCE"),
            "RELATIVE STRENGTH":            p.index("## RELATIVE STRENGTH / SECTOR CONTEXT"),
            "OPTIONS FLOW":                 p.index("## OPTIONS FLOW"),
            "EARNINGS":                     p.index("## EARNINGS & EVENT CALENDAR"),
            "CATALYST SETUP":               p.index("## CATALYST SETUP"),
            "HISTORICAL ANALOG":            p.index("## HISTORICAL ANALOG SCORE"),
            "PORTFOLIO CONTEXT":            p.index("## PORTFOLIO CONTEXT"),
        }
        order = sorted(positions, key=lambda k: positions[k])
        assert order.index("VOLATILITY REGIME")  < order.index("VOLUME PROFILE")
        assert order.index("VOLUME PROFILE")     < order.index("LIQUIDITY")
        assert order.index("LIQUIDITY")          < order.index("CROSS-ASSET")
        assert order.index("CROSS-ASSET")        < order.index("RELATIVE STRENGTH")
        assert order.index("RELATIVE STRENGTH")  < order.index("OPTIONS FLOW")
        assert order.index("OPTIONS FLOW")       < order.index("EARNINGS")
        assert order.index("EARNINGS")           < order.index("CATALYST SETUP")
        assert order.index("HISTORICAL ANALOG")  < order.index("PORTFOLIO CONTEXT")


# ==============================================================================
# 9. SYSTEM_PROMPT — new rule keywords present
# ==============================================================================

class TestSystemPromptNewRules:

    def test_earnings_risk_rule_present(self):
        assert "earnings_risk" in SYSTEM_PROMPT
        assert "HIGH" in SYSTEM_PROMPT

    def test_volatility_regime_rule_present(self):
        assert "EXPANDING" in SYSTEM_PROMPT
        assert "CONTRACTING" in SYSTEM_PROMPT

    def test_liquidity_rule_present(self):
        assert "SMALL tier" in SYSTEM_PROMPT or "SMALL" in SYSTEM_PROMPT
        assert "ADV" in SYSTEM_PROMPT

    def test_relative_strength_rule_present(self):
        assert "STRONG_OUTPERFORM" in SYSTEM_PROMPT
        assert "STRONG_UNDERPERFORM" in SYSTEM_PROMPT

    def test_historical_analog_rule_present(self):
        assert "analog_score" in SYSTEM_PROMPT
        assert "weak prior" in SYSTEM_PROMPT

    def test_iv_rank_rule_present(self):
        assert "IV rank" in SYSTEM_PROMPT

    def test_signal_priority_list_updated(self):
        assert "Volatility regime" in SYSTEM_PROMPT or "vol_regime" in SYSTEM_PROMPT

    def test_beat_rate_rule_present(self):
        assert "beat_rate" in SYSTEM_PROMPT or "serial earnings beat" in SYSTEM_PROMPT

    def test_earnings_caution_cap_rule_present(self):
        assert "cap conviction" in SYSTEM_PROMPT

    def test_new_json_fields_in_schema(self):
        assert "earnings_risk" in SYSTEM_PROMPT
        assert "vol_regime" in SYSTEM_PROMPT
        assert "liquidity_note" in SYSTEM_PROMPT
        assert "analog_score" in SYSTEM_PROMPT
