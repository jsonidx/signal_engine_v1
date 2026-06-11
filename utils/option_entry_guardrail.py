"""
Option Entry Fair Value and Live Quote Guardrails  (TRD-049)
================================================================================
Deterministic micro-execution layer that evaluates whether the current quote
for an option candidate is fresh, fairly priced, and actionable.

Answers:
  - Is the quote fresh enough to trust?
  - Is the market too wide or unstable to enter?
  - Is the option overpriced versus a fair-value entry band?
  - Should the user: enter now | reduce size | wait for repricing | skip?

Key outputs:
  entry_action           enter_now | reduce_size | enter_if_repriced | skip_for_now
  quote_freshness_label  live | recent | stale | unknown
  quote_age_seconds      seconds since quote timestamp (when available)
  fair_value_entry_low   conservative entry target (≥ bid, 20% into spread)
  fair_value_entry_high  fair-value ceiling — do not pay above this
  entry_overpay_pct      % above fv_high the recommended entry represents (≥ 0)
  market_quality_label   tight | acceptable | wide | very_wide | one_sided
  live_guardrail_reason  one-sentence explanation for the entry_action

Design constraints:
  - Deterministic and conservative: uncertain inputs degrade to more cautious
    actions (reduce_size or skip_for_now) rather than permitting blind entry.
  - No LLM involvement: all outputs are derived from numeric quote inputs.
  - Fail safely: missing timestamps or quotes degrade honestly, never fabricate.
  - Compatible with existing TRD-031 execution-guidance fields.

Fair-value band:
  fair_value_entry_high = mid × (1 − IV_haircut)
      In a normal IV regime this equals mid. In high/extreme IV regimes it is
      below mid, reflecting that the option carries an elevated volatility
      premium relative to its historical range.

  fair_value_entry_low = max(bid, bid + spread_abs × 0.20)
      Conservative limit-order target; realistic for patient fills.

  entry_overpay_pct = max(0, (recommended_entry − fv_high) / fv_high × 100)
      Zero when entry price ≤ fv_high (normal case). Positive when IV richness
      has pushed the fair-value ceiling below the recommended entry price.
================================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

_GUARDRAIL_VERSION = "v1.0"

# ── Spread thresholds (% of mid) ──────────────────────────────────────────────
_SPREAD_TIGHT = 3.0
_SPREAD_ACCEPTABLE = 8.0
_SPREAD_WIDE_GUARD = 12.0    # > 12% → very_wide for guardrail purposes

# ── Quote age thresholds (seconds) ───────────────────────────────────────────
_AGE_LIVE_S = 60.0           # < 60 s     → "live"
_AGE_RECENT_S = 300.0        # 60–300 s   → "recent"; ≥ 300 s → "stale"

# ── IV regime fair-value haircut (applied to mid to derive fv_high) ──────────
# A high/extreme IV regime means the option is priced richly vs history;
# we lower the fair-value ceiling proportionally.
_IV_FV_HAIRCUT: dict[str, float] = {
    "low":        0.00,
    "normal":     0.00,
    "elevated":   0.02,    # mild richness: up to 2% below mid
    "high":       0.05,    # rich: up to 5% below mid
    "extreme":    0.10,    # very rich: up to 10% below mid
    "unknown_iv": 0.00,    # no IV data: no haircut (fail open)
}

# ── Entry-overpay thresholds (% above fv_high) ───────────────────────────────
_OVERPAY_SKIP = 20.0          # hard block
_OVERPAY_REPRICED = 10.0      # wait for repricing
_OVERPAY_REDUCE = 5.0         # reduce size


# ══════════════════════════════════════════════════════════════════════════════
# Output schema
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class EntryGuardrail:
    """
    Deterministic entry guardrail for a single option candidate.

    entry_action:
        enter_now          All checks pass; enter at recommended price with
                           normal position size.
        reduce_size        Market is actionable but one or more caution signals
                           are present; enter with reduced size.
        enter_if_repriced  Structural issue (wide spread or extreme IV); wait
                           for a better market before committing.
        skip_for_now       Hard block: stale quote, one-sided market, or severe
                           overpay versus the fair-value ceiling.

    quote_freshness_label: live | recent | stale | unknown
    market_quality_label:  tight | acceptable | wide | very_wide | one_sided
    guardrail_version:     bumped when decision logic changes materially.
    """

    entry_action: str
    quote_freshness_label: str
    quote_age_seconds: Optional[float]
    fair_value_entry_low: Optional[float]
    fair_value_entry_high: Optional[float]
    entry_overpay_pct: Optional[float]
    market_quality_label: str
    live_guardrail_reason: str
    guardrail_version: str = _GUARDRAIL_VERSION


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════


def _quote_age(
    source: str,
    contract_quote_time: Optional[str],
    chain_fetch_time: Optional[str],
) -> tuple[Optional[float], str]:
    """
    Return (age_seconds_or_None, freshness_label).

    Priority for timestamp:
      1. contract_quote_time — per-contract timestamp (IBKR may set this)
      2. chain_fetch_time    — chain-level timestamp (OptionChainResult.fetch_time)
      3. source == "ibkr"   — treat as live without a timestamp (IBKR streams
                              are real-time; labelling "live" is accurate)
      4. No data available  — degrade to "unknown"
    """
    # IBKR without an explicit timestamp → real-time by definition
    if source == "ibkr" and not contract_quote_time and not chain_fetch_time:
        return None, "live"

    ts_str = contract_quote_time or chain_fetch_time
    if not ts_str:
        return None, "unknown"

    try:
        now = datetime.now(timezone.utc)
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = max(0.0, (now - ts).total_seconds())

        if source == "ibkr" or age < _AGE_LIVE_S:
            return age, "live"
        if age < _AGE_RECENT_S:
            return age, "recent"
        return age, "stale"
    except Exception as exc:
        log.debug("_quote_age: could not parse timestamp %r: %s", ts_str, exc)
        return None, "unknown"


def _market_quality(bid: Optional[float], spread_pct: Optional[float]) -> str:
    """
    Classify market quality from bid and spread_pct.

    one_sided  — bid is None or ≤ 0 (no valid bid; can't price an entry)
    tight      — spread ≤ 3%
    acceptable — 3% < spread ≤ 8%
    wide       — 8% < spread ≤ 12%
    very_wide  — spread > 12%

    When spread_pct is None (data gap) we default to "acceptable" — this
    intentionally fails open so a missing spread metric doesn't hard-block
    a contract that already passed the spread hard-filter upstream.
    """
    if bid is None or bid <= 0:
        return "one_sided"
    if spread_pct is None:
        return "acceptable"
    if spread_pct <= _SPREAD_TIGHT:
        return "tight"
    if spread_pct <= _SPREAD_ACCEPTABLE:
        return "acceptable"
    if spread_pct <= _SPREAD_WIDE_GUARD:
        return "wide"
    return "very_wide"


def _fair_value_band(
    bid: Optional[float],
    ask: Optional[float],
    mid: Optional[float],
    iv_regime_label: str,
) -> tuple[Optional[float], Optional[float]]:
    """
    Returns (fair_value_entry_low, fair_value_entry_high).

    fair_value_entry_high:
        mid × (1 − IV haircut).  In a normal IV regime this equals mid.
        In high/extreme IV regimes the option is priced richly vs history,
        so the ceiling is reduced accordingly.

    fair_value_entry_low:
        max(bid, bid + spread_abs × 0.20).  A conservative limit target
        that is always ≥ bid and 20% of the way through the spread.
    """
    if mid is None or mid <= 0:
        return None, None

    haircut = _IV_FV_HAIRCUT.get(iv_regime_label or "normal", 0.00)
    fv_high = round(mid * (1.0 - haircut), 4)

    if bid is None or ask is None:
        return round(mid, 4), fv_high

    spread_abs = max(0.0, ask - bid)
    fv_low = round(max(bid, bid + spread_abs * 0.20), 4)
    # Ensure fv_low never exceeds fv_high (can happen under extreme IV haircut
    # when the fair-value ceiling drops below the bid level)
    fv_low = round(min(fv_low, fv_high), 4)
    return fv_low, fv_high


def _overpay_pct(
    recommended_entry_price: Optional[float],
    fv_high: Optional[float],
) -> Optional[float]:
    """
    How much % above the fair-value ceiling the recommended entry price is.
    Returns 0.0 when entry ≤ fv_high (no overpay), None when inputs are missing.
    """
    if recommended_entry_price is None or fv_high is None or fv_high <= 0:
        return None
    return round(max(0.0, (recommended_entry_price - fv_high) / fv_high * 100.0), 2)


def _decide(
    freshness: str,
    market_quality: str,
    overpay: Optional[float],
    iv_regime_label: str,
    spread_pct: Optional[float],
) -> tuple[str, str]:
    """
    Deterministic priority chain → (entry_action, live_guardrail_reason).

    Tier 1 (skip_for_now)  — hard blocks; quote or market structurally unusable
    Tier 2 (enter_if_repriced) — structural issues; wait for better conditions
    Tier 3 (reduce_size)   — minor caution signals; proceed with smaller size
    Tier 4 (enter_now)     — all clear
    """
    sp_str = f"{spread_pct:.1f}%" if spread_pct is not None else "unknown spread"

    # ── Tier 1: skip_for_now ─────────────────────────────────────────────────
    if market_quality == "one_sided":
        return "skip_for_now", "Market is one-sided (no valid bid) — cannot price entry"
    if market_quality == "very_wide":
        return "skip_for_now", f"{sp_str} spread too wide for reliable entry — wait for liquidity"
    if freshness == "stale":
        return "skip_for_now", "Quote is stale (>5 min old) — refresh market data before entering"
    if overpay is not None and overpay > _OVERPAY_SKIP:
        return "skip_for_now", (
            f"Entering at recommended price would overpay by {overpay:.1f}% above "
            "fair-value ceiling — wait for IV to settle"
        )

    # ── Tier 2: enter_if_repriced ─────────────────────────────────────────────
    if market_quality == "wide":
        return "enter_if_repriced", (
            f"{sp_str} spread — use a limit order well inside the market; "
            "consider waiting for a tighter bid/ask"
        )
    if iv_regime_label == "extreme":
        return "enter_if_repriced", (
            "IV in extreme regime — option is expensive; wait for IV contraction "
            "before entering"
        )
    if overpay is not None and overpay > _OVERPAY_REPRICED:
        return "enter_if_repriced", (
            f"Option priced {overpay:.1f}% above fair-value ceiling due to IV "
            "richness — wait for IV to settle"
        )

    # ── Tier 3: reduce_size ───────────────────────────────────────────────────
    if overpay is not None and overpay > _OVERPAY_REDUCE:
        return "reduce_size", (
            f"Entry is {overpay:.1f}% above fair-value ceiling (elevated IV premium) "
            "— reduce position size"
        )
    if iv_regime_label == "high":
        return "reduce_size", (
            "IV in high regime — option is rich relative to history; reduce size"
        )
    if freshness == "recent" and market_quality in ("acceptable", "wide"):
        return "reduce_size", (
            "Quote is 1–5 min old with moderate spread — enter with reduced size; "
            "recheck before scaling up"
        )

    # ── Tier 4: enter_now ────────────────────────────────────────────────────
    return "enter_now", (
        "Quote fresh, market quality acceptable, pricing within fair-value band"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════


def compute_entry_guardrail(
    candidate: Any,
    chain_fetch_time: Optional[str] = None,
) -> EntryGuardrail:
    """
    Compute deterministic live-entry guardrail for a single option candidate.

    Parameters
    ----------
    candidate        : OptionCandidate (typed loosely to avoid circular import).
                       Accesses: bid, ask, mid, spread_pct, source, quote_time,
                       recommended_entry_price, iv_regime_label.
    chain_fetch_time : ISO-8601 UTC string from OptionChainResult.fetch_time.
                       Used as the quote timestamp when the contract itself does
                       not carry a per-contract quote_time (yfinance path).

    Returns EntryGuardrail.  Never raises — degrades to skip_for_now on error.
    """
    try:
        bid        = getattr(candidate, "bid", None)
        ask        = getattr(candidate, "ask", None)
        mid        = getattr(candidate, "mid", None)
        spread_pct = getattr(candidate, "spread_pct", None)
        source     = getattr(candidate, "source", "unknown") or "unknown"
        quote_time = getattr(candidate, "quote_time", None)
        rec_entry  = getattr(candidate, "recommended_entry_price", None)
        iv_label   = getattr(candidate, "iv_regime_label", "unknown_iv") or "unknown_iv"

        tp1 = getattr(candidate, "projected_option_tp1", None)

        # Hard block: thesis targets are below current option price — stale thesis
        if tp1 is not None and mid is not None and mid > 0 and tp1 < mid:
            return EntryGuardrail(
                entry_action="skip_for_now",
                quote_freshness_label="unknown",
                quote_age_seconds=None,
                fair_value_entry_low=None,
                fair_value_entry_high=None,
                entry_overpay_pct=None,
                market_quality_label="unknown",
                live_guardrail_reason=(
                    "Thesis T1 target is below current option price — underlying has "
                    "already moved past thesis targets; thesis is stale"
                ),
            )

        age_s, freshness = _quote_age(source, quote_time, chain_fetch_time)
        mq               = _market_quality(bid, spread_pct)
        fv_low, fv_high  = _fair_value_band(bid, ask, mid, iv_label)
        overpay          = _overpay_pct(rec_entry, fv_high)
        action, reason   = _decide(freshness, mq, overpay, iv_label, spread_pct)

        return EntryGuardrail(
            entry_action=action,
            quote_freshness_label=freshness,
            quote_age_seconds=round(age_s, 1) if age_s is not None else None,
            fair_value_entry_low=fv_low,
            fair_value_entry_high=fv_high,
            entry_overpay_pct=overpay,
            market_quality_label=mq,
            live_guardrail_reason=reason,
        )

    except Exception as exc:
        log.warning("compute_entry_guardrail failed: %s", exc)
        return EntryGuardrail(
            entry_action="skip_for_now",
            quote_freshness_label="unknown",
            quote_age_seconds=None,
            fair_value_entry_low=None,
            fair_value_entry_high=None,
            entry_overpay_pct=None,
            market_quality_label="unknown",
            live_guardrail_reason=f"Guardrail error — defaulting to skip ({exc})",
        )
