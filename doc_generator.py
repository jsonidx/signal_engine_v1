#!/usr/bin/env python3
"""
================================================================================
DOC GENERATOR — Signal Engine Internal Documentation Maintainer
================================================================================
Reads the live codebase and refreshes the auto-generated sections of
docs/INTERNALS.md without touching hand-written narrative sections.

WHAT IT UPDATES:
  - <!-- AUTO:HEADER -->        Last updated date, module count
  - <!-- AUTO:MODULE_INVENTORY --> Module table, line counts, docstrings,
                                  public function lists
  - <!-- AUTO:EQUITY_FACTORS --> Factor table pulled live from config.py
  - <!-- AUTO:CRYPTO_VOL_GATE --> Vol gate thresholds from config.py
  - <!-- AUTO:CONFIG -->         All config values from config.py

USAGE:
  python3 doc_generator.py           # Refresh auto sections only
  python3 doc_generator.py --full    # Also rebuild Scoring section from scratch
  python3 doc_generator.py --check   # Diff only — don't write, just show changes
  python3 doc_generator.py --quiet   # No output except errors

HOOK INTO run_master.sh:
  Add this line near the bottom of run_master.sh:
    python3 "$PROJECT_DIR/doc_generator.py" --quiet
================================================================================
"""

import argparse
import ast
import importlib.util
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ─── Paths ────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
DOCS_DIR     = PROJECT_ROOT / "docs"
INTERNALS_MD = DOCS_DIR / "INTERNALS.md"

# ─── Module registry ──────────────────────────────────────────────────────────
# (filename, category, manual_description_if_no_docstring)
MODULES = [
    # Screeners
    ("signal_engine.py",          "screener",   None),
    ("catalyst_screener.py",      "screener",   "Detects high-momentum setups: short squeeze, volume breakout, volatility compression"),
    ("options_flow.py",           "screener",   "Options heat ranking via IV rank, volume spike, expected move, and put/call ratio"),
    ("squeeze_screener.py",       "screener",   "Dedicated short squeeze candidate ranker (0–100 score)"),
    ("polymarket_screener.py",    "screener",   "Extracts financial catalyst signals from Polymarket prediction markets"),
    ("fundamental_analysis.py",   "screener",   "Quant-grade fundamental scorecard: valuation, growth, quality, balance sheet"),
    ("midweek_scan.py",           "screener",   "Wednesday evening mid-cycle update: market pulse, watchlist moves, catalyst check"),
    # Analysis
    ("ai_quant.py",               "analysis",   None),
    ("sec_module.py",             "analysis",   "Tracks insider transactions (Form 4), activist stakes (13D/G), material events (8-K)"),
    ("volume_profile.py",         "analysis",   "Volume-at-price analysis: POC, value area, HVN/LVN support/resistance, anchored VWAPs"),
    # Paper trading
    ("paper_trader.py",           "trading",    "Records weekly signals into SQLite, tracks P&L vs SPY benchmark"),
    ("trade_journal.py",          "trading",    "Computes ATR-based buy/sell zones; logs trades and unrealized P&L"),
    # Utilities
    ("fx_rates.py",               "utility",    "Multi-source EUR FX conversion: Yahoo Finance → ECB → Frankfurter, 30-min cache"),
    ("fundamentals_cache.py",     "utility",    "30-day SQLite cache for yfinance quarterly fundamentals"),
]

SCRIPTS = [
    ("config.py",       "Master configuration: portfolio parameters, factor weights, thresholds, API settings"),
    ("run_master.sh",   "10-step Sunday evening orchestration pipeline"),
    ("run_midweek.sh",  "Wednesday midweek scan launcher"),
    ("run_weekly.sh",   "Simplified weekly report runner"),
]


# ==============================================================================
# AST HELPERS
# ==============================================================================

def get_module_docstring(filepath: Path) -> Optional[str]:
    """Return the first line of the module-level docstring, or None."""
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)
        raw = ast.get_docstring(tree)
        if raw:
            # Keep only the first non-empty line (the title line)
            for line in raw.splitlines():
                line = line.strip()
                if line and not all(c == "=" for c in line):
                    return line
    except Exception:
        pass
    return None


def get_public_functions(filepath: Path) -> list[str]:
    """Return list of top-level public function/class names with their signatures."""
    results = []
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            # Only top-level (direct children of module)
            if not any(
                isinstance(parent, ast.Module)
                for parent in ast.walk(tree)
                if hasattr(parent, "body") and node in getattr(parent, "body", [])
            ):
                continue
            name = node.name
            if name.startswith("_"):
                continue
            if isinstance(node, ast.ClassDef):
                results.append(f"class `{name}`")
            else:
                # Build simple arg list (no defaults, no annotations for brevity)
                args = [a.arg for a in node.args.args if a.arg != "self"]
                results.append(f"`{name}({', '.join(args)})`")
    except Exception:
        pass
    return results


def count_lines(filepath: Path) -> int:
    try:
        return len(filepath.read_text(encoding="utf-8").splitlines())
    except Exception:
        return 0


# ==============================================================================
# CONFIG EXTRACTOR
# ==============================================================================

def load_config() -> dict:
    """Import config.py and return its namespace as a dict."""
    config_path = PROJECT_ROOT / "config.py"
    spec = importlib.util.spec_from_file_location("config", config_path)
    mod  = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"  [warn] Could not load config.py: {e}")
        return {}
    return {k: getattr(mod, k) for k in dir(mod) if not k.startswith("__")}


# ==============================================================================
# SECTION RENDERERS
# ==============================================================================

def render_header(cfg: dict) -> str:
    module_count = len(MODULES)
    return (
        f"**Last updated:** {datetime.now().strftime('%Y-%m-%d')}\n"
        f"**Generator version:** 1.0\n"
        f"**Modules tracked:** {module_count}"
    )


def render_module_inventory() -> str:
    categories = {
        "screener": ("Screeners", []),
        "analysis": ("Analysis & Signal Synthesis", []),
        "trading":  ("Paper Trading & Journaling", []),
        "utility":  ("Utilities & Data", []),
    }

    for filename, cat, fallback_desc in MODULES:
        filepath = PROJECT_ROOT / filename
        if not filepath.exists():
            desc = f"*(file not found)*"
        else:
            doc = get_module_docstring(filepath)
            desc = doc or fallback_desc or "No module docstring"
        lines = count_lines(filepath) if filepath.exists() else "—"
        categories[cat][1].append((filename, lines, desc))

    blocks = []
    for cat_key, (cat_title, entries) in categories.items():
        rows = "\n".join(
            f"| [{fn}](../{fn}) | {lines} | {desc} |"
            for fn, lines, desc in entries
        )
        blocks.append(
            f"### {cat_title}\n\n"
            f"| File | Lines | Description |\n"
            f"|------|-------|-------------|\n"
            f"{rows}"
        )

    # Scripts table (no line count)
    script_rows = "\n".join(
        f"| [{fn}](../{fn}) | {desc} |" for fn, desc in SCRIPTS
    )
    blocks.append(
        "### Configuration & Scripts\n\n"
        "| File | Description |\n"
        "|------|-------------|\n"
        + script_rows
    )

    # Per-module function index
    blocks.append("### Public Functions Per Module")
    for filename, cat, _ in MODULES:
        filepath = PROJECT_ROOT / filename
        if not filepath.exists():
            continue
        funcs = get_public_functions(filepath)
        if not funcs:
            continue
        func_lines = "\n".join(f"- {f}" for f in funcs[:20])  # cap at 20
        if len(funcs) > 20:
            func_lines += f"\n- *…and {len(funcs) - 20} more*"
        blocks.append(f"#### {filename}\n{func_lines}")

    return "\n\n".join(blocks)


def render_equity_factors(cfg: dict) -> str:
    factors = cfg.get("EQUITY_FACTORS", {})
    if not factors:
        return "*(EQUITY_FACTORS not found in config.py)*"

    label_map = {
        "momentum_12_1":          "12-month return minus last month (Jegadeesh-Titman)",
        "momentum_6_1":           "6-month return minus last month",
        "mean_reversion_5d":      "Short-term losers bounce",
        "volatility_quality":     "Low realized vol = quality proxy",
        "risk_adjusted_momentum": "Sharpe-like ratio",
    }

    rows = []
    for name, params in factors.items():
        weight = f"{int(params.get('weight', 0) * 100)}%"
        lb_long  = params.get("lookback_long",  params.get("lookback", "—"))
        lb_skip  = params.get("lookback_skip",  None)
        lb_mom   = params.get("mom_lookback",   None)
        lb_vol   = params.get("vol_lookback",   None)
        invert   = params.get("invert", False)

        if lb_skip:
            lookback = f"{lb_long}d long, {lb_skip}d skip"
        elif lb_mom:
            lookback = f"{lb_mom}d mom / {lb_vol}d vol"
        else:
            lookback = f"{lb_long}d" + (" (inverted)" if invert else "")

        desc = label_map.get(name, "")
        rows.append(f"| `{name}` | {weight} | {lookback} | {desc} |")

    header = (
        "| Factor | Weight | Lookback | Logic |\n"
        "|--------|--------|----------|-------|"
    )
    return header + "\n" + "\n".join(rows)


def render_crypto_vol_gate(cfg: dict) -> str:
    p = cfg.get("CRYPTO_PARAMS", {})
    hi  = int(p.get("vol_threshold_high",    0.80) * 100)
    ext = int(p.get("vol_threshold_extreme", 1.20) * 100)
    sf  = p.get("vol_scale_factor", 0.5)
    return (
        "| Ann. Vol | `vol_scale` | Effect |\n"
        "|----------|-------------|--------|\n"
        f"| < {hi}% | 1.0 | Full position |\n"
        f"| {hi}–{ext}% | {sf} | Half position |\n"
        f"| > {ext}% | 0.0 | Cash — no position |"
    )


def render_config(cfg: dict) -> str:
    nav  = cfg.get("PORTFOLIO_NAV", "—")
    ea   = cfg.get("EQUITY_ALLOCATION", 0)
    ca   = cfg.get("CRYPTO_ALLOCATION", 0)
    cb   = cfg.get("CASH_BUFFER", 0)

    rp   = cfg.get("RISK_PARAMS", {})
    kf   = rp.get("kelly_fraction", "—")
    meq  = rp.get("max_position_equity_pct", 0)
    mcr  = rp.get("max_position_crypto_pct", 0)
    neq  = rp.get("max_equity_positions", "—")
    ncr  = rp.get("max_crypto_positions", "—")
    ecb  = rp.get("equity_cost_bps", "—")
    ccb  = rp.get("crypto_cost_bps", "—")
    wdd  = rp.get("weekly_dd_warning", 0)
    mdd  = rp.get("monthly_dd_stop", 0)
    minp = rp.get("min_position_eur", "—")

    ef   = cfg.get("EQUITY_FACTORS", {})
    cp   = cfg.get("CRYPTO_PARAMS", {})
    pm   = cfg.get("POLYMARKET_PARAMS", {})
    dl   = cfg.get("DATA_LOOKBACK_DAYS", "—")
    yft  = cfg.get("YAHOO_FINANCE_TIMEOUT", "—")
    od   = cfg.get("OUTPUT_DIR", "—")

    # Build factor rows from live config
    factor_rows = ""
    label_map = {
        "momentum_12_1":          "252d long, 21d skip",
        "momentum_6_1":           "126d long, 21d skip",
        "mean_reversion_5d":      "5d (inverted)",
        "volatility_quality":     "63d (inverted)",
        "risk_adjusted_momentum": "126d mom / 63d vol",
    }
    for fname, fparams in ef.items():
        w  = f"{int(fparams.get('weight', 0) * 100)}%"
        lb = label_map.get(fname, "—")
        factor_rows += f"| `{fname}` | {w} | {lb} |\n"

    return f"""### Portfolio Parameters
| Parameter | Value |
|-----------|-------|
| `PORTFOLIO_NAV` | {nav:,} EUR |
| `EQUITY_ALLOCATION` | {ea * 100:.1f}% |
| `CRYPTO_ALLOCATION` | {ca * 100:.1f}% |
| `CASH_BUFFER` | {cb * 100:.1f}% |

### Risk Parameters
| Parameter | Value |
|-----------|-------|
| `kelly_fraction` | {kf} |
| `max_position_equity_pct` | {meq * 100:.1f}% |
| `max_position_crypto_pct` | {mcr * 100:.1f}% |
| `max_equity_positions` | {neq} |
| `max_crypto_positions` | {ncr} |
| `equity_cost_bps` | {ecb} bps |
| `crypto_cost_bps` | {ccb} bps |
| `weekly_dd_warning` | {wdd * 100:.1f}% |
| `monthly_dd_stop` | {mdd * 100:.1f}% |
| `min_position_eur` | €{minp:,} |

### Equity Factor Weights
| Factor | Weight | Lookback |
|--------|--------|----------|
{factor_rows.rstrip()}

### Crypto Parameters
| Parameter | Value |
|-----------|-------|
| `ema_fast` | {cp.get('ema_fast', '—')} |
| `ema_slow` | {cp.get('ema_slow', '—')} |
| `ema_trend` | {cp.get('ema_trend', '—')} |
| `roc_periods` | {cp.get('roc_periods', '—')} |
| `roc_weights` | {cp.get('roc_weights', '—')} |
| `rsi_period` | {cp.get('rsi_period', '—')} |
| `rsi_oversold` | {cp.get('rsi_oversold', '—')} |
| `rsi_overbought` | {cp.get('rsi_overbought', '—')} |
| `vol_threshold_high` | {int(cp.get('vol_threshold_high', 0) * 100)}% |
| `vol_threshold_extreme` | {int(cp.get('vol_threshold_extreme', 0) * 100)}% |
| `vol_scale_factor` | {cp.get('vol_scale_factor', '—')} |

### Polymarket Parameters
| Parameter | Value |
|-----------|-------|
| `api_base_url` | {pm.get('api_base_url', '—')} |
| `cache_ttl_hours` | {pm.get('cache_ttl_hours', '—')} |
| `min_volume_24h` | ${pm.get('min_volume_24h', '—'):,} |
| `min_liquidity` | ${pm.get('min_liquidity', '—'):,} |
| `max_days_to_resolution` | {pm.get('max_days_to_resolution', '—')} |
| `strong_consensus_high` | {int(pm.get('strong_consensus_high', 0) * 100)}% |
| `strong_consensus_low` | {int(pm.get('strong_consensus_low', 0) * 100)}% |
| `volume_high` | ${pm.get('volume_high', '—'):,} |
| `liquidity_high` | ${pm.get('liquidity_high', '—'):,} |

### Data Settings
| Parameter | Value |
|-----------|-------|
| `DATA_LOOKBACK_DAYS` | {dl} |
| `YAHOO_FINANCE_TIMEOUT` | {yft}s |
| `OUTPUT_DIR` | {od} |"""


# ==============================================================================
# SECTION REPLACEMENT ENGINE
# ==============================================================================

def replace_section(content: str, tag: str, new_body: str) -> tuple[str, bool]:
    """Replace content between <!-- AUTO:TAG --> and <!-- /AUTO:TAG --> markers."""
    pattern = re.compile(
        rf"(<!-- AUTO:{re.escape(tag)} -->)(.*?)(<!-- /AUTO:{re.escape(tag)} -->)",
        re.DOTALL
    )
    new_block = f"<!-- AUTO:{tag} -->\n{new_body}\n<!-- /AUTO:{tag} -->"
    result, count = pattern.subn(new_block, content)
    return result, count > 0


def update_internals(check_only: bool = False, quiet: bool = False) -> bool:
    """
    Read INTERNALS.md, replace all AUTO sections, write back.
    Returns True if anything changed.
    """
    if not INTERNALS_MD.exists():
        print(f"ERROR: {INTERNALS_MD} not found. Run with --full to create it.")
        return False

    original = INTERNALS_MD.read_text(encoding="utf-8")
    content  = original

    cfg = load_config()

    sections = [
        ("HEADER",           render_header(cfg)),
        ("MODULE_INVENTORY", render_module_inventory()),
        ("EQUITY_FACTORS",   render_equity_factors(cfg)),
        ("CRYPTO_VOL_GATE",  render_crypto_vol_gate(cfg)),
        ("CONFIG",           render_config(cfg)),
    ]

    changed_sections = []
    for tag, body in sections:
        new_content, found = replace_section(content, tag, body)
        if not found:
            if not quiet:
                print(f"  [warn] Section AUTO:{tag} not found in {INTERNALS_MD.name} — skipping")
            continue
        if new_content != content:
            changed_sections.append(tag)
        content = new_content

    changed = content != original

    if check_only:
        if changed:
            print(f"[doc_generator] Would update sections: {', '.join(changed_sections)}")
        else:
            print("[doc_generator] No changes detected.")
        return changed

    if changed:
        INTERNALS_MD.write_text(content, encoding="utf-8")
        if not quiet:
            print(f"[doc_generator] Updated {INTERNALS_MD} — sections refreshed: {', '.join(changed_sections)}")
    else:
        if not quiet:
            print("[doc_generator] No changes — docs are already up to date.")

    return changed


# ==============================================================================
# MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Refresh auto-generated sections of docs/INTERNALS.md"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Reserved for future full-rebuild mode (currently same as default)"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Diff only — show what would change without writing"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output except errors"
    )
    args = parser.parse_args()

    if not DOCS_DIR.exists():
        DOCS_DIR.mkdir(parents=True)

    changed = update_internals(check_only=args.check, quiet=args.quiet)
    sys.exit(0 if not changed or not args.check else 1)


if __name__ == "__main__":
    main()
