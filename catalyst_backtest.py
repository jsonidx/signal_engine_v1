#!/usr/bin/env python3
"""
================================================================================
CATALYST PATTERN STUDY v1.0
================================================================================
Historical analysis of what happens AFTER catalyst setup conditions appear.

THIS IS NOT A TRADITIONAL BACKTEST. It is a pattern study that answers:
  "When short squeeze / breakout conditions existed in the past,
   what happened over the next 1, 2, 3, and 4 weeks?"

WHY IT'S DIFFERENT:
  - The equity multi-factor backtest has 140+ weekly data points → statistical
  - Catalyst events are RARE (5-15 per year per stock) → descriptive only
  - Results show base rates, not predictive edge
  - Sample size is too small for Sharpe ratios to be meaningful

WHAT IT MEASURES:
  For each stock, scan 3 years of history for weeks where:
    - Short squeeze setup conditions were elevated
    - Volume breakout conditions were present
    - Volatility was compressed (Bollinger squeeze)
    - Technical momentum was turning positive
  Then measure forward returns at 5, 10, 15, 20 trading days.

USAGE:
    python3 catalyst_backtest.py                        # Full universe
    python3 catalyst_backtest.py --ticker GME            # Single stock
    python3 catalyst_backtest.py --ticker GME --verbose  # Detailed event log
    python3 catalyst_backtest.py --universe meme         # Meme stocks only

IMPORTANT: This is NOT investment advice. Past catalyst events
           do not predict future squeeze outcomes.
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

warnings.filterwarnings("ignore")

try:
    from config import OUTPUT_DIR
except ImportError:
    OUTPUT_DIR = "./signals_output"

TRADING_DAYS_YEAR = 252
ANNUALIZE = np.sqrt(TRADING_DAYS_YEAR)

# Forward return windows (trading days)
FORWARD_WINDOWS = [5, 10, 15, 20]  # 1, 2, 3, 4 weeks

# Universes
CATALYST_UNIVERSE = [
    # Classic meme / squeeze stocks
    "GME", "AMC", "BB", "KOSS", "BBBY",
    # Current watchlist
    "PATH", "AFRM", "COIN", "BBAI", "NVAX",
    "AI", "MARA", "RIOT", "SMCI", "IONQ", "RGTI",
    # High short interest names
    "CVNA", "UPST", "BYND", "FUBO", "CLOV",
    "SKLZ", "WISH", "SOFI", "PLTR", "RIVN",
    "LCID", "NIO", "LUNR", "RKLB", "ASTS",
    # Large caps with catalyst history
    "TSLA", "NVDA", "AMD", "NFLX",
]

MEME_ONLY = [
    "GME", "AMC", "BB", "KOSS", "BBBY",
    "MARA", "RIOT", "COIN", "SOFI", "PLTR",
    "RIVN", "LCID", "NIO", "IONQ", "RGTI",
]


# ==============================================================================
# SECTION 1: HISTORICAL DATA
# ==============================================================================

def fetch_history(ticker: str, years: int = 3) -> pd.DataFrame:
    """Fetch historical price/volume data."""
    try:
        data = yf.download(ticker, period=f"{years}y", auto_adjust=True, progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        if data.empty or len(data) < 100:
            return pd.DataFrame()
        return data
    except Exception:
        return pd.DataFrame()


def fetch_short_interest_proxy(ticker: str) -> float:
    """
    Get current short interest from yfinance.
    NOTE: Historical short interest is not freely available.
    We use current SI as a proxy — this is a known limitation.
    """
    try:
        info = yf.Ticker(ticker).info
        return info.get("shortPercentOfFloat", 0) or 0
    except Exception:
        return 0


# ==============================================================================
# SECTION 2: CONDITION DETECTION
# ==============================================================================

def detect_conditions_at_date(hist: pd.DataFrame, idx: int) -> dict:
    """
    Detect catalyst setup conditions at a specific date index.
    Returns dict of condition scores (each 0-1 normalized).
    Uses ONLY data available up to that date (no look-ahead).
    """
    if idx < 60:
        return None

    window = hist.iloc[:idx + 1]
    close = window["Close"]
    volume = window["Volume"]

    scores = {}

    # ── Volume Surge ──
    vol_20d = volume.iloc[-20:].mean()
    vol_5d = volume.iloc[-5:].mean()
    vol_ratio = vol_5d / max(vol_20d, 1)
    scores["volume_surge"] = min(vol_ratio / 3.0, 1.0)  # Normalize: 3x = max

    # ── OBV Trend (accumulation) ──
    obv = (np.sign(close.diff()) * volume).cumsum()
    if len(obv) >= 20:
        obv_vals = obv.iloc[-20:].values
        x = np.arange(len(obv_vals))
        slope = np.polyfit(x, obv_vals, 1)[0]
        obv_rising = slope > 0
        price_flat_or_down = close.iloc[-1] <= close.iloc[-20]
        # Hidden accumulation: OBV up, price flat/down
        scores["hidden_accumulation"] = 1.0 if (obv_rising and price_flat_or_down) else (0.5 if obv_rising else 0.0)
    else:
        scores["hidden_accumulation"] = 0.0

    # ── Bollinger Squeeze ──
    if len(close) >= 60:
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_width = ((sma20 + 2 * std20) - (sma20 - 2 * std20)) / sma20
        bb_width = bb_width.dropna()
        if len(bb_width) >= 50:
            current_width = bb_width.iloc[-1]
            percentile = (bb_width < current_width).mean()
            # Lower percentile = more compressed = higher score
            scores["vol_compression"] = max(1.0 - percentile, 0)
        else:
            scores["vol_compression"] = 0.0
    else:
        scores["vol_compression"] = 0.0

    # ── RSI Sweet Spot (40-60 = room to run) ──
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=13, min_periods=14).mean()
    avg_loss = loss.ewm(com=13, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = (100 - (100 / (1 + rs))).iloc[-1]

    if 40 <= rsi <= 60:
        scores["rsi_sweet_spot"] = 1.0
    elif 30 <= rsi < 40 or 60 < rsi <= 70:
        scores["rsi_sweet_spot"] = 0.5
    else:
        scores["rsi_sweet_spot"] = 0.0

    # ── Price Momentum (5-day) ──
    price_5d = close.iloc[-1] / close.iloc[-5] - 1
    if price_5d > 0.10:
        scores["short_term_momentum"] = 1.0
    elif price_5d > 0.05:
        scores["short_term_momentum"] = 0.7
    elif price_5d > 0:
        scores["short_term_momentum"] = 0.3
    else:
        scores["short_term_momentum"] = 0.0

    # ── EMA Alignment ──
    if len(close) >= 50:
        ema9 = close.ewm(span=9, adjust=False).mean().iloc[-1]
        ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
        ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
        current = close.iloc[-1]

        if current > ema9 > ema21 > ema50:
            scores["ema_alignment"] = 1.0
        elif current > ema21:
            scores["ema_alignment"] = 0.5
        else:
            scores["ema_alignment"] = 0.0
    else:
        scores["ema_alignment"] = 0.0

    # ── Composite ──
    weights = {
        "volume_surge": 0.25,
        "hidden_accumulation": 0.15,
        "vol_compression": 0.20,
        "rsi_sweet_spot": 0.10,
        "short_term_momentum": 0.15,
        "ema_alignment": 0.15,
    }
    composite = sum(scores[k] * weights[k] for k in weights)
    scores["composite"] = composite

    return scores


# ==============================================================================
# SECTION 3: FORWARD RETURN MEASUREMENT
# ==============================================================================

def measure_forward_returns(hist: pd.DataFrame, signal_idx: int) -> dict:
    """
    Measure returns at 5, 10, 15, 20 trading days after signal.
    Also measures max gain and max drawdown in the 20-day window.
    """
    close = hist["Close"]
    entry_price = close.iloc[signal_idx]
    results = {"entry_price": entry_price, "entry_date": hist.index[signal_idx]}

    for days in FORWARD_WINDOWS:
        target_idx = signal_idx + days
        if target_idx < len(close):
            exit_price = close.iloc[target_idx]
            results[f"return_{days}d"] = exit_price / entry_price - 1
        else:
            results[f"return_{days}d"] = np.nan

    # Max gain and max drawdown in 20-day window
    end_idx = min(signal_idx + 20, len(close))
    forward_prices = close.iloc[signal_idx:end_idx]
    if len(forward_prices) > 1:
        returns = forward_prices / entry_price - 1
        results["max_gain_20d"] = returns.max()
        results["max_drawdown_20d"] = returns.min()
    else:
        results["max_gain_20d"] = np.nan
        results["max_drawdown_20d"] = np.nan

    return results


# ==============================================================================
# SECTION 4: PATTERN STUDY ENGINE
# ==============================================================================

def run_pattern_study(
    ticker: str,
    years: int = 3,
    threshold: float = 0.45,
    verbose: bool = False,
) -> dict:
    """
    Run full pattern study for a single ticker.

    Scans every week in the history for setup conditions,
    triggers when composite > threshold, and measures
    forward returns.

    Returns dict with events list and aggregate statistics.
    """
    hist = fetch_history(ticker, years)
    if hist.empty:
        return None

    short_pct = fetch_short_interest_proxy(ticker)

    events = []
    scan_interval = 5  # Check every 5 trading days (weekly)

    for idx in range(60, len(hist) - 20, scan_interval):
        conditions = detect_conditions_at_date(hist, idx)
        if conditions is None:
            continue

        composite = conditions["composite"]

        if composite >= threshold:
            # Measure what happened next
            forward = measure_forward_returns(hist, idx)

            event = {
                "date": hist.index[idx],
                "price": float(hist["Close"].iloc[idx]),
                "composite": composite,
                **{k: v for k, v in conditions.items() if k != "composite"},
                **forward,
            }
            events.append(event)

    if not events:
        return {
            "ticker": ticker,
            "short_pct": short_pct,
            "n_events": 0,
            "events": [],
            "stats": None,
        }

    # Aggregate statistics
    df = pd.DataFrame(events)

    stats = {
        "n_events": len(events),
        "avg_composite": df["composite"].mean(),
    }

    for days in FORWARD_WINDOWS:
        col = f"return_{days}d"
        if col in df.columns:
            valid = df[col].dropna()
            if len(valid) > 0:
                stats[f"avg_return_{days}d"] = valid.mean()
                stats[f"median_return_{days}d"] = valid.median()
                stats[f"hit_rate_{days}d"] = (valid > 0).mean()
                stats[f"avg_win_{days}d"] = valid[valid > 0].mean() if (valid > 0).any() else 0
                stats[f"avg_loss_{days}d"] = valid[valid < 0].mean() if (valid < 0).any() else 0
                stats[f"best_{days}d"] = valid.max()
                stats[f"worst_{days}d"] = valid.min()

    if "max_gain_20d" in df.columns:
        stats["avg_max_gain_20d"] = df["max_gain_20d"].dropna().mean()
        stats["avg_max_dd_20d"] = df["max_drawdown_20d"].dropna().mean()

    return {
        "ticker": ticker,
        "short_pct": short_pct,
        "n_events": len(events),
        "events": events,
        "stats": stats,
    }


# ==============================================================================
# SECTION 5: REPORTING
# ==============================================================================

def print_single_study(result: dict, verbose: bool = False):
    """Print detailed pattern study for a single stock."""
    ticker = result["ticker"]
    stats = result["stats"]
    events = result["events"]

    print(f"\n{'═' * 60}")
    print(f"  CATALYST PATTERN STUDY: {ticker}")
    print(f"  Current Short Interest: {result['short_pct']:.0%}")
    print(f"  Signal Events Found: {result['n_events']}")
    print(f"{'═' * 60}")

    if not stats:
        print(f"  No catalyst setup conditions detected in history.")
        return

    # Forward return table
    print(f"\n  FORWARD RETURNS AFTER SETUP CONDITIONS:")
    print(f"  {'Window':<12}{'Avg':>10}{'Median':>10}{'Hit Rate':>10}{'Avg Win':>10}{'Avg Loss':>10}{'Best':>10}{'Worst':>10}")
    print(f"  {'─' * 82}")

    for days in FORWARD_WINDOWS:
        prefix = f"{days}d"
        avg = stats.get(f"avg_return_{prefix}", 0)
        med = stats.get(f"median_return_{prefix}", 0)
        hr = stats.get(f"hit_rate_{prefix}", 0)
        aw = stats.get(f"avg_win_{prefix}", 0)
        al = stats.get(f"avg_loss_{prefix}", 0)
        best = stats.get(f"best_{prefix}", 0)
        worst = stats.get(f"worst_{prefix}", 0)

        print(f"  {days:>2} days    "
              f"{avg:>9.1%}"
              f"{med:>9.1%}"
              f"{hr:>9.0%}"
              f"{aw:>9.1%}"
              f"{al:>9.1%}"
              f"{best:>9.1%}"
              f"{worst:>9.1%}")

    # Max gain / drawdown
    mg = stats.get("avg_max_gain_20d", 0)
    md = stats.get("avg_max_dd_20d", 0)
    print(f"\n  20-day window: avg max gain {mg:.1%} | avg max drawdown {md:.1%}")

    # Assessment
    hr_10 = stats.get("hit_rate_10d", 0)
    avg_10 = stats.get("avg_return_10d", 0)
    n = stats["n_events"]

    print(f"\n  ASSESSMENT:")
    if n < 5:
        print(f"  ⚠️  Only {n} events — sample too small for conclusions.")
    elif hr_10 > 0.60 and avg_10 > 0.03:
        print(f"  🟢 Pattern shows positive edge: {hr_10:.0%} hit rate, {avg_10:.1%} avg 10d return.")
        print(f"     Setup conditions preceded positive moves more often than not.")
    elif hr_10 > 0.50:
        print(f"  🟡 Marginal pattern: {hr_10:.0%} hit rate, {avg_10:.1%} avg 10d return.")
        print(f"     Slightly better than coin flip. Needs a catalyst to be actionable.")
    else:
        print(f"  🔴 No reliable pattern: {hr_10:.0%} hit rate, {avg_10:.1%} avg 10d return.")
        print(f"     Setup conditions alone don't predict direction. Need additional catalyst.")

    # Verbose: show each event
    if verbose and events:
        print(f"\n  EVENT LOG ({len(events)} events):")
        print(f"  {'Date':>12}{'Price':>10}{'Score':>8}{'5d Ret':>9}{'10d Ret':>9}{'20d Ret':>9}{'Max Gain':>10}{'Max DD':>10}")
        print(f"  {'─' * 78}")

        for e in events:
            date_str = e["date"].strftime("%Y-%m-%d") if hasattr(e["date"], "strftime") else str(e["date"])[:10]
            r5 = e.get("return_5d", np.nan)
            r10 = e.get("return_10d", np.nan)
            r20 = e.get("return_20d", np.nan)
            mg = e.get("max_gain_20d", np.nan)
            md = e.get("max_drawdown_20d", np.nan)

            r5_s = f"{r5:.1%}" if not np.isnan(r5) else "N/A"
            r10_s = f"{r10:.1%}" if not np.isnan(r10) else "N/A"
            r20_s = f"{r20:.1%}" if not np.isnan(r20) else "N/A"
            mg_s = f"{mg:.1%}" if not np.isnan(mg) else "N/A"
            md_s = f"{md:.1%}" if not np.isnan(md) else "N/A"

            # Flag big moves
            flag = ""
            if not np.isnan(mg) and mg > 0.20:
                flag = " 🔥"
            elif not np.isnan(md) and md < -0.20:
                flag = " 💀"

            print(f"  {date_str:>12}"
                  f"  ${e['price']:>8.2f}"
                  f"{e['composite']:>7.2f}"
                  f"{r5_s:>9}"
                  f"{r10_s:>9}"
                  f"{r20_s:>9}"
                  f"{mg_s:>10}"
                  f"{md_s:>10}{flag}")


def print_universe_summary(results: list):
    """Print summary across all stocks."""
    valid = [r for r in results if r and r["stats"]]

    if not valid:
        print("\n  No pattern data available.")
        return

    print(f"\n{'█' * 60}")
    print(f"  CATALYST PATTERN STUDY — UNIVERSE SUMMARY")
    print(f"  {len(valid)} stocks analyzed | {sum(r['n_events'] for r in valid)} total events")
    print(f"{'█' * 60}")

    # Sort by 10d hit rate
    rows = []
    for r in valid:
        s = r["stats"]
        rows.append({
            "ticker": r["ticker"],
            "short_pct": r["short_pct"],
            "n_events": s["n_events"],
            "hit_5d": s.get("hit_rate_5d", 0),
            "hit_10d": s.get("hit_rate_10d", 0),
            "hit_20d": s.get("hit_rate_20d", 0),
            "avg_10d": s.get("avg_return_10d", 0),
            "avg_max_gain": s.get("avg_max_gain_20d", 0),
            "avg_max_dd": s.get("avg_max_dd_20d", 0),
        })

    df = pd.DataFrame(rows).sort_values("hit_10d", ascending=False)

    print(f"\n  {'Ticker':<8}{'Short%':>8}{'Events':>8}{'Hit 5d':>9}{'Hit 10d':>9}{'Hit 20d':>9}"
          f"{'Avg 10d':>10}{'AvgMaxGn':>10}{'AvgMaxDD':>10}")
    print(f"  {'─' * 81}")

    for _, row in df.iterrows():
        # Tier indicator
        if row["hit_10d"] > 0.60 and row["avg_10d"] > 0.03:
            tier = "🟢"
        elif row["hit_10d"] > 0.50:
            tier = "🟡"
        else:
            tier = "🔴"

        n_str = str(int(row["n_events"]))
        if row["n_events"] < 5:
            n_str += "*"  # Asterisk = low sample

        print(f"  {row['ticker']:<8}"
              f"{row['short_pct']:>7.0%}"
              f"{n_str:>8}"
              f"{row['hit_5d']:>8.0%}"
              f"{row['hit_10d']:>8.0%}"
              f"{row['hit_20d']:>8.0%}"
              f"{row['avg_10d']:>9.1%}"
              f"{row['avg_max_gain']:>9.1%}"
              f"{row['avg_max_dd']:>9.1%}"
              f"  {tier}")

    print(f"\n  * = fewer than 5 events (insufficient for conclusions)")

    # Cross-stock statistics
    all_events = []
    for r in valid:
        all_events.extend(r["events"])

    if all_events:
        all_df = pd.DataFrame(all_events)
        print(f"\n  AGGREGATE (ALL {len(all_events)} EVENTS ACROSS ALL STOCKS):")
        for days in FORWARD_WINDOWS:
            col = f"return_{days}d"
            if col in all_df.columns:
                v = all_df[col].dropna()
                if len(v) > 0:
                    print(f"    {days:>2}d: avg {v.mean():.1%} | median {v.median():.1%} | "
                          f"hit rate {(v>0).mean():.0%} | "
                          f"best {v.max():.1%} | worst {v.min():.1%}")

    # Key insight
    print(f"\n  KEY INSIGHT:")
    print(f"  Setup conditions identify ELEVATED PROBABILITY of a move,")
    print(f"  not the DIRECTION. The avg max gain and avg max drawdown")
    print(f"  show these stocks move a LOT in both directions after setup.")
    print(f"  Your edge comes from combining this with a CATALYST trigger")
    print(f"  (social momentum, earnings, news) — not from the setup alone.")


# ==============================================================================
# SECTION 6: MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Catalyst Pattern Study v1.0")
    parser.add_argument("--ticker", type=str, help="Single stock deep dive")
    parser.add_argument("--universe", choices=["full", "meme"], default="full")
    parser.add_argument("--years", type=int, default=3, help="Years of history")
    parser.add_argument("--threshold", type=float, default=0.40,
                        help="Composite threshold to trigger (default: 0.40)")
    parser.add_argument("--verbose", action="store_true", help="Show individual events")
    args = parser.parse_args()

    print(f"\n{'█' * 60}")
    print(f"  CATALYST PATTERN STUDY v1.0")
    print(f"  Lookback: {args.years} years | Threshold: {args.threshold:.0%}")
    print(f"{'█' * 60}")

    if args.ticker:
        # Single stock
        ticker = args.ticker.upper()
        print(f"\n  Analyzing: {ticker}...")
        result = run_pattern_study(ticker, args.years, args.threshold, args.verbose)
        if result:
            print_single_study(result, verbose=args.verbose)
        else:
            print(f"  [ERROR] No data for {ticker}")
    else:
        # Universe scan
        universe = MEME_ONLY if args.universe == "meme" else CATALYST_UNIVERSE
        print(f"\n  Universe: {len(universe)} stocks")

        results = []
        for i, ticker in enumerate(universe):
            print(f"\r  Analyzing: {ticker:<8} ({i+1}/{len(universe)})", end="", flush=True)
            result = run_pattern_study(ticker, args.years, args.threshold)
            if result:
                results.append(result)

        print(f"\r  Analysis complete: {len(results)} stocks" + " " * 30)

        # Print individual studies for top stocks
        top_results = sorted(
            [r for r in results if r["stats"]],
            key=lambda r: r["stats"].get("hit_rate_10d", 0),
            reverse=True
        )[:5]

        for r in top_results:
            print_single_study(r, verbose=args.verbose)

        # Universe summary
        print_universe_summary(results)

        # Export
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        export_rows = []
        for r in results:
            if r["stats"]:
                export_rows.append({
                    "ticker": r["ticker"],
                    "short_pct": r["short_pct"],
                    **r["stats"]
                })
        if export_rows:
            path = os.path.join(OUTPUT_DIR, f"catalyst_patterns_{datetime.now().strftime('%Y%m%d')}.csv")
            pd.DataFrame(export_rows).to_csv(path, index=False)
            print(f"\n  📁 Exported to: {path}")

    print(f"\n{'█' * 60}")
    print(f"  ⚠️  PATTERN STUDY — NOT A BACKTEST.")
    print(f"  Past catalyst events do not predict future outcomes.")
    print(f"  Setup conditions show VOLATILITY, not DIRECTION.")
    print(f"  THIS IS NOT INVESTMENT ADVICE.")
    print(f"{'█' * 60}\n")


if __name__ == "__main__":
    main()
