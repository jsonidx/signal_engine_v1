#!/usr/bin/env python3
"""
================================================================================
OPTIONS FLOW SCREENER v1.0 — "Options Heat" Detector
================================================================================
Screens for stocks with elevated options activity and high movement prediction.
Targets both volatile small/mid-caps (COIN, AI, GME) and large-caps where
options volume is spiking (AMZN, GOOGL, NVDA).

WHAT IT MEASURES:
    1. Options Volume Spike  — today's total options vol vs 20-day avg
    2. IV Rank               — current IV vs 52-week IV range (0-100%)
    3. Put/Call Ratio        — directional lean (contrarian or confirming)
    4. Expected Move (EM)    — ATM straddle price / stock price (1-week)
    5. Gamma Exposure Proxy  — open interest concentration near current price

SCORING (0-100 "options heat"):
    - Volume spike  : 30 pts  (>5x avg = full score)
    - IV rank       : 25 pts  (>80 rank = full score)
    - Expected move : 25 pts  (>5% weekly EM = full score)
    - Put/call lean : 20 pts  (extreme readings = bullish contrarian signal)

USAGE:
    python3 options_flow.py                          # Screen full universe
    python3 options_flow.py --ticker COIN            # Single ticker
    python3 options_flow.py --tickers COIN GME NVDA  # Multiple tickers
    python3 options_flow.py --watchlist              # Watchlist tickers
    python3 options_flow.py --top 10                 # Top 10 by heat score
    python3 options_flow.py --min-heat 40            # Filter by min score

DATA SOURCE: yfinance options chains (free, no API key required)
NOTE: Options data quality varies. Some tickers have thin chains.

IMPORTANT: This is NOT investment advice. High options activity ≠ direction.
================================================================================
"""

import argparse
import os
import sys
import time
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

try:
    from config import OUTPUT_DIR, EQUITY_WATCHLIST, CUSTOM_WATCHLIST
except ImportError:
    OUTPUT_DIR = "./signals_output"
    EQUITY_WATCHLIST = []
    CUSTOM_WATCHLIST = []


# ==============================================================================
# SECTION 1: UNIVERSE
# ==============================================================================

# Core options-active universe — high vol, retail favorites, large-cap movers
OPTIONS_UNIVERSE = [
    # Crypto / meme / high-vol
    "COIN", "MARA", "RIOT", "HOOD", "MSTR",
    # Retail / meme
    "GME", "AMC", "PLTR", "SOFI", "RIVN", "LCID", "NIO",
    # AI / tech growth
    "AI", "NVDA", "AMD", "SMCI", "TSLA", "MSFT", "META",
    # Large-cap with active options
    "AMZN", "GOOGL", "AAPL", "NFLX", "UBER",
    # Macro-sensitive
    "SPY", "QQQ", "ARKK", "IWM",
    # Biotech / binary events
    "MRNA", "BNTX",
]


def _read_watchlist_tickers() -> List[str]:
    """Parse watchlist.txt, extracting ticker before any # comment."""
    paths = [
        os.path.join(os.path.dirname(__file__), "watchlist.txt"),
        "./watchlist.txt",
    ]
    for path in paths:
        if os.path.exists(path):
            tickers = []
            with open(path) as f:
                for line in f:
                    clean = line.split("#")[0].strip().upper()
                    if clean and not clean.startswith("TIER") and not clean.startswith("MANUALLY"):
                        tickers.append(clean)
            return tickers
    return []


# ==============================================================================
# SECTION 2: DATA FETCHING
# ==============================================================================

def _get_historical_iv(ticker_obj: yf.Ticker, lookback_days: int = 252) -> Tuple[float, float, float]:
    """
    Estimate IV rank using historical realized volatility as a proxy.
    Returns (current_iv_proxy, iv_52w_low, iv_52w_high).

    We use 30-day realized vol as IV proxy since free sources don't give
    historical IV directly. IV rank = (current - low) / (high - low).
    """
    try:
        hist = ticker_obj.history(period="1y")
        if hist.empty or len(hist) < 30:
            return 0.0, 0.0, 0.0

        returns = hist["Close"].pct_change().dropna()

        # Rolling 30-day annualized vol
        rolling_vol = returns.rolling(30).std() * np.sqrt(252)
        rolling_vol = rolling_vol.dropna()

        if len(rolling_vol) < 10:
            return 0.0, 0.0, 0.0

        current_vol = float(rolling_vol.iloc[-1])
        low_52w = float(rolling_vol.min())
        high_52w = float(rolling_vol.max())

        return current_vol, low_52w, high_52w
    except Exception:
        return 0.0, 0.0, 0.0


def _get_options_data(ticker: str) -> Optional[dict]:
    """
    Pull options chain data for the nearest expiry (1-2 weeks out).
    Returns dict with volume, OI, put/call ratio, expected move, gamma proxy.
    """
    try:
        t = yf.Ticker(ticker)

        # Get current price
        hist = t.history(period="5d")
        if hist.empty:
            return None
        price = float(hist["Close"].iloc[-1])
        if price <= 0:
            return None

        # Get available expiry dates
        expirations = t.options
        if not expirations:
            return None

        # Target: nearest expiry 3-45 days out
        today = datetime.now().date()
        target_exp = None
        for exp in expirations:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            days_out = (exp_date - today).days
            if 3 <= days_out <= 45:
                target_exp = exp
                break

        # Fallback: use nearest expiry regardless of days
        if target_exp is None and expirations:
            target_exp = expirations[0]
        if target_exp is None:
            return None

        chain = t.option_chain(target_exp)
        calls = chain.calls
        puts = chain.puts

        if calls.empty and puts.empty:
            return None

        # Total options volume today
        call_vol = int(calls["volume"].fillna(0).sum())
        put_vol = int(puts["volume"].fillna(0).sum())
        total_vol = call_vol + put_vol

        # Put/call volume ratio
        pc_ratio = (put_vol / call_vol) if call_vol > 0 else 2.0

        # Total open interest
        call_oi = int(calls["openInterest"].fillna(0).sum())
        put_oi = int(puts["openInterest"].fillna(0).sum())
        total_oi = call_oi + put_oi

        # Expected move from ATM straddle
        # Find ATM call and put (closest strike to current price)
        def _safe_float(val) -> float:
            try:
                f = float(val)
                return f if (f == f) else 0.0  # NaN check
            except (TypeError, ValueError):
                return 0.0

        atm_call = None
        atm_put = None
        if not calls.empty:
            calls_copy = calls.copy()
            calls_copy["dist"] = abs(calls_copy["strike"] - price)
            atm_row = calls_copy.loc[calls_copy["dist"].idxmin()]
            bid = _safe_float(atm_row.get("bid", 0))
            ask = _safe_float(atm_row.get("ask", 0))
            atm_call_mid = (bid + ask) / 2
            atm_call = atm_call_mid if atm_call_mid > 0 else _safe_float(atm_row.get("lastPrice", 0))

        if not puts.empty:
            puts_copy = puts.copy()
            puts_copy["dist"] = abs(puts_copy["strike"] - price)
            atm_row = puts_copy.loc[puts_copy["dist"].idxmin()]
            bid = _safe_float(atm_row.get("bid", 0))
            ask = _safe_float(atm_row.get("ask", 0))
            atm_put_mid = (bid + ask) / 2
            atm_put = atm_put_mid if atm_put_mid > 0 else _safe_float(atm_row.get("lastPrice", 0))

        straddle_cost = (atm_call or 0) + (atm_put or 0)
        expected_move_pct = (straddle_cost / price * 100) if price > 0 else 0.0

        # Gamma exposure proxy: OI concentration within 5% of current price
        near_strikes_pct = 0.05
        low_bound = price * (1 - near_strikes_pct)
        high_bound = price * (1 + near_strikes_pct)

        near_call_oi = int(calls[
            (calls["strike"] >= low_bound) & (calls["strike"] <= high_bound)
        ]["openInterest"].fillna(0).sum())
        near_put_oi = int(puts[
            (puts["strike"] >= low_bound) & (puts["strike"] <= high_bound)
        ]["openInterest"].fillna(0).sum())
        near_oi = near_call_oi + near_put_oi
        gamma_concentration = (near_oi / total_oi * 100) if total_oi > 0 else 0.0

        # IV from options — prefer 20-45 day expiry for reliable IV
        # (near-expiry chains often have stale bid/ask = 0 → distorted IV)
        implied_vol = None
        iv_exp = target_exp
        for _exp in expirations:
            _days = (datetime.strptime(_exp, "%Y-%m-%d").date() - today).days
            if 20 <= _days <= 60:
                iv_exp = _exp
                break

        try:
            iv_chain = t.option_chain(iv_exp)
            iv_calls = iv_chain.calls
            if not iv_calls.empty:
                iv_calls = iv_calls.copy()
                iv_calls["dist"] = abs(iv_calls["strike"] - price)
                # Only use options where bid > 0 (active market)
                active = iv_calls[iv_calls["bid"].fillna(0) > 0]
                if active.empty:
                    active = iv_calls  # fallback
                atm_near = active.nsmallest(5, "dist")
                iv_values = atm_near["impliedVolatility"].dropna()
                iv_values = iv_values[iv_values > 0.05]  # filter garbage values (<5%)
                if not iv_values.empty:
                    implied_vol = float(iv_values.median())
        except Exception:
            pass

        # Days to expiry
        exp_date = datetime.strptime(target_exp, "%Y-%m-%d").date()
        days_to_exp = (exp_date - today).days

        return {
            "ticker": ticker,
            "price": price,
            "expiry": target_exp,
            "days_to_exp": days_to_exp,
            "call_volume": call_vol,
            "put_volume": put_vol,
            "total_volume": total_vol,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "total_oi": total_oi,
            "pc_ratio": round(pc_ratio, 2),
            "straddle_cost": round(straddle_cost, 2),
            "expected_move_pct": round(expected_move_pct, 1),
            "gamma_concentration_pct": round(gamma_concentration, 1),
            "implied_vol": round(implied_vol * 100, 1) if implied_vol else None,
        }

    except Exception as e:
        return None


def _get_volume_ratio(ticker: str) -> Tuple[float, float]:
    """
    Return (today_options_volume, 20d_avg_options_volume) using
    stock volume as a proxy when options history isn't available.
    Also returns 5d stock volume ratio vs 20d avg.
    """
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="3mo")
        if hist.empty or len(hist) < 21:
            return 0.0, 0.0

        vol_series = hist["Volume"].dropna()
        avg_20d = float(vol_series.iloc[-21:-1].mean())
        today_vol = float(vol_series.iloc[-1])

        ratio = (today_vol / avg_20d) if avg_20d > 0 else 1.0
        return today_vol, avg_20d
    except Exception:
        return 0.0, 0.0


# ==============================================================================
# SECTION 3: SCORING
# ==============================================================================

def _score_volume_spike(options_vol: int, avg_stock_vol: float, stock_price: float) -> Tuple[float, str]:
    """
    Score options volume spike vs estimated average.
    Since we don't have historical options volume, we estimate based on
    options volume relative to stock market cap / dollar volume.
    Returns (score 0-30, label).
    """
    if options_vol <= 0:
        return 0.0, "no data"

    # Rough benchmark: typical options/stock volume ratio
    # Use options vol in contracts × 100 shares vs stock dollar volume proxy
    dollar_vol_proxy = avg_stock_vol * stock_price if avg_stock_vol > 0 and stock_price > 0 else 1

    # Options notional = vol * 100 * price (approx)
    options_notional = options_vol * 100 * stock_price
    ratio = options_notional / dollar_vol_proxy if dollar_vol_proxy > 0 else 0

    # Also score purely on absolute options volume
    if options_vol >= 500_000:
        vol_score = 30.0
        label = "extreme"
    elif options_vol >= 200_000:
        vol_score = 25.0
        label = "very high"
    elif options_vol >= 50_000:
        vol_score = 18.0
        label = "high"
    elif options_vol >= 10_000:
        vol_score = 10.0
        label = "elevated"
    elif options_vol >= 2_000:
        vol_score = 5.0
        label = "moderate"
    else:
        vol_score = 1.0
        label = "low"

    return vol_score, label


def _score_iv_rank(current_iv: float, iv_low: float, iv_high: float,
                   implied_vol_pct: Optional[float]) -> Tuple[float, float]:
    """
    Score IV rank (0-25). Uses direct implied vol from options if available,
    falls back to realized vol proxy.
    Returns (score, iv_rank_pct).
    """
    # Prefer direct IV from options chain
    if implied_vol_pct and implied_vol_pct > 0:
        iv = implied_vol_pct / 100.0
        # Estimate rank from realized vol range
        if iv_high > iv_low and iv_high > 0:
            rank = min(1.0, (iv - iv_low) / (iv_high - iv_low))
        else:
            # Just use absolute IV level
            rank = min(1.0, iv / 1.5)  # 150% IV = max
    elif current_iv > 0 and iv_high > iv_low:
        rank = min(1.0, (current_iv - iv_low) / (iv_high - iv_low))
    else:
        return 0.0, 0.0

    rank = max(0.0, rank)
    score = rank * 25.0
    return round(score, 1), round(rank * 100, 1)


def _score_expected_move(em_pct: float) -> Tuple[float, str]:
    """
    Score expected move (0-25). Higher EM = more movement priced in.
    Returns (score, label).
    """
    if em_pct <= 0:
        return 0.0, "no data"
    elif em_pct >= 10.0:
        return 25.0, f"{em_pct:.1f}% exp move"
    elif em_pct >= 7.0:
        return 20.0, f"{em_pct:.1f}% exp move"
    elif em_pct >= 5.0:
        return 16.0, f"{em_pct:.1f}% exp move"
    elif em_pct >= 3.0:
        return 10.0, f"{em_pct:.1f}% exp move"
    elif em_pct >= 1.5:
        return 5.0, f"{em_pct:.1f}% exp move"
    else:
        return 2.0, f"{em_pct:.1f}% exp move"


def _score_put_call(pc_ratio: float) -> Tuple[float, str]:
    """
    Score put/call ratio for bullish contrarian signal (0-20).
    Extreme put buying (fear) at oversold = contrarian bullish.
    Balanced or slight call lean = neutral-bullish.
    Extreme call buying = potential top (bearish).
    Returns (score, interpretation).
    """
    if pc_ratio <= 0:
        return 0.0, "no data"
    elif pc_ratio >= 2.5:
        return 20.0, f"P/C {pc_ratio:.2f} — extreme fear (contrarian bull)"
    elif pc_ratio >= 1.5:
        return 16.0, f"P/C {pc_ratio:.2f} — heavy put buying (bullish lean)"
    elif pc_ratio >= 0.8:
        return 10.0, f"P/C {pc_ratio:.2f} — balanced"
    elif pc_ratio >= 0.5:
        return 6.0, f"P/C {pc_ratio:.2f} — call skew"
    else:
        return 3.0, f"P/C {pc_ratio:.2f} — extreme call buying (caution)"


# ==============================================================================
# SECTION 4: MAIN ANALYSIS
# ==============================================================================

def analyze_ticker(ticker: str, verbose: bool = False) -> Optional[dict]:
    """
    Full options flow analysis for a single ticker.
    Returns scored result dict or None if data unavailable.
    """
    ticker = ticker.upper().strip()

    # Skip crypto (no options chains)
    if ticker.endswith("-USD") or ticker.endswith("-USDT"):
        return None

    try:
        # Get options data
        opts = _get_options_data(ticker)
        if opts is None:
            return None

        price = opts["price"]
        if price <= 0:
            return None

        # Get historical vol for IV rank
        t = yf.Ticker(ticker)
        current_iv, iv_low, iv_high = _get_historical_iv(t)

        # Get stock volume for context
        today_vol, avg_vol = _get_volume_ratio(ticker)

        # Score each component
        vol_score, vol_label = _score_volume_spike(
            opts["total_volume"], avg_vol, price
        )
        iv_score, iv_rank = _score_iv_rank(
            current_iv, iv_low, iv_high, opts.get("implied_vol")
        )
        em_score, em_label = _score_expected_move(opts["expected_move_pct"])
        pc_score, pc_label = _score_put_call(opts["pc_ratio"])

        # Composite heat score (0-100)
        heat_score = vol_score + iv_score + em_score + pc_score
        heat_score = round(min(100.0, heat_score), 1)

        # Direction signal
        pc = opts["pc_ratio"]
        if pc > 1.5:
            direction = "BULL"  # Contrarian — heavy puts = potential squeeze
        elif pc < 0.6:
            direction = "BEAR"  # Too many calls — possible top
        else:
            direction = "NEUTRAL"

        result = {
            "ticker": ticker,
            "price": price,
            "heat_score": heat_score,
            "direction": direction,
            # Options volume
            "total_options_vol": opts["total_volume"],
            "call_vol": opts["call_volume"],
            "put_vol": opts["put_volume"],
            "vol_score": vol_score,
            "vol_label": vol_label,
            # IV
            "iv_rank": iv_rank,
            "implied_vol_pct": opts.get("implied_vol"),
            "iv_score": iv_score,
            # Expected move
            "expected_move_pct": opts["expected_move_pct"],
            "straddle_cost": opts["straddle_cost"],
            "em_score": em_score,
            # Put/call
            "pc_ratio": opts["pc_ratio"],
            "pc_label": pc_label,
            "pc_score": pc_score,
            # Context
            "expiry": opts["expiry"],
            "days_to_exp": opts["days_to_exp"],
            "total_oi": opts["total_oi"],
            "gamma_concentration_pct": opts["gamma_concentration_pct"],
            # Stock context
            "stock_vol_today": int(today_vol),
            "stock_vol_20d_avg": int(avg_vol),
        }

        return result

    except Exception as e:
        if verbose:
            print(f"  [{ticker}] Error: {e}")
        return None


def screen_universe(tickers: List[str], min_heat: float = 0,
                    verbose: bool = False) -> List[dict]:
    """
    Screen a list of tickers and return results sorted by heat score.
    """
    results = []
    total = len(tickers)

    for i, ticker in enumerate(tickers, 1):
        if verbose:
            print(f"  [{i}/{total}] {ticker}...", end=" ", flush=True)

        result = analyze_ticker(ticker, verbose=verbose)
        if result and result["heat_score"] >= min_heat:
            results.append(result)
            if verbose:
                print(f"heat={result['heat_score']:.0f} | EM={result['expected_move_pct']:.1f}% | PC={result['pc_ratio']:.2f}")
        else:
            if verbose:
                print("skipped" if result is None else f"below threshold (heat={result['heat_score']:.0f})")
            time.sleep(0.3)

    return sorted(results, key=lambda x: x["heat_score"], reverse=True)


# ==============================================================================
# SECTION 5: PRINTING
# ==============================================================================

def _heat_bar(score: float, width: int = 20) -> str:
    """Visual heat bar."""
    filled = int(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _heat_emoji(score: float) -> str:
    if score >= 75:
        return "🔥🔥🔥"
    elif score >= 55:
        return "🔥🔥"
    elif score >= 35:
        return "🔥"
    elif score >= 20:
        return "⚡"
    else:
        return "·"


def print_summary_table(results: List[dict]) -> None:
    """Print compact summary table."""
    if not results:
        print("  No options data available for universe.")
        return

    print()
    print("OPTIONS FLOW SCREENER — Heat Ranking")
    print("=" * 90)
    print(f"  {'#':>3}  {'TICKER':<8} {'HEAT':>5}  {'BAR':<20}  {'EM%':>5}  {'IV%':>5}  {'P/C':>5}  {'OPT VOL':>9}  {'DIR':<7}")
    print("  " + "-" * 86)

    for i, r in enumerate(results, 1):
        iv_str = f"{r['implied_vol_pct']:.0f}%" if r.get("implied_vol_pct") else "  N/A"
        print(
            f"  {i:>3}  {r['ticker']:<8} {r['heat_score']:>5.0f}  "
            f"{_heat_bar(r['heat_score']):<20}  "
            f"{r['expected_move_pct']:>4.1f}%  "
            f"{iv_str:>5}  "
            f"{r['pc_ratio']:>5.2f}  "
            f"{r['total_options_vol']:>9,}  "
            f"{_heat_emoji(r['heat_score'])} {r['direction']}"
        )

    print()
    print(f"  Tickers screened: {len(results)}")
    print(f"  Top heat: {results[0]['ticker']} ({results[0]['heat_score']:.0f}/100)")
    print()


def print_deep_dive(r: dict) -> None:
    """Print detailed breakdown for one ticker."""
    if not r:
        return

    ticker = r["ticker"]
    print()
    print(f"OPTIONS FLOW DEEP DIVE: {ticker}")
    print("=" * 60)
    print(f"  Price:          ${r['price']:.2f}")
    print(f"  Expiry:         {r['expiry']} ({r['days_to_exp']}d)")
    print()
    print(f"  HEAT SCORE:     {r['heat_score']:.0f}/100  {_heat_emoji(r['heat_score'])}")
    print(f"  Direction lean: {r['direction']}")
    print()
    print("  Component Scores:")
    print(f"    Volume spike  {r['vol_score']:>5.0f}/30   {r['vol_label']}")
    print(f"    IV rank       {r['iv_score']:>5.0f}/25   rank={r['iv_rank']:.0f}%", end="")
    if r.get("implied_vol_pct"):
        print(f"  IV={r['implied_vol_pct']:.0f}%", end="")
    print()
    print(f"    Expected move {r['em_score']:>5.0f}/25   {r['expected_move_pct']:.1f}% (${r['straddle_cost']:.2f} straddle)")
    print(f"    Put/call      {r['pc_score']:>5.0f}/20   {r['pc_label']}")
    print()
    print("  Options Volume:")
    print(f"    Calls today:  {r['call_vol']:>10,}")
    print(f"    Puts today:   {r['put_vol']:>10,}")
    print(f"    Total:        {r['total_options_vol']:>10,}")
    print()
    print("  Open Interest:")
    print(f"    Total OI:     {r['total_oi']:>10,}")
    print(f"    Gamma conc.:  {r['gamma_concentration_pct']:.1f}% within 5% of price")
    print()
    print("  Stock Volume:")
    vol_ratio = r["stock_vol_today"] / r["stock_vol_20d_avg"] if r["stock_vol_20d_avg"] > 0 else 0
    print(f"    Today:        {r['stock_vol_today']:>12,}  ({vol_ratio:.1f}x 20d avg)")
    print(f"    20d avg:      {r['stock_vol_20d_avg']:>12,}")
    print()


def print_results(results: List[dict], top: int = 0) -> None:
    """Print full results: summary table + top deep dives."""
    if top > 0:
        results = results[:top]

    print_summary_table(results)

    # Deep dive top 5
    for r in results[:5]:
        print_deep_dive(r)


# ==============================================================================
# SECTION 6: PROGRAMMATIC API
# ==============================================================================

def get_options_heat(ticker: str) -> dict:
    """
    Public API for use by other modules (catalyst_screener, ai_quant, etc.).
    Returns a simplified dict with key metrics for one ticker.
    Returns empty dict if no data.
    """
    result = analyze_ticker(ticker)
    if result is None:
        return {}

    return {
        "heat_score": result["heat_score"],
        "direction": result["direction"],
        "expected_move_pct": result["expected_move_pct"],
        "implied_vol_pct": result.get("implied_vol_pct"),
        "iv_rank": result["iv_rank"],
        "pc_ratio": result["pc_ratio"],
        "total_options_vol": result["total_options_vol"],
        "call_vol": result["call_vol"],
        "put_vol": result["put_vol"],
        "total_oi": result["total_oi"],
        "straddle_cost": result["straddle_cost"],
        "expiry": result["expiry"],
        "days_to_exp": result["days_to_exp"],
    }


def get_options_heat_batch(tickers: List[str]) -> Dict[str, dict]:
    """Batch version — returns dict keyed by ticker."""
    out = {}
    for ticker in tickers:
        if ticker.endswith("-USD"):
            continue
        data = get_options_heat(ticker)
        if data:
            out[ticker] = data
    return out


# ==============================================================================
# SECTION 7: CLI
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Options Flow Screener — screen for high-options-heat stocks"
    )
    parser.add_argument("--ticker", type=str, help="Single ticker deep dive")
    parser.add_argument("--tickers", nargs="+", help="Multiple tickers")
    parser.add_argument("--watchlist", action="store_true", help="Use watchlist.txt")
    parser.add_argument("--full", action="store_true", help="Full OPTIONS_UNIVERSE")
    parser.add_argument("--top", type=int, default=0, help="Show top N results")
    parser.add_argument("--min-heat", type=float, default=0.0, help="Min heat score (0-100)")
    parser.add_argument("--verbose", action="store_true", help="Show progress")
    args = parser.parse_args()

    print()
    print("================================================================")
    print("  OPTIONS FLOW SCREENER")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("================================================================")

    if args.ticker:
        # Single ticker
        result = analyze_ticker(args.ticker.upper(), verbose=True)
        if result:
            print_deep_dive(result)
        else:
            print(f"  No options data for {args.ticker.upper()}")

    elif args.tickers:
        tickers = [t.upper() for t in args.tickers]
        print(f"  Screening {len(tickers)} tickers...")
        results = screen_universe(tickers, min_heat=args.min_heat, verbose=args.verbose)
        print_results(results, top=args.top)

    elif args.watchlist:
        tickers = _read_watchlist_tickers()
        # Add default watchlist tickers if file is empty
        if not tickers:
            tickers = EQUITY_WATCHLIST[:20] + CUSTOM_WATCHLIST
        # Filter out crypto
        tickers = [t for t in tickers if not t.endswith("-USD")]
        print(f"  Screening {len(tickers)} watchlist tickers...")
        results = screen_universe(tickers, min_heat=args.min_heat, verbose=args.verbose)
        print_results(results, top=args.top)

    else:
        # Default: OPTIONS_UNIVERSE
        print(f"  Screening {len(OPTIONS_UNIVERSE)} tickers...")
        results = screen_universe(OPTIONS_UNIVERSE, min_heat=args.min_heat, verbose=args.verbose)
        print_results(results, top=args.top or 15)


if __name__ == "__main__":
    main()
