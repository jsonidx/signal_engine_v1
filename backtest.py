#!/usr/bin/env python3
"""
================================================================================
BACKTEST MODULE v1.0
================================================================================
Walk-forward validation for the Weekly Signal Engine.

Tests the EXACT same signal logic used in signal_engine.py against historical
data to determine whether the multi-factor equity and crypto trend signals
have generated alpha after costs.

METHODOLOGY:
    - Walk-forward: Signals computed using only data available at each point
    - Weekly rebalance (every 5 trading days) to match live cadence
    - Realistic transaction costs applied on every rebalance
    - No look-ahead bias: universe is fixed at start (survivorship bias caveat)
    - Performance vs benchmark (SPY for equity, BTC-USD for crypto)

USAGE:
    python backtest.py                    # Full backtest, both modules
    python backtest.py --equity-only      # Equity backtest only
    python backtest.py --crypto-only      # Crypto backtest only
    python backtest.py --years 5          # Override lookback period

IMPORTANT:
    - Survivorship bias WARNING: universe is today's constituents projected
      backward. Stocks that delisted/bankrupted are NOT in the sample.
      This FLATTERS results. Treat all metrics with appropriate skepticism.
    - Yahoo Finance data may have gaps or adjustment errors.
    - This is NOT investment advice. For research/educational purposes only.

DEPENDENCIES:
    pip install yfinance pandas numpy scipy tabulate matplotlib
================================================================================
"""

import argparse
import os
import sys
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats

warnings.filterwarnings("ignore")

try:
    from config import (
        PORTFOLIO_NAV, EQUITY_ALLOCATION, CRYPTO_ALLOCATION,
        EQUITY_WATCHLIST, CUSTOM_WATCHLIST, CRYPTO_TICKERS,
        EQUITY_FACTORS, CRYPTO_PARAMS, RISK_PARAMS, OUTPUT_DIR,
    )
except ImportError:
    print("ERROR: config.py not found. Place it in the same directory.")
    sys.exit(1)

# ─── Constants ────────────────────────────────────────────────────────────────
TRADING_DAYS_YEAR = 252
ANNUALIZE = np.sqrt(TRADING_DAYS_YEAR)
REBALANCE_FREQ = 5  # Trading days between rebalances (weekly)
MIN_HISTORY = 252    # Minimum days of history before first signal


# ==============================================================================
# SECTION 1: DATA
# ==============================================================================

def fetch_backtest_data(tickers: List[str], years: int, label: str) -> pd.DataFrame:
    """Fetch historical prices for backtesting."""
    end = datetime.now()
    start = end - timedelta(days=years * 365 + MIN_HISTORY)

    print(f"\n  Fetching {label}: {len(tickers)} tickers, {years}y + {MIN_HISTORY}d warmup")
    print(f"  Window: {start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')}")

    raw = yf.download(tickers, start=start, end=end, auto_adjust=True,
                      progress=True, threads=True)

    if isinstance(raw.columns, pd.MultiIndex):
        prices = raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw
    else:
        prices = raw[["Close"]].rename(columns={"Close": tickers[0]}) if len(tickers) == 1 else raw

    min_obs = int((years * 252 + MIN_HISTORY) * 0.5)
    valid = prices.columns[prices.count() >= min_obs]
    dropped = set(prices.columns) - set(valid)
    if dropped:
        print(f"  [WARN] Dropped {len(dropped)} tickers: {', '.join(list(dropped)[:10])}")
    prices = prices[valid].ffill(limit=5)
    print(f"  [OK] {len(prices.columns)} tickers, {len(prices)} days loaded")
    return prices


# ==============================================================================
# SECTION 2: SIGNAL REPLICATION (same logic as signal_engine.py)
# ==============================================================================

def zscore_cs(series: pd.Series) -> pd.Series:
    """Cross-sectional Z-score, winsorized at ±3."""
    z = (series - series.mean()) / (series.std() + 1e-10)
    return z.clip(-3, 3)


def compute_equity_signal_at_date(prices: pd.DataFrame, idx: int) -> pd.Series:
    """
    Compute composite equity signal using data up to index `idx`.
    Returns a Series of composite Z-scores indexed by ticker.
    Mirrors signal_engine.py logic exactly.
    """
    if idx < MIN_HISTORY:
        return pd.Series(dtype=float)

    window = prices.iloc[:idx + 1]
    signals = pd.DataFrame(index=window.columns)

    # Factor 1: 12-1 month momentum
    cfg = EQUITY_FACTORS["momentum_12_1"]
    lb, skip = cfg["lookback_long"], cfg["lookback_skip"]
    if len(window) > lb:
        raw = window.iloc[-skip] / window.iloc[-lb] - 1
        signals["mom_12_1"] = zscore_cs(raw) * cfg["weight"]
    else:
        return pd.Series(dtype=float)

    # Factor 2: 6-1 month momentum
    cfg = EQUITY_FACTORS["momentum_6_1"]
    lb, skip = cfg["lookback_long"], cfg["lookback_skip"]
    raw = window.iloc[-skip] / window.iloc[-lb] - 1 if len(window) > lb else pd.Series(0, index=window.columns)
    signals["mom_6_1"] = zscore_cs(raw) * cfg["weight"]

    # Factor 3: 5-day mean reversion (inverted)
    cfg = EQUITY_FACTORS["mean_reversion_5d"]
    raw = -(window.iloc[-1] / window.iloc[-cfg["lookback"]] - 1)
    signals["mr_5d"] = zscore_cs(raw) * cfg["weight"]

    # Factor 4: Low volatility
    cfg = EQUITY_FACTORS["volatility_quality"]
    log_ret = np.log(window / window.shift(1))
    vol = -(log_ret.iloc[-cfg["lookback"]:].std() * ANNUALIZE)
    signals["low_vol"] = zscore_cs(vol) * cfg["weight"]

    # Factor 5: Risk-adjusted momentum
    cfg = EQUITY_FACTORS["risk_adjusted_momentum"]
    mom = window.iloc[-1] / window.iloc[-cfg["mom_lookback"]] - 1
    v = log_ret.iloc[-cfg["vol_lookback"]:].std() * ANNUALIZE
    v = v.replace(0, np.nan)
    ram = mom / v
    signals["risk_adj"] = zscore_cs(ram) * cfg["weight"]

    composite = signals.sum(axis=1).dropna()
    return composite


def compute_crypto_signal_at_date(prices: pd.DataFrame, idx: int, ticker: str) -> float:
    """
    Compute crypto trend/momentum signal for a single asset at index `idx`.
    Returns adjusted signal value. Mirrors signal_engine.py logic.
    """
    params = CRYPTO_PARAMS
    px = prices[ticker].iloc[:idx + 1].dropna()

    if len(px) < params["ema_trend"]:
        return np.nan

    current = px.iloc[-1]

    # EMAs
    ema_f = px.ewm(span=params["ema_fast"], adjust=False).mean().iloc[-1]
    ema_s = px.ewm(span=params["ema_slow"], adjust=False).mean().iloc[-1]
    ema_t = px.ewm(span=params["ema_trend"], adjust=False).mean().iloc[-1]

    trend = ((1 if current > ema_f else -1) +
             (1 if current > ema_s else -1) +
             (1 if current > ema_t else -1)) / 3.0

    # ROC
    roc = 0
    for period, weight in zip(params["roc_periods"], params["roc_weights"]):
        if len(px) > period:
            roc += (px.iloc[-1] / px.iloc[-period] - 1) * weight

    # RSI
    delta = px.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_g = gain.ewm(com=params["rsi_period"] - 1, min_periods=params["rsi_period"]).mean().iloc[-1]
    avg_l = loss.ewm(com=params["rsi_period"] - 1, min_periods=params["rsi_period"]).mean().iloc[-1]
    rsi = 100 - (100 / (1 + avg_g / max(avg_l, 1e-10)))

    rsi_sig = 1.0 if rsi < params["rsi_oversold"] else (-0.5 if rsi > params["rsi_overbought"] else 0.0)

    # Vol regime
    log_ret = np.log(px / px.shift(1)).dropna()
    rvol = log_ret.iloc[-params["vol_lookback"]:].std() * ANNUALIZE

    if rvol > params["vol_threshold_extreme"]:
        vol_scale = 0.0
    elif rvol > params["vol_threshold_high"]:
        vol_scale = params["vol_scale_factor"]
    else:
        vol_scale = 1.0

    raw = (0.40 * trend +
           0.30 * np.clip(roc * 5, -1, 1) +
           0.15 * rsi_sig +
           0.15 * (1 if trend > 0 and roc > 0 else -0.5))

    return raw * vol_scale


# ==============================================================================
# SECTION 3: WALK-FORWARD BACKTEST ENGINE
# ==============================================================================

def backtest_equity(prices: pd.DataFrame, benchmark: pd.Series,
                    top_n: int = 10, cost_bps: float = 15) -> pd.DataFrame:
    """
    Walk-forward equity backtest.

    At each rebalance:
    1. Compute composite signal using only past data
    2. Go long top_n stocks, equal-weight (simple) or inverse-vol
    3. Hold for REBALANCE_FREQ days
    4. Deduct transaction costs on turnover

    Returns DataFrame with daily portfolio and benchmark returns.
    """
    cost = cost_bps / 10_000
    n_days = len(prices)
    dates = prices.index

    port_values = [1.0]  # Start with $1
    bench_values = [1.0]
    holdings = {}  # ticker -> weight
    trade_log = []

    rebal_dates = list(range(MIN_HISTORY, n_days, REBALANCE_FREQ))

    print(f"\n  Running equity walk-forward: {len(rebal_dates)} rebalance points")
    print(f"  Top {top_n} stocks, {cost_bps} bps/side, rebal every {REBALANCE_FREQ} days")

    for i in range(MIN_HISTORY, n_days):
        date = dates[i]

        # Daily return for current holdings
        if holdings:
            daily_ret = 0
            for ticker, weight in holdings.items():
                if ticker in prices.columns:
                    prev = prices[ticker].iloc[i - 1]
                    curr = prices[ticker].iloc[i]
                    if prev > 0 and not np.isnan(prev) and not np.isnan(curr):
                        daily_ret += weight * (curr / prev - 1)
            port_values.append(port_values[-1] * (1 + daily_ret))
        else:
            port_values.append(port_values[-1])

        # Benchmark return
        bp = benchmark.iloc[i]
        bp_prev = benchmark.iloc[i - 1]
        if bp_prev > 0 and not np.isnan(bp_prev) and not np.isnan(bp):
            bench_values.append(bench_values[-1] * (bp / bp_prev))
        else:
            bench_values.append(bench_values[-1])

        # Rebalance?
        if i in rebal_dates:
            signal = compute_equity_signal_at_date(prices, i)
            if signal.empty:
                continue

            # Top N stocks
            top = signal.nlargest(top_n)
            new_holdings = {}
            for ticker in top.index:
                new_holdings[ticker] = 1.0 / len(top)  # Equal weight

            # Compute turnover & costs
            old_tickers = set(holdings.keys())
            new_tickers = set(new_holdings.keys())
            turnover = 0
            for t in old_tickers | new_tickers:
                old_w = holdings.get(t, 0)
                new_w = new_holdings.get(t, 0)
                turnover += abs(new_w - old_w)

            cost_drag = turnover * cost  # Per side
            port_values[-1] *= (1 - cost_drag)

            trade_log.append({
                "date": date,
                "turnover": turnover,
                "cost_bps": turnover * cost_bps,
                "n_holdings": len(new_holdings),
                "top_pick": top.index[0] if len(top) > 0 else None,
            })

            holdings = new_holdings

    # Align lengths
    result_dates = dates[MIN_HISTORY - 1:]
    min_len = min(len(result_dates), len(port_values), len(bench_values))

    results = pd.DataFrame({
        "date": result_dates[:min_len],
        "portfolio": port_values[:min_len],
        "benchmark": bench_values[:min_len],
    }).set_index("date")

    return results, trade_log


def backtest_crypto(prices: pd.DataFrame, benchmark: pd.Series,
                    top_n: int = 3, cost_bps: float = 30) -> Tuple[pd.DataFrame, list]:
    """
    Walk-forward crypto backtest.

    At each rebalance:
    1. Compute trend/momentum signal for each asset
    2. Go long top_n assets with positive signals only
    3. Cash (flat) if no positive signals
    4. Apply vol regime scaling
    """
    cost = cost_bps / 10_000
    n_days = len(prices)
    dates = prices.index

    port_values = [1.0]
    bench_values = [1.0]
    holdings = {}
    trade_log = []

    rebal_dates = list(range(MIN_HISTORY, n_days, REBALANCE_FREQ))

    print(f"\n  Running crypto walk-forward: {len(rebal_dates)} rebalance points")
    print(f"  Top {top_n} positive signals, {cost_bps} bps/side")

    for i in range(MIN_HISTORY, n_days):
        date = dates[i]

        # Daily return
        if holdings:
            daily_ret = 0
            for ticker, weight in holdings.items():
                if ticker in prices.columns:
                    prev = prices[ticker].iloc[i - 1]
                    curr = prices[ticker].iloc[i]
                    if prev > 0 and not np.isnan(prev) and not np.isnan(curr):
                        daily_ret += weight * (curr / prev - 1)
            port_values.append(port_values[-1] * (1 + daily_ret))
        else:
            port_values.append(port_values[-1])

        # Benchmark
        bp = benchmark.iloc[i]
        bp_prev = benchmark.iloc[i - 1]
        if bp_prev > 0 and not np.isnan(bp_prev) and not np.isnan(bp):
            bench_values.append(bench_values[-1] * (bp / bp_prev))
        else:
            bench_values.append(bench_values[-1])

        # Rebalance
        if i in rebal_dates:
            signals = {}
            for ticker in prices.columns:
                sig = compute_crypto_signal_at_date(prices, i, ticker)
                if not np.isnan(sig):
                    signals[ticker] = sig

            # Only long positive signals
            positive = {k: v for k, v in signals.items() if v > 0.3}
            sorted_sigs = sorted(positive.items(), key=lambda x: -x[1])[:top_n]

            new_holdings = {}
            if sorted_sigs:
                for ticker, _ in sorted_sigs:
                    new_holdings[ticker] = 1.0 / len(sorted_sigs)

            # Turnover & costs
            old_tickers = set(holdings.keys())
            new_tickers = set(new_holdings.keys())
            turnover = 0
            for t in old_tickers | new_tickers:
                turnover += abs(holdings.get(t, 0) - new_holdings.get(t, 0))

            cost_drag = turnover * cost
            port_values[-1] *= (1 - cost_drag)

            trade_log.append({
                "date": date,
                "turnover": turnover,
                "n_holdings": len(new_holdings),
                "in_market": len(new_holdings) > 0,
            })

            holdings = new_holdings

    result_dates = dates[MIN_HISTORY - 1:]
    min_len = min(len(result_dates), len(port_values), len(bench_values))

    results = pd.DataFrame({
        "date": result_dates[:min_len],
        "portfolio": port_values[:min_len],
        "benchmark": bench_values[:min_len],
    }).set_index("date")

    return results, trade_log


# ==============================================================================
# SECTION 4: PERFORMANCE METRICS
# ==============================================================================

def compute_metrics(results: pd.DataFrame, trade_log: list, label: str) -> dict:
    """
    Compute comprehensive performance metrics.
    Returns dict with all key stats.
    """
    port = results["portfolio"]
    bench = results["benchmark"]

    # Daily returns
    port_ret = port.pct_change().dropna()
    bench_ret = bench.pct_change().dropna()

    # Annualized return (CAGR)
    n_years = len(port_ret) / TRADING_DAYS_YEAR
    if n_years > 0 and port.iloc[-1] > 0 and port.iloc[0] > 0:
        cagr_port = (port.iloc[-1] / port.iloc[0]) ** (1 / n_years) - 1
        cagr_bench = (bench.iloc[-1] / bench.iloc[0]) ** (1 / n_years) - 1
    else:
        cagr_port = cagr_bench = 0

    # Volatility
    vol_port = port_ret.std() * ANNUALIZE
    vol_bench = bench_ret.std() * ANNUALIZE

    # Sharpe (assuming 0% risk-free for simplicity)
    sharpe = cagr_port / vol_port if vol_port > 0 else 0

    # Sortino (downside deviation)
    downside = port_ret[port_ret < 0]
    downside_vol = downside.std() * ANNUALIZE if len(downside) > 0 else 0
    sortino = cagr_port / downside_vol if downside_vol > 0 else 0

    # Max drawdown
    cummax = port.cummax()
    drawdown = (port - cummax) / cummax
    max_dd = drawdown.min()

    # Max drawdown duration (days)
    dd_duration = 0
    current_dd = 0
    for i in range(1, len(port)):
        if port.iloc[i] < cummax.iloc[i]:
            current_dd += 1
            dd_duration = max(dd_duration, current_dd)
        else:
            current_dd = 0

    # Hit rate (% of positive weekly returns)
    weekly_ret = port_ret.rolling(5).sum().dropna()
    hit_rate = (weekly_ret > 0).mean()

    # Profit factor
    gross_profit = port_ret[port_ret > 0].sum()
    gross_loss = abs(port_ret[port_ret < 0].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    # Beta to benchmark
    if len(port_ret) > 30 and bench_ret.std() > 0:
        cov = np.cov(port_ret.values, bench_ret.values)
        beta = cov[0, 1] / cov[1, 1] if cov[1, 1] > 0 else 0
    else:
        beta = 0

    # Alpha (Jensen's)
    alpha = cagr_port - beta * cagr_bench

    # Information ratio
    excess = port_ret - bench_ret
    tracking_error = excess.std() * ANNUALIZE
    info_ratio = (cagr_port - cagr_bench) / tracking_error if tracking_error > 0 else 0

    # Total costs
    total_turnover = sum(t.get("turnover", 0) for t in trade_log)
    avg_turnover = total_turnover / len(trade_log) if trade_log else 0

    # Time in market (crypto)
    if trade_log and "in_market" in trade_log[0]:
        time_in_market = sum(1 for t in trade_log if t.get("in_market", False)) / len(trade_log)
    else:
        time_in_market = 1.0

    return {
        "label": label,
        "period_years": round(n_years, 1),
        "cagr_port": cagr_port,
        "cagr_bench": cagr_bench,
        "vol_port": vol_port,
        "vol_bench": vol_bench,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "max_dd_duration_days": dd_duration,
        "hit_rate_weekly": hit_rate,
        "profit_factor": profit_factor,
        "beta": beta,
        "alpha": alpha,
        "info_ratio": info_ratio,
        "avg_turnover_per_rebal": avg_turnover,
        "total_return_port": port.iloc[-1] / port.iloc[0] - 1,
        "total_return_bench": bench.iloc[-1] / bench.iloc[0] - 1,
        "time_in_market": time_in_market,
    }


# ==============================================================================
# SECTION 5: REPORTING
# ==============================================================================

def print_metrics(m: dict):
    """Print formatted performance report."""
    print(f"\n{'═' * 60}")
    print(f"  {m['label']}")
    print(f"  Period: {m['period_years']} years")
    print(f"{'═' * 60}")

    print(f"\n  RETURNS:")
    print(f"  {'Portfolio CAGR:':<30} {m['cagr_port']:>10.2%}")
    print(f"  {'Benchmark CAGR:':<30} {m['cagr_bench']:>10.2%}")
    print(f"  {'Total Return (port):':<30} {m['total_return_port']:>10.2%}")
    print(f"  {'Total Return (bench):':<30} {m['total_return_bench']:>10.2%}")

    print(f"\n  RISK:")
    print(f"  {'Portfolio Vol:':<30} {m['vol_port']:>10.2%}")
    print(f"  {'Benchmark Vol:':<30} {m['vol_bench']:>10.2%}")
    print(f"  {'Max Drawdown:':<30} {m['max_drawdown']:>10.2%}")
    print(f"  {'Max DD Duration:':<30} {m['max_dd_duration_days']:>10} days")

    print(f"\n  RISK-ADJUSTED:")
    print(f"  {'Sharpe Ratio:':<30} {m['sharpe']:>10.3f}")
    print(f"  {'Sortino Ratio:':<30} {m['sortino']:>10.3f}")
    print(f"  {'Information Ratio:':<30} {m['info_ratio']:>10.3f}")

    # Sharpe quality assessment
    sharpe = m["sharpe"]
    if sharpe >= 2.5:
        quality = "⚠️  SUSPICIOUS — likely overfitting or survivorship bias"
    elif sharpe >= 1.5:
        quality = "🟢 EXCELLENT (but verify out-of-sample)"
    elif sharpe >= 0.8:
        quality = "🟡 ACCEPTABLE — proceed with caution"
    elif sharpe >= 0.5:
        quality = "🟠 MARGINAL — needs improvement"
    else:
        quality = "🔴 INSUFFICIENT — does not clear hurdle"
    print(f"  {'Sharpe Assessment:':<30} {quality}")

    print(f"\n  FACTOR ANALYSIS:")
    print(f"  {'Beta to Benchmark:':<30} {m['beta']:>10.3f}")
    print(f"  {'Alpha (Jensen):':<30} {m['alpha']:>10.2%}")

    print(f"\n  TRADE STATISTICS:")
    print(f"  {'Hit Rate (weekly):':<30} {m['hit_rate_weekly']:>10.1%}")
    print(f"  {'Profit Factor:':<30} {m['profit_factor']:>10.2f}")
    print(f"  {'Avg Turnover/Rebal:':<30} {m['avg_turnover_per_rebal']:>10.2%}")
    if m["time_in_market"] < 1.0:
        print(f"  {'Time in Market:':<30} {m['time_in_market']:>10.1%}")


def print_annual_returns(results: pd.DataFrame, label: str):
    """Print year-by-year performance breakdown."""
    port = results["portfolio"]
    bench = results["benchmark"]

    print(f"\n  ANNUAL RETURNS ({label}):")
    print(f"  {'Year':<8}{'Portfolio':>12}{'Benchmark':>12}{'Excess':>12}")
    print(f"  {'─' * 44}")

    years = sorted(set(results.index.year))
    for year in years:
        mask = results.index.year == year
        yr_data = results[mask]
        if len(yr_data) < 20:
            continue
        p_ret = yr_data["portfolio"].iloc[-1] / yr_data["portfolio"].iloc[0] - 1
        b_ret = yr_data["benchmark"].iloc[-1] / yr_data["benchmark"].iloc[0] - 1
        excess = p_ret - b_ret
        marker = "✓" if excess > 0 else "✗"
        print(f"  {year:<8}{p_ret:>11.2%} {b_ret:>11.2%} {excess:>11.2%}  {marker}")


def print_drawdown_analysis(results: pd.DataFrame, label: str):
    """Print worst drawdown periods."""
    port = results["portfolio"]
    cummax = port.cummax()
    drawdown = (port - cummax) / cummax

    # Find top 5 drawdown troughs
    dd_series = drawdown.copy()
    worst_dds = []

    for _ in range(5):
        if dd_series.empty or dd_series.min() >= -0.001:
            break
        trough_idx = dd_series.idxmin()
        trough_val = dd_series[trough_idx]

        # Find start (last peak before trough)
        pre_trough = dd_series.loc[:trough_idx]
        peaks = pre_trough[pre_trough >= -0.001]
        start_idx = peaks.index[-1] if len(peaks) > 0 else pre_trough.index[0]

        # Find recovery (next peak after trough)
        post_trough = dd_series.loc[trough_idx:]
        recoveries = post_trough[post_trough >= -0.001]
        end_idx = recoveries.index[0] if len(recoveries) > 0 else post_trough.index[-1]

        worst_dds.append({
            "start": start_idx,
            "trough": trough_idx,
            "end": end_idx,
            "depth": trough_val,
            "duration": (end_idx - start_idx).days,
        })

        # Zero out this region to find next worst
        dd_series.loc[start_idx:end_idx] = 0

    if worst_dds:
        print(f"\n  WORST DRAWDOWNS ({label}):")
        print(f"  {'#':<4}{'Depth':>8}{'Start':>14}{'Trough':>14}{'Recovery':>14}{'Days':>8}")
        print(f"  {'─' * 62}")
        for i, dd in enumerate(worst_dds, 1):
            rec = dd["end"].strftime("%Y-%m-%d") if dd["end"] != results.index[-1] else "ongoing"
            print(f"  {i:<4}{dd['depth']:>7.1%} "
                  f"{dd['start'].strftime('%Y-%m-%d'):>13} "
                  f"{dd['trough'].strftime('%Y-%m-%d'):>13} "
                  f"{rec:>13} "
                  f"{dd['duration']:>7}")


def save_results(results: pd.DataFrame, metrics: dict, label: str):
    """Save backtest results to CSV."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    tag = label.lower().replace(" ", "_").replace("/", "_")

    results_path = os.path.join(OUTPUT_DIR, f"backtest_{tag}_equity_curve.csv")
    results.to_csv(results_path)

    metrics_path = os.path.join(OUTPUT_DIR, f"backtest_{tag}_metrics.csv")
    pd.Series(metrics).to_csv(metrics_path, header=["value"])

    print(f"\n  📁 Saved: {results_path}")
    print(f"  📁 Saved: {metrics_path}")


def print_verdict(eq_metrics: dict = None, cr_metrics: dict = None):
    """Final assessment using the strategy framework."""
    print(f"\n\n{'█' * 60}")
    print(f"  BACKTEST VERDICT")
    print(f"{'█' * 60}")

    if eq_metrics:
        s = eq_metrics["sharpe"]
        print(f"\n  EQUITY MULTI-FACTOR:")
        if s >= 0.8:
            print(f"  → PROCEED — Sharpe {s:.2f} clears 0.8 hurdle (net of costs)")
            print(f"  → Alpha of {eq_metrics['alpha']:.1%} vs benchmark suggests real signal")
        elif s >= 0.5:
            print(f"  → INVESTIGATE FURTHER — Sharpe {s:.2f} is marginal")
            print(f"  → May improve with factor tuning or universe expansion")
        else:
            print(f"  → TERMINATE — Sharpe {s:.2f} below 0.5 threshold")
            print(f"  → Signal does not justify transaction costs")

        if s > 2.5:
            print(f"  → ⚠️  Sharpe > 2.5 is SUSPICIOUS. Likely survivorship bias.")

    if cr_metrics:
        s = cr_metrics["sharpe"]
        print(f"\n  CRYPTO TREND/MOMENTUM:")
        if s >= 0.8:
            print(f"  → PROCEED — Sharpe {s:.2f} clears hurdle")
        elif s >= 0.5:
            print(f"  → INVESTIGATE FURTHER — Sharpe {s:.2f}, trend-following")
            print(f"    has inherent whipsaw cost in range-bound markets")
        else:
            print(f"  → TERMINATE or redesign — Sharpe {s:.2f}")

        tim = cr_metrics.get("time_in_market", 1.0)
        if tim < 0.3:
            print(f"  → Only in market {tim:.0%} of the time — signal is very selective")

    print(f"\n  SURVIVORSHIP BIAS CAVEAT:")
    print(f"  These results use today's universe projected backward.")
    print(f"  Stocks that went bankrupt or delisted are NOT included.")
    print(f"  Real-world performance would be WORSE than shown here.")
    print(f"  Discount all Sharpe ratios by ~0.2-0.4 for this effect.")

    print(f"\n  RECOMMENDATION:")
    print(f"  Run this backtest quarterly as your universe changes.")
    print(f"  If Sharpe degrades below 0.5, revisit signal construction.")
    print(f"{'█' * 60}\n")


# ==============================================================================
# SECTION 6: MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Signal Engine Backtester v1.0")
    parser.add_argument("--equity-only", action="store_true")
    parser.add_argument("--crypto-only", action="store_true")
    parser.add_argument("--years", type=int, default=3, help="Years of history (default: 3)")
    parser.add_argument("--top-n-equity", type=int, default=10, help="Top N equities (default: 10)")
    parser.add_argument("--top-n-crypto", type=int, default=3, help="Top N crypto (default: 3)")
    args = parser.parse_args()

    print("\n" + "█" * 60)
    print("  SIGNAL ENGINE BACKTESTER v1.0")
    print(f"  Lookback: {args.years} years | Walk-forward | Weekly rebalance")
    print("█" * 60)

    eq_metrics = None
    cr_metrics = None

    # ── Equity Backtest ──
    if not args.crypto_only:
        print("\n\n" + "█" * 60)
        print("  EQUITY MULTI-FACTOR BACKTEST")
        print("█" * 60)

        universe = list(set(EQUITY_WATCHLIST + CUSTOM_WATCHLIST + ["SPY"]))
        prices = fetch_backtest_data(universe, args.years, "equity + benchmark")

        if "SPY" in prices.columns:
            benchmark = prices["SPY"]
            eq_prices = prices.drop(columns=["SPY"], errors="ignore")
        else:
            print("  [ERROR] SPY benchmark not available. Cannot proceed.")
            eq_prices = pd.DataFrame()
            benchmark = pd.Series()

        if not eq_prices.empty:
            results, trade_log = backtest_equity(
                eq_prices, benchmark,
                top_n=args.top_n_equity,
                cost_bps=RISK_PARAMS["equity_cost_bps"]
            )
            eq_metrics = compute_metrics(results, trade_log, "Equity Multi-Factor")
            print_metrics(eq_metrics)
            print_annual_returns(results, "Equity")
            print_drawdown_analysis(results, "Equity")
            save_results(results, eq_metrics, "equity")

    # ── Crypto Backtest ──
    if not args.equity_only:
        print("\n\n" + "█" * 60)
        print("  CRYPTO TREND/MOMENTUM BACKTEST")
        print("█" * 60)

        crypto_universe = CRYPTO_TICKERS + ["BTC-USD"] if "BTC-USD" not in CRYPTO_TICKERS else CRYPTO_TICKERS
        prices = fetch_backtest_data(crypto_universe, args.years, "crypto + benchmark")

        if "BTC-USD" in prices.columns:
            benchmark = prices["BTC-USD"]
        else:
            print("  [ERROR] BTC-USD benchmark not available.")
            benchmark = pd.Series()

        if not prices.empty and not benchmark.empty:
            results, trade_log = backtest_crypto(
                prices, benchmark,
                top_n=args.top_n_crypto,
                cost_bps=RISK_PARAMS["crypto_cost_bps"]
            )
            cr_metrics = compute_metrics(results, trade_log, "Crypto Trend/Momentum")
            print_metrics(cr_metrics)
            print_annual_returns(results, "Crypto")
            print_drawdown_analysis(results, "Crypto")
            save_results(results, cr_metrics, "crypto")

    # ── Final Verdict ──
    print_verdict(eq_metrics, cr_metrics)

    print("  ⚠️  THIS IS NOT INVESTMENT ADVICE.")
    print("  All results are for research/educational purposes only.\n")


if __name__ == "__main__":
    main()
