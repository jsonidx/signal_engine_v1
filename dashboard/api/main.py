#!/usr/bin/env python3
"""
================================================================================
SIGNAL ENGINE API v2.0 — FastAPI Backend
================================================================================
Serves all signal_engine output data to the React dashboard as JSON.
Reads from SQLite databases and CSV files in the parent project directory.

ENDPOINTS:
  Portfolio  /api/portfolio/{summary,history,positions}
  Signals    /api/signals/{latest,heatmap,ticker/{t},dates}
  Screeners  /api/screeners/{squeeze,catalysts,options}
  Regime     /api/regime/{current,sectors}
  Dark Pool  /api/darkpool/{top,ticker/{t}}
  Resolution /api/resolution/{log,stats}
  Backtest   /api/backtest/results
  Universe   /api/universe/stats

USAGE:
  cd dashboard/api && uvicorn main:app --reload --host 0.0.0.0 --port 8000
  Or: bash run.sh
================================================================================
"""

import csv
import glob
import json
import logging
import math
import os
import sqlite3
import sys
import time
import warnings
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ─── Resolve project root (two levels up from this file) ─────────────────────
BASE_DIR = Path(__file__).resolve().parents[2]

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger("signal_api")

# ─── Add project root to sys.path so config.py is importable ─────────────────
sys.path.insert(0, str(BASE_DIR))

try:
    from config import (
        PORTFOLIO_NAV,
        EQUITY_ALLOCATION,
        CRYPTO_ALLOCATION,
    )
except ImportError:
    PORTFOLIO_NAV = 50_000
    EQUITY_ALLOCATION = 0.65
    CRYPTO_ALLOCATION = 0.25

# ─── Paths ────────────────────────────────────────────────────────────────────
SIGNALS_DIR      = BASE_DIR / "signals_output"
DATA_DIR         = BASE_DIR / "data"
LOGS_DIR         = BASE_DIR / "logs"
PAPER_TRADES_DB  = BASE_DIR / "paper_trades.db"
TRADE_JOURNAL_DB = BASE_DIR / "trade_journal.db"
AI_QUANT_DB      = BASE_DIR / "ai_quant_cache.db"
REGIME_CACHE     = DATA_DIR / "regime_cache.json"
SECTOR_CACHE     = DATA_DIR / "sector_cache.json"

# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Signal Engine API",
    version="2.0",
    description="JSON API for the Signal Engine quantitative trading system",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# SECTION 1: IN-MEMORY TTL CACHE
# ==============================================================================

class _CacheEntry:
    __slots__ = ("value", "expires_at")
    def __init__(self, value: Any, ttl: int):
        self.value = value
        self.expires_at = time.monotonic() + ttl


class DataCache:
    """Simple thread-safe (GIL) in-memory TTL cache.

    Usage:
        cache = DataCache()
        value = cache.get("key")          # None if missing/expired
        cache.set("key", value, ttl=900)  # 900 s = 15 min
    """

    def __init__(self):
        self._store: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl: int = 900) -> None:
        self._store[key] = _CacheEntry(value, ttl)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)


_cache = DataCache()

TTL_SHORT  = 300   # 5 min — static outputs (CSVs, ai_quant db)
TTL_MEDIUM = 900   # 15 min — live prices, portfolio positions
TTL_LONG   = 3600  # 1 hr  — regime, backtest, universe stats


def cached(key_fn, ttl: int = TTL_MEDIUM):
    """Decorator that caches an async endpoint's result."""
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs)
            hit = _cache.get(key)
            if hit is not None:
                return hit
            result = await fn(*args, **kwargs)
            _cache.set(key, result, ttl)
            return result
        return wrapper
    return decorator


# ==============================================================================
# SECTION 2: SHARED HELPERS
# ==============================================================================

def _db_connect(path: Path) -> Optional[sqlite3.Connection]:
    """Return a read-only SQLite connection, or None if file missing."""
    if not path.exists():
        log.warning("DB not found: %s", path)
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _no_data(reason: str = "data not available") -> dict:
    """Standard envelope for endpoints with missing data."""
    return {"data_available": False, "reason": reason, "data": []}


def get_latest_signals_file(date: str = None) -> Optional[Path]:
    """Return the most recent equity_signals_*.csv, optionally filtered by date."""
    pattern = str(SIGNALS_DIR / "equity_signals_*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        return None
    if date:
        target = SIGNALS_DIR / f"equity_signals_{date}.csv"
        return target if target.exists() else None
    return Path(files[-1])


def get_signals_date_list() -> list[str]:
    """Return all available signal dates (YYYYMMDD) sorted descending."""
    pattern = str(SIGNALS_DIR / "equity_signals_*.csv")
    dates = []
    for f in glob.glob(pattern):
        stem = Path(f).stem  # equity_signals_YYYYMMDD
        parts = stem.split("_")
        if len(parts) >= 3:
            dates.append(parts[-1])
    return sorted(dates, reverse=True)


def _safe_float(v, default=None):
    try:
        return float(v) if v is not None and str(v).strip() not in ("", "nan", "None") else default
    except (TypeError, ValueError):
        return default


def _safe_int(v, default=None):
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _fetch_current_prices(tickers: list[str], cache_ttl: int = TTL_MEDIUM) -> dict[str, float]:
    """Fetch latest prices via yfinance with in-memory caching."""
    if not tickers:
        return {}
    key = "prices:" + ",".join(sorted(tickers))
    cached_prices = _cache.get(key)
    if cached_prices:
        return cached_prices

    prices: dict[str, float] = {}
    try:
        data = yf.download(tickers, period="5d", auto_adjust=True, progress=False, threads=True)
        if isinstance(data.columns, pd.MultiIndex):
            close = data["Close"]
        else:
            close = data[["Close"]].rename(columns={"Close": tickers[0]})
        for t in tickers:
            if t in close.columns:
                series = close[t].dropna()
                if not series.empty:
                    prices[t] = float(series.iloc[-1])
    except Exception as e:
        log.warning("yfinance fetch error: %s", e)

    _cache.set(key, prices, cache_ttl)
    return prices


def _normalize_score(value: float, min_val: float, max_val: float,
                     invert: bool = False) -> float:
    """Map [min_val, max_val] → [-1, +1]. Returns 0 on bad input."""
    try:
        if max_val == min_val:
            return 0.0
        norm = 2 * (value - min_val) / (max_val - min_val) - 1
        norm = max(-1.0, min(1.0, norm))
        return -norm if invert else norm
    except Exception:
        return 0.0


def _cross_asset_signal_to_score(signal: Optional[str]) -> Optional[float]:
    """Convert cross_asset string signal to [-1, 1] score."""
    if not signal:
        return None
    s = str(signal).upper()
    if "BOTTOM" in s or "OVERSOLD" in s or "BULLISH" in s:
        return 0.8
    if "TOP" in s or "OVERBOUGHT" in s or "BEARISH" in s:
        return -0.8
    return 0.0


def _equity_signals_composite_z(ticker: str) -> Optional[float]:
    """Look up composite_z for ticker from the latest equity_signals CSV."""
    try:
        sig_file = get_latest_signals_file()
        if not sig_file:
            return None
        df = pd.read_csv(sig_file)
        if "ticker" not in df.columns:
            df = df.reset_index()
            df.columns = ["ticker"] + list(df.columns[1:])
        row = df[df["ticker"].str.upper() == ticker.upper()]
        if row.empty:
            return None
        return _safe_float(row.iloc[0].get("composite_z"))
    except Exception:
        return None


def _normalise_dp_trend(raw) -> Optional[str]:
    """Convert dark_pool short_ratio_trend to 'up'/'down'/'flat' string."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    try:
        val = float(raw)
        if val > 0.001:
            return "up"
        if val < -0.001:
            return "down"
        return "flat"
    except Exception:
        return None


def _normalise_dp_intensity(raw) -> Optional[float]:
    """Normalise dark_pool_intensity to a 0–100 percentage.
    dark_pool_flow stores it as a ratio (0.43 = 43%); convert if < 1."""
    v = _safe_float(raw)
    if v is None:
        return None
    return round(v * 100, 1) if v <= 1.0 else round(v, 1)


def _json_safe(obj: Any) -> Any:
    """Recursively replace float NaN/Inf with None for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


# ==============================================================================
# SECTION 3: STARTUP
# ==============================================================================

@app.on_event("startup")
async def startup_event():
    """Log warnings for expected data files that are missing."""
    expected = {
        "paper_trades.db":   PAPER_TRADES_DB,
        "trade_journal.db":  TRADE_JOURNAL_DB,
        "ai_quant_cache.db": AI_QUANT_DB,
        "regime_cache.json": REGIME_CACHE,
        "signals_output/":   SIGNALS_DIR,
    }
    for name, path in expected.items():
        if not Path(path).exists():
            log.warning("MISSING: %s → %s", name, path)
        else:
            log.info("OK: %s", name)
    log.info("Signal Engine API v2.0 ready on http://0.0.0.0:8000")


# ==============================================================================
# SECTION 4: PORTFOLIO ENDPOINTS
# ==============================================================================

@app.get("/api/portfolio/summary")
async def portfolio_summary():
    """
    Aggregate performance summary from paper_trades.db.
    Returns NAV, weekly return, SPY benchmark, Sharpe, drawdown, hit rate.
    """
    conn = _db_connect(PAPER_TRADES_DB)
    if conn is None:
        return _no_data("paper_trades.db not found")

    try:
        # Weekly returns
        df = pd.read_sql("SELECT * FROM weekly_returns ORDER BY week_ending ASC", conn)

        # Regime
        regime = "UNKNOWN"
        regime_score = 0
        if REGIME_CACHE.exists():
            with open(REGIME_CACHE) as f:
                rc = json.load(f)
            regime = rc.get("market_regime", {}).get("regime", "UNKNOWN")
            regime_score = rc.get("market_regime", {}).get("score", 0)

        # Open positions count
        open_pos = 0
        tj_conn = _db_connect(TRADE_JOURNAL_DB)
        if tj_conn:
            row = tj_conn.execute(
                "SELECT COUNT(*) as cnt FROM trades WHERE action='BUY' AND status='open'"
            ).fetchone()
            open_pos = row["cnt"] if row else 0
            tj_conn.close()

        if df.empty or len(df) < 2:
            conn.close()
            return {
                "data_available": True,
                "total_value_eur": PORTFOLIO_NAV,
                "weekly_return_pct": 0.0,
                "benchmark_return_pct": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "hit_rate_pct": 0.0,
                "open_positions": open_pos,
                "regime": regime,
                "regime_score": regime_score,
                "as_of": datetime.utcnow().isoformat() + "Z",
            }

        # Last week's returns
        last = df.iloc[-1]
        weekly_ret = float(last["portfolio_return"] or 0)
        bench_ret  = float(last["benchmark_return"] or 0)

        # Cumulative
        cum_port = (1 + df["portfolio_return"]).cumprod()
        total_ret = float(cum_port.iloc[-1] - 1)
        total_value = PORTFOLIO_NAV * (1 + total_ret)

        # Annualised Sharpe
        n_weeks = len(df)
        vol = float(df["portfolio_return"].std()) * np.sqrt(52) if n_weeks > 1 else 0
        cagr = (1 + total_ret) ** (52 / n_weeks) - 1 if n_weeks > 0 else 0
        sharpe = cagr / vol if vol > 0 else 0.0

        # Max drawdown
        cummax = cum_port.cummax()
        dd = (cum_port - cummax) / cummax
        max_dd = float(dd.min())

        # Hit rate
        hit_rate = float((df["portfolio_return"] > 0).mean() * 100)

        conn.close()
        return {
            "data_available": True,
            "total_value_eur": round(total_value, 2),
            "weekly_return_pct": round(weekly_ret * 100, 3),
            "benchmark_return_pct": round(bench_ret * 100, 3),
            "sharpe_ratio": round(sharpe, 3),
            "max_drawdown_pct": round(max_dd * 100, 3),
            "hit_rate_pct": round(hit_rate, 1),
            "open_positions": open_pos,
            "regime": regime,
            "regime_score": regime_score,
            "as_of": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as e:
        log.exception("portfolio_summary error")
        conn.close()
        return _no_data(str(e))


@app.get("/api/portfolio/history")
async def portfolio_history(weeks: int = Query(52, ge=1, le=260)):
    """Weekly portfolio vs SPY history from paper_trades.db."""
    conn = _db_connect(PAPER_TRADES_DB)
    if conn is None:
        return _no_data("paper_trades.db not found")

    try:
        df = pd.read_sql(
            "SELECT week_ending, portfolio_return, benchmark_return, snapshot_id "
            "FROM weekly_returns ORDER BY week_ending ASC",
            conn,
        )
        conn.close()

        if df.empty:
            return {"data_available": True, "data": []}

        df = df.tail(weeks).copy()

        # Cumulative PnL in EUR
        cum_port = (1 + df["portfolio_return"]).cumprod()
        df["cumulative_pnl_eur"] = (cum_port - 1) * PORTFOLIO_NAV

        # Position count per snapshot
        conn2 = _db_connect(PAPER_TRADES_DB)
        pos_counts = {}
        if conn2:
            rows = conn2.execute(
                "SELECT snapshot_id, COUNT(*) as cnt FROM equity_positions GROUP BY snapshot_id"
            ).fetchall()
            pos_counts = {r["snapshot_id"]: r["cnt"] for r in rows}
            conn2.close()

        records = []
        for _, row in df.iterrows():
            pf_ret = row["portfolio_return"]
            bm_ret = row["benchmark_return"]
            records.append({
                "week_ending": row["week_ending"],
                "portfolio_return": round(float(pf_ret) * 100 if pf_ret is not None and pf_ret == pf_ret else 0.0, 3),
                "spy_return":       round(float(bm_ret) * 100 if bm_ret is not None and bm_ret == bm_ret else 0.0, 3),
                "cumulative_pnl_eur": round(float(row["cumulative_pnl_eur"]), 2),
                "positions_count":  pos_counts.get(int(row["snapshot_id"]), 0),
            })

        return {"data_available": True, "data": records}

    except Exception as e:
        log.exception("portfolio_history error")
        return _no_data(str(e))


@app.get("/api/portfolio/positions")
async def portfolio_positions():
    """
    Open positions from trade_journal.db enriched with current prices via yfinance.
    Prices are cached 15 min.
    """
    conn = _db_connect(TRADE_JOURNAL_DB)
    if conn is None:
        return {"data_available": True, "data": []}

    try:
        rows = conn.execute("""
            SELECT ticker, date AS entry_date, price AS entry_price,
                   size_eur AS position_size_eur, shares AS quantity,
                   signal_composite AS conviction, stop_loss,
                   target_1, target_2, notes
            FROM trades
            WHERE action = 'BUY' AND status = 'open'
            ORDER BY date DESC
        """).fetchall()
        conn.close()

        if not rows:
            return {"data_available": True, "data": []}

        tickers = [r["ticker"] for r in rows]
        current_prices = _fetch_current_prices(tickers)

        today = datetime.utcnow().date()
        positions = []
        for r in rows:
            ticker     = r["ticker"]
            entry_px   = _safe_float(r["entry_price"], 0)
            cur_px     = current_prices.get(ticker, 0)
            size_eur   = _safe_float(r["position_size_eur"], 0)
            entry_date = r["entry_date"] or ""
            conviction = _safe_float(r["conviction"])

            # Days held
            try:
                d = datetime.strptime(entry_date[:10], "%Y-%m-%d").date()
                days_held = (today - d).days
            except Exception:
                days_held = 0

            # P&L
            if entry_px > 0 and cur_px > 0 and size_eur > 0:
                qty = size_eur / entry_px
                unrealized_pnl_eur = (cur_px - entry_px) * qty
                unrealized_pnl_pct = (cur_px / entry_px - 1) * 100
            else:
                unrealized_pnl_eur = 0.0
                unrealized_pnl_pct = 0.0

            positions.append({
                "ticker":              ticker,
                "entry_date":          entry_date[:10] if entry_date else None,
                "entry_price":         round(entry_px, 4),
                "current_price":       round(cur_px, 4),
                "unrealized_pnl_eur":  round(unrealized_pnl_eur, 2),
                "unrealized_pnl_pct":  round(unrealized_pnl_pct, 3),
                "position_size_eur":   round(size_eur, 2),
                "direction":           "LONG",
                "conviction":          conviction,
                "days_held":           days_held,
                "stop_loss":           _safe_float(r["stop_loss"]),
                "target_1":            _safe_float(r["target_1"]),
                "target_2":            _safe_float(r["target_2"]),
            })

        return {"data_available": True, "data": positions}

    except Exception as e:
        log.exception("portfolio_positions error")
        return _no_data(str(e))


# ==============================================================================
# SECTION 5: SIGNALS ENDPOINTS
# ==============================================================================

@app.get("/api/signals/dates")
async def signals_dates():
    """Return all available signal dates for the date picker (YYYYMMDD, descending)."""
    dates = get_signals_date_list()
    return {"data_available": bool(dates), "dates": dates}


@app.get("/api/signals/latest")
async def signals_latest(date: Optional[str] = Query(None)):
    """
    Return all rows from the most recent (or specified) equity_signals CSV.
    Columns: ticker, rank, composite_z, factor columns, weight_pct, position_eur, etc.
    """
    cache_key = f"signals_latest:{date or 'latest'}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    signals_file = get_latest_signals_file(date)
    if signals_file is None:
        result = _no_data("no equity_signals CSV found")
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    try:
        df = pd.read_csv(signals_file, index_col=0)
        df.index.name = "ticker"
        df = df.reset_index()

        # Add weight and position from matching positions file
        date_suffix = signals_file.stem.split("_")[-1]
        pos_file = SIGNALS_DIR / f"equity_positions_{date_suffix}.csv"
        pos_df = None
        if pos_file.exists():
            pos_df = pd.read_csv(pos_file, index_col=0)
            pos_df.index.name = "ticker"

        records = []
        for _, row in df.iterrows():
            ticker = str(row.get("ticker", "")).upper()
            rec = {
                "ticker":             ticker,
                "rank":               _safe_int(row.get("rank")),
                "composite_z":        _safe_float(row.get("composite_z")),
                "momentum_12_1":      _safe_float(row.get("momentum_12_1_z")),
                "momentum_6_1":       _safe_float(row.get("momentum_6_1_z")),
                "mean_reversion_5d":  _safe_float(row.get("mean_rev_5d_z")),
                "volatility_quality": _safe_float(row.get("vol_quality_z")),
                "risk_adj_momentum":  _safe_float(row.get("risk_adj_mom_z")),
                "earnings_revision":  _safe_float(row.get("earnings_revision_z")),
                "ivol":               _safe_float(row.get("ivol_z")),
                "proximity_52wk":     _safe_float(row.get("proximity_52wk_z")),
                "weight_pct":         None,
                "position_eur":       None,
                "sector":             _safe_float(row.get("sector")),
                "regime_sector":      row.get("regime_sector"),
            }
            if pos_df is not None and ticker in pos_df.index:
                p = pos_df.loc[ticker]
                rec["weight_pct"]   = _safe_float(p.get("weight_pct"))
                rec["position_eur"] = _safe_float(p.get("position_eur"))
            records.append(rec)

        file_date = date_suffix
        result = {
            "data_available": True,
            "file_date":      file_date,
            "file_path":      str(signals_file.name),
            "count":          len(records),
            "data":           records,
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("signals_latest error")
        return _no_data(str(e))


@app.get("/api/signals/heatmap")
async def signals_heatmap():
    """
    Module-level signal matrix — sourced from equity_signals CSV (all watchlist
    tickers), enriched with Claude cache, dark pool, squeeze, and fundamentals.
    Each row: ticker × {signal_engine, squeeze, options_flow, dark_pool,
                         fundamentals, social, polymarket, cross_asset}
    All scores normalised to [-1, +1].
    """
    cache_key = "signals_heatmap"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    try:
        # ── 1. Primary ticker universe: equity_signals CSV ────────────────────
        sig_file = get_latest_signals_file()
        sig_lookup: dict = {}   # ticker → row dict
        if sig_file:
            sig_df = pd.read_csv(sig_file)
            # handle both index=ticker and column=ticker
            if "ticker" not in sig_df.columns and sig_df.index.name == "ticker":
                sig_df = sig_df.reset_index()
            for _, row in sig_df.iterrows():
                t = str(row.get("ticker", "")).upper()
                if t:
                    sig_lookup[t] = row.to_dict()

        # ── 2. Watchlist tickers not in signals CSV ────────────────────────────
        wl_path = BASE_DIR / "watchlist.txt"
        if wl_path.exists():
            for line in wl_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                t = line.split("#")[0].strip().upper()
                if t and t not in sig_lookup:
                    sig_lookup[t] = {}

        if not sig_lookup:
            result = _no_data("no equity_signals CSV and no watchlist.txt found")
            _cache.set(cache_key, result, TTL_SHORT)
            return result

        # ── 3. Claude cache: per-ticker module signals_json ───────────────────
        claude_cache: dict = {}   # ticker → {direction, conviction, agreement, sigs}
        conn = _db_connect(AI_QUANT_DB)
        if conn:
            rows = conn.execute("""
                SELECT ticker, direction, conviction, signal_agreement_score, signals_json
                FROM thesis_cache ORDER BY date DESC
            """).fetchall()
            conn.close()
            for r in rows:
                t = r["ticker"]
                if t not in claude_cache:
                    sigs = json.loads(r["signals_json"]) if r["signals_json"] else {}
                    claude_cache[t] = {
                        "direction":  r["direction"],
                        "conviction": r["conviction"],
                        "agreement":  r["signal_agreement_score"],
                        "sigs":       sigs,
                    }

        # ── 4. Dark pool lookup ────────────────────────────────────────────────
        dp_data = _load_dark_pool() or {}

        # ── 5. Squeeze signals CSV ────────────────────────────────────────────
        sq_lookup: dict = {}
        sq_pattern = str(SIGNALS_DIR / "squeeze_signals_*.csv")
        sq_files = sorted(glob.glob(sq_pattern))
        if sq_files:
            sq_df = pd.read_csv(sq_files[-1])
            for _, row in sq_df.iterrows():
                t = str(row.get("ticker", "")).upper()
                if t:
                    sq_lookup[t] = row.to_dict()

        # ── 6. Fundamentals CSV ───────────────────────────────────────────────
        fu_lookup: dict = {}
        fu_pattern = str(SIGNALS_DIR / "fundamental_*.csv")
        fu_files = sorted(glob.glob(fu_pattern))
        if fu_files:
            fu_df = pd.read_csv(fu_files[-1])
            for _, row in fu_df.iterrows():
                t = str(row.get("ticker", "")).upper()
                if t:
                    fu_lookup[t] = row.to_dict()

        # ── 7. Build heatmap rows ─────────────────────────────────────────────
        heatmap = []
        for ticker, sig_row in sig_lookup.items():
            cc = claude_cache.get(ticker, {})
            sigs = cc.get("sigs", {})

            # signal_engine: composite_z → normalise [-3, +3] to [-1, +1]
            cz = _safe_float(sig_row.get("composite_z"), None)
            se_score = _normalize_score(cz, -3.0, 3.0) if cz is not None else 0.0

            # squeeze: from squeeze CSV first, then Claude cache
            sq_row = sq_lookup.get(ticker, {})
            sq_raw = _safe_float(sq_row.get("final_score"), None)
            if sq_raw is None:
                sq = sigs.get("squeeze") or sigs.get("catalyst") or {}
                sq_raw = _safe_float(sq.get("short_squeeze_score") or sq.get("final_score"), 50)
            sq_score = _normalize_score(sq_raw, 0.0, 100.0)

            # options_flow: Claude cache only
            of = sigs.get("options_flow") or {}
            of_score = _normalize_score(_safe_float(of.get("heat_score"), 50), 0.0, 100.0)

            # dark_pool
            dp_row = dp_data.get(ticker, {})
            dp_raw = _safe_float(dp_row.get("dark_pool_score") or dp_row.get("score"), 50)
            dp_score = _normalize_score(dp_raw, 0.0, 100.0) if dp_row else 0.0

            # fundamentals: from fundamentals CSV (operating_margin, earnings growth)
            fu_row = fu_lookup.get(ticker, {})
            fu_cc = sigs.get("fundamentals") or {}
            fu_pct = _safe_float(fu_cc.get("fundamental_score_pct"), None)
            if fu_pct is None:
                # derive a simple score from earnings_growth_yoy if available
                eg = _safe_float(fu_row.get("earnings_growth_yoy"), None)
                fu_pct = 50.0 + min(40.0, max(-40.0, (eg or 0) * 100)) if eg is not None else 50.0
            fu_score = _normalize_score(fu_pct, 0.0, 100.0)

            # social / polymarket / cross_asset: Claude cache only
            soc = sigs.get("social") or {}
            bull_ratio = _safe_float(soc.get("bull_ratio"), 0.5)
            soc_score = _normalize_score(bull_ratio, 0.0, 1.0)

            pm = sigs.get("polymarket") or {}
            pm_score = max(-1.0, min(1.0, _safe_float(pm.get("signal_score") or pm.get("score"), 0)))

            ca = sigs.get("cross_asset") or {}
            ca_map = {"TOP": 1.0, "BOTTOM": -1.0, "NEUTRAL": 0.0}
            ca_score = ca_map.get(str(ca.get("signal", "NEUTRAL")).upper(), 0.0)

            # pre_resolved_direction: Claude DB → else derive from composite_z
            direction = cc.get("direction") or (
                "BULL" if (cz or 0) > 0.3 else "BEAR" if (cz or 0) < -0.3 else "NEUTRAL"
            )

            sector_val = str(sig_row.get("sector") or fu_row.get("sector") or "")

            heatmap.append({
                "ticker":                  ticker,
                "sector":                  sector_val,
                "signal_engine":           round(se_score, 3),
                "squeeze":                 round(sq_score, 3),
                "options":                 round(of_score, 3),
                "dark_pool":               round(dp_score, 3),
                "fundamentals":            round(fu_score, 3),
                "social":                  round(soc_score, 3),
                "polymarket":              round(pm_score, 3),
                "cross_asset":             round(ca_score, 3),
                "pre_resolved_direction":  direction,
                "signal_agreement_score":  _safe_float(cc.get("agreement")),
            })

        # Sort by absolute signal_engine score descending
        heatmap.sort(key=lambda r: abs(r["signal_engine"]), reverse=True)

        result = {"data_available": True, "count": len(heatmap), "data": heatmap}
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("signals_heatmap error")
        return _no_data(str(e))


@app.get("/api/signals/ticker/{ticker}")
async def signals_ticker(ticker: str):
    """Full detail for one ticker: AI thesis + all module signals."""
    ticker = ticker.upper()
    cache_key = f"signals_ticker:{ticker}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    conn = _db_connect(AI_QUANT_DB)
    if conn is None:
        return _no_data("ai_quant_cache.db not found")

    try:
        row = conn.execute("""
            SELECT * FROM thesis_cache
            WHERE ticker = ?
            ORDER BY date DESC LIMIT 1
        """, (ticker,)).fetchone()
        conn.close()

        if row is None:
            return _no_data(f"no cached analysis for {ticker}")

        # row_factory=sqlite3.Row — convert to plain dict
        data = dict(row)

        # Parse JSON columns
        for col in ("catalysts_json", "risks_json", "signals_json", "expected_moves_json"):
            if data.get(col):
                try:
                    data[col] = json.loads(data[col])
                except Exception:
                    pass

        # Extract sub-signals for convenience
        sigs = data.get("signals_json") or {}
        of   = sigs.get("options_flow") or {}
        dp   = sigs.get("dark_pool_flow") or sigs.get("dark_pool") or {}
        soc  = sigs.get("social") or {}
        sq   = sigs.get("squeeze") or sigs.get("catalyst") or {}
        vp   = sigs.get("volume_profile") or {}
        mp   = sigs.get("max_pain") or {}

        # Build module scores dict expected by frontend ModuleMiniHeatmap
        # signal_engine: composite_z from equity_signals CSV (authoritative); no fallback
        # squeeze: squeeze_score_100 is 0–100 → normalise to [-1, 1] via (x-50)/50
        # fundamentals: fundamental_score_pct is 0–100 → same normalisation
        # cross_asset: string signal → numeric score
        sq_raw   = sigs.get("squeeze") or sigs.get("catalyst") or {}
        sq_score = _safe_float(sq_raw.get("squeeze_score_100") or sq_raw.get("short_squeeze_score"))
        fu_raw   = sigs.get("fundamentals") or {}
        fu_score = _safe_float(fu_raw.get("fundamental_score_pct") or fu_raw.get("composite_score"))
        pm_raw   = sigs.get("polymarket")
        pm_score = _safe_float(
            (pm_raw.get("signal_score") if isinstance(pm_raw, dict) else None)
        )
        modules = {
            "signal_engine": _equity_signals_composite_z(ticker),
            "squeeze":        round((sq_score - 50) / 50, 3) if sq_score is not None else None,
            "options":        _normalize_score(_safe_float(of.get("heat_score"), 50), 0, 100),
            "dark_pool":      _normalize_score(_safe_float(dp.get("dark_pool_score") or dp.get("score"), 50), 0, 100),
            "fundamentals":   round((fu_score - 50) / 50, 3) if fu_score is not None else None,
            "social":         _safe_float(soc.get("bull_bear_ratio")),
            "polymarket":     pm_score,
            "cross_asset":    _cross_asset_signal_to_score((sigs.get("cross_asset") or {}).get("signal")),
        }
        modules = {k: v for k, v in modules.items() if v is not None}

        # Fetch live price for PriceLadder rendering
        prices = _fetch_current_prices([ticker])
        current_price = prices.get(ticker)

        result = {
            # ── Identity ──────────────────────────────────────────────────────
            "data_available": True,
            "ticker":         ticker,
            "last_updated":   data.get("created_at"),
            "as_of":          data.get("date"),
            "current_price":  current_price,
            # ── Top-level fields expected by TickerDetail/TickerSignal ────────
            "direction":      data.get("direction"),
            "conviction":     data.get("conviction"),
            "signal_agreement_score": data.get("signal_agreement_score"),
            "ai_synthesis":   data.get("thesis"),   # TickerSignal compat alias
            "modules":        modules,
            "regime":         (sigs.get("regime") or {}).get("regime"),
            # ── AI thesis fields ──────────────────────────────────────────────
            "thesis":             data.get("thesis"),
            "primary_scenario":   data.get("primary_scenario"),
            "bear_scenario":      data.get("bear_scenario"),
            "key_invalidation":   data.get("key_invalidation"),
            "bull_probability":   data.get("bull_probability"),
            "bear_probability":   data.get("bear_probability"),
            "neutral_probability": data.get("neutral_probability"),
            "time_horizon":       data.get("time_horizon"),
            "data_quality":       data.get("data_quality"),
            "catalysts":          data.get("catalysts_json"),
            "risks":              data.get("risks_json"),
            # ── Price levels ──────────────────────────────────────────────────
            "entry_low":    data.get("entry_low"),
            "entry_high":   data.get("entry_high"),
            "stop_loss":    data.get("stop_loss"),
            "target_1":     data.get("target_1"),
            "target_2":     data.get("target_2"),
            "position_size_pct": data.get("position_size_pct"),
            "expected_moves": data.get("expected_moves_json") or [],
            "poc":          _safe_float(vp.get("poc")),
            "vwap":         _safe_float(vp.get("vwap_20d")),
            "max_pain":     _safe_float(mp.get("nearest_max_pain") or mp.get("max_pain_strike") or mp.get("max_pain")),
            # ── Squeeze ───────────────────────────────────────────────────────
            "squeeze_score":    _safe_float(sq.get("score")),
            "float_short_pct":  _safe_float(sq.get("short_float_pct") or sq.get("float_short_pct")),
            "days_to_cover":    _safe_float(sq.get("days_to_cover")),
            "volume_surge":     _safe_float(sq.get("volume_surge")),
            "recent_squeeze":   sq.get("recent_squeeze"),
            "ftd_shares":       sq.get("ftd_shares"),
            # ── Options ───────────────────────────────────────────────────────
            "heat_score":        _safe_float(of.get("heat_score")),
            "iv_rank":           _safe_float(of.get("iv_rank")),
            "iv_source":         of.get("iv_source"),
            "expected_move_pct": _safe_float(of.get("expected_move_pct")),
            "put_call_ratio":    _safe_float(of.get("pc_ratio") or of.get("put_call_ratio")),
            # ── Dark pool ─────────────────────────────────────────────────────
            "dark_pool_score":     _safe_float(dp.get("dark_pool_score") or dp.get("score")),
            "short_ratio_trend":   _normalise_dp_trend(dp.get("short_ratio_trend") or dp.get("trend")),
            "dark_pool_intensity": _normalise_dp_intensity(dp.get("dark_pool_intensity") or dp.get("intensity") or dp.get("off_exchange_pct")),
            # ── Social ────────────────────────────────────────────────────────
            "trend_score":   _safe_float(soc.get("trend_score")),
            "interest_level": _safe_float(soc.get("interest_level")),
            "bull_bear_ratio": _safe_float(soc.get("bull_bear_ratio")),
            "message_count": soc.get("message_count"),
            # ── Raw nested (kept for backward compat) ─────────────────────────
            "ai_thesis":     {
                "direction": data.get("direction"), "conviction": data.get("conviction"),
                "thesis": data.get("thesis"), "signal_agreement_score": data.get("signal_agreement_score"),
            },
            "entry_zone":    {"low": data.get("entry_low"), "high": data.get("entry_high")},
            "targets":       [data.get("target_1"), data.get("target_2")],
            "signals":       sigs,
            "dark_pool":     dp,
            "social":        soc,
            "squeeze":       sq,
            "volume_profile": vp,
            "options_heat":  of,
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("signals_ticker error for %s", ticker)
        return _no_data(str(e))


# ==============================================================================
# SECTION 5b: DEEP DIVE LIST ENDPOINT
# ==============================================================================

@app.get("/api/deepdive/tickers")
async def deepdive_tickers():
    """All tickers analyzed by Claude, most recent first. Powers the Deep Dive list page."""
    cache_key = "deepdive_tickers"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    conn = _db_connect(AI_QUANT_DB)
    if conn is None:
        result = _no_data("ai_quant_cache.db not found")
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    try:
        rows = conn.execute("""
            SELECT ticker, date, direction, conviction, signal_agreement_score,
                   time_horizon, data_quality, thesis, bull_probability,
                   bear_probability, neutral_probability, created_at
            FROM thesis_cache
            ORDER BY date DESC, created_at DESC
        """).fetchall()
        conn.close()

        # Keep only the most recent entry per ticker
        seen: set = set()
        tickers = []
        for r in rows:
            t = r["ticker"]
            if t in seen:
                continue
            seen.add(t)
            tickers.append({
                "ticker":                 t,
                "date":                   r["date"],
                "direction":              r["direction"],
                "conviction":             r["conviction"],
                "signal_agreement_score": r["signal_agreement_score"],
                "time_horizon":           r["time_horizon"],
                "data_quality":           r["data_quality"],
                "thesis_short":           (r["thesis"] or "")[:160],
                "bull_probability":       r["bull_probability"],
                "bear_probability":       r["bear_probability"],
                "neutral_probability":    r["neutral_probability"],
            })

        result = {"data_available": bool(tickers), "count": len(tickers), "data": tickers}
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("deepdive_tickers error")
        return _no_data(str(e))


# ==============================================================================
# SECTION 6: SCREENER ENDPOINTS
# ==============================================================================

def _latest_screener_file(prefix: str) -> Optional[Path]:
    """Find the most recent screener CSV matching signals_output/{prefix}*.csv."""
    files = sorted(glob.glob(str(SIGNALS_DIR / f"{prefix}*.csv")))
    return Path(files[-1]) if files else None


@app.get("/api/screeners/squeeze")
async def screeners_squeeze(min_score: float = Query(40.0, ge=0.0)):
    """Ranked short squeeze candidates from the most recent squeeze_screener run."""
    cache_key = f"screeners_squeeze:{min_score}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    f = _latest_screener_file("squeeze_signals")
    if f is None:
        result = _no_data("no squeeze_signals CSV found")
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    try:
        df = pd.read_csv(f, index_col=0)
        df = df.reset_index()

        col_map = {
            df.columns[0]: "ticker",
        }
        df = df.rename(columns=col_map)
        if "ticker" not in df.columns:
            df.rename(columns={"index": "ticker"}, inplace=True)

        score_col = next(
            (c for c in ["final_score", "composite", "score"] if c in df.columns),
            None
        )
        if score_col:
            df = df[df[score_col].fillna(0) >= min_score]

        df = df.sort_values(score_col, ascending=False) if score_col else df

        records = []
        for _, row in df.iterrows():
            records.append({
                "ticker":          str(row.get("ticker", "")),
                "final_score":     _safe_float(row.get("final_score") or row.get("composite")),
                "pct_float_short": _safe_float(row.get("short_pct_float") or row.get("short_pct")),
                "days_to_cover":   _safe_float(row.get("days_to_cover")),
                "volume_surge":    _safe_float(row.get("volume_surge_score")),
                "cost_to_borrow":  _safe_float(row.get("cost_to_borrow_score")),
                "recent_squeeze":  bool(row.get("recent_squeeze", False)),
                "ev_score":        _safe_float(row.get("ev_score")),
                "juice_target":    _safe_float(row.get("juice_target")),
                "rank":            _safe_int(row.get("rank")),
            })

        result = {
            "data_available": True,
            "source_file":   f.name,
            "count":         len(records),
            "data":          records,
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("screeners_squeeze error")
        return _no_data(str(e))


@app.get("/api/screeners/catalysts")
async def screeners_catalysts(min_score: float = Query(5.0, ge=0.0)):
    """Catalyst-scored opportunities from the most recent catalyst_screen run."""
    cache_key = f"screeners_catalysts:{min_score}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    f = _latest_screener_file("catalyst_screen")
    if f is None:
        result = _no_data("no catalyst_screen CSV found")
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    try:
        df = pd.read_csv(f)
        # First column is ticker
        if df.columns[0] not in ("ticker", "Ticker"):
            df = df.rename(columns={df.columns[0]: "ticker"})

        score_col = next(
            (c for c in ["composite", "total_score", "score"] if c in df.columns),
            None
        )
        if score_col:
            df = df[df[score_col].fillna(0) >= min_score]
        df = df.sort_values(score_col, ascending=False) if score_col else df

        records = []
        for _, row in df.iterrows():
            records.append({
                "ticker":         str(row.get("ticker", "")),
                "total_score":    _safe_float(row.get("composite") or row.get("total_score")),
                "squeeze_setup":  _safe_float(row.get("squeeze_score")),
                "volume_breakout": _safe_float(row.get("volume_score")),
                "social_score":   _safe_float(row.get("social_score")),
                "dark_pool_signal": _safe_float(row.get("dark_pool_score")),
                "options_score":  _safe_float(row.get("options_score")),
                "technical_score": _safe_float(row.get("technical_score")),
                "polymarket_score": _safe_float(row.get("polymarket_score")),
                "n_flags":        _safe_int(row.get("n_flags")),
                "setup_details":  str(row.get("flags", "")),
            })

        result = {
            "data_available": True,
            "source_file":   f.name,
            "count":         len(records),
            "data":          records,
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("screeners_catalysts error")
        return _no_data(str(e))


@app.get("/api/screeners/options")
async def screeners_options(min_heat: float = Query(50.0, ge=0.0)):
    """
    Options heat screener — pulls from ai_quant_cache options_flow signals
    since there is no standalone options CSV output.
    """
    cache_key = f"screeners_options:{min_heat}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    conn = _db_connect(AI_QUANT_DB)
    if conn is None:
        result = _no_data("ai_quant_cache.db not found")
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    try:
        rows = conn.execute("""
            SELECT ticker, date, signals_json
            FROM thesis_cache
            ORDER BY date DESC
        """).fetchall()
        conn.close()

        # Deduplicate by ticker (most recent first)
        seen: dict[str, Any] = {}
        for r in rows:
            if r["ticker"] not in seen:
                seen[r["ticker"]] = r

        records = []
        for ticker, r in seen.items():
            try:
                sj = json.loads(r["signals_json"] or "{}")
                of = sj.get("options_flow") or {}
                heat = _safe_float(of.get("heat_score"), 0)
                if heat < min_heat:
                    continue
                mp = sj.get("max_pain") or {}
                records.append({
                    "ticker":           ticker,
                    "heat_score":       heat,
                    "iv_rank":          _safe_float(of.get("iv_rank")),
                    "iv_source":        of.get("iv_source", "options_chain"),
                    "volume_spike_ratio": _safe_float(of.get("total_options_vol")),
                    "expected_move_pct": _safe_float(of.get("expected_move_pct")),
                    "put_call_ratio":   _safe_float(of.get("pc_ratio")),
                    "max_pain_strike":  _safe_float(mp.get("nearest_max_pain")),
                    "days_to_expiry":   _safe_int(of.get("days_to_exp")),
                    "as_of":            r["date"],
                })
            except Exception:
                continue

        records.sort(key=lambda x: x["heat_score"] or 0, reverse=True)
        result = {
            "data_available": True,
            "count":          len(records),
            "data":           records,
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("screeners_options error")
        return _no_data(str(e))


# ==============================================================================
# SECTION 7: REGIME & MACRO ENDPOINTS
# ==============================================================================

@app.get("/api/regime/current")
async def regime_current():
    """Full regime_filter output from data/regime_cache.json."""
    cache_key = "regime_current"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    if not REGIME_CACHE.exists():
        result = _no_data("regime_cache.json not found")
        _cache.set(cache_key, result, TTL_LONG)
        return result

    try:
        with open(REGIME_CACHE) as f:
            rc = json.load(f)

        mr = rc.get("market_regime", {})
        sr = rc.get("sector_regimes", {})

        result = {
            "data_available":  True,
            "regime":          mr.get("regime", "UNKNOWN"),
            "score":           mr.get("score", 0),
            "vix":             mr.get("vix"),
            "spy_vs_200ma":    mr.get("spy_vs_200ma"),
            "yield_curve_spread": mr.get("yield_curve_spread"),
            "components":      mr.get("components", {}),
            "computed_at":     mr.get("computed_at"),
            "sector_regimes":  {k: v for k, v in sr.items() if k != "computed_at"},
        }
        _cache.set(cache_key, result, TTL_LONG)
        return result

    except Exception as e:
        log.exception("regime_current error")
        return _no_data(str(e))


@app.get("/api/regime/sectors")
async def regime_sectors():
    """
    Sector regime breakdown. Reads sector_cache.json for detailed ETF data,
    falls back to sector_regimes in regime_cache.json.
    """
    cache_key = "regime_sectors"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    # Try rich sector_cache first
    if SECTOR_CACHE.exists():
        try:
            with open(SECTOR_CACHE) as f:
                sc = json.load(f)
            result = {"data_available": True, "data": sc}
            _cache.set(cache_key, result, TTL_LONG)
            return result
        except Exception:
            pass

    # Fallback: regime_cache sector_regimes
    if REGIME_CACHE.exists():
        try:
            with open(REGIME_CACHE) as f:
                rc = json.load(f)
            sr = rc.get("sector_regimes", {})
            sectors = {k: {"regime": v} for k, v in sr.items() if k != "computed_at"}
            result = {"data_available": True, "data": sectors}
            _cache.set(cache_key, result, TTL_LONG)
            return result
        except Exception as e:
            return _no_data(str(e))

    return _no_data("no sector data found")


# ==============================================================================
# SECTION 8: DARK POOL ENDPOINTS
# ==============================================================================

def _load_dark_pool() -> Optional[dict]:
    """Load dark_pool_latest.json. Returns {ticker: row_dict} or None if missing.

    Normalises two formats:
      - New: {"generated": "...", "results": [{ticker, dark_pool_score, signal, ...}]}
      - Legacy: {ticker: {signal, score, ...}}
    """
    dp_file = DATA_DIR / "dark_pool_latest.json"
    if not dp_file.exists():
        finra_dir = DATA_DIR / "finra_cache"
        if finra_dir.exists():
            files = sorted(finra_dir.glob("*.json"))
            if files:
                with open(files[-1]) as f:
                    raw = json.load(f)
            else:
                return None
        else:
            return None
    else:
        with open(dp_file) as f:
            raw = json.load(f)

    # New list-of-records format: {"results": [...]}
    if isinstance(raw, dict) and "results" in raw:
        return {row["ticker"]: row for row in raw["results"] if "ticker" in row}

    # Legacy dict format: {ticker: {...}}
    if isinstance(raw, dict):
        return raw

    return None


@app.get("/api/darkpool/top")
async def darkpool_top(
    signal: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
):
    """Top tickers by dark pool signal score."""
    cache_key = f"darkpool_top:{signal}:{limit}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    dp = _load_dark_pool()
    if dp is None:
        result = _no_data("dark_pool_latest.json not found")
        _cache.set(cache_key, result, TTL_MEDIUM)
        return result

    try:
        rows = []
        for ticker, info in dp.items():
            if not isinstance(info, dict):
                continue
            dp_signal = str(info.get("signal", "")).upper()
            if signal and dp_signal != signal.upper():
                continue

            # Normalise short_ratio_trend: numeric slope → 'up'/'down'/'flat'
            raw_trend = info.get("short_ratio_trend")
            if isinstance(raw_trend, (int, float)):
                trend_str = "up" if raw_trend > 0.0001 else ("down" if raw_trend < -0.0001 else "flat")
            else:
                trend_str = str(raw_trend).lower() if raw_trend else "flat"

            rows.append({
                "ticker":              ticker,
                "signal":              dp_signal,
                "dark_pool_score":     _safe_float(info.get("dark_pool_score") or info.get("score")),
                "short_ratio":         _safe_float(info.get("short_ratio_today") or info.get("short_ratio")),
                "short_ratio_trend":   trend_str,
                "dark_pool_intensity": _safe_float(info.get("dark_pool_intensity") or info.get("intensity")),
                "off_exchange_pct":    _safe_float(info.get("off_exchange_pct")),
                "history":             info.get("short_ratio_history") or [],
            })

        rows.sort(key=lambda x: x.get("dark_pool_score") or 0, reverse=True)
        rows = rows[:limit]

        result = {"data_available": True, "count": len(rows), "data": rows}
        _cache.set(cache_key, result, TTL_MEDIUM)
        return result

    except Exception as e:
        log.exception("darkpool_top error")
        return _no_data(str(e))


@app.get("/api/darkpool/ticker/{ticker}")
async def darkpool_ticker(ticker: str):
    """Full dark pool signal dict for one ticker."""
    ticker = ticker.upper()
    dp = _load_dark_pool()
    if dp is None:
        return _no_data("dark_pool_latest.json not found")
    if ticker not in dp:
        return _no_data(f"no dark pool data for {ticker}")
    return {"data_available": True, "ticker": ticker, "data": dp[ticker]}


# ==============================================================================
# SECTION 8b: MAX PAIN LIVE ENDPOINT
# ==============================================================================

@app.get("/api/max_pain/{ticker}")
async def max_pain_live(ticker: str, expirations: int = Query(4, ge=1, le=12)):
    """Live max pain calculation — always fetches fresh options chain (1h TTL)."""
    ticker = ticker.upper()
    cache_key = f"max_pain:{ticker}:{expirations}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    try:
        from max_pain import get_max_pain
        result = get_max_pain(ticker, n_expirations=expirations)
        if not result:
            out = _no_data(f"no options data for {ticker}")
        else:
            out = {"data_available": True, "ticker": ticker, "data": result}
        _cache.set(cache_key, out, 3600)  # 1h TTL — options chains update intraday
        return out
    except Exception as e:
        log.exception("max_pain_live error")
        return _no_data(str(e))


# ==============================================================================
# SECTION 9: CONFLICT RESOLUTION ENDPOINTS
# ==============================================================================

def _list_resolution_logs() -> list[Path]:
    """Return all conflict_resolution_*.csv logs sorted by date descending."""
    files = sorted(glob.glob(str(LOGS_DIR / "conflict_resolution_*.csv")), reverse=True)
    return [Path(f) for f in files]


@app.get("/api/resolution/log")
async def resolution_log(
    date: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=10000),
):
    """
    Return rows from the conflict resolution log CSV as JSON.
    date: YYYYMMDD — if omitted, uses the most recent log.
    """
    logs = _list_resolution_logs()
    if not logs:
        return _no_data("no conflict_resolution logs found")

    if date:
        target = LOGS_DIR / f"conflict_resolution_{date}.csv"
        log_file = target if target.exists() else None
    else:
        log_file = logs[0]

    if log_file is None or not log_file.exists():
        return _no_data(f"conflict_resolution log not found for date={date}")

    try:
        df = pd.read_csv(log_file)
        df = df.tail(limit)
        raw_records = df.to_dict(orient="records")
        records = _json_safe(raw_records)
        return {
            "data_available": True,
            "source_file":    log_file.name,
            "count":          len(records),
            "data":           records,
        }
    except Exception as e:
        log.exception("resolution_log error")
        return _no_data(str(e))


@app.get("/api/resolution/stats")
async def resolution_stats():
    """Aggregated stats from the last 4 weeks of resolution logs."""
    cache_key = "resolution_stats"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    logs = _list_resolution_logs()[:4]  # last 4 log files
    if not logs:
        result = _no_data("no conflict_resolution logs found")
        _cache.set(cache_key, result, TTL_LONG)
        return result

    try:
        frames = []
        for lf in logs:
            try:
                frames.append(pd.read_csv(lf))
            except Exception:
                pass

        if not frames:
            return _no_data("could not read resolution logs")

        df = pd.concat(frames, ignore_index=True)

        # claude_skipped column
        skip_col = next((c for c in df.columns if "skip" in c.lower()), None)
        if skip_col:
            df[skip_col] = df[skip_col].astype(str).str.lower().isin(["true", "1", "yes"])
            claude_skip_rate = float(df[skip_col].mean() * 100)
        else:
            claude_skip_rate = 0.0

        # Most common override
        override_col = next((c for c in df.columns if "override" in c.lower()), None)
        most_common_override = ""
        bear_circuit_hits = 0
        if override_col:
            overrides = df[override_col].dropna().astype(str)
            overrides = overrides[overrides.str.strip().str.len() > 0]
            if not overrides.empty:
                most_common_override = str(overrides.mode().iloc[0])
            bear_circuit_hits = int(overrides.str.contains("bear", case=False, na=False).sum())

        # Module agreement: from confidence or bull_weight columns
        conf_col = next((c for c in df.columns if "confidence" in c.lower()), None)
        module_agreement_avg = 0.0
        if conf_col:
            module_agreement_avg = float(df[conf_col].dropna().mean() * 100)

        result = {
            "data_available":        True,
            "rows_analyzed":         len(df),
            "log_files_included":    len(frames),
            "claude_skip_rate":      round(claude_skip_rate, 1),
            "most_common_override":  most_common_override,
            "module_agreement_avg":  round(module_agreement_avg, 1),
            "bear_circuit_breaker_hits": bear_circuit_hits,
        }
        _cache.set(cache_key, result, TTL_LONG)
        return result

    except Exception as e:
        log.exception("resolution_stats error")
        return _no_data(str(e))


# ==============================================================================
# SECTION 10: BACKTEST ENDPOINT
# ==============================================================================

@app.get("/api/backtest/results")
async def backtest_results():
    """
    Return backtest results. Prefers data/backtest_results.json if it exists,
    otherwise reads from signals_output/backtest_equity_metrics.csv.
    """
    cache_key = "backtest_results"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    # 1) Try pre-built JSON
    json_file = DATA_DIR / "backtest_results.json"
    if json_file.exists():
        try:
            with open(json_file) as f:
                data = json.load(f)
            result = {"data_available": True, **data}
            _cache.set(cache_key, result, TTL_LONG)
            return result
        except Exception:
            pass

    # 2) Build from CSV outputs
    metrics_file   = SIGNALS_DIR / "backtest_equity_metrics.csv"
    eq_curve_file  = SIGNALS_DIR / "backtest_equity_equity_curve.csv"

    if not metrics_file.exists():
        result = _no_data("backtest_equity_metrics.csv not found")
        _cache.set(cache_key, result, TTL_LONG)
        return result

    try:
        metrics = pd.read_csv(metrics_file)
        # metrics is key/value rows: label, value
        raw_m = dict(zip(metrics.iloc[:, 0], metrics.iloc[:, 1])) if len(metrics.columns) >= 2 else {}
        m = _json_safe(raw_m)

        equity_curve = []
        period_start, period_end = None, None
        if eq_curve_file.exists():
            ec = pd.read_csv(eq_curve_file)
            equity_curve = _json_safe(ec.to_dict(orient="records"))
            if len(ec) > 0 and "date" in ec.columns:
                period_start = str(ec["date"].iloc[0])
                period_end   = str(ec["date"].iloc[-1])

        # Build a synthetic single window from aggregate metrics so the
        # frontend's windows[] array is non-empty and renders correctly.
        synthetic_window = {
            "period_start":      period_start or "",
            "period_end":        period_end   or "",
            "total_return_pct":  (_safe_float(m.get("total_return_port")) or 0) * 100,
            "sharpe":            _safe_float(m.get("sharpe")) or 0,
            "max_drawdown_pct":  (_safe_float(m.get("max_drawdown")) or 0) * 100,
            "hit_rate_pct":      (_safe_float(m.get("hit_rate_weekly")) or 0) * 100,
            "n_trades":          0,
        }

        result = {
            "data_available":    True,
            "label":             m.get("label", "Equity Multi-Factor"),
            "metrics":           m,
            "equity_curve":      equity_curve,
            "windows":           [synthetic_window],
            "overall_sharpe":    _safe_float(m.get("sharpe_ratio") or m.get("sharpe")),
            "spy_sharpe":        _safe_float(m.get("cagr_bench")),  # best proxy available
            "factor_ic_table":   [],
            "worst_window":      None,
            "weight_recommendations": {},
        }
        _cache.set(cache_key, result, TTL_LONG)
        return result

    except Exception as e:
        log.exception("backtest_results error")
        return _no_data(str(e))


# ==============================================================================
# SECTION 11: UNIVERSE ENDPOINT
# ==============================================================================

@app.get("/api/universe/stats")
async def universe_stats():
    """Universe composition summary."""
    cache_key = "universe_stats"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    # Try dedicated universe stats file first
    uni_file = DATA_DIR / "universe_stats.json"
    if uni_file.exists():
        try:
            with open(uni_file) as f:
                data = json.load(f)
            result = {"data_available": True, **data}
            _cache.set(cache_key, result, TTL_LONG)
            return result
        except Exception:
            pass

    # Fallback: derive from config + latest signals file
    try:
        from config import (
            EQUITY_WATCHLIST, CUSTOM_WATCHLIST, UNIVERSE_INDICES,
            UNIVERSE_PRESCREEN_TOP_N,
        )
        total = len(set(EQUITY_WATCHLIST + CUSTOM_WATCHLIST))

        sig_file = get_latest_signals_file()
        sig_count = 0
        if sig_file:
            df = pd.read_csv(sig_file, index_col=0)
            sig_count = len(df)

        result = {
            "data_available":   True,
            "total_tickers":    total,
            "post_prescreen":   UNIVERSE_PRESCREEN_TOP_N,
            "tier1_count":      None,
            "tier2_count":      None,
            "tier3_count":      None,
            "signals_in_latest_run": sig_count,
            "last_updated":     sig_file.stem.split("_")[-1] if sig_file else None,
            "indices_included": UNIVERSE_INDICES,
        }
        _cache.set(cache_key, result, TTL_LONG)
        return result

    except Exception as e:
        log.exception("universe_stats error")
        return _no_data(str(e))


# ==============================================================================
# SECTION 12: UTILITY ENDPOINTS
# ==============================================================================

@app.get("/api/health")
async def health():
    """Health check — confirms API is running and lists data availability."""
    checks = {
        "paper_trades_db":  PAPER_TRADES_DB.exists(),
        "trade_journal_db": TRADE_JOURNAL_DB.exists(),
        "ai_quant_db":      AI_QUANT_DB.exists(),
        "regime_cache":     REGIME_CACHE.exists(),
        "signals_output":   SIGNALS_DIR.exists(),
        "logs_dir":         LOGS_DIR.exists(),
    }
    return {
        "status":        "ok",
        "version":       "2.0",
        "base_dir":      str(BASE_DIR),
        "data_checks":   checks,
        "latest_signals": get_latest_signals_file() and get_latest_signals_file().name,
        "available_dates": get_signals_date_list()[:5],
    }


@app.get("/api/cache/invalidate")
async def cache_invalidate():
    """Force-clear the in-memory cache (useful after a data refresh)."""
    _cache._store.clear()
    return {"status": "cache cleared"}


@app.post("/api/cache/invalidate")
async def cache_invalidate_post():
    """POST version — called by run_master.sh after the pipeline completes."""
    _cache._store.clear()
    return {"invalidated": True, "timestamp": datetime.utcnow().isoformat() + "Z"}
