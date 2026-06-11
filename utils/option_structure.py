"""
Option Structure Selection Policy by Thesis Tempo  (TRD-048)
================================================================================
Deterministic archetype classification: maps thesis tempo and setup type to
the appropriate option structure family before final contract scoring.

The archetype policy runs before the scoring loop so the candidate engine
narrows the valid structure family first and then ranks within it.

Flow (called from get_option_candidates):
  ThesisContext
      → classify_structure_archetype()    # horizon + event signals → archetype
      → StructurePolicy                   # DTE, spread, LEAPS eligibility rules
      → apply_policy_to_preset()          # tighten each preset's hard filters

Archetypes:
  short_breakout   — ≤ 14-day horizon; short-dated options only (14–35 DTE)
  medium_swing     — 15–42-day horizon; standard swing range (21–60 DTE)
  slow_macro       — > 42-day horizon; LEAPS preferred (45–60 DTE swing fallback)
  event_sensitive  — earnings 3–14 days out; tight DTE + tighter spread (14–45 DTE)
  default_swing    — unclear/missing tempo; conservative defaults (14–60 DTE)

LLMs must NOT call this module to choose structure family.
No orders are placed here.
================================================================================
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

_STRUCTURE_VERSION = "v1.0"


# ──────────────────────────────────────────────────────────────────────────────
# StructurePolicy
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StructurePolicy:
    """
    Archetype-derived structure constraints applied before contract scoring.

    Fields:
        archetype         — one of: short_breakout, medium_swing, slow_macro,
                            event_sensitive, default_swing
        reason            — human-readable explanation for the chosen archetype
        allow_leaps       — whether LEAPS presets (180–560 DTE) are eligible
        prefer_leaps      — LEAPS listed before swing presets when True
        swing_dte_min     — minimum DTE enforced on swing presets (long_call/long_put)
        swing_dte_max     — maximum DTE enforced on swing presets
        max_spread_pct    — spread cap applied to all active presets; may be
                            stricter than the preset default (event_sensitive uses 10%)
        structure_version — policy version string for analytics lineage
    """
    archetype: str
    reason: str
    allow_leaps: bool
    prefer_leaps: bool
    swing_dte_min: int
    swing_dte_max: int
    max_spread_pct: float
    structure_version: str = _STRUCTURE_VERSION


# ──────────────────────────────────────────────────────────────────────────────
# Policy table  (frozen dataclasses are hashable; defined once at module load)
# ──────────────────────────────────────────────────────────────────────────────

POLICIES: Dict[str, StructurePolicy] = {
    "short_breakout": StructurePolicy(
        archetype="short_breakout",
        reason=(
            "Short-term breakout/momentum (≤14-day horizon): "
            "near-dated options (14–35 DTE) to limit time-value cost; "
            "LEAPS inappropriate for this tempo"
        ),
        allow_leaps=False,
        prefer_leaps=False,
        swing_dte_min=14,
        swing_dte_max=35,
        max_spread_pct=12.0,
    ),
    "medium_swing": StructurePolicy(
        archetype="medium_swing",
        reason=(
            "Medium swing (2–6-week horizon): "
            "standard option range (21–60 DTE); "
            "LEAPS allowed as secondary for high-conviction setups"
        ),
        allow_leaps=True,
        prefer_leaps=False,
        swing_dte_min=21,
        swing_dte_max=60,
        max_spread_pct=12.0,
    ),
    "slow_macro": StructurePolicy(
        archetype="slow_macro",
        reason=(
            "Slow macro/thematic (> 6-week horizon): "
            "LEAPS preferred for duration and lower theta drag; "
            "swing options accepted at 45–60 DTE as fallback"
        ),
        allow_leaps=True,
        prefer_leaps=True,
        swing_dte_min=45,
        swing_dte_max=60,
        max_spread_pct=15.0,
    ),
    "event_sensitive": StructurePolicy(
        archetype="event_sensitive",
        reason=(
            "Event-sensitive setup (earnings 3–14 days out): "
            "restrict to 14–45 DTE to bound IV-crush risk; "
            "tighter spread (≤10%) required; LEAPS excluded"
        ),
        allow_leaps=False,
        prefer_leaps=False,
        swing_dte_min=14,
        swing_dte_max=45,
        max_spread_pct=10.0,
    ),
    "default_swing": StructurePolicy(
        archetype="default_swing",
        reason=(
            "Thesis tempo unclear or missing: "
            "conservative default (14–60 DTE); "
            "standard spread; LEAPS allowed"
        ),
        allow_leaps=True,
        prefer_leaps=False,
        swing_dte_min=14,
        swing_dte_max=60,
        max_spread_pct=12.0,
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# Horizon parsing
# ──────────────────────────────────────────────────────────────────────────────


def parse_horizon_days(horizon: Optional[str]) -> Optional[int]:
    """
    Parse a free-text time horizon string into an upper-bound estimate in days.
    Returns None when the input is absent or unrecognisable.

    Recognised patterns:
        "N days" / "N–M days"     → M days (upper bound)
        "N weeks" / "N–M weeks"   → M × 7
        "N months" / "N–M months" → M × 30
        No explicit unit, number ≤ 12 → treat as weeks (× 7)
        No explicit unit, number > 12 → treat as days

    Keyword fallbacks (no numbers present):
        short / quick / fast / momentum / breakout → 10 days
        medium / swing → 30 days
        long / macro / thematic / leaps → 120 days
    """
    if not horizon:
        return None

    text = horizon.lower().strip()

    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]

    if not nums:
        # Keyword-only hints
        if any(w in text for w in ("short", "quick", "fast", "momentum", "breakout")):
            return 10
        if any(w in text for w in ("medium", "swing")):
            return 30
        if any(w in text for w in ("long", "macro", "thematic", "leaps")):
            return 120
        return None

    upper = max(nums)

    if "month" in text:
        return int(upper * 30)
    if "week" in text:
        return int(upper * 7)
    if "day" in text:
        return int(upper)

    # No explicit unit: treat small numbers as weeks, large as days
    if upper <= 12:
        return int(upper * 7)
    return int(upper)


# ──────────────────────────────────────────────────────────────────────────────
# Archetype classification
# ──────────────────────────────────────────────────────────────────────────────


def classify_structure_archetype(thesis: Any) -> StructurePolicy:
    """
    Classify a ThesisContext into a deterministic StructurePolicy.

    Priority order:
        1. Event sensitivity — earnings 3–14 days out overrides tempo
        2. Parsed time horizon — drives short / medium / macro classification
        3. Default swing — when tempo is unknown or ambiguous

    Args:
        thesis: ThesisContext (typed loosely to avoid circular import)

    Returns:
        StructurePolicy for the chosen archetype.
        Never raises; always returns a valid policy.
    """
    try:
        days_to_earnings = getattr(thesis, "days_to_earnings", None)
        time_horizon = getattr(thesis, "time_horizon", None)

        # 1. Event sensitivity (earnings approaching but not already blocked by suppression)
        if days_to_earnings is not None and 3 <= int(days_to_earnings) <= 14:
            return POLICIES["event_sensitive"]

        # 2. Parse time horizon to days
        horizon_days = parse_horizon_days(time_horizon)

        if horizon_days is not None:
            if horizon_days <= 14:
                return POLICIES["short_breakout"]
            if horizon_days <= 42:   # up to 6 weeks
                return POLICIES["medium_swing"]
            # > 42 days → slow macro
            return POLICIES["slow_macro"]

        # 3. Fallback when tempo is unclear
        return POLICIES["default_swing"]

    except Exception as exc:
        log.warning("classify_structure_archetype failed: %s", exc)
        return POLICIES["default_swing"]


# ──────────────────────────────────────────────────────────────────────────────
# Preset application
# ──────────────────────────────────────────────────────────────────────────────


def apply_policy_to_preset(
    preset: Dict[str, Any],
    preset_name: str,
    policy: StructurePolicy,
) -> Dict[str, Any]:
    """
    Return a copy of a strategy preset with the archetype policy constraints applied.

    Rules:
        - LEAPS presets (180–560 DTE) are not DTE-constrained by the archetype
          policy; their range is already inherently long-duration.
        - Swing presets (long_call / long_put) have their DTE range tightened
          to the archetype's swing_dte_min / swing_dte_max.
        - Spread cap is applied to all presets (stricter of policy vs preset default).

    A narrowed DTE range where min > max produces an empty match list, which is
    the correct safe behaviour when no contracts fit the archetype.
    """
    ep = dict(preset)  # shallow copy — never mutate the global PRESETS

    if not preset_name.startswith("leaps"):
        # Tighten DTE window to archetype swing range
        ep["min_dte"] = max(ep["min_dte"], policy.swing_dte_min)
        ep["max_dte"] = min(ep["max_dte"], policy.swing_dte_max)

    # Spread cap: take the stricter of preset default and archetype policy
    ep["max_spread_pct"] = min(ep["max_spread_pct"], policy.max_spread_pct)

    return ep
