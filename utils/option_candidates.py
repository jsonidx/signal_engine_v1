"""
Option Candidate Engine  (TRD-022)
================================================================================
Deterministic filtering and scoring of option contracts for a single ticker,
driven by the existing stock thesis context and the IBKR chain adapter output.

Flow:
  ThesisContext + OptionChainResult
      → suppression check (direction, conviction, event-risk)
      → preset matching  (long_call / long_put / leaps_call / leaps_put)
      → hard filters     (DTE, delta band, spread %, OI floor, min mid)
      → soft scoring     (delta quality, spread tightness, DTE centrality, liquidity)
      → return up to N candidates + rejection reasons

The LLM is NOT part of this module. It operates on the output of this module.
No orders are placed or simulated here.
================================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Input schema
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class ThesisContext:
    """
    Stock-thesis parameters used by the candidate engine.
    Sourced from thesis_cache at request time.
    """

    ticker: str
    direction: str                        # "BULL" | "BEAR" | "NEUTRAL"
    conviction: int                       # 1–5
    current_price: Optional[float] = None
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    stop_loss: Optional[float] = None
    time_horizon: Optional[str] = None   # free text e.g. "2-4 weeks"
    days_to_earnings: Optional[int] = None
    heat_score: Optional[float] = None
    expected_move_pct: Optional[float] = None


# ══════════════════════════════════════════════════════════════════════════════
# Output schemas
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class OptionCandidate:
    """A single option contract that passed all filters, scored and annotated."""

    # Identity
    ticker: str
    expiry: str
    strike: float
    right: str              # "C" | "P"
    dte: int

    # Execution fields
    bid: Optional[float]
    ask: Optional[float]
    mid: Optional[float]
    spread_pct: Optional[float]
    delta: Optional[float]
    implied_vol: Optional[float]
    open_interest: Optional[int]
    volume: Optional[int]
    breakeven: Optional[float]  # strike ± mid at expiry

    # Scoring / explanation
    score: float = 0.0
    rationale: str = ""
    strategy_preset: str = ""   # "long_call" | "long_put" | "leaps_call" | "leaps_put"

    # Provenance
    source: str = "yfinance"

    # Exit plan (TRD-026 — mandatory for persistence and analytics)
    holding_window_days: Optional[int] = None   # planned hold in calendar days
    exit_by_date: Optional[str] = None           # ISO date: expiry minus 7 days
    underlying_target_1: Optional[float] = None  # from thesis
    underlying_target_2: Optional[float] = None  # from thesis
    underlying_stop: Optional[float] = None      # from thesis
    option_take_profit_1: Optional[float] = None # mid × 1.50
    option_take_profit_2: Optional[float] = None # mid × 2.00
    option_stop_loss: Optional[float] = None     # mid × 0.50
    max_holding_rule: Optional[str] = None       # prose rule e.g. "close 7d before expiry"
    event_exit_rule: Optional[str] = None        # e.g. "exit before earnings"


@dataclass
class CandidateResult:
    """Full output of one get_option_candidates() call."""

    ticker: str
    generated_at: str               # ISO-8601 UTC

    suppressed: bool = False
    suppression_reason: Optional[str] = None

    candidates: List[OptionCandidate] = field(default_factory=list)
    rejection_reasons: List[str] = field(default_factory=list)

    underlying_price: Optional[float] = None
    chain_source: str = "unknown"
    chain_error: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════════
# Strategy presets  (v1: long_call, long_put, leaps_call, leaps_put)
# ══════════════════════════════════════════════════════════════════════════════

#: Each preset defines hard-filter thresholds applied deterministically.
PRESETS: Dict[str, Dict[str, Any]] = {
    "long_call": {
        "right": "C",
        "min_dte": 14,
        "max_dte": 60,
        "delta_min": 0.25,
        "delta_max": 0.65,
        "max_spread_pct": 12.0,
        "min_oi": 50,
        "directions": {"BULL"},
    },
    "long_put": {
        "right": "P",
        "min_dte": 14,
        "max_dte": 60,
        "delta_min": -0.65,
        "delta_max": -0.25,
        "max_spread_pct": 12.0,
        "min_oi": 50,
        "directions": {"BEAR"},
    },
    "leaps_call": {
        "right": "C",
        "min_dte": 180,
        "max_dte": 560,
        "delta_min": 0.35,
        "delta_max": 0.80,
        "max_spread_pct": 15.0,
        "min_oi": 20,
        "directions": {"BULL"},
    },
    "leaps_put": {
        "right": "P",
        "min_dte": 180,
        "max_dte": 560,
        "delta_min": -0.80,
        "delta_max": -0.35,
        "max_spread_pct": 15.0,
        "min_oi": 20,
        "directions": {"BEAR"},
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# Suppression logic
# ══════════════════════════════════════════════════════════════════════════════


def _should_suppress(thesis: ThesisContext) -> Tuple[bool, Optional[str]]:
    """
    Return (suppressed, reason).  Suppressed = do not return any candidates.
    Covers structurally unsuitable theses before any chain data is needed.
    """
    direction = (thesis.direction or "").upper()

    if direction == "NEUTRAL":
        return True, (
            "Thesis direction is NEUTRAL — no directional option trade warranted"
        )

    if direction not in ("BULL", "BEAR"):
        return True, f"Unrecognised thesis direction '{thesis.direction}'"

    if thesis.conviction is not None and thesis.conviction < 2:
        return True, (
            f"Conviction {thesis.conviction}/5 is below threshold for options "
            "(minimum 2/5 required)"
        )

    # Event-risk suppression: earnings within 3 calendar days
    if thesis.days_to_earnings is not None and 0 < thesis.days_to_earnings <= 3:
        return True, (
            f"Earnings in {thesis.days_to_earnings} calendar day(s) — "
            "IV crush risk is high; wait until after the report"
        )

    return False, None


# ══════════════════════════════════════════════════════════════════════════════
# Contract scoring
# ══════════════════════════════════════════════════════════════════════════════


def _score_contract(
    contract: Any,          # OptionContract
    preset: Dict[str, Any],
    thesis: ThesisContext,
) -> Tuple[float, List[str]]:
    """
    Apply hard filters then soft scoring to one OptionContract.

    Returns (score 0–100, rejection_reasons).
    A non-empty rejection_reasons list means the contract is excluded;
    the score value is 0 in that case.
    """
    rejections: List[str] = []
    label = f"{contract.right} {contract.strike} {contract.expiry}"

    # ── Hard filters ──────────────────────────────────────────────────────────

    # Require a tradable mid price
    if contract.mid is None or contract.mid < 0.01:
        rejections.append(f"{label}: no valid mid price")
        return 0.0, rejections

    # Spread quality  (always checked when bid/ask available)
    sp = contract.spread_pct
    if sp is not None and sp > preset["max_spread_pct"]:
        rejections.append(
            f"{label}: spread {sp:.1f}% > {preset['max_spread_pct']}% limit"
        )
        return 0.0, rejections

    # Open interest floor
    oi = contract.open_interest
    if oi is not None and oi < preset["min_oi"]:
        rejections.append(
            f"{label}: OI {oi} < {preset['min_oi']} minimum"
        )
        return 0.0, rejections

    # Delta range (checked only when delta is available)
    delta = contract.delta
    if delta is not None:
        d_min = preset["delta_min"]
        d_max = preset["delta_max"]
        if not (d_min <= delta <= d_max):
            rejections.append(
                f"{label}: delta {delta:+.2f} outside [{d_min:+.2f}, {d_max:+.2f}]"
            )
            return 0.0, rejections

    # ── Soft scoring ──────────────────────────────────────────────────────────

    score = 40.0  # base for any contract that clears all hard filters

    # Delta quality: prefer ±0.40 for swing, ±0.50 for directional conviction
    target_delta = 0.40 if contract.right == "C" else -0.40
    if delta is not None:
        delta_dist = abs(delta - target_delta)
        score += max(0.0, 15.0 - delta_dist * 60.0)   # 15 pts at target; 0 at 0.25 away

    # Spread tightness: tighter is always better
    if sp is not None:
        score += max(0.0, 15.0 - sp * 1.5)

    # DTE centrality: prefer mid of the preset range
    dte_mid = (preset["min_dte"] + preset["max_dte"]) / 2.0
    dte_half_range = max(dte_mid - preset["min_dte"], 1.0)
    dte_dist = abs(contract.dte - dte_mid) / dte_half_range
    score += max(0.0, 10.0 - dte_dist * 10.0)

    # Liquidity bonus
    if oi is not None:
        score += min(15.0, oi / 100.0)   # up to 15 pts for OI ≥ 1 500
    if contract.volume is not None and contract.volume > 0:
        score += min(10.0, contract.volume / 50.0)  # up to 10 pts for volume ≥ 500

    # Premium affordability relative to underlying (penalise very expensive contracts)
    if thesis.current_price and thesis.current_price > 0 and contract.mid:
        prem_pct = contract.mid / thesis.current_price * 100.0
        if prem_pct < 2.0:
            score += 5.0
        elif prem_pct > 15.0:
            score -= 10.0

    return round(max(0.0, min(100.0, score)), 1), []


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _breakeven(contract: Any) -> Optional[float]:
    """Breakeven at expiry (strike + mid for calls, strike - mid for puts)."""
    if contract.mid is None:
        return None
    if contract.right == "C":
        return round(contract.strike + contract.mid, 2)
    return round(contract.strike - contract.mid, 2)


def _compute_exit_plan(
    contract: Any,
    preset_name: str,
    thesis: ThesisContext,
) -> dict:
    """
    Compute deterministic exit-plan fields for a candidate.
    Returns a dict keyed by OptionCandidate exit-plan field names.
    """
    from datetime import date, timedelta

    today = date.today()

    # Holding window: swing = half DTE, LEAPS = 90 days
    if preset_name.startswith("leaps"):
        holding_days = 90
    else:
        holding_days = max(7, contract.dte // 2)

    # Close 7 days before expiry (avoid gamma risk near expiry)
    try:
        exp_dt = date.fromisoformat(contract.expiry)
        exit_dt = exp_dt - timedelta(days=7)
        # But no later than holding_window_days from today
        latest_hold = today + timedelta(days=holding_days)
        actual_exit = min(exit_dt, latest_hold)
        exit_by_date = actual_exit.isoformat()
    except (ValueError, TypeError):
        exit_by_date = None

    # Option price targets
    mid = contract.mid
    opt_tp1 = round(mid * 1.50, 4) if mid else None
    opt_tp2 = round(mid * 2.00, 4) if mid else None
    opt_sl = round(mid * 0.50, 4) if mid else None

    max_rule = f"Close by {exit_by_date} (7 days before expiry)" if exit_by_date else "Close 7 days before expiry"
    event_rule = None
    if thesis.days_to_earnings is not None and 0 < thesis.days_to_earnings <= holding_days:
        event_rule = f"Exit before earnings ({thesis.days_to_earnings}d away)"

    return {
        "holding_window_days": holding_days,
        "exit_by_date": exit_by_date,
        "underlying_target_1": thesis.target_1,
        "underlying_target_2": thesis.target_2,
        "underlying_stop": thesis.stop_loss,
        "option_take_profit_1": opt_tp1,
        "option_take_profit_2": opt_tp2,
        "option_stop_loss": opt_sl,
        "max_holding_rule": max_rule,
        "event_exit_rule": event_rule,
    }


def _build_rationale(
    contract: Any,
    preset_name: str,
    thesis: ThesisContext,
) -> str:
    """Short human-readable rationale for the UI."""
    direction = "bullish" if thesis.direction == "BULL" else "bearish"
    label = preset_name.replace("_", " ")
    parts: List[str] = []
    if contract.delta is not None:
        parts.append(f"Δ{contract.delta:+.2f}")
    if contract.implied_vol is not None:
        parts.append(f"IV {contract.implied_vol * 100:.0f}%")
    if contract.dte:
        parts.append(f"{contract.dte}d DTE")
    if contract.spread_pct is not None:
        parts.append(f"spread {contract.spread_pct:.1f}%")
    detail = ", ".join(parts)
    return f"{direction} {label}" + (f" — {detail}" if detail else "")


# ══════════════════════════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════════════════════════


def get_option_candidates(
    ticker: str,
    thesis: Optional[ThesisContext] = None,
    chain_result: Optional[Any] = None,    # OptionChainResult; fetched if None
    max_candidates: int = 3,
    include_leaps: bool = True,
) -> CandidateResult:
    """
    Deterministic option candidate selection for a single ticker.

    Steps:
      1. Suppress early if thesis doesn't warrant options trading.
      2. Determine which strategy presets apply (based on direction).
      3. Fetch chain if not provided.
      4. Apply hard filters + soft scoring per preset.
      5. Return top *max_candidates* contracts with full context.
    """
    from utils.ibkr_options import OptionChainResult, get_option_chain  # local import avoids circular

    generated_at = datetime.utcnow().isoformat()
    sym = ticker.upper()

    # ── No thesis ─────────────────────────────────────────────────────────────
    if thesis is None:
        return CandidateResult(
            ticker=sym,
            generated_at=generated_at,
            suppressed=True,
            suppression_reason="No thesis context available for this ticker",
        )

    # ── Suppression check ─────────────────────────────────────────────────────
    suppressed, suppression_reason = _should_suppress(thesis)
    if suppressed:
        return CandidateResult(
            ticker=sym,
            generated_at=generated_at,
            suppressed=True,
            suppression_reason=suppression_reason,
        )

    # ── Active presets ────────────────────────────────────────────────────────
    direction = (thesis.direction or "").upper()
    active_presets: List[str] = []
    if direction == "BULL":
        active_presets.append("long_call")
        if include_leaps:
            active_presets.append("leaps_call")
    elif direction == "BEAR":
        active_presets.append("long_put")
        if include_leaps:
            active_presets.append("leaps_put")

    # ── Fetch chain ───────────────────────────────────────────────────────────
    if chain_result is None:
        try:
            chain_result = get_option_chain(sym, min_dte=7, max_dte=560)
        except Exception as exc:
            return CandidateResult(
                ticker=sym,
                generated_at=generated_at,
                suppressed=True,
                suppression_reason=f"Could not fetch option chain: {exc}",
                chain_error=str(exc),
            )

    if chain_result.error and not chain_result.contracts:
        return CandidateResult(
            ticker=sym,
            generated_at=generated_at,
            suppressed=True,
            suppression_reason=f"Chain unavailable: {chain_result.error}",
            chain_source=chain_result.source,
            chain_error=chain_result.error,
        )

    # ── Score contracts ───────────────────────────────────────────────────────
    all_rejections: List[str] = []
    scored: List[Tuple[float, OptionCandidate]] = []

    for preset_name in active_presets:
        preset = PRESETS[preset_name]

        matching = [
            c for c in chain_result.contracts
            if c.right == preset["right"]
            and preset["min_dte"] <= c.dte <= preset["max_dte"]
        ]

        for contract in matching:
            score, rejections = _score_contract(contract, preset, thesis)
            if rejections:
                all_rejections.extend(rejections)
                continue

            exit_plan = _compute_exit_plan(contract, preset_name, thesis)
            candidate = OptionCandidate(
                ticker=contract.ticker,
                expiry=contract.expiry,
                strike=contract.strike,
                right=contract.right,
                dte=contract.dte,
                bid=contract.bid,
                ask=contract.ask,
                mid=contract.mid,
                spread_pct=contract.spread_pct,
                delta=contract.delta,
                implied_vol=contract.implied_vol,
                open_interest=contract.open_interest,
                volume=contract.volume,
                breakeven=_breakeven(contract),
                score=score,
                rationale=_build_rationale(contract, preset_name, thesis),
                strategy_preset=preset_name,
                source=contract.source,
                **exit_plan,
            )
            scored.append((score, candidate))

    # ── Select top N ──────────────────────────────────────────────────────────
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [c for _, c in scored[:max_candidates]]

    # Deduplicate and cap rejection list for readability
    unique_rejections = list(dict.fromkeys(all_rejections))[:12]

    if not top:
        return CandidateResult(
            ticker=sym,
            generated_at=generated_at,
            suppressed=False,
            suppression_reason=(
                "No contracts passed quality filters — "
                "chain may be illiquid or Greeks unavailable"
            ),
            candidates=[],
            rejection_reasons=unique_rejections,
            underlying_price=chain_result.underlying_price,
            chain_source=chain_result.source,
        )

    return CandidateResult(
        ticker=sym,
        generated_at=generated_at,
        suppressed=False,
        candidates=top,
        rejection_reasons=unique_rejections,
        underlying_price=chain_result.underlying_price,
        chain_source=chain_result.source,
    )
