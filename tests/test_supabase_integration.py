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
    11. Resolution cache — conflict_resolver global cache read/write
    12. API usage — log_api_usage() inserts rows; cost computed correctly
    13. API keys — create_api_key() and _verify_api_key() round-trip
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
    "strategy_config", "thesis_outcomes", "resolution_cache",
    "api_usage", "user_api_keys",
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


# ---------------------------------------------------------------------------
# 11. Resolution cache — conflict_resolver global cache
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def cleanup_test_resolution():
    yield
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM resolution_cache WHERE ticker = %s", (TEST_TICKER,))
    conn.commit()
    conn.close()


def test_resolution_cache_write_and_read(cleanup_test_resolution):
    """_save_resolution_cache() writes; get_cached_resolution() reads it back."""
    from conflict_resolver import _save_resolution_cache, get_cached_resolution

    today = datetime.now().strftime("%Y-%m-%d")
    result = {
        "pre_resolved_direction":  "BULL",
        "pre_resolved_confidence": 0.72,
        "signal_agreement_score":  0.80,
        "override_flags":          ["context: test_flag"],
        "module_votes":            {"signal_engine_composite_z": "BULL"},
        "bull_weight":             0.45,
        "bear_weight":             0.10,
        "skip_claude":             False,
        "max_conviction_override": None,
        "position_size_override":  None,
    }
    _save_resolution_cache(TEST_TICKER, today, "RISK_ON", result)

    cached = get_cached_resolution(TEST_TICKER, today)
    assert cached is not None, "get_cached_resolution returned None after save"
    assert cached["pre_resolved_direction"] == "BULL"
    assert abs(cached["pre_resolved_confidence"] - 0.72) < 0.001
    assert "context: test_flag" in cached["override_flags"]
    assert cached["skip_claude"] is False


def test_resolution_cache_miss_returns_none():
    """get_cached_resolution() returns None for unknown ticker/date."""
    from conflict_resolver import get_cached_resolution
    result = get_cached_resolution("ZZZNEVEREXISTS", "1900-01-01")
    assert result is None


def test_module_weights_loaded_from_supabase():
    """_load_module_weights() returns weights from strategy_config (not fallback)."""
    import importlib
    import conflict_resolver as cr
    # Reset the module-level cache so the next call hits Supabase
    cr._weights_cache = None
    weights = cr._load_module_weights()
    assert isinstance(weights, dict)
    assert len(weights) >= 8, "Expected at least 8 module weight entries"
    total = sum(weights.values())
    assert abs(total - 1.0) < 0.05, f"module_weights sum {total:.3f} should be near 1.0"


# ---------------------------------------------------------------------------
# 12. API usage logging
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def cleanup_test_api_usage():
    yield
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM api_usage WHERE ticker = %s", (TEST_TICKER,))
    conn.commit()
    conn.close()


def test_api_usage_log_inserts_row(cleanup_test_api_usage):
    """log_api_usage() inserts a row into api_usage with correct cost."""
    from utils.usage import log_api_usage, compute_cost

    log_api_usage(
        module="thesis",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        ticker=TEST_TICKER,
        cache_hit=False,
        user_id=None,
    )

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM api_usage WHERE ticker = %s ORDER BY created_at DESC LIMIT 1",
        (TEST_TICKER,),
    )
    row = cur.fetchone()
    conn.close()

    assert row is not None, "api_usage row not found after log_api_usage()"
    assert row["module"] == "thesis"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["input_tokens"] == 1000
    assert row["output_tokens"] == 500
    assert row["cache_hit"] is False

    expected_cost = compute_cost("claude-sonnet-4-6", 1000, 500)
    assert abs(row["cost_usd"] - expected_cost) < 0.0001


def test_api_usage_cache_hit_zero_cost(cleanup_test_api_usage):
    """Cache hits (tokens=0) result in zero cost."""
    from utils.usage import log_api_usage

    log_api_usage(
        module="transcript",
        model="claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=0,
        ticker=TEST_TICKER,
        cache_hit=True,
        user_id=None,
    )

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT cost_usd, cache_hit FROM api_usage WHERE ticker = %s AND cache_hit = TRUE LIMIT 1",
        (TEST_TICKER,),
    )
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert row["cache_hit"] is True
    assert row["cost_usd"] == 0.0


def test_compute_cost_sonnet():
    """compute_cost() returns correct USD for known model + token counts."""
    from utils.usage import compute_cost
    # 1M input + 1M output for sonnet: $3 + $15 = $18
    cost = compute_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert abs(cost - 18.0) < 0.001


# ---------------------------------------------------------------------------
# 13. API key create and verify
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def cleanup_test_api_key():
    """Track key IDs created during tests and delete them after."""
    created_ids: list = []
    yield created_ids
    if created_ids:
        conn = get_connection()
        cur = conn.cursor()
        for kid in created_ids:
            cur.execute("DELETE FROM user_api_keys WHERE id = %s", (kid,))
        conn.commit()
        conn.close()


def test_api_key_create_and_verify(cleanup_test_api_key):
    """create_api_key() persists key; _verify_api_key() returns AuthUser."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard", "api"))
    from auth import create_api_key, _verify_api_key

    # Use a fake UUID-style user_id (not a real Supabase user)
    fake_user_id = "00000000-0000-0000-0000-000000000001"
    result = create_api_key(user_id=fake_user_id, email="test@example.com", name="test key")
    cleanup_test_api_key.append(result["id"])

    assert result["raw_key"].startswith("se_")
    assert result["key_prefix"] == result["raw_key"][:11]

    auth_user = _verify_api_key(result["raw_key"])
    assert auth_user is not None, "_verify_api_key returned None for valid key"
    assert auth_user.user_id == fake_user_id
    assert auth_user.auth_method == "api_key"


def test_api_key_revoke(cleanup_test_api_key):
    """revoke_api_key() marks key revoked; _verify_api_key returns None after."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard", "api"))
    from auth import create_api_key, revoke_api_key, _verify_api_key

    fake_user_id = "00000000-0000-0000-0000-000000000002"
    result = create_api_key(user_id=fake_user_id, email="test2@example.com", name="revoke test")
    cleanup_test_api_key.append(result["id"])

    revoked = revoke_api_key(result["id"], fake_user_id)
    assert revoked is True

    auth_user = _verify_api_key(result["raw_key"])
    assert auth_user is None, "_verify_api_key should return None after revocation"


def test_api_key_wrong_key_returns_none():
    """_verify_api_key() returns None for a key that doesn't exist."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard", "api"))
    from auth import _verify_api_key

    auth_user = _verify_api_key("se_" + "x" * 64)
    assert auth_user is None
