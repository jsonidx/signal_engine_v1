#!/usr/bin/env python3
"""
================================================================================
POLYMARKET SCREENER v1.0 — Prediction Market Signal Extractor
================================================================================
Fetches real-time prediction market data from Polymarket's Gamma API and
converts crowd-sourced probabilities into catalyst confirmation signals.

WHAT IT DOES:
    Fetches active markets from Polymarket (free, no auth required)
    Matches markets to catalyst triggers (earnings, M&A, economic, crypto)
    Extracts implied probabilities and trading volume as signal strength
    Scores markets on 0–1 scale: consensus + volume + liquidity + timing
    Tracks trend via cache comparison (is the crowd getting more bullish?)
    Integrates with existing catalyst_screener.py pipeline

DATA SOURCE:
    Gamma API: https://gamma-api.polymarket.com/markets
    - No authentication required. Rate limit: ~1,000 calls/hour.
    - Key fields: outcomePrices (implied probabilities), volume24hr, liquidity

SIGNAL SCORE COMPONENTS (0–1 scale):
    0.30  Consensus strength   (probability far from 50/50)
    0.25  24h trading volume   (market activity / reliability)
    0.25  Liquidity depth      (order book quality)
    0.20  Time to resolution   (relevance to catalyst window)

USAGE:
    python3 polymarket_screener.py                       # Screen all catalysts
    python3 polymarket_screener.py --ticker TSLA         # Match TSLA markets
    python3 polymarket_screener.py --query "Fed rate"    # Free-form search
    python3 polymarket_screener.py --type earnings       # Filter by type
    python3 polymarket_screener.py --top 15 --export     # Export top 15
    python3 polymarket_screener.py --refresh             # Force cache refresh

IMPORTANT: Prediction markets reflect crowd probability, not certainty.
           These are SIGNALS, not forecasts. Size positions accordingly.
================================================================================
"""

import argparse
import json
import os
import sys
import time
import warnings
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import urllib.request
import urllib.parse
import urllib.error

warnings.filterwarnings("ignore")

try:
    from config import OUTPUT_DIR, POLYMARKET_PARAMS
except ImportError:
    OUTPUT_DIR = "./signals_output"
    POLYMARKET_PARAMS = {}

# ── defaults (overridden by config) ──────────────────────────────────────────
_P = POLYMARKET_PARAMS
API_BASE        = _P.get("api_base_url",        "https://gamma-api.polymarket.com")
CACHE_FILE      = _P.get("cache_file",           "polymarket_cache.json")
CACHE_TTL_H     = _P.get("cache_ttl_hours",      1)
MAX_MARKETS     = _P.get("max_markets_fetch",     500)
PAGE_SIZE       = _P.get("page_size",             100)
REQ_TIMEOUT     = _P.get("request_timeout",       15)
REQ_DELAY       = _P.get("request_delay",         0.5)
MIN_VOL_24H     = _P.get("min_volume_24h",        500)
MIN_LIQ         = _P.get("min_liquidity",         500)
MIN_DAYS_RES    = _P.get("min_days_to_resolution",1)
MAX_DAYS_RES    = _P.get("max_days_to_resolution",180)
STRONG_HIGH     = _P.get("strong_consensus_high", 0.70)
STRONG_LOW      = _P.get("strong_consensus_low",  0.30)
MOD_HIGH        = _P.get("moderate_consensus_high",0.60)
MOD_LOW         = _P.get("moderate_consensus_low", 0.40)
VOL_HIGH        = _P.get("volume_high",           50_000)
VOL_MED         = _P.get("volume_medium",         10_000)
VOL_LOW         = _P.get("volume_low",            1_000)
LIQ_HIGH        = _P.get("liquidity_high",        100_000)
LIQ_MED         = _P.get("liquidity_medium",      10_000)
LIQ_LOW         = _P.get("liquidity_low",         1_000)


# ==============================================================================
# TICKER → KEYWORD MAPPING
# ==============================================================================
# Maps equity tickers to search phrases used in Polymarket market questions.
# Add or extend freely — the screener does substring matching (case-insensitive).

TICKER_KEYWORDS: Dict[str, List[str]] = {
    # ── US Mega / Large Cap ────────────────────────────────────────────────────
    "AAPL":  ["apple", "iphone", "apple earnings"],
    "MSFT":  ["microsoft", "azure", "microsoft earnings"],
    "GOOGL": ["google", "alphabet", "google earnings"],
    "AMZN":  ["amazon", "aws", "amazon earnings"],
    "NVDA":  ["nvidia", "nvda", "nvidia earnings"],
    "META":  ["meta", "facebook", "meta earnings"],
    "TSLA":  ["tesla", "tsla", "elon musk", "tesla earnings"],
    "NFLX":  ["netflix", "nflx", "netflix earnings"],
    "BRK-B": ["berkshire"],
    "JPM":   ["jpmorgan", "jp morgan"],
    "GS":    ["goldman sachs", "goldman"],
    "MS":    ["morgan stanley"],
    "BAC":   ["bank of america"],
    "WFC":   ["wells fargo"],
    "INTC":  ["intel", "intc"],
    "AMD":   ["amd", "advanced micro devices", "amd earnings"],
    "CRM":   ["salesforce", "crm"],
    "ORCL":  ["oracle"],
    "IBM":   ["ibm"],
    "QCOM":  ["qualcomm"],
    "AVGO":  ["broadcom"],
    "TXN":   ["texas instruments"],
    # ── Growth / Tech ──────────────────────────────────────────────────────────
    "PLTR":  ["palantir", "pltr"],
    "COIN":  ["coinbase", "coin earnings"],
    "HOOD":  ["robinhood"],
    "SOFI":  ["sofi"],
    "SNOW":  ["snowflake"],
    "NET":   ["cloudflare"],
    "CRWD":  ["crowdstrike"],
    "DDOG":  ["datadog"],
    "ZS":    ["zscaler"],
    "AFRM":  ["affirm"],
    "UPST":  ["upstart"],
    "RBLX":  ["roblox"],
    "RIVN":  ["rivian"],
    "LCID":  ["lucid motors", "lucid"],
    "NIO":   ["nio"],
    "SMCI":  ["supermicro", "smci"],
    "ARM":   ["arm holdings", "arm ipo"],
    "SHOP":  ["shopify"],
    "SQ":    ["block", "square", "cash app"],
    # ── Crypto ────────────────────────────────────────────────────────────────
    "BTC-USD": ["bitcoin", "btc", "bitcoin price"],
    "ETH-USD": ["ethereum", "eth", "ethereum price"],
    "SOL-USD": ["solana", "sol price"],
    "XRP-USD": ["xrp", "ripple"],
    "BNB-USD": ["bnb", "binance coin"],
    "ADA-USD": ["cardano", "ada"],
    "AVAX-USD":["avalanche", "avax"],
    "MATIC-USD":["polygon", "matic"],
    "LINK-USD": ["chainlink", "link"],
    "DOGE-USD": ["dogecoin", "doge"],
    # ── Mining / Crypto Proxies ───────────────────────────────────────────────
    "MARA":  ["marathon digital", "mara"],
    "RIOT":  ["riot platforms", "riot"],
    "MSTR":  ["microstrategy", "michael saylor"],
}

# Maps catalyst type → relevant search keywords for un-tickered catalysts
CATALYST_TYPE_KEYWORDS: Dict[str, List[str]] = {
    "earnings":    ["earnings", "beat", "eps", "revenue", "quarterly results"],
    "fed":         ["federal reserve", "fomc", "rate cut", "rate hike",
                    "fed rate", "interest rate", "powell"],
    "inflation":   ["cpi", "pce", "inflation", "consumer price"],
    "jobs":        ["jobs report", "nonfarm payrolls", "unemployment", "jobless claims"],
    "gdp":         ["gdp", "economic growth", "recession"],
    "crypto":      ["bitcoin", "ethereum", "crypto", "defi", "nft"],
    "ma":          ["acquisition", "merger", "buyout", "takeover", "deal closes"],
    "regulatory":  ["fda approval", "fda", "sec", "antitrust", "regulation"],
    "election":    ["election", "president", "congress", "senate", "vote"],
    "ipo":         ["ipo", "direct listing", "spac"],
    "geopolitical":["war", "sanctions", "tariff", "trade war"],
}


# ==============================================================================
# SECTION 1: CACHE MANAGEMENT
# ==============================================================================

def _load_cache() -> dict:
    """Load Polymarket market cache from disk."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"markets": [], "timestamp": None, "price_history": {}}


def _save_cache(cache: dict) -> None:
    """Persist cache to disk (silently fails if disk is unavailable)."""
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, separators=(",", ":"))
    except Exception:
        pass


def _is_cache_fresh(cache: dict) -> bool:
    """Return True if the cache was populated within the TTL window."""
    ts = cache.get("timestamp")
    if not ts or not cache.get("markets"):
        return False
    try:
        cached_at = datetime.fromisoformat(ts)
        age_hours = (datetime.now() - cached_at).total_seconds() / 3600
        return age_hours < CACHE_TTL_H
    except Exception:
        return False


# ==============================================================================
# SECTION 2: GAMMA API FETCHING
# ==============================================================================

def _parse_json_field(value) -> list:
    """
    Gamma API returns outcomes/outcomePrices as either a Python list
    (when already decoded) or a JSON string that needs a second parse.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return []


def _fetch_page(offset: int = 0) -> List[dict]:
    """
    Fetch one page of active, open markets from the Gamma API.
    Returns a list of raw market dicts (empty list on any error).
    """
    params = {
        "active":    "true",
        "closed":    "false",
        "limit":     str(PAGE_SIZE),
        "offset":    str(offset),
        "order":     "volume24hr",
        "ascending": "false",
    }
    url = f"{API_BASE}/markets?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "SignalEngine/1.0 (research tool; non-commercial)"}

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=REQ_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            # Gamma API returns a list directly or {"data": [...]}
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "data" in data:
                return data["data"]
            return []
    except urllib.error.HTTPError as e:
        print(f"    [WARN] Polymarket API HTTP {e.code}: {url}")
        return []
    except urllib.error.URLError as e:
        print(f"    [WARN] Polymarket API unavailable: {e.reason}")
        return []
    except Exception as e:
        print(f"    [WARN] Polymarket fetch error: {e}")
        return []


def fetch_active_markets(force_refresh: bool = False) -> List[dict]:
    """
    Return a list of active Polymarket market dicts.
    Results are cached to `polymarket_cache.json` with a 1-hour TTL.

    Each market dict is normalised (outcomes and outcomePrices as Python
    lists; numeric fields as float).
    """
    cache = _load_cache()

    if not force_refresh and _is_cache_fresh(cache):
        return cache["markets"]

    print("  Fetching Polymarket markets (Gamma API)...", end="", flush=True)
    all_markets: List[dict] = []

    for offset in range(0, MAX_MARKETS, PAGE_SIZE):
        page = _fetch_page(offset)
        if not page:
            break
        all_markets.extend(page)
        if len(page) < PAGE_SIZE:
            break
        if offset + PAGE_SIZE < MAX_MARKETS:
            time.sleep(REQ_DELAY)

    # Normalise fields
    normalised = []
    for m in all_markets:
        try:
            norm = _normalise_market(m)
            if norm:
                normalised.append(norm)
        except Exception:
            continue

    print(f" {len(normalised)} markets loaded.")

    # Build price history delta: store old yes_price keyed by slug
    old_prices: dict = cache.get("price_history", {})
    new_prices: dict = {}
    for m in normalised:
        slug = m.get("slug", "")
        yes_price = m.get("yes_price")
        if slug and yes_price is not None:
            # Record prior price for trend calculation
            if slug in old_prices:
                m["_prev_yes_price"] = old_prices[slug].get("yes_price")
            new_prices[slug] = {
                "yes_price": yes_price,
                "timestamp": datetime.now().isoformat(),
            }

    cache["markets"]       = normalised
    cache["timestamp"]     = datetime.now().isoformat()
    cache["price_history"] = new_prices
    _save_cache(cache)

    return normalised


def _normalise_market(m: dict) -> Optional[dict]:
    """
    Normalise a raw Gamma API market dict into a consistent structure.
    Returns None if the market is malformed or already resolved.
    """
    if m.get("closed") or not m.get("active"):
        return None

    outcomes      = _parse_json_field(m.get("outcomes", []))
    outcome_prices = _parse_json_field(m.get("outcomePrices", []))

    if not outcomes:
        return None

    # Convert string prices to float
    prices_float: List[float] = []
    for p in outcome_prices:
        try:
            prices_float.append(float(p))
        except (TypeError, ValueError):
            prices_float.append(0.0)

    # Pad prices list to match outcomes length
    while len(prices_float) < len(outcomes):
        prices_float.append(0.0)

    # Identify the "Yes" outcome index (binary market)
    yes_idx = None
    for i, name in enumerate(outcomes):
        if str(name).strip().lower() in ("yes", "true", "1"):
            yes_idx = i
            break
    yes_price = prices_float[yes_idx] if yes_idx is not None else None

    # Parse volumes / liquidity as float
    def _f(v) -> float:
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    vol_24h   = _f(m.get("volume24hr"))
    liquidity = _f(m.get("liquidity"))
    volume    = _f(m.get("volume"))

    # Parse resolution date
    end_date_str = m.get("endDate") or m.get("resolutionSource") or ""
    end_date: Optional[str] = None
    days_to_resolution: Optional[int] = None
    if end_date_str:
        try:
            # Accept ISO strings like "2026-04-15T00:00:00Z"
            dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            end_date = dt.strftime("%Y-%m-%d")
            now_utc = datetime.now(timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            days_to_resolution = max(0, (dt - now_utc).days)
        except Exception:
            pass

    # Tags → flat string list
    raw_tags = m.get("tags") or []
    tags: List[str] = []
    for t in raw_tags:
        if isinstance(t, dict):
            tags.append(str(t.get("slug", t.get("label", ""))).lower())
        elif isinstance(t, str):
            tags.append(t.lower())

    return {
        "id":                  m.get("id", ""),
        "condition_id":        m.get("conditionId", ""),
        "slug":                m.get("slug", ""),
        "question":            m.get("question", ""),
        "description":         (m.get("description") or "")[:300],
        "outcomes":            outcomes,
        "prices":              prices_float,
        "yes_price":           yes_price,         # None for multi-outcome
        "yes_idx":             yes_idx,
        "volume_24h":          vol_24h,
        "volume_total":        volume,
        "liquidity":           liquidity,
        "end_date":            end_date,
        "days_to_resolution":  days_to_resolution,
        "tags":                tags,
        "is_binary":           len(outcomes) == 2,
        "_prev_yes_price":     None,              # Filled in by fetch_active_markets
    }


# ==============================================================================
# SECTION 3: MARKET SEARCH & TICKER MATCHING
# ==============================================================================

def search_markets(
    query: str,
    markets: Optional[List[dict]] = None,
    force_refresh: bool = False,
) -> List[dict]:
    """
    Return markets whose question or slug contains any word from `query`.
    Case-insensitive substring matching.
    """
    if markets is None:
        markets = fetch_active_markets(force_refresh=force_refresh)

    query_lower = query.lower().strip()
    keywords = [w for w in query_lower.split() if len(w) >= 3]
    if not keywords:
        return []

    results = []
    for m in markets:
        haystack = (m.get("question", "") + " " + m.get("slug", "")).lower()
        if any(kw in haystack for kw in keywords):
            results.append(m)
    return results


def match_ticker_markets(
    ticker: str,
    catalyst_type: Optional[str] = None,
    markets: Optional[List[dict]] = None,
    force_refresh: bool = False,
) -> List[dict]:
    """
    Find Polymarket markets relevant to a given equity/crypto ticker.

    Strategy:
      1. Use TICKER_KEYWORDS to generate search phrases for the ticker
      2. Optionally intersect with CATALYST_TYPE_KEYWORDS for catalyst type
      3. Filter by time-to-resolution and minimum volume
    """
    if markets is None:
        markets = fetch_active_markets(force_refresh=force_refresh)

    ticker_upper = ticker.upper()
    kws = TICKER_KEYWORDS.get(ticker_upper, [ticker_upper.lower()])

    # Add catalyst type refinement keywords (optional AND filter)
    type_kws: List[str] = []
    if catalyst_type and catalyst_type in CATALYST_TYPE_KEYWORDS:
        type_kws = CATALYST_TYPE_KEYWORDS[catalyst_type]

    matched = []
    for m in markets:
        # Skip if outside resolution window
        dtr = m.get("days_to_resolution")
        if dtr is not None:
            if dtr < MIN_DAYS_RES or dtr > MAX_DAYS_RES:
                continue

        haystack = (m.get("question", "") + " " + m.get("slug", "")).lower()

        # Must match at least one ticker keyword
        ticker_hit = any(kw in haystack for kw in kws)
        if not ticker_hit:
            continue

        # If catalyst type given, optionally boost (don't hard-filter)
        # by checking if type keywords also appear — used in scoring later
        m = dict(m)  # shallow copy to avoid mutating cache
        m["_type_keyword_hit"] = bool(type_kws and any(kw in haystack for kw in type_kws))
        matched.append(m)

    return matched


def search_by_catalyst_type(
    catalyst_type: str,
    markets: Optional[List[dict]] = None,
) -> List[dict]:
    """Return markets that match a catalyst type keyword cluster."""
    if markets is None:
        markets = fetch_active_markets()

    kws = CATALYST_TYPE_KEYWORDS.get(catalyst_type, [catalyst_type.lower()])
    results = []
    for m in markets:
        dtr = m.get("days_to_resolution")
        if dtr is not None and (dtr < MIN_DAYS_RES or dtr > MAX_DAYS_RES):
            continue
        haystack = (m.get("question", "") + " " + m.get("slug", "")).lower()
        if any(kw in haystack for kw in kws):
            results.append(m)
    return results


# ==============================================================================
# SECTION 4: SIGNAL EXTRACTION & SCORING
# ==============================================================================

def _compute_signal_score(
    prediction: float,
    volume_24h: float,
    liquidity: float,
    days_to_resolution: Optional[int],
    type_keyword_hit: bool = False,
) -> float:
    """
    Compute a 0–1 signal score from market attributes.

    Component weights (sum = 1.0):
      0.30  Consensus strength  (probability far from 50/50)
      0.25  24h trading volume  (market activity proxy)
      0.25  Liquidity depth     (order-book quality)
      0.20  Time to resolution  (proximity to catalyst)
    """
    score = 0.0

    # ── 1. Consensus strength (0.30) ──────────────────────────────────────────
    if prediction >= STRONG_HIGH or prediction <= STRONG_LOW:
        score += 0.30
    elif prediction >= MOD_HIGH or prediction <= MOD_LOW:
        score += 0.15

    # ── 2. Volume (0.25) ──────────────────────────────────────────────────────
    if volume_24h >= VOL_HIGH:
        score += 0.25
    elif volume_24h >= VOL_MED:
        score += 0.15
    elif volume_24h >= VOL_LOW:
        score += 0.08
    elif volume_24h >= 100:
        score += 0.03

    # ── 3. Liquidity (0.25) ───────────────────────────────────────────────────
    if liquidity >= LIQ_HIGH:
        score += 0.25
    elif liquidity >= LIQ_MED:
        score += 0.15
    elif liquidity >= LIQ_LOW:
        score += 0.08
    elif liquidity >= 100:
        score += 0.03

    # ── 4. Time to resolution (0.20) ──────────────────────────────────────────
    if days_to_resolution is not None and days_to_resolution > 0:
        if days_to_resolution <= 7:
            score += 0.20   # Imminent event
        elif days_to_resolution <= 14:
            score += 0.18
        elif days_to_resolution <= 30:
            score += 0.14
        elif days_to_resolution <= 60:
            score += 0.10
        elif days_to_resolution <= 90:
            score += 0.06
        elif days_to_resolution <= MAX_DAYS_RES:
            score += 0.03

    # ── Minor boost for catalyst-type keyword alignment ───────────────────────
    if type_keyword_hit:
        score = min(score + 0.03, 1.0)

    return round(min(score, 1.0), 4)


def _compute_confidence(volume_24h: float, liquidity: float) -> float:
    """
    Market confidence based on volume and liquidity depth.
    High volume + deep liquidity = crowd has real money behind the prediction.
    """
    if volume_24h >= VOL_HIGH and liquidity >= LIQ_HIGH:
        return 0.90
    if volume_24h >= VOL_HIGH or liquidity >= LIQ_HIGH:
        return 0.80
    if volume_24h >= VOL_MED and liquidity >= LIQ_MED:
        return 0.70
    if volume_24h >= VOL_MED or liquidity >= LIQ_MED:
        return 0.60
    if volume_24h >= VOL_LOW or liquidity >= LIQ_LOW:
        return 0.45
    if volume_24h >= 100:
        return 0.25
    return 0.10


def _liquidity_label(liquidity: float) -> str:
    if liquidity >= LIQ_HIGH:
        return "high"
    if liquidity >= LIQ_MED:
        return "medium"
    if liquidity >= LIQ_LOW:
        return "low"
    return "very_low"


def _trend_label(yes_price: Optional[float], prev_yes_price: Optional[float]) -> str:
    """
    Derive trend from cached price comparison.
    Returns: "bullish" | "bearish" | "neutral" | "unknown"
    """
    if yes_price is None or prev_yes_price is None:
        return "unknown"
    delta = yes_price - prev_yes_price
    if delta >= 0.03:
        return "bullish"
    if delta <= -0.03:
        return "bearish"
    return "neutral"


def extract_signal(
    market: dict,
    ticker: Optional[str] = None,
    catalyst_type: Optional[str] = None,
) -> Optional[dict]:
    """
    Convert a normalised Polymarket market dict into a PolymarketSignal dict.

    Returns None if the market doesn't meet minimum quality thresholds
    (insufficient volume, already resolved, bad data, etc.).
    """
    vol_24h   = market.get("volume_24h", 0.0)
    liquidity = market.get("liquidity", 0.0)
    dtr       = market.get("days_to_resolution")

    # Quality gate: skip thin markets
    if vol_24h < MIN_VOL_24H and liquidity < MIN_LIQ:
        return None
    if dtr is not None and dtr < MIN_DAYS_RES:
        return None  # Already resolving today
    if dtr is not None and dtr > MAX_DAYS_RES:
        return None  # Too far out

    outcomes = market.get("outcomes", [])
    prices   = market.get("prices", [])
    yes_price = market.get("yes_price")

    # For binary markets use Yes price; for multi-outcome use max-prob outcome
    if yes_price is not None:
        prediction = yes_price
    elif prices:
        prediction = max(prices)
    else:
        return None

    # Must have a non-degenerate price
    if not (0.001 <= prediction <= 0.999):
        return None

    signal_score = _compute_signal_score(
        prediction     = prediction,
        volume_24h     = vol_24h,
        liquidity      = liquidity,
        days_to_resolution = dtr,
        type_keyword_hit   = market.get("_type_keyword_hit", False),
    )
    confidence = _compute_confidence(vol_24h, liquidity)
    trend      = _trend_label(yes_price, market.get("_prev_yes_price"))

    return {
        "source":              "polymarket",
        "market_id":           market.get("id", ""),
        "condition_id":        market.get("condition_id", ""),
        "market_slug":         market.get("slug", ""),
        "question":            market.get("question", ""),
        "ticker":              ticker,
        "catalyst_type":       catalyst_type,
        "prediction":          round(prediction, 4),
        "signal_score":        signal_score,
        "volume_24h":          round(vol_24h, 2),
        "volume_total":        round(market.get("volume_total", 0.0), 2),
        "liquidity":           _liquidity_label(liquidity),
        "liquidity_usd":       round(liquidity, 2),
        "outcomes":            outcomes,
        "prices":              [round(p, 4) for p in prices],
        "is_binary":           market.get("is_binary", True),
        "time_to_resolution":  market.get("end_date"),
        "days_to_resolution":  dtr,
        "confidence":          confidence,
        "trend":               trend,
        "tags":                market.get("tags", []),
        "timestamp":           datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ==============================================================================
# SECTION 5: SCREENER — BATCH PROCESSING
# ==============================================================================

class PolymarketScreener:
    """
    High-level screener that fetches Polymarket markets and generates
    catalyst signals for a list of tickers or free-form queries.

    Usage (as a library):
        screener = PolymarketScreener()
        signals  = screener.run_ticker_screen(["TSLA", "NVDA", "BTC-USD"])
        signals += screener.run_query_screen(["Fed rate cut", "Bitcoin $100k"])
        screener.print_results(signals)
    """

    def __init__(self, force_refresh: bool = False):
        self._markets: Optional[List[dict]] = None
        self._force_refresh = force_refresh

    @property
    def markets(self) -> List[dict]:
        if self._markets is None:
            self._markets = fetch_active_markets(force_refresh=self._force_refresh)
        return self._markets

    # ── Ticker-based screening ─────────────────────────────────────────────────

    def screen_ticker(
        self,
        ticker: str,
        catalyst_type: Optional[str] = None,
    ) -> List[dict]:
        """Return all signals for a single ticker."""
        matched = match_ticker_markets(
            ticker         = ticker,
            catalyst_type  = catalyst_type,
            markets        = self.markets,
        )
        signals = []
        for m in matched:
            sig = extract_signal(m, ticker=ticker, catalyst_type=catalyst_type)
            if sig:
                signals.append(sig)
        return signals

    def run_ticker_screen(
        self,
        tickers: List[str],
        catalyst_type: Optional[str] = None,
    ) -> List[dict]:
        """Screen a list of tickers; deduplicate by market slug."""
        seen_slugs: set = set()
        all_signals: List[dict] = []

        for ticker in tickers:
            for sig in self.screen_ticker(ticker, catalyst_type=catalyst_type):
                slug = sig["market_slug"]
                if slug not in seen_slugs:
                    seen_slugs.add(slug)
                    all_signals.append(sig)

        return sorted(all_signals, key=lambda s: s["signal_score"], reverse=True)

    # ── Query-based screening ──────────────────────────────────────────────────

    def screen_query(self, query: str, catalyst_type: Optional[str] = None) -> List[dict]:
        """Return signals matching a free-form query string."""
        matched = search_markets(query, markets=self.markets)
        signals = []
        for m in matched:
            sig = extract_signal(m, catalyst_type=catalyst_type)
            if sig:
                signals.append(sig)
        return signals

    def run_query_screen(
        self,
        queries: List[str],
        catalyst_type: Optional[str] = None,
    ) -> List[dict]:
        """Screen a list of query strings; deduplicate by market slug."""
        seen_slugs: set = set()
        all_signals: List[dict] = []

        for q in queries:
            for sig in self.screen_query(q, catalyst_type=catalyst_type):
                slug = sig["market_slug"]
                if slug not in seen_slugs:
                    seen_slugs.add(slug)
                    all_signals.append(sig)

        return sorted(all_signals, key=lambda s: s["signal_score"], reverse=True)

    # ── Catalyst-type screening ────────────────────────────────────────────────

    def run_catalyst_type_screen(self, catalyst_type: str) -> List[dict]:
        """Return signals for all markets matching a catalyst type cluster."""
        matched = search_by_catalyst_type(catalyst_type, markets=self.markets)
        signals = []
        seen_slugs: set = set()
        for m in matched:
            sig = extract_signal(m, catalyst_type=catalyst_type)
            if sig and sig["market_slug"] not in seen_slugs:
                seen_slugs.add(sig["market_slug"])
                signals.append(sig)
        return sorted(signals, key=lambda s: s["signal_score"], reverse=True)

    # ── Top-markets screen (no filter) ────────────────────────────────────────

    def run_top_markets_screen(self, min_score: float = 0.20) -> List[dict]:
        """Return signals for all active markets above a minimum score threshold."""
        signals = []
        seen_slugs: set = set()
        for m in self.markets:
            sig = extract_signal(m)
            if sig and sig["signal_score"] >= min_score:
                slug = sig["market_slug"]
                if slug not in seen_slugs:
                    seen_slugs.add(slug)
                    signals.append(sig)
        return sorted(signals, key=lambda s: s["signal_score"], reverse=True)

    # ── Reporting ─────────────────────────────────────────────────────────────

    def print_results(self, signals: List[dict], top_n: int = 20) -> None:
        print_results(signals, top_n=top_n)

    # ── Export ────────────────────────────────────────────────────────────────

    def export_csv(self, signals: List[dict], label: str = "polymarket") -> str:
        return export_signals_csv(signals, label=label)


# ==============================================================================
# SECTION 6: REPORTING
# ==============================================================================

def print_results(signals: List[dict], top_n: int = 20) -> None:
    """Print a formatted table of Polymarket signals."""
    if not signals:
        print("\n  No Polymarket signals found matching criteria.")
        return

    top = signals[:top_n]
    print(f"\n{'─' * 80}")
    print(f"  POLYMARKET SIGNALS — TOP {len(top)}")
    print(f"{'─' * 80}")
    print(f"\n  {'#':<4} {'Ticker':<8} {'Score':>6} {'Pred':>6} {'Vol24h':>10} "
          f"{'Liq':>6} {'Trend':>8} {'DaysRes':>8}  Question")
    print(f"  {'─' * 78}")

    for i, sig in enumerate(top, 1):
        ticker  = (sig.get("ticker") or "—")[:7]
        score   = sig["signal_score"]
        pred    = sig["prediction"]
        vol     = sig["volume_24h"]
        liq     = sig["liquidity"]
        trend   = sig["trend"]
        dtr     = sig.get("days_to_resolution")
        q       = sig["question"][:50]

        # Tier emoji
        if score >= 0.70:
            tier = "🔥"
        elif score >= 0.50:
            tier = "🟡"
        else:
            tier = "  "

        dtr_str = f"{dtr}d" if dtr is not None else "—"
        vol_str = f"${vol/1_000:.0f}k" if vol >= 1_000 else f"${vol:.0f}"

        print(f"  {i:<4} {ticker:<8} {score:>5.2f}  {pred:>5.0%}  {vol_str:>9} "
              f"  {liq[:5]:>5}  {trend:>8} {dtr_str:>7}  {tier}{q}")

    # ── Detail block for top 5 ────────────────────────────────────────────────
    print(f"\n{'─' * 80}")
    print(f"  DETAIL — TOP 5 SIGNALS")
    print(f"{'─' * 80}")

    for sig in top[:5]:
        print(f"\n  [{sig.get('ticker') or 'MACRO'}] {sig['question']}")
        print(f"    Slug:      {sig['market_slug']}")
        print(f"    Score:     {sig['signal_score']:.2f}  |  "
              f"Confidence: {sig['confidence']:.0%}  |  "
              f"Trend: {sig['trend']}")
        print(f"    Outcomes:  {sig['outcomes']}")
        print(f"    Prices:    {[f'{p:.0%}' for p in sig['prices']]}")
        print(f"    Volume24h: ${sig['volume_24h']:,.0f}  |  "
              f"Liquidity: {sig['liquidity']} (${sig['liquidity_usd']:,.0f})")
        if sig.get("time_to_resolution"):
            print(f"    Resolves:  {sig['time_to_resolution']} "
                  f"({sig.get('days_to_resolution', '?')} days)")
        if sig.get("tags"):
            print(f"    Tags:      {', '.join(sig['tags'][:5])}")


# ==============================================================================
# SECTION 7: CSV EXPORT
# ==============================================================================

def export_signals_csv(signals: List[dict], label: str = "polymarket") -> str:
    """
    Export signals to a dated CSV in OUTPUT_DIR.
    Returns the file path written.
    """
    import csv

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    path = os.path.join(OUTPUT_DIR, f"{label}_signals_{date_str}.csv")

    if not signals:
        return path

    # Flatten list fields for CSV compatibility
    rows = []
    for s in signals:
        row = dict(s)
        row["outcomes"] = "|".join(str(o) for o in s.get("outcomes", []))
        row["prices"]   = "|".join(f"{p:.4f}" for p in s.get("prices", []))
        row["tags"]     = "|".join(s.get("tags", []))
        rows.append(row)

    fieldnames = [
        "source", "market_slug", "ticker", "catalyst_type",
        "prediction", "signal_score", "confidence", "trend",
        "volume_24h", "volume_total", "liquidity", "liquidity_usd",
        "outcomes", "prices", "is_binary",
        "time_to_resolution", "days_to_resolution",
        "question", "tags", "market_id", "condition_id", "timestamp",
    ]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return path


# ==============================================================================
# SECTION 8: INTEGRATION HELPER FOR CATALYST SCREENER
# ==============================================================================

def enrich_catalyst_results(catalyst_df, top_n_tickers: int = 10) -> List[dict]:
    """
    Convenience function: take the top N tickers from a catalyst_screener
    DataFrame and return Polymarket signals for them.

    Example usage in catalyst_screener.py:
        from polymarket_screener import enrich_catalyst_results
        poly_signals = enrich_catalyst_results(results_df, top_n_tickers=10)
        print_results(poly_signals)

    Returns an empty list if Polymarket API is unavailable.
    """
    try:
        if catalyst_df is None or catalyst_df.empty:
            return []

        tickers = catalyst_df.head(top_n_tickers)["ticker"].tolist()
        screener = PolymarketScreener()
        return screener.run_ticker_screen(tickers)
    except Exception as e:
        print(f"  [WARN] Polymarket enrichment failed: {e}")
        return []


# ==============================================================================
# SECTION 9: MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Screener v1.0 — Prediction Market Signals"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--ticker", type=str,
        help="Equity/crypto ticker to match (e.g. TSLA, BTC-USD)",
    )
    group.add_argument(
        "--query", type=str,
        help="Free-form search query (e.g. 'Fed rate cut')",
    )
    group.add_argument(
        "--type", dest="catalyst_type",
        choices=list(CATALYST_TYPE_KEYWORDS.keys()),
        help="Screen by catalyst type",
    )
    group.add_argument(
        "--all", action="store_true",
        help="Screen all active markets (no filter)",
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="Number of results to display (default: 20)",
    )
    parser.add_argument(
        "--export", action="store_true",
        help="Export results to CSV in signals_output/",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Force cache refresh (ignore TTL)",
    )
    parser.add_argument(
        "--min-score", type=float, default=0.10,
        dest="min_score",
        help="Minimum signal score to include (default: 0.10)",
    )
    args = parser.parse_args()

    print(f"\n{'█' * 60}")
    print(f"  POLYMARKET SCREENER v1.0")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Source: {API_BASE}")
    print(f"{'█' * 60}")

    screener = PolymarketScreener(force_refresh=args.refresh)
    signals: List[dict] = []

    if args.ticker:
        ticker = args.ticker.upper()
        print(f"\n  Matching markets for ticker: {ticker}")
        signals = screener.screen_ticker(ticker)
        if not signals:
            print(f"  No active markets found for {ticker}.")
        label = f"polymarket_{ticker.lower().replace('-', '')}"

    elif args.query:
        print(f"\n  Searching markets for: '{args.query}'")
        signals = screener.screen_query(args.query)
        label = "polymarket_query"

    elif args.catalyst_type:
        print(f"\n  Screening catalyst type: {args.catalyst_type}")
        signals = screener.run_catalyst_type_screen(args.catalyst_type)
        label = f"polymarket_{args.catalyst_type}"

    else:
        # Default: screen a curated default set of tickers + economic catalysts
        print(f"\n  Running default catalyst screen...")
        default_tickers = [
            "TSLA", "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META",
            "BTC-USD", "ETH-USD", "COIN", "MARA",
        ]
        default_queries = [
            "Fed rate", "federal reserve", "interest rate cut",
            "Bitcoin 100000", "bitcoin price", "recession",
        ]

        signals  = screener.run_ticker_screen(default_tickers)
        signals += screener.run_query_screen(default_queries)

        # Deduplicate
        seen: set = set()
        unique: List[dict] = []
        for s in signals:
            if s["market_slug"] not in seen:
                seen.add(s["market_slug"])
                unique.append(s)
        signals = sorted(unique, key=lambda s: s["signal_score"], reverse=True)
        label = "polymarket_default"

    # Apply minimum score filter
    signals = [s for s in signals if s["signal_score"] >= args.min_score]

    print_results(signals, top_n=args.top)

    if args.export and signals:
        path = export_signals_csv(signals, label=label)
        print(f"\n  Exported {len(signals)} signals to: {path}")

    print(f"\n{'█' * 60}")
    print(f"  Polymarket data reflects crowd probability, NOT certainty.")
    print(f"  High signal score = strong consensus + active market,")
    print(f"  not a guaranteed outcome. THIS IS NOT INVESTMENT ADVICE.")
    print(f"{'█' * 60}\n")

    return signals


if __name__ == "__main__":
    main()
