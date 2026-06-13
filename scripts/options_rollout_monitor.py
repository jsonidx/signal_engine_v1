#!/usr/bin/env python3
"""
scripts/options_rollout_monitor.py
====================================
Daily operational monitor for the live options stack.

Checks snapshot health, field null-rates, distributions, outcome accumulation,
and data-quality warnings.  Prints a concise CLI report; use --json for
machine-readable output.

Usage:
    python scripts/options_rollout_monitor.py
    python scripts/options_rollout_monitor.py --days 7
    python scripts/options_rollout_monitor.py --days 1 --json

Exit codes:
    0 — healthy (no warnings)
    1 — warnings present
    2 — DB unreachable or table missing
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

# ── Thresholds ────────────────────────────────────────────────────────────────

NULL_RATE_WARN        = 0.10   # >10% null on a required field → warning
INSUF_INPUTS_WARN     = 0.50   # >50% insufficient_inputs → warning
GUARDRAIL_STRICT_WARN = 0.75   # >75% enter_if_repriced + skip_for_now → warning
MIN_ROWS_DISTRIB      = 5      # below this, skip distribution analysis
COMPARATOR_MIN_ROWS   = 20     # below this, comparator is still in warm-up


# ── DB queries ────────────────────────────────────────────────────────────────

def _query_snapshot_health(cur, days: int) -> dict[str, Any]:
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE created_at >= NOW() - (%(days)s || ' days')::interval)
                AS fresh_rows,
            COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours')
                AS rows_24h,
            COUNT(*) FILTER (WHERE algo_version >= '2.0')
                AS v2_rows,
            COUNT(*) FILTER (WHERE algo_version IS NULL)
                AS null_algo_version,
            COUNT(*) FILTER (WHERE suppressed = true)
                AS suppressed_rows,
            COUNT(*) FILTER (WHERE suppressed = false AND strike IS NULL)
                AS no_candidate_rows,
            COUNT(*) FILTER (WHERE suppressed = false AND strike IS NOT NULL)
                AS candidate_rows,
            -- null rates among live (non-suppressed, has-candidate) rows
            -- structure_archetype / target_projection_method require algo_version >= 2.1 (TRD-048)
            COUNT(*) FILTER (
                WHERE suppressed = false AND strike IS NOT NULL AND algo_version >= '2.0'
                  AND risk_allowed IS NULL)
                AS null_risk_allowed,
            COUNT(*) FILTER (
                WHERE suppressed = false AND strike IS NOT NULL AND algo_version >= '2.1'
                  AND structure_archetype IS NULL)
                AS null_structure_archetype,
            COUNT(*) FILTER (
                WHERE suppressed = false AND strike IS NOT NULL AND algo_version >= '2.1'
                  AND target_projection_method IS NULL)
                AS null_target_projection_method,
            COUNT(*) FILTER (
                WHERE suppressed = false AND strike IS NOT NULL AND algo_version >= '2.0'
                  AND entry_action IS NULL)
                AS null_entry_action,
            COUNT(*) FILTER (
                WHERE suppressed = false AND strike IS NOT NULL AND algo_version >= '2.0'
                  AND scenarios_json IS NULL
                  AND target_projection_method != 'insufficient_inputs')
                AS null_scenarios_unexpected,
            COUNT(*) FILTER (
                WHERE suppressed = false AND strike IS NOT NULL AND algo_version >= '2.1')
                AS v2_candidate_rows
        FROM option_candidate_snapshots
        WHERE created_at >= NOW() - (%(days)s || ' days')::interval
    """, {"days": days})
    return dict(cur.fetchone())


def _query_distributions(cur, days: int) -> dict[str, Any]:
    def _distrib(col: str) -> list[dict]:
        cur.execute(f"""
            SELECT {col} AS value, COUNT(*) AS n
            FROM option_candidate_snapshots
            WHERE created_at >= NOW() - (%(days)s || ' days')::interval
              AND suppressed = false
              AND strike IS NOT NULL
              AND algo_version >= '2.1'
            GROUP BY {col}
            ORDER BY n DESC
        """, {"days": days})
        return [dict(r) for r in cur.fetchall()]

    return {
        "entry_action":              _distrib("entry_action"),
        "position_size_tier":        _distrib("position_size_tier"),
        "structure_archetype":       _distrib("structure_archetype"),
        "target_projection_method":  _distrib("target_projection_method"),
        "risk_nav_source":           _distrib("risk_nav_source"),
    }


def _query_outcomes(cur, days: int) -> dict[str, Any]:
    cur.execute("""
        SELECT COUNT(*) AS total_outcomes,
               COUNT(*) FILTER (WHERE target_projection_method IS NOT NULL)
                   AS outcomes_with_method,
               COUNT(*) FILTER (WHERE hit_v2_tp1 IS NOT NULL OR hit_v2_tp2 IS NOT NULL)
                   AS outcomes_with_v2_hits
        FROM option_candidate_outcomes
        WHERE resolved_at >= NOW() - (%(days)s || ' days')::interval
    """, {"days": days})
    outcomes = dict(cur.fetchone())

    cur.execute("""
        SELECT COUNT(*) AS all_time_outcomes
        FROM option_candidate_outcomes
    """)
    outcomes["all_time_outcomes"] = cur.fetchone()["all_time_outcomes"]

    return outcomes


def _query_comparator_readiness(cur) -> dict[str, Any]:
    cur.execute("""
        SELECT
            COUNT(*) AS resolved_snapshots,
            COUNT(DISTINCT s.ticker) AS tickers_covered,
            COUNT(*) FILTER (WHERE s.target_projection_method = 'delta_dte_adjusted')
                AS delta_dte_rows,
            COUNT(*) FILTER (WHERE s.target_projection_method = 'delta_only')
                AS delta_only_rows
        FROM option_candidate_snapshots s
        JOIN option_candidate_outcomes o ON o.candidate_snapshot_id = s.id
        WHERE s.algo_version = '2.0'
    """)
    return dict(cur.fetchone())


# ── Warning detection ─────────────────────────────────────────────────────────

def compute_warnings(
    health: dict[str, Any],
    distributions: dict[str, Any],
    outcomes: dict[str, Any],
    comparator: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    n = health.get("v2_candidate_rows", 0)

    # ── Null-rate warnings ────────────────────────────────────────────────────
    null_fields = {
        "risk_allowed":              health.get("null_risk_allowed", 0),
        "structure_archetype":       health.get("null_structure_archetype", 0),
        "target_projection_method":  health.get("null_target_projection_method", 0),
        "entry_action":              health.get("null_entry_action", 0),
    }
    for field, null_count in null_fields.items():
        rate = null_count / n if n > 0 else 0.0
        if rate > NULL_RATE_WARN:
            warnings.append(
                f"NULL_RATE: {field} is {rate:.0%} null "
                f"({null_count}/{n} v2 candidate rows)"
            )

    # scenarios_json unexpectedly null (excluding insufficient_inputs)
    scen_null = health.get("null_scenarios_unexpected", 0)
    if n > 0 and scen_null / n > NULL_RATE_WARN:
        warnings.append(
            f"NULL_RATE: scenarios_json missing on {scen_null}/{n} non-insufficient rows"
        )

    # ── Distribution warnings ─────────────────────────────────────────────────
    if n >= MIN_ROWS_DISTRIB:
        # All insufficient_inputs
        tp_dist = {r["value"]: r["n"] for r in distributions.get("target_projection_method", [])}
        insuf = tp_dist.get("insufficient_inputs", 0)
        if n > 0 and insuf / n > INSUF_INPUTS_WARN:
            warnings.append(
                f"DISTRIB: {insuf/n:.0%} of rows are insufficient_inputs "
                f"— v2 target engine may lack chain data"
            )

        # Guardrail too strict
        ea_dist = {r["value"]: r["n"] for r in distributions.get("entry_action", [])}
        blocked = ea_dist.get("enter_if_repriced", 0) + ea_dist.get("skip_for_now", 0)
        if n > 0 and blocked / n > GUARDRAIL_STRICT_WARN:
            warnings.append(
                f"GUARDRAIL: {blocked/n:.0%} of rows are enter_if_repriced or skip_for_now "
                f"— guardrails may be too strict"
            )

        # Everything in one size tier (unexpected uniformity)
        tier_dist = distributions.get("position_size_tier", [])
        if tier_dist:
            top_tier = tier_dist[0]
            if n > 0 and top_tier["n"] / n > 0.95:
                warnings.append(
                    f"DISTRIB: {top_tier['n']/n:.0%} of rows have "
                    f"position_size_tier='{top_tier['value']}' — unexpected uniformity"
                )

    # ── No fresh data ─────────────────────────────────────────────────────────
    if health.get("rows_24h", 0) == 0:
        warnings.append("STALENESS: no new snapshots in the last 24h")

    # ── Comparator warm-up notice (not a warning, just status) ───────────────
    # Not a WARNING, reported separately in the output

    return warnings


# ── Formatting ────────────────────────────────────────────────────────────────

def _pct(num: int, denom: int) -> str:
    if denom == 0:
        return "—"
    return f"{num/denom:.0%}"


def _distrib_lines(rows: list[dict], total: int, indent: str = "    ") -> list[str]:
    lines = []
    for r in rows:
        val = r["value"] if r["value"] is not None else "(null)"
        pct = _pct(r["n"], total)
        lines.append(f"{indent}{val:<30} {r['n']:>5}  {pct}")
    if not rows:
        lines.append(f"{indent}(no data)")
    return lines


def format_text_report(
    days: int,
    health: dict[str, Any],
    distributions: dict[str, Any],
    outcomes: dict[str, Any],
    comparator: dict[str, Any],
    warnings: list[str],
    generated_at: str,
) -> str:
    n_cand = health.get("v2_candidate_rows", 0)
    lines: list[str] = []
    W = 62

    lines += [
        "=" * W,
        "  OPTIONS ROLLOUT MONITOR",
        f"  window: {days}d   generated: {generated_at}",
        "=" * W,
    ]

    # ── 1. Snapshot health ────────────────────────────────────────────────────
    lines += [
        "",
        "── SNAPSHOT HEALTH ──────────────────────────────────────",
        f"  Last 24h rows         : {health['rows_24h']}",
        f"  Last {days}d rows           : {health['fresh_rows']}",
        f"  algo_version=2.0 rows : {health['v2_rows']}",
        f"  Candidate rows (v2)   : {n_cand}",
        f"  Suppressed rows       : {health['suppressed_rows']}",
        f"  No-candidate rows     : {health['no_candidate_rows']}",
        "",
        "  Null rates (v2 candidate rows):",
        f"    algo_version          : {_pct(health['null_algo_version'], health['fresh_rows'])} "
            f"({health['null_algo_version']} legacy rows without algo_version)",
        f"    risk_allowed          : {_pct(health['null_risk_allowed'], n_cand)}",
        f"    structure_archetype   : {_pct(health['null_structure_archetype'], n_cand)}",
        f"    target_proj_method    : {_pct(health['null_target_projection_method'], n_cand)}",
        f"    entry_action          : {_pct(health['null_entry_action'], n_cand)}",
        f"    scenarios_json*       : {_pct(health['null_scenarios_unexpected'], n_cand)}"
            " (*excl. insufficient_inputs)",
    ]

    # ── 2. Distributions ──────────────────────────────────────────────────────
    lines += [
        "",
        "── DISTRIBUTIONS (v2 candidate rows, last {0}d) ──────────".format(days),
    ]
    for field in ("entry_action", "structure_archetype", "target_projection_method",
                  "position_size_tier", "risk_nav_source"):
        rows = distributions.get(field, [])
        total = sum(r["n"] for r in rows)
        lines.append(f"  {field} (n={total}):")
        lines += _distrib_lines(rows, total)

    # ── 3. Outcomes & comparator ──────────────────────────────────────────────
    lines += [
        "",
        "── OUTCOMES & COMPARATOR ────────────────────────────────",
        f"  All-time resolved outcomes : {outcomes['all_time_outcomes']}",
        f"  Window ({days}d) outcomes       : {outcomes['total_outcomes']}",
        f"    with target_proj_method  : {outcomes['outcomes_with_method']}",
        f"    with v2 hit markers      : {outcomes['outcomes_with_v2_hits']}",
        f"  Comparator-ready snapshots : {comparator['resolved_snapshots']}",
        f"    delta_dte_adjusted rows  : {comparator['delta_dte_rows']}",
        f"    delta_only rows          : {comparator['delta_only_rows']}",
    ]

    if comparator["resolved_snapshots"] < COMPARATOR_MIN_ROWS:
        lines.append(
            f"  ⚠  Comparator still warming up "
            f"({comparator['resolved_snapshots']}/{COMPARATOR_MIN_ROWS} min rows)"
        )
    else:
        lines.append("  ✓  Comparator has sufficient data for cohort stats")

    # ── 4. Warnings ───────────────────────────────────────────────────────────
    lines += ["", "── WARNINGS ─────────────────────────────────────────────"]
    if warnings:
        for w in warnings:
            lines.append(f"  ⚠  {w}")
    else:
        lines.append("  ✓  No data-quality warnings")

    lines += ["", "=" * W]
    return "\n".join(lines)


def build_json_report(
    days: int,
    health: dict[str, Any],
    distributions: dict[str, Any],
    outcomes: dict[str, Any],
    comparator: dict[str, Any],
    warnings: list[str],
    generated_at: str,
) -> dict[str, Any]:
    n = health.get("v2_candidate_rows", 0)

    def _rate(num, denom):
        return round(num / denom, 4) if denom > 0 else None

    return {
        "generated_at": generated_at,
        "window_days": days,
        "snapshot_health": {
            "rows_24h": health["rows_24h"],
            "fresh_rows": health["fresh_rows"],
            "v2_rows": health["v2_rows"],
            "candidate_rows": n,
            "suppressed_rows": health["suppressed_rows"],
            "no_candidate_rows": health["no_candidate_rows"],
            "null_rates": {
                "risk_allowed": _rate(health["null_risk_allowed"], n),
                "structure_archetype": _rate(health["null_structure_archetype"], n),
                "target_projection_method": _rate(health["null_target_projection_method"], n),
                "entry_action": _rate(health["null_entry_action"], n),
                "scenarios_json_unexpected": _rate(health["null_scenarios_unexpected"], n),
            },
        },
        "distributions": distributions,
        "outcomes": outcomes,
        "comparator": comparator,
        "warnings": warnings,
        "status": "warning" if warnings else "ok",
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run(days: int, as_json: bool) -> int:
    from utils.db import managed_connection

    try:
        with managed_connection() as conn:
            with conn.cursor() as cur:
                # Verify table exists
                cur.execute("""
                    SELECT to_regclass('public.option_candidate_snapshots') AS tbl
                """)
                if cur.fetchone()["tbl"] is None:
                    msg = "ERROR: option_candidate_snapshots table not found — run migrations first"
                    print(msg, file=sys.stderr)
                    return 2

                health       = _query_snapshot_health(cur, days)
                distributions = _query_distributions(cur, days)
                outcomes     = _query_outcomes(cur, days)
                comparator   = _query_comparator_readiness(cur)

    except Exception as exc:
        print(f"ERROR: DB unreachable — {exc}", file=sys.stderr)
        return 2

    warnings = compute_warnings(health, distributions, outcomes, comparator)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if as_json:
        report = build_json_report(
            days, health, distributions, outcomes, comparator, warnings, generated_at
        )
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_text_report(
            days, health, distributions, outcomes, comparator, warnings, generated_at
        ))

    return 1 if warnings else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Options rollout daily monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="look-back window in days (default: 7)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="output machine-readable JSON instead of text",
    )
    args = parser.parse_args()
    sys.exit(run(args.days, args.as_json))


if __name__ == "__main__":
    main()
