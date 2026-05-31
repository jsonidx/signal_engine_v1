"""
scripts/collect_options_si_state.py — Daily options-state and short-interest collection
(TRD-039)

Wires the collection helpers in utils/supabase_persist.py into a runnable daily job.
Not part of run_master.sh — run independently or add as a cron/GHA step.

Usage
-----
    # Collect for recent daily_rankings tickers (last 7 days)
    source venv/bin/activate && python3 scripts/collect_options_si_state.py

    # Dry-run (collect but do not write to DB)
    python3 scripts/collect_options_si_state.py --dry-run

    # Limit to N tickers (fastest for testing)
    python3 scripts/collect_options_si_state.py --max-tickers 10

    # Explicit ticker list
    python3 scripts/collect_options_si_state.py --tickers AAPL MSFT GME

Runtime notes
-------------
- Requires venv with psycopg2, yfinance, DATABASE_URL in environment.
- Rate-limits yfinance calls with a 0.4s sleep between tickers.
- Options collection skips tickers with no listed options chain.
- SI collection skips tickers where yfinance returns no sharesShort.
- Both are non-fatal — partial collection is still persisted.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SLEEP_BETWEEN_TICKERS = 0.4   # seconds, respects yfinance rate limits
DEFAULT_MAX_TICKERS = 100


def load_tickers_from_db(max_tickers: int) -> list[str]:
    """Load recent tickers from daily_rankings (last 7 days)."""
    from utils.db import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT ticker FROM daily_rankings
        WHERE run_date >= CURRENT_DATE - 7
        ORDER BY ticker
        LIMIT %s
        """,
        (max_tickers,),
    )
    tickers = [r["ticker"] for r in cur.fetchall()]
    conn.close()
    return tickers


def run_collection(
    tickers: list[str],
    snapshot_date: date,
    dry_run: bool = False,
) -> dict:
    """
    Collect options-state and short-interest for each ticker and persist.

    Returns summary: {options_ok, options_skip, si_ok, si_skip, dry_run}
    """
    from utils.supabase_persist import (
        collect_options_state_for_ticker,
        collect_short_interest_for_ticker,
        save_options_state_snapshots,
        save_short_interest_history,
    )

    options_records = []
    si_records = []
    options_skip = 0
    si_skip = 0

    for i, ticker in enumerate(tickers):
        logger.info("[%d/%d] %s", i + 1, len(tickers), ticker)

        # Options state
        opt_rec = collect_options_state_for_ticker(ticker, snapshot_date=snapshot_date)
        if opt_rec:
            options_records.append(opt_rec)
        else:
            options_skip += 1
            logger.debug("%s: no options data", ticker)

        # Short interest
        si_rec = collect_short_interest_for_ticker(ticker, snapshot_date=snapshot_date)
        if si_rec:
            si_records.append(si_rec)
        else:
            si_skip += 1
            logger.debug("%s: no SI data", ticker)

        time.sleep(SLEEP_BETWEEN_TICKERS)

    logger.info(
        "Collection complete: options=%d skipped=%d | SI=%d skipped=%d",
        len(options_records), options_skip, len(si_records), si_skip,
    )

    if not dry_run:
        if options_records:
            save_options_state_snapshots(options_records)
            logger.info("Persisted %d options-state rows", len(options_records))
        if si_records:
            save_short_interest_history(si_records)
            logger.info("Persisted %d SI rows", len(si_records))
    else:
        logger.info("[dry-run] Would write %d options + %d SI rows",
                    len(options_records), len(si_records))

    return {
        "options_ok": len(options_records),
        "options_skip": options_skip,
        "si_ok": len(si_records),
        "si_skip": si_skip,
        "dry_run": dry_run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect daily options-state and short-interest snapshots (TRD-039)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Collect but do not write to DB")
    parser.add_argument("--max-tickers", type=int, default=DEFAULT_MAX_TICKERS,
                        help="Max tickers to process from daily_rankings")
    parser.add_argument("--tickers", nargs="+", default=None, metavar="TICKER",
                        help="Explicit ticker list (overrides DB lookup)")
    parser.add_argument("--date", default=None, help="Snapshot date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    snapshot_date = date.fromisoformat(args.date) if args.date else date.today()

    if args.tickers:
        tickers = [t.upper().strip() for t in args.tickers if t.strip()]
        logger.info("Using %d tickers from --tickers", len(tickers))
    else:
        logger.info("Loading tickers from daily_rankings (max=%d)...", args.max_tickers)
        tickers = load_tickers_from_db(args.max_tickers)
        logger.info("Loaded %d tickers", len(tickers))

    if not tickers:
        logger.error("No tickers to process — aborting")
        sys.exit(1)

    summary = run_collection(tickers, snapshot_date=snapshot_date, dry_run=args.dry_run)
    logger.info("Done: %s", summary)


if __name__ == "__main__":
    main()
