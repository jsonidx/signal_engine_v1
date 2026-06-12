"""
tests/test_13f_ingestion.py  —  TRD-083

Unit tests for the 13F ingestion diff logic and XML parsing.
No network calls, no DB — all external interactions are mocked.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.fetch_13f import (
    _parse_infotable_xml,
    compute_diffs,
    build_alert,
)


# ── XML parsing ───────────────────────────────────────────────────────────────

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>BLOOM ENERGY CORP</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>093712107</cusip>
    <value>878700</value>
    <shrsOrPrnAmt>
      <sshPrnamt>50000000</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
  </infoTable>
  <infoTable>
    <nameOfIssuer>NVIDIA CORP</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>67066G104</cusip>
    <value>1600000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>4000000</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <putCall>Put</putCall>
    <investmentDiscretion>SOLE</investmentDiscretion>
  </infoTable>
</informationTable>"""

SAMPLE_XML_NO_NS = """<?xml version="1.0"?>
<informationTable>
  <infoTable>
    <nameOfIssuer>COREWEAVE INC</nameOfIssuer>
    <cusip>21874X108</cusip>
    <value>500000</value>
    <sshPrnamt>10000000</sshPrnamt>
  </infoTable>
</informationTable>"""


class TestParseInfotableXml:
    def test_parses_two_holdings(self):
        rows = _parse_infotable_xml(SAMPLE_XML)
        assert len(rows) == 2

    def test_equity_row_fields(self):
        rows = _parse_infotable_xml(SAMPLE_XML)
        equity = next(r for r in rows if r["put_call"] is None)
        assert equity["name_of_issuer"] == "BLOOM ENERGY CORP"
        assert equity["cusip"] == "093712107"
        assert equity["value_usd"] == 878700
        assert equity["shares"] == 50_000_000
        assert equity["put_call"] is None

    def test_put_option_row(self):
        rows = _parse_infotable_xml(SAMPLE_XML)
        put = next(r for r in rows if r["put_call"] == "Put")
        assert put["name_of_issuer"] == "NVIDIA CORP"
        assert put["cusip"] == "67066G104"
        assert put["put_call"] == "Put"

    def test_no_namespace_xml(self):
        rows = _parse_infotable_xml(SAMPLE_XML_NO_NS)
        assert len(rows) == 1
        assert rows[0]["name_of_issuer"] == "COREWEAVE INC"

    def test_bad_xml_returns_empty(self):
        rows = _parse_infotable_xml("<not valid xml <<<")
        assert rows == []

    def test_empty_xml_returns_empty(self):
        rows = _parse_infotable_xml("<informationTable/>")
        assert rows == []


# ── Diff computation ──────────────────────────────────────────────────────────

def _make_conn(prior_rows: list[dict], prior_period: date | None = None):
    """Create a mock DB connection returning the given prior rows."""
    conn = MagicMock()
    cur  = MagicMock()
    conn.cursor.return_value = cur

    fetchone_sequence = [
        {"period": prior_period} if prior_period else None,
    ]
    fetchall_returns = [prior_rows] if prior_rows else [[]]

    cur.fetchone.side_effect = fetchone_sequence
    cur.fetchall.side_effect = fetchall_returns
    return conn


class TestComputeDiffs:
    def test_all_new_on_first_ingestion(self):
        conn = _make_conn(prior_rows=[], prior_period=None)
        rows = [
            {"cusip": "AAA", "put_call": None, "shares": 1000, "value_usd": 500},
            {"cusip": "BBB", "put_call": "Put", "shares": 200, "value_usd": 100},
        ]
        enriched = compute_diffs("test-fund", date(2026, 3, 31), rows, conn)
        assert all(r["change_type"] == "new" for r in enriched)
        assert all(r["shares_delta"] is None for r in enriched)

    def test_added_when_shares_increased(self):
        prior = [{"cusip": "AAA", "put_call": None, "shares": 500, "value_usd": 250}]
        conn  = _make_conn(prior_rows=prior, prior_period=date(2025, 12, 31))
        rows  = [{"cusip": "AAA", "put_call": None, "shares": 1000, "value_usd": 500}]
        enriched = compute_diffs("test-fund", date(2026, 3, 31), rows, conn)
        r = enriched[0]
        assert r["change_type"] == "added"
        assert r["shares_delta"] == 500
        assert r["value_delta_usd"] == 250

    def test_trimmed_when_shares_decreased(self):
        prior = [{"cusip": "AAA", "put_call": None, "shares": 1000, "value_usd": 500}]
        conn  = _make_conn(prior_rows=prior, prior_period=date(2025, 12, 31))
        rows  = [{"cusip": "AAA", "put_call": None, "shares": 600, "value_usd": 300}]
        enriched = compute_diffs("test-fund", date(2026, 3, 31), rows, conn)
        r = enriched[0]
        assert r["change_type"] == "trimmed"
        assert r["shares_delta"] == -400

    def test_unchanged_when_shares_same(self):
        prior = [{"cusip": "AAA", "put_call": None, "shares": 1000, "value_usd": 500}]
        conn  = _make_conn(prior_rows=prior, prior_period=date(2025, 12, 31))
        rows  = [{"cusip": "AAA", "put_call": None, "shares": 1000, "value_usd": 510}]
        enriched = compute_diffs("test-fund", date(2026, 3, 31), rows, conn)
        assert enriched[0]["change_type"] == "unchanged"

    def test_closed_position_appended_for_missing_holding(self):
        prior = [
            {"cusip": "AAA", "put_call": None, "shares": 1000, "value_usd": 500},
            {"cusip": "BBB", "put_call": None, "shares": 200,  "value_usd": 100},
        ]
        conn = _make_conn(prior_rows=prior, prior_period=date(2025, 12, 31))
        rows = [{"cusip": "AAA", "put_call": None, "shares": 1000, "value_usd": 500}]
        enriched = compute_diffs("test-fund", date(2026, 3, 31), rows, conn)
        closed = [r for r in enriched if r.get("change_type") == "closed"]
        assert len(closed) == 1
        assert closed[0]["cusip"] == "BBB"
        assert closed[0]["shares"] == 0
        assert closed[0]["shares_delta"] == -200

    def test_put_and_equity_same_cusip_treated_separately(self):
        prior = [
            {"cusip": "AAA", "put_call": None,  "shares": 500, "value_usd": 250},
            {"cusip": "AAA", "put_call": "Put",  "shares": 100, "value_usd": 50},
        ]
        conn = _make_conn(prior_rows=prior, prior_period=date(2025, 12, 31))
        rows = [
            {"cusip": "AAA", "put_call": None,  "shares": 700, "value_usd": 350},
            {"cusip": "AAA", "put_call": "Put",  "shares": 100, "value_usd": 50},
        ]
        enriched = compute_diffs("test-fund", date(2026, 3, 31), rows, conn)
        eq  = next(r for r in enriched if r["put_call"] is None)
        put = next(r for r in enriched if r["put_call"] == "Put")
        assert eq["change_type"] == "added"
        assert put["change_type"] == "unchanged"


# ── Idempotency ───────────────────────────────────────────────────────────────

class TestIdempotency:
    def test_upsert_called_with_on_conflict(self):
        """Verify the SQL contains ON CONFLICT so re-runs are safe."""
        import inspect
        from scripts.fetch_13f import upsert_positions
        src = inspect.getsource(upsert_positions)
        assert "ON CONFLICT" in src
        assert "DO UPDATE" in src


# ── Alert formatting ──────────────────────────────────────────────────────────

class TestBuildAlert:
    def _fund(self):
        return {"slug": "test-fund", "name": "Test Fund LP", "cik": "0001234567"}

    def test_alert_contains_fund_name(self):
        rows = [{"change_type": "new", "ticker": "BE", "name_of_issuer": "BLOOM ENERGY",
                 "value_usd": 878_700, "put_call": None}]
        msg = build_alert(self._fund(), date(2026, 3, 31), date(2026, 5, 18), rows)
        assert "Test Fund LP" in msg

    def test_alert_contains_new_tickers(self):
        rows = [{"change_type": "new", "ticker": "BE", "name_of_issuer": "BLOOM",
                 "value_usd": 100_000, "put_call": None}]
        msg = build_alert(self._fund(), date(2026, 3, 31), date(2026, 5, 18), rows)
        assert "BE" in msg
        assert "NEW" in msg

    def test_alert_no_crash_on_null_ticker(self):
        rows = [{"change_type": "new", "ticker": None, "name_of_issuer": "UNKNOWN CORP",
                 "value_usd": 50_000, "put_call": None}]
        msg = build_alert(self._fund(), date(2026, 3, 31), None, rows)
        assert "UNKNOWN CORP" in msg

    def test_alert_value_formatted_in_billions(self):
        rows = [{"change_type": "new", "ticker": "NVDA", "name_of_issuer": "NVIDIA",
                 "value_usd": 1_600_000, "put_call": "Put"}]
        msg = build_alert(self._fund(), date(2026, 3, 31), date(2026, 5, 18), rows)
        assert "B" in msg  # billions


# ── Config file ───────────────────────────────────────────────────────────────

class TestConfig:
    def test_hedge_funds_json_is_valid(self):
        config_path = Path(__file__).resolve().parent.parent / "config" / "hedge_funds.json"
        assert config_path.exists(), "config/hedge_funds.json missing"
        funds = json.loads(config_path.read_text())
        assert isinstance(funds, list)
        assert len(funds) >= 1

    def test_each_fund_has_required_fields(self):
        config_path = Path(__file__).resolve().parent.parent / "config" / "hedge_funds.json"
        funds = json.loads(config_path.read_text())
        for f in funds:
            assert "slug" in f
            assert "name" in f
            assert "cik" in f

    def test_situational_awareness_lp_present(self):
        config_path = Path(__file__).resolve().parent.parent / "config" / "hedge_funds.json"
        funds = json.loads(config_path.read_text())
        slugs = [f["slug"] for f in funds]
        assert "situational-awareness-lp" in slugs
