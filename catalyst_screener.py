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
import logging
import os
import sys
import time
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

try:
    from utils.ticker_quarantine import quarantine as _quarantine, is_quarantined as _is_quarantined
    _QUARANTINE_AVAILABLE = True
except ImportError:
    _QUARANTINE_AVAILABLE = False
    def _quarantine(t, r): pass          # type: ignore[misc]
    def _is_quarantined(t): return False # type: ignore[misc]

# Module-level error deduplication: only log each unique provider error once per run
_SEEN_PROVIDER_ERRORS: set = set()
_PROVIDER_ERROR_COUNTS: dict = {}

try:
    from config import OUTPUT_DIR, UNIVERSE_CACHE_TTL_HOURS as _UNIVERSE_TTL
except ImportError:
    OUTPUT_DIR = "./signals_output"
    _UNIVERSE_TTL = 24

try:
    from utils.db import get_connection as _get_db_connection
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False

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

def get_stock_data(ticker: str, prefetched_hist: "pd.DataFrame | None" = None) -> dict:
    """
    Pull comprehensive data for a single ticker from yfinance.
    Returns dict with price data, fundamentals, short interest, etc.

    Args:
        prefetched_hist: Pre-fetched OHLCV DataFrame from yf_cache.bulk_history().
            When provided the expensive stock.history() HTTP call is skipped.
            Pass None (default) to fall back to the normal per-ticker fetch.
    """
    if _is_quarantined(ticker):
        return None

    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}

        # Detect 404 / delisted via the info dict itself (no quoteType = bad symbol)
        if not info or info.get("quoteType") is None:
            _quarantine(ticker, "no quoteType in yfinance info (delisted/invalid)")
            return None

        # Price history — use pre-fetched bulk data when available
        if prefetched_hist is not None and not prefetched_hist.empty and len(prefetched_hist) >= 20:
            hist = prefetched_hist
        else:
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

    except Exception as exc:
        err_str = str(exc)
        # 404 = symbol not found / delisted — quarantine immediately
        if "404" in err_str or "Quote not found" in err_str or "No fundamentals data" in err_str:
            _quarantine(ticker, f"HTTP 404: {err_str[:120]}")
            err_key = f"404:{ticker}"
            if err_key not in _SEEN_PROVIDER_ERRORS:
                _SEEN_PROVIDER_ERRORS.add(err_key)
                logger.warning("get_stock_data %s: 404 (quarantined for this run): %s", ticker, err_str[:120])
            return None
        # 401 / crumb errors — log once, count
        if "401" in err_str or "Invalid Crumb" in err_str or "unable to access" in err_str.lower():
            _PROVIDER_ERROR_COUNTS["yf_401"] = _PROVIDER_ERROR_COUNTS.get("yf_401", 0) + 1
            if "yf_401" not in _SEEN_PROVIDER_ERRORS:
                _SEEN_PROVIDER_ERRORS.add("yf_401")
                logger.warning(
                    "get_stock_data: Yahoo Finance 401/Unauthorized — "
                    "crumb may be stale. Further 401s will be suppressed."
                )
            return None
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
    float_shares = data["float_shares"]

    # Self-computed DTC: (SI% × float_shares) / avg_vol_30d
    # Vendor shortRatio uses shares_outstanding as denominator → underestimates float-adjusted DTC
    _avg_vol = data.get("volume_avg_30d", data.get("volume_avg_20d", 0)) or 0
    if float_shares > 0 and short_pct > 0 and _avg_vol > 0:
        dtc = (short_pct * float_shares) / _avg_vol
    else:
        dtc = data.get("short_ratio_dtc", 0) or 0

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


def _score_iv_rank_for_squeeze(iv_rank: Optional[float]) -> float:
    """
    Map IV rank (0–100 scale) to a squeeze-context score (0–10).

    High IV = market is pricing in a significant move, which can confirm
    squeeze momentum OR indicate crowded positioning.  Used as context only
    — not added directly to the squeeze final_score.
    """
    if iv_rank is None:
        return 0.0
    if iv_rank >= 80:
        return 10.0
    elif iv_rank >= 60:
        return 7.0
    elif iv_rank >= 40:
        return 5.0
    elif iv_rank >= 20:
        return 3.0
    return 1.0


def score_options_activity(data: dict, iv_rank: Optional[float] = None) -> dict:
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

    CHUNK-09: optional iv_rank (0–100 scale) adds IV context to the output
    without changing the core score/max values used by catalyst_screener.
    """
    stock_obj = data["stock_obj"]
    score = 0
    flags = []

    # CHUNK-09 tracking variables
    total_call_vol = 0
    total_put_vol = 0
    total_call_oi = 0
    total_put_oi = 0
    unusual_call_activity_flag = False
    options_chain_available = False

    try:
        # Get available expiration dates
        expirations = stock_obj.options
        if not expirations:
            iv_rank_score = _score_iv_rank_for_squeeze(iv_rank)
            return {
                "score": 0, "max": 6, "flags": ["No options data available"],
                "options_pressure_score": 0.0,
                "iv_rank": iv_rank,
                "iv_rank_score": iv_rank_score,
                "iv_data_confidence": "high" if iv_rank is not None else "none",
                "unusual_call_activity_flag": False,
                "call_put_volume_ratio": None,
                "call_put_oi_ratio": None,
            }

        options_chain_available = True

        # Look at nearest 2 expirations
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
            unusual_call_activity_flag = True
            flags.append(f"Unusual OTM call activity detected")

        # Total options volume vs stock volume
        total_opt_vol = total_call_vol + total_put_vol
        stock_vol = data["volume_current"]
        if stock_vol > 0 and total_opt_vol > stock_vol * 0.5:
            score += 1
            flags.append(f"High options/stock volume ratio")

    except Exception:
        flags.append(f"Options data unavailable")

    # CHUNK-09: compute auxiliary output fields
    iv_rank_score = _score_iv_rank_for_squeeze(iv_rank)

    call_put_volume_ratio = None
    if total_call_vol > 0 and total_put_vol > 0:
        call_put_volume_ratio = round(total_call_vol / total_put_vol, 2)

    call_put_oi_ratio = None
    if total_call_oi > 0 and total_put_oi > 0:
        call_put_oi_ratio = round(total_call_oi / total_put_oi, 2)

    # options_pressure_score: normalize existing 0-6 activity score to 0-10 scale
    options_pressure_score = round(min(10.0, (score / 6.0) * 10.0), 1) if score > 0 else 0.0

    if iv_rank is not None and options_chain_available:
        iv_data_confidence = "high"
    elif options_chain_available:
        iv_data_confidence = "low"
    else:
        iv_data_confidence = "high" if iv_rank is not None else "none"

    return {
        "score": score,
        "max": 6,
        "flags": flags,
        # CHUNK-09 extended fields
        "options_pressure_score": options_pressure_score,
        "iv_rank": iv_rank,
        "iv_rank_score": iv_rank_score,
        "iv_data_confidence": iv_data_confidence,
        "unusual_call_activity_flag": unusual_call_activity_flag,
        "call_put_volume_ratio": call_put_volume_ratio,
        "call_put_oi_ratio": call_put_oi_ratio,
    }


def get_squeeze_options_context(ticker: str, iv_rank: Optional[float] = None) -> dict:
    """
    CHUNK-09: Fetch live options chain for *ticker* and return options/IV
    context fields for the squeeze screener.

    Wraps score_options_activity() with a fresh yf.Ticker so that
    squeeze_screener.py does not need to pre-build a data dict.

    Returns a neutral result dict on any failure — never raises.
    Missing options data is non-fatal.
    """
    iv_rank_score = _score_iv_rank_for_squeeze(iv_rank)
    _neutral = {
        "score": 0,
        "max": 6,
        "flags": ["Options data unavailable"],
        "options_pressure_score": 0.0,
        "iv_rank": iv_rank,
        "iv_rank_score": iv_rank_score,
        "iv_data_confidence": "high" if iv_rank is not None else "none",
        "unusual_call_activity_flag": False,
        "call_put_volume_ratio": None,
        "call_put_oi_ratio": None,
    }
    try:
        stock_obj = yf.Ticker(ticker)
        hist = stock_obj.history(period="5d")
        if hist.empty:
            return _neutral
        price = float(hist["Close"].iloc[-1])
        volume_current = float(hist["Volume"].iloc[-1])
        data = {
            "stock_obj": stock_obj,
            "price": price,
            "volume_current": volume_current,
        }
        return score_options_activity(data, iv_rank=iv_rank)
    except Exception:
        return _neutral


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
# SECTION 2B: EARNINGS PROXIMITY + ANALYST UPGRADE CLUSTERING
# ==============================================================================

def score_earnings_catalyst(data: dict) -> dict:
    """
    Earnings proximity score.

    Pre-earnings runs are one of the most reliable short-term setups:
    analyst upgrades cluster, options premiums inflate, and momentum
    traders front-run the report. The closer earnings are, the higher
    the urgency.

    Data source: yfinance info["earningsTimestamp"] + stock.calendar

    Scoring (max=5):
      ≤ 7 days  → 5  (earnings imminent — maximum urgency)
      8–14 days → 4  (high urgency window)
      15–21 days → 3  (pre-earnings accumulation window)
      22–30 days → 2  (early setup phase)
      31–45 days → 1  (on radar)
      > 45 days  → 0
    """
    score = 0
    flags = []
    days_to_earnings = None

    try:
        info = data.get("info") or {}
        stock_obj = data.get("stock_obj")

        # Try earningsTimestamp from info first
        ts = info.get("earningsTimestamp")
        if ts and ts > 0:
            from datetime import timezone
            earnings_dt = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
            days_to_earnings = (earnings_dt - datetime.utcnow()).days

        # Fallback: stock.calendar DataFrame
        if days_to_earnings is None and stock_obj is not None:
            try:
                cal = stock_obj.calendar
                if cal is not None and not (hasattr(cal, "empty") and cal.empty):
                    # calendar is a dict or DataFrame depending on yfinance version
                    if isinstance(cal, dict):
                        ed = cal.get("Earnings Date")
                        if ed:
                            ed = ed[0] if isinstance(ed, list) else ed
                            if hasattr(ed, "to_pydatetime"):
                                ed = ed.to_pydatetime().replace(tzinfo=None)
                            days_to_earnings = (ed - datetime.utcnow()).days
                    elif hasattr(cal, "loc"):
                        ed = cal.loc["Earnings Date"].iloc[0] if "Earnings Date" in cal.index else None
                        if ed is not None:
                            if hasattr(ed, "to_pydatetime"):
                                ed = ed.to_pydatetime().replace(tzinfo=None)
                            days_to_earnings = (ed - datetime.utcnow()).days
            except Exception:
                pass

    except Exception:
        pass

    if days_to_earnings is not None and days_to_earnings >= 0:
        if days_to_earnings <= 7:
            score = 5
            flags.append(f"EARNINGS IN {days_to_earnings}d — imminent catalyst")
        elif days_to_earnings <= 14:
            score = 4
            flags.append(f"Earnings in {days_to_earnings}d — high-urgency window")
        elif days_to_earnings <= 21:
            score = 3
            flags.append(f"Earnings in {days_to_earnings}d — pre-earnings setup")
        elif days_to_earnings <= 30:
            score = 2
            flags.append(f"Earnings in {days_to_earnings}d — early accumulation window")
        elif days_to_earnings <= 45:
            score = 1
            flags.append(f"Earnings in {days_to_earnings}d — on radar")
    elif days_to_earnings is not None and days_to_earnings < 0:
        flags.append(f"Earnings passed {abs(days_to_earnings)}d ago")

    return {"score": score, "max": 5, "flags": flags, "days_to_earnings": days_to_earnings}


def score_analyst_momentum(data: dict) -> dict:
    """
    Analyst upgrade clustering score.

    Multiple upgrades or price-target raises in the same week signal
    institutional consensus forming — often a precursor to a gap-up.
    The AMD pattern (Apr 2026): 3 upgrades + PT raises in 7 days → +5%.

    Data source: yfinance stock.upgrades_downgrades (free, no API key)

    Scoring (max=6):
      3+ upgrades in 7 days          → +3  (strong clustering)
      2 upgrades in 7 days           → +2  (clustering detected)
      1 upgrade in 7 days            → +1  (single upgrade)
      Bonus: upgrade within earnings → +2  (upgrade × earnings proximity)
      Downgrade in 7 days penalty    → -1 per downgrade (floor 0)
    """
    score = 0
    flags = []
    upgrades_7d = 0
    downgrades_7d = 0

    try:
        stock_obj = data.get("stock_obj")
        if stock_obj is None:
            return {"score": 0, "max": 6, "flags": [], "upgrades_7d": 0}

        upg_df = stock_obj.upgrades_downgrades
        if upg_df is None or (hasattr(upg_df, "empty") and upg_df.empty):
            return {"score": 0, "max": 6, "flags": [], "upgrades_7d": 0}

        # Normalise index to datetime
        if not isinstance(upg_df.index, pd.DatetimeIndex):
            upg_df.index = pd.to_datetime(upg_df.index, utc=True)

        cutoff_7d  = pd.Timestamp.utcnow() - pd.Timedelta(days=7)
        cutoff_30d = pd.Timestamp.utcnow() - pd.Timedelta(days=30)

        recent_7d  = upg_df[upg_df.index >= cutoff_7d]
        recent_30d = upg_df[upg_df.index >= cutoff_30d]

        upgrade_actions   = {"upgrade", "init", "reiterated", "raised"}
        downgrade_actions = {"downgrade", "lowered"}

        def is_upgrade(row):
            action = str(row.get("Action", "")).lower()
            grade  = str(row.get("ToGrade", "")).lower()
            return (action in upgrade_actions or
                    any(k in grade for k in ("buy", "outperform", "overweight", "strong buy")))

        def is_downgrade(row):
            action = str(row.get("Action", "")).lower()
            grade  = str(row.get("ToGrade", "")).lower()
            return (action in downgrade_actions or
                    any(k in grade for k in ("sell", "underperform", "underweight")))

        upgrades_7d   = sum(1 for _, r in recent_7d.iterrows()  if is_upgrade(r))
        downgrades_7d = sum(1 for _, r in recent_7d.iterrows()  if is_downgrade(r))
        upgrades_30d  = sum(1 for _, r in recent_30d.iterrows() if is_upgrade(r))

        # Base clustering score
        if upgrades_7d >= 3:
            score += 3
            flags.append(f"ANALYST CLUSTER: {upgrades_7d} upgrades/raises in 7 days")
        elif upgrades_7d == 2:
            score += 2
            flags.append(f"Analyst clustering: {upgrades_7d} upgrades in 7 days")
        elif upgrades_7d == 1:
            score += 1
            flags.append(f"Analyst upgrade in last 7 days")

        # 30-day momentum context
        if upgrades_30d >= 4 and upgrades_7d == 0:
            score += 1
            flags.append(f"Strong analyst momentum: {upgrades_30d} upgrades in 30 days")

        # Downgrade penalty
        if downgrades_7d > 0:
            penalty = min(downgrades_7d, score)  # don't go below 0 from here
            score = max(0, score - penalty)
            flags.append(f"⚠️  {downgrades_7d} downgrade(s) in 7 days")

    except Exception as exc:
        flags.append(f"[analyst data unavailable: {exc}]")

    return {"score": min(score, 6), "max": 6, "flags": flags, "upgrades_7d": upgrades_7d}


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

    # ── Blacklist + bulk history pre-fetch ───────────────────────────────────
    try:
        from yf_cache import filter_blacklisted, bulk_history as _bulk_history
        tickers = filter_blacklisted(tickers)
        print(f"  Pre-fetching {len(tickers)} price histories (bulk)...", end=" ", flush=True)
        _hist_cache = _bulk_history(tickers, period="6mo")
        print(f"OK ({len(_hist_cache)} loaded)")
    except Exception as _yfc_exc:
        _hist_cache = {}
        logger.debug("yf_cache unavailable: %s", _yfc_exc)

    results = []
    total = len(tickers)

    for i, ticker in enumerate(tickers):
        print(f"\r  Scanning: {ticker:<8} ({i+1}/{total})", end="", flush=True)

        data = get_stock_data(ticker, prefetched_hist=_hist_cache.get(ticker.upper()))
        if data is None:
            continue

        # Compute all scores
        squeeze = score_short_squeeze(data)
        volume = score_volume_breakout(data)
        vol_squeeze = score_volatility_squeeze(data)
        options = score_options_activity(data)
        technical = score_technical_setup(data)
        earnings = score_earnings_catalyst(data)
        analyst = score_analyst_momentum(data)

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
                        polymarket["max"] + dark_pool["max"] +
                        earnings["max"] + analyst["max"])
        raw_total = (squeeze["score"] + volume["score"] + vol_squeeze["score"] +
                     options["score"] + technical["score"] + social["score"] +
                     polymarket["score"] + dark_pool["score"] +
                     earnings["score"] + analyst["score"])

        composite = raw_total / max_possible * 100 if max_possible > 0 else 0

        # Earnings × analyst clustering multiplier:
        # When upgrades cluster AND earnings are near, the signal is
        # disproportionately strong — apply a 1.5x boost (AMD pattern).
        days_to_e = earnings.get("days_to_earnings")
        upgrades_7d = analyst.get("upgrades_7d", 0)
        if (days_to_e is not None and 0 <= days_to_e <= 30
                and upgrades_7d >= 2 and not composite == 0):
            composite = min(composite * 1.5, 100)
            all_boost_flag = (
                f"EARNINGS×ANALYST BOOST ×1.5 — {upgrades_7d} upgrades "
                f"+ earnings in {days_to_e}d"
            )
        else:
            all_boost_flag = None

        all_flags = (squeeze["flags"] + volume["flags"] + vol_squeeze["flags"] +
                     options["flags"] + technical["flags"] + social["flags"] +
                     polymarket["flags"] + dark_pool["flags"] +
                     earnings["flags"] + analyst["flags"])
        if all_boost_flag:
            all_flags.insert(0, all_boost_flag)

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
            "earnings_score": earnings["score"],
            "analyst_score": analyst["score"],
            "days_to_earnings": earnings.get("days_to_earnings"),
            "upgrades_7d": analyst.get("upgrades_7d", 0),
            "post_squeeze_guard": bool(_recent_sq),
            "composite": round(composite, 1),
            "flags": all_flags,
            "n_flags": len(all_flags),
        })

        time.sleep(0.3)  # Rate limit

    print(f"\r  Scanning complete: {len(results)} tickers analyzed" + " " * 20)

    # Summarise provider errors and quarantined tickers at end of scan
    if _PROVIDER_ERROR_COUNTS.get("yf_401", 0):
        print(f"  [WARN] Yahoo Finance 401 errors suppressed: {_PROVIDER_ERROR_COUNTS['yf_401']} occurrences")
    quarantined = {k: v for k, v in __import__('utils.ticker_quarantine', fromlist=['get_quarantined']).get_quarantined().items()}
    if quarantined:
        print(f"  [INFO] Quarantined {len(quarantined)} invalid/delisted ticker(s): {', '.join(sorted(quarantined))}")

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
        ("EARNINGS PROXIMITY", score_earnings_catalyst),
        ("ANALYST MOMENTUM", score_analyst_momentum),
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
    try:
        from utils.supabase_persist import save_catalyst_history
        save_catalyst_history(history)
    except Exception as _exc:
        pass  # non-fatal

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

def _fetch_top_rankings_tickers(n: int = 20) -> list[str]:
    """Return the top-N tickers from the most recent daily_rankings run."""
    if not _DB_AVAILABLE:
        print("  [ERROR] utils/db not available — cannot read daily_rankings.")
        return []
    try:
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ticker
            FROM   daily_rankings
            WHERE  run_date = (SELECT MAX(run_date) FROM daily_rankings)
              AND  rank <= %s
            ORDER  BY rank ASC
            """,
            (n,),
        )
        rows = cur.fetchall()
        conn.close()
        return [r["ticker"] for r in rows]
    except Exception as exc:
        print(f"  [ERROR] Could not fetch daily_rankings: {exc}")
        return []


def main():
    parser = argparse.ArgumentParser(description="Catalyst Screener v1.0")
    parser.add_argument("--universe", choices=["small", "meme", "large", "all"],
                        default="all", help="Which universe to scan")
    parser.add_argument("--ticker", type=str, help="Single stock deep dive")
    parser.add_argument("--top-rankings", action="store_true",
                        help="Run deep dive on every ticker in the current Top-20 daily ranking")
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

    if args.top_rankings:
        tickers = _fetch_top_rankings_tickers(args.top)
        if not tickers:
            print("  [ERROR] No tickers found in daily_rankings. Run the pipeline first.")
            return
        print(f"\n{'═' * 60}")
        print(f"  TOP-{len(tickers)} RANKINGS DEEP DIVE")
        print(f"  {', '.join(tickers)}")
        print(f"{'═' * 60}")
        for i, ticker in enumerate(tickers, start=1):
            print(f"\n[{i}/{len(tickers)}] {ticker}")
            deep_dive(ticker, include_social=args.social,
                      include_polymarket=args.polymarket)
        print(f"\n{'═' * 60}")
        print(f"  Done — {len(tickers)} deep dives complete.")
        print(f"{'═' * 60}\n")
        return

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
        try:
            from utils.supabase_persist import save_catalyst_scores
            save_catalyst_scores(export, datetime.now().strftime("%Y-%m-%d"))
        except Exception as _exc:
            pass  # non-fatal

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
