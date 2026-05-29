"""
Pattern Watch — case-based similarity scoring against SNOW/CRSR/DELL archetypes.

Probabilities and upside are case-based estimates from a very small historical
sample (n=1 per archetype). This is NOT a statistically validated model.
"""

from typing import Optional

# ─── Archetype baselines (from postmortems) ───────────────────────────────────

ARCHETYPES: dict[str, dict] = {
    "SNOW": {
        "case_upside_pct":      42,   # SNOW May 2026 postmortem
        "case_probability_pct": 67,
        "sample_size":          1,
        "confidence":           "LOW_SAMPLE",
    },
    "CRSR": {
        "case_upside_pct":      35,   # conservative midpoint of 22–48% range
        "case_probability_pct": 60,
        "sample_size":          1,
        "confidence":           "LOW_SAMPLE",
    },
    "DELL": {
        "case_upside_pct":      28,   # conservative end of 7–49% range
        "case_probability_pct": 55,
        "sample_size":          1,
        "confidence":           "LOW_SAMPLE",
    },
}

SIMILARITY_THRESHOLD = 50  # minimum score (0–100) to be included


# ─── Per-archetype scorers ────────────────────────────────────────────────────

def _snow_score(cs: dict, snap: Optional[dict]) -> tuple[int, list[str]]:
    """Pre-earnings catalyst re-rating (SNOW May 2026)."""
    score = 0
    flags: list[str] = []

    # SNOW is NOT a squeeze setup
    if cs.get("post_squeeze_guard"):
        return 0, []

    earnings_score  = float(cs.get("earnings_score")  or 0)
    options_score   = float(cs.get("options_score")   or 0)
    technical_score = float(cs.get("technical_score") or 0)
    volume_score    = float(cs.get("volume_score")    or 0)

    if earnings_score >= 4:
        score += 35
        days = cs.get("days_to_earnings")
        flags.append(f"earnings_imminent_{days}d" if days else "earnings_imminent")
    elif earnings_score >= 3:
        score += 15
        flags.append("earnings_approaching")
    else:
        return 0, []  # need some earnings signal for SNOW

    if options_score >= 4:
        score += 30
        flags.append("call_demand_elevated")
    elif options_score >= 3:
        score += 20
        flags.append("call_demand_elevated")

    if technical_score >= 3:
        score += 20
        flags.append("technical_strength")

    if volume_score >= 3:
        score += 15
        flags.append("volume_expansion_confirmed")

    return min(score, 100), flags


def _crsr_score(cs: dict, snap: Optional[dict]) -> tuple[int, list[str]]:
    """Early catalyst momentum / product-news breakout (CRSR May 2026)."""
    score = 0
    flags: list[str] = []

    options_score   = float(cs.get("options_score")   or 0)
    technical_score = float(cs.get("technical_score") or 0)
    dp_signal       = (cs.get("dark_pool_signal") or "").upper()

    selection_reason = ""
    if snap:
        selection_reason = (snap.get("selection_reason") or "").lower()

    catalyst_kws = [
        "fresh_catalyst_breakout", "catalyst_price_expansion",
        "early_momentum_breakout", "catalyst", "breakout",
    ]
    has_catalyst = any(kw in selection_reason for kw in catalyst_kws)

    if has_catalyst:
        score += 45
        flags.append("fresh_catalyst_breakout")
    else:
        # Without a catalyst flag in snap, CRSR match is weak
        score += 10

    if options_score >= 3:
        score += 25
        flags.append("call_demand_elevated")
    elif options_score >= 2:
        score += 10

    if technical_score >= 3:
        score += 15
        flags.append("technical_strength")

    if dp_signal == "ACCUMULATION":
        score += 15
        flags.append("dark_pool_accumulation")

    return min(score, 100), flags


def _dell_score(cs: dict, snap: Optional[dict]) -> tuple[int, list[str]]:
    """Large-cap early momentum continuation (DELL May 2026)."""
    score = 0
    flags: list[str] = []

    technical_score = float(cs.get("technical_score") or 0)
    volume_score    = float(cs.get("volume_score")    or 0)
    earnings_score  = float(cs.get("earnings_score")  or 0)

    # Strong earnings signal pushes into SNOW territory, not DELL
    if earnings_score >= 4:
        return 0, []

    priority_score = float((snap or {}).get("priority_score") or 0)

    if technical_score >= 4:
        score += 40
        flags.append("technical_strength")
    elif technical_score >= 3:
        score += 20
        flags.append("technical_strength")
    else:
        return 0, []  # need technical momentum for DELL

    if volume_score >= 4:
        score += 30
        flags.append("volume_expansion_confirmed")
    elif volume_score >= 3:
        score += 20
        flags.append("volume_expansion_confirmed")

    if priority_score >= 60:
        score += 20
        flags.append("high_priority_score")
    elif priority_score >= 40:
        score += 10

    return min(score, 100), flags


# ─── Public API ───────────────────────────────────────────────────────────────

def score_ticker(
    ticker: str,
    cs_row: dict,
    snap_row: Optional[dict],
) -> Optional[dict]:
    """
    Score ticker against all archetypes and return the best match,
    or None if no archetype reaches SIMILARITY_THRESHOLD.
    """
    scores = {
        "SNOW": _snow_score(cs_row, snap_row),
        "CRSR": _crsr_score(cs_row, snap_row),
        "DELL": _dell_score(cs_row, snap_row),
    }

    best_pattern = max(scores, key=lambda k: scores[k][0])
    best_score, best_flags = scores[best_pattern]

    if best_score < SIMILARITY_THRESHOLD:
        return None

    archetype = ARCHETYPES[best_pattern]

    reason_map = {
        "SNOW": "Pre-earnings catalyst setup similar to SNOW May 2026",
        "CRSR": "Early catalyst momentum setup similar to CRSR May 2026",
        "DELL": "Large-cap early momentum setup similar to DELL May 2026",
    }

    sources = [] if cs_row.get("_synthetic_from_snapshot") else ["catalyst_scores"]
    if snap_row:
        sources.append("candidate_snapshots")

    raw_comp = float(cs_row.get("raw_composite") or cs_row.get("composite") or 0)
    days_earn = cs_row.get("days_to_earnings")

    return {
        "ticker":               ticker,
        "matched_pattern":      best_pattern,
        "similarity_pct":       best_score,
        "case_probability_pct": archetype["case_probability_pct"],
        "case_upside_pct":      archetype["case_upside_pct"],
        "confidence":           archetype["confidence"],
        "sample_size":          archetype["sample_size"],
        "flags":                list(dict.fromkeys(best_flags)),  # dedup, preserve order
        "reason":               reason_map[best_pattern],
        "source":               sources,
        "current_price":        round(float(cs_row.get("price") or 0), 2),
        "days_to_earnings":     _safe_int(days_earn),
        "raw_score":            round(raw_comp, 1),
    }


def _safe_int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
