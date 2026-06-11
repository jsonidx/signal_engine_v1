"""
Tests for TRD-044: Option Target Calibration and Legacy Comparator

Covers:
- MethodStats construction, sparse flag, None suppression
- _safe_rate / _safe_mean / _safe_median with edge cases
- compare_methods(): overall stats, mixed legacy/v2, full-None inputs
- Cohort breakdowns by preset, delta bucket, DTE bucket
- Sparse cohort handling (n < MIN_COHORT_SIZE → rates suppressed)
- V2-eligible subset isolation (only delta_only / delta_dte_adjusted rows)
- method_comparison_to_dict() serialisation roundtrip
- resolve_snapshot() now writes target_projection_method + v2 hit markers
"""

from __future__ import annotations

import pytest

from utils.option_comparator import (
    MIN_COHORT_SIZE,
    MethodComparison,
    MethodStats,
    _V2_METHODS,
    _delta_bucket,
    _dte_bucket,
    _method_stats_for_rows,
    _safe_mean,
    _safe_median,
    _safe_rate,
    compare_methods,
    method_comparison_to_dict,
)


# ── Fixtures / row builders ────────────────────────────────────────────────────

def _row(
    strategy_preset="long_call",
    delta=0.40,
    dte=30,
    direction="BULL",
    target_projection_method="delta_only",
    option_return_5d_pct=5.0,
    hit_option_tp1=True,
    hit_option_tp2=False,
    hit_option_stop=False,
    hit_v2_tp1=True,
    hit_v2_tp2=False,
    hit_v2_stop=False,
    hit_underlying_t1=True,
    hit_underlying_t2=False,
    hit_underlying_stop=False,
) -> dict:
    return {
        "strategy_preset": strategy_preset,
        "delta": delta,
        "dte": dte,
        "direction": direction,
        "target_projection_method": target_projection_method,
        "option_return_5d_pct": option_return_5d_pct,
        "hit_option_tp1": hit_option_tp1,
        "hit_option_tp2": hit_option_tp2,
        "hit_option_stop": hit_option_stop,
        "hit_v2_tp1": hit_v2_tp1,
        "hit_v2_tp2": hit_v2_tp2,
        "hit_v2_stop": hit_v2_stop,
        "hit_underlying_t1": hit_underlying_t1,
        "hit_underlying_t2": hit_underlying_t2,
        "hit_underlying_stop": hit_underlying_stop,
    }


def _rows(n: int, **kw) -> list[dict]:
    return [_row(**kw) for _ in range(n)]


# ══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ══════════════════════════════════════════════════════════════════════════════

class TestSafeRate:
    def test_all_true(self):
        assert _safe_rate([True, True, True]) == 100.0

    def test_all_false(self):
        assert _safe_rate([False, False]) == 0.0

    def test_mixed(self):
        assert _safe_rate([True, False, True, False]) == 50.0

    def test_none_excluded(self):
        assert _safe_rate([True, None, False]) == 50.0

    def test_all_none(self):
        assert _safe_rate([None, None]) is None

    def test_empty(self):
        assert _safe_rate([]) is None

    def test_one_of_four(self):
        assert _safe_rate([True, False, False, False]) == 25.0


class TestSafeMean:
    def test_basic(self):
        assert _safe_mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)

    def test_none_excluded(self):
        assert _safe_mean([4.0, None, 2.0]) == pytest.approx(3.0)

    def test_all_none(self):
        assert _safe_mean([None]) is None

    def test_empty(self):
        assert _safe_mean([]) is None


class TestSafeMedian:
    def test_basic_odd(self):
        assert _safe_median([1.0, 3.0, 5.0]) == pytest.approx(3.0)

    def test_basic_even(self):
        assert _safe_median([1.0, 3.0]) == pytest.approx(2.0)

    def test_none_excluded(self):
        assert _safe_median([10.0, None, 20.0]) == pytest.approx(15.0)

    def test_empty(self):
        assert _safe_median([]) is None


class TestBuckets:
    def test_delta_otm(self):
        assert _delta_bucket(0.20) == "OTM (<0.30)"

    def test_delta_atm_lower(self):
        assert _delta_bucket(0.30) == "ATM (0.30-0.45)"

    def test_delta_atm_upper(self):
        assert _delta_bucket(0.45) == "ATM (0.30-0.45)"

    def test_delta_itm(self):
        assert _delta_bucket(0.60) == "ITM (>0.45)"

    def test_delta_negative_put(self):
        assert _delta_bucket(-0.35) == "ATM (0.30-0.45)"

    def test_delta_none(self):
        assert _delta_bucket(None) == "Unknown"

    def test_dte_short(self):
        assert _dte_bucket(14) == "≤21d"

    def test_dte_medium_low(self):
        assert _dte_bucket(30) == "22-45d"

    def test_dte_medium_high(self):
        assert _dte_bucket(90) == "46-90d"

    def test_dte_long(self):
        assert _dte_bucket(180) == ">90d"

    def test_dte_none(self):
        assert _dte_bucket(None) == "Unknown"


# ══════════════════════════════════════════════════════════════════════════════
# _method_stats_for_rows
# ══════════════════════════════════════════════════════════════════════════════

class TestMethodStatsForRows:

    def _make_rows(self, n=MIN_COHORT_SIZE + 2):
        return _rows(n)

    def test_legacy_stats_basic(self):
        rows = _rows(MIN_COHORT_SIZE + 1, hit_option_tp1=True, hit_option_tp2=False,
                     hit_option_stop=False, option_return_5d_pct=10.0)
        ms = _method_stats_for_rows(rows, "legacy")
        assert ms.method == "legacy"
        assert ms.tp1_hit_rate == 100.0
        assert ms.tp2_hit_rate == 0.0
        assert ms.stop_hit_rate == 0.0
        assert ms.mean_return_pct == pytest.approx(10.0)
        assert not ms.sparse

    def test_v2_stats(self):
        rows = _rows(MIN_COHORT_SIZE + 1, hit_v2_tp1=True, hit_v2_tp2=True,
                     hit_v2_stop=False, option_return_5d_pct=15.0)
        ms = _method_stats_for_rows(rows, "v2")
        assert ms.tp1_hit_rate == 100.0
        assert ms.tp2_hit_rate == 100.0
        assert ms.stop_hit_rate == 0.0

    def test_underlying_stats(self):
        rows = _rows(MIN_COHORT_SIZE + 1, hit_underlying_t1=True, hit_underlying_t2=False,
                     hit_underlying_stop=False)
        ms = _method_stats_for_rows(rows, "underlying")
        assert ms.tp1_hit_rate == 100.0
        assert ms.tp2_hit_rate == 0.0

    def test_sparse_flag_when_below_threshold(self):
        rows = _rows(MIN_COHORT_SIZE - 1)
        ms = _method_stats_for_rows(rows, "legacy")
        assert ms.sparse is True
        assert ms.tp1_hit_rate is None
        assert ms.tp2_hit_rate is None
        assert ms.stop_hit_rate is None
        assert ms.mean_return_pct is None

    def test_exactly_at_threshold_not_sparse(self):
        rows = _rows(MIN_COHORT_SIZE)
        ms = _method_stats_for_rows(rows, "legacy")
        assert ms.sparse is False

    def test_empty_rows(self):
        ms = _method_stats_for_rows([], "legacy")
        assert ms.n == 0
        assert ms.sparse is True

    def test_mixed_returns(self):
        rows = [
            _row(option_return_5d_pct=10.0),
            _row(option_return_5d_pct=20.0),
            _row(option_return_5d_pct=-5.0),
            _row(option_return_5d_pct=0.0),
            _row(option_return_5d_pct=15.0),
            _row(option_return_5d_pct=5.0),
        ]
        ms = _method_stats_for_rows(rows, "legacy")
        assert ms.mean_return_pct == pytest.approx((10 + 20 - 5 + 0 + 15 + 5) / 6, abs=0.1)

    def test_none_hits_excluded_from_rate(self):
        rows = _rows(MIN_COHORT_SIZE + 1, hit_option_tp1=None)
        ms = _method_stats_for_rows(rows, "legacy")
        # All hits are None → rate is None (no valid data)
        assert ms.tp1_hit_rate is None

    def test_n_matches_input_length(self):
        rows = _rows(9)
        ms = _method_stats_for_rows(rows, "legacy")
        assert ms.n == 9


# ══════════════════════════════════════════════════════════════════════════════
# compare_methods — overall
# ══════════════════════════════════════════════════════════════════════════════

class TestCompareMethods:

    def test_returns_method_comparison(self):
        rows = _rows(10)
        mc = compare_methods(rows)
        assert isinstance(mc, MethodComparison)

    def test_total_rows(self):
        rows = _rows(12)
        mc = compare_methods(rows)
        assert mc.total_rows == 12

    def test_v2_eligible_count_correct(self):
        v2_rows = _rows(7, target_projection_method="delta_only")
        leg_rows = _rows(3, target_projection_method=None)
        mc = compare_methods(v2_rows + leg_rows)
        assert mc.v2_eligible_rows == 7

    def test_v2_eligible_includes_delta_dte_adjusted(self):
        rows = _rows(5, target_projection_method="delta_dte_adjusted")
        mc = compare_methods(rows)
        assert mc.v2_eligible_rows == 5

    def test_insufficient_inputs_not_v2_eligible(self):
        rows = _rows(5, target_projection_method="insufficient_inputs")
        mc = compare_methods(rows)
        assert mc.v2_eligible_rows == 0

    def test_none_method_not_v2_eligible(self):
        rows = _rows(5, target_projection_method=None)
        mc = compare_methods(rows)
        assert mc.v2_eligible_rows == 0

    def test_overall_legacy_uses_all_rows(self):
        rows = _rows(10, hit_option_tp1=True)
        mc = compare_methods(rows)
        assert mc.overall_legacy.n == 10
        assert mc.overall_legacy.tp1_hit_rate == 100.0

    def test_overall_v2_uses_only_eligible_rows(self):
        v2 = _rows(6, target_projection_method="delta_only", hit_v2_tp1=True)
        leg = _rows(4, target_projection_method=None, hit_v2_tp1=False)
        mc = compare_methods(v2 + leg)
        # v2 stats should only be computed from the 6 eligible rows
        assert mc.overall_v2.n == 6
        assert mc.overall_v2.tp1_hit_rate == 100.0

    def test_legacy_and_v2_can_diverge(self):
        """V2 hit rate differs from legacy hit rate when targets differ."""
        rows = [
            _row(target_projection_method="delta_only",
                 hit_option_tp1=True, hit_v2_tp1=False),
            _row(target_projection_method="delta_only",
                 hit_option_tp1=False, hit_v2_tp1=True),
            _row(target_projection_method="delta_only",
                 hit_option_tp1=True, hit_v2_tp1=True),
            _row(target_projection_method="delta_only",
                 hit_option_tp1=False, hit_v2_tp1=False),
            _row(target_projection_method="delta_only",
                 hit_option_tp1=True, hit_v2_tp1=True),
            _row(target_projection_method="delta_only",
                 hit_option_tp1=False, hit_v2_tp1=True),
        ]
        mc = compare_methods(rows)
        # Legacy: 3/6 = 50%, V2: 4/6 ≈ 66.7%
        assert mc.overall_legacy.tp1_hit_rate == pytest.approx(50.0)
        assert mc.overall_v2.tp1_hit_rate == pytest.approx(66.7, abs=0.2)

    def test_resolution_type_stored(self):
        mc = compare_methods(_rows(5), resolution_type="10d")
        assert mc.resolution_type == "10d"

    def test_empty_input(self):
        mc = compare_methods([])
        assert mc.total_rows == 0
        assert mc.overall_legacy.n == 0
        assert mc.overall_legacy.sparse is True

    def test_stop_hit_rate_legacy(self):
        rows = [
            _row(hit_option_stop=True),
            _row(hit_option_stop=False),
            _row(hit_option_stop=True),
            _row(hit_option_stop=False),
            _row(hit_option_stop=False),
            _row(hit_option_stop=False),
        ]
        mc = compare_methods(rows)
        assert mc.overall_legacy.stop_hit_rate == pytest.approx(100 * 2 / 6, abs=0.2)

    def test_underlying_stats_in_output(self):
        rows = _rows(8, hit_underlying_t1=True, hit_underlying_stop=False)
        mc = compare_methods(rows)
        assert mc.overall_underlying.tp1_hit_rate == 100.0
        assert mc.overall_underlying.stop_hit_rate == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# compare_methods — mixed legacy/v2 datasets
# ══════════════════════════════════════════════════════════════════════════════

class TestMixedDatasets:

    def test_mixed_methods_both_tracked(self):
        v2  = _rows(5, target_projection_method="delta_only",   hit_option_tp1=True,  hit_v2_tp1=False)
        leg = _rows(5, target_projection_method=None,            hit_option_tp1=False, hit_v2_tp1=None)
        mc = compare_methods(v2 + leg)
        # Legacy tp1: 5 True + 5 False = 50%
        assert mc.overall_legacy.tp1_hit_rate == pytest.approx(50.0)
        # V2 tp1: only the 5 delta_only rows count; all False
        assert mc.overall_v2.tp1_hit_rate == 0.0
        assert mc.overall_v2.n == 5

    def test_all_insufficient_inputs_no_v2_stats(self):
        rows = _rows(8, target_projection_method="insufficient_inputs",
                     hit_v2_tp1=None, hit_v2_tp2=None, hit_v2_stop=None)
        mc = compare_methods(rows)
        assert mc.v2_eligible_rows == 0
        assert mc.overall_v2.n == 0

    def test_legacy_rates_unaffected_by_v2_availability(self):
        """Legacy rates should be the same regardless of whether v2 was available."""
        v2   = _rows(4, target_projection_method="delta_only",   hit_option_tp1=True)
        none = _rows(4, target_projection_method=None,            hit_option_tp1=True)
        mc = compare_methods(v2 + none)
        assert mc.overall_legacy.tp1_hit_rate == 100.0


# ══════════════════════════════════════════════════════════════════════════════
# Cohort breakdowns
# ══════════════════════════════════════════════════════════════════════════════

class TestCohortBreakdown:

    def _make_mixed_preset_rows(self) -> list[dict]:
        return (
            _rows(MIN_COHORT_SIZE + 2, strategy_preset="long_call",  hit_option_tp1=True)
            + _rows(MIN_COHORT_SIZE + 1, strategy_preset="long_put",   hit_option_tp1=False)
            + _rows(2,                   strategy_preset="leaps_call", hit_option_tp1=True)
        )

    def test_by_preset_cohort_labels(self):
        mc = compare_methods(self._make_mixed_preset_rows())
        labels = {c.cohort_label for c in mc.by_preset}
        assert "long_call" in labels
        assert "long_put" in labels

    def test_by_preset_rates_per_cohort(self):
        mc = compare_methods(self._make_mixed_preset_rows())
        by_label = {c.cohort_label: c for c in mc.by_preset}
        assert by_label["long_call"].legacy.tp1_hit_rate == 100.0
        assert by_label["long_put"].legacy.tp1_hit_rate == 0.0

    def test_sparse_cohort_suppresses_rates(self):
        mc = compare_methods(self._make_mixed_preset_rows())
        by_label = {c.cohort_label: c for c in mc.by_preset}
        leaps = by_label["leaps_call"]
        assert leaps.sparse is True
        assert leaps.legacy.tp1_hit_rate is None

    def test_by_delta_bucket_labels(self):
        rows = (
            _rows(MIN_COHORT_SIZE + 1, delta=0.20)   # OTM
            + _rows(MIN_COHORT_SIZE + 1, delta=0.38)  # ATM
            + _rows(MIN_COHORT_SIZE + 1, delta=0.60)  # ITM
        )
        mc = compare_methods(rows)
        labels = {c.cohort_label for c in mc.by_delta_bucket}
        assert "OTM (<0.30)" in labels
        assert "ATM (0.30-0.45)" in labels
        assert "ITM (>0.45)" in labels

    def test_by_delta_bucket_put_uses_abs(self):
        """Puts with delta=-0.35 should land in ATM bucket, not OTM."""
        rows = _rows(MIN_COHORT_SIZE + 1, delta=-0.35)
        mc = compare_methods(rows)
        labels = {c.cohort_label for c in mc.by_delta_bucket}
        assert "ATM (0.30-0.45)" in labels

    def test_by_dte_bucket_labels(self):
        rows = (
            _rows(MIN_COHORT_SIZE + 1, dte=14)    # ≤21d
            + _rows(MIN_COHORT_SIZE + 1, dte=30)  # 22-45d
            + _rows(MIN_COHORT_SIZE + 1, dte=60)  # 46-90d
            + _rows(MIN_COHORT_SIZE + 1, dte=180) # >90d
        )
        mc = compare_methods(rows)
        labels = {c.cohort_label for c in mc.by_dte_bucket}
        assert "≤21d" in labels
        assert "22-45d" in labels
        assert "46-90d" in labels
        assert ">90d" in labels

    def test_cohort_n_matches_row_count(self):
        rows = _rows(7, strategy_preset="long_call")
        mc = compare_methods(rows)
        by_label = {c.cohort_label: c for c in mc.by_preset}
        assert by_label["long_call"].n == 7

    def test_cohort_v2_subset_isolation(self):
        """Within a cohort, v2 stats only use rows with a valid v2 method."""
        rows = (
            _rows(4, strategy_preset="long_call", target_projection_method="delta_only",   hit_v2_tp1=True)
            + _rows(4, strategy_preset="long_call", target_projection_method=None,          hit_v2_tp1=False)
        )
        mc = compare_methods(rows)
        by_label = {c.cohort_label: c for c in mc.by_preset}
        lc = by_label["long_call"]
        # Only 4 v2-eligible rows → v2 stats sparse
        assert lc.v2.sparse is True

    def test_cohort_v2_non_sparse(self):
        rows = _rows(MIN_COHORT_SIZE + 1, strategy_preset="long_call",
                     target_projection_method="delta_only", hit_v2_tp1=True)
        mc = compare_methods(rows)
        by_label = {c.cohort_label: c for c in mc.by_preset}
        lc = by_label["long_call"]
        assert lc.v2.sparse is False
        assert lc.v2.tp1_hit_rate == 100.0


# ══════════════════════════════════════════════════════════════════════════════
# method_comparison_to_dict
# ══════════════════════════════════════════════════════════════════════════════

class TestSerialisation:

    STATS_KEYS = {
        "method", "n", "tp1_hit_rate", "tp2_hit_rate", "stop_hit_rate",
        "mean_return_pct", "median_return_pct", "sparse", "note",
    }
    COHORT_KEYS = {
        "dimension", "cohort_label", "n", "sparse", "legacy", "v2", "underlying",
    }

    def test_top_level_keys(self):
        mc = compare_methods(_rows(8))
        d = method_comparison_to_dict(mc)
        assert "total_rows" in d
        assert "v2_eligible_rows" in d
        assert "resolution_type" in d
        assert "overall_legacy" in d
        assert "overall_v2" in d
        assert "overall_underlying" in d
        assert "by_preset" in d
        assert "by_delta_bucket" in d
        assert "by_dte_bucket" in d

    def test_stats_keys_present(self):
        mc = compare_methods(_rows(8))
        d = method_comparison_to_dict(mc)
        assert self.STATS_KEYS <= set(d["overall_legacy"].keys())

    def test_cohort_keys_present(self):
        mc = compare_methods(_rows(8))
        d = method_comparison_to_dict(mc)
        for cohort in d["by_preset"]:
            assert self.COHORT_KEYS <= set(cohort.keys())

    def test_json_serialisable(self):
        import json
        mc = compare_methods(_rows(8))
        d = method_comparison_to_dict(mc)
        json.dumps(d)   # must not raise

    def test_sparse_none_values_serialised(self):
        """Sparse cohorts must show None for rate fields — never fake numbers."""
        mc = compare_methods(_rows(2))   # all sparse
        d = method_comparison_to_dict(mc)
        assert d["overall_legacy"]["tp1_hit_rate"] is None

    def test_round_trip_values(self):
        rows = _rows(8, hit_option_tp1=True, option_return_5d_pct=20.0)
        mc = compare_methods(rows)
        d = method_comparison_to_dict(mc)
        assert d["overall_legacy"]["tp1_hit_rate"] == 100.0
        assert d["overall_legacy"]["mean_return_pct"] == pytest.approx(20.0)


# ══════════════════════════════════════════════════════════════════════════════
# resolve_snapshot v2 fields (TRD-044 extension of option_outcomes.py)
# ══════════════════════════════════════════════════════════════════════════════

class TestResolveSnapshotV2Fields:
    """
    Verify that resolve_snapshot() now includes target_projection_method and
    hit_v2_tp1/tp2/stop in every outcome dict.
    Uses a mock yfinance via monkeypatching to avoid network calls.
    """

    def _make_snapshot(self, **overrides) -> dict:
        from datetime import date, timedelta
        snap = {
            "ticker": "AAPL",
            "run_date": (date.today() - timedelta(days=10)).isoformat(),
            "mid": 2.0,
            "delta": 0.40,
            "direction": "BULL",
            "underlying_target_1": 155.0,
            "underlying_target_2": 160.0,
            "underlying_stop": 145.0,
            "option_take_profit_1": 3.0,    # legacy: 1.5× mid
            "option_take_profit_2": 4.0,    # legacy: 2× mid
            "option_stop_loss": 1.0,        # legacy: 0.5× mid
            "projected_option_tp1": 2.80,   # v2: tighter target
            "projected_option_tp2": 3.60,
            "projected_option_stop": 1.10,
            "target_projection_method": "delta_only",
            "holding_window_days": 14,
        }
        snap.update(overrides)
        return snap

    def _fake_hist(self, entry_price: float, exit_price: float | None = None):
        """
        Return a mock DataFrame anchored to rec_date (today-10):
          - rec_date → entry_price
          - all subsequent dates → exit_price
        This ensures _close_on_date() returns different values for entry vs
        the 1d/5d/10d windows.
        """
        import pandas as pd
        from datetime import date, timedelta
        if exit_price is None:
            exit_price = entry_price
        # rec_date = today - 10  (matches _make_snapshot)
        rec_date = date.today() - timedelta(days=10)
        dates = [rec_date + timedelta(days=i) for i in range(30)]
        prices = [entry_price] + [exit_price] * 29
        df = pd.DataFrame({"Close": prices}, index=pd.DatetimeIndex(dates))
        return df

    def test_v2_fields_present_in_outcome(self, monkeypatch):
        """resolve_snapshot() outcomes must include the four new fields."""
        import utils.option_outcomes as oo
        monkeypatch.setattr(oo, "_fetch_history", lambda t, s, e: self._fake_hist(150.0, 152.0))

        outcomes = oo.resolve_snapshot(self._make_snapshot(), run_resolution_types=("5d",))
        assert len(outcomes) == 1
        outcome = outcomes[0]["outcome"]
        assert "target_projection_method" in outcome
        assert "hit_v2_tp1" in outcome
        assert "hit_v2_tp2" in outcome
        assert "hit_v2_stop" in outcome

    def test_target_projection_method_copied(self, monkeypatch):
        import utils.option_outcomes as oo
        monkeypatch.setattr(oo, "_fetch_history", lambda t, s, e: self._fake_hist(150.0, 152.0))
        outcomes = oo.resolve_snapshot(self._make_snapshot(), run_resolution_types=("5d",))
        outcome = outcomes[0]["outcome"]
        assert outcome["target_projection_method"] == "delta_only"

    def test_v2_tp1_hit_when_price_rises_enough(self, monkeypatch):
        """
        Entry at 150, exit at 200 (+33%):
        approx_option = 2.0 × (1 + 0.40 × 33.3) = 2.0 × 14.3 = much higher than v2_tp1=2.20.
        """
        import utils.option_outcomes as oo
        monkeypatch.setattr(oo, "_fetch_history", lambda t, s, e: self._fake_hist(150.0, 200.0))

        snap = self._make_snapshot(projected_option_tp1=2.20)
        outcomes = oo.resolve_snapshot(snap, run_resolution_types=("5d",))
        outcome = outcomes[0]["outcome"]
        assert outcome["hit_v2_tp1"] is True

    def test_v2_stop_hit_when_price_falls_enough(self, monkeypatch):
        """
        Entry at 150, exit at 50 (−66.7%):
        approx new_mid = 2.0 × (1 + 0.40 × (−66.7)/100) ≈ 1.47 < v2_stop=1.50.
        """
        import utils.option_outcomes as oo
        monkeypatch.setattr(oo, "_fetch_history", lambda t, s, e: self._fake_hist(150.0, 50.0))

        snap = self._make_snapshot(projected_option_stop=1.50)
        outcomes = oo.resolve_snapshot(snap, run_resolution_types=("5d",))
        outcome = outcomes[0]["outcome"]
        assert outcome["hit_v2_stop"] is True

    def test_v2_fields_none_when_v2_targets_absent(self, monkeypatch):
        """No projected targets in snapshot → v2 hits are all None."""
        import utils.option_outcomes as oo
        monkeypatch.setattr(oo, "_fetch_history", lambda t, s, e: self._fake_hist(150.0, 155.0))

        snap = self._make_snapshot(
            projected_option_tp1=None,
            projected_option_tp2=None,
            projected_option_stop=None,
            target_projection_method="insufficient_inputs",
        )
        outcomes = oo.resolve_snapshot(snap, run_resolution_types=("5d",))
        outcome = outcomes[0]["outcome"]
        assert outcome["hit_v2_tp1"] is None
        assert outcome["hit_v2_tp2"] is None
        assert outcome["hit_v2_stop"] is None

    def test_legacy_fields_still_computed(self, monkeypatch):
        """Existing legacy hit fields must still be present and correct."""
        import utils.option_outcomes as oo
        monkeypatch.setattr(oo, "_fetch_history", lambda t, s, e: self._fake_hist(150.0, 170.0))

        outcomes = oo.resolve_snapshot(self._make_snapshot(), run_resolution_types=("5d",))
        outcome = outcomes[0]["outcome"]
        assert "hit_option_tp1" in outcome
        assert "hit_option_tp2" in outcome
        assert "hit_option_stop" in outcome

    def test_multiple_windows_all_have_v2_fields(self, monkeypatch):
        import utils.option_outcomes as oo
        monkeypatch.setattr(oo, "_fetch_history", lambda t, s, e: self._fake_hist(150.0, 152.0))

        outcomes = oo.resolve_snapshot(self._make_snapshot(), run_resolution_types=("1d", "5d", "10d"))
        assert len(outcomes) == 3
        for item in outcomes:
            outcome = item["outcome"]
            assert "hit_v2_tp1" in outcome
            assert "target_projection_method" in outcome
