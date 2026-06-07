"""
Option Candidate Engine  (TRD-022) + Execution Guidance (TRD-031)
================================================================================
Deterministic filtering and scoring of option contracts for a single ticker,
driven by the existing stock thesis context and the IBKR chain adapter output.

Flow:
  ThesisContext + OptionChainResult
      → suppression check (direction, conviction, event-risk)
      → preset matching  (long_call / long_put / leaps_call / leaps_put)
      → hard filters     (DTE, delta band, spread %, OI floor, min mid)
      → soft scoring     (delta quality, spread tightness, DTE centrality, liquidity)
      → execution guidance (entry price, order type, max chase, rationale)
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
class ExecutionGuidance:
    """
    Deterministic execution guidance for a single option candidate.
    Derived from quote/liquidity inputs — the LLM must not originate these values.
    All prices are guidance only; they are not guaranteed fills.
    """
    recommended_entry_price: Optional[float] = None   # limit order target
    recommended_order_type: str = "limit"              # always "limit" for options
    max_chase_price: Optional[float] = None            # do not pay above this level
    entry_style: str = "balanced"                      # "passive" | "balanced" | "aggressive"
    entry_rationale: str = ""                          # short human-readable explanation
    fill_quality_score: Optional[float] = None         # 0.0–1.0; higher = better fill quality
    slippage_risk_label: str = "moderate"              # "low" | "moderate" | "high" | "very_high"
    skip_if_spread_above_pct: Optional[float] = None  # threshold above which trade is unactionable


@dataclass
class OptionCandidate:
    """A single option contract that passed all filters, scored and annotated."""

    # Identity
    ticker: str
    expiry: str
    strike: float
    right: str              # "C" | "P"
    dte: int

    # Quote fields
    bid: Optional[float]
    ask: Optional[float]
    mid: Optional[float]
    spread_pct: Optional[float]
    delta: Optional[float]
    implied_vol: Optional[float]
    open_interest: Optional[int]
    volume: Optional[int]
    breakeven: Optional[float]  # strike ± mid at expiry

    # Additional Greeks (TRD-050) — populated when IBKR provides model greeks; None for yfinance
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None

    # Scoring / explanation
    score: float = 0.0
    rationale: str = ""
    strategy_preset: str = ""   # "long_call" | "long_put" | "leaps_call" | "leaps_put"

    # Provenance
    source: str = "yfinance"
    quote_time: Optional[str] = None   # ISO-8601 UTC; per-contract from IBKR; None for yfinance

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

    # Execution guidance (TRD-031 — deterministic, derived from quotes/liquidity)
    recommended_entry_price: Optional[float] = None
    recommended_order_type: str = "limit"
    max_chase_price: Optional[float] = None
    entry_style: str = "balanced"
    entry_rationale: str = ""
    fill_quality_score: Optional[float] = None
    slippage_risk_label: str = "moderate"
    skip_if_spread_above_pct: Optional[float] = None

    # V2 Target Engine (TRD-043) — thesis-linked option projections
    projected_option_tp1: Optional[float] = None       # option price at underlying target_1
    projected_option_tp2: Optional[float] = None       # option price at underlying target_2
    projected_option_stop: Optional[float] = None      # option price at underlying stop_loss
    projected_tp1_return_pct: Optional[float] = None   # % gain at tp1 vs entry mid
    projected_tp2_return_pct: Optional[float] = None   # % gain at tp2 vs entry mid
    projected_stop_return_pct: Optional[float] = None  # % loss at stop vs entry mid
    target_projection_method: Optional[str] = None     # "delta_only" | "delta_dte_adjusted" | "insufficient_inputs"

    # Structure archetype (TRD-048) — thesis-tempo to structure-family mapping
    structure_archetype: Optional[str] = None      # short_breakout | medium_swing | slow_macro | event_sensitive | default_swing
    structure_policy_reason: Optional[str] = None  # human-readable policy explanation

    # Risk and Position Sizing (TRD-046) — deterministic PM/risk layer
    risk_allowed: bool = True
    risk_block_reason: Optional[str] = None
    max_premium_risk_usd: Optional[float] = None
    suggested_contract_count: Optional[int] = None
    position_size_tier: str = "standard"             # "skip" | "reduced" | "standard" | "max"
    event_risk_policy: str = "no_event_window"
    iv_regime_label: str = "unknown_iv"
    portfolio_concentration_warning: Optional[str] = None
    exit_hierarchy: List[str] = field(default_factory=list)
    risk_nav_source: str = "model"                   # "account" | "model"

    # Live entry guardrails (TRD-049) — quote freshness + fair-value gating
    entry_action: str = "enter_now"                  # enter_now | reduce_size | enter_if_repriced | skip_for_now
    quote_freshness_label: str = "unknown"           # live | recent | stale | unknown
    quote_age_seconds: Optional[float] = None
    fair_value_entry_low: Optional[float] = None     # conservative entry target
    fair_value_entry_high: Optional[float] = None    # fair-value ceiling (mid haircut for IV richness)
    entry_overpay_pct: Optional[float] = None        # % above fv_high if entering at recommended price
    market_quality_label: str = "unknown"            # tight | acceptable | wide | very_wide | one_sided
    live_guardrail_reason: str = ""

    # Scenario analysis (TRD-047) — 5 bounded paths; JSON-serialisable dicts
    scenarios: List[dict] = field(default_factory=list)

    # Pre-entry buy rule (TRD-054) — single top-level decision
    buy_decision: str = "do_not_buy"              # "buy_now" | "do_not_buy"
    buy_decision_reason: str = ""                  # one-sentence explanation
    buy_decision_blocker: Optional[str] = None     # None | "risk_policy" | "entry_quality" | "both"


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

    # Structure archetype metadata (TRD-048)
    structure_archetype: Optional[str] = None        # short_breakout | medium_swing | slow_macro | event_sensitive | default_swing
    structure_policy_reason: Optional[str] = None    # human-readable policy explanation


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


_TARGET_V2_VERSION = "v2.0"   # bump when projection math changes materially


def compute_target_projections(
    mid: Optional[float],
    delta: Optional[float],
    current_price: Optional[float],
    target_1: Optional[float],
    target_2: Optional[float],
    stop_loss: Optional[float],
    dte: int,
) -> dict:
    """
    Project option prices at the thesis underlying targets and stop via linear
    delta approximation, with an optional DTE-based theta haircut for short-dated
    contracts.

    Formula:
        projected_option = max(0.01, mid + delta × (underlying_target − current_price))

    DTE haircut (method = "delta_dte_adjusted" when dte < 30):
        haircut = (30 − dte) / 30 × 0.25   [linear ramp, capped at 25%]
        Applied to the projected *gain* only, not to the existing mid.

    Returns a dict with keys:
        projected_option_tp1, projected_option_tp2, projected_option_stop
        projected_tp1_return_pct, projected_tp2_return_pct, projected_stop_return_pct
        target_projection_method
        target_projection_version

    Fails safely: if delta, mid, or current_price is missing, all projected
    fields are None and method = "insufficient_inputs".
    """
    _null = {
        "projected_option_tp1": None,
        "projected_option_tp2": None,
        "projected_option_stop": None,
        "projected_tp1_return_pct": None,
        "projected_tp2_return_pct": None,
        "projected_stop_return_pct": None,
        "target_projection_method": "insufficient_inputs",
        "target_projection_version": _TARGET_V2_VERSION,
    }

    if mid is None or mid <= 0 or delta is None or current_price is None or current_price <= 0:
        return _null

    # DTE-based theta haircut on the projected gain component
    if dte < 30:
        haircut = max(0.0, min(0.25, (30 - dte) / 30 * 0.25))
        method = "delta_dte_adjusted"
    else:
        haircut = 0.0
        method = "delta_only"

    def _project(und_target: Optional[float]) -> Optional[float]:
        if und_target is None:
            return None
        move = und_target - current_price
        gain = delta * move          # delta is naturally signed (+call, −put)
        adjusted_gain = gain * (1.0 - haircut)
        return round(max(0.01, mid + adjusted_gain), 4)

    def _return_pct(projected: Optional[float]) -> Optional[float]:
        if projected is None:
            return None
        return round((projected - mid) / mid * 100.0, 2)

    tp1 = _project(target_1)
    tp2 = _project(target_2)
    stop = _project(stop_loss)

    return {
        "projected_option_tp1": tp1,
        "projected_option_tp2": tp2,
        "projected_option_stop": stop,
        "projected_tp1_return_pct": _return_pct(tp1),
        "projected_tp2_return_pct": _return_pct(tp2),
        "projected_stop_return_pct": _return_pct(stop),
        "target_projection_method": method,
        "target_projection_version": _TARGET_V2_VERSION,
    }


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

    # V2 thesis-linked projections (TRD-043)
    v2 = compute_target_projections(
        mid=mid,
        delta=getattr(contract, "delta", None),
        current_price=thesis.current_price,
        target_1=thesis.target_1,
        target_2=thesis.target_2,
        stop_loss=thesis.stop_loss,
        dte=getattr(contract, "dte", 0) or 0,
    )

    return {
        "holding_window_days": holding_days,
        "exit_by_date": exit_by_date,
        "underlying_target_1": thesis.target_1,
        "underlying_target_2": thesis.target_2,
        "underlying_stop": thesis.stop_loss,
        # Legacy flat-multiplier fields (kept for backward compatibility)
        "option_take_profit_1": opt_tp1,
        "option_take_profit_2": opt_tp2,
        "option_stop_loss": opt_sl,
        "max_holding_rule": max_rule,
        "event_exit_rule": event_rule,
        # V2 thesis-linked projections
        "projected_option_tp1": v2["projected_option_tp1"],
        "projected_option_tp2": v2["projected_option_tp2"],
        "projected_option_stop": v2["projected_option_stop"],
        "projected_tp1_return_pct": v2["projected_tp1_return_pct"],
        "projected_tp2_return_pct": v2["projected_tp2_return_pct"],
        "projected_stop_return_pct": v2["projected_stop_return_pct"],
        "target_projection_method": v2["target_projection_method"],
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
# Execution guidance  (TRD-031)
# ══════════════════════════════════════════════════════════════════════════════

#: Spread thresholds (%) separating tight / moderate / wide execution regimes.
_SPREAD_TIGHT = 3.0
_SPREAD_MODERATE = 8.0


def compute_entry_guidance(
    candidate: "OptionCandidate",
) -> ExecutionGuidance:
    """
    Deterministic execution guidance derived from quote/liquidity inputs.

    Inputs used:
        bid, ask, mid, spread_pct, open_interest, volume, strategy_preset

    Spread tiers:
        tight    (≤ 3%)  → entry below mid; passive style; low slippage risk
        moderate (3–8%)  → entry at mid; balanced style; moderate slippage risk
        wide     (> 8%)  → entry conservative below mid; high slippage risk

    The LLM must not modify or override these values.
    All prices are guidance only — not guaranteed fills.
    """
    bid = candidate.bid
    ask = candidate.ask
    mid = candidate.mid
    spread_pct = candidate.spread_pct
    oi = candidate.open_interest or 0
    volume = candidate.volume or 0
    preset = candidate.strategy_preset or ""

    # skip_if_spread_above_pct matches the hard filter limit per preset
    if preset.startswith("leaps"):
        skip_threshold = 15.0
    else:
        skip_threshold = 12.0

    # ── No mid: cannot produce meaningful guidance ────────────────────────────
    if mid is None or mid <= 0:
        return ExecutionGuidance(
            recommended_entry_price=None,
            max_chase_price=None,
            entry_style="balanced",
            entry_rationale="Entry guidance unavailable — no valid mid price",
            fill_quality_score=None,
            slippage_risk_label="moderate",
            skip_if_spread_above_pct=skip_threshold,
        )

    # ── No bid/ask: use mid as fallback ───────────────────────────────────────
    if bid is None or ask is None:
        return ExecutionGuidance(
            recommended_entry_price=round(mid, 2),
            max_chase_price=round(mid, 2),
            entry_style="balanced",
            entry_rationale="Entry at mid (bid/ask unavailable — limit order at mid; actual spread unknown)",
            fill_quality_score=0.40,
            slippage_risk_label="moderate",
            skip_if_spread_above_pct=skip_threshold,
        )

    spread_abs = ask - bid
    if spread_abs < 0:
        spread_abs = 0.0

    # Derive spread_pct from quotes if not supplied
    sp = spread_pct if spread_pct is not None else (spread_abs / mid * 100.0 if mid > 0 else 50.0)

    # ── Liquidity quality factor (0–1) ────────────────────────────────────────
    # Weighted combination of OI and volume, both capped at typical liquid thresholds.
    liq = min(1.0, (oi / 500.0) * 0.7 + (min(volume, 200) / 200.0) * 0.3)

    # ── Spread tier logic ─────────────────────────────────────────────────────
    if sp <= _SPREAD_TIGHT:
        # Tight — enter patiently below mid; patient fills expected
        entry_price = round(bid + spread_abs * 0.45, 2)  # just below mid
        chase_price = round(mid, 2)                       # hard cap at mid
        style = "passive"
        risk_label = "low"
        fill_score = round(min(1.0, 0.70 + liq * 0.30), 2)
        rationale = (
            f"Tight {sp:.1f}% spread — limit order below mid at ${entry_price:.2f}; "
            f"expect fill near entry. Do not chase above mid (${chase_price:.2f})."
        )

    elif sp <= _SPREAD_MODERATE:
        # Moderate — enter at mid; small chase tolerance above
        entry_price = round(mid, 2)
        chase_price = round(bid + spread_abs * 0.65, 2)   # slightly above mid
        style = "balanced"
        risk_label = "moderate"
        fill_score = round(min(1.0, 0.40 + liq * 0.35), 2)
        rationale = (
            f"Moderate {sp:.1f}% spread — limit order at mid (${entry_price:.2f}); "
            f"max chase ${chase_price:.2f}. Watch slippage on partial fills."
        )

    else:
        # Wide (> 8%) — conservative entry below mid; hard cap at mid
        entry_price = round(bid + spread_abs * 0.35, 2)   # conservative, well below mid
        chase_price = round(mid, 2)                        # never pay above mid for wide spreads
        style = "passive"
        risk_label = "high" if sp <= 12.0 else "very_high"
        fill_score = round(min(1.0, max(0.10, 0.35 - (sp - 8.0) * 0.015) + liq * 0.10), 2)
        rationale = (
            f"Wide {sp:.1f}% spread — enter conservatively at ${entry_price:.2f} "
            f"(below mid ${mid:.2f}). Slippage risk is {risk_label}; "
            f"do not pay above mid. Consider skipping if spread stays above {skip_threshold:.0f}%."
        )

    return ExecutionGuidance(
        recommended_entry_price=entry_price,
        recommended_order_type="limit",
        max_chase_price=chase_price,
        entry_style=style,
        entry_rationale=rationale,
        fill_quality_score=fill_score,
        slippage_risk_label=risk_label,
        skip_if_spread_above_pct=skip_threshold,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Pre-entry buy rule  (TRD-054)
# ══════════════════════════════════════════════════════════════════════════════


def compute_buy_decision(
    risk_allowed: bool,
    entry_action: str,
) -> dict:
    """
    Deterministic pre-entry buy decision for a single option candidate.

    Truth table:
        risk_allowed=True  + entry_action="enter_now" → buy_now
        risk_allowed=False + entry_action="enter_now" → do_not_buy / risk_policy
        risk_allowed=True  + entry_action=<other>     → do_not_buy / entry_quality
        risk_allowed=False + entry_action=<other>     → do_not_buy / both
        any missing/ambiguous input                   → do_not_buy / both

    Returns a dict with keys:
        buy_decision         – "buy_now" | "do_not_buy"
        buy_decision_reason  – short one-sentence explanation
        buy_decision_blocker – None | "risk_policy" | "entry_quality" | "both"
    """
    is_risk_ok = risk_allowed is True
    is_entry_ok = (entry_action or "") == "enter_now"

    if is_risk_ok and is_entry_ok:
        return {
            "buy_decision": "buy_now",
            "buy_decision_reason": (
                "Buy allowed: portfolio risk policy passed and entry quality is actionable now."
            ),
            "buy_decision_blocker": None,
        }

    if not is_risk_ok and not is_entry_ok:
        blocker = "both"
        reason = "Do not buy: blocked by both portfolio risk policy and entry quality."
    elif not is_risk_ok:
        blocker = "risk_policy"
        reason = "Do not buy: blocked by portfolio risk policy."
    else:
        blocker = "entry_quality"
        reason = "Do not buy: wait for a better entry."

    return {
        "buy_decision": "do_not_buy",
        "buy_decision_reason": reason,
        "buy_decision_blocker": blocker,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════════════════════════


def get_option_candidates(
    ticker: str,
    thesis: Optional[ThesisContext] = None,
    chain_result: Optional[Any] = None,    # OptionChainResult; fetched if None
    max_candidates: int = 3,
    include_leaps: bool = True,
    portfolio_context: Optional[Any] = None,  # utils.option_risk.PortfolioContext; None = safe defaults
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

    # ── Structure archetype (TRD-048) ─────────────────────────────────────────
    from utils.option_structure import classify_structure_archetype, apply_policy_to_preset
    structure_policy = classify_structure_archetype(thesis)

    # ── Active presets (archetype-gated) ──────────────────────────────────────
    direction = (thesis.direction or "").upper()
    active_presets: List[str] = []
    swing_preset   = "long_call" if direction == "BULL" else "long_put"
    leaps_preset   = "leaps_call" if direction == "BULL" else "leaps_put"

    if direction in ("BULL", "BEAR"):
        if structure_policy.prefer_leaps and include_leaps and structure_policy.allow_leaps:
            # Slow macro: LEAPS first so they outscore swing when both qualify
            active_presets.append(leaps_preset)
        active_presets.append(swing_preset)
        if not structure_policy.prefer_leaps and include_leaps and structure_policy.allow_leaps:
            active_presets.append(leaps_preset)

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

        # Apply archetype DTE/spread constraints before filtering (TRD-048)
        effective_preset = apply_policy_to_preset(preset, preset_name, structure_policy)

        matching = [
            c for c in chain_result.contracts
            if c.right == effective_preset["right"]
            and effective_preset["min_dte"] <= c.dte <= effective_preset["max_dte"]
        ]

        for contract in matching:
            score, rejections = _score_contract(contract, effective_preset, thesis)
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
                # Additional Greeks (TRD-050) — available from IBKR; None for yfinance
                gamma=getattr(contract, "gamma", None),
                theta=getattr(contract, "theta", None),
                vega=getattr(contract, "vega", None),
                score=score,
                rationale=_build_rationale(contract, preset_name, thesis),
                strategy_preset=preset_name,
                # Structure archetype metadata (TRD-048)
                structure_archetype=structure_policy.archetype,
                structure_policy_reason=structure_policy.reason,
                source=contract.source,
                quote_time=getattr(contract, "quote_time", None),
                **exit_plan,
            )
            # TRD-031: attach deterministic execution guidance
            eg = compute_entry_guidance(candidate)
            candidate.recommended_entry_price = eg.recommended_entry_price
            candidate.recommended_order_type = eg.recommended_order_type
            candidate.max_chase_price = eg.max_chase_price
            candidate.entry_style = eg.entry_style
            candidate.entry_rationale = eg.entry_rationale
            candidate.fill_quality_score = eg.fill_quality_score
            candidate.slippage_risk_label = eg.slippage_risk_label
            candidate.skip_if_spread_above_pct = eg.skip_if_spread_above_pct
            # TRD-046: attach deterministic PM/risk assessment
            try:
                from utils.option_risk import compute_option_risk
                ra = compute_option_risk(candidate, thesis, portfolio_context)
                candidate.risk_allowed = ra.risk_allowed
                candidate.risk_block_reason = ra.risk_block_reason
                candidate.max_premium_risk_usd = ra.max_premium_risk_usd
                candidate.suggested_contract_count = ra.suggested_contract_count
                candidate.position_size_tier = ra.position_size_tier
                candidate.event_risk_policy = ra.event_risk_policy
                candidate.iv_regime_label = ra.iv_regime_label
                candidate.portfolio_concentration_warning = ra.portfolio_concentration_warning
                candidate.exit_hierarchy = ra.exit_hierarchy
                candidate.risk_nav_source = ra.nav_source
            except Exception as _risk_exc:
                log.warning("option_risk compute failed for %s: %s", sym, _risk_exc)
            # TRD-049: live entry guardrails
            try:
                from utils.option_entry_guardrail import compute_entry_guardrail
                guardrail = compute_entry_guardrail(
                    candidate,
                    chain_fetch_time=chain_result.fetch_time,
                )
                candidate.entry_action = guardrail.entry_action
                candidate.quote_freshness_label = guardrail.quote_freshness_label
                candidate.quote_age_seconds = guardrail.quote_age_seconds
                candidate.fair_value_entry_low = guardrail.fair_value_entry_low
                candidate.fair_value_entry_high = guardrail.fair_value_entry_high
                candidate.entry_overpay_pct = guardrail.entry_overpay_pct
                candidate.market_quality_label = guardrail.market_quality_label
                candidate.live_guardrail_reason = guardrail.live_guardrail_reason
            except Exception as _g_exc:
                log.warning("entry_guardrail compute failed for %s: %s", sym, _g_exc)
            # TRD-047: path-aware scenario analysis
            try:
                from utils.option_scenario import compute_scenario_set, scenario_set_to_dicts
                ss = compute_scenario_set(
                    mid=candidate.mid,
                    delta=candidate.delta,
                    current_price=thesis.current_price,
                    target_1=thesis.target_1,
                    target_2=thesis.target_2,
                    stop_loss=thesis.stop_loss,
                    dte=candidate.dte,
                    holding_days=candidate.holding_window_days or max(7, candidate.dte // 2),
                )
                candidate.scenarios = scenario_set_to_dicts(ss)
            except Exception as _sc_exc:
                log.warning("scenario engine failed for %s: %s", sym, _sc_exc)
            # TRD-054: top-level pre-entry buy decision
            bd = compute_buy_decision(
                risk_allowed=candidate.risk_allowed,
                entry_action=candidate.entry_action,
            )
            candidate.buy_decision = bd["buy_decision"]
            candidate.buy_decision_reason = bd["buy_decision_reason"]
            candidate.buy_decision_blocker = bd["buy_decision_blocker"]
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
            structure_archetype=structure_policy.archetype,
            structure_policy_reason=structure_policy.reason,
        )

    return CandidateResult(
        ticker=sym,
        generated_at=generated_at,
        suppressed=False,
        candidates=top,
        rejection_reasons=unique_rejections,
        underlying_price=chain_result.underlying_price,
        chain_source=chain_result.source,
        structure_archetype=structure_policy.archetype,
        structure_policy_reason=structure_policy.reason,
    )
