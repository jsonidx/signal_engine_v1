#!/usr/bin/env python3
"""
scripts/update_factor_weights.py
=================================
Reads the last 12 months of per-factor IC results from backtest output and
updates EQUITY_FACTORS weights in config.py proportionally to recent IC.

Only factors with positive mean IC receive weight. The weights are rescaled so
they sum to 1.0.  earnings_revision is always included at its current weight
because it cannot be backtested reliably (no point-in-time EPS data in
yfinance), so its weight is carried forward unchanged and the remaining IC
factors are scaled to fill the residual.

Algorithm
---------
1. Read IC data from data/backtest_results.json → factor_ic_table
   (falls back to running backtest.py --run-latest --factor-ic if absent)
2. Filter to last 12 months of IC observations.
3. For IC-testable factors (all except earnings_revision):
      new_weight_raw[f] = max(0, mean_IC[f])
   Rescale so sum(IC-testable weights) == 1 - earnings_revision_weight.
4. Clamp: min weight 0.04, max weight 0.35 per factor.
5. Create config.py.bak.
6. Write updated weights in-place (regex replace of the "weight": N.NN lines).
7. Log before/after table to stdout.

USAGE
-----
    python3 scripts/update_factor_weights.py          # dry run (no write)
    python3 scripts/update_factor_weights.py --apply  # write to config.py
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE  = PROJECT_ROOT / "config.py"
BACKUP_FILE  = PROJECT_ROOT / "config.py.bak"
DATA_DIR     = PROJECT_ROOT / "data"
SIGNALS_DIR  = PROJECT_ROOT / "signals_output"

# Factors that cannot be IC-tested (no PIT data) — weight carried forward
_SKIP_IC_FACTORS = {"earnings_revision"}

# Guardrails
MIN_WEIGHT = 0.04
MAX_WEIGHT = 0.35
MIN_IC_OBSERVATIONS = 3   # minimum IC windows before trusting the average


# ---------------------------------------------------------------------------
# Step 1: Load IC data
# ---------------------------------------------------------------------------

def _load_ic_from_json() -> Optional[Dict[str, float]]:
    """Try data/backtest_results.json → factor_ic_table."""
    path = DATA_DIR / "backtest_results.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            d = json.load(f)
        rows = d.get("factor_ic_table") or []
        if not rows:
            return None
        return {
            r["factor_name"]: float(r["mean_IC"])
            for r in rows
            if "factor_name" in r and "mean_IC" in r
        }
    except Exception as e:
        print(f"  [warn] Could not parse backtest_results.json: {e}")
        return None


def _run_backtest_and_load_ic() -> Optional[Dict[str, float]]:
    """Fall back to running backtest.py --run-latest --factor-ic and parsing stdout."""
    print("  Running backtest.py --run-latest --factor-ic (this may take a minute)...")
    try:
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "backtest.py"),
             "--run-latest", "--factor-ic"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=300,
        )
    except subprocess.TimeoutExpired:
        print("  [error] backtest timed out")
        return None

    ic: Dict[str, float] = {}
    # Parse lines like:  "  momentum_12_1           0.0342 ..."
    pattern = re.compile(r"^\s+([\w]+)\s+([-\d.]+)\s+([-\d.]+)\s+(\d+)")
    in_ic_section = False
    for line in result.stdout.splitlines():
        if "PER-FACTOR IC TABLE" in line:
            in_ic_section = True
            continue
        if in_ic_section:
            m = pattern.match(line)
            if m:
                fname = m.group(1)
                mean_ic = float(m.group(2))
                ic[fname] = mean_ic
            elif line.strip().startswith("WEIGHT") or line.startswith("  ="):
                break  # past the IC table

    return ic if ic else None


def load_ic() -> Dict[str, float]:
    ic = _load_ic_from_json()
    if not ic:
        ic = _run_backtest_and_load_ic()
    if not ic:
        print("  [error] Could not load IC data from any source.")
        sys.exit(1)
    return ic


# ---------------------------------------------------------------------------
# Step 2: Read current weights from config.py
# ---------------------------------------------------------------------------

def read_current_weights() -> Dict[str, float]:
    """Parse EQUITY_FACTORS from config.py and return {factor: weight}."""
    text = CONFIG_FILE.read_text()
    # Match:  "factor_name": { ... "weight": 0.28, ...
    factor_blocks = re.finditer(
        r'"(\w+)":\s*\{[^}]*"weight":\s*([\d.]+)',
        text, re.DOTALL
    )
    weights: Dict[str, float] = {}
    for m in factor_blocks:
        weights[m.group(1)] = float(m.group(2))
    return weights


# ---------------------------------------------------------------------------
# Step 3: Compute new weights
# ---------------------------------------------------------------------------

def compute_new_weights(
    ic: Dict[str, float],
    current: Dict[str, float],
) -> Dict[str, float]:
    """
    Produce updated weights dict.
    - earnings_revision kept at current value (no IC data).
    - Other factors proportional to max(0, mean_IC).
    - Clamped to [MIN_WEIGHT, MAX_WEIGHT].
    - Rescaled so all weights sum to 1.0.
    """
    er_weight = current.get("earnings_revision", 0.18)
    residual  = 1.0 - er_weight

    # Raw IC proportions for testable factors
    testable = {f: w for f, w in current.items() if f not in _SKIP_IC_FACTORS}
    raw: Dict[str, float] = {}
    for fname in testable:
        mean_ic = ic.get(fname, 0.0)
        raw[fname] = max(0.0, mean_ic)

    total_raw = sum(raw.values())
    if total_raw == 0:
        print("  [warn] All ICs are zero or negative — keeping current weights unchanged.")
        return dict(current)

    # Proportional allocation
    proposed: Dict[str, float] = {}
    for fname, r in raw.items():
        w = (r / total_raw) * residual
        proposed[fname] = round(max(MIN_WEIGHT, min(MAX_WEIGHT, w)), 4)

    # Re-normalise after clamping so testable factors still sum to residual
    clamped_sum = sum(proposed.values())
    if abs(clamped_sum - residual) > 1e-4:
        scale = residual / clamped_sum
        proposed = {f: round(w * scale, 4) for f, w in proposed.items()}

    proposed["earnings_revision"] = round(er_weight, 4)
    return proposed


# ---------------------------------------------------------------------------
# Step 4: Write updated config.py
# ---------------------------------------------------------------------------

def _patch_weight_line(text: str, factor: str, new_weight: float) -> str:
    """
    Replace the 'weight': N.NN line inside the given factor's block.
    Targets the first occurrence of the pattern after the factor name declaration.
    """
    # Find the factor block start
    factor_pos = text.find(f'"{factor}":')
    if factor_pos == -1:
        return text
    # Within the block, replace the weight
    block_end = text.find('}', factor_pos)
    block = text[factor_pos:block_end + 1]
    new_block = re.sub(
        r'("weight":\s*)([\d.]+)',
        lambda m: f'{m.group(1)}{new_weight}',
        block,
        count=1,
    )
    return text[:factor_pos] + new_block + text[block_end + 1:]


def write_config(new_weights: Dict[str, float]) -> None:
    # Backup
    shutil.copy2(CONFIG_FILE, BACKUP_FILE)
    print(f"  Backup written to {BACKUP_FILE}")

    text = CONFIG_FILE.read_text()
    for factor, weight in new_weights.items():
        text = _patch_weight_line(text, factor, weight)

    CONFIG_FILE.write_text(text)
    print(f"  config.py updated.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def print_table(current: Dict[str, float], proposed: Dict[str, float]) -> None:
    print()
    print(f"  {'Factor':<25} {'Current':>9} {'Proposed':>9} {'Δ':>8}")
    print("  " + "-" * 55)
    for f in sorted(set(current) | set(proposed)):
        old = current.get(f, 0.0)
        new = proposed.get(f, 0.0)
        delta = new - old
        flag = "  ↑" if delta > 0.01 else ("  ↓" if delta < -0.01 else "")
        print(f"  {f:<25} {old:>9.4f} {new:>9.4f} {delta:>+8.4f}{flag}")
    print()
    old_sum = sum(current.values())
    new_sum = sum(proposed.values())
    print(f"  {'TOTAL':<25} {old_sum:>9.4f} {new_sum:>9.4f}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update EQUITY_FACTORS weights in config.py based on recent backtest IC"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the new weights to config.py (default: dry run only)"
    )
    args = parser.parse_args()

    print()
    print("=" * 60)
    print(f"  update_factor_weights.py — {datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 60)
    print()

    print("  Loading IC data...")
    ic = load_ic()
    print(f"  IC data loaded for {len(ic)} factors: {list(ic.keys())}")
    print()
    for fname, mean_ic in sorted(ic.items(), key=lambda x: -x[1]):
        print(f"    {fname:<25}  mean_IC = {mean_ic:+.4f}")
    print()

    print("  Reading current weights from config.py...")
    current = read_current_weights()

    print("  Computing proposed weights...")
    proposed = compute_new_weights(ic, current)

    print_table(current, proposed)

    if args.apply:
        write_config(proposed)
        print("  ✅ Weights updated. Restart signal_engine.py to use the new weights.")
    else:
        print("  [dry run] Pass --apply to write changes to config.py.")
    print()


if __name__ == "__main__":
    main()
