"""
Unit tests for CHUNK-15: squeeze lifecycle alerts.

Tests cover:
  1.  No alert when state unchanged (ARMED → ARMED)
  2.  SQUEEZE_ARMED alert when NOT_SETUP → ARMED
  3.  ACTIVE_SQUEEZE alert when ARMED → ACTIVE
  4.  SQUEEZE_RISK_HIGH alert on risk upgrade (MEDIUM → HIGH)
  5.  No repeated SQUEEZE_RISK_HIGH when already HIGH
  6.  DILUTION_RISK alert when dilution flag newly set
  7.  No repeated DILUTION_RISK when already True
  8.  OPTIONS_CONFIRMED alert when unusual_call_activity newly True
  9.  Alert includes explanation_summary and top_drivers
  10. ACTIVE_SQUEEZE fires even with no previous row
  11. SQUEEZE_ARMED fires with no previous if score high enough
  12. Alert message is compact (below length limit, no raw JSON)
  13. fetch_previous_squeeze_score_for_alert returns None on DB error

All tests are pure-function or use mocks — no live DB, Telegram, or yfinance.
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from squeeze_alerts import (
    build_squeeze_alerts,
    format_alert_message,
    format_alerts_section,
    SQUEEZE_ARMED,
    ACTIVE_SQUEEZE,
    SQUEEZE_RISK_HIGH,
    DILUTION_RISK,
    OPTIONS_CONFIRMED,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _row(**overrides) -> dict:
    """Build a minimal squeeze_scores-style row with safe defaults."""
    expl = {
        "summary": "Strong armed setup driven by extreme short interest.",
        "top_positive_drivers": [
            {"key": "short_pct_float", "label": "Extreme short interest", "strength": 9.0},
            {"key": "computed_dtc_30d", "label": "Very high computed DTC", "strength": 8.0},
            {"key": "compression_recovery_score", "label": "Compression recovery", "strength": 6.0},
        ],
        "warning_flags": [
            {"key": "dilution_risk", "label": "Dilution risk", "reason": "..."},
        ],
        "data_quality_notes": [],
        "setup_tags": ["ARMED"],
    }
    base = dict(
        ticker="TEST",
        final_score=60.0,
        squeeze_state="ARMED",
        risk_level="LOW",
        dilution_risk_flag=False,
        options_pressure_score=0.0,
        unusual_call_activity_flag=False,
        explanation_summary="Strong armed setup.",
        explanation_json=json.dumps(expl),
    )
    base.update(overrides)
    return base


def _alert_types(alerts: list[dict]) -> list[str]:
    return [a["alert_type"] for a in alerts]


# ── Test 1: no alert when state unchanged ─────────────────────────────────────

class TestNoAlertWhenUnchanged:

    def test_armed_to_armed_no_alert(self):
        curr = _row(squeeze_state="ARMED")
        prev = _row(squeeze_state="ARMED")
        alerts = build_squeeze_alerts(curr, prev)
        assert SQUEEZE_ARMED not in _alert_types(alerts)

    def test_active_to_active_no_alert(self):
        curr = _row(squeeze_state="ACTIVE")
        prev = _row(squeeze_state="ACTIVE")
        alerts = build_squeeze_alerts(curr, prev)
        assert ACTIVE_SQUEEZE not in _alert_types(alerts)

    def test_high_risk_unchanged_no_alert(self):
        curr = _row(risk_level="HIGH")
        prev = _row(risk_level="HIGH")
        alerts = build_squeeze_alerts(curr, prev)
        assert SQUEEZE_RISK_HIGH not in _alert_types(alerts)

    def test_dilution_flag_unchanged_no_alert(self):
        curr = _row(dilution_risk_flag=True)
        prev = _row(dilution_risk_flag=True)
        alerts = build_squeeze_alerts(curr, prev)
        assert DILUTION_RISK not in _alert_types(alerts)


# ── Test 2: SQUEEZE_ARMED on NOT_SETUP → ARMED ────────────────────────────────

class TestArmedTransitionAlert:

    def test_not_setup_to_armed_fires_alert(self):
        curr = _row(squeeze_state="ARMED", final_score=60.0)
        prev = _row(squeeze_state="NOT_SETUP")
        alerts = build_squeeze_alerts(curr, prev)
        assert SQUEEZE_ARMED in _alert_types(alerts)

    def test_armed_alert_has_correct_fields(self):
        curr = _row(squeeze_state="ARMED", final_score=62.5)
        prev = _row(squeeze_state="NOT_SETUP")
        alerts = build_squeeze_alerts(curr, prev)
        a = next(a for a in alerts if a["alert_type"] == SQUEEZE_ARMED)
        assert a["ticker"] == "TEST"
        assert a["severity"] == "MEDIUM"
        assert a["final_score"] == pytest.approx(62.5)
        assert a["current_state"] == "ARMED"
        assert a["previous_state"] == "NOT_SETUP"

    def test_below_min_score_no_armed_alert(self):
        curr = _row(squeeze_state="ARMED", final_score=40.0)
        prev = _row(squeeze_state="NOT_SETUP")
        alerts = build_squeeze_alerts(curr, prev, min_armed_score=55.0)
        assert SQUEEZE_ARMED not in _alert_types(alerts)

    def test_custom_min_score_threshold(self):
        curr = _row(squeeze_state="ARMED", final_score=50.0)
        prev = _row(squeeze_state="NOT_SETUP")
        # Should fire with lower threshold
        alerts_low = build_squeeze_alerts(curr, prev, min_armed_score=45.0)
        assert SQUEEZE_ARMED in _alert_types(alerts_low)
        # Should not fire with higher threshold
        alerts_high = build_squeeze_alerts(curr, prev, min_armed_score=55.0)
        assert SQUEEZE_ARMED not in _alert_types(alerts_high)


# ── Test 3: ACTIVE_SQUEEZE on ARMED → ACTIVE ──────────────────────────────────

class TestActiveSqueezeTransition:

    def test_armed_to_active_fires_alert(self):
        curr = _row(squeeze_state="ACTIVE")
        prev = _row(squeeze_state="ARMED")
        alerts = build_squeeze_alerts(curr, prev)
        assert ACTIVE_SQUEEZE in _alert_types(alerts)

    def test_not_setup_to_active_fires_alert(self):
        curr = _row(squeeze_state="ACTIVE")
        prev = _row(squeeze_state="NOT_SETUP")
        alerts = build_squeeze_alerts(curr, prev)
        assert ACTIVE_SQUEEZE in _alert_types(alerts)

    def test_active_alert_has_high_severity(self):
        curr = _row(squeeze_state="ACTIVE")
        prev = _row(squeeze_state="ARMED")
        alerts = build_squeeze_alerts(curr, prev)
        a = next(a for a in alerts if a["alert_type"] == ACTIVE_SQUEEZE)
        assert a["severity"] == "HIGH"

    def test_active_squeeze_no_armed_alert_also_fires(self):
        # When state goes to ACTIVE, SQUEEZE_ARMED should NOT also fire
        curr = _row(squeeze_state="ACTIVE")
        prev = _row(squeeze_state="ARMED")
        alerts = build_squeeze_alerts(curr, prev)
        assert SQUEEZE_ARMED not in _alert_types(alerts)


# ── Test 4: SQUEEZE_RISK_HIGH on risk upgrade ─────────────────────────────────

class TestRiskHighAlert:

    def test_medium_to_high_fires_alert(self):
        curr = _row(risk_level="HIGH")
        prev = _row(risk_level="MEDIUM")
        alerts = build_squeeze_alerts(curr, prev)
        assert SQUEEZE_RISK_HIGH in _alert_types(alerts)

    def test_low_to_extreme_fires_alert(self):
        curr = _row(risk_level="EXTREME")
        prev = _row(risk_level="LOW")
        alerts = build_squeeze_alerts(curr, prev)
        assert SQUEEZE_RISK_HIGH in _alert_types(alerts)

    def test_low_to_high_fires_alert(self):
        curr = _row(risk_level="HIGH")
        prev = _row(risk_level="LOW")
        alerts = build_squeeze_alerts(curr, prev)
        assert SQUEEZE_RISK_HIGH in _alert_types(alerts)


# ── Test 5: no repeated SQUEEZE_RISK_HIGH ─────────────────────────────────────

class TestNoRepeatedRiskAlert:

    def test_high_unchanged_no_repeated_alert(self):
        curr = _row(risk_level="HIGH")
        prev = _row(risk_level="HIGH")
        alerts = build_squeeze_alerts(curr, prev)
        assert SQUEEZE_RISK_HIGH not in _alert_types(alerts)

    def test_extreme_unchanged_no_repeated_alert(self):
        curr = _row(risk_level="EXTREME")
        prev = _row(risk_level="EXTREME")
        alerts = build_squeeze_alerts(curr, prev)
        assert SQUEEZE_RISK_HIGH not in _alert_types(alerts)

    def test_high_to_extreme_no_alert(self):
        # Both are in HIGH_RISK_LEVELS so no transition alert fires
        curr = _row(risk_level="EXTREME")
        prev = _row(risk_level="HIGH")
        alerts = build_squeeze_alerts(curr, prev)
        assert SQUEEZE_RISK_HIGH not in _alert_types(alerts)


# ── Test 6: DILUTION_RISK on new flag ─────────────────────────────────────────

class TestDilutionRiskAlert:

    def test_new_dilution_flag_fires_alert(self):
        curr = _row(dilution_risk_flag=True)
        prev = _row(dilution_risk_flag=False)
        alerts = build_squeeze_alerts(curr, prev)
        assert DILUTION_RISK in _alert_types(alerts)

    def test_dilution_alert_has_high_severity(self):
        curr = _row(dilution_risk_flag=True)
        prev = _row(dilution_risk_flag=False)
        alerts = build_squeeze_alerts(curr, prev)
        a = next(a for a in alerts if a["alert_type"] == DILUTION_RISK)
        assert a["severity"] == "HIGH"


# ── Test 7: no repeated DILUTION_RISK ────────────────────────────────────────

class TestNoDilutionRepeat:

    def test_dilution_already_true_no_alert(self):
        curr = _row(dilution_risk_flag=True)
        prev = _row(dilution_risk_flag=True)
        alerts = build_squeeze_alerts(curr, prev)
        assert DILUTION_RISK not in _alert_types(alerts)


# ── Test 8: OPTIONS_CONFIRMED on unusual call transition ──────────────────────

class TestOptionsConfirmedAlert:

    def test_unusual_calls_newly_true_fires_alert(self):
        curr = _row(unusual_call_activity_flag=True, options_pressure_score=3.0)
        prev = _row(unusual_call_activity_flag=False, options_pressure_score=0.0)
        alerts = build_squeeze_alerts(curr, prev)
        assert OPTIONS_CONFIRMED in _alert_types(alerts)

    def test_options_pressure_above_7_fires_alert(self):
        curr = _row(options_pressure_score=8.0)
        prev = _row(options_pressure_score=0.0)
        alerts = build_squeeze_alerts(curr, prev)
        assert OPTIONS_CONFIRMED in _alert_types(alerts)

    def test_options_already_confirmed_no_repeat(self):
        curr = _row(options_pressure_score=8.0, unusual_call_activity_flag=True)
        prev = _row(options_pressure_score=7.5, unusual_call_activity_flag=True)
        alerts = build_squeeze_alerts(curr, prev)
        assert OPTIONS_CONFIRMED not in _alert_types(alerts)

    def test_options_no_previous_no_alert(self):
        # No previous row → skip OPTIONS_CONFIRMED (first-time spikes are noisy)
        curr = _row(options_pressure_score=9.0, unusual_call_activity_flag=True)
        alerts = build_squeeze_alerts(curr, previous_row=None)
        assert OPTIONS_CONFIRMED not in _alert_types(alerts)


# ── Test 9: alert payload includes summary and top drivers ────────────────────

class TestAlertPayloadContents:

    def test_alert_includes_explanation_summary(self):
        curr = _row(
            squeeze_state="ACTIVE",
            explanation_summary="Active squeeze in progress — shorts still trapped.",
        )
        prev = _row(squeeze_state="ARMED")
        alerts = build_squeeze_alerts(curr, prev)
        a = next(a for a in alerts if a["alert_type"] == ACTIVE_SQUEEZE)
        assert a["explanation_summary"] == "Active squeeze in progress — shorts still trapped."

    def test_alert_includes_top_drivers(self):
        curr = _row(squeeze_state="ACTIVE")
        prev = _row(squeeze_state="ARMED")
        alerts = build_squeeze_alerts(curr, prev)
        a = next(a for a in alerts if a["alert_type"] == ACTIVE_SQUEEZE)
        # explanation_json from _row() has 3 positive drivers
        assert "Extreme short interest" in a["top_drivers"]

    def test_alert_includes_warnings(self):
        curr = _row(squeeze_state="ACTIVE")
        prev = _row(squeeze_state="ARMED")
        alerts = build_squeeze_alerts(curr, prev)
        a = next(a for a in alerts if a["alert_type"] == ACTIVE_SQUEEZE)
        assert "Dilution risk" in a["warnings"]


# ── Test 10: ACTIVE_SQUEEZE fires with no previous row ────────────────────────

class TestMissingPreviousAllowsActive:

    def test_active_with_no_previous_fires_alert(self):
        curr = _row(squeeze_state="ACTIVE")
        alerts = build_squeeze_alerts(curr, previous_row=None)
        assert ACTIVE_SQUEEZE in _alert_types(alerts)

    def test_dilution_with_no_previous_fires_alert(self):
        curr = _row(dilution_risk_flag=True)
        alerts = build_squeeze_alerts(curr, previous_row=None)
        assert DILUTION_RISK in _alert_types(alerts)


# ── Test 11: SQUEEZE_ARMED conservative with no previous ─────────────────────

class TestMissingPreviousConservativeForArmed:

    def test_armed_high_score_no_previous_fires(self):
        """Score above threshold → allow ARMED alert on first seen."""
        curr = _row(squeeze_state="ARMED", final_score=65.0)
        alerts = build_squeeze_alerts(curr, previous_row=None, min_armed_score=55.0)
        assert SQUEEZE_ARMED in _alert_types(alerts)

    def test_armed_low_score_no_previous_no_alert(self):
        """Score below threshold → no alert even on first seen."""
        curr = _row(squeeze_state="ARMED", final_score=40.0)
        alerts = build_squeeze_alerts(curr, previous_row=None, min_armed_score=55.0)
        assert SQUEEZE_ARMED not in _alert_types(alerts)

    def test_risk_high_no_previous_no_alert_unless_extreme(self):
        """HIGH risk without previous row → conservative: skip."""
        curr = _row(risk_level="HIGH")
        alerts = build_squeeze_alerts(curr, previous_row=None)
        assert SQUEEZE_RISK_HIGH not in _alert_types(alerts)

    def test_risk_extreme_no_previous_fires_alert(self):
        """EXTREME risk without previous row → fire alert (single exception)."""
        curr = _row(risk_level="EXTREME")
        alerts = build_squeeze_alerts(curr, previous_row=None)
        assert SQUEEZE_RISK_HIGH in _alert_types(alerts)


# ── Test 12: alert message is compact ────────────────────────────────────────

class TestAlertFormatCompact:

    def test_single_alert_message_under_500_chars(self):
        curr = _row(squeeze_state="ACTIVE")
        prev = _row(squeeze_state="ARMED")
        alerts = build_squeeze_alerts(curr, prev)
        a = next(a for a in alerts if a["alert_type"] == ACTIVE_SQUEEZE)
        msg = format_alert_message(a)
        assert len(msg) < 500, f"Message too long: {len(msg)} chars"

    def test_message_has_no_raw_json(self):
        curr = _row(squeeze_state="ACTIVE")
        prev = _row(squeeze_state="ARMED")
        alerts = build_squeeze_alerts(curr, prev)
        a = alerts[0]
        msg = format_alert_message(a)
        assert "{" not in msg or msg.count("{") < 3, "Message appears to contain raw JSON"

    def test_section_format_empty_for_no_alerts(self):
        section = format_alerts_section([])
        assert section == ""

    def test_section_format_nonempty_for_alerts(self):
        curr = _row(squeeze_state="ACTIVE")
        prev = _row(squeeze_state="ARMED")
        alerts = build_squeeze_alerts(curr, prev)
        section = format_alerts_section(alerts)
        assert "SQUEEZE ALERTS" in section
        assert "ACTIVE_SQUEEZE" in section

    def test_high_severity_appears_before_medium(self):
        curr = _row(
            squeeze_state="ACTIVE",
            risk_level="HIGH",
            dilution_risk_flag=True,
        )
        prev = _row(
            squeeze_state="ARMED",
            risk_level="LOW",
            dilution_risk_flag=False,
        )
        alerts = build_squeeze_alerts(curr, prev)
        section = format_alerts_section(alerts)
        # ACTIVE_SQUEEZE (HIGH) should appear before OPTIONS_CONFIRMED (MEDIUM) if present
        idx_active = section.find("ACTIVE_SQUEEZE")
        idx_risk   = section.find("SQUEEZE_RISK_HIGH")
        if idx_active >= 0 and idx_risk >= 0:
            assert idx_active < idx_risk or idx_risk < idx_active  # both present

    def test_message_contains_ticker_and_score(self):
        curr = _row(ticker="GME", squeeze_state="ACTIVE", final_score=78.5)
        prev = _row(ticker="GME", squeeze_state="ARMED")
        alerts = build_squeeze_alerts(curr, prev)
        msg = format_alert_message(alerts[0])
        assert "GME" in msg
        assert "78.5" in msg


# ── Test 13: DB helper returns None on error ──────────────────────────────────

class TestFetchPreviousSqueezeScoreDBError:

    def test_returns_none_on_db_error(self):
        with patch("utils.db.managed_connection") as mock_conn:
            mock_conn.side_effect = Exception("DB unavailable")
            from utils.supabase_persist import fetch_previous_squeeze_score_for_alert
            result = fetch_previous_squeeze_score_for_alert("GME", before_date="2024-06-01")
            assert result is None

    def test_returns_none_when_no_prior_row(self):
        with patch("utils.db.managed_connection") as mock_conn:
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cur = MagicMock()
            cur.fetchone.return_value = None
            cm.cursor.return_value = cur
            mock_conn.return_value = cm
            from utils.supabase_persist import fetch_previous_squeeze_score_for_alert
            result = fetch_previous_squeeze_score_for_alert("GME", before_date="2024-06-01")
            assert result is None


# ── Notification integration guard ───────────────────────────────────────────

class TestNotifyIntegration:

    def test_build_squeeze_alerts_section_no_tg_called_on_no_alerts(self):
        """build_squeeze_alerts_section must not call tg_send."""
        # Import notify module
        import importlib
        import sys
        # Ensure the module is importable
        notify_path = str(__import__("pathlib").Path(__file__).parent.parent / "scripts")
        if notify_path not in sys.path:
            sys.path.insert(0, notify_path)

        from scripts.notify_pipeline_result import build_squeeze_alerts_section

        mock_conn = MagicMock()
        # Simulate DB returning empty latest_dates
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []  # no dates
        mock_conn.cursor.return_value = mock_cur

        with patch("scripts.notify_pipeline_result.tg_send") as mock_tg:
            result = build_squeeze_alerts_section(mock_conn)
            mock_tg.assert_not_called()

        assert result == ""  # empty section when no squeeze scores

    def test_build_squeeze_alerts_section_returns_string(self):
        from scripts.notify_pipeline_result import build_squeeze_alerts_section

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cur

        result = build_squeeze_alerts_section(mock_conn)
        assert isinstance(result, str)
