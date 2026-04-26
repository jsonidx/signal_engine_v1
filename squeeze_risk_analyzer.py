"""
squeeze_risk_analyzer.py — Risk / exhaustion / dilution scoring MVP (CHUNK-16)

Provides a deterministic, pure-function risk layer for squeeze candidates.

DESIGN PRINCIPLES
─────────────────
• Risk is SEPARATE from final_score — it never subtracts from squeeze quality.
• Risk adds visibility; it does not hard-reject candidates.
• All inputs are optional/nullable; missing data degrades risk estimates conservatively.
• Three risk components: dilution, exhaustion, data-quality.
• Thresholds are manually chosen and conservative; validate with replay (CHUNK-11)
  before adjusting.
• No I/O, no DB calls — pure functions only.

PUBLIC API
──────────
    extract_dilution_info(filing_catalysts, float_shares, as_of_date) -> dict
    compute_squeeze_risk_score(...) -> dict
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── Risk level thresholds ─────────────────────────────────────────────────────
_RISK_LOW = 25        # 0-24   → LOW
_RISK_MEDIUM = 50     # 25-49  → MEDIUM
_RISK_HIGH = 75       # 50-74  → HIGH
                      # 75-100 → EXTREME

# ── Dilution risk constants ────────────────────────────────────────────────────
_DIL_BASE = 35           # points for any confirmed dilution filing
_DIL_LARGE_OFFERING = 20 # additional points when shares_offered/float >= 20%
_DIL_MID_OFFERING = 10   # additional points when shares_offered/float >= 10%
_DIL_RECENT_DAYS = 14    # filing within N days is "recent"
_DIL_RECENT_BONUS = 10   # additional points for recent dilution filing

# ── Exhaustion risk constants ─────────────────────────────────────────────────
_EXH_COMPLETED = 30      # recent squeeze already completed
_EXH_ACTIVE_LOW_SI = 25  # ACTIVE state but SI < 20% (shorts largely covered)
_EXH_LOW_DTC = 15        # ACTIVE state but DTC < 2 (covering demand near end)
_EXH_EXT_HIGH = 20       # price >50% above 30-day low
_EXH_EXT_MID = 10        # price >30% above 30-day low
_EXH_REVERSAL = 15       # price-reversal flag

# Thresholds
_EXH_LOW_SI_THRESHOLD = 0.20    # SI below this while ACTIVE = low remaining pressure
_EXH_LOW_DTC_THRESHOLD = 2.0    # DTC below this while ACTIVE
_EXH_PRICE_EXT_HIGH = 0.50      # 50% above recent low
_EXH_PRICE_EXT_MID = 0.30       # 30% above recent low

# ── Data quality risk constants ───────────────────────────────────────────────
_DQ_FLOAT_CONFIDENCE = 5   # unknown or low effective-float confidence
_DQ_SPARSE_SI = 5          # no SI history records


def extract_dilution_info(
    filing_catalysts: List[dict],
    float_shares: Optional[float] = None,
    as_of_date: Optional[date] = None,
) -> dict:
    """
    Extract dilution-risk inputs from filing_catalysts records.

    Filters for records with dilution_risk_flag=True, finds the most recent,
    and derives shares_offered_pct_float if float_shares is known.

    Returns
    -------
    dict with keys:
        dilution_risk_flag          bool
        shares_offered              int | None
        shares_offered_pct_float    float | None  (0.0–1.0+)
        latest_dilution_filing_date date | None
        days_since_filing           int | None
    """
    _today = as_of_date or date.today()

    if not filing_catalysts:
        return _neutral_dilution()

    def _parse_date(val) -> Optional[date]:
        if val is None:
            return None
        if isinstance(val, date):
            return val
        try:
            from datetime import date as _date
            return _date.fromisoformat(str(val)[:10])
        except (ValueError, TypeError):
            return None

    dilution_records = [
        r for r in filing_catalysts
        if r.get("dilution_risk_flag") is True or bool(r.get("dilution_risk_flag"))
    ]

    if not dilution_records:
        return _neutral_dilution()

    # Find most-recent dilution filing
    dilution_records = sorted(
        dilution_records,
        key=lambda r: _parse_date(r.get("filing_date") or r.get("event_date")) or date.min,
        reverse=True,
    )
    latest = dilution_records[0]
    latest_date = _parse_date(latest.get("filing_date") or latest.get("event_date"))

    # Aggregate shares_offered across all recent filings (sum where available)
    total_shares_offered: Optional[int] = None
    for rec in dilution_records:
        so = rec.get("shares_offered")
        if so is not None:
            try:
                total_shares_offered = (total_shares_offered or 0) + int(so)
            except (TypeError, ValueError):
                pass

    # Compute shares_offered as fraction of float
    shares_pct: Optional[float] = None
    if total_shares_offered and float_shares and float_shares > 0:
        shares_pct = total_shares_offered / float(float_shares)

    # Days since most-recent filing
    days_since: Optional[int] = None
    if latest_date:
        days_since = (_today - latest_date).days

    return {
        "dilution_risk_flag": True,
        "shares_offered": total_shares_offered,
        "shares_offered_pct_float": shares_pct,
        "latest_dilution_filing_date": latest_date,
        "days_since_filing": days_since,
    }


def _neutral_dilution() -> dict:
    return {
        "dilution_risk_flag": False,
        "shares_offered": None,
        "shares_offered_pct_float": None,
        "latest_dilution_filing_date": None,
        "days_since_filing": None,
    }


def compute_squeeze_risk_score(
    squeeze_state: Optional[str] = None,
    final_score: Optional[float] = None,
    short_pct_float: Optional[float] = None,
    computed_dtc_30d: Optional[float] = None,
    volume_confirmation_flag: Optional[bool] = None,
    recent_squeeze_state: Optional[str] = None,
    dilution_risk_flag: Optional[bool] = None,
    derivative_exposure_flag: Optional[bool] = None,
    effective_float_confidence: Optional[str] = None,
    shares_offered_pct_float: Optional[float] = None,
    latest_dilution_filing_date: Optional[date] = None,
    as_of_date: Optional[date] = None,
    si_persistence_count: Optional[int] = None,
    price_extension_pct: Optional[float] = None,
    price_reversal_flag: Optional[bool] = None,
) -> dict:
    """
    Compute a deterministic risk score for a squeeze candidate.

    Risk is ADDITIVE — each component contributes independently.
    Final score is NEVER modified by this function.

    Parameters
    ----------
    squeeze_state           : lifecycle state ("NOT_SETUP" | "ARMED" | "ACTIVE")
    final_score             : composite squeeze score (used for context only)
    short_pct_float         : current SI as fraction of float
    computed_dtc_30d        : float-adjusted days-to-cover
    volume_confirmation_flag: True = recent volume surge observed
    recent_squeeze_state    : raw detection output ("false"/"active"/"completed")
    dilution_risk_flag      : True if any dilution filing detected
    derivative_exposure_flag: True if derivative/convertible exposure present
    effective_float_confidence: "high"/"medium"/"low"/"unknown"
    shares_offered_pct_float: shares_offered / float_shares
    latest_dilution_filing_date: date of most-recent dilution filing
    as_of_date              : reference date for recency checks
    si_persistence_count    : number of SI history records available
    price_extension_pct     : (current_price - recent_low) / recent_low
    price_reversal_flag     : True if price shows reversal pattern

    Returns
    -------
    dict:
        risk_score      float   0-100 (clamped)
        risk_level      str     "LOW" | "MEDIUM" | "HIGH" | "EXTREME"
        risk_flags      list    active risk flag strings
        risk_warnings   list    human-readable risk warning strings
        risk_components dict    per-component point breakdown
    """
    _today = as_of_date or date.today()
    _sq_state = (squeeze_state or "").upper()
    _rss = (recent_squeeze_state or "false").lower()

    risk_flags: List[str] = []
    risk_warnings: List[str] = []
    components: dict = {}

    # ── Component 1: Dilution risk ────────────────────────────────────────────
    dil_pts = 0
    if dilution_risk_flag:
        dil_pts += _DIL_BASE
        risk_flags.append("DILUTION_RISK")
        risk_warnings.append(
            "Dilution filing detected (424B5/S-3/ATM). Share issuance may absorb short-covering demand."
        )

        # Large offering adds incremental risk
        if shares_offered_pct_float is not None:
            if shares_offered_pct_float >= 0.20:
                dil_pts += _DIL_LARGE_OFFERING
                risk_flags.append("LARGE_OFFERING_RISK")
                risk_warnings.append(
                    f"Large offering: {shares_offered_pct_float:.1%} of float. "
                    "High dilution volume may cap squeeze upside."
                )
            elif shares_offered_pct_float >= 0.10:
                dil_pts += _DIL_MID_OFFERING
                risk_warnings.append(
                    f"Mid-size offering: {shares_offered_pct_float:.1%} of float. "
                    "Monitor for dilution impact."
                )

        # Recent filing adds incremental risk
        if latest_dilution_filing_date is not None:
            days_since = (_today - latest_dilution_filing_date).days
            if 0 <= days_since <= _DIL_RECENT_DAYS:
                dil_pts += _DIL_RECENT_BONUS
                risk_flags.append("RECENT_DILUTION_FILING")
                risk_warnings.append(
                    f"Dilution filing is recent ({days_since}d ago). "
                    "Offering may not yet be fully priced in."
                )

    if dilution_risk_flag is not True and derivative_exposure_flag:
        # Derivative exposure without dilution = smaller incremental risk
        dil_pts += 5
        risk_warnings.append(
            "Derivative/convertible exposure present. Potential dilutive instruments."
        )

    components["dilution"] = dil_pts

    # ── Component 2: Exhaustion risk ─────────────────────────────────────────
    exh_pts = 0

    # Completed squeeze is the clearest exhaustion signal
    if _rss == "completed":
        exh_pts += _EXH_COMPLETED
        risk_flags.append("COMPLETED_SQUEEZE_RISK")
        risk_warnings.append(
            "Recent squeeze appears completed — short covering pressure has largely unwound."
        )

    # Active squeeze with low remaining SI
    if _sq_state == "ACTIVE" and short_pct_float is not None and short_pct_float < _EXH_LOW_SI_THRESHOLD:
        exh_pts += _EXH_ACTIVE_LOW_SI
        risk_flags.append("LOW_REMAINING_SHORT_PRESSURE")
        risk_warnings.append(
            f"Squeeze is ACTIVE but remaining SI is low ({short_pct_float:.1%}). "
            "Short covering pressure may be near exhaustion."
        )

    # Active squeeze with low DTC
    if _sq_state == "ACTIVE" and computed_dtc_30d is not None and computed_dtc_30d < _EXH_LOW_DTC_THRESHOLD:
        exh_pts += _EXH_LOW_DTC
        risk_flags.append("LOW_DTC_AFTER_MOVE")
        risk_warnings.append(
            f"Squeeze is ACTIVE but DTC is very low ({computed_dtc_30d:.1f}d). "
            "Shorts may have largely covered."
        )

    # Price extension risk
    if price_extension_pct is not None:
        if price_extension_pct >= _EXH_PRICE_EXT_HIGH:
            exh_pts += _EXH_EXT_HIGH
            risk_flags.append("PRICE_EXTENSION_RISK")
            risk_warnings.append(
                f"Price is {price_extension_pct:.0%} above 30-day low — "
                "late entry carries elevated extension risk."
            )
        elif price_extension_pct >= _EXH_PRICE_EXT_MID:
            exh_pts += _EXH_EXT_MID
            risk_flags.append("PRICE_EXTENSION_RISK")
            risk_warnings.append(
                f"Price is {price_extension_pct:.0%} above 30-day low — "
                "moderate extension; position sizing caution warranted."
            )

    # High-volume price reversal
    if price_reversal_flag:
        exh_pts += _EXH_REVERSAL
        risk_flags.append("REVERSAL_RISK")
        risk_warnings.append(
            "Volume-driven price reversal detected — potential squeeze exhaustion signal."
        )

    components["exhaustion"] = exh_pts

    # ── Component 3: Data-quality risk ───────────────────────────────────────
    dq_pts = 0

    if effective_float_confidence in ("unknown", "low"):
        dq_pts += _DQ_FLOAT_CONFIDENCE
        risk_flags.append("LOW_EFFECTIVE_FLOAT_CONFIDENCE")
        risk_warnings.append(
            "Effective float confidence is low/unknown — actual short pressure may differ from estimates."
        )

    if si_persistence_count is not None and si_persistence_count == 0:
        dq_pts += _DQ_SPARSE_SI
        risk_flags.append("SPARSE_SI_HISTORY")
        risk_warnings.append(
            "No SI history available — cannot confirm short interest trend or persistence."
        )

    components["data_quality"] = dq_pts

    # ── Aggregate and map to level ────────────────────────────────────────────
    raw_total = dil_pts + exh_pts + dq_pts
    risk_score = float(min(max(raw_total, 0), 100))

    if risk_score >= _RISK_HIGH:
        risk_level = "EXTREME"
    elif risk_score >= _RISK_MEDIUM:
        risk_level = "HIGH"
    elif risk_score >= _RISK_LOW:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    # Deduplicate flags (preserve order)
    seen: set = set()
    deduped_flags = []
    for f in risk_flags:
        if f not in seen:
            seen.add(f)
            deduped_flags.append(f)

    return {
        "risk_score": risk_score,
        "risk_level": risk_level,
        "risk_flags": deduped_flags,
        "risk_warnings": risk_warnings,
        "risk_components": components,
    }


def risk_level_from_score(risk_score: float) -> str:
    """Map a raw risk score to its level label."""
    if risk_score >= _RISK_HIGH:
        return "EXTREME"
    if risk_score >= _RISK_MEDIUM:
        return "HIGH"
    if risk_score >= _RISK_LOW:
        return "MEDIUM"
    return "LOW"
