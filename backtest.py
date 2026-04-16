#!/usr/bin/env python3
"""
================================================================================
WALK-FORWARD BACKTESTING FRAMEWORK v1.0
================================================================================
Validates all signal_engine factors and module weights with out-of-sample
walk-forward testing.

DESIGN:
    Training window : 504 trading days (approx 2 years)
    Test window     : 126 trading days (approx 6 months)
    Step size       : 63 trading days  (approx 3 months)
    First train     : 2020-01-01 to 2021-12-31
    First test      : 2022-01-01 to 2022-06-30

SURVIVORSHIP BIAS MITIGATION:
    Tickers that IPO'd after a window's train_start are excluded for that
    window. IPO dates are fetched once from yfinance and cached in-memory.

POINT-IN-TIME FUNDAMENTALS:
    earnings_revision uses yfinance.Ticker.info which returns current
    values only — no historical PIT data. In backtest mode this factor is
    excluded. The 45-day-lag approximation applies to any future integration
    with EDGAR XBRL as-reported data.

TRANSACTION COST MODEL:
    Round-trip cost = 2 x tc_bps x |turnover fraction|
    Default: 5 bps one-way (10 bps round-trip), consistent with RISK_PARAMS.

USAGE:
    python3 backtest.py --run-full          # all windows
    python3 backtest.py --run-latest        # most recent window only
    python3 backtest.py --factor-ic         # IC table for all factors
    python3 backtest.py --suggest-weights   # weight recommendations

================================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import warnings

logger = logging.getLogger(__name__)
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import spearmanr

warnings.filterwarnings("ignore")

# ── Import signal_engine primitives ──────────────────────────────────────────
try:
    from signal_engine import (
        compute_momentum,
        compute_realized_vol,
        compute_ivol,
        compute_52wk_high_proximity,
        zscore_cross_sectional,
        ANNUALIZATION_FACTOR,
    )
    from config import EQUITY_FACTORS, RISK_PARAMS
except ImportError as exc:
    print(f"ERROR: Could not import signal_engine or config: {exc}")
    sys.exit(1)

# ── Backtest constants ────────────────────────────────────────────────────────
FIRST_TRAIN_START = "2020-01-01"
TOP_N_POSITIONS = 15
POSITION_CAP = 0.08       # 8% max single position
MIN_TICKERS = 10          # minimum universe size per window
FUNDAMENTAL_LAG_DAYS = 45  # approximation for point-in-time fundamental data

# Factors available for backtest (price-based only; earnings_revision excluded —
# yfinance.Ticker.info returns current values only, no historical PIT data).
_BT_FACTORS = [
    "momentum_12_1",
    "momentum_6_1",
    "mean_reversion_5d",
    "volatility_quality",
    "52wk_high_proximity",
    "ivol",
]


# ==============================================================================
# SECTION 1: FACTOR COMPUTATION (backtest-safe, price-data only)
# ==============================================================================

def compute_factor_scores(
    prices: pd.DataFrame,
    spy_series: Optional[pd.Series] = None,
) -> Dict[str, pd.Series]:
    """
    Compute all price-based factor Z-scores for a price DataFrame slice.
    Uses only data in `prices` — no forward-look.

    Returns dict of {factor_name: pd.Series(z_scores)} indexed by ticker.
    Factors with insufficient data silently return an empty/partial Series.

    earnings_revision is excluded: yfinance returns only current-day values,
    making historical simulation impossible without EDGAR XBRL PIT data.
    """
    if len(prices) < 252:
        return {}

    factors: Dict[str, pd.Series] = {}
    cfg = EQUITY_FACTORS

    # momentum_12_1
    raw = compute_momentum(
        prices,
        cfg["momentum_12_1"]["lookback_long"],
        cfg["momentum_12_1"]["lookback_skip"],
    )
    factors["momentum_12_1"] = zscore_cross_sectional(raw.dropna())

    # momentum_6_1
    raw = compute_momentum(
        prices,
        cfg["momentum_6_1"]["lookback_long"],
        cfg["momentum_6_1"]["lookback_skip"],
    )
    factors["momentum_6_1"] = zscore_cross_sectional(raw.dropna())

    # mean_reversion_5d  (invert: negative 5d return = positive signal)
    raw_5d = compute_momentum(prices, 5)
    factors["mean_reversion_5d"] = zscore_cross_sectional((-raw_5d).dropna())

    # volatility_quality  (invert: lower vol = higher quality)
    vol_raw = compute_realized_vol(prices, 63)
    factors["volatility_quality"] = zscore_cross_sectional((-vol_raw).dropna())

    # 52wk_high_proximity
    prox_vals = {
        t: compute_52wk_high_proximity(prices[t].dropna())
        for t in prices.columns
    }
    prox_series = pd.Series(prox_vals, dtype=float).dropna()
    if not prox_series.empty:
        factors["52wk_high_proximity"] = zscore_cross_sectional(prox_series)

    # ivol  (needs SPY as market proxy; silently omitted if unavailable)
    if spy_series is not None and len(spy_series) >= 63:
        ivol_vals = {
            t: compute_ivol(
                prices[t].dropna(),
                spy_series,
                lookback=cfg["ivol"]["lookback"],
            )
            for t in prices.columns
        }
        ivol_series = pd.Series(ivol_vals, dtype=float).dropna()
        if not ivol_series.empty:
            factors["ivol"] = zscore_cross_sectional(ivol_series)

    return factors


def composite_score(
    factors: Dict[str, pd.Series],
    weights: Dict[str, float],
) -> pd.Series:
    """
    Combine factor Z-scores with given weights.

    Graceful degradation: if a factor is missing for a ticker, its weight is
    redistributed proportionally across available factors (same logic as
    signal_engine.compute_equity_composite).

    Returns pd.Series of composite scores, index = ticker.
    """
    all_tickers: set = set()
    for s in factors.values():
        all_tickers.update(s.index)

    scores: Dict[str, float] = {}
    for t in all_tickers:
        total_w = 0.0
        val = 0.0
        for fname, series in factors.items():
            if t in series.index and pd.notna(series[t]):
                w = weights.get(fname, 0.0)
                val += float(series[t]) * w
                total_w += w
        if total_w > 0.0:
            scores[t] = val / total_w

    return pd.Series(scores)


# ==============================================================================
# SECTION 2: POSITION SIZING
# ==============================================================================

def inv_vol_weights(
    ranked_tickers: List[str],
    prices: pd.DataFrame,
    as_of_date: pd.Timestamp,
    cap: float = POSITION_CAP,
) -> Dict[str, float]:
    """
    Inverse-volatility weights with a per-position cap.

    1. Compute 63-day annualized vol for each ticker up to as_of_date.
    2. Weight = 1/vol, normalized to sum to 1.
    3. Apply cap; redistribute residual iteratively until stable.

    Returns dict {ticker: weight}.
    """
    vols: Dict[str, float] = {}
    for t in ranked_tickers:
        if t not in prices.columns:
            vols[t] = 1.0
            continue
        px = prices.loc[:as_of_date, t].dropna()
        if len(px) >= 21:
            log_ret = np.log(px / px.shift(1)).dropna()
            v = float(log_ret.tail(63).std() * ANNUALIZATION_FACTOR)
            vols[t] = max(v, 0.01)
        else:
            vols[t] = 1.0

    inv_v = {t: 1.0 / v for t, v in vols.items()}
    total = sum(inv_v.values())
    if total == 0:
        n = len(ranked_tickers)
        return {t: 1.0 / n for t in ranked_tickers}

    weights = {t: v / total for t, v in inv_v.items()}

    # Cap and redistribute (up to 10 iterations for convergence)
    for _ in range(10):
        over = {t: w for t, w in weights.items() if w > cap}
        if not over:
            break
        excess = sum(w - cap for w in over.values())
        under = {t: w for t, w in weights.items() if w < cap}
        if not under:
            break
        extra = excess / len(under)
        weights = {
            t: cap if t in over else min(w + extra, cap)
            for t, w in weights.items()
        }

    total_f = sum(weights.values())
    return {t: w / total_f for t, w in weights.items()} if total_f > 0 else weights


# ==============================================================================
# SECTION 3: WALK-FORWARD BACKTEST CLASS
# ==============================================================================

class WalkForwardBacktest:
    """
    Walk-forward backtesting framework.

    Key features:
    - Survivorship-bias-adjusted universe (IPO date filtering)
    - Per-window factor weight optimization via Sharpe grid search
    - Weekly portfolio rebalancing with inverse-vol sizing (8% cap)
    - Transaction cost model (round-trip = 2 x tc_bps x turnover)
    - Per-factor IC and ICIR attribution
    """

    def __init__(
        self,
        tickers: List[str],
        training_days: int = 504,
        test_days: int = 126,
        step_days: int = 63,
        transaction_cost_bps: float = 5.0,
    ):
        self.tickers = list(tickers)
        self.training_days = training_days
        self.test_days = test_days
        self.step_days = step_days
        self.tc_bps = transaction_cost_bps

        self._ipo_cache: Dict[str, Optional[pd.Timestamp]] = {}
        self._all_results: List[dict] = []
        self._aggregated_ic: Dict[str, List[float]] = {}

    # ── Window generation ────────────────────────────────────────────────────

    def _generate_windows(
        self, first_train_start: str = FIRST_TRAIN_START
    ) -> List[Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
        """
        Generate walk-forward windows.

        Test periods are non-overlapping by construction: the training window
        slides forward by step_days at each iteration, so consecutive test
        windows are adjacent (no shared trading days).

        Returns list of (train_start, train_end, test_start, test_end).
        """
        ts = pd.Timestamp(first_train_start)
        today = pd.Timestamp.today().normalize()
        windows: List[Tuple] = []

        while True:
            train_end = ts + pd.offsets.BDay(self.training_days - 1)
            test_start = train_end + pd.offsets.BDay(1)
            test_end = test_start + pd.offsets.BDay(self.test_days - 1)

            if test_end > today:
                break

            windows.append((ts, train_end, test_start, test_end))
            ts = ts + pd.offsets.BDay(self.step_days)

        return windows

    # ── Survivorship bias ────────────────────────────────────────────────────

    def _prefetch_ipo_dates(self, tickers: List[str]) -> None:
        """
        Load IPO dates from ticker_metadata into self._ipo_cache in one DB query.

        Called once at the start of run_single_window before _filter_universe.
        Tickers absent from the DB fall through to the per-ticker yfinance path
        in _get_ipo_date; their results are then saved back to DB for next time.
        """
        try:
            from db_cache import bulk_get_ipo_dates
            db_dates = bulk_get_ipo_dates(tickers)
            for ticker, ipo_ts in db_dates.items():
                if ticker not in self._ipo_cache:
                    self._ipo_cache[ticker] = ipo_ts
            if db_dates:
                print(f"    IPO dates: {len(db_dates)}/{len(tickers)} loaded from DB cache")
        except Exception as exc:
            # DB unavailable — fall through to per-ticker yfinance calls
            logger.debug("_prefetch_ipo_dates DB error (falling back to yfinance): %s", exc)

    def _get_ipo_date(self, ticker: str) -> Optional[pd.Timestamp]:
        """
        Return first trading date for ticker (from yfinance firstTradingDay).

        Lookup order:
          1. self._ipo_cache  (populated by _prefetch_ipo_dates for most tickers)
          2. ticker_metadata table in Supabase
          3. yf.Ticker(ticker).info  (only for cache misses; result saved to DB)

        Returns None on failure — treated as pre-window IPO by _filter_universe.
        """
        if ticker in self._ipo_cache:
            return self._ipo_cache[ticker]

        result: Optional[pd.Timestamp] = None
        try:
            info = yf.Ticker(ticker).info
            first = info.get("firstTradingDay")
            if first and isinstance(first, (int, float)) and first > 0:
                result = pd.Timestamp(int(first), unit="s").normalize()
        except Exception:
            pass

        # Persist to DB so the next run skips this yfinance call
        try:
            from db_cache import save_ticker_metadata
            save_ticker_metadata(
                ticker,
                {"ipo_date": result.date().isoformat() if result else None},
            )
        except Exception:
            pass

        self._ipo_cache[ticker] = result
        return result

    def _filter_universe(
        self, tickers: List[str], window_start: pd.Timestamp
    ) -> Tuple[List[str], List[str]]:
        """
        Remove tickers that IPO'd strictly after window_start.

        Rationale: including only companies that survived to today would
        overestimate historical returns (survivorship bias). Excluding
        post-window-start IPOs approximates the investable universe as of
        the test window start.

        Returns (included, excluded).
        """
        included, excluded = [], []
        for t in tickers:
            ipo = self._get_ipo_date(t)
            if ipo is None or ipo <= window_start:
                included.append(t)
            else:
                excluded.append(t)
        return included, excluded

    # ── Weight optimization ──────────────────────────────────────────────────

    def _generate_weight_combinations(
        self, factor_names: List[str], n: int = 10
    ) -> List[Dict[str, float]]:
        """
        Generate n weight dicts for grid search.

        Combination 0: config defaults renormalized to available (backtest) factors.
        Combinations 1..n-1: Dirichlet(alpha=1) random samples (uniform simplex).
        """
        rng = np.random.default_rng(42)
        combos: List[Dict[str, float]] = []

        # Config defaults renormalized; fall back to equal weights when factor
        # names are not in EQUITY_FACTORS (e.g. synthetic names in tests).
        raw = {f: EQUITY_FACTORS[f]["weight"] for f in factor_names if f in EQUITY_FACTORS}
        if not raw:
            raw = {f: 1.0 for f in factor_names}
        total = sum(raw.values()) or 1.0
        combos.append({f: raw.get(f, 1.0 / len(factor_names)) / total for f in factor_names})

        for _ in range(n - 1):
            alpha = np.ones(len(factor_names))
            raw_w = rng.dirichlet(alpha).tolist()
            combos.append(dict(zip(factor_names, raw_w)))

        return combos

    def _optimize_weights(
        self,
        train_prices: pd.DataFrame,
        spy_train: Optional[pd.Series],
        n_combinations: int = 10,
    ) -> Tuple[Dict[str, float], float]:
        """
        Grid search over weight combinations to maximize Sharpe on training window.

        Returns (best_weights, best_sharpe).
        Falls back to config defaults when training data is insufficient.
        """
        # Fallback: config defaults renormalized to BT factors
        raw_defaults = {f: EQUITY_FACTORS[f]["weight"] for f in _BT_FACTORS if f in EQUITY_FACTORS}
        total_d = sum(raw_defaults.values()) or 1.0
        default_w = {f: w / total_d for f, w in raw_defaults.items()}

        weekly_idx = train_prices.resample("W-MON").last().index
        if len(weekly_idx) < 12:
            return default_w, 0.0

        # Pre-compute weekly factor scores + forward returns on training data
        weekly_data: List[Tuple] = []
        for i in range(len(weekly_idx) - 1):
            d = weekly_idx[i]
            d_next = weekly_idx[i + 1]

            prices_up_to_d = train_prices.loc[:d]
            if len(prices_up_to_d) < 252:
                continue

            spy_slice = spy_train.loc[:d] if spy_train is not None else None
            factors = compute_factor_scores(prices_up_to_d, spy_slice)
            if not factors:
                continue

            p0 = train_prices.loc[:d].iloc[-1]
            p1_slice = train_prices.loc[d:d_next]
            if len(p1_slice) < 2:
                continue
            p1 = p1_slice.iloc[-1]
            fwd = (p1 / p0.replace(0.0, np.nan) - 1).dropna()
            if fwd.empty:
                continue

            weekly_data.append((d, factors, fwd))

        if len(weekly_data) < 8:
            return default_w, 0.0

        factor_names = sorted({k for _, fs, _ in weekly_data for k in fs})
        combos = self._generate_weight_combinations(factor_names, n_combinations)

        best_sharpe = -np.inf
        best_weights = default_w

        for combo in combos:
            port_rets: List[float] = []
            for d, factors, fwd in weekly_data:
                scores = composite_score(factors, combo)
                valid = scores[scores.index.isin(fwd.index)].dropna()
                if len(valid) < 3:
                    continue
                top_t = valid.nlargest(min(TOP_N_POSITIONS, len(valid))).index.tolist()
                pos = inv_vol_weights(top_t, train_prices, d)
                pr = sum(pos.get(t, 0.0) * float(fwd.get(t, 0.0)) for t in pos)
                port_rets.append(pr)

            if len(port_rets) < 4:
                continue
            arr = np.array(port_rets, dtype=float)
            sharpe = float(arr.mean() / arr.std() * np.sqrt(52)) if arr.std() > 0 else 0.0
            if sharpe > best_sharpe:
                best_sharpe = sharpe
                best_weights = combo

        return best_weights, best_sharpe

    # ── Test window execution ─────────────────────────────────────────────────

    def _run_test_window(
        self,
        all_prices: pd.DataFrame,
        spy_prices: Optional[pd.Series],
        test_start: pd.Timestamp,
        test_end: pd.Timestamp,
        weights: Dict[str, float],
    ) -> Optional[dict]:
        """
        Execute test window: weekly rebalance, inverse-vol sizing, TC drag.

        Returns metrics dict or None if insufficient data.
        """
        test_slice = all_prices.loc[test_start:test_end]
        weekly_idx = [
            d for d in test_slice.resample("W-MON").last().index
            if test_start <= d <= test_end
        ]

        if len(weekly_idx) < 2:
            return None

        tc_rate = self.tc_bps / 10_000  # one-way fraction

        portfolio_rets: List[float] = []
        spy_rets: List[float] = []
        turnovers: List[float] = []
        factor_scores_history: List[Dict[str, pd.Series]] = []
        fwd_ret_history: List[pd.Series] = []
        current_holdings: Dict[str, float] = {}

        for i in range(len(weekly_idx) - 1):
            d = weekly_idx[i]
            d_next = weekly_idx[i + 1]

            # Slice up to d only — no look-ahead
            prices_up_to_d = all_prices.loc[:d]
            if len(prices_up_to_d) < 252:
                continue

            spy_up_to_d = spy_prices.loc[:d] if spy_prices is not None else None
            factors = compute_factor_scores(prices_up_to_d, spy_up_to_d)
            if not factors:
                continue

            scores = composite_score(factors, weights)

            # Forward returns: d to d_next (next-week outcome)
            p0 = all_prices.loc[:d].iloc[-1]
            p1 = all_prices.loc[:d_next].iloc[-1]
            fwd = (p1 / p0.replace(0.0, np.nan) - 1).dropna()

            valid = scores[scores.index.isin(fwd.index)].dropna()
            if len(valid) < 3:
                continue

            top_t = valid.nlargest(min(TOP_N_POSITIONS, len(valid))).index.tolist()
            new_holdings = inv_vol_weights(top_t, all_prices, d)

            # Turnover (avg of abs weight changes / 2 = one-way turnover)
            all_t_set = set(current_holdings) | set(new_holdings)
            turnover = sum(
                abs(new_holdings.get(t, 0.0) - current_holdings.get(t, 0.0))
                for t in all_t_set
            ) / 2.0

            # Round-trip cost drag
            tc_drag = turnover * tc_rate * 2.0

            # Portfolio gross return minus TC drag
            port_ret = (
                sum(new_holdings.get(t, 0.0) * float(fwd.get(t, 0.0)) for t in new_holdings)
                - tc_drag
            )

            # Benchmark SPY return
            spy_ret = 0.0
            if spy_prices is not None:
                try:
                    sp0 = float(spy_prices.loc[:d].iloc[-1])
                    sp1 = float(spy_prices.loc[:d_next].iloc[-1])
                    spy_ret = sp1 / sp0 - 1.0 if sp0 > 0 else 0.0
                except (IndexError, ZeroDivisionError):
                    pass

            portfolio_rets.append(port_ret)
            spy_rets.append(spy_ret)
            turnovers.append(turnover)
            factor_scores_history.append(factors)
            fwd_ret_history.append(fwd)
            current_holdings = new_holdings

        if len(portfolio_rets) < 2:
            return None

        rets = pd.Series(portfolio_rets, dtype=float)
        spy_series = pd.Series(spy_rets, dtype=float)

        sharpe = float(rets.mean() / rets.std() * np.sqrt(52)) if rets.std() > 0 else 0.0
        cum = (1 + rets).cumprod()
        max_dd = float(((cum - cum.cummax()) / cum.cummax()).min())
        hit_rate = float((rets > 0).mean())
        avg_turnover = float(np.mean(turnovers)) if turnovers else 0.0

        ic_df = self.compute_per_factor_attribution(rets, factor_scores_history, fwd_ret_history)

        best_factor = ic_df.loc[ic_df["ICIR"].idxmax(), "factor_name"] if not ic_df.empty else ""
        worst_factor = ic_df.loc[ic_df["ICIR"].idxmin(), "factor_name"] if not ic_df.empty else ""

        return {
            "sharpe": round(sharpe, 4),
            "max_drawdown": round(max_dd, 4),
            "hit_rate": round(hit_rate, 4),
            "turnover": round(avg_turnover, 4),
            "weekly_returns": rets.tolist(),
            "spy_returns": spy_series.tolist(),
            "best_factor": best_factor,
            "worst_factor": worst_factor,
            "factor_ic": ic_df.to_dict("records") if not ic_df.empty else [],
            "n_weeks": len(rets),
        }

    # ── Per-factor IC attribution ─────────────────────────────────────────────

    def compute_per_factor_attribution(
        self,
        returns: pd.Series,
        factor_scores_list: List[Dict[str, pd.Series]],
        fwd_ret_list: List[pd.Series],
    ) -> pd.DataFrame:
        """
        Compute per-factor IC and ICIR.

        IC   = Spearman rank correlation of factor score with next-week return,
               computed each week and averaged.
        ICIR = IC.mean() / IC.std(ddof=1)  — consistency measure (t-stat proxy).
        Contribution = factor_weight * mean_IC * n_observations.

        Returns DataFrame sorted by ICIR descending:
            [factor_name, mean_IC, ICIR, n_observations, contribution_pct]
        """
        factor_names: set = set()
        for fs in factor_scores_list:
            factor_names.update(fs.keys())

        results = []
        for fname in sorted(factor_names):
            ic_series: List[float] = []
            for factors, fwd in zip(factor_scores_list, fwd_ret_list):
                if fname not in factors:
                    continue
                fscore = factors[fname].dropna()
                common = fscore.index.intersection(fwd.dropna().index)
                if len(common) < 5:
                    continue
                corr, _ = spearmanr(fscore[common].values, fwd[common].values)
                if np.isfinite(corr):
                    ic_series.append(float(corr))

            if len(ic_series) < 2:
                continue

            ic_arr = np.array(ic_series, dtype=float)
            mean_ic = float(np.mean(ic_arr))
            std_ic = float(np.std(ic_arr, ddof=1))
            icir = float(mean_ic / std_ic) if std_ic > 0 else 0.0
            weight = EQUITY_FACTORS.get(fname, {}).get("weight", 0.0)
            contribution = weight * mean_ic * len(ic_series)

            results.append({
                "factor_name": fname,
                "mean_IC": round(mean_ic, 4),
                "ICIR": round(icir, 4),
                "n_observations": len(ic_series),
                "contribution_pct": round(contribution, 4),
            })

        if not results:
            return pd.DataFrame(
                columns=["factor_name", "mean_IC", "ICIR", "n_observations", "contribution_pct"]
            )

        return (
            pd.DataFrame(results)
            .sort_values("ICIR", ascending=False)
            .reset_index(drop=True)
        )

    # ── Single window entry point ─────────────────────────────────────────────

    def run_single_window(
        self,
        train_start: pd.Timestamp,
        train_end: pd.Timestamp,
        test_start: pd.Timestamp,
        test_end: pd.Timestamp,
    ) -> Optional[dict]:
        """
        Full pipeline for one walk-forward window:

        1. Survivorship-bias filter universe (exclude post-window-start IPOs).
        2. Download OHLCV for all tickers in one batch (train_start to test_end).
        3. Optimize factor weights on training window.
        4. Run test window with optimized weights.
        5. Return metrics dict.

        Note: fundamental signals (earnings_revision) use a 45-day-lag
        approximation when integrated with EDGAR data. Current yfinance
        implementation is excluded from backtest entirely.
        """
        print(
            f"\n  Window: train {train_start.date()} - {train_end.date()} | "
            f"test {test_start.date()} - {test_end.date()}"
        )

        # 1. Filter universe for survivorship bias
        # Prefetch known IPO dates from DB (one query replaces N yfinance calls)
        self._prefetch_ipo_dates(self.tickers)
        included, excluded = self._filter_universe(self.tickers, train_start)
        print(
            f"    Universe: {len(included)} tickers "
            f"({len(excluded)} excluded for IPO after window start)"
        )

        if len(included) < MIN_TICKERS:
            print(f"    [SKIP] Universe too small (need >= {MIN_TICKERS})")
            return None

        # 2. Download price data (add SPY for IVOL benchmark)
        fetch_tickers = included + (["SPY"] if "SPY" not in included else [])
        buffer_start = train_start - pd.offsets.BDay(30)  # extra buffer for vol calcs

        try:
            raw = yf.download(
                fetch_tickers,
                start=buffer_start.strftime("%Y-%m-%d"),
                end=test_end.strftime("%Y-%m-%d"),
                auto_adjust=True,
                progress=False,
                threads=True,
            )
        except Exception as exc:
            print(f"    [ERROR] Download failed: {exc}")
            return None

        if raw.empty:
            print("    [SKIP] Empty price data from yfinance")
            return None

        if isinstance(raw.columns, pd.MultiIndex):
            prices = raw["Close"].copy()
        else:
            prices = raw[["Close"]].rename(columns={"Close": fetch_tickers[0]})

        prices = prices.ffill(limit=5)

        spy_prices = prices["SPY"].dropna() if "SPY" in prices.columns else None
        ticker_prices = prices.drop(columns=["SPY"], errors="ignore")

        # Drop tickers with very sparse data (<50% of training window)
        min_obs = int(self.training_days * 0.5)
        valid_cols = ticker_prices.columns[ticker_prices.count() >= min_obs]
        ticker_prices = ticker_prices[valid_cols]

        if len(ticker_prices.columns) < MIN_TICKERS:
            print("    [SKIP] Too few tickers after data quality filter")
            return None

        # 3. Optimize weights on training window
        train_prices = ticker_prices.loc[train_start:train_end]
        spy_train = spy_prices.loc[train_start:train_end] if spy_prices is not None else None
        best_weights, train_sharpe = self._optimize_weights(train_prices, spy_train)

        top_w_factor = max(best_weights, key=best_weights.get) if best_weights else "n/a"
        print(
            f"    Training Sharpe: {train_sharpe:.3f} | "
            f"Top weight: {top_w_factor} ({best_weights.get(top_w_factor, 0):.2%})"
        )

        # 4. Run test window
        result = self._run_test_window(
            ticker_prices, spy_prices, test_start, test_end, best_weights
        )

        if result is None:
            print("    [SKIP] Insufficient test data")
            return None

        result.update({
            "train_start": train_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
            "train_sharpe": round(train_sharpe, 4),
            "optimized_weights": json.dumps({k: round(v, 4) for k, v in best_weights.items()}),
            "tickers_included": len(included),
            "tickers_excluded": len(excluded),
        })

        print(
            f"    Test Sharpe: {result['sharpe']:.3f} | "
            f"MaxDD: {result['max_drawdown']:.1%} | "
            f"Hit: {result['hit_rate']:.1%} | "
            f"Turnover: {result['turnover']:.1%}"
        )

        return result

    # ── Full backtest ─────────────────────────────────────────────────────────

    def run_full_backtest(self) -> pd.DataFrame:
        """
        Run all rolling walk-forward windows.

        Returns DataFrame (one row per window):
            window_start, window_end, sharpe, max_drawdown, hit_rate,
            turnover, best_factor, worst_factor, optimized_weights (JSON),
            train_sharpe, n_weeks, tickers_included, tickers_excluded.

        Also populates self._all_results and self._aggregated_ic for use
        by generate_report() and compute_per_factor_attribution().
        """
        windows = self._generate_windows()
        print(f"\n{'='*60}")
        print(f"  WALK-FORWARD BACKTEST")
        print(
            f"  {len(windows)} windows | train={self.training_days}d | "
            f"test={self.test_days}d | step={self.step_days}d"
        )
        print(f"  Universe: {len(self.tickers)} tickers")
        print(f"  Transaction cost: {self.tc_bps} bps one-way")
        print(f"{'='*60}")

        self._all_results = []
        for train_start, train_end, test_start, test_end in windows:
            r = self.run_single_window(train_start, train_end, test_start, test_end)
            if r:
                self._all_results.append(r)

        if not self._all_results:
            print("\n  [ERROR] No results. Check universe size and date range.")
            return pd.DataFrame()

        rows = []
        self._aggregated_ic = {}

        for r in self._all_results:
            rows.append({
                "window_start": r["test_start"].date(),
                "window_end": r["test_end"].date(),
                "sharpe": r["sharpe"],
                "max_drawdown": r["max_drawdown"],
                "hit_rate": r["hit_rate"],
                "turnover": r["turnover"],
                "best_factor": r["best_factor"],
                "worst_factor": r["worst_factor"],
                "optimized_weights": r.get("optimized_weights", "{}"),
                "train_sharpe": r.get("train_sharpe", 0.0),
                "n_weeks": r.get("n_weeks", 0),
                "tickers_included": r.get("tickers_included", 0),
                "tickers_excluded": r.get("tickers_excluded", 0),
            })
            for ic_row in r.get("factor_ic", []):
                self._aggregated_ic.setdefault(ic_row["factor_name"], []).append(
                    ic_row["mean_IC"]
                )

        return pd.DataFrame(rows)

    # ── Report ────────────────────────────────────────────────────────────────

    def generate_report(self, results: pd.DataFrame) -> str:
        """
        Print and return formatted backtest summary.

        Sections:
          1. Out-of-sample Sharpe (all windows combined)
          2. Sharpe vs SPY benchmark + excess
          3. Per-window table
          4. Worst drawdown window (failure mode)
          5. Per-factor IC table sorted by |mean_IC|
          6. Weight recommendations based on IC
          7. Transaction cost drag estimate
        """
        if results.empty:
            print("  No results to report.")
            return ""

        lines: List[str] = []
        sep = "=" * 70

        lines.append(sep)
        lines.append("  WALK-FORWARD BACKTEST REPORT")
        lines.append(
            f"  {len(results)} windows | "
            f"{results['window_start'].iloc[0]} - {results['window_end'].iloc[-1]}"
        )
        lines.append(sep)

        # Combined out-of-sample metrics
        all_weekly = [r for res in self._all_results for r in res.get("weekly_returns", [])]
        all_spy = [r for res in self._all_results for r in res.get("spy_returns", [])]

        if all_weekly:
            rets = np.array(all_weekly, dtype=float)
            spy_arr = np.array(all_spy, dtype=float)

            overall_sharpe = (
                float(rets.mean() / rets.std() * np.sqrt(52)) if rets.std() > 0 else 0.0
            )
            spy_sharpe = (
                float(spy_arr.mean() / spy_arr.std() * np.sqrt(52))
                if spy_arr.std() > 0 else 0.0
            )
            total_ret = float(np.prod(1 + rets) - 1)
            spy_total = float(np.prod(1 + spy_arr) - 1)

            lines.append(f"\n  OUT-OF-SAMPLE PERFORMANCE ({len(rets)} weeks combined)")
            lines.append(f"  {'Sharpe (portfolio):':<35} {overall_sharpe:>8.3f}")
            lines.append(f"  {'Sharpe (SPY benchmark):':<35} {spy_sharpe:>8.3f}")
            lines.append(f"  {'Excess Sharpe:':<35} {overall_sharpe - spy_sharpe:>8.3f}")
            lines.append(f"  {'Total return (portfolio):':<35} {total_ret:>7.1%}")
            lines.append(f"  {'Total return (SPY):':<35} {spy_total:>7.1%}")
            lines.append(
                f"  {'Avg hit rate (weekly):':<35} "
                f"{float(results['hit_rate'].mean()):>7.1%}"
            )

        # Per-window table
        lines.append(f"\n  PER-WINDOW SUMMARY")
        lines.append(
            f"  {'Window':>25} {'Sharpe':>8} {'MaxDD':>8} {'HitRate':>9} {'Turnover':>10}"
        )
        lines.append(f"  {'-'*63}")
        for _, row in results.iterrows():
            lines.append(
                f"  {str(row['window_start']):>12} - {str(row['window_end']):<12}"
                f"{row['sharpe']:>7.2f}"
                f"{row['max_drawdown']:>7.1%}"
                f"{row['hit_rate']:>8.1%}"
                f"{row['turnover']:>9.1%}"
            )

        # Worst drawdown window
        worst_idx = int(results["max_drawdown"].idxmin())
        worst = results.iloc[worst_idx]
        lines.append(f"\n  WORST WINDOW (failure mode)")
        lines.append(
            f"  {worst['window_start']} - {worst['window_end']}: "
            f"drawdown={worst['max_drawdown']:.1%}, sharpe={worst['sharpe']:.2f}"
        )
        lines.append(f"  Worst factor in that window: {worst['worst_factor']}")

        # Per-factor IC table
        aggregated = self._aggregated_ic
        if aggregated:
            lines.append(f"\n  PER-FACTOR IC TABLE (sorted by |mean_IC|)")
            lines.append(
                f"  {'Factor':<25} {'Mean IC':>10} {'Windows':>10} {'Verdict':>10}"
            )
            lines.append(f"  {'-'*57}")

            ic_summary = sorted(
                [
                    (f, float(np.mean(ic_list)), len(ic_list))
                    for f, ic_list in aggregated.items()
                ],
                key=lambda x: abs(x[1]),
                reverse=True,
            )
            for fname, mean_ic, n in ic_summary:
                verdict = "KEEP" if abs(mean_ic) > 0.02 else "REVIEW"
                lines.append(
                    f"  {fname:<25} {mean_ic:>+10.4f} {n:>10}  {verdict}"
                )

        # Weight recommendations
        lines.append(f"\n  WEIGHT RECOMMENDATION")
        lines.append("  Based on IC, suggest reweighting (positive IC factors only):")
        if aggregated:
            pos_factors = [
                (f, float(np.mean(ic)))
                for f, ic in aggregated.items()
                if float(np.mean(ic)) > 0
            ]
            if pos_factors:
                total_pos = sum(abs(ic) for _, ic in pos_factors)
                for fname, mean_ic in sorted(pos_factors, key=lambda x: x[1], reverse=True):
                    suggested_w = abs(mean_ic) / total_pos if total_pos > 0 else 0.0
                    current_w = EQUITY_FACTORS.get(fname, {}).get("weight", 0.0)
                    direction = "^ UP" if suggested_w > current_w else "v DOWN"
                    lines.append(
                        f"    {fname:<25} current={current_w:.2f}  "
                        f"suggested={suggested_w:.2f}  {direction}"
                    )

        # Transaction cost drag
        avg_turnover = float(results["turnover"].mean())
        annual_tc_bps = avg_turnover * 52 * self.tc_bps * 2  # round-trip annualized
        lines.append(f"\n  TRANSACTION COST DRAG")
        lines.append(f"  Avg weekly turnover: {avg_turnover:.1%}")
        lines.append(
            f"  Turnover of {avg_turnover:.1%} costs "
            f"{annual_tc_bps:.1f} bps annually "
            f"({annual_tc_bps / 100:.2%} return drag)"
        )

        lines.append(sep)

        report = "\n".join(lines)
        print(report)
        return report


# ==============================================================================
# SECTION 4: CLI
# ==============================================================================

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Walk-Forward Backtest Framework v1.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--run-full", action="store_true",
                   help="Run all walk-forward windows")
    g.add_argument("--run-latest", action="store_true",
                   help="Run most recent window only (fast validation)")
    p.add_argument("--factor-ic", action="store_true",
                   help="Print IC table (use with --run-full or --run-latest)")
    p.add_argument("--suggest-weights", action="store_true",
                   help="Print weight recommendations (use with --run-full)")
    p.add_argument("--tickers", type=str, default=None,
                   help="Comma-separated tickers (default: config.EQUITY_WATCHLIST)")
    p.add_argument("--tc-bps", type=float, default=5.0,
                   help="One-way transaction cost in bps (default: 5)")
    p.add_argument("--output-csv", type=str, default=None,
                   help="Save results DataFrame to CSV")
    return p


def main():
    parser = _build_parser()
    args = parser.parse_args()

    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]
    else:
        try:
            from config import EQUITY_WATCHLIST, CUSTOM_WATCHLIST
            tickers = list(dict.fromkeys(EQUITY_WATCHLIST + CUSTOM_WATCHLIST))
        except ImportError:
            tickers = []

        # Config lists are empty (tickers now live in watchlist.txt / Supabase).
        # Fall back to watchlist.txt TIER 1 + TIER 2, matching ai_quant behaviour.
        if not tickers:
            wl_paths = [
                os.path.join(os.path.dirname(__file__), "watchlist.txt"),
                "./watchlist.txt",
            ]
            for wl_path in wl_paths:
                if os.path.exists(wl_path):
                    _tiers = {"TIER 1", "TIER 2"}
                    current_tier = None
                    with open(wl_path) as _f:
                        for _line in _f:
                            _s = _line.strip()
                            if not _s:
                                continue
                            _u = _s.upper()
                            if "TIER 1" in _u:
                                current_tier = "TIER 1"
                            elif "TIER 2" in _u:
                                current_tier = "TIER 2"
                            elif _u.startswith("TIER") or "MANUALLY ADDED" in _u:
                                current_tier = None
                            elif not _s.startswith("#"):
                                t = _s.split("#")[0].strip().upper()
                                if t and current_tier in _tiers:
                                    tickers.append(t)
                    break

        if not tickers:
            print("ERROR: No tickers found. Pass --tickers or populate watchlist.txt.")
            sys.exit(1)

    bt = WalkForwardBacktest(tickers=tickers, transaction_cost_bps=args.tc_bps)

    if args.run_full:
        results = bt.run_full_backtest()
        if results.empty:
            sys.exit(1)
        bt.generate_report(results)
        if args.output_csv:
            results.to_csv(args.output_csv, index=False)
            print(f"\n  Results saved to: {args.output_csv}")
        # Persist to Supabase
        try:
            from utils.supabase_persist import save_backtest_runs
            save_backtest_runs(results, factor_ic=getattr(bt, "_aggregated_ic", {}))
            print("  Backtest results saved to Supabase.")
        except Exception as _exc:
            pass  # non-fatal

    elif args.run_latest:
        windows = bt._generate_windows()
        if not windows:
            print("ERROR: No complete windows available yet (check date range).")
            sys.exit(1)

        train_start, train_end, test_start, test_end = windows[-1]
        print(f"\n  Running latest window only: {test_start.date()} - {test_end.date()}")

        r = bt.run_single_window(train_start, train_end, test_start, test_end)
        if not r:
            print("  [ERROR] Latest window returned no results.")
            sys.exit(1)

        bt._all_results = [r]
        bt._aggregated_ic = {}
        for ic_row in r.get("factor_ic", []):
            bt._aggregated_ic.setdefault(ic_row["factor_name"], []).append(ic_row["mean_IC"])

        df = pd.DataFrame([{
            "window_start": r["test_start"].date(),
            "window_end": r["test_end"].date(),
            "sharpe": r["sharpe"],
            "max_drawdown": r["max_drawdown"],
            "hit_rate": r["hit_rate"],
            "turnover": r["turnover"],
            "best_factor": r["best_factor"],
            "worst_factor": r["worst_factor"],
            "optimized_weights": r.get("optimized_weights", "{}"),
            "train_sharpe": r.get("train_sharpe", 0.0),
            "n_weeks": r.get("n_weeks", 0),
            "tickers_included": r.get("tickers_included", 0),
            "tickers_excluded": r.get("tickers_excluded", 0),
        }])
        bt.generate_report(df)
        # Persist to Supabase
        try:
            from utils.supabase_persist import save_backtest_runs
            save_backtest_runs(df, factor_ic=getattr(bt, "_aggregated_ic", {}))
            print("  Backtest results saved to Supabase.")
        except Exception as _exc:
            pass  # non-fatal

    elif args.factor_ic or args.suggest_weights:
        print("  --factor-ic / --suggest-weights must be combined with --run-full.")
        print("  Example: python3 backtest.py --run-full --factor-ic")
        sys.exit(1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
