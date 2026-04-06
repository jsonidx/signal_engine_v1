"""
db_cache.py — Supabase-backed blacklist and ticker metadata cache.

Provides caches that survive across GitHub Actions runs (unlike local
JSON/SQLite files which are lost when the runner exits):

  blacklist        — tickers to skip in all pipeline steps, with optional TTL
  ticker_metadata  — IPO dates, sector/industry, delist status, and the
                     liquidity-filter universe snapshot (warm-start cache)

PUBLIC API
----------
    # Blacklist
    is_blacklisted(ticker)                         -> bool
    add_to_blacklist(ticker, reason, expires_at)   -> None
    remove_from_blacklist(ticker)                  -> None
    get_active_blacklist()                         -> list[str]

    # Ticker metadata — single ticker
    get_ticker_metadata(ticker)                    -> dict | None
    save_ticker_metadata(ticker, data)             -> None
    bulk_get_ipo_dates(tickers)                    -> dict[str, Timestamp | None]

    # Universe snapshot — liquidity filter warm-start cache
    get_cached_universe(max_age_hours)             -> list[str] | None
    save_universe_results(passed, sector_map)      -> None

    # Migration helper
    migrate_liquidity_failed_log(log_path)         -> int  (entries added)

USAGE
-----
    from db_cache import is_blacklisted, add_to_blacklist, bulk_get_ipo_dates
    from db_cache import get_cached_universe, save_universe_results

    # Skip known-bad tickers before any yfinance call
    tickers = [t for t in candidates if not is_blacklisted(t)]

    # Prefetch IPO dates into the backtest's in-memory cache
    ipo_map = bulk_get_ipo_dates(tickers)   # one DB query, not 185

    # Warm-start universe (skip 1000-ticker yfinance batch download)
    universe = get_cached_universe()        # None on first run or if stale
    if universe is None:
        universe = run_expensive_liquidity_filter(...)
        save_universe_results(universe, sector_map)

    # Flag a confirmed delist permanently
    add_to_blacklist("LILM", reason="confirmed_delist")

    # Flag a transient data failure with 7-day TTL
    from datetime import datetime, timezone, timedelta
    add_to_blacklist("XYZ", reason="no_yfinance_data",
                     expires_at=datetime.now(timezone.utc) + timedelta(days=7))

All functions fail open (return safe defaults on DB error) so a Supabase
outage never breaks the pipeline.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy import so modules that don't need DB don't pay the psycopg2 import cost
_get_conn = None


def _connection():
    global _get_conn
    if _get_conn is None:
        from utils.db import managed_connection as _mc
        _get_conn = _mc
    return _get_conn


# =============================================================================
# BLACKLIST
# =============================================================================

def is_blacklisted(ticker: str) -> bool:
    """
    Return True if ticker is on the active blacklist (permanent or not yet expired).

    Fails open: returns False on any DB error so the pipeline never stalls.
    """
    try:
        with _connection()() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT 1 FROM blacklist
                WHERE ticker = %s
                  AND (expires_at IS NULL OR expires_at > NOW())
                LIMIT 1
                """,
                (ticker.upper(),),
            )
            return cur.fetchone() is not None
    except Exception as exc:
        logger.debug("is_blacklisted(%s) DB error (failing open): %s", ticker, exc)
        return False


def add_to_blacklist(
    ticker: str,
    reason: str,
    expires_at: Optional[datetime] = None,
) -> None:
    """
    Add or update a blacklist entry.

    expires_at=None  → permanent (use for confirmed delists / junk tickers).
    expires_at=<dt>  → temporary (transient failure; re-evaluated after TTL).

    Idempotent: calling again with a new reason/expiry overwrites the old entry.
    """
    try:
        with _connection()() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO blacklist (ticker, reason, added_at, expires_at)
                VALUES (%s, %s, NOW(), %s)
                ON CONFLICT (ticker) DO UPDATE SET
                    reason     = EXCLUDED.reason,
                    added_at   = NOW(),
                    expires_at = EXCLUDED.expires_at
                """,
                (ticker.upper(), reason, expires_at),
            )
        logger.debug("add_to_blacklist: %s (%s, expires=%s)", ticker, reason, expires_at)
    except Exception as exc:
        logger.warning("add_to_blacklist(%s) DB error: %s", ticker, exc)


def remove_from_blacklist(ticker: str) -> None:
    """Remove a ticker from the blacklist (use after manual review / re-listing)."""
    try:
        with _connection()() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM blacklist WHERE ticker = %s",
                (ticker.upper(),),
            )
        logger.debug("remove_from_blacklist: %s", ticker)
    except Exception as exc:
        logger.warning("remove_from_blacklist(%s) DB error: %s", ticker, exc)


def get_active_blacklist() -> list[str]:
    """
    Return all currently active (non-expired) blacklisted tickers.
    Returns [] on DB error.
    """
    try:
        with _connection()() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ticker FROM blacklist
                WHERE expires_at IS NULL OR expires_at > NOW()
                ORDER BY ticker
                """,
            )
            result = [row["ticker"] for row in cur.fetchall()]
            if result:
                logger.info("get_active_blacklist: %d tickers on blacklist", len(result))
            return result
    except Exception as exc:
        logger.warning("get_active_blacklist DB error: %s", exc)
        return []


# =============================================================================
# TICKER METADATA
# =============================================================================

def get_ticker_metadata(ticker: str) -> Optional[dict]:
    """
    Return metadata dict for ticker, or None if not in DB.

    Returned keys: ticker, is_active, ipo_date, delisted_date,
                   status, sector, industry, updated_at
    """
    try:
        with _connection()() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM ticker_metadata WHERE ticker = %s",
                (ticker.upper(),),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.debug("get_ticker_metadata(%s) DB error: %s", ticker, exc)
        return None


def save_ticker_metadata(ticker: str, data: dict) -> None:
    """
    Upsert metadata for a ticker.  Only keys present in *data* are written;
    existing columns not mentioned are untouched.

    Accepted keys (all optional):
        is_active       bool
        ipo_date        date | str | None   (ISO format string or date object)
        delisted_date   date | str | None
        status          str  — 'active' | 'delisted' | 'suspect' | 'unknown'
        sector          str
        industry        str
    """
    _ALLOWED = {"is_active", "ipo_date", "delisted_date", "status", "sector", "industry"}
    updates = {k: v for k, v in data.items() if k in _ALLOWED}
    if not updates:
        return

    cols = list(updates.keys())
    vals = [updates[c] for c in cols]
    col_list = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)

    try:
        with _connection()() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO ticker_metadata (ticker, {col_list}, updated_at)
                VALUES (%s, {placeholders}, NOW())
                ON CONFLICT (ticker) DO UPDATE SET
                    {set_clause},
                    updated_at = NOW()
                """,
                [ticker.upper()] + vals,
            )
    except Exception as exc:
        logger.warning("save_ticker_metadata(%s) DB error: %s", ticker, exc)


def bulk_get_ipo_dates(tickers: list[str]) -> dict:
    """
    Return {ticker: pd.Timestamp | None} for every ticker that has an ipo_date
    stored in ticker_metadata.  Tickers absent from the DB are omitted — callers
    fall back to yfinance for those.

    Used by WalkForwardBacktest to preload self._ipo_cache in one query instead
    of 185 individual yf.Ticker().info calls.
    """
    if not tickers:
        return {}

    upper = [t.upper() for t in tickers]
    try:
        import pandas as pd
        with _connection()() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ticker, ipo_date FROM ticker_metadata
                WHERE ticker = ANY(%s)
                  AND ipo_date IS NOT NULL
                """,
                (upper,),
            )
            result: dict = {}
            for row in cur.fetchall():
                d = row["ipo_date"]
                result[row["ticker"]] = pd.Timestamp(d).normalize() if d else None
            logger.info(
                "bulk_get_ipo_dates: cache hit — %d/%d tickers have stored IPO dates",
                len(result), len(tickers),
            )
            return result
    except Exception as exc:
        logger.warning("bulk_get_ipo_dates DB error: %s", exc)
        return {}


# =============================================================================
# UNIVERSE SNAPSHOT — liquidity filter warm-start cache
# =============================================================================

# Minimum number of tickers required to trust a cached universe.
# Guards against returning a truncated/corrupt snapshot.
_UNIVERSE_MIN_TICKERS = 50


def get_cached_universe(max_age_hours: float = 25.0) -> list[str] | None:
    """
    Return the set of tickers that passed the last liquidity filter run,
    if the snapshot is younger than *max_age_hours*.  Returns None when:
      - The DB is unavailable
      - No snapshot exists yet (first run)
      - The snapshot is stale (> max_age_hours old)
      - Fewer than _UNIVERSE_MIN_TICKERS tickers found (partial/corrupt cache)

    25h default handles ±1h schedule drift on daily GHA runs without going stale.

    Used by universe_builder._apply_liquidity_filter to skip the 1000-ticker
    yfinance batch download on warm runs (~2-4 min saved per GHA run).
    """
    try:
        with _connection()() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ticker FROM ticker_metadata
                WHERE is_active = TRUE
                  AND updated_at > NOW() - (INTERVAL '1 hour' * %s)
                ORDER BY ticker
                """,
                (max_age_hours,),
            )
            rows = cur.fetchall()

        if len(rows) < _UNIVERSE_MIN_TICKERS:
            logger.info(
                "get_cached_universe: cache miss — %d tickers in snapshot (need >=%d)",
                len(rows), _UNIVERSE_MIN_TICKERS,
            )
            return None

        tickers = [row["ticker"] for row in rows]
        logger.info(
            "get_cached_universe: warm cache HIT — %d tickers (age < %.0fh)",
            len(tickers), max_age_hours,
        )
        return tickers

    except Exception as exc:
        logger.info("get_cached_universe: DB unavailable (cold start) — %s", exc)
        return None


def save_universe_results(
    passed: list[str],
    sector_map: dict[str, str] | None = None,
) -> None:
    """
    Persist the liquidity-filter result into ticker_metadata.

    Each passed ticker is upserted with is_active=True, status='active', and
    sector from *sector_map* (if provided; existing sector is kept when absent).
    updated_at is set to NOW() so get_cached_universe() can detect staleness.

    Uses a single execute_values round-trip for efficiency (~185 rows ≈ <50ms).

    Called by universe_builder._apply_liquidity_filter after the download loop.
    On DB error the function fails silently — the pipeline continues normally.
    """
    if not passed:
        return

    _sm = sector_map or {}
    rows = [
        (t.upper(), True, "active", _sm.get(t.upper()) or _sm.get(t))
        for t in passed
    ]

    try:
        from psycopg2.extras import execute_values
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        execute_values(
            cur,
            """
            INSERT INTO ticker_metadata (ticker, is_active, status, sector, updated_at)
            VALUES %s
            ON CONFLICT (ticker) DO UPDATE SET
                is_active  = EXCLUDED.is_active,
                status     = EXCLUDED.status,
                sector     = COALESCE(EXCLUDED.sector, ticker_metadata.sector),
                updated_at = NOW()
            """,
            rows,
        )
        conn.commit()
        conn.close()
        logger.info("save_universe_results: upserted %d tickers into ticker_metadata", len(rows))
    except Exception as exc:
        logger.warning("save_universe_results DB error: %s", exc)


# =============================================================================
# MIGRATION HELPER
# =============================================================================

def migrate_liquidity_failed_log(log_path: Optional[str] = None) -> int:
    """
    Import entries from liquidity_failed.log into the blacklist table.

    All imported entries get a 7-day TTL (temporary).  The next pipeline run
    re-evaluates them; tickers that consistently fail should be manually
    promoted to permanent entries with:
        add_to_blacklist("TICKER", reason="confirmed_delist")

    Returns the number of new entries added.

    Usage:
        python3 -c "from db_cache import migrate_liquidity_failed_log; \\
                    n = migrate_liquidity_failed_log(); print(f'{n} entries added')"
    """
    if log_path is None:
        log_path = str(Path(__file__).parent / "liquidity_failed.log")

    if not os.path.exists(log_path):
        logger.info("migrate_liquidity_failed_log: %s not found", log_path)
        return 0

    expires = datetime.now(timezone.utc) + timedelta(days=7)
    tickers: list[str] = []
    with open(log_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tickers.append(line.upper())

    if not tickers:
        return 0

    added = 0
    for t in tickers:
        try:
            add_to_blacklist(t, reason="liquidity_filter_failed", expires_at=expires)
            added += 1
        except Exception as exc:
            logger.warning("migrate_liquidity_failed_log: could not add %s: %s", t, exc)

    logger.info("migrate_liquidity_failed_log: %d/%d entries added", added, len(tickers))
    print(f"  Migrated {added} entries from liquidity_failed.log → blacklist (7-day TTL)")
    return added


# =============================================================================
# CLI
# =============================================================================

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="db_cache — blacklist & metadata manager")
    sub = parser.add_subparsers(dest="cmd")

    bl = sub.add_parser("blacklist", help="Manage the blacklist")
    bl.add_argument("--list",   action="store_true", help="Show active blacklist")
    bl.add_argument("--add",    metavar="TICKER",    help="Add ticker (permanent)")
    bl.add_argument("--reason", metavar="REASON",    default="manual", help="Reason (with --add)")
    bl.add_argument("--days",   type=int,            default=None,     help="TTL in days (with --add); omit for permanent")
    bl.add_argument("--remove", metavar="TICKER",    help="Remove ticker")
    bl.add_argument("--migrate-log", action="store_true", help="Import liquidity_failed.log")

    meta = sub.add_parser("metadata", help="Inspect ticker_metadata table")
    meta.add_argument("ticker", nargs="?", help="Show metadata for one ticker")

    args = parser.parse_args()

    if args.cmd == "blacklist":
        if args.list:
            entries = get_active_blacklist()
            if not entries:
                print("  Blacklist is empty.")
            else:
                print(f"\n  {'TICKER':<10}  REASON")
                print("  " + "-" * 40)
                try:
                    with _connection()() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT ticker, reason, expires_at FROM blacklist "
                            "WHERE expires_at IS NULL OR expires_at > NOW() ORDER BY ticker"
                        )
                        for row in cur.fetchall():
                            exp = str(row["expires_at"])[:10] if row["expires_at"] else "permanent"
                            print(f"  {row['ticker']:<10}  {row['reason']}  (expires: {exp})")
                except Exception as exc:
                    print(f"  DB error: {exc}")
        elif args.add:
            exp = None
            if args.days:
                exp = datetime.now(timezone.utc) + timedelta(days=args.days)
            add_to_blacklist(args.add, reason=args.reason, expires_at=exp)
            print(f"  Added {args.add.upper()} (reason={args.reason}, expires={exp or 'permanent'})")
        elif args.remove:
            remove_from_blacklist(args.remove)
            print(f"  Removed {args.remove.upper()} from blacklist.")
        elif args.migrate_log:
            migrate_liquidity_failed_log()
        else:
            parser.print_help()

    elif args.cmd == "metadata":
        if args.ticker:
            m = get_ticker_metadata(args.ticker)
            if m:
                for k, v in m.items():
                    print(f"  {k:<16} {v}")
            else:
                print(f"  {args.ticker.upper()} not found in ticker_metadata.")
        else:
            parser.print_help()

    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
