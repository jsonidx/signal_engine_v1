#!/usr/bin/env python3
"""
================================================================================
CATALYST SCREENER v1.0 — "Hidden Gems" Detector
================================================================================
Identifies stocks with SETUP CONDITIONS for explosive moves.

IMPORTANT REALITY CHECK (read this):
    For every GME that squeezed 10x, there were 50 squeeze candidates
    that did nothing or went to zero. This screener finds the KINDLING,
    not the SPARK. You still need a catalyst (earnings, news, social
    momentum) to ignite the move. Use this as a watchlist generator,
    NOT as a buy signal.

WHAT IT SCREENS FOR:
    1. Short Squeeze Setup — high short interest + rising volume + price uptick
    2. Volume Breakout — unusual volume vs 20-day average (institutional accumulation)
    3. Volatility Compression — tight range about to break (Bollinger squeeze)
    4. Options Heat — unusual call/put activity signaling smart money positioning
    5. Social Momentum — Reddit/social mention velocity (WallStreetBets, etc.)

DATA SOURCES:
    - yfinance: price, volume, short interest, options data (free)
    - Reddit API: social sentiment (free, rate-limited)

USAGE:
    python3 catalyst_screener.py                    # Full scan
    python3 catalyst_screener.py --universe small   # Small-cap focus
    python3 catalyst_screener.py --universe meme    # Meme stock watchlist
    python3 catalyst_screener.py --ticker GME       # Single stock deep dive
    python3 catalyst_screener.py --social           # Include Reddit scan

IMPORTANT: This is NOT investment advice. High-potential = high-risk.
           These setups fail more often than they succeed.
================================================================================
"""

import argparse
import json
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
    from config import OUTPUT_DIR
except ImportError:
    OUTPUT_DIR = "./signals_output"

try:
    from polymarket_screener import PolymarketScreener
    _POLYMARKET_AVAILABLE = True
except ImportError:
    _POLYMARKET_AVAILABLE = False


# ==============================================================================
# UNIVERSES
# ==============================================================================

# Small/mid cap with historically high retail interest and short activity
SMALL_CAP_UNIVERSE = [
    # Meme / retail favorites
    "GME", "AMC", "BB", "BBBY", "CLOV", "WISH", "SOFI", "PLTR", "RIVN",
    "LCID", "NIO", "MARA", "RIOT", "COIN", "HOOD", "DKNG", "SKLZ",
    # Biotech (binary event plays)
    "MRNA", "BNTX", "NVAX", "SAVA", "ATOS",
    # Tech growth
    "SNOW", "NET", "CRWD", "DDOG", "ZS", "BILL", "HUBS", "CFLT",
    "PATH", "U", "RBLX", "AFRM", "UPST", "IONQ", "RGTI", "QUBT",
    # AI / Semiconductor
    "SMCI", "ARM", "MRVL", "ON", "SOUN", "BBAI", "AI", "PLTR",
    # Recent IPOs / SPACs with high short interest
    "JOBY", "LILM", "LUNR", "RKLB", "ASTS",
]

MEME_UNIVERSE = [
    "GME", "AMC", "BB", "SOFI", "PLTR", "RIVN", "LCID", "NIO",
    "MARA", "RIOT", "COIN", "HOOD", "DKNG", "IONQ", "RGTI", "QUBT",
    "SMCI", "SOUN", "BBAI", "RKLB", "ASTS", "LUNR", "AFRM", "UPST",
]

LARGE_CAP_WATCH = [
    "NVDA", "TSLA", "AMD", "META", "NFLX", "GOOGL", "AMZN", "AAPL",
    "MSFT", "CRM", "SHOP", "SQ", "ROKU", "SNAP", "PINS", "ABNB",
]


# ==============================================================================
# SECTION 1: DATA COLLECTION
# ==============================================================================

def get_stock_data(ticker: str) -> dict:
    """
    Pull comprehensive data for a single ticker from yfinance.
    Returns dict with price data, fundamentals, short interest, etc.
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}

        # Price history (6 months for technical analysis)
        hist = stock.history(period="6mo")
        if hist.empty or len(hist) < 20:
            return None

        # Current price & volume
        current_price = float(hist["Close"].iloc[-1])
        current_vol = float(hist["Volume"].iloc[-1])
        avg_vol_20d = float(hist["Volume"].iloc[-20:].mean())
        avg_vol_5d = float(hist["Volume"].iloc[-5:].mean())

        # Market cap
        market_cap = info.get("marketCap", 0)
        shares_outstanding = info.get("sharesOutstanding", 0)

        # Short interest data
        short_pct = info.get("shortPercentOfFloat", 0) or 0
        short_ratio = info.get("shortRatio", 0) or 0  # Days to cover
        float_shares = info.get("floatShares", 0) or 0

        # Institutional ownership
        inst_ownership = info.get("heldPercentInstitutions", 0) or 0
        insider_ownership = info.get("heldPercentInsiders", 0) or 0

        return {
            "ticker": ticker,
            "price": current_price,
            "market_cap": market_cap,
            "shares_outstanding": shares_outstanding,
            "float_shares": float_shares,
            "volume_current": current_vol,
            "volume_avg_20d": avg_vol_20d,
            "volume_avg_5d": avg_vol_5d,
            "short_pct_float": short_pct,
            "short_ratio_dtc": short_ratio,
            "inst_ownership": inst_ownership,
            "insider_ownership": insider_ownership,
            "history": hist,
            "info": info,
            "stock_obj": stock,
        }

    except Exception as e:
        return None


# ==============================================================================
# SECTION 2: SIGNAL COMPUTATION
# ==============================================================================

def score_short_squeeze(data: dict) -> dict:
    """
    Short Squeeze Setup Score.

    What preceded GME:
    - Short interest > 100% of float (extreme)
    - Low float (limited supply)
    - Rising volume (retail piling in)
    - Price starting to uptick (shorts getting nervous)

    Scoring:
    - Short % of float > 20%: high setup
    - Days to cover > 5: shorts can't exit quickly
    - Volume surge + price up = squeeze pressure
    """
    hist = data["history"]
    short_pct = data["short_pct_float"]
    dtc = data["short_ratio_dtc"]
    float_shares = data["float_shares"]

    score = 0
    flags = []

    # Short interest scoring
    if short_pct > 0.40:
        score += 3
        flags.append(f"EXTREME short interest: {short_pct:.0%}")
    elif short_pct > 0.20:
        score += 2
        flags.append(f"HIGH short interest: {short_pct:.0%}")
    elif short_pct > 0.10:
        score += 1
        flags.append(f"Elevated short interest: {short_pct:.0%}")

    # Days to cover
    if dtc > 8:
        score += 2
        flags.append(f"HIGH days to cover: {dtc:.1f}")
    elif dtc > 4:
        score += 1
        flags.append(f"Elevated days to cover: {dtc:.1f}")

    # Low float (harder to cover = more squeeze potential)
    if float_shares > 0 and float_shares < 50_000_000:
        score += 1
        flags.append(f"Low float: {float_shares/1e6:.0f}M shares")

    # Volume acceleration + price up (squeeze starting?)
    vol_ratio = data["volume_avg_5d"] / max(data["volume_avg_20d"], 1)
    price_5d = hist["Close"].iloc[-1] / hist["Close"].iloc[-5] - 1

    if vol_ratio > 2.0 and price_5d > 0.05:
        score += 2
        flags.append(f"Volume surge {vol_ratio:.1f}x + price up {price_5d:.1%}")
    elif vol_ratio > 1.5 and price_5d > 0:
        score += 1
        flags.append(f"Rising volume {vol_ratio:.1f}x with positive price")

    return {"score": score, "max": 9, "flags": flags}


def score_volume_breakout(data: dict) -> dict:
    """
    Volume Breakout Score.

    What preceded NVDA's AI run:
    - Sustained volume increase over weeks (not a 1-day spike)
    - Volume expanding on up days, contracting on down days
    - This indicates institutional accumulation (smart money)

    We measure:
    - Volume ratio (current vs 20-day avg)
    - On-Balance Volume trend
    - Volume-price confirmation
    """
    hist = data["history"]
    score = 0
    flags = []

    # Volume ratio
    vol_ratio = data["volume_avg_5d"] / max(data["volume_avg_20d"], 1)

    if vol_ratio > 3.0:
        score += 3
        flags.append(f"EXTREME volume: {vol_ratio:.1f}x avg")
    elif vol_ratio > 2.0:
        score += 2
        flags.append(f"HIGH volume: {vol_ratio:.1f}x avg")
    elif vol_ratio > 1.5:
        score += 1
        flags.append(f"Above-avg volume: {vol_ratio:.1f}x")

    # On-Balance Volume (OBV) trend
    close = hist["Close"]
    volume = hist["Volume"]
    obv = (np.sign(close.diff()) * volume).cumsum()
    obv_slope = np.polyfit(range(len(obv.iloc[-20:])), obv.iloc[-20:].values, 1)[0]

    if obv_slope > 0 and close.iloc[-1] > close.iloc[-20]:
        score += 2
        flags.append("OBV rising with price — accumulation pattern")
    elif obv_slope > 0 and close.iloc[-1] <= close.iloc[-20]:
        score += 1
        flags.append("OBV rising but price flat — hidden accumulation")

    # Up-volume vs down-volume ratio (last 10 days)
    recent = hist.iloc[-10:]
    up_days = recent[recent["Close"] > recent["Close"].shift(1)]
    down_days = recent[recent["Close"] <= recent["Close"].shift(1)]
    up_vol = up_days["Volume"].sum() if len(up_days) > 0 else 0
    down_vol = down_days["Volume"].sum() if len(down_days) > 0 else 1

    if up_vol / max(down_vol, 1) > 2.0:
        score += 2
        flags.append(f"Strong up-volume dominance: {up_vol/max(down_vol,1):.1f}x")
    elif up_vol / max(down_vol, 1) > 1.3:
        score += 1
        flags.append(f"Up-volume dominant: {up_vol/max(down_vol,1):.1f}x")

    return {"score": score, "max": 7, "flags": flags}


def score_volatility_squeeze(data: dict) -> dict:
    """
    Bollinger Band Squeeze — Volatility Compression.

    Before big moves, volatility often compresses to extreme lows.
    The Bollinger Band width narrows, then EXPANDS explosively.
    Like a spring being compressed before release.

    We measure:
    - Bollinger Band width percentile (current vs 6-month history)
    - Keltner Channel inside Bollinger (the classic "squeeze" setup)
    - ATR compression
    """
    hist = data["history"]
    close = hist["Close"]
    score = 0
    flags = []

    # Bollinger Bands (20-period, 2 std)
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20
    bb_width = ((bb_upper - bb_lower) / sma20).dropna()

    if len(bb_width) < 50:
        return {"score": 0, "max": 6, "flags": ["Insufficient data"]}

    # Current BB width percentile vs 6-month history
    current_width = bb_width.iloc[-1]
    percentile = (bb_width < current_width).mean() * 100

    if percentile < 10:
        score += 3
        flags.append(f"EXTREME vol compression: BB width {percentile:.0f}th percentile")
    elif percentile < 20:
        score += 2
        flags.append(f"Tight Bollinger squeeze: {percentile:.0f}th percentile")
    elif percentile < 30:
        score += 1
        flags.append(f"Below-avg volatility: {percentile:.0f}th percentile")

    # ATR compression (14-day)
    high = hist["High"]
    low = hist["Low"]
    tr = pd.concat([
        high - low,
        abs(high - close.shift(1)),
        abs(low - close.shift(1))
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    if len(atr.dropna()) > 50:
        atr_pct = atr / close
        current_atr_pct = atr_pct.iloc[-1]
        atr_percentile = (atr_pct.dropna() < current_atr_pct).mean() * 100

        if atr_percentile < 15:
            score += 2
            flags.append(f"ATR compressed: {atr_percentile:.0f}th percentile")
        elif atr_percentile < 30:
            score += 1
            flags.append(f"Below-avg ATR: {atr_percentile:.0f}th percentile")

    # Price near key level (within 3% of 52-week high or support)
    high_52w = close.max()
    pct_from_high = (close.iloc[-1] / high_52w - 1)
    if abs(pct_from_high) < 0.03:
        score += 1
        flags.append(f"Near 52-week high ({pct_from_high:.1%}) — breakout watch")

    return {"score": score, "max": 6, "flags": flags}


def score_options_activity(data: dict) -> dict:
    """
    Unusual Options Activity.

    Smart money often positions in options before big moves because:
    - Leverage (control 100 shares per contract)
    - Defined risk
    - Anonymity (harder to trace than stock purchases)

    We look for:
    - High call/put ratio (bullish positioning)
    - Unusual volume in near-term OTM calls (speculative bets)
    - Implied volatility skew changes
    """
    stock_obj = data["stock_obj"]
    score = 0
    flags = []

    try:
        # Get available expiration dates
        expirations = stock_obj.options
        if not expirations:
            return {"score": 0, "max": 6, "flags": ["No options data available"]}

        # Look at nearest 2 expirations
        total_call_vol = 0
        total_put_vol = 0
        total_call_oi = 0
        total_put_oi = 0
        high_iv_calls = 0

        for exp in expirations[:2]:
            try:
                chain = stock_obj.option_chain(exp)
                calls = chain.calls
                puts = chain.puts

                total_call_vol += calls["volume"].sum() if "volume" in calls.columns else 0
                total_put_vol += puts["volume"].sum() if "volume" in puts.columns else 0
                total_call_oi += calls["openInterest"].sum() if "openInterest" in calls.columns else 0
                total_put_oi += puts["openInterest"].sum() if "openInterest" in puts.columns else 0

                # Count OTM calls with unusual volume
                current_price = data["price"]
                otm_calls = calls[calls["strike"] > current_price * 1.1]
                if not otm_calls.empty and "volume" in otm_calls.columns:
                    avg_otm_vol = otm_calls["volume"].mean()
                    if avg_otm_vol > 100:
                        high_iv_calls += 1

            except Exception:
                continue

        # Call/Put volume ratio
        if total_put_vol > 0:
            cp_ratio = total_call_vol / total_put_vol
            if cp_ratio > 3.0:
                score += 2
                flags.append(f"HEAVY call buying: C/P ratio {cp_ratio:.1f}")
            elif cp_ratio > 1.5:
                score += 1
                flags.append(f"Bullish call/put ratio: {cp_ratio:.1f}")

        # Call OI growth (indicates positioning)
        if total_call_oi > total_put_oi * 2:
            score += 1
            flags.append(f"Call OI dominates: {total_call_oi:,.0f} vs {total_put_oi:,.0f} puts")

        # OTM call activity
        if high_iv_calls > 0:
            score += 2
            flags.append(f"Unusual OTM call activity detected")

        # Total options volume vs stock volume
        total_opt_vol = total_call_vol + total_put_vol
        stock_vol = data["volume_current"]
        if stock_vol > 0 and total_opt_vol > stock_vol * 0.5:
            score += 1
            flags.append(f"High options/stock volume ratio")

    except Exception as e:
        flags.append(f"Options data unavailable")

    return {"score": score, "max": 6, "flags": flags}


def score_technical_setup(data: dict) -> dict:
    """
    Technical momentum setup score.

    Looks for patterns that preceded big runs:
    - Price crossing above key MAs
    - RSI recovering from oversold (not already overbought)
    - MACD bullish crossover
    - Relative strength vs market
    """
    hist = data["history"]
    close = hist["Close"]
    score = 0
    flags = []

    if len(close) < 50:
        return {"score": 0, "max": 7, "flags": ["Insufficient history"]}

    current = close.iloc[-1]

    # EMA crossovers
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    # Price above rising EMAs (trend confirmation)
    if current > ema9.iloc[-1] > ema21.iloc[-1] > ema50.iloc[-1]:
        score += 2
        flags.append("Bullish EMA stack: price > 9 > 21 > 50")
    elif current > ema21.iloc[-1] and ema9.iloc[-1] > ema21.iloc[-1]:
        score += 1
        flags.append("9 EMA crossed above 21 — early momentum")

    # RSI — sweet spot is 40-65 (not overbought, but showing strength)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=13, min_periods=14).mean()
    avg_loss = loss.ewm(com=13, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    current_rsi = rsi.iloc[-1]

    if 40 <= current_rsi <= 60:
        score += 2
        flags.append(f"RSI in sweet spot: {current_rsi:.0f} (room to run)")
    elif 30 <= current_rsi < 40:
        score += 1
        flags.append(f"RSI recovering from oversold: {current_rsi:.0f}")
    elif current_rsi > 70:
        score -= 1
        flags.append(f"⚠️ RSI overbought: {current_rsi:.0f} — late entry risk")

    # MACD crossover
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()

    if macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2]:
        score += 2
        flags.append("MACD bullish crossover — fresh signal")
    elif macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-1] > 0:
        score += 1
        flags.append("MACD positive and above signal line")

    # Distance from 52-week low (early recovery = more upside)
    low_52w = close.min()
    pct_from_low = current / low_52w - 1
    if 0.10 < pct_from_low < 0.40:
        score += 1
        flags.append(f"Early recovery: {pct_from_low:.0%} off 52-week low")

    return {"score": score, "max": 7, "flags": flags}


# ==============================================================================
# SECTION 3: POLYMARKET SIGNAL
# ==============================================================================

def score_polymarket_signal(ticker: str, poly_signals: Dict[str, list]) -> dict:
    """
    Polymarket prediction market score for a ticker.

    Uses pre-fetched signals (dict keyed by ticker) to avoid one API call
    per stock. The best-matching market's signal_score (0–1) drives the
    catalyst score (0–5 scale, same as social).

    Scoring:
      signal_score >= 0.70  → 3 pts  (strong consensus + liquid market)
      signal_score >= 0.50  → 2 pts  (moderate consensus)
      signal_score >= 0.30  → 1 pt   (weak / thin market)
      bullish trend bonus   → +1 pt  (crowd moving in bullish direction)
      multiple markets      → +1 pt  (corroborating signals)
    """
    score = 0
    flags = []

    signals = poly_signals.get(ticker, [])
    if not signals:
        return {"score": 0, "max": 5, "flags": []}

    # Best signal by score
    best = signals[0]
    sig_score = best.get("signal_score", 0)
    prediction = best.get("prediction", 0.5)
    trend = best.get("trend", "unknown")
    question = best.get("question", "")[:70]
    dtr = best.get("days_to_resolution")

    # Base score from signal strength
    if sig_score >= 0.70:
        score += 3
        flags.append(f"Strong Polymarket signal ({sig_score:.2f}): {question}")
    elif sig_score >= 0.50:
        score += 2
        flags.append(f"Moderate Polymarket signal ({sig_score:.2f}): {question}")
    elif sig_score >= 0.30:
        score += 1
        flags.append(f"Weak Polymarket signal ({sig_score:.2f}): {question}")

    # Trend bonus
    if trend == "bullish":
        score += 1
        flags.append(f"Polymarket crowd turning bullish (pred={prediction:.0%})")
    elif trend == "bearish":
        flags.append(f"⚠️ Polymarket crowd bearish (pred={prediction:.0%})")

    # Multiple corroborating markets
    if len(signals) >= 2:
        score += 1
        flags.append(f"{len(signals)} Polymarket markets active for this ticker")

    # Imminent resolution context
    if dtr is not None and dtr <= 14:
        flags.append(f"Resolves in {dtr} days — near-term catalyst confirmed")

    return {"score": min(score, 5), "max": 5, "flags": flags}


# ==============================================================================
# SECTION 3B: SOCIAL SENTIMENT (Reddit)
# ==============================================================================

def scan_reddit_mentions(tickers: List[str]) -> Dict[str, dict]:
    """
    Scan Reddit for ticker mentions using public JSON API.
    No API key needed — just rate-limited.

    Scans 3 feeds per subreddit (hot, new, top/week) to catch:
    - hot: currently trending posts
    - new: fresh posts that haven't gained traction yet
    - top (week): high-engagement weekday posts that fell off hot by Sunday

    Lookback: 7 days (captures full Mon-Sun cycle for weekly runs).

    Checks: r/wallstreetbets, r/stocks, r/investing, r/options,
            r/smallstreetbets, r/squeezeplays

    NOTE: For production use, consider the official Reddit API with
    proper auth, or services like Quiver Quantitative or SwaggyStocks.
    """
    import re
    import urllib.request

    subreddits = ["wallstreetbets", "stocks", "investing", "options",
                  "smallstreetbets", "squeezeplays"]
    feeds = ["hot", "new", "top"]  # top defaults to week on Reddit
    LOOKBACK_HOURS = 168  # 7 days

    mention_counts = {t: {
        "total": 0, "weighted_total": 0.0, "subreddits": {},
        "recent_posts": [], "seen_ids": set(),
        "weekday_mentions": 0, "weekend_mentions": 0,
    } for t in tickers}

    headers = {"User-Agent": "SignalEngine/1.0 (educational research tool)"}

    print(f"\n  Scanning Reddit (7-day window, {len(subreddits)} subs, {len(feeds)} feeds each)...")

    for sub in subreddits:
        for feed in feeds:
            try:
                if feed == "top":
                    url = f"https://www.reddit.com/r/{sub}/top.json?t=week&limit=100"
                else:
                    url = f"https://www.reddit.com/r/{sub}/{feed}.json?limit=100"

                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())

                posts = data.get("data", {}).get("children", [])

                for post in posts:
                    pdata = post.get("data", {})
                    post_id = pdata.get("id", "")
                    title = (pdata.get("title", "") or "").upper()
                    selftext = (pdata.get("selftext", "") or "").upper()
                    text = title + " " + selftext
                    upvotes = pdata.get("ups", 0)
                    comments = pdata.get("num_comments", 0)
                    created = pdata.get("created_utc", 0)

                    age_hours = (time.time() - created) / 3600
                    if age_hours > LOOKBACK_HOURS:
                        continue

                    # Recency weight: posts from last 24h = 1.0x,
                    # 1-3 days = 0.7x, 3-5 days = 0.4x, 5-7 days = 0.2x
                    if age_hours <= 24:
                        recency_weight = 1.0
                    elif age_hours <= 72:
                        recency_weight = 0.7
                    elif age_hours <= 120:
                        recency_weight = 0.4
                    else:
                        recency_weight = 0.2

                    # Weekday vs weekend (0=Mon, 6=Sun)
                    from datetime import datetime as dt
                    post_day = dt.utcfromtimestamp(created).weekday()
                    is_weekday = post_day < 5

                    for ticker in tickers:
                        pattern = r'(?:^|\s|\$)' + re.escape(ticker) + r'(?:\s|$|[,.\!\?])'
                        if re.search(pattern, text):
                            # Deduplicate across feeds
                            if post_id in mention_counts[ticker]["seen_ids"]:
                                continue
                            mention_counts[ticker]["seen_ids"].add(post_id)

                            mention_counts[ticker]["total"] += 1
                            mention_counts[ticker]["weighted_total"] += recency_weight
                            mention_counts[ticker]["subreddits"][sub] = \
                                mention_counts[ticker]["subreddits"].get(sub, 0) + 1

                            if is_weekday:
                                mention_counts[ticker]["weekday_mentions"] += 1
                            else:
                                mention_counts[ticker]["weekend_mentions"] += 1

                            if len(mention_counts[ticker]["recent_posts"]) < 5:
                                mention_counts[ticker]["recent_posts"].append({
                                    "subreddit": sub,
                                    "title": pdata.get("title", "")[:80],
                                    "upvotes": upvotes,
                                    "comments": comments,
                                    "age_hours": round(age_hours, 1),
                                    "recency_weight": recency_weight,
                                })

                time.sleep(1)  # Rate limit: 1 request per second

            except Exception as e:
                print(f"    [WARN] r/{sub}/{feed}: {e}")
                continue

    # Clean up non-serializable sets before returning
    for t in tickers:
        del mention_counts[t]["seen_ids"]

    return mention_counts


def score_social_momentum(ticker: str, mentions: dict) -> dict:
    """
    Score based on Reddit mention velocity, engagement, and recency.

    Uses weighted_total (recency-adjusted) so a weekday post from
    Tuesday still counts on Sunday, just at reduced weight.
    """
    score = 0
    flags = []
    data = mentions.get(ticker, {"total": 0, "weighted_total": 0, "recent_posts": []})

    total = data["total"]
    weighted = data.get("weighted_total", 0)
    weekday = data.get("weekday_mentions", 0)
    weekend = data.get("weekend_mentions", 0)

    # Score on weighted total (recency-adjusted)
    if weighted >= 8:
        score += 3
        flags.append(f"🔥 HIGH social buzz: {total} mentions (7d), weighted score {weighted:.1f}")
    elif weighted >= 4:
        score += 2
        flags.append(f"Rising social interest: {total} mentions (7d), weighted {weighted:.1f}")
    elif weighted >= 1.5:
        score += 1
        flags.append(f"Some social activity: {total} mentions (7d), weighted {weighted:.1f}")

    # Weekday activity indicator
    if weekday > 0 and total > 0:
        weekday_pct = weekday / total * 100
        if weekday >= 3:
            flags.append(f"Active weekday discussion: {weekday} weekday posts ({weekday_pct:.0f}%)")

    # High-engagement posts
    for post in data.get("recent_posts", []):
        if post["upvotes"] > 500:
            score += 1
            flags.append(f"Viral post ({post['upvotes']} upvotes): {post['title'][:50]}...")
            break

    # Multi-subreddit spread (organic vs coordinated)
    n_subs = len(data.get("subreddits", {}))
    if n_subs >= 3:
        score += 1
        flags.append(f"Mentioned across {n_subs} subreddits — organic spread")

    return {"score": score, "max": 5, "flags": flags}


# ==============================================================================
# SECTION 4: COMPOSITE SCORING & REPORTING
# ==============================================================================

def screen_universe(
    tickers: List[str],
    include_social: bool = False,
    include_polymarket: bool = False,
) -> pd.DataFrame:
    """Run all screens on a universe of tickers."""

    print(f"\n{'█' * 60}")
    print(f"  CATALYST SCREENER v1.0")
    print(f"  Universe: {len(tickers)} tickers | Date: {datetime.now().strftime('%Y-%m-%d')}")
    print(f"{'█' * 60}")

    # Social data (if requested)
    social_data = {}
    if include_social:
        social_data = scan_reddit_mentions(tickers)

    # Polymarket data (if requested and available)
    poly_data: Dict[str, list] = {}
    if include_polymarket:
        if _POLYMARKET_AVAILABLE:
            try:
                pm = PolymarketScreener()
                for ticker in tickers:
                    sigs = pm.screen_ticker(ticker)
                    if sigs:
                        poly_data[ticker] = sigs
                print(f"  Polymarket: {sum(len(v) for v in poly_data.values())} signals across {len(poly_data)} tickers")
            except Exception as e:
                print(f"  [WARN] Polymarket fetch failed: {e}")
        else:
            print("  [WARN] polymarket_screener.py not found — skipping Polymarket signals")

    results = []
    total = len(tickers)

    for i, ticker in enumerate(tickers):
        print(f"\r  Scanning: {ticker:<8} ({i+1}/{total})", end="", flush=True)

        data = get_stock_data(ticker)
        if data is None:
            continue

        # Compute all scores
        squeeze = score_short_squeeze(data)
        volume = score_volume_breakout(data)
        vol_squeeze = score_volatility_squeeze(data)
        options = score_options_activity(data)
        technical = score_technical_setup(data)

        social = {"score": 0, "max": 5, "flags": []}
        if include_social:
            social = score_social_momentum(ticker, social_data)

        polymarket = {"score": 0, "max": 5, "flags": []}
        if include_polymarket:
            polymarket = score_polymarket_signal(ticker, poly_data)

        # Composite score (weighted)
        max_possible = (squeeze["max"] + volume["max"] + vol_squeeze["max"] +
                        options["max"] + technical["max"] + social["max"] +
                        polymarket["max"])
        raw_total = (squeeze["score"] + volume["score"] + vol_squeeze["score"] +
                     options["score"] + technical["score"] + social["score"] +
                     polymarket["score"])

        composite = raw_total / max_possible * 100 if max_possible > 0 else 0

        # All flags combined
        all_flags = (squeeze["flags"] + volume["flags"] + vol_squeeze["flags"] +
                     options["flags"] + technical["flags"] + social["flags"] +
                     polymarket["flags"])

        results.append({
            "ticker": ticker,
            "price": data["price"],
            "mkt_cap_m": data["market_cap"] / 1e6 if data["market_cap"] else 0,
            "short_pct": data["short_pct_float"],
            "vol_ratio": data["volume_avg_5d"] / max(data["volume_avg_20d"], 1),
            "squeeze_score": squeeze["score"],
            "volume_score": volume["score"],
            "vol_compress": vol_squeeze["score"],
            "options_score": options["score"],
            "technical_score": technical["score"],
            "social_score": social["score"],
            "polymarket_score": polymarket["score"],
            "composite": round(composite, 1),
            "flags": all_flags,
            "n_flags": len(all_flags),
        })

        time.sleep(0.3)  # Rate limit

    print(f"\r  Scanning complete: {len(results)} tickers analyzed" + " " * 20)

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values("composite", ascending=False)

    return df


def print_results(df: pd.DataFrame, top_n: int = 20):
    """Print formatted screening results."""
    if df.empty:
        print("\n  No results.")
        return

    print(f"\n{'─' * 60}")
    print(f"  🔍 TOP {min(top_n, len(df))} CATALYST CANDIDATES")
    print(f"{'─' * 60}")

    has_poly = "polymarket_score" in df.columns and df["polymarket_score"].sum() > 0

    print(f"\n  {'Rank':<5}{'Ticker':<8}{'Price':>10}{'MktCap':>10}"
          f"{'Short%':>8}{'VolRatio':>9}{'Squeeze':>8}{'Volume':>8}"
          f"{'VolComp':>8}{'Options':>8}{'Tech':>7}{'Social':>7}"
          + (f"{'Poly':>6}" if has_poly else "")
          + f"{'TOTAL':>8}")
    print(f"  {'─' * (104 + (6 if has_poly else 0))}")

    for i, (_, row) in enumerate(df.head(top_n).iterrows()):
        # Highlight tier
        if row["composite"] >= 50:
            tier = "🔥"
        elif row["composite"] >= 35:
            tier = "🟡"
        else:
            tier = "  "

        mkt = f"${row['mkt_cap_m']/1000:.1f}B" if row["mkt_cap_m"] > 1000 else f"${row['mkt_cap_m']:.0f}M"

        line = (f"  {i+1:<5}{row['ticker']:<8}"
                f"${row['price']:>9.2f}"
                f"{mkt:>10}"
                f"{row['short_pct']:>7.0%}"
                f"{row['vol_ratio']:>8.1f}x"
                f"{row['squeeze_score']:>6}/9"
                f"{row['volume_score']:>6}/7"
                f"{row['vol_compress']:>6}/6"
                f"{row['options_score']:>6}/6"
                f"{row['technical_score']:>5}/7"
                f"{row['social_score']:>5}/5")
        if has_poly:
            line += f"{row['polymarket_score']:>4}/5"
        line += f" {tier}{row['composite']:>5.0f}%"
        print(line)

    # Detail on top 5
    print(f"\n{'─' * 60}")
    print(f"  📋 DETAILED FLAGS — TOP 5")
    print(f"{'─' * 60}")

    for _, row in df.head(5).iterrows():
        print(f"\n  {row['ticker']} — Composite: {row['composite']:.0f}%")
        for flag in row["flags"]:
            print(f"    • {flag}")


def deep_dive(ticker: str, include_social: bool = False, include_polymarket: bool = False):
    """Single-stock deep analysis."""
    print(f"\n{'█' * 60}")
    print(f"  DEEP DIVE: {ticker}")
    print(f"{'█' * 60}")

    data = get_stock_data(ticker)
    if data is None:
        print(f"  [ERROR] Could not fetch data for {ticker}")
        return

    print(f"\n  Price: ${data['price']:,.2f}")
    print(f"  Market Cap: ${data['market_cap']/1e9:.2f}B" if data['market_cap'] > 1e9
          else f"  Market Cap: ${data['market_cap']/1e6:.0f}M")
    print(f"  Float: {data['float_shares']/1e6:.1f}M shares")
    print(f"  Short % of Float: {data['short_pct_float']:.1%}")
    print(f"  Days to Cover: {data['short_ratio_dtc']:.1f}")
    print(f"  Institutional Ownership: {data['inst_ownership']:.1%}")
    print(f"  Volume (5d avg / 20d avg): {data['volume_avg_5d']/1e6:.1f}M / {data['volume_avg_20d']/1e6:.1f}M")

    # All scores
    for name, func in [
        ("SHORT SQUEEZE SETUP", score_short_squeeze),
        ("VOLUME BREAKOUT", score_volume_breakout),
        ("VOLATILITY COMPRESSION", score_volatility_squeeze),
        ("OPTIONS ACTIVITY", score_options_activity),
        ("TECHNICAL SETUP", score_technical_setup),
    ]:
        result = func(data)
        print(f"\n  {name}: {result['score']}/{result['max']}")
        for flag in result["flags"]:
            print(f"    • {flag}")

    if include_social:
        mentions = scan_reddit_mentions([ticker])
        social = score_social_momentum(ticker, mentions)
        print(f"\n  SOCIAL MOMENTUM: {social['score']}/{social['max']}")
        for flag in social["flags"]:
            print(f"    • {flag}")

    if include_polymarket:
        if _POLYMARKET_AVAILABLE:
            try:
                pm = PolymarketScreener()
                sigs = pm.screen_ticker(ticker)
                poly = score_polymarket_signal(ticker, {ticker: sigs})
                print(f"\n  POLYMARKET SIGNAL: {poly['score']}/{poly['max']}")
                for flag in poly["flags"]:
                    print(f"    • {flag}")
                if sigs:
                    print(f"\n  Top Polymarket markets for {ticker}:")
                    for s in sigs[:3]:
                        print(f"    [{s['signal_score']:.2f}] {s['question'][:70]}")
                        print(f"           Prediction: {s['prediction']:.0%} | "
                              f"Vol24h: ${s['volume_24h']:,.0f} | "
                              f"Resolves: {s.get('time_to_resolution', '?')}")
            except Exception as e:
                print(f"\n  [WARN] Polymarket fetch failed: {e}")
        else:
            print("\n  [WARN] polymarket_screener.py not found — skipping Polymarket signals")


# ==============================================================================
# SECTION 5: MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Catalyst Screener v1.0")
    parser.add_argument("--universe", choices=["small", "meme", "large", "all"],
                        default="all", help="Which universe to scan")
    parser.add_argument("--ticker", type=str, help="Single stock deep dive")
    parser.add_argument("--social", action="store_true", help="Include Reddit scan")
    parser.add_argument("--polymarket", action="store_true", help="Include Polymarket prediction market signals")
    parser.add_argument("--top", type=int, default=20, help="Show top N results")
    args = parser.parse_args()

    if args.ticker:
        deep_dive(args.ticker.upper(), include_social=args.social, include_polymarket=args.polymarket)
        return

    # Build universe
    if args.universe == "small":
        universe = SMALL_CAP_UNIVERSE
    elif args.universe == "meme":
        universe = MEME_UNIVERSE
    elif args.universe == "large":
        universe = LARGE_CAP_WATCH
    else:
        universe = list(set(SMALL_CAP_UNIVERSE + MEME_UNIVERSE + LARGE_CAP_WATCH))

    results = screen_universe(universe, include_social=args.social, include_polymarket=args.polymarket)

    if not results.empty:
        print_results(results, top_n=args.top)

        # Export
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        path = os.path.join(OUTPUT_DIR, f"catalyst_screen_{date_str}.csv")
        export = results.drop(columns=["flags"])
        export.to_csv(path, index=False)
        print(f"\n  📁 Exported to: {path}")

    print(f"\n{'█' * 60}")
    print(f"  ⚠️  THESE ARE SETUP CONDITIONS, NOT BUY SIGNALS.")
    print(f"  Most setups fail. Size positions accordingly.")
    print(f"  Max 1-2% of NAV per speculative position.")
    print(f"  THIS IS NOT INVESTMENT ADVICE.")
    print(f"{'█' * 60}\n")


if __name__ == "__main__":
    main()
