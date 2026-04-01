#!/usr/bin/env python3
"""
utils/iv_calculator.py — True Implied Volatility Computation and History Store
================================================================================
Replaces the realized-vol proxy in options_flow.py with a real IV derived from
live options chains, and maintains a persistent SQLite IV history so that after
60 daily/weekly runs get_iv_rank() returns a proper IV rank (not an estimate).

PUBLIC API
----------
    compute_atm_iv(ticker, target_dte=30) -> float | None
        30-day ATM IV via Black-Scholes + Newton-Raphson on live options chain.

    get_iv_rank(ticker, current_iv, lookback_days=252) -> float | None
        IV Rank = (current - min) / (max - min)  over lookback window.
        Stores current_iv to data/iv_history.db before computing.
        Returns None until IV_MIN_HISTORY_DAYS rows exist.

    get_iv_percentile(ticker, current_iv, lookback_days=252) -> float | None
        Fraction of historical days where IV was below current_iv.
        More robust than rank against a single extreme outlier.

    get_iv_rank_and_percentile(ticker, current_iv, lookback_days=252)
        -> (iv_rank, iv_percentile)   ← preferred: one DB round-trip.

    collect_and_store_iv(tickers) -> {ticker: iv30}
        Batch: compute ATM IV for every ticker and persist to history DB.
        Run from run_master.sh to accumulate history over time.

MATH NOTES
----------
    * Black-Scholes European call price (no dividends, constant vol).
    * Newton-Raphson IV solver: σ_{n+1} = σ_n − (C(σ_n) − market) / vega(σ_n)
    * ATM straddle approach: straddle_mid / 2 ≈ ATM call price (valid at-the-money
      because put ≈ call by put-call parity when K ≈ S).
    * Risk-free rate defaults to IV_RISK_FREE_RATE (config, default 0.05).
"""

import logging
import math
import os
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import yfinance as yf

from utils.db import managed_connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config (with fallback for standalone / test use)
# ---------------------------------------------------------------------------
try:
    from config import (
        IV_HISTORY_DB,
        IV_MIN_HISTORY_DAYS,
        IV_RISK_FREE_RATE,
        IV_TARGET_DTE,
    )
except ImportError:
    IV_RISK_FREE_RATE = 0.05
    IV_TARGET_DTE = 30
    IV_MIN_HISTORY_DAYS = 60
    IV_HISTORY_DB = "data/iv_history.db"


# ===========================================================================
# SECTION 1: BLACK-SCHOLES MATH
# ===========================================================================

def _safe_float(val, default: float = 0.0) -> float:
    """Convert val to float; return default on NaN / None / conversion error."""
    try:
        f = float(val)
        return f if not math.isnan(f) else default
    except (TypeError, ValueError):
        return default


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erf (no external dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Black-Scholes European call price (no dividends).

    Parameters
    ----------
    S     : current spot price
    K     : strike price
    T     : time to expiry in years  (e.g. 30/365)
    r     : annualised risk-free rate (e.g. 0.05)
    sigma : annualised implied volatility (e.g. 0.25 for 25%)

    Returns intrinsic value max(S−K, 0) when T≤0 or sigma≤0.
    """
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Black-Scholes vega — ∂C/∂σ.

    Represents the dollar change in call price per 1-unit change in sigma.
    Used as the Newton-Raphson step denominator.
    Returns 0 when T≤0 or sigma≤0.
    """
    if T <= 0 or sigma <= 0:
        return 0.0
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    return S * _norm_pdf(d1) * sqrt_T


def implied_vol(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float = 0.05,
    initial_sigma: float = 0.3,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> Optional[float]:
    """
    Newton-Raphson implied volatility solver.

    Finds σ such that bs_call(S, K, T, r, σ) == market_price.

    Algorithm
    ---------
        σ_{n+1} = σ_n − (bs_call(σ_n) − market_price) / bs_vega(σ_n)

    Parameters
    ----------
    market_price  : observed option (or half-straddle) price to match
    S, K, T, r    : standard Black-Scholes inputs
    initial_sigma : starting vol guess (default 0.30)
    max_iter      : iteration cap
    tol           : convergence tolerance on |price_diff| (default 1e-6)

    Returns
    -------
    float σ in [0.001, 20.0] on convergence, or None if:
      - inputs are degenerate (T≤0, price≤0, etc.)
      - market_price is below intrinsic value
      - Newton-Raphson does not converge within max_iter steps
    """
    if market_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None

    # Price must not be below intrinsic (with small slack for rounding)
    intrinsic = max(S - K * math.exp(-r * T), 0.0)
    if market_price < intrinsic - 1e-4:
        return None

    sigma = max(initial_sigma, 1e-4)

    for _ in range(max_iter):
        price = bs_call(S, K, T, r, sigma)
        diff = price - market_price
        if abs(diff) < tol:
            return sigma if 0.001 <= sigma <= 20.0 else None
        vega = bs_vega(S, K, T, r, sigma)
        if vega < 1e-10:
            # Vega collapsed — nudge sigma upward and retry
            sigma *= 1.5
            continue
        sigma = sigma - diff / vega
        if sigma <= 0:
            sigma = 1e-4

    return None  # Did not converge


# ===========================================================================
# SECTION 2: ATM IV COMPUTATION FROM LIVE OPTIONS CHAIN
# ===========================================================================

def _compute_atm_iv_for_expiry(
    ticker_obj: yf.Ticker,
    expiry: str,
    price: float,
    r: float = 0.05,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Compute ATM implied vol for a single expiration using the ATM straddle.

    Steps
    -----
    1. Parse T (years to expiry).  Skip if T < 1 day.
    2. Fetch options chain for this expiry.
    3. Find the strike closest to current price (ATM strike).
    4. Get call_mid = (bid + ask) / 2 at ATM strike.
    5. Get put_mid at the same strike.
    6. Require bid > 0 on both legs — return (None, atm_strike) if illiquid.
    7. Solve: implied_vol(straddle_mid / 2, price, K, T, r).
       Using half-straddle as a call-price proxy is valid for ATM options
       because put ≈ call by near-parity when K ≈ S.

    Returns (iv: float | None, atm_strike: float | None).
    """
    today = date.today()
    try:
        exp_date = datetime.strptime(expiry, "%Y-%m-%d").date()
    except ValueError:
        return None, None

    T = (exp_date - today).days / 365.0
    if T < 1.0 / 365.0:  # Less than one calendar day — skip
        return None, None

    try:
        chain = ticker_obj.option_chain(expiry)
        calls = chain.calls.copy()
        puts = chain.puts.copy()
    except Exception:
        return None, None

    if calls.empty or puts.empty:
        return None, None

    # ATM call — strike closest to spot
    calls["_dist"] = (calls["strike"] - price).abs()
    atm_call_row = calls.nsmallest(1, "_dist").iloc[0]
    atm_strike = float(atm_call_row["strike"])

    call_bid = _safe_float(atm_call_row.get("bid"))
    call_ask = _safe_float(atm_call_row.get("ask"))
    if call_bid <= 0:
        return None, atm_strike  # Illiquid — no valid call market

    call_mid = (call_bid + call_ask) / 2.0

    # ATM put — same strike (may differ slightly if nearest put strike differs)
    puts["_dist"] = (puts["strike"] - atm_strike).abs()
    atm_put_row = puts.nsmallest(1, "_dist").iloc[0]

    put_bid = _safe_float(atm_put_row.get("bid"))
    put_ask = _safe_float(atm_put_row.get("ask"))
    if put_bid <= 0:
        return None, atm_strike  # Illiquid — no valid put market

    put_mid = (put_bid + put_ask) / 2.0
    straddle_mid = call_mid + put_mid

    if straddle_mid <= 0:
        return None, atm_strike

    # Back out IV from half-straddle ≈ ATM call price
    iv = implied_vol(straddle_mid / 2.0, price, atm_strike, T, r)
    return iv, atm_strike


def compute_atm_iv(ticker: str, target_dte: int = None) -> Optional[float]:
    """
    Compute the 30-day at-the-money implied volatility by interpolating between
    the two nearest expiration dates that bracket target_dte.

    Algorithm
    ---------
    1. Fetch current price and available expirations via yfinance.
    2. Filter: require ≥ 2 expirations with DTE > 0.
    3. Find bracketing expirations:
         - If an expiry exists both ≤ and > target_dte: true interpolation.
         - If all expirations are after target_dte: use the nearest one.
         - If all are before target_dte: use the furthest one.
    4. Compute ATM straddle IV for each selected expiry.
    5. Linearly interpolate:
         iv_30 = iv_near + (iv_far − iv_near) × weight
         where weight = (target_dte − near_dte) / (far_dte − near_dte)
    6. Return annualised IV as a decimal (0.25 = 25% IV).

    Returns None if:
      - Fewer than 2 expiration dates are available
      - Both ATM legs have bid = 0 (illiquid market)
      - Newton-Raphson fails to converge on all candidates
    """
    if target_dte is None:
        target_dte = IV_TARGET_DTE

    try:
        t = yf.Ticker(ticker)

        hist = t.history(period="5d")
        if hist.empty:
            return None
        price = float(hist["Close"].iloc[-1])
        if price <= 0:
            return None

        expirations = t.options
        if not expirations or len(expirations) < 2:
            return None

        today = date.today()
        exp_dtes: List[Tuple[int, str]] = []
        for exp in expirations:
            try:
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                dte = (exp_date - today).days
                if dte > 0:
                    exp_dtes.append((dte, exp))
            except ValueError:
                continue

        if len(exp_dtes) < 2:
            return None

        below = [(dte, exp) for dte, exp in exp_dtes if dte <= target_dte]
        above = [(dte, exp) for dte, exp in exp_dtes if dte > target_dte]

        if below and above:
            # True bracket: one expiry on each side of target_dte
            near_dte, near_exp = max(below, key=lambda x: x[0])
            far_dte, far_exp = min(above, key=lambda x: x[0])

            iv_near, _ = _compute_atm_iv_for_expiry(t, near_exp, price, IV_RISK_FREE_RATE)
            iv_far, _ = _compute_atm_iv_for_expiry(t, far_exp, price, IV_RISK_FREE_RATE)

            if iv_near is None and iv_far is None:
                return None
            if iv_near is None:
                return iv_far
            if iv_far is None:
                return iv_near

            weight = (target_dte - near_dte) / (far_dte - near_dte)
            return max(0.001, iv_near + (iv_far - iv_near) * weight)

        elif above:
            # All expirations are past target_dte — use the nearest available
            near_dte, near_exp = min(above, key=lambda x: x[0])
            iv, _ = _compute_atm_iv_for_expiry(t, near_exp, price, IV_RISK_FREE_RATE)
            return iv

        else:
            # All expirations are before target_dte — use the furthest available
            far_dte, far_exp = max(below, key=lambda x: x[0])
            iv, _ = _compute_atm_iv_for_expiry(t, far_exp, price, IV_RISK_FREE_RATE)
            return iv

    except Exception as e:
        logger.debug("compute_atm_iv(%s): %s", ticker, e)
        return None


# ===========================================================================
# SECTION 3: IV HISTORY DATABASE
# ===========================================================================

def _ensure_db(db_path: str) -> None:
    """
    Create the iv_history table if it doesn't exist.  Idempotent — safe to
    call on every access.  Also creates the parent directory if needed.

    Schema
    ------
    ticker      TEXT  — ticker symbol (uppercase)
    date        TEXT  — ISO date YYYY-MM-DD (one row per ticker per day)
    iv30        REAL  — 30-day ATM IV as a decimal (0.25 = 25%)
    atm_strike  REAL  — ATM strike used for the computation (informational)
    near_expiry TEXT  — Near expiration used
    far_expiry  TEXT  — Far expiration used (NULL if only one was available)
    computed_at TEXT  — UTC timestamp of computation
    PRIMARY KEY (ticker, date)  — one row per ticker per day, upsert-safe
    """
    dir_part = os.path.dirname(os.path.abspath(db_path))
    if dir_part:
        os.makedirs(dir_part, exist_ok=True)

    with managed_connection(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS iv_history (
                ticker      TEXT NOT NULL,
                date        TEXT NOT NULL,
                iv30        REAL NOT NULL,
                atm_strike  REAL,
                near_expiry TEXT,
                far_expiry  TEXT,
                computed_at TEXT,
                PRIMARY KEY (ticker, date)
            )
        """)
        conn.commit()


def _store_iv(
    ticker: str,
    iv30: float,
    db_path: str,
    atm_strike: Optional[float] = None,
    near_expiry: Optional[str] = None,
    far_expiry: Optional[str] = None,
) -> None:
    """Upsert today's ATM IV for a ticker."""
    today_str = date.today().isoformat()
    computed_at = datetime.utcnow().isoformat(timespec="seconds")
    with managed_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO iv_history
                (ticker, date, iv30, atm_strike, near_expiry, far_expiry, computed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, date) DO UPDATE SET
                iv30        = EXCLUDED.iv30,
                atm_strike  = EXCLUDED.atm_strike,
                near_expiry = EXCLUDED.near_expiry,
                far_expiry  = EXCLUDED.far_expiry,
                computed_at = EXCLUDED.computed_at
            """,
            (ticker, today_str, iv30, atm_strike, near_expiry, far_expiry, computed_at),
        )
        conn.commit()


def _get_iv_metrics(
    ticker: str,
    current_iv: float,
    lookback_days: int = 252,
    db_path: str = None,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Core computation: store current_iv then query history for rank + percentile.

    Order of operations (per spec):
        1. _ensure_db — create table if missing
        2. _store_iv  — persist today's IV (INSERT OR REPLACE)
        3. Query last `lookback_days` rows for this ticker (newest first)
        4. If fewer than IV_MIN_HISTORY_DAYS rows exist → return (None, None)
        5. Compute iv_rank = (current − min) / (max − min)
        6. Compute iv_percentile = count(iv < current) / total

    Returns (iv_rank, iv_percentile) each in [0.0, 1.0], or (None, None).
    """
    if db_path is None:
        db_path = IV_HISTORY_DB

    _ensure_db(db_path)
    _store_iv(ticker, current_iv, db_path)

    with managed_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT iv30 FROM iv_history
            WHERE ticker = %s
            ORDER BY date DESC
            LIMIT %s
            """,
            (ticker, lookback_days),
        ).fetchall()

    if len(rows) < IV_MIN_HISTORY_DAYS:
        return None, None

    values = [r[0] for r in rows]

    min_iv = min(values)
    max_iv = max(values)

    # IV Rank — sensitive to extremes but simple
    if max_iv > min_iv:
        iv_rank = (current_iv - min_iv) / (max_iv - min_iv)
        iv_rank = max(0.0, min(1.0, iv_rank))
    else:
        iv_rank = 0.5  # Degenerate: all stored IVs are identical

    # IV Percentile — fraction of days where historical IV was below current
    # More robust than rank: a single spike doesn't anchor the max at an outlier
    iv_percentile = sum(1 for v in values if v < current_iv) / len(values)

    return iv_rank, iv_percentile


# ---------------------------------------------------------------------------
# Public getters
# ---------------------------------------------------------------------------

def get_iv_rank(
    ticker: str,
    current_iv: float,
    lookback_days: int = 252,
    db_path: str = None,
) -> Optional[float]:
    """
    IV Rank = (current_iv − min_iv_52wk) / (max_iv_52wk − min_iv_52wk).

    Stores current_iv in iv_history.db before computing.
    Returns float in [0, 1] or None if fewer than IV_MIN_HISTORY_DAYS rows exist.
    """
    if db_path is None:
        db_path = IV_HISTORY_DB
    rank, _ = _get_iv_metrics(ticker, current_iv, lookback_days, db_path)
    return rank


def get_iv_percentile(
    ticker: str,
    current_iv: float,
    lookback_days: int = 252,
    db_path: str = None,
) -> Optional[float]:
    """
    IV Percentile = fraction of historical days where IV was below current_iv.

    Unlike IV rank, a single extreme outlier cannot anchor the max and compress
    all other readings — the percentile distribution remains stable.

    Stores current_iv in iv_history.db before computing.
    Returns float in [0, 1] or None if fewer than IV_MIN_HISTORY_DAYS rows exist.
    """
    if db_path is None:
        db_path = IV_HISTORY_DB
    _, percentile = _get_iv_metrics(ticker, current_iv, lookback_days, db_path)
    return percentile


def get_iv_rank_and_percentile(
    ticker: str,
    current_iv: float,
    lookback_days: int = 252,
    db_path: str = None,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Combined getter — compute both IV rank and IV percentile in a single DB
    round-trip.  Preferred over calling get_iv_rank + get_iv_percentile
    separately (avoids a double _store_iv write).

    Returns (iv_rank, iv_percentile) or (None, None) if insufficient history.
    Both values are floats in [0, 1] when available.
    """
    if db_path is None:
        db_path = IV_HISTORY_DB
    return _get_iv_metrics(ticker, current_iv, lookback_days, db_path)


# ===========================================================================
# SECTION 4: BATCH IV COLLECTION
# ===========================================================================

def collect_and_store_iv(tickers: List[str], db_path: str = None) -> Dict[str, float]:
    """
    Compute ATM IV for each ticker and persist to iv_history.db.

    Run this from run_master.sh on every pass to build IV history over time.
    After IV_MIN_HISTORY_DAYS consecutive runs (60 days by default), calls to
    get_iv_rank_and_percentile() will return real values instead of None.

    Skips crypto tickers (ending in -USD / -USDT).

    Returns {ticker: iv30_decimal} for tickers where computation succeeded.
    Logs: "{n} IV values computed, {k} failed (illiquid/missing options)"
    """
    if db_path is None:
        db_path = IV_HISTORY_DB

    _ensure_db(db_path)

    results: Dict[str, float] = {}
    failed = 0

    for ticker in tickers:
        if ticker.endswith("-USD") or ticker.endswith("-USDT"):
            continue
        iv = compute_atm_iv(ticker)
        if iv is not None:
            _store_iv(ticker, iv, db_path)
            results[ticker] = iv
        else:
            failed += 1

    n = len(results)
    logger.info(
        "%d IV values computed, %d failed (illiquid/missing options)", n, failed
    )
    print(f"  IV collection: {n} computed, {failed} failed (illiquid/missing options)")
    return results
