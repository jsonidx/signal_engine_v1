"""
Unit tests for TRD-019: Analyst Price-Target Change Detection.

Tests _classify_analyst_row and score_analyst_momentum in isolation
using synthetic DataFrames — no network calls.
"""
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pandas as pd

from catalyst_screener import _classify_analyst_row, _is_numeric, score_analyst_momentum


def _make_row(
    action="main",
    to_grade="",
    from_grade="",
    pt_action="",
    current_pt=None,
    prior_pt=None,
) -> dict:
    return {
        "Action": action,
        "ToGrade": to_grade,
        "FromGrade": from_grade,
        "priceTargetAction": pt_action,
        "currentPriceTarget": current_pt,
        "priorPriceTarget": prior_pt,
    }


def _make_df(rows: list[dict], days_ago: list[int]) -> pd.DataFrame:
    """Build a mock upgrades_downgrades DataFrame with UTC timestamps."""
    now = datetime.now(tz=timezone.utc)
    index = [now - timedelta(days=d) for d in days_ago]
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(index))
    df.index.name = "GradeDate"
    return df


def _make_data(df: pd.DataFrame) -> dict:
    stock_obj = MagicMock()
    stock_obj.upgrades_downgrades = df
    return {"stock_obj": stock_obj}


class TestIsNumeric(unittest.TestCase):
    def test_float_string(self):
        self.assertTrue(_is_numeric("150.0"))

    def test_none(self):
        self.assertFalse(_is_numeric(None))

    def test_empty_string(self):
        self.assertFalse(_is_numeric(""))

    def test_nan(self):
        import math
        self.assertTrue(_is_numeric(float("nan")))


class TestClassifyAnalystRow(unittest.TestCase):
    # ── rating upgrades ───────────────────────────────────────────────────────

    def test_action_upgrade(self):
        self.assertEqual(_classify_analyst_row(_make_row(action="upgrade")), "upgrade")

    def test_action_init(self):
        self.assertEqual(_classify_analyst_row(_make_row(action="init")), "upgrade")

    def test_to_grade_buy(self):
        self.assertEqual(_classify_analyst_row(_make_row(action="main", to_grade="Buy")), "upgrade")

    def test_to_grade_outperform(self):
        self.assertEqual(
            _classify_analyst_row(_make_row(action="main", to_grade="Outperform")), "upgrade"
        )

    # ── rating downgrades ─────────────────────────────────────────────────────

    def test_action_downgrade(self):
        self.assertEqual(_classify_analyst_row(_make_row(action="downgrade")), "downgrade")

    def test_to_grade_sell(self):
        self.assertEqual(
            _classify_analyst_row(_make_row(action="main", to_grade="Sell")), "downgrade"
        )

    def test_to_grade_underperform(self):
        self.assertEqual(
            _classify_analyst_row(_make_row(action="main", to_grade="Underperform")), "downgrade"
        )

    # ── pure target raises ────────────────────────────────────────────────────

    def test_pt_action_raises(self):
        row = _make_row(action="main", pt_action="Raises", current_pt=200.0, prior_pt=180.0)
        self.assertEqual(_classify_analyst_row(row), "target_raise")

    def test_numeric_raise_no_pt_action(self):
        """Numeric comparison fallback when priceTargetAction is absent."""
        row = _make_row(action="main", pt_action="", current_pt=200.0, prior_pt=180.0)
        self.assertEqual(_classify_analyst_row(row), "target_raise")

    def test_pt_action_raises_case_insensitive(self):
        row = _make_row(action="main", pt_action="raises", current_pt=150.0, prior_pt=130.0)
        self.assertEqual(_classify_analyst_row(row), "target_raise")

    # ── pure target cuts ──────────────────────────────────────────────────────

    def test_pt_action_lowers(self):
        row = _make_row(action="main", pt_action="Lowers", current_pt=100.0, prior_pt=120.0)
        self.assertEqual(_classify_analyst_row(row), "target_cut")

    def test_numeric_cut_no_pt_action(self):
        row = _make_row(action="main", pt_action="", current_pt=90.0, prior_pt=110.0)
        self.assertEqual(_classify_analyst_row(row), "target_cut")

    # ── upgrade wins over concurrent target raise (no double-count) ───────────

    def test_upgrade_wins_over_target_raise(self):
        """An upgrade row that also has a PT raise is classified as upgrade only."""
        row = _make_row(
            action="upgrade", pt_action="Raises", current_pt=200.0, prior_pt=160.0
        )
        self.assertEqual(_classify_analyst_row(row), "upgrade")

    def test_reiterated_with_target_raise_is_upgrade(self):
        """reiterated (counted as upgrade) + PT raise → upgrade, not target_raise."""
        row = _make_row(
            action="reiterated", pt_action="Raises", current_pt=180.0, prior_pt=160.0
        )
        self.assertEqual(_classify_analyst_row(row), "upgrade")

    def test_downgrade_wins_over_target_cut(self):
        row = _make_row(
            action="downgrade", pt_action="Lowers", current_pt=80.0, prior_pt=100.0
        )
        self.assertEqual(_classify_analyst_row(row), "downgrade")

    # ── neutral ───────────────────────────────────────────────────────────────

    def test_maintain_no_pt_change(self):
        row = _make_row(action="main", pt_action="Maintains", current_pt=150.0, prior_pt=150.0)
        self.assertEqual(_classify_analyst_row(row), "neutral")

    def test_empty_row(self):
        self.assertEqual(_classify_analyst_row({}), "neutral")

    def test_missing_pt_fields(self):
        row = _make_row(action="main", pt_action="")
        self.assertEqual(_classify_analyst_row(row), "neutral")


class TestScoreAnalystMomentum(unittest.TestCase):
    """Integration tests for score_analyst_momentum (TRD-019 acceptance criteria)."""

    # ── target raise detected ─────────────────────────────────────────────────

    def test_single_target_raise_contributes_positive_score(self):
        """A pure PT raise (maintain + Raises) must produce score > 0."""
        df = _make_df(
            [_make_row(action="main", pt_action="Raises", current_pt=200.0, prior_pt=170.0)],
            days_ago=[2],
        )
        result = score_analyst_momentum(_make_data(df))
        self.assertGreater(result["score"], 0)
        self.assertTrue(result["target_raise_flag"])
        self.assertEqual(result["target_raises_7d"], 1)
        self.assertEqual(result["upgrades_7d"], 0)

    def test_two_target_raises_score_higher(self):
        df = _make_df(
            [
                _make_row(action="main", pt_action="Raises", current_pt=200.0, prior_pt=170.0),
                _make_row(action="main", pt_action="Raises", current_pt=195.0, prior_pt=180.0),
            ],
            days_ago=[1, 3],
        )
        result = score_analyst_momentum(_make_data(df))
        self.assertEqual(result["target_raises_7d"], 2)
        self.assertGreaterEqual(result["score"], 2)

    def test_target_raise_flag_set(self):
        df = _make_df(
            [_make_row(action="main", pt_action="Raises", current_pt=150.0, prior_pt=130.0)],
            days_ago=[1],
        )
        result = score_analyst_momentum(_make_data(df))
        self.assertTrue(result["target_raise_flag"])
        self.assertFalse(result["target_cut_flag"])

    # ── target cut detected ───────────────────────────────────────────────────

    def test_target_cut_applies_penalty(self):
        """A pure PT cut (maintain + Lowers) must reduce the score and set the flag."""
        df = _make_df(
            [
                _make_row(action="main", pt_action="Raises", current_pt=200.0, prior_pt=170.0),
                _make_row(action="main", pt_action="Lowers", current_pt=80.0, prior_pt=110.0),
            ],
            days_ago=[1, 2],
        )
        result_with_cut = score_analyst_momentum(_make_data(df))

        df_no_cut = _make_df(
            [_make_row(action="main", pt_action="Raises", current_pt=200.0, prior_pt=170.0)],
            days_ago=[1],
        )
        result_without_cut = score_analyst_momentum(_make_data(df_no_cut))

        self.assertTrue(result_with_cut["target_cut_flag"])
        self.assertEqual(result_with_cut["target_cuts_7d"], 1)
        self.assertLessEqual(result_with_cut["score"], result_without_cut["score"])

    def test_target_cut_flag_set(self):
        df = _make_df(
            [_make_row(action="main", pt_action="Lowers", current_pt=90.0, prior_pt=120.0)],
            days_ago=[3],
        )
        result = score_analyst_momentum(_make_data(df))
        self.assertTrue(result["target_cut_flag"])
        self.assertFalse(result["target_raise_flag"])
        self.assertEqual(result["target_cuts_7d"], 1)

    # ── mixed activity in a 7-day window ─────────────────────────────────────

    def test_upgrade_plus_target_raise_no_double_count(self):
        """Upgrade row that also carries PT raise counts as upgrade only."""
        df = _make_df(
            [
                _make_row(
                    action="upgrade", pt_action="Raises",
                    current_pt=200.0, prior_pt=160.0, to_grade="Buy"
                ),
            ],
            days_ago=[2],
        )
        result = score_analyst_momentum(_make_data(df))
        self.assertEqual(result["upgrades_7d"], 1)
        self.assertEqual(result["target_raises_7d"], 0)

    def test_mixed_upgrade_and_pure_raise(self):
        """One upgrade row + one pure PT raise = upgrade counted once, target raise once."""
        df = _make_df(
            [
                _make_row(action="upgrade", to_grade="Buy"),
                _make_row(action="main", pt_action="Raises", current_pt=180.0, prior_pt=160.0),
            ],
            days_ago=[1, 3],
        )
        result = score_analyst_momentum(_make_data(df))
        self.assertEqual(result["upgrades_7d"], 1)
        self.assertEqual(result["target_raises_7d"], 1)
        # Score from upgrade (1) + target raise (1) = 2
        self.assertGreaterEqual(result["score"], 2)

    def test_mixed_downgrade_and_target_cut_penalty(self):
        """Downgrade + pure PT cut both apply their respective penalties without double-count."""
        df = _make_df(
            [
                _make_row(action="upgrade", to_grade="Buy"),        # +1
                _make_row(action="downgrade", to_grade="Sell"),     # -1 penalty
                _make_row(action="main", pt_action="Lowers",
                          current_pt=80.0, prior_pt=100.0),        # -1 penalty
            ],
            days_ago=[1, 2, 3],
        )
        result = score_analyst_momentum(_make_data(df))
        self.assertEqual(result["upgrades_7d"], 1)
        self.assertEqual(result["target_cuts_7d"], 1)
        # Penalties can push score to 0
        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], 6)

    # ── no false positive when only rating text changes without PT change ──────

    def test_rating_text_only_no_target_raise(self):
        """A maintain row with no PT movement must not trigger target_raise_flag."""
        df = _make_df(
            [_make_row(action="main", to_grade="Neutral", pt_action="Maintains",
                       current_pt=150.0, prior_pt=150.0)],
            days_ago=[1],
        )
        result = score_analyst_momentum(_make_data(df))
        self.assertFalse(result["target_raise_flag"])
        self.assertFalse(result["target_cut_flag"])
        self.assertEqual(result["target_raises_7d"], 0)
        self.assertEqual(result["target_cuts_7d"], 0)

    def test_reiterate_no_pt_data_no_target_raise(self):
        """Reiterated with no PT fields must not produce a spurious target_raise."""
        df = _make_df(
            [_make_row(action="reiterated", to_grade="Hold")],
            days_ago=[2],
        )
        result = score_analyst_momentum(_make_data(df))
        self.assertEqual(result["target_raises_7d"], 0)
        # It IS counted as upgrade (reiterated in upgrade_actions)
        self.assertEqual(result["upgrades_7d"], 1)

    # ── score bounds and output shape ─────────────────────────────────────────

    def test_score_never_exceeds_max(self):
        """Score must stay ≤ max regardless of how many raises pile up."""
        rows = [
            _make_row(action="upgrade", to_grade="Buy") for _ in range(5)
        ] + [
            _make_row(action="main", pt_action="Raises", current_pt=200.0, prior_pt=150.0)
            for _ in range(5)
        ]
        df = _make_df(rows, days_ago=list(range(1, 11)))
        result = score_analyst_momentum(_make_data(df))
        self.assertLessEqual(result["score"], result["max"])

    def test_output_keys_present(self):
        """All expected output keys are always present."""
        result = score_analyst_momentum({"stock_obj": None})
        for key in ("score", "max", "flags", "upgrades_7d",
                    "target_raises_7d", "target_cuts_7d",
                    "target_raise_flag", "target_cut_flag"):
            self.assertIn(key, result, f"Missing key: {key}")

    def test_score_not_negative(self):
        """Score floor is 0 even with multiple downgrades and target cuts."""
        rows = [_make_row(action="downgrade", to_grade="Sell")] * 5
        df = _make_df(rows, days_ago=list(range(1, 6)))
        result = score_analyst_momentum(_make_data(df))
        self.assertGreaterEqual(result["score"], 0)

    def test_empty_dataframe_returns_neutral(self):
        df = _make_df([], [])
        result = score_analyst_momentum(_make_data(df))
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["target_raise_flag"])
        self.assertFalse(result["target_cut_flag"])

    def test_old_events_outside_7d_ignored(self):
        """Events older than 7 days must not contribute to 7-day counts."""
        df = _make_df(
            [_make_row(action="main", pt_action="Raises", current_pt=200.0, prior_pt=170.0)],
            days_ago=[10],
        )
        result = score_analyst_momentum(_make_data(df))
        self.assertEqual(result["target_raises_7d"], 0)
        self.assertFalse(result["target_raise_flag"])

    # ── numeric fallback (no priceTargetAction column) ────────────────────────

    def test_numeric_raise_fallback_when_no_pt_action_column(self):
        """When priceTargetAction is absent, numeric comparison triggers target_raise."""
        row = {
            "Action": "main",
            "ToGrade": "",
            "FromGrade": "",
            "currentPriceTarget": 200.0,
            "priorPriceTarget": 170.0,
            # priceTargetAction intentionally absent
        }
        df = pd.DataFrame([row], index=pd.DatetimeIndex([datetime.now(tz=timezone.utc)]))
        result = score_analyst_momentum(_make_data(df))
        self.assertEqual(result["target_raises_7d"], 1)

    def test_numeric_cut_fallback_when_no_pt_action_column(self):
        row = {
            "Action": "main",
            "ToGrade": "",
            "FromGrade": "",
            "currentPriceTarget": 90.0,
            "priorPriceTarget": 120.0,
        }
        df = pd.DataFrame([row], index=pd.DatetimeIndex([datetime.now(tz=timezone.utc)]))
        result = score_analyst_momentum(_make_data(df))
        self.assertEqual(result["target_cuts_7d"], 1)
        self.assertTrue(result["target_cut_flag"])


if __name__ == "__main__":
    unittest.main()
