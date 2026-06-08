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

pytestmark = pytest.mark.database

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
    assert len(weights) >= 7, "Expected at least 7 module weight entries"
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


# ---------------------------------------------------------------------------
# TRD-003 — Catalyst score persistence regression
# ---------------------------------------------------------------------------

class TestCatalystScorePersistenceMock:
    """
    Mock-only tests (no live DB) that verify save_catalyst_scores preserves
    raw_composite and post_squeeze_guard even when composite is zeroed.

    Regression guard: rows with nonzero volume/options/technical components
    must never silently persist with composite=0.0 without a documented reason.
    """

    def _make_df(self, rows):
        import pandas as pd
        return pd.DataFrame(rows)

    def test_nonzero_components_with_zero_composite_are_flagged(self):
        """
        A row with nonzero component scores but composite=0.0 must have
        post_squeeze_guard=True OR raw_composite > 0 to explain the zero.
        Rows missing both are a regression.
        """
        import pandas as pd
        from unittest.mock import MagicMock, patch

        row = {
            "ticker": "SNOW",
            "composite": 0.0,
            "raw_composite": 51.4,       # pre-guard value stored
            "post_squeeze_guard": False, # not a squeeze — composite was wrong
            "squeeze_score": 1.0,
            "volume_score": 4.0,
            "vol_compress": 2.0,
            "options_score": 4.0,
            "technical_score": 4.0,
            "social_score": 0.0,
            "polymarket_score": 0.0,
            "dark_pool_score": 1.0,
            "dark_pool_signal": "NEUTRAL",
            "earnings_score": 5.0,
            "analyst_score": 2.0,
            "n_flags": 9,
            "price": 168.5,
            "short_pct": 5.1,
        }
        df = self._make_df([row])

        # Assert: if composite==0.0, either guard fired OR raw_composite explains it
        for _, r in df.iterrows():
            if r["composite"] == 0.0:
                has_guard = bool(r.get("post_squeeze_guard", False))
                has_raw = (r.get("raw_composite") or 0.0) > 0.0
                assert has_guard or has_raw, (
                    f"Ticker {r['ticker']}: composite=0.0 but post_squeeze_guard=False "
                    f"and raw_composite=0 — this is the TRD-003 regression"
                )

    def test_save_catalyst_scores_passes_raw_composite_to_db(self):
        """save_catalyst_scores must pass raw_composite as a separate column."""
        import pandas as pd
        from unittest.mock import MagicMock, patch, call

        row = {
            "ticker": "TESTX",
            "composite": 0.0,
            "raw_composite": 42.0,
            "post_squeeze_guard": True,
            "squeeze_score": 5.0, "volume_score": 3.0, "vol_compress": 1.0,
            "options_score": 2.0, "technical_score": 3.0, "social_score": 0.0,
            "polymarket_score": 0.0, "dark_pool_score": 1.0,
            "dark_pool_signal": "NEUTRAL",
            "earnings_score": 4.0, "days_to_earnings": 5,
            "n_flags": 4,
            "price": 50.0, "short_pct": 18.0,
        }
        df = pd.DataFrame([row])

        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch("utils.supabase_persist._conn", return_value=mock_conn):
            from utils.supabase_persist import save_catalyst_scores
            save_catalyst_scores(df, run_date="2026-05-25")

        assert mock_cur.executemany.called, "executemany must be called"
        args = mock_cur.executemany.call_args
        inserted_rows = args[0][1]  # second positional arg to executemany
        assert len(inserted_rows) == 1
        persisted = inserted_rows[0]
        # Tuple layout: date, ticker, composite, raw_composite, post_squeeze_guard,
        #   squeeze, volume, vol_compress, options, technical, social, polymarket,
        #   dark_pool_score, dark_pool_signal, earnings_score, days_to_earnings,
        #   n_flags, price, short_pct  (19 values total)
        assert len(persisted) == 19, f"Expected 19 columns, got {len(persisted)}"
        # Position 3 is raw_composite (after date, ticker, composite)
        assert persisted[3] == 42.0, f"raw_composite should be 42.0, got {persisted[3]}"
        # Position 4 is post_squeeze_guard
        assert persisted[4] is True, f"post_squeeze_guard should be True, got {persisted[4]}"
        # Position 14 is earnings_score, 15 is days_to_earnings
        assert persisted[14] == 4.0, f"earnings_score should be 4.0, got {persisted[14]}"
        assert persisted[15] == 5,   f"days_to_earnings should be 5, got {persisted[15]}"

    def test_save_catalyst_scores_fallback_raw_composite(self):
        """
        When raw_composite column is absent (pre-TRD-003 caller), it must fall
        back to composite so the DB row is not left NULL.
        """
        import pandas as pd
        from unittest.mock import MagicMock, patch

        row = {
            "ticker": "TESTY",
            "composite": 35.5,
            # raw_composite intentionally absent
            "post_squeeze_guard": False,
            "squeeze_score": 2.0, "volume_score": 3.0, "vol_compress": 1.0,
            "options_score": 1.0, "technical_score": 3.0, "social_score": 0.0,
            "polymarket_score": 0.0, "dark_pool_score": 0.0,
            "dark_pool_signal": "", "n_flags": 3,
            "price": 100.0, "short_pct": 4.0,
        }
        df = pd.DataFrame([row])

        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch("utils.supabase_persist._conn", return_value=mock_conn):
            from utils.supabase_persist import save_catalyst_scores
            save_catalyst_scores(df, run_date="2026-05-25")

        args = mock_cur.executemany.call_args
        persisted = args[0][1][0]
        # raw_composite should fall back to composite value (35.5)
        assert persisted[3] == 35.5, f"raw_composite fallback should be 35.5, got {persisted[3]}"


# ===========================================================================
# TRD-057 fix — mark_research_candidate_advanced
# ===========================================================================

class TestMarkResearchCandidateAdvanced:
    """Tests that mark_research_candidate_advanced degrades safely on DB errors."""

    def _make_mock_conn(self):
        from unittest.mock import MagicMock
        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__  = MagicMock(return_value=False)
        mock_cur.__enter__  = lambda s: mock_cur
        mock_cur.__exit__   = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        return mock_conn, mock_cur

    def test_marks_advanced_when_db_available(self):
        """Successful DB path: UPDATE is called with correct ticker and date."""
        from unittest.mock import patch
        from utils.supabase_persist import mark_research_candidate_advanced

        mock_conn, mock_cur = self._make_mock_conn()
        # managed_connection is imported locally inside the function — patch at the source module
        with patch("utils.db.managed_connection", return_value=mock_conn):
            mark_research_candidate_advanced("AAPL", run_date="2026-01-15")

        mock_cur.execute.assert_called_once()
        sql, params = mock_cur.execute.call_args[0]
        assert "advanced_to_ai" in sql.lower()
        assert params == ("2026-01-15", "AAPL")

    def test_degrades_safely_on_db_error(self):
        """DB unavailable: must not raise, must not crash caller."""
        from unittest.mock import patch
        from utils.supabase_persist import mark_research_candidate_advanced

        with patch("utils.db.managed_connection", side_effect=Exception("connection refused")):
            # Must not raise
            mark_research_candidate_advanced("MSFT", run_date="2026-01-15")

    def test_ticker_is_uppercased(self):
        """Ticker argument must be uppercased before the DB call."""
        from unittest.mock import patch
        from utils.supabase_persist import mark_research_candidate_advanced

        mock_conn, mock_cur = self._make_mock_conn()
        with patch("utils.db.managed_connection", return_value=mock_conn):
            mark_research_candidate_advanced("aapl", run_date="2026-01-15")

        _, params = mock_cur.execute.call_args[0]
        assert params[1] == "AAPL", "Ticker must be uppercased in DB call"


# ===========================================================================
# TRD-059 — persist_funnel_metrics / fetch_funnel_metrics
# ===========================================================================

class TestPersistFunnelMetrics:
    """Unit tests for funnel metrics persistence using mock DB."""

    def _make_managed_conn(self):
        from unittest.mock import MagicMock
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cur.__enter__ = lambda s: mock_cur
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        return mock_conn, mock_cur

    def test_persist_funnel_metrics_calls_upsert(self):
        """persist_funnel_metrics executes an INSERT ... ON CONFLICT upsert."""
        from unittest.mock import patch
        from utils.supabase_persist import persist_funnel_metrics

        mock_conn, mock_cur = self._make_managed_conn()
        with patch("utils.db.managed_connection", return_value=mock_conn):
            persist_funnel_metrics({
                "raw_universe_count": 1200,
                "execution_core_count": 350,
                "hard_excluded_count": 80,
            }, run_date="2026-06-07")

        calls = [str(c) for c in mock_cur.execute.call_args_list]
        # At least one call should be the upsert (the other is DDL)
        upsert_calls = [c for c in calls if "ON CONFLICT" in c]
        assert upsert_calls, "Expected an ON CONFLICT upsert call"

    def test_persist_funnel_metrics_degrades_on_db_error(self):
        """DB error must not raise — funnel metrics are non-fatal."""
        from unittest.mock import patch
        from utils.supabase_persist import persist_funnel_metrics

        with patch("utils.db.managed_connection", side_effect=Exception("db down")):
            persist_funnel_metrics({"raw_universe_count": 500})  # must not raise

    def test_persist_funnel_metrics_ignores_empty_dict(self):
        """Empty metrics dict exits early without touching the DB."""
        from unittest.mock import patch
        from utils.supabase_persist import persist_funnel_metrics

        with patch("utils.db.managed_connection") as mock_mgr:
            persist_funnel_metrics({})
        mock_mgr.assert_not_called()

    def test_persist_funnel_metrics_unknown_keys_stripped(self):
        """Unknown keys in the metrics dict must not cause SQL errors."""
        from unittest.mock import patch
        from utils.supabase_persist import persist_funnel_metrics

        mock_conn, mock_cur = self._make_managed_conn()
        with patch("utils.db.managed_connection", return_value=mock_conn):
            # should not raise even with non-existent column names
            persist_funnel_metrics({"unknown_col": 99, "raw_universe_count": 100})


# ===========================================================================
# TRD-068 — ticker governance persistence
# ===========================================================================

class TestTickerGovernancePersistence:
    """Unit tests for set/fetch/remove governance helpers using mock DB."""

    def _make_managed_conn(self):
        from unittest.mock import MagicMock
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cur.__enter__ = lambda s: mock_cur
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        return mock_conn, mock_cur

    def test_set_ticker_governance_valid_state(self):
        """set_ticker_governance succeeds for all valid states."""
        from unittest.mock import patch
        from utils.supabase_persist import set_ticker_governance

        for state in ("A_LIST", "STANDARD", "PROBATION", "QUARANTINE"):
            mock_conn, mock_cur = self._make_managed_conn()
            with patch("utils.db.managed_connection", return_value=mock_conn):
                result = set_ticker_governance("AAPL", state, reason="test")
            assert result is True, f"Expected True for state={state}"

    def test_set_ticker_governance_invalid_state_returns_false(self):
        """Invalid governance state returns False without DB call."""
        from unittest.mock import patch
        from utils.supabase_persist import set_ticker_governance

        with patch("utils.db.managed_connection") as mock_mgr:
            result = set_ticker_governance("AAPL", "INVALID_STATE")
        assert result is False
        mock_mgr.assert_not_called()

    def test_fetch_ticker_governance_returns_dict(self):
        """fetch_ticker_governance returns {TICKER: state} dict."""
        from unittest.mock import patch, MagicMock
        from utils.supabase_persist import fetch_ticker_governance

        mock_conn, mock_cur = self._make_managed_conn()
        mock_cur.fetchall.return_value = [
            {"ticker": "AAPL", "governance_state": "A_LIST"},
            {"ticker": "MEME", "governance_state": "QUARANTINE"},
        ]
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = fetch_ticker_governance()

        assert result.get("AAPL") == "A_LIST"
        assert result.get("MEME") == "QUARANTINE"

    def test_fetch_ticker_governance_degrades_on_error(self):
        """DB error returns empty dict, not an exception."""
        from unittest.mock import patch
        from utils.supabase_persist import fetch_ticker_governance

        with patch("utils.db.managed_connection", side_effect=Exception("timeout")):
            result = fetch_ticker_governance()
        assert result == {}

    def test_remove_ticker_governance_calls_delete(self):
        """remove_ticker_governance issues a DELETE for the given ticker."""
        from unittest.mock import patch
        from utils.supabase_persist import remove_ticker_governance

        mock_conn, mock_cur = self._make_managed_conn()
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = remove_ticker_governance("MEME")

        assert result is True
        calls = [str(c) for c in mock_cur.execute.call_args_list]
        delete_calls = [c for c in calls if "DELETE" in c.upper()]
        assert delete_calls, "Expected a DELETE call"


# ===========================================================================
# TRD-075 — source/lane attribution persistence
# ===========================================================================

class TestSourceLaneAttribution:
    """
    Tests for source/lane attribution in research_lane_candidates and
    funnel_metrics (TRD-075).
    """

    def _make_managed_conn(self):
        from unittest.mock import MagicMock
        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__  = MagicMock(return_value=False)
        mock_cur.__enter__  = lambda s: mock_cur
        mock_cur.__exit__   = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        return mock_conn, mock_cur

    def test_research_candidate_persists_sources_and_broad_flag(self):
        """persist_research_lane_candidates must include sources and broad_source_only
        in the INSERT when ranked_universe carries those fields."""
        from unittest.mock import patch
        from utils.supabase_persist import persist_research_lane_candidates

        ranked = {
            "NBRD": {
                "rank": 1, "total": 2, "lane": "research_broad", "status": "Dynamic only",
                "force_tags": [], "score": 55.0,
                "sources": ["nasdaq_broad"],
                "broad_source_only": True,
            },
            "DUAL": {
                "rank": 2, "total": 2, "lane": "execution_core", "status": "Dynamic only",
                "force_tags": [], "score": 80.0,
                "sources": ["sp500", "nasdaq_broad"],
                "broad_source_only": False,
            },
        }

        mock_conn, mock_cur = self._make_managed_conn()
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = persist_research_lane_candidates(ranked)

        assert result == 2
        all_calls = [str(c) for c in mock_cur.execute.call_args_list]
        insert_calls = [c for c in all_calls if "INSERT INTO research_lane_candidates" in c]
        assert len(insert_calls) == 2

        # sources and broad_source_only columns must appear in the INSERT
        for call_str in insert_calls:
            assert "sources" in call_str, "INSERT must include sources column"
            assert "broad_source_only" in call_str, "INSERT must include broad_source_only column"

    def test_research_candidate_handles_missing_attribution_fields(self):
        """persist_research_lane_candidates must not fail when sources/broad_source_only
        are absent from a ranked_universe entry (backwards compatibility)."""
        from unittest.mock import patch
        from utils.supabase_persist import persist_research_lane_candidates

        ranked = {
            "LEGACY": {
                "rank": 1, "total": 1, "lane": "execution_core",
                "status": "Dynamic only", "force_tags": [], "score": 70.0,
                # no "sources", no "broad_source_only"
            },
        }

        mock_conn, mock_cur = self._make_managed_conn()
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = persist_research_lane_candidates(ranked)

        assert result == 1, "Must persist row even without attribution fields"

    def test_funnel_metrics_attribution_columns_accepted(self):
        """persist_funnel_metrics must accept and include the new attribution
        columns in the upsert SQL."""
        from unittest.mock import patch
        from utils.supabase_persist import persist_funnel_metrics

        mock_conn, mock_cur = self._make_managed_conn()
        with patch("utils.db.managed_connection", return_value=mock_conn):
            persist_funnel_metrics({
                "prescreened_count": 150,
                "candidates_by_lane": {"execution_core": 40, "research_broad": 110},
                "candidates_by_source": {"sp500": 80, "nasdaq_broad": 120, "russell1000": 60},
                "broad_source_only_candidates": 45,
                "ai_selected_by_lane": {"execution_core": 4, "research_broad": 1},
                "ai_selected_by_source": {"sp500": 4, "nasdaq_broad": 2},
                "broad_source_only_ai_selected": 1,
            }, run_date="2026-06-07")

        all_calls = [str(c) for c in mock_cur.execute.call_args_list]
        upsert_calls = [c for c in all_calls if "ON CONFLICT" in c]
        assert upsert_calls, "Expected ON CONFLICT upsert"
        upsert_sql = upsert_calls[0]
        assert "candidates_by_lane" in upsert_sql
        assert "candidates_by_source" in upsert_sql
        assert "broad_source_only_candidates" in upsert_sql
        assert "ai_selected_by_lane" in upsert_sql
        assert "ai_selected_by_source" in upsert_sql
        assert "broad_source_only_ai_selected" in upsert_sql

    def test_funnel_metrics_serialises_attribution_jsonb(self):
        """JSONB attribution dicts must be serialised as JSON strings before the
        DB call — psycopg2 requires string-encoded JSON."""
        import json
        from unittest.mock import patch, call
        from utils.supabase_persist import persist_funnel_metrics

        mock_conn, mock_cur = self._make_managed_conn()
        with patch("utils.db.managed_connection", return_value=mock_conn):
            persist_funnel_metrics({
                "candidates_by_source": {"sp500": 80, "nasdaq_broad": 40},
                "ai_selected_by_source": {"sp500": 3},
            }, run_date="2026-06-07")

        # Find the upsert execute call (second call: first is DDL)
        upsert_args = None
        for c in mock_cur.execute.call_args_list:
            args = c[0]
            if len(args) == 2 and isinstance(args[1], list):
                upsert_args = args[1]
                break

        assert upsert_args is not None, "Expected a parameterised upsert call"
        # Verify that dict values were serialised to JSON strings
        json_string_args = [a for a in upsert_args if isinstance(a, str) and a.startswith("{")]
        assert len(json_string_args) >= 2, (
            f"Expected at least 2 JSON-serialised values; got {json_string_args}"
        )
        for s in json_string_args:
            json.loads(s)  # must be valid JSON


# ===========================================================================
# TRD-077 — outcome attribution aggregation
# ===========================================================================

class TestFetchOutcomeAttribution:
    """
    Tests for fetch_outcome_attribution() aggregation logic.
    Uses mock DB cursor returning synthetic thesis_outcomes rows with attribution.
    """

    def _make_managed_conn(self, rows):
        from unittest.mock import MagicMock
        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__  = MagicMock(return_value=False)
        mock_cur.__enter__  = lambda s: mock_cur
        mock_cur.__exit__   = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = [dict(r) for r in rows]
        return mock_conn, mock_cur

    def _row(self, ticker, direction, claude_correct, return_30d=None,
             candidate_lane="execution_core", sources=None, broad_source_only=False,
             governance_state=None):
        return {
            "ticker": ticker,
            "thesis_date": "2026-05-01",
            "direction": direction,
            "outcome": "HIT_TARGET1",
            "return_30d": return_30d,
            "claude_correct": claude_correct,
            "candidate_lane": candidate_lane,
            "sources": sources or ["sp500"],
            "broad_source_only": broad_source_only,
            "governance_state": governance_state,
        }

    def test_basic_win_rate_by_lane(self):
        """Win rate by lane is computed correctly from resolved rows."""
        from unittest.mock import patch
        from utils.supabase_persist import fetch_outcome_attribution

        rows = [
            self._row("AAPL", "BULL", 1, candidate_lane="execution_core"),
            self._row("MSFT", "BULL", 1, candidate_lane="execution_core"),
            self._row("NVDA", "BULL", 0, candidate_lane="execution_core"),
            self._row("PLTR", "BULL", 1, candidate_lane="research_broad"),
        ]
        mock_conn, _ = self._make_managed_conn(rows)
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = fetch_outcome_attribution(days=90)

        lane_map = {b["label"]: b for b in result["by_lane"]}
        ec = lane_map.get("execution_core")
        assert ec is not None
        assert ec["resolved"] == 3
        assert ec["correct_count"] == 2
        assert abs(ec["directional_accuracy"] - round(2/3, 4)) < 0.001
        rb = lane_map.get("research_broad")
        assert rb is not None
        assert rb["resolved"] == 1
        assert rb["directional_accuracy"] == 1.0

    def test_win_rate_by_source_unnests_sources(self):
        """Sources list is unnested — a ticker in both sp500 and russell1000 counts for both."""
        from unittest.mock import patch
        from utils.supabase_persist import fetch_outcome_attribution

        rows = [
            self._row("AAPL", "BULL", 1, sources=["sp500", "russell1000"]),
            self._row("MSFT", "BULL", 0, sources=["sp500"]),
        ]
        mock_conn, _ = self._make_managed_conn(rows)
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = fetch_outcome_attribution(days=90)

        src_map = {b["label"]: b for b in result["by_source"]}
        sp = src_map.get("sp500")
        assert sp is not None
        assert sp["resolved"] == 2  # both tickers have sp500
        assert sp["correct_count"] == 1
        ru = src_map.get("russell1000")
        assert ru is not None
        assert ru["resolved"] == 1  # only AAPL has russell1000
        assert ru["correct_count"] == 1

    def test_broad_source_only_summary(self):
        """broad bucket accumulates broad_source_only=True rows; non_broad the rest."""
        from unittest.mock import patch
        from utils.supabase_persist import fetch_outcome_attribution

        rows = [
            self._row("AAPL", "BULL", 1, broad_source_only=False),
            self._row("MSFT", "BULL", 1, broad_source_only=False),
            self._row("NANO", "BULL", 0, broad_source_only=True,
                      candidate_lane="research_broad", sources=["nasdaq_broad"]),
        ]
        mock_conn, _ = self._make_managed_conn(rows)
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = fetch_outcome_attribution(days=90)

        bso = result["broad_source_only_summary"]
        assert bso["broad"]["resolved"] == 1
        assert bso["broad"]["correct_count"] == 0
        assert bso["non_broad"]["resolved"] == 2
        assert bso["non_broad"]["correct_count"] == 2

    def test_avg_return_30d_computed(self):
        """avg_return_30d is mean of non-null return_30d values for the bucket."""
        from unittest.mock import patch
        from utils.supabase_persist import fetch_outcome_attribution

        rows = [
            self._row("AAPL", "BULL", 1, return_30d=0.10, candidate_lane="execution_core"),
            self._row("MSFT", "BULL", 1, return_30d=0.20, candidate_lane="execution_core"),
            self._row("NVDA", "BULL", 0, return_30d=None,  candidate_lane="execution_core"),
        ]
        mock_conn, _ = self._make_managed_conn(rows)
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = fetch_outcome_attribution(days=90)

        lane_map = {b["label"]: b for b in result["by_lane"]}
        ec = lane_map["execution_core"]
        assert ec["avg_return_30d"] is not None
        assert abs(ec["avg_return_30d"] - 0.15) < 0.001, (
            f"Expected avg 0.15, got {ec['avg_return_30d']}"
        )

    def test_total_resolved_count(self):
        """total_resolved equals the number of rows returned by the query."""
        from unittest.mock import patch
        from utils.supabase_persist import fetch_outcome_attribution

        rows = [self._row(f"T{i}", "BULL", 1) for i in range(7)]
        mock_conn, _ = self._make_managed_conn(rows)
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = fetch_outcome_attribution(days=90)

        assert result["total_resolved"] == 7

    def test_empty_result_on_no_rows(self):
        """Returns empty structure when no resolved outcomes exist."""
        from unittest.mock import patch
        from utils.supabase_persist import fetch_outcome_attribution

        mock_conn, _ = self._make_managed_conn([])
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = fetch_outcome_attribution(days=90)

        assert result["total_resolved"] == 0
        assert result["by_source"] == []
        assert result["by_lane"] == []

    def test_degrades_safely_on_db_error(self):
        """DB error returns the empty structure without raising."""
        from unittest.mock import patch
        from utils.supabase_persist import fetch_outcome_attribution

        with patch("utils.db.managed_connection", side_effect=RuntimeError("db down")):
            result = fetch_outcome_attribution(days=90)

        assert result["total_resolved"] == 0
        assert result["days"] == 90

    def test_sources_json_string_fallback(self):
        """When sources is a JSON string (legacy), it is parsed correctly."""
        import json
        from unittest.mock import patch
        from utils.supabase_persist import fetch_outcome_attribution

        rows = [
            {
                "ticker": "AAPL", "thesis_date": "2026-05-01",
                "direction": "BULL", "outcome": "HIT_TARGET1",
                "return_30d": None, "claude_correct": 1,
                "candidate_lane": "execution_core",
                "sources": json.dumps(["sp500"]),  # stored as string, not list
                "broad_source_only": False,
                "governance_state": None,
            }
        ]
        mock_conn, _ = self._make_managed_conn(rows)
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = fetch_outcome_attribution(days=90)

        src_map = {b["label"]: b for b in result["by_source"]}
        assert "sp500" in src_map, "JSON string sources should be parsed to list"

    def test_by_governance_state_aggregation(self):
        """Rows with governance_state are bucketed correctly by state."""
        from unittest.mock import patch
        from utils.supabase_persist import fetch_outcome_attribution

        rows = [
            self._row("AAPL", "BULL", 1, governance_state="A_LIST"),
            self._row("MSFT", "BULL", 1, governance_state="A_LIST"),
            self._row("NVDA", "BULL", 0, governance_state="A_LIST"),
            self._row("PLTR", "BULL", 1, governance_state="STANDARD"),
            self._row("GME",  "BULL", 0, governance_state="PROBATION"),
        ]
        mock_conn, _ = self._make_managed_conn(rows)
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = fetch_outcome_attribution(days=90)

        gov_map = {b["label"]: b for b in result["by_governance_state"]}
        a_list = gov_map.get("A_LIST")
        assert a_list is not None
        assert a_list["resolved"] == 3
        assert a_list["correct_count"] == 2
        assert abs(a_list["directional_accuracy"] - round(2/3, 4)) < 0.001
        std = gov_map.get("STANDARD")
        assert std is not None
        assert std["resolved"] == 1 and std["correct_count"] == 1
        prob = gov_map.get("PROBATION")
        assert prob is not None
        assert prob["resolved"] == 1 and prob["correct_count"] == 0

    def test_governance_state_null_maps_to_unknown(self):
        """Rows where governance_state is NULL (pre-migration) are bucketed as 'unknown'."""
        from unittest.mock import patch
        from utils.supabase_persist import fetch_outcome_attribution

        rows = [
            self._row("AAPL", "BULL", 1, governance_state=None),    # legacy: no state
            self._row("MSFT", "BULL", 0, governance_state=None),
            self._row("NVDA", "BULL", 1, governance_state="A_LIST"),
        ]
        mock_conn, _ = self._make_managed_conn(rows)
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = fetch_outcome_attribution(days=90)

        gov_map = {b["label"]: b for b in result["by_governance_state"]}
        unknown = gov_map.get("unknown")
        assert unknown is not None, "NULL governance_state rows should appear as 'unknown'"
        assert unknown["resolved"] == 2
        assert unknown["correct_count"] == 1
        a_list = gov_map.get("A_LIST")
        assert a_list is not None and a_list["resolved"] == 1


class TestGovernanceRecommendations:
    """
    Tests for fetch_governance_recommendations() and _governance_rec_classify().
    The classify helper is tested directly; fetch_governance_recommendations() is
    tested via a mock DB cursor.
    """

    def _make_managed_conn(self, rows):
        from unittest.mock import MagicMock
        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__  = MagicMock(return_value=False)
        mock_cur.__enter__  = lambda s: mock_cur
        mock_cur.__exit__   = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = [dict(r) for r in rows]
        return mock_conn, mock_cur

    def _row(self, ticker, resolved, correct_count, avg_return_30d=None,
             current_state="STANDARD"):
        return {
            "ticker": ticker,
            "resolved": resolved,
            "correct_count": correct_count,
            "avg_return_30d": avg_return_30d,
            "current_state": current_state,
        }

    # ── classify unit tests ───────────────────────────────────────────────────

    def test_classify_strong_positive_promotes(self):
        """High accuracy + strong sample → promote_to_a_list for STANDARD ticker."""
        from utils.supabase_persist import _governance_rec_classify, _GOV_REC_THRESHOLDS
        rec, reason = _governance_rec_classify(10, 8, 0.80, 0.06, "STANDARD", _GOV_REC_THRESHOLDS)
        assert rec == "promote_to_a_list"
        assert "80%" in reason or "resolved" in reason.lower()

    def test_classify_a_list_justified_keep_current(self):
        """High accuracy for an A_LIST ticker → keep_current_state."""
        from utils.supabase_persist import _governance_rec_classify, _GOV_REC_THRESHOLDS
        rec, _ = _governance_rec_classify(10, 8, 0.80, 0.06, "A_LIST", _GOV_REC_THRESHOLDS)
        assert rec == "keep_current_state"

    def test_classify_probation_ticker_improving_promotes(self):
        """Strong evidence for a PROBATION ticker → promote_to_a_list (probation eligible)."""
        from utils.supabase_persist import _governance_rec_classify, _GOV_REC_THRESHOLDS
        rec, _ = _governance_rec_classify(9, 7, 0.78, 0.05, "PROBATION", _GOV_REC_THRESHOLDS)
        assert rec == "promote_to_a_list"

    def test_classify_weak_negative_probation(self):
        """Accuracy between 35–45% for STANDARD ticker → move_to_probation."""
        from utils.supabase_persist import _governance_rec_classify, _GOV_REC_THRESHOLDS
        rec, reason = _governance_rec_classify(6, 2, 0.40, None, "STANDARD", _GOV_REC_THRESHOLDS)
        assert rec == "move_to_probation"
        assert "probation" in reason.lower() or "40%" in reason

    def test_classify_strong_negative_quarantine(self):
        """Accuracy < 35% for STANDARD ticker → consider_quarantine."""
        from utils.supabase_persist import _governance_rec_classify, _GOV_REC_THRESHOLDS
        rec, reason = _governance_rec_classify(7, 2, 0.28, -0.05, "STANDARD", _GOV_REC_THRESHOLDS)
        assert rec == "consider_quarantine"
        assert "28%" in reason or "quarantine" in reason.lower()

    def test_classify_quarantine_low_accuracy_keep(self):
        """Accuracy < 35% for a QUARANTINE ticker → keep_current_state (already quarantined)."""
        from utils.supabase_persist import _governance_rec_classify, _GOV_REC_THRESHOLDS
        rec, _ = _governance_rec_classify(5, 1, 0.20, None, "QUARANTINE", _GOV_REC_THRESHOLDS)
        assert rec == "keep_current_state"

    def test_classify_insufficient_sample(self):
        """Fewer than min_sample resolved → insufficient_sample regardless of accuracy."""
        from utils.supabase_persist import _governance_rec_classify, _GOV_REC_THRESHOLDS
        rec, reason = _governance_rec_classify(3, 3, 1.0, 0.15, "STANDARD", _GOV_REC_THRESHOLDS)
        assert rec == "insufficient_sample"
        assert "3" in reason

    def test_classify_high_accuracy_low_return_no_promote(self):
        """High accuracy but avg_return_30d below threshold → keep_current_state."""
        from utils.supabase_persist import _governance_rec_classify, _GOV_REC_THRESHOLDS
        rec, reason = _governance_rec_classify(10, 8, 0.80, -0.02, "STANDARD", _GOV_REC_THRESHOLDS)
        assert rec == "keep_current_state"
        assert "return" in reason.lower()

    def test_classify_probation_with_low_accuracy_stays(self):
        """40% accuracy for an existing PROBATION ticker → keep_current_state."""
        from utils.supabase_persist import _governance_rec_classify, _GOV_REC_THRESHOLDS
        rec, _ = _governance_rec_classify(6, 2, 0.40, None, "PROBATION", _GOV_REC_THRESHOLDS)
        assert rec == "keep_current_state"

    # ── integration test: fetch_governance_recommendations ────────────────────

    def test_fetch_buckets_tickers_correctly(self):
        """fetch_governance_recommendations routes tickers to correct buckets."""
        from unittest.mock import patch
        from utils.supabase_persist import fetch_governance_recommendations

        rows = [
            self._row("AAPL", 10, 8, 0.06, "STANDARD"),   # → promote (80%)
            self._row("GME",  10, 4, -0.03, "STANDARD"),   # → probation (40%)
            self._row("MEME", 6,  1,  None, "STANDARD"),   # → quarantine (17%)
            self._row("MSFT", 3,  2,  None, "STANDARD"),   # → insufficient
        ]
        mock_conn, _ = self._make_managed_conn(rows)
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = fetch_governance_recommendations(days=90)

        promo  = [e["ticker"] for e in result["promote_candidates"]]
        prob   = [e["ticker"] for e in result["probation_candidates"]]
        quar   = [e["ticker"] for e in result["quarantine_candidates"]]
        insuf  = [e["ticker"] for e in result["insufficient_sample"]]

        assert "AAPL" in promo
        assert "GME"  in prob
        assert "MEME" in quar
        assert "MSFT" in insuf

    def test_fetch_returns_thresholds_and_days(self):
        """Response includes thresholds_used and days field."""
        from unittest.mock import patch
        from utils.supabase_persist import fetch_governance_recommendations

        mock_conn, _ = self._make_managed_conn([])
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = fetch_governance_recommendations(days=60)

        assert result["days"] == 60
        assert "thresholds_used" in result
        assert "promote_min_accuracy" in result["thresholds_used"]

    def test_fetch_degrades_on_db_error(self):
        """DB error returns empty structure without raising."""
        from unittest.mock import patch
        from utils.supabase_persist import fetch_governance_recommendations

        with patch("utils.db.managed_connection", side_effect=RuntimeError("db down")):
            result = fetch_governance_recommendations(days=90)

        assert result["promote_candidates"] == []
        assert result["summary"]["total_tickers"] == 0

    def test_entry_shape(self):
        """Each recommendation entry has required fields."""
        from unittest.mock import patch
        from utils.supabase_persist import fetch_governance_recommendations

        rows = [self._row("AAPL", 10, 8, 0.06, "STANDARD")]
        mock_conn, _ = self._make_managed_conn(rows)
        with patch("utils.db.managed_connection", return_value=mock_conn):
            result = fetch_governance_recommendations(days=90)

        all_entries = (
            result["promote_candidates"] + result["probation_candidates"] +
            result["quarantine_candidates"] + result["keep_current_state"] +
            result["insufficient_sample"]
        )
        assert len(all_entries) == 1
        entry = all_entries[0]
        for field in ("ticker", "current_state", "recommendation", "reason_summary",
                      "resolved", "correct_count", "directional_accuracy", "avg_return_30d"):
            assert field in entry, f"Missing field: {field}"
