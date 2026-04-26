"""
Unit tests for Phase 0 + Phase 1 squeeze screener fixes:
  - Float-adjusted DTC formula (CHUNK-01)
  - 3-state detect_recent_squeeze (CHUNK-03)
  - compression_recovery_score signal (CHUNK-04)
  - vol_score excluded from composite (CHUNK-05)
  - SqueezeScore new fields

Phase 2A additions (CHUNK-02):
  - compute_si_persistence_score: neutral with no/one history, daily-duplicate
    deduplication, three-period high score, rising/falling trend detection
  - Anti-lookahead: future publication_date rows must be filtered out
  - _build_si_snapshot: field correctness and computed_dtc_30d propagation
"""

import pandas as pd
import numpy as np
import pytest
from datetime import date, timedelta

from squeeze_screener import (
    detect_recent_squeeze,
    score_mechanics,
    score_structure,
    compute_si_persistence_score,
    _build_si_snapshot,
    SqueezeScore,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_hist(prices, n=None):
    """Build a minimal OHLCV DataFrame from a price list."""
    if n:
        prices = [prices] * n if isinstance(prices, (int, float)) else prices
    dates = pd.date_range("2024-01-01", periods=len(prices), freq="B")
    return pd.DataFrame(
        {"Close": prices, "Volume": [2_000_000] * len(prices)}, index=dates
    )


def _flat_data(**overrides):
    base = {
        "ticker": "TEST",
        "price": 40.0,
        "float_shares": 60_000_000,
        "shares_outstanding": 100_000_000,
        "short_pct_float": 0.30,
        "short_ratio_dtc": 2.8,
        "volume_avg_30d": 3_000_000,
        "volume_avg_5d": 3_000_000,
        "avg_price_60d": 40.0,
        "market_cap": 2_400_000_000,
        "history": _make_hist(40.0, 60),
        "info": {},
    }
    base.update(overrides)
    return base


# ── CHUNK-03: detect_recent_squeeze 3-state ──────────────────────────────────

class TestDetectRecentSqueeze:
    def test_returns_false_for_flat_price(self):
        data = _flat_data()
        assert detect_recent_squeeze(data) == "false"

    def test_returns_completed_when_run_and_low_si(self):
        # big run from 10→70 in 30 days, SI=5% → shorts already covered
        prices = [10.0] * 30 + [10.0 + i * 2.0 for i in range(30)]
        data = _flat_data(history=_make_hist(prices), short_pct_float=0.05)
        assert detect_recent_squeeze(data) == "completed"

    def test_returns_active_when_run_and_high_si(self):
        # same big run, but SI=45% → shorts still trapped
        prices = [10.0] * 30 + [10.0 + i * 2.0 for i in range(30)]
        data = _flat_data(history=_make_hist(prices), short_pct_float=0.45)
        assert detect_recent_squeeze(data) == "active"

    def test_threshold_boundary_si_30pct(self):
        # exactly 30% SI → active (>= 0.30 gate)
        prices = [10.0] * 30 + [10.0 + i * 2.0 for i in range(30)]
        data = _flat_data(history=_make_hist(prices), short_pct_float=0.30)
        assert detect_recent_squeeze(data) == "active"

    def test_insufficient_history_returns_false(self):
        data = _flat_data(history=_make_hist(40.0, 10))
        assert detect_recent_squeeze(data) == "false"


# ── CHUNK-01: float-adjusted DTC formula ─────────────────────────────────────

class TestScoreMechanicsDTC:
    """
    CAR scenario: float=60M, SI=30%, avg_vol_30d=3M
      self-computed DTC = (0.30 × 60M) / 3M = 6.0d  → dtc_score=6 (elevated)
      vendor shortRatio = 2.8d                        → dtc_score=3 (moderate)
    """

    def _run(self, **data_overrides):
        data = _flat_data(**data_overrides)
        finviz = {"short_ratio": None, "hard_to_borrow": False}
        return score_mechanics(data, finviz, pd.DataFrame())

    def test_computed_dtc_uses_float_formula(self):
        result = self._run(
            float_shares=60_000_000,
            short_pct_float=0.30,
            volume_avg_30d=3_000_000,
            short_ratio_dtc=2.8,
        )
        assert result["computed_dtc_30d"] == pytest.approx(6.0)

    def test_dtc_score_elevated_with_float_formula(self):
        # computed DTC=6 → dtc_score=6 (dtc>=5 branch)
        result = self._run(
            float_shares=60_000_000,
            short_pct_float=0.30,
            volume_avg_30d=3_000_000,
            short_ratio_dtc=2.8,
        )
        assert result["dtc_score"] == pytest.approx(6.0)

    def test_fallback_to_vendor_when_inputs_zero(self):
        # float_shares=0 → fallback to short_ratio_dtc
        result = self._run(float_shares=0, short_pct_float=0.30, short_ratio_dtc=5.0)
        assert result["computed_dtc_30d"] == pytest.approx(5.0)

    def test_fallback_to_vendor_when_si_zero(self):
        result = self._run(float_shares=60_000_000, short_pct_float=0.0, short_ratio_dtc=4.0)
        assert result["computed_dtc_30d"] == pytest.approx(4.0)


# ── CHUNK-05: vol_score excluded from composite ───────────────────────────────

class TestScoreMechanicsVolWeight:
    def _run(self, vol_5d=3_000_000, vol_30d=1_000_000):
        data = _flat_data(volume_avg_5d=vol_5d, volume_avg_30d=vol_30d)
        finviz = {"short_ratio": None, "hard_to_borrow": False}
        return score_mechanics(data, finviz, pd.DataFrame())

    def test_mech_max_excludes_vol_weight(self):
        # max should be 10*0.15 + 10*0.05 + 10*0.05 = 2.5, not 3.5
        result = self._run()
        assert result["max"] == pytest.approx(2.5)

    def test_volume_surge_does_not_inflate_composite(self):
        # even with 3x volume surge, mech score should not include vol contribution
        result_surge = self._run(vol_5d=9_000_000, vol_30d=1_000_000)
        result_flat = self._run(vol_5d=1_000_000, vol_30d=1_000_000)
        # Both should have same mech score (vol not in composite)
        assert result_surge["score"] == pytest.approx(result_flat["score"])

    def test_volume_confirmation_flag_set_on_surge_with_positive_price(self):
        prices = [38.0] * 55 + [40.0, 40.5, 41.0, 41.5, 42.0]
        data = _flat_data(
            history=_make_hist(prices),
            volume_avg_5d=4_000_000,
            volume_avg_30d=2_000_000,
        )
        finviz = {"short_ratio": None, "hard_to_borrow": False}
        result = score_mechanics(data, finviz, pd.DataFrame())
        assert result["volume_confirmation_flag"] == True

    def test_volume_confirmation_flag_false_on_quiet_vol(self):
        result = self._run(vol_5d=1_100_000, vol_30d=1_000_000)
        assert result["volume_confirmation_flag"] is False


# ── CHUNK-04: compression_recovery_score ─────────────────────────────────────

class TestScoreStructureCompressionRecovery:
    def _make_drawdown_recovery_hist(self, drawdown_pct, recovery_pct):
        """Build 63-bar history with given drawdown then recovery."""
        start = 50.0
        bottom = start * (1 - drawdown_pct)
        end = bottom * (1 + recovery_pct)
        step_down = (start - bottom) / 20
        step_up = (end - bottom) / 42
        prices = (
            [start]
            + [start - i * step_down for i in range(20)]
            + [bottom + i * step_up for i in range(42)]
        )
        assert len(prices) == 63
        return _make_hist(prices)

    def _run(self, hist, short_pct=0.35):
        data = {
            "history": hist,
            "short_pct_float": short_pct,
            "market_cap": 2_500_000_000,
            "float_shares": 60_000_000,
            "price": float(hist["Close"].iloc[-1]),
        }
        return score_structure(data)

    def test_strong_compression_recovery_scores_10(self):
        hist = self._make_drawdown_recovery_hist(0.40, 0.30)
        result = self._run(hist)
        assert result["comp_rec_score"] == pytest.approx(10.0)

    def test_moderate_compression_recovery_scores_6(self):
        hist = self._make_drawdown_recovery_hist(0.22, 0.12)
        result = self._run(hist)
        assert result["comp_rec_score"] == pytest.approx(6.0)

    def test_si_gate_blocks_score_below_20pct(self):
        hist = self._make_drawdown_recovery_hist(0.40, 0.30)
        result = self._run(hist, short_pct=0.15)
        assert result["comp_rec_score"] == pytest.approx(0.0)

    def test_struct_max_includes_compression_weight(self):
        # max should be 10*(0.07+0.07+0.06+0.04) = 2.4
        hist = _make_hist(40.0, 63)
        data = {
            "history": hist,
            "short_pct_float": 0.10,
            "market_cap": 0,
            "float_shares": 0,
            "price": 40.0,
        }
        result = score_structure(data)
        assert result["max"] == pytest.approx(2.4)

    def test_insufficient_history_skips_compression_signal(self):
        hist = _make_hist(40.0, 30)
        data = {
            "history": hist,
            "short_pct_float": 0.35,
            "market_cap": 0,
            "float_shares": 0,
            "price": 40.0,
        }
        result = score_structure(data)
        assert result["comp_rec_score"] == pytest.approx(0.0)


# ── SqueezeScore new fields ───────────────────────────────────────────────────

class TestSqueezeScoreNewFields:
    def test_default_new_fields(self):
        s = SqueezeScore(
            ticker="X", final_score=50.0, signal_breakdown={},
            juice_target=30.0, recent_squeeze=False
        )
        assert s.computed_dtc_30d == 0.0
        assert s.compression_recovery_score == 0.0
        assert s.volume_confirmation_flag is False
        assert s.squeeze_state == "false"

    def test_to_dict_includes_new_fields(self):
        s = SqueezeScore(
            ticker="X", final_score=50.0, signal_breakdown={},
            juice_target=30.0, recent_squeeze=False,
            computed_dtc_30d=6.5, squeeze_state="active",
            volume_confirmation_flag=True, compression_recovery_score=7.0,
        )
        d = s.to_dict()
        assert d["computed_dtc_30d"] == 6.5
        assert d["squeeze_state"] == "active"
        assert d["volume_confirmation_flag"] is True
        assert d["compression_recovery_score"] == 7.0


# =============================================================================
# CHUNK-02: compute_si_persistence_score tests
# =============================================================================

def _si_row(pub_date, si_pct, settlement_date=None):
    """Helper: build a minimal short_interest_history-style row."""
    return {
        "publication_date": pub_date,
        "settlement_date": settlement_date,
        "short_pct_float": si_pct,
    }


class TestSIPersistenceScore:

    # 1. neutral with no history
    def test_neutral_with_no_history(self):
        result = compute_si_persistence_score([], latest_short_pct=0.80)
        assert result["si_persistence_score"] == pytest.approx(5.0)
        assert result["si_persistence_count"] == 0
        assert result["si_trend_direction"] == "unknown"

    # 2. neutral with exactly one period (no matter how high SI is)
    def test_neutral_with_one_period(self):
        rows = [_si_row("2024-01-15", 0.80)]
        result = compute_si_persistence_score(rows, 0.80, score_date=date(2024, 2, 1))
        assert result["si_persistence_score"] == pytest.approx(5.0)
        assert result["si_persistence_count"] == 1
        assert result["si_trend_direction"] == "unknown"

    # 3. daily duplicates are collapsed to one distinct period
    def test_daily_duplicates_count_as_one_period(self):
        # Three consecutive days — same SI, no settlement_date → 10-day gap rule
        rows = [
            _si_row("2024-01-15", 0.80),
            _si_row("2024-01-16", 0.80),
            _si_row("2024-01-17", 0.80),
        ]
        result = compute_si_persistence_score(rows, 0.80, score_date=date(2024, 2, 1))
        assert result["si_persistence_count"] == 1
        assert result["si_persistence_score"] == pytest.approx(5.0)

    # 4. three periods spaced ≥10 days apart with SI > 40% → score 10
    def test_high_score_for_three_spaced_periods_above_40pct(self):
        rows = [
            _si_row("2024-01-01", 0.45),
            _si_row("2024-01-15", 0.47),
            _si_row("2024-02-01", 0.50),
        ]
        result = compute_si_persistence_score(rows, 0.50, score_date=date(2024, 2, 10))
        assert result["si_persistence_score"] == pytest.approx(10.0)
        assert result["si_persistence_count"] == 3

    # 4b. three periods with SI > 30% but not all > 40% → score 8
    def test_score_8_for_three_spaced_periods_above_30pct(self):
        rows = [
            _si_row("2024-01-01", 0.31),
            _si_row("2024-01-15", 0.33),
            _si_row("2024-02-01", 0.35),
        ]
        result = compute_si_persistence_score(rows, 0.35, score_date=date(2024, 2, 10))
        assert result["si_persistence_score"] == pytest.approx(8.0)

    # 5. rising trend across 2+ spaced periods → score 7, trend "rising"
    def test_rising_trend_two_spaced_periods(self):
        rows = [
            _si_row("2024-01-01", 0.20),
            _si_row("2024-01-15", 0.28),  # +8pp → rising
        ]
        result = compute_si_persistence_score(rows, 0.28, score_date=date(2024, 2, 1))
        assert result["si_trend_direction"] == "rising"
        assert result["si_persistence_score"] == pytest.approx(7.0)
        assert result["si_persistence_count"] == 2

    # 6. falling trend → score 3, trend "falling"
    def test_falling_trend_penalizes_score(self):
        rows = [
            _si_row("2024-01-01", 0.55),
            _si_row("2024-01-15", 0.42),  # -13pp → falling
        ]
        result = compute_si_persistence_score(rows, 0.42, score_date=date(2024, 2, 1))
        assert result["si_trend_direction"] == "falling"
        assert result["si_persistence_score"] == pytest.approx(3.0)

    # 7. anti-lookahead: future rows (pub_date > score_date) must be excluded
    def test_filters_future_publication_dates(self):
        score_date = date(2024, 2, 1)
        rows = [
            _si_row("2024-01-01", 0.45),            # past — included
            _si_row("2024-01-15", 0.47),            # past — included
            _si_row("2024-02-10", 0.55),            # FUTURE — must be excluded
            _si_row("2024-03-01", 0.60),            # FUTURE — must be excluded
        ]
        result = compute_si_persistence_score(rows, 0.47, score_date=score_date)
        # Only two past rows pass, and they are spaced 14 days apart (≥10)
        assert result["si_persistence_count"] == 2
        # Both past rows have SI < 40% but one period is rising → 7 or 5 depending on delta
        # delta = 0.47 - 0.45 = 0.02 — not enough for "rising" (needs >= 0.05)
        assert result["si_trend_direction"] == "stable"
        assert result["si_persistence_score"] == pytest.approx(5.0)

    # 7b. confirm future rows would change the score if NOT filtered
    def test_future_rows_would_inflate_score_without_filter(self):
        score_date = date(2024, 2, 1)
        future_only = [_si_row("2024-02-10", 0.55), _si_row("2024-03-01", 0.60)]
        result = compute_si_persistence_score(future_only, 0.60, score_date=score_date)
        # All rows are future → filtered out → neutral
        assert result["si_persistence_count"] == 0
        assert result["si_persistence_score"] == pytest.approx(5.0)

    # 8. settlement_date rows each count as distinct periods regardless of gap
    def test_settlement_date_rows_each_count_once(self):
        # Three rows with distinct settlement dates on consecutive days — no gap needed
        rows = [
            _si_row("2024-01-15", 0.42, settlement_date="2024-01-14"),
            _si_row("2024-01-16", 0.44, settlement_date="2024-01-28"),
            _si_row("2024-01-30", 0.46, settlement_date="2024-02-11"),
        ]
        result = compute_si_persistence_score(rows, 0.46, score_date=date(2024, 2, 20))
        assert result["si_persistence_count"] == 3
        assert result["si_persistence_score"] == pytest.approx(10.0)


# =============================================================================
# CHUNK-02: _build_si_snapshot tests
# =============================================================================

class TestBuildSiSnapshot:

    def _make_data(self, short_pct, float_shares, avg_vol_30d, short_ratio_dtc):
        import pandas as pd
        hist = pd.DataFrame({"Close": [40.0] * 60, "Volume": [1e6] * 60})
        return {
            "ticker": "CAR",
            "short_pct_float": short_pct,
            "float_shares": float_shares,
            "volume_avg_30d": avg_vol_30d,
            "short_ratio_dtc": short_ratio_dtc,
        }

    def _make_sq(self, computed_dtc_30d):
        return SqueezeScore(
            ticker="CAR", final_score=55.0, signal_breakdown={},
            juice_target=60.0, recent_squeeze=False,
            computed_dtc_30d=computed_dtc_30d,
        )

    def test_shares_short_computed_from_pct_and_float(self):
        # SI=80%, float=10.1M → shares_short ≈ 8,080,000
        data = self._make_data(0.80, 10_100_000, 840_000, 3.5)
        sq = self._make_sq(9.619)
        snap = _build_si_snapshot("CAR", data, sq)
        assert snap["shares_short"] == pytest.approx(8_080_000, rel=1e-3)

    def test_computed_dtc_propagated_from_squeeze_score(self):
        data = self._make_data(0.80, 10_100_000, 840_000, 3.5)
        sq = self._make_sq(9.619)
        snap = _build_si_snapshot("CAR", data, sq)
        assert snap["computed_dtc_30d"] == pytest.approx(9.619)

    def test_source_is_yfinance_snapshot(self):
        data = self._make_data(0.30, 60_000_000, 3_000_000, 2.8)
        sq = self._make_sq(6.0)
        snap = _build_si_snapshot("CAR", data, sq)
        assert snap["source"] == "yfinance_snapshot"

    def test_settlement_date_is_none_for_yfinance(self):
        data = self._make_data(0.30, 60_000_000, 3_000_000, 2.8)
        sq = self._make_sq(6.0)
        snap = _build_si_snapshot("CAR", data, sq)
        assert snap["settlement_date"] is None

    def test_data_confidence_score_is_conservative(self):
        data = self._make_data(0.30, 60_000_000, 3_000_000, 2.8)
        sq = self._make_sq(6.0)
        snap = _build_si_snapshot("CAR", data, sq)
        assert snap["data_confidence_score"] == pytest.approx(0.5)


# ── CHUNK-10: lifecycle state integration ─────────────────────────────────────

class TestLifecycleStateInSqueezeScore:

    def test_squeeze_score_contains_squeeze_state_field(self):
        """compute_squeeze_score must populate squeeze_state with a lifecycle value."""
        from squeeze_screener import compute_squeeze_score
        from unittest.mock import patch

        data = _flat_data(short_pct_float=0.35)
        with patch("squeeze_screener._load_filing_catalysts", return_value=[]):
            with patch("squeeze_screener._load_si_history", return_value=[]):
                sq = compute_squeeze_score("TEST", data, {}, pd.DataFrame(),
                                           si_history=[], filing_catalysts=[])

        assert sq.squeeze_state in ("NOT_SETUP", "ARMED", "ACTIVE")

    def test_squeeze_score_contains_state_confidence(self):
        from squeeze_screener import compute_squeeze_score
        from unittest.mock import patch

        data = _flat_data(short_pct_float=0.35)
        with patch("squeeze_screener._load_filing_catalysts", return_value=[]):
            with patch("squeeze_screener._load_si_history", return_value=[]):
                sq = compute_squeeze_score("TEST", data, {}, pd.DataFrame(),
                                           si_history=[], filing_catalysts=[])

        assert sq.state_confidence in ("low", "medium", "high")
        assert isinstance(sq.state_reasons, list)
        assert isinstance(sq.state_warnings, list)

    def test_low_si_ticker_is_not_setup(self):
        from squeeze_screener import compute_squeeze_score
        from unittest.mock import patch

        data = _flat_data(short_pct_float=0.05)
        with patch("squeeze_screener._load_filing_catalysts", return_value=[]):
            with patch("squeeze_screener._load_si_history", return_value=[]):
                sq = compute_squeeze_score("TEST", data, {}, pd.DataFrame(),
                                           si_history=[], filing_catalysts=[])

        assert sq.squeeze_state == "NOT_SETUP"
