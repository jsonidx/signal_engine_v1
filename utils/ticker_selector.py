#!/usr/bin/env python3
"""
================================================================================
TICKER SELECTOR — Priority-based AI synthesis call budgeting
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

try:
    from config import EVENT_QUEUE_MAX_AI_SLOTS
except ImportError:
    EVENT_QUEUE_MAX_AI_SLOTS = 3

try:
    from config import (
        AI_QUANT_CAPACITY_MIN, AI_QUANT_CAPACITY_MAX,
        AI_QUANT_SCORE_THRESHOLD_HIGH, AI_QUANT_BEAR_DIRECTION_PENALTY,
    )
except ImportError:
    AI_QUANT_CAPACITY_MIN = 2
    AI_QUANT_CAPACITY_MAX = 8
    AI_QUANT_SCORE_THRESHOLD_HIGH = 70.0
    AI_QUANT_BEAR_DIRECTION_PENALTY = 0.85

try:
    from config import GOVERNANCE_A_LIST_MULTIPLIER, GOVERNANCE_PROBATION_MULTIPLIER
except ImportError:
    GOVERNANCE_A_LIST_MULTIPLIER = 1.15
    GOVERNANCE_PROBATION_MULTIPLIER = 0.70


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
      - prob_combined: float 0-1 (calibrated weighted probability — primary signal)
      - signal_agreement_score: float 0-1 (vote fraction — fallback when prob_combined absent)
      - pre_resolved_confidence: float 0-1
      - bull_weight / bear_weight: whichever is higher (directional strength)
      - override_flags: list (penalize if dominated by a single override)
      - skip_claude: bool (if True, excluded immediately — returns -1)

    Inputs from equity_signals CSV:
      - composite_z: float (momentum factor rank score)
      - equity_rank: int (1 = best, lower is better)

    Formula:
      base  = prob_combined * 40           # max 40 pts — calibrated probability
              (falls back to signal_agreement_score × 40 if prob_combined is None)
      base += pre_resolved_confidence * 25 # max 25 pts — conviction strength
      base += max(bull_weight, bear_weight)*20 # max 20 pts — directional weight
      + equity rank bonus (max 15 pts if rank <= 30)
      + composite_z bonus  (max 10 pts)
      - pre_earnings_hold  penalty  (×0.5)

    Returns -1.0 if skip_claude is True.
    """
    # TRD-069: read skip_ai_synthesis (neutral) with fallback to legacy skip_claude
    _skip = resolved_signal.get("skip_ai_synthesis") or resolved_signal.get("skip_claude")
    if _skip:
        return -1.0

    agreement   = float(resolved_signal.get("signal_agreement_score", 0) or 0)
    prob_comb   = resolved_signal.get("prob_combined")
    # Use prob_combined when available; fall back to signal_agreement_score
    # for older resolved_signals.json entries that predate the prob_engine build.
    breadth     = float(prob_comb) if prob_comb is not None else agreement
    confidence  = float(resolved_signal.get("pre_resolved_confidence", 0) or 0)
    bull_weight = float(resolved_signal.get("bull_weight", 0) or 0)
    bear_weight = float(resolved_signal.get("bear_weight", 0) or 0)
    override_flags = resolved_signal.get("override_flags", []) or []

    base  = breadth    * 40
    base += confidence * 25
    base += max(bull_weight, bear_weight) * 20

    # Equity rank bonus (only if in signal_engine top 30)
    if equity_rank is not None and equity_rank <= 30:
        base += (30 - equity_rank) * 0.5       # max 15 pts for rank 1

    # Composite_z bonus
    if composite_z is not None:
        base += min(abs(composite_z), 2.0) * 5  # max 10 pts for strong z-score

    # Penalties
    flags_str = " ".join(str(f) for f in override_flags).lower()
    if "pre_earnings_hold" in flags_str:
        base *= 0.5     # penalize — binary event, Claude can't help much

    # TRD-058: bear direction penalty — bulls fill top slots first at equal strength
    direction = resolved_signal.get("pre_resolved_direction", "NEUTRAL")
    if direction == "BEAR":
        base *= AI_QUANT_BEAR_DIRECTION_PENALTY

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
    """
    Print the AI QUANT SELECTION summary table to stdout.

    Layout:
      - Top N dynamic tickers ranked by priority (open positions excluded from rank)
      - Open positions appended after the dynamic rows, re-sorted by priority
      - Each open position row gets an inline flag: ← high attention — open position
    """
    cost_per_call_eur = 0.03
    n             = len(selected)
    estimated_cost = n * cost_per_call_eur
    skipped_count  = total_input - n

    # Count dynamic vs open-position rows using the internal bookkeeping key
    n_open    = sum(1 for s in selected if s.get("_always_include"))
    n_dynamic = n - n_open

    # Build header text — fits within the 61-char inner box width
    if n_open:
        pos_word    = "position" if n_open == 1 else "positions"
        header_text = f" AI QUANT SELECTION — Top {n_dynamic} dynamic + {n_open} open {pos_word} (high attention)"
    else:
        header_text = f" AI QUANT SELECTION — Top {n_dynamic} tickers for AI synthesis"

    print()
    print("┌─────────────────────────────────────────────────────────────┐")
    print(f"│{header_text:<61}│")
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
        row = f"│ {i:>4} │ {ticker} │ {priority} │ {agreement} │ {direction} │ {eq_str} │"
        if s.get("_always_include"):
            if s.get("open_position_lane_override"):
                row += "  ← DEGRADED OPEN POS — lane_excluded, review required"
            else:
                row += "  ← high attention — open position"
        print(row)

    print("└──────┴────────┴──────────┴───────────┴──────────┴──────────┘")
    print(f"Skipped: {skipped_count} tickers (ai_synthesis_skipped or below agreement threshold)")
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
    event_queue: Optional[List[dict]] = None,
    event_queue_max_slots: Optional[int] = None,
) -> List[dict]:
    """
    Returns an adaptively sized list of dicts for Claude synthesis.
    max_tickers is a hard upper bound; the actual count is driven by signal
    quality (see adaptive capacity below).

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
      7. Inject always_include tickers (open positions, additive on top of normal slots)
      8. Adaptive capacity: n_slots = max(floor, min(cap, strong_count))
         where cap   = min(max_tickers, AI_QUANT_CAPACITY_MAX)
               floor = min(AI_QUANT_CAPACITY_MIN, max_tickers)
         Weak days select down to floor; strong days expand up to cap.
         always_include tickers are additive and not bounded by this.
      9. Generate selection_reason for each
     10. Append event_queue tickers (bounded by event_queue_max_slots)
         that are not already selected, even if absent from resolved_signals
     11. Print summary table, return list

    event_queue: optional list of event candidate dicts from utils/event_queue.py.
                 Each must have at least "ticker" and "reason".  Tickers already
                 in the normal top selection are de-duplicated and not added twice.
    event_queue_max_slots: max additional AI synthesis slots for event candidates
                           (default EVENT_QUEUE_MAX_AI_SLOTS = 3).
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
        # Normalize field name aliases from conflict_resolver.py list format
        for r in resolved_all.values():
            if "pre_resolved_direction" not in r and "direction" in r:
                r["pre_resolved_direction"] = r["direction"]
            if "pre_resolved_confidence" not in r and "confidence" in r:
                r["pre_resolved_confidence"] = r["confidence"]
    except Exception as exc:
        logger.error("Cannot load resolved signals from %s: %s", resolved_signals_path, exc)
        return []

    total_loaded = len(resolved_all)
    # TRD-069: accept skip_ai_synthesis (neutral name) or legacy skip_claude
    resolved_all = {
        t: r for t, r in resolved_all.items()
        if not (r.get("skip_ai_synthesis") or r.get("skip_claude"))
    }

    # ── Step 3: Filter by min_agreement or prob_combined ─────────────────────
    # Always-include tickers bypass the agreement filter.
    # A ticker qualifies if:
    #   agreement >= min_agreement  (legacy path — backwards compatible)
    #   OR prob_combined >= 0.55    (new calibrated gate — slightly lower threshold
    #                                because prob_combined is a more accurate signal)
    PROB_COMBINED_MIN = 0.55
    pre_agreement_count = len(resolved_all)
    resolved_eligible = {}
    for t, r in resolved_all.items():
        agreement    = float(r.get("signal_agreement_score", 0) or 0)
        prob_combined = float(r.get("prob_combined", 0) or 0)
        if t in always_set or agreement >= min_agreement or prob_combined >= PROB_COMBINED_MIN:
            resolved_eligible[t] = r
    filtered_count = pre_agreement_count - len(resolved_eligible)
    if filtered_count > 0:
        print(f"Filtered {filtered_count} tickers below {min_agreement:.0%} agreement "
              f"and below {PROB_COMBINED_MIN:.0%} prob_combined")

    # ── Step 4: Load equity signals + universe lane info ─────────────────────
    eq_path  = equity_signals_path or _find_latest_equity_signals()
    eq_lookup = _load_equity_lookup(eq_path)

    # TRD-068: Load ticker governance (non-STANDARD entries only; degrades gracefully)
    _governance_lookup: Dict[str, str] = {}
    try:
        from utils.supabase_persist import fetch_ticker_governance
        _governance_lookup = fetch_ticker_governance()
    except Exception:
        pass
    _QUARANTINE_TICKERS: set = {
        t for t, g in _governance_lookup.items() if g == "QUARANTINE"
    }

    # TRD-057: load lane assignments from ranked_universe.json if available
    _lane_lookup: Dict[str, str] = {}
    try:
        import os as _os, json as _json
        _ru_path = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "ranked_universe.json",
        )
        if _os.path.exists(_ru_path):
            _ru = _json.load(open(_ru_path))
            _lane_lookup = {t: v.get("lane", "unknown") for t, v in _ru.items()}
    except Exception:
        pass

    # ── Lane eligibility and priority multipliers (TRD-065) ──────────────────
    # lane_excluded  — ticker failed all configured lane thresholds (too cheap,
    #                  illiquid, or insufficient history).  Hard-gated out.
    # hard_excluded  — ticker exceeds extreme ATR%/beta ceiling.  Hard-gated out.
    # Both are excluded from the normal AI selection funnel regardless of signal
    # strength.  The only override is always_include (open-position force-include),
    # which is intentional: an open position must always be reviewed even if it has
    # since fallen below thresholds.  This override is narrow and explicit.
    _NON_EXECUTION_LANES: set = {"lane_excluded", "hard_excluded"}

    # Execution lanes are full-weight; research_broad gets a significant discount
    # so execution-core names fill the top slots first at equal base scores.
    _LANE_PRIORITY_MULTIPLIER: Dict[str, float] = {
        "execution_core":      1.00,
        "execution_high_beta": 0.90,
        "research_broad":      0.60,
        "unknown":             0.80,   # lane data unavailable — partial discount
    }

    # ── Step 5: Compute priority scores ──────────────────────────────────────
    scored: List[dict] = []
    _lane_excluded_skipped = 0
    _governance_skipped = 0
    _lane_override_count = 0
    _lane_override_tickers: Dict[str, str] = {}  # ticker → lane, for PM audit trail

    for ticker, resolved in resolved_eligible.items():
        lane = _lane_lookup.get(ticker, "unknown")

        if lane in _NON_EXECUTION_LANES:
            if ticker not in always_set:
                # Normal exclusion — ticker is not an open position.
                _lane_excluded_skipped += 1
                logger.debug("Lane gate: skipping %s (lane=%s)", ticker, lane)
                continue
            if lane == "hard_excluded":
                # hard_excluded is a PM hard stop — open positions do NOT override it.
                # This is a classification issue: if a live position becomes hard_excluded,
                # PM must resolve the discrepancy manually.
                _lane_excluded_skipped += 1
                logger.warning(
                    "Open position %s is hard_excluded — hard gate enforced; "
                    "review lane classification or clear position from open list",
                    ticker,
                )
                continue
            # lane_excluded + always_include: allow through for open-position review,
            # but flag explicitly so PM can act on the degraded name.
            _lane_override_count += 1
            _lane_override_tickers[ticker] = lane
            logger.warning(
                "Open position override: %s is %s — included for review "
                "(open_position_lane_override=True, degraded_position_review_required=True)",
                ticker, lane,
            )

        # TRD-068: Governance hard gate — QUARANTINE tickers are never AI-selected
        # (always_include open-position override does NOT apply to QUARANTINE — this
        # is a PM hard block, not a lane routing decision)
        gov_state = _governance_lookup.get(ticker, "STANDARD")
        if gov_state == "QUARANTINE":
            _governance_skipped += 1
            logger.debug("Governance gate: skipping %s (QUARANTINE)", ticker)
            continue

        eq_data    = eq_lookup.get(ticker, {})
        eq_rank    = eq_data.get("equity_rank")
        composite_z = eq_data.get("composite_z")
        score = compute_priority_score(
            ticker, resolved, equity_rank=eq_rank, composite_z=composite_z
        )
        if score < 0:
            continue   # skip_ai_synthesis / skip_claude was set (double-safety)

        # Apply lane multiplier — always_include tickers are exempt (open positions)
        if ticker not in always_set:
            multiplier = _LANE_PRIORITY_MULTIPLIER.get(lane, 0.80)
            score = score * multiplier

        # TRD-068: Governance priority multipliers (bounded, explainable)
        if ticker not in always_set:
            if gov_state == "A_LIST":
                score = score * GOVERNANCE_A_LIST_MULTIPLIER
            elif gov_state == "PROBATION":
                score = score * GOVERNANCE_PROBATION_MULTIPLIER

        _is_lane_override = ticker in _lane_override_tickers
        scored.append({
            "ticker":                          ticker,
            "priority_score":                  score,
            "signal_agreement_score":          float(resolved.get("signal_agreement_score", 0) or 0),
            "prob_combined":                   resolved.get("prob_combined"),
            "pre_resolved_direction":          resolved.get("pre_resolved_direction", "NEUTRAL"),
            "pre_resolved_confidence":         float(resolved.get("pre_resolved_confidence", 0) or 0),
            "equity_rank":                     eq_rank,
            "composite_z":                     composite_z,
            "override_flags":                  list(resolved.get("override_flags", []) or []),
            "module_votes":                    resolved.get("module_votes", {}) or {},
            "_always_include":                 ticker in always_set,
            "selection_reason":                "",
            "candidate_lane":                  lane,
            "governance_state":                gov_state,
            "open_position_lane_override":     _is_lane_override,
            "degraded_position_review_required": _is_lane_override,
        })
    if _lane_excluded_skipped:
        logger.info("Lane gate: excluded %d lane_excluded/hard_excluded tickers from AI selection",
                    _lane_excluded_skipped)
    if _governance_skipped:
        logger.info("Governance gate: excluded %d QUARANTINE tickers from AI selection",
                    _governance_skipped)
    if _lane_override_count:
        logger.info(
            "Open-position lane overrides: %d — %s",
            _lane_override_count,
            ", ".join(f"{t}({v})" for t, v in _lane_override_tickers.items()),
        )

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
            _ticker_lane = _lane_lookup.get(always_t, "unknown")
            if _ticker_lane == "hard_excluded":
                # hard_excluded is a PM hard stop — enforce even on inject path.
                logger.warning(
                    "Open position %s is hard_excluded — hard gate enforced (inject path); "
                    "review lane classification or clear position from open list",
                    always_t,
                )
                continue
            _is_inject_override = _ticker_lane == "lane_excluded"
            if _is_inject_override:
                _lane_override_count += 1
                _lane_override_tickers[always_t] = _ticker_lane
                logger.warning(
                    "Open position override: %s is lane_excluded — included for review "
                    "(inject path, open_position_lane_override=True)",
                    always_t,
                )
            resolved = resolved_all.get(always_t, {})
            eq_data  = eq_lookup.get(always_t, {})
            eq_rank  = eq_data.get("equity_rank")
            cz       = eq_data.get("composite_z")
            _gov     = _governance_lookup.get(always_t, "STANDARD")
            always_in_scored.append({
                "ticker":                          always_t,
                "priority_score":                  compute_priority_score(
                                                       always_t,
                                                       resolved or {"signal_agreement_score": 0.0, "skip_claude": False},
                                                       equity_rank=eq_rank,
                                                       composite_z=cz,
                                                   ),
                "signal_agreement_score":          float(resolved.get("signal_agreement_score", 0) or 0),
                "prob_combined":                   resolved.get("prob_combined"),
                "pre_resolved_direction":          resolved.get("pre_resolved_direction", "NEUTRAL"),
                "pre_resolved_confidence":         float(resolved.get("pre_resolved_confidence", 0) or 0),
                "equity_rank":                     eq_rank,
                "composite_z":                     cz,
                "override_flags":                  list(resolved.get("override_flags", []) or []),
                "module_votes":                    resolved.get("module_votes", {}) or {},
                "_always_include":                 True,
                "selection_reason":                "",
                "candidate_lane":                  _ticker_lane,
                "governance_state":                _gov,
                "open_position_lane_override":     _is_inject_override,
                "degraded_position_review_required": _is_inject_override,
            })

    # TRD-058: Adaptive capacity — drive count from qualified strong names.
    # max_tickers is a hard caller cap; adaptive logic operates within it.
    #
    # _caller_cap  = min(max_tickers, CAPACITY_MAX)   — hard ceiling
    # _floor       = min(CAPACITY_MIN, max_tickers)   — minimum, bounded by caller
    # n_normal_slots = max(_floor, min(_caller_cap, strong_count))
    #
    # Result:
    #   weak day  (strong_count < CAPACITY_MIN): selects _floor
    #   normal day: selects strong_count, bounded by [_floor, _caller_cap]
    #   strong day (strong_count ≥ _caller_cap): selects _caller_cap
    strong_count = sum(
        1 for s in normal
        if s.get("priority_score", 0) >= AI_QUANT_SCORE_THRESHOLD_HIGH
    )
    _caller_cap = min(max_tickers, AI_QUANT_CAPACITY_MAX)
    _floor      = min(AI_QUANT_CAPACITY_MIN, max_tickers)
    n_normal_slots = max(_floor, min(_caller_cap, strong_count))
    logger.info(
        "Adaptive capacity: max=%d strong=%d → slots=%d (floor=%d cap=%d)",
        max_tickers, strong_count, n_normal_slots, _floor, _caller_cap,
    )

    # Build final top-N: open positions are additive — always n_normal_slots fresh
    # signals plus every open position on top (total = n_normal_slots + n_open).
    top = normal[:n_normal_slots] + always_in_scored

    # ── Step 8: Re-sort by priority; no cap — open positions are additive ─────
    top.sort(key=lambda x: x["priority_score"], reverse=True)

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

    # ── Step 10: Inject event-queue candidates ────────────────────────────────
    # Event-queue tickers are fresh catalyst breakouts that may not be in
    # resolved_signals.json.  They are appended after normal selection (bounded
    # by event_queue_max_slots) so they do not displace normal ranked names.
    if event_queue:
        eq_max = (event_queue_max_slots
                  if event_queue_max_slots is not None
                  else EVENT_QUEUE_MAX_AI_SLOTS)
        selected_tickers = {s["ticker"] for s in top}
        eq_added = 0
        for eq_entry in event_queue:
            if eq_added >= eq_max:
                break
            eq_ticker = eq_entry.get("ticker", "").upper().strip()
            if not eq_ticker or eq_ticker in selected_tickers:
                continue
            reason_str = eq_entry.get("reason", "fresh_catalyst_breakout")
            # Pull metadata from resolved_signals if available (falls back gracefully)
            resolved = resolved_all.get(eq_ticker, {})
            eq_data  = eq_lookup.get(eq_ticker, {})
            top.append({
                "ticker":                  eq_ticker,
                "priority_score":          float(eq_entry.get("score", 0.0)),
                "signal_agreement_score":  float(resolved.get("signal_agreement_score", 0) or 0),
                "pre_resolved_direction":  resolved.get("pre_resolved_direction", "NEUTRAL"),
                "pre_resolved_confidence": float(resolved.get("pre_resolved_confidence", 0) or 0),
                "equity_rank":             eq_data.get("equity_rank"),
                "composite_z":             eq_data.get("composite_z"),
                "override_flags":          list(resolved.get("override_flags", []) or []),
                "selection_reason":        f"fresh_catalyst_breakout | {reason_str}",
                "_event_queue":            True,
            })
            selected_tickers.add(eq_ticker)
            eq_added += 1
        if eq_added:
            print(f"Event queue: {eq_added} catalyst candidate(s) added for Deep Dive review")

    # ── Step 11: Print summary table ──────────────────────────────────────────
    _print_selection_table(top, total_input=total_loaded, max_tickers=len(top))

    # Strip internal bookkeeping keys before returning
    for s in top:
        s.pop("_always_include", None)
        s.pop("module_votes",    None)
        s.pop("_event_queue",    None)

    return top
