"""
Unit tests for CHUNK-11: SqueezeOutcomeReplay in backtest.py

Tests cover:
  - compute_forward_returns: normal case, missing windows, empty prices
  - classify_squeeze_outcome: all label thresholds
  - _extract_from_explanation: found / missing / malformed JSON
  - SqueezeOutcomeReplay.load_snapshots: injected rows, min_score filter
  - SqueezeOutcomeReplay._build_replay_row: forward returns wired, hit flags
  - SqueezeOutcomeReplay.run: empty result when no snapshots
  - SqueezeOutcomeReplay.run: full round-trip with synthetic data
  - SqueezeOutcomeReplay.summary_metrics: hit rates, avg returns, label counts
  - SqueezeOutcomeReplay.case_study: single-ticker convenience wrapper
  - Anti-lookahead: signal fields sourced exclusively from saved snapshot
  - Point-in-time: future bars only strictly after signal_date

All tests are pure-function — no live DB or network calls.
"""

import json
from datetime import date

import pandas as pd
import pytest

from backtest import (
    SqueezeOutcomeReplay,
    _extract_from_explanation,
    classify_squeeze_outcome,
    compute_forward_returns,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _prices(values: list, start: str = "2024-01-02") -> pd.Series:
    """Create a pd.Series of daily closing prices."""
    idx = pd.date_range(start, periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype=float)


def _snap(
    ticker: str = "CAR",
    signal_date: str = "2024-01-05",
    final_score: float = 6.5,
    short_pct_float: float = 0.35,
    squeeze_state: str = "active",
    explanation_json: dict | None = None,
) -> dict:
    expl = explanation_json or {
        "top_positive_drivers": [
            {"key": "si_persistence", "label": "SI persistence", "strength": 7.0},
            {"key": "effective_float", "label": "Effective float", "strength": 8.0},
        ],
        "top_negative_drivers": [],
    }
    return {
        "date": signal_date,
        "ticker": ticker,
        "final_score": final_score,
        "short_pct_float": short_pct_float,
        "days_to_cover": 3.5,
        "computed_dtc_30d": 3.5,
        "compression_recovery_score": 4.0,
        "volume_confirmation_flag": True,
        "squeeze_state": squeeze_state,
        "explanation_summary": "Setup looks strong.",
        "explanation_json": json.dumps(expl),
        "si_persistence_score": 7.0,  # direct column since CHUNK-15
    }


# ── compute_forward_returns ───────────────────────────────────────────────────

class TestComputeForwardReturns:

    def test_normal_case(self):
        # 40 bars, entry on bar 5 (index=4), check 5d forward
        prices = _prices([10.0] * 4 + [20.0] + [30.0] * 35)
        result = compute_forward_returns(prices, "2024-01-08", windows=(5,))
        # entry = close on 2024-01-08 = 20.0; 5th bar after = 30.0
        assert result["fwd_5d"] == pytest.approx((30.0 - 20.0) / 20.0)

    def test_missing_window_returns_none(self):
        # Only 3 bars after signal_date — window 5 should be None
        prices = _prices([10.0, 10.0, 10.0, 10.0, 20.0, 22.0, 24.0])
        result = compute_forward_returns(prices, "2024-01-08", windows=(5, 10))
        assert result["fwd_5d"] is None  # only 2 future bars
        assert result["fwd_10d"] is None

    def test_empty_prices_returns_empty_dict(self):
        result = compute_forward_returns(pd.Series(dtype=float), "2024-01-08")
        assert result == {}

    def test_none_prices_returns_empty_dict(self):
        result = compute_forward_returns(None, "2024-01-08")
        assert result == {}

    def test_future_bars_strictly_after_signal_date(self):
        # 10 bars; entry on bar index 5; bar at index 5 should NOT be a future bar
        prices = _prices([100.0] * 5 + [200.0] + [300.0] * 10)
        signal_date = prices.index[5]
        result = compute_forward_returns(prices, signal_date, windows=(1,))
        # 1-day forward = first bar AFTER signal_date = 300.0
        assert result["fwd_1d"] == pytest.approx((300.0 - 200.0) / 200.0)


# ── classify_squeeze_outcome ──────────────────────────────────────────────────

class TestClassifySqueezeOutcome:

    @pytest.mark.parametrize("r,label", [
        (0.30, "major"),
        (0.50, "major"),
        (0.15, "strong"),
        (0.29, "strong"),
        (0.05, "minor"),
        (0.14, "minor"),
        (0.00, "none"),
        (0.04, "none"),
        (-0.10, "none"),
        (None, "none"),
    ])
    def test_thresholds(self, r, label):
        assert classify_squeeze_outcome(r) == label


# ── _extract_from_explanation ─────────────────────────────────────────────────

class TestExtractFromExplanation:

    def test_extracts_positive_driver(self):
        expl = json.dumps({
            "top_positive_drivers": [
                {"key": "si_persistence", "strength": 7.5},
            ],
            "top_negative_drivers": [],
        })
        assert _extract_from_explanation(expl, "si_persistence") == pytest.approx(7.5)

    def test_extracts_negative_driver(self):
        expl = json.dumps({
            "top_positive_drivers": [],
            "top_negative_drivers": [
                {"key": "completed_squeeze", "strength": -3.0},
            ],
        })
        assert _extract_from_explanation(expl, "completed_squeeze") == pytest.approx(-3.0)

    def test_missing_key_returns_none(self):
        expl = json.dumps({"top_positive_drivers": [], "top_negative_drivers": []})
        assert _extract_from_explanation(expl, "nonexistent") is None

    def test_malformed_json_returns_none(self):
        assert _extract_from_explanation("{not valid json", "si_persistence") is None

    def test_none_input_returns_none(self):
        assert _extract_from_explanation(None, "si_persistence") is None


# ── SqueezeOutcomeReplay ──────────────────────────────────────────────────────

class TestLoadSnapshots:

    def test_injects_rows_directly(self):
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        snaps = [_snap("CAR"), _snap("GME")]
        n = replay.load_snapshots(rows=snaps)
        assert n == 2
        assert len(replay._snapshots) == 2

    def test_min_score_filter(self):
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31", min_score=7.0)
        snaps = [_snap(final_score=5.0), _snap(final_score=8.0)]
        n = replay.load_snapshots(rows=snaps)
        assert n == 1
        assert replay._snapshots[0]["final_score"] == 8.0

    def test_empty_rows_returns_zero(self):
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        n = replay.load_snapshots(rows=[])
        assert n == 0


class TestBuildReplayRow:

    def test_forward_returns_and_hit_flags(self):
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        prices = _prices([100.0] + [110.0] * 35)  # 10% gain after entry
        snap = _snap(signal_date="2024-01-02")
        row = replay._build_replay_row(snap, prices)

        assert row["fwd_5d"] == pytest.approx(0.1)
        assert row["hit_5d"] is True
        assert row["outcome_label"] == "minor"  # 10% < 15% = minor

    def test_no_prices_gives_none_forward_returns(self):
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        snap = _snap()
        row = replay._build_replay_row(snap, None)
        for w in (5, 10, 20, 30):
            assert row[f"fwd_{w}d"] is None
        assert row["outcome_label"] == "none"

    def test_signal_fields_from_snapshot_only(self):
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        snap = _snap(final_score=9.9, short_pct_float=0.75)
        row = replay._build_replay_row(snap, None)
        assert row["final_score"] == 9.9
        assert row["short_pct_float"] == 0.75

    def test_si_persistence_and_effective_float_in_replay_row(self):
        # si_persistence_score: direct column since CHUNK-15
        # effective_float_score: still extracted from explanation_json
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        snap = _snap()
        row = replay._build_replay_row(snap, None)
        assert row["si_persistence_score"] == pytest.approx(7.0)
        assert row["effective_float_score"] == pytest.approx(8.0)


class TestRunAndSummary:

    def test_run_returns_empty_df_when_no_snapshots(self):
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[])
        df = replay.run(prices={})
        assert df.empty

    def test_run_full_round_trip(self):
        # entry on 2024-01-02 (50.0), future bars all 70.0 → 40% gain
        prices = _prices([100.0, 50.0] + [70.0] * 35, start="2024-01-01")
        snap = _snap("CAR", signal_date="2024-01-02", final_score=7.0)
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31", tickers=["CAR"])
        replay.load_snapshots(rows=[snap])
        df = replay.run(prices={"CAR": prices})

        assert len(df) == 1
        assert df.iloc[0]["ticker"] == "CAR"
        assert df.iloc[0]["outcome_label"] == "major"  # 40% gain >= 30%

    def test_summary_metrics_hit_rate(self):
        # entry on 2024-01-02 (100.0), future bars 120.0 → 20% gain
        prices = _prices([50.0, 100.0] + [120.0] * 35, start="2024-01-01")
        snaps = [_snap("CAR", signal_date="2024-01-02")]
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=snaps)
        replay.run(prices={"CAR": prices})
        metrics = replay.summary_metrics()

        assert metrics["total_signals"] == 1
        assert metrics["hit_rate_5d"] == pytest.approx(1.0)
        assert metrics["avg_fwd_5d"] == pytest.approx(0.2)

    def test_summary_metrics_empty_after_no_run(self):
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        metrics = replay.summary_metrics()
        assert metrics["total_signals"] == 0


class TestCaseStudy:

    def test_case_study_returns_dataframe(self):
        # entry on 2024-01-02 (100.0), future bars 115.0 → 15% gain = strong
        prices = _prices([50.0, 100.0] + [115.0] * 35, start="2024-01-01")
        snap = _snap("CAR", signal_date="2024-01-02")
        df = SqueezeOutcomeReplay.case_study(
            ticker="CAR",
            start_date="2024-01-01",
            end_date="2024-12-31",
            rows=[snap],
            prices={"CAR": prices},
        )
        assert len(df) == 1
        assert df.iloc[0]["outcome_label"] == "strong"  # 15% gain = strong


# ── CHUNK-10: replay handles missing lifecycle state for old rows ─────────────

class TestReplayHandlesMissingLifecycleState:

    def test_old_row_without_squeeze_state_does_not_crash(self):
        """Replay must not crash when old snapshot rows lack squeeze_state."""
        old_row = {
            "date": "2024-01-02",
            "ticker": "OLD",
            "final_score": 5.5,
            "short_pct_float": 0.30,
            "days_to_cover": 3.5,
            "computed_dtc_30d": 3.5,
            "compression_recovery_score": 4.0,
            "volume_confirmation_flag": True,
            # squeeze_state intentionally absent
            "explanation_summary": "Old row.",
            "explanation_json": None,
        }
        prices = _prices([100.0] + [110.0] * 35, start="2024-01-01")
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[old_row])
        df = replay.run(prices={"OLD": prices})

        assert len(df) == 1
        assert df.iloc[0]["squeeze_state"] is None or df.iloc[0]["squeeze_state"] == old_row.get("squeeze_state")

    def test_replay_reads_new_squeeze_state_when_present(self):
        """Replay must propagate squeeze_state from snapshot when present."""
        snap = _snap("CAR", signal_date="2024-01-02")
        snap["squeeze_state"] = "ARMED"
        prices = _prices([100.0] + [110.0] * 35, start="2024-01-01")
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[snap])
        df = replay.run(prices={"CAR": prices})

        assert df.iloc[0]["squeeze_state"] == "ARMED"


# ── CHUNK-16: replay handles risk fields ──────────────────────────────────────

class TestReplayRiskFieldHandling:

    def test_replay_handles_missing_risk_fields_for_old_rows(self):
        """Old snapshot rows without risk fields must not crash replay."""
        old_row = {
            "date": "2024-01-02",
            "ticker": "OLD",
            "final_score": 5.5,
            "short_pct_float": 0.30,
            "days_to_cover": 3.5,
            "computed_dtc_30d": 3.5,
            "compression_recovery_score": 4.0,
            "volume_confirmation_flag": True,
            "squeeze_state": "ARMED",
            "explanation_summary": "Old row.",
            "explanation_json": None,
            # risk_score, risk_level, dilution_risk_flag intentionally absent
        }
        prices = _prices([100.0] + [110.0] * 35, start="2024-01-01")
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[old_row])
        df = replay.run(prices={"OLD": prices})

        assert len(df) == 1
        # Missing risk fields should be None, not crash
        assert df.iloc[0]["risk_score"] is None
        assert df.iloc[0]["risk_level"] is None
        assert df.iloc[0]["dilution_risk_flag"] is None

    def test_replay_includes_risk_fields_when_present(self):
        """New snapshot rows with risk fields must have them propagated in replay output."""
        snap = _snap("CAR", signal_date="2024-01-02")
        snap["risk_score"] = 45.0
        snap["risk_level"] = "MEDIUM"
        snap["dilution_risk_flag"] = True
        prices = _prices([100.0] + [110.0] * 35, start="2024-01-01")
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[snap])
        df = replay.run(prices={"CAR": prices})

        assert df.iloc[0]["risk_score"] == pytest.approx(45.0)
        assert df.iloc[0]["risk_level"] == "MEDIUM"
        assert df.iloc[0]["dilution_risk_flag"] == True  # noqa: E712 — np.True_ vs True


# ── TRD-014: compute_taxonomy_label ──────────────────────────────────────────

class TestComputeTaxonomyLabel:
    """Tests for compute_taxonomy_label() — TRD-014."""

    def test_imports(self):
        from backtest import compute_taxonomy_label
        assert callable(compute_taxonomy_label)

    def test_false_positive_when_no_move(self):
        from backtest import compute_taxonomy_label
        lbl = compute_taxonomy_label("ARMED", None, None, None, None, None)
        assert lbl == "FALSE_POSITIVE"

    def test_false_positive_when_max_return_below_5pct(self):
        from backtest import compute_taxonomy_label
        lbl = compute_taxonomy_label("ARMED", 0.03, 0.04, 0.04, False, False)
        assert lbl == "FALSE_POSITIVE"

    def test_false_positive_when_negative_return(self):
        from backtest import compute_taxonomy_label
        lbl = compute_taxonomy_label("ARMED", -0.10, -0.12, -0.08, False, False)
        assert lbl == "FALSE_POSITIVE"

    def test_early_enough_for_early_armed_with_15pct_hit(self):
        from backtest import compute_taxonomy_label
        lbl = compute_taxonomy_label(
            "EARLY_ARMED", 0.18, 0.22, 0.18,
            hit_15pct_10d=True, hit_25pct_20d=False,
        )
        assert lbl == "EARLY_ENOUGH"

    def test_early_enough_for_armed_with_25pct_20d_hit(self):
        from backtest import compute_taxonomy_label
        lbl = compute_taxonomy_label(
            "ARMED", 0.12, 0.26, 0.26,
            hit_15pct_10d=False, hit_25pct_20d=True,
        )
        assert lbl == "EARLY_ENOUGH"

    def test_late_chase_for_entry_state_no_quick_hit(self):
        """ARMED state with 20% return but slowly (no 15%/10d or 25%/20d hit)."""
        from backtest import compute_taxonomy_label
        lbl = compute_taxonomy_label(
            "ARMED", 0.07, 0.12, 0.20,
            hit_15pct_10d=False, hit_25pct_20d=False,
        )
        assert lbl == "LATE_CHASE"

    def test_late_chase_for_active_state_with_move(self):
        """ACTIVE state always gets LATE_CHASE (not EARLY_ENOUGH) when there's a move."""
        from backtest import compute_taxonomy_label
        lbl = compute_taxonomy_label(
            "ACTIVE", 0.20, 0.30, 0.30,
            hit_15pct_10d=True, hit_25pct_20d=True,
        )
        assert lbl == "LATE_CHASE"

    def test_false_positive_for_active_no_continuation(self):
        """ACTIVE state with no continuation move → FALSE_POSITIVE."""
        from backtest import compute_taxonomy_label
        lbl = compute_taxonomy_label(
            "ACTIVE", 0.02, 0.03, 0.02,
            hit_15pct_10d=False, hit_25pct_20d=False,
        )
        assert lbl == "FALSE_POSITIVE"

    def test_unknown_state_with_move_returns_late_chase(self):
        """Unknown state with a move → LATE_CHASE (conservative default)."""
        from backtest import compute_taxonomy_label
        lbl = compute_taxonomy_label(
            "UNKNOWN_STATE", 0.25, 0.30, 0.30,
            hit_15pct_10d=True, hit_25pct_20d=True,
        )
        assert lbl == "LATE_CHASE"


class TestReplayIncludesTaxonomyFields:
    """Verify _build_replay_row includes hit flags and taxonomy label (TRD-014)."""

    def _snap(self, squeeze_state="ARMED"):
        return {
            "date": "2024-01-02",
            "ticker": "TSTZ",
            "final_score": 65.0,
            "short_pct_float": 0.35,
            "squeeze_state": squeeze_state,
            "days_to_cover": 8.0,
            "computed_dtc_30d": 8.0,
            "compression_recovery_score": 7.0,
            "volume_confirmation_flag": False,
            "explanation_json": None,
            "si_persistence_score": 7.0,
        }

    def _prices(self, values, start="2024-01-01"):
        idx = pd.date_range(start, periods=len(values), freq="B")
        return pd.Series(values, index=idx, dtype=float)

    def test_hit_15pct_10d_flag_true_when_return_exceeds_15pct(self):
        from backtest import SqueezeOutcomeReplay
        # Entry at 100, future bars at 118 → 18% gain → hit_15pct_10d = True
        prices = self._prices([90.0, 100.0] + [118.0] * 35, "2024-01-01")
        snap = self._snap("ARMED")
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[snap])
        df = replay.run(prices={"TSTZ": prices})
        row = df.iloc[0]
        assert bool(row["hit_15pct_10d"]) is True

    def test_hit_15pct_10d_flag_false_when_return_below_15pct(self):
        from backtest import SqueezeOutcomeReplay
        # Entry at 100, future bars at 108 → 8% gain → hit_15pct_10d = False
        prices = self._prices([90.0, 100.0] + [108.0] * 35, "2024-01-01")
        snap = self._snap("ARMED")
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[snap])
        df = replay.run(prices={"TSTZ": prices})
        row = df.iloc[0]
        assert bool(row["hit_15pct_10d"]) is False

    def test_taxonomy_label_early_enough_for_armed_with_large_move(self):
        from backtest import SqueezeOutcomeReplay
        # Entry at 100, future at 120 → 20% in 10d → EARLY_ENOUGH (ARMED state)
        prices = self._prices([90.0, 100.0] + [120.0] * 35, "2024-01-01")
        snap = self._snap("ARMED")
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[snap])
        df = replay.run(prices={"TSTZ": prices})
        assert df.iloc[0]["taxonomy_label"] == "EARLY_ENOUGH"

    def test_taxonomy_label_late_chase_for_active_state(self):
        from backtest import SqueezeOutcomeReplay
        # Even with a 20% gain, ACTIVE state → LATE_CHASE
        prices = self._prices([90.0, 100.0] + [120.0] * 35, "2024-01-01")
        snap = self._snap("ACTIVE")
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[snap])
        df = replay.run(prices={"TSTZ": prices})
        assert df.iloc[0]["taxonomy_label"] == "LATE_CHASE"

    def test_taxonomy_label_false_positive_when_no_move(self):
        from backtest import SqueezeOutcomeReplay
        # Flat price → no move → FALSE_POSITIVE
        prices = self._prices([90.0, 100.0] + [101.0] * 35, "2024-01-01")
        snap = self._snap("ARMED")
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[snap])
        df = replay.run(prices={"TSTZ": prices})
        assert df.iloc[0]["taxonomy_label"] == "FALSE_POSITIVE"

    def test_summary_metrics_includes_taxonomy_counts(self):
        from backtest import SqueezeOutcomeReplay
        prices = self._prices([90.0, 100.0] + [120.0] * 35, "2024-01-01")
        snap = self._snap("ARMED")
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[snap])
        replay.run(prices={"TSTZ": prices})
        metrics = replay.summary_metrics()
        assert "taxonomy_label_counts" in metrics
        assert "EARLY_ENOUGH" in metrics["taxonomy_label_counts"]


# ── Backfill feature completeness (Option A fix) ─────────────────────────────

class TestBackfillSnapshotCoPersistence:
    """
    Verify that _persist_training_outcomes() also materialises training snapshot
    rows using save_squeeze_training_snapshot_backfill(), so the calibration join
    (outcomes LEFT JOIN snapshots) returns non-NULL feature columns.
    """

    def _prices(self, values, start="2024-01-01"):
        idx = pd.date_range(start, periods=len(values), freq="B")
        return pd.Series(values, index=idx, dtype=float)

    def _snap(self, state="ARMED"):
        return {
            "date": "2024-01-02",
            "ticker": "TSTZ",
            "final_score": 65.0,
            "short_pct_float": 0.35,
            "computed_dtc_30d": 8.0,
            "compression_recovery_score": 7.0,
            "si_persistence_score": 7.0,
            "squeeze_state": state,
            "volume_confirmation_flag": False,
            "risk_score": 20.0,
            "risk_level": "LOW",
            "dilution_risk_flag": False,
            "options_pressure_score": 3.0,
            "iv_rank": 40.0,
            "unusual_call_activity_flag": False,
            "explanation_json": None,
        }

    def test_snapshot_backfill_called_with_correct_feature_fields(self):
        """
        When persist_outcomes=True and a 30d window is closed, the replay must
        call save_squeeze_training_snapshot_backfill() with the feature data
        sourced from the replay row, before calling save_squeeze_training_outcome().
        """
        from unittest.mock import patch, call
        from backtest import SqueezeOutcomeReplay

        prices = {"TSTZ": self._prices([90.0, 100.0] + [120.0] * 40, "2024-01-01")}
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[self._snap("ARMED")])

        snap_records = []
        outcome_records = []

        def _cap_snap(rec):
            snap_records.append(rec)

        def _cap_outcome(rec):
            outcome_records.append(rec)

        with patch("utils.supabase_persist.save_squeeze_training_snapshot_backfill",
                   side_effect=_cap_snap), \
             patch("utils.supabase_persist.save_squeeze_training_outcome",
                   side_effect=_cap_outcome):
            replay.run(prices=prices, persist_outcomes=True)

        assert len(snap_records) == 1, "Snapshot backfill should be called once"
        assert len(outcome_records) == 1, "Outcome record should be written once"

        snap = snap_records[0]
        # Join key must match between snapshot and outcome
        assert snap["signal_date"] == outcome_records[0]["signal_date"]
        assert snap["ticker"] == outcome_records[0]["ticker"]
        assert snap["alert_type"] == outcome_records[0]["alert_type"]

        # Feature columns from replay row must be present in the snapshot record
        assert snap["final_score"] == 65.0
        assert snap["short_pct_float"] == 0.35
        assert snap["computed_dtc_30d"] == 8.0
        assert snap["compression_recovery_score"] == 7.0
        assert snap["si_persistence_score"] == 7.0
        assert snap["risk_level"] == "LOW"

    def test_snapshot_backfill_called_before_outcome(self):
        """
        Snapshot must be materialised before the outcome (so the join is ready
        if calibration reads immediately after backfill).
        """
        from unittest.mock import patch
        from backtest import SqueezeOutcomeReplay

        call_order = []

        def _snap_call(rec):
            call_order.append("snapshot")

        def _outcome_call(rec):
            call_order.append("outcome")

        prices = {"TSTZ": self._prices([90.0, 100.0] + [120.0] * 40, "2024-01-01")}
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[self._snap("ARMED")])

        with patch("utils.supabase_persist.save_squeeze_training_snapshot_backfill",
                   side_effect=_snap_call), \
             patch("utils.supabase_persist.save_squeeze_training_outcome",
                   side_effect=_outcome_call):
            replay.run(prices=prices, persist_outcomes=True)

        assert call_order == ["snapshot", "outcome"], (
            f"Expected snapshot before outcome, got: {call_order}"
        )

    def test_snapshot_backfill_not_called_when_persist_false(self):
        """When persist_outcomes=False, neither snapshot nor outcome should be written."""
        from unittest.mock import patch
        from backtest import SqueezeOutcomeReplay

        prices = {"TSTZ": self._prices([90.0, 100.0] + [120.0] * 40, "2024-01-01")}
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[self._snap("ARMED")])

        with patch("utils.supabase_persist.save_squeeze_training_snapshot_backfill") as mock_snap, \
             patch("utils.supabase_persist.save_squeeze_training_outcome") as mock_outcome:
            replay.run(prices=prices, persist_outcomes=False)

        assert not mock_snap.called
        assert not mock_outcome.called

    def test_alert_type_equals_squeeze_state_in_both_records(self):
        """
        alert_type in both snapshot and outcome must equal the squeeze_state
        from the source snap — join key consistency.
        """
        from unittest.mock import patch
        from backtest import SqueezeOutcomeReplay

        snap_records = []
        outcome_records = []

        prices = {"TSTZ": self._prices([90.0, 100.0] + [120.0] * 40, "2024-01-01")}
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[self._snap("EARLY_ARMED")])

        with patch("utils.supabase_persist.save_squeeze_training_snapshot_backfill",
                   side_effect=lambda r: snap_records.append(r)), \
             patch("utils.supabase_persist.save_squeeze_training_outcome",
                   side_effect=lambda r: outcome_records.append(r)):
            replay.run(prices=prices, persist_outcomes=True)

        assert snap_records[0]["alert_type"] == "EARLY_ARMED"
        assert outcome_records[0]["alert_type"] == "EARLY_ARMED"
        assert snap_records[0]["alert_type"] == outcome_records[0]["alert_type"]

    def test_no_snapshot_backfill_when_30d_window_not_closed(self):
        """When fwd_30d is None (window not closed), neither call should happen."""
        from unittest.mock import patch
        from backtest import SqueezeOutcomeReplay

        # Only 10 future bars — 30d window not closed
        prices = {"TSTZ": self._prices([90.0, 100.0] + [110.0] * 10, "2024-01-01")}
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[self._snap("ARMED")])

        with patch("utils.supabase_persist.save_squeeze_training_snapshot_backfill") as mock_snap, \
             patch("utils.supabase_persist.save_squeeze_training_outcome") as mock_outcome:
            replay.run(prices=prices, persist_outcomes=True)

        assert not mock_snap.called
        assert not mock_outcome.called

    def test_effective_float_score_from_explanation_json_included_in_snapshot(self):
        """
        effective_float_score is extracted from explanation_json in _build_replay_row.
        It must flow into the snapshot backfill record.
        """
        import json as _json
        from unittest.mock import patch
        from backtest import SqueezeOutcomeReplay

        expl = _json.dumps({
            "top_positive_drivers": [
                {"key": "effective_float", "label": "Effective float", "strength": 7.5},
            ],
            "top_negative_drivers": [],
        })
        snap_with_expl = {**self._snap("ARMED"), "explanation_json": expl}

        prices = {"TSTZ": self._prices([90.0, 100.0] + [120.0] * 40, "2024-01-01")}
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[snap_with_expl])

        captured = []
        with patch("utils.supabase_persist.save_squeeze_training_snapshot_backfill",
                   side_effect=lambda r: captured.append(r)), \
             patch("utils.supabase_persist.save_squeeze_training_outcome"):
            replay.run(prices=prices, persist_outcomes=True)

        assert len(captured) == 1
        # effective_float_score extracted from explanation_json
        import pytest
        assert captured[0]["effective_float_score"] == pytest.approx(7.5)


# ── Missing/None alert_type skip behavior (data-quality fix) ─────────────────

class TestMissingStateSkipBehavior:
    """
    Verify that rows with missing/None/null squeeze_state are skipped during
    _persist_training_outcomes() and do not produce "None" string alert_type
    values in the training tables.
    """

    def _prices(self, values, start="2024-01-01"):
        idx = pd.date_range(start, periods=len(values), freq="B")
        return pd.Series(values, index=idx, dtype=float)

    def _snap(self, state, ticker="TSTZ"):
        return {
            "date": "2024-01-02",
            "ticker": ticker,
            "final_score": 62.0,
            "short_pct_float": 0.30,
            "computed_dtc_30d": 8.0,
            "compression_recovery_score": 7.0,
            "si_persistence_score": 7.0,
            "squeeze_state": state,
            "volume_confirmation_flag": False,
            "risk_score": 15.0,
            "risk_level": "LOW",
            "dilution_risk_flag": False,
            "options_pressure_score": 2.0,
            "iv_rank": 30.0,
            "unusual_call_activity_flag": False,
            "explanation_json": None,
        }

    def test_none_state_row_skipped_on_backfill(self):
        """A row with squeeze_state=None must not call either persistence helper."""
        from unittest.mock import patch
        from backtest import SqueezeOutcomeReplay

        prices = {"TSTZ": self._prices([90.0, 100.0] + [120.0] * 40, "2024-01-01")}
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[self._snap(None)])  # no state

        with patch("utils.supabase_persist.save_squeeze_training_snapshot_backfill") as ms, \
             patch("utils.supabase_persist.save_squeeze_training_outcome") as mo:
            replay.run(prices=prices, persist_outcomes=True)

        assert not ms.called, "Snapshot must not be written for None state"
        assert not mo.called, "Outcome must not be written for None state"

    def test_not_setup_state_row_skipped_on_backfill(self):
        """A row with squeeze_state='NOT_SETUP' must not be persisted."""
        from unittest.mock import patch
        from backtest import SqueezeOutcomeReplay

        prices = {"TSTZ": self._prices([90.0, 100.0] + [120.0] * 40, "2024-01-01")}
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[self._snap("NOT_SETUP")])

        with patch("utils.supabase_persist.save_squeeze_training_snapshot_backfill") as ms, \
             patch("utils.supabase_persist.save_squeeze_training_outcome") as mo:
            replay.run(prices=prices, persist_outcomes=True)

        assert not ms.called
        assert not mo.called

    def test_valid_state_still_persisted_alongside_invalid(self):
        """Valid rows must still be persisted even when mixed with None-state rows."""
        from unittest.mock import patch
        from backtest import SqueezeOutcomeReplay

        prices = {
            "TSTZ": self._prices([90.0, 100.0] + [120.0] * 40, "2024-01-01"),
            "OLD": self._prices([50.0, 60.0] + [65.0] * 40, "2024-01-01"),
        }
        snaps = [
            self._snap("ARMED", ticker="TSTZ"),
            {**self._snap(None, ticker="OLD")},  # no state
        ]
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=snaps)

        snap_records = []
        outcome_records = []

        with patch("utils.supabase_persist.save_squeeze_training_snapshot_backfill",
                   side_effect=lambda r: snap_records.append(r)), \
             patch("utils.supabase_persist.save_squeeze_training_outcome",
                   side_effect=lambda r: outcome_records.append(r)):
            replay.run(prices=prices, persist_outcomes=True)

        # Only the ARMED row should have been persisted
        assert len(snap_records) == 1
        assert len(outcome_records) == 1
        assert snap_records[0]["ticker"] == "TSTZ"
        assert snap_records[0]["alert_type"] == "ARMED"

    def test_no_none_string_alert_type_in_persisted_records(self):
        """The persisted alert_type must never be the string 'None'."""
        from unittest.mock import patch
        from backtest import SqueezeOutcomeReplay

        # Simulate a pre-CHUNK-10 replay row where squeeze_state=None
        prices = {"TSTZ": self._prices([90.0, 100.0] + [120.0] * 40, "2024-01-01")}
        replay = SqueezeOutcomeReplay("2024-01-01", "2024-12-31")
        replay.load_snapshots(rows=[self._snap("ARMED")])  # valid row

        persisted_types = []

        def _capture_snap(rec):
            persisted_types.append(rec.get("alert_type"))

        def _capture_outcome(rec):
            persisted_types.append(rec.get("alert_type"))

        with patch("utils.supabase_persist.save_squeeze_training_snapshot_backfill",
                   side_effect=_capture_snap), \
             patch("utils.supabase_persist.save_squeeze_training_outcome",
                   side_effect=_capture_outcome):
            replay.run(prices=prices, persist_outcomes=True)

        for at in persisted_types:
            assert at != "None", f'alert_type must not be string "None", got {at!r}'
            assert at is not None, "alert_type must not be Python None in persisted record"


class TestCalibrationNoBogusNoneBucket:
    """
    Verify that compute_metrics() in squeeze_calibration.py does not create
    a bogus 'NONE' or 'UNKNOWN' bucket from rows with missing alert_type.
    """

    def test_none_alert_type_rows_excluded_from_metrics(self):
        """Rows with alert_type=None must not appear in compute_metrics output."""
        from scripts.squeeze_calibration import compute_metrics

        rows = [
            {"alert_type": "ARMED", "taxonomy_label": "EARLY_ENOUGH",
             "hit_15pct_10d": True, "hit_25pct_20d": True,
             "fwd_10d": 0.20, "fwd_20d": 0.25, "max_fwd_return": 0.25},
            {"alert_type": None, "taxonomy_label": "FALSE_POSITIVE",
             "hit_15pct_10d": False, "hit_25pct_20d": False,
             "fwd_10d": 0.02, "fwd_20d": 0.02, "max_fwd_return": 0.02},
        ]
        metrics = compute_metrics(rows)
        assert "ARMED" in metrics
        assert "NONE" not in metrics
        assert None not in metrics

    def test_string_none_alert_type_excluded_from_metrics(self):
        """Rows with alert_type='None' (string) must not produce a 'NONE' bucket."""
        from scripts.squeeze_calibration import compute_metrics

        rows = [
            {"alert_type": "ARMED", "taxonomy_label": "LATE_CHASE",
             "hit_15pct_10d": False, "hit_25pct_20d": True,
             "fwd_10d": 0.10, "fwd_20d": 0.26, "max_fwd_return": 0.26},
            {"alert_type": "None", "taxonomy_label": "FALSE_POSITIVE",
             "hit_15pct_10d": False, "hit_25pct_20d": False,
             "fwd_10d": 0.01, "fwd_20d": 0.01, "max_fwd_return": 0.01},
        ]
        metrics = compute_metrics(rows)
        assert "ARMED" in metrics
        assert "NONE" not in metrics
        assert "None" not in metrics

    def test_not_setup_rows_excluded_from_metrics(self):
        """NOT_SETUP alert_type must not appear in calibration metrics."""
        from scripts.squeeze_calibration import compute_metrics

        rows = [
            {"alert_type": "EARLY_ARMED", "taxonomy_label": "EARLY_ENOUGH",
             "hit_15pct_10d": True, "hit_25pct_20d": True,
             "fwd_10d": 0.18, "fwd_20d": 0.22, "max_fwd_return": 0.22},
            {"alert_type": "NOT_SETUP", "taxonomy_label": "FALSE_POSITIVE",
             "hit_15pct_10d": False, "hit_25pct_20d": False,
             "fwd_10d": 0.0, "fwd_20d": 0.0, "max_fwd_return": 0.0},
        ]
        metrics = compute_metrics(rows)
        assert "EARLY_ARMED" in metrics
        assert "NOT_SETUP" not in metrics

    def test_unknown_bucket_excluded_from_metrics(self):
        """'UNKNOWN' alert_type must not appear in calibration metrics output."""
        from scripts.squeeze_calibration import compute_metrics

        rows = [
            {"alert_type": "UNKNOWN", "taxonomy_label": "FALSE_POSITIVE",
             "hit_15pct_10d": False, "hit_25pct_20d": False,
             "fwd_10d": 0.0, "fwd_20d": 0.0, "max_fwd_return": 0.0},
        ]
        metrics = compute_metrics(rows)
        assert "UNKNOWN" not in metrics
        assert len(metrics) == 0  # all rows were excluded

    def test_valid_states_still_present_after_filtering(self):
        """After filtering, valid labeled states must remain in metrics."""
        from scripts.squeeze_calibration import compute_metrics

        rows = [
            {"alert_type": "EARLY_ARMED", "taxonomy_label": "EARLY_ENOUGH",
             "hit_15pct_10d": True, "hit_25pct_20d": True,
             "fwd_10d": 0.20, "fwd_20d": 0.28, "max_fwd_return": 0.28},
            {"alert_type": "ARMED", "taxonomy_label": "LATE_CHASE",
             "hit_15pct_10d": False, "hit_25pct_20d": False,
             "fwd_10d": 0.08, "fwd_20d": 0.10, "max_fwd_return": 0.10},
            {"alert_type": "ACTIVE", "taxonomy_label": "LATE_CHASE",
             "hit_15pct_10d": False, "hit_25pct_20d": False,
             "fwd_10d": 0.05, "fwd_20d": 0.07, "max_fwd_return": 0.07},
            {"alert_type": None, "taxonomy_label": "FALSE_POSITIVE",
             "hit_15pct_10d": False, "hit_25pct_20d": False,
             "fwd_10d": 0.0, "fwd_20d": 0.0, "max_fwd_return": 0.0},
        ]
        metrics = compute_metrics(rows)
        assert set(metrics.keys()) == {"EARLY_ARMED", "ARMED", "ACTIVE"}
        assert metrics["EARLY_ARMED"]["n"] == 1
        assert metrics["ARMED"]["n"] == 1
        assert metrics["ACTIVE"]["n"] == 1
