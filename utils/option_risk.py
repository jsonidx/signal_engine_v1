"""
Option Risk and Position Sizing Framework  (TRD-046)
================================================================================
Deterministic PM/risk layer for option recommendations.

Given a scored option candidate and a thesis context, this module outputs:
  - whether the trade is risk-allowed (hard block / soft warning / ok)
  - a position size tier and contract count bound
  - IV regime labeling
  - event-risk policy
  - concentration / book-level warnings
  - an ordered exit hierarchy

Rules are tables, not AI calls.  All outputs are deterministic given the same
inputs.  Fail-safe: missing context degrades to conservative defaults rather
than errors.

================================================================================
Version history  (bump _RISK_VERSION when sizing math changes materially)
================================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_RISK_VERSION = "v1.0"

# ──────────────────────────────────────────────────────────────────────────────
# Constants / thresholds
# ──────────────────────────────────────────────────────────────────────────────

#: IV thresholds (decimal, e.g. 0.35 = 35%).
_IV_LOW       = 0.20
_IV_NORMAL    = 0.40   # below = "normal_iv"; 0.20–0.40 range
_IV_ELEVATED  = 0.60   # 0.40–0.60 = "elevated_iv"
_IV_HIGH      = 1.00   # 0.60–1.00 = "high_iv"
_IV_EXTREME   = 1.50   # > 1.00 = "extreme_iv"; > 1.50 triggers hard block

#: DTE thresholds for sizing modifiers.
_DTE_GAMMA_TRAP   = 5    # < 5: hard block (extreme gamma risk)
_DTE_SHORT        = 14   # < 14: size reduction
_DTE_SWING        = 30   # 14–30: moderate (swing)

#: Spread thresholds for sizing modifiers (%).
_SPREAD_TIGHT    = 3.0
_SPREAD_MODERATE = 8.0
_SPREAD_WIDE     = 12.0  # above this: size reduction

#: Per-trade premium risk as % of account NAV by size tier.
_BUDGET_PCT: Dict[str, float] = {
    "max":      0.03,   # 3% NAV — LEAPS + conviction 5 only
    "standard": 0.02,   # 2% NAV — default
    "reduced":  0.01,   # 1% NAV — adverse conditions
    "skip":     0.00,
}

#: Max contract counts by tier to prevent oversizing on cheap options.
_MAX_CONTRACTS: Dict[str, int] = {
    "max":      20,
    "standard": 10,
    "reduced":  5,
    "skip":     0,
}

#: Model portfolio NAV (USD) used when no account context is available.
#: Conservative retail baseline; keeps guidance bounded and sensible.
_MODEL_NAV_USD = 25_000.0

# ──────────────────────────────────────────────────────────────────────────────
# Input: portfolio context
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class PortfolioContext:
    """
    Optional account/book context for position sizing.
    All fields default to safe "unknown" values.
    Populate from portfolio_settings and open-position queries where available.

    nav_label values:
        "model"       — no account data; model portfolio used ($25k baseline)
        "cash_proxy"  — cash balance only from portfolio_settings; deployed
                        capital unknown; conservative understatement of NAV
        "nav_proxy"   — cash + deployed cost-basis from open positions;
                        not mark-to-market but a better approximation of NAV
    """
    account_nav_usd: Optional[float] = None      # total portfolio NAV in USD
    max_options_allocation_pct: float = 0.10     # max 10% NAV in options total
    current_options_exposure_pct: float = 0.0    # % NAV already in options
    open_option_positions: int = 0               # count of distinct open option positions
    open_option_tickers: Optional[List[str]] = None  # tickers with active option positions
    nav_label: str = "model"                     # "model" | "cash_proxy" | "nav_proxy"

    @property
    def effective_nav_usd(self) -> float:
        """NAV to use for sizing; falls back to model portfolio when unknown."""
        if self.account_nav_usd and self.account_nav_usd > 100:
            return self.account_nav_usd
        return _MODEL_NAV_USD

    @property
    def nav_is_model(self) -> bool:
        return not (self.account_nav_usd and self.account_nav_usd > 100)


# ──────────────────────────────────────────────────────────────────────────────
# Output: risk assessment
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class RiskAssessment:
    """
    Full PM/risk output for one option candidate.
    Attached to OptionCandidate before serialization.
    """
    risk_allowed: bool
    risk_block_reason: Optional[str]
    max_premium_risk_usd: Optional[float]
    suggested_contract_count: Optional[int]
    position_size_tier: str              # "skip" | "reduced" | "standard" | "max"
    event_risk_policy: str               # see _event_risk_policy()
    iv_regime_label: str                 # see _iv_regime()
    portfolio_concentration_warning: Optional[str]
    exit_hierarchy: List[str]
    risk_version: str = _RISK_VERSION
    nav_source: str = "model"            # "account" | "model"


# ──────────────────────────────────────────────────────────────────────────────
# IV regime labeling
# ──────────────────────────────────────────────────────────────────────────────


def _iv_regime(iv: Optional[float]) -> str:
    """
    Map implied volatility (decimal) to a human-readable regime label.
    None/missing → "unknown_iv".
    """
    if iv is None:
        return "unknown_iv"
    if iv < _IV_LOW:
        return "low_iv"
    if iv < _IV_NORMAL:
        return "normal_iv"
    if iv < _IV_ELEVATED:
        return "elevated_iv"
    if iv < _IV_HIGH:
        return "high_iv"
    return "extreme_iv"


# ──────────────────────────────────────────────────────────────────────────────
# Event risk policy
# ──────────────────────────────────────────────────────────────────────────────


def _event_risk_policy(days_to_earnings: Optional[int]) -> str:
    """
    Map days-to-earnings to an event-risk policy label.
    None / <= 0 / > 30 → "no_event_window" (no near-term catalyst risk).
    """
    if days_to_earnings is None or days_to_earnings <= 0 or days_to_earnings > 30:
        return "no_event_window"
    if days_to_earnings <= 2:
        return "hard_block_earnings_imminent"
    if days_to_earnings <= 7:
        return "reduce_size_near_earnings"
    if days_to_earnings <= 14:
        return "monitor_event_window"
    return "event_aware"


# ──────────────────────────────────────────────────────────────────────────────
# Size tier determination
# ──────────────────────────────────────────────────────────────────────────────


def _determine_size_tier(
    conviction: int,
    strategy_preset: str,
    iv_label: str,
    event_policy: str,
    spread_pct: Optional[float],
    dte: int,
) -> str:
    """
    Deterministic size tier from trade quality inputs.

    Hard-blocking rules return "skip" immediately.
    Otherwise: start from conviction-based base tier, then apply downgrade rules.
    """
    # Hard blocks at tier level (structural issues)
    if event_policy == "hard_block_earnings_imminent":
        return "skip"
    if iv_label == "extreme_iv":
        return "skip"

    # Base tier from conviction
    if conviction <= 2:
        tier = "reduced"
    elif conviction == 5 and strategy_preset.startswith("leaps"):
        tier = "max"
    else:
        tier = "standard"

    # IV downgrade
    if iv_label in ("high_iv",):
        tier = _downgrade(tier)

    # Event window downgrade
    if event_policy == "reduce_size_near_earnings":
        tier = _downgrade(tier)

    # Spread quality downgrade
    if spread_pct is not None and spread_pct > _SPREAD_WIDE:
        tier = _downgrade(tier)

    # Short DTE downgrade (below swing threshold)
    if dte < _DTE_SHORT:
        tier = _downgrade(tier)

    return tier


_TIER_ORDER = ("skip", "reduced", "standard", "max")


def _downgrade(tier: str) -> str:
    """Move one step down the size-tier ladder; floor is 'reduced' (never forces 'skip')."""
    idx = _TIER_ORDER.index(tier) if tier in _TIER_ORDER else 2
    return _TIER_ORDER[max(1, idx - 1)]


# ──────────────────────────────────────────────────────────────────────────────
# Concentration / book-level warnings
# ──────────────────────────────────────────────────────────────────────────────


def _concentration_warning(
    ticker: str,
    portfolio: PortfolioContext,
) -> Optional[str]:
    """
    Surface a concentration warning when the portfolio context indicates overexposure.
    Conservative: only warns, never blocks.
    Returns None when context is insufficient.
    """
    warnings: List[str] = []

    # Book-level exposure ceiling
    if portfolio.current_options_exposure_pct >= portfolio.max_options_allocation_pct:
        pct = round(portfolio.current_options_exposure_pct * 100, 1)
        warnings.append(
            f"options exposure {pct}% is at or above the {portfolio.max_options_allocation_pct * 100:.0f}% NAV ceiling"
        )

    # Too many open positions
    if portfolio.open_option_positions >= 6:
        warnings.append(
            f"{portfolio.open_option_positions} open option positions — "
            "consider closing losers before adding new trades"
        )

    # Duplicate ticker
    open_tickers = portfolio.open_option_tickers or []
    if ticker.upper() in [t.upper() for t in open_tickers]:
        warnings.append(
            f"already have an open option position in {ticker} — "
            "adding another increases single-name concentration"
        )

    return "; ".join(warnings) if warnings else None


# ──────────────────────────────────────────────────────────────────────────────
# Exit hierarchy builder
# ──────────────────────────────────────────────────────────────────────────────


def _build_exit_hierarchy(
    candidate: Any,   # OptionCandidate — typed loosely to avoid circular import
    thesis_direction: str,
    thesis_stop_loss: Optional[float],
    days_to_earnings: Optional[int],
    holding_window_days: Optional[int],
) -> List[str]:
    """
    Ordered exit hierarchy for a candidate.  Priority: highest urgency first.
    Each entry is a short prose rule — inspectable and UI-renderable.
    """
    exits: List[str] = []

    # 1. Thesis invalidation (highest priority — always first)
    if thesis_stop_loss is not None:
        direction_word = "falls below" if thesis_direction == "BULL" else "rises above"
        exits.append(
            f"thesis_stop: exit if underlying {direction_word} ${thesis_stop_loss:.2f} "
            f"(thesis invalidated)"
        )
    else:
        exits.append("thesis_stop: exit on thesis invalidation (stop level unset)")

    # 2. Scale out at option projected TP1 (v2 preferred, legacy fallback)
    tp1 = getattr(candidate, "projected_option_tp1", None) or getattr(candidate, "option_take_profit_1", None)
    if tp1:
        exits.append(
            f"scale_out_tp1: take 50% off if option reaches ${tp1:.2f} (projected TP1)"
        )
    else:
        exits.append("scale_out_tp1: take 50% off at first profit target (target unset)")

    # 3. Full exit at option projected TP2
    tp2 = getattr(candidate, "projected_option_tp2", None) or getattr(candidate, "option_take_profit_2", None)
    if tp2:
        exits.append(
            f"exit_tp2: close remaining position if option reaches ${tp2:.2f} (projected TP2)"
        )
    else:
        exits.append("exit_tp2: close remaining at second profit target (target unset)")

    # 4. Time stop
    exit_date = getattr(candidate, "exit_by_date", None)
    if exit_date:
        exits.append(f"time_stop: close entire position by {exit_date} (7 days before expiry)")
    else:
        exits.append("time_stop: close 7 days before expiry to avoid gamma risk")

    # 5. Event exit
    if days_to_earnings is not None and 0 < days_to_earnings <= (holding_window_days or 30):
        exits.append(
            f"event_exit: close before earnings report ({days_to_earnings}d away) "
            "to avoid IV crush"
        )

    # 6. Premium stop (hard loss limit)
    opt_sl = getattr(candidate, "option_stop_loss", None)
    if opt_sl:
        exits.append(
            f"premium_stop: close if option drops to ${opt_sl:.2f} "
            f"(≈50% of entry premium)"
        )
    else:
        exits.append(
            "premium_stop: close if option loses 50% of entry premium"
        )

    return exits


# ──────────────────────────────────────────────────────────────────────────────
# Hard-block checks (pre-sizing)
# ──────────────────────────────────────────────────────────────────────────────


def _hard_block_reason(
    mid: Optional[float],
    dte: int,
    iv: Optional[float],
) -> Optional[str]:
    """
    Return a hard-block reason string if any absolute condition is met.
    None = no hard block.
    """
    if mid is None or mid <= 0:
        return "no_valid_price: cannot size a trade without a valid mid price"
    if dte < _DTE_GAMMA_TRAP:
        return f"dte_gamma_trap: DTE={dte} is below {_DTE_GAMMA_TRAP} — extreme gamma risk"
    if iv is not None and iv > _IV_EXTREME:
        return f"extreme_iv: IV={iv * 100:.0f}% exceeds 150% ceiling — speculative premium"
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────


def compute_option_risk(
    candidate: Any,              # OptionCandidate — typed loosely to avoid circular import
    thesis: Any,                 # ThesisContext
    portfolio: Optional[PortfolioContext] = None,
) -> RiskAssessment:
    """
    Compute the full PM/risk assessment for one option candidate.

    Args:
        candidate:  scored OptionCandidate (after v2 target engine and entry guidance)
        thesis:     ThesisContext for this ticker
        portfolio:  optional portfolio context; degrades safely when None

    Returns:
        RiskAssessment with all fields populated deterministically.

    Rules order:
        1. Hard block (DTE, IV, no price) → skip
        2. Event-risk policy → may block or reduce
        3. IV regime → may reduce
        4. Spread/DTE quality modifiers → may reduce
        5. Conviction base tier
        6. Budget and contract count from final tier
        7. Concentration warning (book-level, conservative)
        8. Exit hierarchy
    """
    port = portfolio or PortfolioContext()

    # ── Safe field extraction ─────────────────────────────────────────────────
    mid           = getattr(candidate, "mid", None)
    dte           = getattr(candidate, "dte", 0) or 0
    iv            = getattr(candidate, "implied_vol", None)
    spread_pct    = getattr(candidate, "spread_pct", None)
    preset        = getattr(candidate, "strategy_preset", "") or ""
    ticker        = getattr(candidate, "ticker", "")

    conviction       = getattr(thesis, "conviction", 3) or 3
    thesis_direction = getattr(thesis, "direction", "BULL") or "BULL"
    thesis_stop      = getattr(thesis, "stop_loss", None)
    days_to_earnings = getattr(thesis, "days_to_earnings", None)
    holding_days     = getattr(candidate, "holding_window_days", None)

    # ── Step 1: IV regime ────────────────────────────────────────────────────
    iv_label = _iv_regime(iv)

    # ── Step 2: Event risk policy ────────────────────────────────────────────
    event_policy = _event_risk_policy(days_to_earnings)

    # ── Step 3: Hard block ───────────────────────────────────────────────────
    block_reason = _hard_block_reason(mid, dte, iv)
    if block_reason is None and event_policy == "hard_block_earnings_imminent":
        block_reason = (
            f"event_risk: earnings in {days_to_earnings} day(s) — "
            "IV crush risk is high; wait until after the report"
        )

    if block_reason:
        return RiskAssessment(
            risk_allowed=False,
            risk_block_reason=block_reason,
            max_premium_risk_usd=0.0,
            suggested_contract_count=0,
            position_size_tier="skip",
            event_risk_policy=event_policy,
            iv_regime_label=iv_label,
            portfolio_concentration_warning=None,
            exit_hierarchy=[],
            nav_source="model" if port.nav_is_model else port.nav_label,
        )

    # ── Step 4: Size tier ────────────────────────────────────────────────────
    tier = _determine_size_tier(
        conviction=conviction,
        strategy_preset=preset,
        iv_label=iv_label,
        event_policy=event_policy,
        spread_pct=spread_pct,
        dte=dte,
    )

    # ── Step 5: Budget and contract count ────────────────────────────────────
    nav = port.effective_nav_usd
    budget_pct = _BUDGET_PCT[tier]
    max_premium_risk = round(nav * budget_pct, 2)

    if tier == "skip" or mid is None or mid <= 0:
        suggested_contracts = 0
    else:
        contract_premium = mid * 100.0   # standard 100-share multiplier
        raw_count = int(max_premium_risk / contract_premium) if contract_premium > 0 else 0
        suggested_contracts = max(1, min(_MAX_CONTRACTS[tier], raw_count))

    # ── Step 6: Concentration warning ────────────────────────────────────────
    conc_warning = _concentration_warning(ticker, port)

    # ── Step 7: Exit hierarchy ───────────────────────────────────────────────
    exits = _build_exit_hierarchy(
        candidate=candidate,
        thesis_direction=thesis_direction,
        thesis_stop_loss=thesis_stop,
        days_to_earnings=days_to_earnings,
        holding_window_days=holding_days,
    )

    return RiskAssessment(
        risk_allowed=True,
        risk_block_reason=None,
        max_premium_risk_usd=max_premium_risk,
        suggested_contract_count=suggested_contracts,
        position_size_tier=tier,
        event_risk_policy=event_policy,
        iv_regime_label=iv_label,
        portfolio_concentration_warning=conc_warning,
        exit_hierarchy=exits,
        nav_source="model" if port.nav_is_model else port.nav_label,
    )
