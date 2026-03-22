#!/usr/bin/env python3
"""
================================================================================
CONFLICT RESOLVER v1.0 — Deterministic Signal Arbitration Layer
================================================================================
Runs BEFORE the Claude API call in ai_quant.py.

Reduces Claude API cost (~$0.04/ticker) and improves synthesis quality by:
  1. Pre-resolving obvious conflicts via a weighted vote across 8 signal modules
  2. Applying 4 hard override rules for well-defined risk conditions
  3. Setting skip_claude=True for trivially blocked situations so ai_quant
     returns a templated neutral thesis without touching the API

CALIBRATION:
  MODULE_WEIGHTS are initial priors. After 8–12 weeks of runs, analyse
  logs/conflict_resolution_YYYYMMDD.csv to compute per-module directional
  accuracy and recalibrate weights accordingly.
================================================================================
"""

import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ==============================================================================
# MODULE WEIGHTS
# ==============================================================================
# Weights represent the relative reliability of each module's directional vote.
# Weights sum to 1.0. Adjust in config.py as you gather per-module P&L attribution.

MODULE_WEIGHTS: Dict[str, float] = {
    "signal_engine_composite_z": 0.25,   # Most rigorous multi-factor model
    "fundamental_analysis":       0.20,   # Point-in-time fundamental quality
    "squeeze_screener":           0.15,   # Mechanical; good when data is clean
    "options_flow":               0.15,   # Real market positioning signal
    "cross_asset_divergence":     0.10,   # Macro relative context
    "polymarket":                 0.08,   # Prediction market (some resolution lag)
    "dark_pool_flow":             0.07,   # FINRA ATS institutional routing signal
    "sec_insider":                0.03,   # Directional but very noisy
    "congress_trades":            0.01,   # 30–45 day disclosure lag (reduced; weight given to dark_pool)
    # NOTE: weights intentionally sum to 1.04 after addition of dark_pool_flow.
    # The weighted-vote algorithm compares bull_weight vs bear_weight with a margin
    # threshold and does not require exact sum-to-1. Recalibrate after 8-12 weeks
    # of runs using logs/conflict_resolution_YYYYMMDD.csv.
}

# Maps MODULE_WEIGHTS keys → signals dict keys (as returned by collect_all_signals)
_SIGNALS_KEY_MAP: Dict[str, str] = {
    "signal_engine_composite_z": "signal_engine",
    "fundamental_analysis":       "fundamentals",
    "squeeze_screener":           "squeeze",
    "options_flow":               "options_flow",
    "cross_asset_divergence":     "cross_asset",
    "polymarket":                 "polymarket",
    "dark_pool_flow":             "dark_pool_flow",
    "sec_insider":                "sec",
    "congress_trades":            "congress",
}

# Log output directory
_LOG_DIR = Path(__file__).parent / "logs"


# ==============================================================================
# INTERNAL HELPERS
# ==============================================================================

def _get_days_to_earnings(ticker: str) -> Optional[int]:
    """
    Return days until next earnings announcement via yfinance calendar.
    Returns None if data is unavailable or the ticker has no options/calendar.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        cal = t.calendar
        if cal is None:
            return None

        # yfinance returns either a DataFrame (older) or dict (newer)
        if hasattr(cal, "index") and hasattr(cal, "T"):  # DataFrame
            # Transpose: columns = fields, index = 0/1 (low/high estimate dates)
            try:
                cal_t = cal.T if "Earnings Date" in cal.index else cal
                dates = cal_t.get("Earnings Date")
                if dates is not None:
                    for d in (dates if hasattr(dates, "__iter__") else [dates]):
                        if hasattr(d, "date"):
                            delta = (d.date() - datetime.now().date()).days
                            return delta
            except Exception:
                pass

        if isinstance(cal, dict):
            earn_dates = cal.get("Earnings Date", [])
            if earn_dates:
                d = earn_dates[0]
                if hasattr(d, "date"):
                    return (d.date() - datetime.now().date()).days

        return None
    except Exception as exc:
        logger.debug("_get_days_to_earnings(%s): %s", ticker, exc)
        return None


def _is_earnings_catalyst(signals_dict: dict) -> bool:
    """
    Returns True if the current thesis appears to BE an earnings play,
    in which case the pre-earnings hold override should not fire.

    Heuristics:
      - Polymarket market question mentions earnings / EPS / beat
      - Catalyst screener flags mention earnings
    """
    poly = signals_dict.get("polymarket") or {}
    question = str(poly.get("polymarket_market", "")).lower()
    if any(kw in question for kw in ("earnings", "eps", "revenue beat", "beat estimates", "beat consensus")):
        return True

    catalyst = signals_dict.get("catalyst") or {}
    all_flags = (
        catalyst.get("short_squeeze_flags", [])
        + catalyst.get("vol_compression_flags", [])
    )
    for flag in all_flags:
        if "earnings" in str(flag).lower():
            return True

    return False


# ==============================================================================
# STEP 1: DIRECTION EXTRACTION PER MODULE
# ==============================================================================

def extract_module_direction(module_name: str, module_output: dict) -> Optional[str]:
    """
    Map a single module's output to 'BULL', 'BEAR', or None (no clear signal).

    Rules:
      signal_engine_composite_z : composite_z > 0.5 → BULL | < -0.5 → BEAR
      fundamental_analysis       : score% > 65 → BULL | < 35 → BEAR
      squeeze_screener           : final_score > 55 → BULL (squeezes are directionally BULL only)
      options_flow               : heat > 65 → then PCR < 0.7 → BULL | PCR > 1.8 → BEAR
      cross_asset_divergence     : 'BOTTOM' → BULL | 'TOP' → BEAR | else None
      polymarket                 : score > 0.6 AND prob > 0.65 → BULL | prob < 0.35 → BEAR
      congress_trades            : direction 'bullish' or score > 0 → BULL | 'bearish' → BEAR
      sec_insider                : score > 0 with buy/activist flags → BULL
                                   (sell detection not in current sec_module implementation)
    """
    if not module_output:
        return None

    # ── signal_engine composite z-score ─────────────────────────────────────
    if module_name == "signal_engine_composite_z":
        z = module_output.get("composite_z")
        if z is None:
            return None
        if z > 0.5:
            return "BULL"
        if z < -0.5:
            return "BEAR"
        return None

    # ── fundamental_analysis score ───────────────────────────────────────────
    if module_name == "fundamental_analysis":
        score = module_output.get("fundamental_score_pct")
        if score is None:
            return None
        if score > 65:
            return "BULL"
        if score < 35:
            return "BEAR"
        return None

    # ── squeeze_screener ─────────────────────────────────────────────────────
    # Squeezes are directionally BULL only — never map a squeeze score to BEAR.
    # A low squeeze score simply means no squeeze setup, not a bearish signal.
    if module_name == "squeeze_screener":
        score = module_output.get("squeeze_score_100")
        if score is None:
            return None
        if score > 55:
            return "BULL"
        return None

    # ── options_flow ─────────────────────────────────────────────────────────
    # Heat > 65 qualifies the options activity as "significant"; then the
    # put/call ratio determines direction.
    if module_name == "options_flow":
        heat = module_output.get("heat_score")
        if heat is None or heat <= 65:
            return None
        pcr = module_output.get("pc_ratio")
        if pcr is None:
            return None
        if pcr < 0.7:
            return "BULL"   # Call-heavy = market expects up move
        if pcr > 1.8:
            return "BEAR"   # Put-heavy = market expects down move
        return None

    # ── cross_asset_divergence ───────────────────────────────────────────────
    if module_name == "cross_asset_divergence":
        signal = str(module_output.get("signal", "")).upper()
        if "BOTTOM" in signal:
            return "BULL"
        if "TOP" in signal:
            return "BEAR"
        return None

    # ── polymarket ───────────────────────────────────────────────────────────
    # signal_score in ai_quant is on a 0–5 scale; > 0.6 means any non-trivial signal.
    # Directional inference comes primarily from probability.
    if module_name == "polymarket":
        score = float(module_output.get("polymarket_score", 0) or 0)
        prob  = float(module_output.get("polymarket_probability", 0) or 0)
        if score > 0.6 and prob > 0.65:
            return "BULL"
        if prob < 0.35:
            return "BEAR"
        return None

    # ── congress_trades ──────────────────────────────────────────────────────
    # congress_direction comes from score_congress_signal; fall back to
    # score > 0 (any buys detected) when direction field is absent.
    if module_name == "congress_trades":
        direction = str(module_output.get("congress_direction", "neutral")).lower()
        if "bull" in direction:
            return "BULL"
        if "bear" in direction:
            return "BEAR"
        # Fallback: positive score means congressional buys were detected
        score = float(module_output.get("congress_score", 0) or 0)
        if score > 0:
            return "BULL"
        return None

    # ── dark_pool_flow ───────────────────────────────────────────────────────
    # FINRA ATS short volume institutional flow signal.
    # ACCUMULATION (score >= threshold) → BULL; DISTRIBUTION (score <= threshold) → BEAR.
    if module_name == "dark_pool_flow":
        dp_score = module_output.get("dark_pool_score")
        dp_signal = str(module_output.get("signal", "")).upper()
        # Prefer the pre-classified signal label; fall back to raw score thresholds
        if dp_signal == "ACCUMULATION":
            return "BULL"
        if dp_signal == "DISTRIBUTION":
            return "BEAR"
        # Fallback via raw score (uses same thresholds as dark_pool_flow.py)
        if dp_score is not None:
            try:
                from config import DARK_POOL_ACCUMULATION_THRESHOLD, DARK_POOL_DISTRIBUTION_THRESHOLD
            except ImportError:
                DARK_POOL_ACCUMULATION_THRESHOLD = 65
                DARK_POOL_DISTRIBUTION_THRESHOLD = 35
            if dp_score >= DARK_POOL_ACCUMULATION_THRESHOLD:
                return "BULL"
            if dp_score <= DARK_POOL_DISTRIBUTION_THRESHOLD:
                return "BEAR"
        return None

    # ── sec_insider ──────────────────────────────────────────────────────────
    # Current sec_module only scores positive insider events (buys, activists).
    # It does not independently classify sells; a score > 0 with buy flags → BULL.
    if module_name == "sec_insider":
        sec_score = float(module_output.get("score", 0) or 0)
        if sec_score <= 0:
            return None
        flags = [str(f).lower() for f in module_output.get("flags", [])]
        if any(
            kw in f for f in flags
            for kw in ("buying", "purchase", "activist", "insider buy", "cluster")
        ):
            return "BULL"
        return None

    return None


# ==============================================================================
# STEP 2: WEIGHTED VOTE
# ==============================================================================

def compute_weighted_vote(signals_dict: dict) -> dict:
    """
    Run extract_module_direction for every module and aggregate into a net view.

    Returns:
      bull_weight        — sum of MODULE_WEIGHTS for all BULL modules
      bear_weight        — sum of MODULE_WEIGHTS for all BEAR modules
      net_direction      — 'BULL' if bull > bear + 0.1, 'BEAR' if bear > bull + 0.1, else 'NEUTRAL'
      confidence         — abs(bull - bear) / (bull + bear + epsilon)
      agreement_fraction — fraction of voting modules that agree with net_direction
      module_votes       — dict of module_name → direction (or None)
    """
    bull_weight = 0.0
    bear_weight = 0.0
    module_votes: Dict[str, Optional[str]] = {}

    for module_name, weight in MODULE_WEIGHTS.items():
        signals_key = _SIGNALS_KEY_MAP[module_name]
        module_output = signals_dict.get(signals_key) or {}
        direction = extract_module_direction(module_name, module_output)
        module_votes[module_name] = direction
        if direction == "BULL":
            bull_weight += weight
        elif direction == "BEAR":
            bear_weight += weight

    # Net direction requires a margin of ≥ 0.10 to avoid noise-driven calls
    margin = 0.10
    if bull_weight > bear_weight + margin:
        net_direction = "BULL"
    elif bear_weight > bull_weight + margin:
        net_direction = "BEAR"
    else:
        net_direction = "NEUTRAL"

    # Confidence: normalised distance between bull and bear weight totals
    total = bull_weight + bear_weight
    confidence = abs(bull_weight - bear_weight) / (total + 0.001)

    # Agreement fraction over modules that actually cast a vote
    voting_directions = [v for v in module_votes.values() if v is not None]
    agreeing = [v for v in voting_directions if v == net_direction]
    agreement_fraction = (len(agreeing) / len(voting_directions)) if voting_directions else 0.0

    return {
        "bull_weight":        round(bull_weight, 4),
        "bear_weight":        round(bear_weight, 4),
        "net_direction":      net_direction,
        "confidence":         round(confidence, 4),
        "agreement_fraction": round(agreement_fraction, 4),
        "module_votes":       module_votes,
        "override_flags":     [],
        "skip_claude":        False,
        "max_conviction_override": None,
        "position_size_pct":  None,
    }


# ==============================================================================
# STEP 3: HARD OVERRIDES
# ==============================================================================

def apply_hard_overrides(
    vote_result: dict,
    signals_dict: dict,
    regime: str,
    ticker: str = "",
) -> dict:
    """
    Apply 4 hard override rules after the weighted vote.

    Override 1 — Post-squeeze guard (cross-module):
      squeeze.recent_squeeze == True → direction NEUTRAL, skip_claude=True
      Rationale: squeeze already played out; remaining setup is noise/reversal.

    Override 2 — Bear market circuit breaker:
      regime == 'RISK_OFF' → cap max_conviction at 2, cap position_size at 3%.
      Does NOT set skip_claude — Claude still provides a valid (cautious) thesis.

    Override 3 — Pre-earnings hold:
      days_to_earnings < 5 AND thesis is NOT the earnings play →
      direction NEUTRAL, position_size=0, skip_claude=True
      Rationale: binary event risk; hold until uncertainty resolves.

    Override 4 — Squeeze-driven vs organic demand (context-only):
      squeeze_score > 60 AND 1M momentum < 0.3% →
      Add 'context: squeeze_driven_not_organic' flag. Direction is NOT changed.
      Claude should weigh this distinction but it's informational, not a block.
    """
    result = dict(vote_result)   # shallow copy; vote_result lists are shared (ok — we append)

    squeeze = signals_dict.get("squeeze") or {}
    tech    = signals_dict.get("technical") or {}

    # ── Override 1: Post-squeeze guard ──────────────────────────────────────
    if squeeze.get("recent_squeeze") is True:
        result["net_direction"] = "NEUTRAL"
        result["skip_claude"]   = True
        result["override_flags"].append("override: post_squeeze_guard")
        logger.debug("[%s] Override 1: post_squeeze_guard fired", ticker)

    # ── Override 2: Bear market circuit breaker ──────────────────────────────
    if str(regime).upper() == "RISK_OFF":
        result["max_conviction_override"] = 2
        # min(3%, current) — since we don't know Claude's answer yet, we set the cap
        result["position_size_pct"] = 3.0
        result["override_flags"].append("override: bear_market_circuit_breaker")
        logger.debug("[%s] Override 2: bear_market_circuit_breaker fired", ticker)

    # ── Override 3: Pre-earnings hold ────────────────────────────────────────
    # Skip if Override 1 already set skip_claude (avoids redundant yfinance call)
    if not result["skip_claude"]:
        days_to_earn = _get_days_to_earnings(ticker) if ticker else None
        if days_to_earn is not None and days_to_earn < 5:
            if not _is_earnings_catalyst(signals_dict):
                result["net_direction"]   = "NEUTRAL"
                result["position_size_pct"] = 0.0
                result["skip_claude"]     = True
                result["override_flags"].append(
                    f"override: pre_earnings_hold (earnings in {days_to_earn}d)"
                )
                logger.debug(
                    "[%s] Override 3: pre_earnings_hold fired (days_to_earn=%s)",
                    ticker, days_to_earn,
                )
            else:
                result["override_flags"].append(
                    f"context: earnings_in_{days_to_earn}d_IS_the_thesis"
                )

    # ── Override 4: Squeeze-driven vs organic demand (context only) ──────────
    squeeze_score = float(squeeze.get("squeeze_score_100", 0) or 0)
    mom_1m        = float(tech.get("momentum_1m_pct", 0) or 0)
    if squeeze_score > 60 and mom_1m < 0.3:
        result["override_flags"].append("context: squeeze_driven_not_organic")
        logger.debug(
            "[%s] Override 4: squeeze_driven context flag (score=%.1f, mom_1m=%.2f%%)",
            ticker, squeeze_score, mom_1m,
        )

    return result


# ==============================================================================
# STEP 4: MAIN ENTRY POINT
# ==============================================================================

def resolve(signals_dict: dict, regime: str) -> dict:
    """
    Main entry point. Returns a resolver dict to be injected into the signals
    packet before building the Claude prompt.

    All original signals are unchanged — this adds a 'conflict_resolution' key.

    Returned dict fields:
      pre_resolved_direction  — 'BULL' | 'BEAR' | 'NEUTRAL'
      pre_resolved_confidence — float 0–1
      signal_agreement_score  — fraction of voting modules that agree with net direction
      override_flags          — list of override/context flag strings
      module_votes            — dict: module_name → direction (or None = no signal)
      bull_weight             — sum of BULL module weights
      bear_weight             — sum of BEAR module weights
      skip_claude             — True = return templated neutral thesis, skip API call
      max_conviction_override — int cap (or None = no cap from resolver)
      position_size_override  — float cap in pct (or None = no cap from resolver)
    """
    ticker = str(signals_dict.get("ticker", ""))

    vote     = compute_weighted_vote(signals_dict)
    resolved = apply_hard_overrides(vote, signals_dict, regime, ticker=ticker)

    result = {
        "pre_resolved_direction":  resolved["net_direction"],
        "pre_resolved_confidence": resolved["confidence"],
        "signal_agreement_score":  resolved["agreement_fraction"],
        "override_flags":          resolved["override_flags"],
        "module_votes":            resolved["module_votes"],
        "bull_weight":             resolved["bull_weight"],
        "bear_weight":             resolved["bear_weight"],
        "skip_claude":             resolved["skip_claude"],
        "max_conviction_override": resolved.get("max_conviction_override"),
        "position_size_override":  resolved.get("position_size_pct"),
    }

    _log_resolution(ticker, resolved)
    return result


# ==============================================================================
# LOGGING
# ==============================================================================

def _log_resolution(ticker: str, resolved: dict) -> None:
    """
    Append one row to logs/conflict_resolution_YYYYMMDD.csv.

    Columns:
      timestamp, ticker, pre_resolved, confidence, bull_weight, bear_weight,
      overrides, claude_skipped

    After 8–12 weeks of runs this file will contain enough data to compute
    per-module directional accuracy for MODULE_WEIGHTS recalibration.
    """
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        today    = datetime.now().strftime("%Y%m%d")
        log_path = _LOG_DIR / f"conflict_resolution_{today}.csv"
        write_header = not log_path.exists()

        overrides_str = "; ".join(resolved.get("override_flags", [])) or "none"

        with open(log_path, "a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow([
                    "timestamp", "ticker", "pre_resolved", "confidence",
                    "bull_weight", "bear_weight", "overrides", "claude_skipped",
                ])
            writer.writerow([
                datetime.now().isoformat(),
                ticker,
                resolved.get("net_direction", "NEUTRAL"),
                round(resolved.get("confidence", 0.0), 4),
                round(resolved.get("bull_weight", 0.0), 4),
                round(resolved.get("bear_weight", 0.0), 4),
                overrides_str,
                resolved.get("skip_claude", False),
            ])
    except Exception as exc:
        logger.warning("Failed to write conflict resolution log: %s", exc)
