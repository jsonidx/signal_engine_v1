#!/usr/bin/env python3
"""
Refresh AI theses that haven't been updated in N days.

Usage:
    python refresh_stale_theses.py                    # dry run, 7-day cutoff
    python refresh_stale_theses.py --run              # actually re-run
    python refresh_stale_theses.py --days 14 --run    # 14-day cutoff
    python refresh_stale_theses.py --days 5 --run     # 5-day cutoff
    python refresh_stale_theses.py --llm grok-premium --run

Logic:
    Finds all tickers whose most recent thesis is older than --days.
    Calls ai_quant.py --tickers <list> --no-cache --force-ai for each batch.
"""

import argparse
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from utils.db import get_connection


def get_stale_tickers(days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM blacklist WHERE expires_at IS NULL OR expires_at > NOW()")
            blacklisted = {r["ticker"] for r in cur.fetchall()}
            cur.execute(
                """
                SELECT ticker, MAX(created_at) AS latest_thesis
                FROM thesis_cache
                GROUP BY ticker
                HAVING MAX(created_at::timestamptz) < %s
                ORDER BY latest_thesis ASC
                """,
                (cutoff,),
            )
            return [r for r in cur.fetchall() if r["ticker"] not in blacklisted]


def main():
    parser = argparse.ArgumentParser(description="Refresh stale AI theses")
    parser.add_argument("--days", type=int, default=7, help="Stale threshold in days (default: 7)")
    parser.add_argument("--run", action="store_true", help="Actually execute re-runs (default: dry run)")
    parser.add_argument("--llm", type=str, default=None, help="LLM backend to use (e.g. grok-premium)")
    parser.add_argument("--batch-size", type=int, default=10, help="Tickers per ai_quant.py call (default: 10)")
    args = parser.parse_args()

    rows = get_stale_tickers(args.days)

    if not rows:
        print(f"No stale theses found (cutoff: {args.days} days). All tickers are fresh.")
        return

    tickers = [r["ticker"] for r in rows]
    cutoff_str = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")

    print(f"Found {len(tickers)} tickers with thesis older than {args.days} days (before {cutoff_str}):")
    for r in rows:
        latest = datetime.fromisoformat(r["latest_thesis"]).replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - latest).days
        print(f"  {r['ticker']:<8} last thesis: {latest.strftime('%Y-%m-%d')}  ({age}d ago)")

    if not args.run:
        print(f"\nDry run — pass --run to execute.")
        print(f"Command that would run:")
        for i in range(0, len(tickers), args.batch_size):
            batch = tickers[i : i + args.batch_size]
            cmd = build_cmd(batch, args.llm)
            print(f"  {' '.join(cmd)}")
        return

    print(f"\nRunning in batches of {args.batch_size}...")
    for i in range(0, len(tickers), args.batch_size):
        batch = tickers[i : i + args.batch_size]
        cmd = build_cmd(batch, args.llm)
        print(f"\n--- Batch {i // args.batch_size + 1}: {' '.join(batch)} ---")
        print(f"CMD: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd="/Users/jason/signal_engine_v1")
        if result.returncode != 0:
            print(f"WARNING: batch exited with code {result.returncode}")


def build_cmd(tickers: list[str], llm: str | None) -> list[str]:
    cmd = [sys.executable, "ai_quant.py", "--tickers"] + tickers + ["--no-cache", "--force-ai"]
    if llm:
        cmd += ["--llm", llm]
    return cmd


if __name__ == "__main__":
    main()
