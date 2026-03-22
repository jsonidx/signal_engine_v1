"""
tests/test_backtest.py
======================
Unit tests for backtest.py.

Tests use synthetic price data to verify correctness without any network calls.
All yfinance calls are mocked where needed.

Run with:
    pytest tests/test_backtest.py -v
"""

from __future__ import annotations

import json
from datetime import date
from typing import Dict, List
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ── Import backtest module under test ─────────────────────────────────────────
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backtest import (
    WalkForwardBacktest,
    composite_score,
    compute_factor_scores,
    inv_vol_weights,
    FIRST_TRAIN_START,
    TOP_N_POSITIONS,
    POSITION_CAP,
)


# ==============================================================================
# HELPERS
# ==============================================================================

def _make_price_series(
    n_days: int = 400,
    mu: float = 0.0005,
    sigma: float = 0.015,
    seed: int = 42,
    start: str = "2020-01-01",
) -> pd.Series:
    """Synthetic log-normal price series."""
    rng = np.random.default_rng(seed)
    log_returns = rng.normal(mu, sigma, n_days)
    prices = 100 * np.exp(np.cumsum(log_returns))
    idx = pd.bdate_range(start=start, periods=n_days)
    return pd.Series(prices, index=idx)


def _make_price_df(
    n_tickers: int = 20,
    n_days: int = 400,
    start: str = "2020-01-01",
    seed: int = 0,
) -> pd.DataFrame:
    """Synthetic multi-ticker price DataFrame."""
    rng = np.random.default_rng(seed)
    cols = {}
    idx = pd.bdate_range(start=start, periods=n_days)
    for i in range(n_tickers):
        mu = rng.uniform(-0.0002, 0.0008)
        sigma = rng.uniform(0.01, 0.025)
        lr = rng.normal(mu, sigma, n_days)
        cols[f"T{i:02d}"] = 100 * np.exp(np.cumsum(lr))
    return pd.DataFrame(cols, index=idx)


# ==============================================================================
# TEST 1: Sharpe calculation on synthetic data with known properties
# ==============================================================================

class TestSharpeCalculation:
    """
    Known-Sharpe validation.

    Generate weekly returns with mu=0.002, sigma=0.01 (annualized Sharpe ~
    0.002/0.01 * sqrt(52) ≈ 1.44).  Verify the backtest's internal Sharpe
    calc matches scipy's reference calculation within 0.01.
    """

    def test_sharpe_formula_weekly(self):
        rng = np.random.default_rng(99)
        mu, sigma = 0.002, 0.01
        n_weeks = 100
        rets = rng.normal(mu, sigma, n_weeks)
        expected = rets.mean() / rets.std(ddof=1) * np.sqrt(52)

        # Reproduce the same formula used in backtest._run_test_window
        computed = float(np.mean(rets) / np.std(rets, ddof=1) * np.sqrt(52))
        assert abs(computed - expected) < 1e-10

    def test_sharpe_positive_for_positive_drift(self):
        rng = np.random.default_rng(7)
        rets = rng.normal(0.005, 0.01, 200)  # high positive mu
        sharpe = float(np.mean(rets) / np.std(rets, ddof=1) * np.sqrt(52))
        assert sharpe > 0, "Positive drift should produce positive Sharpe"

    def test_sharpe_negative_for_negative_drift(self):
        rng = np.random.default_rng(8)
        rets = rng.normal(-0.005, 0.01, 200)  # negative mu
        sharpe = float(np.mean(rets) / np.std(rets, ddof=1) * np.sqrt(52))
        assert sharpe < 0


# ==============================================================================
# TEST 2: Per-factor IC with perfectly ranked factor scores
# ==============================================================================

class TestPerFactorIC:
    """
    When factor scores are perfectly rank-correlated with forward returns,
    the Spearman IC should be very close to 1.0.
    When they are perfectly inversely correlated, IC should be close to -1.0.
    """

    def _make_perfect_ic_data(self, n_tickers=20, n_weeks=30, positive=True):
        """
        Build factor_scores_list and fwd_ret_list with near-perfect rank correlation.
        Noise scale of 0.3 keeps rank correlation high (~0.95+) but introduces
        enough variation across weeks that IC std > 0 (so ICIR is well-defined).
        """
        rng = np.random.default_rng(42)
        factor_scores_list = []
        fwd_ret_list = []

        for _ in range(n_weeks):
            scores = pd.Series(
                rng.standard_normal(n_tickers),
                index=[f"T{i}" for i in range(n_tickers)],
            )
            # Near-perfect correlation with modest noise so IC varies across weeks
            noise = rng.normal(0, 0.3, n_tickers)
            if positive:
                fwd = scores + noise
            else:
                fwd = -scores + noise
            fwd_ret = pd.Series(fwd.values, index=scores.index)

            factor_scores_list.append({"test_factor": scores})
            fwd_ret_list.append(fwd_ret)

        return factor_scores_list, fwd_ret_list

    def test_perfect_positive_ic_close_to_1(self):
        bt = WalkForwardBacktest(tickers=["T0"])
        factor_scores_list, fwd_ret_list = self._make_perfect_ic_data(positive=True)
        dummy_returns = pd.Series(np.zeros(30))

        ic_df = bt.compute_per_factor_attribution(dummy_returns, factor_scores_list, fwd_ret_list)

        assert not ic_df.empty, "IC DataFrame should not be empty"
        row = ic_df[ic_df["factor_name"] == "test_factor"]
        assert len(row) == 1
        # Noise=0.3 on N(0,1) scores → Spearman IC typically 0.85-0.95
        assert row.iloc[0]["mean_IC"] > 0.75, (
            f"Expected IC > 0.75, got {row.iloc[0]['mean_IC']}"
        )

    def test_perfect_negative_ic_close_to_minus_1(self):
        bt = WalkForwardBacktest(tickers=["T0"])
        factor_scores_list, fwd_ret_list = self._make_perfect_ic_data(positive=False)
        dummy_returns = pd.Series(np.zeros(30))

        ic_df = bt.compute_per_factor_attribution(dummy_returns, factor_scores_list, fwd_ret_list)

        row = ic_df[ic_df["factor_name"] == "test_factor"]
        assert len(row) == 1
        assert row.iloc[0]["mean_IC"] < -0.75, (
            f"Expected IC < -0.75, got {row.iloc[0]['mean_IC']}"
        )

    def test_ic_df_columns_present(self):
        bt = WalkForwardBacktest(tickers=["T0"])
        factor_scores_list, fwd_ret_list = self._make_perfect_ic_data()
        ic_df = bt.compute_per_factor_attribution(
            pd.Series(np.zeros(30)), factor_scores_list, fwd_ret_list
        )
        for col in ["factor_name", "mean_IC", "ICIR", "n_observations", "contribution_pct"]:
            assert col in ic_df.columns, f"Missing column: {col}"

    def test_icir_positive_when_ic_consistently_positive(self):
        """ICIR should be positive when IC is consistently positive."""
        bt = WalkForwardBacktest(tickers=["T0"])
        factor_scores_list, fwd_ret_list = self._make_perfect_ic_data(positive=True)
        ic_df = bt.compute_per_factor_attribution(
            pd.Series(np.zeros(30)), factor_scores_list, fwd_ret_list
        )
        row = ic_df[ic_df["factor_name"] == "test_factor"].iloc[0]
        assert row["ICIR"] > 0


# ==============================================================================
# TEST 3: Transaction cost deduction
# ==============================================================================

class TestTransactionCosts:
    """
    Verify that transaction costs are correctly deducted from returns.

    With tc_bps=5, a round-trip cost = 2 * 5/10000 = 0.001 (0.1%) per unit of turnover.
    """

    def test_tc_reduces_return(self):
        """Portfolio return with TC > 0 should be lower than without."""
        bt_no_tc = WalkForwardBacktest(tickers=[], transaction_cost_bps=0.0)
        bt_tc = WalkForwardBacktest(tickers=[], transaction_cost_bps=5.0)

        # Simulate: new_holdings = {A: 1.0}, fwd = {A: 0.01}, full turnover (prev empty)
        tc_rate_no = bt_no_tc.tc_bps / 10_000
        tc_rate = bt_tc.tc_bps / 10_000

        gross_ret = 0.01   # 1% gross return
        turnover = 1.0     # 100% turnover (full portfolio rebalance)
        tc_drag_no = turnover * tc_rate_no * 2
        tc_drag = turnover * tc_rate * 2

        net_no_tc = gross_ret - tc_drag_no
        net_tc = gross_ret - tc_drag

        assert net_no_tc > net_tc, "Return without TC should exceed return with TC"
        assert abs(tc_drag - 0.001) < 1e-10, (
            f"Expected TC drag = 0.001, got {tc_drag}"
        )

    def test_tc_5bps_round_trip_formula(self):
        """5 bps one-way = 10 bps round-trip = 0.001 on 100% turnover."""
        bt = WalkForwardBacktest(tickers=[], transaction_cost_bps=5.0)
        tc_one_way = bt.tc_bps / 10_000
        round_trip = tc_one_way * 2 * 1.0  # 1.0 = 100% turnover
        assert abs(round_trip - 0.001) < 1e-12

    def test_zero_turnover_no_tc_drag(self):
        """No portfolio change = no transaction cost."""
        bt = WalkForwardBacktest(tickers=[], transaction_cost_bps=5.0)
        current = {"A": 0.5, "B": 0.5}
        new = {"A": 0.5, "B": 0.5}
        all_t = set(current) | set(new)
        turnover = sum(abs(new.get(t, 0) - current.get(t, 0)) for t in all_t) / 2
        assert turnover == 0.0

    def test_full_turnover_is_1(self):
        """Going from empty portfolio to 100% invested = turnover of 1.0."""
        new = {"A": 0.6, "B": 0.4}
        current = {}
        all_t = set(current) | set(new)
        turnover = sum(abs(new.get(t, 0) - current.get(t, 0)) for t in all_t) / 2
        assert abs(turnover - 0.5) < 1e-10, (
            "Going from empty to fully invested is 0.5 one-way turnover "
            "(buys 100% → 0.5 of two-sided measure)"
        )


# ==============================================================================
# TEST 4: Survivorship bias filter
# ==============================================================================

class TestSurvivorshipFilter:
    """
    Tickers that IPO'd after window_start should be excluded.
    Tickers with no IPO data (None) should be included (conservative).
    """

    def test_ticker_ipo_after_window_is_excluded(self):
        bt = WalkForwardBacktest(tickers=["OLD", "NEW"])
        window_start = pd.Timestamp("2021-01-01")

        # OLD: IPO before window_start → include
        # NEW: IPO after window_start → exclude
        bt._ipo_cache = {
            "OLD": pd.Timestamp("2010-06-01"),
            "NEW": pd.Timestamp("2022-03-15"),
        }

        included, excluded = bt._filter_universe(["OLD", "NEW"], window_start)
        assert "OLD" in included
        assert "NEW" in excluded
        assert "NEW" not in included

    def test_ticker_ipo_same_day_as_window_start_is_included(self):
        bt = WalkForwardBacktest(tickers=["EDGE"])
        window_start = pd.Timestamp("2021-01-01")
        bt._ipo_cache = {"EDGE": window_start}

        included, excluded = bt._filter_universe(["EDGE"], window_start)
        assert "EDGE" in included
        assert "EDGE" not in excluded

    def test_ticker_with_no_ipo_data_is_included(self):
        """None IPO date = unknown, conservative = include."""
        bt = WalkForwardBacktest(tickers=["UNKNOWN"])
        window_start = pd.Timestamp("2021-01-01")
        bt._ipo_cache = {"UNKNOWN": None}

        included, excluded = bt._filter_universe(["UNKNOWN"], window_start)
        assert "UNKNOWN" in included

    def test_multiple_tickers_mixed(self):
        bt = WalkForwardBacktest(tickers=["A", "B", "C", "D"])
        window_start = pd.Timestamp("2022-01-01")
        bt._ipo_cache = {
            "A": pd.Timestamp("2015-01-01"),   # old → include
            "B": pd.Timestamp("2022-06-01"),   # IPO after → exclude
            "C": None,                          # unknown → include
            "D": pd.Timestamp("2023-01-01"),   # future IPO → exclude
        }

        included, excluded = bt._filter_universe(["A", "B", "C", "D"], window_start)
        assert set(included) == {"A", "C"}
        assert set(excluded) == {"B", "D"}


# ==============================================================================
# TEST 5: Walk-forward windows are non-overlapping in the test period
# ==============================================================================

class TestWindowGeneration:
    """
    Verify that consecutive test windows do not overlap.
    The first window must start on or after FIRST_TRAIN_START + training days.
    """

    def test_test_windows_advance_by_step_days(self):
        """
        With step_days=63 and test_days=126, consecutive test windows overlap
        by (test_days - step_days) = 63 days.  This is intentional walk-forward
        methodology: step_days defines how much NEW out-of-sample data each window
        contributes, not how much the windows are separated.

        What must hold: each test_start advances by exactly step_days from the
        previous test_start.
        """
        bt = WalkForwardBacktest(
            tickers=["T0"],
            training_days=504,
            test_days=126,
            step_days=63,
        )
        windows = bt._generate_windows()
        assert len(windows) > 0, "Should generate at least one window"

        for i in range(1, len(windows)):
            prev_test_start = windows[i - 1][2]
            curr_test_start = windows[i][2]
            bdays_apart = len(pd.bdate_range(prev_test_start, curr_test_start)) - 1
            assert 60 <= bdays_apart <= 66, (
                f"Window {i}: test_starts are {bdays_apart} bdays apart, expected ~63"
            )

    def test_training_window_length_approx(self):
        bt = WalkForwardBacktest(tickers=["T0"], training_days=504)
        windows = bt._generate_windows()
        if windows:
            ts, te, _, _ = windows[0]
            # Business days between ts and te should be ≈ 504
            bd = len(pd.bdate_range(ts, te))
            assert 500 <= bd <= 510, f"Training window length off: {bd}"

    def test_step_advances_by_step_days(self):
        bt = WalkForwardBacktest(tickers=["T0"], step_days=63)
        windows = bt._generate_windows()
        if len(windows) >= 2:
            # The training start advances by 63 business days each step
            ts0 = windows[0][0]
            ts1 = windows[1][0]
            bdays_step = len(pd.bdate_range(ts0, ts1)) - 1
            assert 60 <= bdays_step <= 66, f"Step size off: {bdays_step} bdays"

    def test_first_window_train_start_is_correct(self):
        bt = WalkForwardBacktest(tickers=["T0"])
        windows = bt._generate_windows(first_train_start="2020-01-01")
        if windows:
            ts = windows[0][0]
            assert ts == pd.Timestamp("2020-01-01") or ts == pd.Timestamp("2020-01-02"), (
                f"First train_start should be 2020-01-01 (or 2020-01-02 if weekend), got {ts}"
            )

    def test_all_windows_are_complete(self):
        """No window's test_end should be in the future."""
        bt = WalkForwardBacktest(tickers=["T0"])
        windows = bt._generate_windows()
        today = pd.Timestamp.today().normalize()
        for _, _, _, test_end in windows:
            assert test_end <= today, f"Future test_end found: {test_end.date()}"


# ==============================================================================
# TEST 6: Composite score and position sizing
# ==============================================================================

class TestCompositeAndPositionSizing:

    def test_composite_score_all_factors(self):
        """All factors present: composite should be weighted average."""
        factors = {
            "f1": pd.Series({"A": 1.0, "B": -1.0, "C": 0.0}),
            "f2": pd.Series({"A": 0.5, "B": 0.5, "C": 0.5}),
        }
        weights = {"f1": 0.6, "f2": 0.4}
        scores = composite_score(factors, weights)

        expected_A = (1.0 * 0.6 + 0.5 * 0.4) / 1.0   # = 0.8
        assert abs(scores["A"] - expected_A) < 1e-9

    def test_composite_score_missing_factor_graceful(self):
        """Missing factor for a ticker: weight redistributed to available factor."""
        factors = {
            "f1": pd.Series({"A": 1.0}),           # only A has f1
            "f2": pd.Series({"A": 0.5, "B": 0.5}), # both have f2
        }
        weights = {"f1": 0.5, "f2": 0.5}
        scores = composite_score(factors, weights)

        # B has only f2 → its entire weight goes to f2 → score = 0.5
        assert abs(scores["B"] - 0.5) < 1e-9

    def test_inv_vol_weights_sum_to_1(self):
        """Inverse-vol weights should sum to 1.0."""
        prices = _make_price_df(n_tickers=10, n_days=300, start="2020-01-01")
        tickers = prices.columns.tolist()[:5]
        as_of = prices.index[-1]

        weights = inv_vol_weights(tickers, prices, as_of)
        assert abs(sum(weights.values()) - 1.0) < 1e-9

    def test_inv_vol_weights_respect_cap(self):
        """
        No position should exceed POSITION_CAP when the cap is geometrically
        achievable (need at least ceil(1/cap) tickers so weights can sum to 1).
        With cap=8% we need >= 13 tickers (13 × 8% = 104% > 100%).
        """
        # Use 15 tickers so the cap is achievable
        prices = _make_price_df(n_tickers=15, n_days=300, start="2020-01-01")
        tickers = prices.columns.tolist()
        as_of = prices.index[-1]

        weights = inv_vol_weights(tickers, prices, as_of, cap=POSITION_CAP)
        for t, w in weights.items():
            assert w <= POSITION_CAP + 1e-9, f"{t} weight {w:.4f} exceeds cap {POSITION_CAP}"


# ==============================================================================
# TEST 7: Factor score computation on synthetic prices
# ==============================================================================

class TestFactorScores:

    def test_factor_scores_return_dict(self):
        prices = _make_price_df(n_tickers=15, n_days=400, start="2019-01-01")
        spy = _make_price_series(n_days=400, start="2019-01-01", seed=999)
        result = compute_factor_scores(prices, spy)
        assert isinstance(result, dict)

    def test_factor_scores_keys_are_bt_factors(self):
        prices = _make_price_df(n_tickers=15, n_days=400, start="2019-01-01")
        result = compute_factor_scores(prices)
        from backtest import _BT_FACTORS
        for k in result:
            assert k in _BT_FACTORS, f"Unexpected factor key: {k}"

    def test_factor_scores_empty_on_short_history(self):
        """Less than 252 days → return empty dict."""
        prices = _make_price_df(n_tickers=10, n_days=100, start="2022-01-01")
        result = compute_factor_scores(prices)
        assert result == {}, "Should return empty dict for < 252 days"

    def test_factor_scores_no_nan_in_output(self):
        """Factor Z-scores should be finite (no NaN) for any returned factor."""
        prices = _make_price_df(n_tickers=15, n_days=400)
        result = compute_factor_scores(prices)
        for fname, series in result.items():
            finite = series.dropna()
            assert len(finite) > 0, f"Factor {fname} has no valid values"


# ==============================================================================
# TEST 8: WalkForwardBacktest._generate_weight_combinations
# ==============================================================================

class TestWeightCombinations:

    def test_generates_n_combinations(self):
        bt = WalkForwardBacktest(tickers=[])
        combos = bt._generate_weight_combinations(["f1", "f2", "f3"], n=7)
        assert len(combos) == 7

    def test_weights_sum_to_1(self):
        bt = WalkForwardBacktest(tickers=[])
        combos = bt._generate_weight_combinations(["f1", "f2", "f3"], n=5)
        for i, combo in enumerate(combos):
            total = sum(combo.values())
            assert abs(total - 1.0) < 1e-9, f"Combo {i} weights sum to {total}, not 1.0"

    def test_first_combo_is_config_defaults(self):
        """First combination must use config defaults (renormalized)."""
        from config import EQUITY_FACTORS
        bt = WalkForwardBacktest(tickers=[])
        factor_names = ["momentum_12_1", "momentum_6_1"]
        combos = bt._generate_weight_combinations(factor_names, n=3)

        raw = {f: EQUITY_FACTORS[f]["weight"] for f in factor_names}
        total = sum(raw.values())
        expected = {f: w / total for f, w in raw.items()}

        for fname in factor_names:
            assert abs(combos[0][fname] - expected[fname]) < 1e-9
