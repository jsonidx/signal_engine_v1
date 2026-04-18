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

from fastapi import FastAPI, Query, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse

try:
    from dashboard.api.auth import (
        get_current_user, get_optional_user, AuthUser,
        create_api_key, revoke_api_key,
    )
    _AUTH_AVAILABLE = True
except ImportError:
    _AUTH_AVAILABLE = False
    # Fallback stubs so routes compile without the auth module
    async def get_current_user():  # type: ignore[misc]
        return None
    async def get_optional_user():  # type: ignore[misc]
        return None
    class AuthUser:  # type: ignore[misc]
        user_id = "anonymous"

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
    from options_flow import compute_max_pain as _compute_max_pain
except ImportError:
    _compute_max_pain = None  # type: ignore[assignment]

try:
    from config import (
        PORTFOLIO_NAV,
        EQUITY_ALLOCATION,
        CRYPTO_ALLOCATION,
    )
except ImportError:
    PORTFOLIO_NAV = 0
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
REGIME_CACHE     = DATA_DIR / "regime_cache.json"
SECTOR_CACHE     = DATA_DIR / "sector_cache.json"
PAPER_TRADES_DB  = BASE_DIR / "paper_trades.db"
TRADE_JOURNAL_DB = BASE_DIR / "trade_journal.db"
AI_QUANT_DB      = BASE_DIR / "ai_quant_cache.db"

# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Signal Engine API",
    version="2.0",
    description="JSON API for the Signal Engine quantitative trading system",
)

_CORS_ORIGINS_DEFAULT = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173"
_CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", _CORS_ORIGINS_DEFAULT).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
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

class _PGConn:
    """
    Thin wrapper around psycopg2 that adds SQLite-compatible conn.execute() so
    existing dashboard code doesn't need to be rewritten call-site by call-site.
    execute() returns the cursor (with fetchone/fetchall). RealDictCursor is used
    so rows behave like dicts.
    """
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        import psycopg2.extras
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return cur

    def cursor(self, **kw):
        import psycopg2.extras
        return self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def _db_connect() -> _PGConn:
    """Return a Supabase PostgreSQL connection wrapped for SQLite-compat API."""
    sys.path.insert(0, str(BASE_DIR))
    from utils.db import get_connection
    return _PGConn(get_connection())


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


def _fundamentals_lookup(ticker: str) -> dict:
    """Return target_mean, analyst_count, analyst_rating from latest fundamental CSV."""
    try:
        f = _latest_screener_file("fundamental_")
        if f is None:
            return {}
        df = pd.read_csv(f)
        if "ticker" not in df.columns:
            return {}
        row = df[df["ticker"].str.upper() == ticker.upper()]
        if row.empty:
            return {}
        r = row.iloc[0]
        return {
            "target_mean":    _safe_float(r.get("target_mean")),
            "analyst_count":  _safe_int(r.get("analyst_count")),
            "analyst_rating": _safe_float(r.get("analyst_rating")),
        }
    except Exception:
        return {}


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
        "regime_cache.json": REGIME_CACHE,
        "signals_output/":   SIGNALS_DIR,
    }
    for name, path in expected.items():
        if not Path(path).exists():
            log.warning("MISSING: %s → %s", name, path)
        else:
            log.info("OK: %s", name)
    try:
        conn = _db_connect()
        conn.close()
        log.info("OK: Supabase connection")
    except Exception as e:
        log.warning("MISSING: Supabase connection — %s", e)
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
    conn = _db_connect()
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

        # Open positions count + deployed capital
        open_pos = 0
        deployed_eur = 0.0
        tj_conn = _db_connect()
        if tj_conn:
            row = tj_conn.execute(
                "SELECT COUNT(*) as cnt, COALESCE(SUM(size_eur), 0) as total"
                " FROM trades WHERE action='BUY' AND status='open'"
            ).fetchone()
            open_pos = row["cnt"] if row else 0
            deployed_eur = float(row["total"]) if row else 0.0
            tj_conn.close()

        # Real NAV = cash on hand + deployed capital (entry values of open positions)
        cash_eur, _ = _get_cash_eur(conn)
        real_nav = cash_eur + deployed_eur if (cash_eur + deployed_eur) > 0 else PORTFOLIO_NAV

        if df.empty or len(df) < 2:
            conn.close()
            return {
                "data_available": True,
                "total_value_eur": real_nav,
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
        total_value = real_nav

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
    conn = _db_connect()
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
        conn2 = _db_connect()
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
    conn = _db_connect()
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


@app.get("/api/portfolio/sparklines")
async def portfolio_sparklines():
    """
    5-day closing-price series for all open positions.
    Returns {ticker: [p0, p1, p2, p3, p4]} — oldest to newest.
    Used for inline position sparklines on the Portfolio page.
    """
    cache_key = "portfolio_sparklines"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    conn = _db_connect()
    if conn is None:
        return {}

    try:
        rows = conn.execute(
            "SELECT ticker FROM trades WHERE action='BUY' AND status='open'"
        ).fetchall()
        conn.close()
        tickers = [r["ticker"] for r in rows]
    except Exception:
        return {}

    if not tickers:
        return {}

    try:
        import yfinance as yf
        raw = yf.download(
            tickers, period="7d", interval="1d",
            auto_adjust=True, progress=False, threads=True,
        )
        result: dict = {}
        if raw.empty:
            return {}

        if isinstance(raw.columns, pd.MultiIndex):
            for t in tickers:
                try:
                    closes = raw["Close"][t].dropna().tolist()[-5:]
                    result[t] = [round(float(p), 4) for p in closes]
                except Exception:
                    pass
        else:
            t = tickers[0]
            closes = raw["Close"].dropna().tolist()[-5:]
            result[t] = [round(float(p), 4) for p in closes]

        _cache.set(cache_key, result, TTL_MEDIUM)
        return result
    except Exception as e:
        log.warning(f"sparklines error: {e}")
        return {}


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


def _ensure_trades_table(conn) -> None:
    """Tables exist in Supabase — add any missing columns idempotently."""
    cur = conn.cursor()
    for col, defn in [
        ("direction",       "TEXT"),
        ("price_eur",       "REAL"),
        ("currency",        "TEXT"),
        ("fx_rate",         "REAL"),
        ("close_date",      "TEXT"),
        ("close_price",     "REAL"),
        ("close_price_eur", "REAL"),
        ("close_currency",  "TEXT"),
        ("close_fx_rate",   "REAL"),
        ("pnl_eur",         "REAL"),
    ]:
        try:
            cur.execute(f"ALTER TABLE trades ADD COLUMN {col} {defn}")
            conn.commit()
        except Exception:
            conn.rollback()  # Must rollback or PostgreSQL keeps the connection in aborted state


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
    sell_price:     float
    currency:       str   = "USD"
    shares_to_sell: Optional[float] = None   # None or 0 = full close


@app.post("/api/portfolio/positions")
async def add_position(req: AddPositionRequest):
    """Manually insert an open position into trade_journal.db."""
    if req.entry_price <= 0 or req.size_eur <= 0:
        raise HTTPException(status_code=400, detail="entry_price and size_eur must be > 0")
    if req.currency not in ("EUR", "USD"):
        raise HTTPException(status_code=400, detail="currency must be EUR or USD")
    conn = _db_connect()
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
                 notes, action, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'BUY', 'open', NOW())
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
    """
    Close (fully or partially) the most recent open position for ticker.
    If shares_to_sell is provided and less than total shares, a partial close is performed:
      - The open row's shares/size_eur are reduced.
      - A new closed row is inserted for the sold portion with its P&L.
    """
    if req.sell_price <= 0:
        raise HTTPException(status_code=400, detail="sell_price must be > 0")
    if req.currency not in ("EUR", "USD"):
        raise HTTPException(status_code=400, detail="currency must be EUR or USD")
    conn = _db_connect()
    try:
        _ensure_trades_table(conn)
        sym = ticker.upper().strip()
        row = conn.execute(
            "SELECT * FROM trades WHERE ticker = %s AND action = 'BUY' AND status = 'open' ORDER BY date DESC LIMIT 1",
            (sym,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"No open position for {sym}")

        eur_usd         = _get_eur_usd_rate()
        close_price_eur = _to_eur(req.sell_price, req.currency, eur_usd)
        entry_price_eur = row["price_eur"] if row["price_eur"] else _to_eur(row["price"], row["currency"] or "USD", row["fx_rate"] or 1.08)
        total_shares    = row["shares"] or (row["size_eur"] / entry_price_eur if entry_price_eur > 0 else 0)
        direction       = (row["direction"] or "LONG").upper()
        today           = datetime.utcnow().strftime("%Y-%m-%d")

        # Determine how many shares to sell
        sell_shares = req.shares_to_sell if (req.shares_to_sell and req.shares_to_sell > 0) else total_shares
        sell_shares = min(sell_shares, total_shares)  # can't sell more than held
        partial     = sell_shares < total_shares

        # P&L for the sold portion
        if direction == "LONG":
            pnl_eur = (close_price_eur - entry_price_eur) * sell_shares
        else:
            pnl_eur = (entry_price_eur - close_price_eur) * sell_shares

        if partial:
            # Reduce remaining open position
            remaining_shares  = total_shares - sell_shares
            remaining_size_eur = remaining_shares * entry_price_eur
            conn.execute(
                "UPDATE trades SET shares = %s, size_eur = %s WHERE id = %s",
                (remaining_shares, remaining_size_eur, row["id"]),
            )
            # Insert a closed record for the sold portion
            conn.execute("""
                INSERT INTO trades
                    (ticker, direction, date, price, price_eur, size_eur, shares,
                     currency, fx_rate, signal_composite, stop_loss, target_1, target_2,
                     notes, action, status, created_at,
                     close_date, close_price, close_price_eur, close_currency, close_fx_rate, pnl_eur)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'BUY','closed',NOW(),
                        %s,%s,%s,%s,%s,%s)
            """, (
                sym, direction, row["date"], row["price"], entry_price_eur,
                sell_shares * entry_price_eur, sell_shares,
                row["currency"] or "USD", row["fx_rate"] or eur_usd,
                row["signal_composite"], row["stop_loss"], row["target_1"], row["target_2"], row["notes"],
                today, req.sell_price, close_price_eur, req.currency.upper(), eur_usd, pnl_eur,
            ))
        else:
            # Full close — update the existing row
            conn.execute("""
                UPDATE trades SET
                    status = 'closed', close_date = %s,
                    close_price = %s, close_price_eur = %s,
                    close_currency = %s, close_fx_rate = %s, pnl_eur = %s
                WHERE id = %s
            """, (today, req.sell_price, close_price_eur, req.currency.upper(), eur_usd, pnl_eur, row["id"]))

        conn.commit()
        log.info("Position %s %s @ %.4f %s → P&L %.2f EUR (shares=%.4f, partial=%s)",
                 "partial-sold" if partial else "sold", sym,
                 req.sell_price, req.currency, pnl_eur, sell_shares, partial)
        return {
            "ok": True, "ticker": sym, "partial": partial,
            "shares_sold": round(sell_shares, 6),
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
    conn = _db_connect()
    try:
        result = conn.execute(
            "UPDATE trades SET status = 'closed' WHERE ticker = %s AND action = 'BUY' AND status = 'open'",
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
    conn = _db_connect()
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

def _ensure_cash_table(conn) -> None:
    """portfolio_settings table exists in Supabase — no-op."""
    pass


def _get_cash_eur(conn) -> tuple[float, str | None]:
    """Return (cash_eur, updated_at) from portfolio_settings, or (0.0, None)."""
    cur = conn.cursor()
    cur.execute("SELECT value, updated_at FROM portfolio_settings WHERE key = 'cash_eur'")
    row = cur.fetchone()
    if row is None:
        return 0.0, None
    return float(row['value']), row.get('updated_at')


@app.get("/api/portfolio/cash")
async def get_cash():
    """Return the manually-set cash balance from Supabase portfolio_settings."""
    conn = _db_connect()
    try:
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

    conn = _db_connect()
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
            VALUES ('cash_eur', %s, %s)
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
                         fundamentals, cross_asset}
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
        conn = _db_connect()
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
            dp_signal = dp_row.get("signal", "NEUTRAL") if dp_row else "NEUTRAL"
            dp_zscore = _safe_float(dp_row.get("short_ratio_zscore"), None)

            # fundamentals: from fundamentals CSV (operating_margin, earnings growth)
            fu_row = fu_lookup.get(ticker, {})
            fu_cc = sigs.get("fundamentals") or {}
            fu_pct = _safe_float(fu_cc.get("fundamental_score_pct"), None)
            if fu_pct is None:
                # derive a simple score from earnings_growth_yoy if available
                eg = _safe_float(fu_row.get("earnings_growth_yoy"), None)
                fu_pct = 50.0 + min(40.0, max(-40.0, (eg or 0) * 100)) if eg is not None else 50.0
            fu_score = _normalize_score(fu_pct, 0.0, 100.0)

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
                "dark_pool_signal":        dp_signal,
                "dark_pool_zscore":        round(dp_zscore, 3) if dp_zscore is not None else None,
                "fundamentals":            round(fu_score, 3),
                "pre_resolved_direction":  direction,
                "signal_agreement_score":  _safe_float(cc.get("agreement")),
            })

        # Sort by agreement score descending (most actionable first)
        heatmap.sort(key=lambda r: r["signal_agreement_score"] or 0.0, reverse=True)

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

    conn = _db_connect()
    if conn is None:
        return _no_data("ai_quant_cache.db not found")

    try:
        row = conn.execute("""
            SELECT * FROM thesis_cache
            WHERE ticker = %s
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
        sq   = sigs.get("squeeze") or sigs.get("catalyst") or {}
        vp   = sigs.get("volume_profile") or {}


        # Build module scores dict expected by frontend ModuleMiniHeatmap
        # signal_engine: composite_z from equity_signals CSV (authoritative); no fallback
        # squeeze: squeeze_score_100 is 0–100 → normalise to [-1, 1] via (x-50)/50
        # fundamentals: fundamental_score_pct is 0–100 → same normalisation
        # cross_asset: string signal → numeric score
        sq_raw   = sigs.get("squeeze") or sigs.get("catalyst") or {}
        sq_score = _safe_float(sq_raw.get("squeeze_score_100") or sq_raw.get("short_squeeze_score"))
        fu_raw   = sigs.get("fundamentals") or {}
        fu_score = _safe_float(fu_raw.get("fundamental_score_pct") or fu_raw.get("composite_score"))
        modules = {
            "signal_engine": _equity_signals_composite_z(ticker),
            "squeeze":        round((sq_score - 50) / 50, 3) if sq_score is not None else None,
            "options":        _normalize_score(_safe_float(of.get("heat_score"), 50), 0, 100),
            "dark_pool":      _normalize_score(_safe_float(dp.get("dark_pool_score") or dp.get("score"), 50), 0, 100),
            "fundamentals":   round((fu_score - 50) / 50, 3) if fu_score is not None else None,
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
            "prob_combined":      data.get("prob_combined"),
            "prob_technical":     data.get("prob_technical"),
            "prob_options":       data.get("prob_options"),
            "prob_catalyst":      data.get("prob_catalyst"),
            "prob_news":          data.get("prob_news"),
            "time_horizon":       data.get("time_horizon"),
            "data_quality":       data.get("data_quality"),
            "model_used":         data.get("model_used"),
            "cost_usd":           data.get("cost_usd"),
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
            "iv_history_days":   _safe_int(of.get("iv_history_days")),
            "expected_move_pct": _safe_float(of.get("expected_move_pct")),
            "put_call_ratio":    _safe_float(of.get("pc_ratio") or of.get("put_call_ratio")),
            # ── Dark pool ─────────────────────────────────────────────────────
            "dark_pool_score":     _safe_float(dp.get("dark_pool_score") or dp.get("score")),
            "short_ratio_trend":   _normalise_dp_trend(dp.get("short_ratio_trend") or dp.get("trend")),
            "dark_pool_intensity": _normalise_dp_intensity(dp.get("dark_pool_intensity") or dp.get("intensity") or dp.get("off_exchange_pct")),
            # ── Raw nested (kept for backward compat) ─────────────────────────
            "ai_thesis":     {
                "direction": data.get("direction"), "conviction": data.get("conviction"),
                "thesis": data.get("thesis"), "signal_agreement_score": data.get("signal_agreement_score"),
            },
            "entry_zone":    {"low": data.get("entry_low"), "high": data.get("entry_high")},
            "targets":       [data.get("target_1"), data.get("target_2")],
            "signals":       sigs,
            "dark_pool":     dp,
            "squeeze":       sq,
            "volume_profile": vp,
            "options_heat":  of,
        }

        # ── Max pain (live yfinance call, cached per ticker separately) ───────
        mp_cache_key = f"max_pain:{ticker}"
        mp = _cache.get(mp_cache_key)
        if mp is None:
            try:
                mp = _compute_max_pain(ticker) if _compute_max_pain is not None else None
            except Exception:
                mp = None
            _cache.set(mp_cache_key, mp or {}, TTL_SHORT)
        if mp:
            result["max_pain_strike"]      = _safe_float(mp.get("max_pain_strike"))
            result["max_pain_distance_pct"]= _safe_float(mp.get("distance_pct"))
            result["max_pain_expiry"]      = mp.get("expiry")
            result["max_pain_days_to_expiry"] = _safe_int(mp.get("days_to_expiry"))
        else:
            result["max_pain_strike"]         = None
            result["max_pain_distance_pct"]   = None
            result["max_pain_expiry"]         = None
            result["max_pain_days_to_expiry"] = None

        # ── Analyst target from fundamentals CSV ──────────────────────────────
        fund = _fundamentals_lookup(ticker)
        result["target_mean"]    = fund.get("target_mean")
        result["analyst_count"]  = fund.get("analyst_count")
        result["analyst_rating"] = fund.get("analyst_rating")

        # ── ADV 20d (average daily volume, 20-day) — live yfinance, cached ────
        adv_cache_key = f"adv_20d:{ticker}"
        adv_cached = _cache.get(adv_cache_key)
        if adv_cached is not None:
            result["adv_20d"] = adv_cached.get("adv_20d")
        else:
            try:
                hist = yf.Ticker(ticker).history(period="1mo")
                adv_20d = int(hist["Volume"].tail(20).mean()) if len(hist) >= 20 else None
            except Exception:
                adv_20d = None
            _cache.set(adv_cache_key, {"adv_20d": adv_20d}, TTL_SHORT)
            result["adv_20d"] = adv_20d

        result = _json_safe(result)
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

    conn = _db_connect()
    seen: set = set()
    tickers = []

    if conn is not None:
        try:
            rows = conn.execute("""
                SELECT tc.ticker, tc.date, tc.direction, tc.conviction, tc.signal_agreement_score,
                       tc.time_horizon, tc.data_quality, tc.thesis, tc.bull_probability,
                       tc.bear_probability, tc.neutral_probability, tc.created_at,
                       tc.entry_low, tc.entry_high, tc.target_1, tc.target_2, tc.stop_loss,
                       ss.rank AS equity_rank
                FROM thesis_cache tc
                LEFT JOIN (
                    SELECT ticker, rank
                    FROM screener_signals
                    WHERE date = (SELECT MAX(date) FROM screener_signals)
                ) ss ON ss.ticker = tc.ticker
                WHERE tc.ticker NOT IN (
                    SELECT ticker FROM blacklist
                    WHERE expires_at IS NULL OR expires_at > NOW()
                )
                ORDER BY tc.date DESC, tc.created_at DESC
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
                    "current_price":          fd.get("price"),
                    "date":                   r["date"],
                    "created_at":             r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else r["created_at"],
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
                    "equity_rank":            r["equity_rank"],
                })
        except Exception:
            log.exception("deepdive_tickers: thesis_cache read error")

    # ── Always include open positions from trade_journal ─────────────────────────
    tj_conn = _db_connect()
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
                        "current_price":          fd.get("price"),
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
                "current_price":          fd.get("price"),
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

    # Enrich with live prices (yfinance batch, 5-min cache)
    if tickers:
        all_syms = [t["ticker"] for t in tickers]
        live_prices = _fetch_current_prices(all_syms, cache_ttl=TTL_SHORT)
        for t in tickers:
            lp = live_prices.get(t["ticker"])
            if lp is not None:
                t["current_price"] = lp

    result = {"data_available": bool(tickers), "count": len(tickers), "data": tickers}
    _cache.set(cache_key, result, TTL_SHORT)
    return result


@app.get("/api/deepdive/live-zones")
async def deepdive_live_zones():
    """Batch live buy zones for all analyzed tickers. Powers zone-overlap filter."""
    cache_key = "deepdive_live_zones"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    if not _HAS_ACTION_ZONES:
        return {"data_available": False, "zones": {}}

    conn = _db_connect()
    analyzed: list[str] = []
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT DISTINCT ticker FROM thesis_cache WHERE entry_low IS NOT NULL AND entry_high IS NOT NULL"
            ).fetchall()
            conn.close()
            analyzed = [r["ticker"] for r in rows]
        except Exception:
            pass

    if not analyzed:
        out = {"data_available": True, "zones": {}}
        _cache.set(cache_key, out, TTL_SHORT)
        return out

    loop = asyncio.get_event_loop()

    async def _zone_for(ticker: str):
        ck = f"action_zones_{ticker}"
        cached = _cache.get(ck)
        if cached and cached.get("data_available") and "buy_zone_low" in cached:
            return ticker, float(cached["buy_zone_low"]), float(cached["buy_zone_high"])
        try:
            z = await loop.run_in_executor(None, _compute_action_zones, ticker)
            if z:
                return ticker, float(z["buy_zone_low"]), float(z["buy_zone_high"])
        except Exception:
            pass
        return ticker, None, None

    results = await asyncio.gather(*[_zone_for(t) for t in analyzed], return_exceptions=True)

    zones: dict = {}
    for r in results:
        if isinstance(r, Exception):
            continue
        ticker, low, high = r
        if low is not None and high is not None:
            zones[ticker] = {"buy_zone_low": round(low, 2), "buy_zone_high": round(high, 2)}

    out = {"data_available": True, "zones": zones}
    _cache.set(cache_key, out, TTL_SHORT)
    return out


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

    conn = _db_connect()
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
            WHERE o.thesis_date >= %s
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

    conn = _db_connect()
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
                TO_CHAR(thesis_date::date, 'YYYY-MM')      AS month,
                COUNT(*)                                    AS total,
                SUM(CASE WHEN outcome != 'OPEN' THEN 1 ELSE 0 END) AS resolved,
                SUM(CASE WHEN outcome  = 'OPEN' THEN 1 ELSE 0 END) AS open,
                -- direction accuracy (claude_correct is integer 0/1/NULL)
                SUM(CASE WHEN claude_correct = 1 THEN 1 ELSE 0 END) AS correct,
                SUM(CASE WHEN claude_correct = 0 THEN 1 ELSE 0 END) AS wrong,
                -- target / stop hit counts (hit_* are boolean)
                SUM(CASE WHEN hit_target_1 IS TRUE THEN 1 ELSE 0 END)  AS hit_target_1,
                SUM(CASE WHEN hit_stop IS TRUE
                         AND (hit_target_1 IS NOT TRUE OR days_to_stop < days_to_target_1)
                         THEN 1 ELSE 0 END)                              AS hit_stop_first,
                -- averages
                ROUND(AVG(return_30d)::numeric,       2)   AS avg_return_30d,
                ROUND(AVG(vs_target_1_pct)::numeric,  2)   AS avg_vs_target_1_pct,
                ROUND(AVG(days_to_target_1)::numeric, 1)   AS avg_days_to_target_1,
                -- traded (was_traded is integer 0/1)
                SUM(CASE WHEN was_traded = 1 THEN 1 ELSE 0 END)         AS traded
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
            "as_of":         datetime.utcfromtimestamp(f.stat().st_mtime).isoformat() + "Z",
            "count":         len(records),
            "data":          records,
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("screeners_squeeze error")
        return _no_data(str(e))


@app.get("/api/screeners/redflags")
async def screeners_redflags(min_score: int = Query(0, ge=0)):
    """
    Accounting and behavioral red flags from the most recent red_flag_screener run.

    Returns rows sorted by red_flag_score descending.  Rows with risk_level='CLEAN'
    are included so the caller can filter; use min_score to restrict to flagged tickers.
    Cached for 5 minutes (TTL_SHORT).
    """
    cache_key = f"screeners_redflags:{min_score}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    f = _latest_screener_file("red_flags")
    if f is None:
        result = _no_data("no red_flags CSV found — run Step 8b (red_flag_screener.py)")
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    try:
        df = pd.read_csv(f)

        if "red_flag_score" in df.columns:
            df = df[df["red_flag_score"].fillna(0) >= min_score]
            df = df.sort_values("red_flag_score", ascending=False)

        records = []
        for _, row in df.iterrows():
            records.append({
                "ticker":             str(row.get("ticker", "")),
                "red_flag_score":     _safe_int(row.get("red_flag_score")) or 0,
                "risk_level":         str(row.get("risk_level") or "CLEAN"),
                "top_flag":           str(row.get("top_flag") or ""),
                "data_quality":       str(row.get("data_quality") or ""),
                "gaap_score":         _safe_int(row.get("gaap_score")) or 0,
                "accruals_score":     _safe_int(row.get("accruals_score")) or 0,
                "accruals_ratio":     _safe_float(row.get("accruals_ratio")),
                "payout_score":       _safe_int(row.get("payout_score")) or 0,
                "payout_ratio_fcf":   _safe_float(row.get("payout_ratio_fcf")),
                "rev_quality_score":  _safe_int(row.get("rev_quality_score")) or 0,
                "restatement_score":  _safe_int(row.get("restatement_score")) or 0,
            })

        # Extract date from filename (red_flags_YYYYMMDD.csv)
        stem = f.stem  # e.g. "red_flags_20260405"
        as_of = stem.split("_")[-1] if "_" in stem else None

        result = {
            "data_available": True,
            "source_file":    f.name,
            "as_of":          as_of,
            "count":          len(records),
            "generated_at":   datetime.utcnow().isoformat() + "Z",
            "data":           _json_safe(records),
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("screeners_redflags error")
        return _no_data(str(e))


@app.get("/api/screeners/fundamentals")
async def screeners_fundamentals(
    min_composite:          float = Query(0.0,  ge=0.0,  le=100.0, description="Min composite score (0–100)"),
    max_pe_forward:         float = Query(999.0, description="Max forward PE (use 999 to disable)"),
    min_revenue_growth:     float = Query(-99.0, description="Min revenue growth YoY (0.1 = 10%)"),
    min_operating_margin:   float = Query(-99.0, description="Min operating margin (0.10 = 10%)"),
):
    """
    Fundamental analysis screener from the most recent fundamental_*.csv.

    Returns up to 500 rows sorted by composite score descending.
    Supports optional server-side pre-filtering via query params.
    Client-side filtering for ticker search and preset quick-filters
    is handled in the frontend.
    Cached for 5 minutes (TTL_SHORT).
    """
    cache_key = f"screeners_fundamentals:{min_composite}:{max_pe_forward}:{min_revenue_growth}:{min_operating_margin}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    f = _latest_screener_file("fundamental_")
    if f is None:
        result = _no_data("no fundamental CSV found — run Step 7 (fundamental_analysis.py)")
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    try:
        df = pd.read_csv(f)

        # Coerce numeric columns (some cells may be empty strings)
        num_cols = [
            "price", "mkt_cap", "pe_forward", "pe_trailing",
            "revenue_growth_yoy", "earnings_growth_yoy",
            "operating_margin", "roe", "free_cash_flow",
            "analyst_rating", "analyst_count", "target_mean",
            "composite", "extended_composite",
            "score_valuation", "score_growth", "score_quality",
            "score_balance", "score_earnings", "score_analyst",
            "score_dcf_valuation", "score_peer_relative", "score_accounting_quality",
        ]
        for col in num_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Server-side pre-filters
        if "composite" in df.columns:
            df = df[df["composite"].fillna(0) >= min_composite]
        if "pe_forward" in df.columns and max_pe_forward < 900:
            df = df[(df["pe_forward"].isna()) | (df["pe_forward"] <= max_pe_forward)]
        if "revenue_growth_yoy" in df.columns and min_revenue_growth > -99:
            df = df[(df["revenue_growth_yoy"].isna()) | (df["revenue_growth_yoy"] >= min_revenue_growth)]
        if "operating_margin" in df.columns and min_operating_margin > -99:
            df = df[(df["operating_margin"].isna()) | (df["operating_margin"] >= min_operating_margin)]

        df = df.sort_values("composite", ascending=False) if "composite" in df.columns else df

        def _mktcap_tier(cap) -> str:
            if cap is None or (isinstance(cap, float) and cap != cap):
                return "unknown"
            cap = float(cap)
            if cap >= 200e9:  return "mega"
            if cap >= 10e9:   return "large"
            if cap >= 2e9:    return "mid"
            if cap >= 300e6:  return "small"
            return "micro"

        records = []
        for _, row in df.iterrows():
            records.append({
                "ticker":                  str(row.get("ticker", "")),
                "name":                    str(row.get("name", "") or ""),
                "sector":                  str(row.get("sector", "") or ""),
                "price":                   _safe_float(row.get("price")),
                "mkt_cap":                 _safe_float(row.get("mkt_cap")),
                "mkt_cap_tier":            _mktcap_tier(row.get("mkt_cap")),
                "pe_forward":              _safe_float(row.get("pe_forward")),
                "pe_trailing":             _safe_float(row.get("pe_trailing")),
                "revenue_growth_yoy":      _safe_float(row.get("revenue_growth_yoy")),
                "earnings_growth_yoy":     _safe_float(row.get("earnings_growth_yoy")),
                "operating_margin":        _safe_float(row.get("operating_margin")),
                "roe":                     _safe_float(row.get("roe")),
                "free_cash_flow":          _safe_float(row.get("free_cash_flow")),
                "analyst_rating":          _safe_float(row.get("analyst_rating")),
                "analyst_count":           _safe_int(row.get("analyst_count")),
                "target_mean":             _safe_float(row.get("target_mean")),
                "composite":               _safe_float(row.get("composite")),
                "extended_composite":      _safe_float(row.get("extended_composite")),
                "score_valuation":         _safe_int(row.get("score_valuation")),
                "score_growth":            _safe_int(row.get("score_growth")),
                "score_quality":           _safe_int(row.get("score_quality")),
                "score_balance":           _safe_int(row.get("score_balance")),
                "score_earnings":          _safe_int(row.get("score_earnings")),
                "score_analyst":           _safe_int(row.get("score_analyst")),
                "score_accounting_quality": _safe_int(row.get("score_accounting_quality")),
            })

        # Extract date from filename (fundamental_YYYYMMDD.csv)
        as_of = f.stem.split("_")[-1] if "_" in f.stem else None

        result = {
            "data_available": True,
            "source_file":    f.name,
            "as_of":          as_of,
            "count":          len(records),
            "generated_at":   datetime.utcnow().isoformat() + "Z",
            "data":           _json_safe(records),
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("screeners_fundamentals error")
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
                "ticker":           str(row.get("ticker", "")),
                "total_score":      _safe_float(row.get("composite") or row.get("total_score")),
                "squeeze_setup":    _safe_float(row.get("squeeze_score")),
                "volume_breakout":  _safe_float(row.get("volume_score")),
                "dark_pool_signal": _safe_float(row.get("dark_pool_score")),
                "options_score":    _safe_float(row.get("options_score")),
                "technical_score":  _safe_float(row.get("technical_score")),
                "earnings_score":   _safe_float(row.get("earnings_score")),
                "analyst_score":    _safe_float(row.get("analyst_score")),
                "days_to_earnings": _safe_int(row.get("days_to_earnings")),
                "upgrades_7d":      _safe_int(row.get("upgrades_7d")),
                "n_flags":          _safe_int(row.get("n_flags")),
                "setup_details":    str(row.get("flags", "")),
            })

        result = {
            "data_available": True,
            "source_file":   f.name,
            "as_of":         datetime.utcfromtimestamp(f.stat().st_mtime).isoformat() + "Z",
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

    conn = _db_connect()
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
                records.append({
                    "ticker":           ticker,
                    "heat_score":       heat,
                    "iv_rank":          _safe_float(of.get("iv_rank")),
                    "iv_source":        of.get("iv_source", "options_chain"),
                    "volume_spike_ratio": _safe_float(of.get("total_options_vol")),
                    "expected_move_pct": _safe_float(of.get("expected_move_pct")),
                    "put_call_ratio":   _safe_float(of.get("pc_ratio")),
                    "days_to_expiry":   _safe_int(of.get("days_to_exp")),
                    "as_of":            r["date"],
                })
            except Exception:
                continue

        records.sort(key=lambda x: x["heat_score"] or 0, reverse=True)
        # as_of = most recent data date across all records
        dates = [r["as_of"] for r in records if r.get("as_of")]
        result = {
            "data_available": True,
            "count":          len(records),
            "as_of":          max(dates) + "Z" if dates else None,
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


@app.get("/api/rankings/latest")
async def rankings_latest():
    """
    Latest Top-20 daily ranking.

    Returns all 20 rows for the most recent run_date in daily_rankings,
    sorted by rank ascending.  Cached for 5 minutes (data updates once daily).
    """
    cache_key = "rankings_latest"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    try:
        conn = _db_connect()
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT *, MAX(created_at) OVER () AS pipeline_run_at
            FROM   daily_rankings
            WHERE  run_date = (SELECT MAX(run_date) FROM daily_rankings)
              AND  rank <= 20
              AND  ticker NOT IN (
                       SELECT ticker FROM blacklist
                       WHERE expires_at IS NULL OR expires_at > NOW()
                   )
            ORDER  BY rank ASC
            """
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            result = _no_data("daily_rankings table is empty")
            _cache.set(cache_key, result, TTL_SHORT)
            return result

        # Fetch live prices for all ranked tickers (run in thread so it doesn't block the event loop)
        ranked_tickers = [str(r["ticker"]) for r in rows]
        live_prices = await asyncio.to_thread(_fetch_current_prices, ranked_tickers)

        # pipeline_run_at: when the pipeline last wrote this ranking to the DB
        pipeline_run_at = rows[0].get("pipeline_run_at")
        pipeline_run_at_str = pipeline_run_at.strftime("%Y-%m-%dT%H:%M:%SZ") if pipeline_run_at else None

        records = []
        for r in rows:
            ticker = str(r["ticker"])
            records.append({
                "run_date":        str(r["run_date"]),
                "rank":            _safe_int(r["rank"]),
                "ticker":          ticker,
                "current_price":   live_prices.get(ticker),
                "priority_score":  _safe_float(r["priority_score"]),
                "final_score":     _safe_float(r["final_score"]),
                "weight":          _safe_float(r["weight"]),
                "raw_weight":      _safe_float(r["raw_weight"]),
                "cap_hit":         bool(r["cap_hit"]) if r["cap_hit"] is not None else False,
                "sector":          str(r["sector"] or "Unknown"),
                "hist_vol_60d":    _safe_float(r["hist_vol_60d"]),
                "adv_20d":         _safe_float(r["adv_20d"]),
                "rank_change":     str(r["rank_change"] or "—"),
                "rank_yesterday":  _safe_int(r["rank_yesterday"]),
                # Swing trade fields
                "direction":        str(r["direction"] or "NEUTRAL"),
                "t1_price":         _safe_float(r["t1_price"]),
                "t2_price":         _safe_float(r["t2_price"]),
                "stop_price":       _safe_float(r["stop_price"]),
                "prob_t1":          _safe_float(r["prob_t1"]),
                "prob_t2":          _safe_float(r["prob_t2"]),
                "hold_days":        _safe_int(r["hold_days"]),
                "agreement_score":  _safe_float(r["agreement_score"]),
                "ev_t1_pct":        _safe_float(r["ev_t1_pct"]),
                "is_open_position": bool(r["is_open_position"]) if r["is_open_position"] is not None else False,
                "prob_combined":    _safe_float(r.get("prob_combined")),
            })

        result = {
            "data_available":  True,
            "count":           len(records),
            "as_of":           records[0]["run_date"] if records else None,
            "pipeline_run_at": pipeline_run_at_str,
            "generated_at":    datetime.utcnow().isoformat() + "Z",
            "data":            _json_safe(records),
        }
        _cache.set(cache_key, result, TTL_MEDIUM)  # rankings data is daily; 15-min cache is plenty
        return result

    except Exception as e:
        log.exception("rankings_latest error")
        return _no_data(str(e))


@app.get("/api/signals/selection")
async def signals_selection():
    """
    AI Quant Selection — today's top 5 dynamic tickers + all open positions.

    Reads from candidate_snapshots (Supabase) for the most recent run_date.
    Open positions (is_open_position=True) are always included and flagged.
    Dynamic rows are the top 5 by priority_score among non-open-position candidates.

    Returns up to 8 rows: 5 dynamic + however many open positions exist.
    Cached for 5 minutes (data updates once daily).
    """
    cache_key = "signals_selection"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    try:
        conn = _db_connect()
        cur  = conn.execute(
            """
            SELECT ticker, priority_score, signal_agreement_score,
                   pre_resolved_direction, equity_rank, composite_z,
                   is_open_position, selection_reason, run_date
            FROM   candidate_snapshots
            WHERE  run_date = (SELECT MAX(run_date) FROM candidate_snapshots)
            ORDER  BY is_open_position DESC, priority_score DESC
            """
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            result = _no_data("candidate_snapshots table is empty")
            _cache.set(cache_key, result, TTL_SHORT)
            return result

        run_date = str(rows[0]["run_date"])

        # Separate open positions from dynamic candidates
        open_pos  = [r for r in rows if r["is_open_position"]]
        dynamic   = [r for r in rows if not r["is_open_position"]]

        # Top 5 dynamic by priority_score (already sorted)
        top_dynamic = dynamic[:5]

        # Merge: dynamic first, then open positions not already in dynamic
        dynamic_tickers = {r["ticker"] for r in top_dynamic}
        extra_open      = [r for r in open_pos if r["ticker"] not in dynamic_tickers]

        selection = top_dynamic + extra_open

        records = []
        for i, r in enumerate(selection, 1):
            eq_rank = _safe_int(r["equity_rank"])
            records.append({
                "rank":             i,
                "ticker":           str(r["ticker"]),
                "priority_score":   round(_safe_float(r["priority_score"]) or 0.0, 1),
                "agreement_pct":    round((_safe_float(r["signal_agreement_score"]) or 0.0) * 100),
                "direction":        str(r["pre_resolved_direction"] or "NEUTRAL"),
                "equity_rank":      eq_rank,
                "is_open_position": bool(r["is_open_position"]),
                "selection_reason": str(r["selection_reason"] or ""),
            })

        result = {
            "data_available": True,
            "count":          len(records),
            "as_of":          run_date,
            "generated_at":   datetime.utcnow().isoformat() + "Z",
            "n_dynamic":      len(top_dynamic),
            "n_open":         len(extra_open),
            "data":           _json_safe(records),
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("signals_selection error")
        return _no_data(str(e))


@app.get("/api/signals/candidates")
async def signals_candidates():
    """
    Full priority-scored candidate pool for the most recent run.

    Returns all rows from candidate_snapshots for the latest run_date, sorted
    by priority_score descending. Each row includes a `selected` flag marking
    whether it made it into the final AI Quant Selection (top 5 dynamic +
    all open positions).

    Cached for 5 minutes (TTL_SHORT).
    """
    import json as _json

    cache_key = "signals_candidates"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    try:
        conn = _db_connect()
        cur  = conn.execute(
            """
            SELECT ticker, priority_score, signal_agreement_score,
                   pre_resolved_direction, pre_resolved_confidence,
                   equity_rank, composite_z,
                   override_flags, selection_reason,
                   is_open_position, run_date
            FROM   candidate_snapshots
            WHERE  run_date = (SELECT MAX(run_date) FROM candidate_snapshots)
              AND  ticker NOT IN (
                       SELECT ticker FROM blacklist
                       WHERE expires_at IS NULL OR expires_at > NOW()
                   )
            ORDER  BY priority_score DESC
            """
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            result = _no_data("candidate_snapshots table is empty")
            _cache.set(cache_key, result, TTL_SHORT)
            return result

        run_date = str(rows[0]["run_date"])

        # Mirror the selection logic: top 5 dynamic by priority + all open positions
        open_tickers    = {r["ticker"] for r in rows if r["is_open_position"]}
        dynamic_ranked  = [r for r in rows if not r["is_open_position"]]
        top5_tickers    = {r["ticker"] for r in dynamic_ranked[:5]}
        selected_tickers = top5_tickers | open_tickers

        records = []
        for rank, r in enumerate(rows, 1):
            try:
                flags = _json.loads(r["override_flags"] or "[]")
            except Exception:
                flags = []

            records.append({
                "rank":             rank,
                "ticker":           str(r["ticker"]),
                "priority_score":   round(_safe_float(r["priority_score"]) or 0.0, 1),
                "agreement_pct":    round((_safe_float(r["signal_agreement_score"]) or 0.0) * 100),
                "direction":        str(r["pre_resolved_direction"] or "NEUTRAL"),
                "confidence_pct":   round((_safe_float(r["pre_resolved_confidence"]) or 0.0) * 100),
                "equity_rank":      _safe_int(r["equity_rank"]),
                "composite_z":      round(_safe_float(r["composite_z"]) or 0.0, 3),
                "override_flags":   flags,
                "selection_reason": str(r["selection_reason"] or ""),
                "is_open_position": bool(r["is_open_position"]),
                "selected":         r["ticker"] in selected_tickers,
            })

        result = {
            "data_available": True,
            "count":          len(records),
            "as_of":          run_date,
            "generated_at":   datetime.utcnow().isoformat() + "Z",
            "n_selected":     len(selected_tickers),
            "data":           _json_safe(records),
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("signals_candidates error")
        return _no_data(str(e))


@app.get("/api/rankings/history")
async def rankings_history(
    ticker: Optional[str] = Query(None, description="Filter to a single ticker (e.g. NVDA)"),
    days:   int           = Query(30,   ge=1, le=365, description="Look-back window in calendar days"),
):
    """
    Top-20 rank history for the last N calendar days.

    - Omit ``ticker`` to get the full rolling table (all tickers × all dates).
    - Pass ``?ticker=NVDA`` to get a single ticker's rank history — ideal for
      the rank-over-time chart in the ticker detail panel.

    Sorted by run_date DESC, then rank ASC.
    """
    cache_key = f"rankings_history:{ticker or 'all'}:{days}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    try:
        from datetime import date, timedelta
        cutoff = date.today() - timedelta(days=days)

        conn = _db_connect()
        cur  = conn.cursor()

        if ticker is None:
            cur.execute(
                """
                SELECT *
                FROM   daily_rankings
                WHERE  run_date >= %s
                ORDER  BY run_date DESC, rank ASC
                """,
                (cutoff,),
            )
        else:
            cur.execute(
                """
                SELECT *
                FROM   daily_rankings
                WHERE  run_date >= %s
                  AND  UPPER(ticker) = UPPER(%s)
                ORDER  BY run_date DESC, rank ASC
                """,
                (cutoff, ticker.upper()),
            )

        rows = cur.fetchall()
        conn.close()

        if not rows:
            result = _no_data(f"no ranking history found (ticker={ticker}, days={days})")
            _cache.set(cache_key, result, TTL_SHORT)
            return result

        records = []
        for r in rows:
            records.append({
                "run_date":       str(r["run_date"]),
                "rank":           _safe_int(r["rank"]),
                "ticker":         str(r["ticker"]),
                "priority_score": _safe_float(r["priority_score"]),
                "final_score":    _safe_float(r["final_score"]),
                "weight":         _safe_float(r["weight"]),
                "raw_weight":     _safe_float(r["raw_weight"]),
                "cap_hit":        bool(r["cap_hit"]) if r["cap_hit"] is not None else False,
                "sector":         str(r["sector"] or "Unknown"),
                "hist_vol_60d":   _safe_float(r["hist_vol_60d"]),
                "adv_20d":        _safe_float(r["adv_20d"]),
                "rank_change":    str(r["rank_change"] or "—"),
                "rank_yesterday": _safe_int(r["rank_yesterday"]),
            })

        result = {
            "data_available": True,
            "count":          len(records),
            "ticker":         ticker,
            "days":           days,
            "generated_at":   datetime.utcnow().isoformat() + "Z",
            "data":           _json_safe(records),
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("rankings_history error")
        return _no_data(str(e))


@app.get("/api/hot-entry/rankings")
async def hot_entry_rankings():
    """
    Ranked hot-entry candidates for today.

    Scoring formula (higher = better buy today):
      score = is_hot_bonus(50) + prob_t1 * t1_upside_pct * 2 + min(rr,3)*3 + conviction

    Saves a daily snapshot to hot_entry_rankings on first call of the day.
    Returns ranked rows with rank numbers and rank_change vs yesterday.
    """
    cache_key = "hot_entry_rankings"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    try:
        from datetime import date as _date, timedelta
        import math

        today = _date.today()
        conn = _db_connect()

        # ── 1. Pull thesis_cache (with equity_rank) ───────────────────────────
        tc_rows = conn.execute("""
            SELECT tc.ticker, tc.direction, tc.conviction,
                   tc.entry_low, tc.entry_high, tc.target_1, tc.target_2, tc.stop_loss,
                   ss.rank AS equity_rank
            FROM thesis_cache tc
            LEFT JOIN (
                SELECT ticker, rank FROM screener_signals
                WHERE date = (SELECT MAX(date) FROM screener_signals)
            ) ss ON ss.ticker = tc.ticker
            WHERE tc.ticker NOT IN (
                SELECT ticker FROM blacklist
                WHERE expires_at IS NULL OR expires_at > NOW()
            )
              AND tc.entry_low IS NOT NULL AND tc.entry_high IS NOT NULL
            ORDER BY tc.date DESC, tc.created_at DESC
        """).fetchall()

        # Deduplicate — keep latest thesis per ticker
        seen: set = set()
        theses: list = []
        for r in tc_rows:
            t = r["ticker"]
            if t not in seen:
                seen.add(t)
                theses.append(dict(r))

        # ── 2. Pull latest daily_rankings for prob_t1/t2 and live targets ─────
        rk_rows = conn.execute("""
            SELECT ticker, prob_t1, prob_t2, t1_price, t2_price
            FROM daily_rankings
            WHERE run_date = (SELECT MAX(run_date) FROM daily_rankings)
        """).fetchall()
        rk_map = {r["ticker"]: r for r in rk_rows}

        # ── 3. Pull live prices via yfinance ──────────────────────────────────
        tickers_needed = [t["ticker"] for t in theses]
        fund_map: dict = _fetch_current_prices(tickers_needed) if tickers_needed else {}

        # ── 4. Pull live buy zones ────────────────────────────────────────────
        live_zone_key = "deepdive_live_zones"
        lz_cached = _cache.get(live_zone_key)
        live_zones: dict = lz_cached.get("zones", {}) if lz_cached else {}

        # ── 5. Score each thesis ticker ───────────────────────────────────────
        def _score_row(t: dict, price: float | None) -> dict | None:
            el, eh = t["entry_low"], t["entry_high"]
            if el is None or eh is None or price is None:
                return None
            in_ai = el <= price <= eh
            lz = live_zones.get(t["ticker"])
            in_live = lz and lz.get("buy_zone_low", 0) <= price <= lz.get("buy_zone_high", 0)
            is_hot = bool(in_ai and in_live)
            if not in_ai:
                return None  # not in any zone — skip

            entry_mid = (el + eh) / 2
            rk = rk_map.get(t["ticker"], {})
            t1_ai = t["target_1"]
            t1_live = rk.get("t1_price") if rk else None
            t1_med = (t1_ai + t1_live) / 2 if t1_ai and t1_live else (t1_ai or t1_live)
            t2_ai = t["target_2"]
            t2_live = rk.get("t2_price") if rk else None
            t2_med = (t2_ai + t2_live) / 2 if t2_ai and t2_live else (t2_ai or t2_live)

            t1_upside = ((t1_med - entry_mid) / entry_mid * 100) if t1_med else None
            t2_upside = ((t2_med - entry_mid) / entry_mid * 100) if t2_med else None

            stop = t["stop_loss"]
            sp_pct = abs((stop - entry_mid) / entry_mid * 100) if stop else None
            rr_val = (abs(t1_upside) / sp_pct) if t1_upside and sp_pct and sp_pct > 0 else None

            prob_t1 = float(rk.get("prob_t1") or 0) if rk else 0.0
            prob_t2 = float(rk.get("prob_t2") or 0) if rk else 0.0
            conviction = int(t.get("conviction") or 0)

            score = 0.0
            if is_hot:
                score += 50
            if prob_t1 and t1_upside:
                score += prob_t1 * abs(t1_upside) * 2
            if rr_val:
                score += min(rr_val, 3) * 3
            score += conviction

            return {
                "ticker":        t["ticker"],
                "is_hot":        is_hot,
                "status":        "HOT" if is_hot else "IN_ZONE",
                "hot_score":     round(score, 2),
                "current_price": price,
                "entry_low":     el,
                "entry_high":    eh,
                "t1_median":     round(t1_med, 2) if t1_med else None,
                "t2_median":     round(t2_med, 2) if t2_med else None,
                "prob_t1":       prob_t1 or None,
                "prob_t2":       prob_t2 or None,
                "t1_upside_pct": round(t1_upside, 2) if t1_upside else None,
                "t2_upside_pct": round(t2_upside, 2) if t2_upside else None,
                "rr":            round(rr_val, 2) if rr_val else None,
                "conviction":    conviction,
                "equity_rank":   t.get("equity_rank"),
                "direction":     t.get("direction"),
            }

        scored = []
        for t in theses:
            price = fund_map.get(t["ticker"])
            row = _score_row(t, price)
            if row:
                scored.append(row)

        scored.sort(key=lambda r: r["hot_score"], reverse=True)

        # ── 6. Pull yesterday's ranks for rank_change ─────────────────────────
        yesterday = today - timedelta(days=1)
        prev_rows = conn.execute(
            "SELECT ticker, rank FROM hot_entry_rankings WHERE run_date = %s",
            (yesterday,)
        ).fetchall()
        prev_rank = {r["ticker"]: r["rank"] for r in prev_rows}

        # ── 7. Assign ranks + compute rank_change ─────────────────────────────
        records = []
        for i, row in enumerate(scored, 1):
            prev = prev_rank.get(row["ticker"])
            if prev is None:
                rank_change = "NEW"
            else:
                delta = prev - i
                rank_change = f"+{delta}" if delta > 0 else (str(delta) if delta < 0 else "—")
            records.append({**row, "rank": i, "rank_change": rank_change, "run_date": str(today)})

        # ── 8. Snapshot to DB once per day ────────────────────────────────────
        existing = conn.execute(
            "SELECT COUNT(*) AS cnt FROM hot_entry_rankings WHERE run_date = %s", (today,)
        ).fetchone()
        if existing["cnt"] == 0 and records:
            for rec in records:
                conn.execute("""
                    INSERT INTO hot_entry_rankings
                        (run_date, rank, ticker, hot_score, status, is_hot,
                         current_price, entry_low, entry_high,
                         t1_median, t2_median, prob_t1, prob_t2,
                         t1_upside_pct, rr, conviction, equity_rank, rank_change)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (run_date, ticker) DO NOTHING
                """, (
                    today, rec["rank"], rec["ticker"], rec["hot_score"],
                    rec["status"], rec["is_hot"], rec["current_price"],
                    rec["entry_low"], rec["entry_high"],
                    rec["t1_median"], rec["t2_median"],
                    rec["prob_t1"], rec["prob_t2"],
                    rec["t1_upside_pct"], rec["rr"],
                    rec["conviction"], rec["equity_rank"], rec["rank_change"]
                ))
            conn.commit()

        conn.close()

        result = {
            "data_available": True,
            "count": len(records),
            "as_of": str(today),
            "data": _json_safe(records),
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception:
        log.exception("hot_entry_rankings error")
        return _no_data("hot_entry_rankings failed")


@app.get("/api/hot-entry/history")
async def hot_entry_history(
    ticker: str = Query(..., description="Ticker symbol"),
    days:   int = Query(30, ge=1, le=365),
):
    """Rank history for a single ticker in the hot-entry table."""
    cache_key = f"hot_entry_history:{ticker.upper()}:{days}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    try:
        from datetime import date as _date, timedelta
        cutoff = _date.today() - timedelta(days=days)
        conn = _db_connect()
        rows = conn.execute(
            """
            SELECT run_date, rank, hot_score, status, rank_change
            FROM hot_entry_rankings
            WHERE ticker = %s AND run_date >= %s
            ORDER BY run_date DESC
            """,
            (ticker.upper(), cutoff)
        ).fetchall()
        conn.close()

        records = [{"run_date": str(r["run_date"]), "rank": r["rank"],
                    "hot_score": float(r["hot_score"] or 0),
                    "status": r["status"], "rank_change": r["rank_change"]} for r in rows]
        result = {"data_available": bool(records), "ticker": ticker.upper(),
                  "count": len(records), "data": records}
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception:
        log.exception("hot_entry_history error")
        return _no_data("hot_entry_history failed")


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

        # Normalize the overrides column: CSV stores a semicolon-delimited string,
        # frontend expects a list of individual flag strings.
        records = []
        for r in raw_records:
            overrides_raw = r.get("overrides") or ""
            if overrides_raw and str(overrides_raw).strip():
                # Split on "; " — each item looks like "override: flag_name"
                override_list = [
                    s.strip()
                    for s in str(overrides_raw).split(";")
                    if s.strip()
                ]
            else:
                override_list = []

            records.append({
                **{k: v for k, v in r.items() if k != "overrides"},
                "overrides": override_list,
            })

        return {
            "data_available": True,
            "source_file":    log_file.name,
            "count":          len(records),
            "data":           _json_safe(records),
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


@app.get("/api/resolution/accuracy-matrix")
async def resolution_accuracy_matrix(
    days: int = Query(180, ge=1, le=730),
):
    """
    3D accuracy breakdown of Claude thesis outcomes, sliced by:
      - market_regime  (RISK_ON / TRANSITIONAL / RISK_OFF)
      - conviction     (1–5)
      - agreement_bucket  (<0.50 / 0.50–0.70 / ≥0.70)

    Returns a list of cells, each containing win_rate, avg_return_30d,
    hit_t1_rate, sample_size for that combination. Cells with n=0 are omitted.
    """
    cache_key = f"accuracy_matrix:{days}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    try:
        from utils.db import get_connection as _supabase_conn
        conn = _supabase_conn()
    except Exception as e:
        return _no_data(str(e))

    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        cur = conn.cursor()

        # Graceful no-op if table doesn't exist yet
        try:
            cur.execute("SELECT id FROM thesis_outcomes LIMIT 1")
        except Exception:
            conn.close()
            result = _no_data("thesis_outcomes not yet populated — run thesis_checker.py")
            _cache.set(cache_key, result, TTL_SHORT)
            return result

        cur.execute(
            """SELECT o.conviction, o.outcome, o.claude_correct,
                      o.return_30d, o.hit_target_1,
                      c.signal_agreement_score, c.signals_json
               FROM thesis_outcomes o
               JOIN thesis_cache c ON o.thesis_id = c.id
               WHERE o.thesis_date >= %s
                 AND o.outcome NOT IN ('OPEN')
               ORDER BY o.thesis_date DESC""",
            (cutoff,),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        log.exception("accuracy_matrix query error")
        try:
            conn.close()
        except Exception:
            pass
        return _no_data(str(e))

    if not rows:
        result = {"data_available": False, "cells": [], "summary": {}}
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    def _agreement_bucket(score) -> str:
        if score is None:
            return "unknown"
        s = float(score)
        if s >= 0.70:
            return "high"       # ≥0.70
        if s >= 0.50:
            return "mid"        # 0.50–0.70
        return "low"            # <0.50

    def _regime_from_signals(signals_json) -> str:
        """Extract market regime from signals_json blob."""
        if not signals_json:
            return "unknown"
        try:
            sigs = json.loads(signals_json) if isinstance(signals_json, str) else signals_json
            mr = sigs.get("market_regime") or {}
            return (mr.get("regime") or "unknown").upper()
        except Exception:
            return "unknown"

    # Accumulate per-cell stats
    from collections import defaultdict
    cells: dict = defaultdict(lambda: {"n": 0, "correct": 0, "hit_t1": 0, "return_sum": 0.0, "return_n": 0})

    for row in rows:
        regime     = _regime_from_signals(row["signals_json"])
        conv       = int(row["conviction"] or 0)
        bucket     = _agreement_bucket(row["signal_agreement_score"])
        key        = (regime, conv, bucket)

        c = cells[key]
        c["n"] += 1
        if row["claude_correct"] == 1:
            c["correct"] += 1
        if row["hit_target_1"]:
            c["hit_t1"] += 1
        if row["return_30d"] is not None:
            c["return_sum"] += float(row["return_30d"])
            c["return_n"] += 1

    result_cells = []
    for (regime, conv, bucket), c in sorted(cells.items()):
        n = c["n"]
        result_cells.append({
            "regime":          regime,
            "conviction":      conv,
            "agreement_bucket": bucket,
            "sample_size":     n,
            "win_rate":        round(c["correct"] / n, 3) if n > 0 else None,
            "hit_t1_rate":     round(c["hit_t1"]  / n, 3) if n > 0 else None,
            "avg_return_30d":  round(c["return_sum"] / c["return_n"], 2) if c["return_n"] > 0 else None,
        })

    # Summary totals for each slice dimension
    total_n   = sum(c["n"] for c in cells.values())
    total_win = sum(c["correct"] for c in cells.values())

    result = {
        "data_available": True,
        "filters": {"days": days},
        "total_resolved": total_n,
        "overall_win_rate": round(total_win / total_n, 3) if total_n > 0 else None,
        "cells": result_cells,
    }
    _cache.set(cache_key, result, TTL_MEDIUM)
    return result


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


@app.get("/api/status/cache")
async def status_cache():
    """Pipeline status + live in-memory cache stats for the dashboard status card."""
    status_file = DATA_DIR / "pipeline_status.json"
    pipeline: dict = {}
    if status_file.exists():
        try:
            pipeline = json.loads(status_file.read_text())
        except Exception:
            pass
    return {
        "pipeline": pipeline,
        "cache": {
            "warm_keys": len(_cache._store),
        },
        "as_of": datetime.utcnow().isoformat() + "Z",
    }


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


class AnalyzeRequest(BaseModel):
    llm: str = "grok"   # "grok" | "grok-premium" | "claude"


@app.post("/api/ticker/{symbol}/analyze")
async def ticker_analyze(symbol: str, req: AnalyzeRequest = AnalyzeRequest()):
    """
    Trigger ai_quant.py --ticker SYMBOL --no-cache [--llm LLM] as a background subprocess.
    Always bypasses thesis cache so the chosen LLM is actually called.
    Returns immediately. Poll /analyze/status to see when the job completes.
    """
    sym = symbol.upper()
    llm = req.llm if req.llm in ("grok", "grok-premium", "claude") else "grok"

    # If already running for the same LLM, return current status
    job = _analysis_jobs.get(sym)
    if job and job.get("status") == "running":
        proc = job.get("proc")
        if proc and proc.returncode is None:
            return {"status": "running", "symbol": sym, "started_at": job["started_at"],
                    "llm": job.get("llm", llm)}
        else:
            _analysis_jobs[sym]["status"] = "done"

    ai_quant_path = BASE_DIR / "ai_quant.py"
    python_exec   = BASE_DIR / ".venv" / "bin" / "python"
    if not python_exec.exists():
        python_exec = sys.executable

    try:
        import os as _os
        sub_env = dict(_os.environ)
        # Ensure API keys are forwarded to the subprocess
        for key in ("XAI_API_KEY", "ANTHROPIC_API_KEY"):
            val = _os.environ.get(key)
            if val:
                sub_env[key] = val
        proc = await asyncio.create_subprocess_exec(
            str(python_exec), str(ai_quant_path),
            "--ticker", sym, "--no-cache", "--llm", llm,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(BASE_DIR),
            env=sub_env,
        )
        started = datetime.utcnow().isoformat() + "Z"
        _analysis_jobs[sym] = {"status": "running", "started_at": started, "proc": proc, "llm": llm}
        log.info(f"Analysis started for {sym} with llm={llm} (pid {proc.pid})")
        # Estimate model and cost for the chosen LLM
        try:
            from config import AI_MODEL_DEFAULT, AI_MODEL_PREMIUM
            from utils.usage import compute_cost
            if llm == "claude":
                est_model = "claude-sonnet-4-6"
                est_cost  = round(compute_cost(est_model, 3558, 1300), 4)
            elif llm == "grok-premium":
                est_model = AI_MODEL_PREMIUM
                est_cost  = round(compute_cost(est_model, 3558, 1300), 4)
            else:
                est_model = AI_MODEL_DEFAULT
                est_cost  = round(compute_cost(est_model, 3558, 1300), 4)
        except Exception:
            est_model = {"grok-premium": "grok-4.20-0309-reasoning", "claude": "claude-sonnet-4-6"}.get(llm, "grok-4-1-fast-reasoning")
            est_cost  = {"grok-premium": 0.027, "claude": 0.012}.get(llm, 0.009)
        return {
            "status": "running", "symbol": sym, "started_at": started, "pid": proc.pid,
            "estimated_model": est_model, "estimated_cost": est_cost, "llm": llm,
        }
    except Exception as e:
        log.error(f"Failed to launch analysis for {sym}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _get_setting(key: str, default: str) -> str:
    """Read a single value from strategy_config, falling back to default."""
    try:
        conn = _db_connect()
        row = conn.execute("SELECT value FROM strategy_config WHERE key = %s", (key,)).fetchone()
        conn.close()
        return row["value"] if row and row["value"] is not None else default
    except Exception:
        return default


def _apply_thesis_calibration(ticker: str, model: str, min_sample: int | None = None) -> bool:
    """
    Adjust the latest thesis_cache targets for `ticker` using the model's historical
    avg calibration error vs T1 and T2 (from thesis_outcomes).

    Only applies when the model has >= min_sample resolved outcomes.
    Returns True if calibration was applied.
    """
    if min_sample is None:
        min_sample = int(_get_setting("calibration_min_sample", "20"))
    window = int(_get_setting("calibration_window", "60"))

    try:
        conn = _db_connect()

        # Fetch calibration factors for this model (most recent `window` resolved outcomes)
        cal = conn.execute("""
            WITH recent AS (
                SELECT o.*
                FROM thesis_outcomes o
                JOIN thesis_cache tc ON tc.id = o.thesis_id
                WHERE tc.model_used = %s
                  AND o.outcome IN ('HIT_TARGET1','HIT_TARGET2','HIT_STOP')
                ORDER BY o.thesis_date DESC
                LIMIT %s
            )
            SELECT
                COUNT(*) AS n,
                AVG(
                    CASE
                        WHEN o.outcome = 'HIT_TARGET1' AND o.entry_price > 0 AND o.target_1 IS NOT NULL
                            THEN (o.target_1 - o.entry_price) / o.entry_price * 100
                                * CASE WHEN o.direction = 'BEAR' THEN -1 ELSE 1 END
                        WHEN o.outcome = 'HIT_TARGET2' AND o.entry_price > 0 AND o.target_2 IS NOT NULL
                            THEN (o.target_2 - o.entry_price) / o.entry_price * 100
                                * CASE WHEN o.direction = 'BEAR' THEN -1 ELSE 1 END
                        WHEN o.outcome = 'HIT_STOP'    AND o.entry_price > 0 AND o.stop_loss IS NOT NULL
                            THEN (o.stop_loss - o.entry_price) / o.entry_price * 100
                                * CASE WHEN o.direction = 'BEAR' THEN -1 ELSE 1 END
                    END
                ) AS avg_outcome_return,
                AVG(o.vs_target_1_pct) FILTER (WHERE o.vs_target_1_pct IS NOT NULL) AS avg_vs_t1,
                AVG(o.vs_target_2_pct) FILTER (WHERE o.vs_target_2_pct IS NOT NULL) AS avg_vs_t2
            FROM recent o
        """, (model, window)).fetchone()

        if not cal or (cal["n"] or 0) < min_sample:
            conn.close()
            return False

        avg_vs_t1 = float(cal["avg_vs_t1"]) if cal["avg_vs_t1"] is not None else None
        avg_vs_t2 = float(cal["avg_vs_t2"]) if cal["avg_vs_t2"] is not None else None

        if avg_vs_t1 is None and avg_vs_t2 is None:
            conn.close()
            return False

        # Fetch the latest thesis for this ticker
        thesis = conn.execute("""
            SELECT id, target_1, target_2, direction
            FROM thesis_cache
            WHERE ticker = %s
            ORDER BY created_at DESC
            LIMIT 1
        """, (ticker,)).fetchone()

        if not thesis:
            conn.close()
            return False

        t1_raw = thesis["target_1"]
        t2_raw = thesis["target_2"]
        direction = thesis["direction"]
        bear = direction == "BEAR"

        # Calibration: if avg_vs_t1 = -2%, model overshoots by 2% → lower target by 2%
        # For BEAR: target_1 < entry_price, so a negative vs_t1 means price didn't fall enough
        # The correction is symmetric: multiply target by (1 + avg_vs_t1/100)
        updates = {}
        if t1_raw and avg_vs_t1 is not None:
            factor = 1 + avg_vs_t1 / 100
            updates["target_1"] = round(float(t1_raw) * factor, 2)

        if t2_raw and avg_vs_t2 is not None:
            factor = 1 + avg_vs_t2 / 100
            updates["target_2"] = round(float(t2_raw) * factor, 2)

        if not updates:
            conn.close()
            return False

        set_clause = ", ".join(f"{k} = %s" for k in updates)
        conn.execute(
            f"UPDATE thesis_cache SET {set_clause} WHERE id = %s",
            (*updates.values(), thesis["id"])
        )
        conn.commit()

        detail = {
            "model":    model,
            "sample_n": int(cal["n"]),
            "t1_raw":   float(t1_raw) if t1_raw else None,
            "t1_cal":   updates.get("target_1"),
            "t1_bias":  round(avg_vs_t1, 2) if avg_vs_t1 is not None else None,
            "t2_raw":   float(t2_raw) if t2_raw else None,
            "t2_cal":   updates.get("target_2"),
            "t2_bias":  round(avg_vs_t2, 2) if avg_vs_t2 is not None else None,
        }
        log.info("Calibration applied to %s: %s", ticker, detail)
        conn.close()
        return detail

    except Exception:
        log.exception("_apply_thesis_calibration failed for %s / %s", ticker, model)
        return False


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
    # Pull model + cost from the freshly saved thesis if available
    model_used = cost_usd = None
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "SELECT model_used, cost_usd FROM thesis_cache WHERE ticker=%s ORDER BY created_at DESC LIMIT 1",
            (sym,)
        )
        row = cur.fetchone()
        conn.close()
        if row:
            model_used = row["model_used"]
            cost_usd   = row["cost_usd"]
    except Exception:
        pass
    # Apply historical calibration to the freshly saved targets
    calibration = None
    if model_used:
        try:
            calibration = _apply_thesis_calibration(sym, model_used)
        except Exception:
            log.exception("calibration apply error for %s", sym)

    return {
        "status": "done", "symbol": sym, "started_at": job.get("started_at"),
        "used_model": model_used, "cost_usd": cost_usd,
        "calibration": calibration if calibration else None,
    }


# ==============================================================================
# SECTION 14: TICKER INTELLIGENCE — SEC FILINGS, EARNINGS
# ==============================================================================

_SEC_USER_AGENT = "SignalEngine/2.0 research@localhost"
_TTL_24H = 86_400

# Module-level caches for large datasets (don't use DataCache — these can be 10+ MB)
_edgar_cik_map: dict = {}
_edgar_cik_fetched_at: float = 0.0
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


def _quarter_label(dt) -> str:
    """'Q4 '24' from a period-end date."""
    q = (dt.month - 1) // 3 + 1
    return f"Q{q} '{str(dt.year)[-2:]}"


def _next_quarter_label(last_label: str) -> str:
    """Advance the most-recent reported quarter label by one.
    e.g. 'Q4 '25' → 'Q1 '26',  'Q2 '26' → 'Q3 '26'
    Falls back to '' if the label doesn't match the expected format.
    """
    import re
    m = re.match(r"Q(\d) '(\d{2})", last_label)
    if not m:
        return ""
    q, yr = int(m.group(1)), int(m.group(2))
    q += 1
    if q > 4:
        q = 1
        yr = (yr + 1) % 100
    return f"Q{q} '{yr:02d}"


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

    # Infer next quarter label from last reported quarter
    next_quarter: Optional[str] = None
    if quarterly:
        next_quarter = _next_quarter_label(quarterly[-1]["label"]) or None

    result = {
        "data_available":       bool(quarterly or annual or next_date),
        "next_earnings":        next_date,
        "next_earnings_quarter": next_quarter,
        "next_eps":             next_eps,
        "next_revenue":         next_revenue,
        "eps_growth_yoy":       eps_growth,
        "quarterly":            quarterly,
        "annual":               annual,
    }
    _cache.set(cache_key, result, 4 * 3600)
    return result


@app.get("/api/ticker/{symbol}/analogs")
async def ticker_analogs(symbol: str, limit: int = Query(12, ge=1, le=50)):
    """
    Historical analog setups from thesis_outcomes for the same direction as this
    ticker's current thesis.  Returns per-row stats (hit T1/T2/stop, 30d return,
    hold days) plus a summary with win rates, stop rate, and expectancy in R.

    Data source: thesis_outcomes table (populated by thesis_checker.py).
    If the table is empty the endpoint returns data_available=False gracefully.
    """
    sym = symbol.upper()
    cache_key = f"analogs:{sym}:{limit}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    conn = _db_connect()
    if conn is None:
        result = _no_data("ai_quant_cache.db not found")
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    # Graceful no-op when thesis_outcomes hasn't been created yet
    try:
        conn.execute("SELECT id FROM thesis_outcomes LIMIT 1")
    except Exception:
        result = _no_data("thesis_outcomes table not yet populated — run thesis_checker.py")
        _cache.set(cache_key, result, TTL_SHORT)
        conn.close()
        return result

    try:
        # Determine this ticker's current direction from thesis_cache
        current = conn.execute("""
            SELECT direction, conviction, signal_agreement_score
            FROM thesis_cache WHERE ticker = ?
            ORDER BY date DESC, created_at DESC LIMIT 1
        """, (sym,)).fetchone()
        direction = current["direction"] if current else "BULL"

        # Fetch resolved analogs universe-wide for the same direction
        rows = conn.execute("""
            SELECT ticker, thesis_date, direction, conviction,
                   signal_agreement_score,
                   entry_price, target_1, target_2, stop_loss,
                   hit_target_1, hit_target_2, hit_stop,
                   return_30d, days_to_target_1, days_to_stop,
                   outcome, claude_correct
            FROM thesis_outcomes
            WHERE outcome NOT IN ('OPEN', 'EXPIRED')
              AND direction = ?
            ORDER BY thesis_date DESC
            LIMIT ?
        """, (direction, limit)).fetchall()
        conn.close()

        analogs = []
        for r in rows:
            d = dict(r)
            entry = d.get("entry_price")
            t1    = d.get("target_1")
            t2    = d.get("target_2")
            sl    = d.get("stop_loss")

            # R-multiple for T1 and T2 relative to the risk (entry→stop)
            t1_r = t2_r = None
            if entry and sl and abs(entry - sl) > 0:
                risk = abs(entry - sl)
                if t1: t1_r = round((t1 - entry) / risk, 2)
                if t2: t2_r = round((t2 - entry) / risk, 2)

            analogs.append({
                "ticker":           d["ticker"],
                "date":             d["thesis_date"],
                "direction":        d["direction"],
                "conviction":       d["conviction"],
                "signal_agreement": d["signal_agreement_score"],
                "hit_t1":           bool(d["hit_target_1"]),
                "hit_t2":           bool(d["hit_target_2"]),
                "hit_stop":         bool(d["hit_stop"]),
                "return_30d":       d["return_30d"],
                "days_to_t1":       d["days_to_target_1"],
                "days_to_stop":     d["days_to_stop"],
                "outcome":          d["outcome"],
                "t1_r":             t1_r,
                "t2_r":             t2_r,
            })

        n = len(analogs)

        # Aggregate summary
        win_t1  = sum(1 for a in analogs if a["hit_t1"])
        win_t2  = sum(1 for a in analogs if a["hit_t2"])
        stopped = sum(1 for a in analogs if a["hit_stop"])

        hold_days = [a["days_to_t1"] for a in analogs if a["hit_t1"] and a["days_to_t1"] is not None]
        avg_hold = round(sum(hold_days) / len(hold_days), 1) if hold_days else None

        # Average T1 R-multiple across winning trades
        t1_rs = [a["t1_r"] for a in analogs if a["hit_t1"] and a["t1_r"] is not None]
        avg_t1_r = round(sum(t1_rs) / len(t1_rs), 2) if t1_rs else None

        # Expectancy = win_rate × avg_win_R + (1 - win_rate) × (-1R)
        win_rate_t1  = round(win_t1  / n * 100, 1) if n > 0 else None
        win_rate_t2  = round(win_t2  / n * 100, 1) if n > 0 else None
        stop_rate    = round(stopped / n * 100, 1) if n > 0 else None
        expectancy_r = None
        if win_rate_t1 is not None and avg_t1_r is not None:
            w = win_rate_t1 / 100
            expectancy_r = round(w * avg_t1_r + (1 - w) * (-1.0), 2)

        summary = {
            "total":           n,
            "direction":       direction,
            "win_rate_t1_pct": win_rate_t1,
            "win_rate_t2_pct": win_rate_t2,
            "stop_rate_pct":   stop_rate,
            "avg_hold_days":   avg_hold,
            "avg_t1_r":        avg_t1_r,
            "expectancy_r":    expectancy_r,
        }

        result = {
            "data_available": bool(analogs),
            "ticker":  sym,
            "summary": summary,
            "data":    analogs,
        }
        _cache.set(cache_key, result, TTL_SHORT)
        return result

    except Exception as e:
        log.exception("ticker_analogs error for %s", sym)
        return _no_data(str(e))


@app.get("/api/ticker/{symbol}/ohlcv")
async def ticker_ohlcv(
    symbol: str,
    period: str = Query("3M", pattern="^(1M|3M|6M|1Y)$"),
):
    """
    Daily OHLCV price history for the interactive candlestick chart.
    Powered by yfinance. Cached 15 min (intraday freshness).

    period: 1M | 3M | 6M | 1Y
    Returns list of { date, open, high, low, close, volume }.
    """
    sym = symbol.upper()
    cache_key = f"ohlcv:{sym}:{period}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    period_map = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y"}
    yf_period = period_map.get(period, "3mo")

    try:
        tk = yf.Ticker(sym)
        hist = tk.history(period=yf_period, interval="1d", auto_adjust=True)
        if hist is None or hist.empty:
            result = _no_data(f"No OHLCV data available for {sym}")
            _cache.set(cache_key, result, TTL_SHORT)
            return result

        bars = []
        for dt, row in hist.iterrows():
            bars.append({
                "date":   dt.strftime("%Y-%m-%d"),
                "open":   round(float(row["Open"]),  2),
                "high":   round(float(row["High"]),  2),
                "low":    round(float(row["Low"]),   2),
                "close":  round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            })

        result = {
            "data_available": True,
            "ticker": sym,
            "period": period,
            "data":   bars,
        }
        _cache.set(cache_key, result, 15 * 60)
        return result

    except Exception as e:
        log.exception("ticker_ohlcv error for %s", sym)
        return _no_data(str(e))


@app.get("/api/ticker/{symbol}/earnings-reactions")
async def ticker_earnings_reactions(symbol: str):
    """
    Historical post-earnings price reactions for the last 8 quarters.

    For each reported quarter:
      - Looks up the announcement timestamp from yfinance earnings_dates
      - Computes day-before-close → day-of/after-close price change
      - Returns raw reactions + summary statistics

    Summary includes: median move, +1SD, beat/miss split,
    avg beat reaction vs avg miss reaction.
    Cached 4 h (earnings dates don't change intraday).
    """
    sym = symbol.upper()
    cache_key = f"earnings_reactions:{sym}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    _empty = {"data_available": False, "ticker": sym, "summary": {}, "data": []}

    try:
        tk = yf.Ticker(sym)

        # ── Earnings announcement dates ──────────────────────────────────────
        ed = tk.earnings_dates
        if ed is None or ed.empty:
            _cache.set(cache_key, _empty, 3600)
            return _empty

        # Keep only reported quarters (not forward estimates), most recent 8
        try:
            reported = ed[ed["Reported EPS"].notna()].head(8)
        except (KeyError, TypeError):
            _cache.set(cache_key, _empty, 3600)
            return _empty

        if reported.empty:
            _cache.set(cache_key, _empty, 3600)
            return _empty

        # ── 2-year price history for reaction lookups ────────────────────────
        hist = tk.history(period="2y", interval="1d", auto_adjust=True)
        if hist is None or hist.empty:
            _cache.set(cache_key, _empty, 3600)
            return _empty

        # Normalize history index to naive UTC dates
        try:
            hist.index = hist.index.normalize()
            if hist.index.tz is not None:
                hist.index = hist.index.tz_localize(None)
        except Exception:
            pass

        reactions = []
        for ann_ts, row in reported.iterrows():
            try:
                # Normalize announcement timestamp → naive date
                ann_dt = ann_ts
                if hasattr(ann_dt, "normalize"):
                    ann_dt = ann_dt.normalize()
                if hasattr(ann_dt, "tz_localize") and ann_dt.tzinfo is not None:
                    ann_dt = ann_dt.tz_localize(None)

                ann_str = ann_dt.strftime("%Y-%m-%d")

                # Prices before announcement (use previous close as baseline)
                prices_before = hist[hist.index < ann_dt]["Close"]
                # Prices from announcement day forward
                prices_from   = hist[hist.index >= ann_dt]["Close"]

                if len(prices_before) == 0 or len(prices_from) == 0:
                    continue

                pre_close  = float(prices_before.iloc[-1])
                post_close = float(prices_from.iloc[0])

                if pre_close == 0:
                    continue

                reaction_pct = (post_close - pre_close) / pre_close * 100

                # 5-day drift after the announcement day
                drift_5d_pct = None
                if len(prices_from) >= 5:
                    close_5d = float(prices_from.iloc[4])
                    drift_5d_pct = round((close_5d - post_close) / post_close * 100, 2)

                eps_actual   = _safe_float(row.get("Reported EPS"))
                eps_estimate = _safe_float(row.get("EPS Estimate"))
                surprise_pct = _safe_float(row.get("Surprise(%)"))
                beat = (
                    bool(eps_actual > eps_estimate)
                    if eps_actual is not None and eps_estimate is not None
                    else None
                )

                reactions.append({
                    "date":             ann_str,
                    "eps_actual":       eps_actual,
                    "eps_estimate":     eps_estimate,
                    "eps_surprise_pct": surprise_pct,
                    "beat":             beat,
                    "pre_close":        round(pre_close,  2),
                    "post_close":       round(post_close, 2),
                    "reaction_pct":     round(reaction_pct, 2),
                    "drift_5d_pct":     drift_5d_pct,
                })
            except Exception:
                continue

        if not reactions:
            _cache.set(cache_key, _empty, 3600)
            return _empty

        # ── Summary statistics ───────────────────────────────────────────────
        def _median(lst: list):
            if not lst:
                return None
            s = sorted(lst)
            m = len(s) // 2
            return round(s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2, 2)

        def _std(lst: list):
            if len(lst) < 2:
                return None
            mean = sum(lst) / len(lst)
            return round((sum((x - mean) ** 2 for x in lst) / (len(lst) - 1)) ** 0.5, 2)

        all_moves  = [r["reaction_pct"]     for r in reactions]
        abs_moves  = [abs(r["reaction_pct"]) for r in reactions]
        beats      = [r for r in reactions if r["beat"] is True]
        misses     = [r for r in reactions if r["beat"] is False]
        beat_moves = [r["reaction_pct"]  for r in beats]
        miss_moves = [r["reaction_pct"]  for r in misses]

        n = len(reactions)
        std_val = _std(all_moves)
        med_abs = _median(abs_moves)
        avg_abs = round(sum(abs_moves) / len(abs_moves), 2) if abs_moves else None

        summary = {
            "total":                    n,
            "beat_count":               len(beats),
            "miss_count":               len(misses),
            "beat_rate_pct":            round(len(beats) / n * 100, 1) if n else None,
            "median_abs_move_pct":      med_abs,
            "avg_abs_move_pct":         avg_abs,
            "std_move_pct":             std_val,
            "plus_1sd_pct":             round((med_abs or 0) + (std_val or 0), 2),
            "minus_1sd_pct":            round((med_abs or 0) - (std_val or 0), 2),
            "median_beat_reaction_pct": _median(beat_moves),
            "median_miss_reaction_pct": _median(miss_moves),
        }

        result = {
            "data_available": True,
            "ticker":  sym,
            "summary": summary,
            "data":    list(reversed(reactions)),  # oldest → newest for chart
        }
        _cache.set(cache_key, result, 4 * 3600)
        return result

    except Exception as e:
        log.exception("ticker_earnings_reactions error for %s", sym)
        return {**_empty, "error": str(e)}


# ==============================================================================
# AUTH ENDPOINTS
# ==============================================================================

@app.get("/api/auth/me")
async def auth_me(user: AuthUser = Depends(get_current_user)):
    """Return current authenticated user info."""
    return {"user_id": user.user_id, "email": user.email, "auth_method": user.auth_method}


@app.post("/api/auth/keys")
async def auth_create_key(
    name: str = Query(default="Default key", description="Friendly label for this key"),
    user: AuthUser = Depends(get_current_user),
):
    """
    Generate a new API key for the authenticated user.
    The raw key is returned ONCE — store it securely. It cannot be recovered.
    """
    if not _AUTH_AVAILABLE:
        raise HTTPException(status_code=501, detail="Auth module not available")
    result = create_api_key(user_id=user.user_id, email=user.email, name=name)
    return result


@app.get("/api/auth/keys")
async def auth_list_keys(user: AuthUser = Depends(get_current_user)):
    """List all active API keys for the authenticated user (prefixes only, no raw keys)."""
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """SELECT id, key_prefix, name, created_at, last_used
               FROM user_api_keys
               WHERE user_id = %s AND revoked = FALSE
               ORDER BY created_at DESC""",
            (user.user_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return {"keys": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/api/auth/keys/{key_id}")
async def auth_revoke_key(key_id: int, user: AuthUser = Depends(get_current_user)):
    """Revoke an API key by ID. Only the owner can revoke their own keys."""
    if not _AUTH_AVAILABLE:
        raise HTTPException(status_code=501, detail="Auth module not available")
    ok = revoke_api_key(key_id, user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Key not found or already revoked")
    return {"revoked": True, "key_id": key_id}


# ==============================================================================
# ALERTS ENDPOINTS
# ==============================================================================

@app.post("/api/alerts/telegram")
async def send_telegram_alert(
    dry_run: bool = Query(True),
    user: AuthUser = Depends(get_current_user),
):
    """
    Trigger scripts/send_weekly_alert.py.
    dry_run=True (default) prints the message without sending.
    dry_run=False sends to Telegram/Discord.
    """
    script = BASE_DIR / "scripts" / "send_weekly_alert.py"
    if not script.exists():
        raise HTTPException(status_code=404, detail="send_weekly_alert.py not found")

    import os as _os
    sub_env = dict(_os.environ)

    args = [sys.executable, str(script)]
    if dry_run:
        args.append("--dry-run")

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(BASE_DIR),
            env=sub_env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode(errors="replace").strip()
        return {
            "sent": proc.returncode == 0 and not dry_run,
            "dry_run": dry_run,
            "output": output,
            "returncode": proc.returncode,
        }
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Alert script timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==============================================================================
# USAGE ENDPOINTS
# ==============================================================================

@app.get("/api/usage/summary")
async def usage_summary(
    days: int = Query(default=30, ge=1, le=365),
    user: AuthUser = Depends(get_current_user),
):
    """
    Return API usage stats for the authenticated user over the last N days.
    Includes total calls, tokens, cost, and per-module breakdown.
    """
    try:
        from utils.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()

        cur.execute(
            """SELECT
                   module,
                   COUNT(*) AS calls,
                   SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) AS cache_hits,
                   SUM(input_tokens)  AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cost_usd)      AS cost_usd
               FROM api_usage
               WHERE created_at >= %s
                 AND (user_id = %s OR user_id IS NULL)
               GROUP BY module
               ORDER BY cost_usd DESC""",
            (since, user.user_id),
        )
        by_module = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """SELECT
                   COUNT(*) AS total_calls,
                   SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) AS cache_hits,
                   SUM(input_tokens)  AS total_input_tokens,
                   SUM(output_tokens) AS total_output_tokens,
                   SUM(cost_usd)      AS total_cost_usd
               FROM api_usage
               WHERE created_at >= %s
                 AND (user_id = %s OR user_id IS NULL)""",
            (since, user.user_id),
        )
        totals = dict(cur.fetchone() or {})
        conn.close()

        return {
            "period_days":  days,
            "totals":       totals,
            "by_module":    by_module,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ==============================================================================
# FAVORITES ENDPOINTS
# ==============================================================================

def _ensure_favorites_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_favorites (
                id        SERIAL PRIMARY KEY,
                symbol    TEXT NOT NULL UNIQUE,
                added_at  TIMESTAMPTZ DEFAULT NOW(),
                notes     TEXT DEFAULT ''
            )
        """)
    conn.commit()


@app.get("/api/favorites")
async def get_favorites():
    """Return all user-pinned favorite tickers."""
    try:
        conn = _db_connect()
        if conn is None:
            return {"favorites": []}
        _ensure_favorites_table(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT symbol, added_at, notes FROM user_favorites ORDER BY added_at")
            rows = cur.fetchall()
        conn.close()
        return {"favorites": [
            {"symbol": r["symbol"], "added_at": str(r["added_at"]), "notes": r.get("notes", "")}
            for r in rows
        ]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/favorites/{symbol}")
async def add_favorite(symbol: str):
    """Add a ticker to favorites."""
    symbol = symbol.upper().strip()
    if not symbol or len(symbol) > 10:
        raise HTTPException(status_code=400, detail="Invalid symbol")
    try:
        conn = _db_connect()
        if conn is None:
            raise HTTPException(status_code=503, detail="Database unavailable")
        _ensure_favorites_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_favorites (symbol) VALUES (%s) ON CONFLICT (symbol) DO NOTHING",
                (symbol,),
            )
        conn.commit()
        conn.close()
        # Sync to watchlist.txt in the background
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent))
            from favorites import sync_to_watchlist
            sync_to_watchlist()
        except Exception:
            pass
        return {"ok": True, "symbol": symbol}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/api/favorites/{symbol}")
async def remove_favorite(symbol: str):
    """Remove a ticker from favorites."""
    symbol = symbol.upper().strip()
    try:
        conn = _db_connect()
        if conn is None:
            raise HTTPException(status_code=503, detail="Database unavailable")
        _ensure_favorites_table(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_favorites WHERE symbol = %s", (symbol,))
        conn.commit()
        conn.close()
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent))
            from favorites import sync_to_watchlist
            sync_to_watchlist()
        except Exception:
            pass
        return {"ok": True, "symbol": symbol}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ==============================================================================
# SECTION 20: GITHUB ACTIONS WORKFLOWS
# ==============================================================================

_GH_TOKEN = os.getenv("GITHUB_TOKEN", "")
_GH_REPO  = os.getenv("GITHUB_REPO", "jsonidx/signal_engine_v1")

_WORKFLOW_META = {
    "daily_pipeline.yml":  {"label": "Daily Pipeline",  "has_ai": False, "cost": "€0.00"},
    "manual_pipeline.yml": {"label": "Manual Pipeline", "has_ai": True,  "cost": "~€0.03–0.05"},
}


@app.get("/api/workflows/runs")
async def get_workflow_runs(per_page: int = 15):
    """Fetch recent GitHub Actions workflow runs for all pipelines."""
    if not _GH_TOKEN:
        return {"runs": [], "error": "GITHUB_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {_GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"https://api.github.com/repos/{_GH_REPO}/actions/runs"

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, headers=headers, params={"per_page": per_page})
        if r.status_code != 200:
            return {"runs": [], "error": f"GitHub API returned {r.status_code}"}

        raw_runs = r.json().get("workflow_runs", [])
        runs = []
        for run in raw_runs:
            path = run.get("path", "")                     # ".github/workflows/daily_pipeline.yml"
            filename = path.split("/")[-1] if path else ""
            meta = _WORKFLOW_META.get(filename, {"label": run.get("name", filename), "has_ai": None, "cost": None})

            # duration in seconds
            started  = run.get("run_started_at") or run.get("created_at")
            updated  = run.get("updated_at")
            duration = None
            if started and updated and run.get("status") == "completed":
                try:
                    from datetime import timezone
                    s = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    u = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    duration = int((u - s).total_seconds())
                except Exception:
                    pass

            runs.append({
                "id":           run["id"],
                "run_number":   run.get("run_number"),
                "workflow_file": filename,
                "label":        meta["label"],
                "has_ai":       meta["has_ai"],
                "cost":         meta["cost"],
                "status":       run.get("status"),          # queued | in_progress | completed
                "conclusion":   run.get("conclusion"),      # success | failure | cancelled | null
                "event":        run.get("event"),           # schedule | workflow_dispatch
                "created_at":   run.get("created_at"),
                "updated_at":   run.get("updated_at"),
                "duration_secs": duration,
                "html_url":     run.get("html_url"),
                "head_branch":  run.get("head_branch"),
            })

        return {"runs": runs}

    except Exception as exc:
        return {"runs": [], "error": str(exc)}


@app.get("/api/workflows/report")
async def download_workflow_report():
    """Download the latest pipeline run report (quant_reports/0_run-pipeline.txt)."""
    report_path = BASE_DIR / "quant_reports" / "0_run-pipeline.txt"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report file not found")
    return FileResponse(
        path=str(report_path),
        media_type="text/plain",
        filename="0_run-pipeline.txt",
        headers={"Content-Disposition": "attachment; filename=0_run-pipeline.txt"},
    )


@app.get("/api/workflows/report/text")
async def get_workflow_report_text():
    """
    Return the latest clean pipeline report for LLM prompt building.
    Reads from the Supabase pipeline_reports table, written by
    scripts/upload_pipeline_report.py at the end of every workflow run.
    The content is pure pipeline stdout — no CI runner noise.
    """
    try:
        conn = _db_connect()
        if conn is None:
            raise HTTPException(status_code=503, detail="Database unavailable")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, run_id, workflow_name, conclusion, content,
                       run_at AT TIME ZONE 'UTC' AS run_at
                FROM pipeline_reports
                ORDER BY id DESC
                LIMIT 1
            """)
            row = cur.fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="No pipeline report found — run the pipeline once to generate one")
        label = f"{row['workflow_name']} ({row['conclusion']}) — {str(row['run_at'])[:19]} UTC"
        return {
            "content":    row["content"],
            "run_id":     row["run_id"],
            "label":      label,
            "conclusion": row["conclusion"],
            "run_at":     str(row["run_at"]),
            "source":     "supabase",
        }
    except HTTPException:
        raise
    except Exception as exc:
        msg = str(exc)
        if "pipeline_reports" in msg and "does not exist" in msg:
            raise HTTPException(status_code=404, detail="No pipeline report yet — will be available after the next workflow run completes")
        raise HTTPException(status_code=500, detail=msg)


# ─── Model Accuracy Benchmark ─────────────────────────────────────────────────

@app.get("/api/thesis/benchmark")
async def thesis_benchmark(days: int = 90):
    """
    Per-model accuracy stats from thesis_outcomes JOIN thesis_cache.
    Returns summary scorecards + recent per-ticker outcomes.
    """
    cache_key = f"thesis_benchmark:{days}"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    try:
        from datetime import date as _date, timedelta
        cutoff = str(_date.today() - timedelta(days=days))

        conn = _db_connect()

        # ── Model summary ──────────────────────────────────────────────────────
        summary_rows = conn.execute("""
            SELECT
                COALESCE(tc.model_used, 'unknown') AS model,
                COUNT(*)                            AS theses,
                SUM(CASE WHEN o.hit_target_1 THEN 1 ELSE 0 END)  AS t1_hits,
                SUM(CASE WHEN o.hit_target_2 THEN 1 ELSE 0 END)  AS t2_hits,
                SUM(CASE WHEN o.hit_stop    THEN 1 ELSE 0 END)   AS stop_hits,
                SUM(CASE WHEN o.outcome IN ('HIT_TARGET1','HIT_TARGET2') THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN o.outcome = 'HIT_STOP'   THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN o.outcome = 'OPEN'       THEN 1 ELSE 0 END) AS open_count,
                -- best available return: 30d actual > 7d actual > outcome-implied
                AVG(CASE
                    WHEN o.return_30d IS NOT NULL THEN o.return_30d
                    WHEN o.return_7d  IS NOT NULL THEN o.return_7d
                    WHEN o.outcome = 'HIT_TARGET1' AND o.entry_price > 0 AND o.target_1 IS NOT NULL
                        THEN (o.target_1 - o.entry_price) / o.entry_price * 100
                            * CASE WHEN o.direction = 'BEAR' THEN -1 ELSE 1 END
                    WHEN o.outcome = 'HIT_TARGET2' AND o.entry_price > 0 AND o.target_2 IS NOT NULL
                        THEN (o.target_2 - o.entry_price) / o.entry_price * 100
                            * CASE WHEN o.direction = 'BEAR' THEN -1 ELSE 1 END
                    WHEN o.outcome = 'HIT_STOP' AND o.entry_price > 0 AND o.stop_loss IS NOT NULL
                        THEN (o.stop_loss - o.entry_price) / o.entry_price * 100
                            * CASE WHEN o.direction = 'BEAR' THEN -1 ELSE 1 END
                    END
                ) AS avg_return_30d,
                AVG(CASE WHEN o.days_to_target_1 IS NOT NULL THEN o.days_to_target_1 END) AS avg_days_to_t1,
                AVG(CASE WHEN o.vs_target_1_pct IS NOT NULL THEN o.vs_target_1_pct END)   AS avg_vs_t1_pct,
                SUM(CASE WHEN o.direction = 'BULL' THEN 1 ELSE 0 END) AS bull_count,
                SUM(CASE WHEN o.direction = 'BEAR' THEN 1 ELSE 0 END) AS bear_count,
                SUM(CASE WHEN o.direction = 'NEUTRAL' THEN 1 ELSE 0 END) AS neutral_count
            FROM thesis_outcomes o
            JOIN thesis_cache tc ON tc.id = o.thesis_id
            WHERE o.thesis_date >= %s
            GROUP BY COALESCE(tc.model_used, 'unknown')
            ORDER BY theses DESC
        """, (cutoff,)).fetchall()

        summary = []
        for r in summary_rows:
            theses  = r["theses"] or 0
            wins    = r["wins"] or 0
            losses  = r["losses"] or 0
            resolved = wins + losses
            win_rate = round(wins / resolved * 100, 1) if resolved > 0 else None
            t1_rate  = round((r["t1_hits"] or 0) / theses * 100, 1) if theses > 0 else None
            t2_rate  = round((r["t2_hits"] or 0) / theses * 100, 1) if theses > 0 else None
            stop_rate = round((r["stop_hits"] or 0) / theses * 100, 1) if theses > 0 else None
            summary.append({
                "model":          r["model"],
                "theses":         theses,
                "wins":           wins,
                "losses":         losses,
                "open_count":     r["open_count"] or 0,
                "win_rate_pct":   win_rate,
                "t1_hit_rate_pct": t1_rate,
                "t2_hit_rate_pct": t2_rate,
                "stop_rate_pct":  stop_rate,
                "avg_return_30d": round(float(r["avg_return_30d"]), 2) if r["avg_return_30d"] else None,
                "avg_days_to_t1": round(float(r["avg_days_to_t1"]), 1) if r["avg_days_to_t1"] else None,
                "avg_vs_t1_pct":  round(float(r["avg_vs_t1_pct"]), 2) if r["avg_vs_t1_pct"] else None,
                "bull_count":     r["bull_count"] or 0,
                "bear_count":     r["bear_count"] or 0,
                "neutral_count":  r["neutral_count"] or 0,
            })

        # ── Per-ticker outcomes ────────────────────────────────────────────────
        recent_rows = conn.execute("""
            SELECT
                o.thesis_date, o.ticker, o.direction, o.conviction,
                o.outcome, o.hit_target_1, o.hit_target_2, o.hit_stop,
                o.return_7d, o.return_30d,
                o.days_to_target_1, o.days_to_target_2, o.days_to_stop,
                o.vs_target_1_pct, o.vs_target_2_pct,
                o.entry_price, o.target_1, o.target_2, o.stop_loss,
                COALESCE(tc.model_used, 'unknown') AS model
            FROM thesis_outcomes o
            JOIN thesis_cache tc ON tc.id = o.thesis_id
            WHERE o.thesis_date >= %s
            ORDER BY o.thesis_date DESC, o.ticker
        """, (cutoff,)).fetchall()

        def _outcome_return(r) -> float | None:
            """Best available return: 30d > 7d > outcome-implied from prices."""
            if r["return_30d"] is not None:
                return float(r["return_30d"])
            if r["return_7d"] is not None:
                return float(r["return_7d"])
            ep = r["entry_price"]
            if not ep or ep <= 0:
                return None
            bear = r["direction"] == "BEAR"
            if r["outcome"] == "HIT_TARGET1" and r["target_1"]:
                raw = (r["target_1"] - ep) / ep * 100
                return -raw if bear else raw
            if r["outcome"] == "HIT_TARGET2" and r["target_2"]:
                raw = (r["target_2"] - ep) / ep * 100
                return -raw if bear else raw
            if r["outcome"] == "HIT_STOP" and r["stop_loss"]:
                raw = (r["stop_loss"] - ep) / ep * 100
                return -raw if bear else raw
            return None

        recent = []
        for r in recent_rows:
            row = dict(r)
            row["outcome_return_pct"] = _outcome_return(r)
            recent.append(row)

        conn.close()

        result = {
            "data_available": True,
            "days":    days,
            "summary": _json_safe(summary),
            "recent":  _json_safe(recent),
        }
        _cache.set(cache_key, result, TTL_LONG)
        return result

    except Exception:
        log.exception("thesis_benchmark error")
        return _no_data("thesis_benchmark failed")


# ─── Settings ─────────────────────────────────────────────────────────────────

SETTINGS_SCHEMA = [
    # key, label, group, type, default, description, options (for select)
    # ── AI Analysis ───────────────────────────────────────────────────────────
    ("ai_model_default",       "Default LLM",              "AI Analysis",   "select",  "grok-4-1-fast-reasoning",
     "Model used for all standard ai_quant runs",
     ["grok-4-1-fast-reasoning", "grok-4.20-0309-reasoning", "claude-sonnet-4-6"]),
    ("ai_model_premium",       "Premium LLM",              "AI Analysis",   "select",  "grok-4.20-0309-reasoning",
     "Model used for high-conviction / manual deep-dive re-runs",
     ["grok-4-1-fast-reasoning", "grok-4.20-0309-reasoning", "claude-sonnet-4-6", "claude-opus-4-7"]),
    ("ai_model_fallback",      "Fallback LLM",             "AI Analysis",   "select",  "grok-4-1-fast-reasoning",
     "Retry model if primary call fails",
     ["grok-4-1-fast-reasoning", "claude-sonnet-4-6"]),
    ("ai_min_conviction_score","Min Conviction Score",     "AI Analysis",   "number",  "13",
     "Minimum composite catalyst score for a ticker to qualify for AI analysis (0–100)"),
    # ── Calibration ───────────────────────────────────────────────────────────
    ("calibration_min_sample", "Min Sample for Calibration","Calibration",  "number",  "20",
     "Minimum resolved thesis outcomes required before applying target calibration"),
    ("calibration_window",     "Calibration Window",       "Calibration",   "number",  "60",
     "Max number of most-recent resolved outcomes to use for bias calculation"),
    # ── Portfolio & Sizing ────────────────────────────────────────────────────
    ("kelly_fraction",         "Kelly Fraction",           "Portfolio",     "number",  "0.25",
     "Fraction of Kelly criterion to use for position sizing (0.25 = quarter-Kelly)"),
    ("max_position_equity_pct","Max Equity Position %",    "Portfolio",     "number",  "8",
     "Maximum single equity position as % of portfolio"),
    ("max_position_crypto_pct","Max Crypto Position %",    "Portfolio",     "number",  "10",
     "Maximum single crypto position as % of portfolio"),
    # ── Universe ──────────────────────────────────────────────────────────────
    ("universe_prescreen_top_n","Universe Top-N",          "Universe",      "number",  "200",
     "Number of tickers to pre-screen from index constituents each run"),
    ("universe_min_dollar_vol", "Min Dollar Volume ($)",   "Universe",      "number",  "3000000",
     "Minimum 30-day avg dollar volume to include in universe"),
    ("universe_min_price",      "Min Share Price ($)",     "Universe",      "number",  "1.5",
     "Minimum share price for universe inclusion"),
    ("universe_atr_pct_max",    "Max ATR % (20d)",         "Universe",      "number",  "6.0",
     "Exclude tickers with 20-day ATR% above this threshold"),
    ("universe_beta_max",       "Max Beta (60d vs SPY)",   "Universe",      "number",  "2.0",
     "Exclude tickers with 60-day beta above this"),
    # ── Alerts ────────────────────────────────────────────────────────────────
    ("telegram_bot_token",      "Telegram Bot Token",      "Alerts",        "secret",  "",
     "Bot token from @BotFather — stored encrypted, never shown in full"),
    ("telegram_chat_id",        "Telegram Chat ID",        "Alerts",        "string",  "",
     "Chat or channel ID to send alerts to"),
]

@app.get("/api/settings")
async def get_settings():
    """Return all settings with current values (from strategy_config) merged with schema defaults."""
    try:
        conn = _db_connect()
        rows = conn.execute("SELECT key, value FROM strategy_config").fetchall()
        conn.close()
        stored = {r["key"]: r["value"] for r in rows}

        groups: dict = {}
        for key, label, group, typ, default, desc, *rest in SETTINGS_SCHEMA:
            options = rest[0] if rest else None
            value = stored.get(key, default)
            display_value = "••••••••" if typ == "secret" and value else value
            groups.setdefault(group, []).append({
                "key":     key,
                "label":   label,
                "type":    typ,
                "value":   display_value,
                "default": default,
                "description": desc,
                **({"options": options} if options else {}),
            })

        return {"data_available": True, "groups": groups}
    except Exception:
        log.exception("get_settings error")
        return _no_data("settings unavailable")


@app.put("/api/settings/{key}")
async def update_setting(key: str, body: dict):
    """Upsert a single setting into strategy_config."""
    valid_keys = {row[0] for row in SETTINGS_SCHEMA}
    if key not in valid_keys:
        raise HTTPException(status_code=400, detail=f"Unknown setting key: {key}")

    value = str(body.get("value", ""))
    try:
        conn = _db_connect()
        conn.execute("""
            INSERT INTO strategy_config (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, (key, value))
        conn.commit()
        conn.close()
        # Bust cache so next run picks up new values
        _cache._store.pop("settings", None)
        return {"saved": True, "key": key, "value": value}
    except Exception:
        log.exception("update_setting error for %s", key)
        raise HTTPException(status_code=500, detail="Failed to save setting")


@app.get("/api/thesis/live-performance")
async def thesis_live_performance():
    """
    Open thesis_outcomes enriched with live prices.
    Computes current P&L %, distance to T1/T2/stop, and a progress status.
    """
    cache_key = "thesis_live_performance"
    hit = _cache.get(cache_key)
    if hit is not None:
        return hit

    try:
        conn = _db_connect()
        rows = conn.execute("""
            SELECT o.ticker, o.direction, o.conviction, o.thesis_date,
                   o.entry_price, o.target_1, o.target_2, o.stop_loss,
                   COALESCE(tc.model_used, 'unknown') AS model
            FROM thesis_outcomes o
            JOIN thesis_cache tc ON tc.id = o.thesis_id
            WHERE o.outcome = 'OPEN'
              AND o.entry_price IS NOT NULL
            ORDER BY o.thesis_date DESC
        """).fetchall()
        conn.close()

        tickers = list({r["ticker"] for r in rows})
        prices  = _fetch_current_prices(tickers) if tickers else {}

        def _status(direction, cur, entry, t1, t2, stop):
            bear = direction == "BEAR"
            if stop and (cur <= stop if bear else cur <= stop):
                return "AT_STOP"
            if t2 and (cur <= t2 if bear else cur >= t2):
                return "HIT_T2"
            if t1 and (cur <= t1 if bear else cur >= t1):
                return "HIT_T1"
            if entry:
                pnl = (entry - cur) / entry if bear else (cur - entry) / entry
                if pnl > 0.02:
                    return "ADVANCING"
                if pnl < -0.02:
                    return "RETREATING"
            return "FLAT"

        STATUS_SORT = {"HIT_T2": 0, "HIT_T1": 1, "ADVANCING": 2, "FLAT": 3, "RETREATING": 4, "AT_STOP": 5}

        data = []
        for r in rows:
            cur   = prices.get(r["ticker"])
            entry = float(r["entry_price"]) if r["entry_price"] else None
            t1    = float(r["target_1"])    if r["target_1"]    else None
            t2    = float(r["target_2"])    if r["target_2"]    else None
            stop  = float(r["stop_loss"])   if r["stop_loss"]   else None
            bear  = r["direction"] == "BEAR"

            pnl_pct       = None
            pct_to_t1     = None
            pct_to_t2     = None
            pct_to_stop   = None
            progress_t1   = None  # 0–1 how far toward T1 from entry

            if cur and entry:
                raw_pnl  = (entry - cur) / entry if bear else (cur - entry) / entry
                pnl_pct  = round(raw_pnl * 100, 2)
                if t1:
                    pct_to_t1 = round(((t1 - cur) / cur * 100) * (-1 if bear else 1), 2)
                    total_move = abs(t1 - entry)
                    done_move  = abs(cur - entry)
                    progress_t1 = round(min(1.0, done_move / total_move), 3) if total_move > 0 else 0
                if t2:
                    pct_to_t2 = round(((t2 - cur) / cur * 100) * (-1 if bear else 1), 2)
                if stop:
                    pct_to_stop = round(((stop - cur) / cur * 100) * (-1 if bear else 1), 2)

            status = _status(r["direction"], cur, entry, t1, t2, stop) if cur else "NO_PRICE"

            data.append({
                "ticker":       r["ticker"],
                "direction":    r["direction"],
                "conviction":   r["conviction"],
                "thesis_date":  str(r["thesis_date"]),
                "model":        r["model"],
                "entry_price":  entry,
                "current_price":cur,
                "target_1":     t1,
                "target_2":     t2,
                "stop_loss":    stop,
                "pnl_pct":      pnl_pct,
                "pct_to_t1":    pct_to_t1,
                "pct_to_t2":    pct_to_t2,
                "pct_to_stop":  pct_to_stop,
                "progress_t1":  progress_t1,
                "status":       status,
            })

        data.sort(key=lambda r: (STATUS_SORT.get(r["status"], 9), -(r["pnl_pct"] or 0)))

        result = {"data_available": True, "count": len(data), "data": _json_safe(data)}
        _cache.set(cache_key, result, TTL_MEDIUM)
        return result

    except Exception:
        log.exception("thesis_live_performance error")
        return _no_data("thesis_live_performance failed")
