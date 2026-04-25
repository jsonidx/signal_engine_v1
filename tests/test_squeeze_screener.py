"""
Unit tests for Phase 0 + Phase 1 squeeze screener fixes:
  - Float-adjusted DTC formula (CHUNK-01)
  - 3-state detect_recent_squeeze (CHUNK-03)
  - compression_recovery_score signal (CHUNK-04)
  - vol_score excluded from composite (CHUNK-05)
  - SqueezeScore new fields
"""

import pandas as pd
import numpy as np
import pytest

from squeeze_screener import (
    detect_recent_squeeze,
    score_mechanics,
    score_structure,
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
