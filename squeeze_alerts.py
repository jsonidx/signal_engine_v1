"""
squeeze_alerts.py — CHUNK-15 + TRD-011: Lifecycle alert builder for squeeze state/risk changes.

Pure helper — no DB, no HTTP, no side effects.
The same inputs always produce the same outputs.

PUBLIC API
----------
    build_squeeze_alerts(current_row, previous_row, min_armed_score=55.0,
                         min_early_armed_score=45.0)
        Compare one ticker's latest vs previous squeeze_scores row.
        Returns a list of alert dicts (may be empty).
        Deduplicates: only fires on state/risk/flag TRANSITIONS.

    format_alert_message(alert)
        Format a single alert dict into a concise Telegram HTML string (~400 chars max).

    format_alerts_section(alerts)
        Format a list of alerts into a full Telegram section.

ALERT TYPES
-----------
    EARLY_ARMED_ALERT  — LOW/MEDIUM  state transitioned into EARLY_ARMED.
                         Semantics: "Early setup / entry hunting — DDD-style coiled
                         spring forming. Watch for entry; lower hit rate than ARMED."
                         This is the earliest fresh-entry alert.

    SQUEEZE_ARMED      — MEDIUM  state transitioned into ARMED (score >= min_armed_score).
                         Semantics: "Structural setup confirmed — primary pre-breakout
                         entry watch. Not yet ignited."

    ACTIVE_SQUEEZE     — HIGH    state transitioned into ACTIVE.
                         Semantics: "Move in progress / chase risk HIGH — this is a
                         continuation alert, not a fresh-entry signal."

    SQUEEZE_RISK_HIGH  — HIGH    risk_level upgraded to HIGH or EXTREME
    DILUTION_RISK      — HIGH    dilution_risk_flag newly set to True
    OPTIONS_CONFIRMED  — MEDIUM  options_pressure_score >= 7 or unusual_call_activity_flag,
                                 only on transition (never on first-seen — too noisy)

ENTRY STATE PRIORITY
--------------------
    EARLY_ARMED < ARMED < ACTIVE (by setup maturity)
    Primary fresh-entry alerts: EARLY_ARMED and ARMED.
    ACTIVE is primarily a continuation / chase-risk / management state.

DEDUPLICATION
-------------
    No alert fires when state/risk/flags are UNCHANGED from the previous row.
    With no previous row:
      - ACTIVE_SQUEEZE:    always alert (high severity, important to catch)
      - SQUEEZE_ARMED:     alert if final_score >= min_armed_score
      - EARLY_ARMED_ALERT: alert if final_score >= min_early_armed_score
      - DILUTION_RISK:     always alert (high severity)
      - SQUEEZE_RISK_HIGH: alert only for EXTREME (HIGH alone is too noisy without history)
      - OPTIONS_CONFIRMED: skip (first-time options spikes are noisy without history context)
"""

from __future__ import annotations

import json
from typing import Optional

# ── Alert type constants ───────────────────────────────────────────────────────
EARLY_ARMED_ALERT = "EARLY_ARMED_ALERT"   # TRD-011: early entry-hunting alert
SQUEEZE_ARMED     = "SQUEEZE_ARMED"
ACTIVE_SQUEEZE    = "ACTIVE_SQUEEZE"
SQUEEZE_RISK_HIGH = "SQUEEZE_RISK_HIGH"
DILUTION_RISK     = "DILUTION_RISK"
OPTIONS_CONFIRMED = "OPTIONS_CONFIRMED"

_SEVERITY: dict[str, str] = {
    EARLY_ARMED_ALERT: "MEDIUM",
    SQUEEZE_ARMED:     "MEDIUM",
    ACTIVE_SQUEEZE:    "HIGH",
    SQUEEZE_RISK_HIGH: "HIGH",
    DILUTION_RISK:     "HIGH",
    OPTIONS_CONFIRMED: "MEDIUM",
}

_ICONS: dict[str, str] = {
    EARLY_ARMED_ALERT: "🔍",
    SQUEEZE_ARMED:     "⚡",
    ACTIVE_SQUEEZE:    "🚨",
    SQUEEZE_RISK_HIGH: "⚠️",
    DILUTION_RISK:     "🧨",
    OPTIONS_CONFIRMED: "📊",
}

_HIGH_RISK_LEVELS = frozenset({"HIGH", "EXTREME"})


# ── Internal helpers ───────────────────────────────────────────────────────────

def _sf(row: dict, key: str, default=None):
    """Safe field getter — returns *default* when key is missing or None."""
    v = row.get(key)
    return v if v is not None else default


def _parse_explanation(raw) -> dict:
    """
    Safely deserialise explanation_json.
    Accepts str (JSON), dict (already parsed by psycopg2 JSONB), or None.
    Returns {} on any failure.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        result = json.loads(raw)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _top_drivers_text(expl: dict, n: int = 3) -> str:
    """Extract top N positive driver labels as a comma-separated string."""
    drivers = expl.get("top_positive_drivers") or []
    labels = [d.get("label", "") for d in drivers[:n] if d.get("label")]
    return ", ".join(labels) if labels else ""


def _warnings_text(expl: dict, n: int = 2) -> str:
    """Extract top N warning labels as a comma-separated string."""
    warnings = expl.get("warning_flags") or []
    labels = [w.get("label", "") for w in warnings[:n] if w.get("label")]
    return ", ".join(labels) if labels else ""


def _make_alert(
    alert_type: str,
    current: dict,
    previous: Optional[dict],
    expl: dict,
) -> dict:
    """Construct a single alert dict from the current row + parsed explanation."""
    prev_state = _sf(previous, "squeeze_state") if previous is not None else None
    return {
        "alert_type": alert_type,
        "ticker": _sf(current, "ticker", "?"),
        "severity": _SEVERITY.get(alert_type, "MEDIUM"),
        "title": f"{_ICONS.get(alert_type, '')} {alert_type}: {_sf(current, 'ticker', '?')}",
        "current_state": _sf(current, "squeeze_state"),
        "previous_state": prev_state,
        "final_score": _sf(current, "final_score", 0.0),
        "risk_level": _sf(current, "risk_level", "LOW"),
        "explanation_summary": _sf(current, "explanation_summary", ""),
        "top_drivers": _top_drivers_text(expl),
        "warnings": _warnings_text(expl),
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def build_squeeze_alerts(
    current_row: dict,
    previous_row: Optional[dict] = None,
    min_armed_score: float = 55.0,
    min_early_armed_score: float = 45.0,
) -> list[dict]:
    """
    Compare one ticker's latest vs previous squeeze_scores row.
    Returns a list of alert dicts (may be empty).

    Parameters
    ----------
    current_row          : dict from the latest squeeze_scores run
    previous_row         : dict from the prior run (None if first-time or unavailable)
    min_armed_score      : minimum final_score to fire SQUEEZE_ARMED (default 55)
    min_early_armed_score: minimum final_score to fire EARLY_ARMED_ALERT (default 45)

    State priority for alerts:
        ACTIVE_SQUEEZE fires when state is ACTIVE.
        SQUEEZE_ARMED fires when state is ARMED (not ACTIVE — that fires instead).
        EARLY_ARMED_ALERT fires when state is EARLY_ARMED (not ARMED or ACTIVE).

    ACTIVE_SQUEEZE alert copy includes explicit chase-risk language.
    EARLY_ARMED_ALERT copy explicitly frames it as a watch/preparation alert.
    """
    alerts: list[dict] = []
    expl = _parse_explanation(_sf(current_row, "explanation_json"))

    curr_state = (_sf(current_row, "squeeze_state") or "").upper()
    prev_state = (_sf(previous_row, "squeeze_state") or "").upper() if previous_row is not None else None

    curr_risk  = (_sf(current_row, "risk_level") or "LOW").upper()
    prev_risk  = (_sf(previous_row, "risk_level") or "LOW").upper() if previous_row is not None else None

    curr_dil   = bool(_sf(current_row, "dilution_risk_flag", False))
    prev_dil   = bool(_sf(previous_row, "dilution_risk_flag", False)) if previous_row is not None else None

    curr_opts  = float(_sf(current_row, "options_pressure_score", 0.0) or 0.0)
    curr_unusual = bool(_sf(current_row, "unusual_call_activity_flag", False))
    prev_opts  = float(_sf(previous_row, "options_pressure_score", 0.0) or 0.0) if previous_row is not None else None
    prev_unusual = bool(_sf(previous_row, "unusual_call_activity_flag", False)) if previous_row is not None else None

    final_score = float(_sf(current_row, "final_score", 0.0) or 0.0)

    # ── 1. ACTIVE_SQUEEZE ─────────────────────────────────────────────────────
    # Chase-risk alert: move in progress, shorts still trapped.
    # NOT the primary fresh-entry alert — framed as continuation / chase risk.
    if curr_state == "ACTIVE":
        if previous_row is None or prev_state != "ACTIVE":
            alerts.append(_make_alert(ACTIVE_SQUEEZE, current_row, previous_row, expl))

    # ── 2. SQUEEZE_ARMED ──────────────────────────────────────────────────────
    # Primary pre-breakout entry-watch alert — structural setup confirmed.
    # Only triggers when current is ARMED (not when ACTIVE — that fires #1 instead).
    elif curr_state == "ARMED" and final_score >= min_armed_score:
        if previous_row is None:
            alerts.append(_make_alert(SQUEEZE_ARMED, current_row, previous_row, expl))
        elif prev_state not in ("ARMED", "ACTIVE"):
            # Transition from any lower state to ARMED (but not back from ACTIVE)
            alerts.append(_make_alert(SQUEEZE_ARMED, current_row, previous_row, expl))

    # ── 3. EARLY_ARMED_ALERT ──────────────────────────────────────────────────
    # Earliest entry-hunting alert: DDD-style coiled spring forming.
    # Only triggers when current is EARLY_ARMED (not ARMED or ACTIVE — those fire above).
    # Explicitly framed as a watch/preparation alert, not a confirmed setup.
    elif curr_state == "EARLY_ARMED" and final_score >= min_early_armed_score:
        if previous_row is None:
            alerts.append(_make_alert(EARLY_ARMED_ALERT, current_row, previous_row, expl))
        elif prev_state not in ("EARLY_ARMED", "ARMED", "ACTIVE"):
            # Only fire when transitioning INTO EARLY_ARMED from a lower state
            alerts.append(_make_alert(EARLY_ARMED_ALERT, current_row, previous_row, expl))

    # ── 3. SQUEEZE_RISK_HIGH ──────────────────────────────────────────────────
    if curr_risk in _HIGH_RISK_LEVELS:
        if previous_row is None:
            # No history: only fire for EXTREME (HIGH without context is too noisy)
            if curr_risk == "EXTREME":
                alerts.append(_make_alert(SQUEEZE_RISK_HIGH, current_row, previous_row, expl))
        elif prev_risk not in _HIGH_RISK_LEVELS:
            # Genuine upgrade: was LOW/MEDIUM, now HIGH or EXTREME
            alerts.append(_make_alert(SQUEEZE_RISK_HIGH, current_row, previous_row, expl))
        # else: already HIGH/EXTREME before → no repeated alert

    # ── 4. DILUTION_RISK ──────────────────────────────────────────────────────
    if curr_dil:
        if previous_row is None:
            # No history: always alert for new dilution flag (high severity)
            alerts.append(_make_alert(DILUTION_RISK, current_row, previous_row, expl))
        elif not prev_dil:
            # Newly set to True
            alerts.append(_make_alert(DILUTION_RISK, current_row, previous_row, expl))
        # else: was already True → no repeated alert

    # ── 5. OPTIONS_CONFIRMED ──────────────────────────────────────────────────
    curr_opts_confirmed = curr_opts >= 7.0 or curr_unusual
    prev_opts_confirmed = (
        (prev_opts is not None and prev_opts >= 7.0) or (prev_unusual is True)
    ) if previous_row is not None else None

    if curr_opts_confirmed and previous_row is not None:
        # Never fire on first-time: options spikes without historical context are too noisy
        if not prev_opts_confirmed:
            alerts.append(_make_alert(OPTIONS_CONFIRMED, current_row, previous_row, expl))

    return alerts


_ALERT_CONTEXT: dict[str, str] = {
    EARLY_ARMED_ALERT: "⚠️ WATCH / entry-hunting — early setup forming, lower hit rate than ARMED",
    SQUEEZE_ARMED:     "📋 ARMED — structural setup confirmed, pre-breakout entry watch",
    ACTIVE_SQUEEZE:    "🏃 CHASE RISK — move in progress, not a fresh-entry signal",
}


def format_alert_message(alert: dict) -> str:
    """
    Format a single alert dict into a concise Telegram HTML string.

    Target: ~300–400 chars. No raw JSON. No trade instructions.
    Includes explicit semantic context for EARLY_ARMED and ACTIVE_SQUEEZE.
    """
    alert_type = alert.get("alert_type", "")
    ticker     = alert.get("ticker", "?")
    icon       = _ICONS.get(alert_type, "⚠️")
    severity   = alert.get("severity", "MEDIUM")
    score      = alert.get("final_score")
    state      = alert.get("current_state") or "—"
    prev_state = alert.get("previous_state") or "—"
    risk       = alert.get("risk_level") or "—"
    summary    = alert.get("explanation_summary") or ""
    drivers    = alert.get("top_drivers") or ""
    warnings   = alert.get("warnings") or ""

    score_str  = f"{float(score):.1f}" if score is not None else "—"

    lines = [
        f"{icon} <b>{alert_type}: {ticker}</b>  [{severity}]",
        f"Score: {score_str}  |  State: {state}  |  Risk: {risk}",
    ]
    if prev_state and prev_state not in ("—", "None") and prev_state != state:
        lines.append(f"Transition: {prev_state} → {state}")
    context = _ALERT_CONTEXT.get(alert_type)
    if context:
        lines.append(f"<i>{context}</i>")
    if drivers:
        lines.append(f"Why: {drivers}")
    if warnings:
        lines.append(f"Warnings: {warnings}")
    if summary:
        short = summary[:120] + "…" if len(summary) > 120 else summary
        lines.append(f"<i>{short}</i>")

    return "\n".join(lines)


def format_alerts_section(alerts: list[dict]) -> str:
    """
    Format a list of alert dicts into a complete Telegram section string.
    Returns empty string when alerts is empty (no section added to message).
    HIGH-severity alerts appear first.
    """
    if not alerts:
        return ""

    high   = [a for a in alerts if a.get("severity") == "HIGH"]
    medium = [a for a in alerts if a.get("severity") == "MEDIUM"]
    ordered = high + medium

    lines = [f"\n<b>── SQUEEZE ALERTS ({len(ordered)}) ──</b>"]
    for alert in ordered:
        lines.append("")
        lines.append(format_alert_message(alert))

    return "\n".join(lines)
