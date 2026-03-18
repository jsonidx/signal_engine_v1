#!/usr/bin/env python3
"""
================================================================================
FUNDAMENTAL ANALYSIS MODULE v1.0
================================================================================
Pulls quant-grade fundamental data for watchlist tickers via yfinance (free).

WHAT IT COVERS:
    1. Valuation   — P/E (trailing + forward), P/S, P/B, EV/EBITDA
    2. Growth      — Revenue YoY, EPS YoY, quarterly acceleration
    3. Quality     — Gross/operating/net margins, ROE, ROA, debt/equity
    4. Balance     — Current ratio, free cash flow, cash vs debt
    5. Earnings    — Next earnings date, EPS beat/miss streak, analyst estimates
    6. Analyst     — Consensus rating, price target upside, # analysts

SCORING (each 0–4, total 0–24 → normalized 0–100%):
    - Valuation score   : cheap vs rich vs no-data
    - Growth score      : revenue + EPS acceleration
    - Quality score     : margins + return on capital
    - Balance score     : FCF + cash/debt health
    - Earnings score    : beat streak + upcoming catalyst proximity
    - Analyst score     : consensus + target upside

USAGE:
    python3 fundamental_analysis.py --watchlist          # All watchlist tickers
    python3 fundamental_analysis.py --ticker GME         # Single ticker deep dive
    python3 fundamental_analysis.py --watchlist --top 10 # Top 10 by score

DATA SOURCE: yfinance (Yahoo Finance) — free, no API key required.
NOTE: Crypto and some ETFs will have limited fundamental data.

IMPORTANT: This is NOT investment advice. Fundamentals lag price action.
================================================================================
"""

import argparse
import os
import sys
import time
import warnings
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

try:
    from config import OUTPUT_DIR
except ImportError:
    OUTPUT_DIR = "./signals_output"


# ==============================================================================
# SECTION 1: DATA COLLECTION
# ==============================================================================

def fetch_fundamentals(ticker: str, use_cache: bool = True) -> Optional[dict]:
    """
    Pull all fundamental data for a ticker from yfinance.
    Returns None if the ticker is invalid or has no data.

    Results are cached in fundamentals_cache.db for DEFAULT_TTL_DAYS (30 days)
    to avoid redundant yfinance calls for quarterly-changing data.
    Pass use_cache=False to force a fresh fetch (e.g. after an earnings release).
    """
    if use_cache:
        try:
            from fundamentals_cache import get_cached, save_to_cache as _save
            cached = get_cached(ticker)
            if cached is not None:
                return cached
        except ImportError:
            _save = None
    else:
        _save = None
        try:
            from fundamentals_cache import save_to_cache as _save
        except ImportError:
            pass

    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}

        if not info or info.get("quoteType") is None:
            return None

        quote_type = info.get("quoteType", "").upper()

        # Collect raw fields — use .get() with None defaults throughout
        raw = {
            "ticker": ticker,
            "name": info.get("longName") or info.get("shortName") or ticker,
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "quote_type": quote_type,

            # Price
            "price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "mkt_cap": info.get("marketCap"),

            # Valuation
            "pe_trailing": info.get("trailingPE"),
            "pe_forward": info.get("forwardPE"),
            "ps_ratio": info.get("priceToSalesTrailingTwelveMonths"),
            "pb_ratio": info.get("priceToBook"),
            "ev_ebitda": info.get("enterpriseToEbitda"),

            # Growth
            "revenue_growth_yoy": info.get("revenueGrowth"),        # e.g. 0.23 = 23%
            "earnings_growth_yoy": info.get("earningsGrowth"),
            "revenue_growth_qoq": info.get("revenueQuarterlyGrowth"),
            "earnings_growth_qoq": info.get("earningsQuarterlyGrowth"),

            # Margins
            "gross_margin": info.get("grossMargins"),
            "operating_margin": info.get("operatingMargins"),
            "net_margin": info.get("profitMargins"),

            # Returns
            "roe": info.get("returnOnEquity"),
            "roa": info.get("returnOnAssets"),

            # Balance sheet
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "free_cash_flow": info.get("freeCashflow"),
            "total_cash": info.get("totalCash"),
            "total_debt": info.get("totalDebt"),

            # Analyst
            "analyst_rating": info.get("recommendationMean"),  # 1=Strong Buy, 5=Strong Sell
            "analyst_count": info.get("numberOfAnalystOpinions"),
            "target_mean": info.get("targetMeanPrice"),
            "target_high": info.get("targetHighPrice"),
            "target_low": info.get("targetLowPrice"),

            # Earnings event
            "earnings_timestamp": info.get("earningsTimestamp"),
        }

        # EPS beat/miss history
        try:
            hist = stock.earnings_history
            if hist is not None and not hist.empty:
                raw["eps_history"] = hist.to_dict("records")
            else:
                raw["eps_history"] = []
        except Exception:
            raw["eps_history"] = []

        # Save to cache for future runs
        try:
            if _save is not None:
                _save(ticker, raw)
            else:
                from fundamentals_cache import save_to_cache
                save_to_cache(ticker, raw)
        except Exception:
            pass

        return raw

    except Exception:
        return None


# ==============================================================================
# SECTION 2: SCORING
# ==============================================================================

def score_valuation(raw: dict) -> dict:
    """
    Score: 0–4
    Cheap valuation = high score. Rich or missing = lower.
    Uses forward P/E as primary; falls back to trailing, then P/S.
    """
    score = 0
    flags = []

    pe = raw.get("pe_forward") or raw.get("pe_trailing")
    ps = raw.get("ps_ratio")
    ev_ebitda = raw.get("ev_ebitda")
    pb = raw.get("pb_ratio")

    if pe is not None and pe > 0:
        if pe < 15:
            score += 2
            flags.append(f"Cheap P/E: {pe:.1f}x (growth-adjusted value)")
        elif pe < 25:
            score += 1
            flags.append(f"Fair P/E: {pe:.1f}x")
        elif pe > 60:
            score -= 1
            flags.append(f"Expensive P/E: {pe:.1f}x — priced for perfection")
        else:
            flags.append(f"P/E: {pe:.1f}x (neutral)")

    if ps is not None and ps > 0:
        if ps < 2:
            score += 1
            flags.append(f"Low P/S: {ps:.1f}x — value territory")
        elif ps > 15:
            flags.append(f"High P/S: {ps:.1f}x — premium growth multiple")
        else:
            flags.append(f"P/S: {ps:.1f}x")

    if ev_ebitda is not None and ev_ebitda > 0:
        if ev_ebitda < 10:
            score += 1
            flags.append(f"Attractive EV/EBITDA: {ev_ebitda:.1f}x")
        elif ev_ebitda > 30:
            flags.append(f"Rich EV/EBITDA: {ev_ebitda:.1f}x")

    if pe is None and ps is None:
        flags.append("No valuation data (pre-revenue or crypto)")

    return {"score": max(score, 0), "max": 4, "flags": flags}


def score_growth(raw: dict) -> dict:
    """
    Score: 0–4
    Rewards revenue AND earnings acceleration, especially QoQ.
    """
    score = 0
    flags = []

    rev_yoy = raw.get("revenue_growth_yoy")
    eps_yoy = raw.get("earnings_growth_yoy")
    rev_qoq = raw.get("revenue_growth_qoq")
    eps_qoq = raw.get("earnings_growth_qoq")

    # Revenue growth
    if rev_yoy is not None:
        if rev_yoy > 0.30:
            score += 2
            flags.append(f"Strong revenue growth: {rev_yoy:.0%} YoY")
        elif rev_yoy > 0.10:
            score += 1
            flags.append(f"Revenue growth: {rev_yoy:.0%} YoY")
        elif rev_yoy < 0:
            score -= 1
            flags.append(f"Revenue declining: {rev_yoy:.0%} YoY")
        else:
            flags.append(f"Flat revenue: {rev_yoy:.0%} YoY")

    # QoQ acceleration (more recent signal)
    if rev_qoq is not None and rev_qoq > 0.10:
        score += 1
        flags.append(f"Revenue accelerating QoQ: {rev_qoq:.0%}")

    # EPS growth
    if eps_yoy is not None:
        if eps_yoy > 0.25:
            score += 1
            flags.append(f"Strong EPS growth: {eps_yoy:.0%} YoY")
        elif eps_yoy < -0.20:
            flags.append(f"EPS declining: {eps_yoy:.0%} YoY")

    if rev_yoy is None and eps_yoy is None:
        flags.append("No growth data available")

    return {"score": max(score, 0), "max": 4, "flags": flags}


def score_quality(raw: dict) -> dict:
    """
    Score: 0–4
    Rewards high margins, strong ROE, manageable debt.
    """
    score = 0
    flags = []

    gm = raw.get("gross_margin")
    om = raw.get("operating_margin")
    nm = raw.get("net_margin")
    roe = raw.get("roe")
    roa = raw.get("roa")

    if gm is not None:
        if gm > 0.50:
            score += 1
            flags.append(f"High gross margin: {gm:.0%} (asset-light business)")
        elif gm > 0.30:
            flags.append(f"Gross margin: {gm:.0%}")
        elif gm < 0.10:
            flags.append(f"Thin gross margin: {gm:.0%}")

    if om is not None:
        if om > 0.20:
            score += 1
            flags.append(f"Strong operating margin: {om:.0%}")
        elif om > 0.05:
            flags.append(f"Operating margin: {om:.0%}")
        elif om < 0:
            flags.append(f"Operating loss: {om:.0%}")

    if roe is not None:
        if roe > 0.20:
            score += 1
            flags.append(f"High ROE: {roe:.0%} — efficient capital use")
        elif roe > 0.10:
            flags.append(f"ROE: {roe:.0%}")
        elif roe < 0:
            flags.append(f"Negative ROE: {roe:.0%}")

    if nm is not None and nm > 0.15:
        score += 1
        flags.append(f"Net margin: {nm:.0%}")

    if gm is None and om is None and roe is None:
        flags.append("No quality/margin data (crypto or ETF)")

    return {"score": max(score, 0), "max": 4, "flags": flags}


def score_balance_sheet(raw: dict) -> dict:
    """
    Score: 0–4
    Rewards FCF positive, cash > debt, healthy current ratio.
    """
    score = 0
    flags = []

    fcf = raw.get("free_cash_flow")
    cash = raw.get("total_cash")
    debt = raw.get("total_debt")
    cr = raw.get("current_ratio")
    de = raw.get("debt_to_equity")

    if fcf is not None:
        if fcf > 0:
            score += 2
            flags.append(f"FCF positive: ${fcf/1e6:.0f}M — self-funding")
        else:
            flags.append(f"FCF negative: ${fcf/1e6:.0f}M — burning cash")

    if cash is not None and debt is not None and debt > 0:
        net_cash = cash - debt
        if net_cash > 0:
            score += 1
            flags.append(f"Net cash position: ${net_cash/1e6:.0f}M")
        else:
            flags.append(f"Net debt: ${-net_cash/1e6:.0f}M")
    elif cash is not None and (debt is None or debt == 0):
        score += 1
        flags.append(f"Debt-free, cash: ${cash/1e6:.0f}M")

    if cr is not None:
        if cr > 2.0:
            score += 1
            flags.append(f"Strong current ratio: {cr:.1f}x")
        elif cr < 1.0:
            flags.append(f"Weak current ratio: {cr:.1f}x — liquidity risk")
        else:
            flags.append(f"Current ratio: {cr:.1f}x")

    if de is not None:
        if de > 200:
            flags.append(f"High debt/equity: {de:.0f}% — leveraged")

    if fcf is None and cash is None:
        flags.append("No balance sheet data")

    return {"score": max(score, 0), "max": 4, "flags": flags}


def score_earnings_catalyst(raw: dict) -> dict:
    """
    Score: 0–4
    Rewards upcoming earnings (binary catalyst), beat streaks.
    """
    score = 0
    flags = []

    # Upcoming earnings proximity
    ts = raw.get("earnings_timestamp")
    if ts:
        try:
            now = datetime.now(timezone.utc).timestamp()
            days_until = (ts - now) / 86400
            if 0 < days_until <= 7:
                score += 2
                flags.append(f"Earnings in {days_until:.0f} days — imminent binary catalyst")
            elif 0 < days_until <= 21:
                score += 1
                flags.append(f"Earnings in {days_until:.0f} days — near-term catalyst")
            elif days_until < 0:
                days_ago = -days_until
                if days_ago < 14:
                    flags.append(f"Earnings {days_ago:.0f} days ago — reaction settling")
        except Exception:
            pass

    # EPS beat/miss streak
    history = raw.get("eps_history", [])
    if history:
        recent = history[-4:]  # Last 4 quarters
        beats = sum(
            1 for q in recent
            if q.get("epsActual") is not None
            and q.get("epsEstimate") is not None
            and q.get("epsActual", 0) >= q.get("epsEstimate", 0)
        )
        total = len([q for q in recent if q.get("epsActual") is not None])

        if total > 0:
            beat_rate = beats / total
            if beats >= 3:
                score += 2
                flags.append(f"Beat streak: {beats}/{total} quarters beat EPS estimates")
            elif beats >= 2:
                score += 1
                flags.append(f"Mixed EPS history: {beats}/{total} beats")
            else:
                flags.append(f"Miss history: {beats}/{total} beats — execution risk")

            # Surprise magnitude on last quarter
            last = next((q for q in reversed(recent) if q.get("epsActual") is not None), None)
            if last and last.get("epsEstimate") and last.get("epsEstimate") != 0:
                surprise = (last["epsActual"] - last["epsEstimate"]) / abs(last["epsEstimate"])
                if surprise > 0.10:
                    flags.append(f"Last quarter beat by {surprise:.0%} — strong execution")
                elif surprise < -0.10:
                    flags.append(f"Last quarter missed by {abs(surprise):.0%}")
    else:
        flags.append("No EPS history (pre-earnings or crypto)")

    return {"score": max(score, 0), "max": 4, "flags": flags}


def score_analyst_consensus(raw: dict) -> dict:
    """
    Score: 0–4
    recommendationMean: 1.0 = Strong Buy, 3.0 = Hold, 5.0 = Strong Sell
    Also scores price target upside.
    """
    score = 0
    flags = []

    rating = raw.get("analyst_rating")
    count = raw.get("analyst_count") or 0
    target = raw.get("target_mean")
    price = raw.get("price")

    if rating is not None and count > 0:
        if rating <= 1.5:
            score += 2
            flags.append(f"Strong Buy consensus: {rating:.1f}/5 ({count} analysts)")
        elif rating <= 2.5:
            score += 1
            flags.append(f"Buy consensus: {rating:.1f}/5 ({count} analysts)")
        elif rating >= 3.5:
            flags.append(f"Sell/Hold consensus: {rating:.1f}/5 ({count} analysts)")
        else:
            flags.append(f"Hold consensus: {rating:.1f}/5 ({count} analysts)")

        if count >= 10:
            score += 1
            flags.append(f"High analyst coverage: {count} analysts")

    if target is not None and price is not None and price > 0:
        upside = (target - price) / price
        if upside > 0.30:
            score += 1
            flags.append(f"Target upside: {upside:.0%} (mean target ${target:.2f})")
        elif upside > 0.10:
            flags.append(f"Target upside: {upside:.0%} (mean target ${target:.2f})")
        elif upside < -0.10:
            flags.append(f"Analysts see downside: {upside:.0%} to target ${target:.2f}")

    if rating is None and target is None:
        flags.append("No analyst coverage data")

    return {"score": max(score, 0), "max": 4, "flags": flags}


# ==============================================================================
# SECTION 3: COMPOSITE + REPORTING
# ==============================================================================

def analyze_ticker(ticker: str, use_cache: bool = True) -> Optional[dict]:
    """Run full fundamental analysis on one ticker. Returns result dict or None."""
    raw = fetch_fundamentals(ticker, use_cache=use_cache)
    if raw is None:
        return None

    val = score_valuation(raw)
    growth = score_growth(raw)
    quality = score_quality(raw)
    balance = score_balance_sheet(raw)
    earnings = score_earnings_catalyst(raw)
    analyst = score_analyst_consensus(raw)

    total_score = val["score"] + growth["score"] + quality["score"] + balance["score"] + earnings["score"] + analyst["score"]
    max_score = val["max"] + growth["max"] + quality["max"] + balance["max"] + earnings["max"] + analyst["max"]
    composite = total_score / max_score * 100 if max_score > 0 else 0

    all_flags = val["flags"] + growth["flags"] + quality["flags"] + balance["flags"] + earnings["flags"] + analyst["flags"]

    return {
        "ticker": ticker,
        "name": raw.get("name", ticker),
        "sector": raw.get("sector", "N/A"),
        "price": raw.get("price"),
        "mkt_cap": raw.get("mkt_cap"),
        "pe_forward": raw.get("pe_forward"),
        "pe_trailing": raw.get("pe_trailing"),
        "revenue_growth_yoy": raw.get("revenue_growth_yoy"),
        "earnings_growth_yoy": raw.get("earnings_growth_yoy"),
        "operating_margin": raw.get("operating_margin"),
        "roe": raw.get("roe"),
        "free_cash_flow": raw.get("free_cash_flow"),
        "analyst_rating": raw.get("analyst_rating"),
        "analyst_count": raw.get("analyst_count"),
        "target_mean": raw.get("target_mean"),
        "scores": {
            "valuation": val["score"],
            "growth": growth["score"],
            "quality": quality["score"],
            "balance": balance["score"],
            "earnings": earnings["score"],
            "analyst": analyst["score"],
        },
        "composite": round(composite, 1),
        "flags": all_flags,
    }


def print_summary_table(results: List[dict], top_n: int = None):
    """Print ranked summary table."""
    if not results:
        print("\n  No results.")
        return

    ranked = sorted(results, key=lambda x: x["composite"], reverse=True)
    if top_n:
        ranked = ranked[:top_n]

    print(f"\n{'─' * 70}")
    print(f"  FUNDAMENTAL SCORECARD — {len(ranked)} tickers")
    print(f"{'─' * 70}")
    print(f"\n  {'Rank':<5}{'Ticker':<8}{'Sector':<16}{'Price':>9}{'FwdPE':>7}{'RevGrw':>8}"
          f"{'OpMgn':>7}{'ROE':>6}{'Val':>5}{'Grw':>5}{'Qlty':>5}{'Bal':>5}{'Ear':>5}{'Anl':>5}{'SCORE':>7}")
    print(f"  {'─' * 116}")

    for i, r in enumerate(ranked):
        if r["composite"] >= 60:
            tier = "🟢"
        elif r["composite"] >= 40:
            tier = "🟡"
        else:
            tier = "🔴"

        sector = (r["sector"] or "N/A")[:14]
        price = f"${r['price']:.2f}" if r["price"] else "N/A"
        pe = f"{r['pe_forward']:.0f}x" if r["pe_forward"] else ("—" if not r["pe_trailing"] else f"{r['pe_trailing']:.0f}x")
        rev = f"{r['revenue_growth_yoy']:.0%}" if r["revenue_growth_yoy"] is not None else "N/A"
        om = f"{r['operating_margin']:.0%}" if r["operating_margin"] is not None else "N/A"
        roe = f"{r['roe']:.0%}" if r["roe"] is not None else "N/A"

        s = r["scores"]
        print(f"  {i+1:<5}{r['ticker']:<8}{sector:<16}{price:>9}{pe:>7}{rev:>8}"
              f"{om:>7}{roe:>6}"
              f"{s['valuation']:>4}/4"
              f"{s['growth']:>4}/4"
              f"{s['quality']:>4}/4"
              f"{s['balance']:>4}/4"
              f"{s['earnings']:>4}/4"
              f"{s['analyst']:>4}/4"
              f" {tier}{r['composite']:>4.0f}%")


def print_deep_dive(result: dict):
    """Print full fundamental deep dive for one ticker."""
    print(f"\n{'█' * 60}")
    print(f"  FUNDAMENTAL DEEP DIVE: {result['ticker']}")
    print(f"  {result['name']}")
    print(f"  Sector: {result['sector']} | Industry: N/A")
    print(f"{'█' * 60}")

    if result["price"]:
        mkt = result.get("mkt_cap")
        mkt_str = f"${mkt/1e9:.2f}B" if mkt and mkt > 1e9 else (f"${mkt/1e6:.0f}M" if mkt else "N/A")
        print(f"\n  Price: ${result['price']:.2f} | Market Cap: {mkt_str}")

    # Score breakdown
    print(f"\n  SCORE BREAKDOWN:")
    s = result["scores"]
    bars = {
        "Valuation":  (s["valuation"], 4),
        "Growth":     (s["growth"], 4),
        "Quality":    (s["quality"], 4),
        "Bal Sheet":  (s["balance"], 4),
        "Earnings":   (s["earnings"], 4),
        "Analyst":    (s["analyst"], 4),
    }
    for label, (score, mx) in bars.items():
        bar = "█" * score + "░" * (mx - score)
        print(f"    {label:<12} [{bar}] {score}/{mx}")
    print(f"    {'─' * 30}")
    print(f"    {'COMPOSITE':<12}  {result['composite']:.0f}%")

    # Flags
    print(f"\n  SIGNALS:")
    for flag in result["flags"]:
        print(f"    • {flag}")

    print(f"\n  ⚠️  Fundamental data lags by 1–3 months. Not investment advice.")


def read_watchlist_tickers(path: str = "./watchlist.txt") -> List[str]:
    """Parse watchlist.txt and return all unique tickers."""
    if not os.path.exists(path):
        print(f"  [WARN] watchlist.txt not found at {path}")
        return []

    tickers = []
    seen = set()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ticker = line.split("#")[0].strip().upper()
            if ticker and ticker not in seen:
                tickers.append(ticker)
                seen.add(ticker)
    return tickers


# ==============================================================================
# SECTION 4: MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Fundamental Analysis Module v1.0")
    parser.add_argument("--ticker", type=str, help="Single ticker deep dive")
    parser.add_argument("--watchlist", action="store_true",
                        help="Analyze all tickers in watchlist.txt")
    parser.add_argument("--tickers", type=str,
                        help="Comma-separated list of tickers")
    parser.add_argument("--top", type=int, default=None,
                        help="Show only top N results")
    parser.add_argument("--refresh-cache", action="store_true",
                        help="Force re-fetch from yfinance, ignoring cached data")
    parser.add_argument("--cache-status", action="store_true",
                        help="Show what is currently cached and exit")
    args = parser.parse_args()

    if args.cache_status:
        try:
            from fundamentals_cache import cache_status, DEFAULT_TTL_DAYS
            rows = cache_status()
            if not rows:
                print("  Fundamentals cache is empty.")
            else:
                print(f"\n  {'TICKER':<8}  {'FETCHED':<19}  {'AGE':>6}  STATUS")
                print("  " + "-" * 50)
                for r in rows:
                    status = "EXPIRED" if r["expired"] else "fresh"
                    print(f"  {r['ticker']:<8}  {r['fetched_at']:<19}  {r['age_days']:>5.1f}d  {status}")
                print(f"\n  {len(rows)} tickers cached  |  TTL = {DEFAULT_TTL_DAYS} days\n")
        except ImportError:
            print("  fundamentals_cache module not found.")
        return

    print(f"\n{'█' * 60}")
    print(f"  FUNDAMENTAL ANALYSIS v1.0")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'█' * 60}")

    # Build ticker list
    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    elif args.watchlist:
        tickers = read_watchlist_tickers()
        # Skip crypto (yfinance has no fundamentals for them)
        equity = [t for t in tickers if not t.endswith("-USD")]
        skipped = [t for t in tickers if t.endswith("-USD")]
        if skipped:
            print(f"\n  Skipping crypto (no fundamentals): {', '.join(skipped)}")
        tickers = equity
    else:
        parser.print_help()
        return

    if not tickers:
        print("  No tickers to analyze.")
        return

    print(f"\n  Analyzing {len(tickers)} ticker(s)...")

    use_cache = not args.refresh_cache
    if args.refresh_cache:
        print("  Cache refresh requested — fetching live data from yfinance.\n")

    results = []
    for i, ticker in enumerate(tickers):
        print(f"\r  Fetching: {ticker:<8} ({i+1}/{len(tickers)})", end="", flush=True)
        result = analyze_ticker(ticker, use_cache=use_cache)
        if result:
            results.append(result)
        time.sleep(0.3)  # Be gentle with yfinance

    print(f"\r  Done: {len(results)}/{len(tickers)} tickers with data." + " " * 20)

    if not results:
        print("  No fundamental data retrieved.")
        return

    if args.ticker and len(results) == 1:
        # Deep dive for single ticker
        print_deep_dive(results[0])
    else:
        # Summary table + deep dive for top 3
        print_summary_table(results, top_n=args.top)

        print(f"\n{'─' * 60}")
        print(f"  DETAILED FLAGS — TOP 3")
        print(f"{'─' * 60}")
        top3 = sorted(results, key=lambda x: x["composite"], reverse=True)[:3]
        for r in top3:
            print(f"\n  {r['ticker']} — {r['composite']:.0f}% fundamental score")
            for flag in r["flags"]:
                print(f"    • {flag}")

    # Export CSV
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    export_path = os.path.join(OUTPUT_DIR, f"fundamental_{date_str}.csv")
    export_rows = []
    for r in results:
        row = {k: v for k, v in r.items() if k not in ("flags", "scores")}
        row.update({f"score_{k}": v for k, v in r["scores"].items()})
        export_rows.append(row)
    pd.DataFrame(export_rows).sort_values("composite", ascending=False).to_csv(
        export_path, index=False
    )
    print(f"\n  📁 Exported: {export_path}")

    print(f"\n{'█' * 60}")
    print(f"  ⚠️  Fundamental data from Yahoo Finance — may lag 1–3 months.")
    print(f"  Cross-reference with SEC filings for accuracy.")
    print(f"  THIS IS NOT INVESTMENT ADVICE.")
    print(f"{'█' * 60}\n")


if __name__ == "__main__":
    main()
