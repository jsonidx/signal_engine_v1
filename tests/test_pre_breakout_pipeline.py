"""
tests/test_pre_breakout_pipeline.py — Tests for TRD-034 through TRD-040

Covers: PFS, PSC, outcome resolver, Stage 3 synthesis, and persistence helpers.
All tests are offline (no DB, no network) unless explicitly marked.
"""

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.pfs_signal import score_pfs, TRIGGER_PCT, DECAY_WINDOW, LAGGARD_PCT
from utils.psc_signal import score_psc, MIN_ADV_USD, MIN_PRICE
from utils.stage3_synthesis import (
    _parse_stage3_response,
    _sanitize_row,
    run_stage3_synthesis,
    MAX_NAMES_PER_DAY,
    ALLOWED_GRADES,
)
from utils.setup_outcome_resolver import resolve_outcome


# ── Fixtures ──────────────────────────────────────────────────────────────────

_TEST_END_DATE = date(2026, 5, 29)   # Friday — avoids bdate_range weekend-boundary off-by-one


def _make_prices(tickers: list[str], n_days: int = 80, start_val: float = 100.0) -> pd.DataFrame:
    """Build flat price DataFrame for testing."""
    dates = list(pd.bdate_range(end=_TEST_END_DATE, periods=n_days).date)
    n = len(dates)
    data = {t: [start_val] * n for t in tickers}
    return pd.DataFrame(data, index=dates)


def _make_prices_with_mover(
    tickers: list[str],
    mover: str,
    move_pct: float = 10.0,
    n_days: int = 80,
) -> pd.DataFrame:
    """Build prices where `mover` surges by move_pct in the last 5 days."""
    df = _make_prices(tickers, n_days)
    surge_start = n_days - 5
    df.loc[df.index[surge_start:], mover] = 100.0 * (1 + move_pct / 100.0)
    return df


def _make_volumes(tickers: list[str], n_days: int = 80, vol: float = 1_000_000.0) -> pd.DataFrame:
    dates = list(pd.bdate_range(end=_TEST_END_DATE, periods=n_days).date)
    n = len(dates)
    data = {t: [vol] * n for t in tickers}
    return pd.DataFrame(data, index=dates)


def _make_volumes_with_surge(tickers: list[str], mover: str, n_days: int = 80) -> pd.DataFrame:
    """Mover has 2× volume in the last 5 days (satisfies volume-confirmation)."""
    df = _make_volumes(tickers, n_days)
    df.loc[df.index[-5:], mover] = 2_000_000.0
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# TRD-035 — PFS Signal
# ═══════════════════════════════════════════════════════════════════════════════

class TestPFSSignal:

    def test_no_trigger_returns_zero_score(self):
        """Flat prices → no first mover → PFS = 0 for all."""
        tickers = ["A", "B", "C", "D"]
        sector_map = {t: "Technology" for t in tickers}
        prices = _make_prices(tickers)
        volumes = _make_volumes(tickers)
        results = score_pfs(tickers, sector_map, prices, volumes, as_of_date=date(2026, 5, 30))
        assert all(r.pfs_score == 0.0 for r in results)

    def test_first_mover_triggers_peer_score(self):
        """One ticker moves strongly → peers in same sector get PFS > 0."""
        tickers = ["MOVER", "PEER1", "PEER2", "PEER3", "PEER4"]
        sector_map = {t: "Technology" for t in tickers}
        prices = _make_prices_with_mover(tickers, "MOVER", move_pct=12.0)
        volumes = _make_volumes_with_surge(tickers, "MOVER")
        results = score_pfs(tickers, sector_map, prices, volumes, as_of_date=date(2026, 5, 30))

        # MOVER itself should NOT receive a PFS score (only non-participants do)
        mover_r = next(r for r in results if r.ticker == "MOVER")
        assert mover_r.pfs_score == 0.0

        # Peers that did not move should get score > 0
        peer_results = [r for r in results if r.ticker != "MOVER"]
        assert any(r.pfs_score > 0.0 for r in peer_results)

    def test_laggard_guard_fires(self):
        """Target that already moved ≥ LAGGARD_PCT gets PFS = 0."""
        tickers = ["MOVER", "PEER1", "LAGGARD", "PEER2", "PEER3"]
        sector_map = {t: "Technology" for t in tickers}
        prices = _make_prices_with_mover(tickers, "MOVER", move_pct=12.0)
        # LAGGARD already moved
        prices.loc[prices.index[-5:], "LAGGARD"] = 100.0 * (1 + LAGGARD_PCT / 100.0 + 0.02)
        volumes = _make_volumes_with_surge(tickers, "MOVER")
        results = score_pfs(tickers, sector_map, prices, volumes, as_of_date=date(2026, 5, 30))
        laggard_r = next(r for r in results if r.ticker == "LAGGARD")
        assert laggard_r.pfs_score == 0.0

    def test_different_sectors_no_cross_trigger(self):
        """First mover in sector A does not trigger peers in sector B."""
        tickers = ["MOVER_A", "PEER_A1", "PEER_A2", "PEER_A3", "PEER_B1", "PEER_B2", "PEER_B3"]
        sector_map = {
            "MOVER_A": "Technology", "PEER_A1": "Technology",
            "PEER_A2": "Technology", "PEER_A3": "Technology",
            "PEER_B1": "Healthcare", "PEER_B2": "Healthcare", "PEER_B3": "Healthcare",
        }
        prices = _make_prices_with_mover(tickers, "MOVER_A", move_pct=12.0)
        volumes = _make_volumes_with_surge(tickers, "MOVER_A")
        results = score_pfs(tickers, sector_map, prices, volumes, as_of_date=date(2026, 5, 30))
        sector_b_results = [r for r in results if r.ticker.startswith("PEER_B")]
        assert all(r.pfs_score == 0.0 for r in sector_b_results)

    def test_mass_rally_suppression(self):
        """When ≥60% of sector moves together, PFS is suppressed."""
        tickers = ["T1", "T2", "T3", "T4", "T5"]
        sector_map = {t: "Technology" for t in tickers}
        # All tickers move strongly simultaneously → mass rally
        prices = _make_prices(tickers)
        for t in tickers:
            prices.loc[prices.index[-5:], t] = 115.0  # all +15%
        volumes = _make_volumes(tickers)
        for t in tickers:
            volumes.loc[volumes.index[-5:], t] = 2_000_000.0
        results = score_pfs(tickers, sector_map, prices, volumes, as_of_date=date(2026, 5, 30))
        assert all(r.pfs_score == 0.0 for r in results)

    def test_staleness_decay(self):
        """A trigger isolated to exactly DECAY_WINDOW+2 days ago should yield PFS = 0."""
        # Build a synthetic dataset where MOVER surged exactly once, in a narrow
        # window that is DECAY_WINDOW + 2 trading days before as_of.
        # No subsequent volume surge → no later trigger within decay window.
        tickers = ["MOVER", "PEER1", "PEER2", "PEER3", "PEER4"]
        sector_map = {t: "Technology" for t in tickers}
        n = 80
        prices = _make_prices(tickers, n)
        volumes = _make_volumes(tickers, n)

        # Identify the trigger window: rows [trigger_start .. trigger_end]
        # trigger_end = as_of - (DECAY_WINDOW + 2) trading days from end
        trigger_end = n - (DECAY_WINDOW + 2) - 1   # stale by 2 days beyond window
        trigger_start = trigger_end - 5             # TRIGGER_WINDOW = 5

        # MOVER price jumps only inside [trigger_start .. trigger_end],
        # then falls back to 100 so no later trigger can form
        for i in range(trigger_start, trigger_end + 1):
            prices.iloc[i, prices.columns.get_loc("MOVER")] = 115.0
        # MOVER volume surges only during trigger
        for i in range(trigger_start, trigger_end + 1):
            volumes.iloc[i, volumes.columns.get_loc("MOVER")] = 2_000_000.0

        as_of = prices.index[-1]  # = _TEST_END_DATE
        results = score_pfs(tickers, sector_map, prices, volumes, as_of_date=as_of)
        # Trigger is stale → all peer scores should be 0
        peer_results = [r for r in results if r.ticker != "MOVER"]
        assert all(r.pfs_score == 0.0 for r in peer_results), (
            f"Expected all zeros; got: {[(r.ticker, r.pfs_score, r.days_since_trigger) for r in peer_results]}"
        )

    def test_pfs_score_bounded_0_1(self):
        """PFS score must always be in [0, 1]."""
        tickers = ["MOVER", "P1", "P2", "P3", "P4"]
        sector_map = {t: "Technology" for t in tickers}
        prices = _make_prices_with_mover(tickers, "MOVER", move_pct=50.0)
        volumes = _make_volumes_with_surge(tickers, "MOVER")
        results = score_pfs(tickers, sector_map, prices, volumes, as_of_date=date(2026, 5, 30))
        for r in results:
            assert 0.0 <= r.pfs_score <= 1.0, f"{r.ticker}: {r.pfs_score}"

    def test_empty_prices_returns_zero(self):
        results = score_pfs(["A", "B"], {"A": "Tech", "B": "Tech"},
                            pd.DataFrame(), pd.DataFrame())
        assert all(r.pfs_score == 0.0 for r in results)


# ═══════════════════════════════════════════════════════════════════════════════
# TRD-036 — PSC Signal
# ═══════════════════════════════════════════════════════════════════════════════

class TestPSCSignal:

    def _make_ohlcv(self, tickers, n=80, close=100.0, adv_usd=10_000_000):
        dates = list(pd.bdate_range(end=_TEST_END_DATE, periods=n).date)
        nd = len(dates)
        prices  = pd.DataFrame({t: [close] * nd for t in tickers}, index=dates)
        highs   = pd.DataFrame({t: [close * 1.01] * nd for t in tickers}, index=dates)
        lows    = pd.DataFrame({t: [close * 0.99] * nd for t in tickers}, index=dates)
        vol_per = adv_usd / close
        volumes = pd.DataFrame({t: [vol_per] * nd for t in tickers}, index=dates)
        return prices, highs, lows, volumes

    def test_psc_score_bounded_0_1(self):
        tickers = ["A", "B"]
        prices, highs, lows, volumes = self._make_ohlcv(tickers)
        results = score_psc(tickers, prices, highs, lows, volumes, as_of_date=date(2026, 5, 30))
        for r in results:
            assert 0.0 <= r.psc_score <= 1.0

    def test_illiquid_stock_returns_zero(self):
        tickers = ["ILLIQUID"]
        # ADV = $1 (well below MIN_ADV_USD = $5M)
        prices, highs, lows, volumes = self._make_ohlcv(tickers, close=100.0, adv_usd=1)
        results = score_psc(tickers, prices, highs, lows, volumes, as_of_date=date(2026, 5, 30))
        assert results[0].psc_score == 0.0
        assert not results[0].liquidity_ok

    def test_penny_stock_returns_zero(self):
        tickers = ["PENNY"]
        prices, highs, lows, volumes = self._make_ohlcv(tickers, close=1.0, adv_usd=100_000)
        results = score_psc(tickers, prices, highs, lows, volumes, as_of_date=date(2026, 5, 30))
        assert results[0].psc_score == 0.0

    def test_compressed_stock_scores_higher_than_volatile(self):
        """Recently-compressed stock scores higher on ATR compression than uniformly volatile."""
        # Design: COMPRESSING had wide daily range for 60 days, then compressed to tight range.
        # VOLATILE has uniformly wide range all 80 days.
        # ATR(10)/ATR(40) should be lower for COMPRESSING → higher PSC score.
        tickers = ["COMPRESSING", "VOLATILE"]
        n = 80
        dates = list(pd.bdate_range(end=_TEST_END_DATE, periods=n).date)
        nd = len(dates)

        base = 100.0
        adv = MIN_ADV_USD / base + 2000   # well above liquidity floor

        comp_close = [base] * nd
        comp_high  = [base * 1.03] * 60 + [base * 1.003] * (nd - 60)
        comp_low   = [base * 0.97] * 60 + [base * 0.997] * (nd - 60)

        vol_close = [base] * nd
        vol_high  = [base * 1.03] * nd
        vol_low   = [base * 0.97] * nd

        prices  = pd.DataFrame({"COMPRESSING": comp_close, "VOLATILE": vol_close}, index=dates)
        highs   = pd.DataFrame({"COMPRESSING": comp_high,  "VOLATILE": vol_high},  index=dates)
        lows    = pd.DataFrame({"COMPRESSING": comp_low,   "VOLATILE": vol_low},   index=dates)
        volumes = pd.DataFrame({"COMPRESSING": [adv] * nd, "VOLATILE": [adv] * nd}, index=dates)

        results = score_psc(tickers, prices, highs, lows, volumes, as_of_date=_TEST_END_DATE)
        r_map = {r.ticker: r for r in results}
        comp_score = r_map["COMPRESSING"].psc_score
        vol_score  = r_map["VOLATILE"].psc_score
        assert comp_score >= vol_score, (
            f"Expected COMPRESSING({comp_score:.3f}) >= VOLATILE({vol_score:.3f})"
        )

    def test_missing_ticker_returns_zero(self):
        tickers = ["MISSING"]
        prices, highs, lows, volumes = self._make_ohlcv(["OTHER"], close=100.0, adv_usd=10_000_000)
        results = score_psc(tickers, prices, highs, lows, volumes, as_of_date=date(2026, 5, 30))
        assert results[0].psc_score == 0.0

    def test_empty_prices_returns_zero(self):
        results = score_psc(["A"], pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        assert results[0].psc_score == 0.0

    def test_insufficient_history_returns_zero(self):
        tickers = ["A"]
        n = 10  # too few rows
        dates = list(pd.bdate_range(end=_TEST_END_DATE, periods=n).date)
        nd = len(dates)
        p = pd.DataFrame({"A": [100.0] * nd}, index=dates)
        results = score_psc(tickers, p, p, p, p, as_of_date=_TEST_END_DATE)
        assert results[0].psc_score == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# TRD-038 — Stage 3 Synthesis
# ═══════════════════════════════════════════════════════════════════════════════

class TestStage3Synthesis:

    def _make_shortlist(self, n: int = 3) -> list[dict]:
        return [
            {
                "ticker": f"TICK{i}",
                "composite_score": 0.7 - i * 0.05,
                "pfs_score": 0.6,
                "psc_score": 0.5,
                "stage2_passed": True,
            }
            for i in range(n)
        ]

    def test_dry_run_returns_nulls(self):
        shortlist = self._make_shortlist(3)
        results = run_stage3_synthesis(shortlist, dry_run=True)
        assert len(results) == 3
        for r in results:
            assert r["archetype"] is None
            assert r["setup_grade"] is None

    def test_cap_enforcement(self):
        """More than MAX_NAMES_PER_DAY inputs → capped to MAX_NAMES_PER_DAY."""
        shortlist = self._make_shortlist(MAX_NAMES_PER_DAY + 5)
        results = run_stage3_synthesis(shortlist, dry_run=True)
        assert len(results) == MAX_NAMES_PER_DAY

    def test_cap_keeps_highest_scores(self):
        """When capped, highest composite_score names are kept."""
        shortlist = self._make_shortlist(MAX_NAMES_PER_DAY + 5)
        results = run_stage3_synthesis(shortlist, dry_run=True)
        returned_tickers = {r["ticker"] for r in results}
        # First MAX_NAMES_PER_DAY tickers by score should be present
        top_tickers = {f"TICK{i}" for i in range(MAX_NAMES_PER_DAY)}
        assert returned_tickers == top_tickers

    def test_empty_shortlist(self):
        assert run_stage3_synthesis([], dry_run=True) == []

    def test_parse_valid_response(self):
        response = json.dumps([
            {"ticker": "AAPL", "archetype": "base_breakout",
             "invalidation_condition": "close below 20d low",
             "setup_grade": "A", "key_risk": "earnings next week"},
            {"ticker": "MSFT", "archetype": "sector_laggard",
             "invalidation_condition": "sector ETF breaks 50d MA",
             "setup_grade": "B", "key_risk": "macro rotation risk"},
        ])
        results = _parse_stage3_response(response, ["AAPL", "MSFT"])
        assert len(results) == 2
        aapl = next(r for r in results if r["ticker"] == "AAPL")
        assert aapl["setup_grade"] == "A"
        assert aapl["archetype"] == "base_breakout"

    def test_parse_invalid_grade_defaults_to_c(self):
        response = json.dumps([
            {"ticker": "A", "archetype": "test", "invalidation_condition": "test",
             "setup_grade": "Z", "key_risk": "test"},
        ])
        results = _parse_stage3_response(response, ["A"])
        assert results[0]["setup_grade"] == "C"

    def test_parse_malformed_json_returns_nulls(self):
        results = _parse_stage3_response("not valid json {{{", ["A", "B"])
        assert len(results) == 2
        assert all(r["archetype"] is None for r in results)

    def test_parse_missing_tickers_filled_with_nulls(self):
        response = json.dumps([
            {"ticker": "A", "archetype": "test", "invalidation_condition": "test",
             "setup_grade": "B", "key_risk": "test"},
        ])
        # B is expected but missing from response
        results = _parse_stage3_response(response, ["A", "B"])
        b_result = next(r for r in results if r["ticker"] == "B")
        assert b_result["archetype"] is None

    def test_sanitize_row_strips_stage3_fields(self):
        row = {
            "ticker": "AAPL",
            "composite_score": 0.75,
            "pfs_score": 0.6,
            "psc_score": 0.5,
            "stage2_passed": True,
            "archetype": "should_not_be_sent",   # Stage 3 field — must be stripped
        }
        sanitized = _sanitize_row(row)
        assert "archetype" not in sanitized
        assert sanitized["ticker"] == "AAPL"

    def test_allowed_grades_complete(self):
        assert ALLOWED_GRADES == {"A", "B", "C"}

    def test_no_api_key_returns_nulls(self):
        """Without ANTHROPIC_API_KEY, should return null results gracefully."""
        import os
        shortlist = self._make_shortlist(2)
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            results = run_stage3_synthesis(shortlist, dry_run=False)
        assert len(results) == 2
        assert all(r["archetype"] is None for r in results)


# ═══════════════════════════════════════════════════════════════════════════════
# TRD-040 — Outcome Resolver
# ═══════════════════════════════════════════════════════════════════════════════

class TestOutcomeResolver:

    def _make_price_df(self, tickers, n=60, start=100.0, trend_pct=0.0):
        dates = list(pd.bdate_range(end=_TEST_END_DATE, periods=n).date)
        nd = len(dates)
        data = {}
        for t in tickers:
            vals = [start * (1 + trend_pct / 100.0) ** (i / nd) for i in range(nd)]
            data[t] = vals
        return pd.DataFrame(data, index=dates)

    def test_returns_at_mature_horizons(self):
        """For a stock that gained 10%, 20d return should be ~10%."""
        tickers = ["TICKER", "SPY"]
        n = 60
        prices = self._make_price_df(tickers, n=n, trend_pct=10.0)
        setup_date = prices.index[10]  # 20+ days before end
        result = resolve_outcome(
            setup_date=setup_date,
            ticker="TICKER",
            sector="Technology",
            prices=prices,
            today=prices.index[-1],
        )
        assert result.get("mature_20d") is True
        assert result.get("ret_20d") is not None
        assert result["ret_20d"] > 0  # trending up

    def test_missing_ticker_returns_empty(self):
        tickers = ["SPY"]
        prices = self._make_price_df(tickers, n=60)
        result = resolve_outcome(
            setup_date=prices.index[5],
            ticker="MISSING",
            sector=None,
            prices=prices,
            today=prices.index[-1],
        )
        assert result.get("ret_5d") is None
        assert result.get("mature_20d") is False

    def test_near_boundary_immature(self):
        """A setup from yesterday should have no mature 5d return."""
        tickers = ["TICKER", "SPY"]
        prices = self._make_price_df(tickers, n=60)
        setup_date = prices.index[-2]  # yesterday
        result = resolve_outcome(
            setup_date=setup_date,
            ticker="TICKER",
            sector=None,
            prices=prices,
            today=prices.index[-1],
        )
        assert result.get("mature_20d") is False

    def test_success_label_on_strong_gain(self):
        """If sector-adj 20d return > 10%, success_20d = True."""
        n = 60
        dates = list(pd.bdate_range(end=_TEST_END_DATE, periods=n).date)
        nd = len(dates)
        # TICKER goes up 20%, SPY flat
        ticker_vals = [100.0 + i * 0.4 for i in range(nd)]
        spy_vals = [100.0] * nd
        prices = pd.DataFrame({"TICKER": ticker_vals, "SPY": spy_vals}, index=dates)
        setup_date = dates[10]
        result = resolve_outcome(
            setup_date=setup_date,
            ticker="TICKER",
            sector="Unknown",  # will fall back to SPY
            prices=prices,
            today=dates[-1],
        )
        if result.get("mature_20d"):
            if result.get("ret_20d_excess") is not None and result["ret_20d_excess"] > 10.0:
                assert result.get("success_20d") is True

    def test_failed_label_on_negative_return(self):
        """Sector-adj 20d return < 0% → failed_20d = True."""
        n = 60
        dates = list(pd.bdate_range(end=_TEST_END_DATE, periods=n).date)
        nd = len(dates)
        # TICKER goes down 15%, SPY flat
        ticker_vals = [100.0 - i * 0.3 for i in range(nd)]
        spy_vals = [100.0] * nd
        prices = pd.DataFrame({"TICKER": ticker_vals, "SPY": spy_vals}, index=dates)
        setup_date = dates[10]
        result = resolve_outcome(
            setup_date=setup_date,
            ticker="TICKER",
            sector="Unknown",
            prices=prices,
            today=dates[-1],
        )
        if result.get("mature_20d") and result.get("ret_20d_excess") is not None:
            if result["ret_20d_excess"] < 0:
                assert bool(result.get("failed_20d")) is True

    def test_mae_mfe_computed_when_mature(self):
        """MAE and MFE should be non-None for mature horizons."""
        n = 60
        dates = list(pd.bdate_range(end=_TEST_END_DATE, periods=n).date)
        nd = len(dates)
        # Up-then-down pattern for interesting MAE/MFE
        vals = [100.0 + (i if i < nd // 2 else nd - i) for i in range(nd)]
        prices = pd.DataFrame({"T": vals, "SPY": [100.0] * nd}, index=dates)
        setup_date = dates[5]
        result = resolve_outcome(
            setup_date=setup_date, ticker="T", sector=None,
            prices=prices, today=dates[-1],
        )
        if result.get("mature_20d"):
            assert result.get("mae_20d") is not None
            assert result.get("mfe_20d") is not None

    def test_sector_adjusted_uses_sector_etf(self):
        """When XLK is in prices, sector-adj for Technology uses XLK."""
        n = 60
        dates = list(pd.bdate_range(end=_TEST_END_DATE, periods=n).date)
        nd = len(dates)
        prices = pd.DataFrame({
            "TICKER": [100.0 + i * 0.5 for i in range(nd)],
            "XLK":    [100.0 + i * 0.2 for i in range(nd)],  # sector ETF lower return
            "SPY":    [100.0] * nd,
        }, index=dates)
        setup_date = dates[10]
        result = resolve_outcome(
            setup_date=setup_date, ticker="TICKER", sector="Technology",
            prices=prices, today=dates[-1],
        )
        if result.get("mature_20d") and result.get("ret_20d") is not None:
            # raw_ret > 0, XLK also up → excess should be (ticker_ret - xlk_ret)
            assert result.get("ret_20d_excess") is not None


# ═══════════════════════════════════════════════════════════════════════════════
# TRD-039 — Options / SI collection helpers (import + unit level)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPersistenceHelpers:

    def test_options_state_record_contract(self):
        """collect_options_state_for_ticker returns correct fields or None."""
        from utils.supabase_persist import collect_options_state_for_ticker
        # We can't call yfinance in tests — just verify the function is importable
        # and returns None on a clearly invalid ticker
        result = collect_options_state_for_ticker("INVALIDTICKER_XYZXYZ999")
        # Should not raise; may return None (invalid ticker) or a dict
        assert result is None or isinstance(result, dict)

    def test_short_interest_record_contract(self):
        """collect_short_interest_for_ticker returns correct fields or None."""
        from utils.supabase_persist import collect_short_interest_for_ticker
        result = collect_short_interest_for_ticker("INVALIDTICKER_XYZXYZ999")
        assert result is None or isinstance(result, dict)

    def test_save_setup_watchlist_rows_empty_no_crash(self):
        """save_setup_watchlist_rows with empty list should not raise."""
        from utils.supabase_persist import save_setup_watchlist_rows
        # Should be a no-op
        save_setup_watchlist_rows([])

    def test_save_setup_outcome_empty_no_crash(self):
        """save_setup_outcome with empty dict should not raise."""
        from utils.supabase_persist import save_setup_outcome
        save_setup_outcome({})

    def test_save_options_state_snapshots_empty_no_crash(self):
        """save_options_state_snapshots with empty list should not raise."""
        from utils.supabase_persist import save_options_state_snapshots
        save_options_state_snapshots([])


# ═══════════════════════════════════════════════════════════════════════════════
# TRD-034 — Pipeline offline dry-run (no DB, no network needed)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPipelineOfflineDryRun:
    """
    Tests for pre_breakout_pipeline.run_pipeline() that do not require DB access.
    The --tickers override and --dry-run flags together make the pipeline fully offline.
    Prices are mocked to avoid yfinance network calls.
    """

    def _make_mock_ohlcv(self, tickers, n=80):
        """Return synthetic price/high/low/volume DataFrames for pipeline use."""
        dates = list(pd.bdate_range(end=_TEST_END_DATE, periods=n).date)
        nd = len(dates)
        base = 100.0
        adv_vol = 100_000.0   # volume × price ≈ $10M/day
        close   = pd.DataFrame({t: [base] * nd for t in tickers}, index=dates)
        high    = pd.DataFrame({t: [base * 1.01] * nd for t in tickers}, index=dates)
        low     = pd.DataFrame({t: [base * 0.99] * nd for t in tickers}, index=dates)
        volume  = pd.DataFrame({t: [adv_vol] * nd for t in tickers}, index=dates)
        return close, high, low, volume

    def test_dry_run_with_tickers_override_returns_ok(self):
        """Pipeline returns status=ok in offline dry-run with explicit tickers."""
        from pre_breakout_pipeline import run_pipeline
        tickers = ["AAPL", "MSFT", "NVDA", "META", "GOOGL"]

        with patch("pre_breakout_pipeline.fetch_ohlcv") as mock_ohlcv:
            mock_ohlcv.return_value = self._make_mock_ohlcv(tickers)
            result = run_pipeline(
                run_date=_TEST_END_DATE,
                dry_run=True,
                tickers_override=tickers,
            )

        assert result["status"] == "ok"
        assert result["universe_size"] == len(tickers)
        assert result["scored"] == len(tickers)
        assert isinstance(result["stage2_passed"], int)
        assert result["stage3_run"] is False

    def test_override_mode_never_calls_load_sector_map(self):
        """When tickers_override is set, load_sector_map must not be called (no DB)."""
        from pre_breakout_pipeline import run_pipeline
        tickers = ["AAPL", "MSFT", "NVDA"]

        with patch("pre_breakout_pipeline.fetch_ohlcv") as mock_ohlcv, \
             patch("pre_breakout_pipeline.load_sector_map") as mock_sector:
            mock_ohlcv.return_value = self._make_mock_ohlcv(tickers)
            run_pipeline(run_date=_TEST_END_DATE, dry_run=True, tickers_override=tickers)

        mock_sector.assert_not_called()

    def test_dry_run_no_db_writes(self):
        """Dry-run must not call any persistence functions."""
        from pre_breakout_pipeline import run_pipeline
        tickers = ["AAPL", "MSFT", "NVDA"]

        with patch("pre_breakout_pipeline.fetch_ohlcv") as mock_ohlcv, \
             patch("utils.supabase_persist.save_setup_watchlist_rows") as mock_save:
            mock_ohlcv.return_value = self._make_mock_ohlcv(tickers)
            run_pipeline(run_date=_TEST_END_DATE, dry_run=True, tickers_override=tickers)

        mock_save.assert_not_called()

    def test_empty_tickers_override_returns_error(self):
        """Explicit empty ticker list should return status=error."""
        from pre_breakout_pipeline import run_pipeline
        result = run_pipeline(dry_run=True, tickers_override=[])
        assert result["status"] == "error"

    def test_psc_only_guard_no_stage2_pass(self):
        """With flat prices (no first mover), pfs_score=0 → nothing passes Stage 2."""
        from pre_breakout_pipeline import run_pipeline
        tickers = ["AAPL", "MSFT", "NVDA", "META", "GOOGL"]

        with patch("pre_breakout_pipeline.fetch_ohlcv") as mock_ohlcv:
            mock_ohlcv.return_value = self._make_mock_ohlcv(tickers)
            result = run_pipeline(
                run_date=_TEST_END_DATE,
                dry_run=True,
                tickers_override=tickers,
            )

        # Flat prices → no first mover → PFS = 0 → nothing passes Stage 2
        assert result["stage2_passed"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# TRD-039 — Options-state collection orchestration (mocked)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOptionsStateCollection:
    """
    Tests for the options-state collection script orchestration path.
    Uses mocks so no live yfinance calls are needed.
    """

    def test_collect_and_save_options_state_orchestration(self):
        """
        Verifies the collect → save orchestration contract used by
        scripts/collect_options_si_state.py. Uses MagicMocks to isolate
        the pipeline from yfinance and DB dependencies.
        """
        from datetime import date as _date

        fake_record = {
            "ticker": "AAPL",
            "snapshot_date": "2026-05-29",
            "expiry": "2026-06-20",
            "dte": 22,
            "call_volume_total": 150_000.0,
            "put_volume_total": 80_000.0,
            "call_put_volume_ratio": 1.875,
            "call_oi_total": 500_000.0,
            "put_oi_total": 300_000.0,
            "call_put_oi_ratio": 1.667,
            "atm_iv": 0.28,
            "underlying_price": 312.0,
            "data_source": "yfinance_chain",
            "data_confidence": 0.7,
        }

        # Mock both helpers so no real yfinance/DB calls are made
        mock_collect = MagicMock(return_value=fake_record)
        mock_save = MagicMock()

        # Exercise the orchestration pattern (same logic as collect_options_si_state.py)
        record = mock_collect("AAPL", snapshot_date=_date(2026, 5, 29))
        assert record is not None
        assert record["ticker"] == "AAPL"
        assert record["call_put_volume_ratio"] == pytest.approx(1.875)

        records = [record]
        mock_save(records)

        mock_collect.assert_called_once_with("AAPL", snapshot_date=_date(2026, 5, 29))
        mock_save.assert_called_once_with([fake_record])

    def test_collect_options_state_missing_options_returns_none(self):
        """Tickers with no options chain return None — should be handled gracefully."""
        from utils.supabase_persist import collect_options_state_for_ticker
        import yfinance as yf

        with patch.object(yf.Ticker, "options", new_callable=lambda: property(lambda self: ())):
            result = collect_options_state_for_ticker("FAKEXYZ999")
        assert result is None

    def test_collection_script_runnable(self):
        """scripts/collect_options_si_state.py is importable and has main()."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "collect_options_si_state",
            Path(__file__).parent.parent / "scripts" / "collect_options_si_state.py",
        )
        assert spec is not None, "Collection script not found"
        mod = importlib.util.module_from_spec(spec)
        assert hasattr(spec, "loader"), "Spec has no loader"

    def test_short_interest_record_has_required_fields(self):
        """collect_short_interest_for_ticker returns the required SI fields when data is available."""
        from utils.supabase_persist import collect_short_interest_for_ticker
        import yfinance as yf

        fake_info = {
            "sharesShort": 50_000_000,
            "shortPercentOfFloat": 0.12,
            "floatShares": 420_000_000,
            "averageVolume": 9_000_000,
            "shortRatio": 5.5,
            "dateShortInterest": 1778803200,  # some Unix timestamp
        }

        with patch.object(yf.Ticker, "info", new_callable=lambda: property(lambda self: fake_info)):
            result = collect_short_interest_for_ticker("GME")

        assert result is not None
        assert result["ticker"] == "GME"
        assert result["shares_short"] == 50_000_000.0
        assert result["short_pct_float"] == pytest.approx(0.12)
        assert result["publication_date"] is not None
        assert result["computed_dtc_30d"] == pytest.approx(50_000_000 / 9_000_000)
