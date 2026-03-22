#!/usr/bin/env python3
"""
================================================================================
AI QUANT ANALYST v1.0 — Claude-Powered Signal Synthesis
================================================================================
Uses claude-opus-4-6 with adaptive thinking to analyze aggregated signals for
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
    pip install anthropic
    export ANTHROPIC_API_KEY="your-key"

NOTE: Costs ~$0.02-0.04 per ticker with adaptive thinking on Opus 4.6.
      Watchlist of 10 tickers + portfolio briefing ≈ $0.40-0.50 per run.

IMPORTANT: This is NOT investment advice. Claude is analyzing the same
           signals you have — it doesn't have secret alpha. Use as a
           structured second opinion, not gospel.
================================================================================
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
import warnings
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

from utils.db import get_connection

warnings.filterwarnings("ignore")

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic package not installed.")
    print("       Run: pip install anthropic")
    sys.exit(1)

try:
    from config import OUTPUT_DIR, PORTFOLIO_NAV, CRYPTO_ALLOCATION, EQUITY_ALLOCATION
except ImportError:
    OUTPUT_DIR = "./signals_output"
    PORTFOLIO_NAV = 50_000
    CRYPTO_ALLOCATION = 0.25
    EQUITY_ALLOCATION = 0.65

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


# ==============================================================================
# SECTION 0: RESULT CACHE (SQLite)
# ==============================================================================

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_quant_cache.db")


def _init_db() -> sqlite3.Connection:
    """Open (and if needed, create) the cache database."""
    conn = get_connection(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS thesis_cache (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker        TEXT    NOT NULL,
            date          TEXT    NOT NULL,
            direction     TEXT,
            conviction    INTEGER,
            time_horizon  TEXT,
            entry_low     REAL,
            entry_high    REAL,
            stop_loss     REAL,
            target_1      REAL,
            target_2      REAL,
            position_size_pct REAL,
            thesis        TEXT,
            data_quality  TEXT,
            notes         TEXT,
            catalysts_json TEXT,
            risks_json    TEXT,
            raw_response  TEXT,
            signals_json  TEXT,
            created_at    TEXT,
            bull_probability       REAL,
            bear_probability       REAL,
            neutral_probability    REAL,
            signal_agreement_score REAL,
            key_invalidation       TEXT,
            primary_scenario       TEXT,
            bear_scenario          TEXT,
            UNIQUE(ticker, date)
        )
    """)
    # Migrate existing databases that pre-date the probabilistic schema
    _new_columns = [
        ("bull_probability",       "REAL"),
        ("bear_probability",       "REAL"),
        ("neutral_probability",    "REAL"),
        ("signal_agreement_score", "REAL"),
        ("key_invalidation",       "TEXT"),
        ("primary_scenario",       "TEXT"),
        ("bear_scenario",          "TEXT"),
    ]
    for col, coltype in _new_columns:
        try:
            conn.execute(f"ALTER TABLE thesis_cache ADD COLUMN {col} {coltype}")
        except Exception:
            pass  # Column already exists — safe to ignore
    conn.commit()
    return conn


def get_cached_thesis(ticker: str, date: str = None) -> Optional[dict]:
    """
    Return today's cached thesis for ticker, or None if not found.
    date defaults to today (YYYY-MM-DD).
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    try:
        conn = _init_db()
        row = conn.execute(
            "SELECT * FROM thesis_cache WHERE ticker=? AND date=?",
            (ticker.upper(), date),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        cols = [
            "id", "ticker", "date", "direction", "conviction", "time_horizon",
            "entry_low", "entry_high", "stop_loss", "target_1", "target_2",
            "position_size_pct", "thesis", "data_quality", "notes",
            "catalysts_json", "risks_json", "raw_response", "signals_json", "created_at",
            "bull_probability", "bear_probability", "neutral_probability",
            "signal_agreement_score", "key_invalidation", "primary_scenario", "bear_scenario",
        ]
        d = dict(zip(cols, row))
        # Expand JSON fields back to lists/dicts
        d["catalysts"]     = json.loads(d.pop("catalysts_json") or "[]")
        d["risks"]         = json.loads(d.pop("risks_json")     or "[]")
        d["raw_response"]  = d.get("raw_response", "")
        d["signals"]       = json.loads(d.pop("signals_json")   or "{}")
        return d
    except Exception:
        return None


def save_thesis(thesis: dict) -> None:
    """Upsert a thesis result into the cache for today."""
    try:
        date = datetime.now().strftime("%Y-%m-%d")
        conn = _init_db()
        conn.execute("""
            INSERT INTO thesis_cache
                (ticker, date, direction, conviction, time_horizon,
                 entry_low, entry_high, stop_loss, target_1, target_2,
                 position_size_pct, thesis, data_quality, notes,
                 catalysts_json, risks_json, raw_response, signals_json, created_at,
                 bull_probability, bear_probability, neutral_probability,
                 signal_agreement_score, key_invalidation, primary_scenario, bear_scenario)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                bear_scenario=excluded.bear_scenario
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
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [cache] WARNING: Could not save to cache: {e}")


def print_cache_table(days: int = 7) -> None:
    """Print cached theses from the last N days."""
    try:
        conn = _init_db()
        rows = conn.execute("""
            SELECT ticker, date, direction, conviction, time_horizon,
                   entry_low, target_1, stop_loss, thesis
            FROM thesis_cache
            ORDER BY date DESC, conviction DESC
            LIMIT 200
        """).fetchall()
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
    for date, ticker, direction, conviction, horizon, entry, target, stop, thesis_text in [
        (r[1], r[0], r[2], r[3], r[4], r[5], r[6], r[7], r[8]) for r in rows
    ]:
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


def _collect_cross_asset_signals(ticker: str) -> dict:
    """Cross-asset divergence signal (Bottom/Top Finder logic)."""
    try:
        from cross_asset_divergence import get_cross_asset_signal
        return get_cross_asset_signal(ticker)
    except Exception:
        return {}


def _collect_max_pain_signals(ticker: str) -> dict:
    """Max pain — options expiration price gravity target."""
    try:
        from max_pain import get_max_pain
        return get_max_pain(ticker)
    except Exception:
        return {}


def _collect_options_signals(ticker: str) -> dict:
    """Pull options flow data from options_flow module."""
    try:
        from options_flow import get_options_heat
        return get_options_heat(ticker)
    except Exception:
        return {}


def _collect_congress_signals(ticker: str) -> dict:
    """Pull congressional trade signal."""
    try:
        from congress_trades import score_congress_signal, get_all_trades
        trades = get_all_trades()
        result = score_congress_signal(ticker, trades)
        if not result:
            return {}
        return {
            "congress_score": result.get("score", 0),
            "congress_direction": result.get("direction", "neutral"),
            "congress_trade_count": result.get("trade_count", 0),
            "congress_notable_traders": result.get("notable_traders", []),
            "congress_recent_trades": result.get("recent_trades", [])[:3],
        }
    except Exception:
        return {}


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

    # 1. signal_engine composite_z
    comp_z = (signals_dict.get("signal_engine") or {}).get("composite_z")
    if comp_z is not None:
        if comp_z > 0.5:
            votes.append("BULL")
        elif comp_z < -0.5:
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

    # 4. cross_asset_divergence signal  (BOTTOM → BULL, TOP → BEAR)
    cadiv_signal = str((signals_dict.get("cross_asset") or {}).get("signal") or "")
    upper_sig = cadiv_signal.upper()
    if "BOTTOM" in upper_sig:
        votes.append("BULL")
    elif "TOP" in upper_sig:
        votes.append("BEAR")

    # 5. fundamental_analysis composite score
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
        print(f"  [{ticker}]   → cross-asset divergence...", end=" ", flush=True)
    cadiv = _collect_cross_asset_signals(ticker)
    signals["cross_asset"] = cadiv
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
    mp = _collect_max_pain_signals(ticker)
    signals["max_pain"] = mp
    if verbose:
        print("done")

    if verbose:
        print(f"  [{ticker}]   → congress...", end=" ", flush=True)
    cong = _collect_congress_signals(ticker)
    signals["congress"] = cong
    if verbose:
        print("done")

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

            resolved_all[ticker] = resolved
            direction = resolved.get("pre_resolved_direction", "NEUTRAL")
            skip      = resolved.get("skip_claude", False)
            agreement = resolved.get("signal_agreement_score", 0.0)
            print(
                f"{direction:<8} agreement={agreement:.0%}"
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
    Reads open positions dynamically from trade_journal.db at runtime.
    Falls back to config.AI_QUANT_ALWAYS_INCLUDE if the DB is unavailable
    or returns an empty list.

    This means newly opened positions are automatically included in the
    top-10 AI synthesis without any manual config.py edits.
    Closed positions are automatically excluded the next run after closing.
    """
    try:
        from trade_journal import get_open_positions
        positions = get_open_positions()
        tickers = list(dict.fromkeys(
            p["ticker"] for p in positions if p.get("ticker")
        ))
        if tickers:
            logger.info(f"_get_open_positions: live from DB → {tickers}")
            return tickers
        # DB returned empty — fall back rather than passing empty always_include
        raise ValueError("No open positions returned from trade_journal.db")
    except Exception as e:
        logger.warning(f"_get_open_positions fallback to config: {e}")
        from config import AI_QUANT_ALWAYS_INCLUDE
        return list(AI_QUANT_ALWAYS_INCLUDE)


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
            print("  No TIER 1/TIER 2 tickers found in watchlist.txt")
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
            print("  No TIER 1/TIER 2 tickers found in watchlist.txt")
            sys.exit(1)
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
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  ERROR: ANTHROPIC_API_KEY not set.")
        print("  Set it with: export ANTHROPIC_API_KEY='your-key'")
        sys.exit(1)

    # ── Run Claude on selected tickers ────────────────────────────────────────
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
            ticker, verbose=args.verbose, raw_output=args.raw, use_cache=False
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
    }


SYSTEM_PROMPT = """You are a senior quant analyst at a top-tier hedge fund.
You think rigorously, quantitatively, and concisely. You have expertise in:
- Technical analysis and momentum factors
- Options market microstructure and flow interpretation
- Fundamental valuation across sectors
- Event-driven catalysts (earnings, regulatory, insider activity)
- Risk management and position sizing

Your task: analyze a structured signal packet for a stock and produce
a precise, actionable quant thesis. Be specific about price levels.
Always flag if signal data is thin or contradictory.
Do not hedge everything — give a clear directional view with conviction.

Output MUST be in JSON format with this exact structure:
{
  "ticker": "...",
  "direction": "BULL | BEAR | NEUTRAL",
  "bull_probability": 0.0-1.0,
  "bear_probability": 0.0-1.0,
  "neutral_probability": 0.0-1.0,
  "conviction": 1-5,
  "time_horizon": "days | weeks | months",
  "primary_scenario": "one sentence describing the bull/bear case",
  "bear_scenario": "one sentence describing the opposing scenario",
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
  "thesis": "2-3 sentence narrative",
  "data_quality": "HIGH|MEDIUM|LOW",
  "notes": "any caveats or data gaps"
}

IMPORTANT: bull_probability + bear_probability + neutral_probability MUST sum to exactly 1.0."""


def _build_prompt(signals: dict) -> str:
    """Build the analysis prompt from collected signals."""
    ticker            = signals["ticker"]
    agreement_score   = signals.get("signal_agreement_score")
    wr       = signals.get("weekly_regime", {})
    tech     = signals.get("technical", {})
    vp       = signals.get("volume_profile", {})
    cadiv    = signals.get("cross_asset", {})
    fund     = signals.get("fundamentals", {})
    opts     = signals.get("options_flow", {})
    mp       = signals.get("max_pain", {})
    cong     = signals.get("congress", {})
    poly     = signals.get("polymarket", {})
    sec      = signals.get("sec", {})
    catalyst = signals.get("catalyst", {})
    mr       = signals.get("market_regime", {})
    sr       = signals.get("ticker_sector_regime")
    sector   = signals.get("ticker_sector")

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

    prompt_parts += ["", "## CROSS-ASSET DIVERGENCE (vs RSP / HYG / DXY)"]
    if cadiv:
        prompt_parts += [
            f"Signal:         {cadiv.get('signal', 'N/A')}",
            f"Bottom line:    {cadiv.get('bot_line', 'N/A')} (MA={cadiv.get('bot_line_ma', 'N/A')}, spike={cadiv.get('bot_diff', 'N/A')}x) | trigger={cadiv.get('bot_trigger', False)}",
            f"Top line:       {cadiv.get('top_line', 'N/A')} (MA={cadiv.get('top_line_ma', 'N/A')}, spike={cadiv.get('top_diff', 'N/A')}x) | trigger={cadiv.get('top_trigger', False)}",
            f"Interpretation: {cadiv.get('interpretation', 'N/A')}",
        ]
    else:
        prompt_parts.append("Cross-asset divergence: unavailable")

    prompt_parts += ["", "## OPTIONS FLOW"]
    if opts:
        prompt_parts += [
            f"Heat score: {opts.get('heat_score', 'N/A')}/100",
            f"Options direction: {opts.get('direction', 'N/A')}",
            f"Expected move ({opts.get('days_to_exp', '?')}d): {opts.get('expected_move_pct', 'N/A')}%",
            f"Implied vol: {opts.get('implied_vol_pct', 'N/A')}%",
            f"IV rank: {opts.get('iv_rank', 'N/A')}%",
            f"Put/call ratio: {opts.get('pc_ratio', 'N/A')}",
            f"Total options volume: {opts.get('total_options_vol', 'N/A'):,}" if isinstance(opts.get('total_options_vol'), int) else f"Total options volume: {opts.get('total_options_vol', 'N/A')}",
            f"Straddle cost: ${opts.get('straddle_cost', 'N/A')}",
        ]
    else:
        prompt_parts.append("Options data: unavailable (possibly crypto or thin options)")

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

    prompt_parts += ["", "## MAX PAIN (Options Expiration Price Target)"]
    if mp:
        exps = mp.get("all_expirations", [])
        prompt_parts += [
            f"Current price: ${mp.get('current_price', 'N/A')}",
            f"Nearest expiry ({mp.get('nearest_expiry', 'N/A')}, {mp.get('nearest_days_to_expiry', '?')}d away):",
            f"  Max pain: ${mp.get('nearest_max_pain', 'N/A')}  |  "
            f"Distance: {mp.get('nearest_distance_pct', 'N/A'):+.2f}%  |  "
            f"Direction: {mp.get('nearest_direction', 'N/A')}  |  "
            f"Strength: {mp.get('nearest_signal_strength', 'N/A')}"
            if isinstance(mp.get('nearest_distance_pct'), (int, float)) else
            f"  Max pain: ${mp.get('nearest_max_pain', 'N/A')}",
            f"  Pin zone: ${mp.get('pin_zone_low', 'N/A')} — ${mp.get('pin_zone_high', 'N/A')}  "
            f"(OI: {mp.get('nearest_total_oi', 0):,})",
        ]
        if len(exps) > 1:
            prompt_parts.append("Upcoming expirations:")
            for e in exps[1:]:
                prompt_parts.append(
                    f"  {e['expiry']} ({e['days_to_expiry']}d): "
                    f"max pain ${e['max_pain']}  {e['distance_pct']:+.2f}%  "
                    f"{e['direction']}  OI:{e['total_oi']:,}"
                )
        prompt_parts.append(f"Interpretation: {mp.get('interpretation', '')}")
    else:
        prompt_parts.append("Max pain: unavailable (no listed options or data failure)")

    prompt_parts += ["", "## CONGRESSIONAL TRADES"]
    if cong:
        traders = cong.get("congress_notable_traders", [])
        trades = cong.get("congress_recent_trades", [])
        prompt_parts += [
            f"Congress signal score: {cong.get('congress_score', 'N/A')}/100",
            f"Direction: {cong.get('congress_direction', 'N/A')}",
            f"Trade count (recent): {cong.get('congress_trade_count', 'N/A')}",
            f"Notable traders: {', '.join(traders) if traders else 'None'}",
        ]
        if trades:
            prompt_parts.append("Recent trades:")
            for trade in trades[:3]:
                if isinstance(trade, dict):
                    prompt_parts.append(
                        f"  - {trade.get('politician', '?')}: {trade.get('type', '?')} "
                        f"${trade.get('amount', '?')} on {trade.get('date', '?')}"
                    )
    else:
        prompt_parts.append("Congressional trade data: none / unavailable")

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

    prompt_parts += ["", "## POLYMARKET PREDICTION MARKETS"]
    if poly:
        prompt_parts += [
            f"Polymarket score: {poly.get('polymarket_score', 'N/A')}/5",
            f"Direction: {poly.get('polymarket_direction', 'N/A')}",
            f"Market: \"{poly.get('polymarket_market', 'N/A')}\"",
            f"Probability: {poly.get('polymarket_probability', 'N/A')}",
            f"24h volume: ${poly.get('polymarket_volume_24h', 'N/A'):,}" if isinstance(poly.get('polymarket_volume_24h'), (int, float)) else f"24h volume: {poly.get('polymarket_volume_24h', 'N/A')}",
        ]
    else:
        prompt_parts.append("Polymarket data: no relevant markets found")

    dp = signals.get("dark_pool_flow") or {}
    prompt_parts += ["", "## DARK POOL FLOW (FINRA ATS — Institutional Routing)"]
    if dp and dp.get("signal"):
        prompt_parts += [
            f"Signal: {dp.get('signal', 'N/A')}  (score {dp.get('dark_pool_score', 'N/A')}/100, 50=neutral)",
            f"Short ratio today   : {dp.get('short_ratio_today', 0):.3%}"
            if isinstance(dp.get('short_ratio_today'), float) else
            f"Short ratio today   : {dp.get('short_ratio_today', 'N/A')}",
            f"Short ratio mean    : {dp.get('short_ratio_mean', 0):.3%}"
            if isinstance(dp.get('short_ratio_mean'), float) else
            f"Short ratio mean    : {dp.get('short_ratio_mean', 'N/A')}",
            f"Short ratio trend   : {dp.get('short_ratio_trend', 0):+.5f}/day"
            if isinstance(dp.get('short_ratio_trend'), float) else
            f"Short ratio trend   : {dp.get('short_ratio_trend', 'N/A')}",
            f"Short ratio z-score : {dp.get('short_ratio_zscore', 0):+.2f}"
            if isinstance(dp.get('short_ratio_zscore'), float) else
            f"Short ratio z-score : {dp.get('short_ratio_zscore', 'N/A')}",
            f"Dark pool intensity : {dp.get('dark_pool_intensity', 0):.1%}"
            if isinstance(dp.get('dark_pool_intensity'), float) else
            f"Dark pool intensity : {dp.get('dark_pool_intensity', 'N/A')}",
            f"Days of FINRA data  : {dp.get('days_of_data', 'N/A')}",
            f"Interpretation      : {dp.get('interpretation', '')}",
        ]
    else:
        prompt_parts.append("Dark pool data: unavailable (FINRA file not yet published or ticker not found)")

    # Portfolio context
    prompt_parts += [
        "",
        "## PORTFOLIO CONTEXT",
        f"Portfolio NAV: ${PORTFOLIO_NAV:,}",
        f"Equity allocation: {EQUITY_ALLOCATION*100:.0f}% | Crypto allocation: {CRYPTO_ALLOCATION*100:.0f}%",
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


def _call_claude(prompt: str, verbose: bool = False) -> Optional[str]:
    """
    Call claude-opus-4-6 with adaptive thinking and streaming.
    Returns the response text, or None on failure.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ERROR: ANTHROPIC_API_KEY environment variable not set.")
        print("         export ANTHROPIC_API_KEY='your-key-here'")
        return None

    client = anthropic.Anthropic(api_key=api_key)

    if verbose:
        print("  Calling Claude API (opus-4-6 + adaptive thinking)...", flush=True)

    full_text = ""
    thinking_shown = False

    try:
        # Use streaming to handle long responses and avoid timeouts
        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for event in stream:
                if event.type == "content_block_start":
                    if hasattr(event, "content_block"):
                        if event.content_block.type == "thinking" and verbose and not thinking_shown:
                            print("  [thinking...]", flush=True)
                            thinking_shown = True
                elif event.type == "content_block_delta":
                    if hasattr(event, "delta"):
                        if event.delta.type == "text_delta":
                            full_text += event.delta.text

        return full_text.strip() if full_text else None

    except anthropic.AuthenticationError:
        print("  ERROR: Invalid ANTHROPIC_API_KEY.")
        return None
    except anthropic.RateLimitError:
        print("  ERROR: Claude API rate limit hit. Wait and retry.")
        return None
    except anthropic.APIStatusError as e:
        print(f"  ERROR: Claude API error {e.status_code}: {e.message}")
        return None
    except Exception as e:
        print(f"  ERROR: Unexpected error calling Claude: {e}")
        return None


def _parse_response(raw: str) -> Optional[dict]:
    """Extract JSON from Claude's response."""
    if not raw:
        return None

    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON block
    import re
    patterns = [
        r"```json\s*([\s\S]+?)\s*```",
        r"```\s*([\s\S]+?)\s*```",
        r"(\{[\s\S]+\})",
    ]
    for pattern in patterns:
        m = re.search(pattern, raw)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue

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
    lines = [
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
    ] + tier3 + [""],

    with open(watchlist_path, "w") as f:
        f.write("\n".join(lines[0]))  # lines is a tuple due to trailing comma

    dropped = len(results) - len(tier1) - len(tier2) - len(tier3)
    print(f"  Watchlist updated: {len(tier1)} TIER 1 | {len(tier2)} TIER 2 | {len(tier3)} TIER 3 | {dropped} dropped")
    print(f"  Written to: {watchlist_path}")


# ==============================================================================
# SECTION 5: ANALYSIS PIPELINE
# ==============================================================================

def analyze_ticker(ticker: str, verbose: bool = False, raw_output: bool = False,
                   use_cache: bool = True) -> Optional[dict]:
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
            return cached

    print(f"\n  Analyzing {ticker}...")

    # Collect signals
    signals = collect_all_signals(ticker, verbose=verbose)

    # Pre-compute agreement score (kept for backward compat; may be overridden by resolver)
    signals["signal_agreement_score"] = compute_signal_agreement(signals)

    # ── Conflict resolution — pre-resolve before Claude ──────────────────────
    if _RESOLVER_AVAILABLE:
        try:
            mr_dict    = signals.get("market_regime") or {}
            regime_str = mr_dict.get("regime", "TRANSITIONAL") if mr_dict else "TRANSITIONAL"
            resolved   = _cr.resolve(signals, regime_str)
            signals["conflict_resolution"] = resolved
            # Resolver's agreement score uses MODULE_WEIGHTS; prefer it over simple vote
            signals["signal_agreement_score"] = resolved["signal_agreement_score"]

            if resolved["skip_claude"]:
                flags = resolved.get("override_flags", [])
                flag0 = flags[0] if flags else "pre-resolved block"
                print(f"  [{ticker}] Claude skipped — {flag0}")
                thesis = _make_neutral_thesis(ticker, signals, resolved)
                save_thesis(thesis)
                return thesis
        except Exception as exc:
            logger.warning("Conflict resolver failed for %s: %s", ticker, exc)

    # Build prompt (includes conflict_resolution context if available)
    prompt = _build_prompt(signals)

    if verbose and raw_output:
        print("\n--- PROMPT ---")
        print(prompt)
        print("--- END PROMPT ---\n")

    # Call Claude
    raw = _call_claude(prompt, verbose=verbose)
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

    # --- Save to cache ---
    save_thesis(thesis)

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
    print("  AI QUANT ANALYSIS — POWERED BY CLAUDE OPUS 4.6")
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
    Send an existing signal report file to Claude for portfolio-level analysis.
    Useful for analyzing the output of run_master.sh.
    """
    if not os.path.exists(report_path):
        print(f"  ERROR: Report file not found: {report_path}")
        return None

    with open(report_path) as f:
        content = f.read()

    # Truncate if very long (keep first 80k chars to stay within context)
    max_chars = 80_000
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n[... report truncated at {max_chars} chars ...]"

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ERROR: ANTHROPIC_API_KEY not set.")
        return None

    client = anthropic.Anthropic(api_key=api_key)

    system = """You are a senior portfolio manager and quant analyst at a hedge fund.
Analyze the provided weekly signal report and give a structured portfolio briefing:

1. TOP 3 HIGHEST CONVICTION IDEAS — with thesis, entry, stop, target, sizing
2. KEY RISKS THIS WEEK — macro, sector, position-specific
3. PORTFOLIO POSITIONING — recommended adjustments
4. WATCHLIST PRIORITIES — which tickers deserve immediate deep dive
5. SIGNALS TO IGNORE — what's noise in this report

Be direct, specific, and quantitative. Use actual price levels from the data."""

    prompt = f"""Analyze this weekly signal report for my portfolio:\n\n{content}"""

    print(f"  Sending report to Claude ({len(content):,} chars)...")

    full_response = ""
    try:
        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for event in stream:
                if event.type == "content_block_delta":
                    if hasattr(event, "delta") and event.delta.type == "text_delta":
                        full_response += event.delta.text
                        if verbose:
                            print(event.delta.text, end="", flush=True)

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
        "--dry-run", action="store_true",
        help="Print selection table and cost estimate without calling Claude",
    )
    args = parser.parse_args()
    use_cache = not args.no_cache

    # --cache-show: print cache table and exit
    if args.cache_show:
        print()
        print("================================================================")
        print("  AI QUANT ANALYST — POWERED BY CLAUDE OPUS 4.6")
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
            if not os.environ.get("ANTHROPIC_API_KEY"):
                print("  ERROR: ANTHROPIC_API_KEY not set — cannot run Claude analysis.")
                print("  Set it with: export ANTHROPIC_API_KEY='your-key'")
                sys.exit(1)

            print(f"  Running Claude analysis on top {len(top_candidates)}: "
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

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  ERROR: ANTHROPIC_API_KEY not set.")
        print("  Set it with: export ANTHROPIC_API_KEY='your-key'")
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
            use_cache=use_cache,
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
