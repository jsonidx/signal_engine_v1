#!/usr/bin/env python3
"""
================================================================================
WEEKLY SIGNAL ENGINE v1.0
================================================================================
Multi-factor equity screener + crypto trend/momentum signal generator.
Designed for weekly (Sunday evening) execution with sub-€50K portfolios.

USAGE:
    python signal_engine.py                  # Full run, both modules
    python signal_engine.py --equity-only    # Equity screener only
    python signal_engine.py --crypto-only    # Crypto signals only
    python signal_engine.py --watchlist AAPL,MSFT,GOOGL  # Custom tickers

AUTHOR NOTES:
    - All signals are INFORMATIONAL. This is not investment advice.
    - No look-ahead bias: signals use only data available at calc time.
    - Transaction costs are baked into position sizing recommendations.
    - Crypto vol regime filter will automatically de-risk in high-vol.

DEPENDENCIES:
    pip install yfinance pandas numpy scipy tabulate
================================================================================
"""

import argparse
import os
import sys
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats

warnings.filterwarnings("ignore")

# ─── Import config ────────────────────────────────────────────────────────────
try:
    from config import (
        PORTFOLIO_NAV, EQUITY_ALLOCATION, CRYPTO_ALLOCATION, CASH_BUFFER,
        EQUITY_WATCHLIST, CUSTOM_WATCHLIST, CRYPTO_TICKERS,
        EQUITY_FACTORS, CRYPTO_PARAMS, RISK_PARAMS,
        OUTPUT_DIR, CSV_EXPORT, CONSOLE_PRINT, DATA_LOOKBACK_DAYS,
    )
except ImportError:
    print("ERROR: config.py not found. Place it in the same directory.")
    sys.exit(1)

# ─── Constants ────────────────────────────────────────────────────────────────
TRADING_DAYS_PER_YEAR = 252
ANNUALIZATION_FACTOR = np.sqrt(TRADING_DAYS_PER_YEAR)
TODAY = datetime.now()
SIGNAL_DATE = TODAY.strftime("%Y-%m-%d")


# ==============================================================================
# SECTION 1: DATA ACQUISITION
# ==============================================================================

def fetch_price_data(
    tickers: List[str],
    lookback_days: int = DATA_LOOKBACK_DAYS,
    label: str = "assets"
) -> pd.DataFrame:
    """
    Fetch adjusted close prices from Yahoo Finance.
    Returns a DataFrame indexed by date with tickers as columns.

    NOTE: Yahoo Finance data is adequate for weekly screening on liquid
    large-caps. For anything sub-$1B market cap or for precise backtesting,
    you need a proper data vendor (Bloomberg, Refinitiv, Polygon.io).
    """
    end_date = TODAY
    start_date = end_date - timedelta(days=lookback_days)

    print(f"\n{'='*60}")
    print(f"  Fetching {label}: {len(tickers)} tickers")
    print(f"  Window: {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")
    print(f"{'='*60}")

    # Download in batch — faster than individual calls
    try:
        raw = yf.download(
            tickers,
            start=start_date,
            end=end_date,
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        print(f"  [ERROR] Download failed: {e}")
        return pd.DataFrame()

    # Handle single vs multi-ticker return format
    if isinstance(raw.columns, pd.MultiIndex):
        prices = raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw.iloc[:, :len(tickers)]
    else:
        prices = raw[["Close"]].rename(columns={"Close": tickers[0]}) if len(tickers) == 1 else raw

    # Drop tickers with insufficient data (< 60% of expected days)
    min_obs = int(lookback_days * 0.4)  # Generous threshold for newer listings
    valid_cols = prices.columns[prices.count() >= min_obs]
    dropped = set(prices.columns) - set(valid_cols)
    if dropped:
        print(f"  [WARN] Dropped {len(dropped)} tickers (insufficient data): "
              f"{', '.join(list(dropped)[:10])}{'...' if len(dropped) > 10 else ''}")
    prices = prices[valid_cols]

    # Forward-fill small gaps (weekends, holidays) — max 5 days
    prices = prices.ffill(limit=5)

    print(f"  [OK] {len(prices.columns)} tickers loaded, "
          f"{len(prices)} trading days")
    return prices


# ==============================================================================
# SECTION 2: SIGNAL COMPUTATION — EQUITY MULTI-FACTOR
# ==============================================================================

def zscore_cross_sectional(series: pd.Series) -> pd.Series:
    """
    Cross-sectional Z-score: (x - mean) / std across all stocks at a point in time.
    Winsorize at ±3σ to prevent outlier contamination.
    """
    z = (series - series.mean()) / series.std()
    return z.clip(-3, 3)


def compute_momentum(prices: pd.DataFrame, lookback: int, skip: int = 0) -> pd.Series:
    """
    Classic momentum signal: return over [T-lookback, T-skip].
    Skip last `skip` days to avoid short-term mean-reversion contamination.
    """
    if skip > 0:
        returns = prices.iloc[-skip] / prices.iloc[-lookback] - 1
    else:
        returns = prices.iloc[-1] / prices.iloc[-lookback] - 1
    return returns


def compute_realized_vol(prices: pd.DataFrame, lookback: int) -> pd.Series:
    """Annualized realized volatility over lookback period."""
    log_returns = np.log(prices / prices.shift(1))
    vol = log_returns.iloc[-lookback:].std() * ANNUALIZATION_FACTOR
    return vol


def compute_risk_adjusted_momentum(
    prices: pd.DataFrame, mom_lookback: int, vol_lookback: int
) -> pd.Series:
    """Momentum divided by volatility — Sharpe-like signal."""
    mom = compute_momentum(prices, mom_lookback)
    vol = compute_realized_vol(prices, vol_lookback)
    # Avoid division by zero
    vol = vol.replace(0, np.nan)
    return mom / vol


def compute_equity_composite(prices: pd.DataFrame) -> pd.DataFrame:
    """
    Compute multi-factor composite score for equity universe.

    Returns DataFrame with columns:
        ticker, momentum_12_1, momentum_6_1, mean_reversion_5d,
        volatility_quality, risk_adj_mom, composite_z, rank
    """
    if prices.empty or len(prices) < 252:
        print("  [ERROR] Insufficient price history for equity signals")
        return pd.DataFrame()

    signals = pd.DataFrame(index=prices.columns)
    signals.index.name = "ticker"

    # ── Factor 1: 12-1 Month Momentum ──
    cfg = EQUITY_FACTORS["momentum_12_1"]
    raw = compute_momentum(prices, cfg["lookback_long"], cfg["lookback_skip"])
    signals["momentum_12_1_raw"] = raw
    signals["momentum_12_1_z"] = zscore_cross_sectional(raw)

    # ── Factor 2: 6-1 Month Momentum ──
    cfg = EQUITY_FACTORS["momentum_6_1"]
    raw = compute_momentum(prices, cfg["lookback_long"], cfg["lookback_skip"])
    signals["momentum_6_1_raw"] = raw
    signals["momentum_6_1_z"] = zscore_cross_sectional(raw)

    # ── Factor 3: 5-Day Mean Reversion ──
    cfg = EQUITY_FACTORS["mean_reversion_5d"]
    raw = compute_momentum(prices, cfg["lookback"])
    if cfg.get("invert", False):
        raw = -raw  # Losers expected to bounce
    signals["mean_rev_5d_raw"] = -compute_momentum(prices, cfg["lookback"])  # Store original
    signals["mean_rev_5d_z"] = zscore_cross_sectional(raw)

    # ── Factor 4: Low Volatility (Quality Proxy) ──
    cfg = EQUITY_FACTORS["volatility_quality"]
    raw = compute_realized_vol(prices, cfg["lookback"])
    if cfg.get("invert", False):
        raw = -raw  # Lower vol = higher score
    signals["vol_quality_raw"] = compute_realized_vol(prices, cfg["lookback"])
    signals["vol_quality_z"] = zscore_cross_sectional(raw)

    # ── Factor 5: Risk-Adjusted Momentum ──
    cfg = EQUITY_FACTORS["risk_adjusted_momentum"]
    raw = compute_risk_adjusted_momentum(prices, cfg["mom_lookback"], cfg["vol_lookback"])
    signals["risk_adj_mom_raw"] = raw
    signals["risk_adj_mom_z"] = zscore_cross_sectional(raw)

    # ── Composite Score ──
    weights = {
        "momentum_12_1_z": EQUITY_FACTORS["momentum_12_1"]["weight"],
        "momentum_6_1_z": EQUITY_FACTORS["momentum_6_1"]["weight"],
        "mean_rev_5d_z": EQUITY_FACTORS["mean_reversion_5d"]["weight"],
        "vol_quality_z": EQUITY_FACTORS["volatility_quality"]["weight"],
        "risk_adj_mom_z": EQUITY_FACTORS["risk_adjusted_momentum"]["weight"],
    }

    signals["composite_z"] = sum(
        signals[col] * w for col, w in weights.items()
    )

    # Rank (1 = best)
    signals["rank"] = signals["composite_z"].rank(ascending=False).astype(int)
    signals = signals.sort_values("rank")

    # Drop rows where composite couldn't be computed
    signals = signals.dropna(subset=["composite_z"])

    return signals


# ==============================================================================
# SECTION 3: SIGNAL COMPUTATION — CRYPTO TREND / MOMENTUM
# ==============================================================================

def compute_ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=span, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_crypto_signals(prices: pd.DataFrame) -> pd.DataFrame:
    """
    Compute trend/momentum signals for crypto universe.

    Signal logic:
    1. Trend score: Position relative to fast/slow/trend EMAs
    2. Momentum score: Weighted ROC across multiple lookbacks
    3. RSI: Timing overlay (oversold = better entry)
    4. Vol regime: Scale factor based on realized volatility

    Returns DataFrame with per-asset signals and sizing recommendations.
    """
    if prices.empty:
        print("  [ERROR] No crypto price data available")
        return pd.DataFrame()

    params = CRYPTO_PARAMS
    results = []

    for ticker in prices.columns:
        px = prices[ticker].dropna()
        if len(px) < params["ema_trend"]:
            continue

        current_price = px.iloc[-1]

        # ── EMAs ──
        ema_fast = compute_ema(px, params["ema_fast"]).iloc[-1]
        ema_slow = compute_ema(px, params["ema_slow"]).iloc[-1]
        ema_trend = compute_ema(px, params["ema_trend"]).iloc[-1]

        # Trend score: +1 for each EMA the price is above
        trend_score = (
            (1 if current_price > ema_fast else -1) +
            (1 if current_price > ema_slow else -1) +
            (1 if current_price > ema_trend else -1)
        ) / 3.0  # Normalize to [-1, +1]

        # ── Rate of Change (multi-period momentum) ──
        roc_scores = []
        for period, weight in zip(params["roc_periods"], params["roc_weights"]):
            if len(px) > period:
                roc = (px.iloc[-1] / px.iloc[-period] - 1)
                roc_scores.append(roc * weight)
        momentum_score = sum(roc_scores) if roc_scores else 0

        # ── RSI ──
        rsi = compute_rsi(px, params["rsi_period"]).iloc[-1]

        # RSI regime: 1 = oversold (good entry), -1 = overbought (caution)
        if rsi < params["rsi_oversold"]:
            rsi_signal = 1.0
        elif rsi > params["rsi_overbought"]:
            rsi_signal = -0.5
        else:
            rsi_signal = 0.0

        # ── Realized Volatility & Regime ──
        log_ret = np.log(px / px.shift(1)).dropna()
        realized_vol = log_ret.iloc[-params["vol_lookback"]:].std() * ANNUALIZATION_FACTOR

        if realized_vol > params["vol_threshold_extreme"]:
            vol_regime = "EXTREME"
            vol_scale = 0.0  # No position
        elif realized_vol > params["vol_threshold_high"]:
            vol_regime = "HIGH"
            vol_scale = params["vol_scale_factor"]
        else:
            vol_regime = "NORMAL"
            vol_scale = 1.0

        # ── Composite Signal ──
        # Trend (40%) + Momentum (30%) + RSI timing (15%) + Vol adjustment
        raw_signal = (
            0.40 * trend_score +
            0.30 * np.clip(momentum_score * 5, -1, 1) +  # Scale ROC to [-1,1]
            0.15 * rsi_signal +
            0.15 * (1 if trend_score > 0 and momentum_score > 0 else -0.5)
        )

        # Apply vol regime scaling
        adjusted_signal = raw_signal * vol_scale

        # Signal classification
        if adjusted_signal > 0.3:
            action = "BUY"
        elif adjusted_signal > 0.0:
            action = "HOLD"
        elif adjusted_signal > -0.3:
            action = "REDUCE"
        else:
            action = "SELL / NO POSITION"

        results.append({
            "ticker": ticker,
            "price": current_price,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "ema_trend": ema_trend,
            "trend_score": trend_score,
            "momentum_score": momentum_score,
            "rsi": rsi,
            "realized_vol_ann": realized_vol,
            "vol_regime": vol_regime,
            "raw_signal": raw_signal,
            "adjusted_signal": adjusted_signal,
            "action": action,
        })

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values("adjusted_signal", ascending=False)
        df["rank"] = range(1, len(df) + 1)
    return df


# ==============================================================================
# SECTION 4: POSITION SIZING & RISK MANAGEMENT
# ==============================================================================

def compute_position_sizes(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    asset_type: str,  # "equity" or "crypto"
    total_allocation_eur: float,
) -> pd.DataFrame:
    """
    Kelly-fractional position sizing with risk constraints.

    For equities: Top N by composite score, inverse-vol weighted.
    For crypto: Only BUY signals, vol-adjusted sizing.
    """
    rp = RISK_PARAMS

    if asset_type == "equity":
        max_positions = rp["max_equity_positions"]
        max_pct = rp["max_position_equity_pct"]
        cost_bps = rp["equity_cost_bps"]
        # Take top N
        top = signals.head(max_positions).copy()
        tickers = top.index.tolist()
    else:
        max_positions = rp["max_crypto_positions"]
        max_pct = rp["max_position_crypto_pct"]
        cost_bps = rp["crypto_cost_bps"]
        # Only BUY signals
        buy_signals = signals[signals["action"] == "BUY"].head(max_positions)
        if buy_signals.empty:
            return pd.DataFrame()
        tickers = buy_signals["ticker"].tolist()
        top = buy_signals.set_index("ticker")

    # Compute inverse-volatility weights
    vol_data = {}
    for t in tickers:
        if t in prices.columns:
            log_ret = np.log(prices[t] / prices[t].shift(1)).dropna()
            vol = log_ret.iloc[-63:].std() * ANNUALIZATION_FACTOR
            vol_data[t] = vol if vol > 0 else np.nan

    vol_series = pd.Series(vol_data)
    inv_vol = 1.0 / vol_series.dropna()
    weights_raw = inv_vol / inv_vol.sum()

    # Apply Kelly fraction
    weights_kelly = weights_raw * rp["kelly_fraction"]

    # Cap individual positions
    weights_capped = weights_kelly.clip(upper=max_pct)

    # Renormalize so total = kelly_fraction
    if weights_capped.sum() > 0:
        weights_final = weights_capped * (rp["kelly_fraction"] / weights_capped.sum())
        # But don't exceed total allocation
        weights_final = weights_final.clip(upper=max_pct)

    # Convert to EUR
    position_eur = weights_final * total_allocation_eur

    # Filter out positions below minimum size
    position_eur = position_eur[position_eur >= rp["min_position_eur"]]

    # Estimate transaction cost
    cost_eur = position_eur * (cost_bps / 10_000) * 2  # Round-trip

    result = pd.DataFrame({
        "weight_pct": (weights_final * 100).round(2),
        "position_eur": position_eur.round(0),
        "annual_vol": (vol_series * 100).round(1),
        "est_round_trip_cost_eur": cost_eur.round(2),
    })
    result = result[result["position_eur"].notna()]
    result = result.sort_values("position_eur", ascending=False)

    return result


# ==============================================================================
# SECTION 5: REPORTING
# ==============================================================================

def print_header():
    """Print engine header."""
    print("\n" + "█" * 60)
    print("  WEEKLY SIGNAL ENGINE — Run Date: " + SIGNAL_DATE)
    print("  Portfolio: €{:,.0f} | Equity: {:.0%} | Crypto: {:.0%} | Cash: {:.0%}".format(
        PORTFOLIO_NAV, EQUITY_ALLOCATION, CRYPTO_ALLOCATION, CASH_BUFFER))
    print("█" * 60)


def print_equity_report(signals: pd.DataFrame, positions: pd.DataFrame):
    """Print formatted equity screening results."""
    print("\n" + "─" * 60)
    print("  📊 EQUITY MULTI-FACTOR SCREENER")
    print("─" * 60)

    if signals.empty:
        print("  [NO SIGNALS] Insufficient data.")
        return

    # Top picks
    n = min(20, len(signals))
    top = signals.head(n)
    print(f"\n  TOP {n} RANKED STOCKS (by composite Z-score):\n")
    print(f"  {'Rank':<6}{'Ticker':<10}{'Composite':>10}{'Mom12-1':>10}"
          f"{'Mom6-1':>10}{'MeanRev5d':>10}{'VolQual':>10}{'RiskAdj':>10}")
    print("  " + "─" * 76)

    for _, row in top.iterrows():
        name = row.name if isinstance(row.name, str) else str(row.name)
        print(f"  {int(row['rank']):<6}{name:<10}"
              f"{row['composite_z']:>10.3f}"
              f"{row['momentum_12_1_z']:>10.3f}"
              f"{row['momentum_6_1_z']:>10.3f}"
              f"{row['mean_rev_5d_z']:>10.3f}"
              f"{row['vol_quality_z']:>10.3f}"
              f"{row['risk_adj_mom_z']:>10.3f}")

    # Bottom 5 (potential shorts / avoids)
    print(f"\n  BOTTOM 5 (AVOID / UNDERWEIGHT):\n")
    bottom = signals.tail(5)
    for _, row in bottom.iterrows():
        name = row.name if isinstance(row.name, str) else str(row.name)
        print(f"  {int(row['rank']):<6}{name:<10}{row['composite_z']:>10.3f}")

    # Position sizing
    if not positions.empty:
        print(f"\n  RECOMMENDED POSITION SIZES (Quarter-Kelly, €{PORTFOLIO_NAV * EQUITY_ALLOCATION:,.0f} equity allocation):\n")
        print(f"  {'Ticker':<10}{'Weight%':>10}{'EUR':>12}{'AnnVol%':>10}{'Cost€':>10}")
        print("  " + "─" * 52)
        for ticker, row in positions.iterrows():
            print(f"  {ticker:<10}{row['weight_pct']:>10.1f}%"
                  f"{row['position_eur']:>11,.0f}"
                  f"{row['annual_vol']:>10.1f}%"
                  f"{row['est_round_trip_cost_eur']:>10.2f}")
        print(f"\n  Total allocated: €{positions['position_eur'].sum():,.0f} "
              f"| Total est. costs: €{positions['est_round_trip_cost_eur'].sum():,.2f}")


def print_crypto_report(signals: pd.DataFrame, positions: pd.DataFrame):
    """Print formatted crypto signal results."""
    print("\n" + "─" * 60)
    print("  🪙 CRYPTO TREND / MOMENTUM SIGNALS")
    print("─" * 60)

    if signals.empty:
        print("  [NO SIGNALS] Insufficient data.")
        return

    print(f"\n  {'Rank':<6}{'Ticker':<12}{'Price':>12}{'Signal':>9}"
          f"{'Trend':>8}{'Mom':>8}{'RSI':>7}{'Vol%':>8}{'VolReg':>10}{'Action':<16}")
    print("  " + "─" * 96)

    for _, row in signals.iterrows():
        action_color = row["action"]
        print(f"  {int(row['rank']):<6}{row['ticker']:<12}"
              f"${row['price']:>11,.2f}"
              f"{row['adjusted_signal']:>9.3f}"
              f"{row['trend_score']:>8.2f}"
              f"{row['momentum_score']:>8.4f}"
              f"{row['rsi']:>7.1f}"
              f"{row['realized_vol_ann']*100:>7.1f}%"
              f"{row['vol_regime']:>10}"
              f"  {action_color:<16}")

    # Vol regime warnings
    extreme = signals[signals["vol_regime"] == "EXTREME"]
    high = signals[signals["vol_regime"] == "HIGH"]
    if not extreme.empty:
        print(f"\n  ⚠️  EXTREME VOL ({len(extreme)} assets): "
              f"{', '.join(extreme['ticker'].tolist())} — ZERO POSITION RECOMMENDED")
    if not high.empty:
        print(f"  ⚠️  HIGH VOL ({len(high)} assets): "
              f"{', '.join(high['ticker'].tolist())} — HALF SIZE")

    # Position sizing
    if not positions.empty:
        print(f"\n  RECOMMENDED CRYPTO POSITIONS (€{PORTFOLIO_NAV * CRYPTO_ALLOCATION:,.0f} allocation):\n")
        print(f"  {'Ticker':<12}{'Weight%':>10}{'EUR':>12}{'AnnVol%':>10}{'Cost€':>10}")
        print("  " + "─" * 54)
        for ticker, row in positions.iterrows():
            print(f"  {ticker:<12}{row['weight_pct']:>10.1f}%"
                  f"{row['position_eur']:>11,.0f}"
                  f"{row['annual_vol']:>10.1f}%"
                  f"{row['est_round_trip_cost_eur']:>10.2f}")
        print(f"\n  Total allocated: €{positions['position_eur'].sum():,.0f} "
              f"| Total est. costs: €{positions['est_round_trip_cost_eur'].sum():,.2f}")


def print_portfolio_summary(eq_pos: pd.DataFrame, cr_pos: pd.DataFrame):
    """Print consolidated portfolio summary."""
    print("\n" + "═" * 60)
    print("  📋 PORTFOLIO SUMMARY")
    print("═" * 60)

    eq_total = eq_pos["position_eur"].sum() if not eq_pos.empty else 0
    cr_total = cr_pos["position_eur"].sum() if not cr_pos.empty else 0
    invested = eq_total + cr_total
    cash = PORTFOLIO_NAV - invested

    print(f"\n  NAV:             €{PORTFOLIO_NAV:>12,.0f}")
    print(f"  Equity exposure: €{eq_total:>12,.0f}  ({eq_total/PORTFOLIO_NAV*100:.1f}%)")
    print(f"  Crypto exposure: €{cr_total:>12,.0f}  ({cr_total/PORTFOLIO_NAV*100:.1f}%)")
    print(f"  Cash:            €{cash:>12,.0f}  ({cash/PORTFOLIO_NAV*100:.1f}%)")

    total_costs = 0
    if not eq_pos.empty:
        total_costs += eq_pos["est_round_trip_cost_eur"].sum()
    if not cr_pos.empty:
        total_costs += cr_pos["est_round_trip_cost_eur"].sum()

    print(f"\n  Est. rebalance cost: €{total_costs:,.2f} "
          f"({total_costs/PORTFOLIO_NAV*10000:.1f} bps of NAV)")

    # Concentration warnings
    print(f"\n  RISK CHECKS:")
    n_eq = len(eq_pos) if not eq_pos.empty else 0
    n_cr = len(cr_pos) if not cr_pos.empty else 0
    print(f"  ✓ Equity positions: {n_eq} (max {RISK_PARAMS['max_equity_positions']})")
    print(f"  ✓ Crypto positions: {n_cr} (max {RISK_PARAMS['max_crypto_positions']})")

    if not eq_pos.empty and eq_pos["weight_pct"].max() > RISK_PARAMS["max_position_equity_pct"] * 100:
        print(f"  ⚠️ Max equity position exceeds {RISK_PARAMS['max_position_equity_pct']*100}% limit!")
    else:
        print(f"  ✓ Max equity position within limits")

    if cash / PORTFOLIO_NAV < CASH_BUFFER * 0.5:
        print(f"  ⚠️ Cash buffer below {CASH_BUFFER*50:.0f}% minimum!")
    else:
        print(f"  ✓ Cash buffer adequate")


def export_to_csv(
    equity_signals: pd.DataFrame,
    crypto_signals: pd.DataFrame,
    equity_positions: pd.DataFrame,
    crypto_positions: pd.DataFrame,
):
    """Export all signal data to CSV files."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    date_str = TODAY.strftime("%Y%m%d")

    files_written = []

    if not equity_signals.empty:
        path = os.path.join(OUTPUT_DIR, f"equity_signals_{date_str}.csv")
        equity_signals.to_csv(path)
        files_written.append(path)

    if not crypto_signals.empty:
        path = os.path.join(OUTPUT_DIR, f"crypto_signals_{date_str}.csv")
        crypto_signals.to_csv(path, index=False)
        files_written.append(path)

    if not equity_positions.empty:
        path = os.path.join(OUTPUT_DIR, f"equity_positions_{date_str}.csv")
        equity_positions.to_csv(path)
        files_written.append(path)

    if not crypto_positions.empty:
        path = os.path.join(OUTPUT_DIR, f"crypto_positions_{date_str}.csv")
        crypto_positions.to_csv(path)
        files_written.append(path)

    if files_written:
        print(f"\n  📁 CSV files exported to {OUTPUT_DIR}/:")
        for f in files_written:
            print(f"     → {f}")


# ==============================================================================
# SECTION 6: MAIN EXECUTION
# ==============================================================================

def run_equity_module() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Execute equity screening pipeline."""
    print("\n\n" + "█" * 60)
    print("  MODULE 1: EQUITY MULTI-FACTOR SCREENER")
    print("█" * 60)

    # Combine universes
    universe = list(set(EQUITY_WATCHLIST + CUSTOM_WATCHLIST))
    print(f"  Universe: {len(universe)} tickers")

    # Fetch data
    prices = fetch_price_data(universe, label="equity universe")
    if prices.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Compute signals
    print("\n  Computing multi-factor signals...")
    signals = compute_equity_composite(prices)

    # Compute positions
    positions = pd.DataFrame()
    if not signals.empty:
        equity_eur = PORTFOLIO_NAV * EQUITY_ALLOCATION
        positions = compute_position_sizes(signals, prices, "equity", equity_eur)

    # Report
    if CONSOLE_PRINT:
        print_equity_report(signals, positions)

    return signals, positions


def run_crypto_module() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Execute crypto signal pipeline."""
    print("\n\n" + "█" * 60)
    print("  MODULE 2: CRYPTO TREND / MOMENTUM")
    print("█" * 60)

    # Fetch data
    prices = fetch_price_data(CRYPTO_TICKERS, label="crypto universe")
    if prices.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Compute signals
    print("\n  Computing trend/momentum signals...")
    signals = compute_crypto_signals(prices)

    # Compute positions
    positions = pd.DataFrame()
    if not signals.empty:
        crypto_eur = PORTFOLIO_NAV * CRYPTO_ALLOCATION
        positions = compute_position_sizes(signals, prices, "crypto", crypto_eur)

    # Report
    if CONSOLE_PRINT:
        print_crypto_report(signals, positions)

    return signals, positions


def main():
    parser = argparse.ArgumentParser(description="Weekly Signal Engine v1.0")
    parser.add_argument("--equity-only", action="store_true", help="Run equity module only")
    parser.add_argument("--crypto-only", action="store_true", help="Run crypto module only")
    parser.add_argument("--watchlist", type=str, help="Comma-separated custom tickers to add")
    parser.add_argument("--nav", type=float, help="Override portfolio NAV (EUR)")
    args = parser.parse_args()

    # Override config if CLI args provided
    global PORTFOLIO_NAV
    if args.nav:
        PORTFOLIO_NAV = args.nav

    if args.watchlist:
        extra = [t.strip().upper() for t in args.watchlist.split(",")]
        CUSTOM_WATCHLIST.extend(extra)
        print(f"  Added {len(extra)} tickers to watchlist: {', '.join(extra)}")

    print_header()

    eq_signals, eq_positions = pd.DataFrame(), pd.DataFrame()
    cr_signals, cr_positions = pd.DataFrame(), pd.DataFrame()

    if not args.crypto_only:
        eq_signals, eq_positions = run_equity_module()

    if not args.equity_only:
        cr_signals, cr_positions = run_crypto_module()

    # Portfolio summary
    if CONSOLE_PRINT:
        print_portfolio_summary(eq_positions, cr_positions)

    # Export
    if CSV_EXPORT:
        export_to_csv(eq_signals, cr_signals, eq_positions, cr_positions)

    print("\n" + "█" * 60)
    print("  ✅ SIGNAL GENERATION COMPLETE")
    print("  ⚠️  THIS IS NOT INVESTMENT ADVICE. ALL SIGNALS ARE")
    print("     INFORMATIONAL. REVIEW BEFORE ACTING.")
    print("█" * 60 + "\n")


if __name__ == "__main__":
    main()
