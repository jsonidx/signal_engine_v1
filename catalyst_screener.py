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
    from config import OUTPUT_DIR, UNIVERSE_CACHE_TTL_HOURS as _UNIVERSE_TTL
except ImportError:
    OUTPUT_DIR = "./signals_output"
    _UNIVERSE_TTL = 24

try:
    import universe_builder as _ub
    _UNIVERSE_BUILDER_AVAILABLE = True
except ImportError:
    _ub = None  # type: ignore[assignment]
    _UNIVERSE_BUILDER_AVAILABLE = False

try:
    from polymarket_screener import PolymarketScreener
    _POLYMARKET_AVAILABLE = True
except ImportError:
    _POLYMARKET_AVAILABLE = False


try:
    import dark_pool_flow as _dpf
    _DARK_POOL_AVAILABLE = True
except ImportError:
    _dpf = None  # type: ignore[assignment]
    _DARK_POOL_AVAILABLE = False

try:
    from social_sentiment import get_combined_social_score as _get_social_score
    _SOCIAL_SENTIMENT_AVAILABLE = True
except ImportError:
    _get_social_score = None  # type: ignore[assignment]
    _SOCIAL_SENTIMENT_AVAILABLE = False

try:
    from squeeze_screener import detect_recent_squeeze as _squeeze_detect_recent
    _SQUEEZE_GUARD_AVAILABLE = True
except ImportError:
    _squeeze_detect_recent = None  # type: ignore[assignment]
    _SQUEEZE_GUARD_AVAILABLE = False


# ==============================================================================
# UNIVERSES — 100% dynamic
# ==============================================================================

def _load_dynamic_universe() -> list:
    """
    Load the screening universe from dynamic sources only (no hardcoded tickers).

    Priority:
      1. universe_builder dynamic universe (if built)
      2. watchlist.txt
      3. Supabase user_favorites (via favorites.py)

    Returns deduplicated list of uppercase ticker strings.
    """
    tickers: list[str] = []

    # 1. universe_builder
    if _UNIVERSE_BUILDER_AVAILABLE:
        try:
            built = _ub.build_master_universe()
            if built:
                tickers = list(built)
        except Exception as exc:
            pass  # fall through

    # 2. watchlist.txt
    if not tickers:
        tickers = _read_watchlist_tickers()

    # 3. user_favorites
    if not tickers:
        try:
            from favorites import load_favorites
            tickers = load_favorites()
        except Exception:
            pass

    return list(dict.fromkeys(t.upper() for t in tickers if t))


def _read_watchlist_tickers() -> list:
    """Parse watchlist.txt, extracting ticker before any # comment."""
    paths = [
        os.path.join(os.path.dirname(__file__), "watchlist.txt"),
        "./watchlist.txt",
    ]
    for path in paths:
        if os.path.exists(path):
            result = []
            with open(path) as f:
                for line in f:
                    clean = line.split("#")[0].strip().upper()
                    if clean and not clean.startswith("TIER") and not clean.startswith("MANUALLY") and "." not in clean:
                        result.append(clean)
            return result
    return []


# Backward-compat aliases — point to empty lists; callers that used
# the old hardcoded names should call _load_dynamic_universe() instead.
SMALL_CAP_UNIVERSE: list = []
MEME_UNIVERSE:      list = []
LARGE_CAP_WATCH:    list = []


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
# SECTION 3B: SOCIAL SENTIMENT (Google Trends + StockTwits)
# ==============================================================================

def score_social_sentiment(ticker: str, data: dict) -> dict:
    """
    Social sentiment score using Google Trends search-volume and StockTwits
    public stream — both free and unauthenticated.

    Replaces the previous Reddit scan_reddit_mentions() approach which broke
    when Reddit enforced API authentication in 2023.

    Scoring (0–5 scale, max=5):
      score_100 > 75  → 5  (strongly bullish across both sources)
      score_100 > 65  → 4  (bullish)
      score_100 > 58  → 3  (mildly bullish)
      score_100 42–58 → 2  (neutral — slight positive for any social attention)
      score_100 < 35  → 1  (bearish)
      score_100 < 25  → 0  (strongly bearish)
      no data         → 0  (graceful degradation)

    Delegates to social_sentiment.get_combined_social_score() which handles
    caching, rate-limiting, and single-source confidence discounting.
    """
    if not _SOCIAL_SENTIMENT_AVAILABLE:
        return {"score": 0, "max": 5, "flags": ["[social_sentiment.py not found]"]}

    info = data.get("info") or {}
    company_name = info.get("longName") or None

    try:
        combined = _get_social_score(ticker, company_name=company_name)
    except Exception as exc:
        return {"score": 0, "max": 5, "flags": [f"[social error: {exc}]"]}

    score_100 = combined.get("score")
    if score_100 is None:
        return {
            "score": 0,
            "max": 5,
            "flags": ["Social data unavailable (Trends + StockTwits both failed)"],
        }

    # Map 0–100 → 0–5
    if score_100 > 75:
        mapped = 5
    elif score_100 > 65:
        mapped = 4
    elif score_100 > 58:
        mapped = 3
    elif score_100 >= 25:
        mapped = 2 if score_100 >= 42 else 1
    else:
        mapped = 0

    flags: List[str] = []
    interp = combined.get("interpretation", "NEUTRAL")
    trends_sig = combined.get("trends_signal") or "N/A"
    twits_sig = combined.get("twits_signal") or "N/A"
    sources = combined.get("sources", [])

    label = f"score {score_100}/100 | Trends={trends_sig} | StockTwits={twits_sig}"
    if interp in ("BULLISH", "MILDLY_BULLISH"):
        flags.append(f"Positive social signal ({label})")
    elif interp in ("BEARISH", "MILDLY_BEARISH"):
        flags.append(f"⚠️  Negative social signal ({label})")
    else:
        src_str = "+".join(sources) if sources else "no sources"
        flags.append(f"Neutral social signal ({label}) [{src_str}]")

    return {"score": mapped, "max": 5, "flags": flags}


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

    # Social data is fetched per-ticker (cached inside social_sentiment.py);
    # no bulk pre-scan is needed for the new Google Trends + StockTwits approach.
    if include_social and not _SOCIAL_SENTIMENT_AVAILABLE:
        print("  [WARN] social_sentiment.py not found — install pytrends and retry")

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

    # Dark pool — pre-load FINRA files once; try result cache first (from run_master.sh)
    _dp_result_cache: Dict[str, dict] = {}
    _dp_preloaded: Dict = {}
    if _DARK_POOL_AVAILABLE:
        try:
            _dp_result_cache = _dpf.load_result_cache()
            if _dp_result_cache:
                print(f"  Dark pool: loaded {len(_dp_result_cache)} tickers from today's cache")
            else:
                print(f"  Dark pool: pre-loading FINRA files (~20 business days)...")
                _bdays = _dpf._prev_business_days(27)
                for _day in _bdays:
                    _ds = _day.strftime("%Y%m%d")
                    _df = _dpf._fetch_single_date(_day)
                    if _df is not None:
                        _dp_preloaded[_ds] = _df
                print(f"  Dark pool: {len(_dp_preloaded)} FINRA date files loaded")
        except Exception as _e:
            print(f"  [WARN] Dark pool pre-load failed: {_e}")

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
            social = score_social_sentiment(ticker, data)

        polymarket = {"score": 0, "max": 5, "flags": []}
        if include_polymarket:
            polymarket = score_polymarket_signal(ticker, poly_data)

        # Dark pool: +2 bonus (ACCUMULATION) or -1 penalty (DISTRIBUTION)
        dark_pool = {"score": 0, "max": 2, "flags": [], "signal": "NEUTRAL", "detail": None}
        if _DARK_POOL_AVAILABLE:
            try:
                dark_pool = _dpf.score_dark_pool(
                    ticker,
                    preloaded_frames=_dp_preloaded if _dp_preloaded else None,
                    result_cache=_dp_result_cache if _dp_result_cache else None,
                )
            except Exception:
                pass

        # Composite score
        max_possible = (squeeze["max"] + volume["max"] + vol_squeeze["max"] +
                        options["max"] + technical["max"] + social["max"] +
                        polymarket["max"] + dark_pool["max"])
        raw_total = (squeeze["score"] + volume["score"] + vol_squeeze["score"] +
                     options["score"] + technical["score"] + social["score"] +
                     polymarket["score"] + dark_pool["score"])

        composite = raw_total / max_possible * 100 if max_possible > 0 else 0

        all_flags = (squeeze["flags"] + volume["flags"] + vol_squeeze["flags"] +
                     options["flags"] + technical["flags"] + social["flags"] +
                     polymarket["flags"] + dark_pool["flags"])

        # Cross-module post-squeeze guard (mirrors squeeze_screener.py logic).
        # Zeroes the composite score when a squeeze has already fired — the
        # setup is spent; entries before the price resets are low-probability.
        _recent_sq = False
        if _SQUEEZE_GUARD_AVAILABLE:
            try:
                _recent_sq = bool(_squeeze_detect_recent(data))
            except Exception:
                pass
        if _recent_sq:
            composite = 0.0
            all_flags.insert(
                0,
                "POST-SQUEEZE GUARD — squeeze already fired, score zeroed. "
                "Wait for price reset before re-entry.",
            )

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
            "dark_pool_score": dark_pool["score"],
            "dark_pool_signal": dark_pool["signal"],
            "post_squeeze_guard": bool(_recent_sq),
            "composite": round(composite, 1),
            "flags": all_flags,
            "n_flags": len(all_flags),
        })

        time.sleep(0.3)  # Rate limit

    print(f"\r  Scanning complete: {len(results)} tickers analyzed" + " " * 20)

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values("composite", ascending=False)
        # Keep post_squeeze_guard as Python bool so `is True` comparisons work
        if "post_squeeze_guard" in df.columns:
            df["post_squeeze_guard"] = df["post_squeeze_guard"].astype(object)

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
    has_dark_pool = "dark_pool_score" in df.columns and df["dark_pool_score"].abs().sum() > 0

    print(f"\n  {'Rank':<5}{'Ticker':<8}{'Price':>10}{'MktCap':>10}"
          f"{'Short%':>8}{'VolRatio':>9}{'Squeeze':>8}{'Volume':>8}"
          f"{'VolComp':>8}{'Options':>8}{'Tech':>7}{'Social':>7}"
          + (f"{'Poly':>6}" if has_poly else "")
          + (f"{'DkPool':>7}" if has_dark_pool else "")
          + f"{'TOTAL':>8}")
    print(f"  {'─' * (104 + (6 if has_poly else 0) + (7 if has_dark_pool else 0))}")

    for i, (_, row) in enumerate(df.head(top_n).iterrows()):
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
        if has_dark_pool:
            dp_s = row.get("dark_pool_score", 0)
            dp_icon = "↑" if dp_s > 0 else ("↓" if dp_s < 0 else " ")
            line += f"  {dp_icon}{dp_s:+d}"
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

    # Dark pool flow
    if _DARK_POOL_AVAILABLE:
        try:
            dp = _dpf.score_dark_pool(ticker)
            detail = dp.get("detail") or {}
            dp_score_str = f"{detail.get('dark_pool_score', '?')}/100" if detail else "no data"
            print(f"\n  DARK POOL FLOW (FINRA ATS): {dp_score_str}  [{dp['signal']}]")
            for flag in dp["flags"]:
                print(f"    • {flag}")
            if detail:
                print(f"    Short ratio today  : {detail.get('short_ratio_today', 0):.3%}")
                print(f"    Short ratio trend  : {detail.get('short_ratio_trend', 0):+.5f}/day")
                print(f"    Dark pool intensity: {detail.get('dark_pool_intensity', 0):.1%}")
                print(f"    Days of FINRA data : {detail.get('days_of_data', 0)}")
        except Exception as _e:
            print(f"\n  DARK POOL FLOW: unavailable ({_e})")

    if include_social:
        social = score_social_sentiment(ticker, data)
        print(f"\n  SOCIAL SENTIMENT (Trends + StockTwits): {social['score']}/{social['max']}")
        for flag in social["flags"]:
            print(f"    • {flag}")

    # Post-squeeze guard status in deep-dive
    if _SQUEEZE_GUARD_AVAILABLE:
        try:
            if _squeeze_detect_recent(data):
                print("\n  ⚠️  POST-SQUEEZE GUARD ACTIVE — squeeze already fired. Wait for reset.")
        except Exception:
            pass

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
# SECTION 5: WATCHLIST MANAGEMENT
# ==============================================================================

WATCHLIST_PATH = "./watchlist.txt"
WATCHLIST_HISTORY_PATH = "./watchlist_history.json"
AUTO_ADD_THRESHOLD = 40.0  # composite % to auto-add new tickers to watchlist


def _read_all_watchlist_tickers(path: str) -> Tuple[set, set]:
    """
    Parse watchlist.txt and return:
      - all_tickers:    every ticker currently in the file (deduplicated)
      - manual_tickers: tickers in the "MANUALLY ADDED" section
    Handles both the old free-form format and the new tiered/annotated format.
    """
    if not os.path.exists(path):
        return set(), set()

    all_tickers: set = set()
    manual_tickers: set = set()
    in_manual_section = False

    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                upper = line.upper()
                if "MANUALLY ADDED" in upper:
                    in_manual_section = True
                elif any(kw in upper for kw in ("TIER 1", "TIER 2", "TIER 3",
                                                 "HOT", "WATCH", "MONITOR")):
                    in_manual_section = False
                continue
            # Strip inline comment, normalise
            ticker = line.split("#")[0].strip().upper()
            if ticker:
                all_tickers.add(ticker)
                if in_manual_section:
                    manual_tickers.add(ticker)

    return all_tickers, manual_tickers


def _load_watchlist_history(path: str) -> dict:
    """Load watchlist_history.json; return {} if missing or corrupt."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_watchlist_history(history: dict, path: str):
    with open(path, "w") as f:
        json.dump(history, f, indent=2)


def _rank_annotation(ticker: str, history: dict) -> Tuple[str, str]:
    """
    Returns (arrow_str, note_str) for a ticker based on its history.
    arrow_str: '↑3', '↓1', '→'
    note_str:  ' 🔥 deep dive candidate' if 2+ consecutive rank rises, else ''
    """
    entries = history.get(ticker, [])
    if not entries:
        return "NEW", ""

    delta = entries[-1].get("delta", 0)
    if delta > 0:
        arrow = f"↑{delta}"
    elif delta < 0:
        arrow = f"↓{abs(delta)}"
    else:
        arrow = "→"

    # Count consecutive rank rises (positive delta) from the tail
    consec_rises = 0
    for e in reversed(entries):
        if e.get("delta", 0) > 0:
            consec_rises += 1
        else:
            break

    note = " 🔥 deep dive candidate" if consec_rises >= 2 else ""
    return arrow, note


def _format_ticker_line(ticker: str, composite: float,
                        history: dict, is_new: bool) -> str:
    """Build a single annotated watchlist line."""
    arrow, note = _rank_annotation(ticker, history)

    entries = history.get(ticker, [])
    rank_move = ""
    if len(entries) >= 2:
        prev = entries[-2]["rank"]
        curr = entries[-1]["rank"]
        if prev != curr:
            rank_move = f" | rank {prev}→{curr}"

    new_tag = " [NEW]" if is_new else ""
    return f"{ticker:<10} # {arrow:<5} {composite:.0f}%{rank_move}{note}{new_tag}"


def update_watchlist(
    results_df: pd.DataFrame,
    watchlist_path: str = WATCHLIST_PATH,
    history_path: str = WATCHLIST_HISTORY_PATH,
    auto_add_threshold: float = AUTO_ADD_THRESHOLD,
):
    """
    Merge screener results into watchlist.txt and append to watchlist_history.json.

    Rules:
    - Existing watchlist tickers always stay (never deleted)
    - Screener top picks above threshold get auto-added
    - Tickers re-ranked each run by composite score; delta vs last run shown inline
    - Tickers not scanned this run fall into MANUALLY ADDED section
    - History is append-only
    """
    today = datetime.now().strftime("%Y-%m-%d")

    # Current watchlist state
    existing, manual = _read_all_watchlist_tickers(watchlist_path)
    history = _load_watchlist_history(history_path)

    # Build screener results map
    screened: Dict[str, float] = {}
    if not results_df.empty:
        for _, row in results_df.iterrows():
            screened[row["ticker"]] = float(row["composite"])

    # Auto-add new high-scorers not yet on watchlist
    new_auto_adds = {
        t for t, c in screened.items()
        if t not in existing and c >= auto_add_threshold
    }

    all_tracked = existing | new_auto_adds

    # Ranked list: only tickers we have screener data for
    scanned_tracked = {t: screened[t] for t in all_tracked if t in screened}
    ranked = sorted(scanned_tracked.items(), key=lambda x: x[1], reverse=True)

    # Previous rank lookup
    prev_ranks: Dict[str, int] = {
        t: entries[-1]["rank"]
        for t, entries in history.items()
        if entries
    }

    # Update history for every scanned ticker
    for new_rank, (ticker, composite) in enumerate(ranked, start=1):
        prev_rank = prev_ranks.get(ticker)
        delta = (prev_rank - new_rank) if prev_rank is not None else 0
        history.setdefault(ticker, []).append({
            "date": today,
            "composite": round(composite, 1),
            "rank": new_rank,
            "delta": delta,
        })

    _save_watchlist_history(history, history_path)

    # Tickers on watchlist that weren't scanned → go to MANUALLY ADDED section
    scanned_set = {t for t, _ in ranked}
    unscanned = all_tracked - scanned_set

    # Tier buckets
    tier1 = [(t, c) for t, c in ranked if c >= 50]
    tier2 = [(t, c) for t, c in ranked if 30 <= c < 50]
    tier3 = [(t, c) for t, c in ranked if c < 30]

    # Write new watchlist.txt
    lines = [
        "# ============================================================",
        f"# WATCHLIST — Auto-updated {today}",
        "# Sorted by catalyst composite score (highest priority first)",
        "# ↑/↓ = rank change vs last scan  |  → = stable  |  NEW = first appearance",
        "# 🔥 = deep dive candidate (2+ consecutive rank rises)",
        "# ============================================================",
        "",
    ]

    def section(header, tickers):
        if not tickers:
            return
        lines.append(header)
        for t, c in tickers:
            lines.append(_format_ticker_line(t, c, history, t in new_auto_adds))
        lines.append("")

    section("# ── TIER 1: HOT (≥50%) ─────────────────────────────────────", tier1)
    section("# ── TIER 2: WATCH (30–49%) ─────────────────────────────────", tier2)
    section("# ── TIER 3: MONITOR (<30%) ─────────────────────────────────", tier3)

    if unscanned:
        lines.append("# ── MANUALLY ADDED / NOT YET SCREENED ──────────────────────")
        for ticker in sorted(unscanned):
            entries = history.get(ticker, [])
            if entries:
                last = entries[-1]
                lines.append(
                    f"{ticker:<10} # last seen {last['date']}: {last['composite']:.0f}%"
                )
            else:
                lines.append(ticker)
        lines.append("")

    with open(watchlist_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    # Console summary
    risers = [(t, history[t][-1]["delta"]) for t in scanned_set
              if history.get(t) and history[t][-1]["delta"] > 0]
    fallers = [(t, history[t][-1]["delta"]) for t in scanned_set
               if history.get(t) and history[t][-1]["delta"] < 0]
    deep_dive_flags = [
        t for t in scanned_set
        if sum(1 for e in (history.get(t) or [])[-2:] if e.get("delta", 0) > 0) >= 2
    ]

    print(f"\n  {'─' * 50}")
    print(f"  📋 WATCHLIST UPDATED — {today}")
    print(f"  {'─' * 50}")
    print(f"  Tracked : {len(all_tracked)} tickers  |  New auto-adds: {len(new_auto_adds)}")
    if risers:
        top = sorted(risers, key=lambda x: x[1], reverse=True)[:5]
        print(f"  Rising  : {', '.join(f'{t}(↑{d})' for t, d in top)}")
    if fallers:
        top = sorted(fallers, key=lambda x: x[1])[:5]
        print(f"  Falling : {', '.join(f'{t}(↓{abs(d)})' for t, d in top)}")
    if deep_dive_flags:
        print(f"  🔥 Deep dive: {', '.join(deep_dive_flags)}")
    print(f"  Files   : {watchlist_path}  |  {history_path}")


# ==============================================================================
# SECTION 6: MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Catalyst Screener v1.0")
    parser.add_argument("--universe", choices=["small", "meme", "large", "all"],
                        default="all", help="Which universe to scan")
    parser.add_argument("--ticker", type=str, help="Single stock deep dive")
    parser.add_argument("--social", action="store_true", help="Include Reddit scan")
    parser.add_argument("--polymarket", action="store_true", help="Include Polymarket prediction market signals")
    parser.add_argument("--top", type=int, default=20, help="Show top N results")
    parser.add_argument("--update-watchlist", action="store_true",
                        help="After screening, re-rank watchlist.txt and append history")
    parser.add_argument(
        "--use-dynamic-universe",
        dest="use_dynamic_universe",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use universe_builder dynamic universe (default: True). "
             "Pass --no-use-dynamic-universe to force hardcoded lists.",
    )
    args = parser.parse_args()

    if args.ticker:
        deep_dive(args.ticker.upper(), include_social=args.social,
                  include_polymarket=args.polymarket)
        return

    # ------------------------------------------------------------------
    # Build universe — two-pass dynamic or hardcoded fallback
    # ------------------------------------------------------------------
    universe = _build_universe(args)
    _main_continue(args, universe)


def _build_universe(args) -> list:
    """
    Build the screening universe for the given CLI args.

    Pass 1: universe_builder.build_master_universe() + fast_momentum_prescreen()
    Pass 2: _load_dynamic_universe() (watchlist.txt + user_favorites)
    """
    import time as _time
    from pathlib import Path as _Path

    if args.use_dynamic_universe and _UNIVERSE_BUILDER_AVAILABLE:
        cache_dir = _Path(__file__).parent / "data" / "universe_cache"
        cache_is_fresh = False
        if cache_dir.exists():
            cache_files = list(cache_dir.glob("*_constituents.json"))
            if cache_files:
                oldest_mtime = min(f.stat().st_mtime for f in cache_files)
                age_h = (_time.time() - oldest_mtime) / 3600
                cache_is_fresh = age_h < _UNIVERSE_TTL

        if cache_is_fresh or args.use_dynamic_universe:
            try:
                universe = _ub.build_master_universe()
                universe = _ub.fast_momentum_prescreen(universe)
                print(
                    f"  Dynamic universe: {len(universe)} tickers "
                    f"(after momentum pre-screen)"
                )
                return universe
            except Exception as exc:
                print(
                    f"  [WARN] universe_builder failed: {exc} "
                    f"— falling back to watchlist.txt/favorites"
                )

    # Dynamic fallback (watchlist.txt + user_favorites — no hardcoded tickers)
    universe = _load_dynamic_universe()
    if universe:
        print(f"  Using dynamic fallback universe: {len(universe)} tickers (watchlist.txt + favorites)")
    else:
        print("  [WARN] No universe available — run universe_builder.py or add favorites")
    return universe


def _main_continue(args, universe):
    """Continuation of main() after universe is built — kept for readability."""

    results = screen_universe(universe, include_social=args.social,
                              include_polymarket=args.polymarket)

    if not results.empty:
        print_results(results, top_n=args.top)

        # Export CSV
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        path = os.path.join(OUTPUT_DIR, f"catalyst_screen_{date_str}.csv")
        export = results.drop(columns=["flags"])
        export.to_csv(path, index=False)
        print(f"\n  📁 Exported to: {path}")

        # Update watchlist
        if args.update_watchlist:
            update_watchlist(results)

    print(f"\n{'█' * 60}")
    print(f"  ⚠️  THESE ARE SETUP CONDITIONS, NOT BUY SIGNALS.")
    print(f"  Most setups fail. Size positions accordingly.")
    print(f"  Max 1-2% of NAV per speculative position.")
    print(f"  THIS IS NOT INVESTMENT ADVICE.")
    print(f"{'█' * 60}\n")


if __name__ == "__main__":
    main()
