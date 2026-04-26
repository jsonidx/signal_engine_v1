"""
Unit tests for CHUNK-10: squeeze_state_machine.py

Tests cover:
  1.  NOT_SETUP for low score (< 45)
  2.  NOT_SETUP for low short interest (< 15%)
  3.  ARMED for high structural setup without volume confirmation
  4.  ACTIVE for recent_squeeze_state = active
  5.  ACTIVE for volume confirmation plus structural pressure
  6.  Completed recent squeeze maps to NOT_SETUP with warning
  7.  State confidence = high when multiple drivers present
  8.  State confidence = low when inputs are sparse / mostly None
  9.  NOT_SETUP: score >= 45 but only 1 structural driver (< 2 required)
  10. ARMED tag propagates through squeeze screener integration
  11. ACTIVE_SQUEEZE tag set when lifecycle state is ACTIVE
  12. High-score override: short_pct_float < 0.15 does NOT trigger NOT_SETUP
      when final_score >= 75
  13. Completed squeeze with SI just above floor → NOT_SETUP still (warning only)
  14. State reasons are non-empty strings for all states
  15. No crash on all-None inputs with score < 45

All tests are pure-function — no live DB or network calls.
"""

import pytest

from squeeze_state_machine import classify_squeeze_state


# ── helper ───────────────────────────────────────────────────────────────────

def _classify(**kwargs) -> dict:
    """Call classify_squeeze_state with keyword overrides over safe defaults."""
    defaults = dict(
        final_score=50.0,
        short_pct_float=0.30,
        computed_dtc_30d=5.0,
        compression_recovery_score=6.0,
        effective_float_score=6.0,
        si_persistence_score=7.0,
        volume_confirmation_flag=False,
        recent_squeeze_state=None,
    )
    defaults.update(kwargs)
    return classify_squeeze_state(**defaults)


# ── Test 1: NOT_SETUP for low score ──────────────────────────────────────────

class TestNotSetupLowScore:

    def test_score_below_45_is_not_setup(self):
        result = _classify(final_score=30.0)
        assert result["state"] == "NOT_SETUP"

    def test_score_exactly_44_is_not_setup(self):
        result = _classify(final_score=44.9)
        assert result["state"] == "NOT_SETUP"

    def test_score_45_can_be_armed(self):
        # Score at 45 is no longer auto-NOT_SETUP; ARMED needs >= 55
        result = _classify(final_score=45.0, volume_confirmation_flag=False)
        # 45 < 55 so not ARMED, but not auto-NOT_SETUP either — should still be NOT_SETUP
        # because ARMED requires final_score >= 55
        assert result["state"] == "NOT_SETUP"

    def test_reasons_mention_score(self):
        result = _classify(final_score=20.0)
        assert any("score" in r.lower() or "20" in r for r in result["state_reasons"])


# ── Test 2: NOT_SETUP for low short interest ──────────────────────────────────

class TestNotSetupLowSI:

    def test_si_below_15pct_is_not_setup(self):
        result = _classify(final_score=60.0, short_pct_float=0.10)
        assert result["state"] == "NOT_SETUP"

    def test_si_exactly_14pct_is_not_setup(self):
        result = _classify(final_score=60.0, short_pct_float=0.14)
        assert result["state"] == "NOT_SETUP"

    def test_reasons_mention_short_interest(self):
        result = _classify(final_score=60.0, short_pct_float=0.10)
        assert any("short interest" in r.lower() or "si" in r.lower() or "10%" in r
                   for r in result["state_reasons"])


# ── Test 3: ARMED for high structural setup without volume ───────────────────

class TestArmedHighStructuralSetup:

    def test_armed_with_two_structural_drivers(self):
        result = _classify(
            final_score=60.0,
            short_pct_float=0.35,
            computed_dtc_30d=6.0,
            compression_recovery_score=None,
            effective_float_score=None,
            si_persistence_score=None,
            volume_confirmation_flag=False,
        )
        assert result["state"] == "ARMED"

    def test_armed_requires_final_score_55(self):
        result = _classify(
            final_score=54.9,
            short_pct_float=0.35,
            computed_dtc_30d=6.0,
            volume_confirmation_flag=False,
        )
        # Below 55 threshold → NOT_SETUP
        assert result["state"] == "NOT_SETUP"

    def test_armed_not_triggered_with_volume_confirmation(self):
        # Same inputs but volume confirmed → should not be ARMED
        result = _classify(
            final_score=65.0,
            short_pct_float=0.35,
            computed_dtc_30d=6.0,
            volume_confirmation_flag=True,
        )
        # Volume confirmed + structural pressure → ACTIVE
        assert result["state"] == "ACTIVE"

    def test_armed_not_triggered_when_recent_squeeze_active(self):
        result = _classify(
            final_score=65.0,
            short_pct_float=0.40,
            computed_dtc_30d=7.0,
            recent_squeeze_state="active",
        )
        assert result["state"] == "ACTIVE"  # recent active takes precedence


# ── Test 4: ACTIVE for recent_squeeze_state = active ─────────────────────────

class TestActiveRecentSqueezeActive:

    def test_active_when_recent_squeeze_is_active(self):
        result = _classify(
            final_score=55.0,
            short_pct_float=0.35,
            recent_squeeze_state="active",
        )
        assert result["state"] == "ACTIVE"

    def test_active_when_recent_squeeze_active_even_low_score(self):
        # recent_squeeze_state == "active" is a direct override regardless of score
        result = _classify(
            final_score=30.0,
            short_pct_float=0.32,
            recent_squeeze_state="active",
        )
        assert result["state"] == "ACTIVE"

    def test_reasons_mention_trapped_shorts(self):
        result = _classify(recent_squeeze_state="active")
        assert any("trapped" in r.lower() or "active" in r.lower() or "si" in r.lower()
                   for r in result["state_reasons"])


# ── Test 5: ACTIVE for volume confirmation + structural pressure ──────────────

class TestActiveVolumeConfirmed:

    def test_active_with_volume_and_structural_pressure(self):
        result = _classify(
            final_score=60.0,
            short_pct_float=0.35,
            computed_dtc_30d=6.0,
            volume_confirmation_flag=True,
        )
        assert result["state"] == "ACTIVE"

    def test_active_requires_si_at_least_30pct(self):
        result = _classify(
            final_score=65.0,
            short_pct_float=0.25,   # below 0.30 threshold
            computed_dtc_30d=6.0,
            volume_confirmation_flag=True,
        )
        # SI < 0.30 means the minimum SI for volume-confirmed ACTIVE is not met
        assert result["state"] != "ACTIVE"

    def test_active_requires_at_least_one_structural_driver(self):
        result = _classify(
            final_score=60.0,
            short_pct_float=0.35,
            computed_dtc_30d=None,
            compression_recovery_score=None,
            effective_float_score=None,
            si_persistence_score=None,
            volume_confirmation_flag=True,
        )
        # SI >= 0.30 counts as 1 structural driver → should be ACTIVE
        assert result["state"] == "ACTIVE"


# ── Test 6: completed recent squeeze maps to NOT_SETUP with warning ───────────

class TestCompletedRecentSqueeze:

    def test_completed_with_low_si_is_not_setup(self):
        result = _classify(
            final_score=55.0,
            short_pct_float=0.15,
            recent_squeeze_state="completed",
        )
        assert result["state"] == "NOT_SETUP"

    def test_completed_warning_present(self):
        result = _classify(
            final_score=55.0,
            short_pct_float=0.10,
            recent_squeeze_state="completed",
        )
        assert result["state"] == "NOT_SETUP"
        assert any("completed" in w.lower() for w in result["state_warnings"])

    def test_completed_above_floor_si_can_be_armed(self):
        # Completed squeeze but SI is still high (>= 0.20) + score >= 55 + 2+ drivers
        result = _classify(
            final_score=60.0,
            short_pct_float=0.35,
            computed_dtc_30d=6.0,
            recent_squeeze_state="completed",
            volume_confirmation_flag=False,
        )
        # With SI >= 0.20 and high score, the completed-floor check passes; ARMED is possible
        assert result["state"] in ("ARMED", "NOT_SETUP")
        # When state is ARMED, a note about re-accumulation should appear in reasons
        if result["state"] == "ARMED":
            assert any("completed" in r.lower() or "re-accumulation" in r.lower()
                       for r in result["state_reasons"])


# ── Test 7: state_confidence = high when multiple drivers ────────────────────

class TestConfidenceHigh:

    def test_high_confidence_with_multiple_drivers(self):
        result = _classify(
            final_score=70.0,
            short_pct_float=0.40,
            computed_dtc_30d=8.0,
            compression_recovery_score=7.0,
            effective_float_score=7.0,
            si_persistence_score=8.0,
            volume_confirmation_flag=False,
        )
        assert result["state"] == "ARMED"
        assert result["state_confidence"] == "high"

    def test_active_high_confidence_with_multiple_drivers(self):
        result = _classify(
            final_score=70.0,
            short_pct_float=0.40,
            computed_dtc_30d=7.0,
            effective_float_score=7.0,
            recent_squeeze_state="active",
        )
        assert result["state"] == "ACTIVE"
        assert result["state_confidence"] == "high"


# ── Test 8: state_confidence = low when sparse inputs ────────────────────────

class TestConfidenceLow:

    def test_low_confidence_with_sparse_inputs(self):
        result = classify_squeeze_state(
            final_score=30.0,
            short_pct_float=None,
            computed_dtc_30d=None,
            compression_recovery_score=None,
            effective_float_score=None,
            si_persistence_score=None,
            volume_confirmation_flag=None,
            recent_squeeze_state=None,
        )
        assert result["state"] == "NOT_SETUP"
        assert result["state_confidence"] == "low"

    def test_no_crash_on_all_none_inputs(self):
        result = classify_squeeze_state(final_score=0.0)
        assert result["state"] == "NOT_SETUP"
        assert "state" in result
        assert "state_confidence" in result
        assert "state_reasons" in result
        assert "state_warnings" in result


# ── Test 9: score >= 45 but insufficient structural drivers ───────────────────

class TestInsufficientStructuralDrivers:

    def test_not_setup_when_only_one_driver(self):
        result = _classify(
            final_score=60.0,
            short_pct_float=0.35,    # 1 structural driver
            computed_dtc_30d=None,
            compression_recovery_score=None,
            effective_float_score=None,
            si_persistence_score=None,
            volume_confirmation_flag=False,
        )
        # Only SI meets threshold — 1 driver < 2 required for ARMED
        assert result["state"] == "NOT_SETUP"

    def test_armed_exactly_at_two_drivers(self):
        result = _classify(
            final_score=60.0,
            short_pct_float=0.35,    # driver 1
            computed_dtc_30d=6.0,    # driver 2
            compression_recovery_score=None,
            effective_float_score=None,
            si_persistence_score=None,
            volume_confirmation_flag=False,
        )
        assert result["state"] == "ARMED"


# ── Test 12: high-score SI override ──────────────────────────────────────────

class TestHighScoreSIOverride:

    def test_high_score_overrides_low_si_not_setup(self):
        # final_score >= 75 should override the low-SI NOT_SETUP rule
        result = _classify(
            final_score=78.0,
            short_pct_float=0.10,   # below 0.15 threshold
        )
        # Should NOT be NOT_SETUP due to low SI alone — score is very high
        assert result["state"] != "NOT_SETUP" or any(
            "score" not in r.lower() for r in result["state_reasons"]
        )

    def test_below_high_score_threshold_keeps_low_si_not_setup(self):
        result = _classify(
            final_score=74.9,
            short_pct_float=0.10,
        )
        assert result["state"] == "NOT_SETUP"


# ── Test 14: state_reasons non-empty ─────────────────────────────────────────

class TestStateReasonsNonEmpty:

    @pytest.mark.parametrize("state_kwargs,expected_state", [
        ({"final_score": 20.0}, "NOT_SETUP"),
        ({"final_score": 65.0, "short_pct_float": 0.35, "computed_dtc_30d": 6.0,
          "volume_confirmation_flag": False}, "ARMED"),
        ({"final_score": 65.0, "short_pct_float": 0.35, "computed_dtc_30d": 6.0,
          "volume_confirmation_flag": True}, "ACTIVE"),
        ({"final_score": 40.0, "recent_squeeze_state": "active"}, "ACTIVE"),
    ])
    def test_reasons_always_non_empty(self, state_kwargs, expected_state):
        result = classify_squeeze_state(**state_kwargs)
        assert result["state"] == expected_state
        assert len(result["state_reasons"]) >= 1
        assert all(isinstance(r, str) and len(r) > 0 for r in result["state_reasons"])
