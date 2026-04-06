#!/usr/bin/env python3
"""
================================================================================
FUNDAMENTALS CACHE  — Supabase-backed cache for quarterly fundamental data
================================================================================
Fundamental data (PE ratios, revenue growth, margins, analyst ratings) changes
at most once per quarter. This module caches it in Supabase so we skip the
yfinance network call on every run — including GitHub Actions runners that have
no local state between runs.

CACHE TABLE:  fundamentals  (in the shared Supabase project)
DEFAULT TTL:  30 days  (refreshes ~monthly, well within one earnings quarter)

The public API is identical to the old SQLite version so no changes are needed
in fundamental_analysis.py or any other caller.

USAGE:
    from fundamentals_cache import get_cached, save_to_cache, clear_ticker, clear_all

    # Read (returns None if missing or expired)
    raw = get_cached("GME")

    # Write
    save_to_cache("GME", raw_dict)

    # Force-expire a single ticker (next run re-fetches)
    clear_ticker("GME")

    # Wipe everything
    clear_all()

CLI:
    python3 fundamentals_cache.py --list            # Show all cached tickers + age
    python3 fundamentals_cache.py --clear GME       # Expire one ticker
    python3 fundamentals_cache.py --clear-all       # Wipe entire cache
    python3 fundamentals_cache.py --refresh GME     # Force re-fetch GME right now
================================================================================
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_TTL_DAYS = 30   # Refresh at most once per month


# ── Internal helpers ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_days(fetched_at_iso) -> float:
    """Accept a datetime object (psycopg2) or an ISO string."""
    if isinstance(fetched_at_iso, datetime):
        fetched = fetched_at_iso
    else:
        fetched = datetime.fromisoformat(str(fetched_at_iso))
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - fetched).total_seconds() / 86400


def _conn():
    """Open a Supabase connection. Raises if DATABASE_URL is not set."""
    from utils.db import get_connection
    return get_connection()


# ── Public API ────────────────────────────────────────────────────────────────

def get_cached(ticker: str, ttl_days: int = DEFAULT_TTL_DAYS) -> Optional[dict]:
    """
    Return cached fundamental data for ticker if it exists and is not expired.
    Returns None if cache miss or stale (caller should fetch fresh data).
    """
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT data_json, fetched_at FROM fundamentals WHERE ticker = %s",
            (ticker.upper(),),
        )
        row = cur.fetchone()
        conn.close()

        if row is None:
            return None

        if _age_days(row["fetched_at"]) > ttl_days:
            return None  # Stale

        return json.loads(row["data_json"])

    except Exception as exc:
        from db_cache import _handle_missing_tables
        if not _handle_missing_tables(exc):
            logger.debug("fundamentals_cache get_cached(%s): %s", ticker, exc)
        return None


def save_to_cache(ticker: str, data: dict) -> None:
    """
    Save or update the cached fundamental data for a ticker.
    Call this after a successful yfinance fetch.
    """
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO fundamentals (ticker, data_json, fetched_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (ticker) DO UPDATE SET
                data_json  = EXCLUDED.data_json,
                fetched_at = NOW()
            """,
            (ticker.upper(), json.dumps(data)),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        from db_cache import _handle_missing_tables
        if not _handle_missing_tables(exc):
            logger.debug("fundamentals_cache save_to_cache(%s): %s", ticker, exc)
        # Cache write failure is non-fatal


def clear_ticker(ticker: str) -> None:
    """Remove one ticker from the cache (forces re-fetch on next run)."""
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM fundamentals WHERE ticker = %s", (ticker.upper(),))
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("fundamentals_cache clear_ticker(%s): %s", ticker, exc)


def clear_all() -> None:
    """Wipe the entire cache."""
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM fundamentals")
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("fundamentals_cache clear_all: %s", exc)


def cache_status() -> list:
    """
    Return list of dicts: [{ticker, fetched_at, age_days, expired}] for all rows.
    """
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT ticker, fetched_at FROM fundamentals ORDER BY ticker"
        )
        rows = cur.fetchall()
        conn.close()

        result = []
        for row in rows:
            age = _age_days(row["fetched_at"])
            result.append({
                "ticker":     row["ticker"],
                "fetched_at": str(row["fetched_at"])[:19].replace("T", " "),
                "age_days":   round(age, 1),
                "expired":    age > DEFAULT_TTL_DAYS,
            })
        return result
    except Exception as exc:
        logger.debug("fundamentals_cache cache_status: %s", exc)
        return []


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fundamentals cache manager")
    parser.add_argument("--list",      action="store_true", help="Show all cached tickers")
    parser.add_argument("--clear",     metavar="TICKER",    help="Expire one ticker")
    parser.add_argument("--clear-all", action="store_true", help="Wipe entire cache")
    parser.add_argument("--refresh",   metavar="TICKER",    help="Force re-fetch one ticker now")
    args = parser.parse_args()

    if args.list:
        rows = cache_status()
        if not rows:
            print("Cache is empty.")
            return
        print(f"\n  {'TICKER':<8}  {'FETCHED':<19}  {'AGE':>6}  STATUS")
        print("  " + "-" * 50)
        for r in rows:
            status = "EXPIRED" if r["expired"] else "fresh"
            print(f"  {r['ticker']:<8}  {r['fetched_at']:<19}  {r['age_days']:>5.1f}d  {status}")
        print(f"\n  {len(rows)} tickers cached  |  TTL = {DEFAULT_TTL_DAYS} days\n")

    elif args.clear:
        clear_ticker(args.clear)
        print(f"  Cleared {args.clear.upper()} from cache.")

    elif args.clear_all:
        clear_all()
        print("  Cache wiped.")

    elif args.refresh:
        ticker = args.refresh.upper()
        clear_ticker(ticker)
        from fundamental_analysis import fetch_fundamentals
        print(f"  Fetching {ticker} from yfinance...", end=" ", flush=True)
        raw = fetch_fundamentals(ticker, use_cache=False)
        if raw:
            print("OK")
        else:
            print("FAILED (ticker invalid or no data)")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
