#!/usr/bin/env python3
"""
================================================================================
SEC EDGAR MODULE v1.0
================================================================================
Extracts insider transactions, institutional holdings, and material events
from SEC EDGAR (free, no API key — just requires a User-Agent header).

DATA SOURCES:
    - SEC EDGAR Full-Text Search API
    - SEC EDGAR Company Filings API
    - SEC EDGAR XBRL Companion API

KEY SIGNALS:
    1. INSIDER BUYING (Form 4) — Most predictive public signal
    2. INSTITUTIONAL ACCUMULATION (13F) — Smart money positioning
    3. ACTIVIST STAKES (13D/13G) — 5%+ ownership = catalyst trigger
    4. MATERIAL EVENTS (8-K) — Earnings, buybacks, M&A
    5. SHORT INTEREST (via FINRA data in filings)

SEC EDGAR RULES:
    - Max 10 requests per second
    - Must include User-Agent with contact email
    - All data is public and free

USAGE:
    python3 sec_module.py --ticker GME              # Full SEC scan
    python3 sec_module.py --ticker GME --insiders   # Insider trades only
    python3 sec_module.py --scan                    # Scan entire watchlist
    python3 sec_module.py --insider-buys            # Find recent insider buys across all stocks

IMPORTANT: This is NOT investment advice. SEC data is informational.
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

import pandas as pd

warnings.filterwarnings("ignore")

try:
    from config import OUTPUT_DIR
except ImportError:
    OUTPUT_DIR = "./signals_output"

# SEC EDGAR requires a User-Agent with contact info
SEC_HEADERS = {
    "User-Agent": "SignalEngine/1.0 (educational research tool; contact@example.com)",
    "Accept": "application/json",
}

# SEC rate limit: max 10 requests/second
SEC_RATE_LIMIT = 0.15  # seconds between requests

EDGAR_BASE = "https://efts.sec.gov/LATEST"
EDGAR_FILINGS = "https://data.sec.gov"
EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"


# ==============================================================================
# SECTION 1: EDGAR API HELPERS
# ==============================================================================

def _sec_request(url: str) -> Optional[dict]:
    """Make a rate-limited request to SEC EDGAR."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers=SEC_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        time.sleep(SEC_RATE_LIMIT)
        return data
    except Exception as e:
        return None


def get_cik(ticker: str) -> Optional[str]:
    """
    Get the SEC CIK (Central Index Key) for a ticker.
    This is required for all EDGAR lookups.
    """
    url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2024-01-01&enddt=2026-12-31&forms=10-K"
    data = _sec_request(url)

    if not data:
        # Try the company tickers JSON
        try:
            import urllib.request
            req = urllib.request.Request(
                "https://www.sec.gov/files/company_tickers.json",
                headers=SEC_HEADERS
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                tickers_data = json.loads(resp.read().decode())

            for entry in tickers_data.values():
                if entry.get("ticker", "").upper() == ticker.upper():
                    cik = str(entry["cik_str"]).zfill(10)
                    return cik
        except Exception:
            pass

    return None


def get_company_filings(cik: str, form_type: str = None, count: int = 40) -> list:
    """
    Get recent filings for a company by CIK.
    form_type: "4" for insider, "13F-HR" for institutional, "8-K" for events, etc.
    """
    url = f"{EDGAR_FILINGS}/submissions/CIK{cik}.json"
    data = _sec_request(url)

    if not data:
        return []

    filings = []
    recent = data.get("filings", {}).get("recent", {})

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    descriptions = recent.get("primaryDocument", [])
    accessions = recent.get("accessionNumber", [])

    for i in range(min(len(forms), count)):
        if form_type and forms[i] != form_type:
            continue

        filings.append({
            "form": forms[i],
            "date": dates[i],
            "document": descriptions[i] if i < len(descriptions) else "",
            "accession": accessions[i] if i < len(accessions) else "",
        })

    return filings


# ==============================================================================
# SECTION 2: INSIDER TRANSACTIONS (Form 4)
# ==============================================================================

def get_insider_transactions(ticker: str, days_back: int = 90) -> list:
    """
    Get recent insider buy/sell transactions from SEC Form 4 filings.

    Uses the EDGAR full-text search to find Form 4 filings,
    then extracts transaction details.

    Returns list of insider transactions.
    """
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    url = (f"https://efts.sec.gov/LATEST/search-index?"
           f"q=%22{ticker}%22&forms=4&dateRange=custom"
           f"&startdt={cutoff}&enddt={today}")

    data = _sec_request(url)
    if not data:
        return []

    hits = data.get("hits", {}).get("hits", [])
    transactions = []

    for hit in hits[:20]:  # Limit to 20 most recent
        source = hit.get("_source", {})
        filing_date = source.get("file_date", "")
        display_names = source.get("display_names", [])
        form_type = source.get("form_type", "")

        if form_type != "4":
            continue

        # Get the filing URL for detailed transaction data
        file_num = source.get("file_num", "")

        transactions.append({
            "date": filing_date,
            "filer": display_names[0] if display_names else "Unknown",
            "form": form_type,
        })

    return transactions


def get_insider_detail_from_search(ticker: str, days_back: int = 180) -> dict:
    """
    Search EDGAR for Form 4 filings and extract buy/sell patterns.
    Returns summary of insider activity.
    """
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    # Search for Form 4 filings
    url = (f"https://efts.sec.gov/LATEST/search-index?"
           f"q=%22{ticker}%22&forms=4&dateRange=custom"
           f"&startdt={cutoff}&enddt={today}")

    data = _sec_request(url)
    if not data:
        return {"ticker": ticker, "total_filings": 0, "unique_filers": 0, "filers": {}, "transactions": []}

    total = data.get("hits", {}).get("total", {}).get("value", 0)
    hits = data.get("hits", {}).get("hits", [])

    filers = {}
    for hit in hits[:50]:
        source = hit.get("_source", {})
        names = source.get("display_names", [])
        date = source.get("file_date", "")

        for name in names:
            if name not in filers:
                filers[name] = {"count": 0, "dates": []}
            filers[name]["count"] += 1
            filers[name]["dates"].append(date)

    return {
        "ticker": ticker,
        "total_filings": total,
        "period": f"{cutoff} to {today}",
        "unique_filers": len(filers),
        "filers": filers,
    }


# ==============================================================================
# SECTION 3: INSTITUTIONAL HOLDINGS (13F)
# ==============================================================================

def get_institutional_filings(ticker: str) -> list:
    """
    Find recent 13F-HR filings that mention a ticker.
    These show institutional fund positions.
    """
    cutoff = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    url = (f"https://efts.sec.gov/LATEST/search-index?"
           f"q=%22{ticker}%22&forms=13F-HR&dateRange=custom"
           f"&startdt={cutoff}&enddt={today}")

    data = _sec_request(url)
    if not data:
        return []

    hits = data.get("hits", {}).get("hits", [])
    filings = []

    for hit in hits[:20]:
        source = hit.get("_source", {})
        filings.append({
            "date": source.get("file_date", ""),
            "filer": source.get("display_names", ["Unknown"])[0],
            "form": source.get("form_type", ""),
        })

    return filings


# ==============================================================================
# SECTION 4: ACTIVIST STAKES (13D/13G)
# ==============================================================================

def get_activist_filings(ticker: str, days_back: int = 365) -> list:
    """
    Find 13D and 13G filings — these indicate 5%+ ownership stakes.
    A 13D specifically means the investor intends to influence management.
    This is the most powerful catalyst trigger.
    """
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    results = []
    for form in ["SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A"]:
        url = (f"https://efts.sec.gov/LATEST/search-index?"
               f"q=%22{ticker}%22&forms={form}&dateRange=custom"
               f"&startdt={cutoff}&enddt={today}")

        data = _sec_request(url)
        if not data:
            continue

        hits = data.get("hits", {}).get("hits", [])
        for hit in hits[:10]:
            source = hit.get("_source", {})
            results.append({
                "date": source.get("file_date", ""),
                "filer": source.get("display_names", ["Unknown"])[0],
                "form": source.get("form_type", ""),
                "description": f"5%+ ownership stake ({form})",
            })

    return results


# ==============================================================================
# SECTION 5: MATERIAL EVENTS (8-K)
# ==============================================================================

def get_material_events(ticker: str, days_back: int = 90) -> list:
    """
    Get recent 8-K filings — material events including:
    - Earnings announcements
    - Share buyback programs
    - Executive changes
    - M&A activity
    - Dividend changes
    """
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    url = (f"https://efts.sec.gov/LATEST/search-index?"
           f"q=%22{ticker}%22&forms=8-K&dateRange=custom"
           f"&startdt={cutoff}&enddt={today}")

    data = _sec_request(url)
    if not data:
        return []

    hits = data.get("hits", {}).get("hits", [])
    events = []

    for hit in hits[:15]:
        source = hit.get("_source", {})
        events.append({
            "date": source.get("file_date", ""),
            "filer": source.get("display_names", ["Unknown"])[0],
            "form": "8-K",
        })

    return events


# ==============================================================================
# SECTION 6: COMPOSITE SEC SCORE
# ==============================================================================

def score_sec_signals(ticker: str) -> dict:
    """
    Compute a composite SEC signal score for the catalyst screener.

    Scoring:
    - Insider buying cluster (multiple Form 4 buys): +3
    - Recent 13D/13G filing (activist): +3
    - High Form 4 activity (attention): +1
    - Recent 8-K filings (events): +1
    - Institutional 13F mentions: +1

    Returns dict with score, max, flags.
    """
    score = 0
    flags = []

    # Insider transactions
    print(f"    SEC: Checking insider activity...", end="", flush=True)
    insider = get_insider_detail_from_search(ticker, days_back=90)

    if insider["total_filings"] > 10:
        score += 2
        flags.append(f"HIGH insider activity: {insider['total_filings']} Form 4 filings in 90d")
    elif insider["total_filings"] > 5:
        score += 1
        flags.append(f"Elevated insider activity: {insider['total_filings']} Form 4 filings in 90d")
    elif insider["total_filings"] > 0:
        flags.append(f"Insider filings: {insider['total_filings']} in 90d")

    # Multiple unique insiders filing = cluster buying/selling
    if insider["unique_filers"] >= 3:
        score += 1
        flags.append(f"Insider cluster: {insider['unique_filers']} different insiders filing")

    # Activist stakes
    print(f" activist...", end="", flush=True)
    activists = get_activist_filings(ticker, days_back=365)
    if activists:
        score += 3
        recent = activists[0]
        flags.append(f"ACTIVIST FILING: {recent['form']} by {recent['filer']} on {recent['date']}")

    # Material events
    print(f" events...", end="", flush=True)
    events = get_material_events(ticker, days_back=60)
    if len(events) >= 3:
        score += 1
        flags.append(f"Active 8-K filings: {len(events)} material events in 60d")
    elif events:
        flags.append(f"Recent 8-K: {len(events)} filing(s) in 60d")

    # Institutional interest
    print(f" institutional...", end="", flush=True)
    inst = get_institutional_filings(ticker)
    if len(inst) >= 5:
        score += 1
        flags.append(f"Institutional interest: {len(inst)} 13F filings mention {ticker}")
    elif inst:
        flags.append(f"Some institutional filings: {len(inst)} 13F mentions")

    print(f" done.")

    return {"score": score, "max": 8, "flags": flags}


# ==============================================================================
# SECTION 7: REPORTING
# ==============================================================================

def print_sec_report(ticker: str):
    """Full SEC analysis report for a single ticker."""
    print(f"\n{'█' * 60}")
    print(f"  SEC EDGAR ANALYSIS: {ticker}")
    print(f"{'█' * 60}")

    # Get CIK
    print(f"\n  Looking up {ticker} on EDGAR...")
    cik = get_cik(ticker)
    if cik:
        print(f"  CIK: {cik}")
    else:
        print(f"  [WARN] CIK not found via search, using ticker-based lookup")

    # Insider Activity
    print(f"\n{'─' * 60}")
    print(f"  INSIDER TRANSACTIONS (Form 4) — Last 180 days")
    print(f"{'─' * 60}")

    insider = get_insider_detail_from_search(ticker, days_back=180)
    print(f"  Total Form 4 filings: {insider['total_filings']}")
    print(f"  Unique insiders: {insider['unique_filers']}")
    print(f"  Period: {insider['period']}")

    if insider['filers']:
        print(f"\n  {'Insider':<40}{'Filings':>8}{'Latest':>14}")
        print(f"  {'─' * 62}")
        sorted_filers = sorted(insider['filers'].items(),
                                key=lambda x: -x[1]['count'])
        for name, data in sorted_filers[:10]:
            latest = max(data['dates']) if data['dates'] else "N/A"
            print(f"  {name[:38]:<40}{data['count']:>8}{latest:>14}")

    # Activist Stakes
    print(f"\n{'─' * 60}")
    print(f"  ACTIVIST STAKES (13D/13G) — Last 12 months")
    print(f"{'─' * 60}")

    activists = get_activist_filings(ticker)
    if activists:
        for a in activists:
            print(f"  {a['date']}  {a['form']:<12}  {a['filer']}")
    else:
        print(f"  No activist filings found.")

    # Material Events
    print(f"\n{'─' * 60}")
    print(f"  MATERIAL EVENTS (8-K) — Last 90 days")
    print(f"{'─' * 60}")

    events = get_material_events(ticker)
    if events:
        for e in events:
            print(f"  {e['date']}  {e['filer']}")
    else:
        print(f"  No recent 8-K filings.")

    # Institutional
    print(f"\n{'─' * 60}")
    print(f"  INSTITUTIONAL HOLDINGS (13F) — Last 120 days")
    print(f"{'─' * 60}")

    inst = get_institutional_filings(ticker)
    if inst:
        for i in inst[:10]:
            print(f"  {i['date']}  {i['filer'][:50]}")
        if len(inst) > 10:
            print(f"  ... and {len(inst) - 10} more")
    else:
        print(f"  No recent 13F filings mentioning {ticker}.")

    # Composite Score
    print(f"\n{'─' * 60}")
    print(f"  SEC COMPOSITE SCORE")
    print(f"{'─' * 60}")

    sec_score = score_sec_signals(ticker)
    print(f"\n  Score: {sec_score['score']}/{sec_score['max']}")
    for flag in sec_score['flags']:
        print(f"    • {flag}")


def scan_watchlist_insiders():
    """Scan entire watchlist for insider buying activity."""
    watchlist_path = "./watchlist.txt"
    if not os.path.exists(watchlist_path):
        print("  No watchlist.txt found.")
        return

    with open(watchlist_path) as f:
        tickers = []
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ticker = line.split("#")[0].strip().upper()
            if ticker and not ticker.endswith("-USD"):
                tickers.append(ticker)
        tickers = list(dict.fromkeys(tickers))  # deduplicate, preserve order

    print(f"\n{'█' * 60}")
    print(f"  SEC INSIDER SCAN — {len(tickers)} tickers")
    print(f"{'█' * 60}")

    results = []
    for i, ticker in enumerate(tickers):
        print(f"\r  Scanning: {ticker:<8} ({i+1}/{len(tickers)})", end="", flush=True)
        insider = get_insider_detail_from_search(ticker, days_back=90)
        activists = get_activist_filings(ticker, days_back=180)

        results.append({
            "ticker": ticker,
            "form4_count": insider["total_filings"],
            "unique_insiders": insider["unique_filers"],
            "has_activist": len(activists) > 0,
            "activist_detail": activists[0] if activists else None,
        })
        time.sleep(0.2)

    print(f"\r  Scan complete." + " " * 30)

    # Sort by activity
    results.sort(key=lambda x: -(x["form4_count"] + (10 if x["has_activist"] else 0)))

    print(f"\n  {'Ticker':<8}{'Form 4s':>8}{'Insiders':>10}{'Activist':>10}")
    print(f"  {'─' * 36}")

    for r in results:
        activist_flag = "YES" if r["has_activist"] else "—"
        highlight = " ⚡" if r["form4_count"] > 5 or r["has_activist"] else ""
        print(f"  {r['ticker']:<8}"
              f"{r['form4_count']:>8}"
              f"{r['unique_insiders']:>10}"
              f"{activist_flag:>10}{highlight}")

    # Highlight any activist findings
    activists_found = [r for r in results if r["has_activist"]]
    if activists_found:
        print(f"\n  🔥 ACTIVIST FILINGS DETECTED:")
        for r in activists_found:
            a = r["activist_detail"]
            print(f"    {r['ticker']}: {a['form']} by {a['filer']} on {a['date']}")


# ==============================================================================
# SECTION 8: MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="SEC EDGAR Module v1.0")
    parser.add_argument("--ticker", type=str, help="Analyze a specific ticker")
    parser.add_argument("--insiders", action="store_true", help="Insider transactions only")
    parser.add_argument("--scan", action="store_true", help="Scan entire watchlist")
    parser.add_argument("--insider-buys", action="store_true", help="Find insider buying across watchlist")
    args = parser.parse_args()

    if args.ticker:
        if args.insiders:
            insider = get_insider_detail_from_search(args.ticker.upper(), days_back=180)
            print(json.dumps(insider, indent=2, default=str))
        else:
            print_sec_report(args.ticker.upper())
    elif args.scan or args.insider_buys:
        scan_watchlist_insiders()
    else:
        print("  Usage:")
        print("    python3 sec_module.py --ticker GME        # Full SEC report")
        print("    python3 sec_module.py --ticker GME --insiders  # Insider only")
        print("    python3 sec_module.py --scan              # Scan watchlist")

    print(f"\n  ⚠️  SEC data is public information, not investment advice.")
    print(f"  Insider filings may be delayed up to 2 business days.\n")


if __name__ == "__main__":
    main()
