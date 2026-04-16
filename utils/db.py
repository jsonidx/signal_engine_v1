"""
utils/db.py — PostgreSQL (Supabase) connection factory.

All modules that need persistent storage use get_connection() or
managed_connection() — both return a psycopg2 connection to Supabase.

For local-only caches (fundamentals_cache) that deliberately stay off
Supabase, use get_local_connection(path) / managed_local_connection(path)
which return a WAL-mode SQLite connection.

Environment
-----------
    DATABASE_URL — PostgreSQL DSN, e.g.:
        postgresql://postgres:password@db.xxx.supabase.co:5432/postgres
        (set in .env at project root)

Usage
-----
    # One-shot (caller closes):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT ...")
    conn.commit()
    conn.close()

    # Context-manager (auto-commit/rollback/close):
    with managed_connection() as conn:
        cur = conn.cursor()
        cur.execute("INSERT ...")

    # Local SQLite cache (fundamentals_cache only):
    conn = get_local_connection("fundamentals_cache.db")
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_DATABASE_URL: str | None = None


def _get_dsn() -> str:
    global _DATABASE_URL
    if _DATABASE_URL is None:
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise EnvironmentError(
                "DATABASE_URL not set. Add it to your .env file:\n"
                "  DATABASE_URL=postgresql://postgres:password@db.xxx.supabase.co:5432/postgres"
            )
        _DATABASE_URL = dsn
    return _DATABASE_URL


def get_connection() -> psycopg2.extensions.connection:
    """
    Open and return a psycopg2 connection to Supabase PostgreSQL.

    Rows are returned as dicts (RealDictCursor) — same ergonomics as sqlite3.Row.
    Caller is responsible for closing.
    """
    return psycopg2.connect(
        _get_dsn(),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


@contextmanager
def managed_connection() -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Context-manager variant of get_connection().

    Commits on clean exit, rolls back on exception, always closes.

    Usage:
        with managed_connection() as conn:
            cur = conn.cursor()
            cur.execute(...)
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Local SQLite helpers — for caches that deliberately stay off Supabase
# ---------------------------------------------------------------------------

def get_local_connection(db_path: str) -> sqlite3.Connection:
    """
    Open a WAL-mode SQLite connection for a local cache file.
    Caller is responsible for closing.
    """
    dir_part = os.path.dirname(os.path.abspath(db_path))
    if dir_part:
        os.makedirs(dir_part, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")
    return conn


@contextmanager
def managed_local_connection(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """Context-manager variant of get_local_connection()."""
    conn = get_local_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Portfolio NAV helper — always reads from Supabase, falls back to config
# ---------------------------------------------------------------------------

def load_portfolio_nav(fallback: float = 0.0) -> float:
    """
    Return the current portfolio NAV from Supabase portfolio_settings (key='cash_eur').
    Falls back to `fallback` (typically config.PORTFOLIO_NAV) if DB is unreachable
    or the row doesn't exist.
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT value FROM portfolio_settings WHERE key = 'cash_eur'")
        row = cur.fetchone()
        conn.close()
        if row and float(row["value"]) > 0:
            return float(row["value"])
    except Exception:
        pass
    return fallback
