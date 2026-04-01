#!/usr/bin/env python3
"""
================================================================================
EARNINGS TRANSCRIPT ANALYZER v1.0 — SEC EDGAR 8-K + Claude NLP
================================================================================
Fetches the most recent earnings call transcript from SEC EDGAR (8-K Item 2.02
/ 7.01 filings) and runs Claude API analysis to extract management sentiment,
guidance quality, and capital allocation signals.

EDGAR PATH:
    Ticker → CIK → submissions API → latest 8-K → download HTML → strip tags → text

CLAUDE ANALYSIS OUTPUTS:
    - tone_score          : -5 (very bearish) to +5 (very bullish)
    - tone_label          : BULLISH | NEUTRAL | BEARISH
    - guidance_direction  : RAISED | MAINTAINED | LOWERED | WITHDRAWN | NONE
    - guidance_confidence : HIGH | MEDIUM | LOW (management's conviction level)
    - capex_signal        : INCREASING | STABLE | CUTTING | NONE
    - buyback_signal      : ACTIVE | ANNOUNCED | NONE
    - key_quotes          : list of up to 3 significant CEO/CFO statements
    - management_summary  : 2-sentence synthesis
    - risks_mentioned     : list of risks management cited
    - catalysts_mentioned : list of catalysts management cited

CACHE: SQLite transcript_cache.db (7-day TTL — re-fetch after each earnings)

USAGE:
    python3 earnings_transcript.py --ticker AAPL
    python3 earnings_transcript.py --ticker NVDA --raw   # show transcript text
    python3 earnings_transcript.py --ticker MSFT --json  # JSON output
    python3 earnings_transcript.py --watchlist           # batch scan

COST: ~$0.02–0.05 per transcript (claude-sonnet-4-6, adaptive thinking off)
================================================================================
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import warnings
from datetime import datetime, timedelta
from typing import Optional

from utils.db import get_connection

warnings.filterwarnings("ignore")

try:
    from config import OUTPUT_DIR
except ImportError:
    OUTPUT_DIR = "./signals_output"

SEC_HEADERS = {
    "User-Agent": "SignalEngine/1.0 (educational research; contact@example.com)",
    "Accept": "application/json",
}
SEC_RATE_LIMIT = 0.15

CACHE_TTL_DAYS = 7    # Re-fetch after each earnings cycle
MAX_TRANSCRIPT_CHARS = 18_000  # ~4K tokens — enough for key sections


# ==============================================================================
# SECTION 0: CACHE (Supabase — global shared cache)
# ==============================================================================

def _get_cached(ticker: str) -> Optional[dict]:
    """Return cached analysis if less than CACHE_TTL_DAYS old."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cutoff = (datetime.now() - timedelta(days=CACHE_TTL_DAYS)).isoformat()
        cur.execute(
            "SELECT analysis_json, filing_date FROM transcript_cache "
            "WHERE ticker=%s AND created_at>%s ORDER BY filing_date DESC LIMIT 1",
            (ticker.upper(), cutoff),
        )
        row = cur.fetchone()
        conn.close()
        if row and row['analysis_json']:
            data = json.loads(row['analysis_json'])
            data["filing_date"] = row['filing_date']
            data["cached"] = True
            return data
    except Exception:
        pass
    return None


def _save_cache(ticker: str, filing_date: str, analysis: dict, snippet: str) -> None:
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO transcript_cache
               (ticker, filing_date, analysis_json, transcript_snippet, created_at)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT(ticker, filing_date) DO UPDATE SET
                   analysis_json=excluded.analysis_json,
                   transcript_snippet=excluded.transcript_snippet,
                   created_at=excluded.created_at""",
            (
                ticker.upper(),
                filing_date,
                json.dumps(analysis),
                snippet[:2000],
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ==============================================================================
# SECTION 1: EDGAR TRANSCRIPT RETRIEVAL
# ==============================================================================

def _edgar_get(url: str, accept_html: bool = False) -> Optional[str]:
    """Raw EDGAR fetch, returns text. Rate-limited."""
    try:
        headers = dict(SEC_HEADERS)
        if accept_html:
            headers["Accept"] = "text/html,application/xhtml+xml,text/plain"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
        time.sleep(SEC_RATE_LIMIT)
        return content
    except Exception:
        return None


def _edgar_json(url: str) -> Optional[dict]:
    """EDGAR fetch → parse JSON."""
    text = _edgar_get(url)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _get_cik(ticker: str) -> Optional[str]:
    """Ticker → 10-digit padded CIK."""
    try:
        text = _edgar_get("https://www.sec.gov/files/company_tickers.json")
        if not text:
            return None
        data = json.loads(text)
        tu = ticker.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == tu:
                return str(entry["cik_str"]).zfill(10)
    except Exception:
        pass
    return None


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode common entities."""
    # Remove script/style blocks
    html = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Replace <br>, <p>, <div>, <tr> with newlines
    html = re.sub(r"<(br|p|div|tr|li)[^>]*>", "\n", html, flags=re.IGNORECASE)
    # Strip remaining tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Decode entities
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&nbsp;", " ").replace("&#160;", " ").replace("&quot;", '"')
    # Collapse whitespace
    html = re.sub(r"\n{3,}", "\n\n", html)
    html = re.sub(r" {2,}", " ", html)
    return html.strip()


def _find_transcript_section(text: str) -> str:
    """
    Extract the earnings call transcript portion from an 8-K document.
    Heuristics: look for "Operator" lines, "Q&A", "prepared remarks", etc.
    Returns relevant excerpt capped at MAX_TRANSCRIPT_CHARS.
    """
    lines = text.split("\n")
    start_idx = 0

    # Find transcript start markers
    markers = [
        "operator", "good morning", "good afternoon", "good evening",
        "welcome to the", "thank you for joining", "prepared remarks",
        "opening remarks", "conference call", "earnings call",
        "q4 20", "q3 20", "q2 20", "q1 20", "fourth quarter", "third quarter",
        "second quarter", "first quarter", "fiscal year",
    ]
    for i, line in enumerate(lines):
        ll = line.lower().strip()
        if any(m in ll for m in markers) and len(ll) > 10:
            start_idx = max(0, i - 2)
            break

    # Take from transcript start to max length
    excerpt = "\n".join(lines[start_idx:])
    return excerpt[:MAX_TRANSCRIPT_CHARS]


def fetch_transcript(ticker: str) -> Optional[dict]:
    """
    Fetch the most recent earnings call transcript from EDGAR 8-K.

    Returns dict with: {text, filing_date, accession_number, url}
    or None if not found.
    """
    cik = _get_cik(ticker)
    if not cik:
        return None

    # Get recent filings
    subs = _edgar_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    if not subs:
        return None

    recent = subs.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    items_list = recent.get("items", [])

    # Find 8-K filings with Items 2.02 or 7.01 (earnings disclosure)
    candidate_indices = []
    for i, (form, items) in enumerate(zip(forms, items_list)):
        if form.upper() != "8-K":
            continue
        items_str = str(items or "").lower()
        if "2.02" in items_str or "7.01" in items_str:
            candidate_indices.append(i)

    if not candidate_indices:
        # Fallback: take the 3 most recent 8-K filings and look for transcripts
        for i, form in enumerate(forms):
            if form.upper() == "8-K":
                candidate_indices.append(i)
            if len(candidate_indices) >= 3:
                break

    for idx in candidate_indices[:3]:
        accn = accns[idx].replace("-", "")
        filing_date = dates[idx]
        primary_doc = docs[idx]

        # Try to find transcript exhibit in the filing index
        index_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn}/{primary_doc}"
        # Also try filing-summary or index JSON for additional docs
        index_json_url = f"https://data.sec.gov/submissions/CIK{cik}.json"

        content = _edgar_get(index_url, accept_html=True)
        if not content:
            continue

        stripped = _strip_html(content)
        # Check if this looks like a transcript (has dialogue markers)
        has_transcript = any(
            kw in stripped.lower()
            for kw in ("operator", "prepared remarks", "earnings call", "conference call",
                       "ceo", "chief executive", "chief financial", "questions and answers")
        )

        if has_transcript or len(stripped) > 3000:
            excerpt = _find_transcript_section(stripped)
            if len(excerpt) > 500:
                return {
                    "text": excerpt,
                    "full_text_len": len(stripped),
                    "filing_date": filing_date,
                    "accession_number": accns[idx],
                    "url": index_url,
                }

    return None


# ==============================================================================
# SECTION 2: CLAUDE ANALYSIS
# ==============================================================================

_ANALYSIS_PROMPT = """You are a quantitative analyst reviewing an earnings call transcript excerpt.

Analyze the following transcript and return a JSON object with EXACTLY these fields:

{{
  "tone_score": <integer -5 to +5, where -5=very bearish, 0=neutral, +5=very bullish>,
  "tone_label": "<BULLISH|NEUTRAL|BEARISH>",
  "guidance_direction": "<RAISED|MAINTAINED|LOWERED|WITHDRAWN|NONE>",
  "guidance_confidence": "<HIGH|MEDIUM|LOW>",
  "capex_signal": "<INCREASING|STABLE|CUTTING|NONE>",
  "buyback_signal": "<ACTIVE|ANNOUNCED|NONE>",
  "key_quotes": ["<quote 1>", "<quote 2>", "<quote 3>"],
  "management_summary": "<2 sentences: what management said about the business and outlook>",
  "risks_mentioned": ["<risk 1>", "<risk 2>", "<risk 3>"],
  "catalysts_mentioned": ["<catalyst 1>", "<catalyst 2>", "<catalyst 3>"]
}}

Rules:
- key_quotes must be direct verbatim quotes from the transcript (max 120 chars each)
- risks/catalysts: extract from management language, max 3 each
- guidance_confidence: HIGH=gave specific numbers, MEDIUM=directional only, LOW=vague/avoided
- tone_score: be calibrated — most calls are +1 to +2; reserve +4/+5 for exceptional beats

Transcript for {ticker}:
---
{transcript}
---

Return ONLY the JSON object, no other text."""


def analyze_transcript_with_claude(ticker: str, transcript_text: str) -> dict:
    """
    Send transcript excerpt to Claude API for NLP analysis.
    Returns parsed analysis dict. Falls back to a neutral template on failure.
    """
    neutral_fallback = {
        "tone_score": 0,
        "tone_label": "NEUTRAL",
        "guidance_direction": "NONE",
        "guidance_confidence": "LOW",
        "capex_signal": "NONE",
        "buyback_signal": "NONE",
        "key_quotes": [],
        "management_summary": "Transcript analysis unavailable.",
        "risks_mentioned": [],
        "catalysts_mentioned": [],
        "analysis_source": "fallback",
    }

    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return {**neutral_fallback, "error": "ANTHROPIC_API_KEY not set"}

        client = anthropic.Anthropic(api_key=api_key)
        prompt = _ANALYSIS_PROMPT.format(
            ticker=ticker.upper(),
            transcript=transcript_text[:MAX_TRANSCRIPT_CHARS],
        )

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = response.content[0].text.strip()
        # Extract JSON from response
        json_match = re.search(r"\{[\s\S]*\}", raw_text)
        if json_match:
            result = json.loads(json_match.group())
            result["analysis_source"] = "claude-sonnet-4-6"
            return result
        return {**neutral_fallback, "error": "Could not parse Claude JSON response"}

    except ImportError:
        return {**neutral_fallback, "error": "anthropic package not installed"}
    except Exception as e:
        return {**neutral_fallback, "error": str(e)[:80]}


# ==============================================================================
# SECTION 3: MAIN ENTRY POINT
# ==============================================================================

def get_transcript_signals(ticker: str, use_cache: bool = True) -> dict:
    """
    Full pipeline: fetch transcript → Claude analysis → return signals dict.

    Returns a signals dict suitable for injection into ai_quant collect_all_signals().
    Always returns a dict; degrades gracefully if transcript unavailable.
    """
    ticker = ticker.upper()

    # Check cache
    if use_cache:
        cached = _get_cached(ticker)
        if cached:
            return cached

    # Fetch transcript from EDGAR
    transcript_data = fetch_transcript(ticker)
    if not transcript_data:
        return {
            "ticker": ticker,
            "transcript_available": False,
            "tone_score": None,
            "tone_label": "NEUTRAL",
            "guidance_direction": "NONE",
            "guidance_confidence": "LOW",
            "capex_signal": "NONE",
            "buyback_signal": "NONE",
            "key_quotes": [],
            "management_summary": "Earnings transcript not found in EDGAR 8-K filings.",
            "risks_mentioned": [],
            "catalysts_mentioned": [],
            "filing_date": None,
            "cached": False,
        }

    # Run Claude analysis
    analysis = analyze_transcript_with_claude(ticker, transcript_data["text"])

    result = {
        "ticker": ticker,
        "transcript_available": True,
        "filing_date": transcript_data["filing_date"],
        "transcript_url": transcript_data["url"],
        "transcript_char_count": transcript_data["full_text_len"],
        "cached": False,
        **analysis,
    }

    # Save to cache
    _save_cache(ticker, transcript_data["filing_date"], result, transcript_data["text"][:2000])
    return result


# ==============================================================================
# CLI
# ==============================================================================

def _read_watchlist(path: str = "./watchlist.txt") -> list:
    tickers = []
    try:
        with open(path) as f:
            for line in f:
                l = line.strip()
                if not l or l.startswith("#"):
                    continue
                t = l.split("#")[0].strip().upper()
                if t and not t.endswith("-USD"):
                    tickers.append(t)
    except FileNotFoundError:
        pass
    return list(dict.fromkeys(tickers))


def main():
    parser = argparse.ArgumentParser(description="Earnings Transcript Analyzer v1.0")
    parser.add_argument("--ticker", type=str)
    parser.add_argument("--tickers", type=str, help="Comma-separated")
    parser.add_argument("--watchlist", action="store_true")
    parser.add_argument("--raw", action="store_true", help="Show raw transcript text")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    elif args.watchlist:
        tickers = _read_watchlist()
    else:
        parser.print_help()
        return

    for ticker in tickers:
        print(f"\n  Fetching transcript for {ticker}...", end="", flush=True)

        if args.raw:
            t = fetch_transcript(ticker)
            if t:
                print(f"\n  Filing: {t['filing_date']}")
                print(f"  URL: {t['url']}")
                print(f"\n{'─' * 60}")
                print(t["text"][:3000])
                print(f"{'─' * 60}")
            else:
                print(f"\n  No transcript found for {ticker}")
            continue

        result = get_transcript_signals(ticker, use_cache=not args.no_cache)

        if args.json:
            print(f"\n{json.dumps(result, indent=2)}")
            continue

        cached_tag = " [cached]" if result.get("cached") else ""
        print(f"\r  {ticker} — {result.get('filing_date', 'no transcript')}{cached_tag}    ")
        print(f"{'─' * 60}")

        if not result.get("transcript_available"):
            print(f"  No transcript found in EDGAR 8-K filings.")
            continue

        tone = result.get("tone_label", "NEUTRAL")
        score = result.get("tone_score", 0)
        guidance = result.get("guidance_direction", "NONE")
        icon = "🟢" if tone == "BULLISH" else ("🔴" if tone == "BEARISH" else "🟡")

        print(f"  Tone          : {icon} {tone}  (score: {score:+d}/5)")
        print(f"  Guidance      : {guidance}  (confidence: {result.get('guidance_confidence')})")
        print(f"  CapEx         : {result.get('capex_signal')}")
        print(f"  Buybacks      : {result.get('buyback_signal')}")
        print(f"\n  Summary:")
        print(f"    {result.get('management_summary', '')}")

        if result.get("key_quotes"):
            print(f"\n  Key Quotes:")
            for q in result["key_quotes"][:3]:
                print(f"    \"{q}\"")

        if result.get("catalysts_mentioned"):
            print(f"\n  Mgmt Catalysts: {', '.join(result['catalysts_mentioned'][:3])}")
        if result.get("risks_mentioned"):
            print(f"  Mgmt Risks:     {', '.join(result['risks_mentioned'][:3])}")

        if result.get("error"):
            print(f"\n  [WARN] Claude error: {result['error']}")

    print()


if __name__ == "__main__":
    main()
