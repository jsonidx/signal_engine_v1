"""
effective_float_analyzer.py — Simplified effective-float estimator (CHUNK-06)

Uses EDGAR-derived 13G/13D large-holder records from the filing_catalysts table
to estimate how much of the reported float may be controlled by concentrated
holders, and computes an adjusted short-float ratio that can indicate a tighter
squeeze setup than the raw SI% implies.

CONSERVATIVE DESIGN PRINCIPLES
───────────────────────────────
• Large-holder shares may still be lent; we do not assume all are locked.
• Derivative exposure (warrants, options, swaps) is flagged but NOT added to
  beneficial ownership — the holder may or may not control the underlying shares.
• 10% float floor prevents effective float from collapsing to zero or near-zero,
  which would otherwise produce infinite effective-SI ratios on sparse data.
• When no usable records exist the module returns neutral values and never
  penalises the ticker.
• All functions are pure (no I/O); DB access is a thin wrapper in supabase_persist.

PUBLIC API
──────────
    analyze_effective_float(ticker, reported_float, shares_outstanding, large_holder_records)
    normalize_large_holder_records(records, shares_outstanding)
    compute_large_holder_ownership_pct(normalized_records)
    estimate_effective_float(reported_float, large_holder_shares)
    compute_effective_short_float_ratio(shares_short, effective_float_estimate)
    compute_effective_float_score(effective_short_float_ratio)
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

# Concentration thresholds
_EXTREME_LOCK_PCT = 50.0       # large_holder_ownership_pct threshold for extreme flag
_EXTREME_LOCK_RATIO = 0.50     # estimated_locked / reported_float threshold for extreme flag
_CONCENTRATION_PCT = 30.0      # threshold for large_holder_concentration_flag
_FLOAT_FLOOR_PCT = 0.10        # effective float never drops below 10% of reported float

# Ownership filing types this module trusts
_OWNERSHIP_FORM_PREFIXES = ("SC 13D", "SC 13G")


# ==============================================================================
# SECTION 1: RECORD NORMALISATION
# ==============================================================================

def normalize_large_holder_records(
    records: list[dict],
    shares_outstanding: Optional[float],
) -> list[dict]:
    """
    Filter, clean, and deduplicate large-holder records.

    Rules applied:
    1.  Keep only records with ownership_accumulation_flag=True OR whose
        filing_type starts with SC 13D / SC 13G.
    2.  Back-fill pct_class from shares_beneficially_owned / shares_outstanding
        when pct_class is missing.
    3.  Deduplicate: keep the most-recent filing per holder_name.
        If holder_name is missing, treat each accession_number as its own holder.

    Returns a list of cleaned dicts, one per distinct holder, sorted
    oldest-to-newest by filing_date.
    """
    if not records:
        return []

    def _is_ownership(rec: dict) -> bool:
        ft = str(rec.get("filing_type") or "").upper()
        flag = bool(rec.get("ownership_accumulation_flag"))
        return flag or any(ft.startswith(p) for p in _OWNERSHIP_FORM_PREFIXES)

    def _parse_date(val) -> Optional[date]:
        if val is None:
            return None
        if isinstance(val, date):
            return val
        try:
            return date.fromisoformat(str(val)[:10])
        except (ValueError, TypeError):
            return None

    # Step 1: filter to ownership records
    ownership = [r for r in records if _is_ownership(r)]
    if not ownership:
        return []

    # Step 2: back-fill pct_class where missing
    normalised: list[dict] = []
    for rec in ownership:
        r = dict(rec)  # shallow copy — do not mutate caller's data
        if r.get("pct_class") is None:
            sbo = r.get("shares_beneficially_owned")
            if sbo is not None and shares_outstanding and shares_outstanding > 0:
                r["pct_class"] = (float(sbo) / float(shares_outstanding)) * 100.0
        normalised.append(r)

    # Step 3: deduplicate — keep newest filing per holder
    # Key: holder_name if present, else accession_number, else row index (unique)
    holder_latest: dict[str, dict] = {}
    for idx, rec in enumerate(normalised):
        name = (rec.get("holder_name") or "").strip()
        accession = (rec.get("accession_number") or "").strip()
        key = name if name else (accession if accession else f"__anon_{idx}__")
        existing = holder_latest.get(key)
        if existing is None:
            holder_latest[key] = rec
        else:
            # Keep the one with the later filing_date
            d_new = _parse_date(rec.get("filing_date")) or date.min
            d_old = _parse_date(existing.get("filing_date")) or date.min
            if d_new >= d_old:
                holder_latest[key] = rec

    deduped = sorted(
        holder_latest.values(),
        key=lambda r: _parse_date(r.get("filing_date")) or date.min,
    )
    return deduped


# ==============================================================================
# SECTION 2: OWNERSHIP PCT AGGREGATION
# ==============================================================================

def compute_large_holder_ownership_pct(
    normalized_records: list[dict],
    reported_float: Optional[float] = None,
    shares_outstanding: Optional[float] = None,
) -> dict:
    """
    Sum pct_class across all de-duplicated holders.

    Returns:
        {
            "large_holder_ownership_pct": float,    # 0–100 (or higher if data is off)
            "large_holder_shares": float,           # best-effort share count
            "has_any_pct": bool,                    # at least one pct_class available
        }
    """
    if not normalized_records:
        return {
            "large_holder_ownership_pct": 0.0,
            "large_holder_shares": 0.0,
            "has_any_pct": False,
        }

    total_pct = 0.0
    total_shares = 0.0
    has_pct = False

    for rec in normalized_records:
        pct = rec.get("pct_class")
        sbo = rec.get("shares_beneficially_owned")

        if pct is not None:
            try:
                total_pct += float(pct)
                has_pct = True
            except (TypeError, ValueError):
                pass

        if sbo is not None:
            try:
                total_shares += float(sbo)
            except (TypeError, ValueError):
                pass

    # If we only have share counts but no pct_class, derive pct from shares
    if not has_pct and total_shares > 0:
        denom = shares_outstanding or reported_float
        if denom and float(denom) > 0:
            total_pct = (total_shares / float(denom)) * 100.0

    return {
        "large_holder_ownership_pct": round(total_pct, 4),
        "large_holder_shares": round(total_shares, 0),
        "has_any_pct": has_pct,
    }


# ==============================================================================
# SECTION 3: EFFECTIVE FLOAT ESTIMATION
# ==============================================================================

def estimate_effective_float(
    reported_float: Optional[float],
    large_holder_shares: float,
    large_holder_ownership_pct: float = 0.0,
    shares_outstanding: Optional[float] = None,
) -> dict:
    """
    Estimate how much float remains freely tradeable after large-holder positions.

    Conservative rules:
    - estimated_locked_float = min(large_holder_shares, reported_float)
      (cap at reported float so we never remove more than 100%)
    - effective_float_estimate = max(reported_float - estimated_locked_float,
                                     reported_float * FLOOR_PCT)
      (10% floor prevents infinite ratios on sparse or noisy data)

    If reported_float is missing, falls back to shares_outstanding * 0.85 as a
    rough proxy, or 0 if that too is missing.

    Returns a dict with:
        estimated_locked_float, effective_float_estimate,
        extreme_float_lock_flag, large_holder_concentration_flag, float_floor_applied
    """
    # Resolve reported_float
    rf: float = 0.0
    if reported_float is not None and float(reported_float) > 0:
        rf = float(reported_float)
    elif shares_outstanding is not None and float(shares_outstanding) > 0:
        rf = float(shares_outstanding) * 0.85   # rough proxy

    if rf <= 0:
        return {
            "estimated_locked_float": 0.0,
            "effective_float_estimate": 0.0,
            "extreme_float_lock_flag": False,
            "large_holder_concentration_flag": False,
            "float_floor_applied": False,
        }

    # Locked shares: use share count if available, else derive from pct
    if large_holder_shares > 0:
        locked = large_holder_shares
    elif large_holder_ownership_pct > 0:
        locked = (large_holder_ownership_pct / 100.0) * rf
    else:
        locked = 0.0

    locked = min(locked, rf)   # cap at reported float
    floor = rf * _FLOAT_FLOOR_PCT
    raw_effective = rf - locked
    floor_applied = raw_effective < floor
    effective = max(raw_effective, floor)

    lock_ratio = locked / rf if rf > 0 else 0.0
    extreme = (
        large_holder_ownership_pct >= _EXTREME_LOCK_PCT
        or lock_ratio >= _EXTREME_LOCK_RATIO
    )
    concentration = large_holder_ownership_pct >= _CONCENTRATION_PCT

    return {
        "estimated_locked_float": round(locked, 0),
        "effective_float_estimate": round(effective, 0),
        "extreme_float_lock_flag": extreme,
        "large_holder_concentration_flag": concentration,
        "float_floor_applied": floor_applied,
    }


# ==============================================================================
# SECTION 4: SHORT-FLOAT RATIO AND SCORING
# ==============================================================================

def compute_effective_short_float_ratio(
    shares_short: Optional[float],
    effective_float_estimate: float,
) -> float:
    """
    effective_short_float_ratio = shares_short / effective_float_estimate.

    Returns 0.0 if either input is missing or effective_float_estimate <= 0.
    """
    if not shares_short or shares_short <= 0:
        return 0.0
    if effective_float_estimate <= 0:
        return 0.0
    return round(float(shares_short) / float(effective_float_estimate), 4)


def compute_effective_float_score(effective_short_float_ratio: float) -> float:
    """
    Map effective_short_float_ratio to a 0–10 signal score.

    Thresholds reflect how difficult short covering would be relative to
    the freely-tradeable supply:
        >= 1.00  -> 10  (shares short exceed the entire effective float)
        >= 0.75  ->  9
        >= 0.50  ->  8
        >= 0.35  ->  6
        >= 0.20  ->  4
        >= 0.10  ->  2
        otherwise -> 0

    Returns 0 if ratio is 0 (no data / no effective float data).
    """
    r = effective_short_float_ratio
    if r <= 0:
        return 0.0
    if r >= 1.00:
        return 10.0
    if r >= 0.75:
        return 9.0
    if r >= 0.50:
        return 8.0
    if r >= 0.35:
        return 6.0
    if r >= 0.20:
        return 4.0
    if r >= 0.10:
        return 2.0
    return 0.0


# ==============================================================================
# SECTION 5: CONFIDENCE ASSESSMENT
# ==============================================================================

def _assess_confidence(
    normalized_records: list[dict],
    shares_outstanding: Optional[float],
) -> str:
    """
    Return a confidence level for the effective-float estimate.

    high    — at least one record with both pct_class and holder_name
    medium  — share counts available and shares_outstanding known
    low     — some records but sparse/ambiguous
    unknown — no usable records
    """
    if not normalized_records:
        return "unknown"

    for rec in normalized_records:
        if rec.get("pct_class") is not None and rec.get("holder_name"):
            return "high"

    # medium: can compute pct_class from shares + shares_outstanding
    for rec in normalized_records:
        if rec.get("shares_beneficially_owned") is not None:
            if shares_outstanding and shares_outstanding > 0:
                return "medium"

    return "low"


# ==============================================================================
# SECTION 6: TOP-LEVEL ANALYSER
# ==============================================================================

def analyze_effective_float(
    ticker: str,
    reported_float: Optional[float],
    shares_outstanding: Optional[float],
    large_holder_records: list[dict],
) -> dict:
    """
    Compute all effective-float metrics from large-holder filing records.

    This is the primary entry point.  All sub-functions are pure and can be
    unit-tested independently.

    Parameters
    ----------
    ticker              : ticker symbol (used for logging only)
    reported_float      : float shares from yfinance/broker
    shares_outstanding  : total shares outstanding (used for pct_class back-fill)
    large_holder_records: raw rows from filing_catalysts (any subset is safe)

    Returns
    -------
    dict with keys:
        effective_float_estimate        float
        large_holder_ownership_pct      float
        large_holder_shares             float
        estimated_locked_float          float
        effective_short_float_ratio     float   (0 until shares_short is injected)
        effective_float_score           float   (0–10)
        extreme_float_lock_flag         bool
        large_holder_concentration_flag bool
        float_floor_applied             bool
        effective_float_confidence      str     (high/medium/low/unknown)
        derivative_exposure_present     bool    (informational, not modeled)
        record_count                    int
    """
    _rf = float(reported_float) if (reported_float is not None and reported_float > 0) else 0.0
    _so = float(shares_outstanding) if (shares_outstanding is not None and shares_outstanding > 0) else 0.0

    # Neutral result returned when there are no records
    neutral: dict = {
        "effective_float_estimate": _rf or _so * 0.85 or 0.0,
        "large_holder_ownership_pct": 0.0,
        "large_holder_shares": 0.0,
        "estimated_locked_float": 0.0,
        "effective_short_float_ratio": 0.0,
        "effective_float_score": 0.0,
        "extreme_float_lock_flag": False,
        "large_holder_concentration_flag": False,
        "float_floor_applied": False,
        "effective_float_confidence": "unknown",
        "derivative_exposure_present": False,
        "record_count": 0,
    }

    if not large_holder_records:
        return neutral

    # 1. Normalise and deduplicate
    normalised = normalize_large_holder_records(large_holder_records, _so or None)
    if not normalised:
        return neutral

    # 2. Aggregate ownership
    ownership = compute_large_holder_ownership_pct(normalised, _rf or None, _so or None)

    # 3. Estimate effective float
    ef = estimate_effective_float(
        reported_float=_rf or None,
        large_holder_shares=ownership["large_holder_shares"],
        large_holder_ownership_pct=ownership["large_holder_ownership_pct"],
        shares_outstanding=_so or None,
    )

    # 4. Confidence
    confidence = _assess_confidence(normalised, _so or None)

    # 5. Derivative exposure flag (informational only — not modeled)
    derivative_present = any(r.get("derivative_exposure_flag") for r in normalised)

    return {
        "effective_float_estimate": ef["effective_float_estimate"],
        "large_holder_ownership_pct": ownership["large_holder_ownership_pct"],
        "large_holder_shares": ownership["large_holder_shares"],
        "estimated_locked_float": ef["estimated_locked_float"],
        "effective_short_float_ratio": 0.0,   # caller injects shares_short
        "effective_float_score": 0.0,          # caller injects after shares_short
        "extreme_float_lock_flag": ef["extreme_float_lock_flag"],
        "large_holder_concentration_flag": ef["large_holder_concentration_flag"],
        "float_floor_applied": ef["float_floor_applied"],
        "effective_float_confidence": confidence,
        "derivative_exposure_present": derivative_present,
        "record_count": len(normalised),
    }
