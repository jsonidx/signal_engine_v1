"""
tests/test_api_crypto.py
========================
Integration-style tests for GET /api/screeners/crypto.

Uses FastAPI TestClient with:
  - fx_rates pre-stubbed (no network)
  - _latest_screener_file monkeypatched to control file discovery
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard" / "api"))

# ── pre-stub fx_rates so tests never hit the network ─────────────────────────
if "fx_rates" not in sys.modules:
    _fx_stub = MagicMock()
    # 1 USD → ~0.9174 EUR  (rate = 1.09 USD per EUR)
    _fx_stub.convert_to_eur.side_effect = lambda amount, currency="USD": round(amount / 1.09, 4)
    sys.modules["fx_rates"] = _fx_stub

from fastapi.testclient import TestClient  # noqa: E402
import main as _api_main                   # noqa: E402
from main import app                       # noqa: E402

client = TestClient(app, raise_server_exceptions=False)

# ── sample CSV data ───────────────────────────────────────────────────────────
_FIELDNAMES = [
    "ticker", "price", "ema_fast", "ema_slow", "ema_trend",
    "trend_score", "momentum_score", "rsi", "realized_vol_ann",
    "vol_regime", "raw_signal", "adjusted_signal", "action", "rank",
]

_SAMPLE_ROWS = [
    dict(ticker="BTC-USD", price="70494.12", ema_fast="70000", ema_slow="72000",
         ema_trend="86000", trend_score="-0.333", momentum_score="-0.029",
         rsi="50.5", realized_vol_ann="0.447", vol_regime="NORMAL",
         raw_signal="-0.252", adjusted_signal="-0.252", action="REDUCE", rank="1"),
    dict(ticker="ETH-USD", price="2131.06", ema_fast="2100", ema_slow="2200",
         ema_trend="2800", trend_score="-0.333", momentum_score="-0.028",
         rsi="51.3", realized_vol_ann="0.605", vol_regime="NORMAL",
         raw_signal="-0.251", adjusted_signal="-0.251", action="REDUCE", rank="2"),
    dict(ticker="LTC-USD", price="55.25", ema_fast="55.3", ema_slow="57.6",
         ema_trend="75.9", trend_score="-1.0", momentum_score="-0.024",
         rsi="49.3", realized_vol_ann="0.467", vol_regime="NORMAL",
         raw_signal="-0.511", adjusted_signal="-0.511", action="SELL / NO POSITION",
         rank="3"),
    dict(ticker="SOL-USD", price="89.83", ema_fast="88.5", ema_slow="93.1",
         ema_trend="127.9", trend_score="-0.333", momentum_score="-0.019",
         rsi="51.6", realized_vol_ann="0.576", vol_regime="NORMAL",
         raw_signal="-0.237", adjusted_signal="-0.237", action="HOLD", rank="4"),
]


def _write_csv(rows: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline=""
    )
    writer = csv.DictWriter(tmp, fieldnames=_FIELDNAMES)
    writer.writeheader()
    writer.writerows(rows)
    tmp.close()
    return Path(tmp.name)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_cache():
    """Wipe the in-process TTL cache before every test."""
    _api_main._cache._store.clear()
    yield
    _api_main._cache._store.clear()


# ── tests ─────────────────────────────────────────────────────────────────────

class TestCryptoEndpointFound:
    """CSV exists → 200 response with correct structure."""

    def setup_method(self):
        self.csv_path = _write_csv(_SAMPLE_ROWS)

    def teardown_method(self):
        os.unlink(self.csv_path)

    def test_returns_200(self):
        with patch.object(_api_main, "_latest_screener_file", return_value=self.csv_path):
            resp = client.get("/api/screeners/crypto")
        assert resp.status_code == 200

    def test_response_has_required_top_level_keys(self):
        with patch.object(_api_main, "_latest_screener_file", return_value=self.csv_path):
            data = client.get("/api/screeners/crypto").json()
        assert "generated_at" in data
        assert "btc_200ma_signal" in data
        assert "tickers" in data

    def test_btc_200ma_signal_is_cash_when_adjusted_signal_negative(self):
        # All sample rows have negative adjusted_signal for BTC → CASH
        with patch.object(_api_main, "_latest_screener_file", return_value=self.csv_path):
            data = client.get("/api/screeners/crypto").json()
        assert data["btc_200ma_signal"] == "CASH"

    def test_btc_200ma_signal_is_active_when_adjusted_signal_positive(self):
        rows = [r.copy() for r in _SAMPLE_ROWS]
        rows[0]["adjusted_signal"] = "0.30"  # BTC-USD → positive
        csv_path = _write_csv(rows)
        try:
            with patch.object(_api_main, "_latest_screener_file", return_value=csv_path):
                data = client.get("/api/screeners/crypto").json()
            assert data["btc_200ma_signal"] == "ACTIVE"
        finally:
            os.unlink(csv_path)

    def test_btc_is_always_first_ticker(self):
        with patch.object(_api_main, "_latest_screener_file", return_value=self.csv_path):
            data = client.get("/api/screeners/crypto").json()
        assert data["tickers"][0]["ticker"] == "BTC-USD"

    def test_tickers_sorted_by_signal_score_desc_after_btc(self):
        with patch.object(_api_main, "_latest_screener_file", return_value=self.csv_path):
            data = client.get("/api/screeners/crypto").json()
        scores = [t["signal_score"] for t in data["tickers"][1:]]
        assert scores == sorted(scores, reverse=True)

    def test_ticker_row_has_all_required_fields(self):
        with patch.object(_api_main, "_latest_screener_file", return_value=self.csv_path):
            data = client.get("/api/screeners/crypto").json()
        required = {"ticker", "price_usd", "price_eur", "signal_score",
                    "trend", "momentum", "rsi", "vol_pct", "action"}
        for t in data["tickers"]:
            assert required <= t.keys(), f"missing fields in {t['ticker']}"

    def test_price_eur_is_usd_divided_by_rate(self):
        with patch.object(_api_main, "_latest_screener_file", return_value=self.csv_path):
            data = client.get("/api/screeners/crypto").json()
        btc = next(t for t in data["tickers"] if t["ticker"] == "BTC-USD")
        expected_eur = round(70494.12 / 1.09, 4)
        assert abs(btc["price_eur"] - expected_eur) < 0.01

    def test_sell_no_position_normalised_to_sell(self):
        with patch.object(_api_main, "_latest_screener_file", return_value=self.csv_path):
            data = client.get("/api/screeners/crypto").json()
        ltc = next(t for t in data["tickers"] if t["ticker"] == "LTC-USD")
        assert ltc["action"] == "SELL"

    def test_signal_score_in_0_to_100_range(self):
        with patch.object(_api_main, "_latest_screener_file", return_value=self.csv_path):
            data = client.get("/api/screeners/crypto").json()
        for t in data["tickers"]:
            assert 0.0 <= t["signal_score"] <= 100.0, f"out of range for {t['ticker']}"

    def test_generated_at_is_iso_string(self):
        from datetime import datetime
        with patch.object(_api_main, "_latest_screener_file", return_value=self.csv_path):
            data = client.get("/api/screeners/crypto").json()
        # Should parse without error
        datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00"))


class TestCryptoEndpointMissing:
    """CSV missing → 404 with correct error envelope."""

    def test_returns_404(self):
        with patch.object(_api_main, "_latest_screener_file", return_value=None):
            resp = client.get("/api/screeners/crypto")
        assert resp.status_code == 404

    def test_404_body_has_error_key(self):
        with patch.object(_api_main, "_latest_screener_file", return_value=None):
            body = client.get("/api/screeners/crypto").json()
        assert "error" in body
        assert "crypto signals" in body["error"].lower()
