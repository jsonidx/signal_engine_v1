"""
Unit tests for CHUNK-14: squeeze explanation cards.

Tests cover:
  - Neutral/minimal score does not crash
  - Positive driver ranking for each signal group
  - Volume missing is a timing note, not a major negative
  - Completed squeeze appears in negative drivers
  - Dilution risk surfaces in warning_flags
  - Unknown effective-float confidence surfaces in data_quality_notes
  - Drivers sorted by strength descending
  - SqueezeScore.explanation field exists and is serializable
  - Supabase persistence payload includes explanation fields

All tests use only pure functions and mocked DB — no live calls.
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from squeeze_screener import (
    SqueezeScore,
    build_squeeze_explanation,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_sq(**overrides) -> SqueezeScore:
    """Build a minimal SqueezeScore suitable for explanation tests."""
    defaults = dict(
        ticker="TEST",
        final_score=50.0,
        signal_breakdown={
            "pct_float_short_score": 0.0,
            "short_pnl_score": 0.0,
            "si_persistence_score": 5.0,
            "si_persistence_count": 0,
            "si_trend_direction": "unknown",
            "days_to_cover_score": 0.0,
            "volume_surge_score": 0.0,
            "ftd_score": 0.0,
            "cost_to_borrow_score": 0.0,
            "market_cap_score": 0.0,
            "float_score": 0.0,
            "price_divergence_score": 0.0,
            "compression_recovery_score": 0.0,
            "effective_float_score": 0.0,
            "effective_float_estimate": 0.0,
            "large_holder_ownership_pct": 0.0,
            "effective_short_float_ratio": 0.0,
            "extreme_float_lock_flag": 0.0,
            "large_holder_concentration_flag": 0.0,
        },
        juice_target=20.0,
        recent_squeeze=False,
        short_pct_float=0.0,
        computed_dtc_30d=0.0,
        days_to_cover=0.0,
        squeeze_state="false",
        volume_confirmation_flag=False,
        compression_recovery_score=0.0,
        effective_float_score=0.0,
        effective_float_confidence="unknown",
        extreme_float_lock_flag=False,
        large_holder_concentration_flag=False,
        large_holder_ownership_pct=0.0,
        effective_short_float_ratio=0.0,
        flags=[],
        explanation={},
    )
    defaults.update(overrides)
    return SqueezeScore(**defaults)


def _sq_with_breakdown(**bd_overrides) -> SqueezeScore:
    """Build a SqueezeScore with specific signal_breakdown overrides."""
    base_bd = {
        "pct_float_short_score": 0.0,
        "short_pnl_score": 0.0,
        "si_persistence_score": 5.0,
        "si_persistence_count": 0,
        "si_trend_direction": "unknown",
        "days_to_cover_score": 0.0,
        "volume_surge_score": 0.0,
        "ftd_score": 0.0,
        "cost_to_borrow_score": 0.0,
        "market_cap_score": 0.0,
        "float_score": 0.0,
        "price_divergence_score": 0.0,
        "compression_recovery_score": 0.0,
        "effective_float_score": 0.0,
        "effective_float_estimate": 0.0,
        "large_holder_ownership_pct": 0.0,
        "effective_short_float_ratio": 0.0,
        "extreme_float_lock_flag": 0.0,
        "large_holder_concentration_flag": 0.0,
    }
    base_bd.update(bd_overrides)
    return _make_sq(signal_breakdown=base_bd)


def _driver_labels(drivers: list) -> list[str]:
    return [d["label"] for d in drivers]


# ── Pure explanation tests ────────────────────────────────────────────────────

class TestExplanationBasics:

    def test_neutral_score_has_no_crash(self):
        sq = _make_sq()
        result = build_squeeze_explanation(sq)
        assert isinstance(result, dict)
        assert "summary" in result
        assert "top_positive_drivers" in result
        assert "top_negative_drivers" in result
        assert "warning_flags" in result
        assert "data_quality_notes" in result
        assert "setup_tags" in result

    def test_summary_is_string(self):
        sq = _make_sq()
        result = build_squeeze_explanation(sq)
        assert isinstance(result["summary"], str)
        assert len(result["summary"]) > 0

    def test_all_list_fields_are_lists(self):
        sq = _make_sq()
        result = build_squeeze_explanation(sq)
        assert isinstance(result["top_positive_drivers"], list)
        assert isinstance(result["top_negative_drivers"], list)
        assert isinstance(result["warning_flags"], list)
        assert isinstance(result["data_quality_notes"], list)
        assert isinstance(result["setup_tags"], list)


class TestPositiveDriverRanking:

    def test_ranks_extreme_short_interest(self):
        sq = _sq_with_breakdown(pct_float_short_score=8.5)
        sq.short_pct_float = 0.50
        result = build_squeeze_explanation(sq)
        labels = _driver_labels(result["top_positive_drivers"])
        assert any("short interest" in lbl.lower() for lbl in labels)
        # The highest-score driver should be extreme SI
        assert result["top_positive_drivers"][0]["key"] == "short_pct_float"

    def test_ranks_high_short_interest_at_score_7(self):
        sq = _sq_with_breakdown(pct_float_short_score=7.0)
        sq.short_pct_float = 0.35
        result = build_squeeze_explanation(sq)
        labels = _driver_labels(result["top_positive_drivers"])
        assert any("short interest" in lbl.lower() for lbl in labels)

    def test_ranks_high_dtc_driver(self):
        sq = _sq_with_breakdown(days_to_cover_score=8.0)
        sq.computed_dtc_30d = 9.6
        result = build_squeeze_explanation(sq)
        labels = _driver_labels(result["top_positive_drivers"])
        assert any("dtc" in lbl.lower() or "days" in lbl.lower() for lbl in labels)
        dtc_driver = next(d for d in result["top_positive_drivers"] if d["key"] == "computed_dtc_30d")
        assert "9.6" in dtc_driver["display_value"]

    def test_ranks_compression_recovery_driver(self):
        sq = _sq_with_breakdown(compression_recovery_score=6.0)
        sq.compression_recovery_score = 6.0
        result = build_squeeze_explanation(sq)
        labels = _driver_labels(result["top_positive_drivers"])
        assert any("compression" in lbl.lower() for lbl in labels)

    def test_ranks_effective_float_score_driver(self):
        sq = _sq_with_breakdown(effective_float_score=9.0)
        sq.effective_float_score = 9.0
        sq.effective_short_float_ratio = 1.2
        sq.extreme_float_lock_flag = True
        sq.large_holder_ownership_pct = 55.0
        result = build_squeeze_explanation(sq)
        labels = _driver_labels(result["top_positive_drivers"])
        assert any("float" in lbl.lower() for lbl in labels)

    def test_ranks_effective_float_concentration_driver(self):
        sq = _sq_with_breakdown(effective_float_score=6.0)
        sq.effective_float_score = 6.0
        sq.large_holder_concentration_flag = True
        sq.large_holder_ownership_pct = 32.0
        sq.effective_short_float_ratio = 0.55
        result = build_squeeze_explanation(sq)
        labels = _driver_labels(result["top_positive_drivers"])
        assert any("float" in lbl.lower() for lbl in labels)

    def test_persistent_si_driver_at_score_8(self):
        sq = _sq_with_breakdown(
            si_persistence_score=8.0,
            si_persistence_count=3,
            si_trend_direction="stable",
        )
        result = build_squeeze_explanation(sq)
        labels = _driver_labels(result["top_positive_drivers"])
        assert any("persistent" in lbl.lower() for lbl in labels)

    def test_rising_si_trend_driver(self):
        sq = _sq_with_breakdown(
            si_persistence_score=7.0,
            si_persistence_count=2,
            si_trend_direction="rising",
        )
        result = build_squeeze_explanation(sq)
        labels = _driver_labels(result["top_positive_drivers"])
        assert any("rising" in lbl.lower() for lbl in labels)


class TestVolumeConfirmation:

    def test_volume_missing_is_not_major_negative(self):
        """No volume confirmation should NOT appear in top_negative_drivers."""
        sq = _make_sq(volume_confirmation_flag=False)
        result = build_squeeze_explanation(sq)
        neg_labels = _driver_labels(result["top_negative_drivers"])
        assert not any("volume" in lbl.lower() for lbl in neg_labels)

    def test_volume_missing_appears_as_warning_or_note(self):
        """Missing volume should be a timing note in warning_flags."""
        sq = _make_sq(volume_confirmation_flag=False)
        result = build_squeeze_explanation(sq)
        warning_keys = [w["key"] for w in result["warning_flags"]]
        assert "volume_confirmation_flag" in warning_keys

    def test_volume_confirmed_appears_as_positive(self):
        sq = _sq_with_breakdown(volume_surge_score=7.5)
        sq.volume_confirmation_flag = True
        result = build_squeeze_explanation(sq)
        labels = _driver_labels(result["top_positive_drivers"])
        assert any("volume" in lbl.lower() for lbl in labels)


class TestNegativeDrivers:

    def test_completed_squeeze_is_negative_driver(self):
        sq = _make_sq(squeeze_state="completed", final_score=0.0, recent_squeeze=True)
        result = build_squeeze_explanation(sq)
        neg_labels = _driver_labels(result["top_negative_drivers"])
        assert any("completed" in lbl.lower() or "squeeze" in lbl.lower() for lbl in neg_labels)

    def test_completed_squeeze_in_summary(self):
        sq = _make_sq(squeeze_state="completed", final_score=0.0)
        result = build_squeeze_explanation(sq)
        assert "completed" in result["summary"].lower()

    def test_low_si_score_is_negative(self):
        sq = _sq_with_breakdown(pct_float_short_score=1.0)
        sq.short_pct_float = 0.02
        result = build_squeeze_explanation(sq)
        neg_labels = _driver_labels(result["top_negative_drivers"])
        assert any("short interest" in lbl.lower() for lbl in neg_labels)


class TestWarningFlags:

    def test_dilution_risk_is_warning_not_score_change(self):
        sq = _make_sq(flags=["Potential dilution filing (424B5) — recent issuance risk"])
        result = build_squeeze_explanation(sq)
        warn_keys = [w["key"] for w in result["warning_flags"]]
        assert "dilution_risk_flag" in warn_keys
        # It must NOT appear in positive or negative drivers
        all_driver_keys = (
            [d["key"] for d in result["top_positive_drivers"]]
            + [d["key"] for d in result["top_negative_drivers"]]
        )
        assert "dilution_risk_flag" not in all_driver_keys

    def test_atm_language_triggers_dilution_warning(self):
        sq = _make_sq(flags=["entered into an equity distribution agreement"])
        result = build_squeeze_explanation(sq)
        warn_keys = [w["key"] for w in result["warning_flags"]]
        assert "dilution_risk_flag" in warn_keys


class TestDataQualityNotes:

    def test_unknown_effective_float_confidence_is_dq_note(self):
        sq = _make_sq(effective_float_confidence="unknown")
        result = build_squeeze_explanation(sq)
        dq_keys = [n["key"] for n in result["data_quality_notes"]]
        assert "effective_float_confidence" in dq_keys

    def test_low_effective_float_confidence_is_dq_note(self):
        sq = _make_sq(effective_float_confidence="low")
        result = build_squeeze_explanation(sq)
        dq_keys = [n["key"] for n in result["data_quality_notes"]]
        assert "effective_float_confidence" in dq_keys

    def test_high_confidence_has_no_dq_note(self):
        sq = _make_sq(effective_float_confidence="high")
        result = build_squeeze_explanation(sq)
        dq_keys = [n["key"] for n in result["data_quality_notes"]]
        assert "effective_float_confidence" not in dq_keys


class TestDriverSorting:

    def test_top_drivers_sorted_by_strength_descending(self):
        sq = _sq_with_breakdown(
            pct_float_short_score=8.5,   # strength 8.5
            days_to_cover_score=6.0,     # strength 6.0
            si_persistence_score=9.0,    # strength 9.0 → highest
            si_persistence_count=3,
        )
        sq.short_pct_float = 0.52
        sq.computed_dtc_30d = 5.5
        result = build_squeeze_explanation(sq)
        pos = result["top_positive_drivers"]
        assert len(pos) >= 2
        strengths = [d["strength"] for d in pos]
        assert strengths == sorted(strengths, reverse=True)

    def test_positive_drivers_capped_at_five(self):
        # Trigger 6+ positive drivers
        sq = _sq_with_breakdown(
            pct_float_short_score=8.5,
            days_to_cover_score=8.0,
            si_persistence_score=9.0,
            si_persistence_count=3,
            compression_recovery_score=8.0,
            price_divergence_score=8.0,
            cost_to_borrow_score=10.0,
        )
        sq.short_pct_float = 0.52
        sq.computed_dtc_30d = 9.0
        sq.volume_confirmation_flag = True
        bd = dict(sq.signal_breakdown)
        bd["volume_surge_score"] = 9.0
        sq.signal_breakdown = bd
        result = build_squeeze_explanation(sq)
        assert len(result["top_positive_drivers"]) <= 5


class TestOutputPersistence:

    def test_squeeze_score_contains_explanation_field(self):
        """After compute_squeeze_score, explanation should be a non-empty dict."""
        # We can test this without running the full pipeline by calling
        # build_squeeze_explanation directly and checking the result
        sq = _make_sq(final_score=55.0, short_pct_float=0.35)
        sq.explanation = build_squeeze_explanation(sq)
        assert isinstance(sq.explanation, dict)
        assert "summary" in sq.explanation
        assert "top_positive_drivers" in sq.explanation

    def test_explanation_json_is_serializable(self):
        sq = _make_sq(final_score=55.0, short_pct_float=0.35)
        sq.explanation = build_squeeze_explanation(sq)
        serialized = json.dumps(sq.explanation)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["summary"] == sq.explanation["summary"]

    def test_to_dict_includes_explanation_summary_and_json(self):
        sq = _make_sq(final_score=55.0)
        sq.explanation = build_squeeze_explanation(sq)
        d = sq.to_dict()
        assert "explanation_summary" in d
        assert "explanation_json" in d
        assert isinstance(d["explanation_summary"], str)
        assert isinstance(d["explanation_json"], str)
        # explanation_json must be valid JSON
        parsed = json.loads(d["explanation_json"])
        assert "summary" in parsed

    def test_save_squeeze_scores_payload_includes_explanation(self):
        """Mock the DB cursor and verify explanation fields are in the INSERT tuple."""
        import pandas as pd
        from unittest.mock import MagicMock, patch

        sq = _make_sq(ticker="CAR", final_score=65.0, short_pct_float=0.35)
        sq.explanation = build_squeeze_explanation(sq)
        row = sq.to_dict()
        for k, v in row.pop("signal_breakdown", {}).items():
            row[k] = v
        row["flags"] = " | ".join(row.get("flags", []))
        df = pd.DataFrame([row])

        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch("utils.supabase_persist._conn", return_value=mock_conn):
            from utils.supabase_persist import save_squeeze_scores
            save_squeeze_scores(df, run_date="2026-04-25")

        # Find the executemany call
        executemany_call = mock_cur.executemany.call_args
        assert executemany_call is not None

        # The rows tuple must have 42 elements:
        # 22 original + explanation_summary + explanation_json (CHUNK-14)
        # + state_confidence + state_reasons + state_warnings (CHUNK-10)
        # + risk_score + risk_level + risk_flags + risk_warnings + risk_components
        #   + dilution_risk_flag + latest_dilution_filing_date + shares_offered_pct_float (CHUNK-16)
        # + options_pressure_score + iv_rank + iv_rank_score + iv_data_confidence
        #   + unusual_call_activity_flag + call_put_volume_ratio + call_put_oi_ratio (CHUNK-09)
        rows_arg = executemany_call[0][1]
        assert len(rows_arg) == 1
        assert len(rows_arg[0]) == 42

        # explanation_summary at index 22 should be a non-empty string
        assert isinstance(rows_arg[0][22], str)
        assert len(rows_arg[0][22]) > 0


class TestSetupTags:

    def test_extreme_si_tag(self):
        sq = _make_sq(short_pct_float=0.55)
        result = build_squeeze_explanation(sq)
        assert "EXTREME_SHORT_INTEREST" in result["setup_tags"]

    def test_high_si_tag_at_30pct(self):
        sq = _make_sq(short_pct_float=0.30)
        result = build_squeeze_explanation(sq)
        assert "HIGH_SHORT_INTEREST" in result["setup_tags"]

    def test_armed_tag_for_high_score(self):
        # CHUNK-10: ARMED tag is driven by lifecycle state == "ARMED"
        sq = _make_sq(final_score=65.0, squeeze_state="ARMED")
        result = build_squeeze_explanation(sq)
        assert "ARMED" in result["setup_tags"]

    def test_completed_squeeze_tag(self):
        sq = _make_sq(squeeze_state="completed", final_score=0.0)
        result = build_squeeze_explanation(sq)
        assert "COMPLETED_SQUEEZE" in result["setup_tags"]
        assert "ARMED" not in result["setup_tags"]

    def test_float_locked_tag(self):
        sq = _make_sq(extreme_float_lock_flag=True)
        result = build_squeeze_explanation(sq)
        assert "FLOAT_LOCKED" in result["setup_tags"]


# ── CHUNK-10: explanation uses lifecycle state ────────────────────────────────

class TestLifecycleStateInExplanation:

    def test_active_state_yields_active_squeeze_tag(self):
        sq = _make_sq(squeeze_state="ACTIVE", short_pct_float=0.40)
        result = build_squeeze_explanation(sq)
        assert "ACTIVE_SQUEEZE" in result["setup_tags"]
        assert "ARMED" not in result["setup_tags"]

    def test_armed_state_yields_armed_tag(self):
        sq = _make_sq(squeeze_state="ARMED", final_score=65.0)
        result = build_squeeze_explanation(sq)
        assert "ARMED" in result["setup_tags"]
        assert "ACTIVE_SQUEEZE" not in result["setup_tags"]

    def test_not_setup_state_yields_no_primary_lifecycle_tag(self):
        sq = _make_sq(squeeze_state="NOT_SETUP", final_score=20.0)
        result = build_squeeze_explanation(sq)
        assert "ACTIVE_SQUEEZE" not in result["setup_tags"]
        assert "ARMED" not in result["setup_tags"]

    def test_active_state_produces_active_squeeze_positive_driver(self):
        sq = _make_sq(squeeze_state="ACTIVE")
        result = build_squeeze_explanation(sq)
        driver_keys = [d["key"] for d in result["top_positive_drivers"]]
        assert "squeeze_state" in driver_keys

    def test_recent_squeeze_completed_produces_completed_squeeze_tag(self):
        sq = _make_sq(squeeze_state="NOT_SETUP", final_score=0.0, recent_squeeze=True)
        result = build_squeeze_explanation(sq)
        assert "COMPLETED_SQUEEZE" in result["setup_tags"]


# ── CHUNK-16: risk warnings in explanation ────────────────────────────────────

class TestRiskWarningsInExplanation:

    def test_explanation_surfaces_dilution_risk_warning(self):
        """When sq.risk_warnings contains dilution text, explanation must surface it."""
        sq = _make_sq(
            squeeze_state="ARMED",
            risk_warnings=["Dilution filing detected (424B5/S-3/ATM). Share issuance warning."],
            risk_flags=["DILUTION_RISK"],
            risk_level="HIGH",
            dilution_risk_flag=True,
        )
        result = build_squeeze_explanation(sq)
        warning_keys = [w["key"] for w in result["warning_flags"]]
        assert any("dilution" in k for k in warning_keys)

    def test_high_risk_tag_present_when_risk_high(self):
        sq = _make_sq(
            squeeze_state="ARMED",
            risk_level="HIGH",
            risk_warnings=["High risk test."],
            risk_flags=["COMPLETED_SQUEEZE_RISK"],
        )
        result = build_squeeze_explanation(sq)
        assert "HIGH_RISK" in result["setup_tags"]

    def test_extreme_risk_tag_present(self):
        sq = _make_sq(squeeze_state="ARMED", risk_level="EXTREME")
        result = build_squeeze_explanation(sq)
        assert "EXTREME_RISK" in result["setup_tags"]

    def test_dilution_risk_flag_adds_dilution_tag(self):
        sq = _make_sq(dilution_risk_flag=True, risk_level="HIGH")
        result = build_squeeze_explanation(sq)
        assert "DILUTION_RISK" in result["setup_tags"]

    def test_low_risk_no_risk_tag(self):
        sq = _make_sq(risk_level="LOW")
        result = build_squeeze_explanation(sq)
        assert "HIGH_RISK" not in result["setup_tags"]
        assert "EXTREME_RISK" not in result["setup_tags"]


# ── CHUNK-09: Options/IV context in explanation ───────────────────────────────

class TestOptionsIVInExplanation:

    def test_high_options_pressure_surfaces_in_positive_drivers(self):
        sq = _make_sq()
        sq.signal_breakdown["options_pressure_score"] = 8.0
        sq.signal_breakdown["iv_rank"] = 50.0
        sq.signal_breakdown["iv_data_confidence"] = "high"
        sq.signal_breakdown["unusual_call_activity_flag"] = False
        result = build_squeeze_explanation(sq)
        keys = [d["key"] for d in result["top_positive_drivers"]]
        assert "options_pressure_score" in keys

    def test_unusual_call_activity_flag_surfaces_in_positive_drivers(self):
        sq = _make_sq()
        sq.signal_breakdown["options_pressure_score"] = 3.0
        sq.signal_breakdown["iv_rank"] = None
        sq.signal_breakdown["iv_data_confidence"] = "none"
        sq.signal_breakdown["unusual_call_activity_flag"] = True
        result = build_squeeze_explanation(sq)
        keys = [d["key"] for d in result["top_positive_drivers"]]
        assert "options_pressure_score" in keys

    def test_iv_rank_above_80_surfaces_as_warning(self):
        sq = _make_sq()
        sq.signal_breakdown["options_pressure_score"] = 0.0
        sq.signal_breakdown["iv_rank"] = 85.0
        sq.signal_breakdown["iv_data_confidence"] = "high"
        sq.signal_breakdown["unusual_call_activity_flag"] = False
        result = build_squeeze_explanation(sq)
        warning_keys = [w["key"] for w in result["warning_flags"]]
        assert "high_iv_rank" in warning_keys

    def test_missing_iv_data_surfaces_as_dq_note(self):
        sq = _make_sq()
        sq.signal_breakdown["options_pressure_score"] = 0.0
        sq.signal_breakdown["iv_rank"] = None
        sq.signal_breakdown["iv_data_confidence"] = "none"
        sq.signal_breakdown["unusual_call_activity_flag"] = False
        result = build_squeeze_explanation(sq)
        dq_keys = [n["key"] for n in result["data_quality_notes"]]
        assert "iv_data_confidence" in dq_keys

    def test_options_confirmed_tag_when_pressure_high(self):
        sq = _make_sq()
        sq.signal_breakdown["options_pressure_score"] = 7.5
        sq.signal_breakdown["iv_rank"] = 55.0
        sq.signal_breakdown["iv_data_confidence"] = "high"
        sq.signal_breakdown["unusual_call_activity_flag"] = False
        result = build_squeeze_explanation(sq)
        assert "OPTIONS_CONFIRMED" in result["setup_tags"]
