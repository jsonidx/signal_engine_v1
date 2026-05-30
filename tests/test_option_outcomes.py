"""
Tests for option outcome resolution  (TRD-027).
All yfinance and DB calls are mocked.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from utils.option_outcomes import (
    _approx_option_price,
    _close_on_date,
    _pct,
    resolve_snapshot,
    resolve_batch,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _today() -> date:
    return date.today()


def _make_hist(prices: dict) -> pd.DataFrame:
    """Build a minimal DataFrame with Close column keyed by date."""
    rows = {pd.Timestamp(d): {"Close": p} for d, p in prices.items()}
    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index.name = None
    return df


def _snap(
    ticker="AAPL",
    run_date=None,
    mid=2.00,
    delta=0.40,
    direction="BULL",
    opt_tp1=3.00,
    opt_tp2=4.00,
    opt_sl=1.00,
    und_t1=160.0,
    und_t2=175.0,
    und_stop=140.0,
    holding_days=15,
):
    run_date = run_date or (_today() - timedelta(days=12)).isoformat()
    return {
        "id": 1,
        "ticker": ticker,
        "run_date": run_date,
        "created_at": run_date,
        "mid": mid,
        "delta": delta,
        "direction": direction,
        "option_take_profit_1": opt_tp1,
        "option_take_profit_2": opt_tp2,
        "option_stop_loss": opt_sl,
        "underlying_target_1": und_t1,
        "underlying_target_2": und_t2,
        "underlying_stop": und_stop,
        "holding_window_days": holding_days,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Low-level helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestHelpers:
    def test_pct_correct(self):
        assert _pct(110.0, 100.0) == pytest.approx(10.0)
        assert _pct(90.0, 100.0)  == pytest.approx(-10.0)

    def test_pct_none_on_zero_base(self):
        assert _pct(100.0, 0.0) is None

    def test_pct_none_on_none_input(self):
        assert _pct(None, 100.0) is None
        assert _pct(100.0, None) is None

    def test_close_on_date_returns_correct_close(self):
        target = _today()
        hist = _make_hist({
            target.isoformat(): 150.0,
            (target + timedelta(days=1)).isoformat(): 152.0,
        })
        result = _close_on_date(hist, target)
        assert result == pytest.approx(150.0)

    def test_close_on_date_uses_next_available_when_missing(self):
        target = _today()
        hist = _make_hist({
            (target + timedelta(days=1)).isoformat(): 153.0,
        })
        result = _close_on_date(hist, target)
        assert result == pytest.approx(153.0)

    def test_close_on_date_none_on_empty(self):
        assert _close_on_date(pd.DataFrame(), _today()) is None

    def test_approx_option_price_positive_move_call(self):
        # 10% underlying move, delta 0.40, mid 2.00
        # option_return ≈ 0.40 × 10 = 4%, new_mid ≈ 2.00 × 1.04 = 2.08
        new_mid = _approx_option_price(10.0, 0.40, 2.00)
        assert new_mid == pytest.approx(2.08, abs=0.01)

    def test_approx_option_price_negative_move_call(self):
        new_mid = _approx_option_price(-5.0, 0.40, 2.00)
        assert new_mid is not None
        assert new_mid < 2.00

    def test_approx_option_price_floor_at_minimum(self):
        # delta=1.0, move=-200% on mid=0.10: raw = 1.0 × (-200) = -200
        # new = 0.10 × (1 + -200/100) = 0.10 × (-1.0) = -0.10 → floor at 0.01
        new_mid = _approx_option_price(-200.0, 1.0, 0.10)
        assert new_mid == pytest.approx(0.01)

    def test_approx_option_price_none_inputs(self):
        assert _approx_option_price(None, 0.40, 2.00) is None
        assert _approx_option_price(10.0, None, 2.00) is None
        assert _approx_option_price(10.0, 0.40, None) is None


# ══════════════════════════════════════════════════════════════════════════════
# resolve_snapshot
# ══════════════════════════════════════════════════════════════════════════════

class TestResolveSnapshot:
    def _patched_history(self, entry_px=148.0, px_1d=150.0, px_5d=155.0, px_10d=152.0):
        """Return a mock that patches _fetch_history with pre-built prices."""
        run_date = (_today() - timedelta(days=12)).isoformat()
        entry_dt = date.fromisoformat(run_date)
        prices = {
            run_date:                                       entry_px,
            (entry_dt + timedelta(days=1)).isoformat():    px_1d,
            (entry_dt + timedelta(days=5)).isoformat():    px_5d,
            (entry_dt + timedelta(days=10)).isoformat():   px_10d,
        }
        return _make_hist(prices)

    @patch("utils.option_outcomes._fetch_history")
    def test_returns_list_of_outcomes(self, mock_hist):
        mock_hist.return_value = self._patched_history()
        snap = _snap()
        results = resolve_snapshot(snap, run_resolution_types=("1d", "5d"))
        assert isinstance(results, list)
        assert len(results) == 2
        for r in results:
            assert "resolution_type" in r
            assert "outcome" in r

    @patch("utils.option_outcomes._fetch_history")
    def test_1d_resolution_type_present(self, mock_hist):
        mock_hist.return_value = self._patched_history()
        results = resolve_snapshot(_snap(), run_resolution_types=("1d",))
        assert results[0]["resolution_type"] == "1d"

    @patch("utils.option_outcomes._fetch_history")
    def test_underlying_returns_populated(self, mock_hist):
        mock_hist.return_value = self._patched_history(entry_px=148.0, px_5d=155.0)
        snap = _snap()
        results = resolve_snapshot(snap, run_resolution_types=("5d",))
        outcome = results[0]["outcome"]
        assert outcome["underlying_return_5d_pct"] == pytest.approx(
            (155.0 - 148.0) / 148.0 * 100, abs=0.1
        )

    @patch("utils.option_outcomes._fetch_history")
    def test_hit_underlying_t1_bull(self, mock_hist):
        # t1 = 155.0, px_5d = 156.0 → should hit
        mock_hist.return_value = self._patched_history(entry_px=148.0, px_5d=156.0)
        snap = _snap(direction="BULL", und_t1=155.0)
        results = resolve_snapshot(snap, run_resolution_types=("5d",))
        assert results[0]["outcome"]["hit_underlying_t1"] is True

    @patch("utils.option_outcomes._fetch_history")
    def test_miss_underlying_t1_bull(self, mock_hist):
        # t1 = 160.0, px_5d = 150.0 → should miss
        mock_hist.return_value = self._patched_history(entry_px=148.0, px_5d=150.0)
        snap = _snap(direction="BULL", und_t1=160.0)
        results = resolve_snapshot(snap, run_resolution_types=("5d",))
        assert results[0]["outcome"]["hit_underlying_t1"] is False

    @patch("utils.option_outcomes._fetch_history")
    def test_hit_underlying_stop_bull(self, mock_hist):
        # stop = 145.0, px_5d = 142.0 → hits stop (bull)
        mock_hist.return_value = self._patched_history(entry_px=148.0, px_5d=142.0)
        snap = _snap(direction="BULL", und_stop=145.0)
        results = resolve_snapshot(snap, run_resolution_types=("5d",))
        assert results[0]["outcome"]["hit_underlying_stop"] is True

    @patch("utils.option_outcomes._fetch_history")
    def test_bear_hit_t1_when_price_falls(self, mock_hist):
        # bear setup: t1 = 130.0, entry = 148, px_5d = 128 → hits bear target
        mock_hist.return_value = self._patched_history(entry_px=148.0, px_5d=128.0)
        snap = _snap(direction="BEAR", und_t1=130.0, und_stop=155.0)
        results = resolve_snapshot(snap, run_resolution_types=("5d",))
        assert results[0]["outcome"]["hit_underlying_t1"] is True

    @patch("utils.option_outcomes._fetch_history")
    def test_no_entry_price_returns_empty(self, mock_hist):
        # Empty history → no entry price → return []
        mock_hist.return_value = pd.DataFrame()
        results = resolve_snapshot(_snap())
        assert results == []

    @patch("utils.option_outcomes._fetch_history")
    def test_missing_marks_handled_gracefully(self, mock_hist):
        # Only entry price available; resolution windows return None
        run_date = (_today() - timedelta(days=12)).isoformat()
        mock_hist.return_value = _make_hist({run_date: 148.0})
        snap = _snap()
        results = resolve_snapshot(snap, run_resolution_types=("1d",))
        # Should not raise; underlying_return_1d_pct may be None
        assert isinstance(results, list)

    def test_missing_ticker_returns_empty(self):
        snap = _snap()
        snap["ticker"] = ""
        results = resolve_snapshot(snap)
        assert results == []

    def test_missing_run_date_returns_empty(self):
        snap = _snap()
        snap["run_date"] = None
        snap["created_at"] = None
        results = resolve_snapshot(snap)
        assert results == []

    @patch("utils.option_outcomes._fetch_history")
    def test_repeated_resolution_does_not_fail(self, mock_hist):
        mock_hist.return_value = self._patched_history()
        snap = _snap()
        r1 = resolve_snapshot(snap)
        r2 = resolve_snapshot(snap)
        assert len(r1) == len(r2)


# ══════════════════════════════════════════════════════════════════════════════
# resolve_batch
# ══════════════════════════════════════════════════════════════════════════════

class TestResolveBatch:
    @patch("utils.supabase_persist.save_option_candidate_outcome")
    @patch("utils.option_outcomes._fetch_history")
    def test_batch_resolves_multiple_snapshots(self, mock_hist, mock_save):
        run_date = (_today() - timedelta(days=12)).isoformat()
        entry_dt = date.fromisoformat(run_date)
        prices = {
            run_date:                                      148.0,
            (entry_dt + timedelta(days=1)).isoformat():   150.0,
        }
        mock_hist.return_value = _make_hist(prices)
        mock_save.return_value = True

        snaps = [_snap(ticker="AAPL"), _snap(ticker="MSFT")]
        counts = resolve_batch(snaps, resolution_types=("1d",), persist=True)
        assert counts["resolved"] >= 2
        assert counts["persisted"] >= 2

    @patch("utils.option_outcomes._fetch_history")
    def test_batch_no_persist(self, mock_hist):
        run_date = (_today() - timedelta(days=12)).isoformat()
        entry_dt = date.fromisoformat(run_date)
        prices = {
            run_date:                                      148.0,
            (entry_dt + timedelta(days=1)).isoformat():   150.0,
        }
        mock_hist.return_value = _make_hist(prices)
        counts = resolve_batch([_snap()], resolution_types=("1d",), persist=False)
        assert counts["persisted"] == 0

    @patch("utils.option_outcomes._fetch_history")
    def test_batch_handles_individual_failures(self, mock_hist):
        # First snapshot succeeds, second has empty history
        call_count = 0
        def hist_side_effect(ticker, start, end):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                run_date = (_today() - timedelta(days=12)).isoformat()
                entry_dt = date.fromisoformat(run_date)
                return _make_hist({
                    run_date: 148.0,
                    (entry_dt + timedelta(days=1)).isoformat(): 150.0,
                })
            return pd.DataFrame()

        mock_hist.side_effect = hist_side_effect
        counts = resolve_batch(
            [_snap(ticker="AAPL"), _snap(ticker="MSFT")],
            resolution_types=("1d",), persist=False,
        )
        # Should not raise; failed snapshots tracked
        assert isinstance(counts, dict)
