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

    def test_extracts_si_persistence_from_explanation(self):
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
