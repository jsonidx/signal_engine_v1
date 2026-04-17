#!/usr/bin/env python3
"""
================================================================================
AI QUANT ANALYST v1.0 — Claude-Powered Signal Synthesis
================================================================================
Uses grok-4-1 (xAI) to analyze aggregated signals for
a ticker and produce a structured quant thesis.

WHAT IT DOES:
    1. Gathers all available signals: technical, options flow, fundamentals,
       SEC filings, congressional trades, social sentiment, polymarket
    2. Sends structured signal packet to Claude API
    3. Returns quant thesis: direction, conviction, entry/stop/target,
       position size, catalysts, risks, time horizon

OUTPUT STRUCTURE (per ticker):
    - Direction    : BULL | BEAR | NEUTRAL
    - Conviction   : 1-5 (1=weak, 5=high)
    - Entry range  : price levels
    - Stop loss    : invalidation level
    - Target       : price target(s)
    - Position %   : suggested allocation of portfolio slice
    - Catalysts    : 3 top supporting factors
    - Risks        : 3 top risk factors
    - Time horizon : days/weeks/months
    - Thesis       : 2-3 sentence narrative

USAGE:
    python3 ai_quant.py --ticker COIN          # Single ticker analysis
    python3 ai_quant.py --tickers COIN GME AI  # Multiple tickers
    python3 ai_quant.py --watchlist            # All TIER 1 + TIER 2 tickers
    python3 ai_quant.py --report <file>        # Analyze existing report file
    python3 ai_quant.py --ticker COIN --raw    # Show raw Claude response

REQUIREMENTS:
    pip install openai
    export XAI_API_KEY="your-key"

COST (grok-4-1-fast-reasoning default, $1/M in · $4/M out):
    Prompt size : ~3,558 input tokens (system + user)
    Per ticker  : ~$0.009 default  |  ~$0.027 premium (grok-4.20-0309-reasoning)
    5-ticker run: ~$0.045 default  |  ~$0.135 premium
    Premium auto-enabled when signal_agreement_score ≥ AI_PREMIUM_THRESHOLD (0.85).
    Fallback: if premium fails → retries with grok-4-1-fast-reasoning.

    New section token cost breakdown:
      Earnings & Event Calendar : ~127 tok
      Historical Analog Score   : ~122 tok
      Relative Strength/Sector  : ~90 tok
      Liquidity & TC            : ~68 tok
      Volatility Regime         : ~54 tok

    NOTE: Output cost dominates (5× input rate), so doubling prompt size
    only raised per-ticker cost by ~26% ($0.020 → $0.026 standard mode).

IMPORTANT: This is NOT investment advice. Claude is analyzing the same
           signals you have — it doesn't have secret alpha. Use as a
           structured second opinion, not gospel.
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
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

from utils.db import get_connection

warnings.filterwarnings("ignore")

try:
    from openai import OpenAI as _OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OpenAI = None  # type: ignore[assignment]
    _OPENAI_AVAILABLE = False

try:
    import anthropic as _anthropic_module
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _anthropic_module = None  # type: ignore[assignment]
    _ANTHROPIC_AVAILABLE = False

try:
    from config import (
        OUTPUT_DIR, PORTFOLIO_NAV as _CONFIG_NAV, CRYPTO_ALLOCATION, EQUITY_ALLOCATION,
        AI_MODEL_DEFAULT, AI_MODEL_PREMIUM, AI_MODEL_FALLBACK, AI_PREMIUM_THRESHOLD,
    )
except ImportError:
    OUTPUT_DIR = "./signals_output"
    _CONFIG_NAV = 50_000
    CRYPTO_ALLOCATION = 0.25
    EQUITY_ALLOCATION = 0.65
    AI_MODEL_DEFAULT   = "grok-4-1-fast-reasoning"
    AI_MODEL_PREMIUM   = "grok-4.20-0309-reasoning"
    AI_MODEL_FALLBACK  = "grok-4-1-fast-reasoning"
    AI_PREMIUM_THRESHOLD = 0.85

try:
    from utils.db import load_portfolio_nav
    PORTFOLIO_NAV = load_portfolio_nav(fallback=_CONFIG_NAV)
except Exception:
    PORTFOLIO_NAV = _CONFIG_NAV

# ─── Regime filter (optional — degrades gracefully) ───────────────────────────
try:
    import regime_filter as _rf
    _REGIME_AVAILABLE = True
except ImportError:
    _rf = None
    _REGIME_AVAILABLE = False

# ─── Conflict resolver (optional — degrades gracefully) ───────────────────────
try:
    import conflict_resolver as _cr
    _RESOLVER_AVAILABLE = True
except ImportError:
    _cr = None
    _RESOLVER_AVAILABLE = False

# ─── IV calculator (optional — degrades gracefully) ──────────────────────────
try:
    from utils.iv_calculator import compute_atm_iv, get_iv_rank_and_percentile
    _IV_AVAILABLE = True
except ImportError:
    _IV_AVAILABLE = False

# ─── Sector ETF mapping (yfinance sector name → benchmark ETF ticker) ─────────
SECTOR_ETF_MAP: Dict[str, str] = {
    "Technology":              "XLK",
    "Financial Services":      "XLF",
    "Energy":                  "XLE",
    "Healthcare":              "XLV",
    "Consumer Cyclical":       "XLY",
    "Consumer Defensive":      "XLP",
    "Industrials":             "XLI",
    "Basic Materials":         "XLB",
    "Utilities":               "XLU",
    "Real Estate":             "XLRE",
    "Communication Services":  "XLC",
}

# ─── Historical analog: ordered feature names (cosine-similarity vector) ─────
# Exactly 12 features in this order; _FEATURE_RANGES must contain every entry.
# Source modules: technical(4) · options_flow(2) · catalyst(2) · dark_pool(2) · fundamentals(1) · top-level(1)
_HISTORICAL_ANALOG_FEATURE_NAMES: List[str] = [
    "rsi_14",               # technical   — momentum/overbought proxy
    "above_ma200",          # technical   — structural trend (0=below, 1=above)
    "momentum_1m",          # technical   — short-term price momentum %
    "momentum_3m",          # technical   — medium-term price momentum %
    "iv_rank",              # options     — options expensiveness (0-100)
    "heat_score",           # options     — options activity heat (0-100)
    "short_squeeze_score",  # catalyst    — squeeze setup intensity (0-100)
    "vol_compression_score",# catalyst    — Bollinger compression (0-10)
    "dark_pool_score",      # dark pool   — institutional routing bias (0-100)
    "short_ratio_zscore",   # dark pool   — FINRA short ratio z-score (-3..+3)
    "fundamental_score",    # fundamentals— composite quality/value score (0-100)
    "agreement_score",      # top-level   — pre-computed signal consensus (0-1)
]

# ─── Feature normalization bounds for historical analog similarity ─────────────
# Bounds define the [lo, hi] range mapped to [0, 1]; values are clipped at edges.
_FEATURE_RANGES: Dict[str, tuple] = {
    "rsi_14":               (0.0,   100.0),
    "above_ma200":          (0.0,     1.0),
    "momentum_1m":          (-30.0,  30.0),
    "momentum_3m":          (-50.0,  50.0),
    "iv_rank":              (0.0,   100.0),
    "heat_score":           (0.0,   100.0),
    "short_squeeze_score":  (0.0,   100.0),
    "vol_compression_score":(0.0,    10.0),
    "dark_pool_score":      (0.0,   100.0),
    "short_ratio_zscore":   (-3.0,    3.0),
    "fundamental_score":    (0.0,   100.0),
    "agreement_score":      (0.0,     1.0),
}

# ─── Liquidity tier thresholds (dollar ADV) + position-sizing alignment ───────
# Tier caps enforced in SYSTEM_PROMPT and _build_prompt liquidity note.
#   MEGA  ≥$100M ADV → no cap beyond portfolio-level limits (up to 8% equity)
#   LARGE $10-100M   → standard sizing, max 5% per RISK_PARAMS
#   MID   $1-10M     → cap position_size_pct ≤ 5%; use limit orders
#   SMALL <$1M       → cap position_size_pct ≤ 3%; flag market-impact risk
LIQUIDITY_TIER_THRESHOLDS: Dict[str, int] = {
    "MEGA":  100_000_000,   # ≥ $100M ADV
    "LARGE":  10_000_000,   # $10M – $100M ADV
    "MID":     1_000_000,   # $1M  – $10M  ADV
    "SMALL":           0,   # < $1M ADV
}

# === NEW: UNIVERSE RANK EXPORT FOR AI QUANT ===
_RANKED_UNIVERSE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ranked_universe.json")

def _inject_universe_rank(signals: dict, ticker: str) -> dict:
    """Inject universe rank/status from ranked_universe.json into the signals dict."""
    default = {"rank": "N/A", "total": 215, "status": "Dynamic only"}
    try:
        if not os.path.exists(_RANKED_UNIVERSE_PATH):
            signals["universe_rank"] = default
            return signals
        with open(_RANKED_UNIVERSE_PATH) as f:
            ranked = json.load(f)
        signals["universe_rank"] = ranked.get(ticker.upper(), default)
    except Exception as exc:
        logger.warning("Could not load ranked_universe.json for %s: %s", ticker, exc)
        signals["universe_rank"] = default
    return signals


# ==============================================================================
# SECTION 0: RESULT CACHE (Supabase — global shared cache)
# ==============================================================================

def _init_db():
    """Return a Supabase connection (thesis_cache table already exists)."""
    return get_connection()


def get_cached_thesis(ticker: str, date: str = None) -> Optional[dict]:
    """
    Return today's cached thesis for ticker, or None if not found.
    date defaults to today (YYYY-MM-DD).
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    try:
        conn = _init_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM thesis_cache WHERE ticker=%s AND date=%s",
            (ticker.upper(), date),
        )
        row = cur.fetchone()
        conn.close()
        if row is None:
            return None
        d = dict(row)
        # Expand JSON fields back to lists/dicts
        d["catalysts"]      = json.loads(d.pop("catalysts_json")      or "[]")
        d["risks"]          = json.loads(d.pop("risks_json")          or "[]")
        d["raw_response"]   = d.get("raw_response", "")
        d["signals"]        = json.loads(d.pop("signals_json")        or "{}")
        d["expected_moves"] = json.loads(d.pop("expected_moves_json") or "[]")
        return d
    except Exception:
        return None


def _migrate_prob_columns(cur, conn) -> None:
    """Add prob_* columns to thesis_cache if not already present (idempotent)."""
    try:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'thesis_cache'
        """)
        existing = {row["column_name"] for row in cur.fetchall()}
        new_cols = {
            "prob_combined":  "FLOAT",
            "prob_technical": "FLOAT",
            "prob_options":   "FLOAT",
            "prob_catalyst":  "FLOAT",
            "prob_news":      "FLOAT",
            "model_used":     "TEXT",
            "cost_usd":       "FLOAT",
        }
        for col, col_type in new_cols.items():
            if col not in existing:
                cur.execute(f"ALTER TABLE thesis_cache ADD COLUMN {col} {col_type}")
        conn.commit()
    except Exception as exc:
        logger.warning("_migrate_prob_columns: %s", exc)


def save_thesis(thesis: dict) -> None:
    """Upsert a thesis result into the cache for today."""
    try:
        date = datetime.now().strftime("%Y-%m-%d")
        conn = _init_db()
        cur = conn.cursor()
        # Ensure prob_combined columns exist (added in Step 5 of prob_engine build)
        _migrate_prob_columns(cur, conn)

        cur.execute("""
            INSERT INTO thesis_cache
                (ticker, date, direction, conviction, time_horizon,
                 entry_low, entry_high, stop_loss, target_1, target_2,
                 position_size_pct, thesis, data_quality, notes,
                 catalysts_json, risks_json, raw_response, signals_json, created_at,
                 bull_probability, bear_probability, neutral_probability,
                 signal_agreement_score, key_invalidation, primary_scenario, bear_scenario,
                 expected_moves_json, model_used, cost_usd,
                 prob_combined, prob_technical, prob_options, prob_catalyst, prob_news)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(ticker, date) DO UPDATE SET
                direction=excluded.direction,
                conviction=excluded.conviction,
                time_horizon=excluded.time_horizon,
                entry_low=excluded.entry_low,
                entry_high=excluded.entry_high,
                stop_loss=excluded.stop_loss,
                target_1=excluded.target_1,
                target_2=excluded.target_2,
                position_size_pct=excluded.position_size_pct,
                thesis=excluded.thesis,
                data_quality=excluded.data_quality,
                notes=excluded.notes,
                catalysts_json=excluded.catalysts_json,
                risks_json=excluded.risks_json,
                raw_response=excluded.raw_response,
                signals_json=excluded.signals_json,
                created_at=excluded.created_at,
                bull_probability=excluded.bull_probability,
                bear_probability=excluded.bear_probability,
                neutral_probability=excluded.neutral_probability,
                signal_agreement_score=excluded.signal_agreement_score,
                key_invalidation=excluded.key_invalidation,
                primary_scenario=excluded.primary_scenario,
                bear_scenario=excluded.bear_scenario,
                expected_moves_json=excluded.expected_moves_json,
                model_used=excluded.model_used,
                cost_usd=excluded.cost_usd,
                prob_combined=excluded.prob_combined,
                prob_technical=excluded.prob_technical,
                prob_options=excluded.prob_options,
                prob_catalyst=excluded.prob_catalyst,
                prob_news=excluded.prob_news
        """, (
            thesis.get("ticker", "").upper(),
            date,
            thesis.get("direction"),
            thesis.get("conviction"),
            thesis.get("time_horizon"),
            thesis.get("entry_low"),
            thesis.get("entry_high"),
            thesis.get("stop_loss"),
            thesis.get("target_1"),
            thesis.get("target_2"),
            thesis.get("position_size_pct"),
            thesis.get("thesis"),
            thesis.get("data_quality"),
            thesis.get("notes"),
            json.dumps(thesis.get("catalysts") or []),
            json.dumps(thesis.get("risks") or []),
            thesis.get("raw_response", ""),
            json.dumps(thesis.get("signals") or {}),
            datetime.now().isoformat(),
            thesis.get("bull_probability"),
            thesis.get("bear_probability"),
            thesis.get("neutral_probability"),
            thesis.get("signal_agreement_score"),
            thesis.get("key_invalidation"),
            thesis.get("primary_scenario"),
            thesis.get("bear_scenario"),
            json.dumps(thesis.get("expected_moves") or []),
            thesis.get("model_used"),
            thesis.get("cost_usd"),
            thesis.get("prob_combined"),
            thesis.get("prob_technical"),
            thesis.get("prob_options"),
            thesis.get("prob_catalyst"),
            thesis.get("prob_news"),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [cache] WARNING: Could not save to cache: {e}")


def print_cache_table(days: int = 7) -> None:
    """Print cached theses from the last N days."""
    try:
        conn = _init_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT ticker, date, direction, conviction, time_horizon,
                   entry_low, target_1, stop_loss, thesis
            FROM thesis_cache
            ORDER BY date DESC, conviction DESC
            LIMIT 200
        """)
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        print(f"  [cache] ERROR: {e}")
        return

    if not rows:
        print("  Cache is empty.")
        return

    print()
    print("AI QUANT CACHE")
    print("=" * 90)
    print(f"  {'DATE':<12} {'TICKER':<8} {'DIR':<7} {'CONV':>5}  {'ENTRY':>8}  {'TARGET':>8}  THESIS")
    print("  " + "-" * 84)
    for r in rows:
        date, ticker, direction, conviction, horizon, entry, target, stop, thesis_text = (
            r['date'], r['ticker'], r['direction'], r['conviction'], r['time_horizon'],
            r['entry_low'], r['target_1'], r['stop_loss'], r['thesis']
        )
        icon = DIRECTION_ICON.get(direction or "NEUTRAL", "◯")
        entry_s  = f"${entry:.2f}"  if entry  else "   N/A"
        target_s = f"${target:.2f}" if target else "   N/A"
        short_thesis = (thesis_text or "")[:50]
        print(f"  {date:<12} {ticker:<8} {icon} {(direction or '?'):<5} "
              f"{(conviction or 0):>5}  {entry_s:>8}  {target_s:>8}  {short_thesis}")
    print()


# ==============================================================================
# SECTION 1: SIGNAL COLLECTION
# ==============================================================================

def _read_watchlist_tickers(tier_filter: Optional[List[str]] = None) -> List[str]:
    """Parse watchlist.txt. tier_filter=['TIER 1','TIER 2'] restricts tiers."""
    paths = [
        os.path.join(os.path.dirname(__file__), "watchlist.txt"),
        "./watchlist.txt",
    ]
    for path in paths:
        if os.path.exists(path):
            tickers = []
            current_tier = None
            with open(path) as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    # Section header detection
                    upper = stripped.upper()
                    if "TIER 1" in upper:
                        current_tier = "TIER 1"
                        continue
                    elif "TIER 2" in upper:
                        current_tier = "TIER 2"
                        continue
                    elif "TIER 3" in upper:
                        current_tier = "TIER 3"
                        continue
                    elif "MANUALLY ADDED" in upper:
                        current_tier = "MANUALLY ADDED"
                        continue
                    if stripped.startswith("#"):
                        continue
                    ticker = stripped.split("#")[0].strip().upper()
                    if not ticker:
                        continue
                    if tier_filter is None or current_tier in tier_filter:
                        tickers.append(ticker)
            return tickers
    return []


def _collect_technical_signals(ticker: str) -> dict:
    """Pull basic price/volume technical signals via yfinance."""
    try:
        import yfinance as yf
        import numpy as np

        t = yf.Ticker(ticker)
        hist = t.history(period="1y")
        if hist.empty:
            return {}

        close = hist["Close"]
        volume = hist["Volume"]
        price = float(close.iloc[-1])

        # Moving averages
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma50 = float(close.rolling(50).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])

        # RSI (14)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = float((100 - 100 / (1 + rs)).iloc[-1])

        # Momentum
        mom_1m = float((close.iloc[-1] / close.iloc[-21] - 1) * 100) if len(close) > 21 else 0
        mom_3m = float((close.iloc[-1] / close.iloc[-63] - 1) * 100) if len(close) > 63 else 0
        mom_6m = float((close.iloc[-1] / close.iloc[-126] - 1) * 100) if len(close) > 126 else 0

        # Volume trend
        vol_5d_avg = float(volume.iloc[-5:].mean())
        vol_20d_avg = float(volume.iloc[-21:-1].mean())
        vol_ratio = vol_5d_avg / vol_20d_avg if vol_20d_avg > 0 else 1.0

        # 52w range — fall back to full available history if < 252 bars
        window_52w = min(252, len(close))
        high_52w = float(close.iloc[-window_52w:].max())
        low_52w  = float(close.iloc[-window_52w:].min())
        pct_from_high = (price - high_52w) / high_52w * 100 if high_52w > 0 else 0
        pct_from_low  = (price - low_52w)  / low_52w  * 100 if low_52w  > 0 else 0

        # Trend assessment
        above_ma200 = price > ma200
        above_ma50 = price > ma50
        above_ma20 = price > ma20

        return {
            "price": round(price, 2),
            "ma20": round(ma20, 2),
            "ma50": round(ma50, 2),
            "ma200": round(ma200, 2),
            "above_ma200": above_ma200,
            "above_ma50": above_ma50,
            "above_ma20": above_ma20,
            "rsi_14": round(rsi, 1),
            "momentum_1m_pct": round(mom_1m, 1),
            "momentum_3m_pct": round(mom_3m, 1),
            "momentum_6m_pct": round(mom_6m, 1),
            "volume_ratio_5d_vs_20d": round(vol_ratio, 2),
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            "pct_from_52w_high": round(pct_from_high, 1),
            "pct_from_52w_low": round(pct_from_low, 1),
        }
    except Exception:
        return {}


def _collect_fundamental_signals(ticker: str) -> dict:
    """Pull fundamental data from fundamental_analysis module if available."""
    try:
        from fundamental_analysis import analyze_ticker
        result = analyze_ticker(ticker)
        if result is None:
            return {}
        scores = result.get("scores", {})
        composite = result.get("composite", 0)
        raw_analyst_rating = result.get("analyst_rating")
        target_mean = result.get("target_mean")
        price = result.get("price")
        analyst_upside = None
        if target_mean and price and price > 0:
            analyst_upside = round((target_mean / price - 1) * 100, 1)
        return {
            "fundamental_score_pct": round(composite, 1),
            "valuation_score": scores.get("valuation", 0),
            "growth_score": scores.get("growth", 0),
            "quality_score": scores.get("quality", 0),
            "pe_ratio": result.get("pe_trailing"),
            "forward_pe": result.get("pe_forward"),
            "revenue_growth_yoy": result.get("revenue_growth_yoy"),
            "eps_growth_yoy": result.get("earnings_growth_yoy"),
            "gross_margin": None,  # not returned by analyze_ticker
            "next_earnings_days": None,  # not returned by analyze_ticker
            "analyst_rating": raw_analyst_rating,
            "analyst_price_target": target_mean,
            "analyst_upside_pct": analyst_upside,
        }
    except Exception:
        return {}


def _collect_volume_profile_signals(ticker: str) -> dict:
    """Volume-at-price support/resistance levels."""
    try:
        from volume_profile import get_volume_profile
        return get_volume_profile(ticker)
    except Exception:
        return {}



def _collect_options_signals(ticker: str) -> dict:
    """Pull options flow data from options_flow module."""
    try:
        from options_flow import get_options_heat
        return get_options_heat(ticker)
    except Exception:
        return {}


def _collect_max_pain(ticker: str) -> Optional[dict]:
    """Compute max pain strike from the nearest options expiry via yfinance."""
    try:
        from options_flow import compute_max_pain
        return compute_max_pain(ticker)
    except Exception:
        return None


def _collect_news_sentiment(ticker: str) -> dict:
    """Fetch entity-linked news sentiment via Marketaux (falls back to neutral)."""
    try:
        from quant_report import fetch_news_sentiment
        return fetch_news_sentiment(ticker)
    except Exception:
        return {
            "ticker": ticker,
            "articles_found": 0,
            "avg_sentiment": 0.0,
            "sentiment_label": "Neutral",
            "period_days": 7,
            "source": "fallback_neutral",
        }


def _collect_polymarket_signals(ticker: str) -> dict:
    """Pull Polymarket prediction market signal."""
    try:
        from polymarket_screener import PolymarketScreener
        screener = PolymarketScreener()
        result = screener.extract_signal(ticker)
        if not result or result.get("signal_score", 0) == 0:
            return {}
        return {
            "polymarket_score": result.get("signal_score", 0),
            "polymarket_direction": result.get("direction", "neutral"),
            "polymarket_market": result.get("question", ""),
            "polymarket_probability": result.get("probability", 0),
            "polymarket_volume_24h": result.get("volume_24h", 0),
        }
    except Exception:
        return {}


def _collect_sec_signals(ticker: str) -> dict:
    """SEC EDGAR: insider buying (Form 4), activist stakes (13D/G), material events (8-K)."""
    try:
        from sec_module import score_sec_signals
        return score_sec_signals(ticker)
    except Exception:
        return {}


def _collect_catalyst_signals(ticker: str) -> dict:
    """Catalyst setup: short squeeze conditions, volatility compression, float/ownership data."""
    try:
        from catalyst_screener import get_stock_data, score_short_squeeze, score_volatility_squeeze
        data = get_stock_data(ticker)
        if not data:
            return {}
        squeeze = score_short_squeeze(data)
        vol_compress = score_volatility_squeeze(data)
        return {
            "short_pct_float": data.get("short_pct_float"),
            "days_to_cover": data.get("short_ratio_dtc"),
            "float_shares": data.get("float_shares"),
            "inst_ownership": data.get("inst_ownership"),
            "insider_ownership": data.get("insider_ownership"),
            "short_squeeze_score": squeeze.get("score", 0),
            "short_squeeze_max": squeeze.get("max", 0),
            "short_squeeze_flags": squeeze.get("flags", []),
            "vol_compression_score": vol_compress.get("score", 0),
            "vol_compression_max": vol_compress.get("max", 0),
            "vol_compression_flags": vol_compress.get("flags", []),
        }
    except Exception:
        return {}


def _get_weekly_regime(ticker: str) -> dict:
    """
    Weekly structural trend filter: price vs 20-week SMA + MA slope.

    Four regime states:
      bullish    — price above MA, MA slope positive   → trade with trend
      weakening  — price above MA, MA slope negative   → proceed with caution
      recovering — price below MA, MA slope positive   → wait for confirmation
      bearish    — price below MA, MA slope negative   → avoid long setups
      unknown    — insufficient data
    """
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
        hist = t.history(period="9mo", interval="1wk")
        if hist.empty or len(hist) < 5:
            return {"regime": "unknown", "reason": "insufficient weekly data"}

        close = hist["Close"]
        price = float(close.iloc[-1])

        # 20-week SMA (or fewer bars if history is short)
        ma_period = min(20, len(close))
        ma20w = float(close.rolling(ma_period).mean().iloc[-1])

        # Slope: compare current MA to MA 4 weeks ago (% change)
        ma_series = close.rolling(ma_period).mean().dropna()
        if len(ma_series) >= 5:
            ma_slope_pct = float((ma_series.iloc[-1] / ma_series.iloc[-5] - 1) * 100)
        else:
            ma_slope_pct = 0.0

        above_ma = price > ma20w
        pct_from_ma = (price - ma20w) / ma20w * 100

        if above_ma and ma_slope_pct >= 0:
            regime = "bullish"
        elif above_ma and ma_slope_pct < 0:
            regime = "weakening"
        elif not above_ma and ma_slope_pct > 0:
            regime = "recovering"
        else:
            regime = "bearish"

        return {
            "regime": regime,
            "price": round(price, 2),
            "ma20w": round(ma20w, 2),
            "pct_from_ma20w": round(pct_from_ma, 1),
            "ma_slope_4w_pct": round(ma_slope_pct, 2),
            "above_ma20w": above_ma,
        }
    except Exception as e:
        return {"regime": "unknown", "reason": str(e)}


def _collect_dark_pool_signals(ticker: str) -> dict:
    """
    Pull dark pool / institutional flow signal from dark_pool_flow module.
    Uses today's pre-computed result cache (data/dark_pool_latest.json) if
    available, otherwise computes live from cached FINRA files.
    """
    try:
        from dark_pool_flow import compute_dark_pool_signal, load_result_cache
        cache = load_result_cache()
        if ticker in cache:
            return cache[ticker]
        result = compute_dark_pool_signal(ticker)
        return result or {}
    except Exception:
        return {}


def _collect_squeeze_signals(ticker: str) -> dict:
    """Pull dedicated squeeze score from squeeze_screener module (0–100 score)."""
    try:
        from squeeze_screener import run_screener
        results = run_screener(
            tickers=[ticker],
            min_score=0,
            top_n=1,
            include_finviz=False,   # skip Finviz to keep ai_quant fast
            include_ftd=False,      # FTD already fetched in full universe run
            verbose=False,
        )
        if not results:
            return {}
        r = results[0]
        return {
            "squeeze_score_100": r.final_score,
            "juice_target_pct": r.juice_target,
            "recent_squeeze": r.recent_squeeze,
            "signal_breakdown": r.signal_breakdown,
            "squeeze_flags": r.flags[:6],
        }
    except Exception:
        return {}


def _collect_dcf_signals(ticker: str) -> dict:
    """
    Pull DCF valuation, WACC, ROIC vs WACC spread from utils/dcf_model.
    Degrades gracefully if module unavailable or data insufficient.
    """
    try:
        from utils.dcf_model import run_dcf
        result = run_dcf(ticker)
        if result.get("data_quality") == "INSUFFICIENT":
            return {"dcf_available": False}
        return {
            "dcf_available": True,
            "dcf_intrinsic_value": result.get("intrinsic_value"),
            "dcf_current_price": result.get("current_price"),
            "dcf_upside_pct": result.get("upside_pct"),
            "dcf_wacc": result.get("wacc"),
            "dcf_roic": result.get("roic"),
            "dcf_roic_wacc_spread": result.get("roic_wacc_spread"),
            "dcf_fcf_yield": result.get("fcf_yield"),
            "dcf_data_quality": result.get("data_quality"),
            "dcf_flags": result.get("flags", []),
        }
    except Exception:
        return {"dcf_available": False}


def _collect_peer_benchmarking_signals(ticker: str) -> dict:
    """
    Pull sector peer comparison and historical multiple context.
    """
    try:
        from utils.peer_benchmarking import run_peer_benchmarking
        result = run_peer_benchmarking(ticker)
        return {
            "peer_available": True,
            "peer_relative_valuation": result.get("relative_valuation"),
            "peer_pe_vs_history_pct": result.get("pe_vs_history_pct"),
            "peer_pe_vs_peers_pct": result.get("pe_vs_peers_pct"),
            "peer_sector": result.get("sector"),
            "peer_median_pe": result.get("peer_median_pe"),
            "peer_stock_pe": result.get("stock_pe"),
            "peer_median_ev_ebitda": result.get("peer_median_ev_ebitda"),
            "peer_historical_pe_avg": result.get("historical_pe_avg"),
            "peer_flags": result.get("flags", []),
        }
    except Exception:
        return {"peer_available": False}


def _collect_red_flag_signals(ticker: str) -> dict:
    """
    Pull accounting red flags and financial quality risk score.
    Uses skip_edgar=True for speed (avoids EDGAR network calls in batch mode).
    """
    try:
        from red_flag_screener import run_red_flag_screener
        result = run_red_flag_screener(ticker, skip_edgar=True)
        return {
            "red_flag_available": True,
            "red_flag_score": result.get("red_flag_score"),
            "red_flag_risk_level": result.get("risk_level"),
            "red_flag_accruals_ratio": result.get("checks", {}).get("accruals", {}).get("ratio"),
            "red_flag_payout_ratio_fcf": result.get("checks", {}).get("payout_risk", {}).get("payout_ratio_fcf"),
            "red_flag_flags": result.get("flags", []),
        }
    except Exception:
        return {"red_flag_available": False}



def compute_signal_agreement(signals_dict: dict) -> float:
    """
    Pre-compute a 0.0–1.0 signal agreement score across all directional modules.

    Each module casts a BULL or BEAR vote when it has a clear directional signal.
    Score = agreements_with_plurality_direction / total_valid_votes.
    Returns 0.0 when no module produces a valid directional output.

    Modules evaluated:
      signal_engine  composite_z >  0.5  → BULL  |  < -0.5  → BEAR
      squeeze        squeeze_score_100 > 50       → BULL
      options_flow   heat_score > 60              → BULL
      cross_asset    signal contains 'BOTTOM'     → BULL  |  'TOP' → BEAR
      fundamentals   fundamental_score_pct > 60   → BULL  |  < 40  → BEAR
      polymarket     probability > 0.65            → BULL  |  < 0.35 → BEAR
    """
    from collections import Counter

    votes: list = []

    # 1. signal_engine composite_z (live run) or RSI proxy (stored signals use "technical")
    comp_z = (signals_dict.get("signal_engine") or {}).get("composite_z")
    if comp_z is not None:
        if comp_z > 0.5:
            votes.append("BULL")
        elif comp_z < -0.5:
            votes.append("BEAR")
    else:
        # Fallback: use RSI from "technical" block (always present in stored signals)
        rsi = (signals_dict.get("technical") or {}).get("rsi_14")
        if rsi is not None:
            if rsi > 60:
                votes.append("BULL")
            elif rsi < 40:
                votes.append("BEAR")

    # 2. squeeze_screener final_score  (key: squeeze_score_100)
    sq_score = (signals_dict.get("squeeze") or {}).get("squeeze_score_100")
    if sq_score is not None:
        if sq_score > 50:
            votes.append("BULL")

    # 3. options_flow heat score
    heat = (signals_dict.get("options_flow") or {}).get("heat_score")
    if heat is not None:
        if heat > 60:
            votes.append("BULL")

    # 4. fundamental_analysis composite score
    fund_score = (signals_dict.get("fundamentals") or {}).get("fundamental_score_pct")
    if fund_score is not None:
        if fund_score > 60:
            votes.append("BULL")
        elif fund_score < 40:
            votes.append("BEAR")

    # 6. polymarket consensus probability
    poly_prob = (signals_dict.get("polymarket") or {}).get("polymarket_probability")
    if poly_prob is not None and poly_prob > 0:
        if poly_prob > 0.65:
            votes.append("BULL")
        elif poly_prob < 0.35:
            votes.append("BEAR")

    if not votes:
        return 0.0

    counts = Counter(votes)
    plurality_count = counts.most_common(1)[0][1]
    return round(plurality_count / len(votes), 4)


# ==============================================================================
# SECTION 1a-NEW: FIVE UPGRADE SIGNAL COLLECTORS
# ==============================================================================

def _extract_signal_features(signals: dict) -> dict:
    """
    Extract a normalized [0,1] scalar feature vector from a signals dict.

    Features (12, in _HISTORICAL_ANALOG_FEATURE_NAMES order):
      rsi_14, above_ma200, momentum_1m, momentum_3m,        ← technical (4)
      iv_rank, heat_score,                                   ← options_flow (2)
      short_squeeze_score, vol_compression_score,            ← catalyst (2)
      dark_pool_score, short_ratio_zscore,                   ← dark_pool_flow (2)
      fundamental_score,                                     ← fundamentals (1)
      agreement_score                                        ← top-level (1)

    Missing or {available: False} sub-dicts degrade gracefully — those
    features are simply omitted from the returned dict (sparse vector).
    Returns {} if the signals dict itself is empty or None.
    """
    # Guard: empty/None input → empty feature dict (caller handles <3 features)
    if not signals:
        return {}

    tech     = signals.get("technical")      or {}
    opts     = signals.get("options_flow")   or {}
    catalyst = signals.get("catalyst")       or {}
    dp       = signals.get("dark_pool_flow") or {}
    fund     = signals.get("fundamentals")   or {}

    # Raw values extracted in _HISTORICAL_ANALOG_FEATURE_NAMES order
    raw: Dict[str, Optional[float]] = {
        "rsi_14":                tech.get("rsi_14"),
        "above_ma200":           1.0 if tech.get("above_ma200") is True else (
                                 0.0 if tech.get("above_ma200") is False else None),
        "momentum_1m":           tech.get("momentum_1m_pct"),
        "momentum_3m":           tech.get("momentum_3m_pct"),
        "iv_rank":               opts.get("iv_rank"),
        "heat_score":            opts.get("heat_score"),
        "short_squeeze_score":   catalyst.get("short_squeeze_score"),
        "vol_compression_score": catalyst.get("vol_compression_score"),
        "dark_pool_score":       dp.get("dark_pool_score"),
        "short_ratio_zscore":    dp.get("short_ratio_zscore"),
        "fundamental_score":     fund.get("fundamental_score_pct"),
        "agreement_score":       signals.get("signal_agreement_score"),
    }

    normalized: dict = {}
    for key, val in raw.items():
        if val is None:
            continue  # missing feature → excluded from vector (sparse OK)
        lo, hi = _FEATURE_RANGES.get(key, (0.0, 100.0))
        span = hi - lo
        normalized[key] = 0.5 if span == 0 else max(0.0, min(1.0, (float(val) - lo) / span))
    return normalized


def _cosine_similarity_features(a: dict, b: dict) -> float:
    """
    Cosine similarity between two normalized feature dicts.
    Only keys present (non-None) in BOTH dicts contribute.
    Returns 0.0 when fewer than 3 shared features are available.
    """
    import numpy as np
    shared = [k for k in a if k in b]
    if len(shared) < 3:
        return 0.0
    va = np.array([a[k] for k in shared], dtype=float)
    vb = np.array([b[k] for k in shared], dtype=float)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.clip(np.dot(va, vb) / (na * nb), -1.0, 1.0))


def _collect_earnings_event_signals(ticker: str) -> dict:
    """
    Collect earnings calendar + historical surprise data.

    Returns
    -------
    next_earnings_date      : str  YYYY-MM-DD or None
    days_to_next_earnings   : int or None
    earnings_risk           : 'HIGH' (0-14d) | 'MEDIUM' (15-30d) | 'LOW' (>30d or unknown)
    earnings_surprises_4q   : list of {date, eps_estimate, eps_actual, surprise_pct}
    avg_surprise_magnitude  : float (mean abs surprise %, last 4Q)
    beat_rate_4q            : float (# beats / 4; None if no data)
    """
    try:
        import yfinance as yf
        tk  = yf.Ticker(ticker)
        cal = tk.calendar  # DataFrame or None

        # ── Next earnings date ────────────────────────────────────────────────
        next_earnings: Optional[str] = None
        days_to: Optional[int]       = None
        if cal is not None and not (hasattr(cal, "empty") and cal.empty):
            try:
                # yfinance calendar shape varies; handle both Series and DataFrame
                if hasattr(cal, "loc"):
                    ed = cal.loc["Earnings Date"] if "Earnings Date" in cal.index else None
                    if ed is not None:
                        val = ed.iloc[0] if hasattr(ed, "iloc") else ed
                        next_earnings = str(val)[:10]
                elif isinstance(cal, dict):
                    ed_list = cal.get("Earnings Date", [])
                    if ed_list:
                        next_earnings = str(ed_list[0])[:10]
            except Exception:
                pass

        if next_earnings:
            try:
                next_dt = datetime.strptime(next_earnings, "%Y-%m-%d")
                days_to = (next_dt - datetime.now()).days
            except Exception:
                pass

        earnings_risk: str
        if days_to is not None and 0 <= days_to <= 14:
            earnings_risk = "HIGH"
        elif days_to is not None and days_to <= 30:
            earnings_risk = "MEDIUM"
        else:
            earnings_risk = "LOW"

        # ── Historical surprises (last 4 quarters) ────────────────────────────
        surprises: list = []
        try:
            hist = tk.earnings_history
            if hist is not None and not hist.empty:
                for idx, row in hist.tail(4).iterrows():
                    est = row.get("epsEstimate") if hasattr(row, "get") else None
                    act = row.get("epsActual")   if hasattr(row, "get") else None
                    sup = row.get("surprisePercent") if hasattr(row, "get") else None
                    date_str = str(idx.date()) if hasattr(idx, "date") else str(idx)[:10]
                    surprises.append({
                        "date":         date_str,
                        "eps_estimate": round(float(est), 2) if est is not None else None,
                        "eps_actual":   round(float(act), 2) if act is not None else None,
                        "surprise_pct": round(float(sup) * 100, 1) if sup is not None else None,
                    })
        except Exception:
            pass

        valid_surprises = [s["surprise_pct"] for s in surprises if s.get("surprise_pct") is not None]
        avg_magnitude   = round(sum(abs(v) for v in valid_surprises) / len(valid_surprises), 1) if valid_surprises else None
        beats           = sum(1 for v in valid_surprises if v > 0)
        beat_rate       = round(beats / len(valid_surprises), 2) if valid_surprises else None

        return {
            "earnings_available":       True,
            "next_earnings_date":       next_earnings,
            "days_to_next_earnings":    days_to,
            "earnings_risk":            earnings_risk,
            "earnings_surprises_4q":    surprises,
            "avg_surprise_magnitude":   avg_magnitude,
            "beat_rate_4q":             beat_rate,
        }
    except Exception as exc:
        logger.warning("[%s] Earnings event collection failed: %s", ticker, exc)
        return {"earnings_available": False, "error": str(exc), "earnings_risk": "LOW"}


def _collect_relative_strength_signals(ticker: str, sector: Optional[str] = None) -> dict:
    """
    Ticker performance vs sector ETF + RSP (equal-weight S&P 500) for 20/60/120d.
    sector : yfinance sector string (e.g. 'Technology'); used to pick ETF from SECTOR_ETF_MAP.
    """
    try:
        import yfinance as yf
        sector_etf = SECTOR_ETF_MAP.get(sector or "", "SPY")
        benchmark  = "RSP"
        needed     = list({ticker, sector_etf, benchmark})
        end        = datetime.now()
        start      = end - timedelta(days=150)   # covers 120 trading-day lookback

        raw = yf.download(needed, start=start, end=end, auto_adjust=True, progress=False, threads=False)
        prices = raw["Close"] if "Close" in raw else raw
        if ticker not in prices.columns or prices.empty:
            return {"rs_available": False, "error": "price data unavailable"}

        out: dict = {"rs_available": True, "sector_etf": sector_etf}
        for label, n_days in (("20d", 20), ("60d", 60), ("120d", 120)):
            if len(prices) < n_days:
                continue
            def _ret(col: str) -> Optional[float]:
                if col not in prices.columns:
                    return None
                s = prices[col].dropna()
                if len(s) < n_days:
                    return None
                return round((s.iloc[-1] / s.iloc[-n_days] - 1) * 100, 2)

            t_ret  = _ret(ticker)
            rsp_ret = _ret(benchmark)
            etf_ret = _ret(sector_etf)

            out[f"ticker_return_{label}"]  = t_ret
            out[f"rsp_return_{label}"]     = rsp_ret
            out[f"sector_return_{label}"]  = etf_ret
            out[f"vs_rsp_{label}"]         = round(t_ret - rsp_ret, 2) if (t_ret is not None and rsp_ret is not None) else None
            out[f"vs_sector_{label}"]      = round(t_ret - etf_ret, 2) if (t_ret is not None and etf_ret is not None) else None

        # Primary RS signal: 20d vs RSP
        vs_rsp_20 = out.get("vs_rsp_20d") or 0.0
        if   vs_rsp_20 >=  5.0: rs_signal = "STRONG_OUTPERFORM"
        elif vs_rsp_20 >=  1.5: rs_signal = "OUTPERFORM"
        elif vs_rsp_20 >= -1.5: rs_signal = "INLINE"
        elif vs_rsp_20 >= -5.0: rs_signal = "UNDERPERFORM"
        else:                   rs_signal = "STRONG_UNDERPERFORM"
        out["rs_signal_20d"] = rs_signal

        return out
    except Exception as exc:
        logger.warning("[%s] Relative strength collection failed: %s", ticker, exc)
        return {"rs_available": False, "error": str(exc)}


def _collect_liquidity_signals(ticker: str) -> dict:
    """
    30-day ADV (shares + dollars), today's volume ratio vs ADV,
    and estimated bid-ask spread (Corwin-Schultz HL proxy).

    Tier → max position_size_pct cap (see LIQUIDITY_TIER_THRESHOLDS + SYSTEM_PROMPT):
      MEGA  (≥$100M ADV) → no extra cap; standard portfolio limits apply
      LARGE ($10-100M)   → standard sizing (up to 5% per RISK_PARAMS)
      MID   ($1-10M)     → cap ≤ 5%; use limit orders to minimize impact
      SMALL (<$1M)       → cap ≤ 3%; flag market-impact risk in notes

    Position-as-%-of-ADV is computed in _build_prompt where NAV is known.
    adv_shares and adv_dollars are always returned for downstream logging.
    """
    try:
        import yfinance as yf
        end   = datetime.now()
        start = end - timedelta(days=50)   # buffer for weekends + holidays
        hist  = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
        if hist.empty or len(hist) < 5:
            return {"liquidity_available": False, "error": "insufficient history"}

        vol_30    = hist["Volume"].tail(30)
        price_30  = hist["Close"].tail(30)
        adv_sh    = float(vol_30.mean())
        cur_px    = float(price_30.iloc[-1])
        adv_usd   = adv_sh * cur_px
        vol_today = float(hist["Volume"].iloc[-1])
        vol_ratio = round(vol_today / adv_sh, 2) if adv_sh > 0 else None

        # Corwin-Schultz half-spread proxy: 10-day rolling (High-Low)/(High+Low)
        recent     = hist.tail(10)
        hl_ratio   = (recent["High"] - recent["Low"]) / (recent["High"] + recent["Low"])
        spread_bps = round(float(hl_ratio.mean()) * 10_000 / 2, 1)   # half-spread in bps

        # Tier derived from LIQUIDITY_TIER_THRESHOLDS (single source of truth)
        if   adv_usd >= LIQUIDITY_TIER_THRESHOLDS["MEGA"]:  tier = "MEGA"
        elif adv_usd >= LIQUIDITY_TIER_THRESHOLDS["LARGE"]: tier = "LARGE"
        elif adv_usd >= LIQUIDITY_TIER_THRESHOLDS["MID"]:   tier = "MID"
        else:                                                tier = "SMALL"

        return {
            "liquidity_available": True,
            "adv_shares":          int(adv_sh),       # always present for logging
            "adv_dollars":         round(adv_usd),    # always present for logging
            "current_price":       round(cur_px, 2),
            "vol_today_shares":    int(vol_today),
            "vol_ratio_vs_adv":    vol_ratio,
            "spread_bps":          spread_bps,
            "liquidity_tier":      tier,
        }
    except Exception as exc:
        logger.warning("[%s] Liquidity collection failed: %s", ticker, exc)
        return {"liquidity_available": False, "error": str(exc)}


def _collect_historical_analog_signals(
    ticker: str,
    current_signals: dict,
    db_path: Optional[str] = None,
) -> dict:
    """
    Find the top-3 most similar past signal setups from the last 3 years
    stored in ai_quant_cache.db.  Similarity = cosine distance on a
    normalized 12-feature vector.  Returns weighted analog score 0-100.

    db_path: override for testing (pass ':memory:' path with pre-seeded data).
    """
    try:
        import json as _json

        current_features = _extract_signal_features(current_signals)
        if len(current_features) < 3:
            return {"analog_available": False, "reason": "insufficient current features"}

        cutoff = (datetime.now() - timedelta(days=3 * 365)).strftime("%Y-%m-%d")
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """SELECT ticker, date, direction, conviction, signal_agreement_score,
                      signals_json, entry_low, target_1
               FROM thesis_cache
               WHERE date >= %s AND signals_json IS NOT NULL
               ORDER BY date DESC LIMIT 500""",
            (cutoff,),
        )
        rows = cur.fetchall()
        conn.close()

        if len(rows) < 5:
            return {
                "analog_available": False,
                "reason": f"only {len(rows)} historical theses (need ≥5)",
            }

        scored: list = []
        for r in rows:
            try:
                hist_sig  = _json.loads(r['signals_json']) if r['signals_json'] else {}
                hist_feat = _extract_signal_features(hist_sig)
                sim       = _cosine_similarity_features(current_features, hist_feat)
                scored.append({
                    "ticker":     r['ticker'],
                    "date":       r['date'],
                    "direction":  r['direction'],
                    "conviction": r['conviction'],
                    "similarity": round(sim * 100, 1),
                })
            except Exception:
                continue

        if not scored:
            return {"analog_available": False, "reason": "similarity computation failed"}

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        top3 = scored[:3]

        # Weighted composite score (50/35/15)
        weights     = [0.50, 0.35, 0.15]
        analog_score = sum(a["similarity"] * w for a, w in zip(top3, weights))

        direction_counts: dict = {}
        for a in top3:
            d = a.get("direction") or "NEUTRAL"
            direction_counts[d] = direction_counts.get(d, 0) + 1
        modal_direction = max(direction_counts, key=direction_counts.get)

        return {
            "analog_available":    True,
            "analog_score":        round(analog_score, 1),
            "top_3_analogs":       top3,
            "modal_direction":     modal_direction,
            "n_searched":          len(scored),
        }
    except Exception as exc:
        logger.warning("[%s] Historical analog failed: %s", ticker, exc)
        return {"analog_available": False, "reason": str(exc)}


def _collect_volatility_regime_signals(ticker: str) -> dict:
    """
    20d and 60d realized vol (annualized %), vol ratio, IV rank/percentile,
    and VIX percentile vs 1-year range.
    """
    try:
        import yfinance as yf
        import numpy as np
        end   = datetime.now()
        start = end - timedelta(days=90)   # need ~60 trading days

        hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
        if hist.empty or len(hist) < 25:
            return {"vol_regime_available": False, "error": "insufficient history"}

        log_ret = np.log(hist["Close"] / hist["Close"].shift(1)).dropna()
        rv_20   = float(log_ret.tail(20).std() * np.sqrt(252) * 100)
        rv_60   = float(log_ret.tail(60).std() * np.sqrt(252) * 100) if len(log_ret) >= 60 else None
        vol_ratio = round(rv_20 / rv_60, 3) if rv_60 else None

        if   vol_ratio is not None and vol_ratio > 1.2: vol_regime = "EXPANDING"
        elif vol_ratio is not None and vol_ratio < 0.8: vol_regime = "CONTRACTING"
        elif vol_ratio is not None:                     vol_regime = "STABLE"
        else:                                           vol_regime = "UNKNOWN"

        # ── IV rank / percentile ─────────────────────────────────────────────
        current_iv: Optional[float]  = None
        iv_rank: Optional[float]     = None
        iv_percentile: Optional[float] = None
        if _IV_AVAILABLE:
            try:
                current_iv = compute_atm_iv(ticker)
                if current_iv is not None:
                    iv_rank, iv_percentile = get_iv_rank_and_percentile(
                        ticker, current_iv, lookback_days=30, min_history=5
                    )
            except Exception:
                pass

        # ── VIX percentile (vs 252-day range) ────────────────────────────────
        vix_current: Optional[float]     = None
        vix_percentile: Optional[float]  = None
        try:
            vix_start = end - timedelta(days=400)
            vh = yf.Ticker("^VIX").history(start=vix_start, end=end, auto_adjust=True)
            if not vh.empty:
                vix_current   = round(float(vh["Close"].iloc[-1]), 2)
                vix_1y        = vh["Close"].tail(252)
                vix_percentile = round(float((vix_1y < vix_current).mean() * 100), 1)
        except Exception:
            pass

        return {
            "vol_regime_available": True,
            "rv_20d_pct":           round(rv_20, 1),
            "rv_60d_pct":           round(rv_60, 1) if rv_60 is not None else None,
            "vol_ratio_20_60":      vol_ratio,
            "vol_regime":           vol_regime,
            "current_iv_pct":       round(current_iv * 100, 1) if (current_iv and current_iv < 10) else (round(current_iv, 1) if current_iv else None),
            "iv_rank":              round(iv_rank, 1) if iv_rank is not None else None,
            "iv_percentile":        round(iv_percentile, 1) if iv_percentile is not None else None,
            "vix_current":          vix_current,
            "vix_percentile":       vix_percentile,
        }
    except Exception as exc:
        logger.warning("[%s] Volatility regime collection failed: %s", ticker, exc)
        return {"vol_regime_available": False, "error": str(exc)}


def collect_all_signals(ticker: str, verbose: bool = False) -> dict:
    """
    Collect all available signals for a ticker.
    Returns structured dict for Claude prompt.
    """
    ticker = ticker.upper().strip()
    signals = {"ticker": ticker, "timestamp": datetime.now().isoformat()}

    if verbose:
        print(f"  [{ticker}] Collecting signals...")

    if verbose:
        print(f"  [{ticker}]   → weekly regime...", end=" ", flush=True)
    wr = _get_weekly_regime(ticker)
    signals["weekly_regime"] = wr
    if verbose:
        print(f"done ({wr.get('regime', 'unknown')})")

    if verbose:
        print(f"  [{ticker}]   → technical...", end=" ", flush=True)
    tech = _collect_technical_signals(ticker)
    signals["technical"] = tech
    if verbose:
        print("done")

    if verbose:
        print(f"  [{ticker}]   → fundamentals...", end=" ", flush=True)
    fund = _collect_fundamental_signals(ticker)
    signals["fundamentals"] = fund
    if verbose:
        print("done")

    if verbose:
        print(f"  [{ticker}]   → volume profile...", end=" ", flush=True)
    vp = _collect_volume_profile_signals(ticker)
    signals["volume_profile"] = vp
    if verbose:
        print("done")

    if verbose:
        print(f"  [{ticker}]   → options flow...", end=" ", flush=True)
    opts = _collect_options_signals(ticker)
    signals["options_flow"] = opts
    if verbose:
        print("done")

    if verbose:
        print(f"  [{ticker}]   → max pain...", end=" ", flush=True)
    mp = _collect_max_pain(ticker)
    signals["max_pain"] = mp
    if verbose:
        strike = f"${mp['max_pain_strike']}" if mp else "unavailable"
        print(f"done ({strike})")

    if verbose:
        print(f"  [{ticker}]   → polymarket...", end=" ", flush=True)
    poly = _collect_polymarket_signals(ticker)
    signals["polymarket"] = poly
    if verbose:
        print("done")

    if verbose:
        print(f"  [{ticker}]   → SEC filings...", end=" ", flush=True)
    sec = _collect_sec_signals(ticker)
    signals["sec"] = sec
    if verbose:
        print("done")

    if verbose:
        print(f"  [{ticker}]   → catalyst setup...", end=" ", flush=True)
    catalyst = _collect_catalyst_signals(ticker)
    signals["catalyst"] = catalyst
    if verbose:
        print("done")

    if verbose:
        print(f"  [{ticker}]   → squeeze score...", end=" ", flush=True)
    squeeze = _collect_squeeze_signals(ticker)
    signals["squeeze"] = squeeze
    if verbose:
        print("done")

    if verbose:
        print(f"  [{ticker}]   → dark pool flow...", end=" ", flush=True)
    dp = _collect_dark_pool_signals(ticker)
    signals["dark_pool_flow"] = dp
    if verbose:
        print(f"done ({dp.get('signal', 'no data')})")

    if verbose:
        print(f"  [{ticker}]   → DCF valuation...", end=" ", flush=True)
    dcf = _collect_dcf_signals(ticker)
    signals["dcf"] = dcf
    if verbose:
        dq = dcf.get("dcf_data_quality", "N/A") if dcf.get("dcf_available") else "N/A"
        print(f"done (quality={dq})")

    if verbose:
        print(f"  [{ticker}]   → peer benchmarking...", end=" ", flush=True)
    peers = _collect_peer_benchmarking_signals(ticker)
    signals["peer_benchmarking"] = peers
    if verbose:
        verdict = peers.get("peer_relative_valuation", "N/A") if peers.get("peer_available") else "N/A"
        print(f"done ({verdict})")

    if verbose:
        print(f"  [{ticker}]   → red flag screener...", end=" ", flush=True)
    red_flags = _collect_red_flag_signals(ticker)
    signals["red_flags"] = red_flags
    if verbose:
        level = red_flags.get("red_flag_risk_level", "N/A") if red_flags.get("red_flag_available") else "N/A"
        print(f"done ({level})")

    if verbose:
        print(f"  [{ticker}]   → earnings event...", end=" ", flush=True)
    evnt = _collect_earnings_event_signals(ticker)
    signals["earnings_event"] = evnt
    if verbose:
        risk = evnt.get("earnings_risk", "N/A")
        dte  = evnt.get("days_to_next_earnings")
        print(f"done (risk={risk}, dte={dte})")

    if verbose:
        print(f"  [{ticker}]   → liquidity...", end=" ", flush=True)
    liq = _collect_liquidity_signals(ticker)
    signals["liquidity"] = liq
    if verbose:
        tier = liq.get("liquidity_tier", "N/A") if liq.get("liquidity_available") else "N/A"
        print(f"done (tier={tier})")

    if verbose:
        print(f"  [{ticker}]   → volatility regime...", end=" ", flush=True)
    vr = _collect_volatility_regime_signals(ticker)
    signals["volatility_regime"] = vr
    if verbose:
        regime_v = vr.get("vol_regime", "N/A") if vr.get("vol_regime_available") else "N/A"
        print(f"done ({regime_v})")

    if verbose:
        print(f"  [{ticker}]   → news sentiment...", end=" ", flush=True)
    news_sent = _collect_news_sentiment(ticker)
    signals["news_sentiment"] = news_sent
    if verbose:
        src   = news_sent.get("source", "?")
        label = news_sent.get("sentiment_label", "?")
        n_art = news_sent.get("articles_found", 0)
        print(f"done ({label}, {n_art} articles, src={src})")

    # ── Macro + sector regime ─────────────────────────────────────────────────
    if _REGIME_AVAILABLE:
        if verbose:
            print(f"  [{ticker}]   → macro regime...", end=" ", flush=True)
        try:
            mr = _rf.get_market_regime()
            sr = _rf.get_sector_regimes()
            signals["market_regime"] = mr
            signals["ticker_sector"] = _rf.get_ticker_sector(ticker)
            # Derive sector-level regime for this ticker
            ticker_sector_name = signals["ticker_sector"] or ""
            # Map yfinance sector names to our SECTOR_ETFS keys (best-effort)
            _sector_key_map = {
                "Technology":              "tech",
                "Financial Services":      "financials",
                "Energy":                  "energy",
                "Healthcare":              "healthcare",
                "Consumer Cyclical":       "consumer_disc",
                "Consumer Defensive":      "consumer_staples",
                "Industrials":             "industrials",
                "Basic Materials":         "materials",
                "Utilities":               "utilities",
                "Real Estate":             "real_estate",
                "Communication Services":  "comm_services",
            }
            sector_key = _sector_key_map.get(ticker_sector_name)
            signals["ticker_sector_regime"] = sr.get(sector_key) if sector_key else None
        except Exception as exc:
            logger.warning("Regime collection failed for %s: %s", ticker, exc)
            signals["market_regime"]       = {}
            signals["ticker_sector"]       = None
            signals["ticker_sector_regime"] = None
        if verbose:
            regime_label = signals.get("market_regime", {}).get("regime", "unknown")
            print(f"done ({regime_label})")
    else:
        signals["market_regime"]       = {}
        signals["ticker_sector"]       = None
        signals["ticker_sector_regime"] = None

    # ── Relative strength (needs sector from regime step above) ──────────────
    if verbose:
        print(f"  [{ticker}]   → relative strength...", end=" ", flush=True)
    rs = _collect_relative_strength_signals(ticker, sector=signals.get("ticker_sector"))
    signals["relative_strength"] = rs
    if verbose:
        sig = rs.get("rs_signal_20d", "N/A") if rs.get("rs_available") else "N/A"
        print(f"done ({sig})")

    # ── Historical analog (needs all other signals collected first) ───────────
    if verbose:
        print(f"  [{ticker}]   → historical analog...", end=" ", flush=True)
    analog = _collect_historical_analog_signals(ticker, signals)
    signals["historical_analog"] = analog
    if verbose:
        score = analog.get("analog_score", "N/A") if analog.get("analog_available") else "N/A"
        print(f"done (score={score})")

    return signals


# ==============================================================================
# SECTION 1b: TOP-N SELECTION HELPERS
# ==============================================================================

def _find_latest_equity_signals_file() -> Optional[str]:
    """Return the most recent signals_output/equity_signals_YYYYMMDD.csv path."""
    import glob as _glob
    pattern = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "signals_output",
        "equity_signals_*.csv",
    )
    matches = sorted(_glob.glob(pattern), reverse=True)
    return matches[0] if matches else None


def _generate_resolved_signals_file(
    tickers: List[str],
    output_path: str,
    verbose: bool = False,
) -> dict:
    """
    For each ticker: collect_all_signals() + run conflict_resolver.
    Saves result to output_path (data/resolved_signals.json).
    Returns the full {ticker: resolved_dict} dict.

    Called by the --top-n mode before select_top_tickers() to build the
    priority-scoring input. Does NOT call Claude — purely signal collection.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    resolved_all: dict = {}

    print(f"\n  Pre-screening {len(tickers)} tickers (no Claude cost)...\n")
    for i, ticker in enumerate(tickers, 1):
        print(f"  [{i:>3}/{len(tickers)}] {ticker:<8}", end=" ", flush=True)
        try:
            signals = collect_all_signals(ticker, verbose=False)

            if _RESOLVER_AVAILABLE:
                mr_dict    = signals.get("market_regime") or {}
                regime_str = mr_dict.get("regime", "TRANSITIONAL") if mr_dict else "TRANSITIONAL"
                resolved   = _cr.resolve(signals, regime_str)
            else:
                # Minimal resolver output from the lightweight agreement scorer
                agreement = compute_signal_agreement(signals)
                resolved = {
                    "pre_resolved_direction":  "NEUTRAL",
                    "pre_resolved_confidence": 0.0,
                    "signal_agreement_score":  agreement,
                    "override_flags":          [],
                    "module_votes":            {},
                    "bull_weight":             0.0,
                    "bear_weight":             0.0,
                    "skip_claude":             False,
                    "max_conviction_override": None,
                    "position_size_override":  None,
                }

            # Inject prob_combined into resolved so ticker_selector can gate on it
            try:
                from utils.prob_engine import compute_prob_combined as _cpc
                signals["signal_agreement_score"] = resolved.get("signal_agreement_score", 0.0)
                _pr = _cpc(signals)
                resolved["prob_combined"] = _pr["prob_combined"]
            except Exception:
                resolved["prob_combined"] = resolved.get("signal_agreement_score", 0.50)

            resolved_all[ticker] = resolved
            direction = resolved.get("pre_resolved_direction", "NEUTRAL")
            skip      = resolved.get("skip_claude", False)
            agreement = resolved.get("signal_agreement_score", 0.0)
            pc        = resolved.get("prob_combined", agreement)
            print(
                f"{direction:<8} agreement={agreement:.0%}  prob={pc:.2f}"
                + ("  [skip_claude]" if skip else "")
            )
        except Exception as exc:
            logger.warning("[%s] Signal collection error: %s", ticker, exc)
            print(f"ERROR: {exc}")

    try:
        with open(output_path, "w") as f:
            json.dump(resolved_all, f, indent=2, default=str)
        print(f"\n  Resolved signals saved → {output_path}")
    except Exception as exc:
        logger.warning("Failed to save resolved signals: %s", exc)

    return resolved_all


def _get_open_positions() -> list:
    """
    Reads open positions dynamically from Supabase trades table at runtime.
    Returns [] if the DB is unavailable — no hardcoded fallback.

    Newly opened positions are automatically always-included in AI synthesis
    without any manual edits. Closed positions are automatically excluded.
    """
    try:
        from trade_journal import get_open_positions
        positions = get_open_positions()
        tickers = list(dict.fromkeys(
            p["ticker"] for p in positions if p.get("ticker")
        ))
        logger.info(f"_get_open_positions: live from DB → {tickers}")
        return tickers
    except Exception as e:
        logger.warning(f"_get_open_positions: DB unavailable ({e}) — always_include will be empty")
        return []


def _run_top_n_mode(args, use_cache: bool) -> None:
    """
    Priority-based ticker selection mode (--top-n / --no-limit / --dry-run).

    Flow:
      1. Determine ticker pool (watchlist TIER 1+2, or force list from --tickers)
      2. If --no-limit: use all non-skipped tickers (no priority scoring)
      3. Otherwise: generate data/resolved_signals.json, call select_top_tickers()
      4. If --dry-run: print table + cost estimate, exit without calling Claude
      5. Run analyze_ticker() on selected tickers only
    """
    from utils.ticker_selector import select_top_tickers

    try:
        from config import AI_QUANT_MAX_TICKERS, AI_QUANT_MIN_AGREEMENT, AI_QUANT_ALWAYS_INCLUDE
    except ImportError:
        AI_QUANT_MAX_TICKERS   = 10
        AI_QUANT_MIN_AGREEMENT = 0.60
        AI_QUANT_ALWAYS_INCLUDE = []

    top_n         = args.top_n if args.top_n is not None else AI_QUANT_MAX_TICKERS
    min_agreement = args.min_agreement if args.min_agreement is not None else AI_QUANT_MIN_AGREEMENT

    # ── Always-include: live open positions via _get_open_positions() ──────────
    always_include = _get_open_positions()
    print(f"  Always-include: {always_include} (from trade_journal.db; fallback: config.AI_QUANT_ALWAYS_INCLUDE)")

    # --tickers used as force list in top-n mode (support both "A B" and "A,B")
    force_tickers: Optional[List[str]] = None
    if args.tickers:
        force_tickers = []
        for t in args.tickers:
            force_tickers.extend(t.upper().split(","))
        force_tickers = [t.strip() for t in force_tickers if t.strip()]

    resolved_signals_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "resolved_signals.json"
    )

    # ── Force mode (--tickers provided) ──────────────────────────────────────
    if force_tickers:
        ticker_list = force_tickers[:top_n]
        selected = [
            {
                "ticker":           t,
                "priority_score":   0.0,
                "selection_reason": "force_tickers override",
            }
            for t in ticker_list
        ]
        print(f"Force mode: {ticker_list}")

    # ── No-limit mode ─────────────────────────────────────────────────────────
    elif args.no_limit:
        print("WARNING: --no-limit flag set. Running on ALL non-skipped tickers.")
        print("         API costs apply (~€0.03 per ticker).")
        wl = _read_watchlist_tickers(tier_filter=["TIER 1", "TIER 2"])
        if not wl:
            print("  [WARN] No TIER 1/TIER 2 headers in watchlist.txt — falling back to all tickers")
            wl = _read_watchlist_tickers()
        if not wl:
            print("  ERROR: watchlist.txt is empty or missing")
            sys.exit(1)
        resolved_all = _generate_resolved_signals_file(
            wl, resolved_signals_path, verbose=args.verbose
        )
        ticker_list = [t for t, r in resolved_all.items() if not r.get("skip_claude")]
        selected = [
            {
                "ticker":           t,
                "priority_score":   0.0,
                "selection_reason": "no_limit mode — all non-skipped tickers",
            }
            for t in ticker_list
        ]

    # ── Normal top-N mode ─────────────────────────────────────────────────────
    else:
        wl = _read_watchlist_tickers(tier_filter=["TIER 1", "TIER 2"])
        if not wl:
            print("  [WARN] No TIER 1/TIER 2 headers in watchlist.txt — falling back to all tickers")
            wl = _read_watchlist_tickers()
        if not wl:
            print("  ERROR: watchlist.txt is empty or missing")
            sys.exit(1)
        # Reuse today's resolved_signals.json if Step 12 already built it — skip
        # the expensive per-ticker re-fetch (congressional trades, SEC, etc.)
        from datetime import date as _date
        _rs_today = (
            os.path.exists(resolved_signals_path)
            and _date.fromtimestamp(os.path.getmtime(resolved_signals_path)) == _date.today()
        )
        if _rs_today:
            print("  Reusing today's resolved_signals.json from Step 12 (skipping pre-screen)")
        else:
            _generate_resolved_signals_file(wl, resolved_signals_path, verbose=args.verbose)
        equity_path = _find_latest_equity_signals_file()
        selected = select_top_tickers(
            resolved_signals_path=resolved_signals_path,
            equity_signals_path=equity_path,
            max_tickers=top_n,
            min_agreement=min_agreement,
            always_include=always_include,
            force_tickers=None,
        )
        ticker_list = [s["ticker"] for s in selected]

    print(f"\nAI Quant: processing {len(ticker_list)} tickers\n")

    # ── Dry-run exit ──────────────────────────────────────────────────────────
    if args.dry_run:
        print(f"Dry run complete. Would process {len(ticker_list)} tickers.")
        print(f"Estimated cost: ~€{len(ticker_list) * 0.03:.2f}")
        return

    # ── API key required beyond this point ───────────────────────────────────
    if not os.environ.get("XAI_API_KEY"):
        print("  ERROR: XAI_API_KEY not set.")
        print("  Set it with: export XAI_API_KEY='your-key'")
        sys.exit(1)

    # ── Run Grok on selected tickers ──────────────────────────────────────────
    results = []
    for i, selection in enumerate(selected, 1):
        ticker = selection["ticker"]
        print(f"\n[{i}/{len(selected)}] {ticker}")

        # Cache check
        if use_cache:
            cached = get_cached_thesis(ticker)
            if cached:
                print(f"  [{ticker}] Using cached result from today — skipping API call.")
                results.append(cached)
                continue

        result = analyze_ticker(
            ticker, verbose=args.verbose, raw_output=args.raw, use_cache=False,
            force_ai=getattr(args, "force_ai", False),
        )
        if result:
            result["selection_rank"]   = i
            result["priority_score"]   = selection.get("priority_score", 0.0)
            result["selection_reason"] = selection.get("selection_reason", "")
            results.append(result)
        time.sleep(1)

    print_full_report(results)


# ==============================================================================
# SECTION 2: PROMPT CONSTRUCTION
# ==============================================================================

def _make_neutral_thesis(ticker: str, signals: dict, resolved: dict) -> dict:
    """
    Return a templated NEUTRAL thesis when conflict_resolver sets skip_claude=True.
    Saves ~$0.04/ticker for post-squeeze guards, pre-earnings holds, and similar blocks.

    The returned dict has all fields expected by save_thesis() and print_thesis().
    """
    overrides     = resolved.get("override_flags", [])
    override_str  = "; ".join(overrides) if overrides else "pre_resolved block"
    tech          = signals.get("technical") or {}
    price         = tech.get("price")
    max_conv      = resolved.get("max_conviction_override")  # None means no cap

    notes_parts = [f"Claude API call skipped — {override_str}"]
    if max_conv is not None:
        notes_parts.append(f"Max conviction cap: {max_conv}")

    return {
        "ticker":              ticker,
        "direction":           "NEUTRAL",
        "conviction":          1,
        "time_horizon":        "days",
        "entry_low":           price,
        "entry_high":          price,
        "stop_loss":           None,
        "target_1":            None,
        "target_2":            None,
        "position_size_pct":   resolved.get("position_size_override") or 0,
        "thesis":              f"No directional thesis: {override_str}.",
        "data_quality":        "MEDIUM",
        "notes":               " | ".join(notes_parts),
        "catalysts":           [],
        "risks":               [override_str],
        "raw_response":        "",
        "signals":             signals,
        "bull_probability":    0.33,
        "bear_probability":    0.33,
        "neutral_probability": 0.34,
        "signal_agreement_score": resolved.get("signal_agreement_score", 0.0),
        "key_invalidation":    None,
        "primary_scenario":    f"Blocked: {override_str}",
        "bear_scenario":       None,
        "expected_moves":      [],
    }


SYSTEM_PROMPT = """You are an elite institutional quant analyst running a global multi-factor signal engine.
You are given a single ticker from a carefully curated ~215-ticker watchlist built with a 5-factor
prescreen (20d momentum rank 35%, volume surge 20%, near 52wk high 15%, earnings momentum proxy 15%,
sector-relative strength 15%) + liquidity filter + volatility/beta quality gate.
Your job: produce a concise, high-conviction, actionable trading synthesis for the next 1-4 weeks.
Never hallucinate data. Use only the signals provided.

═══════════════════════════════════════════
ANALYSIS RULES (strictly follow)
═══════════════════════════════════════════

Insider / SEC signal
Always distinguish net buying vs net selling.
Only call it "informed accumulation" if net shares purchased > net shares sold in the last 90 days
(after excluding routine 10b5-1 sales). If net selling or only grants/10b5-1, say "net insider
selling" or "routine filings only".

Universe context
Always populate universe_rank and universe_status using the rank data passed to you. Use
"Persistent favorite" if the ticker has been in top-50 for 3+ consecutive days; "Tier-1" if
currently top-50; otherwise "Dynamic only".

Regime & circuit-breaker logic
Macro regime and weekly regime have override power.
If RISK_OFF or strong bearish weekly regime → cap conviction at 2/5 and position_size_pct ≤ 3
unless dark-pool / options / insider signals are overwhelmingly bullish.
High-beta names (crypto, small-cap biotech, semiconductors) get extra conservative sizing in RISK_OFF.

Signal weighting priority (highest → lowest)
1. Macro + weekly regime  +  Volatility regime (EXPANDING → reduce size; CONTRACTING → tighten stops)
2. Dark pool + options flow + max pain  +  Earnings event (HIGH risk → cap conviction ≤ 3)
3. Technical regime & volume profile  +  Liquidity (SMALL/MID tier → hard cap position_size_pct ≤ 3)
4. Relative strength vs RSP/sector  +  Insider + SEC + earnings transcript tone
5. Fundamentals / DCF / peer benchmarking
6. Historical analog score (weak prior — calibrate confidence only, never override hard rules)

Earnings & Event risk rules
If earnings_risk = HIGH (≤14d): cap conviction at 3/5 unless implied straddle move ≤ 0.7× avg historical surprise
magnitude (setup is "already priced in"). Always name the exact earnings date in key_invalidation.
If beat_rate_4q ≥ 0.75 and avg_surprise_magnitude > 5%: note "serial earnings beat" as a catalyst.
If beat_rate_4q ≤ 0.25: note "serial earnings miss risk" in risks array.

Relative strength rules
STRONG_OUTPERFORM (vs RSP 20d): adds +0.5 to qualitative conviction (non-integer signal, reflected in thesis).
STRONG_UNDERPERFORM: always mention in bear_scenario; discount BULL conviction by 1 point.
For sector rotation setups (ticker strong vs RSP but weak vs sector ETF): flag as "sector rotation laggard risk".

Liquidity & transaction cost rules
SMALL tier (<$1M ADV): maximum position_size_pct = 3. State "thin liquidity" in notes.
MID tier ($1-10M ADV): maximum position_size_pct = 5.
Spread > 30 bps: add spread cost to the entry friction note (e.g. "30bps spread adds ~$X round-trip cost").
If position is >20% of ADV: flag as "market impact risk" in risks.

Historical analog rules
analog_score ≥ 70: "Strong historical precedent — past setups with similar signal confluence resolved
[modal_direction] X% of the time." Include in thesis.
analog_score 40-69: mention analog in notes only; do not use in conviction calculation.
analog_score < 40 or unavailable: ignore; do not reference analogs in output.

Volatility regime rules
EXPANDING vol + RISK_ON macro: widen stop_loss by 1 ATR; reduce position_size_pct by 25%.
EXPANDING vol + RISK_OFF macro: maximum conviction = 2; maximum position_size_pct = 2.
CONTRACTING vol: note potential compression breakout; straddle cost context is especially relevant.
IV Rank (30-day) rules
If IV Rank < 25 (cheap options): apply +0.05 qualitative boost to options-based conviction.
  Note "cheap vol — asymmetric upside from options vs stock" in catalysts.
  Straddle / long call is attractive relative to expected move.
If IV Rank > 75 (expensive options): apply -0.05 qualitative discount to options-based conviction.
  Note "elevated IV crush risk — favour stock/delta position over buying premium" in risks.
  Straddle is expensive; prefer defined-risk directional spread or stock position.
If IV Rank 25–75 (normal): no adjustment; options fairly priced.
If IV Rank source = "estimated" or "Building history": treat as weak signal only.
  Do not cite IV rank in catalysts or risks — insufficient history.
  Rely on straddle cost and expected move instead for options pricing context.

Tone & style
Clinical, no hype. Always give a clear Primary vs Counter thesis.
Thesis should be 3-4 sentences explaining why the final conviction and sizing were chosen.
Be brutally honest when signals conflict.

prob_combined anchor rule
prob_combined is a pre-computed weighted probability (0.30–0.90) from 7 signal scalars.
It represents the quantitative prior for directional success.
Your bull_probability MUST be within ±0.10 of prob_combined.
If you believe the correct bull_probability lies outside that range, you MUST state why
in the thesis field (e.g. "overriding prob_combined — earnings catalyst not captured by base rate").
Never silently deviate from prob_combined without narrative justification.

═══════════════════════════════════════════
OUTPUT FORMAT (JSON — exact structure below)
═══════════════════════════════════════════
Output MUST be valid JSON with this exact structure:
{
  "ticker": "...",
  "direction": "BULL | BEAR | NEUTRAL",
  "bull_probability": 0.0-1.0,
  "bear_probability": 0.0-1.0,
  "neutral_probability": 0.0-1.0,
  "conviction": 1-5,
  "time_horizon": "days | weeks | months",
  "primary_scenario": "1-2 sentence bull case",
  "bear_scenario": "1-2 sentence bear case",
  "key_invalidation": "specific price level or event that breaks the thesis",
  "entry_low": price,
  "entry_high": price,
  "stop_loss": price,
  "target_1": price,
  "target_2": price (or null),
  "position_size_pct": 0-100 (percent of allocated crypto/equity slice),
  "signal_agreement_score": float (echo back the pre-computed value, or 0.0 if not provided),
  "catalysts": ["...", "..."],
  "risks": ["...", "..."],
  "thesis": "3-4 sentence balanced synthesis explaining conviction and sizing",
  "data_quality": "HIGH|MEDIUM|LOW",
  "notes": "any caveats, data gaps, or forward-looking assumptions",
  "universe_rank": "Ranked #NN / 215 in global multi-factor prescreen (factors: mom/vol-surge/near-high/earnings/sector-RS)",
  "universe_status": "Persistent favorite | Tier-1 | Dynamic only",
  "earnings_risk": "HIGH | MEDIUM | LOW | NONE (HIGH = earnings ≤14d away; affects conviction cap)",
  "vol_regime": "EXPANDING | CONTRACTING | STABLE | UNKNOWN (from 20d/60d realized vol ratio)",
  "liquidity_note": "string or null — populate if tier is SMALL/MID or position exceeds 20% of ADV",
  "analog_score": "float 0-100 or null — echo back the pre-computed historical analog score",
  "expected_moves": [
    {
      "horizon": "today",
      "bear_pct": -X.X,
      "base_pct": X.X,
      "bull_pct": X.X,
      "bear_price": price,
      "base_price": price,
      "bull_price": price,
      "bull_prob": 0.0-1.0,
      "bear_prob": 0.0-1.0,
      "neutral_prob": 0.0-1.0
    },
    { "horizon": "week", ... },
    { "horizon": "month", ... },
    { "horizon": "year", ... }
  ]
}

For expected_moves: use intraday volatility (ATR/daily range) for "today", weekly ATR for "week",
options expected move or fundamental catalysts for "month", and fundamental/macro thesis for "year".
Each row's bull_prob + bear_prob + neutral_prob MUST sum to 1.0.
IMPORTANT: bull_probability + bear_probability + neutral_probability MUST sum to exactly 1.0."""


def _build_prompt(signals: dict) -> str:
    """Build the analysis prompt from collected signals."""
    ticker            = signals["ticker"]
    agreement_score   = signals.get("signal_agreement_score")
    wr       = signals.get("weekly_regime", {})
    tech     = signals.get("technical", {})
    vp       = signals.get("volume_profile", {})
    fund     = signals.get("fundamentals", {})
    opts     = signals.get("options_flow", {})
    mp       = signals.get("max_pain") or {}
    sec      = signals.get("sec", {})
    catalyst = signals.get("catalyst", {})
    mr       = signals.get("market_regime", {})
    sr       = signals.get("ticker_sector_regime")
    sector   = signals.get("ticker_sector")
    evnt     = signals.get("earnings_event")   or {}
    rs       = signals.get("relative_strength") or {}
    liq      = signals.get("liquidity")        or {}
    analog   = signals.get("historical_analog") or {}
    vr       = signals.get("volatility_regime") or {}
    news_sent = signals.get("news_sentiment")  or {}

    prompt_parts = [
        f"Analyze {ticker} using the following signal data collected on {datetime.now().strftime('%Y-%m-%d')}.",
        "",
        "## MACRO REGIME",
    ]

    if mr and mr.get("regime"):
        regime      = mr.get("regime", "UNKNOWN")
        score       = mr.get("score", "?")
        comp        = mr.get("components", {})
        vix         = mr.get("vix")
        spy200      = mr.get("spy_vs_200ma")
        yc          = mr.get("yield_curve_spread")
        mult        = _rf.get_position_size_multiplier(regime) if _REGIME_AVAILABLE else "N/A"
        max_conv    = _rf.get_max_conviction(regime) if _REGIME_AVAILABLE else "N/A"
        sector_str  = f"  Sector ({sector}): {sr}" if sector and sr else ""
        prompt_parts += [
            f"Market regime: {regime} (composite score: {score:+d})",
            f"  Trend: {comp.get('trend', '?'):+d}  |  VIX: {comp.get('volatility', '?'):+d} "
            f"(VIX={vix})" if vix else
            f"  Trend: {comp.get('trend', '?'):+d}  |  VIX: {comp.get('volatility', '?'):+d}",
            f"  Credit (HYG): {comp.get('credit', '?'):+d}  |  Yield curve: {comp.get('yield_curve', '?'):+d} "
            f"(T10Y2Y={yc:+.3f}%)" if yc is not None else
            f"  Credit (HYG): {comp.get('credit', '?'):+d}  |  Yield curve: {comp.get('yield_curve', '?'):+d}",
        ]
        if sector_str:
            prompt_parts.append(sector_str)
        prompt_parts += [
            f"Position size multiplier: {mult}x  |  Max conviction allowed: {max_conv}/5",
            f"Note: {'Risk-on environment — full position sizing and momentum weights active.' if regime == 'RISK_ON' else 'Risk-off environment — reduce sizing, favour quality/mean-reversion signals.' if regime == 'RISK_OFF' else 'Transitional environment — moderate sizing, use balanced signal weights.'}",
        ]
    else:
        prompt_parts.append("Macro regime: unavailable")

    prompt_parts += [
        "",
        "## WEEKLY REGIME (Structural Trend Filter)",
    ]

    if wr and wr.get("regime") != "unknown":
        regime = wr.get("regime", "unknown").upper()
        prompt_parts += [
            f"Regime: {regime}",
            f"Price vs 20-week MA: ${wr.get('price', 'N/A')} vs ${wr.get('ma20w', 'N/A')} "
            f"({wr.get('pct_from_ma20w', 'N/A'):+.1f}%)" if isinstance(wr.get('pct_from_ma20w'), (int, float)) else
            f"Price vs 20-week MA: ${wr.get('price', 'N/A')} vs ${wr.get('ma20w', 'N/A')}",
            f"MA slope (4-week): {wr.get('ma_slope_4w_pct', 'N/A'):+.2f}%" if isinstance(wr.get('ma_slope_4w_pct'), (int, float)) else "",
            f"Note: {'Long setups aligned with weekly trend.' if wr.get('regime') in ('bullish', 'recovering') else 'Weekly structure is bearish — long setups are counter-trend and require higher conviction.'}",
        ]
    else:
        prompt_parts.append(f"Weekly regime: unavailable ({wr.get('reason', 'unknown error')})")

    prompt_parts += ["", "## TECHNICAL SIGNALS"]

    if tech:
        price = tech.get("price", "N/A")
        prompt_parts += [
            f"Price: ${price}",
            f"RSI(14): {tech.get('rsi_14', 'N/A')}",
            f"Trend: {'above' if tech.get('above_ma200') else 'below'} 200MA (${tech.get('ma200', 'N/A')}), "
            f"{'above' if tech.get('above_ma50') else 'below'} 50MA (${tech.get('ma50', 'N/A')})",
            f"Momentum: 1M={tech.get('momentum_1m_pct', 'N/A')}%, 3M={tech.get('momentum_3m_pct', 'N/A')}%, "
            f"6M={tech.get('momentum_6m_pct', 'N/A')}%",
            f"Volume ratio (5d/20d): {tech.get('volume_ratio_5d_vs_20d', 'N/A')}x",
            f"52w range: ${tech.get('low_52w', 'N/A')} - ${tech.get('high_52w', 'N/A')} "
            f"(currently {tech.get('pct_from_52w_high', 'N/A')}% from high)",
        ]
    else:
        prompt_parts.append("Technical data: unavailable")

    prompt_parts += ["", "## VOLATILITY REGIME"]
    if vr.get("vol_regime_available"):
        rv20  = vr.get("rv_20d_pct", "N/A")
        rv60  = vr.get("rv_60d_pct", "N/A")
        ratio = vr.get("vol_ratio_20_60")
        vreg  = vr.get("vol_regime", "UNKNOWN")
        prompt_parts += [
            f"Realized vol — 20d: {rv20}%  |  60d: {rv60}%  "
            f"|  ratio (20/60): {ratio:.3f} → {vreg}" if isinstance(ratio, float) else
            f"Realized vol — 20d: {rv20}%  |  60d: {rv60}%  |  regime: {vreg}",
        ]
        if vr.get("iv_rank") is not None:
            prompt_parts.append(
                f"IV rank: {vr['iv_rank']:.0f}/100  |  IV percentile: {vr.get('iv_percentile', 'N/A')}%"
                f"  |  Current IV: {vr.get('current_iv_pct', 'N/A')}%"
            )
        if vr.get("vix_current") is not None:
            prompt_parts.append(
                f"VIX: {vr['vix_current']}  ({vr.get('vix_percentile', 'N/A')}th percentile vs 1-year range)"
            )
        # Interpretation note
        if vreg == "EXPANDING":
            prompt_parts.append("Note: Vol is expanding — wider stops required; reduce position size vs base case.")
        elif vreg == "CONTRACTING":
            prompt_parts.append("Note: Vol is contracting — potential compression breakout setup; tighten targets.")
    else:
        prompt_parts.append("Volatility regime: unavailable")

    prompt_parts += ["", "## VOLUME PROFILE (Support & Resistance)"]
    if vp:
        sup  = vp.get("support_levels", [])
        res  = vp.get("resistance_levels", [])
        prompt_parts += [
            f"POC (highest volume):  ${vp.get('poc_price', 'N/A')}  ({vp.get('poc_distance_pct', 'N/A'):+.2f}% from price)" if isinstance(vp.get('poc_distance_pct'), (int, float)) else f"POC: ${vp.get('poc_price', 'N/A')}",
            f"Value Area:            ${vp.get('value_area_low', 'N/A')} — ${vp.get('value_area_high', 'N/A')}  (70% of volume)",
            f"VWAP 20d: ${vp.get('vwap_20d', 'N/A')}  |  VWAP 50d: ${vp.get('vwap_50d', 'N/A')}",
        ]
        if res:
            prompt_parts.append("Resistance levels (above price):")
            for r in res:
                prompt_parts.append(f"  ${r['price']}  dist: {r['distance_pct']:+.2f}%  strength: {r['strength_pct']}%")
        if sup:
            prompt_parts.append("Support levels (below price):")
            for s in sup:
                prompt_parts.append(f"  ${s['price']}  dist: {s['distance_pct']:+.2f}%  strength: {s['strength_pct']}%")
        lvns = vp.get("lvn_levels", [])
        if lvns:
            prompt_parts.append(f"Low-volume zones (price moves fast through): {lvns}")
    else:
        prompt_parts.append("Volume profile: unavailable")

    prompt_parts += ["", "## LIQUIDITY & TRANSACTION COST"]
    if liq.get("liquidity_available"):
        adv_usd   = liq.get("adv_dollars", 0)
        adv_str   = f"${adv_usd/1e6:.1f}M" if adv_usd >= 1_000_000 else f"${adv_usd:,.0f}"
        tier      = liq.get("liquidity_tier", "N/A")
        spread    = liq.get("spread_bps", "N/A")
        vol_ratio = liq.get("vol_ratio_vs_adv")
        prompt_parts += [
            f"ADV (30d): {liq.get('adv_shares', 'N/A'):,} shares ({adv_str}/day) — tier: {tier}",
            f"Today's volume: {liq.get('vol_today_shares', 'N/A'):,} shares"
            + (f"  ({vol_ratio:.1f}x vs ADV)" if isinstance(vol_ratio, float) else ""),
            f"Implied bid-ask spread: {spread} bps (half-spread proxy)",
        ]
        # Compute position-as-%-of-ADV for a reference 5% position
        try:
            ref_pos_usd = PORTFOLIO_NAV * EQUITY_ALLOCATION * 0.05
            pct_of_adv  = round(ref_pos_usd / adv_usd * 100, 2) if adv_usd > 0 else None
            if pct_of_adv is not None:
                liquidity_risk = "LOW" if pct_of_adv < 5 else ("MEDIUM" if pct_of_adv < 20 else "HIGH")
                prompt_parts.append(
                    f"Reference 5% position (${ref_pos_usd:,.0f}): {pct_of_adv:.1f}% of ADV "
                    f"— liquidity risk: {liquidity_risk}"
                )
        except Exception:
            pass
        if tier in ("SMALL", "MID"):
            prompt_parts.append(
                "Note: Thin liquidity — cap position_size_pct ≤ 3% and use limit orders."
            )
    else:
        prompt_parts.append("Liquidity data: unavailable")

    prompt_parts += ["", "## RELATIVE STRENGTH / SECTOR CONTEXT"]
    if rs.get("rs_available"):
        etf = rs.get("sector_etf", "SPY")
        hdr = f"{'Period':<8}  {'Ticker':>8}  {'vs RSP':>8}  {'vs Sector(' + etf + ')':>16}"
        prompt_parts.append(hdr)
        for label in ("20d", "60d", "120d"):
            t_ret  = rs.get(f"ticker_return_{label}")
            vs_rsp = rs.get(f"vs_rsp_{label}")
            vs_etf = rs.get(f"vs_sector_{label}")
            if t_ret is None:
                continue
            def _fmt(v: Optional[float]) -> str:
                return f"{v:+.1f}%" if isinstance(v, float) else "N/A"
            prompt_parts.append(
                f"  {label:<6}  {_fmt(t_ret):>8}  {_fmt(vs_rsp):>8}  {_fmt(vs_etf):>16}"
            )
        prompt_parts.append(f"RS signal (20d vs RSP): {rs.get('rs_signal_20d', 'N/A')}")
        sig_20 = rs.get("rs_signal_20d", "INLINE")
        if sig_20 in ("STRONG_UNDERPERFORM", "UNDERPERFORM"):
            prompt_parts.append(
                "Note: Relative weakness vs market — discount long setups; confirm with regime and dark-pool flow."
            )
        elif sig_20 == "STRONG_OUTPERFORM":
            prompt_parts.append(
                "Note: Strong relative strength — momentum confirmation for BULL thesis."
            )
    else:
        prompt_parts.append("Relative strength data: unavailable")

    prompt_parts += ["", "## OPTIONS FLOW"]
    if opts:
        # ── IV Rank display ──────────────────────────────────────────────────
        iv_rank_val  = opts.get("iv_rank")       # float 0–100 or 0.0 fallback
        iv_src       = opts.get("iv_source", "estimated")
        iv_hist_days = opts.get("iv_history_days", 0)
        iv_true_pct  = opts.get("true_iv_pct")

        if iv_src == "true" and isinstance(iv_rank_val, float) and iv_rank_val > 0:
            # Real Supabase-backed 30-day rank
            if iv_rank_val < 25:
                iv_label = "Low IV — options cheap, asymmetric upside if direction is right"
            elif iv_rank_val > 75:
                iv_label = "High IV — options expensive, favour delta/stock over buying premium"
            else:
                iv_label = "Normal IV — options fairly priced"
            iv_rank_str = (
                f"{iv_rank_val:.1f}%  [{iv_label}]"
                f"  (30-day window, {iv_hist_days} snapshots, source: Black-Scholes)"
            )
        elif iv_hist_days > 0:
            # Snapshots exist but fewer than 5 — show progress
            iv_rank_str = (
                f"Building history ({iv_hist_days} of 5 snapshots — "
                f"recheck in {max(0, 5 - iv_hist_days)} days)"
            )
        elif isinstance(iv_rank_val, float) and iv_rank_val > 0:
            # Fallback realized-vol estimate — label it clearly
            iv_rank_str = f"{iv_rank_val:.1f}%  [estimated from realized vol — true rank pending]"
        else:
            iv_rank_str = "Building history (no snapshots yet — recheck after first daily run)"

        prompt_parts += [
            f"Heat score: {opts.get('heat_score', 'N/A')}/100",
            f"Options direction: {opts.get('direction', 'N/A')}",
            f"Expected move ({opts.get('days_to_exp', '?')}d): {opts.get('expected_move_pct', 'N/A')}%",
            f"Implied vol (ATM): {iv_true_pct}%  |  Chain IV: {opts.get('implied_vol_pct', 'N/A')}%"
            if iv_true_pct is not None else
            f"Implied vol: {opts.get('implied_vol_pct', 'N/A')}%",
            f"IV Rank (30-day): {iv_rank_str}",
            f"Put/call ratio: {opts.get('pc_ratio', 'N/A')}",
            f"Total options volume: {opts.get('total_options_vol', 'N/A'):,}" if isinstance(opts.get('total_options_vol'), int) else f"Total options volume: {opts.get('total_options_vol', 'N/A')}",
            f"Straddle cost: ${opts.get('straddle_cost', 'N/A')}",
        ]
    else:
        prompt_parts.append("Options data: unavailable (possibly crypto or thin options)")

    # Max pain — always rendered regardless of opts availability (uses own yfinance fetch)
    if mp and mp.get("max_pain_strike"):
        dist_str = f"{mp['distance_pct']:+.1f}%" if isinstance(mp.get("distance_pct"), float) else "N/A"
        prompt_parts += [
            f"Max pain strike: ${mp['max_pain_strike']}  (expiry: {mp.get('expiry', 'N/A')}"
            f", {mp.get('days_to_expiry', '?')}d)",
            f"Price vs max pain: current ${mp.get('current_price', 'N/A')} is "
            f"{mp.get('direction', '?')} max pain by {dist_str}",
            "Note: price tends to gravitate toward max pain into OpEx — "
            "use as entry zone anchor and T1 confirmation near expiry.",
        ]
    else:
        prompt_parts.append("Max pain: unavailable (no options chain or thin market)")

    prompt_parts += ["", "## EARNINGS & EVENT CALENDAR"]
    if evnt.get("earnings_available"):
        dte      = evnt.get("days_to_next_earnings")
        risk     = evnt.get("earnings_risk", "LOW")
        dte_str  = f"{dte}d away" if dte is not None else "date unknown"
        prompt_parts += [
            f"Next earnings: {evnt.get('next_earnings_date', 'N/A')} ({dte_str}) — earnings risk: {risk}",
        ]
        # Implied move from options cross-reference
        if opts and opts.get("expected_move_pct") is not None:
            prompt_parts.append(
                f"Options-implied move ({opts.get('days_to_exp', '?')}d straddle): "
                f"±{opts.get('expected_move_pct')}%  (straddle cost: ${opts.get('straddle_cost', 'N/A')})"
            )
        surprises = evnt.get("earnings_surprises_4q", [])
        if surprises:
            prompt_parts.append("Historical EPS surprises (last 4Q):")
            for s in surprises:
                sup_str = f"{s['surprise_pct']:+.1f}%" if s.get("surprise_pct") is not None else "N/A"
                prompt_parts.append(
                    f"  {s.get('date', '?')}: est ${s.get('eps_estimate', '?')} → "
                    f"actual ${s.get('eps_actual', '?')}  (surprise: {sup_str})"
                )
        mag   = evnt.get("avg_surprise_magnitude")
        brate = evnt.get("beat_rate_4q")
        if mag is not None or brate is not None:
            prompt_parts.append(
                f"Avg surprise magnitude: {mag}%  |  Beat rate: {int(brate*4) if brate is not None else '?'}/4 quarters"
            )
        if risk == "HIGH":
            prompt_parts.append(
                "CAUTION: Earnings within 14 days — binary event risk. "
                "Cap conviction ≤ 3/5 unless implied move is already priced in vs historical magnitude."
            )
    else:
        prompt_parts.append("Earnings calendar: unavailable")

    prompt_parts += ["", "## CATALYST SETUP (Short Squeeze / Volatility Compression)"]
    if catalyst:
        float_m = catalyst.get("float_shares", 0)
        float_str = f"{float_m/1e6:.1f}M" if float_m and float_m > 0 else "N/A"
        prompt_parts += [
            f"Short % of float: {catalyst.get('short_pct_float', 'N/A'):.1%}" if isinstance(catalyst.get('short_pct_float'), float) else f"Short % of float: {catalyst.get('short_pct_float', 'N/A')}",
            f"Days to cover (DTC): {catalyst.get('days_to_cover', 'N/A')}",
            f"Float: {float_str}  |  Institutional ownership: {catalyst.get('inst_ownership', 'N/A'):.1%}" if isinstance(catalyst.get('inst_ownership'), float) else f"Float: {float_str}",
            f"Insider ownership: {catalyst.get('insider_ownership', 'N/A'):.1%}" if isinstance(catalyst.get('insider_ownership'), float) else f"Insider ownership: {catalyst.get('insider_ownership', 'N/A')}",
            f"Short squeeze score: {catalyst.get('short_squeeze_score', 'N/A')}/{catalyst.get('short_squeeze_max', 'N/A')}",
        ]
        for flag in catalyst.get("short_squeeze_flags", []):
            prompt_parts.append(f"  • {flag}")
        prompt_parts.append(f"Volatility compression score: {catalyst.get('vol_compression_score', 'N/A')}/{catalyst.get('vol_compression_max', 'N/A')}")
        for flag in catalyst.get("vol_compression_flags", []):
            prompt_parts.append(f"  • {flag}")
    else:
        prompt_parts.append("Catalyst setup data: unavailable")

    prompt_parts += ["", "## FUNDAMENTAL SIGNALS"]
    if fund:
        prompt_parts += [
            f"Fundamental score: {fund.get('fundamental_score_pct', 'N/A')}%",
            f"P/E (trailing): {fund.get('pe_ratio', 'N/A')}",
            f"P/E (forward): {fund.get('forward_pe', 'N/A')}",
            f"Revenue growth YoY: {fund.get('revenue_growth_yoy', 'N/A')}",
            f"EPS growth YoY: {fund.get('eps_growth_yoy', 'N/A')}",
            f"Gross margin: {fund.get('gross_margin', 'N/A')}",
            f"Next earnings: {fund.get('next_earnings_days', 'N/A')} days away",
            f"Analyst consensus: {fund.get('analyst_rating', 'N/A')} | "
            f"Target: ${fund.get('analyst_price_target', 'N/A')} "
            f"({fund.get('analyst_upside_pct', 'N/A')}% upside)",
        ]
    else:
        prompt_parts.append("Fundamental data: unavailable")

    prompt_parts += ["", "## SEC FILINGS (Insider / Activist / Institutional)"]
    if sec:
        prompt_parts += [
            f"SEC signal score: {sec.get('score', 'N/A')}/{sec.get('max', 'N/A')}",
        ]
        for flag in sec.get("flags", []):
            prompt_parts.append(f"  • {flag}")
        if not sec.get("flags"):
            prompt_parts.append("  No notable SEC filings detected")
    else:
        prompt_parts.append("SEC data: unavailable")

    dp = signals.get("dark_pool_flow") or {}
    prompt_parts += ["", "## DARK POOL FLOW (FINRA ATS)"]
    if dp and dp.get("signal"):
        prompt_parts += [
            f"Signal: {dp.get('signal', 'N/A')}  "
            f"(short_ratio_zscore={dp.get('short_ratio_zscore', 'N/A'):+.2f})"
            if isinstance(dp.get('short_ratio_zscore'), float) else
            f"Signal: {dp.get('signal', 'N/A')}",
            f"Short ratio today: {dp.get('short_ratio_today', 0):.3%}"
            if isinstance(dp.get('short_ratio_today'), float) else
            f"Short ratio today: {dp.get('short_ratio_today', 'N/A')}",
            f"Days of FINRA data: {dp.get('days_of_data', 'N/A')}",
        ]
    else:
        prompt_parts.append("Dark pool data: unavailable")

    prompt_parts += ["", "## NEWS SENTIMENT (7-day)"]
    ns_src   = news_sent.get("source", "fallback_neutral")
    ns_label = news_sent.get("sentiment_label", "Neutral")
    ns_avg   = news_sent.get("avg_sentiment", 0.0)
    ns_n     = news_sent.get("articles_found", 0)
    if ns_src == "marketaux" and ns_n > 0:
        avg_str = f"{ns_avg:+.3f}" if isinstance(ns_avg, float) else "N/A"
        prompt_parts += [
            f"Source: Marketaux (entity-linked NLP sentiment)",
            f"Articles analysed: {ns_n}  |  Avg entity sentiment: {avg_str}  |  Label: {ns_label}",
        ]
        if ns_label == "Bullish":
            prompt_parts.append(
                "Note: Positive news flow confirms catalyst thesis — "
                "add +0.05 qualitative weight to BULL conviction."
            )
        elif ns_label == "Bearish":
            prompt_parts.append(
                "Note: Negative news flow is a headwind — "
                "apply -0.05 qualitative discount to BULL conviction; flag in risks."
            )
    else:
        prompt_parts.append(
            "News sentiment: unavailable (no Marketaux key or no entity-matched articles — neutral assumed)"
        )

    prompt_parts += ["", "## HISTORICAL ANALOG SCORE"]
    if analog.get("analog_available"):
        ascore = analog.get("analog_score", 0)
        modal  = analog.get("modal_direction", "NEUTRAL")
        n_srch = analog.get("n_searched", 0)
        prompt_parts += [
            f"Analog score: {ascore:.0f}/100  (searched {n_srch} historical setups; modal direction: {modal})",
            "Top-3 most similar past setups:",
        ]
        for i, a in enumerate(analog.get("top_3_analogs", []), 1):
            prompt_parts.append(
                f"  {i}. {a.get('ticker','?')} ({a.get('date','?')}): "
                f"direction={a.get('direction','?')}  conviction={a.get('conviction','?')}  "
                f"similarity={a.get('similarity','?')}%"
            )
        prompt_parts.append(
            "Note: Analog score is a weak prior — use only to calibrate confidence, "
            "never override regime or hard constraints."
        )
    else:
        reason = analog.get("reason", "no historical theses cached yet")
        prompt_parts.append(f"Historical analog: unavailable ({reason})")

    # Portfolio context
    prompt_parts += [
        "",
        "## PORTFOLIO CONTEXT",
        f"Portfolio NAV: ${PORTFOLIO_NAV:,}",
        f"Equity allocation: {EQUITY_ALLOCATION*100:.0f}% | Crypto allocation: {CRYPTO_ALLOCATION*100:.0f}%",
    ]

    # Probability assessment block
    _pr = signals.get("prob_combined_result") or {}
    _pc = signals.get("prob_combined")
    if _pc is not None and _pr:
        _dq    = _pr.get("data_quality", "LOW")
        _ninputs = sum(1 for v in (_pr.get("inputs_used") or {}).values() if v is not None)
        prompt_parts += [
            "",
            "## PROBABILITY ASSESSMENT (pre-computed, calibrated)",
            f"prob_combined:  {_pc:.3f}  ({_dq} confidence — {_ninputs}/7 inputs real)",
            f"  ├─ Technical:   {_pr.get('prob_technical', 0):.3f}  (RSI normalized)",
            f"  ├─ Options:     {_pr.get('prob_options', 0):.3f}  (heat_score/100)",
            f"  ├─ Catalyst:    {_pr.get('prob_catalyst', 0):.3f}  (earnings beat rate 4Q)",
            f"  └─ News:        {_pr.get('prob_news', 0):.3f}  (7-day Marketaux sentiment)",
            "IMPORTANT: Your bull_probability output must stay within ±0.10 of prob_combined "
            "unless you explicitly justify the deviation in your thesis text.",
        ]

    # Pre-computed signal agreement + conflict resolution context
    cr_data = signals.get("conflict_resolution") or {}
    if agreement_score is not None or cr_data:
        prompt_parts += [
            "",
            "## PRE-COMPUTED SIGNAL AGREEMENT & CONFLICT RESOLUTION",
        ]
        if agreement_score is not None:
            prompt_parts += [
                f"signal_agreement_score: {agreement_score:.4f}",
                "IMPORTANT: signal_agreement_score is pre-computed — do not recalculate it.",
                "Use it as a confidence prior. A score above 0.75 means strong module consensus.",
            ]
        if cr_data:
            direction = cr_data.get("pre_resolved_direction", "NEUTRAL")
            conf      = cr_data.get("pre_resolved_confidence", 0.0)
            bull_w    = cr_data.get("bull_weight", 0.0)
            bear_w    = cr_data.get("bear_weight", 0.0)
            votes     = cr_data.get("module_votes") or {}
            overrides = cr_data.get("override_flags") or []
            max_conv_cap   = cr_data.get("max_conviction_override")
            pos_size_cap   = cr_data.get("position_size_override")

            prompt_parts += [
                "",
                f"Pre-resolved direction: {direction}  (confidence: {conf:.0%})",
                f"Weighted vote — bull: {bull_w:.3f}  |  bear: {bear_w:.3f}",
            ]
            # Per-module breakdown (only modules that cast a vote)
            active_votes = [(m, v) for m, v in votes.items() if v is not None]
            if active_votes:
                vote_str = "  |  ".join(f"{m.replace('_', ' ')}: {v}" for m, v in active_votes)
                prompt_parts.append(f"Module votes: {vote_str}")
            if overrides:
                prompt_parts.append(f"Override/context flags: {'; '.join(overrides)}")
            if max_conv_cap is not None:
                prompt_parts.append(
                    f"HARD CONSTRAINT: max conviction = {max_conv_cap} (regime override applied)"
                )
            if pos_size_cap is not None:
                prompt_parts.append(
                    f"HARD CONSTRAINT: max position_size_pct = {pos_size_cap:.1f}% (regime override applied)"
                )
            prompt_parts += [
                "",
                f"Your role: reason about WHY the pre_resolved_direction of '{direction}' is correct, "
                f"or explain what nuance the weighted vote missed. "
                f"Do NOT recalculate the weighted vote — accept it as a given and add qualitative depth.",
            ]

    prompt_parts += [
        "",
        "## TASK",
        f"Produce a quant thesis for {ticker} in the required JSON format.",
        "Position size % = percent of the relevant allocation slice (equity or crypto).",
        "Be specific about price levels based on the technical data provided.",
        "If data is contradictory or thin, reflect that in conviction score and data_quality.",
    ]

    return "\n".join(prompt_parts)


# ==============================================================================
# SECTION 3: CLAUDE API CALL
# ==============================================================================

def _validate_probabilities(thesis: dict) -> None:
    """
    Warn (not raise) if bull + bear + neutral probabilities do not sum to ~1.0.
    Tolerance: ±0.05.
    """
    bull    = thesis.get("bull_probability")    or 0.0
    bear    = thesis.get("bear_probability")    or 0.0
    neutral = thesis.get("neutral_probability") or 0.0
    if bull == 0.0 and bear == 0.0 and neutral == 0.0:
        return  # Probabilities not present; nothing to validate
    total = bull + bear + neutral
    if abs(total - 1.0) > 0.05:
        warnings.warn(
            f"[{thesis.get('ticker', '?')}] Probability sum {total:.3f} ≠ 1.0 "
            f"(bull={bull}, bear={bear}, neutral={neutral}). Check Claude response.",
            UserWarning,
            stacklevel=2,
        )


# Model routing: premium kicks in when signal_agreement_score ≥ AI_PREMIUM_THRESHOLD (config.py)
XAI_BASE_URL = "https://api.x.ai/v1"

_last_call_usage: dict = {"input_tokens": 0, "output_tokens": 0, "model": AI_MODEL_DEFAULT}


def _grok_stream(client, model: str, prompt: str) -> str:
    """Inner streaming call — returns full response text. Raises on error."""
    full_text = ""
    with client.chat.completions.create(
        model=model,
        max_tokens=8192,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        stream=True,
    ) as stream:
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                full_text += delta.content
    return full_text


def _call_claude(prompt: str, verbose: bool = False, use_thinking: bool = False) -> Optional[str]:
    """
    Call xAI Grok with streaming (OpenAI-compatible API).

    Model routing:
      use_thinking=False → AI_MODEL_DEFAULT  (grok-4-1-fast-reasoning)
      use_thinking=True  → AI_MODEL_PREMIUM  (grok-4.20-0309-reasoning)
                           falls back to AI_MODEL_FALLBACK on any error

    Returns the response text, or None on failure.
    Token usage is stored in module-level _last_call_usage after each call.
    """
    if not _OPENAI_AVAILABLE:
        print("ERROR: openai package not installed. Run: pip install openai")
        return None
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        print("  ERROR: XAI_API_KEY environment variable not set.")
        print("         export XAI_API_KEY='your-key-here'")
        return None

    primary_model = AI_MODEL_PREMIUM if use_thinking else AI_MODEL_DEFAULT
    client = _OpenAI(api_key=api_key, base_url=XAI_BASE_URL)

    if verbose:
        label = f"premium ({AI_MODEL_PREMIUM})" if use_thinking else f"default ({AI_MODEL_DEFAULT})"
        print(f"  Calling xAI Grok API ({label})...", flush=True)
    elif use_thinking:
        print(f"  [premium model — agreement ≥ {AI_PREMIUM_THRESHOLD}]", flush=True)

    try:
        full_text = _grok_stream(client, primary_model, prompt)
    except Exception as e:
        if use_thinking:
            # Fallback: retry with the cheaper model instead of failing
            print(f"  [premium failed ({e}), retrying with fallback {AI_MODEL_FALLBACK}]", flush=True)
            try:
                full_text = _grok_stream(client, AI_MODEL_FALLBACK, prompt)
                primary_model = AI_MODEL_FALLBACK
            except Exception as e2:
                err = str(e2)
                if "401" in err or "authentication" in err.lower():
                    print("  ERROR: Invalid XAI_API_KEY.")
                elif "429" in err or "rate" in err.lower():
                    print("  ERROR: Grok API rate limit hit. Wait and retry.")
                else:
                    print(f"  ERROR: Grok fallback also failed: {e2}")
                return None
        else:
            err = str(e)
            if "401" in err or "authentication" in err.lower():
                print("  ERROR: Invalid XAI_API_KEY.")
            elif "429" in err or "rate" in err.lower():
                print("  ERROR: Grok API rate limit hit. Wait and retry.")
            else:
                print(f"  ERROR: Unexpected error calling Grok: {e}")
            return None

    _last_call_usage["input_tokens"]  = (len(SYSTEM_PROMPT) + len(prompt)) // 4
    _last_call_usage["output_tokens"] = len(full_text) // 4
    _last_call_usage["model"]         = primary_model

    return full_text.strip() if full_text else None


def _call_anthropic(prompt: str, verbose: bool = False,
                    model: str = "claude-sonnet-4-6") -> Optional[str]:
    """
    Call Anthropic Claude API (non-streaming).

    model: claude-sonnet-4-6 (default) or claude-opus-4-6
    Token usage is stored in module-level _last_call_usage after each call.
    """
    if not _ANTHROPIC_AVAILABLE:
        print("ERROR: anthropic package not installed. Run: pip install anthropic")
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ERROR: ANTHROPIC_API_KEY environment variable not set.")
        return None

    if verbose:
        print(f"  Calling Anthropic Claude API ({model})...", flush=True)

    try:
        client = _anthropic_module.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text if response.content else ""
        _last_call_usage["input_tokens"]  = response.usage.input_tokens
        _last_call_usage["output_tokens"] = response.usage.output_tokens
        _last_call_usage["model"]         = model
        return text.strip() if text else None
    except Exception as e:
        err = str(e)
        if "401" in err or "authentication" in err.lower():
            print("  ERROR: Invalid ANTHROPIC_API_KEY.")
        elif "429" in err or "rate" in err.lower():
            print("  ERROR: Anthropic API rate limit hit. Wait and retry.")
        else:
            print(f"  ERROR: Anthropic API call failed: {e}")
        return None


def _parse_response(raw: str) -> Optional[dict]:
    """Extract JSON from Claude's response, with truncation recovery."""
    if not raw:
        return None

    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    import re

    # Extract the JSON candidate string from various fence patterns
    candidates = []
    for pattern in [r"```json\s*([\s\S]+?)\s*```", r"```\s*([\s\S]+?)\s*```", r"(\{[\s\S]+\})"]:
        m = re.search(pattern, raw)
        if m:
            candidates.append(m.group(1))

    # If no closed fence found, the response was truncated — grab everything after ```json
    if not candidates:
        m = re.search(r"```json\s*([\s\S]+)", raw)
        if m:
            candidates.append(m.group(1))

    for candidate in candidates:
        # Try as-is first
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # Try truncation recovery: strip trailing partial key/value, then close open brackets
        try:
            text = candidate.strip().rstrip(",")
            # Count unclosed braces/brackets
            depth_curly = text.count("{") - text.count("}")
            depth_square = text.count("[") - text.count("]")
            # Close open string if we're inside one (odd number of unescaped quotes in last line)
            last_line = text.rsplit("\n", 1)[-1]
            if last_line.count('"') % 2 == 1:
                text += '"'
            # Close open array/object chains
            text += "]" * max(0, depth_square) + "}" * max(0, depth_curly)
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, Exception):
            pass

    return None


# ==============================================================================
# SECTION 4: PRE-SCREENER (signal scoring, no Claude API cost)
# ==============================================================================

def _pre_screen_score(signals: dict) -> dict:
    """
    Score a ticker's collected signals without calling Claude.
    Returns {score, max, flags, grade} for ranking candidates.

    Scoring breakdown (max = 28):
      Weekly Regime -3 to +3 — structural trend filter (bearish penalized)
      Technical      0 to 10 — trend, momentum, volume
      Fundamental    0 to  5 — valuation, growth, analyst
      Catalyst       0 to  5 — short squeeze, vol compression, congress
      SEC            0 to  3 — insider buying, activist stakes
      Options        0 to  2 — bullish flow
    """
    score = 0
    flags = []

    wr   = signals.get("weekly_regime", {})
    tech = signals.get("technical", {})
    fund = signals.get("fundamentals", {})
    cat  = signals.get("catalyst", {})
    cong = signals.get("congress", {})
    sec  = signals.get("sec", {})
    opts = signals.get("options_flow", {})

    # ── Weekly Regime (max +3, min -3) ────────────────────────────────────────
    regime = wr.get("regime", "unknown")
    if regime == "bullish":
        score += 3; flags.append("Weekly regime: BULLISH (above MA, slope up)")
    elif regime == "weakening":
        score += 1; flags.append("Weekly regime: WEAKENING (above MA, slope rolling over)")
    elif regime == "recovering":
        score += 1; flags.append("Weekly regime: RECOVERING (below MA, slope turning up)")
    elif regime == "bearish":
        score -= 3; flags.append("Weekly regime: BEARISH (below MA, slope down) — counter-trend")

    # ── Technical (max 10) ────────────────────────────────────────────────────
    if tech:
        if tech.get("above_ma200"):
            score += 2; flags.append("Above 200MA")
        if tech.get("above_ma50"):
            score += 1; flags.append("Above 50MA")
        if tech.get("above_ma20"):
            score += 1; flags.append("Above 20MA")

        rsi = tech.get("rsi_14", 50)
        if 40 <= rsi <= 68:
            score += 2; flags.append(f"RSI healthy ({rsi})")
        elif rsi > 68:
            flags.append(f"RSI overbought ({rsi})")

        if (tech.get("momentum_1m_pct") or 0) > 0:
            score += 1; flags.append("1M momentum positive")
        if (tech.get("momentum_3m_pct") or 0) > 0:
            score += 1; flags.append("3M momentum positive")
        if (tech.get("volume_ratio_5d_vs_20d") or 0) >= 1.1:
            score += 1; flags.append("Volume expanding")
        if (tech.get("pct_from_52w_low") or 0) > 15:
            score += 1; flags.append("Strong off 52w low")

    # ── Fundamental (max 5) ───────────────────────────────────────────────────
    if fund:
        if (fund.get("fundamental_score_pct") or 0) >= 60:
            score += 2; flags.append(f"Strong fundamentals ({fund['fundamental_score_pct']}%)")
        elif (fund.get("fundamental_score_pct") or 0) >= 40:
            score += 1

        if (fund.get("analyst_upside_pct") or 0) >= 15:
            score += 2; flags.append(f"Analyst upside {fund['analyst_upside_pct']}%")
        elif (fund.get("analyst_upside_pct") or 0) >= 5:
            score += 1

        if (fund.get("revenue_growth_yoy") or 0) > 0:
            score += 1; flags.append("Revenue growing YoY")

    # ── Catalyst (max 5) ──────────────────────────────────────────────────────
    if cat:
        sq = cat.get("short_squeeze_score", 0)
        sq_max = cat.get("short_squeeze_max", 1) or 1
        if sq / sq_max >= 0.5:
            score += 2; flags.append(f"Short squeeze setup ({sq}/{sq_max})")

        vc = cat.get("vol_compression_score", 0)
        vc_max = cat.get("vol_compression_max", 1) or 1
        if vc / vc_max >= 0.5:
            score += 2; flags.append(f"Volatility compression ({vc}/{vc_max})")

    if cong and (cong.get("congress_score") or 0) > 0:
        score += 1; flags.append(f"Congress buying ({cong.get('congress_score')})")

    # ── SEC Filings (max 3) ───────────────────────────────────────────────────
    if sec:
        sec_score = sec.get("score", 0)
        if sec_score >= 3:
            score += 3; flags.append(f"Strong SEC signals ({sec_score}/{sec.get('max','?')})")
        elif sec_score >= 1:
            score += 1; flags.append(f"SEC signal ({sec_score}/{sec.get('max','?')})")

    # ── Options Flow (max 2) ──────────────────────────────────────────────────
    if opts:
        heat = opts.get("heat_score", 0)
        direction = opts.get("direction", "")
        if heat >= 60 and "bull" in str(direction).lower():
            score += 2; flags.append(f"Bullish options flow (heat={heat})")
        elif heat >= 40 and "bull" in str(direction).lower():
            score += 1

    # ── Grade ─────────────────────────────────────────────────────────────────
    pct = score / 28
    if pct >= 0.72:
        grade = "A"
    elif pct >= 0.52:
        grade = "B"
    elif pct >= 0.36:
        grade = "C"
    else:
        grade = "D"

    return {"score": score, "max": 25, "grade": grade, "flags": flags}


def _build_screen_universe(mode: str = "all") -> List[str]:
    """
    Build the ticker universe to scan.

    Modes:
      watchlist — only tickers already in watchlist.txt
      small     — small-cap / high-momentum pool from catalyst_screener
      meme      — meme/retail favourites
      large     — large-cap / mega-cap
      config    — EQUITY_WATCHLIST + CUSTOM_WATCHLIST from config.py
      all       — everything combined (default)
    """
    tickers: set = set()

    if mode in ("watchlist",):
        tickers.update(_read_watchlist_tickers())
        return sorted(tickers)

    # Always include watchlist so existing holdings are re-evaluated
    tickers.update(_read_watchlist_tickers())

    # Pull config universe
    try:
        from config import EQUITY_WATCHLIST, CUSTOM_WATCHLIST
        tickers.update(EQUITY_WATCHLIST)
        tickers.update(CUSTOM_WATCHLIST)
    except ImportError:
        pass

    # Pull catalyst_screener universes
    try:
        import catalyst_screener as cs
        if mode in ("small", "all"):
            tickers.update(getattr(cs, "SMALL_CAP_UNIVERSE", []))
        if mode in ("meme", "all"):
            tickers.update(getattr(cs, "MEME_UNIVERSE", []))
        if mode in ("large", "all"):
            tickers.update(getattr(cs, "LARGE_CAP_WATCH", []))
    except ImportError:
        pass

    return sorted(tickers)


def screen_tickers(
    tickers: List[str],
    min_score: int = 8,
    top_n: int = 0,
    verbose: bool = False,
    regime_filter: bool = True,
) -> List[dict]:
    """
    Score every ticker in the universe without calling Claude.
    Returns full ranked list filtered by min_score (then top_n if set).

    regime_filter=True (default): fetch weekly regime first and skip bearish
    tickers before collecting any other signals, saving time.
    """
    print(f"\n  Screening {len(tickers)} tickers (no API cost)...\n")
    results = []
    skipped_bearish = []

    for i, ticker in enumerate(tickers, 1):
        print(f"  [{i:>3}/{len(tickers)}] {ticker:<8}", end=" ", flush=True)

        # Weekly regime pre-filter: skip structurally bearish names immediately
        if regime_filter:
            wr = _get_weekly_regime(ticker)
            if wr.get("regime") == "bearish":
                skipped_bearish.append(ticker)
                print(f"SKIP  weekly regime BEARISH "
                      f"(${wr.get('price','?')} < 20w MA ${wr.get('ma20w','?')}, "
                      f"slope {wr.get('ma_slope_4w_pct', 0):+.1f}%)")
                continue

        signals = collect_all_signals(ticker, verbose=False)
        screen  = _pre_screen_score(signals)
        results.append({
            "ticker":  ticker,
            "signals": signals,
            "screen":  screen,
        })
        print(f"score={screen['score']:>2}/28  grade={screen['grade']}")

    if skipped_bearish:
        print(f"\n  [regime filter] Skipped {len(skipped_bearish)} bearish tickers: "
              f"{', '.join(skipped_bearish)}")

    results.sort(key=lambda r: r["screen"]["score"], reverse=True)

    qualified = [r for r in results if r["screen"]["score"] >= min_score]
    if top_n > 0:
        qualified = qualified[:top_n]

    return qualified


def print_screen_table(results: List[dict], watchlist_tickers: set = None) -> None:
    """Print ranked screening results table."""
    if watchlist_tickers is None:
        watchlist_tickers = set(_read_watchlist_tickers())

    print()
    print("AI QUANT — SCREENER RESULTS")
    print("=" * 80)
    print(f"  {'#':<3}  {'TICKER':<8}  {'SCORE':>6}  {'GRADE'}  {'WL?':<5}  TOP SIGNALS")
    print("  " + "-" * 74)

    for rank, r in enumerate(results, 1):
        sc       = r["screen"]["score"]
        grade    = r["screen"]["grade"]
        ticker   = r["ticker"]
        in_wl    = "YES" if ticker in watchlist_tickers else "new"
        flags    = r["screen"]["flags"][:3]
        flag_str = " | ".join(flags) if flags else "—"
        print(f"  {rank:<3}  {ticker:<8}  {sc:>4}/28  [{grade}]  {in_wl:<5}  {flag_str}")

    print()


def update_watchlist_from_screen(
    results: List[dict],
    watchlist_path: str = "./watchlist.txt",
    min_tier1: int = 18,   # score >= 18 → TIER 1  (grade A)
    min_tier2: int = 13,   # score >= 13 → TIER 2  (grade B)
    min_tier3: int = 8,    # score >=  8 → TIER 3  (grade C)
) -> None:
    """
    Re-write watchlist.txt using screener scores.

    Tiers:
      TIER 1  score >= 18  (grade A)   — highest conviction buys
      TIER 2  score >= 13  (grade B)   — worth monitoring
      TIER 3  score >=  8  (grade C)   — weak signal, low priority
      Dropped score <  8  (grade D)   — removed from watchlist
    """
    from datetime import datetime as _dt

    tier1, tier2, tier3 = [], [], []
    for r in results:
        sc     = r["screen"]["score"]
        ticker = r["ticker"]
        flags  = r["screen"]["flags"]
        note   = flags[0] if flags else ""
        entry  = f"{ticker:<8}  # {sc}/25 — {note}"
        if sc >= min_tier1:
            tier1.append(entry)
        elif sc >= min_tier2:
            tier2.append(entry)
        elif sc >= min_tier3:
            tier3.append(entry)
        # Below min_tier3 → not written

    today = _dt.now().strftime("%Y-%m-%d")
    lines = (
        [
            "# ============================================================",
            f"# WATCHLIST — AI Quant screener  |  updated {today}",
            "# Scored by 10-module signal quality (max 28, weekly regime ±3)",
            "# TIER 1 ≥18  TIER 2 ≥13  TIER 3 ≥8  (below 8 excluded)",
            "# ============================================================",
            "",
            "# TIER 1 — High conviction (Grade A, score ≥18)",
        ] + tier1 + [
            "",
            "# TIER 2 — Monitor (Grade B, score ≥13)",
        ] + tier2 + [
            "",
            "# TIER 3 — Weak signal (Grade C, score ≥8)",
        ] + tier3 + [""]
    )

    with open(watchlist_path, "w") as f:
        f.write("\n".join(lines))

    dropped = len(results) - len(tier1) - len(tier2) - len(tier3)
    print(f"  Watchlist updated: {len(tier1)} TIER 1 | {len(tier2)} TIER 2 | {len(tier3)} TIER 3 | {dropped} dropped")
    print(f"  Written to: {watchlist_path}")


# ==============================================================================
# SECTION 5: ANALYSIS PIPELINE
# ==============================================================================

def analyze_ticker(ticker: str, verbose: bool = False, raw_output: bool = False,
                   use_cache: bool = True, llm: str = "grok",
                   force_ai: bool = False) -> Optional[dict]:
    """
    Full AI quant analysis for one ticker.
    Returns parsed thesis dict, or None on failure.
    Checks SQLite cache first (today's date); skips API call if hit.
    Pass use_cache=False to force a fresh analysis.
    """
    ticker = ticker.upper().strip()

    # --- Cache check ---
    if use_cache:
        cached = get_cached_thesis(ticker)
        if cached:
            print(f"\n  [{ticker}] Using cached result from today — skipping API call.")
            try:
                from utils.usage import log_api_usage
                log_api_usage(module="thesis", model=AI_MODEL_DEFAULT,
                              input_tokens=0, output_tokens=0, ticker=ticker, cache_hit=True)
            except Exception:
                pass
            return cached

    print(f"\n  Analyzing {ticker}...")

    # Collect signals
    signals = collect_all_signals(ticker, verbose=verbose)

    # === NEW: UNIVERSE RANK EXPORT FOR AI QUANT ===
    signals = _inject_universe_rank(signals, ticker)

    # Pre-compute agreement score (kept for backward compat; may be overridden by resolver)
    signals["signal_agreement_score"] = compute_signal_agreement(signals)

    # ── Calibrated probability (single source of truth across both pipelines) ─
    try:
        from utils.prob_engine import compute_prob_combined as _compute_prob_combined
        _prob_result = _compute_prob_combined(signals)
        signals["prob_combined_result"] = _prob_result
        signals["prob_combined"] = _prob_result["prob_combined"]
    except Exception as _prob_exc:
        logger.warning("compute_prob_combined failed for %s: %s", ticker, _prob_exc)
        signals["prob_combined"] = signals.get("signal_agreement_score", 0.50) or 0.50
        signals["prob_combined_result"] = {}

    # ── Conflict resolution — pre-resolve before Claude ──────────────────────
    if _RESOLVER_AVAILABLE:
        try:
            mr_dict    = signals.get("market_regime") or {}
            regime_str = mr_dict.get("regime", "TRANSITIONAL") if mr_dict else "TRANSITIONAL"
            resolved   = _cr.resolve(signals, regime_str)
            signals["conflict_resolution"] = resolved
            # Resolver's agreement score uses MODULE_WEIGHTS; prefer it over simple vote
            signals["signal_agreement_score"] = resolved["signal_agreement_score"]

            if resolved["skip_claude"] and not force_ai:
                flags = resolved.get("override_flags", [])
                flag0 = flags[0] if flags else "pre-resolved block"
                print(f"  [{ticker}] Claude skipped — {flag0}")
                thesis = _make_neutral_thesis(ticker, signals, resolved)
                save_thesis(thesis)
                return thesis
            elif resolved["skip_claude"] and force_ai:
                flags = resolved.get("override_flags", [])
                flag0 = flags[0] if flags else "pre-resolved block"
                print(f"  [{ticker}] --force-ai: overriding skip_claude ({flag0}) — calling AI anyway")
        except Exception as exc:
            logger.warning("Conflict resolver failed for %s: %s", ticker, exc)

    # Build prompt (includes conflict_resolution context if available)
    prompt = _build_prompt(signals)

    if verbose and raw_output:
        print("\n--- PROMPT ---")
        print(prompt)
        print("--- END PROMPT ---\n")

    # Route to the selected LLM backend
    if llm == "claude":
        # Anthropic Claude (claude-sonnet-4-6)
        raw = _call_anthropic(prompt, verbose=verbose)
    elif llm == "grok-premium":
        # Force xAI Grok premium model regardless of agreement score
        raw = _call_claude(prompt, verbose=verbose, use_thinking=True)
    else:
        # Default: xAI Grok — upgrade to premium for highest-conviction setups
        # Use prob_combined as primary signal; fall back to agreement score
        _prob_gate = signals.get("prob_combined") or signals.get("signal_agreement_score", 0.0) or 0.0
        use_thinking = _prob_gate >= AI_PREMIUM_THRESHOLD
        raw = _call_claude(prompt, verbose=verbose, use_thinking=use_thinking)
    if raw is None:
        return None

    if raw_output:
        print("\n--- RAW CLAUDE RESPONSE ---")
        print(raw)
        print("--- END RESPONSE ---\n")

    # Parse response
    thesis = _parse_response(raw)
    if thesis is None:
        print(f"  WARNING: Could not parse JSON from Claude response for {ticker}")
        print(f"  Raw response: {raw[:500]}...")
        return None

    thesis["ticker"] = ticker
    thesis["signals"] = signals
    thesis["raw_response"] = raw
    # Always use the pre-computed agreement score — don't rely on Claude to echo it back
    thesis["signal_agreement_score"] = signals.get("signal_agreement_score", 0.0)
    # Persist prob_combined components for calibration tracking
    _pr = signals.get("prob_combined_result") or {}
    thesis["prob_combined"]  = signals.get("prob_combined") or _pr.get("prob_combined")
    thesis["prob_technical"] = _pr.get("prob_technical")
    thesis["prob_options"]   = _pr.get("prob_options")
    thesis["prob_catalyst"]  = _pr.get("prob_catalyst")
    thesis["prob_news"]      = _pr.get("prob_news")

    # ── Apply conflict resolver hard constraints (Override 2: bear market cap) ─
    if _RESOLVER_AVAILABLE:
        try:
            cr_data = signals.get("conflict_resolution") or {}
            max_conv_cap  = cr_data.get("max_conviction_override")
            pos_size_cap  = cr_data.get("position_size_override")

            if max_conv_cap is not None:
                raw_conv = thesis.get("conviction", 5)
                if isinstance(raw_conv, (int, float)) and raw_conv > max_conv_cap:
                    thesis["conviction"] = max_conv_cap
                    thesis["notes"] = (
                        f"[Conviction capped at {max_conv_cap} — conflict resolver] "
                        + (thesis.get("notes") or "")
                    ).strip()

            if pos_size_cap is not None:
                raw_pos = thesis.get("position_size_pct", 0) or 0
                if isinstance(raw_pos, (int, float)) and raw_pos > pos_size_cap:
                    thesis["position_size_pct"] = pos_size_cap
                    thesis["notes"] = (
                        f"[Position capped at {pos_size_cap:.1f}% — conflict resolver] "
                        + (thesis.get("notes") or "")
                    ).strip()
        except Exception:
            pass

    # ── Cap conviction by market regime (regime_filter layer) ────────────────
    if _REGIME_AVAILABLE:
        try:
            mr_cached  = signals.get("market_regime", {})
            mkt_regime = mr_cached.get("regime") if mr_cached else None
            if mkt_regime:
                max_conv = _rf.get_max_conviction(mkt_regime)
                raw_conv = thesis.get("conviction", 5)
                if isinstance(raw_conv, (int, float)) and raw_conv > max_conv:
                    thesis["conviction"] = max_conv
                    thesis["notes"] = (
                        f"[Conviction capped at {max_conv} — {mkt_regime} regime] "
                        + (thesis.get("notes") or "")
                    ).strip()
        except Exception:
            pass

    # Validate probability sum; emit warning if not ~1.0
    _validate_probabilities(thesis)

    # --- Stamp model provenance + cost ---
    try:
        from utils.usage import compute_cost
        _model  = _last_call_usage.get("model", AI_MODEL_DEFAULT)
        _in_tok = _last_call_usage.get("input_tokens", 0)
        _out_tok= _last_call_usage.get("output_tokens", 0)
        thesis["model_used"] = _model
        thesis["cost_usd"]   = round(compute_cost(_model, _in_tok, _out_tok), 4)
    except Exception:
        pass

    # --- Save to cache ---
    save_thesis(thesis)

    # --- Log API usage ---
    try:
        from utils.usage import log_api_usage
        log_api_usage(
            module="thesis",
            model=_last_call_usage.get("model", AI_MODEL_DEFAULT),
            input_tokens=_last_call_usage.get("input_tokens", 0),
            output_tokens=_last_call_usage.get("output_tokens", 0),
            ticker=ticker,
            cache_hit=False,
        )
    except Exception:
        pass

    return thesis


def analyze_tickers(tickers: List[str], verbose: bool = False,
                    raw_output: bool = False, use_cache: bool = True) -> List[dict]:
    """Analyze multiple tickers, returning sorted by conviction."""
    results = []
    for i, ticker in enumerate(tickers, 1):
        print(f"\n[{i}/{len(tickers)}] {ticker}")
        result = analyze_ticker(ticker, verbose=verbose, raw_output=raw_output,
                                use_cache=use_cache)
        if result:
            results.append(result)
        time.sleep(1)  # Brief pause between API calls

    # Sort by conviction desc, then direction (BULL first)
    def sort_key(r):
        direction_order = {"BULL": 0, "NEUTRAL": 1, "BEAR": 2}
        return (
            -(r.get("conviction", 0)),
            direction_order.get(r.get("direction", "NEUTRAL"), 1),
        )

    return sorted(results, key=sort_key)


# ==============================================================================
# SECTION 5: PRINTING
# ==============================================================================

DIRECTION_ICON = {"BULL": "🟢", "BEAR": "🔴", "NEUTRAL": "🟡"}
CONVICTION_BARS = {1: "▪░░░░", 2: "▪▪░░░", 3: "▪▪▪░░", 4: "▪▪▪▪░", 5: "▪▪▪▪▪"}


def print_thesis(t: dict) -> None:
    """Print formatted thesis for one ticker."""
    if not t:
        return

    ticker = t.get("ticker", "?")
    direction = t.get("direction", "NEUTRAL")
    conviction = t.get("conviction", 0)
    icon = DIRECTION_ICON.get(direction, "◯")
    bars = CONVICTION_BARS.get(conviction, "?????")

    print()
    print(f"  ┌─ {ticker} {'─'*(54-len(ticker))}┐")
    print(f"  │  {icon} {direction:<8}  Conviction: {conviction}/5 {bars}          │")
    print(f"  │  Horizon: {str(t.get('time_horizon','?')):<10}  Data quality: {t.get('data_quality','?'):<6}    │")
    print(f"  └{'─'*58}┘")
    print()

    # Regime line
    sigs = t.get("signals", {})
    mr   = sigs.get("market_regime", {}) if isinstance(sigs, dict) else {}
    if mr and mr.get("regime") and _REGIME_AVAILABLE:
        regime      = mr.get("regime", "?")
        score       = mr.get("score", 0)
        mult        = _rf.get_position_size_multiplier(regime)
        sector_reg  = sigs.get("ticker_sector_regime") if isinstance(sigs, dict) else None
        sector_str  = f" | Sector: {sector_reg}" if sector_reg else ""
        print(f"  Regime: {regime} (score: {score:+d}){sector_str} | Position multiplier: {mult:.1f}x")
        print()

    # Price levels
    entry_low = t.get("entry_low")
    entry_high = t.get("entry_high")
    stop = t.get("stop_loss")
    t1 = t.get("target_1")
    t2 = t.get("target_2")
    pos_pct = t.get("position_size_pct")

    if entry_low and entry_high:
        print(f"  Entry:     ${entry_low:.2f} – ${entry_high:.2f}")
    if stop:
        print(f"  Stop:      ${stop:.2f}")
    if t1:
        t2_str = f" → ${t2:.2f}" if t2 else ""
        print(f"  Target:    ${t1:.2f}{t2_str}")
    if pos_pct is not None:
        print(f"  Size:      {pos_pct:.0f}% of allocation slice")

    # R/R ratio
    if entry_high and stop and t1:
        try:
            risk = entry_high - stop
            reward = t1 - entry_high
            if risk > 0:
                rr = reward / risk
                print(f"  R/R:       {rr:.1f}x")
        except Exception:
            pass

    # Agreement / probabilistic summary line
    agreement = t.get("signal_agreement_score")
    bull_prob = t.get("bull_probability")
    bear_prob = t.get("bear_probability")
    key_inv   = t.get("key_invalidation")
    summary_parts = []
    if agreement is not None:
        summary_parts.append(f"Agreement: {agreement:.0%}")
    if bull_prob is not None and bear_prob is not None:
        summary_parts.append(f"Bull: {bull_prob:.0%} / Bear: {bear_prob:.0%}")
    if key_inv:
        summary_parts.append(f"Invalidation: {key_inv}")
    if summary_parts:
        print(f"  {' | '.join(summary_parts)}")

    print()

    # Scenarios
    primary = t.get("primary_scenario")
    bear_sc  = t.get("bear_scenario")
    if primary:
        print(f"  Primary:  {primary}")
    if bear_sc:
        print(f"  Counter:  {bear_sc}")
    if primary or bear_sc:
        print()

    # Thesis
    print(f"  Thesis: {t.get('thesis', 'N/A')}")
    print()

    # Universe rank
    u_rank   = t.get("universe_rank")
    u_status = t.get("universe_status")
    if u_rank:
        status_str = f"  Status: {u_status}" if u_status else ""
        print(f"  {u_rank}.{status_str}")
        print()

    # Catalysts
    catalysts = t.get("catalysts", [])
    if catalysts:
        print("  Catalysts:")
        for c in catalysts[:3]:
            print(f"    ✓ {c}")

    # Risks
    risks = t.get("risks", [])
    if risks:
        print("  Risks:")
        for r in risks[:3]:
            print(f"    ✗ {r}")

    if t.get("notes"):
        print(f"\n  Notes: {t['notes']}")

    print()


def print_summary_table(results: List[dict]) -> None:
    """Print compact summary of all analyzed tickers."""
    if not results:
        return

    print()
    print("AI QUANT — SUMMARY TABLE")
    print("=" * 80)
    print(f"  {'TICKER':<8} {'DIR':<7} {'CONV':>5}  {'ENTRY':>8}  {'STOP':>8}  {'TARGET':>8}  {'SIZE%':>6}  {'HORIZON'}")
    print("  " + "-" * 74)

    for t in results:
        icon = DIRECTION_ICON.get(t.get("direction", "NEUTRAL"), "◯")
        entry = f"${t['entry_low']:.2f}" if t.get("entry_low") else "   N/A"
        stop = f"${t['stop_loss']:.2f}" if t.get("stop_loss") else "   N/A"
        target = f"${t['target_1']:.2f}" if t.get("target_1") else "   N/A"
        size = f"{t['position_size_pct']:.0f}%" if t.get("position_size_pct") is not None else "  N/A"
        conv = t.get("conviction", 0)
        print(
            f"  {t['ticker']:<8} {icon} {t.get('direction','?'):<5} "
            f"{conv:>5}  {entry:>8}  {stop:>8}  {target:>8}  {size:>6}  "
            f"{t.get('time_horizon','?')}"
        )

    print()


def print_full_report(results: List[dict]) -> None:
    """Print complete AI quant analysis."""
    print()
    print("================================================================")
    print(f"  AI QUANT ANALYSIS — POWERED BY {AI_MODEL_DEFAULT.upper()} / {AI_MODEL_PREMIUM.upper()}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("================================================================")

    if not results:
        print("  No results.")
        return

    print_summary_table(results)

    print("─" * 62)
    print("  DETAILED THESES")
    print("─" * 62)

    for t in results:
        print_thesis(t)
        print()

    # Portfolio allocation summary
    bulls = [r for r in results if r.get("direction") == "BULL"]
    bears = [r for r in results if r.get("direction") == "BEAR"]
    neutrals = [r for r in results if r.get("direction") == "NEUTRAL"]

    print("─" * 62)
    print("  PORTFOLIO SIGNAL SUMMARY")
    print("─" * 62)
    print(f"  Bull:    {len(bulls)} ticker(s): {', '.join(r['ticker'] for r in bulls)}")
    print(f"  Bear:    {len(bears)} ticker(s): {', '.join(r['ticker'] for r in bears)}")
    print(f"  Neutral: {len(neutrals)} ticker(s): {', '.join(r['ticker'] for r in neutrals)}")

    high_conv = [r for r in results if r.get("conviction", 0) >= 4]
    if high_conv:
        print()
        print("  High conviction (4-5/5):")
        for r in high_conv:
            icon = DIRECTION_ICON.get(r.get("direction", "NEUTRAL"), "◯")
            print(f"    {icon} {r['ticker']} — {r.get('thesis','')[:80]}...")
    print()


# ==============================================================================
# SECTION 6: REPORT FILE ANALYSIS
# ==============================================================================

def analyze_report_file(report_path: str, verbose: bool = False) -> Optional[str]:
    """
    Send an existing signal report file to Grok for portfolio-level analysis.
    Uses grok-4-20 for complex multi-ticker synthesis.
    """
    if not _OPENAI_AVAILABLE:
        print("ERROR: openai package not installed. Run: pip install openai")
        return None

    if not os.path.exists(report_path):
        print(f"  ERROR: Report file not found: {report_path}")
        return None

    with open(report_path) as f:
        content = f.read()

    # Truncate if very long (keep first 80k chars to stay within context)
    max_chars = 80_000
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n[... report truncated at {max_chars} chars ...]"

    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        print("  ERROR: XAI_API_KEY not set.")
        return None

    client = _OpenAI(api_key=api_key, base_url=XAI_BASE_URL)

    system = """You are a senior portfolio manager and quant analyst at a hedge fund.
Analyze the provided weekly signal report and give a structured portfolio briefing:

1. TOP 3 HIGHEST CONVICTION IDEAS — with thesis, entry, stop, target, sizing
2. KEY RISKS THIS WEEK — macro, sector, position-specific
3. PORTFOLIO POSITIONING — recommended adjustments
4. WATCHLIST PRIORITIES — which tickers deserve immediate deep dive
5. SIGNALS TO IGNORE — what's noise in this report

Be direct, specific, and quantitative. Use actual price levels from the data."""

    prompt = f"""Analyze this weekly signal report for my portfolio:\n\n{content}"""

    print(f"  Sending report to Grok ({len(content):,} chars)...")

    full_response = ""
    try:
        with client.chat.completions.create(
            model=AI_MODEL_PREMIUM,
            max_tokens=8192,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            stream=True,
        ) as stream:
            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    full_response += delta.content
                    if verbose:
                        print(delta.content, end="", flush=True)

        return full_response.strip()

    except Exception as e:
        print(f"  ERROR: {e}")
        return None


# ==============================================================================
# SECTION 7: CLI
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="AI Quant Analyst — Claude-powered signal synthesis"
    )
    parser.add_argument("--ticker", type=str, help="Single ticker analysis")
    parser.add_argument("--tickers", nargs="+", help="Multiple tickers")
    parser.add_argument("--watchlist", action="store_true", help="TIER 1 + TIER 2 watchlist (full Claude analysis)")
    parser.add_argument("--tier1-only", action="store_true", help="TIER 1 watchlist only (full Claude analysis)")
    parser.add_argument("--screen", action="store_true",
                        help="Screen full universe by signal quality — finds high-potential tickers, no Claude cost")
    parser.add_argument("--universe", choices=["all", "large", "small", "meme", "config", "watchlist"],
                        default="all",
                        help="Universe to scan with --screen (default: all)")
    parser.add_argument("--top", type=int, default=0, metavar="N",
                        help="After --screen, run Claude on top N candidates")
    parser.add_argument("--min-score", type=int, default=8, metavar="N",
                        help="Minimum signal score to appear in --screen results (default: 8/25)")
    parser.add_argument("--update-watchlist", action="store_true",
                        help="After --screen, rewrite watchlist.txt using screener scores")
    parser.add_argument("--report", type=str, help="Analyze existing signal report file")
    parser.add_argument("--raw", action="store_true", help="Show raw Claude response")
    parser.add_argument("--verbose", action="store_true", help="Show collection progress")
    parser.add_argument("--no-cache", action="store_true",
                        help="Force fresh analysis, ignoring today's cached results")
    parser.add_argument("--force-ai", action="store_true",
                        help="Bypass conflict-resolver skip_claude blocks — always call the AI")
    parser.add_argument("--cache-show", action="store_true",
                        help="Show recent cached results and exit")
    # ── Top-N priority selection args ─────────────────────────────────────────
    try:
        from config import AI_QUANT_MAX_TICKERS, AI_QUANT_MIN_AGREEMENT
        _default_top_n   = AI_QUANT_MAX_TICKERS
        _default_min_agr = AI_QUANT_MIN_AGREEMENT
    except ImportError:
        _default_top_n   = 10
        _default_min_agr = 0.60
    parser.add_argument(
        "--top-n", type=int, default=None, metavar="N",
        help=(
            f"Run Claude on top N tickers by priority score "
            f"(default when used: {_default_top_n}). "
            f"Uses TIER 1+2 watchlist. --tickers acts as force list."
        ),
    )
    parser.add_argument(
        "--min-agreement", type=float, default=None, metavar="F",
        help=(
            f"Min signal_agreement_score 0.0-1.0 for top-n mode "
            f"(default: {_default_min_agr:.2f})"
        ),
    )
    parser.add_argument(
        "--no-limit", action="store_true",
        help="Run on ALL non-skipped tickers (WARNING: high API cost)",
    )
    parser.add_argument(
        "--llm", type=str, default="grok",
        choices=["grok", "grok-premium", "claude"],
        help="LLM backend: grok (default xAI fast), grok-premium (forced premium Grok), claude (Anthropic Claude Sonnet)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print selection table and cost estimate without calling Claude",
    )
    parser.add_argument(
        "--backfill-agreement", action="store_true",
        help="Recompute signal_agreement_score for all cached tickers (no API calls)",
    )
    parser.add_argument(
        "--dump-prompt",
        action="store_true",
        help="Collect signals and print prompt for Claude Code analysis (no API call needed)",
    )
    parser.add_argument(
        "--inject-response",
        type=str,
        metavar="FILE",
        help="Read Claude Code thesis response from FILE and save to cache (use with --ticker)",
    )
    args = parser.parse_args()
    use_cache = not args.no_cache

    # --backfill-agreement: recompute agreement scores from stored signals_json, no Claude calls
    if args.backfill_agreement:
        try:
            conn = _init_db()
        except Exception as exc:
            print(f"Backfill skipped — DB unreachable: {exc}")
            return
        cur = conn.cursor()
        cur.execute("SELECT ticker, signals_json FROM thesis_cache")
        rows = cur.fetchall()
        updated = 0
        for r in rows:
            sigs = json.loads(r["signals_json"]) if r["signals_json"] else {}
            score = compute_signal_agreement(sigs)
            cur.execute(
                "UPDATE thesis_cache SET signal_agreement_score=%s WHERE ticker=%s",
                (score, r["ticker"]),
            )
            updated += 1
        conn.commit()
        conn.close()
        print(f"Backfilled signal_agreement_score for {updated} cached ticker(s).")
        return

    # --cache-show: print cache table and exit
    if args.cache_show:
        print()
        print("================================================================")
        print(f"  AI QUANT ANALYST — POWERED BY {AI_MODEL_DEFAULT.upper()} / {AI_MODEL_PREMIUM.upper()}")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("================================================================")
        print_cache_table()
        return

    print()
    print("================================================================")
    print("  AI QUANT ANALYST — POWERED BY CLAUDE OPUS 4.6")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("================================================================")
    print()

    # ── --dump-prompt: collect signals, print for Claude Code analysis ──────────
    if args.dump_prompt:
        tickers = []
        if args.ticker:
            tickers = [args.ticker.upper()]
        elif args.tickers:
            tickers = [t.upper() for t in args.tickers]
        else:
            print("  ERROR: --dump-prompt requires --ticker or --tickers")
            sys.exit(1)

        os.makedirs(os.path.join("data", "prompts"), exist_ok=True)

        for ticker in tickers:
            print(f"\n  Collecting signals for {ticker}...")
            signals = collect_all_signals(ticker, verbose=args.verbose)
            signals = _inject_universe_rank(signals, ticker)
            signals["signal_agreement_score"] = compute_signal_agreement(signals)

            # Run conflict resolver — may skip Claude entirely
            skip_claude = False
            if _RESOLVER_AVAILABLE:
                try:
                    mr_dict    = signals.get("market_regime") or {}
                    regime_str = mr_dict.get("regime", "TRANSITIONAL") if mr_dict else "TRANSITIONAL"
                    resolved   = _cr.resolve(signals, regime_str)
                    signals["conflict_resolution"] = resolved
                    signals["signal_agreement_score"] = resolved["signal_agreement_score"]
                    if resolved["skip_claude"]:
                        flags  = resolved.get("override_flags", [])
                        flag0  = flags[0] if flags else "pre-resolved block"
                        print(f"  [{ticker}] Claude skipped — {flag0} (neutral thesis saved)")
                        thesis = _make_neutral_thesis(ticker, signals, resolved)
                        save_thesis(thesis)
                        skip_claude = True
                except Exception as exc:
                    logger.warning("Conflict resolver failed for %s: %s", ticker, exc)

            if skip_claude:
                continue

            prompt    = _build_prompt(signals)
            dump_path = os.path.join("data", "prompts", f"{ticker}_prompt.json")
            with open(dump_path, "w") as fh:
                json.dump(
                    {"ticker": ticker, "system_prompt": SYSTEM_PROMPT,
                     "user_prompt": prompt,
                     "agreement_score": signals.get("signal_agreement_score", 0.0),
                     "signals": signals},
                    fh, indent=2, default=str,
                )

            print(f"\n{'='*70}")
            print(f"  SIGNAL DUMP: {ticker}  (agreement={signals.get('signal_agreement_score', 0.0):.2f})")
            print(f"  Prompt saved to: {dump_path}")
            print(f"{'='*70}")
            print(f"\n[SYSTEM PROMPT]\n")
            print(SYSTEM_PROMPT)
            print(f"\n[SIGNAL DATA]\n")
            print(prompt)
            print(f"\n{'='*70}")
            print(f"  After Claude Code analysis, save thesis to cache:")
            print(f"  python3 ai_quant.py --ticker {ticker} --inject-response <response_file>")
            print(f"{'='*70}")
        return

    # ── --inject-response: save Claude Code thesis to Supabase cache ────────────
    if args.inject_response:
        if not args.ticker:
            print("  ERROR: --inject-response requires --ticker")
            sys.exit(1)

        ticker        = args.ticker.upper()
        response_file = args.inject_response

        if not os.path.exists(response_file):
            print(f"  ERROR: Response file not found: {response_file}")
            sys.exit(1)

        with open(response_file, "r") as fh:
            raw = fh.read().strip()

        # Reload signals from the dump so caps can be applied
        signals   = {}
        dump_path = os.path.join("data", "prompts", f"{ticker}_prompt.json")
        if os.path.exists(dump_path):
            with open(dump_path, "r") as fh:
                signals = json.load(fh).get("signals", {})

        thesis = _parse_response(raw)
        if thesis is None:
            print(f"  ERROR: Could not parse JSON from: {response_file}")
            print(f"  Preview: {raw[:300]}")
            sys.exit(1)

        thesis["ticker"]                = ticker
        thesis["signals"]               = signals
        thesis["raw_response"]          = raw
        thesis["signal_agreement_score"] = signals.get("signal_agreement_score", 0.0)

        # Apply conflict resolver hard caps
        if _RESOLVER_AVAILABLE:
            try:
                cr_data      = signals.get("conflict_resolution") or {}
                max_conv_cap = cr_data.get("max_conviction_override")
                pos_size_cap = cr_data.get("position_size_override")
                if max_conv_cap is not None:
                    raw_conv = thesis.get("conviction", 5)
                    if isinstance(raw_conv, (int, float)) and raw_conv > max_conv_cap:
                        thesis["conviction"] = max_conv_cap
                        thesis["notes"] = (
                            f"[Conviction capped at {max_conv_cap} — conflict resolver] "
                            + (thesis.get("notes") or "")
                        ).strip()
                if pos_size_cap is not None:
                    raw_pos = thesis.get("position_size_pct", 0) or 0
                    if isinstance(raw_pos, (int, float)) and raw_pos > pos_size_cap:
                        thesis["position_size_pct"] = pos_size_cap
                        thesis["notes"] = (
                            f"[Position capped at {pos_size_cap:.1f}% — conflict resolver] "
                            + (thesis.get("notes") or "")
                        ).strip()
            except Exception:
                pass

        # Apply regime conviction cap
        if _REGIME_AVAILABLE:
            try:
                mr_cached  = signals.get("market_regime", {})
                mkt_regime = mr_cached.get("regime") if mr_cached else None
                if mkt_regime:
                    max_conv = _rf.get_max_conviction(mkt_regime)
                    raw_conv = thesis.get("conviction", 5)
                    if isinstance(raw_conv, (int, float)) and raw_conv > max_conv:
                        thesis["conviction"] = max_conv
                        thesis["notes"] = (
                            f"[Conviction capped at {max_conv} — {mkt_regime} regime] "
                            + (thesis.get("notes") or "")
                        ).strip()
            except Exception:
                pass

        _validate_probabilities(thesis)
        save_thesis(thesis)
        print(f"\n  [{ticker}] Thesis saved to cache.")
        print_thesis(thesis)
        return

    # --screen does NOT need the API key for signal collection
    if args.screen:
        universe = _build_screen_universe(args.universe)
        if not universe:
            print("  No tickers found for the selected universe.")
            sys.exit(1)

        print(f"  Universe: {args.universe.upper()} — {len(universe)} tickers")

        # Score ALL tickers in universe, no top_n limit yet (need full list for watchlist update)
        all_scored = screen_tickers(universe, min_score=0, top_n=0, verbose=args.verbose)

        # Tickers already in watchlist (for WL? column)
        wl_set = set(_read_watchlist_tickers())

        # Qualified candidates for display
        qualified = [r for r in all_scored if r["screen"]["score"] >= args.min_score]
        display   = qualified[:args.top] if args.top else qualified
        print_screen_table(display, watchlist_tickers=wl_set)

        if not qualified:
            print(f"  No tickers passed the min-score threshold ({args.min_score}/25).")
        else:
            new_finds = [r["ticker"] for r in qualified if r["ticker"] not in wl_set]
            if new_finds:
                print(f"  New high-potential tickers NOT yet in watchlist: {', '.join(new_finds)}")
                print()

        # Optionally update watchlist.txt
        if args.update_watchlist:
            update_watchlist_from_screen(all_scored)

        # Optionally run Claude on the top N
        if args.top and qualified:
            top_candidates = display
            if not os.environ.get("XAI_API_KEY"):
                print("  ERROR: XAI_API_KEY not set — cannot run Grok analysis.")
                print("  Set it with: export XAI_API_KEY='your-key'")
                sys.exit(1)

            print(f"  Running Grok analysis on top {len(top_candidates)}: "
                  f"{', '.join(r['ticker'] for r in top_candidates)}")
            results = []
            for i, r in enumerate(top_candidates, 1):
                ticker = r["ticker"]
                print(f"\n[{i}/{len(top_candidates)}] {ticker}")
                # Check cache first
                if use_cache:
                    cached = get_cached_thesis(ticker)
                    if cached:
                        print(f"  [{ticker}] Using cached result from today — skipping API call.")
                        cached["screen"] = r["screen"]
                        results.append(cached)
                        continue
                r["signals"]["signal_agreement_score"] = compute_signal_agreement(r["signals"])
                prompt = _build_prompt(r["signals"])
                raw    = _call_claude(prompt, verbose=args.verbose)
                if raw is None:
                    continue
                if args.raw:
                    print(raw)
                thesis = _parse_response(raw)
                if thesis:
                    thesis["ticker"]       = ticker
                    thesis["signals"]      = r["signals"]
                    thesis["raw_response"] = raw
                    thesis["screen"]       = r["screen"]
                    _validate_probabilities(thesis)
                    save_thesis(thesis)
                    results.append(thesis)
                time.sleep(1)

            results.sort(key=lambda r: (-(r.get("conviction", 0)),
                                        {"BULL": 0, "NEUTRAL": 1, "BEAR": 2}.get(r.get("direction", "NEUTRAL"), 1)))
            print_full_report(results)
        return

    # ── Top-N priority selection mode (--top-n / --no-limit / --dry-run) ──────
    if args.top_n is not None or args.no_limit or args.dry_run:
        _run_top_n_mode(args, use_cache)
        return

    if not os.environ.get("XAI_API_KEY"):
        print("  ERROR: XAI_API_KEY not set.")
        print("  Set it with: export XAI_API_KEY='your-key'")
        sys.exit(1)

    if args.report:
        print(f"  Analyzing report: {args.report}")
        analysis = analyze_report_file(args.report, verbose=args.verbose)
        if analysis:
            print()
            print("─" * 62)
            print("  CLAUDE'S PORTFOLIO BRIEFING")
            print("─" * 62)
            print()
            print(analysis)
            print()

    elif args.ticker:
        result = analyze_ticker(
            args.ticker.upper(), verbose=args.verbose, raw_output=args.raw,
            use_cache=use_cache, llm=args.llm,
        )
        if result:
            print_thesis(result)

    elif args.tickers:
        tickers = [t.upper() for t in args.tickers]
        print(f"  Analyzing {len(tickers)} tickers...")
        results = analyze_tickers(tickers, verbose=args.verbose, raw_output=args.raw,
                                  use_cache=use_cache)
        print_full_report(results)

    elif args.tier1_only:
        tickers = _read_watchlist_tickers(tier_filter=["TIER 1"])
        if not tickers:
            print("  No TIER 1 tickers found in watchlist.txt")
            sys.exit(1)
        print(f"  Analyzing {len(tickers)} TIER 1 tickers: {', '.join(tickers)}")
        results = analyze_tickers(tickers, verbose=args.verbose, raw_output=args.raw,
                                  use_cache=use_cache)
        print_full_report(results)

    elif args.watchlist:
        tickers = _read_watchlist_tickers(tier_filter=["TIER 1", "TIER 2"])
        if not tickers:
            print("  No TIER 1/TIER 2 tickers found in watchlist.txt")
            sys.exit(1)
        print(f"  Analyzing {len(tickers)} watchlist tickers: {', '.join(tickers)}")
        results = analyze_tickers(tickers, verbose=args.verbose, raw_output=args.raw,
                                  use_cache=use_cache)
        print_full_report(results)

    else:
        parser.print_help()
        print()
        print("  Examples:")
        print("    python3 ai_quant.py --ticker COIN")
        print("    python3 ai_quant.py --tickers COIN GME NVDA --verbose")
        print("    python3 ai_quant.py --top-n 10                    # top 10 watchlist tickers by priority score")
        print("    python3 ai_quant.py --top-n 10 --dry-run          # preview selection table, no Claude calls")
        print("    python3 ai_quant.py --top-n 5 --min-agreement 0.7 # stricter agreement filter")
        print("    python3 ai_quant.py --tickers AAPL,MSFT --top-n 2 # force specific tickers")
        print("    python3 ai_quant.py --no-limit                    # all non-skipped tickers (high cost)")
        print("    python3 ai_quant.py --screen                      # scan full universe, no Claude cost")
        print("    python3 ai_quant.py --screen --universe large      # large-cap only")
        print("    python3 ai_quant.py --screen --top 5              # screen then Claude on top 5")
        print("    python3 ai_quant.py --screen --top 5 --update-watchlist")
        print("    python3 ai_quant.py --watchlist")
        print("    python3 ai_quant.py --report signal_reports/signal_report_20260318.txt")
        print("    python3 ai_quant.py --ticker COIN --no-cache      # force fresh analysis")
        print("    python3 ai_quant.py --cache-show                  # view cached results")
        print()


if __name__ == "__main__":
    main()
