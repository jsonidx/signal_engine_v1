"""
utils/db.py — Shared SQLite connection factory with WAL mode.

All modules that open a SQLite database should use get_connection() (or
managed_connection()) instead of sqlite3.connect() directly.  This ensures:
  • WAL journal mode   → concurrent readers never block a writer
  • synchronous=NORMAL → safe on crash, 2-3× faster than FULL
  • cache_size=-64000  → 64 MB page cache to cut I/O on larger DBs
"""

import logging
import sqlite3
from contextlib import contextmanager
from typing import Generator

logger = logging.getLogger(__name__)


def get_connection(db_path: str) -> sqlite3.Connection:
    """
    Open a SQLite connection and configure it for concurrent use.

    PRAGMAs applied on every new connection:
        PRAGMA journal_mode = WAL      — writers don't block readers
        PRAGMA synchronous  = NORMAL   — durable on OS crash, fast
        PRAGMA cache_size   = -64000   — 64 MB in-process page cache

    A one-time WAL verification is performed: if journal_mode comes back as
    anything other than 'wal' a WARNING is logged (e.g. on read-only FS).

    Returns the open sqlite3.Connection — caller is responsible for closing.
    """
    conn = sqlite3.connect(db_path)

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")

    # Verify WAL actually took effect (may not on some network or read-only paths)
    row = conn.execute("PRAGMA journal_mode").fetchone()
    active_mode = row[0] if row else "unknown"
    if active_mode != "wal":
        logger.warning(
            "SQLite WAL mode could not be enabled on %s (got %r). "
            "Concurrent writes may contend.",
            db_path,
            active_mode,
        )

    return conn


@contextmanager
def managed_connection(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager variant of get_connection().

    Usage:
        with managed_connection("my.db") as conn:
            conn.execute(...)
            conn.commit()
    """
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()
