"""
Tests for Signal Engine FastAPI backend.

Run:  pytest dashboard/api/tests/ -v
      (from project root, with venv activated)

Fixtures mock all database and filesystem I/O so tests run without
live data files.
"""

import csv
import io
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

# ─── Add project root so main.py imports succeed ─────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

# ─── Patch config before importing main so constants are available ───────────
with patch.dict(os.environ, {}):
    import dashboard.api.main as api_main

from dashboard.api.main import app, _cache, BASE_DIR

client = TestClient(app, raise_server_exceptions=False)


# ==============================================================================
# HELPERS
# ==============================================================================

def _make_paper_trades_db(path: str):
    """Create an in-memory-style paper_trades.db with minimal test data."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, created_at TEXT, portfolio_nav REAL,
            equity_allocation REAL, crypto_allocation REAL, cash_allocation REAL,
            spy_price REAL, btc_price REAL, btc_ma200 REAL, btc_signal TEXT, notes TEXT
        );
        CREATE TABLE equity_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER, ticker TEXT, rank INTEGER,
            composite_z REAL, weight_pct REAL, position_eur REAL, entry_price REAL
        );
        CREATE TABLE weekly_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER, week_ending TEXT,
            portfolio_return REAL, benchmark_return REAL,
            equity_return REAL, crypto_return REAL, btc_return REAL
        );
        INSERT INTO snapshots VALUES (1,'2026-03-01','2026-03-01T10:00:00',50000,0.65,0.25,0.10,560.0,85000,82000,'LONG',NULL);
        INSERT INTO snapshots VALUES (2,'2026-03-08','2026-03-08T10:00:00',50000,0.65,0.25,0.10,563.0,86000,82500,'LONG',NULL);
        INSERT INTO equity_positions VALUES (1,1,'AAPL',1,1.5,10.0,3250,175.0);
        INSERT INTO equity_positions VALUES (2,1,'NVDA',2,1.2,8.0,2600,450.0);
        INSERT INTO weekly_returns VALUES (1,2,'2026-03-08',0.012,0.008,0.015,0.010,0.012);
    """)
    conn.commit()
    conn.close()


def _make_trade_journal_db(path: str):
    """Create a minimal trade_journal.db."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT, action TEXT, price REAL, size_eur REAL, shares REAL,
            date TEXT, created_at TEXT, signal_composite REAL, signal_type TEXT,
            buy_zone_low REAL, buy_zone_high REAL, target_1 REAL, target_2 REAL,
            stop_loss REAL, notes TEXT, linked_buy_id INTEGER, status TEXT DEFAULT 'open'
        );
        INSERT INTO trades VALUES (
            1,'AAPL','BUY',175.0,3250.0,18.57,
            '2026-03-01','2026-03-01T10:00:00',1.5,'equity',
            170.0,178.0,190.0,205.0,165.0,NULL,NULL,'open'
        );
    """)
    conn.commit()
    conn.close()


def _make_ai_quant_db(path: str):
    """Create a minimal ai_quant_cache.db with one thesis entry."""
    signals = json.dumps({
        "technical": {},
        "options_flow": {"heat_score": 72.5, "iv_rank": 45.0, "pc_ratio": 0.82,
                         "expected_move_pct": 3.1, "days_to_exp": 7},
        "fundamentals": {"fundamental_score_pct": 66.0},
        "squeeze": {"short_squeeze_score": 20, "short_squeeze_max": 100},
        "social": {"bull_ratio": 0.62},
        "polymarket": {},
        "volume_profile": {"poc": 176.5, "vwap_20d": 174.0},
    })
    conn = sqlite3.connect(path)
    conn.executescript(f"""
        CREATE TABLE thesis_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT, date TEXT, direction TEXT, conviction INTEGER,
            time_horizon TEXT, entry_low REAL, entry_high REAL, stop_loss REAL,
            target_1 REAL, target_2 REAL, position_size_pct REAL,
            thesis TEXT, data_quality TEXT, notes TEXT,
            catalysts_json TEXT, risks_json TEXT, raw_response TEXT,
            signals_json TEXT, created_at TEXT,
            bull_probability REAL, bear_probability REAL, neutral_probability REAL,
            signal_agreement_score REAL, key_invalidation TEXT,
            primary_scenario TEXT, bear_scenario TEXT,
            UNIQUE(ticker, date)
        );
        INSERT INTO thesis_cache
            (ticker, date, direction, conviction, time_horizon,
             entry_low, entry_high, stop_loss, target_1, target_2,
             position_size_pct, thesis, data_quality,
             catalysts_json, risks_json, signals_json, created_at,
             bull_probability, bear_probability, neutral_probability,
             signal_agreement_score)
        VALUES
            ('AAPL','2026-03-22','BULL',4,'2-4 weeks',
             172.0,178.0,165.0,190.0,205.0,
             5.0,'Strong momentum with options confirmation.','HIGH',
             '["Strong earnings revision","Options heat 72"]',
             '["Macro headwinds","High valuation"]',
             '{signals}','2026-03-22T10:00:00',
             0.65,0.20,0.15,0.78);
    """.replace("{signals}", signals.replace("'", "''")))
    conn.commit()
    conn.close()


def _make_signals_csv(path: Path, date: str = "20260318"):
    """Write a minimal equity_signals CSV for testing."""
    path.mkdir(parents=True, exist_ok=True)
    rows = [
        "ticker,momentum_12_1_z,momentum_6_1_z,mean_rev_5d_z,vol_quality_z,"
        "risk_adj_mom_z,composite_z,rank",
        "AAPL,1.5,1.2,-0.3,-0.8,1.4,1.21,1",
        "NVDA,1.8,1.5,0.2,-1.1,1.6,1.35,2",
        "MSFT,0.9,0.7,0.5,-0.2,0.8,0.72,3",
    ]
    with open(path / f"equity_signals_{date}.csv", "w") as f:
        f.write("\n".join(rows))


def _make_conflict_log(path: Path):
    """Write a minimal conflict_resolution log."""
    path.mkdir(parents=True, exist_ok=True)
    rows = [
        "timestamp,ticker,pre_resolved,confidence,bull_weight,bear_weight,overrides,claude_skipped",
        "2026-03-22T10:00:00,AAPL,BULL,0.85,0.70,0.05,,False",
        "2026-03-22T10:01:00,NVDA,BULL,0.90,0.80,0.02,override: bear_market_circuit_breaker,False",
        "2026-03-22T10:02:00,GOOGL,NEUTRAL,0.60,0.30,0.30,,True",
    ]
    with open(path / "conflict_resolution_20260322.csv", "w") as f:
        f.write("\n".join(rows))


# ==============================================================================
# SHARED MOCK HELPERS
# ==============================================================================

def _db_conn_raises():
    """Patch target that makes _db_connect() raise (simulates DB unavailable)."""
    return patch("dashboard.api.main._db_connect", side_effect=Exception("db unavailable"))


def _make_db_conn(*, fetchone=None, fetchall=None):
    """
    Build a mock _PGConn that satisfies both calling patterns used by endpoints:
      - conn.execute(sql).fetchone()   / .fetchall()   (direct execute)
      - with conn.cursor() as cur:     (context manager, used by option_candidates)
    """
    if fetchall is None:
        fetchall = []
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = fetchone
    mock_cur.fetchall.return_value = fetchall

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_cur)
    ctx.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.execute.return_value = mock_cur
    conn.cursor.return_value = ctx
    conn.close.return_value = None
    conn.__bool__ = lambda self: True
    return conn


# ==============================================================================
# FIXTURES
# ==============================================================================

@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the in-memory cache before each test."""
    _cache._store.clear()
    yield
    _cache._store.clear()


@pytest.fixture()
def tmp_data(tmp_path):
    """Build a temporary project layout with all required files."""
    signals_dir = tmp_path / "signals_output"
    data_dir    = tmp_path / "data"
    logs_dir    = tmp_path / "logs"
    signals_dir.mkdir()
    data_dir.mkdir()
    logs_dir.mkdir()

    pt_db  = tmp_path / "paper_trades.db"
    tj_db  = tmp_path / "trade_journal.db"
    aq_db  = tmp_path / "ai_quant_cache.db"

    _make_paper_trades_db(str(pt_db))
    _make_trade_journal_db(str(tj_db))
    _make_ai_quant_db(str(aq_db))
    _make_signals_csv(signals_dir)
    _make_conflict_log(logs_dir)

    regime = {
        "market_regime": {
            "regime": "RISK_OFF", "score": -2,
            "vix": 26.78, "spy_vs_200ma": -1.21,
            "yield_curve_spread": None,
            "components": {"trend": -1, "volatility": 0, "credit": -1, "yield_curve": 0},
            "computed_at": "2026-03-22T18:05:18Z",
        },
        "sector_regimes": {"tech": "BULL", "financials": "BEAR"},
    }
    with open(data_dir / "regime_cache.json", "w") as f:
        json.dump(regime, f)

    # Patch all path constants in api_main
    patches = {
        "PAPER_TRADES_DB":  pt_db,
        "TRADE_JOURNAL_DB": tj_db,
        "AI_QUANT_DB":      aq_db,
        "SIGNALS_DIR":      signals_dir,
        "DATA_DIR":         data_dir,
        "LOGS_DIR":         logs_dir,
        "REGIME_CACHE":     data_dir / "regime_cache.json",
        "SECTOR_CACHE":     data_dir / "sector_cache.json",
    }

    with patch.multiple("dashboard.api.main", **patches):
        yield tmp_path


# ==============================================================================
# CORS TEST (no data needed)
# ==============================================================================

def test_cors_headers_present():
    """CORS headers must be present on all API responses."""
    response = client.get(
        "/api/health",
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers


def test_cors_vite_origin():
    """Vite dev server origin (5173) must also be allowed."""
    response = client.get(
        "/api/health",
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") in (
        "http://localhost:5173", "*"
    )


# ==============================================================================
# HEALTH
# ==============================================================================

def test_health_returns_200():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "data_checks" in body


# ==============================================================================
# PORTFOLIO SUMMARY — mocked db
# ==============================================================================

def test_portfolio_summary_with_db(tmp_data):
    with patch.multiple(
        "dashboard.api.main",
        PAPER_TRADES_DB=tmp_data / "paper_trades.db",
        TRADE_JOURNAL_DB=tmp_data / "trade_journal.db",
        REGIME_CACHE=tmp_data / "data" / "regime_cache.json",
    ):
        resp = client.get("/api/portfolio/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    assert "total_value_eur" in body
    assert "sharpe_ratio" in body
    assert "regime" in body
    assert body["regime"] == "RISK_OFF"


def test_portfolio_summary_missing_db():
    """Must return 200 with data_available=False when DB is unavailable."""
    with _db_conn_raises():
        resp = client.get("/api/portfolio/summary")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


# ==============================================================================
# PORTFOLIO HISTORY
# ==============================================================================

def test_portfolio_history_returns_list(tmp_data):
    with patch.multiple(
        "dashboard.api.main",
        PAPER_TRADES_DB=tmp_data / "paper_trades.db",
    ):
        resp = client.get("/api/portfolio/history?weeks=52")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    assert isinstance(body["data"], list)
    if body["data"]:
        row = body["data"][0]
        assert "week_ending" in row
        assert "portfolio_return" in row
        assert "spy_return" in row
        assert "cumulative_pnl_eur" in row


def test_portfolio_history_missing_db():
    """Must return 200 with data_available=False when DB is unavailable."""
    with _db_conn_raises():
        resp = client.get("/api/portfolio/history")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


# ==============================================================================
# SIGNALS LATEST — CSV fixture
# ==============================================================================

def test_signals_latest_with_csv(tmp_data):
    with patch("dashboard.api.main.SIGNALS_DIR", tmp_data / "signals_output"):
        resp = client.get("/api/signals/latest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    assert body["count"] == 3
    tickers = [r["ticker"] for r in body["data"]]
    assert "AAPL" in tickers
    assert "NVDA" in tickers
    # Verify column presence
    row = body["data"][0]
    assert "composite_z" in row
    assert "momentum_12_1" in row
    assert "rank" in row


def test_signals_latest_specific_date(tmp_data):
    with patch("dashboard.api.main.SIGNALS_DIR", tmp_data / "signals_output"):
        resp = client.get("/api/signals/latest?date=20260318")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is True


def test_signals_latest_missing_returns_200_not_404(tmp_data):
    """Missing signals CSV must return 200 with data_available=False."""
    with patch("dashboard.api.main.SIGNALS_DIR", Path("/nonexistent/signals_output")):
        resp = client.get("/api/signals/latest")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


def test_signals_dates(tmp_data):
    with patch("dashboard.api.main.SIGNALS_DIR", tmp_data / "signals_output"):
        resp = client.get("/api/signals/dates")
    assert resp.status_code == 200
    body = resp.json()
    assert "dates" in body
    assert "20260318" in body["dates"]


# ==============================================================================
# SIGNALS HEATMAP — normalisation check
# ==============================================================================

def test_signals_heatmap_normalised(tmp_data):
    """All module scores in each heatmap row must be normalised to [-1, +1]."""
    # The heatmap reads tickers from the signals CSV (still uses SIGNALS_DIR),
    # then enriches via _db_connect() (Supabase). Mock _db_connect to return the
    # test AAPL thesis row so the test is deterministic.
    signals_json = json.dumps({
        "options_flow": {"heat_score": 72.5, "iv_rank": 45.0},
        "squeeze":      {"short_squeeze_score": 20},
        "fundamentals": {"fundamental_score_pct": 66.0},
    })
    aapl_row = {
        "ticker": "AAPL", "direction": "BULL", "conviction": 4,
        "signal_agreement_score": 0.78, "signals_json": signals_json,
    }
    mock_conn = _make_db_conn(fetchall=[aapl_row])

    with (
        patch("dashboard.api.main.SIGNALS_DIR", tmp_data / "signals_output"),
        patch("dashboard.api.main.DATA_DIR",    tmp_data / "data"),
        patch("dashboard.api.main._db_connect", return_value=mock_conn),
    ):
        resp = client.get("/api/signals/heatmap")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    assert body["count"] >= 1
    # Response schema: module scores are flat top-level fields (not nested in "modules")
    MODULE_FIELDS = ["signal_engine", "squeeze", "options", "dark_pool", "fundamentals"]
    for row in body["data"]:
        for field in MODULE_FIELDS:
            score = row[field]
            assert -1.0 <= score <= 1.0, (
                f"{field}={score} out of [-1,1] for {row['ticker']}"
            )


def test_signals_heatmap_missing_db():
    """Returns data_available=False when signals CSV, watchlist, and DB are all unavailable."""
    with (
        patch("dashboard.api.main.SIGNALS_DIR", Path("/nope/signals_output")),
        patch("dashboard.api.main.BASE_DIR", Path("/nope")),
        _db_conn_raises(),
    ):
        resp = client.get("/api/signals/heatmap")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


# ==============================================================================
# SIGNALS TICKER
# ==============================================================================

def test_signals_ticker_found():
    """With a valid thesis row in the DB, the ticker endpoint returns full data."""
    signals_json = json.dumps({
        "options_flow": {"heat_score": 72.5, "iv_rank": 45.0, "pc_ratio": 0.82,
                         "expected_move_pct": 3.1, "days_to_exp": 7},
        "fundamentals": {"fundamental_score_pct": 66.0},
        "squeeze":      {"short_squeeze_score": 20},
        "volume_profile": {"poc": 176.5, "vwap_20d": 174.0},
    })
    thesis_row = {
        "ticker": "AAPL", "date": "2026-03-22", "direction": "BULL",
        "conviction": 4, "time_horizon": "2-4 weeks",
        "entry_low": 172.0, "entry_high": 178.0, "stop_loss": 165.0,
        "target_1": 190.0, "target_2": 205.0,
        "thesis": "Strong momentum with options confirmation.",
        "data_quality": "HIGH", "signal_agreement_score": 0.78,
        "bull_probability": 0.65, "bear_probability": 0.20, "neutral_probability": 0.15,
        "catalysts_json": '["Strong earnings revision"]',
        "risks_json": '["Macro headwinds"]',
        "signals_json": signals_json,
        "created_at": "2026-03-22T10:00:00",
        "key_invalidation": None, "primary_scenario": None, "bear_scenario": None,
        "prob_combined": None, "prob_technical": None, "prob_options": None,
        "prob_catalyst": None, "prob_news": None,
        "model_used": None, "cost_usd": None, "position_size_pct": 5.0,
        "expected_moves_json": None,
    }
    mock_conn = _make_db_conn(fetchone=thesis_row)
    with patch("dashboard.api.main._db_connect", return_value=mock_conn):
        resp = client.get("/api/signals/ticker/AAPL")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    assert body["ticker"] == "AAPL"
    assert "ai_thesis"   in body   # backward-compat nested dict
    assert "entry_zone"  in body   # backward-compat nested dict
    assert "options_heat" in body  # backward-compat nested dict


def test_signals_ticker_not_found(tmp_data):
    with patch("dashboard.api.main.AI_QUANT_DB", tmp_data / "ai_quant_cache.db"):
        resp = client.get("/api/signals/ticker/ZZZZ")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


# ==============================================================================
# TRD-072 — Analysis job concurrency gate (asyncio.Semaphore)
# ==============================================================================

class TestAnalysisGate:
    """TRD-072: ticker_analyze enqueues jobs; semaphore limits concurrency."""

    def setup_method(self):
        import asyncio
        import dashboard.api.main as m
        m._analysis_jobs.clear()
        m._analysis_semaphore = asyncio.Semaphore(m.MAX_CONCURRENT_ANALYSIS)

    def test_returns_queued_status(self):
        """POST /analyze must return status=queued."""
        with (
            patch("dashboard.api.main._missing_required_llm_env_var", return_value=None),
            patch("asyncio.create_task"),
        ):
            resp = client.post("/api/ticker/TSLA/analyze", json={"llm": "grok-4.3"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "queued"
        assert body["symbol"] == "TSLA"
        assert body["llm"] == "grok-4.3"

    def test_dedupes_already_queued_job(self):
        """Submitting the same symbol+LLM twice while queued must not create a second task."""
        task_count = 0

        def count_task(coro):
            nonlocal task_count
            task_count += 1
            coro.close()

        with (
            patch("dashboard.api.main._missing_required_llm_env_var", return_value=None),
            patch("asyncio.create_task", side_effect=count_task),
        ):
            r1 = client.post("/api/ticker/AMZN/analyze", json={"llm": "grok-4.3"})
            r2 = client.post("/api/ticker/AMZN/analyze", json={"llm": "grok-4.3"})

        assert r1.json()["status"] == "queued"
        assert r2.json()["status"] == "queued"
        assert task_count == 1, "second submit must not spawn a new task for the same queued job"

    def test_status_endpoint_returns_queued(self):
        """GET /analyze/status returns {status: queued} when job is in queue."""
        import dashboard.api.main as m
        m._analysis_jobs["MSFT::grok-4.3"] = {
            "status": "queued",
            "queued_at": "2026-06-10T10:00:00Z",
            "llm": "grok-4.3",
            "symbol": "MSFT",
            "job_key": "MSFT::grok-4.3",
        }
        resp = client.get("/api/ticker/MSFT/analyze/status?llm=grok-4.3")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "queued"
        assert body["llm"] == "grok-4.3"
        assert "queued_at" in body

    def test_different_llm_same_symbol_not_deduped(self):
        """Two different LLMs for the same symbol each get their own queue slot."""
        task_count = 0

        def count_task(coro):
            nonlocal task_count
            task_count += 1
            coro.close()

        with (
            patch("dashboard.api.main._missing_required_llm_env_var", return_value=None),
            patch("asyncio.create_task", side_effect=count_task),
        ):
            client.post("/api/ticker/NVDA/analyze", json={"llm": "grok-4.3"})
            client.post("/api/ticker/NVDA/analyze", json={"llm": "gpt-5.1"})

        assert task_count == 2, "different LLMs for same symbol must each create a task"

    @pytest.mark.asyncio
    async def test_semaphore_gate_holds_overflow_and_advances_on_release(self):
        """Overflow job stays queued while all slots are taken; advances once a slot frees."""
        import asyncio
        import dashboard.api.main as m

        # Acquire all slots to simulate MAX_CONCURRENT_ANALYSIS jobs already running
        for _ in range(m.MAX_CONCURRENT_ANALYSIS):
            await m._analysis_semaphore.acquire()

        job_key = "GATETEST::grok-4.3"
        m._analysis_jobs[job_key] = {
            "status": "queued",
            "queued_at": "2026-06-10T00:00:00Z",
            "llm": "grok-4.3",
            "symbol": "GATETEST",
            "job_key": job_key,
        }

        # Stub subprocess so the gate test doesn't actually run ai_quant.py
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"gate test"))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
            task = asyncio.create_task(
                m._run_queued_analysis(job_key, "GATETEST", "grok-4.3", "/bin/python", "/dev/null", {})
            )

            # Yield control so the task can attempt semaphore.acquire (it will block)
            await asyncio.sleep(0)

            assert m._analysis_jobs[job_key]["status"] == "queued", (
                "Job must stay queued while all concurrency slots are occupied"
            )

            # Release one slot — the overflow job should now advance
            m._analysis_semaphore.release()
            await asyncio.sleep(0.05)

            final_status = m._analysis_jobs[job_key]["status"]
            assert final_status != "queued", (
                f"Job must leave queued state after a slot frees up, got {final_status!r}"
            )

            await task

        # Restore semaphore to full capacity
        for _ in range(m.MAX_CONCURRENT_ANALYSIS - 1):
            m._analysis_semaphore.release()


# ==============================================================================
# BUG-001 — _fetch_current_prices must be offloaded via asyncio.to_thread
# ==============================================================================

def _make_full_thesis_row(ticker: str = "AAPL") -> dict:
    signals_json = json.dumps({
        "options_flow": {"heat_score": 72.5},
        "fundamentals": {"fundamental_score_pct": 66.0},
        "squeeze":      {"short_squeeze_score": 20},
    })
    return {
        "ticker": ticker, "date": "2026-03-22", "direction": "BULL",
        "conviction": 4, "time_horizon": "2-4 weeks",
        "entry_low": 172.0, "entry_high": 178.0, "stop_loss": 165.0,
        "target_1": 190.0, "target_2": 205.0, "position_size_pct": 5.0,
        "thesis": "Test thesis.", "data_quality": "HIGH",
        "signal_agreement_score": 0.78,
        "bull_probability": 0.65, "bear_probability": 0.20, "neutral_probability": 0.15,
        "catalysts_json": "[]", "risks_json": "[]", "signals_json": signals_json,
        "created_at": "2026-03-22T10:00:00",
        "key_invalidation": None, "primary_scenario": None, "bear_scenario": None,
        "prob_combined": None, "prob_technical": None, "prob_options": None,
        "prob_catalyst": None, "prob_news": None,
        "model_used": None, "cost_usd": None, "expected_moves_json": None,
    }


def test_signals_ticker_db_connect_failure_returns_no_data():
    """signals_ticker must return data_available:false (not 500) when DB connect raises."""
    with patch("dashboard.api.main._db_connect", side_effect=Exception("connection refused")):
        resp = client.get("/api/signals/ticker/CRDO")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is False
    assert "database" in body.get("reason", "").lower()


def test_signals_ticker_price_fetch_uses_to_thread():
    """signals_ticker must offload the price fetch via asyncio.to_thread (BUG-001)."""
    from dashboard.api.main import _fetch_current_prices

    mock_conn = _make_db_conn(fetchone=_make_full_thesis_row())
    offloaded: list = []

    async def spy_to_thread(fn, *args, **kwargs):
        offloaded.append(fn)
        return fn(*args, **kwargs)

    with (
        patch("dashboard.api.main._db_connect", return_value=mock_conn),
        patch("dashboard.api.main._md_get_prices", return_value={"AAPL": 180.0}),
        patch("asyncio.to_thread", side_effect=spy_to_thread),
    ):
        resp = client.get("/api/signals/ticker/AAPL")

    assert resp.status_code == 200
    assert resp.json()["data_available"] is True
    assert _fetch_current_prices in offloaded, (
        "signals_ticker must call price fetch via asyncio.to_thread, not inline"
    )


def test_option_candidates_price_fetch_uses_to_thread():
    """ticker_option_candidates must offload the market-data price fetch via asyncio.to_thread (BUG-001)."""
    # thesis_row deliberately omits current_price so the endpoint triggers the price fetch
    thesis_row = {
        "ticker": "AAPL", "direction": "BULL", "conviction": 4,
        "entry_low": 145.0, "entry_high": 150.0, "target_1": 165.0,
        "target_2": None, "stop_loss": 140.0, "time_horizon": None,
        "signal_agreement_score": None, "signals_json": None,
        # current_price absent → ThesisContext.current_price will be None → triggers fetch
    }
    mock_conn = _make_mock_db_conn(thesis_row=thesis_row)
    offloaded: list = []

    async def spy_to_thread(fn, *args, **kwargs):
        offloaded.append(fn)
        return fn(*args, **kwargs)

    from dashboard.api.main import _fetch_current_prices

    with (
        patch("dashboard.api.main._db_connect", return_value=mock_conn),
        patch("dashboard.api.main._md_get_prices", return_value={"AAPL": 148.0}),
        patch("dashboard.api.main.get_option_candidates",
              return_value=_make_suppressed_result("AAPL")),
        patch("asyncio.to_thread", side_effect=spy_to_thread),
    ):
        resp = client.get("/api/ticker/AAPL/option-candidates")

    assert resp.status_code == 200
    assert _fetch_current_prices in offloaded, (
        "ticker_option_candidates must call price fetch via asyncio.to_thread, not inline"
    )


# ==============================================================================
# SCREENERS
# ==============================================================================

def test_screeners_squeeze_missing_csv():
    with patch("dashboard.api.main.SIGNALS_DIR", Path("/nope")):
        resp = client.get("/api/screeners/squeeze")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


def test_screeners_catalysts_missing_csv():
    with patch("dashboard.api.main.SIGNALS_DIR", Path("/nope")):
        resp = client.get("/api/screeners/catalysts")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


def test_screeners_options_from_cache():
    """Options screener returns tickers that exceed the heat threshold."""
    signals_json = json.dumps({
        "options_flow": {"heat_score": 72.5, "iv_rank": 45.0, "pc_ratio": 0.82,
                         "expected_move_pct": 3.1, "days_to_exp": 7},
    })
    aapl_row = {"ticker": "AAPL", "date": "2026-03-22", "signals_json": signals_json}
    mock_conn = _make_db_conn(fetchall=[aapl_row])
    with patch("dashboard.api.main._db_connect", return_value=mock_conn):
        resp = client.get("/api/screeners/options?min_heat=50")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    # AAPL has heat_score=72.5 > min_heat=50, must appear
    tickers = [r["ticker"] for r in body["data"]]
    assert "AAPL" in tickers


# ==============================================================================
# TRD-080: OPTIONS SCREENER SNAPSHOT
# ==============================================================================

def test_options_screener_reads_snapshot():
    """GET /api/options/screener returns stored snapshot data, not live fan-out."""
    from datetime import datetime, timezone
    snapshot_row = {
        "run_at": datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc),
        "tickers_evaluated": 5,
        "tickers_completed": 5,
        "partial": False,
        "timed_out_tickers": [],
        "count": 2,
        "data": [{"ticker": "AAPL", "score": 75.0}, {"ticker": "MSFT", "score": 70.0}],
    }
    mock_conn = _make_db_conn(fetchone=snapshot_row)
    with patch("dashboard.api.main._db_connect", return_value=mock_conn):
        resp = client.get("/api/options/screener?min_conviction=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    assert body["count"] == 2
    assert body["snapshot_time"] is not None
    assert "2026-06-10" in body["snapshot_time"]
    assert len(body["data"]) == 2
    assert body["data"][0]["ticker"] == "AAPL"


def test_options_screener_no_snapshot_returns_data_available_false():
    """GET /api/options/screener with empty snapshot table returns data_available=false."""
    mock_conn = _make_db_conn(fetchone=None)
    with patch("dashboard.api.main._db_connect", return_value=mock_conn):
        resp = client.get("/api/options/screener?min_conviction=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is False
    assert body["snapshot_time"] is None
    assert "message" in body


def test_options_screener_refresh_queues_background_job():
    """POST /api/options/screener/refresh returns queued=true and starts background thread."""
    import dashboard.api.main as api_main
    api_main._screener_job_running = False
    with patch("threading.Thread") as mock_thread:
        mock_thread.return_value.start.return_value = None
        resp = client.post("/api/options/screener/refresh?min_conviction=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["queued"] is True
    assert "queued" in body["message"].lower() or "refresh" in body["message"].lower()
    mock_thread.assert_called_once()


def test_options_screener_refresh_rate_limited_when_running():
    """POST /api/options/screener/refresh returns queued=false when a job is already running."""
    import dashboard.api.main as api_main
    api_main._screener_job_running = True
    try:
        resp = client.post("/api/options/screener/refresh?min_conviction=2")
        assert resp.status_code == 200
        body = resp.json()
        assert body["queued"] is False
    finally:
        api_main._screener_job_running = False


# ==============================================================================
# REGIME
# ==============================================================================

def test_regime_current_with_data(tmp_data):
    with patch("dashboard.api.main.REGIME_CACHE", tmp_data / "data" / "regime_cache.json"):
        resp = client.get("/api/regime/current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    assert body["regime"] == "RISK_OFF"
    assert "sector_regimes" in body
    assert "tech" in body["sector_regimes"]


def test_regime_current_missing():
    with patch("dashboard.api.main.REGIME_CACHE", Path("/nope/regime.json")):
        resp = client.get("/api/regime/current")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


# ==============================================================================
# DARK POOL
# ==============================================================================

def test_darkpool_top_no_data():
    with patch("dashboard.api.main.DATA_DIR", Path("/nope")):
        resp = client.get("/api/darkpool/top")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


def test_darkpool_ticker_not_found():
    with patch("dashboard.api.main.DATA_DIR", Path("/nope")):
        resp = client.get("/api/darkpool/ticker/AAPL")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


# ==============================================================================
# CONFLICT RESOLUTION
# ==============================================================================

def test_resolution_log_with_data(tmp_data):
    with patch("dashboard.api.main.LOGS_DIR", tmp_data / "logs"):
        resp = client.get("/api/resolution/log")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    assert body["count"] == 3
    assert "timestamp" in body["data"][0]


def test_resolution_log_missing():
    with patch("dashboard.api.main.LOGS_DIR", Path("/nope/logs")):
        resp = client.get("/api/resolution/log")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


def test_resolution_stats_with_data(tmp_data):
    with patch("dashboard.api.main.LOGS_DIR", tmp_data / "logs"):
        resp = client.get("/api/resolution/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    assert "claude_skip_rate" in body
    assert "bear_circuit_breaker_hits" in body
    assert body["rows_analyzed"] == 3


# ==============================================================================
# BACKTEST
# ==============================================================================

def test_backtest_from_csv(tmp_data):
    # Write a minimal backtest_equity_metrics.csv
    (tmp_data / "signals_output" / "backtest_equity_metrics.csv").write_text(
        "label,value\nEquity Multi-Factor,\nsharpe_ratio,0.85\n"
    )
    with patch("dashboard.api.main.SIGNALS_DIR", tmp_data / "signals_output"):
        with patch("dashboard.api.main.DATA_DIR", tmp_data / "data"):
            resp = client.get("/api/backtest/results")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True


def test_backtest_missing_files():
    """Returns data_available=False when neither DB nor CSV files have backtest data."""
    with (
        patch("dashboard.api.main.SIGNALS_DIR", Path("/nope")),
        patch("dashboard.api.main.DATA_DIR",    Path("/nope")),
        _db_conn_raises(),
    ):
        resp = client.get("/api/backtest/results")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


# ==============================================================================
# "MISSING DATA RETURNS 200 WITH data_available=False" — comprehensive sweep
# ==============================================================================

MISSING_ENDPOINTS = [
    "/api/portfolio/summary",
    "/api/portfolio/history",
    "/api/portfolio/positions",
    "/api/signals/latest",
    "/api/signals/heatmap",
    "/api/signals/ticker/FAKE",
    "/api/screeners/squeeze",
    "/api/screeners/catalysts",
    "/api/screeners/options",
    "/api/regime/current",
    "/api/darkpool/top",
    "/api/darkpool/ticker/FAKE",
    "/api/resolution/log",
    "/api/resolution/stats",
    "/api/backtest/results",
]


@pytest.mark.parametrize("endpoint", MISSING_ENDPOINTS)
def test_missing_data_returns_200(endpoint):
    """All endpoints must return HTTP 200 even when data files are absent."""
    nowhere = Path("/nonexistent_path_for_testing")
    with patch.multiple(
        "dashboard.api.main",
        PAPER_TRADES_DB=nowhere / "paper_trades.db",
        TRADE_JOURNAL_DB=nowhere / "trade_journal.db",
        AI_QUANT_DB=nowhere / "ai_quant_cache.db",
        SIGNALS_DIR=nowhere / "signals_output",
        DATA_DIR=nowhere / "data",
        LOGS_DIR=nowhere / "logs",
        REGIME_CACHE=nowhere / "regime_cache.json",
        SECTOR_CACHE=nowhere / "sector_cache.json",
    ):
        resp = client.get(endpoint)
    assert resp.status_code == 200, (
        f"{endpoint} returned {resp.status_code} instead of 200"
    )
    body = resp.json()
    assert "data_available" in body, (
        f"{endpoint} missing 'data_available' key in response"
    )


# ==============================================================================
# TRD-004 — Watch Setup Alert endpoint tests
# ==============================================================================

class TestWatchSetupEndpoint:
    """
    Tests for /api/watch-setup.
    All DB calls are mocked — no live Supabase required.
    """

    def _mock_db_rows(self, rows):
        """Return a mock _PGConn that yields the given rows from execute()."""
        mock_conn  = MagicMock()
        mock_cur   = MagicMock()
        mock_conn.execute.return_value = mock_cur
        mock_cur.fetchall.return_value = rows
        return mock_conn

    def test_watch_setup_returns_200_with_no_data(self):
        """Endpoint returns 200 + data_available:False when DB call fails."""
        with patch("dashboard.api.main._db_connect", side_effect=Exception("no db")):
            _cache.invalidate("watch_setup_alerts")
            resp = client.get("/api/watch-setup")
        assert resp.status_code == 200
        body = resp.json()
        assert "data_available" in body

    def test_watch_setup_alert_shape(self):
        """Alert rows must contain ticker, alert_type, reasons, note."""
        fake_rows = [
            {
                "ticker": "SNOW",
                "composite": 0.0,
                "raw_composite": 51.4,
                "options_score": 4.0,
                "volume_score": 4.0,
                "technical_score": 4.0,
                "dark_pool_score": 1.0,
                "dark_pool_signal": "NEUTRAL",
                "earnings_score": 5.0,
                "post_squeeze_guard": False,
                "price": 168.5,
                "days_to_earnings": 2,
                "thesis_direction": "NEUTRAL",
                "entry_low": 147.0,
                "entry_high": 153.0,
            }
        ]
        mock_conn = self._mock_db_rows(fake_rows)
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            _cache.invalidate("watch_setup_alerts")
            resp = client.get("/api/watch-setup")

        assert resp.status_code == 200
        body = resp.json()
        assert body.get("data_available") is True
        assert "alerts" in body
        if body["alerts"]:
            alert = body["alerts"][0]
            assert alert["ticker"] == "SNOW"
            assert alert["alert_type"] == "catalyst_setup"
            assert isinstance(alert["reasons"], list)
            assert "note" in alert
            # Must NOT be labeled as a buy signal
            note = alert["note"].lower()
            assert "buy" not in note or "not" in note or "watch" in note

    def test_watch_setup_skips_hot_entry_zone_tickers(self):
        """Tickers whose price is inside the entry zone must be excluded."""
        fake_rows = [
            {
                "ticker": "INZONE",
                "composite": 45.0,
                "raw_composite": 45.0,
                "options_score": 4.0,
                "volume_score": 3.0,
                "technical_score": 3.0,
                "dark_pool_score": 1.0,
                "dark_pool_signal": "NEUTRAL",
                "earnings_score": 4.0,
                "post_squeeze_guard": False,
                "price": 150.0,       # inside entry zone
                "days_to_earnings": 3,
                "thesis_direction": "BULL",
                "entry_low": 148.0,
                "entry_high": 153.0,  # 150 is inside 148-153 * 1.02
            }
        ]
        mock_conn = self._mock_db_rows(fake_rows)
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            _cache.invalidate("watch_setup_alerts")
            resp = client.get("/api/watch-setup")

        assert resp.status_code == 200
        body = resp.json()
        tickers = [a["ticker"] for a in body.get("alerts", [])]
        assert "INZONE" not in tickers, "Tickers in Hot Entry zone must not appear in watch-setup"

    def test_watch_setup_label_is_not_hot(self):
        """Label must say 'Catalyst Setup', not 'HOT' or 'Buy'."""
        fake_rows = [
            {
                "ticker": "TESTWS",
                "composite": 0.0,
                "raw_composite": 42.0,
                "options_score": 3.5,
                "volume_score": 3.0,
                "technical_score": 3.0,
                "dark_pool_score": 2.0,
                "dark_pool_signal": "ACCUMULATION",
                "earnings_score": 3.0,
                "post_squeeze_guard": False,
                "price": 75.0,
                "days_to_earnings": 8,
                "thesis_direction": "NEUTRAL",
                "entry_low": 60.0,
                "entry_high": 65.0,
            }
        ]
        mock_conn = self._mock_db_rows(fake_rows)
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            _cache.invalidate("watch_setup_alerts")
            resp = client.get("/api/watch-setup")

        body = resp.json()
        for alert in body.get("alerts", []):
            assert alert["label"] != "HOT", "Watch setup label must not be 'HOT'"
            label_lower = alert["label"].lower()
            assert "buy" not in label_lower, "Watch setup label must not say 'buy'"


# ==============================================================================
# /api/pattern-watch — TRD-017
# ==============================================================================

class TestPatternWatchEndpoint:
    """
    Tests for /api/pattern-watch.
    All DB calls are mocked — no live Supabase required.
    """

    # ── helpers ────────────────────────────────────────────────────────────────

    def _mock_db(self, cs_rows, snap_rows=None):
        """Return a mock _PGConn whose execute().fetchall() returns the right rows."""
        mock_conn = MagicMock()

        def _execute(sql, params=None):
            cur = MagicMock()
            sql_lower = sql.strip().lower()
            if "catalyst_scores" in sql_lower:
                cur.fetchall.return_value = cs_rows
            elif "candidate_snapshots" in sql_lower:
                cur.fetchall.return_value = snap_rows or []
            else:
                cur.fetchall.return_value = []
            return cur

        mock_conn.execute.side_effect = _execute
        return mock_conn

    def _cs(self, ticker, **kwargs):
        """Minimal catalyst_scores row with sane defaults."""
        return {
            "ticker":           ticker,
            "composite":        kwargs.get("composite", 40.0),
            "raw_composite":    kwargs.get("raw_composite", kwargs.get("composite", 40.0)),
            "options_score":    kwargs.get("options_score", 2.0),
            "volume_score":     kwargs.get("volume_score", 2.0),
            "technical_score":  kwargs.get("technical_score", 2.0),
            "dark_pool_score":  kwargs.get("dark_pool_score", 1.0),
            "dark_pool_signal": kwargs.get("dark_pool_signal", "NEUTRAL"),
            "earnings_score":   kwargs.get("earnings_score", 1.0),
            "post_squeeze_guard": kwargs.get("post_squeeze_guard", False),
            "price":            kwargs.get("price", 100.0),
            "days_to_earnings": kwargs.get("days_to_earnings", None),
        }

    def _snap(self, ticker, **kwargs):
        """Minimal candidate_snapshots row."""
        return {
            "ticker":           ticker,
            "selection_reason": kwargs.get("selection_reason", ""),
            "priority_score":   kwargs.get("priority_score", 50.0),
        }

    # ── envelope shape ─────────────────────────────────────────────────────────

    def test_pattern_watch_returns_200_envelope_always(self):
        """Endpoint always returns 200 with data_available:true, even on DB error."""
        with patch("dashboard.api.main._db_connect", side_effect=Exception("no db")):
            _cache.invalidate("pattern_watch")
            resp = client.get("/api/pattern-watch")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        assert "count" in body
        assert "method_note" in body
        assert isinstance(body["data"], list)

    def test_pattern_watch_empty_dataset_returns_empty_list(self):
        """Empty catalyst_scores → count:0 and data:[], not a 404."""
        mock_conn = self._mock_db(cs_rows=[])
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            _cache.invalidate("pattern_watch")
            resp = client.get("/api/pattern-watch")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is True
        assert body["count"] == 0
        assert body["data"] == []

    # ── SNOW archetype ─────────────────────────────────────────────────────────

    def test_pattern_watch_snow_like_candidate_matches_snow(self):
        """High earnings + options + technical → matched_pattern == SNOW."""
        cs = [self._cs(
            "SNOWTEST",
            earnings_score=5.0, options_score=4.0, technical_score=4.0,
            volume_score=3.0, days_to_earnings=3, price=168.5,
            post_squeeze_guard=False,
        )]
        mock_conn = self._mock_db(cs_rows=cs)
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            _cache.invalidate("pattern_watch")
            resp = client.get("/api/pattern-watch")

        body = resp.json()
        assert body["data_available"] is True
        items = body["data"]
        assert len(items) >= 1
        item = items[0]
        assert item["ticker"] == "SNOWTEST"
        assert item["matched_pattern"] == "SNOW"
        assert item["similarity_pct"] >= 50
        assert item["case_upside_pct"] > 0
        assert item["case_probability_pct"] > 0
        assert item["confidence"] == "LOW_SAMPLE"
        assert isinstance(item["flags"], list)
        assert "earnings_imminent_3d" in item["flags"] or "earnings_imminent" in " ".join(item["flags"])

    # ── CRSR archetype ─────────────────────────────────────────────────────────

    def test_pattern_watch_crsr_like_candidate_matches_crsr(self):
        """Catalyst breakout in selection_reason + options → matched_pattern == CRSR."""
        cs = [self._cs(
            "CRSRTEST",
            earnings_score=1.0, options_score=3.5, technical_score=3.0,
            dark_pool_signal="ACCUMULATION",
        )]
        snap = [self._snap("CRSRTEST", selection_reason="fresh_catalyst_breakout", priority_score=55.0)]
        mock_conn = self._mock_db(cs_rows=cs, snap_rows=snap)
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            _cache.invalidate("pattern_watch")
            resp = client.get("/api/pattern-watch")

        body = resp.json()
        items = body["data"]
        assert len(items) >= 1
        item = next((i for i in items if i["ticker"] == "CRSRTEST"), None)
        assert item is not None
        assert item["matched_pattern"] == "CRSR"
        assert item["similarity_pct"] >= 50
        assert "fresh_catalyst_breakout" in item["flags"]

    def test_pattern_watch_candidate_snapshot_only_candidate_is_included(self):
        """Candidate-only catalyst breakouts should not wait for catalyst_scores."""
        snap = [self._snap(
            "SNAPONLY",
            selection_reason="fresh_catalyst_breakout catalyst_price_expansion",
            priority_score=62.0,
        )]
        mock_conn = self._mock_db(cs_rows=[], snap_rows=snap)
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            _cache.invalidate("pattern_watch")
            resp = client.get("/api/pattern-watch")

        body = resp.json()
        item = next((i for i in body["data"] if i["ticker"] == "SNAPONLY"), None)
        assert item is not None
        assert item["matched_pattern"] == "CRSR"
        assert item["similarity_pct"] >= 50
        assert item["source"] == ["candidate_snapshots"]

    # ── DELL archetype ─────────────────────────────────────────────────────────

    def test_pattern_watch_dell_like_candidate_matches_dell(self):
        """Strong technical + volume + high priority, no earnings → matched_pattern == DELL."""
        cs = [self._cs(
            "DELLTEST",
            earnings_score=1.0, technical_score=5.0, volume_score=4.0,
        )]
        snap = [self._snap("DELLTEST", priority_score=70.0)]
        mock_conn = self._mock_db(cs_rows=cs, snap_rows=snap)
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            _cache.invalidate("pattern_watch")
            resp = client.get("/api/pattern-watch")

        body = resp.json()
        items = body["data"]
        assert len(items) >= 1
        item = next((i for i in items if i["ticker"] == "DELLTEST"), None)
        assert item is not None
        assert item["matched_pattern"] == "DELL"
        assert item["similarity_pct"] >= 50

    # ── deduplication ──────────────────────────────────────────────────────────

    def test_pattern_watch_deduplication_by_ticker(self):
        """Same ticker appearing twice in catalyst_scores must appear once in result."""
        snow_row = self._cs(
            "DUP",
            earnings_score=5.0, options_score=4.0, technical_score=4.0, volume_score=3.0,
            days_to_earnings=2, post_squeeze_guard=False,
        )
        mock_conn = self._mock_db(cs_rows=[snow_row, snow_row])
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            _cache.invalidate("pattern_watch")
            resp = client.get("/api/pattern-watch")

        body = resp.json()
        tickers = [i["ticker"] for i in body["data"]]
        assert tickers.count("DUP") == 1

    # ── required response fields ───────────────────────────────────────────────

    def test_pattern_watch_item_has_all_required_fields(self):
        """Every returned item must have all required response fields."""
        required = {
            "ticker", "matched_pattern", "similarity_pct",
            "case_probability_pct", "case_upside_pct", "confidence",
            "sample_size", "flags", "reason", "source",
            "current_price", "days_to_earnings", "raw_score",
        }
        cs = [self._cs(
            "FIELDCHECK",
            earnings_score=5.0, options_score=4.0, technical_score=4.0,
            volume_score=3.0, days_to_earnings=2, post_squeeze_guard=False,
        )]
        mock_conn = self._mock_db(cs_rows=cs)
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            _cache.invalidate("pattern_watch")
            resp = client.get("/api/pattern-watch")

        items = resp.json()["data"]
        assert items, "Expected at least one item"
        missing = required - set(items[0].keys())
        assert not missing, f"Missing fields: {missing}"

    # ── method_note must be present ────────────────────────────────────────────

    def test_pattern_watch_method_note_present(self):
        """method_note must be present and mention 'low sample'."""
        mock_conn = self._mock_db(cs_rows=[])
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            _cache.invalidate("pattern_watch")
            resp = client.get("/api/pattern-watch")

        note = resp.json().get("method_note", "")
        assert note, "method_note must not be empty"
        assert "sample" in note.lower()


# ==============================================================================
# TRD-022: /api/ticker/{symbol}/option-candidates
# ==============================================================================

def _make_mock_db_conn(thesis_row: dict | None):
    """
    Build a minimal mock _PGConn that returns *thesis_row* from a cursor fetchone.
    The cursor is used as a context manager (with conn.cursor() as cur:).
    """
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = thesis_row

    # cursor() used as a context manager
    ctx_mgr = MagicMock()
    ctx_mgr.__enter__ = MagicMock(return_value=mock_cur)
    ctx_mgr.__exit__ = MagicMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = ctx_mgr
    mock_conn.close.return_value = None
    return mock_conn


def _make_suppressed_result(ticker: str = "AAPL", reason: str = "No thesis context") -> dict:
    """Return a CandidateResult-like dict for a suppressed response."""
    from utils.option_candidates import CandidateResult
    return CandidateResult(
        ticker=ticker,
        generated_at="2026-05-29T12:00:00",
        suppressed=True,
        suppression_reason=reason,
    )


def _make_candidate_result(ticker: str = "AAPL") -> dict:
    """Return a CandidateResult-like object with one mock candidate."""
    from utils.option_candidates import CandidateResult, OptionCandidate
    from datetime import date, timedelta
    expiry = (date.today() + timedelta(days=21)).strftime("%Y-%m-%d")
    candidate = OptionCandidate(
        ticker=ticker,
        expiry=expiry,
        strike=150.0,
        right="C",
        dte=21,
        bid=2.0,
        ask=2.20,
        mid=2.10,
        spread_pct=9.5,
        delta=0.40,
        implied_vol=0.35,
        open_interest=500,
        volume=100,
        breakeven=152.10,
        score=68.0,
        rationale="bullish long call — Δ+0.40, IV 35%, 21d DTE",
        strategy_preset="long_call",
        source="yfinance",
    )
    return CandidateResult(
        ticker=ticker,
        generated_at="2026-05-29T12:00:00",
        suppressed=False,
        candidates=[candidate],
        rejection_reasons=["C 155.0 2026-06-20: OI 10 < 50 minimum"],
        underlying_price=148.0,
        chain_source="yfinance",
    )


class TestOptionCandidatesEndpoint:
    """Tests for GET /api/ticker/{symbol}/option-candidates  (TRD-022)."""

    def setup_method(self):
        _cache._store.clear()

    def test_returns_200_always(self):
        """Endpoint must return 200 even when no data is available."""
        with (
            patch("dashboard.api.main._db_connect", side_effect=Exception("no db")),
            patch("dashboard.api.main.get_option_candidates",
                  return_value=_make_suppressed_result("AAPL", "No thesis context available")),
        ):
            resp = client.get("/api/ticker/AAPL/option-candidates")
        assert resp.status_code == 200

    def test_response_shape_suppressed(self):
        """Suppressed response must have all required fields."""
        with (
            patch("dashboard.api.main._db_connect", side_effect=Exception("no db")),
            patch("dashboard.api.main.get_option_candidates",
                  return_value=_make_suppressed_result()),
        ):
            resp = client.get("/api/ticker/AAPL/option-candidates")

        body = resp.json()
        required = {
            "ticker", "generated_at", "suppressed", "suppression_reason",
            "candidates", "rejection_reasons", "underlying_price",
            "chain_source", "chain_error",
        }
        missing = required - set(body.keys())
        assert not missing, f"Missing fields: {missing}"

    def test_suppressed_when_no_db(self):
        """When DB is unavailable, no thesis → suppressed=True."""
        with (
            patch("dashboard.api.main._db_connect", side_effect=Exception("no db")),
            patch("dashboard.api.main.get_option_candidates",
                  return_value=_make_suppressed_result()),
        ):
            resp = client.get("/api/ticker/AAPL/option-candidates")

        body = resp.json()
        assert body["suppressed"] is True
        assert body["candidates"] == []

    def test_suppressed_when_no_thesis_row(self):
        """When thesis_cache has no row for this ticker, suppressed=True."""
        mock_conn = _make_mock_db_conn(thesis_row=None)
        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  return_value=_make_suppressed_result("AAPL", "No thesis context available for this ticker")),
        ):
            resp = client.get("/api/ticker/AAPL/option-candidates")

        body = resp.json()
        assert body["suppressed"] is True
        assert body["candidates"] == []
        assert body["suppression_reason"] is not None

    def test_returns_candidates_when_thesis_found(self):
        """With a valid thesis, the engine result is serialized correctly."""
        thesis_row = {
            "ticker": "AAPL", "direction": "BULL", "conviction": 4,
            "entry_low": 145.0, "entry_high": 150.0, "target_1": 165.0,
            "target_2": 175.0, "stop_loss": 140.0, "time_horizon": "2-4 weeks",
            "signals_json": None, "current_price": 148.0,
        }
        mock_conn = _make_mock_db_conn(thesis_row=thesis_row)
        engine_result = _make_candidate_result("AAPL")

        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates", return_value=engine_result),
        ):
            resp = client.get("/api/ticker/AAPL/option-candidates")

        body = resp.json()
        assert resp.status_code == 200
        assert body["suppressed"] is False
        assert len(body["candidates"]) == 1
        c = body["candidates"][0]
        assert c["right"]  == "C"
        assert c["strike"] == 150.0
        assert c["delta"]  == pytest.approx(0.40)
        assert c["source"] == "yfinance"

    def test_candidate_fields_complete(self):
        """Each candidate must include all execution-relevant fields."""
        thesis_row = {
            "ticker": "AAPL", "direction": "BULL", "conviction": 3,
            "entry_low": 145.0, "entry_high": 150.0, "target_1": 165.0,
            "target_2": None, "stop_loss": 140.0, "time_horizon": None,
            "signals_json": None, "current_price": 148.0,
        }
        mock_conn = _make_mock_db_conn(thesis_row=thesis_row)
        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  return_value=_make_candidate_result("AAPL")),
        ):
            resp = client.get("/api/ticker/AAPL/option-candidates")

        c = resp.json()["candidates"][0]
        required_fields = {
            "ticker", "expiry", "strike", "right", "dte",
            "bid", "ask", "mid", "spread_pct", "delta",
            "implied_vol", "open_interest", "volume",
            "breakeven", "score", "rationale", "strategy_preset", "source",
        }
        missing = required_fields - set(c.keys())
        assert not missing, f"Candidate missing fields: {missing}"

    def test_implied_vol_serialized_as_percentage(self):
        """implied_vol must be returned as a percentage (e.g. 35.0 not 0.35)."""
        thesis_row = {
            "ticker": "AAPL", "direction": "BULL", "conviction": 3,
            "entry_low": 145.0, "entry_high": 150.0, "target_1": 165.0,
            "target_2": None, "stop_loss": 140.0, "time_horizon": None,
            "signals_json": None, "current_price": 148.0,
        }
        mock_conn = _make_mock_db_conn(thesis_row=thesis_row)
        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  return_value=_make_candidate_result("AAPL")),
        ):
            resp = client.get("/api/ticker/AAPL/option-candidates")

        c = resp.json()["candidates"][0]
        # Candidate has implied_vol=0.35; serializer multiplies by 100 → 35.0
        assert c["implied_vol"] == pytest.approx(35.0)
        assert c["implied_vol"] > 1.0, "implied_vol must be percentage not decimal"

    def test_rejection_reasons_included(self):
        """rejection_reasons must be a list (even when empty)."""
        mock_conn = _make_mock_db_conn(thesis_row=None)
        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  return_value=_make_suppressed_result()),
        ):
            resp = client.get("/api/ticker/AAPL/option-candidates")

        body = resp.json()
        assert isinstance(body["rejection_reasons"], list)

    def test_ticker_normalized_to_uppercase(self):
        """Lowercase symbol in URL must be normalized to uppercase in response."""
        mock_conn = _make_mock_db_conn(thesis_row=None)
        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  return_value=_make_suppressed_result("AAPL")),
        ):
            resp = client.get("/api/ticker/aapl/option-candidates")

        assert resp.status_code == 200
        assert resp.json()["ticker"] == "AAPL"

    def test_cache_hit_avoids_second_db_call(self):
        """A cached response must be returned on a second identical request."""
        thesis_row = {
            "ticker": "MSFT", "direction": "BULL", "conviction": 3,
            "entry_low": 400.0, "entry_high": 410.0, "target_1": 430.0,
            "target_2": None, "stop_loss": 390.0, "time_horizon": None,
            "signals_json": None, "current_price": 405.0,
        }
        mock_conn = _make_mock_db_conn(thesis_row=thesis_row)

        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn) as db_mock,
            patch("dashboard.api.main.get_option_candidates",
                  return_value=_make_candidate_result("MSFT")),
        ):
            _cache.invalidate("option_candidates:MSFT")
            client.get("/api/ticker/MSFT/option-candidates")
            count_after_first = db_mock.call_count
            client.get("/api/ticker/MSFT/option-candidates")
            # Second request must be served from cache — no additional DB calls
            assert db_mock.call_count == count_after_first, (
                "Second request should not hit DB (cache hit)"
            )

    def test_thesis_direction_and_conviction_in_response(self):
        """The response must include thesis_direction and thesis_conviction."""
        thesis_row = {
            "ticker": "AAPL", "direction": "BEAR", "conviction": 2,
            "entry_low": 145.0, "entry_high": 150.0, "target_1": 130.0,
            "target_2": None, "stop_loss": 155.0, "time_horizon": None,
            "signals_json": None, "current_price": 148.0,
        }
        mock_conn = _make_mock_db_conn(thesis_row=thesis_row)
        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  return_value=_make_suppressed_result("AAPL", "NEUTRAL direction")),
        ):
            resp = client.get("/api/ticker/AAPL/option-candidates")

        body = resp.json()
        assert body["thesis_direction"] == "BEAR"
        assert body["thesis_conviction"] == 2

    def test_non_finite_underlying_price_is_sanitized(self):
        """NaN values in the engine result must be converted to null before JSON serialization."""
        import math
        from utils.option_candidates import CandidateResult

        thesis_row = {
            "ticker": "DDOG", "direction": "BULL", "conviction": 4,
            "entry_low": 168.0, "entry_high": 178.0, "target_1": 216.0,
            "target_2": 238.0, "stop_loss": 147.0, "time_horizon": "2-4 weeks",
            "signals_json": None, "current_price": 250.0,
        }
        mock_conn = _make_mock_db_conn(thesis_row=thesis_row)
        engine_result = CandidateResult(
            ticker="DDOG",
            generated_at="2026-06-05T07:21:00",
            suppressed=False,
            suppression_reason="No contracts passed quality filters",
            candidates=[],
            rejection_reasons=["C 95.0 2026-06-26: no valid mid price"],
            underlying_price=math.nan,
            chain_source="yfinance",
        )

        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates", return_value=engine_result),
        ):
            resp = client.get("/api/ticker/DDOG/option-candidates")

        assert resp.status_code == 200
        body = resp.json()
        assert body["underlying_price"] is None
        assert body["suppressed"] is False

    def test_thesis_id_and_date_extracted_from_row(self):
        """thesis_id, thesis_date, and signal_agreement must be populated from the DB row.
        thesis_cache.id is a real BIGSERIAL in the live DB even though schema.sql omits it."""
        thesis_row = {
            "id": 42, "date": "2026-05-29",
            "ticker": "AAPL", "direction": "BULL", "conviction": 3,
            "entry_low": 145.0, "entry_high": 150.0, "target_1": 165.0,
            "target_2": None, "stop_loss": 140.0, "time_horizon": "2-4 weeks",
            "signal_agreement_score": 0.75,
            "signals_json": None,
        }
        mock_conn = _make_mock_db_conn(thesis_row=thesis_row)
        persisted: dict = {}

        def _capture_persist(result, thesis_id=None, thesis_context=None):
            persisted["thesis_id"] = thesis_id
            persisted["thesis_date"] = (thesis_context or {}).get("thesis_date")
            persisted["signal_agreement"] = (thesis_context or {}).get("signal_agreement")

        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  return_value=_make_candidate_result("AAPL")),
            patch("utils.supabase_persist.save_option_candidate_snapshot",
                  side_effect=_capture_persist),
        ):
            resp = client.get("/api/ticker/AAPL/option-candidates")

        assert resp.status_code == 200
        # Give fire-and-forget executor a moment to run
        import time; time.sleep(0.05)
        assert persisted.get("thesis_id") == 42, "thesis_id must come from the DB row id field"
        assert persisted.get("thesis_date") == "2026-05-29", "thesis_date must be set"
        assert persisted.get("signal_agreement") == pytest.approx(0.75)


# ==============================================================================
# TRD-059 — Funnel metrics API endpoints
# ==============================================================================

class TestFunnelEndpoints:
    """Tests for /api/funnel/summary and /api/funnel/history."""

    def test_funnel_summary_no_data_returns_200(self):
        """funnel/summary must return 200 even when no DB rows exist."""
        with patch("utils.supabase_persist.fetch_funnel_metrics", return_value=[]):
            resp = client.get("/api/funnel/summary")
        assert resp.status_code == 200

    def test_funnel_summary_returns_row(self):
        """funnel/summary returns the most recent row when data is available."""
        fake_row = {
            "run_date": "2026-06-07",
            "raw_universe_count": 1200,
            "prescreened_count": 200,
            "ai_selected_count": 5,
            "active_thesis_count": 3,
        }
        with patch("utils.supabase_persist.fetch_funnel_metrics", return_value=[fake_row]):
            resp = client.get("/api/funnel/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("run_date") == "2026-06-07"
        assert body.get("raw_universe_count") == 1200

    def test_funnel_history_returns_rows_list(self):
        """funnel/history wraps rows in a dict with 'rows' and 'count'."""
        fake_rows = [
            {"run_date": "2026-06-07", "ai_selected_count": 5},
            {"run_date": "2026-06-06", "ai_selected_count": 4},
        ]
        with patch("utils.supabase_persist.fetch_funnel_metrics", return_value=fake_rows):
            resp = client.get("/api/funnel/history?days=7")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert len(body["rows"]) == 2

    def test_funnel_history_empty_returns_200(self):
        """funnel/history must return 200 with empty rows when no data."""
        with patch("utils.supabase_persist.fetch_funnel_metrics", return_value=[]):
            resp = client.get("/api/funnel/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rows"] == []
        assert body["count"] == 0

    def test_funnel_summary_passes_through_reason_dicts(self):
        """excluded_by_source and suppression_reasons must be present in summary response."""
        fake_row = {
            "run_date": "2026-06-07",
            "raw_universe_count": 1200,
            "ai_selected_count": 7,
            "active_thesis_count": 3,
            "excluded_by_source":  {"hard_excluded": 12, "lane_excluded": 340},
            "suppression_reasons": {
                "low_conviction": 1,
                "pre_earnings_hold": 1,
                "bear_below_threshold": 1,
                "no_geometry": 1,
                "neutral_direction": 1,
                "no_conviction": 0,
                "governance_quarantine": 0,
            },
        }
        with patch("utils.supabase_persist.fetch_funnel_metrics", return_value=[fake_row]):
            resp = client.get("/api/funnel/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("excluded_by_source") == {"hard_excluded": 12, "lane_excluded": 340}
        sr = body.get("suppression_reasons", {})
        assert sr.get("low_conviction") == 1
        assert sr.get("pre_earnings_hold") == 1
        assert sr.get("no_geometry") == 1
        assert sr.get("neutral_direction") == 1

    def test_funnel_summary_passes_through_attribution_fields(self):
        """Source/lane attribution fields must be present and correctly serialised
        in the funnel/summary response (TRD-075)."""
        fake_row = {
            "run_date": "2026-06-07",
            "raw_universe_count": 1500,
            "prescreened_count": 160,
            "ai_selected_count": 6,
            "active_thesis_count": 4,
            "candidates_by_lane":   {"execution_core": 40, "execution_high_beta": 20, "research_broad": 100},
            "candidates_by_source": {"sp500": 90, "nasdaq_broad": 130, "russell1000": 60},
            "broad_source_only_candidates": 45,
            "ai_selected_by_lane":   {"execution_core": 4, "research_broad": 2},
            "ai_selected_by_source": {"sp500": 4, "nasdaq_broad": 3},
            "broad_source_only_ai_selected": 1,
            "broad_source_health": {
                "nasdaq_broad": {
                    "source": "nasdaq_broad",
                    "fetch_mode": "live_fetch",
                    "raw_rows": 4800,
                    "eligible_count": 3900,
                    "warning": None,
                    "fetched_at": "2026-06-07T19:00:00Z",
                },
                "nyse_listed": {
                    "source": "nyse_listed",
                    "fetch_mode": "stale_cache",
                    "raw_rows": None,
                    "eligible_count": 4100,
                    "warning": "network_unavailable: serving stale cache",
                    "fetched_at": "2026-06-07T19:00:05Z",
                },
            },
        }
        with patch("utils.supabase_persist.fetch_funnel_metrics", return_value=[fake_row]):
            resp = client.get("/api/funnel/summary")
        assert resp.status_code == 200
        body = resp.json()

        cbl = body.get("candidates_by_lane")
        assert isinstance(cbl, dict), "candidates_by_lane must be a dict"
        assert cbl.get("execution_core") == 40
        assert cbl.get("research_broad") == 100

        cbs = body.get("candidates_by_source")
        assert isinstance(cbs, dict), "candidates_by_source must be a dict"
        assert cbs.get("nasdaq_broad") == 130

        assert body.get("broad_source_only_candidates") == 45

        asl = body.get("ai_selected_by_lane")
        assert isinstance(asl, dict), "ai_selected_by_lane must be a dict"
        assert asl.get("execution_core") == 4

        ass_ = body.get("ai_selected_by_source")
        assert isinstance(ass_, dict), "ai_selected_by_source must be a dict"
        assert ass_.get("nasdaq_broad") == 3

        assert body.get("broad_source_only_ai_selected") == 1

        # Broad-source health passthrough (TRD-056 hardening)
        bsh = body.get("broad_source_health")
        assert isinstance(bsh, dict), "broad_source_health must be a dict"
        nb = bsh.get("nasdaq_broad")
        assert nb is not None, "nasdaq_broad entry must be present"
        assert nb["fetch_mode"] == "live_fetch"
        assert nb["eligible_count"] == 3900
        assert nb["raw_rows"] == 4800
        assert nb["warning"] is None
        nl = bsh.get("nyse_listed")
        assert nl is not None, "nyse_listed entry must be present"
        assert nl["fetch_mode"] == "stale_cache"
        assert nl["warning"] == "network_unavailable: serving stale cache"


# ==============================================================================
# TRD-068 — Ticker governance API endpoints
# ==============================================================================

class TestGovernanceEndpoints:
    """Tests for /api/governance CRUD endpoints."""

    def test_get_governance_returns_list(self):
        """GET /api/governance returns governance entries."""
        fake_entries = [
            {"ticker": "MEME", "governance_state": "QUARANTINE", "reason": "delisted risk",
             "notes": None, "set_by": "pm", "set_at": "2026-06-01T00:00:00", "updated_at": None},
        ]
        with patch("utils.supabase_persist.fetch_ticker_governance_full", return_value=fake_entries):
            resp = client.get("/api/governance")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["governance"][0]["ticker"] == "MEME"

    def test_set_governance_valid_state(self):
        """POST /api/governance/{ticker} with valid state returns ok=True."""
        with patch("utils.supabase_persist.set_ticker_governance", return_value=True):
            resp = client.post("/api/governance/AAPL", json={"governance_state": "PROBATION", "reason": "testing"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_set_governance_invalid_state_returns_400(self):
        """POST with invalid governance_state returns HTTP 400."""
        resp = client.post("/api/governance/AAPL", json={"governance_state": "INVALID"})
        assert resp.status_code == 400

    def test_delete_governance_returns_ok(self):
        """DELETE /api/governance/{ticker} returns ok=True."""
        with patch("utils.supabase_persist.remove_ticker_governance", return_value=True):
            resp = client.delete("/api/governance/AAPL")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ==============================================================================
# TRD-077: Outcome Attribution endpoint
# ==============================================================================

class TestOutcomeAttributionEndpoint:
    """Tests for GET /api/outcome/attribution."""

    def _fake_attribution(self, total=20):
        return {
            "by_source": [
                {"label": "sp500",        "resolved": 15, "correct_count": 10, "directional_accuracy": 0.6667, "avg_return_30d": 0.08},
                {"label": "nasdaq_broad", "resolved": 5,  "correct_count": 2,  "directional_accuracy": 0.4,    "avg_return_30d": -0.02},
            ],
            "by_lane": [
                {"label": "execution_core", "resolved": 12, "correct_count": 9, "directional_accuracy": 0.75,  "avg_return_30d": 0.10},
                {"label": "research_broad", "resolved": 8,  "correct_count": 3, "directional_accuracy": 0.375, "avg_return_30d": -0.01},
            ],
            "by_direction": [
                {"label": "BULL", "resolved": 18, "correct_count": 12, "directional_accuracy": 0.6667, "avg_return_30d": 0.07},
                {"label": "BEAR", "resolved": 2,  "correct_count": 0,  "directional_accuracy": 0.0,    "avg_return_30d": -0.05},
            ],
            "by_governance_state": [
                {"label": "A_LIST",   "resolved": 10, "correct_count": 8, "directional_accuracy": 0.8,    "avg_return_30d": 0.12},
                {"label": "STANDARD", "resolved": 8,  "correct_count": 4, "directional_accuracy": 0.5,    "avg_return_30d": 0.03},
                {"label": "unknown",  "resolved": 2,  "correct_count": 0, "directional_accuracy": 0.0,    "avg_return_30d": None},
            ],
            "broad_source_only_summary": {
                "broad":     {"label": "broad_source_only", "resolved": 5,  "correct_count": 2,  "directional_accuracy": 0.4,    "avg_return_30d": -0.02},
                "non_broad": {"label": "quality_index",     "resolved": 15, "correct_count": 10, "directional_accuracy": 0.6667, "avg_return_30d": 0.08},
            },
            "days": 90,
            "total_resolved": total,
        }

    def test_outcome_attribution_returns_200(self):
        """GET /api/outcome/attribution returns 200 with required shape."""
        _cache._store.clear()
        with patch("utils.supabase_persist.fetch_outcome_attribution",
                   return_value=self._fake_attribution()):
            resp = client.get("/api/outcome/attribution")
        assert resp.status_code == 200
        body = resp.json()
        assert "by_source" in body
        assert "by_lane" in body
        assert "by_direction" in body
        assert "by_governance_state" in body
        assert "broad_source_only_summary" in body
        assert "total_resolved" in body
        assert "days" in body

    def test_outcome_attribution_by_governance_state_shape(self):
        """by_governance_state buckets have required fields and expected labels."""
        _cache._store.clear()
        with patch("utils.supabase_persist.fetch_outcome_attribution",
                   return_value=self._fake_attribution()):
            resp = client.get("/api/outcome/attribution")
        body = resp.json()
        gov_buckets = body["by_governance_state"]
        assert isinstance(gov_buckets, list)
        assert len(gov_buckets) > 0
        labels = {b["label"] for b in gov_buckets}
        assert "A_LIST" in labels
        for bucket in gov_buckets:
            assert "label" in bucket
            assert "resolved" in bucket
            assert "correct_count" in bucket
            assert "directional_accuracy" in bucket

    def test_outcome_attribution_buckets_have_required_fields(self):
        """Each bucket has label, resolved, correct_count, directional_accuracy."""
        _cache._store.clear()
        with patch("utils.supabase_persist.fetch_outcome_attribution",
                   return_value=self._fake_attribution()):
            resp = client.get("/api/outcome/attribution")
        body = resp.json()
        for bucket in body["by_source"] + body["by_lane"]:
            assert "label" in bucket
            assert "resolved" in bucket
            assert "correct_count" in bucket
            assert "directional_accuracy" in bucket

    def test_outcome_attribution_days_param(self):
        """?days=30 is forwarded to fetch_outcome_attribution."""
        _cache._store.clear()
        received_days = []

        def _capture(days=90):
            received_days.append(days)
            return self._fake_attribution(total=5)

        with patch("utils.supabase_persist.fetch_outcome_attribution", side_effect=_capture):
            client.get("/api/outcome/attribution?days=30")

        assert received_days == [30], f"Expected days=30, got {received_days}"

    def test_outcome_attribution_cached_on_second_call(self):
        """Second call within TTL returns cached result without re-querying."""
        _cache._store.clear()
        call_count = [0]

        def _counter(days=90):
            call_count[0] += 1
            return self._fake_attribution()

        with patch("utils.supabase_persist.fetch_outcome_attribution", side_effect=_counter):
            client.get("/api/outcome/attribution")
            client.get("/api/outcome/attribution")

        assert call_count[0] == 1, "Expected only one DB call due to cache hit"

    def test_outcome_attribution_db_error_returns_empty(self):
        """DB error returns empty structure with 200 status (degrades gracefully)."""
        _cache._store.clear()
        with patch("utils.supabase_persist.fetch_outcome_attribution",
                   side_effect=RuntimeError("db down")):
            resp = client.get("/api/outcome/attribution")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_resolved"] == 0
        assert body["by_source"] == []


class TestGovernanceRecommendationsEndpoint:
    """Tests for GET /api/governance/recommendations."""

    def _fake_recs(self, days=90):
        entry = lambda ticker, rec: {
            "ticker": ticker, "current_state": "STANDARD",
            "recommendation": rec, "reason_summary": "test",
            "resolved": 8, "correct_count": 6,
            "directional_accuracy": 0.75, "avg_return_30d": 0.05,
            "days": days,
        }
        return {
            "promote_candidates":   [entry("AAPL", "promote_to_a_list")],
            "probation_candidates": [entry("GME",  "move_to_probation")],
            "quarantine_candidates":[entry("MEME", "consider_quarantine")],
            "keep_current_state":   [entry("MSFT", "keep_current_state")],
            "insufficient_sample":  [],
            "summary": {
                "total_tickers": 4,
                "by_recommendation": {
                    "promote_to_a_list": 1, "move_to_probation": 1,
                    "consider_quarantine": 1, "keep_current_state": 1,
                    "insufficient_sample": 0,
                },
            },
            "thresholds_used": {
                "min_sample": 5, "promote_min_sample": 8,
                "promote_min_accuracy": 0.70, "promote_min_return": 0.03,
                "probation_max_accuracy": 0.45, "quarantine_max_accuracy": 0.35,
            },
            "days": days,
        }

    def test_returns_200_with_required_shape(self):
        """GET /api/governance/recommendations returns 200 with all required top-level keys."""
        _cache._store.clear()
        with patch("utils.supabase_persist.fetch_governance_recommendations",
                   return_value=self._fake_recs()):
            resp = client.get("/api/governance/recommendations")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("promote_candidates", "probation_candidates", "quarantine_candidates",
                    "keep_current_state", "insufficient_sample", "summary",
                    "thresholds_used", "days"):
            assert key in body, f"Missing key: {key}"

    def test_entry_has_required_fields(self):
        """Each candidate entry contains ticker, recommendation, resolved, directional_accuracy."""
        _cache._store.clear()
        with patch("utils.supabase_persist.fetch_governance_recommendations",
                   return_value=self._fake_recs()):
            resp = client.get("/api/governance/recommendations")
        body = resp.json()
        entry = body["promote_candidates"][0]
        for field in ("ticker", "current_state", "recommendation", "reason_summary",
                      "resolved", "correct_count", "directional_accuracy"):
            assert field in entry, f"Missing entry field: {field}"

    def test_days_param_forwarded(self):
        """?days=30 is passed through to fetch_governance_recommendations."""
        _cache._store.clear()
        received = []

        def _cap(days=90):
            received.append(days)
            return self._fake_recs(days)

        with patch("utils.supabase_persist.fetch_governance_recommendations", side_effect=_cap):
            client.get("/api/governance/recommendations?days=30")

        assert received == [30]

    def test_cached_on_second_call(self):
        """Second call within TTL returns cached data without re-querying."""
        _cache._store.clear()
        call_count = [0]

        def _counter(days=90):
            call_count[0] += 1
            return self._fake_recs()

        with patch("utils.supabase_persist.fetch_governance_recommendations", side_effect=_counter):
            client.get("/api/governance/recommendations")
            client.get("/api/governance/recommendations")

        assert call_count[0] == 1

    def test_db_error_degrades_gracefully(self):
        """DB error returns empty structure with 200 status."""
        _cache._store.clear()
        with patch("utils.supabase_persist.fetch_governance_recommendations",
                   side_effect=RuntimeError("db down")):
            resp = client.get("/api/governance/recommendations")
        assert resp.status_code == 200
        body = resp.json()
        assert body["promote_candidates"] == []
        assert body["summary"]["total_tickers"] == 0


# ==============================================================================
# QA-002: _db_connect() failure handling across endpoint types
# ==============================================================================

class TestDbConnectFailure:
    """QA-002: All covered endpoints must return a controlled response (not 500)
    when _db_connect() raises — matching the fix applied in this sprint."""

    def test_portfolio_positions_db_failure(self):
        with _db_conn_raises():
            resp = client.get("/api/portfolio/positions")
        assert resp.status_code == 200
        assert resp.json()["data_available"] is False
        assert "database" in resp.json().get("reason", "").lower()

    def test_portfolio_sparklines_db_failure(self):
        with _db_conn_raises():
            resp = client.get("/api/portfolio/sparklines")
        assert resp.status_code == 200
        # Returns {} on DB failure — empty but not a 500
        assert isinstance(resp.json(), dict)

    def test_get_trades_db_failure(self):
        with _db_conn_raises():
            resp = client.get("/api/portfolio/trades")
        assert resp.status_code == 200
        assert resp.json()["data_available"] is False

    def test_add_position_db_failure(self):
        with _db_conn_raises():
            resp = client.post("/api/portfolio/positions", json={
                "ticker": "AAPL", "entry_price": 150.0, "size_eur": 1000.0
            })
        assert resp.status_code == 503
        assert "database" in resp.json()["detail"].lower()

    def test_sell_position_db_failure(self):
        with _db_conn_raises():
            resp = client.post("/api/portfolio/positions/AAPL/sell", json={
                "sell_price": 160.0
            })
        assert resp.status_code == 503
        assert "database" in resp.json()["detail"].lower()

    def test_close_position_db_failure(self):
        with _db_conn_raises():
            resp = client.delete("/api/portfolio/positions/AAPL")
        assert resp.status_code == 503
        assert "database" in resp.json()["detail"].lower()

    def test_get_cash_db_failure(self):
        with _db_conn_raises():
            resp = client.get("/api/portfolio/cash")
        assert resp.status_code == 503
        assert "database" in resp.json()["detail"].lower()

    def test_update_cash_db_failure(self):
        with _db_conn_raises():
            resp = client.post("/api/portfolio/cash", json={"action": "set", "amount": 5000.0})
        assert resp.status_code == 503
        assert "database" in resp.json()["detail"].lower()

    def test_signals_outcomes_db_failure(self):
        _cache._store.clear()
        with _db_conn_raises():
            resp = client.get("/api/signals/outcomes")
        assert resp.status_code == 200
        assert resp.json()["data_available"] is False
        assert "database" in resp.json().get("reason", "").lower()

    def test_signals_accuracy_db_failure(self):
        _cache._store.clear()
        with _db_conn_raises():
            resp = client.get("/api/signals/accuracy")
        assert resp.status_code == 200
        assert resp.json()["data_available"] is False
        assert "database" in resp.json().get("reason", "").lower()

    def test_screeners_options_db_failure(self):
        _cache._store.clear()
        with _db_conn_raises():
            resp = client.get("/api/screeners/options")
        assert resp.status_code == 200
        assert resp.json()["data_available"] is False

    def test_ticker_analogs_db_failure(self):
        _cache._store.clear()
        with _db_conn_raises():
            resp = client.get("/api/ticker/AAPL/analogs")
        assert resp.status_code == 200
        assert resp.json()["data_available"] is False
