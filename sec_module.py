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
import re
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

# Filing classification sets
_OWNERSHIP_FORMS = frozenset({"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"})
_DILUTION_FORMS  = frozenset({"424B5", "S-3", "S-3ASR", "F-3"})

# Threshold: pct_class >= this → large_holder_flag
_LARGE_HOLDER_PCT_THRESHOLD = 10.0


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
# SECTION 4: FILING CATALYST PARSERS  (CHUNK-07)
# Pure functions — unit-testable without live EDGAR calls.
# ==============================================================================

def _parse_pct_class(text: str) -> Optional[float]:
    """
    Extract the percent-of-class figure from 13D/13G filing text.

    Looks for patterns such as:
        Percent of class: 22.2%
        Percent of Class Represented by Amount in Row (11): 22.2
        aggregate percent of class: 22.2%
    """
    patterns = [
        r"percent\s+of\s+class\s*(?:represented\s+by\s+amount\s+in\s+row\s*\(?1[12]\)?)?\s*[:\-]?\s*([\d,]+\.?\d*)\s*%?",
        r"(?:aggregate\s+)?percent\s+of\s+(?:the\s+)?(?:outstanding\s+)?(?:shares\s+of\s+)?(?:common\s+)?(?:stock|class)(?:\s+of\s+\w+)?\s*[:\-]?\s*([\d,]+\.?\d*)\s*%?",
        r"\(1[12]\)\s*([\d,]+\.?\d*)\s*%",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                if 0 < val <= 100:
                    return val
            except (ValueError, AttributeError):
                continue
    return None


def _parse_beneficial_shares(text: str) -> Optional[int]:
    """
    Extract shares beneficially owned from 13D/13G filing text.

    Looks for patterns such as:
        Amount beneficially owned: 7,824,100
        aggregate amount beneficially owned: 7,824,100
        Sole voting power: 7,824,100
    """
    patterns = [
        r"(?:aggregate\s+)?amount\s+beneficially\s+owned\s*[:\-]?\s*([\d,]+)",
        r"(?:total\s+)?shares\s+beneficially\s+owned\s*[:\-]?\s*([\d,]+)",
        r"(?:11\)|row\s+11)\s*[:\-]?\s*([\d,]+)",
        r"sole\s+(?:voting|dispositive)\s+power\s*[:\-]?\s*([\d,]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                val = int(m.group(1).replace(",", ""))
                if val > 0:
                    return val
            except (ValueError, AttributeError):
                continue
    return None


def _parse_shares_offered(text: str) -> Optional[int]:
    """
    Extract the number of shares offered from a dilution filing.

    Looks for patterns such as:
        up to 5,000,000 shares
        up to 5 million shares
        offer and sell shares of common stock having an aggregate offering price of up to $100,000,000
        an aggregate of 3,500,000 shares
    """
    # Named-number patterns (e.g. "5 million")
    _WORD_NUMS = {
        "hundred": 100, "thousand": 1_000, "million": 1_000_000,
        "billion": 1_000_000_000,
    }
    # Try numeric first
    patterns = [
        r"up\s+to\s+([\d,]+)\s+shares",
        r"aggregate\s+(?:amount\s+)?of\s+([\d,]+)\s+shares",
        r"offer(?:ing)?\s+(?:and\s+sell\s+)?(?:up\s+to\s+)?([\d,]+)\s+shares",
        r"issuance\s+of\s+(?:up\s+to\s+)?([\d,]+)\s+shares",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                val = int(m.group(1).replace(",", ""))
                if val > 0:
                    return val
            except (ValueError, AttributeError):
                continue

    # Word-number patterns: "up to 5 million shares"
    m = re.search(
        r"up\s+to\s+([\d,.]+)\s+(hundred|thousand|million|billion)\s+shares",
        text, re.IGNORECASE,
    )
    if m:
        try:
            base = float(m.group(1).replace(",", ""))
            mult = _WORD_NUMS.get(m.group(2).lower(), 1)
            val = int(base * mult)
            if val > 0:
                return val
        except (ValueError, AttributeError):
            pass

    return None


def _detect_derivative_exposure(text: str) -> bool:
    """
    Return True if the text clearly mentions derivative instruments such as
    call options, warrants, or swaps.

    Conservative: only flags obvious derivative language.
    Does not estimate economic exposure.
    """
    patterns = [
        r"\bcall\s+options?\b",
        r"\bwarrants?\b",
        r"\bswaps?\b",
        r"\bderivatives?\b",
        r"\boptions?\s+exercisable\b",
        r"\bshares?\s+issuable\s+upon\s+exercise\b",
        r"\bconvertible\s+(?:notes?|securities|preferred)\b",
    ]
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


def _detect_atm_language(text: str) -> bool:
    """
    Return True if the text contains at-the-market or equity distribution language
    indicative of a dilution event.
    """
    patterns = [
        r"\bat[\s\-]the[\s\-]market\b",
        r"\batm\s+offering\b",
        r"\bsales\s+agreement\b",
        r"\bequity\s+distribution\s+agreement\b",
        r"\bprospectus\s+supplement\b",
        r"\bmay\s+offer\s+and\s+sell\b",
        r"\bshelf\s+registration\b",
        r"\baggregate\s+offering\s+(?:price|proceeds)\b",
    ]
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


def classify_filing(form_type: str, title: str = "", text: str = "") -> dict:
    """
    Classify a SEC filing and return a dict of flags and parsed fields.

    Returns:
        ownership_accumulation_flag  — True for 13D/13G family
        dilution_risk_flag           — True for 424B5/S-3 or ATM language
        derivative_exposure_flag     — True if derivative language detected
        large_holder_flag            — True if pct_class >= _LARGE_HOLDER_PCT_THRESHOLD
        pct_class                    — float | None
        shares_beneficially_owned    — int | None
        shares_offered               — int | None
    """
    combined = f"{title} {text}"
    form_upper = (form_type or "").upper().strip()

    ownership_flag = form_upper in {f.upper() for f in _OWNERSHIP_FORMS}
    dilution_flag = (form_upper in {f.upper() for f in _DILUTION_FORMS}
                     or _detect_atm_language(combined))

    pct_class = _parse_pct_class(combined) if ownership_flag or text else None
    shares_owned = _parse_beneficial_shares(combined) if ownership_flag or text else None
    shares_offered = _parse_shares_offered(combined) if dilution_flag or text else None
    deriv_flag = _detect_derivative_exposure(combined)
    large_flag = (pct_class is not None and pct_class >= _LARGE_HOLDER_PCT_THRESHOLD)

    return {
        "ownership_accumulation_flag": ownership_flag,
        "dilution_risk_flag": dilution_flag,
        "derivative_exposure_flag": deriv_flag,
        "large_holder_flag": large_flag,
        "pct_class": pct_class,
        "shares_beneficially_owned": shares_owned,
        "shares_offered": shares_offered,
    }


# ==============================================================================
# SECTION 4B: ACTIVIST STAKES (13D/13G)  — extended for CHUNK-07
# ==============================================================================

def get_activist_filings(ticker: str, days_back: int = 365) -> list:
    """
    Find 13D and 13G filings — 5%+ ownership stakes and activist positions.
    Extended (CHUNK-07): adds accession_number, source_url, holder_name,
    ownership_accumulation_flag, large_holder_flag, derivative_exposure_flag,
    and parsed pct_class / shares_beneficially_owned where available in the
    EDGAR search hit metadata.
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
            filing_date = source.get("file_date", "")
            form_type = source.get("form_type", form)
            display_names = source.get("display_names", [])
            holder_name = display_names[0] if display_names else "Unknown"
            accession_no = source.get("accession_no", "") or hit.get("_id", "")

            # Build a direct EDGAR search link using the accession number
            source_url = (
                f"https://efts.sec.gov/LATEST/search-index?q=%22{accession_no}%22"
                if accession_no else
                f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms={form}"
            )

            # Classify using form type (text not fetched at this stage for speed)
            flags = classify_filing(form_type, title="")

            results.append({
                "date": filing_date,
                "filer": holder_name,
                "form": form_type,
                "description": f"5%+ ownership stake ({form_type})",
                # Extended fields for CHUNK-07
                "accession_number": accession_no,
                "holder_name": holder_name,
                "source_url": source_url,
                "ownership_accumulation_flag": flags["ownership_accumulation_flag"],
                "large_holder_flag": flags["large_holder_flag"],
                "derivative_exposure_flag": flags["derivative_exposure_flag"],
                "pct_class": flags["pct_class"],
                "shares_beneficially_owned": flags["shares_beneficially_owned"],
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
# SECTION 5B: DILUTION FILINGS (424B5 / S-3)  — CHUNK-07
# ==============================================================================

def get_dilution_filings(ticker: str, days_back: int = 365) -> list:
    """
    Find dilution-risk filings: 424B5, S-3, S-3ASR, F-3.

    These forms indicate that the issuer is selling new shares (or has a
    shelf registration that enables future sales). They are relevant context
    for squeeze candidates: high short interest + imminent dilution risk can
    suppress a squeeze before it ignites.

    Returns a list of dicts with dilution_risk_flag, shares_offered, and
    other metadata. Callers should persist via build_filing_catalyst_records().

    Note: does NOT change the squeeze score — that is CHUNK-16's job.
    """
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    results = []
    for form in ["424B5", "S-3", "S-3ASR", "F-3"]:
        url = (f"https://efts.sec.gov/LATEST/search-index?"
               f"q=%22{ticker}%22&forms={form}&dateRange=custom"
               f"&startdt={cutoff}&enddt={today}")

        data = _sec_request(url)
        if not data:
            continue

        hits = data.get("hits", {}).get("hits", [])
        for hit in hits[:10]:
            source = hit.get("_source", {})
            filing_date = source.get("file_date", "")
            form_type = source.get("form_type", form)
            display_names = source.get("display_names", [])
            issuer = display_names[0] if display_names else ticker
            accession_no = source.get("accession_no", "") or hit.get("_id", "")

            # Use the filing description/entity_name as a lightweight title proxy
            title_proxy = source.get("entity_name", "") or source.get("description", "")
            flags = classify_filing(form_type, title=title_proxy)

            source_url = (
                f"https://efts.sec.gov/LATEST/search-index?q=%22{accession_no}%22"
                if accession_no else
                f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms={form}"
            )

            results.append({
                "date": filing_date,
                "filer": issuer,
                "form": form_type,
                "description": f"Potential dilution filing ({form_type})",
                "accession_number": accession_no,
                "issuer": issuer,
                "source_url": source_url,
                "dilution_risk_flag": flags["dilution_risk_flag"],
                "shares_offered": flags["shares_offered"],
                "derivative_exposure_flag": flags["derivative_exposure_flag"],
            })

    return results


def build_filing_catalyst_records(
    ticker: str,
    activist_filings: list,
    dilution_filings: list,
) -> list:
    """
    Normalize activist and dilution filing lists into filing_catalysts records
    ready for persistence via save_filing_catalysts().

    Called after get_activist_filings() and get_dilution_filings() to produce
    a single list of records with a consistent schema.
    """
    from datetime import datetime as _dt
    records = []
    now_ts = _dt.utcnow().isoformat() + "Z"

    for f in activist_filings:
        records.append({
            "ticker": ticker,
            "filing_date": f.get("date", ""),
            "event_date": f.get("date", ""),
            "filing_type": f.get("form", ""),
            "accession_number": f.get("accession_number"),
            "issuer": ticker,
            "holder_name": f.get("holder_name") or f.get("filer"),
            "summary": f.get("description", ""),
            "ownership_accumulation_flag": f.get("ownership_accumulation_flag", True),
            "dilution_risk_flag": False,
            "derivative_exposure_flag": f.get("derivative_exposure_flag", False),
            "large_holder_flag": f.get("large_holder_flag", False),
            "shares_beneficially_owned": f.get("shares_beneficially_owned"),
            "pct_class": f.get("pct_class"),
            "shares_offered": None,
            "source_url": f.get("source_url"),
            "source": "edgar_search",
            "source_timestamp": now_ts,
        })

    for f in dilution_filings:
        records.append({
            "ticker": ticker,
            "filing_date": f.get("date", ""),
            "event_date": f.get("date", ""),
            "filing_type": f.get("form", ""),
            "accession_number": f.get("accession_number"),
            "issuer": f.get("issuer", ticker),
            "holder_name": None,
            "summary": f.get("description", ""),
            "ownership_accumulation_flag": False,
            "dilution_risk_flag": f.get("dilution_risk_flag", True),
            "derivative_exposure_flag": f.get("derivative_exposure_flag", False),
            "large_holder_flag": False,
            "shares_beneficially_owned": None,
            "pct_class": None,
            "shares_offered": f.get("shares_offered"),
            "source_url": f.get("source_url"),
            "source": "edgar_search",
            "source_timestamp": now_ts,
        })

    return records


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

    Extended (CHUNK-07):
    - Checks for 424B5/S-3 dilution filings (informational only — no score change)
    - Persists all detected catalysts to filing_catalysts table
    - Returns dilution_risk_flag and ownership_accumulation_flag in output dict

    Returns dict with score, max, flags, dilution_risk_flag, ownership_accumulation_flag.
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

    # Activist stakes (13D/13G) — extended with ownership parsing
    print(f" activist...", end="", flush=True)
    activists = get_activist_filings(ticker, days_back=365)
    ownership_accumulation_flag = len(activists) > 0
    if activists:
        score += 3
        recent = activists[0]
        flags.append(f"ACTIVIST FILING: {recent['form']} by {recent['filer']} on {recent['date']}")
        if recent.get("pct_class"):
            flags.append(f"  Ownership: {recent['pct_class']:.1f}% of class")
        if recent.get("large_holder_flag"):
            flags.append(f"  LARGE HOLDER (≥{_LARGE_HOLDER_PCT_THRESHOLD:.0f}%)")

    # Dilution risk (424B5 / S-3 / S-3ASR) — informational, no score change
    print(f" dilution...", end="", flush=True)
    dilution_filings = get_dilution_filings(ticker, days_back=365)
    dilution_risk_flag = any(f.get("dilution_risk_flag") for f in dilution_filings)
    if dilution_risk_flag:
        recent_dil = dilution_filings[0]
        flags.append(
            f"DILUTION RISK: {recent_dil['form']} filed {recent_dil.get('date', 'N/A')} "
            f"(informational — may suppress squeeze)"
        )
        if recent_dil.get("shares_offered"):
            flags.append(f"  Shares offered: {recent_dil['shares_offered']:,}")

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

    # Persist all detected catalysts (non-fatal)
    try:
        from utils.supabase_persist import save_filing_catalysts
        catalyst_records = build_filing_catalyst_records(ticker, activists, dilution_filings)
        if catalyst_records:
            save_filing_catalysts(catalyst_records)
    except Exception as _exc:
        pass  # non-fatal

    print(f" done.")

    return {
        "score": score,
        "max": 8,
        "flags": flags,
        "dilution_risk_flag": dilution_risk_flag,
        "ownership_accumulation_flag": ownership_accumulation_flag,
    }


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
