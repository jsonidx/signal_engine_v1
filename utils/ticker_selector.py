#!/usr/bin/env python3
"""
================================================================================
TICKER SELECTOR — Priority-based Claude API call budgeting
================================================================================
Selects the top N tickers for AI synthesis based on pre-resolved signal quality,
before any API cost is incurred.

The selection runs on output from conflict_resolver.py (data/resolved_signals.json)
and signal_engine.py (signals_output/equity_signals_YYYYMMDD.csv).

Public API:
    compute_priority_score(ticker, resolved_signal, equity_rank, composite_z) -> float
    select_top_tickers(resolved_signals_path, equity_signals_path, ...) -> list[dict]
================================================================================
"""

import glob as _glob
import json
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import pandas as pd
    _PANDAS_AVAILABLE = True
except ImportError:
    _PANDAS_AVAILABLE = False

try:
    from config import AI_QUANT_MAX_TICKERS, AI_QUANT_MIN_AGREEMENT, AI_QUANT_ALWAYS_INCLUDE
except ImportError:
    AI_QUANT_MAX_TICKERS = 10
    AI_QUANT_MIN_AGREEMENT = 0.60
    AI_QUANT_ALWAYS_INCLUDE: List[str] = []


# ==============================================================================
# PRIORITY SCORING
# ==============================================================================

def compute_priority_score(
    ticker: str,
    resolved_signal: dict,
    equity_rank: Optional[int] = None,
    composite_z: Optional[float] = None,
) -> float:
    """
    Computes a single priority score (higher = more deserving of Claude synthesis).

    Inputs from resolved_signals (output of conflict_resolver.py):
      - signal_agreement_score: float 0-1 (fraction of modules agreeing)
      - pre_resolved_confidence: float 0-1
      - bull_weight / bear_weight: whichever is higher (directional strength)
      - override_flags: list (penalize if dominated by a single override)
      - skip_claude: bool (if True, excluded immediately — returns -1)

    Inputs from equity_signals CSV:
      - composite_z: float (momentum factor rank score)
      - equity_rank: int (1 = best, lower is better)

    Formula:
      base  = signal_agreement_score  * 40    # max 40 pts — breadth of signal
      base += pre_resolved_confidence  * 25    # max 25 pts — conviction strength
      base += max(bull_weight, bear_weight)*20 # max 20 pts — directional weight
      + equity rank bonus (max 15 pts if rank <= 30)
      + composite_z bonus  (max 10 pts)
      - post_squeeze_guard penalty  (×0.3)
      - pre_earnings_hold  penalty  (×0.5)

    Returns -1.0 if skip_claude is True.
    """
    if resolved_signal.get("skip_claude"):
        return -1.0

    agreement   = float(resolved_signal.get("signal_agreement_score", 0) or 0)
    confidence  = float(resolved_signal.get("pre_resolved_confidence", 0) or 0)
    bull_weight = float(resolved_signal.get("bull_weight", 0) or 0)
    bear_weight = float(resolved_signal.get("bear_weight", 0) or 0)
    override_flags = resolved_signal.get("override_flags", []) or []

    base  = agreement   * 40
    base += confidence  * 25
    base += max(bull_weight, bear_weight) * 20

    # Equity rank bonus (only if in signal_engine top 30)
    if equity_rank is not None and equity_rank <= 30:
        base += (30 - equity_rank) * 0.5       # max 15 pts for rank 1

    # Composite_z bonus
    if composite_z is not None:
        base += min(abs(composite_z), 2.0) * 5  # max 10 pts for strong z-score

    # Penalties
    flags_str = " ".join(str(f) for f in override_flags).lower()
    if "post_squeeze_guard" in flags_str:
        base *= 0.3     # heavily penalize — already fired, low info value
    if "pre_earnings_hold" in flags_str:
        base *= 0.5     # penalize — binary event, Claude can't help much

    return round(base, 2)


# ==============================================================================
# EQUITY SIGNALS LOADER
# ==============================================================================

def _find_latest_equity_signals(signals_dir: str = None) -> Optional[str]:
    """Return the most recent equity_signals_YYYYMMDD.csv path, or None."""
    if signals_dir is None:
        signals_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "signals_output",
        )
    pattern = os.path.join(signals_dir, "equity_signals_*.csv")
    matches = sorted(_glob.glob(pattern), reverse=True)
    return matches[0] if matches else None


def _load_equity_lookup(equity_signals_path: Optional[str]) -> Dict[str, dict]:
    """
    Load equity_signals CSV → {TICKER: {equity_rank, composite_z}} dict.
    Returns empty dict if file unavailable or pandas not installed.
    """
    if not equity_signals_path or not os.path.exists(equity_signals_path):
        return {}
    if not _PANDAS_AVAILABLE:
        logger.warning("pandas not available — equity rank bonus disabled")
        return {}
    try:
        df = pd.read_csv(equity_signals_path)
        lookup: Dict[str, dict] = {}
        for _, row in df.iterrows():
            t = str(row.get("ticker", "")).upper().strip()
            if not t:
                continue
            lookup[t] = {
                "equity_rank": (
                    int(row["rank"])
                    if "rank" in row and not pd.isna(row["rank"])
                    else None
                ),
                "composite_z": (
                    float(row["composite_z"])
                    if "composite_z" in row and not pd.isna(row["composite_z"])
                    else None
                ),
            }
        return lookup
    except Exception as exc:
        logger.warning("Failed to load equity signals from %s: %s", equity_signals_path, exc)
        return {}


# ==============================================================================
# SELECTION REASON GENERATOR
# ==============================================================================

def _generate_selection_reason(
    ticker: str,
    priority_score: float,
    signal_agreement_score: float,
    direction: str,
    module_votes: dict,
    composite_z: Optional[float],
    equity_rank: Optional[int],
    rank: int,
    always_include: bool = False,
) -> str:
    """Human-readable string explaining why this ticker was selected."""
    agreement_pct = f"{signal_agreement_score:.0%}"
    strong_votes = sum(1 for v in (module_votes or {}).values() if v is not None)
    total_modules = len(module_votes or {})

    if always_include:
        return f"Always included (open position) | {agreement_pct} agreement | {direction}"

    parts = [f"Rank #{rank} by priority"]

    if composite_z is not None and abs(composite_z) >= 1.5:
        parts = [f"High composite_z ({composite_z:.2f})"]

    parts.append(f"{agreement_pct} agreement")
    parts.append(direction)

    if total_modules > 0:
        parts.append(f"{strong_votes} of {total_modules} modules signal")

    if equity_rank is not None and equity_rank <= 10:
        parts.append(f"signal_engine rank #{equity_rank}")

    return " | ".join(parts)


# ==============================================================================
# SELECTION TABLE PRINTER
# ==============================================================================

def _print_selection_table(
    selected: List[dict],
    total_input: int,
    max_tickers: int,
) -> None:
    """Print the AI QUANT SELECTION summary table to stdout."""
    cost_per_call_eur = 0.03
    n = len(selected)
    estimated_cost = n * cost_per_call_eur
    skipped_count  = total_input - n

    print()
    print("┌─────────────────────────────────────────────────────────────┐")
    print(f"│ AI QUANT SELECTION — Top {max_tickers} tickers for Claude synthesis    │")
    print("├──────┬────────┬──────────┬───────────┬──────────┬──────────┤")
    print("│ Rank │ Ticker │ Priority │ Agreement │ Direction│ Eq.Rank  │")
    print("├──────┼────────┼──────────┼───────────┼──────────┼──────────┤")

    for i, s in enumerate(selected, 1):
        ticker    = s["ticker"][:6].ljust(6)
        priority  = f"{s['priority_score']:>8.1f}"
        agreement = f"{s['signal_agreement_score']:>8.0%} "
        direction = (s.get("pre_resolved_direction") or "NEUTRAL")[:8].ljust(8)
        eq_rank   = s.get("equity_rank")
        eq_str    = (f"#{eq_rank}" if eq_rank else "N/A").ljust(8)
        print(f"│ {i:>4} │ {ticker} │ {priority} │ {agreement} │ {direction} │ {eq_str} │")

        reason = s.get("selection_reason", "")
        if "always_include" in reason.lower() or "open position" in reason.lower():
            print("│      │ (always include — open position)                            │")

    print("└──────┴────────┴──────────┴───────────┴──────────┴──────────┘")
    print(f"Skipped: {skipped_count} tickers (skip_claude=True or below agreement threshold)")
    print(f"Saved: ~{skipped_count} API calls (~€{skipped_count * cost_per_call_eur:.2f} this run)")
    print(f"Estimated cost: ~€{estimated_cost:.2f} for {n} tickers")
    print()


# ==============================================================================
# MAIN SELECTION FUNCTION
# ==============================================================================

def select_top_tickers(
    resolved_signals_path: str,
    equity_signals_path: Optional[str] = None,
    max_tickers: Optional[int] = None,
    min_agreement: Optional[float] = None,
    always_include: Optional[List[str]] = None,
    force_tickers: Optional[List[str]] = None,
) -> List[dict]:
    """
    Returns ordered list of up to max_tickers dicts for Claude synthesis.

    Each dict contains:
      ticker, priority_score, signal_agreement_score, pre_resolved_direction,
      pre_resolved_confidence, equity_rank, composite_z, override_flags,
      selection_reason

    Selection order:
      1. force_tickers (CLI override) → skip all scoring, return exactly those
      2. Load resolved_signals.json, filter skip_claude=True
      3. Filter signal_agreement_score < min_agreement
      4. Load equity_signals CSV for rank/composite_z lookup
      5. Score every remaining ticker via compute_priority_score()
      6. Sort by score descending
      7. Inject always_include tickers (displace lowest non-always if needed)
      8. Take top max_tickers
      9. Generate selection_reason for each
     10. Print summary table, return list
    """
    if max_tickers is None:
        max_tickers = AI_QUANT_MAX_TICKERS
    if min_agreement is None:
        min_agreement = AI_QUANT_MIN_AGREEMENT
    if always_include is None:
        always_include = list(AI_QUANT_ALWAYS_INCLUDE)

    always_set = {t.upper() for t in (always_include or [])}

    # ── Step 1: Force mode ────────────────────────────────────────────────────
    if force_tickers:
        tickers = [t.upper().strip() for t in force_tickers if t.strip()][:max_tickers]
        print(f"Force mode: {tickers}")
        # Try loading resolved signals for metadata; degrade gracefully if missing
        resolved_all: dict = {}
        try:
            with open(resolved_signals_path) as f:
                resolved_all = json.load(f)
        except Exception:
            pass
        eq_lookup = _load_equity_lookup(equity_signals_path or _find_latest_equity_signals())
        result = []
        for i, ticker in enumerate(tickers, 1):
            resolved = resolved_all.get(ticker, {})
            eq_data  = eq_lookup.get(ticker, {})
            result.append({
                "ticker":                  ticker,
                "priority_score":          0.0,
                "signal_agreement_score":  float(resolved.get("signal_agreement_score", 0) or 0),
                "pre_resolved_direction":  resolved.get("pre_resolved_direction", "NEUTRAL"),
                "pre_resolved_confidence": float(resolved.get("pre_resolved_confidence", 0) or 0),
                "equity_rank":             eq_data.get("equity_rank"),
                "composite_z":             eq_data.get("composite_z"),
                "override_flags":          resolved.get("override_flags", []) or [],
                "selection_reason":        "force_tickers override",
            })
        return result

    # ── Step 2: Load resolved signals, filter skip_claude ─────────────────────
    try:
        with open(resolved_signals_path) as f:
            raw = json.load(f)
        # Support both list-of-dicts and dict-keyed-by-ticker formats
        if isinstance(raw, list):
            resolved_all = {r["ticker"]: r for r in raw if "ticker" in r}
        else:
            resolved_all = raw
    except Exception as exc:
        logger.error("Cannot load resolved signals from %s: %s", resolved_signals_path, exc)
        return []

    total_loaded = len(resolved_all)
    resolved_all = {t: r for t, r in resolved_all.items() if not r.get("skip_claude")}

    # ── Step 3: Filter by min_agreement ───────────────────────────────────────
    # Always-include tickers bypass the agreement filter
    pre_agreement_count = len(resolved_all)
    resolved_eligible = {}
    for t, r in resolved_all.items():
        agreement = float(r.get("signal_agreement_score", 0) or 0)
        if t in always_set or agreement >= min_agreement:
            resolved_eligible[t] = r
    filtered_count = pre_agreement_count - len(resolved_eligible)
    if filtered_count > 0:
        print(f"Filtered {filtered_count} tickers below {min_agreement:.0%} agreement")

    # ── Step 4: Load equity signals ───────────────────────────────────────────
    eq_path  = equity_signals_path or _find_latest_equity_signals()
    eq_lookup = _load_equity_lookup(eq_path)

    # ── Step 5: Compute priority scores ──────────────────────────────────────
    scored: List[dict] = []
    for ticker, resolved in resolved_eligible.items():
        eq_data    = eq_lookup.get(ticker, {})
        eq_rank    = eq_data.get("equity_rank")
        composite_z = eq_data.get("composite_z")
        score = compute_priority_score(
            ticker, resolved, equity_rank=eq_rank, composite_z=composite_z
        )
        if score < 0:
            continue   # skip_claude was set (double-safety)
        scored.append({
            "ticker":                  ticker,
            "priority_score":          score,
            "signal_agreement_score":  float(resolved.get("signal_agreement_score", 0) or 0),
            "pre_resolved_direction":  resolved.get("pre_resolved_direction", "NEUTRAL"),
            "pre_resolved_confidence": float(resolved.get("pre_resolved_confidence", 0) or 0),
            "equity_rank":             eq_rank,
            "composite_z":             composite_z,
            "override_flags":          list(resolved.get("override_flags", []) or []),
            "module_votes":            resolved.get("module_votes", {}) or {},
            "_always_include":         ticker in always_set,
            "selection_reason":        "",
        })

    # ── Step 6: Sort by priority score descending ─────────────────────────────
    scored.sort(key=lambda x: x["priority_score"], reverse=True)

    # ── Step 7: Inject always_include, guarantee their presence in top N ──────
    # Strategy: always_include tickers displace the lowest-scoring non-always
    # tickers if they would otherwise fall outside the top N cutoff.
    always_in_scored = [s for s in scored if s["_always_include"]]
    normal           = [s for s in scored if not s["_always_include"]]

    # Check for always_include tickers not yet in scored (e.g. filtered by agreement)
    scored_tickers = {s["ticker"] for s in scored}
    for always_t in always_set:
        if always_t not in scored_tickers:
            # Inject with a minimal entry (the ticker passes agreement bypass above)
            resolved = resolved_all.get(always_t, {})
            eq_data  = eq_lookup.get(always_t, {})
            eq_rank  = eq_data.get("equity_rank")
            cz       = eq_data.get("composite_z")
            always_in_scored.append({
                "ticker":                  always_t,
                "priority_score":          compute_priority_score(
                                               always_t,
                                               resolved or {"signal_agreement_score": 0.0, "skip_claude": False},
                                               equity_rank=eq_rank,
                                               composite_z=cz,
                                           ),
                "signal_agreement_score":  float(resolved.get("signal_agreement_score", 0) or 0),
                "pre_resolved_direction":  resolved.get("pre_resolved_direction", "NEUTRAL"),
                "pre_resolved_confidence": float(resolved.get("pre_resolved_confidence", 0) or 0),
                "equity_rank":             eq_rank,
                "composite_z":             cz,
                "override_flags":          list(resolved.get("override_flags", []) or []),
                "module_votes":            resolved.get("module_votes", {}) or {},
                "_always_include":         True,
                "selection_reason":        "",
            })

    # Build final top-N: guarantee all always_include, fill remainder from normal
    n_always      = len(always_in_scored)
    n_normal_slots = max(0, max_tickers - n_always)
    top = always_in_scored + normal[:n_normal_slots]

    # ── Step 8: Re-sort at natural score order, cap at max_tickers ────────────
    top.sort(key=lambda x: x["priority_score"], reverse=True)
    top = top[:max_tickers]

    # ── Step 9: Generate selection reasons ────────────────────────────────────
    for rank, s in enumerate(top, 1):
        s["selection_reason"] = _generate_selection_reason(
            ticker=s["ticker"],
            priority_score=s["priority_score"],
            signal_agreement_score=s["signal_agreement_score"],
            direction=s.get("pre_resolved_direction", "NEUTRAL"),
            module_votes=s.get("module_votes", {}),
            composite_z=s.get("composite_z"),
            equity_rank=s.get("equity_rank"),
            rank=rank,
            always_include=s.get("_always_include", False),
        )

    # ── Step 10: Print summary table ──────────────────────────────────────────
    # total_input = all tickers seen before any filter (skip_claude or agreement)
    _print_selection_table(top, total_input=total_loaded, max_tickers=max_tickers)

    # Strip internal bookkeeping keys before returning
    for s in top:
        s.pop("_always_include", None)
        s.pop("module_votes",    None)

    return top
