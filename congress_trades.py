#!/usr/bin/env python3
"""
================================================================================
CONGRESSIONAL TRADING MODULE v1.0
================================================================================
Tracks stock trades by US Senators and Representatives.

DATA SOURCES (free, no API key):
    - House Stock Watcher:  housestockwatcher.com/api
    - Senate Stock Watcher: senatestockwatcher.com/api

STOCK ACT (2012):
    Members of Congress must disclose stock transactions >$1,000
    within 45 days. This delay means trades are backward-looking,
    but academic research shows significant alpha even after disclosure.

WHY IT MATTERS:
    - Congress members sit on committees that regulate industries
    - They receive classified briefings before the public
    - They vote on legislation that moves markets
    - Studies show 5-12% annual outperformance

USAGE:
    python3 congress_trades.py                    # Show recent trades
    python3 congress_trades.py --ticker GME       # Trades for specific stock
    python3 congress_trades.py --scan             # Check watchlist
    python3 congress_trades.py --top-traders      # Most active politicians

IMPORTANT: This is NOT investment advice. Congressional trade data
           has a 30-45 day reporting lag.
================================================================================
"""

import argparse
import json
import os
import sys
import time
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional

warnings.filterwarnings("ignore")

CACHE_FILE = "congress_cache.json"
CACHE_MAX_AGE_HOURS = 12  # Refresh twice daily at most

# Key congressional committees and their market relevance
COMMITTEE_RELEVANCE = {
    "Finance": ["banks", "insurance", "tax", "trade"],
    "Banking": ["banks", "fintech", "crypto", "housing"],
    "Commerce": ["tech", "telecom", "energy", "consumer"],
    "Armed Services": ["defense", "aerospace", "cyber"],
    "Energy": ["oil", "gas", "renewables", "utilities"],
    "Health": ["pharma", "biotech", "hospitals", "insurance"],
    "Intelligence": ["defense", "cyber", "surveillance"],
    "Judiciary": ["tech", "antitrust", "crypto"],
}

# Notable traders (high-profile, frequently tracked)
NOTABLE_TRADERS = [
    "Nancy Pelosi", "Pelosi", "Dan Crenshaw", "Tommy Tuberville",
    "Markwayne Mullin", "Josh Gottheimer", "Michael McCaul",
    "Pat Fallon", "Ro Khanna", "Mark Green",
]

# Known spouse traders — spouses who trade actively on behalf of the household.
# Spouse trades are MORE informative than member trades because:
# 1. Plausible deniability — "my spouse manages our portfolio"
# 2. Less scrutiny from media than direct member trades
# 3. Academic research shows spouse trades outperform member trades
SPOUSE_TRADERS = {
    "Nancy Pelosi": "Paul Pelosi",       # Legendary options trader
    "Dan Crenshaw": "Tara Crenshaw",
    "Michael McCaul": "Linda McCaul",
    "Ro Khanna": "Ritu Khanna",
    "Mark Green": "Camie Green",
    "Josh Gottheimer": "Marla Gottheimer",
    "Tommy Tuberville": "Suzanne Tuberville",
}


def _fetch_json(url: str) -> Optional[list]:
    """Fetch JSON from URL with rate limiting."""
    import urllib.request
    try:
        headers = {"User-Agent": "SignalEngine/1.0 (educational research)"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        time.sleep(0.5)  # Rate limit
        return data
    except Exception as e:
        return None


def _load_cache() -> dict:
    """Load cached congressional trade data."""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r") as f:
            cache = json.load(f)
        age_hours = (datetime.now() - datetime.fromisoformat(
            cache.get("timestamp", "2000-01-01"))).total_seconds() / 3600
        if age_hours > CACHE_MAX_AGE_HOURS:
            return {}
        return cache
    except Exception:
        return {}


def _save_cache(cache: dict):
    """Save trade data to cache."""
    cache["timestamp"] = datetime.now().isoformat()
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass


def fetch_house_trades(days_back: int = 90) -> list:
    """
    Fetch recent House of Representatives stock trades.
    Source: housestockwatcher.com/api
    """
    cache = _load_cache()
    if "house_trades" in cache:
        return cache["house_trades"]

    print("  Fetching House trades...")

    # The API returns all trades as JSON
    url = "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
    data = _fetch_json(url)

    if not data:
        # Fallback: try the API endpoint
        url = "https://housestockwatcher.com/api"
        data = _fetch_json(url)

    if not data:
        return []

    # Filter to recent trades
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    recent = []

    for trade in data:
        # Handle different date field names
        tx_date = trade.get("transaction_date", trade.get("transactionDate", ""))
        disclosure_date = trade.get("disclosure_date", trade.get("disclosureDate", ""))

        if not tx_date:
            continue

        # Normalize date format
        try:
            if "/" in tx_date:
                tx_date = datetime.strptime(tx_date, "%m/%d/%Y").strftime("%Y-%m-%d")
        except Exception:
            continue

        if tx_date < cutoff:
            continue

        ticker = trade.get("ticker", trade.get("asset_description", "")).strip()
        if not ticker or ticker == "--" or len(ticker) > 6:
            continue

        tx_type = trade.get("type", trade.get("transaction", "")).upper()
        representative = trade.get("representative", trade.get("name", "Unknown"))
        amount = trade.get("amount", trade.get("transaction_amount", ""))
        district = trade.get("district", "")

        # Owner: "self", "spouse", "dependent", "joint" — the STOCK Act
        # requires disclosure for all household members
        owner = trade.get("owner", trade.get("asset_owner", "self")).lower()
        is_spouse = "spouse" in owner or "joint" in owner
        is_dependent = "child" in owner or "dependent" in owner

        owner_label = "self"
        if is_spouse:
            owner_label = "spouse"
        elif is_dependent:
            owner_label = "dependent"

        recent.append({
            "chamber": "House",
            "politician": representative,
            "ticker": ticker.upper(),
            "type": "BUY" if "PURCHASE" in tx_type or "BUY" in tx_type else
                    "SELL" if "SALE" in tx_type or "SELL" in tx_type else tx_type,
            "amount": amount,
            "tx_date": tx_date,
            "disclosure_date": disclosure_date,
            "district": district,
            "owner": owner_label,
        })

    # Update cache
    cache = _load_cache() or {}
    cache["house_trades"] = recent
    _save_cache(cache)

    return recent


def fetch_senate_trades(days_back: int = 90) -> list:
    """
    Fetch recent Senate stock trades.
    Source: senatestockwatcher.com/api
    """
    cache = _load_cache()
    if "senate_trades" in cache:
        return cache["senate_trades"]

    print("  Fetching Senate trades...")

    url = "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json"
    data = _fetch_json(url)

    if not data:
        url = "https://senatestockwatcher.com/api"
        data = _fetch_json(url)

    if not data:
        return []

    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    recent = []

    for trade in data:
        tx_date = trade.get("transaction_date", "")
        if not tx_date or tx_date < cutoff:
            continue

        ticker = trade.get("ticker", "").strip()
        if not ticker or ticker == "--" or len(ticker) > 6:
            continue

        tx_type = trade.get("type", "").upper()
        senator = trade.get("senator", trade.get("name", "Unknown"))
        amount = trade.get("amount", "")

        owner = trade.get("owner", trade.get("asset_owner", "self")).lower()
        is_spouse = "spouse" in owner or "joint" in owner
        owner_label = "spouse" if is_spouse else ("dependent" if "child" in owner else "self")

        recent.append({
            "chamber": "Senate",
            "politician": senator,
            "ticker": ticker.upper(),
            "type": "BUY" if "PURCHASE" in tx_type or "BUY" in tx_type else
                    "SELL" if "SALE" in tx_type or "SELL" in tx_type else tx_type,
            "amount": amount,
            "tx_date": tx_date,
            "disclosure_date": trade.get("disclosure_date", ""),
            "owner": owner_label,
        })

    cache = _load_cache() or {}
    cache["senate_trades"] = recent
    _save_cache(cache)

    return recent


def get_all_trades(days_back: int = 90) -> list:
    """Fetch and combine House + Senate trades."""
    house = fetch_house_trades(days_back)
    senate = fetch_senate_trades(days_back)
    all_trades = house + senate
    all_trades.sort(key=lambda t: t.get("tx_date", ""), reverse=True)
    return all_trades


def get_trades_for_ticker(ticker: str, days_back: int = 180) -> list:
    """Get all congressional trades for a specific ticker."""
    ticker = ticker.upper()
    all_trades = get_all_trades(days_back)
    return [t for t in all_trades if t["ticker"] == ticker]


def score_congress_signal(ticker: str, days_back: int = 90) -> dict:
    """
    Score congressional trading activity for the catalyst screener.

    Scoring (max 5):
    - Any congressional purchase:               +1
    - Multiple politicians buying:              +1
    - Notable trader (Pelosi, etc.) buying:     +1
    - Spouse trade by notable member:           +1 (more informative than direct)
    - Buy cluster (3+ buys, 0 sells):          +1

    WHY SPOUSE TRADES MATTER MORE:
    Academic research (Eggers & Hainmueller 2013, Ziobrowski et al.) shows
    spouse trades actually outperform member trades. The mechanism:
    - Members face media scrutiny → trade cautiously or delay
    - Spouses face less scrutiny → trade closer to the information event
    - "My spouse manages our portfolio" = plausible deniability
    - Paul Pelosi's NVDA/AAPL/GOOGL calls are the canonical example

    Returns dict with score, max, flags.
    """
    score = 0
    flags = []

    # Skip non-US tickers
    if ticker.endswith("-USD") or "." in ticker:
        return {"score": 0, "max": 5, "flags": []}

    trades = get_trades_for_ticker(ticker, days_back)

    if not trades:
        return {"score": 0, "max": 5, "flags": []}

    buys = [t for t in trades if t["type"] == "BUY"]
    sells = [t for t in trades if t["type"] == "SELL"]

    if buys:
        score += 1
        buy_politicians = list(set(t["politician"] for t in buys))

        # Count spouse vs self trades
        spouse_buys = [t for t in buys if t.get("owner") == "spouse"]
        self_buys = [t for t in buys if t.get("owner", "self") == "self"]

        buy_summary = f"{len(buys)} purchase(s) by {len(buy_politicians)} politician(s)"
        if spouse_buys:
            buy_summary += f" ({len(spouse_buys)} via spouse)"
        flags.append(f"CONGRESS BUYING: {buy_summary} in {days_back}d")

        # Multiple politicians buying = stronger signal
        if len(buy_politicians) >= 2:
            score += 1
            names = ", ".join(buy_politicians[:3])
            if len(buy_politicians) > 3:
                names += f" +{len(buy_politicians)-3} more"
            flags.append(f"Congress cluster: {names}")

        # Check for notable traders (member or their spouse)
        notable_found = False
        for buy in buys:
            for notable in NOTABLE_TRADERS:
                if notable.lower() in buy["politician"].lower():
                    score += 1
                    owner_note = ""
                    if buy.get("owner") == "spouse":
                        spouse_name = SPOUSE_TRADERS.get(buy["politician"], "spouse")
                        owner_note = f" (via {spouse_name})"
                    flags.append(
                        f"Notable trader: {buy['politician']}{owner_note} bought "
                        f"on {buy['tx_date']} ({buy.get('amount', '?')})"
                    )
                    notable_found = True
                    break
            if notable_found:
                break

        # Spouse trade bonus — spouse trades by ANY member are more informative
        if spouse_buys and not notable_found:
            score += 1
            spouse_politician = spouse_buys[0]["politician"]
            spouse_name = SPOUSE_TRADERS.get(spouse_politician, "spouse")
            flags.append(
                f"SPOUSE TRADE: {spouse_politician}'s {spouse_name} bought "
                f"on {spouse_buys[0]['tx_date']} — often more informative than direct trades"
            )

        # Buy cluster: 3+ buys with no sells
        if len(buys) >= 3 and len(sells) == 0:
            score += 1
            flags.append(
                f"Pure buy cluster: {len(buys)} buys, 0 sells — "
                f"strong congressional conviction"
            )

        # Show most recent buy with owner info
        latest = buys[0]
        owner_tag = ""
        if latest.get("owner") == "spouse":
            owner_tag = " [SPOUSE]"
        elif latest.get("owner") == "dependent":
            owner_tag = " [DEPENDENT]"
        flags.append(
            f"  → {latest['politician']} ({latest['chamber']}){owner_tag}: "
            f"{latest['type']} on {latest['tx_date']} ({latest.get('amount', '?')})"
        )

    if sells and not buys:
        spouse_sells = [t for t in sells if t.get("owner") == "spouse"]
        sell_note = f" ({len(spouse_sells)} via spouse)" if spouse_sells else ""
        flags.append(
            f"Congress SELLING only: {len(sells)} sale(s){sell_note}, 0 buys — bearish"
        )

    return {"score": score, "max": 5, "flags": flags}


# ==============================================================================
# REPORTING
# ==============================================================================

def print_ticker_report(ticker: str):
    """Print congressional trading report for a specific ticker."""
    ticker = ticker.upper()

    print(f"\n{'█' * 60}")
    print(f"  CONGRESSIONAL TRADES: {ticker}")
    print(f"{'█' * 60}")

    trades = get_trades_for_ticker(ticker, days_back=365)

    if not trades:
        print(f"\n  No congressional trades found for {ticker} in the last 12 months.")
        return

    buys = [t for t in trades if t["type"] == "BUY"]
    sells = [t for t in trades if t["type"] == "SELL"]

    print(f"\n  Last 12 months: {len(buys)} buys, {len(sells)} sells")

    # Spouse trade summary
    spouse_buys = [t for t in buys if t.get("owner") == "spouse"]
    spouse_sells = [t for t in sells if t.get("owner") == "spouse"]
    if spouse_buys or spouse_sells:
        print(f"  Spouse trades: {len(spouse_buys)} buys, {len(spouse_sells)} sells")

    print(f"\n  {'Date':>12}{'Politician':<25}{'Owner':<9}{'Chamber':<8}{'Type':<8}{'Amount':<20}")
    print(f"  {'─' * 82}")

    for t in trades[:20]:
        marker = " ← BUY" if t["type"] == "BUY" else (" ← SELL" if t["type"] == "SELL" else "")
        is_notable = any(n.lower() in t["politician"].lower() for n in NOTABLE_TRADERS)
        star = " ⭐" if is_notable else ""
        owner = t.get("owner", "self").upper()
        if owner == "SPOUSE":
            owner = "SPOUSE"
            # Try to show spouse name
            spouse_name = SPOUSE_TRADERS.get(t["politician"], "")
            if spouse_name:
                owner = spouse_name[:8]

        print(f"  {t['tx_date']:>12}"
              f"  {t['politician'][:23]:<25}"
              f"{owner:<9}"
              f"{t['chamber']:<8}"
              f"{t['type']:<8}"
              f"{str(t.get('amount', ''))[:18]:<20}{marker}{star}")

    if len(trades) > 20:
        print(f"\n  ... and {len(trades) - 20} more trades")

    # Score
    score = score_congress_signal(ticker)
    print(f"\n  Congress Score: {score['score']}/{score['max']}")
    for flag in score["flags"]:
        print(f"    • {flag}")


def print_watchlist_scan():
    """Scan watchlist for congressional trading activity."""
    watchlist_path = "./watchlist.txt"
    if not os.path.exists(watchlist_path):
        print("  No watchlist.txt found.")
        return

    with open(watchlist_path) as f:
        tickers = []
        seen = set()
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ticker = line.split("#")[0].strip().upper()
            if ticker and not ticker.endswith("-USD") and ticker not in seen:
                tickers.append(ticker)
                seen.add(ticker)

    print(f"\n{'█' * 60}")
    print(f"  CONGRESSIONAL TRADE SCAN — {len(tickers)} tickers")
    print(f"{'█' * 60}")

    # Fetch all trades once
    all_trades = get_all_trades(days_back=90)

    results = []
    for ticker in tickers:
        ticker_trades = [t for t in all_trades if t["ticker"] == ticker]
        buys = [t for t in ticker_trades if t["type"] == "BUY"]
        sells = [t for t in ticker_trades if t["type"] == "SELL"]

        if ticker_trades:
            politicians = list(set(t["politician"] for t in buys))
            results.append({
                "ticker": ticker,
                "buys": len(buys),
                "sells": len(sells),
                "politicians": politicians,
                "latest": ticker_trades[0]["tx_date"],
            })

    results.sort(key=lambda r: -r["buys"])

    if results:
        print(f"\n  {'Ticker':<8}{'Buys':>6}{'Sells':>7}{'Politicians':>12}{'Latest':>14}")
        print(f"  {'─' * 47}")

        for r in results:
            highlight = " ⚡" if r["buys"] >= 2 else ""
            print(f"  {r['ticker']:<8}"
                  f"{r['buys']:>6}"
                  f"{r['sells']:>7}"
                  f"{len(r['politicians']):>12}"
                  f"{r['latest']:>14}{highlight}")

        # Detail on top hits
        top = [r for r in results if r["buys"] >= 2]
        if top:
            print(f"\n  🔥 CONGRESSIONAL BUYING CLUSTERS:")
            for r in top:
                names = ", ".join(r["politicians"][:3])
                print(f"    {r['ticker']}: {r['buys']} buys by {names}")
    else:
        print(f"\n  No congressional trades found for watchlist stocks in last 90 days.")


def print_top_traders():
    """Show most active congressional traders."""
    all_trades = get_all_trades(days_back=90)

    if not all_trades:
        print("\n  No trade data available.")
        return

    print(f"\n{'█' * 60}")
    print(f"  MOST ACTIVE CONGRESSIONAL TRADERS (90 days)")
    print(f"{'█' * 60}")

    # Count by politician
    trader_counts = {}
    for t in all_trades:
        name = t["politician"]
        if name not in trader_counts:
            trader_counts[name] = {"buys": 0, "sells": 0, "tickers": set(),
                                    "chamber": t["chamber"]}
        if t["type"] == "BUY":
            trader_counts[name]["buys"] += 1
        elif t["type"] == "SELL":
            trader_counts[name]["sells"] += 1
        trader_counts[name]["tickers"].add(t["ticker"])

    # Sort by total activity
    sorted_traders = sorted(trader_counts.items(),
                             key=lambda x: -(x[1]["buys"] + x[1]["sells"]))

    print(f"\n  {'Politician':<30}{'Chamber':<8}{'Buys':>6}{'Sells':>7}"
          f"{'Stocks':>8}{'Notable':>8}")
    print(f"  {'─' * 67}")

    for name, data in sorted_traders[:20]:
        is_notable = any(n.lower() in name.lower() for n in NOTABLE_TRADERS)
        star = "⭐" if is_notable else ""

        print(f"  {name[:28]:<30}"
              f"{data['chamber']:<8}"
              f"{data['buys']:>6}"
              f"{data['sells']:>7}"
              f"{len(data['tickers']):>8}"
              f"  {star}")


def main():
    parser = argparse.ArgumentParser(description="Congressional Trading Module v1.0")
    parser.add_argument("--ticker", type=str, help="Trades for a specific stock")
    parser.add_argument("--scan", action="store_true", help="Scan watchlist")
    parser.add_argument("--top-traders", action="store_true", help="Most active politicians")
    parser.add_argument("--days", type=int, default=90, help="Lookback days (default: 90)")
    parser.add_argument("--refresh", action="store_true", help="Clear cache and refresh")
    args = parser.parse_args()

    if args.refresh and os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
        print("  Cache cleared.")

    if args.ticker:
        print_ticker_report(args.ticker.upper())
    elif args.scan:
        print_watchlist_scan()
    elif args.top_traders:
        print_top_traders()
    else:
        print("  Usage:")
        print("    python3 congress_trades.py --ticker GME     # Trades for GME")
        print("    python3 congress_trades.py --scan           # Scan watchlist")
        print("    python3 congress_trades.py --top-traders    # Most active politicians")

    print(f"\n  ⚠️  Congressional trades have a 30-45 day reporting lag.")
    print(f"  This data is informational, not investment advice.\n")


if __name__ == "__main__":
    main()
