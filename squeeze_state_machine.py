"""
squeeze_state_machine.py — 3-state squeeze lifecycle classifier (CHUNK-10)

Conservative, deterministic lifecycle labeling for squeeze setups.

STATES
──────
    NOT_SETUP   Setup quality is weak or structural preconditions are absent.
    ARMED       Structural squeeze preconditions met; ignition not yet confirmed.
    ACTIVE      Squeeze mechanics appear live (recent price run with trapped shorts,
                or volume-confirmed ignition alongside structural pressure).

DESIGN PRINCIPLES
─────────────────
• Pure function — no I/O, no DB calls.
• All inputs are optional/nullable; missing data degrades confidence, never crashes.
• `recent_squeeze_state = "completed"` maps to NOT_SETUP with an explicit warning.
• Thresholds are conservative and manually chosen; use replay (CHUNK-11) to
  validate before changing them.
• EXHAUSTION_RISK intentionally excluded — deferred to CHUNK-16.
• Full six-state lifecycle deferred; validation must confirm ARMED/ACTIVE
  separation before adding more states.

PUBLIC API
──────────
    classify_squeeze_state(final_score, short_pct_float, computed_dtc_30d,
                           compression_recovery_score, effective_float_score,
                           si_persistence_score, volume_confirmation_flag,
                           recent_squeeze_state) -> dict
"""

from __future__ import annotations

from typing import Optional

# ── Thresholds (single source of truth — do not duplicate in other modules) ───

_SCORE_MIN_ARMED = 55.0          # final_score floor for ARMED or ACTIVE
_SCORE_MIN_SETUP = 45.0          # final_score below this → always NOT_SETUP
_SCORE_HIGH_OVERRIDE = 75.0      # override low-SI NOT_SETUP when score is very high

_SI_MIN_SETUP = 0.15             # short_pct_float below this → NOT_SETUP (unless high score)
_SI_MIN_STRUCTURAL = 0.30        # structural driver threshold
_SI_MIN_ACTIVE = 0.30            # minimum SI for volume-confirmed ACTIVE

_DTC_STRUCTURAL = 5.0            # computed_dtc_30d structural driver threshold
_COMP_REC_STRUCTURAL = 6.0       # compression_recovery_score structural driver threshold
_EF_STRUCTURAL = 6.0             # effective_float_score structural driver threshold
_SI_PERSIST_STRUCTURAL = 7.0     # si_persistence_score structural driver threshold

_COMPLETED_SI_FLOOR = 0.20       # recent completed squeeze + SI below this → NOT_SETUP

_STRUCTURAL_MIN_ARMED = 2        # minimum structural drivers for ARMED
_STRUCTURAL_MIN_ACTIVE_VOL = 1   # minimum structural drivers for volume-confirmed ACTIVE


def classify_squeeze_state(
    final_score: float,
    short_pct_float: Optional[float] = None,
    computed_dtc_30d: Optional[float] = None,
    compression_recovery_score: Optional[float] = None,
    effective_float_score: Optional[float] = None,
    si_persistence_score: Optional[float] = None,
    volume_confirmation_flag: Optional[bool] = None,
    recent_squeeze_state: Optional[str] = None,
) -> dict:
    """
    Classify the squeeze lifecycle state from signal inputs.

    Parameters
    ----------
    final_score              : 0–100 composite squeeze score (already computed)
    short_pct_float          : short interest as fraction of float (e.g. 0.35 = 35%)
    computed_dtc_30d         : float-adjusted days-to-cover (SI×float / avg_vol_30d)
    compression_recovery_score: 0–10 price compression + recovery pattern score
    effective_float_score    : 0–10 effective-float-adjusted SI score
    si_persistence_score     : 0–10 short interest persistence score
    volume_confirmation_flag : True if recent volume surge confirms ignition
    recent_squeeze_state     : raw detect_recent_squeeze() output:
                               "false" | "active" | "completed"

    Returns
    -------
    dict with keys:
        state            : "NOT_SETUP" | "ARMED" | "ACTIVE"
        state_confidence : "low" | "medium" | "high"
        state_reasons    : list[str]   — factors leading to this state
        state_warnings   : list[str]   — caveats / risks
    """
    reasons: list[str] = []
    warnings: list[str] = []

    # Normalise optional inputs
    si = float(short_pct_float) if short_pct_float is not None else None
    dtc = float(computed_dtc_30d) if computed_dtc_30d is not None else None
    comp_rec = float(compression_recovery_score) if compression_recovery_score is not None else None
    ef = float(effective_float_score) if effective_float_score is not None else None
    si_persist = float(si_persistence_score) if si_persistence_score is not None else None
    vol_confirmed = bool(volume_confirmation_flag) if volume_confirmation_flag is not None else False
    rss = (recent_squeeze_state or "false").lower()

    # Count known inputs for confidence estimation
    known_count = sum(v is not None for v in [si, dtc, comp_rec, ef, si_persist])

    # ── Structural driver evaluation ──────────────────────────────────────────
    structural_drivers: list[str] = []
    if si is not None and si >= _SI_MIN_STRUCTURAL:
        structural_drivers.append(f"Short interest {si:.1%} ≥ {_SI_MIN_STRUCTURAL:.0%}")
    if dtc is not None and dtc >= _DTC_STRUCTURAL:
        structural_drivers.append(f"Computed DTC {dtc:.1f} ≥ {_DTC_STRUCTURAL:.0f}")
    if comp_rec is not None and comp_rec >= _COMP_REC_STRUCTURAL:
        structural_drivers.append(f"Compression/recovery score {comp_rec:.1f} ≥ {_COMP_REC_STRUCTURAL:.0f}")
    if ef is not None and ef >= _EF_STRUCTURAL:
        structural_drivers.append(f"Effective float score {ef:.1f} ≥ {_EF_STRUCTURAL:.0f}")
    if si_persist is not None and si_persist >= _SI_PERSIST_STRUCTURAL:
        structural_drivers.append(f"SI persistence score {si_persist:.1f} ≥ {_SI_PERSIST_STRUCTURAL:.0f}")

    n_struct = len(structural_drivers)

    # ── ACTIVE: highest priority ───────────────────────────────────────────────

    # Case 1: recent_squeeze detection says active (shorts still trapped)
    if rss == "active":
        reasons.append("Recent price run ≥50% with short interest still ≥30% — shorts remain trapped.")
        confidence = _confidence_active(n_struct, vol_confirmed, known_count, rss="active")
        return _result("ACTIVE", confidence, reasons, warnings)

    # Case 2: volume-confirmed ignition with structural pressure
    if (
        final_score >= _SCORE_MIN_ARMED
        and si is not None and si >= _SI_MIN_ACTIVE
        and vol_confirmed
        and n_struct >= _STRUCTURAL_MIN_ACTIVE_VOL
    ):
        reasons.append(f"Volume confirmation with structural pressure ({n_struct} driver(s) present).")
        reasons.extend(structural_drivers[:3])
        confidence = _confidence_active(n_struct, vol_confirmed, known_count, rss=rss)
        return _result("ACTIVE", confidence, reasons, warnings)

    # ── Completed recent squeeze warning (before further checks) ──────────────
    completed_squeeze = (rss == "completed")
    if completed_squeeze:
        warnings.append("Recent squeeze appears completed — structural reset expected.")

    # ── NOT_SETUP: hard disqualifiers ─────────────────────────────────────────

    # Low score (always NOT_SETUP regardless of other inputs)
    if final_score < _SCORE_MIN_SETUP:
        reasons.append(f"Final score {final_score:.1f} below minimum setup threshold ({_SCORE_MIN_SETUP:.0f}).")
        return _result("NOT_SETUP", _confidence_not_setup(known_count, clear=True), reasons, warnings)

    # Completed squeeze with low remaining SI
    if completed_squeeze and (si is None or si < _COMPLETED_SI_FLOOR):
        si_display = f"{si:.1%}" if si is not None else "unknown"
        reasons.append(
            f"Recent squeeze completed and current SI ({si_display}) is below reset floor "
            f"({_COMPLETED_SI_FLOOR:.0%})."
        )
        return _result("NOT_SETUP", _confidence_not_setup(known_count, clear=True), reasons, warnings)

    # Low short interest (primary fuel absent), unless score is high for other reasons
    if si is not None and si < _SI_MIN_SETUP and final_score < _SCORE_HIGH_OVERRIDE:
        reasons.append(
            f"Short interest {si:.1%} below minimum setup level ({_SI_MIN_SETUP:.0%}); "
            f"squeeze fuel is insufficient."
        )
        return _result("NOT_SETUP", _confidence_not_setup(known_count, clear=True), reasons, warnings)

    # ── ARMED: structural setup without ignition ──────────────────────────────
    if (
        final_score >= _SCORE_MIN_ARMED
        and n_struct >= _STRUCTURAL_MIN_ARMED
        and not vol_confirmed
        and rss != "active"
    ):
        reasons.append(f"Structural squeeze setup present ({n_struct} driver(s)) without ignition confirmation.")
        reasons.extend(structural_drivers)
        if completed_squeeze:
            reasons.append("Note: recent squeeze completed; setup may be a re-accumulation phase.")
        confidence = _confidence_armed(n_struct, known_count)
        return _result("ARMED", confidence, reasons, warnings)

    # ── Fallthrough: NOT_SETUP ────────────────────────────────────────────────
    if final_score >= _SCORE_MIN_ARMED and n_struct < _STRUCTURAL_MIN_ARMED:
        reasons.append(
            f"Score {final_score:.1f} meets threshold but only {n_struct} structural driver(s) "
            f"present (minimum {_STRUCTURAL_MIN_ARMED} required for ARMED)."
        )
    elif final_score < _SCORE_MIN_ARMED:
        reasons.append(f"Final score {final_score:.1f} below ARMED threshold ({_SCORE_MIN_ARMED:.0f}).")
    else:
        reasons.append("Setup conditions not met.")

    return _result("NOT_SETUP", _confidence_not_setup(known_count, clear=False), reasons, warnings)


# ── Confidence helpers ────────────────────────────────────────────────────────

def _confidence_active(
    n_struct: int,
    vol_confirmed: bool,
    known_count: int,
    rss: str,
) -> str:
    if known_count <= 1:
        return "low"
    if rss == "active" and n_struct >= 2:
        return "high"
    if rss == "active":
        return "medium"
    # volume-confirmed path
    if n_struct >= 3 and known_count >= 3:
        return "high"
    return "medium"


def _confidence_armed(n_struct: int, known_count: int) -> str:
    if known_count <= 1:
        return "low"
    if n_struct >= 3 and known_count >= 3:
        return "high"
    if n_struct >= 2:
        return "medium"
    return "low"


def _confidence_not_setup(known_count: int, clear: bool) -> str:
    if known_count <= 1:
        return "low"
    return "medium" if clear else "low"


def _result(state: str, confidence: str, reasons: list, warnings: list) -> dict:
    return {
        "state": state,
        "state_confidence": confidence,
        "state_reasons": reasons,
        "state_warnings": warnings,
    }
