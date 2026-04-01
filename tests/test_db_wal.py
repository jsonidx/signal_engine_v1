"""
tests/test_db_wal.py — Verify DB connection helpers in utils/db.py.

After the SQLite→PostgreSQL migration:
  • get_connection() / managed_connection() connect to Supabase (PostgreSQL).
  • get_local_connection() still provides a WAL-mode SQLite connection for
    local-only caches (fundamentals_cache).

Tests:
    1. get_connection() reaches Supabase and returns rows as dicts.
    2. managed_connection() commits on clean exit.
    3. managed_connection() rolls back on exception.
    4. get_local_connection() sets WAL mode on a temp SQLite file.
    5. Two concurrent local SQLite connections don't lock each other.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from utils.db import get_connection, managed_connection, get_local_connection


# ---------------------------------------------------------------------------
# 1. PostgreSQL connection (Supabase)
# ---------------------------------------------------------------------------

def test_pg_connection_returns_dict_rows():
    """get_connection() reaches Supabase; rows behave like dicts."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 42 AS answer")
    row = cur.fetchone()
    conn.close()
    assert row["answer"] == 42


# ---------------------------------------------------------------------------
# 2 & 3. managed_connection commit / rollback
# ---------------------------------------------------------------------------

def test_managed_connection_commits():
    """managed_connection() commits on clean exit."""
    with managed_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO portfolio_settings (key, value)
            VALUES ('_wal_test_commit', 'ok')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """)

    conn2 = get_connection()
    cur2 = conn2.cursor()
    cur2.execute("SELECT value FROM portfolio_settings WHERE key = '_wal_test_commit'")
    row = cur2.fetchone()
    cur2.execute("DELETE FROM portfolio_settings WHERE key = '_wal_test_commit'")
    conn2.commit()
    conn2.close()
    assert row is not None
    assert row["value"] == "ok"


def test_managed_connection_rollback_on_exception():
    """managed_connection() rolls back when an exception is raised."""
    try:
        with managed_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO portfolio_settings (key, value)
                VALUES ('_wal_test_rollback', 'should_not_persist')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """)
            raise RuntimeError("deliberate rollback trigger")
    except RuntimeError:
        pass

    conn2 = get_connection()
    cur2 = conn2.cursor()
    cur2.execute("SELECT value FROM portfolio_settings WHERE key = '_wal_test_rollback'")
    row = cur2.fetchone()
    conn2.close()
    assert row is None, "Row should not exist after rollback"


# ---------------------------------------------------------------------------
# 4 & 5. Local SQLite (fundamentals cache path)
# ---------------------------------------------------------------------------

def test_local_connection_wal_mode(tmp_path):
    """get_local_connection() enables WAL journal mode on a SQLite file."""
    db_path = str(tmp_path / "local_test.db")
    conn = get_local_connection(db_path)
    row = conn.execute("PRAGMA journal_mode").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "wal", f"Expected 'wal', got {row[0]!r}"


def test_two_local_readers_do_not_lock(tmp_path):
    """Two simultaneous local SQLite connections can both read without locking."""
    db_path = str(tmp_path / "concurrent_test.db")

    # Seed data
    conn_setup = get_local_connection(db_path)
    conn_setup.execute("CREATE TABLE t (v INTEGER)")
    conn_setup.execute("INSERT INTO t VALUES (99)")
    conn_setup.commit()
    conn_setup.close()

    conn1 = get_local_connection(db_path)
    conn2 = get_local_connection(db_path)
    try:
        row1 = conn1.execute("SELECT v FROM t").fetchone()
        row2 = conn2.execute("SELECT v FROM t").fetchone()
    finally:
        conn1.close()
        conn2.close()

    assert row1[0] == 99, f"conn1 got {row1}"
    assert row2[0] == 99, f"conn2 got {row2}"
