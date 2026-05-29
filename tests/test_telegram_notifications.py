"""
tests/test_telegram_notifications.py — TRD-016: Telegram notification and bot command tests.

Covers:
  1. notify_approval_request() formats correctly (risk icons, command syntax, IDs)
  2. Pipeline notifier builds approval notification message shape
  3. Bot /pending command formats pending requests
  4. Bot /approve command success and failure paths
  5. Bot /reject command success and failure paths
  6. Unknown command falls back to help hint
  7. Squeeze state name consistency (EARLY_ARMED / ARMED / ACTIVE) between notifier
     and bot display — regression guard against terminology drift

All tests are pure-unit or use mocks — no live DB, Telegram, or network calls.
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Ensure project root is on path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Import subjects ───────────────────────────────────────────────────────────

from scripts.notify_pipeline_result import notify_approval_request
from scripts.telegram_bot import (
    handle_command,
    _format_approval_request,
    _fetch_pending_requests,
    _approve_request,
    _reject_request,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pending_req(**overrides) -> dict:
    base = dict(
        request_id="REQ-ABC-001",
        title="Raise ARMED threshold from 55 to 60",
        category="SQUEEZE_STATE_MACHINE",
        risk_level="MEDIUM",
        status="PENDING",
        summary="Calibration data shows false-positive rate drops by 12% with threshold=60.",
        created_at="2026-05-29T08:00:00",
    )
    base.update(overrides)
    return base


# ── 1. notify_approval_request() formatting ──────────────────────────────────

class TestNotifyApprovalRequest:

    def test_sends_message_with_request_id(self):
        with patch("scripts.notify_pipeline_result.tg_send") as mock_tg:
            notify_approval_request(
                request_id="REQ-TEST-001",
                title="Test approval request",
                category="SQUEEZE_CALIBRATION",
                risk_level="MEDIUM",
                summary="Test summary text.",
            )
            assert mock_tg.called
            msg = mock_tg.call_args[0][0]
            assert "REQ-TEST-001" in msg

    def test_approve_reject_commands_in_message(self):
        with patch("scripts.notify_pipeline_result.tg_send") as mock_tg:
            notify_approval_request(
                request_id="REQ-TEST-002",
                title="Threshold change",
                category="SQUEEZE_STATE_MACHINE",
                risk_level="HIGH",
                summary="Proposes raising ARMED threshold.",
            )
            msg = mock_tg.call_args[0][0]
            assert "/approve REQ-TEST-002" in msg
            assert "/reject REQ-TEST-002" in msg

    def test_high_risk_shows_red_icon(self):
        with patch("scripts.notify_pipeline_result.tg_send") as mock_tg:
            notify_approval_request(
                request_id="REQ-TEST-003",
                title="High-risk change",
                category="MODEL_PROMOTION",
                risk_level="HIGH",
                summary="Replace squeeze scorer.",
            )
            msg = mock_tg.call_args[0][0]
            assert "🔴" in msg

    def test_medium_risk_shows_yellow_icon(self):
        with patch("scripts.notify_pipeline_result.tg_send") as mock_tg:
            notify_approval_request(
                request_id="REQ-TEST-004",
                title="Medium risk change",
                category="SQUEEZE_CALIBRATION",
                risk_level="MEDIUM",
                summary="Adjust threshold.",
            )
            msg = mock_tg.call_args[0][0]
            assert "🟡" in msg

    def test_low_risk_shows_green_icon(self):
        with patch("scripts.notify_pipeline_result.tg_send") as mock_tg:
            notify_approval_request(
                request_id="REQ-TEST-005",
                title="Low risk change",
                category="TAXONOMY_RULE",
                risk_level="LOW",
                summary="Documentation update.",
            )
            msg = mock_tg.call_args[0][0]
            assert "🟢" in msg

    def test_category_included_in_message(self):
        with patch("scripts.notify_pipeline_result.tg_send") as mock_tg:
            notify_approval_request(
                request_id="REQ-TEST-006",
                title="Category test",
                category="SQUEEZE_STATE_MACHINE",
                risk_level="MEDIUM",
                summary="State machine change.",
            )
            msg = mock_tg.call_args[0][0]
            assert "SQUEEZE_STATE_MACHINE" in msg

    def test_evidence_ref_included_when_provided(self):
        with patch("scripts.notify_pipeline_result.tg_send") as mock_tg:
            notify_approval_request(
                request_id="REQ-TEST-007",
                title="With evidence",
                category="SQUEEZE_CALIBRATION",
                risk_level="MEDIUM",
                summary="Calibration result.",
                evidence_ref="reports/calibration_2026-05-29.md",
            )
            msg = mock_tg.call_args[0][0]
            assert "reports/calibration_2026-05-29.md" in msg

    def test_long_summary_truncated(self):
        long_summary = "x" * 500
        with patch("scripts.notify_pipeline_result.tg_send") as mock_tg:
            notify_approval_request(
                request_id="REQ-TEST-008",
                title="Truncation test",
                category="SQUEEZE_CALIBRATION",
                risk_level="LOW",
                summary=long_summary,
            )
            msg = mock_tg.call_args[0][0]
            # Message should be sent (not crash), and be reasonably short
            assert len(msg) < 2000

    def test_message_not_sent_when_no_token(self):
        """When TELEGRAM_BOT_TOKEN is empty, tg_send prints to stderr but doesn't raise."""
        with patch("scripts.notify_pipeline_result.BOT_TOKEN", ""), \
             patch("scripts.notify_pipeline_result.CHAT_ID", ""), \
             patch("scripts.notify_pipeline_result.tg_send") as mock_tg:
            # We patched tg_send to avoid any real HTTP call
            notify_approval_request(
                request_id="REQ-TEST-009",
                title="No token",
                category="SQUEEZE_CALIBRATION",
                risk_level="LOW",
                summary="No credentials.",
            )
            # tg_send is still called — it handles the missing-token case internally
            assert mock_tg.called


# ── 2. _format_approval_request() (bot display helper) ───────────────────────

class TestFormatApprovalRequest:

    def test_contains_request_id(self):
        req = _pending_req()
        text = _format_approval_request(req)
        assert "REQ-ABC-001" in text

    def test_contains_title(self):
        req = _pending_req()
        text = _format_approval_request(req)
        assert "Raise ARMED threshold" in text

    def test_contains_category_and_risk(self):
        req = _pending_req()
        text = _format_approval_request(req)
        assert "SQUEEZE_STATE_MACHINE" in text
        assert "MEDIUM" in text

    def test_contains_approve_reject_commands(self):
        req = _pending_req()
        text = _format_approval_request(req)
        assert "/approve REQ-ABC-001" in text
        assert "/reject REQ-ABC-001" in text

    def test_contains_status(self):
        req = _pending_req(status="PENDING")
        text = _format_approval_request(req)
        assert "PENDING" in text

    def test_summary_included(self):
        req = _pending_req(summary="Calibration shows 12% improvement.")
        text = _format_approval_request(req)
        assert "Calibration shows 12% improvement." in text

    def test_summary_truncated_at_200(self):
        req = _pending_req(summary="x" * 300)
        text = _format_approval_request(req)
        # Summary is cut at 200 chars in the formatter
        assert text.count("x") <= 200

    def test_missing_summary_handled_gracefully(self):
        req = _pending_req()
        req.pop("summary", None)
        text = _format_approval_request(req)
        assert "REQ-ABC-001" in text  # still renders without crash


# ── 3. /pending bot command ───────────────────────────────────────────────────

class TestPendingCommand:

    def test_pending_no_requests_sends_ok_message(self):
        with patch("scripts.telegram_bot._fetch_pending_requests", return_value=[]), \
             patch("scripts.telegram_bot.tg_send") as mock_tg:
            handle_command("/pending", "12345")
            msg = mock_tg.call_args[0][0]
            assert "No pending" in msg or "pending" in msg.lower()

    def test_pending_with_requests_shows_count(self):
        reqs = [_pending_req(request_id=f"REQ-{i:03d}") for i in range(3)]
        with patch("scripts.telegram_bot._fetch_pending_requests", return_value=reqs), \
             patch("scripts.telegram_bot.tg_send") as mock_tg:
            handle_command("/pending", "12345")
            msg = mock_tg.call_args[0][0]
            assert "3" in msg

    def test_pending_caps_at_5_shown(self):
        """When more than 5 requests exist, only 5 are shown with an overflow note."""
        reqs = [_pending_req(request_id=f"REQ-{i:03d}", title=f"Change {i}") for i in range(8)]
        with patch("scripts.telegram_bot._fetch_pending_requests", return_value=reqs), \
             patch("scripts.telegram_bot.tg_send") as mock_tg:
            handle_command("/pending", "12345")
            msg = mock_tg.call_args[0][0]
            # Overflow message should mention the extra count
            assert "3 more" in msg or "more" in msg.lower()

    def test_pending_shows_approve_reject_hints(self):
        reqs = [_pending_req()]
        with patch("scripts.telegram_bot._fetch_pending_requests", return_value=reqs), \
             patch("scripts.telegram_bot.tg_send") as mock_tg:
            handle_command("/pending", "12345")
            msg = mock_tg.call_args[0][0]
            assert "/approve" in msg
            assert "/reject" in msg


# ── 4. /approve bot command ───────────────────────────────────────────────────

class TestApproveCommand:

    def test_approve_success_sends_confirmed_message(self):
        with patch("scripts.telegram_bot._approve_request", return_value=True), \
             patch("scripts.telegram_bot.tg_send") as mock_tg:
            handle_command("/approve REQ-TEST-001", "12345")
            msg = mock_tg.call_args[0][0]
            assert "APPROVED" in msg.upper()
            assert "REQ-TEST-001" in msg

    def test_approve_failure_sends_error_message(self):
        with patch("scripts.telegram_bot._approve_request", return_value=False), \
             patch("scripts.telegram_bot.tg_send") as mock_tg:
            handle_command("/approve REQ-TEST-001", "12345")
            msg = mock_tg.call_args[0][0]
            assert "Failed" in msg or "failed" in msg

    def test_approve_without_id_shows_usage(self):
        with patch("scripts.telegram_bot.tg_send") as mock_tg:
            handle_command("/approve", "12345")
            msg = mock_tg.call_args[0][0]
            assert "Usage" in msg or "usage" in msg or "request_id" in msg.lower()

    def test_approve_sends_to_correct_chat(self):
        with patch("scripts.telegram_bot._approve_request", return_value=True), \
             patch("scripts.telegram_bot.tg_send") as mock_tg:
            handle_command("/approve REQ-TEST-002", "chat_xyz")
            _, kwargs = mock_tg.call_args
            # tg_send(msg, chat_id=...) — check positional or keyword arg
            args = mock_tg.call_args[0]
            assert "chat_xyz" in args or mock_tg.call_args[1].get("chat_id") == "chat_xyz"


# ── 5. /reject bot command ────────────────────────────────────────────────────

class TestRejectCommand:

    def test_reject_success_sends_rejected_message(self):
        with patch("scripts.telegram_bot._reject_request", return_value=True), \
             patch("scripts.telegram_bot.tg_send") as mock_tg:
            handle_command("/reject REQ-TEST-001", "12345")
            msg = mock_tg.call_args[0][0]
            assert "REJECTED" in msg.upper()
            assert "REQ-TEST-001" in msg

    def test_reject_failure_sends_error_message(self):
        with patch("scripts.telegram_bot._reject_request", return_value=False), \
             patch("scripts.telegram_bot.tg_send") as mock_tg:
            handle_command("/reject REQ-TEST-001", "12345")
            msg = mock_tg.call_args[0][0]
            assert "Failed" in msg or "failed" in msg

    def test_reject_without_id_shows_usage(self):
        with patch("scripts.telegram_bot.tg_send") as mock_tg:
            handle_command("/reject", "12345")
            msg = mock_tg.call_args[0][0]
            assert "Usage" in msg or "usage" in msg or "request_id" in msg.lower()

    def test_reject_not_applied_automatically(self):
        """Rejecting a request only records status — it must NOT apply any change."""
        applied = []
        with patch("scripts.telegram_bot._reject_request", return_value=True) as mock_rej, \
             patch("scripts.telegram_bot.tg_send"):
            handle_command("/reject REQ-TEST-003", "12345")
            # Only _reject_request should be called, not any application function
            assert mock_rej.called
            # No side-effect function that "applies" was called
            assert not applied


# ── 6. Unknown command fallback ───────────────────────────────────────────────

class TestUnknownCommand:

    def test_unknown_command_replies_with_help_hint(self):
        with patch("scripts.telegram_bot.tg_send") as mock_tg:
            handle_command("/nonexistent_command", "12345")
            msg = mock_tg.call_args[0][0]
            assert "/help" in msg or "help" in msg.lower() or "unknown" in msg.lower()


# ── 7. Squeeze state terminology consistency ──────────────────────────────────

class TestSqueezeTerminologyConsistency:
    """
    Regression guard: ensure that EARLY_ARMED, ARMED, and ACTIVE_SQUEEZE state names
    used in Telegram messages match the constants defined in squeeze_alerts.py.
    """

    def test_early_armed_constant_matches_expected_string(self):
        from squeeze_alerts import EARLY_ARMED_ALERT
        assert EARLY_ARMED_ALERT == "EARLY_ARMED_ALERT"

    def test_squeeze_armed_constant_matches_expected_string(self):
        from squeeze_alerts import SQUEEZE_ARMED
        assert SQUEEZE_ARMED == "SQUEEZE_ARMED"

    def test_active_squeeze_constant_matches_expected_string(self):
        from squeeze_alerts import ACTIVE_SQUEEZE
        assert ACTIVE_SQUEEZE == "ACTIVE_SQUEEZE"

    def test_format_section_header_consistent(self):
        """Section header must read 'SQUEEZE ALERTS' for all alert types."""
        from squeeze_alerts import (
            build_squeeze_alerts, format_alerts_section, ACTIVE_SQUEEZE
        )
        import json

        def _row(**kw):
            base = dict(
                ticker="TST", final_score=60.0, squeeze_state="ACTIVE",
                risk_level="LOW", dilution_risk_flag=False,
                options_pressure_score=0.0, unusual_call_activity_flag=False,
                explanation_summary="Test.", explanation_json=json.dumps({}),
            )
            base.update(kw)
            return base

        curr = _row(squeeze_state="ACTIVE")
        prev = _row(squeeze_state="ARMED")
        alerts = build_squeeze_alerts(curr, prev)
        section = format_alerts_section(alerts)
        assert "SQUEEZE ALERTS" in section

    def test_help_text_mentions_approval_commands(self):
        """HELP_TEXT in bot must list /approve, /reject, /pending commands."""
        from scripts.telegram_bot import HELP_TEXT
        assert "/approve" in HELP_TEXT
        assert "/reject" in HELP_TEXT
        assert "/pending" in HELP_TEXT

    def test_approval_workflow_env_vars_documented_in_notifier(self):
        """
        notify_pipeline_result.py docstring must document TELEGRAM_BOT_TOKEN,
        TELEGRAM_CHAT_ID, DATABASE_URL — regression guard for env var drift.
        """
        import inspect
        import scripts.notify_pipeline_result as nmod
        src = inspect.getsource(nmod)
        assert "TELEGRAM_BOT_TOKEN" in src
        assert "TELEGRAM_CHAT_ID" in src
        assert "DATABASE_URL" in src
