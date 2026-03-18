#!/usr/bin/env python3
"""
================================================================================
CROSS-ASSET DIVERGENCE SIGNAL
================================================================================
Python implementation of the "Bottom and Top Finder" TradingView indicator
by TheUltimator5.

LOGIC:
  Uses log-normalized Directional Movement (+DI/-DI) to compare a stock's
  directional momentum against 3 macro reference symbols:
    - RSP  (equal-weight S&P 500)  — broad equity breadth
    - HYG  (high-yield bonds)       — credit / risk appetite
    - DXY  (dollar index)           — macro currency context

  botLine = weighted avg of (ref_plusDI / stock_plusDI)
            When references surge UP but the stock doesn't → potential bottom divergence

  topLine = weighted avg of (ref_minusDI / stock_minusDI)
            When references drop more than the stock → potential top divergence

  A signal fires when:
    1. line > threshold (0.65 by default)
    2. line > 1.8x its 20-bar SMA (spike in relative weakness)

OUTPUT dict keys:
    bot_line          float  — current bottom divergence ratio
    top_line          float  — current top divergence ratio
    bot_trigger       bool   — bottom signal fired
    top_trigger       bool   — top signal fired
    bot_line_ma       float  — 20-bar SMA of bot_line
    top_line_ma       float  — 20-bar SMA of top_line
    bot_diff          float  — bot_line / bot_line_ma ratio
    top_diff          float  — top_line / top_line_ma ratio
    signal            str    — "BOTTOM" | "TOP" | "NEUTRAL"
    interpretation    str    — plain-language summary

CLI:
    python3 cross_asset_divergence.py AAPL
    python3 cross_asset_divergence.py GME --period 6mo
================================================================================
"""

import argparse
import math
import sys
import warnings
from typing import Optional

warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────────────────
REFERENCE_SYMBOLS = [
    ("RSP", 1),   # Equal-weight S&P 500
    ("HYG", 2),   # High-yield bonds (double weight — best risk-on/off proxy)
    ("UUP", 1),   # Invesco Dollar ETF (yfinance proxy for DXY)
]
DI_LENGTH       = 5      # Smoothing window for +DI / -DI (matches Pine default)
MA_LENGTH       = 20     # SMA length for spike detection
THRESHOLD_BOT   = 0.65
THRESHOLD_TOP   = 0.65
SPIKE_RATIO     = 1.8    # line must be > 1.8x its MA to fire
DEFAULT_PERIOD  = "1y"   # yfinance download period


# ── Core math ─────────────────────────────────────────────────────────────────

def _safe_log(a, b):
    """log(a/b); returns NaN if either is <= 0."""
    if a is None or b is None or a <= 0 or b <= 0:
        return float("nan")
    return math.log(a / b)


def _rma(series, length):
    """
    Wilder's smoothed moving average (RMA / EMA with alpha=1/length).
    series: list of floats (may contain nan).
    Returns list of floats.
    """
    alpha = 1.0 / length
    result = [float("nan")] * len(series)
    prev = float("nan")
    for i, val in enumerate(series):
        if math.isnan(val):
            result[i] = prev
            continue
        if math.isnan(prev):
            prev = val
        else:
            prev = alpha * val + (1 - alpha) * prev
        result[i] = prev
    return result


def _sma(series, length):
    """Simple moving average, returns list of floats."""
    result = [float("nan")] * len(series)
    for i in range(length - 1, len(series)):
        window = [v for v in series[i - length + 1 : i + 1] if not math.isnan(v)]
        if window:
            result[i] = sum(window) / len(window)
    return result


def _compute_di(highs, lows, closes, length):
    """
    Compute log-normalized +DI and -DI series.
    Returns (plus_di_list, minus_di_list) of same length as inputs.
    """
    n = len(closes)
    plus_di  = [float("nan")] * n
    minus_di = [float("nan")] * n

    plus_dm_raw  = [float("nan")] * n
    minus_dm_raw = [float("nan")] * n
    tr_raw       = [float("nan")] * n

    for i in range(1, n):
        up   = _safe_log(highs[i],  highs[i - 1])
        down = _safe_log(lows[i - 1], lows[i])

        plus_dm_raw[i]  = up   if (up > down and up > 0)   else 0.0
        minus_dm_raw[i] = down if (down > up and down > 0) else 0.0

        hl  = _safe_log(highs[i], lows[i])
        hc  = abs(_safe_log(highs[i], closes[i - 1]))
        lc  = abs(_safe_log(lows[i],  closes[i - 1]))
        tr_raw[i] = max(x for x in [hl, hc, lc] if not math.isnan(x)) if any(not math.isnan(x) for x in [hl, hc, lc]) else float("nan")

    smooth_plus  = _rma(plus_dm_raw,  length)
    smooth_minus = _rma(minus_dm_raw, length)
    smooth_tr    = _rma(tr_raw,       length)

    for i in range(n):
        if not math.isnan(smooth_tr[i]) and smooth_tr[i] > 0:
            plus_di[i]  = 100 * smooth_plus[i]  / smooth_tr[i]
            minus_di[i] = 100 * smooth_minus[i] / smooth_tr[i]

    return plus_di, minus_di


# ── Main function ─────────────────────────────────────────────────────────────

def get_cross_asset_signal(
    ticker: str,
    period: str = DEFAULT_PERIOD,
    ref_symbols: list = None,
    di_length: int = DI_LENGTH,
    ma_length: int = MA_LENGTH,
    threshold_bot: float = THRESHOLD_BOT,
    threshold_top: float = THRESHOLD_TOP,
    spike_ratio: float = SPIKE_RATIO,
) -> dict:
    """
    Compute cross-asset divergence signal for a ticker.

    Returns dict with signal results, or empty dict on failure.
    """
    if ref_symbols is None:
        ref_symbols = REFERENCE_SYMBOLS

    try:
        import yfinance as yf
        import numpy as np
    except ImportError:
        return {}

    # Download stock + all reference symbols in one batch
    symbols_to_fetch = [ticker] + [sym for sym, _ in ref_symbols]

    try:
        raw = yf.download(
            symbols_to_fetch,
            period=period,
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception:
        return {}

    if raw.empty:
        return {}

    def _get_series(df, col, sym):
        """Extract a price series, handling single vs multi-ticker dataframes."""
        try:
            if isinstance(df.columns, tuple) or hasattr(df.columns, "levels"):
                # Multi-level columns: (col, sym)
                return df[col][sym].dropna()
            else:
                return df[col].dropna()
        except (KeyError, TypeError):
            return None

    def _extract_ohlc(sym):
        h = _get_series(raw, "High",  sym)
        l = _get_series(raw, "Low",   sym)
        c = _get_series(raw, "Close", sym)
        if h is None or l is None or c is None:
            return None, None, None
        # Align on common index
        idx = h.index.intersection(l.index).intersection(c.index)
        return list(h.loc[idx]), list(l.loc[idx]), list(c.loc[idx])

    # Get stock OHLC
    h_stock, l_stock, c_stock = _extract_ohlc(ticker)
    if h_stock is None or len(h_stock) < ma_length + di_length + 5:
        return {}

    # Compute stock +DI / -DI
    pdi_stock, mdi_stock = _compute_di(h_stock, l_stock, c_stock, di_length)

    # Compute each reference symbol's DI and build ratio series
    total_weight = sum(w for _, w in ref_symbols)

    # bot_ratios[i] = weighted sum of sqrt(ref_pdi) / stock_pdi per reference
    # (sqrt per the Pine script — dampens outliers in reference DI)
    n = len(pdi_stock)
    bot_ratios = [0.0] * n
    top_ratios = [0.0] * n
    valid_refs  = 0

    for ref_sym, weight in ref_symbols:
        h_ref, l_ref, c_ref = _extract_ohlc(ref_sym)
        if h_ref is None or len(h_ref) < len(h_stock):
            # Pad or skip — try to align by taking last n bars
            if h_ref is not None and len(h_ref) >= ma_length + di_length + 5:
                # Trim to last n bars
                h_ref = h_ref[-n:]
                l_ref = l_ref[-n:]
                c_ref = c_ref[-n:]
            else:
                continue  # skip this reference

        # Align lengths
        h_ref = h_ref[-n:]
        l_ref = l_ref[-n:]
        c_ref = c_ref[-n:]

        pdi_ref, mdi_ref = _compute_di(h_ref, l_ref, c_ref, di_length)

        for i in range(n):
            ps = pdi_stock[i]
            pr = pdi_ref[i]
            ms = mdi_stock[i]
            mr = mdi_ref[i]

            if (math.isnan(ps) or ps <= 0 or math.isnan(pr) or pr <= 0 or
                    math.isnan(ms) or ms <= 0 or math.isnan(mr) or mr <= 0):
                continue

            bot_ratios[i] += (math.sqrt(pr) * weight) / ps
            top_ratios[i] += (math.sqrt(mr) * weight) / ms

        valid_refs += 1

    if valid_refs == 0:
        return {}

    # Normalize by total weight
    bot_line = [v / total_weight for v in bot_ratios]
    top_line = [v / total_weight for v in top_ratios]

    # Replace 0.0 sentinel (bars with missing data) with NaN for MA calc
    bot_line_clean = [v if v > 0 else float("nan") for v in bot_line]
    top_line_clean = [v if v > 0 else float("nan") for v in top_line]

    # 20-bar SMA of each line
    bot_ma = _sma(bot_line_clean, ma_length)
    top_ma = _sma(top_line_clean, ma_length)

    # Latest values
    def _last_valid(lst):
        for v in reversed(lst):
            if not math.isnan(v) and v > 0:
                return v
        return float("nan")

    cur_bot  = _last_valid(bot_line_clean)
    cur_top  = _last_valid(top_line_clean)
    cur_bma  = _last_valid(bot_ma)
    cur_tma  = _last_valid(top_ma)

    if math.isnan(cur_bot) or math.isnan(cur_top):
        return {}

    bot_diff = (cur_bot / cur_bma) if (not math.isnan(cur_bma) and cur_bma > 0) else float("nan")
    top_diff = (cur_top / cur_tma) if (not math.isnan(cur_tma) and cur_tma > 0) else float("nan")

    bot_trigger = (
        cur_bot > threshold_bot and
        not math.isnan(bot_diff) and bot_diff > spike_ratio
    )
    top_trigger = (
        cur_top > threshold_top and
        not math.isnan(top_diff) and top_diff > spike_ratio
    )

    # Signal classification
    if bot_trigger and top_trigger:
        signal = "CONFLICTED"
    elif bot_trigger:
        signal = "BOTTOM"
    elif top_trigger:
        signal = "TOP"
    elif cur_bot > threshold_bot:
        signal = "BOTTOM_ZONE"   # Above threshold but not a spike yet
    elif cur_top > threshold_top:
        signal = "TOP_ZONE"
    else:
        signal = "NEUTRAL"

    # Plain-language interpretation
    interp_map = {
        "BOTTOM":       f"{ticker} is diverging bearishly from RSP/HYG/DXY — potential reversal bottom (buy zone).",
        "TOP":          f"{ticker} is diverging bullishly from RSP/HYG/DXY — potential reversal top (caution/sell zone).",
        "BOTTOM_ZONE":  f"{ticker} showing early bottom divergence from macro references — watch for confirmation.",
        "TOP_ZONE":     f"{ticker} showing early top divergence from macro references — watch for confirmation.",
        "CONFLICTED":   f"{ticker} shows both bottom and top divergence signals simultaneously — data inconclusive.",
        "NEUTRAL":      f"No significant cross-asset divergence detected for {ticker}.",
    }

    return {
        "bot_line":       round(cur_bot, 4),
        "top_line":       round(cur_top, 4),
        "bot_line_ma":    round(cur_bma, 4) if not math.isnan(cur_bma) else None,
        "top_line_ma":    round(cur_tma, 4) if not math.isnan(cur_tma) else None,
        "bot_diff":       round(bot_diff, 3) if not math.isnan(bot_diff) else None,
        "top_diff":       round(top_diff, 3) if not math.isnan(top_diff) else None,
        "bot_trigger":    bot_trigger,
        "top_trigger":    top_trigger,
        "signal":         signal,
        "interpretation": interp_map[signal],
        "threshold_bot":  threshold_bot,
        "threshold_top":  threshold_top,
        "refs_used":      valid_refs,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Cross-asset divergence signal")
    parser.add_argument("ticker", help="Ticker symbol (e.g. AAPL, GME, BTC-USD)")
    parser.add_argument("--period", default=DEFAULT_PERIOD, help="yfinance period (default: 1y)")
    args = parser.parse_args()

    ticker = args.ticker.upper()
    print(f"\nComputing cross-asset divergence for {ticker}...")
    result = get_cross_asset_signal(ticker, period=args.period)

    if not result:
        print("  ERROR: Could not compute signal (insufficient data or download failed).")
        sys.exit(1)

    print(f"\n  Signal:         {result['signal']}")
    print(f"  Bot line:       {result['bot_line']}  (MA: {result['bot_line_ma']}, ratio: {result['bot_diff']}x)")
    print(f"  Top line:       {result['top_line']}  (MA: {result['top_line_ma']}, ratio: {result['top_diff']}x)")
    print(f"  Bot trigger:    {result['bot_trigger']}")
    print(f"  Top trigger:    {result['top_trigger']}")
    print(f"  References:     {result['refs_used']}/3 loaded")
    print(f"\n  → {result['interpretation']}\n")


if __name__ == "__main__":
    main()
