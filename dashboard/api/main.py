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

import asyncio
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

import httpx
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

try:
    from fx_rates import convert_to_eur as _convert_to_eur
except ImportError:
    def _convert_to_eur(amount: float, currency: str = "USD") -> float:  # type: ignore[misc]
        return round(amount / 1.09, 4)

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


_eur_usd_cache: dict = {"rate": 1.08, "fetched_at": 0.0}

def _get_eur_usd_rate() -> float:
    """Return current EUR/USD rate (1 EUR = X USD). Cached 10 min."""
    import time
    if time.time() - _eur_usd_cache["fetched_at"] < 600:
        return _eur_usd_cache["rate"]
    try:
        import yfinance as yf
        hist = yf.Ticker("EURUSD=X").history(period="1d")
        if not hist.empty:
            rate = float(hist["Close"].iloc[-1])
            _eur_usd_cache["rate"] = rate
            _eur_usd_cache["fetched_at"] = time.time()
            return rate
    except Exception:
        pass
    return _eur_usd_cache["rate"]


def _to_eur(price: float, currency: str, eur_usd: float) -> float:
    """Convert price to EUR. eur_usd = 1 EUR in USD (e.g. 1.08)."""
    if currency == "EUR":
        return price
    return price / eur_usd  # USD → EUR


def _ensure_trades_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker           TEXT    NOT NULL,
            direction        TEXT    NOT NULL DEFAULT 'LONG',
            date             TEXT    NOT NULL,
            price            REAL    NOT NULL,
            price_eur        REAL    NOT NULL,
            size_eur         REAL    NOT NULL,
            shares           REAL,
            currency         TEXT    NOT NULL DEFAULT 'USD',
            fx_rate          REAL    NOT NULL DEFAULT 1.08,
            signal_composite REAL,
            stop_loss        REAL,
            target_1         REAL,
            target_2         REAL,
            notes            TEXT,
            action           TEXT    NOT NULL DEFAULT 'BUY',
            status           TEXT    NOT NULL DEFAULT 'open',
            close_date       TEXT,
            close_price      REAL,
            close_price_eur  REAL,
            close_currency   TEXT,
            close_fx_rate    REAL,
            pnl_eur          REAL
        )
    """)
    # Migrate existing tables that predate the new columns
    existing = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
    for col, defn in [
        ("direction",       "TEXT NOT NULL DEFAULT 'LONG'"),
        ("price_eur",       "REAL NOT NULL DEFAULT 0"),
        ("currency",        "TEXT NOT NULL DEFAULT 'USD'"),
        ("fx_rate",         "REAL NOT NULL DEFAULT 1.08"),
        ("close_date",      "TEXT"),
        ("close_price",     "REAL"),
        ("close_price_eur", "REAL"),
        ("close_currency",  "TEXT"),
        ("close_fx_rate",   "REAL"),
        ("pnl_eur",         "REAL"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {defn}")
    conn.commit()


from pydantic import BaseModel as _BaseModel  # noqa: E402 (already imported below)

class AddPositionRequest(_BaseModel):
    ticker:      str
    direction:   str   = "LONG"
    entry_price: float
    currency:    str   = "USD"
    size_eur:    float
    conviction:  Optional[float] = None
    stop_loss:   Optional[float] = None
    target_1:    Optional[float] = None
    target_2:    Optional[float] = None
    notes:       Optional[str]   = None


class SellPositionRequest(_BaseModel):
    sell_price: float
    currency:   str = "USD"


@app.post("/api/portfolio/positions")
async def add_position(req: AddPositionRequest):
    """Manually insert an open position into trade_journal.db."""
    if req.entry_price <= 0 or req.size_eur <= 0:
        raise HTTPException(status_code=400, detail="entry_price and size_eur must be > 0")
    if req.currency not in ("EUR", "USD"):
        raise HTTPException(status_code=400, detail="currency must be EUR or USD")
    conn = sqlite3.connect(str(TRADE_JOURNAL_DB))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_trades_table(conn)
        eur_usd   = _get_eur_usd_rate()
        price_eur = _to_eur(req.entry_price, req.currency, eur_usd)
        shares    = req.size_eur / price_eur if price_eur > 0 else 0
        today     = datetime.utcnow().strftime("%Y-%m-%d")
        conn.execute("""
            INSERT INTO trades
                (ticker, direction, date, price, price_eur, size_eur, shares,
                 currency, fx_rate, signal_composite, stop_loss, target_1, target_2,
                 notes, action, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'BUY', 'open')
        """, (
            req.ticker.upper().strip(), req.direction.upper(),
            today, req.entry_price, price_eur, req.size_eur, shares,
            req.currency.upper(), eur_usd,
            req.conviction, req.stop_loss, req.target_1, req.target_2, req.notes,
        ))
        conn.commit()
        log.info("Position added: %s %s %.2f EUR @ %.4f %s (%.4f EUR)",
                 req.direction, req.ticker.upper(), req.size_eur,
                 req.entry_price, req.currency, price_eur)
        return {"ok": True, "ticker": req.ticker.upper().strip(), "price_eur": round(price_eur, 4), "fx_rate": eur_usd}
    except Exception as e:
        log.exception("add_position error")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.post("/api/portfolio/positions/{ticker}/sell")
async def sell_position(ticker: str, req: SellPositionRequest):
    """Close the most recent open position for ticker with sell price, computing P&L in EUR."""
    if req.sell_price <= 0:
        raise HTTPException(status_code=400, detail="sell_price must be > 0")
    if req.currency not in ("EUR", "USD"):
        raise HTTPException(status_code=400, detail="currency must be EUR or USD")
    if not TRADE_JOURNAL_DB.exists():
        raise HTTPException(status_code=503, detail="trade_journal.db not found — no open positions")
    conn = sqlite3.connect(str(TRADE_JOURNAL_DB))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_trades_table(conn)
        row = conn.execute(
            "SELECT * FROM trades WHERE ticker = ? AND action = 'BUY' AND status = 'open' ORDER BY date DESC LIMIT 1",
            (ticker.upper().strip(),),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"No open position for {ticker.upper()}")

        eur_usd        = _get_eur_usd_rate()
        close_price_eur = _to_eur(req.sell_price, req.currency, eur_usd)
        entry_price_eur = row["price_eur"] if row["price_eur"] else _to_eur(row["price"], row["currency"] or "USD", row["fx_rate"] or 1.08)
        shares         = row["shares"] or (row["size_eur"] / entry_price_eur if entry_price_eur > 0 else 0)
        direction      = (row["direction"] or "LONG").upper()

        if direction == "LONG":
            pnl_eur = (close_price_eur - entry_price_eur) * shares
        else:  # SHORT
            pnl_eur = (entry_price_eur - close_price_eur) * shares

        today = datetime.utcnow().strftime("%Y-%m-%d")
        conn.execute("""
            UPDATE trades SET
                status          = 'closed',
                close_date      = ?,
                close_price     = ?,
                close_price_eur = ?,
                close_currency  = ?,
                close_fx_rate   = ?,
                pnl_eur         = ?
            WHERE id = ?
        """, (today, req.sell_price, close_price_eur, req.currency.upper(), eur_usd, pnl_eur, row["id"]))
        conn.commit()
        log.info("Position sold: %s @ %.4f %s → P&L %.2f EUR", ticker.upper(), req.sell_price, req.currency, pnl_eur)
        return {
            "ok": True,
            "ticker": ticker.upper(),
            "pnl_eur": round(pnl_eur, 2),
            "close_price_eur": round(close_price_eur, 4),
            "fx_rate": eur_usd,
        }
    except HTTPException:
        raise
    except Exception as e:
        log.exception("sell_position error")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.delete("/api/portfolio/positions/{ticker}")
async def close_position(ticker: str):
    """Mark the most recent open position as closed (no P&L recorded)."""
    if not TRADE_JOURNAL_DB.exists():
        raise HTTPException(status_code=503, detail="trade_journal.db not found")
    conn = sqlite3.connect(str(TRADE_JOURNAL_DB))
    conn.row_factory = sqlite3.Row
    try:
        result = conn.execute(
            "UPDATE trades SET status = 'closed' WHERE ticker = ? AND action = 'BUY' AND status = 'open'",
            (ticker.upper().strip(),),
        )
        conn.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"No open position for {ticker.upper()}")
        return {"ok": True, "ticker": ticker.upper().strip()}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("close_position error")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/portfolio/trades")
async def get_trades():
    """Return all trades (open + closed) with P&L for the trades dashboard."""
    conn = _db_connect(TRADE_JOURNAL_DB)
    if conn is None:
        return {"data_available": True, "data": []}
    try:
        rows = conn.execute(
            "SELECT * FROM trades WHERE action = 'BUY' ORDER BY date DESC, id DESC"
        ).fetchall()
        conn.close()
        trades = []
        for r in rows:
            trades.append({
                "id":              r["id"],
                "ticker":          r["ticker"],
                "direction":       r["direction"] or "LONG",
                "date":            r["date"],
                "entry_price":     r["price"],
                "entry_price_eur": r["price_eur"] or r["price"],
                "currency":        r["currency"] or "USD",
                "fx_rate":         r["fx_rate"] or 1.08,
                "size_eur":        r["size_eur"],
                "shares":          r["shares"],
                "status":          r["status"],
                "close_date":      r["close_date"],
                "close_price":     r["close_price"],
                "close_price_eur": r["close_price_eur"],
                "close_currency":  r["close_currency"],
                "pnl_eur":         r["pnl_eur"],
                "stop_loss":       r["stop_loss"],
                "target_1":        r["target_1"],
                "notes":           r["notes"],
            })
        return {"data_available": True, "data": trades}
    except Exception as e:
        log.exception("get_trades error")
        return _no_data(str(e))


# ==============================================================================
# SECTION 4b: CASH MANAGEMENT ENDPOINTS
# ==============================================================================

def _ensure_cash_table(conn: sqlite3.Connection) -> None:
    """Create portfolio_settings table if it doesn't exist (write connection)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_settings (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _get_cash_eur(conn: sqlite3.Connection) -> tuple[float, str | None]:
    """Return (cash_eur, updated_at) from portfolio_settings, or (0.0, None)."""
    row = conn.execute(
        "SELECT value, updated_at FROM portfolio_settings WHERE key = 'cash_eur'"
    ).fetchone()
    if row is None:
        return 0.0, None
    return float(row[0]), row[1]


@app.get("/api/portfolio/cash")
async def get_cash():
    """Return the manually-set cash balance from paper_trades.db."""
    if not PAPER_TRADES_DB.exists():
        return {"cash_eur": 0.0, "updated_at": None}
    conn = sqlite3.connect(str(PAPER_TRADES_DB))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_cash_table(conn)
        cash, updated_at = _get_cash_eur(conn)
        return {"cash_eur": round(cash, 2), "updated_at": updated_at}
    finally:
        conn.close()


from pydantic import BaseModel


class CashUpdateRequest(BaseModel):
    action: str   # "set" | "add" | "reduce"
    amount: float


@app.post("/api/portfolio/cash")
async def update_cash(req: CashUpdateRequest):
    """
    Set, add to, or reduce the manual cash balance.
    Body: { "action": "set"|"add"|"reduce", "amount": <float> }
    """
    if req.action not in ("set", "add", "reduce"):
        raise HTTPException(status_code=400, detail="action must be 'set', 'add', or 'reduce'")
    if req.amount < 0:
        raise HTTPException(status_code=400, detail="amount must be >= 0")

    if not PAPER_TRADES_DB.exists():
        raise HTTPException(status_code=503, detail="paper_trades.db not found")

    conn = sqlite3.connect(str(PAPER_TRADES_DB))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_cash_table(conn)
        current_cash, _ = _get_cash_eur(conn)

        if req.action == "set":
            new_cash = req.amount
        elif req.action == "add":
            new_cash = current_cash + req.amount
        else:  # reduce
            new_cash = max(0.0, current_cash - req.amount)

        now = datetime.utcnow().isoformat() + "Z"
        conn.execute("""
            INSERT INTO portfolio_settings (key, value, updated_at)
            VALUES ('cash_eur', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """, (str(new_cash), now))
        conn.commit()

        # Invalidate portfolio summary cache so next call reflects new cash
        _cache.invalidate("portfolio_summary")

        log.info("Cash updated: %s %.2f → new balance %.2f", req.action, req.amount, new_cash)
        return {"cash_eur": round(new_cash, 2), "updated_at": now}
    finally:
        conn.close()


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
    """All universe tickers for the Deep Dive list. Claude-analyzed tickers have has_thesis=True;
    unanalyzed tickers from the fundamental CSV appear with has_thesis=False."""
    cache_key = "deepdive_tickers"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    # ── Load fundamental universe for name/sector/price enrichment ──────────────
    fund_map: dict[str, dict] = {}
    fund_files = sorted(glob.glob(str(SIGNALS_DIR / "fundamental_*.csv")))
    if fund_files:
        try:
            with open(fund_files[-1], newline="") as fh:
                for row in csv.DictReader(fh):
                    t = row.get("ticker", "").strip().upper()
                    if t:
                        fund_map[t] = {
                            "name":   row.get("name", ""),
                            "sector": row.get("sector", ""),
                            "price":  _safe_float(row.get("price")),
                        }
        except Exception:
            pass

    conn = _db_connect(AI_QUANT_DB)
    seen: set = set()
    tickers = []

    if conn is not None:
        try:
            rows = conn.execute("""
                SELECT ticker, date, direction, conviction, signal_agreement_score,
                       time_horizon, data_quality, thesis, bull_probability,
                       bear_probability, neutral_probability, created_at,
                       entry_low, entry_high, target_1, target_2, stop_loss
                FROM thesis_cache
                ORDER BY date DESC, created_at DESC
            """).fetchall()
            conn.close()

            for r in rows:
                t = r["ticker"]
                if t in seen:
                    continue
                seen.add(t)
                fd = fund_map.get(t, {})
                tickers.append({
                    "ticker":                 t,
                    "has_thesis":             True,
                    "name":                   fd.get("name", ""),
                    "sector":                 fd.get("sector", ""),
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
                    "entry_low":              r["entry_low"],
                    "entry_high":             r["entry_high"],
                    "target_1":               r["target_1"],
                    "target_2":               r["target_2"],
                    "stop_loss":              r["stop_loss"],
                })
        except Exception:
            log.exception("deepdive_tickers: thesis_cache read error")

    # ── Always include open positions from trade_journal ─────────────────────────
    tj_conn = _db_connect(TRADE_JOURNAL_DB)
    if tj_conn is not None:
        try:
            open_rows = tj_conn.execute(
                "SELECT DISTINCT ticker FROM trades WHERE status='open'"
            ).fetchall()
            tj_conn.close()
            for r in open_rows:
                t = r["ticker"]
                if t not in seen:
                    seen.add(t)
                    fd = fund_map.get(t, {})
                    tickers.insert(0, {
                        "ticker":                 t,
                        "has_thesis":             False,
                        "name":                   fd.get("name", ""),
                        "sector":                 fd.get("sector", ""),
                        "date":                   None,
                        "direction":              None,
                        "conviction":             None,
                        "signal_agreement_score": None,
                        "time_horizon":           None,
                        "data_quality":           None,
                        "thesis_short":           None,
                        "bull_probability":       None,
                        "bear_probability":       None,
                        "neutral_probability":    None,
                        "entry_low":              None,
                        "entry_high":             None,
                        "target_1":               None,
                        "target_2":               None,
                        "stop_loss":              None,
                    })
        except Exception:
            pass

    # ── Add all remaining fundamental universe tickers (unanalyzed) ──────────────
    for t, fd in fund_map.items():
        if t not in seen:
            seen.add(t)
            tickers.append({
                "ticker":                 t,
                "has_thesis":             False,
                "name":                   fd.get("name", ""),
                "sector":                 fd.get("sector", ""),
                "date":                   None,
                "direction":              None,
                "conviction":             None,
                "signal_agreement_score": None,
                "time_horizon":           None,
                "data_quality":           None,
                "thesis_short":           None,
                "bull_probability":       None,
                "bear_probability":       None,
                "neutral_probability":    None,
                "entry_low":              None,
                "entry_high":             None,
                "target_1":               None,
                "target_2":               None,
                "stop_loss":              None,
            })

    result = {"data_available": bool(tickers), "count": len(tickers), "data": tickers}
    _cache.set(cache_key, result, TTL_SHORT)
    return result


@app.get("/api/signals/outcomes")
async def signals_outcomes(days: int = Query(90, ge=1, le=365)):
    """
    Claude thesis outcome history — direction accuracy, target hit rate,
    return % vs entry, % gap vs targets. Powered by thesis_outcomes table.
    """
    cache_key = f"signals_outcomes:{days}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    conn = _db_connect(AI_QUANT_DB)
    if conn is None:
        result = _no_data("ai_quant_cache.db not found")
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    try:
        conn.execute("SELECT id FROM thesis_outcomes LIMIT 1")
    except Exception:
        result = _no_data("thesis_outcomes table not yet populated — run thesis_checker.py")
        _cache.set(cache_key, result, TTL_SHORT)
        conn.close()
        return result

    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute("""
            SELECT o.ticker, o.thesis_date, o.direction, o.conviction,
                   o.entry_price, o.target_1, o.target_2, o.stop_loss,
                   o.price_7d, o.price_14d, o.price_30d,
                   o.return_7d, o.return_14d, o.return_30d,
                   o.vs_target_1_pct, o.vs_target_2_pct,
                   o.hit_target_1, o.hit_target_2, o.hit_stop,
                   o.days_to_target_1, o.days_to_stop,
                   o.outcome, o.claude_correct, o.was_traded,
                   o.last_checked
            FROM thesis_outcomes o
            WHERE o.thesis_date >= ?
            ORDER BY o.thesis_date DESC
        """, (cutoff,)).fetchall()
        conn.close()

        data = [dict(r) for r in rows]

        # Summary stats
        resolved  = [r for r in data if r["outcome"] not in ("OPEN", None)]
        correct   = [r for r in data if r["claude_correct"] == 1]
        wrong     = [r for r in data if r["claude_correct"] == 0]
        hit_t1    = [r for r in data if r["hit_target_1"]]
        hit_stop  = [r for r in data if r["hit_stop"]]
        ret30     = [r["return_30d"] for r in data if r["return_30d"] is not None]
        vt1       = [r["vs_target_1_pct"] for r in data if r["vs_target_1_pct"] is not None]

        def _avg(lst):
            return round(sum(lst) / len(lst), 2) if lst else None

        direction_accuracy = (
            round(len(correct) / (len(correct) + len(wrong)) * 100, 1)
            if (len(correct) + len(wrong)) > 0 else None
        )

        summary = {
            "total":               len(data),
            "resolved":            len(resolved),
            "open":                len(data) - len(resolved),
            "direction_correct":   len(correct),
            "direction_wrong":     len(wrong),
            "direction_accuracy_pct": direction_accuracy,
            "hit_target_1":        len(hit_t1),
            "hit_stop":            len(hit_stop),
            "avg_return_30d":      _avg(ret30),
            "avg_vs_target_1_pct": _avg(vt1),
        }

        result = {"data_available": bool(data), "days": days, "summary": summary, "data": data}
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("signals_outcomes error")
        return _no_data(str(e))


@app.get("/api/signals/accuracy")
async def signals_accuracy():
    """
    Monthly Claude precision report — proof-of-concept accuracy tracking.

    Returns per-month aggregates plus an all-time summary:
      - direction_accuracy_pct : % of theses where BULL/BEAR direction was correct at 30d
      - target_hit_rate_pct    : % of theses that hit target_1
      - stop_hit_rate_pct      : % of theses that hit stop_loss first
      - avg_return_30d         : average 30d return across all theses that month
      - avg_vs_target_1_pct    : how close price got to Claude's target (neg = fell short)
      - total / resolved / open counts

    Never deletes data — accumulates forever as proof of Claude's prediction quality.
    """
    cache_key = "signals_accuracy"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    conn = _db_connect(AI_QUANT_DB)
    if conn is None:
        result = _no_data("ai_quant_cache.db not found")
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    try:
        conn.execute("SELECT id FROM thesis_outcomes LIMIT 1")
    except Exception:
        result = _no_data("thesis_outcomes table not yet populated — run thesis_checker.py")
        _cache.set(cache_key, result, TTL_SHORT)
        conn.close()
        return result

    try:
        rows = conn.execute("""
            SELECT
                strftime('%Y-%m', thesis_date)            AS month,
                COUNT(*)                                   AS total,
                SUM(CASE WHEN outcome != 'OPEN' THEN 1 ELSE 0 END) AS resolved,
                SUM(CASE WHEN outcome  = 'OPEN' THEN 1 ELSE 0 END) AS open,
                -- direction accuracy
                SUM(CASE WHEN claude_correct = 1 THEN 1 ELSE 0 END) AS correct,
                SUM(CASE WHEN claude_correct = 0 THEN 1 ELSE 0 END) AS wrong,
                -- target / stop hit counts
                SUM(CASE WHEN hit_target_1 = 1 THEN 1 ELSE 0 END)  AS hit_target_1,
                SUM(CASE WHEN hit_stop     = 1
                         AND (hit_target_1 = 0 OR days_to_stop < days_to_target_1)
                         THEN 1 ELSE 0 END)                          AS hit_stop_first,
                -- averages
                ROUND(AVG(return_30d),        2)            AS avg_return_30d,
                ROUND(AVG(vs_target_1_pct),   2)            AS avg_vs_target_1_pct,
                ROUND(AVG(days_to_target_1),  1)            AS avg_days_to_target_1,
                -- traded
                SUM(CASE WHEN was_traded = 1 THEN 1 ELSE 0 END)     AS traded
            FROM thesis_outcomes
            GROUP BY month
            ORDER BY month DESC
        """).fetchall()

        months = []
        for r in rows:
            total     = r["total"]    or 0
            correct   = r["correct"]  or 0
            wrong     = r["wrong"]    or 0
            hit_t1    = r["hit_target_1"] or 0
            resolved  = r["resolved"] or 0

            dir_acc  = round(correct / (correct + wrong) * 100, 1) if (correct + wrong) > 0 else None
            t1_rate  = round(hit_t1  / resolved * 100, 1)          if resolved > 0           else None
            stop_rate= round((r["hit_stop_first"] or 0) / resolved * 100, 1) if resolved > 0 else None

            months.append({
                "month":                  r["month"],
                "total":                  total,
                "resolved":               resolved,
                "open":                   r["open"] or 0,
                "traded":                 r["traded"] or 0,
                "correct":                correct,
                "wrong":                  wrong,
                "direction_accuracy_pct": dir_acc,
                "hit_target_1":           hit_t1,
                "hit_stop_first":         r["hit_stop_first"] or 0,
                "target_hit_rate_pct":    t1_rate,
                "stop_hit_rate_pct":      stop_rate,
                "avg_return_30d":         r["avg_return_30d"],
                "avg_vs_target_1_pct":    r["avg_vs_target_1_pct"],
                "avg_days_to_target_1":   r["avg_days_to_target_1"],
            })

        # All-time summary
        all_total    = sum(m["total"]    for m in months)
        all_correct  = sum(m["correct"]  for m in months)
        all_wrong    = sum(m["wrong"]    for m in months)
        all_resolved = sum(m["resolved"] for m in months)
        all_hit_t1   = sum(m["hit_target_1"]   for m in months)
        all_hit_stop = sum(m["hit_stop_first"]  for m in months)
        ret30_vals   = [m["avg_return_30d"]      for m in months if m["avg_return_30d"]     is not None]
        vt1_vals     = [m["avg_vs_target_1_pct"] for m in months if m["avg_vs_target_1_pct"] is not None]

        def _wavg(vals, weights):
            pairs = [(v, w) for v, w in zip(vals, weights) if v is not None and w]
            return round(sum(v * w for v, w in pairs) / sum(w for _, w in pairs), 2) if pairs else None

        month_weights = [m["resolved"] for m in months]

        all_time = {
            "total":                  all_total,
            "resolved":               all_resolved,
            "correct":                all_correct,
            "wrong":                  all_wrong,
            "direction_accuracy_pct": round(all_correct / (all_correct + all_wrong) * 100, 1) if (all_correct + all_wrong) > 0 else None,
            "hit_target_1":           all_hit_t1,
            "hit_stop_first":         all_hit_stop,
            "target_hit_rate_pct":    round(all_hit_t1   / all_resolved * 100, 1) if all_resolved > 0 else None,
            "stop_hit_rate_pct":      round(all_hit_stop / all_resolved * 100, 1) if all_resolved > 0 else None,
            "avg_return_30d":         _wavg(ret30_vals, month_weights),
            "avg_vs_target_1_pct":    _wavg(vt1_vals,  month_weights),
        }

        conn.close()
        result = {
            "data_available": bool(months),
            "all_time":       all_time,
            "by_month":       months,
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("signals_accuracy error")
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


@app.get("/api/screeners/equity")
async def screeners_equity():
    """
    Multi-factor equity rankings with Quarter-Kelly sizing.
    Merges equity_signals CSV (composite_z, factor Z-scores) with equity_positions CSV
    (weight_pct, position_eur). Returns all tickers sorted by composite_z descending.
    """
    cache_key = "screeners_equity"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    signals_file = get_latest_signals_file()
    if signals_file is None:
        result = _no_data("no equity_signals CSV found")
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    try:
        df = pd.read_csv(signals_file, index_col=0)
        df.index.name = "ticker"
        df = df.reset_index()

        date_suffix = signals_file.stem.split("_")[-1]
        pos_file = SIGNALS_DIR / f"equity_positions_{date_suffix}.csv"
        pos_lookup: dict[str, Any] = {}
        if pos_file.exists():
            pos_df = pd.read_csv(pos_file, index_col=0)
            pos_df.index.name = "ticker"
            for t, p in pos_df.iterrows():
                pos_lookup[str(t).upper()] = p

        records = []
        for _, row in df.iterrows():
            ticker = str(row.get("ticker", "")).upper()
            p = pos_lookup.get(ticker)  # None if not found
            records.append({
                "ticker":             ticker,
                "rank":               _safe_int(row.get("rank")),
                "composite_z":        _safe_float(row.get("composite_z")),
                "momentum_12_1":      _safe_float(row.get("momentum_12_1_z")),
                "momentum_6_1":       _safe_float(row.get("momentum_6_1_z")),
                "mean_reversion_5d":  _safe_float(row.get("mean_rev_5d_z")),
                "volatility_quality": _safe_float(row.get("vol_quality_z")),
                "risk_adj_momentum":  _safe_float(row.get("risk_adj_mom_z")),
                "weight_pct":         _safe_float(p.get("weight_pct"))   if p is not None else None,
                "position_eur":       _safe_float(p.get("position_eur")) if p is not None else None,
            })

        records.sort(key=lambda x: x["composite_z"] or 0, reverse=True)
        result = {
            "data_available": True,
            "count":          len(records),
            "as_of":          date_suffix,
            "generated_at":   datetime.utcnow().isoformat() + "Z",
            "data":           records,
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("screeners_equity error")
        return _no_data(str(e))


@app.get("/api/screeners/crypto")
async def screeners_crypto():
    """
    Crypto monitoring signals from the most recent crypto_signals_YYYYMMDD.csv.
    Prices are converted from USD to EUR via fx_rates.
    BTC is always pinned at index 0; remaining tickers sorted by signal_score desc.
    Returns 404 if no file exists.
    """
    cache_key = "screeners_crypto"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    f = _latest_screener_file("crypto_signals")
    if f is None:
        return JSONResponse({"error": "no crypto signals file found"}, status_code=404)

    try:
        df = pd.read_csv(f)

        # ── normalise column names ────────────────────────────────────────────
        if df.columns[0] not in ("ticker", "Ticker"):
            df = df.rename(columns={df.columns[0]: "ticker"})

        def _norm_action(raw: str) -> str:
            up = str(raw).strip().upper()
            if up.startswith("SELL"):
                return "SELL"
            if up in ("HOLD", "REDUCE", "BUY"):
                return up
            return raw.strip()

        def _trend_str(v: Optional[float]) -> str:
            if v is None:
                return "NEUTRAL"
            return "UP" if v > 0 else ("DOWN" if v < 0 else "NEUTRAL")

        def _scale_score(adj: Optional[float]) -> float:
            """Map adjusted_signal [-1, +1] → [0, 100]."""
            if adj is None:
                return 50.0
            return round(max(0.0, min(100.0, (adj + 1.0) / 2.0 * 100.0)), 1)

        # ── determine BTC 200MA signal ────────────────────────────────────────
        btc_rows = df[df["ticker"] == "BTC-USD"]
        if not btc_rows.empty:
            btc_adj = _safe_float(btc_rows.iloc[0].get("adjusted_signal"), 0.0)
            btc_200ma_signal = "ACTIVE" if (btc_adj or 0.0) > 0 else "CASH"
        else:
            btc_200ma_signal = "CASH"

        # ── build ticker records ──────────────────────────────────────────────
        records: list[dict] = []
        for _, row in df.iterrows():
            price_usd = _safe_float(row.get("price"), 0.0) or 0.0
            adj_sig   = _safe_float(row.get("adjusted_signal"))
            trend_raw = _safe_float(row.get("trend_score"))
            vol_ann   = _safe_float(row.get("realized_vol_ann"), 0.0) or 0.0

            records.append({
                "ticker":       str(row.get("ticker", "")),
                "price_usd":    round(price_usd, 4),
                "price_eur":    _convert_to_eur(price_usd, "USD"),
                "signal_score": _scale_score(adj_sig),
                "trend":        _trend_str(trend_raw),
                "momentum":     _safe_float(row.get("momentum_score"), 0.0),
                "rsi":          _safe_float(row.get("rsi"), 50.0),
                "vol_pct":      round(vol_ann * 100.0, 2),
                "action":       _norm_action(str(row.get("action", "HOLD"))),
            })

        # ── sort: BTC pinned first, rest by signal_score desc ─────────────────
        btc_list   = [r for r in records if r["ticker"] == "BTC-USD"]
        other_list = [r for r in records if r["ticker"] != "BTC-USD"]
        other_list.sort(key=lambda x: x["signal_score"], reverse=True)
        tickers = btc_list + other_list

        # ── file mtime as generated_at ────────────────────────────────────────
        generated_at = datetime.utcfromtimestamp(f.stat().st_mtime).isoformat() + "Z"

        result = {
            "generated_at":      generated_at,
            "btc_200ma_signal":  btc_200ma_signal,
            "tickers":           tickers,
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("screeners_crypto error")
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

        _regime_str = mr.get("regime", "UNKNOWN")
        result = {
            "data_available":  True,
            "regime":          _regime_str,
            "score":           mr.get("score", 0),
            "size_multiplier": {"RISK_ON": 1.0, "TRANSITIONAL": 0.7, "RISK_OFF": 0.4}.get(
                                   _regime_str, 0.7),
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


# ==============================================================================
# SECTION 13: ACTION ZONES + AI ANALYSIS TRIGGER
# ==============================================================================

try:
    from trade_journal import compute_action_zones as _compute_action_zones
    _HAS_ACTION_ZONES = True
except Exception:
    _HAS_ACTION_ZONES = False

try:
    from fx_rates import get_eur_rate as _get_eur_rate, get_ticker_currency as _get_ticker_currency
    _HAS_FX = True
except Exception:
    _HAS_FX = False


@app.get("/api/ticker/{symbol}/action-zones")
async def ticker_action_zones(symbol: str):
    """
    Live action zones for a ticker: buy zone, stop, targets, ATR, RSI timing.
    R:R computed from buy zone midpoint (correct entry reference).
    Prices returned in both USD and EUR.
    """
    sym = symbol.upper()
    cache_key = f"action_zones_{sym}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    if not _HAS_ACTION_ZONES:
        return {"data_available": False, "error": "trade_journal not importable"}

    try:
        zones = await asyncio.get_event_loop().run_in_executor(
            None, _compute_action_zones, sym
        )
    except Exception as e:
        return {"data_available": False, "error": str(e)}

    if not zones:
        result = {"data_available": False, "error": f"No price data for {sym}"}
        _cache.set(cache_key, result, 300)
        return result

    # FX conversion
    fx_rate = 1.0
    ccy = "USD"
    try:
        if _HAS_FX:
            ccy = _get_ticker_currency(sym)
            fx_rate = _get_eur_rate(ccy) if ccy != "EUR" else 1.0
    except Exception:
        fx_rate = 1.0 / 1.09  # fallback

    def to_eur(v):
        return round(v / fx_rate, 2) if v is not None and fx_rate > 0 else None

    entry_mid_usd = (zones["buy_zone_low"] + zones["buy_zone_high"]) / 2
    entry_mid_eur = to_eur(entry_mid_usd)

    # Derive action from price vs zones
    price_usd = zones["current_price"]
    stop_usd   = zones["stop_loss"]
    bzl_usd    = zones["buy_zone_low"]
    bzh_usd    = zones["buy_zone_high"]
    t1_usd     = zones["target_1"]
    t2_usd     = zones["target_2"]

    if price_usd < stop_usd:
        action = "BELOW STOP — thesis invalidated"
        action_color = "red"
    elif bzl_usd <= price_usd <= bzh_usd:
        action = "IN BUY ZONE — valid entry, confirm catalyst"
        action_color = "green"
    elif price_usd < bzl_usd:
        action = "BELOW ZONE — wait for stabilization"
        action_color = "amber"
    elif price_usd >= t2_usd:
        action = "AT/ABOVE T2 — exit or trail stop"
        action_color = "blue"
    elif price_usd >= t1_usd:
        action = "AT/ABOVE T1 — take partial profits, move stop to entry"
        action_color = "blue"
    else:
        action = "ABOVE ZONE — wait for pullback to buy zone"
        action_color = "neutral"

    result = {
        "data_available":  True,
        "ticker":          sym,
        "currency":        ccy,
        "fx_rate":         round(fx_rate, 4),
        # USD prices (raw)
        "current_price":   round(price_usd, 2),
        "atr":             round(zones["atr"], 2),
        "atr_pct":         round(zones["atr_pct"] * 100, 1),
        "buy_zone_low":    round(bzl_usd, 2),
        "buy_zone_high":   round(bzh_usd, 2),
        "entry_mid":       round(entry_mid_usd, 2),
        "stop_loss":       round(stop_usd, 2),
        "target_1":        round(t1_usd, 2),
        "target_2":        round(t2_usd, 2),
        "rsi":             round(zones["rsi"], 1),
        "ema21":           round(zones["ema21"], 2),
        "ema50":           round(zones["ema50"], 2),
        "rr_t1":           round(zones["risk_reward_t1"], 2),
        "rr_t2":           round(zones["risk_reward_t2"], 2),
        "timing":          zones["timing"],
        "suggested_size_eur": zones["suggested_size_eur"],
        "action":          action,
        "action_color":    action_color,
        # EUR prices
        "eur": {
            "current":    to_eur(price_usd),
            "atr":        to_eur(zones["atr"]),
            "buy_low":    to_eur(bzl_usd),
            "buy_high":   to_eur(bzh_usd),
            "entry_mid":  entry_mid_eur,
            "stop":       to_eur(stop_usd),
            "t1":         to_eur(t1_usd),
            "t2":         to_eur(t2_usd),
        },
        # % from entry mid (correct reference)
        "pct": {
            "stop":       round((stop_usd - entry_mid_usd) / entry_mid_usd * 100, 1),
            "t1":         round((t1_usd  - entry_mid_usd) / entry_mid_usd * 100, 1),
            "t2":         round((t2_usd  - entry_mid_usd) / entry_mid_usd * 100, 1),
            "current":    round((price_usd - entry_mid_usd) / entry_mid_usd * 100, 1),
        },
    }
    _cache.set(cache_key, result, 15 * 60)  # 15-min cache (live data)
    return result


# Running analysis jobs: symbol → {"status", "started_at", "pid"}
_analysis_jobs: dict = {}


@app.post("/api/ticker/{symbol}/analyze")
async def ticker_analyze(symbol: str):
    """
    Trigger ai_quant.py --ticker SYMBOL as a background subprocess.
    Returns immediately. Poll /api/signals/ticker/{symbol} to see when thesis appears.
    """
    sym = symbol.upper()

    # If already running, return current status
    job = _analysis_jobs.get(sym)
    if job and job.get("status") == "running":
        proc = job.get("proc")
        if proc and proc.returncode is None:
            return {"status": "running", "symbol": sym, "started_at": job["started_at"]}
        else:
            _analysis_jobs[sym]["status"] = "done"

    ai_quant_path = BASE_DIR / "ai_quant.py"
    python_exec   = BASE_DIR / ".venv" / "bin" / "python"
    if not python_exec.exists():
        python_exec = sys.executable

    try:
        proc = await asyncio.create_subprocess_exec(
            str(python_exec), str(ai_quant_path), "--ticker", sym,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(BASE_DIR),
        )
        started = datetime.utcnow().isoformat() + "Z"
        _analysis_jobs[sym] = {"status": "running", "started_at": started, "proc": proc}
        log.info(f"Analysis started for {sym} (pid {proc.pid})")
        return {"status": "running", "symbol": sym, "started_at": started, "pid": proc.pid}
    except Exception as e:
        log.error(f"Failed to launch analysis for {sym}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ticker/{symbol}/analyze/status")
async def ticker_analyze_status(symbol: str):
    """Poll whether a background analysis job has completed."""
    sym = symbol.upper()
    job = _analysis_jobs.get(sym)
    if not job:
        return {"status": "idle", "symbol": sym}
    proc = job.get("proc")
    if proc and proc.returncode is None:
        return {"status": "running", "symbol": sym, "started_at": job["started_at"]}
    # Finished — clear cache so fresh thesis is fetched
    _cache._store.pop(f"signals_ticker_{sym}", None)
    return {"status": "done", "symbol": sym, "started_at": job.get("started_at")}


# ==============================================================================
# SECTION 14: TICKER INTELLIGENCE — SEC FILINGS, CONGRESS TRADES, EARNINGS
# ==============================================================================

_SEC_USER_AGENT = "SignalEngine/2.0 research@localhost"
_TTL_24H = 86_400

# Module-level caches for large datasets (don't use DataCache — these can be 10+ MB)
_edgar_cik_map: dict = {}
_edgar_cik_fetched_at: float = 0.0
_house_trades: list = []
_house_trades_fetched_at: float = 0.0
_senate_trades: list = []
_senate_trades_fetched_at: float = 0.0

_IMPORTANT_FORMS = {"10-K", "10-Q", "8-K", "DEF 14A", "S-1", "424B4", "SC 13G", "SC 13D"}


async def _fetch_edgar_cik_map() -> dict:
    global _edgar_cik_map, _edgar_cik_fetched_at
    if _edgar_cik_map and time.time() - _edgar_cik_fetched_at < _TTL_24H:
        return _edgar_cik_map
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                "https://www.sec.gov/files/company_tickers.json",
                headers={"User-Agent": _SEC_USER_AGENT},
            )
            data = r.json()
            _edgar_cik_map = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in data.values()}
            _edgar_cik_fetched_at = time.time()
    except Exception as e:
        log.warning(f"EDGAR CIK map fetch failed: {e}")
    return _edgar_cik_map


async def _fetch_house_trades() -> list:
    global _house_trades, _house_trades_fetched_at
    if _house_trades and time.time() - _house_trades_fetched_at < _TTL_24H:
        return _house_trades
    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.get(
                "https://house-stock-watcher-data.s3-us-east-2.amazonaws.com/data/all_transactions.json"
            )
            _house_trades = r.json()
            _house_trades_fetched_at = time.time()
    except Exception as e:
        log.warning(f"House trades fetch failed: {e}")
    return _house_trades


async def _fetch_senate_trades() -> list:
    global _senate_trades, _senate_trades_fetched_at
    if _senate_trades and time.time() - _senate_trades_fetched_at < _TTL_24H:
        return _senate_trades
    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.get(
                "https://senate-stock-watcher-data.s3-us-east-2.amazonaws.com/aggregate/all_transactions.json"
            )
            _senate_trades = r.json()
            _senate_trades_fetched_at = time.time()
    except Exception as e:
        log.warning(f"Senate trades fetch failed: {e}")
    return _senate_trades


@app.get("/api/ticker/{symbol}/sec-filings")
async def ticker_sec_filings(symbol: str):
    """Recent 10-K/10-Q/8-K filings from EDGAR for a ticker."""
    sym = symbol.upper()
    cache_key = f"sec_filings_{sym}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    cik_map = await _fetch_edgar_cik_map()
    cik = cik_map.get(sym)
    if not cik:
        result = {"data_available": False, "data": [], "error": f"CIK not found for {sym}"}
        _cache.set(cache_key, result, 300)
        return result

    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"https://data.sec.gov/submissions/CIK{cik}.json",
                headers={"User-Agent": _SEC_USER_AGENT},
            )
            sub = r.json()

        recent = sub.get("filings", {}).get("recent", {})
        forms  = recent.get("form", [])
        dates  = recent.get("filingDate", [])
        accs   = recent.get("accessionNumber", [])
        descs  = recent.get("primaryDocDescription", [])
        docs   = recent.get("primaryDocument", [])

        filings = []
        cik_int = int(cik)
        for form, date, acc, desc, doc in zip(forms, dates, accs, descs, docs):
            if form not in _IMPORTANT_FORMS:
                continue
            acc_clean = acc.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{doc}"
            filings.append({"form": form, "date": date, "description": desc or form, "url": url})
            if len(filings) >= 10:
                break

        result = {"data_available": bool(filings), "data": filings}
        _cache.set(cache_key, result, 6 * 3600)
        return result

    except Exception as e:
        log.warning(f"SEC filings failed for {sym}: {e}")
        result = {"data_available": False, "data": [], "error": str(e)}
        _cache.set(cache_key, result, 300)
        return result


@app.get("/api/ticker/{symbol}/congress-trades")
async def ticker_congress_trades(symbol: str):
    """Recent House + Senate stock disclosures for a ticker."""
    sym = symbol.upper()
    cache_key = f"congress_{sym}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    house_raw, senate_raw = await asyncio.gather(
        _fetch_house_trades(), _fetch_senate_trades()
    )

    trades: list = []

    if isinstance(house_raw, list):
        for t in house_raw:
            if (t.get("ticker") or "").upper().strip() == sym:
                trades.append({
                    "chamber": "House",
                    "member":  t.get("representative", "Unknown"),
                    "date":    t.get("transaction_date") or t.get("disclosure_date", ""),
                    "type":    t.get("type", ""),
                    "amount":  t.get("amount", ""),
                    "asset":   t.get("asset_description", ""),
                })

    if isinstance(senate_raw, list):
        for t in senate_raw:
            if (t.get("ticker") or "").upper().strip() == sym:
                trades.append({
                    "chamber": "Senate",
                    "member":  t.get("senator", "Unknown"),
                    "date":    t.get("transaction_date") or t.get("disclosure_date", ""),
                    "type":    t.get("type", ""),
                    "amount":  t.get("amount", ""),
                    "asset":   t.get("asset_description", ""),
                })

    trades.sort(key=lambda x: x.get("date") or "", reverse=True)
    trades = trades[:25]

    result = {"data_available": bool(trades), "data": trades}
    _cache.set(cache_key, result, 3600)
    return result


def _quarter_label(dt) -> str:
    """'Q4 '24' from a period-end date."""
    q = (dt.month - 1) // 3 + 1
    return f"Q{q} '{str(dt.year)[-2:]}"


@app.get("/api/ticker/{symbol}/earnings")
async def ticker_earnings(symbol: str):
    """
    Full earnings dataset:
      - next_earnings date + EPS/Revenue forward estimates
      - quarterly: last 8Q EPS (estimate, actual, surprise) + revenue actuals
      - annual: last 5Y revenue + diluted EPS
      - eps_growth_yoy
    """
    sym = symbol.upper()
    cache_key = f"earnings_{sym}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    quarterly: list = []
    annual: list = []
    next_date: Optional[str] = None
    next_eps: Optional[dict] = None
    next_revenue: Optional[dict] = None
    eps_growth: Optional[float] = None

    try:
        tk = yf.Ticker(sym)

        # ── Calendar → next quarter estimates ──────────────────────────────
        try:
            cal = tk.calendar or {}
            raw_dates = cal.get("Earnings Date")
            if raw_dates:
                dates_list = list(raw_dates) if hasattr(raw_dates, "__iter__") and not isinstance(raw_dates, str) else [raw_dates]
                if dates_list:
                    d0 = dates_list[0]
                    next_date = d0.strftime("%Y-%m-%d") if hasattr(d0, "strftime") else str(d0)[:10]
            ea_avg = _safe_float(cal.get("Earnings Average"), None)
            if ea_avg is not None:
                next_eps = {
                    "avg":  ea_avg,
                    "high": _safe_float(cal.get("Earnings High"), None),
                    "low":  _safe_float(cal.get("Earnings Low"), None),
                }
            ra_avg = _safe_float(cal.get("Revenue Average"), None)
            if ra_avg is not None:
                next_revenue = {
                    "avg":  ra_avg,
                    "high": _safe_float(cal.get("Revenue High"), None),
                    "low":  _safe_float(cal.get("Revenue Low"), None),
                }
        except Exception:
            pass

        # ── Quarterly EPS from earnings_dates ───────────────────────────────
        eps_map: dict = {}   # ann_datetime → {eps_estimate, eps_actual, surprise_pct}
        try:
            ed = tk.earnings_dates
            if ed is not None and not ed.empty:
                ed_reported = ed[ed["Reported EPS"].notna()].head(12)
                for ann_ts, row in ed_reported.iterrows():
                    ann_dt = ann_ts.replace(tzinfo=None) if hasattr(ann_ts, "replace") else ann_ts
                    eps_map[ann_dt] = {
                        "eps_estimate": _safe_float(row.get("EPS Estimate"), None),
                        "eps_actual":   _safe_float(row.get("Reported EPS"), None),
                        "surprise_pct": _safe_float(row.get("Surprise(%)"), None),
                    }
        except Exception:
            pass

        # ── Forward revenue estimates (0q, +1q) keyed by analyst period ────
        # yfinance revenue_estimate only gives forward quarters, not historical.
        # We store them by period label for potential future matching.
        fwd_rev_estimates: dict = {}  # period_label → avg estimate
        try:
            re_df = tk.revenue_estimate
            if re_df is not None and not re_df.empty and "avg" in re_df.columns:
                for period_label, row in re_df.iterrows():
                    avg = _safe_float(row.get("avg"), None)
                    if avg is not None:
                        fwd_rev_estimates[str(period_label)] = avg
        except Exception:
            pass

        # ── Quarterly income stmt → revenue per period ──────────────────────
        try:
            qi = tk.quarterly_income_stmt
            if qi is not None and not qi.empty:
                rev_row = qi.loc["Total Revenue"] if "Total Revenue" in qi.index else None
                eps_row = qi.loc["Diluted EPS"] if "Diluted EPS" in qi.index else None
                periods = list(qi.columns)  # newest first

                for period_col in periods[:8]:
                    period_dt = pd.Timestamp(period_col).normalize()
                    rev = _safe_float(rev_row[period_col] if rev_row is not None else None, None)
                    qi_eps = _safe_float(eps_row[period_col] if eps_row is not None else None, None)

                    # Match to eps_map by announcement date within [0, 90] days after period end
                    matched_eps: dict = {}
                    for ann_dt, eps_data in eps_map.items():
                        delta = (ann_dt.normalize() - period_dt).days
                        if 0 <= delta <= 90:
                            matched_eps = eps_data
                            break

                    eps_actual    = matched_eps.get("eps_actual") or qi_eps
                    eps_estimate  = matched_eps.get("eps_estimate")
                    surprise_pct  = matched_eps.get("surprise_pct")
                    eps_beat: Optional[bool] = None
                    if eps_actual is not None and eps_estimate is not None:
                        eps_beat = eps_actual >= eps_estimate

                    # Revenue beat — historical consensus unavailable from yfinance free.
                    # revenue_estimate only covers forward quarters (0q/+1q).
                    rev_estimate: Optional[float] = None
                    rev_beat: Optional[bool] = None
                    if rev is not None and rev_estimate is not None:
                        rev_beat = rev >= rev_estimate

                    quarterly.append({
                        "label":            _quarter_label(period_dt),
                        "period":           str(period_col)[:10],
                        "eps_estimate":     eps_estimate,
                        "eps_actual":       eps_actual,
                        "surprise_pct":     surprise_pct,
                        "revenue":          rev,
                        "revenue_estimate": rev_estimate,
                        "beat":             eps_beat,
                        "revenue_beat":     rev_beat,
                    })

                quarterly.reverse()  # oldest → newest for chart left→right

                # YoY EPS growth: compare most recent Q vs same Q 1 year prior
                if len(quarterly) >= 5:
                    latest = quarterly[-1]["eps_actual"]
                    year_ago = quarterly[-5]["eps_actual"]
                    if latest is not None and year_ago not in (None, 0.0):
                        eps_growth = round((latest - year_ago) / abs(year_ago) * 100, 1)
        except Exception as ex:
            log.debug(f"Quarterly income stmt failed for {sym}: {ex}")

        # ── Annual income stmt → 5-year view ───────────────────────────────
        try:
            inc = tk.income_stmt
            if inc is not None and not inc.empty:
                rev_row = inc.loc["Total Revenue"] if "Total Revenue" in inc.index else None
                eps_row = inc.loc["Diluted EPS"]   if "Diluted EPS"   in inc.index else None
                ni_row  = inc.loc["Net Income"]    if "Net Income"    in inc.index else None

                for col in list(inc.columns)[:5]:
                    dt = pd.Timestamp(col)
                    rev = _safe_float(rev_row[col] if rev_row is not None else None, None)
                    eps = _safe_float(eps_row[col] if eps_row is not None else None, None)
                    ni  = _safe_float(ni_row[col]  if ni_row  is not None else None, None)
                    if rev is None and eps is None:
                        continue
                    annual.append({
                        "label":      f"FY{dt.year}",
                        "year":       dt.year,
                        "revenue":    rev,
                        "eps":        eps,
                        "net_income": ni,
                    })

                annual.reverse()  # oldest → newest
        except Exception as ex:
            log.debug(f"Annual income stmt failed for {sym}: {ex}")

    except Exception as e:
        log.warning(f"Earnings fetch failed for {sym}: {e}")

    result = {
        "data_available":  bool(quarterly or annual or next_date),
        "next_earnings":   next_date,
        "next_eps":        next_eps,
        "next_revenue":    next_revenue,
        "eps_growth_yoy":  eps_growth,
        "quarterly":       quarterly,
        "annual":          annual,
    }
    _cache.set(cache_key, result, 4 * 3600)
    return result
