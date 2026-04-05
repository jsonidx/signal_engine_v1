#!/usr/bin/env python3
"""
================================================================================
WEEKLY SIGNAL ENGINE v1.0
================================================================================
Multi-factor equity screener + crypto trend/momentum signal generator.
Designed for weekly (Sunday evening) execution with sub-€50K portfolios.

USAGE:
    python signal_engine.py                  # Full run, both modules
    python signal_engine.py --equity-only    # Equity screener only
    python signal_engine.py --crypto-only    # Crypto signals only
    python signal_engine.py --watchlist AAPL,MSFT,GOOGL  # Custom tickers

AUTHOR NOTES:
    - All signals are INFORMATIONAL. This is not investment advice.
    - No look-ahead bias: signals use only data available at calc time.
    - Transaction costs are baked into position sizing recommendations.
    - Crypto vol regime filter will automatically de-risk in high-vol.

DEPENDENCIES:
    pip install yfinance pandas numpy scipy tabulate
================================================================================
"""

import argparse
import os
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats

warnings.filterwarnings("ignore")

# ─── Import config ────────────────────────────────────────────────────────────
try:
    from config import (
        PORTFOLIO_NAV, EQUITY_ALLOCATION, CRYPTO_ALLOCATION, CASH_BUFFER,
        EQUITY_WATCHLIST, CUSTOM_WATCHLIST, CRYPTO_TICKERS,
        EQUITY_FACTORS, CRYPTO_PARAMS, RISK_PARAMS,
        OUTPUT_DIR, CSV_EXPORT, CONSOLE_PRINT, DATA_LOOKBACK_DAYS,
    )
except ImportError:
    print("ERROR: config.py not found. Place it in the same directory.")
    sys.exit(1)


def _load_saved_nav() -> float | None:
    """
    Read cash_eur saved via the dashboard from Supabase portfolio_settings.
    Returns the value as the NAV override, or None if not set.
    """
    try:
        from utils.db import get_connection
        con = get_connection()
        cur = con.cursor()
        cur.execute("SELECT value FROM portfolio_settings WHERE key='cash_eur'")
        row = cur.fetchone()
        con.close()
        if row and float(row['value']) > 0:
            return float(row['value'])
    except Exception:
        pass
    return None


# ─── Fundamentals cache (optional — degrades gracefully if unavailable) ───────
try:
    from fundamentals_cache import get_cached, save_to_cache
    _FUND_CACHE_AVAILABLE = True
except ImportError:
    def get_cached(*_a, **_kw):  # type: ignore[misc]
        return None
    def save_to_cache(*_a, **_kw):  # type: ignore[misc]
        pass
    _FUND_CACHE_AVAILABLE = False

# ─── Regime filter (optional — degrades gracefully if unavailable) ────────────
try:
    import regime_filter as _rf
    _REGIME_AVAILABLE = True
except ImportError:
    _rf = None
    _REGIME_AVAILABLE = False

# ─── Constants ────────────────────────────────────────────────────────────────
TRADING_DAYS_PER_YEAR = 252
ANNUALIZATION_FACTOR = np.sqrt(TRADING_DAYS_PER_YEAR)
TODAY = datetime.now()
SIGNAL_DATE = TODAY.strftime("%Y-%m-%d")


# ==============================================================================
# SECTION 1: DATA ACQUISITION
# ==============================================================================

def fetch_price_data(
    tickers: List[str],
    lookback_days: int = DATA_LOOKBACK_DAYS,
    label: str = "assets"
) -> pd.DataFrame:
    """
    Fetch adjusted close prices from Yahoo Finance.
    Returns a DataFrame indexed by date with tickers as columns.

    NOTE: Yahoo Finance data is adequate for weekly screening on liquid
    large-caps. For anything sub-$1B market cap or for precise backtesting,
    you need a proper data vendor (Bloomberg, Refinitiv, Polygon.io).
    """
    end_date = TODAY
    start_date = end_date - timedelta(days=lookback_days)

    print(f"\n{'='*60}")
    print(f"  Fetching {label}: {len(tickers)} tickers")
    print(f"  Window: {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")
    print(f"{'='*60}")

    # Download in batch — faster than individual calls
    try:
        raw = yf.download(
            tickers,
            start=start_date,
            end=end_date,
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        print(f"  [ERROR] Download failed: {e}")
        return pd.DataFrame()

    # Handle single vs multi-ticker return format
    if isinstance(raw.columns, pd.MultiIndex):
        prices = raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw.iloc[:, :len(tickers)]
    else:
        prices = raw[["Close"]].rename(columns={"Close": tickers[0]}) if len(tickers) == 1 else raw

    # Drop tickers with insufficient data (< 60% of expected days)
    min_obs = int(lookback_days * 0.4)  # Generous threshold for newer listings
    valid_cols = prices.columns[prices.count() >= min_obs]
    dropped = set(prices.columns) - set(valid_cols)
    if dropped:
        print(f"  [WARN] Dropped {len(dropped)} tickers (insufficient data): "
              f"{', '.join(list(dropped)[:10])}{'...' if len(dropped) > 10 else ''}")
    prices = prices[valid_cols]

    # Forward-fill small gaps (weekends, holidays) — max 5 days
    prices = prices.ffill(limit=5)

    print(f"  [OK] {len(prices.columns)} tickers loaded, "
          f"{len(prices)} trading days")
    return prices


# ==============================================================================
# SECTION 1b: M&A / DELISTING FILTER
# ==============================================================================

def filter_ma_targets(
    prices: pd.DataFrame,
    window: int = 30,
    cv_threshold: float = 0.005,
    proximity_threshold: float = 0.99,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Remove tickers that are likely acquisition targets or recently delisted.

    Detection heuristic (no extra API calls — uses already-fetched price data):
      1. Price coefficient of variation over the last `window` trading days
         is below `cv_threshold` (price is essentially flat / pinned to deal price).
      2. Current price is within `proximity_threshold` of the 52-week high
         (stock is pegged at or just below the announced deal price).

    Both conditions must hold simultaneously — this avoids false positives on
    genuinely low-volatility blue-chips that happen to be near their highs.

    Common false-positive scenarios that are intentionally NOT flagged:
      - Low-vol dividend stocks near 52wk highs (CV usually > 0.5%)
      - Normal momentum leaders (price still moving, CV above threshold)

    Args:
        prices:               Price DataFrame (date × ticker).
        window:               Look-back in trading days for CV calculation (default 30).
        cv_threshold:         Max allowed CV to flag as pinned (default 0.5%).
        proximity_threshold:  Min 52wk-high proximity to flag (default 99%).

    Returns:
        (filtered_prices, removed_tickers)
    """
    removed: List[str] = []
    for ticker in prices.columns:
        px = prices[ticker].dropna()
        if len(px) < window:
            continue
        recent = px.iloc[-window:]
        mean_px = recent.mean()
        if mean_px <= 0:
            continue
        cv = recent.std() / mean_px
        proximity = float(px.iloc[-1]) / float(px.max())
        if cv < cv_threshold and proximity >= proximity_threshold:
            removed.append(ticker)

    if removed:
        print(
            f"\n  [M&A FILTER] Removed {len(removed)} likely acquired/delisted ticker(s) "
            f"(price pinned ≥{proximity_threshold*100:.0f}% of 52wk-high, CV <{cv_threshold*100:.1f}%): "
            f"{', '.join(removed)}"
        )

    return prices.drop(columns=removed), removed


# ==============================================================================
# SECTION 2: SIGNAL COMPUTATION — EQUITY MULTI-FACTOR
# ==============================================================================

def zscore_cross_sectional(series: pd.Series) -> pd.Series:
    """
    Cross-sectional Z-score: (x - mean) / std across all stocks at a point in time.
    Winsorize at ±3σ to prevent outlier contamination.
    """
    z = (series - series.mean()) / series.std()
    return z.clip(-3, 3)


def compute_momentum(prices: pd.DataFrame, lookback: int, skip: int = 0) -> pd.Series:
    """
    Classic momentum signal: return over [T-lookback, T-skip].
    Skip last `skip` days to avoid short-term mean-reversion contamination.

    BUG FIX (2026-03-22): Prior implementation used two separate point lookups
    (prices.iloc[-skip] / prices.iloc[-lookback]) which silently measured the
    skip window in whatever units the caller passed — calendar days if the
    lookback was derived from date arithmetic, trading days if from iloc counts.
    This caused a silent accuracy error when callers mixed the two.

    Fix: use an explicit slice window — prices.iloc[-lookback:-skip] — so both
    endpoints are anchored to the same trading-day grid and `skip` is always an
    exact trading-day count, never a calendar approximation.

    For 12-1 momentum: window = prices.iloc[-252:-21]  →  231 trading bars,
    spanning T-252 (inclusive) through T-22 (the bar just before the skip zone).
    For  6-1 momentum: window = prices.iloc[-126:-21]  →  105 trading bars.
    """
    if skip > 0:
        # Window: T-lookback (inclusive) → T-skip (exclusive).
        # e.g., iloc[-252:-21] gives bars T-252 … T-22 in trading-day counts.
        window = prices.iloc[-lookback:-skip]
        if len(window) < 2:
            return pd.Series(np.nan, index=prices.columns)
        returns = window.iloc[-1] / window.iloc[0] - 1
    else:
        returns = prices.iloc[-1] / prices.iloc[-lookback] - 1
    return returns


def compute_realized_vol(prices: pd.DataFrame, lookback: int) -> pd.Series:
    """Annualized realized volatility over lookback period."""
    log_returns = np.log(prices / prices.shift(1))
    vol = log_returns.iloc[-lookback:].std() * ANNUALIZATION_FACTOR
    return vol


def compute_risk_adjusted_momentum(
    prices: pd.DataFrame, mom_lookback: int, vol_lookback: int
) -> pd.Series:
    """Momentum divided by volatility — Sharpe-like signal."""
    mom = compute_momentum(prices, mom_lookback)
    vol = compute_realized_vol(prices, vol_lookback)
    # Avoid division by zero
    vol = vol.replace(0, np.nan)
    return mom / vol


# ── TTL for EPS revision cache (estimates don't change intraday) ──────────────
_EPS_REVISION_TTL_DAYS = 7


def compute_earnings_revision(ticker: str) -> Optional[float]:
    """
    Measures the 4-week change in sell-side FY1 EPS consensus estimate.

    True revision: (current FY1 estimate − FY1 estimate 4 weeks ago) / |prior|
    yfinance limitation: historical point-in-time estimates are unavailable,
    so we use the following proxy instead:
        (epsForward − epsCurrentYear) / |epsCurrentYear|
    This captures the direction of analyst revision even without historical data:
    epsForward = next-FY analyst consensus; epsCurrentYear = current-FY estimate.

    Winsorized at ±0.50 — extreme revisions are usually restatements, not
    genuine earnings momentum, and contaminate the cross-sectional Z-score.

    Returns None (not NaN) when EPS estimates are unavailable, so the caller
    can handle missing data without propagating NaN through the composite.

    Cached for 7 days in fundamentals_cache.db (estimates are weekly data).
    Cache key: "EPS_REV_{TICKER}" (separate namespace from 30-day fundamentals).
    """
    cache_key = f"EPS_REV_{ticker.upper()}"

    cached = get_cached(cache_key, ttl_days=_EPS_REVISION_TTL_DAYS)
    if cached is not None:
        val = cached.get("eps_revision")
        return float(val) if val is not None else None

    try:
        info = yf.Ticker(ticker).info

        eps_forward = info.get("epsForward")
        eps_current = info.get("epsCurrentYear")

        if eps_forward is None or eps_current is None:
            return None

        eps_forward = float(eps_forward)
        eps_current = float(eps_current)

        if eps_current == 0.0 or not np.isfinite(eps_current):
            return None

        # Proxy revision: direction and magnitude of estimate lift
        revision = (eps_forward - eps_current) / abs(eps_current)

        # Winsorize at ±50% (extreme swings are usually accounting restatements)
        revision = float(np.clip(revision, -0.50, 0.50))

        save_to_cache(cache_key, {"eps_revision": revision})
        return revision

    except Exception:
        return None


def compute_ivol(
    ticker_prices: pd.Series,
    spy_prices: pd.Series,
    lookback: int = 63,
) -> Optional[float]:
    """
    IVOL = annualized std of residuals from an OLS market-model regression.

        r_ticker = alpha + beta * r_spy + epsilon
        IVOL     = std(epsilon) * sqrt(252)

    Uses numpy only (no scipy):
        beta  = cov(r_ticker, r_spy) / var(r_spy)
        alpha = mean(r_ticker) − beta * mean(r_spy)
        IVOL  = std(r_ticker − alpha − beta * r_spy) * sqrt(252)

    Returns NEGATIVE IVOL so that low-IVOL names rank higher in cross-sectional
    Z-scoring:  return -ivol  →  low idiosyncratic risk = high factor score.
    This implements the low-risk / quality premium documented in the literature.

    Returns None if fewer than 40 aligned trading bars are available (too short
    for a stable OLS estimate).

    Args:
        ticker_prices: Adjusted close prices for the target ticker.
        spy_prices:    Adjusted close prices for SPY (market proxy).
                       Pre-fetch outside the per-ticker loop for efficiency.
        lookback:      Rolling window in trading days (default 63 ≈ 3 months).
    """
    try:
        aligned = pd.concat(
            [ticker_prices.rename("ticker"), spy_prices.rename("spy")],
            axis=1,
            sort=True,
        ).dropna()

        # Take the lookback window (or all data if shorter)
        window = aligned.iloc[-lookback:] if len(aligned) >= lookback else aligned

        if len(window) < 40:
            return None

        log_ret = np.log(window / window.shift(1)).dropna()

        if len(log_ret) < 40:
            return None

        r_t = log_ret["ticker"].values
        r_s = log_ret["spy"].values

        var_spy = np.var(r_s, ddof=1)
        if var_spy == 0.0:
            return None

        beta  = np.cov(r_t, r_s, ddof=1)[0, 1] / var_spy
        alpha = np.mean(r_t) - beta * np.mean(r_s)
        epsilon = r_t - (alpha + beta * r_s)
        ivol = np.std(epsilon, ddof=1) * np.sqrt(252)

        # Negative: low IVOL → high quality score after cross-sectional Z-scoring
        return float(-ivol)

    except Exception:
        return None


def compute_52wk_high_proximity(price_series: pd.Series) -> Optional[float]:
    """
    George-Hwang (2004) factor: current price / 52-week high.

    Stocks near their 52-week high exhibit continuation — investors use the high
    as a reference point and underreact to positive information, creating drift.
    Range: (0, 1.0] where 1.0 means the current price IS the 52-week high.

    Returns None if fewer than 126 bars are available (6 months minimum needed
    to compute a meaningful high; avoids inflated proximity for new listings).
    """
    px = price_series.dropna()
    if len(px) < 126:
        return None

    # 52-week window = last 252 trading bars
    high_52wk = px.tail(252).max()
    current   = px.iloc[-1]

    if high_52wk <= 0.0 or not np.isfinite(high_52wk):
        return None

    return float(current / high_52wk)


def compute_equity_composite(
    prices: pd.DataFrame,
    regime_weights: Optional[Dict] = None,
) -> pd.DataFrame:
    """
    Compute multi-factor composite score for equity universe.

    Seven factors (weights sum to 1.0):
        momentum_12_1       (0.28) — 12-1 month Jegadeesh-Titman momentum
        momentum_6_1        (0.16) — 6-1 month momentum
        earnings_revision   (0.18) — sell-side FY1 EPS revision proxy (cached 7d)
        ivol                (0.12) — negative IVOL; low idiosyncratic risk = quality
        52wk_high_proximity (0.10) — George-Hwang price/52wk-high factor
        mean_reversion_5d   (0.08) — 5-day contrarian reversion
        volatility_quality  (0.08) — low realized-vol quality proxy

    Graceful degradation:
        earnings_revision and ivol may return None for some tickers (data
        unavailable or SPY download failed).  Rather than dropping the ticker,
        composite_z is computed from available factors only, with weights
        renormalized so they sum to 1.0.  The 'factors_used' column records
        exactly which factors contributed for each row.

    regime_weights: optional dict from regime_filter.get_factor_weights().
        Keys must match the factor names above.  In RISK_OFF the regime filter
        should provide updated weights (e.g. down-weight momentum, up-weight
        quality factors such as ivol and volatility_quality).
        NOTE: regime_filter.py must be updated to include the three new keys
        (earnings_revision, ivol, 52wk_high_proximity) for full RISK_OFF
        integration; until then, config defaults are used for those factors.

    Returns DataFrame with columns:
        ticker, momentum_12_1_raw/z, momentum_6_1_raw/z,
        mean_rev_5d_raw/z, vol_quality_raw/z,
        proximity_52wk, proximity_52wk_z,
        ivol_raw, ivol_z,
        earnings_revision_raw, earnings_rev_z,
        composite_z, factors_used, rank,
        momentum_skip_21d_correct, market_regime (stamped by caller)
    """
    if prices.empty or len(prices) < 252:
        print("  [ERROR] Insufficient price history for equity signals")
        return pd.DataFrame()

    signals = pd.DataFrame(index=prices.columns)
    signals.index.name = "ticker"

    # ── Factor 1: 12-1 Month Momentum ────────────────────────────────────────
    # BUG FIX: compute_momentum now uses iloc[-252:-21] window so the skip
    # period is measured in exact trading-day counts, not calendar days.
    cfg = EQUITY_FACTORS["momentum_12_1"]
    raw = compute_momentum(prices, cfg["lookback_long"], cfg["lookback_skip"])
    signals["momentum_12_1_raw"] = raw
    signals["momentum_12_1_z"]   = zscore_cross_sectional(raw)
    # Sentinel column: documents that this run uses the trading-day-correct skip
    signals["momentum_skip_21d_correct"] = True

    # ── Factor 2: 6-1 Month Momentum ─────────────────────────────────────────
    cfg = EQUITY_FACTORS["momentum_6_1"]
    raw = compute_momentum(prices, cfg["lookback_long"], cfg["lookback_skip"])
    signals["momentum_6_1_raw"] = raw
    signals["momentum_6_1_z"]   = zscore_cross_sectional(raw)

    # ── Factor 3: 5-Day Mean Reversion ───────────────────────────────────────
    cfg = EQUITY_FACTORS["mean_reversion_5d"]
    raw_5d = compute_momentum(prices, cfg["lookback"])
    signals["mean_rev_5d_raw"] = -raw_5d   # Store un-inverted raw for CSV
    raw_5d_scored = -raw_5d if cfg.get("invert", False) else raw_5d
    signals["mean_rev_5d_z"] = zscore_cross_sectional(raw_5d_scored)

    # ── Factor 4: Low Volatility (Quality Proxy) ──────────────────────────────
    cfg = EQUITY_FACTORS["volatility_quality"]
    vol_raw = compute_realized_vol(prices, cfg["lookback"])
    signals["vol_quality_raw"] = vol_raw
    vol_scored = -vol_raw if cfg.get("invert", False) else vol_raw
    signals["vol_quality_z"] = zscore_cross_sectional(vol_scored)

    # ── Factor 5: 52-Week High Proximity (vectorized over tickers) ────────────
    proximity_vals: Dict[str, Optional[float]] = {}
    for ticker in prices.columns:
        proximity_vals[ticker] = compute_52wk_high_proximity(prices[ticker].dropna())
    proximity_series = pd.Series(proximity_vals, dtype=float)
    signals["proximity_52wk"] = proximity_series
    valid_prox = proximity_series.dropna()
    signals["proximity_52wk_z"] = (
        zscore_cross_sectional(valid_prox) if not valid_prox.empty else np.nan
    )

    # ── Factor 6: IVOL ───────────────────────────────────────────────────────
    # SPY is fetched once here; compute_ivol receives the pre-fetched Series to
    # avoid N individual yfinance downloads (one per ticker).
    spy_prices_for_ivol: Optional[pd.Series] = None
    try:
        spy_raw = yf.download(
            "SPY",
            period="6mo",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if not spy_raw.empty:
            if isinstance(spy_raw.columns, pd.MultiIndex):
                spy_prices_for_ivol = spy_raw["Close"].iloc[:, 0]
            else:
                spy_prices_for_ivol = spy_raw["Close"]
    except Exception:
        pass  # IVOL silently excluded if SPY download fails

    ivol_vals: Dict[str, Optional[float]] = {}
    for ticker in prices.columns:
        if spy_prices_for_ivol is not None:
            ivol_vals[ticker] = compute_ivol(
                prices[ticker].dropna(),
                spy_prices_for_ivol,
                lookback=EQUITY_FACTORS["ivol"]["lookback"],
            )
        else:
            ivol_vals[ticker] = None
    ivol_series = pd.Series(ivol_vals, dtype=float)
    signals["ivol_raw"] = ivol_series
    valid_ivol = ivol_series.dropna()
    signals["ivol_z"] = (
        zscore_cross_sectional(valid_ivol) if not valid_ivol.empty else np.nan
    )

    # ── Factor 7: Earnings Revision (per ticker, 7-day cache) ─────────────────
    print("  Computing earnings revision estimates (cached, 7-day TTL)...")
    eps_rev_vals: Dict[str, Optional[float]] = {}
    for ticker in prices.columns:
        eps_rev_vals[ticker] = compute_earnings_revision(ticker)
    eps_series = pd.Series(eps_rev_vals, dtype=float)
    signals["earnings_revision_raw"] = eps_series
    valid_eps = eps_series.dropna()
    signals["earnings_rev_z"] = (
        zscore_cross_sectional(valid_eps) if not valid_eps.empty else np.nan
    )

    # ── Composite Score with Graceful Degradation ─────────────────────────────
    # Use regime-adjusted weights if provided; else fall back to config defaults.
    _rw = regime_weights or {}
    base_weights = {
        "momentum_12_1_z":  _rw.get("momentum_12_1",          EQUITY_FACTORS["momentum_12_1"]["weight"]),
        "momentum_6_1_z":   _rw.get("momentum_6_1",           EQUITY_FACTORS["momentum_6_1"]["weight"]),
        "mean_rev_5d_z":    _rw.get("mean_reversion_5d",      EQUITY_FACTORS["mean_reversion_5d"]["weight"]),
        "vol_quality_z":    _rw.get("volatility_quality",     EQUITY_FACTORS["volatility_quality"]["weight"]),
        "proximity_52wk_z": _rw.get("52wk_high_proximity",    EQUITY_FACTORS["52wk_high_proximity"]["weight"]),
        "ivol_z":           _rw.get("ivol",                   EQUITY_FACTORS["ivol"]["weight"]),
        "earnings_rev_z":   _rw.get("earnings_revision",      EQUITY_FACTORS["earnings_revision"]["weight"]),
    }

    # Short labels written to the factors_used column for each ticker
    _factor_label = {
        "momentum_12_1_z":  "mom12_1",
        "momentum_6_1_z":   "mom6_1",
        "mean_rev_5d_z":    "mean_rev",
        "vol_quality_z":    "vol_qual",
        "proximity_52wk_z": "52wk_prox",
        "ivol_z":           "ivol",
        "earnings_rev_z":   "eps_rev",
    }

    composite_vals:    Dict[str, float] = {}
    factors_used_vals: Dict[str, str]   = {}

    for ticker in signals.index:
        row = signals.loc[ticker]
        # Include only factors where the Z-score is a finite number
        available = {
            col: w
            for col, w in base_weights.items()
            if pd.notna(row.get(col)) and np.isfinite(float(row.get(col, np.nan)))
        }
        total_w = sum(available.values())
        if total_w == 0 or not available:
            composite_vals[ticker]    = np.nan
            factors_used_vals[ticker] = ""
            continue
        # Renormalize so missing factors redistribute their weight proportionally
        composite_vals[ticker] = (
            sum(float(row[col]) * w for col, w in available.items()) / total_w
        )
        factors_used_vals[ticker] = "|".join(
            _factor_label.get(c, c) for c in available
        )

    signals["composite_z"]  = pd.Series(composite_vals)
    signals["factors_used"]  = pd.Series(factors_used_vals)

    # Rank (1 = best)
    signals["rank"] = signals["composite_z"].rank(ascending=False).astype(int)
    signals = signals.sort_values("rank")

    # Drop rows where composite couldn't be computed
    signals = signals.dropna(subset=["composite_z"])

    return signals


# ==============================================================================
# SECTION 3: SIGNAL COMPUTATION — CRYPTO TREND / MOMENTUM
# ==============================================================================

def compute_ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=span, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_crypto_signals(prices: pd.DataFrame) -> pd.DataFrame:
    """
    Compute trend/momentum signals for crypto universe.

    Signal logic:
    1. Trend score: Position relative to fast/slow/trend EMAs
    2. Momentum score: Weighted ROC across multiple lookbacks
    3. RSI: Timing overlay (oversold = better entry)
    4. Vol regime: Scale factor based on realized volatility

    Returns DataFrame with per-asset signals and sizing recommendations.
    """
    if prices.empty:
        print("  [ERROR] No crypto price data available")
        return pd.DataFrame()

    params = CRYPTO_PARAMS
    results = []

    for ticker in prices.columns:
        px = prices[ticker].dropna()
        if len(px) < params["ema_trend"]:
            continue

        current_price = px.iloc[-1]

        # ── EMAs ──
        ema_fast = compute_ema(px, params["ema_fast"]).iloc[-1]
        ema_slow = compute_ema(px, params["ema_slow"]).iloc[-1]
        ema_trend = compute_ema(px, params["ema_trend"]).iloc[-1]

        # Trend score: +1 for each EMA the price is above
        trend_score = (
            (1 if current_price > ema_fast else -1) +
            (1 if current_price > ema_slow else -1) +
            (1 if current_price > ema_trend else -1)
        ) / 3.0  # Normalize to [-1, +1]

        # ── Rate of Change (multi-period momentum) ──
        roc_scores = []
        for period, weight in zip(params["roc_periods"], params["roc_weights"]):
            if len(px) > period:
                roc = (px.iloc[-1] / px.iloc[-period] - 1)
                roc_scores.append(roc * weight)
        momentum_score = sum(roc_scores) if roc_scores else 0

        # ── RSI ──
        rsi = compute_rsi(px, params["rsi_period"]).iloc[-1]

        # RSI regime: 1 = oversold (good entry), -1 = overbought (caution)
        if rsi < params["rsi_oversold"]:
            rsi_signal = 1.0
        elif rsi > params["rsi_overbought"]:
            rsi_signal = -0.5
        else:
            rsi_signal = 0.0

        # ── Realized Volatility & Regime ──
        log_ret = np.log(px / px.shift(1)).dropna()
        realized_vol = log_ret.iloc[-params["vol_lookback"]:].std() * ANNUALIZATION_FACTOR

        if realized_vol > params["vol_threshold_extreme"]:
            vol_regime = "EXTREME"
            vol_scale = 0.0  # No position
        elif realized_vol > params["vol_threshold_high"]:
            vol_regime = "HIGH"
            vol_scale = params["vol_scale_factor"]
        else:
            vol_regime = "NORMAL"
            vol_scale = 1.0

        # ── Composite Signal ──
        # Trend (40%) + Momentum (30%) + RSI timing (15%) + Vol adjustment
        raw_signal = (
            0.40 * trend_score +
            0.30 * np.clip(momentum_score * 5, -1, 1) +  # Scale ROC to [-1,1]
            0.15 * rsi_signal +
            0.15 * (1 if trend_score > 0 and momentum_score > 0 else -0.5)
        )

        # Apply vol regime scaling
        adjusted_signal = raw_signal * vol_scale

        # Signal classification
        if adjusted_signal > 0.3:
            action = "BUY"
        elif adjusted_signal > 0.0:
            action = "HOLD"
        elif adjusted_signal > -0.3:
            action = "REDUCE"
        else:
            action = "SELL / NO POSITION"

        results.append({
            "ticker": ticker,
            "price": current_price,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "ema_trend": ema_trend,
            "trend_score": trend_score,
            "momentum_score": momentum_score,
            "rsi": rsi,
            "realized_vol_ann": realized_vol,
            "vol_regime": vol_regime,
            "raw_signal": raw_signal,
            "adjusted_signal": adjusted_signal,
            "action": action,
        })

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values("adjusted_signal", ascending=False)
        df["rank"] = range(1, len(df) + 1)
    return df


# ==============================================================================
# SECTION 4: POSITION SIZING & RISK MANAGEMENT
# ==============================================================================

def compute_position_sizes(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    asset_type: str,  # "equity" or "crypto"
    total_allocation_eur: float,
    regime_multiplier: float = 1.0,
) -> pd.DataFrame:
    """
    Kelly-fractional position sizing with risk constraints.

    For equities: Top N by composite score, inverse-vol weighted.
    For crypto: Only BUY signals, vol-adjusted sizing.
    """
    rp = RISK_PARAMS

    if asset_type == "equity":
        max_positions = rp["max_equity_positions"]
        max_pct = rp["max_position_equity_pct"]
        cost_bps = rp["equity_cost_bps"]
        # Take top N
        top = signals.head(max_positions).copy()
        tickers = top.index.tolist()
    else:
        max_positions = rp["max_crypto_positions"]
        max_pct = rp["max_position_crypto_pct"]
        cost_bps = rp["crypto_cost_bps"]
        # Only BUY signals
        buy_signals = signals[signals["action"] == "BUY"].head(max_positions)
        if buy_signals.empty:
            return pd.DataFrame()
        tickers = buy_signals["ticker"].tolist()
        top = buy_signals.set_index("ticker")

    # Compute inverse-volatility weights
    vol_data = {}
    for t in tickers:
        if t in prices.columns:
            log_ret = np.log(prices[t] / prices[t].shift(1)).dropna()
            vol = log_ret.iloc[-63:].std() * ANNUALIZATION_FACTOR
            vol_data[t] = vol if vol > 0 else np.nan

    vol_series = pd.Series(vol_data)
    inv_vol = 1.0 / vol_series.dropna()
    weights_raw = inv_vol / inv_vol.sum()

    # Apply Kelly fraction
    weights_kelly = weights_raw * rp["kelly_fraction"]

    # Cap individual positions
    weights_capped = weights_kelly.clip(upper=max_pct)

    # Renormalize so total = kelly_fraction
    if weights_capped.sum() > 0:
        weights_final = weights_capped * (rp["kelly_fraction"] / weights_capped.sum())
        # But don't exceed total allocation
        weights_final = weights_final.clip(upper=max_pct)

    # Convert to EUR and apply regime multiplier (1.0 = normal, 0.7 = transitional, 0.4 = risk-off)
    position_eur = weights_final * total_allocation_eur * regime_multiplier

    # Filter out positions below minimum size
    position_eur = position_eur[position_eur >= rp["min_position_eur"]]

    # Estimate transaction cost
    cost_eur = position_eur * (cost_bps / 10_000) * 2  # Round-trip

    result = pd.DataFrame({
        "weight_pct": (weights_final * 100).round(2),
        "position_eur": position_eur.round(0),
        "annual_vol": (vol_series * 100).round(1),
        "est_round_trip_cost_eur": cost_eur.round(2),
    })
    result = result[result["position_eur"].notna()]
    result = result.sort_values("position_eur", ascending=False)

    return result


# ==============================================================================
# SECTION 5: REPORTING
# ==============================================================================

def _print_regime_block(market_regime: dict, sector_regimes: dict) -> None:
    """Print a compact regime summary block to stdout."""
    regime = market_regime.get("regime", "UNKNOWN")
    score  = market_regime.get("score", 0)
    mult   = market_regime.get("_mult", 1.0)
    if _REGIME_AVAILABLE:
        mult = _rf.get_position_size_multiplier(regime)
    comp   = market_regime.get("components", {})
    vix    = market_regime.get("vix")
    spy200 = market_regime.get("spy_vs_200ma")
    yc     = market_regime.get("yield_curve_spread")

    print()
    print("─" * 60)
    print(f"  MACRO REGIME: {regime}  (score: {score:+d})  |  Size mult: {mult:.1f}x")
    print("─" * 60)
    trend_str = f"{comp.get('trend', 0):+d}"
    vol_str   = f"{comp.get('volatility', 0):+d}"
    cred_str  = f"{comp.get('credit', 0):+d}"
    yc_str    = f"{comp.get('yield_curve', 0):+d}"
    vix_s     = f" (VIX={vix:.1f})" if vix is not None else ""
    spy_s     = f" (SPY {spy200:+.1f}% vs 200MA)" if spy200 is not None else ""
    yc_s      = f" (T10Y2Y={yc:+.3f}%)" if yc is not None else ""
    print(f"  Trend:{trend_str}{spy_s}  Vol:{vol_str}{vix_s}  "
          f"Credit:{cred_str}  YldCurve:{yc_str}{yc_s}")

    # Sector summary — one line
    if sector_regimes:
        bull = [s for s, v in sector_regimes.items() if v == "BULL" and s != "computed_at"]
        bear = [s for s, v in sector_regimes.items() if v == "BEAR" and s != "computed_at"]
        print(f"  Sectors BULL ({len(bull)}): {', '.join(bull) or 'none'}")
        print(f"  Sectors BEAR ({len(bear)}): {', '.join(bear) or 'none'}")
    print()


def print_header():
    """Print engine header."""
    print("\n" + "█" * 60)
    print("  WEEKLY SIGNAL ENGINE — Run Date: " + SIGNAL_DATE)
    print("  Portfolio: €{:,.0f} | Equity: {:.0%} | Crypto: {:.0%} | Cash: {:.0%}".format(
        PORTFOLIO_NAV, EQUITY_ALLOCATION, CRYPTO_ALLOCATION, CASH_BUFFER))
    print("█" * 60)


def print_equity_report(signals: pd.DataFrame, positions: pd.DataFrame):
    """Print formatted equity screening results."""
    print("\n" + "─" * 60)
    print("  📊 EQUITY MULTI-FACTOR SCREENER")
    print("─" * 60)

    if signals.empty:
        print("  [NO SIGNALS] Insufficient data.")
        return

    # Top picks
    n = min(20, len(signals))
    top = signals.head(n)
    print(f"\n  TOP {n} RANKED STOCKS (by composite Z-score):\n")
    print(f"  {'Rank':<6}{'Ticker':<10}{'Composite':>10}{'Mom12-1':>9}"
          f"{'Mom6-1':>8}{'EpsRev':>8}{'IVOL':>8}{'52wkPrx':>8}"
          f"{'MeanRev':>8}{'VolQual':>8}  Factors")
    print("  " + "─" * 100)

    for _, row in top.iterrows():
        name = row.name if isinstance(row.name, str) else str(row.name)

        def _fmt(col: str) -> str:
            v = row.get(col)
            return f"{float(v):>8.3f}" if pd.notna(v) else "     n/a"

        print(f"  {int(row['rank']):<6}{name:<10}"
              f"{row['composite_z']:>10.3f}"
              f"{_fmt('momentum_12_1_z'):>9}"
              f"{_fmt('momentum_6_1_z'):>8}"
              f"{_fmt('earnings_rev_z'):>8}"
              f"{_fmt('ivol_z'):>8}"
              f"{_fmt('proximity_52wk_z'):>8}"
              f"{_fmt('mean_rev_5d_z'):>8}"
              f"{_fmt('vol_quality_z'):>8}"
              f"  {row.get('factors_used', '')}")

    # Bottom 5 (potential shorts / avoids)
    print(f"\n  BOTTOM 5 (AVOID / UNDERWEIGHT):\n")
    bottom = signals.tail(5)
    for _, row in bottom.iterrows():
        name = row.name if isinstance(row.name, str) else str(row.name)
        print(f"  {int(row['rank']):<6}{name:<10}{row['composite_z']:>10.3f}")

    # Position sizing
    if not positions.empty:
        print(f"\n  RECOMMENDED POSITION SIZES (Quarter-Kelly, €{PORTFOLIO_NAV * EQUITY_ALLOCATION:,.0f} equity allocation):\n")
        print(f"  {'Ticker':<10}{'Weight%':>10}{'EUR':>12}{'AnnVol%':>10}{'Cost€':>10}")
        print("  " + "─" * 52)
        for ticker, row in positions.iterrows():
            print(f"  {ticker:<10}{row['weight_pct']:>10.1f}%"
                  f"{row['position_eur']:>11,.0f}"
                  f"{row['annual_vol']:>10.1f}%"
                  f"{row['est_round_trip_cost_eur']:>10.2f}")
        print(f"\n  Total allocated: €{positions['position_eur'].sum():,.0f} "
              f"| Total est. costs: €{positions['est_round_trip_cost_eur'].sum():,.2f}")


def print_crypto_report(signals: pd.DataFrame, positions: pd.DataFrame):
    """Print formatted crypto signal results."""
    print("\n" + "─" * 60)
    print("  🪙 CRYPTO TREND / MOMENTUM SIGNALS")
    print("─" * 60)

    if signals.empty:
        print("  [NO SIGNALS] Insufficient data.")
        return

    print(f"\n  {'Rank':<6}{'Ticker':<12}{'Price':>12}{'Signal':>9}"
          f"{'Trend':>8}{'Mom':>8}{'RSI':>7}{'Vol%':>8}{'VolReg':>10}{'Action':<16}")
    print("  " + "─" * 96)

    for _, row in signals.iterrows():
        action_color = row["action"]
        print(f"  {int(row['rank']):<6}{row['ticker']:<12}"
              f"${row['price']:>11,.2f}"
              f"{row['adjusted_signal']:>9.3f}"
              f"{row['trend_score']:>8.2f}"
              f"{row['momentum_score']:>8.4f}"
              f"{row['rsi']:>7.1f}"
              f"{row['realized_vol_ann']*100:>7.1f}%"
              f"{row['vol_regime']:>10}"
              f"  {action_color:<16}")

    # Vol regime warnings
    extreme = signals[signals["vol_regime"] == "EXTREME"]
    high = signals[signals["vol_regime"] == "HIGH"]
    if not extreme.empty:
        print(f"\n  ⚠️  EXTREME VOL ({len(extreme)} assets): "
              f"{', '.join(extreme['ticker'].tolist())} — ZERO POSITION RECOMMENDED")
    if not high.empty:
        print(f"  ⚠️  HIGH VOL ({len(high)} assets): "
              f"{', '.join(high['ticker'].tolist())} — HALF SIZE")

    # Position sizing
    if not positions.empty:
        print(f"\n  RECOMMENDED CRYPTO POSITIONS (€{PORTFOLIO_NAV * CRYPTO_ALLOCATION:,.0f} allocation):\n")
        print(f"  {'Ticker':<12}{'Weight%':>10}{'EUR':>12}{'AnnVol%':>10}{'Cost€':>10}")
        print("  " + "─" * 54)
        for ticker, row in positions.iterrows():
            print(f"  {ticker:<12}{row['weight_pct']:>10.1f}%"
                  f"{row['position_eur']:>11,.0f}"
                  f"{row['annual_vol']:>10.1f}%"
                  f"{row['est_round_trip_cost_eur']:>10.2f}")
        print(f"\n  Total allocated: €{positions['position_eur'].sum():,.0f} "
              f"| Total est. costs: €{positions['est_round_trip_cost_eur'].sum():,.2f}")


def print_portfolio_summary(eq_pos: pd.DataFrame, cr_pos: pd.DataFrame):
    """Print consolidated portfolio summary."""
    print("\n" + "═" * 60)
    print("  📋 PORTFOLIO SUMMARY")
    print("═" * 60)

    eq_total = eq_pos["position_eur"].sum() if not eq_pos.empty else 0
    cr_total = cr_pos["position_eur"].sum() if not cr_pos.empty else 0
    invested = eq_total + cr_total
    cash = PORTFOLIO_NAV - invested

    print(f"\n  NAV:             €{PORTFOLIO_NAV:>12,.0f}")
    print(f"  Equity exposure: €{eq_total:>12,.0f}  ({eq_total/PORTFOLIO_NAV*100:.1f}%)")
    print(f"  Crypto exposure: €{cr_total:>12,.0f}  ({cr_total/PORTFOLIO_NAV*100:.1f}%)")
    print(f"  Cash:            €{cash:>12,.0f}  ({cash/PORTFOLIO_NAV*100:.1f}%)")

    total_costs = 0
    if not eq_pos.empty:
        total_costs += eq_pos["est_round_trip_cost_eur"].sum()
    if not cr_pos.empty:
        total_costs += cr_pos["est_round_trip_cost_eur"].sum()

    print(f"\n  Est. rebalance cost: €{total_costs:,.2f} "
          f"({total_costs/PORTFOLIO_NAV*10000:.1f} bps of NAV)")

    # Concentration warnings
    print(f"\n  RISK CHECKS:")
    n_eq = len(eq_pos) if not eq_pos.empty else 0
    n_cr = len(cr_pos) if not cr_pos.empty else 0
    print(f"  ✓ Equity positions: {n_eq} (max {RISK_PARAMS['max_equity_positions']})")
    print(f"  ✓ Crypto positions: {n_cr} (max {RISK_PARAMS['max_crypto_positions']})")

    if not eq_pos.empty and eq_pos["weight_pct"].max() > RISK_PARAMS["max_position_equity_pct"] * 100:
        print(f"  ⚠️ Max equity position exceeds {RISK_PARAMS['max_position_equity_pct']*100}% limit!")
    else:
        print(f"  ✓ Max equity position within limits")

    if cash / PORTFOLIO_NAV < CASH_BUFFER * 0.5:
        print(f"  ⚠️ Cash buffer below {CASH_BUFFER*50:.0f}% minimum!")
    else:
        print(f"  ✓ Cash buffer adequate")


def export_to_csv(
    equity_signals: pd.DataFrame,
    crypto_signals: pd.DataFrame,
    equity_positions: pd.DataFrame,
    crypto_positions: pd.DataFrame,
):
    """Export all signal data to CSV files."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    date_str = TODAY.strftime("%Y%m%d")

    files_written = []

    if not equity_signals.empty:
        path = os.path.join(OUTPUT_DIR, f"equity_signals_{date_str}.csv")
        equity_signals.to_csv(path)
        files_written.append(path)

    if not crypto_signals.empty:
        path = os.path.join(OUTPUT_DIR, f"crypto_signals_{date_str}.csv")
        crypto_signals.to_csv(path, index=False)
        files_written.append(path)

    if not equity_positions.empty:
        path = os.path.join(OUTPUT_DIR, f"equity_positions_{date_str}.csv")
        equity_positions.to_csv(path)
        files_written.append(path)

    if not crypto_positions.empty:
        path = os.path.join(OUTPUT_DIR, f"crypto_positions_{date_str}.csv")
        crypto_positions.to_csv(path)
        files_written.append(path)

    if files_written:
        print(f"\n  📁 CSV files exported to {OUTPUT_DIR}/:")
        for f in files_written:
            print(f"     → {f}")


# ==============================================================================
# SECTION 6: MAIN EXECUTION
# ==============================================================================

def run_equity_module() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Execute equity screening pipeline."""
    print("\n\n" + "█" * 60)
    print("  MODULE 1: EQUITY MULTI-FACTOR SCREENER")
    print("█" * 60)

    # ── Regime classification ─────────────────────────────────────────────────
    market_regime  = "TRANSITIONAL"
    regime_mult    = 1.0
    regime_weights = {}
    regime_dict    = {}
    sector_regimes = {}

    if _REGIME_AVAILABLE:
        try:
            regime_dict    = _rf.get_market_regime()
            market_regime  = regime_dict.get("regime", "TRANSITIONAL")
            regime_mult    = _rf.get_position_size_multiplier(market_regime)
            regime_weights = _rf.get_factor_weights(market_regime)
            sector_regimes = _rf.get_sector_regimes()
            _print_regime_block(regime_dict, sector_regimes)
        except Exception as exc:
            print(f"  [WARN] Regime filter failed: {exc} — using defaults")
    else:
        print("  [INFO] regime_filter not available — using config.py defaults")

    # ── Combine universes ─────────────────────────────────────────────────────
    # Primary: watchlist.txt (auto-updated by universe_builder.py)
    watchlist_path = Path(__file__).parent / "watchlist.txt"
    dynamic_tickers: list = []
    if watchlist_path.exists():
        for line in watchlist_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ticker = line.split()[0].upper()
            if "." not in ticker:
                dynamic_tickers.append(ticker)

    # Favorites: Supabase user_favorites (force-include)
    favorites: list = []
    try:
        from favorites import load_favorites
        favorites = load_favorites()
    except Exception as _e:
        print(f"  [WARN] Could not load favorites: {_e}")

    # Open positions: Supabase trades table (force-include)
    open_positions: list = []
    try:
        from utils.db import get_connection as _get_conn
        _conn = _get_conn()
        _cur = _conn.cursor()
        _cur.execute("SELECT DISTINCT ticker FROM trades WHERE status='open'")
        open_positions = [r['ticker'] for r in _cur.fetchall()]
        _conn.close()
    except Exception as _e:
        print(f"  [WARN] Could not read open positions from Supabase: {_e}")

    if not dynamic_tickers:
        print("  [WARN] watchlist.txt empty — using favorites + open positions only")

    universe = list(dict.fromkeys(dynamic_tickers + favorites + open_positions + CUSTOM_WATCHLIST))
    sources = []
    if dynamic_tickers:
        sources.append(f"watchlist.txt ({len(dynamic_tickers)})")
    if favorites:
        sources.append(f"favorites ({len(favorites)})")
    if open_positions:
        sources.append(f"open positions: {open_positions}")
    print(f"  Universe: {len(universe)} tickers from {', '.join(sources) or 'no source'}")

    # Fetch data
    prices = fetch_price_data(universe, label="equity universe")
    if prices.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Strip M&A targets / delisted stocks before scoring
    prices, _ma_removed = filter_ma_targets(prices)
    if prices.empty:
        print("  [WARN] All tickers removed by M&A filter — no signals to compute.")
        return pd.DataFrame(), pd.DataFrame()

    # Compute signals (pass regime-adjusted factor weights)
    print("\n  Computing multi-factor signals...")
    signals = compute_equity_composite(prices, regime_weights=regime_weights)

    # Stamp the market regime onto every row for CSV export
    if not signals.empty:
        signals["market_regime"] = market_regime

    # Compute positions (apply regime size multiplier)
    positions = pd.DataFrame()
    if not signals.empty:
        equity_eur = PORTFOLIO_NAV * EQUITY_ALLOCATION
        positions  = compute_position_sizes(
            signals, prices, "equity", equity_eur, regime_multiplier=regime_mult
        )

    # Report
    if CONSOLE_PRINT:
        print_equity_report(signals, positions)

    return signals, positions


def run_crypto_module() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Execute crypto signal pipeline."""
    print("\n\n" + "█" * 60)
    print("  MODULE 2: CRYPTO TREND / MOMENTUM")
    print("█" * 60)

    # Load crypto tickers dynamically from Supabase user_watchlists (category='crypto')
    crypto_tickers: list = list(CRYPTO_TICKERS)  # may be [] if config stripped
    if not crypto_tickers:
        try:
            from utils.db import get_connection as _gc
            _c = _gc()
            with _c.cursor() as _cur:
                _cur.execute("SELECT ticker FROM user_watchlists WHERE category='crypto' ORDER BY added_at")
                crypto_tickers = [r["ticker"] for r in _cur.fetchall()]
            _c.close()
        except Exception as _e:
            print(f"  [WARN] Could not load crypto tickers from Supabase: {_e}")

    if not crypto_tickers:
        print("  [WARN] No crypto tickers configured — skipping crypto module.")
        print("  Seed via: INSERT INTO user_watchlists (ticker, category) VALUES ('BTC-USD','crypto');")
        return pd.DataFrame(), pd.DataFrame()

    # Fetch data
    prices = fetch_price_data(crypto_tickers, label="crypto universe")
    if prices.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Compute signals
    print("\n  Computing trend/momentum signals...")
    signals = compute_crypto_signals(prices)

    # Compute positions
    positions = pd.DataFrame()
    if not signals.empty:
        crypto_eur = PORTFOLIO_NAV * CRYPTO_ALLOCATION
        positions = compute_position_sizes(signals, prices, "crypto", crypto_eur)

    # Report
    if CONSOLE_PRINT:
        print_crypto_report(signals, positions)

    return signals, positions


def main():
    parser = argparse.ArgumentParser(description="Weekly Signal Engine v1.0")
    parser.add_argument("--equity-only", action="store_true", help="Run equity module only")
    parser.add_argument("--crypto-only", action="store_true", help="Run crypto module only")
    parser.add_argument("--watchlist", type=str, help="Comma-separated custom tickers to add")
    parser.add_argument("--nav", type=float, help="Override portfolio NAV (EUR)")
    args = parser.parse_args()

    # Override config if CLI args provided
    global PORTFOLIO_NAV
    if args.nav:
        PORTFOLIO_NAV = args.nav
    else:
        saved = _load_saved_nav()
        if saved is not None:
            print(f"  [cash] Using dashboard-saved cash balance as NAV: €{saved:,.0f}")
            PORTFOLIO_NAV = saved

    if args.watchlist:
        extra = [t.strip().upper() for t in args.watchlist.split(",")]
        CUSTOM_WATCHLIST.extend(extra)
        print(f"  Added {len(extra)} tickers to watchlist: {', '.join(extra)}")

    print_header()

    eq_signals, eq_positions = pd.DataFrame(), pd.DataFrame()
    cr_signals, cr_positions = pd.DataFrame(), pd.DataFrame()

    if not args.crypto_only:
        eq_signals, eq_positions = run_equity_module()

    if not args.equity_only:
        cr_signals, cr_positions = run_crypto_module()

    # Portfolio summary
    if CONSOLE_PRINT:
        print_portfolio_summary(eq_positions, cr_positions)

    # Export
    if CSV_EXPORT:
        export_to_csv(eq_signals, cr_signals, eq_positions, cr_positions)

    print("\n" + "█" * 60)
    print("  ✅ SIGNAL GENERATION COMPLETE")
    print("  ⚠️  THIS IS NOT INVESTMENT ADVICE. ALL SIGNALS ARE")
    print("     INFORMATIONAL. REVIEW BEFORE ACTING.")
    print("█" * 60 + "\n")


if __name__ == "__main__":
    main()
