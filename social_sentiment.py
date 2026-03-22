#!/usr/bin/env python3
"""
social_sentiment.py — Google Trends + StockTwits Social Signals
================================================================
Replaces the broken Reddit --social flag in catalyst_screener.py with two
reliable, free, unauthenticated data sources:

  1. Google Trends (via pytrends) — search-volume momentum over the last 30d
  2. StockTwits public stream — retail sentiment from tagged bullish/bearish msgs

Public API:
    get_google_trends_score(ticker, company_name, lookback_days) -> dict | None
    get_stocktwits_sentiment(ticker)                             -> dict | None
    get_combined_social_score(ticker, company_name)              -> dict

Both functions cache results to data/trends_cache.json and data/twits_cache.json
to avoid hammering rate-limited APIs on batch runs.

Dependencies:
    pytrends>=4.9.0  (pip install pytrends)
    urllib (stdlib — for StockTwits)
"""

import json
import os
import random
import time
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Config (with fallback defaults so this module works standalone)
# ---------------------------------------------------------------------------
try:
    from config import (
        SOCIAL_TRENDS_LOOKBACK_DAYS,
        SOCIAL_TRENDS_CACHE_TTL_HOURS,
        SOCIAL_STOCKTWITS_CACHE_TTL_HOURS,
        SOCIAL_BULLISH_THRESHOLD,
        SOCIAL_BEARISH_THRESHOLD,
    )
except ImportError:
    SOCIAL_TRENDS_LOOKBACK_DAYS = 30
    SOCIAL_TRENDS_CACHE_TTL_HOURS = 24
    SOCIAL_STOCKTWITS_CACHE_TTL_HOURS = 4
    SOCIAL_BULLISH_THRESHOLD = 0.65
    SOCIAL_BEARISH_THRESHOLD = 0.35

_TRENDS_CACHE_FILE = "data/trends_cache.json"
_TWITS_CACHE_FILE = "data/twits_cache.json"


# ==============================================================================
# CACHE HELPERS
# ==============================================================================

def _load_cache(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


def _cache_get(cache: dict, key: str, ttl_hours: float) -> Optional[dict]:
    """Return cached entry if present and not expired, else None."""
    entry = cache.get(key)
    if not entry:
        return None
    age_seconds = datetime.utcnow().timestamp() - entry.get("_ts", 0)
    if age_seconds > ttl_hours * 3600:
        return None
    return entry


def _cache_set(cache: dict, key: str, value: dict) -> dict:
    """Stamp value with current UTC timestamp and insert into cache dict."""
    stamped = dict(value)
    stamped["_ts"] = datetime.utcnow().timestamp()
    cache[key] = stamped
    return cache


# ==============================================================================
# GOOGLE TRENDS
# ==============================================================================

def get_google_trends_score(
    ticker: str,
    company_name: Optional[str] = None,
    lookback_days: int = SOCIAL_TRENDS_LOOKBACK_DAYS,
) -> Optional[dict]:
    """
    Fetch Google Trends relative search-volume for a ticker using pytrends.

    Search terms: [ticker, company_name] when company_name is provided,
                  otherwise [ticker] only.
    Timeframe:    f'now {lookback_days}-d'   (Google accepts 1–90)
    Geo:          '' (worldwide)

    Computed metrics
    ----------------
    trend_score    -- last 7d avg ÷ prior (lookback-7)d avg
                      > 1.5 = RISING (50 %+ above baseline)
                      < 0.7 = DECLINING
                      else  = STABLE
    spike_score    -- today's value ÷ period max  (1.0 = at all-time high)
    interest_level -- last 7d average on Google's 0–100 scale

    Caching
    -------
    24-hour TTL in data/trends_cache.json keyed by ticker.

    Rate limiting
    -------------
    pytrends is unofficial and quota-limited.  A random 5–10 s sleep is added
    before each live API call.  Returns None on any pytrends exception.

    Returns
    -------
    dict  {ticker, trend_score, spike_score, interest_level, interpretation, cached}
    None  on quota errors, no data, or pytrends not installed.
    """
    cache = _load_cache(_TRENDS_CACHE_FILE)
    hit = _cache_get(cache, ticker, SOCIAL_TRENDS_CACHE_TTL_HOURS)
    if hit:
        result = {k: v for k, v in hit.items() if k != "_ts"}
        result["cached"] = True
        return result

    try:
        from pytrends.request import TrendReq  # type: ignore[import]
    except ImportError:
        return None  # pytrends not installed — degrade gracefully

    keywords = [ticker]
    if (
        company_name
        and company_name.upper() != ticker.upper()
        and len(company_name) <= 100
    ):
        keywords.append(company_name[:100])

    try:
        # Rate-limit: pytrends is unofficial and throttles aggressively
        time.sleep(random.uniform(5, 10))

        pt = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
        pt.build_payload(
            kw_list=keywords[:5],
            timeframe=f"now {lookback_days}-d",
            geo="",
        )
        df = pt.interest_over_time()

        if df is None or df.empty:
            return None

        # Prefer the ticker's own column; fall back to the first data column
        # (pytrends strips the 'isPartial' column automatically in newer versions)
        data_cols = [c for c in df.columns if c != "isPartial"]
        col = ticker if ticker in data_cols else data_cols[0]
        series = df[col].astype(float)

        if len(series) < 8:
            return None

        recent_7d = series.iloc[-7:].mean()
        baseline = series.iloc[:-7].mean()
        trend_score = round(recent_7d / baseline, 3) if baseline > 0.001 else 1.0

        period_max = series.max()
        spike_score = (
            round(float(series.iloc[-1]) / period_max, 3) if period_max > 0 else 0.0
        )

        interest_level = round(float(recent_7d), 1)

        if trend_score > 1.5:
            interpretation = "RISING"
        elif trend_score < 0.7:
            interpretation = "DECLINING"
        else:
            interpretation = "STABLE"

        result = {
            "ticker": ticker,
            "trend_score": trend_score,
            "spike_score": spike_score,
            "interest_level": interest_level,
            "interpretation": interpretation,
            "cached": False,
        }

        payload = {k: v for k, v in result.items() if k != "cached"}
        cache = _cache_set(cache, ticker, payload)
        _save_cache(_TRENDS_CACHE_FILE, cache)
        return result

    except Exception:
        # Quota exceeded, ConnectionError, empty result, etc. — degrade cleanly
        return None


# ==============================================================================
# STOCKTWITS
# ==============================================================================

def get_stocktwits_sentiment(ticker: str) -> Optional[dict]:
    """
    Fetch the StockTwits public message stream for a ticker.

    Endpoint: https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json
    No authentication required for public streams (unauthenticated rate limit
    is ~200 req/hour per IP).

    Parses the last 30 messages returned by the API:
      - bullish_count  — messages with sentiment.basic == 'Bullish'
      - bearish_count  — messages with sentiment.basic == 'Bearish'
      - (untagged messages are ignored — low signal retail noise)

    Derived metrics
    ---------------
    bull_ratio       = bullish_count / (bullish_count + bearish_count + 0.001)
    sentiment_signal = 'BULLISH'  if bull_ratio > SOCIAL_BULLISH_THRESHOLD (0.65)
                     = 'BEARISH'  if bull_ratio < SOCIAL_BEARISH_THRESHOLD (0.35)
                     = 'NEUTRAL'  otherwise
    message_count    = total messages in response (volume proxy)

    Caching
    -------
    4-hour TTL in data/twits_cache.json keyed by ticker.

    Returns
    -------
    dict  {ticker, bull_ratio, bullish_count, bearish_count,
           message_count, sentiment_signal, cached}
    None  on HTTP 404 (ticker unknown to StockTwits), 429 (rate limit),
          or any network error.
    """
    import urllib.error
    import urllib.request

    cache = _load_cache(_TWITS_CACHE_FILE)
    hit = _cache_get(cache, ticker, SOCIAL_STOCKTWITS_CACHE_TTL_HOURS)
    if hit:
        result = {k: v for k, v in hit.items() if k != "_ts"}
        result["cached"] = True
        return result

    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
    req = urllib.request.Request(
        url, headers={"User-Agent": "SignalEngine/1.0 (educational research)"}
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 403, 429):
            return None  # Ticker not on StockTwits or rate-limited
        return None
    except Exception:
        return None

    messages = payload.get("messages", [])
    bullish_count = 0
    bearish_count = 0

    for msg in messages[:30]:
        sentiment = (msg.get("entities") or {}).get("sentiment")
        if not sentiment:
            continue
        basic = sentiment.get("basic", "")
        if basic == "Bullish":
            bullish_count += 1
        elif basic == "Bearish":
            bearish_count += 1

    bull_ratio = bullish_count / (bullish_count + bearish_count + 0.001)

    if bull_ratio > SOCIAL_BULLISH_THRESHOLD:
        sentiment_signal = "BULLISH"
    elif bull_ratio < SOCIAL_BEARISH_THRESHOLD:
        sentiment_signal = "BEARISH"
    else:
        sentiment_signal = "NEUTRAL"

    result = {
        "ticker": ticker,
        "bull_ratio": round(bull_ratio, 3),
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "message_count": len(messages),
        "sentiment_signal": sentiment_signal,
        "cached": False,
    }

    store = {k: v for k, v in result.items() if k != "cached"}
    cache = _cache_set(cache, ticker, store)
    _save_cache(_TWITS_CACHE_FILE, cache)
    return result


# ==============================================================================
# COMBINED SCORE
# ==============================================================================

def get_combined_social_score(
    ticker: str,
    company_name: Optional[str] = None,
) -> dict:
    """
    Combine Google Trends and StockTwits into a single social score.

    Score range: 0–100, anchored at 50 (neutral).

    Contribution table
    ------------------
    Google Trends (max ±15):
      RISING                → +10
      RISING + spike > 0.7  → +15  (search spike on top of rising trend)
      DECLINING             → -8

    StockTwits (max ±15):
      bull_ratio > 0.65     → +15
      bull_ratio > 0.55     → +8
      bull_ratio < 0.35     → -15
      bull_ratio < 0.45     → -8

    Single-source confidence discount
    ----------------------------------
    When only one source is available all deltas are halved (less confident).

    Interpretation thresholds
    -------------------------
    score >= 65  → BULLISH
    score >= 58  → MILDLY_BULLISH
    score <= 35  → BEARISH
    score <= 42  → MILDLY_BEARISH
    else         → NEUTRAL

    Returns
    -------
    dict  {score, trends_signal, twits_signal, sources, interpretation}
          score is None when both APIs are unavailable.
    """
    trends = get_google_trends_score(ticker, company_name)
    twits = get_stocktwits_sentiment(ticker)

    if trends is None and twits is None:
        return {
            "score": None,
            "trends_signal": None,
            "twits_signal": None,
            "sources": [],
            "interpretation": "NO_DATA",
        }

    both_available = trends is not None and twits is not None
    sources = []

    # ---- Google Trends delta ------------------------------------------------
    trends_signal = None
    trends_delta = 0
    if trends is not None:
        sources.append("google_trends")
        trends_signal = trends["interpretation"]
        if trends_signal == "RISING":
            trends_delta = 15 if trends["spike_score"] > 0.7 else 10
        elif trends_signal == "DECLINING":
            trends_delta = -8

    # ---- StockTwits delta ---------------------------------------------------
    twits_signal = None
    twits_delta = 0
    if twits is not None:
        sources.append("stocktwits")
        twits_signal = twits["sentiment_signal"]
        br = twits["bull_ratio"]
        if br > SOCIAL_BULLISH_THRESHOLD:
            twits_delta = 15
        elif br > 0.55:
            twits_delta = 8
        elif br < SOCIAL_BEARISH_THRESHOLD:
            twits_delta = -15
        elif br < 0.45:
            twits_delta = -8

    # ---- Single-source confidence discount ----------------------------------
    if not both_available:
        trends_delta = round(trends_delta * 0.5)
        twits_delta = round(twits_delta * 0.5)

    score = max(0, min(100, 50 + trends_delta + twits_delta))

    if score >= 65:
        interpretation = "BULLISH"
    elif score >= 58:
        interpretation = "MILDLY_BULLISH"
    elif score <= 35:
        interpretation = "BEARISH"
    elif score <= 42:
        interpretation = "MILDLY_BEARISH"
    else:
        interpretation = "NEUTRAL"

    return {
        "score": score,
        "trends_signal": trends_signal,
        "twits_signal": twits_signal,
        "sources": sources,
        "interpretation": interpretation,
    }
