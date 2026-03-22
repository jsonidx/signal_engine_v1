#!/usr/bin/env python3
"""
================================================================================
FUNDAMENTALS CACHE  — SQLite-backed cache for quarterly fundamental data
================================================================================
Fundamental data (PE ratios, revenue growth, margins, analyst ratings) changes
at most once per quarter. This module caches it in a local SQLite database so
we skip the yfinance network call on every run.

CACHE FILE:   ./fundamentals_cache.db   (gitignored)
DEFAULT TTL:  30 days  (refreshes ~monthly, well within one earnings quarter)

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
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from utils.db import get_connection

# ── Config ────────────────────────────────────────────────────────────────────
CACHE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fundamentals_cache.db")
DEFAULT_TTL_DAYS = 30   # Refresh at most once per month


# ── Internal helpers ──────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = get_connection(CACHE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals (
            ticker      TEXT PRIMARY KEY,
            data_json   TEXT NOT NULL,
            fetched_at  TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_days(fetched_at_iso: str) -> float:
    fetched = datetime.fromisoformat(fetched_at_iso)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - fetched).total_seconds() / 86400


# ── Public API ────────────────────────────────────────────────────────────────

def get_cached(ticker: str, ttl_days: int = DEFAULT_TTL_DAYS) -> Optional[dict]:
    """
    Return cached fundamental data for ticker if it exists and is not expired.
    Returns None if cache miss or stale (caller should fetch fresh data).
    """
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT data_json, fetched_at FROM fundamentals WHERE ticker = ?",
            (ticker.upper(),)
        ).fetchone()
        conn.close()

        if row is None:
            return None

        data_json, fetched_at = row
        if _age_days(fetched_at) > ttl_days:
            return None  # Stale

        return json.loads(data_json)

    except Exception:
        return None


def save_to_cache(ticker: str, data: dict) -> None:
    """
    Save or update the cached fundamental data for a ticker.
    Call this after a successful yfinance fetch.
    """
    try:
        conn = _connect()
        conn.execute(
            """
            INSERT INTO fundamentals (ticker, data_json, fetched_at)
            VALUES (?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                data_json  = excluded.data_json,
                fetched_at = excluded.fetched_at
            """,
            (ticker.upper(), json.dumps(data), _now_iso())
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Cache write failure is non-fatal


def clear_ticker(ticker: str) -> None:
    """Remove one ticker from the cache (forces re-fetch on next run)."""
    try:
        conn = _connect()
        conn.execute("DELETE FROM fundamentals WHERE ticker = ?", (ticker.upper(),))
        conn.commit()
        conn.close()
    except Exception:
        pass


def clear_all() -> None:
    """Wipe the entire cache."""
    try:
        conn = _connect()
        conn.execute("DELETE FROM fundamentals")
        conn.commit()
        conn.close()
    except Exception:
        pass


def cache_status() -> list:
    """
    Return list of dicts: [{ticker, fetched_at, age_days, expired}] for all rows.
    """
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT ticker, fetched_at FROM fundamentals ORDER BY ticker"
        ).fetchall()
        conn.close()
        result = []
        for ticker, fetched_at in rows:
            age = _age_days(fetched_at)
            result.append({
                "ticker": ticker,
                "fetched_at": fetched_at[:19].replace("T", " "),
                "age_days": round(age, 1),
                "expired": age > DEFAULT_TTL_DAYS,
            })
        return result
    except Exception:
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
        # Import here to avoid circular deps at module level
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
