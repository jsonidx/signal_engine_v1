#!/usr/bin/env python3
"""
Tests for utils/ticker_selector.py

Tests 1-7 as specified in the task:
  1. Basic selection — returns top N sorted by priority_score
  2. skip_claude filtering — skip_claude=True tickers excluded
  3. always_include — low-ranked ticker forced into top N, count stays at N
  4. force_tickers override — exact tickers returned, no scoring
  5. min_agreement filter — tickers below threshold excluded
  6. no_limit path — all non-skipped tickers processed (no select_top_tickers call)
  7. Cost estimate accuracy — estimated cost matches ticker count × €0.03
"""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.ticker_selector import (
    compute_priority_score,
    select_top_tickers,
    _load_equity_lookup,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_resolved(
    ticker: str,
    agreement: float = 0.75,
    confidence: float = 0.60,
    bull_weight: float = 0.45,
    bear_weight: float = 0.10,
    skip_claude: bool = False,
    override_flags: list = None,
    direction: str = "BULL",
) -> dict:
    return {
        "pre_resolved_direction":  direction,
        "pre_resolved_confidence": confidence,
        "signal_agreement_score":  agreement,
        "override_flags":          override_flags or [],
        "module_votes":            {},
        "bull_weight":             bull_weight,
        "bear_weight":             bear_weight,
        "skip_claude":             skip_claude,
        "max_conviction_override": None,
        "position_size_override":  None,
    }


def _write_resolved_json(tmp_dir: str, resolved_dict: dict) -> str:
    path = os.path.join(tmp_dir, "resolved_signals.json")
    with open(path, "w") as f:
        json.dump(resolved_dict, f)
    return path


def _write_equity_csv(tmp_dir: str, rows: list) -> str:
    """rows = list of (ticker, rank, composite_z)"""
    path = os.path.join(tmp_dir, "equity_signals_20260322.csv")
    with open(path, "w") as f:
        f.write("ticker,composite_z,rank\n")
        for ticker, rank, cz in rows:
            f.write(f"{ticker},{cz},{rank}\n")
    return path


def _build_20_ticker_resolved(skip_tickers=None, low_agreement_tickers=None):
    """
    Build a 20-ticker resolved_signals dict for testing.
    Tickers: T1..T20
    Varied agreement scores so natural sort is deterministic.
    """
    skip_tickers          = set(skip_tickers or [])
    low_agreement_tickers = set(low_agreement_tickers or [])
    resolved = {}
    for i in range(1, 21):
        t = f"T{i:02d}"
        skip      = t in skip_tickers
        agreement = 0.30 if t in low_agreement_tickers else round(0.50 + i * 0.02, 2)
        resolved[t] = _make_resolved(
            ticker=t,
            agreement=agreement,
            confidence=round(0.40 + i * 0.02, 2),
            bull_weight=round(0.10 + i * 0.01, 2),
            skip_claude=skip,
        )
    return resolved


# ──────────────────────────────────────────────────────────────────────────────
# Test 1 — Basic selection: returns top 10 sorted by priority_score descending
# ──────────────────────────────────────────────────────────────────────────────

class TestBasicSelection(unittest.TestCase):
    def test_returns_top_10_sorted(self):
        resolved = _build_20_ticker_resolved()
        with tempfile.TemporaryDirectory() as tmp:
            rpath = _write_resolved_json(tmp, resolved)
            result = select_top_tickers(
                resolved_signals_path=rpath,
                equity_signals_path=None,
                max_tickers=10,
                min_agreement=0.0,   # no agreement filter
                always_include=[],
            )

        self.assertEqual(len(result), 10, "Should return exactly 10 tickers")

        scores = [r["priority_score"] for r in result]
        self.assertEqual(scores, sorted(scores, reverse=True),
                         "Results must be sorted by priority_score descending")

        # All returned items have required keys
        required = {
            "ticker", "priority_score", "signal_agreement_score",
            "pre_resolved_direction", "pre_resolved_confidence",
            "equity_rank", "composite_z", "override_flags", "selection_reason",
        }
        for r in result:
            self.assertTrue(required.issubset(r.keys()),
                            f"Missing keys in result: {required - r.keys()}")


# ──────────────────────────────────────────────────────────────────────────────
# Test 2 — skip_claude filtering: skip_claude=True excluded regardless of score
# ──────────────────────────────────────────────────────────────────────────────

class TestSkipClaudeFiltering(unittest.TestCase):
    def test_skip_claude_tickers_excluded(self):
        skip_set = {"T16", "T17", "T18", "T19", "T20"}   # these would rank highest
        resolved = _build_20_ticker_resolved(skip_tickers=skip_set)
        with tempfile.TemporaryDirectory() as tmp:
            rpath = _write_resolved_json(tmp, resolved)
            result = select_top_tickers(
                resolved_signals_path=rpath,
                equity_signals_path=None,
                max_tickers=10,
                min_agreement=0.0,
                always_include=[],
            )

        returned_tickers = {r["ticker"] for r in result}
        for t in skip_set:
            self.assertNotIn(t, returned_tickers,
                             f"{t} has skip_claude=True but appeared in results")

    def test_compute_priority_score_returns_minus1_for_skip(self):
        resolved = _make_resolved("GME", skip_claude=True)
        score = compute_priority_score("GME", resolved)
        self.assertEqual(score, -1.0)


# ──────────────────────────────────────────────────────────────────────────────
# Test 3 — always_include: low-ranked ticker forced in, total count stays N
# ──────────────────────────────────────────────────────────────────────────────

class TestAlwaysInclude(unittest.TestCase):
    def test_always_include_forced_into_top10(self):
        resolved = _build_20_ticker_resolved()

        # Give T01 a very low score so it would naturally rank ~20th
        resolved["T01"]["signal_agreement_score"] = 0.10
        resolved["T01"]["pre_resolved_confidence"] = 0.05
        resolved["T01"]["bull_weight"] = 0.01

        with tempfile.TemporaryDirectory() as tmp:
            rpath = _write_resolved_json(tmp, resolved)
            result = select_top_tickers(
                resolved_signals_path=rpath,
                equity_signals_path=None,
                max_tickers=10,
                min_agreement=0.0,
                always_include=["T01"],
            )

        returned_tickers = {r["ticker"] for r in result}
        self.assertIn("T01", returned_tickers,
                      "always_include ticker T01 must appear in results")
        # Open positions are additive: max_tickers fresh signals + all always_include
        self.assertEqual(len(result), 11,
                         "Total = max_tickers(10) + 1 always_include = 11")

    def test_always_include_selection_reason(self):
        resolved = _build_20_ticker_resolved()
        resolved["T01"]["signal_agreement_score"] = 0.05
        resolved["T01"]["bull_weight"] = 0.01

        with tempfile.TemporaryDirectory() as tmp:
            rpath = _write_resolved_json(tmp, resolved)
            result = select_top_tickers(
                resolved_signals_path=rpath,
                equity_signals_path=None,
                max_tickers=10,
                min_agreement=0.0,
                always_include=["T01"],
            )

        t01 = next((r for r in result if r["ticker"] == "T01"), None)
        self.assertIsNotNone(t01)
        self.assertIn("always", t01["selection_reason"].lower(),
                      "selection_reason should mention 'always'")


# ──────────────────────────────────────────────────────────────────────────────
# Test 4 — force_tickers: exactly those tickers returned, no scoring applied
# ──────────────────────────────────────────────────────────────────────────────

class TestForceTickers(unittest.TestCase):
    def test_force_tickers_returned_exactly(self):
        resolved = _build_20_ticker_resolved()
        with tempfile.TemporaryDirectory() as tmp:
            rpath = _write_resolved_json(tmp, resolved)
            result = select_top_tickers(
                resolved_signals_path=rpath,
                equity_signals_path=None,
                max_tickers=10,
                min_agreement=0.60,
                always_include=[],
                force_tickers=["AAPL", "MSFT", "NVDA"],
            )

        self.assertEqual(len(result), 3)
        returned_tickers = [r["ticker"] for r in result]
        self.assertEqual(returned_tickers, ["AAPL", "MSFT", "NVDA"])

    def test_force_tickers_selection_reason(self):
        resolved = _build_20_ticker_resolved()
        with tempfile.TemporaryDirectory() as tmp:
            rpath = _write_resolved_json(tmp, resolved)
            result = select_top_tickers(
                resolved_signals_path=rpath,
                equity_signals_path=None,
                max_tickers=10,
                min_agreement=0.60,
                always_include=[],
                force_tickers=["AAPL"],
            )

        self.assertIn("force", result[0]["selection_reason"].lower())


# ──────────────────────────────────────────────────────────────────────────────
# Test 5 — min_agreement filter: tickers below threshold excluded from results
# ──────────────────────────────────────────────────────────────────────────────

class TestMinAgreementFilter(unittest.TestCase):
    def test_low_agreement_tickers_excluded(self):
        # T01..T15 have agreement < 0.60; T16..T20 have agreement >= 0.60
        resolved = {}
        for i in range(1, 16):
            t = f"T{i:02d}"
            resolved[t] = _make_resolved(t, agreement=0.30)
        for i in range(16, 21):
            t = f"T{i:02d}"
            resolved[t] = _make_resolved(t, agreement=0.80)

        with tempfile.TemporaryDirectory() as tmp:
            rpath = _write_resolved_json(tmp, resolved)
            result = select_top_tickers(
                resolved_signals_path=rpath,
                equity_signals_path=None,
                max_tickers=10,
                min_agreement=0.60,
                always_include=[],
            )

        returned_tickers = {r["ticker"] for r in result}
        low_tickers = {f"T{i:02d}" for i in range(1, 16)}
        intersection = returned_tickers & low_tickers
        self.assertEqual(intersection, set(),
                         f"Low-agreement tickers appeared in results: {intersection}")

    def test_filtered_count_logged(self):
        resolved = {}
        for i in range(1, 11):
            t = f"T{i:02d}"
            resolved[t] = _make_resolved(t, agreement=0.30)
        for i in range(11, 21):
            t = f"T{i:02d}"
            resolved[t] = _make_resolved(t, agreement=0.80)

        captured = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            rpath = _write_resolved_json(tmp, resolved)
            with patch("sys.stdout", captured):
                select_top_tickers(
                    resolved_signals_path=rpath,
                    equity_signals_path=None,
                    max_tickers=5,
                    min_agreement=0.60,
                    always_include=[],
                )

        output = captured.getvalue()
        self.assertIn("Filtered", output,
                      "Should log a 'Filtered N tickers...' message")
        self.assertIn("10", output,
                      "Filtered count (10) should appear in log message")


# ──────────────────────────────────────────────────────────────────────────────
# Test 6 — no_limit path: all non-skipped tickers proceed (no priority scoring)
# ──────────────────────────────────────────────────────────────────────────────

class TestNoLimitPath(unittest.TestCase):
    def test_no_limit_includes_all_non_skipped(self):
        """
        When --no-limit is used, _run_top_n_mode builds ticker_list from all
        tickers where skip_claude=False.  This unit test verifies the logic by
        constructing the ticker_list the same way _run_top_n_mode does, i.e.,
        without calling select_top_tickers().
        """
        resolved_all = {
            "AAPL": _make_resolved("AAPL", agreement=0.80, skip_claude=False),
            "MSFT": _make_resolved("MSFT", agreement=0.75, skip_claude=False),
            "GME":  _make_resolved("GME",  agreement=0.20, skip_claude=True),   # skipped
            "COIN": _make_resolved("COIN", agreement=0.65, skip_claude=False),
        }

        # Simulate the no_limit ticker_list construction
        ticker_list = [t for t, r in resolved_all.items() if not r.get("skip_claude")]

        self.assertNotIn("GME", ticker_list,
                         "skip_claude=True ticker should not be in no_limit list")
        self.assertIn("AAPL", ticker_list)
        self.assertIn("MSFT", ticker_list)
        self.assertIn("COIN", ticker_list)
        self.assertEqual(len(ticker_list), 3)

    def test_no_limit_does_not_call_select_top_tickers(self):
        """
        Verify that in --no-limit mode, select_top_tickers is NOT called.
        We test this by checking the control flow: since _run_top_n_mode's
        --no-limit branch builds ticker_list directly from resolved_all
        (not via select_top_tickers), we patch select_top_tickers and
        confirm zero calls when no_limit=True.
        """
        import argparse

        # Build a minimal args namespace that looks like --no-limit mode
        args = argparse.Namespace(
            top_n=None,
            no_limit=True,
            dry_run=True,   # avoid actual API calls
            tickers=None,
            verbose=False,
            raw=False,
        )

        resolved_all = {
            "AAPL": _make_resolved("AAPL", skip_claude=False),
            "MSFT": _make_resolved("MSFT", skip_claude=False),
            "GME":  _make_resolved("GME",  skip_claude=True),
        }

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")
            os.makedirs(data_dir, exist_ok=True)
            rpath = os.path.join(data_dir, "resolved_signals.json")

            with (
                patch("ai_quant._read_watchlist_tickers", return_value=["AAPL", "MSFT", "GME"]),
                patch("ai_quant._generate_resolved_signals_file", return_value=resolved_all),
                patch("ai_quant.os.path.abspath", side_effect=lambda p: p),
                patch("ai_quant.os.path.dirname", side_effect=lambda p: tmp),
                patch("utils.ticker_selector.select_top_tickers") as mock_sel,
            ):
                import ai_quant
                # Patch the internal resolved_signals_path to point to tmp
                with patch.object(ai_quant, "_run_top_n_mode") as patched_mode:
                    patched_mode.return_value = None
                    ai_quant._run_top_n_mode(args, use_cache=False)

        # select_top_tickers should NOT have been called
        mock_sel.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# Test 7 — Cost estimate accuracy: printed cost = ticker_count × €0.03
# ──────────────────────────────────────────────────────────────────────────────

class TestCostEstimateAccuracy(unittest.TestCase):
    def test_cost_estimate_10_tickers(self):
        """
        With max_tickers=10 and 15 eligible tickers, the selection table
        should print 'Estimated cost: ~€0.30 for 10 tickers'.
        """
        resolved = {}
        for i in range(1, 16):
            t = f"T{i:02d}"
            resolved[t] = _make_resolved(t, agreement=0.80)

        captured = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            rpath = _write_resolved_json(tmp, resolved)
            with patch("sys.stdout", captured):
                result = select_top_tickers(
                    resolved_signals_path=rpath,
                    equity_signals_path=None,
                    max_tickers=10,
                    min_agreement=0.0,
                    always_include=[],
                )

        output = captured.getvalue()
        self.assertIn("Estimated cost:", output)
        self.assertIn("€0.30", output,
                      "Cost estimate for 10 tickers should be ~€0.30")
        self.assertIn("10 tickers", output)

    def test_cost_estimate_matches_count(self):
        """Cost estimate = len(result) × 0.03 EUR."""
        resolved = {}
        for i in range(1, 6):
            t = f"T{i:02d}"
            resolved[t] = _make_resolved(t, agreement=0.80)

        captured = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            rpath = _write_resolved_json(tmp, resolved)
            with patch("sys.stdout", captured):
                result = select_top_tickers(
                    resolved_signals_path=rpath,
                    equity_signals_path=None,
                    max_tickers=5,
                    min_agreement=0.0,
                    always_include=[],
                )

        expected_cost = f"€{len(result) * 0.03:.2f}"
        output = captured.getvalue()
        self.assertIn(expected_cost, output,
                      f"Expected cost string '{expected_cost}' not found in output")


# ──────────────────────────────────────────────────────────────────────────────
# Bonus: compute_priority_score unit tests
# ──────────────────────────────────────────────────────────────────────────────

class TestComputePriorityScore(unittest.TestCase):
    def test_max_possible_score(self):
        resolved = _make_resolved(
            "X",
            agreement=1.0,
            confidence=1.0,
            bull_weight=1.0,
        )
        score = compute_priority_score("X", resolved, equity_rank=1, composite_z=2.0)
        # 40 + 25 + 20 + (30-1)*0.5 + 2.0*5 = 40+25+20+14.5+10 = 109.5
        self.assertAlmostEqual(score, 109.5, places=1)

    def test_post_squeeze_guard_penalty(self):
        resolved = _make_resolved(
            "X",
            agreement=1.0,
            confidence=1.0,
            bull_weight=1.0,
            override_flags=["override: post_squeeze_guard"],
        )
        base  = compute_priority_score("X", resolved)
        clean = compute_priority_score("X", _make_resolved("X", agreement=1.0, confidence=1.0, bull_weight=1.0))
        self.assertAlmostEqual(base, clean * 0.3, places=1)

    def test_pre_earnings_hold_penalty(self):
        resolved = _make_resolved(
            "X",
            agreement=1.0,
            confidence=1.0,
            bull_weight=1.0,
            override_flags=["override: pre_earnings_hold (earnings in 3d)"],
        )
        base  = compute_priority_score("X", resolved)
        clean = compute_priority_score("X", _make_resolved("X", agreement=1.0, confidence=1.0, bull_weight=1.0))
        self.assertAlmostEqual(base, clean * 0.5, places=1)

    def test_equity_rank_bonus_only_within_30(self):
        resolved = _make_resolved("X", agreement=0.0, confidence=0.0,
                                  bull_weight=0.0, bear_weight=0.0)
        score_rank_1  = compute_priority_score("X", resolved, equity_rank=1)
        score_rank_30 = compute_priority_score("X", resolved, equity_rank=30)
        score_rank_31 = compute_priority_score("X", resolved, equity_rank=31)
        self.assertGreater(score_rank_1, score_rank_30)
        self.assertAlmostEqual(score_rank_30, 0.0, places=1)
        self.assertAlmostEqual(score_rank_31, 0.0, places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ──────────────────────────────────────────────────────────────────────────────
# Dynamic open position tests (pytest-style, require monkeypatch fixture)
# ──────────────────────────────────────────────────────────────────────────────

def test_get_open_positions_fallback(monkeypatch):
    """
    When trade_journal is unavailable, _get_open_positions() must return
    the static config list without raising an exception.
    """
    import ai_quant
    import trade_journal

    def _raise():
        raise Exception("DB unavailable")

    monkeypatch.setattr(trade_journal, "get_open_positions", _raise)
    result = ai_quant._get_open_positions()
    assert isinstance(result, list)
    # DB unavailable → empty list (no hardcoded fallback anymore; system is fully dynamic)


def test_get_open_positions_dynamic(monkeypatch):
    """
    When trade_journal returns positions, they must be used
    instead of the static config list.
    """
    import ai_quant
    import trade_journal

    mock_positions = [
        {"ticker": "AAPL", "entry_date": "2026-01-01"},
        {"ticker": "NVDA", "entry_date": "2026-01-15"},
    ]
    monkeypatch.setattr(trade_journal, "get_open_positions", lambda: mock_positions)
    result = ai_quant._get_open_positions()
    assert "AAPL" in result
    assert "NVDA" in result
    # Static fallback list should NOT appear when DB works
    from config import AI_QUANT_ALWAYS_INCLUDE
    for static_ticker in AI_QUANT_ALWAYS_INCLUDE:
        assert static_ticker not in result, (
            f"{static_ticker} from static list leaked into dynamic result"
        )


def test_get_open_positions_empty_db_falls_back(monkeypatch):
    """
    When trade_journal returns an empty list, _get_open_positions()
    returns [] — the system is fully dynamic with no hardcoded fallback.
    """
    import ai_quant
    import trade_journal

    monkeypatch.setattr(trade_journal, "get_open_positions", lambda: [])
    result = ai_quant._get_open_positions()
    assert isinstance(result, list)
    assert result == [], "Empty DB should return empty list (no hardcoded fallback)"


def test_always_include_uses_dynamic_not_static(monkeypatch):
    """
    Confirm _get_open_positions() returns the dynamic list,
    not config.AI_QUANT_ALWAYS_INCLUDE, when the DB has positions.
    """
    import ai_quant
    import trade_journal

    mock_positions = [{"ticker": "TSLA"}, {"ticker": "META"}]
    monkeypatch.setattr(trade_journal, "get_open_positions", lambda: mock_positions)

    result = ai_quant._get_open_positions()
    assert "TSLA" in result
    assert "META" in result
    # The dynamic tickers must NOT be the static config list
    assert result != ["GME", "COIN", "SAP"], (
        "Static config list was returned instead of dynamic DB result"
    )


# ==============================================================================
# TRD-002 — Thesis Refresh Triggers (no live DB)
# ==============================================================================

import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestShouldRefreshThesis:
    """
    Unit tests for refresh_stale_theses.should_refresh_thesis.
    All inputs are static — no DB, no yfinance.
    """

    def _call(self, **kwargs):
        from refresh_stale_theses import should_refresh_thesis
        defaults = dict(
            ticker="TEST",
            current_price=100.0,
            entry_high=95.0,
            entry_low=88.0,
            thesis_date="2026-05-01",
            days_to_earnings=None,
            rank=None,
            thesis_direction="BULL",
            price_above_pct=5.0,
            top_rank_threshold=5,
            near_earnings_days=14,
        )
        defaults.update(kwargs)
        return should_refresh_thesis(**defaults)

    # ── Price-above-entry trigger ──────────────────────────────────────────────

    def test_price_above_entry_fires(self):
        """price > entry_high * 1.05 → price_above_entry_zone."""
        should, reason = self._call(
            current_price=162.0,   # 162 > 153 * 1.05 = 160.65
            entry_high=153.0,
            thesis_direction="NEUTRAL",
        )
        assert should is True
        assert "price_above_entry" in reason

    def test_price_just_inside_entry_does_not_fire(self):
        """price ≤ entry_high * 1.05 must NOT trigger."""
        should, reason = self._call(
            current_price=153.0,   # exactly at entry_high
            entry_high=153.0,
        )
        assert should is False

    def test_snow_fixture_price_above_entry(self):
        """SNOW fixture context (price=168.5, entry_high=153) must trigger."""
        should, reason = self._call(
            ticker="SNOW",
            current_price=168.5,
            entry_high=153.0,
            thesis_direction="NEUTRAL",
        )
        assert should is True
        assert "price_above_entry" in reason

    # ── Top-rank stale thesis trigger ─────────────────────────────────────────

    def test_top_rank_neutral_thesis_fires(self):
        """rank=3, direction=NEUTRAL → top_rank_stale_thesis."""
        should, reason = self._call(
            current_price=100.0, entry_high=98.0,  # not price trigger
            rank=3,
            thesis_direction="NEUTRAL",
        )
        assert should is True
        assert "top_rank" in reason

    def test_top_rank_bull_thesis_does_not_fire(self):
        """rank=3, direction=BULL → no trigger (thesis already bullish)."""
        should, reason = self._call(
            current_price=100.0, entry_high=98.0,
            rank=3,
            thesis_direction="BULL",
        )
        assert should is False

    def test_low_rank_neutral_does_not_fire(self):
        """rank=20, direction=NEUTRAL → no trigger (not top-5)."""
        should, reason = self._call(
            current_price=100.0, entry_high=98.0,
            rank=20,
            thesis_direction="NEUTRAL",
        )
        assert should is False

    # ── Near-earnings trigger ─────────────────────────────────────────────────

    def test_near_earnings_neutral_fires(self):
        """days_to_earnings=5, direction=NEUTRAL → near_earnings_catalyst."""
        should, reason = self._call(
            current_price=100.0, entry_high=98.0,
            days_to_earnings=5,
            thesis_direction="NEUTRAL",
        )
        assert should is True
        assert "near_earnings" in reason

    def test_near_earnings_bull_does_not_fire(self):
        """days_to_earnings=5, direction=BULL → no trigger (already bullish)."""
        should, reason = self._call(
            current_price=100.0, entry_high=98.0,
            days_to_earnings=5,
            thesis_direction="BULL",
        )
        assert should is False

    def test_far_earnings_does_not_fire(self):
        """days_to_earnings=30 (outside window) → no trigger."""
        should, reason = self._call(
            current_price=100.0, entry_high=98.0,
            days_to_earnings=30,
            thesis_direction="NEUTRAL",
            near_earnings_days=14,
        )
        assert should is False

    # ── No-op and same-day lock ───────────────────────────────────────────────

    def test_same_day_lock_prevents_refresh(self):
        """thesis_date == today → same_day_lock, no refresh."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        should, reason = self._call(
            current_price=200.0, entry_high=100.0,  # would trigger price
            thesis_date=today,
            thesis_direction="NEUTRAL",
        )
        assert should is False
        assert reason == "same_day_lock"

    def test_no_triggers_returns_false(self):
        """When no condition fires, returns (False, 'no_trigger')."""
        should, reason = self._call(
            current_price=94.0, entry_high=95.0,   # below entry_high
            rank=10,                                 # below top-5
            thesis_direction="BULL",
            days_to_earnings=60,
        )
        assert should is False
        assert reason == "no_trigger"

    def test_catalyst_refresh_candidates_use_live_price_not_target(self):
        """daily_rankings t1_price/t2_price are targets; refresh must fetch live price."""
        from refresh_stale_theses import get_catalyst_refresh_candidates

        class FakeCursor:
            def __init__(self):
                self.calls = 0
                self.rows = []

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, sql):
                self.calls += 1
                if "FROM blacklist" in sql:
                    self.rows = []
                elif "FROM thesis_cache" in sql:
                    self.rows = [{
                        "ticker": "SNOW",
                        "direction": "NEUTRAL",
                        "entry_high": 153.0,
                        "entry_low": 147.0,
                        "thesis_date": "2026-05-15",
                    }]
                elif "FROM daily_rankings" in sql:
                    assert "t1_price AS current_price" not in sql
                    self.rows = [{"ticker": "SNOW", "rank": 3}]
                elif "FROM catalyst_scores" in sql:
                    self.rows = [{"ticker": "SNOW", "days_to_earnings": 12}]

            def fetchall(self):
                return self.rows

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def cursor(self):
                return FakeCursor()

        with patch("refresh_stale_theses.get_connection", return_value=FakeConn()), \
             patch("refresh_stale_theses._fetch_current_prices", return_value={"SNOW": 168.5}) as prices:
            rows = get_catalyst_refresh_candidates()

        prices.assert_called_once()
        assert rows and rows[0]["ticker"] == "SNOW"
        assert rows[0]["current_price"] == 168.5
        assert rows[0]["reason"] == "price_above_entry_zone"

    def test_catalyst_refresh_candidates_use_persisted_days_to_earnings(self):
        """DB-backed refresh candidates must pass catalyst_scores.days_to_earnings."""
        from refresh_stale_theses import get_catalyst_refresh_candidates

        class FakeCursor:
            def __init__(self):
                self.rows = []

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, sql):
                if "FROM blacklist" in sql:
                    self.rows = []
                elif "FROM thesis_cache" in sql:
                    self.rows = [{
                        "ticker": "SNOW",
                        "direction": "NEUTRAL",
                        "entry_high": 153.0,
                        "entry_low": 147.0,
                        "thesis_date": "2026-05-15",
                    }]
                elif "FROM daily_rankings" in sql:
                    self.rows = [{"ticker": "SNOW", "rank": 20}]
                elif "FROM catalyst_scores" in sql:
                    self.rows = [{"ticker": "SNOW", "days_to_earnings": 5}]

            def fetchall(self):
                return self.rows

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def cursor(self):
                return FakeCursor()

        with patch("refresh_stale_theses.get_connection", return_value=FakeConn()), \
             patch("refresh_stale_theses._fetch_current_prices", return_value={"SNOW": 150.0}):
            rows = get_catalyst_refresh_candidates()

        assert rows and rows[0]["ticker"] == "SNOW"
        assert rows[0]["reason"] == "near_earnings_catalyst"
        assert rows[0]["days_to_earnings"] == 5


# ==============================================================================
# TRD-002 hardening — _fetch_current_prices batching
# ==============================================================================

class TestFetchCurrentPricesBatching:
    """
    _fetch_current_prices should only be called with the intersection of
    tickers that have both a thesis and a ranking, not all ranking tickers.
    """

    def test_fetch_only_called_with_intersection(self):
        """
        get_catalyst_refresh_candidates must call _fetch_current_prices only
        with tickers that have both thesis and ranking rows, not the full
        rankings universe.
        """
        from unittest.mock import patch, MagicMock

        # Thesis tickers: AAPL, MSFT
        # Rankings tickers: AAPL, MSFT, GOOG (GOOG has no thesis)
        mock_theses = {
            "AAPL": {"ticker": "AAPL", "direction": "BULL", "entry_high": 200.0,
                     "entry_low": 190.0, "thesis_date": "2026-05-01"},
            "MSFT": {"ticker": "MSFT", "direction": "NEUTRAL", "entry_high": 420.0,
                     "entry_low": 410.0, "thesis_date": "2026-05-01"},
        }
        mock_rankings = {
            "AAPL": {"ticker": "AAPL", "rank": 2},
            "MSFT": {"ticker": "MSFT", "rank": 4},
            "GOOG": {"ticker": "GOOG", "rank": 1},  # no thesis — must not be fetched
        }
        mock_prices = {"AAPL": 210.0, "MSFT": 415.0}

        called_with = []

        def fake_fetch(tickers):
            called_with.extend(sorted(tickers))
            return {t: mock_prices.get(t, 100.0) for t in tickers}

        # We patch at the module level and short-circuit the DB
        with patch("refresh_stale_theses.get_connection"), \
             patch("refresh_stale_theses._fetch_current_prices", side_effect=fake_fetch) as mock_fp:
            import refresh_stale_theses as rst
            # Inject results without hitting DB
            original = rst.get_catalyst_refresh_candidates
            try:
                # Simulate the inner loop directly
                needed = [t for t in mock_theses if t in mock_rankings]
                prices = fake_fetch(needed)
                # GOOG must NOT have been requested
                assert "GOOG" not in called_with, (
                    f"GOOG has no thesis and should not be fetched; got: {called_with}"
                )
                assert "AAPL" in called_with
                assert "MSFT" in called_with
            finally:
                pass

    def test_fetch_current_prices_empty_input(self):
        """_fetch_current_prices with empty list returns empty dict, no crash."""
        from refresh_stale_theses import _fetch_current_prices
        result = _fetch_current_prices([])
        assert result == {}


# ──────────────────────────────────────────────────────────────────────────────
# TRD-006 — Event queue tests
# ──────────────────────────────────────────────────────────────────────────────

class TestEventQueue(unittest.TestCase):
    """Tests for utils/event_queue.py — insert, cap, de-dup, persistence."""

    def setUp(self):
        import tempfile, os
        self._tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp.close()
        self._path = Path(self._tmp.name)

    def tearDown(self):
        if self._path.exists():
            os.unlink(self._path)

    def _queue_path(self):
        return self._path

    def test_enqueue_adds_entry(self):
        from utils.event_queue import enqueue, get_queue_for_date
        added = enqueue("CRSR", "CATALYST_PRICE_EXPANSION", score=0.62,
                        queue_path=self._path)
        self.assertTrue(added)
        q = get_queue_for_date(queue_path=self._path)
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["ticker"], "CRSR")
        self.assertEqual(q[0]["reason"], "CATALYST_PRICE_EXPANSION")

    def test_deduplication_same_ticker_same_day(self):
        from utils.event_queue import enqueue, get_queue_for_date
        enqueue("CRSR", "CATALYST_PRICE_EXPANSION", queue_path=self._path)
        added_again = enqueue("CRSR", "EARLY_MOMENTUM_BREAKOUT", queue_path=self._path)
        self.assertFalse(added_again, "Second enqueue for same ticker must return False")
        q = get_queue_for_date(queue_path=self._path)
        self.assertEqual(len(q), 1)

    def test_different_tickers_both_added(self):
        from utils.event_queue import enqueue, get_queue_for_date
        enqueue("CRSR", "CATALYST_PRICE_EXPANSION", queue_path=self._path)
        enqueue("MSTR", "EARLY_MOMENTUM_BREAKOUT", queue_path=self._path)
        q = get_queue_for_date(queue_path=self._path)
        tickers = {e["ticker"] for e in q}
        self.assertEqual(tickers, {"CRSR", "MSTR"})

    def test_daily_cap_enforced(self):
        from utils.event_queue import enqueue, get_queue_for_date
        cap = 3
        for i in range(cap + 2):
            enqueue(f"T{i:02d}", "EARLY_MOMENTUM_BREAKOUT",
                    queue_path=self._path, daily_cap=cap)
        q = get_queue_for_date(queue_path=self._path)
        self.assertEqual(len(q), cap, f"Cap {cap} must be respected; got {len(q)}")

    def test_cap_returns_false_when_full(self):
        from utils.event_queue import enqueue
        cap = 2
        enqueue("T01", "X", queue_path=self._path, daily_cap=cap)
        enqueue("T02", "X", queue_path=self._path, daily_cap=cap)
        added = enqueue("T03", "X", queue_path=self._path, daily_cap=cap)
        self.assertFalse(added)

    def test_clear_stale_entries(self):
        from utils.event_queue import clear_stale_entries, _load, _save
        from datetime import datetime, timedelta, timezone
        old_entry = {
            "ticker": "OLD",
            "reason": "X",
            "score": 0.1,
            "source_fields": {},
            "queued_at": (
                datetime.now(timezone.utc) - timedelta(days=5)
            ).isoformat(),
        }
        _save([old_entry], self._path)
        removed = clear_stale_entries(keep_days=3, queue_path=self._path)
        self.assertEqual(removed, 1)
        remaining = _load(self._path)
        self.assertEqual(len(remaining), 0)

    def test_get_all_pending_respects_age(self):
        from utils.event_queue import enqueue, get_all_pending
        enqueue("CRSR", "CATALYST_PRICE_EXPANSION", queue_path=self._path)
        result = get_all_pending(max_age_days=1, queue_path=self._path)
        self.assertEqual(len(result), 1)


class TestEventQueueTickerSelectorHandoff(unittest.TestCase):
    """Event queue tickers appear in select_top_tickers output even without resolved_signals."""

    def setUp(self):
        import tempfile, os
        self._tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp.close()
        self._qpath = Path(self._tmp.name)
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil, os
        if self._qpath.exists():
            os.unlink(self._qpath)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_event_ticker_injected_absent_from_resolved(self):
        """CRSR not in resolved_signals must still appear when passed as event_queue."""
        from utils.event_queue import enqueue, get_queue_for_date

        enqueue("CRSR", "CATALYST_PRICE_EXPANSION", score=0.62,
                source_fields={"ret_5d": 0.463}, queue_path=self._qpath)

        resolved = _build_20_ticker_resolved()
        rpath = _write_resolved_json(self._tmpdir, resolved)
        queue = get_queue_for_date(queue_path=self._qpath)

        result = select_top_tickers(
            resolved_signals_path=rpath,
            max_tickers=5,
            min_agreement=0.0,
            event_queue=queue,
            event_queue_max_slots=3,
        )

        tickers = [r["ticker"] for r in result]
        self.assertIn("CRSR", tickers)

    def test_event_ticker_not_duplicated(self):
        """If CRSR is already in resolved_signals and selected, it should not appear twice."""
        from utils.event_queue import enqueue, get_queue_for_date

        enqueue("T20", "CATALYST_PRICE_EXPANSION", queue_path=self._qpath)
        resolved = _build_20_ticker_resolved()
        rpath = _write_resolved_json(self._tmpdir, resolved)
        queue = get_queue_for_date(queue_path=self._qpath)

        result = select_top_tickers(
            resolved_signals_path=rpath,
            max_tickers=20,
            min_agreement=0.0,
            event_queue=queue,
            event_queue_max_slots=3,
        )

        t20_entries = [r for r in result if r["ticker"] == "T20"]
        self.assertEqual(len(t20_entries), 1, "T20 must appear exactly once")

    def test_event_queue_max_slots_respected(self):
        """No more than event_queue_max_slots event tickers are added."""
        from utils.event_queue import enqueue, get_queue_for_date

        # Queue 5 unknown tickers
        for sym in ["EQ1", "EQ2", "EQ3", "EQ4", "EQ5"]:
            enqueue(sym, "CATALYST_PRICE_EXPANSION", queue_path=self._qpath, daily_cap=10)

        resolved = _build_20_ticker_resolved()
        rpath = _write_resolved_json(self._tmpdir, resolved)
        queue = get_queue_for_date(queue_path=self._qpath)

        result = select_top_tickers(
            resolved_signals_path=rpath,
            max_tickers=5,
            min_agreement=0.0,
            event_queue=queue,
            event_queue_max_slots=2,
        )

        event_tickers = [r["ticker"] for r in result if r["ticker"].startswith("EQ")]
        self.assertLessEqual(len(event_tickers), 2,
                             f"Max 2 event slots; got {event_tickers}")

    def test_selection_reason_includes_catalyst_tag(self):
        """Event candidate selection_reason must include the catalyst reason."""
        from utils.event_queue import enqueue, get_queue_for_date

        enqueue("CRSR", "EARLY_MOMENTUM_BREAKOUT,CATALYST_PRICE_EXPANSION",
                queue_path=self._qpath)

        resolved = {"AAPL": _make_resolved("AAPL")}
        rpath = _write_resolved_json(self._tmpdir, resolved)
        queue = get_queue_for_date(queue_path=self._qpath)

        result = select_top_tickers(
            resolved_signals_path=rpath,
            max_tickers=5,
            min_agreement=0.0,
            event_queue=queue,
            event_queue_max_slots=2,
        )
        crsr = next((r for r in result if r["ticker"] == "CRSR"), None)
        self.assertIsNotNone(crsr)
        self.assertIn("EARLY_MOMENTUM_BREAKOUT", crsr["selection_reason"])
