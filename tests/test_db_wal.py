"""
tests/test_db_wal.py — Verify WAL mode is applied by utils/db.get_connection().

Tests:
    1. get_connection() sets journal_mode to 'wal' on a fresh temp database.
    2. Two simultaneous connections can both read without locking each other.
"""

import sqlite3
import tempfile
import os
import sys

# Allow imports from project root when run directly or via pytest from root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.db import get_connection, managed_connection


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_temp_db():
    """Return a NamedTemporaryFile-backed path to an empty temp db."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_wal_mode_is_set():
    """get_connection() must enable WAL journal mode."""
    path = _make_temp_db()
    try:
        conn = get_connection(path)
        row = conn.execute("PRAGMA journal_mode").fetchone()
        conn.close()
        assert row is not None, "PRAGMA journal_mode returned nothing"
        assert row[0] == "wal", f"Expected 'wal', got {row[0]!r}"
    finally:
        os.unlink(path)


def test_synchronous_is_normal():
    """get_connection() must set synchronous=NORMAL (value 1)."""
    path = _make_temp_db()
    try:
        conn = get_connection(path)
        row = conn.execute("PRAGMA synchronous").fetchone()
        conn.close()
        # SQLite returns 1 for NORMAL
        assert row is not None
        assert row[0] == 1, f"Expected synchronous=1 (NORMAL), got {row[0]}"
    finally:
        os.unlink(path)


def test_two_concurrent_readers_do_not_lock():
    """
    Two simultaneous connections to the same WAL database must both be able
    to read without either raising OperationalError: database is locked.
    """
    path = _make_temp_db()
    try:
        # Seed the database with a table and a row
        with managed_connection(path) as setup:
            setup.execute("CREATE TABLE t (v INTEGER)")
            setup.execute("INSERT INTO t VALUES (42)")
            setup.commit()

        conn1 = get_connection(path)
        conn2 = get_connection(path)
        try:
            row1 = conn1.execute("SELECT v FROM t").fetchone()
            row2 = conn2.execute("SELECT v FROM t").fetchone()
        finally:
            conn1.close()
            conn2.close()

        assert row1 == (42,), f"conn1 got {row1}"
        assert row2 == (42,), f"conn2 got {row2}"
    finally:
        # WAL mode creates -wal and -shm sidecar files; clean those up too
        for suffix in ("", "-wal", "-shm"):
            p = path + suffix
            if os.path.exists(p):
                os.unlink(p)


def test_managed_connection_closes_on_exit():
    """managed_connection() must close the connection after the with-block."""
    path = _make_temp_db()
    try:
        captured = []
        with managed_connection(path) as conn:
            captured.append(conn)

        # After exiting the context manager the connection should be closed.
        # Attempting a query on a closed connection raises ProgrammingError.
        try:
            captured[0].execute("SELECT 1")
            closed = False
        except Exception:
            closed = True

        assert closed, "managed_connection did not close the connection on exit"
    finally:
        for suffix in ("", "-wal", "-shm"):
            p = path + suffix
            if os.path.exists(p):
                os.unlink(p)
