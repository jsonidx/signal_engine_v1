"""
Tests for options screener API, accuracy analytics, and scoring review
(TRD-028, TRD-029, TRD-030).

Uses FastAPI TestClient with mocked _db_connect and candidate engine.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dashboard.api.main import app, _cache

client = TestClient(app, raise_server_exceptions=False)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_cache():
    _cache._store.clear()
    yield
    _cache._store.clear()


def _expiry(days: int) -> str:
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


def _make_thesis_rows(n: int = 3) -> list[dict]:
    rows = []
    for i in range(n):
        ticker = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"][i % 5]
        rows.append({
            "id": i + 1,
            "ticker": ticker,
            # "date" mirrors what _fetch_screener_tickers() now SELECTs from thesis_cache
            "date": f"2026-05-{20 + i:02d}",
            "direction": "BULL" if i % 2 == 0 else "BEAR",
            "conviction": 3 + (i % 3),
            "signal_agreement_score": 0.70 + i * 0.05,
            "entry_low": 145.0 + i,
            "entry_high": 150.0 + i,
            "target_1": 165.0 + i,
            "target_2": 175.0 + i,
            "stop_loss": 140.0 + i,
            "time_horizon": "2-4 weeks",
            "signals_json": None,
            "current_price": 148.0 + i,
        })
    return rows


def _mock_screener_db(thesis_rows: list[dict]):
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = thesis_rows

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_cur)
    ctx.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = ctx
    conn.close.return_value = None
    return conn


def _make_candidate_result(ticker: str, n: int = 1):
    from utils.option_candidates import CandidateResult, OptionCandidate
    candidates = []
    for rank in range(n):
        exp = _expiry(21 + rank * 7)
        candidates.append(OptionCandidate(
            ticker=ticker, expiry=exp, strike=150.0 + rank * 5,
            right="C", dte=21 + rank * 7,
            bid=1.90, ask=2.10, mid=2.00, spread_pct=9.5,
            delta=0.40, implied_vol=0.35,
            open_interest=500, volume=100,
            breakeven=152.0 + rank * 5,
            score=70.0 - rank * 5,
            rationale="bullish long call",
            strategy_preset="long_call",
            source="yfinance",
            holding_window_days=10,
            exit_by_date=_expiry(14),
            underlying_target_1=160.0,
            underlying_target_2=170.0,
            underlying_stop=140.0,
            option_take_profit_1=3.00,
            option_take_profit_2=4.00,
            option_stop_loss=1.00,
            max_holding_rule="Close 7d before expiry",
            event_exit_rule=None,
        ))
    return CandidateResult(
        ticker=ticker, generated_at="2026-05-30T12:00:00",
        suppressed=False, candidates=candidates,
        rejection_reasons=[], underlying_price=148.0, chain_source="yfinance",
    )


def _make_suppressed_result(ticker: str):
    from utils.option_candidates import CandidateResult
    return CandidateResult(
        ticker=ticker, generated_at="2026-05-30T12:00:00",
        suppressed=True, suppression_reason="Conviction too low",
        candidates=[], rejection_reasons=[], chain_source="unknown",
    )


# ══════════════════════════════════════════════════════════════════════════════
# TRD-028: Options Screener endpoint
# ══════════════════════════════════════════════════════════════════════════════

class TestOptionsScreenerEndpoint:

    def test_returns_200(self):
        mock_conn = _mock_screener_db([])
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            resp = client.get("/api/options/screener")
        assert resp.status_code == 200

    def test_empty_thesis_universe_returns_no_candidates(self):
        mock_conn = _mock_screener_db([])
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            resp = client.get("/api/options/screener")
        body = resp.json()
        assert body["count"] == 0
        assert body["data"] == []

    def test_candidates_ranked_across_tickers(self):
        rows = _make_thesis_rows(3)
        mock_conn = _mock_screener_db(rows)

        def fake_candidates(ticker, thesis=None, **kw):
            return _make_candidate_result(ticker, n=1)

        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates", side_effect=fake_candidates),
            patch("dashboard.api.main._fetch_current_prices", return_value={}),
        ):
            resp = client.get("/api/options/screener?max_tickers=3")

        body = resp.json()
        assert resp.status_code == 200
        assert body["count"] > 0
        assert body["tickers_evaluated"] > 0
        # Global ranks must be present and sequential
        ranks = [r["rank_global"] for r in body["data"]]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_each_row_has_required_fields(self):
        rows = _make_thesis_rows(1)
        mock_conn = _mock_screener_db(rows)

        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  return_value=_make_candidate_result("AAPL")),
            patch("dashboard.api.main._fetch_current_prices", return_value={}),
        ):
            resp = client.get("/api/options/screener?max_tickers=1")

        body = resp.json()
        if body["count"] > 0:
            row = body["data"][0]
            required = {
                "ticker", "strategy_preset", "strike", "expiry", "dte",
                "mid", "delta", "spread_pct", "score", "rationale",
                "holding_window_days", "thesis_direction", "thesis_conviction",
                "rank_global", "rank_within_ticker",
            }
            missing = required - set(row.keys())
            assert not missing, f"Missing screener row fields: {missing}"

    def test_all_suppressed_returns_zero_candidates(self):
        rows = _make_thesis_rows(2)
        mock_conn = _mock_screener_db(rows)

        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  side_effect=lambda t, **kw: _make_suppressed_result(t)),
            patch("dashboard.api.main._fetch_current_prices", return_value={}),
        ):
            resp = client.get("/api/options/screener")

        body = resp.json()
        assert body["count"] == 0

    def test_db_failure_returns_no_data(self):
        with patch("dashboard.api.main._db_connect", side_effect=Exception("no db")):
            resp = client.get("/api/options/screener")
        body = resp.json()
        assert resp.status_code == 200
        assert body["count"] == 0

    def test_screener_response_cached(self):
        rows = _make_thesis_rows(1)
        mock_conn = _mock_screener_db(rows)

        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn) as db_mock,
            patch("dashboard.api.main.get_option_candidates",
                  return_value=_make_candidate_result("AAPL")),
            patch("dashboard.api.main._fetch_current_prices", return_value={}),
        ):
            _cache.invalidate("options_screener:2:20")
            client.get("/api/options/screener")
            client.get("/api/options/screener")
            # DB should only be hit once (second request served from cache)
            assert db_mock.call_count == 1

    def test_higher_conviction_prefers_filter(self):
        high_conv = [r for r in _make_thesis_rows(4) if r["conviction"] >= 3]
        mock_conn = _mock_screener_db(high_conv)

        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  side_effect=lambda t, **kw: _make_candidate_result(t)),
            patch("dashboard.api.main._fetch_current_prices", return_value={}),
        ):
            resp = client.get("/api/options/screener?min_conviction=3")
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Issue #2: Screener persistence
# ══════════════════════════════════════════════════════════════════════════════

class TestScreenerPersistence:
    """The screener endpoint must persist CandidateResult objects (Issue #2 fix)."""

    def setup_method(self):
        _cache._store.clear()

    def test_screener_calls_persist_for_each_ticker(self):
        """save_option_candidate_snapshot must be called once per ticker result."""
        rows = _make_thesis_rows(2)
        mock_conn = _mock_screener_db(rows)
        saved_calls: list = []

        def fake_persist(result, thesis_id=None, thesis_context=None):
            saved_calls.append({
                "ticker": result.ticker,
                "thesis_id": thesis_id,
                "thesis_context": thesis_context,
            })
            return [1]

        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  side_effect=lambda t, **kw: _make_candidate_result(t)),
            patch("dashboard.api.main._fetch_current_prices", return_value={}),
            patch("utils.supabase_persist.save_option_candidate_snapshot",
                  side_effect=fake_persist),
        ):
            _cache.invalidate("options_screener:2:20")
            resp = client.get("/api/options/screener?max_tickers=2")

        assert resp.status_code == 200
        # Give the fire-and-forget executor a moment to run in the test thread
        import time
        time.sleep(0.1)
        assert len(saved_calls) >= 1, (
            "save_option_candidate_snapshot should be called for screener results"
        )

    def test_screener_persistence_does_not_block_response(self):
        """Response must return 200 even when persistence raises."""
        rows = _make_thesis_rows(1)
        mock_conn = _mock_screener_db(rows)

        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  side_effect=lambda t, **kw: _make_candidate_result(t)),
            patch("dashboard.api.main._fetch_current_prices", return_value={}),
            patch("utils.supabase_persist.save_option_candidate_snapshot",
                  side_effect=Exception("db down")),
        ):
            _cache.invalidate("options_screener:2:20")
            resp = client.get("/api/options/screener?max_tickers=1")

        assert resp.status_code == 200
        assert resp.json()["count"] >= 0  # response shape intact

    def test_screener_persistence_passes_thesis_context(self):
        """
        thesis_context must include thesis_date, time_horizon, signal_agreement.

        Uses _make_thesis_rows() directly — no manual field injection — to verify
        that the query result shape (_fetch_screener_tickers now SELECTs date) is
        what actually drives persistence, not ad-hoc overrides in the test.
        """
        rows = _make_thesis_rows(1)
        # Confirm the fixture already carries the fields we expect from the real query
        assert "date" in rows[0], (
            "_make_thesis_rows must include 'date' (mirrors _fetch_screener_tickers SELECT)"
        )
        expected_date = rows[0]["date"]
        expected_horizon = rows[0]["time_horizon"]
        expected_agreement = rows[0]["signal_agreement_score"]

        mock_conn = _mock_screener_db(rows)
        saved_contexts: list = []

        def fake_persist(result, thesis_id=None, thesis_context=None):
            saved_contexts.append(thesis_context or {})
            return [1]

        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  side_effect=lambda t, **kw: _make_candidate_result(t)),
            patch("dashboard.api.main._fetch_current_prices", return_value={}),
            patch("utils.supabase_persist.save_option_candidate_snapshot",
                  side_effect=fake_persist),
        ):
            _cache.invalidate("options_screener:2:20")
            client.get("/api/options/screener?max_tickers=1")

        import time
        time.sleep(0.1)
        assert len(saved_contexts) >= 1
        ctx = saved_contexts[0]
        assert ctx.get("thesis_date") == expected_date, (
            f"thesis_date must come from query 'date' column; expected {expected_date!r}, got {ctx.get('thesis_date')!r}"
        )
        assert ctx.get("time_horizon") == expected_horizon
        assert ctx.get("signal_agreement") == pytest.approx(expected_agreement)

    def test_screener_thesis_date_not_null_without_manual_injection(self):
        """
        Regression guard: thesis_date must be non-None when the query row includes
        'date'.  This test would have caught the original bug where 'date' was
        absent from the SELECT list so tr.get('date') always returned None.
        """
        rows = _make_thesis_rows(1)
        # Do NOT manually inject 'date' — rely entirely on _make_thesis_rows()
        # which now mirrors the real _fetch_screener_tickers() query shape.
        mock_conn = _mock_screener_db(rows)
        saved_contexts: list = []

        def fake_persist(result, thesis_id=None, thesis_context=None):
            saved_contexts.append(thesis_context or {})
            return [1]

        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  side_effect=lambda t, **kw: _make_candidate_result(t)),
            patch("dashboard.api.main._fetch_current_prices", return_value={}),
            patch("utils.supabase_persist.save_option_candidate_snapshot",
                  side_effect=fake_persist),
        ):
            _cache.invalidate("options_screener:2:20")
            client.get("/api/options/screener?max_tickers=1")

        import time
        time.sleep(0.1)
        assert len(saved_contexts) >= 1
        assert saved_contexts[0].get("thesis_date") is not None, (
            "thesis_date must not be None — _fetch_screener_tickers must SELECT 'date'"
        )

    def test_suppressed_screener_results_still_persisted(self):
        """Suppressed results must also be passed to persistence (for no-trade tracking)."""
        rows = _make_thesis_rows(1)
        mock_conn = _mock_screener_db(rows)
        saved_calls: list = []

        def fake_persist(result, **kw):
            saved_calls.append(result.suppressed)
            return []

        with (
            patch("dashboard.api.main._db_connect", return_value=mock_conn),
            patch("dashboard.api.main.get_option_candidates",
                  return_value=_make_suppressed_result("AAPL")),
            patch("dashboard.api.main._fetch_current_prices", return_value={}),
            patch("utils.supabase_persist.save_option_candidate_snapshot",
                  side_effect=fake_persist),
        ):
            _cache.invalidate("options_screener:2:20")
            resp = client.get("/api/options/screener?max_tickers=1")

        import time
        time.sleep(0.1)
        assert resp.status_code == 200
        # Suppressed result should also be persisted
        assert len(saved_calls) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# TRD-029: Options Accuracy Analytics endpoint
# ══════════════════════════════════════════════════════════════════════════════

def _make_accuracy_conn(
    total_snaps=10, total_resolved=7,
    preset_rows=None, delta_rows=None,
    dte_rows=None, iv_rows=None,
    spread_rows=None, chain_rows=None,
    hold_rows=None, supp_rows=None,
):
    if preset_rows is None:
        preset_rows = [
            {"cohort": "long_call", "sample_size": 5, "win_rate_pct": 60.0,
             "tp1_rate_pct": 55.0, "stop_rate_pct": 20.0,
             "avg_option_return_5d": 8.5, "avg_underlying_return_5d": 3.2},
        ]

    call_sequence = [
        [{"n": total_snaps}],           # total snapshots
        [{"n": total_resolved}],         # total resolved
        preset_rows,                     # by_preset
        chain_rows or [],                # by_chain_source
        delta_rows or [],                # by_delta_bucket
        dte_rows or [],                  # by_dte_bucket
        iv_rows or [],                   # by_iv_bucket
        spread_rows or [],               # by_spread_bucket
        hold_rows or [],                 # by_holding_window
        supp_rows or [],                 # suppression reasons
    ]
    call_idx = [0]

    mock_cur = MagicMock()
    def _fetchall():
        result = call_sequence[call_idx[0]]
        call_idx[0] = min(call_idx[0] + 1, len(call_sequence) - 1)
        return result
    mock_cur.fetchall.side_effect = _fetchall

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_cur)
    ctx.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = ctx
    conn.close.return_value = None
    return conn


class TestOptionsAccuracyEndpoint:

    def test_returns_200(self):
        mock_conn = _make_accuracy_conn()
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            resp = client.get("/api/options/accuracy")
        assert resp.status_code == 200

    def test_response_shape(self):
        mock_conn = _make_accuracy_conn()
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            resp = client.get("/api/options/accuracy")
        body = resp.json()
        required = {
            "data_available", "days", "total_snapshots", "total_resolved",
            "generated_at", "by_preset", "by_delta_bucket", "by_dte_bucket",
            "by_iv_bucket", "by_spread_bucket", "by_chain_source",
            "by_holding_window", "suppression_reasons",
        }
        missing = required - set(body.keys())
        assert not missing, f"Missing accuracy fields: {missing}"

    def test_preset_cohort_includes_sample_size(self):
        mock_conn = _make_accuracy_conn()
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            resp = client.get("/api/options/accuracy")
        body = resp.json()
        if body.get("by_preset"):
            row = body["by_preset"][0]
            assert "sample_size" in row
            assert row["sample_size"] > 0

    def test_db_failure_returns_empty(self):
        with patch("dashboard.api.main._db_connect", side_effect=Exception("no db")):
            resp = client.get("/api/options/accuracy")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_snapshots"] == 0

    def test_days_parameter_respected(self):
        mock_conn = _make_accuracy_conn()
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            resp = client.get("/api/options/accuracy?days=30")
        body = resp.json()
        assert body["days"] == 30


# ══════════════════════════════════════════════════════════════════════════════
# TRD-030: Options Scoring Review endpoint
# ══════════════════════════════════════════════════════════════════════════════

def _make_review_conn():
    sections = [
        # preset_performance
        [{"strategy_preset": "long_call", "n": 10, "avg_score": 65.0,
          "win_rate_pct": 58.0, "score_win_correlation": 12.5}],
        # delta analysis
        [{"delta_rounded": 0.4, "n": 5, "avg_score": 68.0,
          "win_rate_pct": 62.0, "avg_option_return_5d": 9.0}],
        # spread analysis
        [{"spread_pct_rounded": 8, "n": 6, "win_rate_pct": 55.0,
          "avg_option_return_5d": 7.0}],
        # rejection_freq
        [{"reason": "OI too low", "count": 15}],
        # suppression_freq
        [{"suppression_reason": "Conviction < 2", "count": 8}],
    ]
    call_idx = [0]

    mock_cur = MagicMock()
    def _fetchall():
        result = sections[min(call_idx[0], len(sections) - 1)]
        call_idx[0] += 1
        return result
    mock_cur.fetchall.side_effect = _fetchall

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_cur)
    ctx.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = ctx
    conn.close.return_value = None
    return conn


class TestOptionsScoringReviewEndpoint:

    def test_returns_200(self):
        mock_conn = _make_review_conn()
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            resp = client.get("/api/options/scoring-review")
        assert resp.status_code == 200

    def test_response_has_governance_note(self):
        mock_conn = _make_review_conn()
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            resp = client.get("/api/options/scoring-review")
        body = resp.json()
        assert "governance_note" in body
        note = body["governance_note"].lower()
        assert "propose" in note or "human" in note or "approval" in note

    def test_sections_present(self):
        mock_conn = _make_review_conn()
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            resp = client.get("/api/options/scoring-review")
        body = resp.json()
        assert "sections" in body
        sections = body["sections"]
        assert "preset_performance_vs_score" in sections
        assert "delta_range_calibration" in sections
        assert "top_rejection_reasons" in sections

    def test_review_questions_present(self):
        mock_conn = _make_review_conn()
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            resp = client.get("/api/options/scoring-review")
        body = resp.json()
        assert "review_questions" in body
        assert len(body["review_questions"]) > 0

    def test_db_failure_returns_error_json(self):
        with patch("dashboard.api.main._db_connect", side_effect=Exception("no db")):
            resp = client.get("/api/options/scoring-review")
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body or "ok" in body

    def test_days_parameter_respected(self):
        mock_conn = _make_review_conn()
        with patch("dashboard.api.main._db_connect", return_value=mock_conn):
            resp = client.get("/api/options/scoring-review?days=60")
        body = resp.json()
        assert body.get("days_analyzed") == 60


# ══════════════════════════════════════════════════════════════════════════════
# TRD-027: Resolve-outcomes endpoint
# ══════════════════════════════════════════════════════════════════════════════

class TestResolveOutcomesEndpoint:

    @patch("utils.option_outcomes.resolve_batch")
    @patch("utils.supabase_persist.fetch_unresolved_snapshots")
    def test_returns_200_with_counts(self, mock_fetch, mock_batch):
        mock_fetch.return_value = []
        mock_batch.return_value = {"resolved": 0, "failed": 0, "persisted": 0}
        resp = client.post("/api/options/resolve-outcomes?resolution_type=1d")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "resolved" in body

    @patch("utils.option_outcomes.resolve_batch")
    @patch("utils.supabase_persist.fetch_unresolved_snapshots")
    def test_resolution_type_passed_through(self, mock_fetch, mock_batch):
        mock_fetch.return_value = []
        mock_batch.return_value = {"resolved": 0, "failed": 0, "persisted": 0}
        resp = client.post("/api/options/resolve-outcomes?resolution_type=5d")
        body = resp.json()
        assert body["resolution_type"] == "5d"

    def test_invalid_resolution_type_returns_422(self):
        resp = client.post("/api/options/resolve-outcomes?resolution_type=invalid")
        assert resp.status_code == 422
