"""
Option Scenario Engine and Path Analysis  (TRD-047)
================================================================================
Deterministic path-aware scenario analysis for option recommendations.

Models five distinct underlying / time paths so the same stock target reached
quickly produces a materially better option outcome than the same target reached
slowly (because theta is path-dependent).

The engine EXTENDS the TRD-043 v2 projection flow:
  - TRD-043: terminal price projection at a single underlying level (one point)
  - TRD-047: projection at five (underlying, time) pairs — a bounded scenario set

Math is kept transparent and does not pretend to be Black-Scholes.

Scenario set (5 bounded scenarios):
  fast_target      — target_1 hit quickly (30% of holding window)
  slow_target      — target_1 hit slowly (90% of holding window)
  sideways_decay   — no move; full holding period; only theta erodes value
  adverse_stop     — underlying hits stop_loss at mid-point of holding window
  gap_overshoot    — underlying gaps to target_2 (or 1.5× target_1 move) at 15%

Per-scenario outputs:
  scenario_id             "fast_target" | "slow_target" | ...
  scenario_label          human-readable name
  underlying_move         $ move in underlying from current price
  underlying_move_pct     % move from current price (signed)
  days_to_resolution      calendar days until scenario resolves
  dte_at_resolution       remaining DTE when scenario resolves
  projected_option_price  projected option mid at resolution
  projected_return_pct    (projected − entry_mid) / entry_mid × 100
  theta_cost              estimated time value consumed over scenario period
  scenario_weight_label   qualitative weight: high | medium | low | tail
  exit_guidance           short action note for this path
  input_method            delta_theta | delta_only | insufficient_inputs

Theta model (square-root decay):
  TV = mid × TV_FRACTION    (fraction of mid treated as time value)
  theta_cost = TV × (1 − √(remaining_dte / initial_dte))

Square-root decay reflects that options decay faster as expiry nears. This
intentionally penalises short-DTE contracts more heavily in slow/sideways paths.

TV_FRACTION = 0.80 (fixed constant; ~80% of a 30-65 delta option is time value).

Note on large-move accuracy:
  Delta approximation is linear and overstates P&L for large underlying moves
  (e.g., gap scenarios). Results for gap_overshoot should be read as an upper
  bound, not a precise projection.

LLMs must NOT call this module to generate scenario math.
No orders are placed here.
================================================================================
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, List, Optional

log = logging.getLogger(__name__)

_SCENARIO_VERSION = "v1.0"

# ── Constants ─────────────────────────────────────────────────────────────────
_TV_FRACTION = 0.80    # fraction of mid treated as time value
_MIN_OPTION_PRICE = 0.01


# ══════════════════════════════════════════════════════════════════════════════
# Output schemas
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class ScenarioProjection:
    """
    Path-aware projection for a single scenario (one of five bounded paths).

    projected_return_pct:  positive = gain, negative = loss, versus entry_mid.
    theta_cost:            amount of time value consumed over the scenario period.
    input_method:          "delta_theta"        — full delta + theta model
                           "delta_only"         — no theta (dte == 0 or instant hit)
                           "insufficient_inputs" — missing required inputs
    """
    scenario_id: str
    scenario_label: str
    underlying_move: float              # $ move from current_price (signed)
    underlying_move_pct: float          # % move from current_price (signed)
    days_to_resolution: int             # calendar days until scenario resolves
    dte_at_resolution: int              # DTE remaining when scenario resolves (≥ 0)
    projected_option_price: Optional[float]
    projected_return_pct: Optional[float]
    theta_cost: Optional[float]
    scenario_weight_label: str          # high | medium | low | tail
    exit_guidance: str
    input_method: str


@dataclass
class ScenarioSet:
    """
    Full scenario analysis output for one option candidate.

    scenarios:              ordered list of ScenarioProjection (5 entries)
    entry_mid:              option price used as the entry baseline
    base_dte:               DTE at time of analysis
    holding_days:           planned holding window in calendar days
    scenario_engine_version: version string for analytics lineage
    """
    scenarios: List[ScenarioProjection]
    entry_mid: Optional[float]
    base_dte: int
    holding_days: int
    scenario_engine_version: str = _SCENARIO_VERSION


# ══════════════════════════════════════════════════════════════════════════════
# Scenario definitions
# ══════════════════════════════════════════════════════════════════════════════

#: Scenario spec: each entry defines path shape; actual numbers filled by compute_*
_SCENARIO_SPECS = [
    {
        "id": "fast_target",
        "label": "Fast Target Hit",
        "days_fraction": 0.30,    # resolves at 30% of holding window
        "weight_label": "medium",
        "exit_guidance": (
            "Take profits near target; do not wait for target_2 on fast moves"
        ),
    },
    {
        "id": "slow_target",
        "label": "Slow Target Hit (Theta Drag)",
        "days_fraction": 0.90,    # resolves at 90% of holding window
        "weight_label": "medium",
        "exit_guidance": (
            "Theta drag significantly reduces profit — exit early if momentum stalls"
        ),
    },
    {
        "id": "sideways_decay",
        "label": "Sideways / No Follow-Through",
        "days_fraction": 1.00,    # full holding period; no underlying move
        "weight_label": "medium",
        "exit_guidance": (
            "Close before expiry at 50% loss stop; theta will continue to erode value"
        ),
    },
    {
        "id": "adverse_stop",
        "label": "Adverse Move to Stop",
        "days_fraction": 0.50,    # stop hit at mid-point of holding window
        "weight_label": "medium",
        "exit_guidance": (
            "Exit at stop; do not average down on options — loss is bounded at premium paid"
        ),
    },
    {
        "id": "gap_overshoot",
        "label": "Gap / Overshoot Beyond Target",
        "days_fraction": 0.15,    # gap resolves quickly
        "weight_label": "low",
        "exit_guidance": (
            "Consider taking full profits immediately on a gap — gaps often fill; "
            "note linear delta overstates projected price on large moves"
        ),
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════


def _theta_cost(
    mid: float,
    initial_dte: int,
    days_elapsed: float,
) -> float:
    """
    Estimate time value consumed over *days_elapsed* using square-root decay.

    TV(t) = TV(0) × sqrt(remaining_dte / initial_dte)
    theta_cost = TV(0) - TV(t) = TV(0) × (1 - sqrt(remaining_dte / initial_dte))

    Square-root decay: theta accelerates as expiry nears.
    TV(0) = mid × TV_FRACTION (conservative constant for ~30-65 delta options).
    """
    if initial_dte <= 0 or days_elapsed <= 0:
        return 0.0

    tv0 = mid * _TV_FRACTION
    remaining = max(0.0, initial_dte - days_elapsed)
    decay = 1.0 - math.sqrt(remaining / initial_dte)
    return round(max(0.0, tv0 * decay), 4)


def _underlying_move_for_scenario(
    spec_id: str,
    current_price: Optional[float],
    target_1: Optional[float],
    target_2: Optional[float],
    stop_loss: Optional[float],
) -> Optional[float]:
    """
    Return the underlying $ move for each scenario type.
    Returns None when required inputs are missing.
    """
    if current_price is None or current_price <= 0:
        return None

    if spec_id in ("fast_target", "slow_target"):
        if target_1 is None:
            return None
        return target_1 - current_price

    if spec_id == "sideways_decay":
        return 0.0   # no move

    if spec_id == "adverse_stop":
        if stop_loss is None:
            return None
        return stop_loss - current_price

    if spec_id == "gap_overshoot":
        if target_2 is not None:
            return target_2 - current_price
        if target_1 is not None:
            # Overshoot to 150% of target_1 move when target_2 not specified
            return (target_1 - current_price) * 1.50
        return None

    return None


def _project_scenario(
    spec: dict,
    mid: float,
    delta: float,
    initial_dte: int,
    holding_days: int,
    current_price: float,
    underlying_move: float,
) -> tuple[Optional[float], Optional[float], Optional[float], str]:
    """
    Compute projected option price, return %, and theta_cost for one scenario.
    Returns (projected_price, return_pct, theta_cost, method).
    """
    days_elapsed = max(1.0, holding_days * spec["days_fraction"])
    dte_at_res = max(0, initial_dte - int(days_elapsed))

    tc = _theta_cost(mid, initial_dte, days_elapsed)
    delta_pnl = delta * underlying_move

    projected = max(_MIN_OPTION_PRICE, mid + delta_pnl - tc)
    return_pct = round((projected - mid) / mid * 100.0, 2)

    method = "delta_only" if days_elapsed <= 0 else "delta_theta"

    return (
        round(projected, 4),
        return_pct,
        round(tc, 4),
        method,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════


def compute_scenario_set(
    mid: Optional[float],
    delta: Optional[float],
    current_price: Optional[float],
    target_1: Optional[float],
    target_2: Optional[float],
    stop_loss: Optional[float],
    dte: int,
    holding_days: int,
) -> ScenarioSet:
    """
    Compute the five-scenario path analysis for one option candidate.

    Parameters
    ----------
    mid            : current option mid price (entry price)
    delta          : option delta (signed; positive for calls, negative for puts)
    current_price  : underlying current price
    target_1       : thesis target_1 (underlying level)
    target_2       : thesis target_2 (underlying level; optional)
    stop_loss      : thesis stop_loss (underlying level; optional)
    dte            : current days to expiry
    holding_days   : planned holding window in calendar days (from exit plan)

    Returns ScenarioSet.  Never raises.
    """
    holding_days = max(1, holding_days)
    base_dte = max(0, dte)

    projections: List[ScenarioProjection] = []

    for spec in _SCENARIO_SPECS:
        sid = spec["id"]

        # Check required inputs
        if mid is None or mid <= 0 or delta is None or current_price is None or current_price <= 0:
            projections.append(ScenarioProjection(
                scenario_id=sid,
                scenario_label=spec["label"],
                underlying_move=0.0,
                underlying_move_pct=0.0,
                days_to_resolution=0,
                dte_at_resolution=base_dte,
                projected_option_price=None,
                projected_return_pct=None,
                theta_cost=None,
                scenario_weight_label=spec["weight_label"],
                exit_guidance=spec["exit_guidance"],
                input_method="insufficient_inputs",
            ))
            continue

        move = _underlying_move_for_scenario(
            sid, current_price, target_1, target_2, stop_loss
        )

        if move is None:
            projections.append(ScenarioProjection(
                scenario_id=sid,
                scenario_label=spec["label"],
                underlying_move=0.0,
                underlying_move_pct=0.0,
                days_to_resolution=0,
                dte_at_resolution=base_dte,
                projected_option_price=None,
                projected_return_pct=None,
                theta_cost=None,
                scenario_weight_label=spec["weight_label"],
                exit_guidance=spec["exit_guidance"],
                input_method="insufficient_inputs",
            ))
            continue

        move_pct = round(move / current_price * 100.0, 2)
        days_elapsed = max(1.0, holding_days * spec["days_fraction"])
        days_to_res = max(1, int(days_elapsed))
        dte_at_res = max(0, base_dte - days_to_res)

        proj, ret_pct, tc, method = _project_scenario(
            spec, mid, delta, base_dte, holding_days, current_price, move
        )

        projections.append(ScenarioProjection(
            scenario_id=sid,
            scenario_label=spec["label"],
            underlying_move=round(move, 4),
            underlying_move_pct=move_pct,
            days_to_resolution=days_to_res,
            dte_at_resolution=dte_at_res,
            projected_option_price=proj,
            projected_return_pct=ret_pct,
            theta_cost=tc,
            scenario_weight_label=spec["weight_label"],
            exit_guidance=spec["exit_guidance"],
            input_method=method,
        ))

    return ScenarioSet(
        scenarios=projections,
        entry_mid=mid,
        base_dte=base_dte,
        holding_days=holding_days,
    )


def scenario_set_to_dicts(ss: ScenarioSet) -> list[dict]:
    """Convert a ScenarioSet to a JSON-serialisable list of dicts."""
    return [
        {
            "scenario_id": s.scenario_id,
            "scenario_label": s.scenario_label,
            "underlying_move": s.underlying_move,
            "underlying_move_pct": s.underlying_move_pct,
            "days_to_resolution": s.days_to_resolution,
            "dte_at_resolution": s.dte_at_resolution,
            "projected_option_price": s.projected_option_price,
            "projected_return_pct": s.projected_return_pct,
            "theta_cost": s.theta_cost,
            "scenario_weight_label": s.scenario_weight_label,
            "exit_guidance": s.exit_guidance,
            "input_method": s.input_method,
        }
        for s in ss.scenarios
    ]
