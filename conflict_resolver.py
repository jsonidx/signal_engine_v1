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

import contextlib
import csv
import io
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-loaded module weights — loaded once from Supabase strategy_config,
# falls back to the hardcoded MODULE_WEIGHTS dict below.
# ---------------------------------------------------------------------------
_weights_cache: Optional[Dict[str, float]] = None


def _load_module_weights() -> Dict[str, float]:
    """
    Load module weights from Supabase strategy_config (key='module_weights').
    Falls back to the hardcoded MODULE_WEIGHTS on any error.
    Result is cached for the lifetime of the process.
    """
    global _weights_cache
    if _weights_cache is not None:
        return _weights_cache
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT value FROM strategy_config WHERE key='module_weights'")
        row = cur.fetchone()
        conn.close()
        if row and row["value"]:
            loaded = json.loads(row["value"])
            if loaded and isinstance(loaded, dict):
                # Filter to only known modules (removes pruned keys like cross_asset_divergence)
                loaded = {k: v for k, v in loaded.items() if k in _SIGNALS_KEY_MAP}
                if loaded:
                    # Normalize so weights always sum to 1.0 after filtering
                    total = sum(loaded.values())
                    if total > 0:
                        loaded = {k: v / total for k, v in loaded.items()}
                    _weights_cache = loaded
                    logger.debug("module_weights loaded from Supabase strategy_config")
                    return _weights_cache
    except Exception as exc:
        logger.debug("Could not load module_weights from Supabase: %s — using defaults", exc)
    _weights_cache = MODULE_WEIGHTS
    return MODULE_WEIGHTS


# ==============================================================================
# MODULE WEIGHTS
# ==============================================================================
# Weights represent the relative reliability of each module's directional vote.
# Weights sum to 1.0. Adjust in config.py as you gather per-module P&L attribution.

MODULE_WEIGHTS: Dict[str, float] = {
    "signal_engine_composite_z": 0.35,   # Most rigorous multi-factor model
    "fundamental_analysis":       0.20,   # Point-in-time fundamental quality
    "squeeze_screener":           0.15,   # Mechanical; good when data is clean
    "options_flow":               0.15,   # Real market positioning signal
    "dark_pool_flow":             0.08,   # FINRA ATS accumulation flag (zscore < -1.5)
    "red_flag_screener":          0.08,   # Accounting quality — BEAR-only vote
    "sec_insider":                0.04,   # Directional but noisy
    # NOTE: cross_asset_divergence, congress_trades, polymarket removed (pruning pass 2026-04).
}

# Maps MODULE_WEIGHTS keys → signals dict keys (as returned by collect_all_signals)
_SIGNALS_KEY_MAP: Dict[str, str] = {
    "signal_engine_composite_z": "signal_engine",
    "fundamental_analysis":       "fundamentals",
    "squeeze_screener":           "squeeze",
    "options_flow":               "options_flow",
    "dark_pool_flow":             "dark_pool_flow",
    "red_flag_screener":          "red_flags",
    "sec_insider":                "sec",
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
      dark_pool_flow             : signal == ACCUMULATION → BULL
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

    # ── dark_pool_flow ───────────────────────────────────────────────────────
    # FINRA ATS accumulation flag: signal == ACCUMULATION → BULL, else no vote.
    if module_name == "dark_pool_flow":
        dp_signal = str(module_output.get("signal", "")).upper()
        if dp_signal == "ACCUMULATION":
            return "BULL"
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

    # ── red_flag_screener ─────────────────────────────────────────────────────
    # BEAR-only module: significant accounting risk overrides bull signals.
    # Only votes BEAR when risk_level is WARNING or CRITICAL.
    # Never votes BULL — clean accounting is baseline expectation, not a positive.
    if module_name == "red_flag_screener":
        risk_level = str(module_output.get("red_flag_risk_level", "")).upper()
        rf_score = float(module_output.get("red_flag_score", 0) or 0)
        if risk_level in ("WARNING", "CRITICAL") or rf_score >= 46:
            return "BEAR"
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

    active_weights = _load_module_weights()
    for module_name, weight in active_weights.items():
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

    # Agreement fraction over modules that actually cast a vote.
    # For NEUTRAL outcomes no module can vote NEUTRAL, so use the larger side's
    # count — conveys "this many modules agreed on the strongest direction".
    voting_directions = [v for v in module_votes.values() if v is not None]
    if voting_directions:
        if net_direction == "NEUTRAL":
            bull_count = voting_directions.count("BULL")
            bear_count = voting_directions.count("BEAR")
            agreement_fraction = max(bull_count, bear_count) / len(voting_directions)
        else:
            agreeing = [v for v in voting_directions if v == net_direction]
            agreement_fraction = len(agreeing) / len(voting_directions)
    else:
        agreement_fraction = 0.0

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
    Apply 5 hard override rules after the weighted vote.

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

    # ── Override 5: M&A / acquisition pin filter ─────────────────────────────
    # Stocks undergoing acquisition trade pinned at the bid price — which often
    # sits at or very near their 52-week high — with no meaningful 5d price action.
    # Analysing them wastes API budget; their direction is determined by deal terms.
    # Fast path: only call yfinance when pct_from_high >= -1% (price near 52wk high).
    if not result["skip_claude"] and ticker:
        pct_from_high = tech.get("pct_from_high")
        # Also trigger on longBusinessSummary M&A keywords (via already-fetched info)
        _ma_keyword = False
        try:
            import yfinance as _yf
            with contextlib.redirect_stderr(io.StringIO()):
                _info = _yf.Ticker(ticker).info
            _summary = (_info.get("longBusinessSummary") or "").lower()
            _ma_keyword = any(
                kw in _summary
                for kw in ["acquired by", "merger with", "no longer traded", "acquisition by"]
            )
        except Exception:
            pass

        _near_52wk_high = pct_from_high is not None and pct_from_high >= -1.0
        if _near_52wk_high or _ma_keyword:
            try:
                import yfinance as _yf
                _hist5d = _yf.Ticker(ticker).history(period="5d")
                if _hist5d.empty or _ma_keyword:
                    result["net_direction"] = "NEUTRAL"
                    result["skip_claude"]   = True
                    _reason = "ma_keyword" if _ma_keyword else "price_pinned_at_52wk_high_no_5d_data"
                    result["override_flags"].append(
                        f"override: ma_acquisition_pin ({_reason})"
                    )
                    logger.info(
                        "[%s] Override 5: ma_acquisition_pin fired (pct_from_high=%.2f%%, keyword=%s)",
                        ticker, pct_from_high or 0.0, _ma_keyword,
                    )
            except Exception as _e:
                logger.debug("[%s] Override 5: yfinance check failed: %s", ticker, _e)

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

    today = datetime.now().strftime("%Y-%m-%d")
    _log_resolution(ticker, resolved)
    _save_resolution_cache(ticker, today, regime, result)
    return result


# ==============================================================================
# RESOLUTION CACHE (Supabase — global shared cache)
# ==============================================================================

def get_cached_resolution(ticker: str, date: str) -> Optional[dict]:
    """
    Return the cached conflict-resolution result for ticker+date, or None.

    Used to avoid re-computing the weighted vote for tickers already resolved
    today (e.g., when multiple users request the same ticker on the same day).
    """
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM resolution_cache WHERE ticker=%s AND date=%s",
            (ticker.upper(), date),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            # JSONB columns are returned as already-parsed Python objects by psycopg2
            flags = row["override_flags"]
            votes = row["module_votes"]
            return {
                "pre_resolved_direction":  row["pre_resolved_direction"],
                "pre_resolved_confidence": row["confidence"],
                "signal_agreement_score":  row["signal_agreement_score"],
                "override_flags":          flags if isinstance(flags, list) else json.loads(flags or "[]"),
                "module_votes":            votes if isinstance(votes, dict) else json.loads(votes or "{}"),
                "bull_weight":             row["bull_weight"],
                "bear_weight":             row["bear_weight"],
                "skip_claude":             row["skip_claude"],
                "max_conviction_override": row["max_conviction_override"],
                "position_size_override":  row["position_size_override"],
            }
    except Exception as exc:
        logger.debug("get_cached_resolution(%s): %s", ticker, exc)
    return None


def _save_resolution_cache(ticker: str, date: str, regime: str, result: dict) -> None:
    """Upsert conflict-resolution result into Supabase resolution_cache."""
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO resolution_cache
                (ticker, date, regime, pre_resolved_direction, confidence,
                 signal_agreement_score, override_flags, module_votes,
                 bull_weight, bear_weight, skip_claude,
                 max_conviction_override, position_size_override, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(ticker, date) DO UPDATE SET
                regime=excluded.regime,
                pre_resolved_direction=excluded.pre_resolved_direction,
                confidence=excluded.confidence,
                signal_agreement_score=excluded.signal_agreement_score,
                override_flags=excluded.override_flags,
                module_votes=excluded.module_votes,
                bull_weight=excluded.bull_weight,
                bear_weight=excluded.bear_weight,
                skip_claude=excluded.skip_claude,
                max_conviction_override=excluded.max_conviction_override,
                position_size_override=excluded.position_size_override,
                created_at=excluded.created_at
            """,
            (
                ticker.upper(),
                date,
                regime,
                result.get("pre_resolved_direction"),
                result.get("pre_resolved_confidence"),
                result.get("signal_agreement_score"),
                json.dumps(result.get("override_flags") or []),
                json.dumps(result.get("module_votes") or {}),
                result.get("bull_weight"),
                result.get("bear_weight"),
                result.get("skip_claude"),
                result.get("max_conviction_override"),
                result.get("position_size_override"),
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("_save_resolution_cache(%s): %s", ticker, exc)


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


# ==============================================================================
# CLI ENTRY POINT
# ==============================================================================

def _load_watchlist(path: str = "./watchlist.txt") -> list:
    tickers = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                t = line.split("#")[0].strip().upper()
                if t:
                    tickers.append(t)
    except FileNotFoundError:
        pass
    return list(dict.fromkeys(tickers))


def _load_ai_quant_signals(ticker: str, db_path: str = None) -> dict:
    """Pull latest signals_json for a ticker from thesis_cache (Supabase)."""
    try:
        import json as _json
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT signals_json FROM thesis_cache WHERE ticker=%s ORDER BY date DESC LIMIT 1",
            (ticker,)
        )
        row = cur.fetchone()
        conn.close()
        if row and row["signals_json"]:
            return _json.loads(row["signals_json"])
    except Exception:
        pass
    return {}


def main():
    import argparse, json as _json, sys

    parser = argparse.ArgumentParser(description="Conflict Resolver — deterministic signal arbitration")
    parser.add_argument("--pre-resolve", action="store_true",
                        help="Run pre-resolution pass for all watchlist tickers")
    parser.add_argument("--output", metavar="PATH", default="data/resolved_signals.json",
                        help="Output JSON file (default: data/resolved_signals.json)")
    parser.add_argument("--regime", default="TRANSITIONAL",
                        help="Market regime override (default: read from data/regime_latest.json)")
    args = parser.parse_args()

    if not args.pre_resolve:
        parser.print_help()
        sys.exit(0)

    # Load current regime
    regime = args.regime
    try:
        import json as _json2
        rj = _json2.load(open("data/regime_latest.json"))
        regime = rj.get("market", {}).get("regime", regime)
    except Exception:
        pass

    tickers = _load_watchlist()
    if not tickers:
        print("  No watchlist tickers found.")
        sys.exit(0)

    results = []
    for ticker in tickers:
        signals_dict = _load_ai_quant_signals(ticker)
        signals_dict["ticker"] = ticker
        try:
            r = resolve(signals_dict, regime)
            results.append({
                "ticker":                  ticker,
                "direction":               r["pre_resolved_direction"],
                "pre_resolved_direction":  r["pre_resolved_direction"],
                "confidence":              round(r["pre_resolved_confidence"], 4),
                "pre_resolved_confidence": round(r["pre_resolved_confidence"], 4),
                "signal_agreement_score":  round(r["signal_agreement_score"], 4),
                "override_flags":          r["override_flags"],
                "skip_claude":             r["skip_claude"],
                "bull_weight":             round(r["bull_weight"], 4),
                "bear_weight":             round(r["bear_weight"], 4),
            })
        except Exception as exc:
            logger.warning("resolve failed for %s: %s", ticker, exc)
            results.append({"ticker": ticker, "direction": "NEUTRAL", "pre_resolved_direction": "NEUTRAL",
                            "confidence": 0.0, "pre_resolved_confidence": 0.0,
                            "signal_agreement_score": 0.0, "override_flags": [], "skip_claude": False,
                            "bull_weight": 0.0, "bear_weight": 0.0})

    import pathlib
    pathlib.Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        _json.dump(results, f, indent=2)

    print(f"  Resolved {len(results)} tickers → {args.output}  (regime: {regime})")


if __name__ == "__main__":
    main()
