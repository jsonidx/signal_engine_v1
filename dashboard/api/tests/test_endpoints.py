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
from unittest.mock import MagicMock, patch

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
    """Must return 200 with data_available=False, never 404 or 500."""
    with patch("dashboard.api.main.PAPER_TRADES_DB", Path("/nonexistent/paper_trades.db")):
        resp = client.get("/api/portfolio/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is False


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
    with patch("dashboard.api.main.PAPER_TRADES_DB", Path("/nope/paper_trades.db")):
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
    with patch.multiple(
        "dashboard.api.main",
        AI_QUANT_DB=tmp_data / "ai_quant_cache.db",
        SIGNALS_DIR=tmp_data / "signals_output",
        DATA_DIR=tmp_data / "data",
    ):
        resp = client.get("/api/signals/heatmap")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    assert body["count"] >= 1
    for row in body["data"]:
        for module, score in row["modules"].items():
            assert -1.0 <= score <= 1.0, (
                f"module={module} score={score} out of [-1,1] for {row['ticker']}"
            )


def test_signals_heatmap_missing_db():
    with patch("dashboard.api.main.AI_QUANT_DB", Path("/nope/ai_quant_cache.db")):
        resp = client.get("/api/signals/heatmap")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


# ==============================================================================
# SIGNALS TICKER
# ==============================================================================

def test_signals_ticker_found(tmp_data):
    with patch.multiple(
        "dashboard.api.main",
        AI_QUANT_DB=tmp_data / "ai_quant_cache.db",
    ):
        resp = client.get("/api/signals/ticker/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    assert body["ticker"] == "AAPL"
    assert "ai_thesis" in body
    assert "entry_zone" in body
    assert "options_heat" in body


def test_signals_ticker_not_found(tmp_data):
    with patch("dashboard.api.main.AI_QUANT_DB", tmp_data / "ai_quant_cache.db"):
        resp = client.get("/api/signals/ticker/ZZZZ")
    assert resp.status_code == 200
    assert resp.json()["data_available"] is False


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


def test_screeners_options_from_cache(tmp_data):
    with patch.multiple(
        "dashboard.api.main",
        AI_QUANT_DB=tmp_data / "ai_quant_cache.db",
    ):
        resp = client.get("/api/screeners/options?min_heat=50")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_available"] is True
    # AAPL has heat_score=72.5 so should appear
    tickers = [r["ticker"] for r in body["data"]]
    assert "AAPL" in tickers


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
    with patch("dashboard.api.main.SIGNALS_DIR", Path("/nope")):
        with patch("dashboard.api.main.DATA_DIR", Path("/nope")):
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
