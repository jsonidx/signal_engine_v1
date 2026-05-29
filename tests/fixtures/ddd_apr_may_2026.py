"""
tests/fixtures/ddd_apr_may_2026.py
===================================
Static postmortem fixture for DDD (3D Systems) — April–May 2026 squeeze.

## What happened
DDD reached a low near $1.78 on April 7, 2026. Short interest was already
elevated (>= 20% of float) and days-to-cover was elevated (~8–10 days).
An early compression-recovery pattern was forming by mid-April.

The current system (pre-TRD-011) only persisted an ARMED squeeze row on
May 11, 2026 — materially later than the optimal entry window.

## Why EARLY_ARMED should fire earlier
On ~April 15–17, 2026 (roughly 8–10 trading days after the low):
  - SI >= 20% (squeeze fuel already elevated)
  - DTC >= 8 (shorts heavily trapped)
  - Early compression-recovery pattern: drawdown >= 20%, recovery >= 8%
  - Price off low by ~15–20% but NOT yet extended
  - Final score in the 47–53 range (below old ARMED threshold of 55)
  - Volume not yet confirmed (no ignition)

Under TRD-011, these conditions map to EARLY_ARMED — the earliest entry-hunting
state. ARMED fires later on May 11 when the setup fully matures.

## Fixture conventions
- All signal inputs use data known on the simulated signal date (point-in-time safe).
- Prices are approximate reconstructions for test purposes.
- Exact DDD data from yfinance/FINRA is not embedded; these are representative values.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# April 7, 2026 — DDD at the low (not yet detectable)
# ---------------------------------------------------------------------------
APR07_STATE_INPUTS = {
    "ticker": "DDD",
    "signal_date": "2026-04-07",
    # Score below EARLY_ARMED threshold — setup not yet forming
    "final_score": 38.0,
    "short_pct_float": 0.22,
    "computed_dtc_30d": 7.5,
    # No compression-recovery yet — stock just reached the low
    "compression_recovery_score": 0.0,
    "si_persistence_score": 5.0,
    "effective_float_score": 0.0,
    "volume_confirmation_flag": False,
    "recent_squeeze_state": "false",
}
APR07_EXPECTED_STATE = "NOT_SETUP"  # too early, score below 45

# ---------------------------------------------------------------------------
# April 15, 2026 — early compression-recovery forming (first EARLY_ARMED window)
# ---------------------------------------------------------------------------
APR15_STATE_INPUTS = {
    "ticker": "DDD",
    "signal_date": "2026-04-15",
    # Score in EARLY_ARMED range (45–54)
    "final_score": 49.0,
    "short_pct_float": 0.24,        # SI >= 20% → elevated, qualifies for EARLY_ARMED
    "computed_dtc_30d": 8.5,        # DTC >= 5 → early structural driver
    # Early compression-recovery: drawdown ~25%, recovery ~9% — score = 3.0
    "compression_recovery_score": 3.0,
    "si_persistence_score": 5.5,
    "effective_float_score": 0.0,
    "volume_confirmation_flag": False,
    "recent_squeeze_state": "false",
}
APR15_EXPECTED_STATE = "EARLY_ARMED"  # fires materially before May 11 ARMED

APR15_SQUEEZE_ROW = {
    "ticker": "DDD",
    "date": "2026-04-15",
    "final_score": 49.0,
    "squeeze_state": "EARLY_ARMED",
    "short_pct_float": 0.24,
    "computed_dtc_30d": 8.5,
    "compression_recovery_score": 3.0,
    "si_persistence_score": 5.5,
    "volume_confirmation_flag": False,
    "risk_level": "LOW",
    "dilution_risk_flag": False,
    "options_pressure_score": 0.0,
    "unusual_call_activity_flag": False,
    "explanation_summary": (
        "DDD: Early squeeze setup forming — elevated DTC visible. "
        "Watch / entry-hunting state; lower hit rate than ARMED."
    ),
}

# ---------------------------------------------------------------------------
# April 28, 2026 — EARLY_ARMED still active, recovering further
# ---------------------------------------------------------------------------
APR28_STATE_INPUTS = {
    "ticker": "DDD",
    "signal_date": "2026-04-28",
    # Score improving but still below ARMED
    "final_score": 52.5,
    "short_pct_float": 0.25,
    "computed_dtc_30d": 9.0,
    "compression_recovery_score": 4.0,   # improving but not yet ARMED threshold (6.0)
    "si_persistence_score": 6.0,
    "effective_float_score": 0.0,
    "volume_confirmation_flag": False,
    "recent_squeeze_state": "false",
}
APR28_EXPECTED_STATE = "EARLY_ARMED"

# ---------------------------------------------------------------------------
# May 11, 2026 — ARMED fires (existing system reference case)
# ---------------------------------------------------------------------------
MAY11_STATE_INPUTS = {
    "ticker": "DDD",
    "signal_date": "2026-05-11",
    # Score crosses ARMED threshold
    "final_score": 62.0,
    "short_pct_float": 0.27,
    "computed_dtc_30d": 10.0,
    # Full compression-recovery confirmed: drawdown >= 30%, recovery >= 20%
    "compression_recovery_score": 8.0,
    "si_persistence_score": 7.5,
    "effective_float_score": 0.0,
    "volume_confirmation_flag": False,
    "recent_squeeze_state": "false",
}
MAY11_EXPECTED_STATE = "ARMED"  # current reference: ARMED fires here

MAY11_SQUEEZE_ROW = {
    "ticker": "DDD",
    "date": "2026-05-11",
    "final_score": 62.0,
    "squeeze_state": "ARMED",
    "short_pct_float": 0.27,
    "computed_dtc_30d": 10.0,
    "compression_recovery_score": 8.0,
    "si_persistence_score": 7.5,
    "volume_confirmation_flag": False,
    "risk_level": "LOW",
    "dilution_risk_flag": False,
    "options_pressure_score": 2.0,
    "unusual_call_activity_flag": False,
    "explanation_summary": "DDD: Moderate armed squeeze setup — elevated DTC is the lead driver.",
}

# ---------------------------------------------------------------------------
# Timeline summary: EARLY_ARMED fires April 15 vs ARMED fires May 11
# The gap is ~18 trading days — this is the "detection improvement" TRD-011 targets.
# ---------------------------------------------------------------------------
DETECTION_IMPROVEMENT_DAYS = 18  # approximate trading days earlier than ARMED

# ---------------------------------------------------------------------------
# Alert transition test: NOT_SETUP → EARLY_ARMED on April 15
# ---------------------------------------------------------------------------
APR15_PREVIOUS_ROW = {
    "ticker": "DDD",
    "date": "2026-04-14",
    "squeeze_state": "NOT_SETUP",
    "final_score": 42.0,
    "risk_level": "LOW",
    "dilution_risk_flag": False,
    "options_pressure_score": 0.0,
    "unusual_call_activity_flag": False,
}
