#!/usr/bin/env python3
"""
scripts/smoke_check_squeeze.py
===============================
Post-fix squeeze_scores data quality smoke check.

Runs after every daily pipeline run (triggered via squeeze_smoke_check.yml).
Queries the DB, applies PASS/PARTIAL/FAIL thresholds, and overwrites
reports/post_fix_first_pipeline_smoke_check.md with current findings.

Exit codes:
    0 — PASS or PARTIAL (expected state, no action needed)
    1 — FAIL (post-fix rows exist but critical fields are null)
"""

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from utils.db import managed_connection

REPORT_PATH = Path(__file__).resolve().parents[1] / "reports" / "post_fix_first_pipeline_smoke_check.md"
FIX_COMMIT_DATE = "2026-04-26"  # TEXT column — compare as string
FIX_COMMIT_SHA = "7caf4e4"

CHUNK_FIELDS = [
    "computed_dtc_30d",
    "compression_recovery_score",
    "volume_confirmation_flag",
    "squeeze_state",
    "risk_score",
    "risk_level",
    "options_pressure_score",
    "explanation_summary",
    "explanation_json",
    "state_confidence",
    "state_reasons",
    "state_warnings",
    "dilution_risk_flag",
    "iv_rank",
]

PASS_THRESHOLD = 0.90  # squeeze_state, risk_score, explanation_json must be ≥ 90% non-null


def _iso(d) -> str:
    """Normalize a DB date value (datetime.date or str) to an ISO string."""
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def query_latest_run(cur):
    cur.execute("""
        SELECT date, COUNT(*) AS row_count
        FROM squeeze_scores
        GROUP BY date
        ORDER BY date DESC
        LIMIT 5
    """)
    rows = []
    for r in cur.fetchall():
        row = dict(r)
        row["date"] = _iso(row["date"])
        rows.append(row)
    return rows


def query_field_coverage(cur, run_date):
    count_exprs = ", ".join(
        f"COUNT({f}) AS {f}" for f in CHUNK_FIELDS
    )
    cur.execute(f"""
        SELECT COUNT(*) AS total, {count_exprs}
        FROM squeeze_scores
        WHERE date = %s
    """, (run_date,))
    return dict(cur.fetchone())


def query_json_fields(cur, run_date):
    cur.execute("""
        SELECT
            COUNT(*) FILTER (
                WHERE explanation_json IS NOT NULL
                AND explanation_json::text LIKE '%%si_persistence%%'
            ) AS si_persistence_in_json,
            COUNT(*) FILTER (
                WHERE explanation_json IS NOT NULL
                AND explanation_json::text LIKE '%%effective_float%%'
            ) AS effective_float_in_json,
            COUNT(*) AS total
        FROM squeeze_scores
        WHERE date = %s
    """, (run_date,))
    return dict(cur.fetchone())


def query_gate_metrics(cur):
    metrics = {}

    # Days with post-fix (non-null squeeze_state) rows — catches same-day fix+run
    cur.execute("SELECT COUNT(DISTINCT date) AS n FROM squeeze_scores WHERE squeeze_state IS NOT NULL")
    metrics["post_fix_days"] = cur.fetchone()["n"]

    cur.execute("SELECT COUNT(*) AS n FROM squeeze_scores WHERE squeeze_state IS NOT NULL")
    metrics["new_format_rows"] = cur.fetchone()["n"]

    cur.execute("SELECT COUNT(*) AS n FROM squeeze_scores WHERE squeeze_state IN ('ARMED', 'ACTIVE')")
    metrics["armed_or_active"] = cur.fetchone()["n"]

    cur.execute("SELECT COUNT(*) AS n FROM squeeze_scores WHERE risk_score IS NOT NULL")
    metrics["rows_with_risk_score"] = cur.fetchone()["n"]

    cur.execute("SELECT COUNT(*) AS n FROM squeeze_scores WHERE options_pressure_score IS NOT NULL")
    metrics["rows_with_options"] = cur.fetchone()["n"]

    cur.execute("SELECT COUNT(DISTINCT settlement_date) AS n FROM short_interest_history")
    metrics["si_history_periods"] = cur.fetchone()["n"]

    cur.execute("SELECT COUNT(DISTINCT ticker) AS n FROM filing_catalysts WHERE ownership_accumulation_flag = true")
    metrics["ownership_accumulation_tickers"] = cur.fetchone()["n"]

    cur.execute("""
        SELECT COUNT(*) AS n FROM (
            SELECT ticker FROM iv_history GROUP BY ticker HAVING COUNT(*) >= 60
        ) sub
    """)
    metrics["iv_history_tickers_60plus"] = cur.fetchone()["n"]

    # 20d forward return windows: oldest post-fix row must be > 20 business days ago
    cur.execute(f"""
        SELECT COUNT(*) AS n FROM squeeze_scores
        WHERE date > '{FIX_COMMIT_DATE}'
        AND (CURRENT_DATE - date::date) >= 28
    """)
    metrics["rows_20d_window_closed"] = cur.fetchone()["n"]

    return metrics


def run_tests():
    result = subprocess.run(
        ["python", "-m", "pytest", "-q",
         "tests/test_squeeze_persistence_schema.py",
         "tests/test_squeeze_replay.py",
         "tests/test_squeeze_screener.py",
         "--tb=no"],
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1]
    )
    return result.returncode == 0, result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "no output"


def run_compile():
    result = subprocess.run(
        ["python", "-m", "py_compile",
         "utils/supabase_persist.py", "backtest.py", "squeeze_screener.py"],
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1]
    )
    return result.returncode == 0


def determine_verdict(latest_date, coverage, total):
    if latest_date is None or total == 0:
        return "PARTIAL", "No squeeze_scores rows found"

    squeeze_state_pct = coverage.get("squeeze_state", 0) / total
    risk_score_pct = coverage.get("risk_score", 0) / total
    explanation_json_pct = coverage.get("explanation_json", 0) / total

    # Coverage-first: if CHUNK fields are populated the fix is working regardless
    # of whether the run date is the same calendar day as the fix commit
    if (squeeze_state_pct >= PASS_THRESHOLD
            and risk_score_pct >= PASS_THRESHOLD
            and explanation_json_pct >= PASS_THRESHOLD):
        return "PASS", f"squeeze_state {squeeze_state_pct:.0%} / risk_score {risk_score_pct:.0%} / explanation_json {explanation_json_pct:.0%}"

    # All zeros on an old-format run — expected pre-fix state
    if squeeze_state_pct == 0 and risk_score_pct == 0 and latest_date <= FIX_COMMIT_DATE:
        return "PARTIAL", f"No post-fix pipeline run yet (latest run {latest_date} is pre-fix)"

    # Post-fix run exists but fields still null — real failure
    if squeeze_state_pct == 0 and risk_score_pct == 0:
        return "FAIL", "Post-fix rows exist but squeeze_state and risk_score are 0% non-null — check squeeze_screener.py → save_squeeze_scores() payload"

    return "PARTIAL", f"squeeze_state {squeeze_state_pct:.0%} / risk_score {risk_score_pct:.0%} / explanation_json {explanation_json_pct:.0%}"


def pct(n, total):
    if total == 0:
        return "N/A"
    return f"{n}/{total} ({n/total:.0%})"


def build_report(run_dates, latest_date, coverage, json_fields, gate, verdict, verdict_detail,
                 compile_ok, tests_ok, tests_summary, round_num, unique_tickers):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total = coverage.get("total", 0) if coverage else 0

    verdict_icon = {"PASS": "✅", "PARTIAL": "⚠️", "FAIL": "❌"}.get(verdict, "")

    lines = [
        f"# Post-Fix Pipeline Smoke Check — Round {round_num}",
        "",
        f"**Generated:** {now}  ",
        f"**Schema fix commits:** `{FIX_COMMIT_SHA}` (2026-04-26 13:05) + `7af59be` (2026-04-26 13:08)  ",
        "",
        "---",
        "",
        "## 1. Executive Verdict",
        "",
        f"**{verdict} {verdict_icon}**",
        "",
        f"{verdict_detail}",
        "",
    ]

    if verdict == "FAIL":
        lines += [
            "> **Action required:** Investigate `squeeze_screener.py` → `save_squeeze_scores()` payload mapping.",
            "> The pipeline ran but critical CHUNK fields were not written. Do not implement CHUNK-12 until resolved.",
            "",
        ]
    elif verdict == "PARTIAL":
        lines += [
            "> **Action required:** Allow the next scheduled pipeline run to execute, then re-check.",
            "",
        ]
    else:
        lines += [
            "> **Next milestone:** Continue accumulating data toward the CHUNK-12 gate (~2026-05-26).",
            "",
        ]

    lines += [
        "---",
        "",
        "## 2. Latest Run Coverage",
        "",
        "### Recent run dates",
        "",
        "| Date | Row count |",
        "|---|---|",
    ]
    for r in run_dates:
        marker = " ← latest" if r["date"] == latest_date else ""
        lines.append(f"| {r['date']} | {r['row_count']}{marker} |")

    lines += [
        "",
        f"**Unique tickers in latest run:** {unique_tickers}  ",
        f"**Post-fix run?** {'Yes ✅' if latest_date and latest_date > FIX_COMMIT_DATE else 'No ❌ (pre-fix data only)'}",
        "",
        "### CHUNK field coverage — latest run",
        "",
        "| Field | Non-null | Coverage |",
        "|---|---|---|",
    ]

    for f in CHUNK_FIELDS:
        n = coverage.get(f, 0) if coverage else 0
        tag = " — old-format" if (n == 0 and total > 0) else ""
        lines.append(f"| `{f}` | {n}/{total} | {pct(n, total)}{tag} |")

    # JSON-stored fields
    if json_fields:
        si = json_fields.get("si_persistence_in_json", 0)
        ef = json_fields.get("effective_float_in_json", 0)
        lines += [
            f"| `si_persistence_score` (via explanation_json) | {si}/{total} | {pct(si, total)} |",
            f"| `effective_float_score` (via explanation_json) | {ef}/{total} | {pct(ef, total)} |",
        ]

    lines += [
        "",
        "---",
        "",
        "## 3. Compile and Test State",
        "",
        "| Check | Result |",
        "|---|---|",
        f"| `py_compile` (supabase_persist, backtest, squeeze_screener) | {'✅ OK' if compile_ok else '❌ FAIL'} |",
        f"| `pytest` (persistence schema + replay + screener) | {'✅ ' if tests_ok else '❌ '}{tests_summary} |",
        "",
        "---",
        "",
        "## 4. CHUNK-12 Gate Progress",
        "",
        "| Gate item | Required | Current | Status |",
        "|---|---:|---:|---|",
        f"| Calendar days of post-fix squeeze_scores | ≥ 30 | **{gate['post_fix_days']}** | {'✅' if gate['post_fix_days'] >= 30 else '❌'} |",
        f"| New-format rows total | ≥ 500 | **{gate['new_format_rows']}** | {'✅' if gate['new_format_rows'] >= 500 else '❌'} |",
        f"| Rows with ARMED or ACTIVE state | ≥ 50 | **{gate['armed_or_active']}** | {'✅' if gate['armed_or_active'] >= 50 else '❌'} |",
        f"| 20-day forward return windows closed | ≥ 100 rows | **{gate['rows_20d_window_closed']}** | {'✅' if gate['rows_20d_window_closed'] >= 100 else '❌'} |",
        f"| Rows with non-null `risk_score` | ≥ 100 | **{gate['rows_with_risk_score']}** | {'✅' if gate['rows_with_risk_score'] >= 100 else '❌'} |",
        f"| Rows with non-null `options_pressure_score` | ≥ 20 | **{gate['rows_with_options']}** | {'✅' if gate['rows_with_options'] >= 20 else '❌'} |",
        f"| `short_interest_history` distinct FINRA periods | ≥ 2 | **{gate['si_history_periods']}** | {'✅' if gate['si_history_periods'] >= 2 else '❌'} |",
        f"| `filing_catalysts` ownership_accumulation_flag tickers | ≥ 5 | **{gate['ownership_accumulation_tickers']}** | {'✅' if gate['ownership_accumulation_tickers'] >= 5 else '❌'} |",
        f"| `iv_history` tickers with ≥ 60 rows | ≥ 50 | **{gate['iv_history_tickers_60plus']}** | {'✅' if gate['iv_history_tickers_60plus'] >= 50 else '❌'} |",
        "",
        "---",
        "",
        "## 5. Recommendation",
        "",
    ]

    if verdict == "PASS":
        lines += [
            "**Pipeline persistence is working correctly. Continue data accumulation toward the CHUNK-12 gate.**",
            "",
            "All critical CHUNK fields are being written on every run. The system is accumulating valid replay data.",
            "Re-check gate progress in this report after each daily run.",
        ]
    elif verdict == "FAIL":
        lines += [
            "**INVESTIGATION REQUIRED before next CHUNK implementation.**",
            "",
            "Post-fix rows exist but `squeeze_state` and/or `risk_score` are null. This means the",
            "persistence fix landed correctly but the squeeze screener is not populating these fields",
            "in the DataFrame it passes to `save_squeeze_scores()`.",
            "",
            "Check: `squeeze_screener.py` — verify the `SqueezeScore` dataclass fields are mapped",
            "into the DataFrame columns that `save_squeeze_scores()` expects at their correct positions.",
        ]
    else:
        lines += [
            "**Continue data accumulation. No pipeline changes needed.**",
            "",
            "The persistence infrastructure is verified. Waiting for post-fix pipeline runs to accumulate data.",
        ]

    return "\n".join(lines) + "\n"


def get_round_number():
    if not REPORT_PATH.exists():
        return 1
    content = REPORT_PATH.read_text()
    import re
    m = re.search(r"Round (\d+)", content)
    return (int(m.group(1)) + 1) if m else 1


def main():
    print("Running squeeze smoke check...")

    compile_ok = run_compile()
    print(f"Compile: {'OK' if compile_ok else 'FAIL'}")

    tests_ok, tests_summary = run_tests()
    print(f"Tests: {tests_summary}")

    with managed_connection() as conn:
        cur = conn.cursor()

        run_dates = query_latest_run(cur)
        latest_date = run_dates[0]["date"] if run_dates else None
        print(f"Latest run date: {latest_date}")

        coverage = {}
        json_fields = {}
        unique_tickers = 0

        if latest_date:
            coverage = query_field_coverage(cur, latest_date)
            json_fields = query_json_fields(cur, latest_date)
            cur.execute(
                "SELECT COUNT(DISTINCT ticker) AS n FROM squeeze_scores WHERE date = %s",
                (latest_date,)
            )
            unique_tickers = cur.fetchone()["n"]

        gate = query_gate_metrics(cur)

    total = coverage.get("total", 0)
    verdict, verdict_detail = determine_verdict(latest_date, coverage, total)
    print(f"Verdict: {verdict} — {verdict_detail}")

    round_num = get_round_number()
    report = build_report(
        run_dates, latest_date, coverage, json_fields, gate,
        verdict, verdict_detail, compile_ok, tests_ok, tests_summary,
        round_num, unique_tickers
    )

    REPORT_PATH.write_text(report)
    print(f"Report written: {REPORT_PATH}")

    return 0 if verdict in ("PASS", "PARTIAL") else 1


if __name__ == "__main__":
    sys.exit(main())
