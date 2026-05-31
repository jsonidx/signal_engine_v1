"""
utils/psc_signal.py — Price-Structure Compression (PSC) Signal  (TRD-036)

Deterministic signal only. No LLM, no options, no dark-pool data.

Features (daily OHLCV only)
---------------------------
1. ATR compression   : ATR(10) / ATR(40) — lower = more compressed
2. Volume decline    : OLS slope of volume over last 20 trading days, normalised
3. High proximity    : distance from close to 52-week high (higher = closer)
4. Range tightness   : (20d high − 20d low) / close vs (60d high − 60d low) / close

Guard rails
-----------
- Minimum liquidity: ADV(20d) ≥ MIN_ADV_USD (avoids dead/illiquid stocks)
- Minimum price:     close ≥ MIN_PRICE
- PSC-only gate:     a PSC-only name (pfs_score=0) CANNOT pass Stage 2 regardless
  of PSC score — enforced externally in the pipeline composite gate, documented here.

All windows use trading days.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Tuning constants ───────────────────────────────────────────────────────────
ATR_SHORT = 10           # trading days
ATR_LONG = 40
VOL_SLOPE_WINDOW = 20    # days for volume-decline OLS
HIGH_PROX_WINDOW = 252   # trading days for 52-week high (approx)
RANGE_SHORT = 20         # days for range-tightness short window
RANGE_LONG = 60          # days for range-tightness long window
MIN_ADV_USD = 5_000_000  # minimum average daily dollar volume (liquidity guard)
MIN_PRICE = 3.0          # minimum close price guard

# Score weights (sum to 1.0)
W_ATR = 0.35
W_VOL = 0.20
W_PROX = 0.25
W_RANGE = 0.20


class PSCResult(NamedTuple):
    ticker: str
    psc_score: float           # 0-1
    atr_compression: float | None   # ATR(10)/ATR(40); low = compressed
    vol_decline_score: float | None # 0-1; higher = more declining volume
    high_proximity: float | None    # 0-1; higher = closer to 52w high
    range_tightness: float | None   # 0-1; higher = tighter
    liquidity_ok: bool
    note: str


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int) -> pd.Series:
    tr = _true_range(high, low, close)
    return tr.rolling(window, min_periods=window // 2).mean()


def _ols_slope(series: pd.Series) -> float | None:
    """Return OLS slope coefficient for a series (x = 0, 1, ..., n-1)."""
    clean = series.dropna()
    if len(clean) < 4:
        return None
    x = np.arange(len(clean), dtype=float)
    # Normalise x to avoid scale dominance
    coeffs = np.polyfit(x, clean.values, 1)
    return float(coeffs[0])


def score_psc(
    universe: list[str],
    prices: pd.DataFrame,          # close prices; date-indexed
    highs: pd.DataFrame,           # daily highs; same shape
    lows: pd.DataFrame,            # daily lows; same shape
    volumes: pd.DataFrame,         # daily volumes; same shape
    as_of_date: "date | None" = None,
) -> list[PSCResult]:
    """
    Score all tickers in *universe* on the PSC signal.

    Parameters
    ----------
    universe    : list of ticker symbols
    prices      : adjusted close prices (date index, ticker columns)
    highs       : daily high prices
    lows        : daily low prices
    volumes     : daily volumes
    as_of_date  : evaluation date (defaults to latest date in prices)

    Returns list of PSCResult, one per ticker in universe.
    """
    from datetime import date as _date

    if prices.empty:
        return [PSCResult(t, 0.0, None, None, None, None, False, "no_data") for t in universe]

    tdays = sorted(prices.index.tolist())
    as_of = as_of_date or tdays[-1]
    if isinstance(as_of, str):
        as_of = _date.fromisoformat(as_of)

    # Trim to as_of (point-in-time safe)
    prices  = prices[prices.index <= as_of]
    highs   = highs[highs.index <= as_of]
    lows    = lows[lows.index <= as_of]
    volumes = volumes[volumes.index <= as_of]

    min_rows = ATR_LONG + 5
    if len(prices) < min_rows:
        return [PSCResult(t, 0.0, None, None, None, None, False, "insufficient_history")
                for t in universe]

    results: list[PSCResult] = []

    for ticker in universe:
        if ticker not in prices.columns:
            results.append(PSCResult(ticker, 0.0, None, None, None, None, False, "missing"))
            continue

        close  = prices[ticker].dropna()
        high   = highs[ticker].dropna() if ticker in highs.columns else close
        low    = lows[ticker].dropna()  if ticker in lows.columns  else close
        volume = volumes[ticker].dropna() if ticker in volumes.columns else pd.Series(dtype=float)

        if len(close) < min_rows:
            results.append(PSCResult(ticker, 0.0, None, None, None, None, False, "short_history"))
            continue

        cur_close = float(close.iloc[-1])

        # ── Liquidity guard ────────────────────────────────────────────────────
        if cur_close < MIN_PRICE:
            results.append(PSCResult(ticker, 0.0, None, None, None, None, False, "price_too_low"))
            continue

        adv = None
        if not volume.empty:
            adv = float(volume.tail(20).mean() * cur_close)
        if adv is not None and adv < MIN_ADV_USD:
            results.append(PSCResult(ticker, 0.0, None, None, None, None, False, "illiquid"))
            continue

        liquidity_ok = True

        # ── ATR compression ────────────────────────────────────────────────────
        # Align indices for ATR calculation
        h = high.reindex(close.index).ffill()
        l = low.reindex(close.index).ffill()

        atr_short = _atr(h, l, close, ATR_SHORT)
        atr_long  = _atr(h, l, close, ATR_LONG)

        cur_atr_s = atr_short.iloc[-1] if not atr_short.empty else None
        cur_atr_l = atr_long.iloc[-1]  if not atr_long.empty  else None

        atr_compression = None
        atr_score = None
        if cur_atr_s is not None and cur_atr_l is not None and cur_atr_l > 0:
            atr_compression = cur_atr_s / cur_atr_l
            # Score: lower ratio = more compressed = higher score
            # Ratio ~0.5 → score ~1.0; ratio ≥1.5 → score ~0.0
            atr_score = float(np.clip(1.0 - (atr_compression - 0.5) / 1.0, 0.0, 1.0))

        # ── Volume decline ─────────────────────────────────────────────────────
        vol_decline_score = None
        if not volume.empty and len(volume) >= VOL_SLOPE_WINDOW:
            vol_tail = volume.tail(VOL_SLOPE_WINDOW)
            vol_mean = vol_tail.mean()
            if vol_mean > 0:
                # Normalise by mean so scale doesn't dominate
                vol_norm = vol_tail / vol_mean
                slope = _ols_slope(vol_norm)
                if slope is not None:
                    # Negative slope = declining volume = bullish for setup
                    # Map slope in [-0.05, 0] → [1.0, 0.5]; positive → <0.5
                    vol_decline_score = float(np.clip(0.5 - slope * 10.0, 0.0, 1.0))

        # ── High proximity ─────────────────────────────────────────────────────
        high_proximity = None
        high_proxy = h if len(h) > 0 else close
        if len(high_proxy) >= min(HIGH_PROX_WINDOW, 60):
            w52_high = float(high_proxy.tail(min(HIGH_PROX_WINDOW, len(high_proxy))).max())
            w52_low  = float(close.tail(min(HIGH_PROX_WINDOW, len(close))).min())
            if w52_high > w52_low and w52_high > 0:
                high_proximity = (cur_close - w52_low) / (w52_high - w52_low)
                high_proximity = float(np.clip(high_proximity, 0.0, 1.0))

        # ── Range tightness ────────────────────────────────────────────────────
        range_tightness = None
        if len(high_proxy) >= RANGE_LONG and len(l) >= RANGE_LONG:
            h_arr = h.tail(RANGE_LONG)
            l_arr = l.tail(RANGE_LONG)

            short_h = float(h_arr.tail(RANGE_SHORT).max())
            short_l = float(l_arr.tail(RANGE_SHORT).min())
            long_h  = float(h_arr.max())
            long_l  = float(l_arr.min())

            short_range = (short_h - short_l) / cur_close if cur_close > 0 else 0.0
            long_range  = (long_h  - long_l)  / cur_close if cur_close > 0 else 1.0

            if long_range > 0:
                ratio = short_range / long_range
                # Lower ratio = tighter recent range = more coiled
                range_tightness = float(np.clip(1.0 - ratio, 0.0, 1.0))

        # ── Composite PSC score ────────────────────────────────────────────────
        components = [
            (atr_score,        W_ATR),
            (vol_decline_score, W_VOL),
            (high_proximity,    W_PROX),
            (range_tightness,   W_RANGE),
        ]
        total_weight = sum(w for v, w in components if v is not None)
        if total_weight < 0.3:  # not enough components computed
            psc_score = 0.0
        else:
            psc_score = sum(v * w for v, w in components if v is not None) / total_weight

        results.append(PSCResult(
            ticker=ticker,
            psc_score=float(np.clip(psc_score, 0.0, 1.0)),
            atr_compression=atr_compression,
            vol_decline_score=vol_decline_score,
            high_proximity=high_proximity,
            range_tightness=range_tightness,
            liquidity_ok=liquidity_ok,
            note="ok",
        ))

    return results
