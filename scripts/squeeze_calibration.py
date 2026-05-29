#!/usr/bin/env python3
"""
scripts/squeeze_calibration.py — TRD-013: Squeeze probability calibration and review gate.

WHAT THIS DOES
--------------
1. Fetches closed-window squeeze training outcomes from Supabase.
2. Computes calibration metrics broken down by:
   - Alert/state type (EARLY_ARMED, ARMED, ACTIVE)
   - Score bucket
   - SI bucket
   - DTC bucket
3. Explicitly evaluates ACTIVE as a continuation / chase-risk state (not fresh-entry).
4. Writes a human-readable report to reports/squeeze_calibration_<date>.md.
5. If sample is sufficient and a threshold change is warranted, creates an
   approval_request in Supabase (TRD-015) — does NOT auto-apply any change.

USAGE
-----
    python3 scripts/squeeze_calibration.py
    python3 scripts/squeeze_calibration.py --start 2026-01-01 --end 2026-12-31
    python3 scripts/squeeze_calibration.py --min-sample 5 --create-approval

POINT-IN-TIME SAFETY
--------------------
All metrics are computed from closed forward windows only. No lookahead.
Only rows with taxonomy_label IS NOT NULL are included (i.e., window closed).

IMPORTANT
---------
This script reports what the data shows. It does NOT claim any specific success rate.
If sample size is too small, the report says so explicitly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_env_path = _ROOT / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

REPORTS_DIR = _ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

# Minimum sample size to report meaningful statistics (warn if below)
MIN_MEANINGFUL_SAMPLE = 10

# Entry-hunting states vs chase state
_ENTRY_STATES = {"EARLY_ARMED", "ARMED"}
_CHASE_STATES = {"ACTIVE"}


def _bucket_score(score: Optional[float]) -> str:
    if score is None:
        return "unknown"
    if score >= 75:
        return "75+"
    if score >= 60:
        return "60-74"
    if score >= 45:
        return "45-59"
    return "<45"


def _bucket_si(si: Optional[float]) -> str:
    if si is None:
        return "unknown"
    p = si * 100
    if p >= 50:
        return "50%+"
    if p >= 30:
        return "30-49%"
    if p >= 20:
        return "20-29%"
    return "<20%"


def _bucket_dtc(dtc: Optional[float]) -> str:
    if dtc is None:
        return "unknown"
    if dtc >= 10:
        return "10+"
    if dtc >= 7:
        return "7-9"
    if dtc >= 5:
        return "5-6"
    return "<5"


def _safe_rate(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "N/A"
    return f"{numerator / denominator:.1%}"


def _safe_mean(values: list) -> str:
    v = [x for x in values if x is not None]
    if not v:
        return "N/A"
    return f"{sum(v) / len(v):.1%}"


def _warn_if_small(n: int, context: str) -> str:
    if n < MIN_MEANINGFUL_SAMPLE:
        return f"⚠️  SMALL SAMPLE (n={n}) — {context} statistics may not be reliable."
    return ""


def compute_metrics(rows: list[dict]) -> dict:
    """
    Compute hit rates and taxonomy distributions from labeled outcome rows.

    Returns a nested dict: state_type → metrics dict.
    """
    from collections import defaultdict

    # Defense-in-depth: exclude rows that slipped through with no meaningful state.
    # These are pre-CHUNK-10 historical rows whose squeeze_state was None and could
    # appear as alert_type=None or as the string "None" / "NOT_SETUP" / "UNKNOWN".
    # They carry no calibration signal and must not create bogus grouping buckets.
    _SKIP_STATES = frozenset({"NONE", "NULL", "NAN", "NOT_SETUP", "UNKNOWN", ""})

    by_state: dict = defaultdict(list)
    for r in rows:
        raw = r.get("alert_type")
        if raw is None:
            continue
        state = str(raw).strip().upper()
        if state in _SKIP_STATES:
            continue
        by_state[state].append(r)

    results: dict = {}
    for state, state_rows in by_state.items():
        n = len(state_rows)
        tax_counts: dict = {}
        for r in state_rows:
            lbl = r.get("taxonomy_label") or "UNKNOWN"
            tax_counts[lbl] = tax_counts.get(lbl, 0) + 1

        hit_15_10 = [r for r in state_rows if r.get("hit_15pct_10d") is True]
        hit_25_20 = [r for r in state_rows if r.get("hit_25pct_20d") is True]
        fp = tax_counts.get("FALSE_POSITIVE", 0)
        early_enough = tax_counts.get("EARLY_ENOUGH", 0)
        late_chase = tax_counts.get("LATE_CHASE", 0)

        fwd_10d_vals = [r.get("fwd_10d") for r in state_rows]
        fwd_20d_vals = [r.get("fwd_20d") for r in state_rows]
        max_fwd_vals = [r.get("max_fwd_return") for r in state_rows]

        results[state] = {
            "n": n,
            "hit_15pct_10d_rate": _safe_rate(len(hit_15_10), n),
            "hit_25pct_20d_rate": _safe_rate(len(hit_25_20), n),
            "false_positive_rate": _safe_rate(fp, n),
            "early_enough_rate": _safe_rate(early_enough, n),
            "late_chase_rate": _safe_rate(late_chase, n),
            "avg_fwd_10d": _safe_mean(fwd_10d_vals),
            "avg_fwd_20d": _safe_mean(fwd_20d_vals),
            "avg_max_fwd": _safe_mean(max_fwd_vals),
            "taxonomy_counts": tax_counts,
            "small_sample_warning": _warn_if_small(n, f"{state}"),
        }

    return results


def breakdown_by_bucket(rows: list[dict], bucket_fn, bucket_key: str) -> dict:
    """Group rows by a bucket function and compute hit rates per bucket."""
    from collections import defaultdict
    grouped: dict = defaultdict(list)
    for r in rows:
        b = bucket_fn(r.get(bucket_key))
        grouped[b].append(r)
    result = {}
    for bucket, bucket_rows in sorted(grouped.items()):
        n = len(bucket_rows)
        hit_15 = sum(1 for r in bucket_rows if r.get("hit_15pct_10d") is True)
        fp = sum(1 for r in bucket_rows if r.get("taxonomy_label") == "FALSE_POSITIVE")
        result[bucket] = {
            "n": n,
            "hit_15pct_10d_rate": _safe_rate(hit_15, n),
            "false_positive_rate": _safe_rate(fp, n),
        }
    return result


def generate_report(
    rows: list[dict],
    report_date: str,
    start_date: Optional[str],
    end_date: Optional[str],
) -> str:
    """Generate a markdown calibration report from labeled outcome rows."""
    total = len(rows)
    metrics = compute_metrics(rows)

    lines = [
        f"# Squeeze Calibration Report — {report_date}",
        f"",
        f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Window:** {start_date or 'all'} → {end_date or 'all'}",
        f"**Total labeled signals:** {total}",
        f"",
        f"> ⚠️  Sample sizes are small. All statistics should be treated as exploratory.",
        f"> Do not draw strong conclusions from fewer than {MIN_MEANINGFUL_SAMPLE} signals per state.",
        f"",
        "---",
        "",
        "## Performance by Alert State",
        "",
        "The taxonomy labels are:",
        "- `EARLY_ENOUGH`: entry-state alert (EARLY_ARMED/ARMED) that preceded a qualifying move",
        "- `LATE_CHASE`: alert fired after the move was underway, or missed the timing threshold",
        "- `FALSE_POSITIVE`: no meaningful move occurred (max return < 5% across all windows)",
        "",
        "**Entry states (EARLY_ARMED, ARMED) are the primary fresh-entry alerts.**",
        "**ACTIVE is a continuation / chase-risk state — its late_chase rate is expected to be high.**",
        "",
    ]

    # Per-state breakdown
    state_order = ["EARLY_ARMED", "ARMED", "ACTIVE"]
    other_states = [s for s in metrics if s not in state_order]
    for state in state_order + other_states:
        if state not in metrics:
            continue
        m = metrics[state]
        n = m["n"]
        semantic = (
            "Entry-hunting (early setup, lower confirmation)"
            if state == "EARLY_ARMED"
            else "Entry-hunting (structural setup confirmed)"
            if state == "ARMED"
            else "Continuation / chase-risk — NOT primary fresh-entry state"
            if state == "ACTIVE"
            else ""
        )
        lines += [
            f"### {state}",
            f"*{semantic}*  n={n}",
            m.get("small_sample_warning", ""),
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Hit 15% in 10d | {m['hit_15pct_10d_rate']} |",
            f"| Hit 25% in 20d | {m['hit_25pct_20d_rate']} |",
            f"| EARLY_ENOUGH rate | {m['early_enough_rate']} |",
            f"| LATE_CHASE rate | {m['late_chase_rate']} |",
            f"| FALSE_POSITIVE rate | {m['false_positive_rate']} |",
            f"| Avg fwd 10d | {m['avg_fwd_10d']} |",
            f"| Avg fwd 20d | {m['avg_fwd_20d']} |",
            f"| Avg max fwd | {m['avg_max_fwd']} |",
            "",
            f"Taxonomy counts: {json.dumps(m['taxonomy_counts'])}",
            "",
        ]

    # Score bucket breakdown
    entry_rows = [r for r in rows if (r.get("alert_type") or "").upper() in _ENTRY_STATES]
    if entry_rows:
        lines += [
            "---",
            "",
            "## Score Buckets (entry states only: EARLY_ARMED + ARMED)",
            "",
            "| Score bucket | n | Hit 15%/10d | False positive |",
            "|---|---|---|---|",
        ]
        score_bkts = breakdown_by_bucket(entry_rows, _bucket_score, "final_score")
        for b, bm in score_bkts.items():
            lines.append(f"| {b} | {bm['n']} | {bm['hit_15pct_10d_rate']} | {bm['false_positive_rate']} |")
        lines.append("")

    # SI bucket breakdown
    if entry_rows:
        lines += [
            "## SI Buckets (entry states only)",
            "",
            "| SI bucket | n | Hit 15%/10d | False positive |",
            "|---|---|---|---|",
        ]
        si_bkts = breakdown_by_bucket(entry_rows, _bucket_si, "short_pct_float")
        for b, bm in si_bkts.items():
            lines.append(f"| {b} | {bm['n']} | {bm['hit_15pct_10d_rate']} | {bm['false_positive_rate']} |")
        lines.append("")

    # DTC bucket breakdown
    if entry_rows:
        lines += [
            "## DTC Buckets (entry states only)",
            "",
            "| DTC bucket | n | Hit 15%/10d | False positive |",
            "|---|---|---|---|",
        ]
        dtc_bkts = breakdown_by_bucket(entry_rows, _bucket_dtc, "computed_dtc_30d")
        for b, bm in dtc_bkts.items():
            lines.append(f"| {b} | {bm['n']} | {bm['hit_15pct_10d_rate']} | {bm['false_positive_rate']} |")
        lines.append("")

    # Entry vs chase comparison
    chase_rows = [r for r in rows if (r.get("alert_type") or "").upper() in _CHASE_STATES]
    lines += [
        "---",
        "",
        "## Entry vs Chase State Comparison",
        "",
        f"- Entry states (EARLY_ARMED + ARMED): **{len(entry_rows)}** signals",
        f"- Chase state (ACTIVE): **{len(chase_rows)}** signals",
        "",
        "The ACTIVE state is a continuation alert. Its primary label is expected to be",
        "LATE_CHASE because the move is already in progress when it fires. This does not",
        "mean the state is 'wrong' — it means it has a different purpose from EARLY_ARMED/ARMED.",
        "",
        "---",
        "",
        "## Residual Risks and Caveats",
        "",
        "1. **Sample size**: All statistics above are based on limited historical data.",
        "   Do not adjust thresholds until at least 10+ signals per state are available.",
        "2. **Selection bias**: only tickers that entered the screener universe are captured.",
        "3. **Point-in-time**: forward returns are close-to-close from signal date.",
        "   Intraday slippage, spread, and liquidity are not modeled.",
        "4. **EARLY_ARMED hit rate**: expected to be lower than ARMED. A lower hit rate",
        "   is acceptable if the early entry improves risk/reward on the winning cases.",
        "5. **Calibration is not a guarantee**: past outcomes do not predict future results.",
        "",
        "---",
        f"*Report generated by scripts/squeeze_calibration.py — {report_date}*",
    ]

    return "\n".join(line for line in lines)


def maybe_create_approval_request(
    metrics: dict,
    report_path: str,
    min_sample: int,
) -> Optional[str]:
    """
    If calibration evidence suggests a threshold change is warranted AND sample
    is sufficient, create an approval_request in Supabase.

    Returns request_id if created, None otherwise.
    Does NOT auto-apply any change.
    """
    try:
        from utils.supabase_persist import save_approval_request

        # Only propose changes when we have enough data
        for state, m in metrics.items():
            if m["n"] < min_sample:
                log.info("maybe_create_approval_request: skipping %s (n=%d < %d)", state, m["n"], min_sample)
                return None

        # Check if EARLY_ARMED has a meaningfully different hit rate than ARMED
        ea = metrics.get("EARLY_ARMED")
        armed = metrics.get("ARMED")
        if not ea or not armed:
            return None

        # Only propose if sample is adequate
        if ea["n"] < min_sample or armed["n"] < min_sample:
            return None

        request_id = str(uuid.uuid4())[:8]
        summary = (
            f"Calibration run on {date.today().isoformat()} found:\n"
            f"  EARLY_ARMED: n={ea['n']}, hit_15pct_10d={ea['hit_15pct_10d_rate']}, "
            f"fp_rate={ea['false_positive_rate']}\n"
            f"  ARMED: n={armed['n']}, hit_15pct_10d={armed['hit_15pct_10d_rate']}, "
            f"fp_rate={armed['false_positive_rate']}\n"
            f"Report: {report_path}"
        )
        request_id_full = save_approval_request({
            "request_id": f"cal-{request_id}",
            "category": "SQUEEZE_CALIBRATION",
            "risk_level": "MEDIUM",
            "title": f"Squeeze calibration review — {date.today().isoformat()}",
            "summary": summary,
            "evidence_ref": report_path,
            "proposed_change_json": {
                "type": "calibration_review",
                "metrics_by_state": metrics,
                "report_path": report_path,
            },
        })
        if request_id_full:
            log.info("Created approval request %s for calibration review", request_id_full)
        return request_id_full or None

    except Exception as exc:
        log.warning("maybe_create_approval_request failed: %s", exc)
        return None


def backfill_outcomes(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    min_score: float = 0.0,
) -> int:
    """
    Run SqueezeOutcomeReplay over historical squeeze_scores data and persist
    labeled outcomes into squeeze_training_outcomes for any row whose 30-day
    forward window is fully closed.

    This is the primary mechanism for populating the training outcomes table
    without manual intervention. Call it before run_calibration() when the
    table may be empty or stale.

    Returns the count of outcome rows persisted (non-fatal on failure).
    """
    try:
        from backtest import SqueezeOutcomeReplay
        log.info("backfill_outcomes: running replay %s → %s (min_score=%.1f)",
                 start_date or "all", end_date or "all", min_score)
        replay = SqueezeOutcomeReplay(
            start_date=start_date or "2020-01-01",
            end_date=end_date or date.today().isoformat(),
            min_score=min_score,
        )
        n_snaps = replay.load_snapshots()
        log.info("backfill_outcomes: loaded %d squeeze_scores snapshots", n_snaps)
        if n_snaps == 0:
            log.info("backfill_outcomes: no snapshots found — nothing to label")
            return 0
        replay.run(persist_outcomes=True)
        # _persist_training_outcomes logs its own count; replay._results has all rows
        n_closed = int(replay._results["fwd_30d"].notna().sum()) if not replay._results.empty else 0
        log.info("backfill_outcomes: %d rows with closed 30d window written", n_closed)
        return n_closed
    except Exception as exc:
        log.warning("backfill_outcomes failed (non-fatal): %s", exc)
        return 0


def run_calibration(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    min_sample: int = MIN_MEANINGFUL_SAMPLE,
    create_approval: bool = False,
    backfill: bool = False,
) -> str:
    """
    Main calibration entry point. Returns path to the written report.

    Parameters
    ----------
    backfill : when True, run SqueezeOutcomeReplay first to populate
               squeeze_training_outcomes before reading. Use this when the table
               is empty or when new squeeze_scores data has been added since the
               last calibration run.
    """
    if backfill:
        log.info("Backfilling training outcomes before calibration…")
        backfill_outcomes(start_date=start_date, end_date=end_date)

    log.info("Fetching training outcomes from Supabase…")
    try:
        from utils.supabase_persist import fetch_squeeze_training_outcomes
        rows = fetch_squeeze_training_outcomes(
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:
        log.error("Failed to fetch training outcomes: %s", exc)
        rows = []

    report_date = date.today().isoformat()
    log.info("Loaded %d labeled outcome rows", len(rows))

    if not rows:
        log.warning("No labeled outcomes found. Check that forward windows have closed and outcomes are persisted.")
        report_text = (
            f"# Squeeze Calibration Report — {report_date}\n\n"
            f"**No labeled outcomes found.**\n\n"
            f"Possible causes:\n"
            f"- No squeeze training snapshots have been persisted yet.\n"
            f"- Forward windows have not yet closed (need >= 30 trading days of history).\n"
            f"- squeeze_training_outcomes table is empty.\n\n"
            f"Run the pipeline to accumulate training data, then re-run calibration.\n"
        )
        report_path = REPORTS_DIR / f"squeeze_calibration_{report_date}.md"
        report_path.write_text(report_text)
        log.info("Empty-data report written to %s", report_path)
        return str(report_path)

    report_text = generate_report(rows, report_date, start_date, end_date)
    report_path = REPORTS_DIR / f"squeeze_calibration_{report_date}.md"
    report_path.write_text(report_text)
    log.info("Calibration report written to %s", report_path)

    if create_approval:
        metrics = compute_metrics(rows)
        maybe_create_approval_request(metrics, str(report_path), min_sample)

    return str(report_path)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Squeeze probability calibration and review gate (TRD-013).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--start", metavar="DATE", help="Start date YYYY-MM-DD (default: all)")
    p.add_argument("--end",   metavar="DATE", help="End date YYYY-MM-DD (default: all)")
    p.add_argument("--min-sample", type=int, default=MIN_MEANINGFUL_SAMPLE,
                   help=f"Min signals per state to flag as sufficient (default {MIN_MEANINGFUL_SAMPLE})")
    p.add_argument("--create-approval", action="store_true",
                   help="Create an approval_request in Supabase when thresholds may need review")
    p.add_argument("--backfill", action="store_true",
                   help="Run SqueezeOutcomeReplay first to populate squeeze_training_outcomes")
    return p


def main() -> None:
    args = _build_parser().parse_args()
    path = run_calibration(
        start_date=args.start,
        end_date=args.end,
        min_sample=args.min_sample,
        create_approval=args.create_approval,
        backfill=args.backfill,
    )
    print(f"Report: {path}")


if __name__ == "__main__":
    main()
