"""
tests/test_supabase_integration.py
====================================
Integration tests for the Supabase PostgreSQL migration.

Tests cover:
    1.  DB connection — get_connection() reaches Supabase
    2.  Schema — all required tables exist
    3.  Data migration integrity — row counts match what was migrated
    4.  Global AI cache — thesis_cache read/write (write + read back + cleanup)
    5.  Global AI cache — transcript_cache read/write + cleanup
    6.  Watchlist — user_watchlists populated
    7.  Strategy config — strategy_config populated
    8.  Trade CRUD — insert a trade, read it back, delete it
    9.  utils/db helpers — managed_connection commit/rollback
    10. Local SQLite cache — get_local_connection() still works for fundamentals
"""

import json
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from utils.db import get_connection, managed_connection, get_local_connection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIRED_TABLES = {
    "trades", "trade_returns", "snapshots", "equity_positions",
    "weekly_returns", "portfolio_settings", "thesis_cache",
    "transcript_cache", "iv_history", "user_watchlists",
    "strategy_config", "thesis_outcomes",
}

TEST_TICKER = "_TEST_INTEGRATION_"   # Dummy ticker — cleaned up after each test


def _get_table_names(conn) -> set:
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    """)
    return {row["table_name"] for row in cur.fetchall()}


def _row_count(conn, table: str) -> int:
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
    return cur.fetchone()["n"]


# ---------------------------------------------------------------------------
# 1. Connection
# ---------------------------------------------------------------------------

def test_connection_reaches_supabase():
    """get_connection() returns a live psycopg2 connection."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 AS ping")
    row = cur.fetchone()
    conn.close()
    assert row["ping"] == 1


# ---------------------------------------------------------------------------
# 2. Schema
# ---------------------------------------------------------------------------

def test_all_required_tables_exist():
    """All tables from the migration plan are present in Supabase."""
    conn = get_connection()
    existing = _get_table_names(conn)
    conn.close()
    missing = REQUIRED_TABLES - existing
    assert not missing, f"Missing tables: {missing}"


# ---------------------------------------------------------------------------
# 3. Data migration integrity
# ---------------------------------------------------------------------------

def test_trades_migrated():
    """36 trades were migrated from the old SQLite file."""
    conn = get_connection()
    count = _row_count(conn, "trades")
    conn.close()
    assert count >= 36, f"Expected ≥36 trades, got {count}"


def test_trade_returns_migrated():
    """12 trade_returns were migrated."""
    conn = get_connection()
    count = _row_count(conn, "trade_returns")
    conn.close()
    assert count >= 12, f"Expected ≥12 trade_returns, got {count}"


def test_equity_positions_migrated():
    """30 equity_positions were migrated from the old paper_trades.db."""
    conn = get_connection()
    count = _row_count(conn, "equity_positions")
    conn.close()
    assert count >= 30, f"Expected ≥30 equity_positions, got {count}"


def test_snapshots_migrated():
    """2 snapshots were migrated."""
    conn = get_connection()
    count = _row_count(conn, "snapshots")
    conn.close()
    assert count >= 2, f"Expected ≥2 snapshots, got {count}"


# ---------------------------------------------------------------------------
# 4. Global AI cache — thesis_cache
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def cleanup_test_thesis():
    yield
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM thesis_cache WHERE ticker = %s", (TEST_TICKER,))
    conn.commit()
    conn.close()


def test_thesis_cache_write_and_read(cleanup_test_thesis):
    """save_thesis() writes to Supabase; get_cached_thesis() reads it back."""
    from ai_quant import save_thesis, get_cached_thesis

    today = datetime.now().strftime("%Y-%m-%d")
    thesis = {
        "ticker": TEST_TICKER,
        "date": today,
        "direction": "BULLISH",
        "conviction": 75,
        "time_horizon": "4-6 weeks",
        "entry_low": 100.0,
        "entry_high": 105.0,
        "stop_loss": 95.0,
        "target_1": 115.0,
        "target_2": 125.0,
        "position_size_pct": 2.5,
        "thesis": "Integration test thesis.",
        "catalysts": ["catalyst_a"],
        "risks": ["risk_a"],
        "signals": {"momentum": 0.8},
        "expected_moves": [],
        "bull_probability": 0.6,
        "bear_probability": 0.2,
        "neutral_probability": 0.2,
        "signal_agreement_score": 0.75,
    }
    save_thesis(thesis)

    result = get_cached_thesis(TEST_TICKER, today)
    assert result is not None, "get_cached_thesis returned None after save"
    assert result["direction"] == "BULLISH"
    assert result["conviction"] == 75
    assert result["catalysts"] == ["catalyst_a"]
    assert result["signals"] == {"momentum": 0.8}


def test_thesis_cache_miss_returns_none():
    """get_cached_thesis() returns None for a ticker with no cache entry."""
    from ai_quant import get_cached_thesis
    result = get_cached_thesis("ZZZNEVEREXISTS", "1900-01-01")
    assert result is None


# ---------------------------------------------------------------------------
# 5. Global AI cache — transcript_cache
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def cleanup_test_transcript():
    yield
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM transcript_cache WHERE ticker = %s", (TEST_TICKER,))
    conn.commit()
    conn.close()


def test_transcript_cache_write_and_read(cleanup_test_transcript):
    """_save_cache() writes; _get_cached() reads within TTL."""
    from earnings_transcript import _save_cache, _get_cached

    analysis = {"tone": "positive", "guidance": "raised", "cached": False}
    filing_date = "2026-01-01"
    _save_cache(TEST_TICKER, filing_date, analysis, "transcript snippet here")

    result = _get_cached(TEST_TICKER)
    assert result is not None, "_get_cached returned None after _save_cache"
    assert result["tone"] == "positive"
    assert result["cached"] is True


# ---------------------------------------------------------------------------
# 6. Watchlist
# ---------------------------------------------------------------------------

def test_watchlist_populated():
    """user_watchlists has tickers from watchlist.txt migration."""
    conn = get_connection()
    count = _row_count(conn, "user_watchlists")
    conn.close()
    assert count >= 100, f"Expected ≥100 watchlist tickers, got {count}"


def test_watchlist_contains_known_tickers():
    """AAPL and NVDA (from TIER1) exist in user_watchlists."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT ticker FROM user_watchlists WHERE ticker IN ('AAPL', 'NVDA')")
    found = {row["ticker"] for row in cur.fetchall()}
    conn.close()
    assert "AAPL" in found
    assert "NVDA" in found


# ---------------------------------------------------------------------------
# 7. Strategy config
# ---------------------------------------------------------------------------

def test_strategy_config_populated():
    """strategy_config has at least 4 entries (module_weights + allocations)."""
    conn = get_connection()
    count = _row_count(conn, "strategy_config")
    conn.close()
    assert count >= 4, f"Expected ≥4 strategy_config rows, got {count}"


def test_module_weights_valid_json():
    """module_weights in strategy_config is valid JSON that sums to ~1.0."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT value FROM strategy_config WHERE key = 'module_weights'")
    row = cur.fetchone()
    conn.close()
    assert row is not None, "module_weights not found in strategy_config"
    weights = json.loads(row["value"])
    total = sum(weights.values())
    assert abs(total - 1.0) < 0.01, f"module_weights sum {total:.3f} ≠ 1.0"


# ---------------------------------------------------------------------------
# 8. Trade CRUD
# ---------------------------------------------------------------------------

@pytest.fixture
def test_trade_id():
    """Insert a test trade and clean it up after the test."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO trades (ticker, action, price, size_eur, shares, date, created_at, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (TEST_TICKER, "BUY", 42.0, 1000.0, 23.8, "2026-01-01",
          datetime.now().isoformat(), "open"))
    trade_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    yield trade_id
    # Cleanup
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM trades WHERE id = %s", (trade_id,))
    conn.commit()
    conn.close()


def test_trade_insert_and_read(test_trade_id):
    """INSERT a trade row, read it back, verify fields."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM trades WHERE id = %s", (test_trade_id,))
    row = cur.fetchone()
    conn.close()
    assert row is not None
    assert row["ticker"] == TEST_TICKER
    assert row["action"] == "BUY"
    assert abs(row["price"] - 42.0) < 0.01
    assert row["status"] == "open"


def test_trade_update_status(test_trade_id):
    """UPDATE trade status from 'open' to 'closed'."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE trades SET status = 'closed' WHERE id = %s", (test_trade_id,))
    conn.commit()
    cur.execute("SELECT status FROM trades WHERE id = %s", (test_trade_id,))
    row = cur.fetchone()
    conn.close()
    assert row["status"] == "closed"


# ---------------------------------------------------------------------------
# 9. managed_connection commit / rollback
# ---------------------------------------------------------------------------

def test_managed_connection_commits():
    """managed_connection() commits on clean exit."""
    with managed_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO portfolio_settings (key, value)
            VALUES ('_test_commit_key', 'test_value')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """)

    # Verify outside the context
    conn2 = get_connection()
    cur2 = conn2.cursor()
    cur2.execute("SELECT value FROM portfolio_settings WHERE key = '_test_commit_key'")
    row = cur2.fetchone()
    # Cleanup
    cur2.execute("DELETE FROM portfolio_settings WHERE key = '_test_commit_key'")
    conn2.commit()
    conn2.close()
    assert row is not None
    assert row["value"] == "test_value"


def test_managed_connection_rollback_on_exception():
    """managed_connection() rolls back when an exception is raised."""
    unique_key = "_test_rollback_key"
    try:
        with managed_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO portfolio_settings (key, value)
                VALUES (%s, 'should_not_persist')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (unique_key,))
            raise ValueError("Intentional error — should trigger rollback")
    except ValueError:
        pass

    conn2 = get_connection()
    cur2 = conn2.cursor()
    cur2.execute("SELECT value FROM portfolio_settings WHERE key = %s", (unique_key,))
    row = cur2.fetchone()
    conn2.close()
    assert row is None, "Row should not exist after rollback"


# ---------------------------------------------------------------------------
# 10. Local SQLite cache still works
# ---------------------------------------------------------------------------

def test_get_local_connection_works(tmp_path):
    """get_local_connection() creates a working SQLite DB for local caches."""
    db_path = str(tmp_path / "test_local.db")
    conn = get_local_connection(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('hello')")
    conn.commit()
    row = conn.execute("SELECT v FROM t").fetchone()
    conn.close()
    assert row[0] == "hello"
