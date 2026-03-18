#!/usr/bin/env python3
"""
================================================================================
VOLUME PROFILE — Support & Resistance via Volume-at-Price
================================================================================
Identifies statistically significant price levels where the most trading
activity occurred — these become support and resistance zones.

LOGIC:
  1. Download OHLCV history (default 1 year daily)
  2. Distribute each bar's volume proportionally across its High-Low range
     (not just at close — gives a true volume profile)
  3. Find HVN peaks  — High Volume Nodes = strong support/resistance
  4. Find LVN troughs — Low Volume Nodes = price moves through quickly
  5. Point of Control (POC) — single highest-volume price in the range
  6. Value Area (VA) — price range containing 70% of all volume
  7. Anchored VWAPs (20d, 50d) — dynamic support/resistance

KEY OUTPUT:
    poc_price           — highest volume price (strongest magnet)
    value_area_high     — top of 70% volume concentration zone
    value_area_low      — bottom of 70% volume concentration zone
    support_levels      — HVNs below current price, strongest first
    resistance_levels   — HVNs above current price, closest first
    nearest_support     — {price, distance_pct, strength_pct}
    nearest_resistance  — {price, distance_pct, strength_pct}
    vwap_20d / vwap_50d — rolling VWAP levels
    interpretation      — plain-language summary

CLI:
    python3 volume_profile.py AAPL
    python3 volume_profile.py GME --bins 75 --period 6mo
================================================================================
"""

import argparse
import sys
import warnings
from typing import Optional

warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_PERIOD    = "1y"
DEFAULT_BINS      = 60     # price buckets across the range
DEFAULT_N_LEVELS  = 5      # max S/R levels to return per side
MERGE_PCT         = 1.5    # merge HVNs within 1.5% of each other
VALUE_AREA_PCT    = 0.70   # capture 70% of volume in value area
PEAK_WINDOW       = 3      # bars each side for local maxima detection
MIN_PEAK_PCT      = 0.08   # HVN must be ≥ 8% of max bin volume


# ── Core math ─────────────────────────────────────────────────────────────────

def _build_volume_profile(hist, n_bins):
    """
    Distribute each bar's volume proportionally across its High-Low range
    into price bins. Returns (volume_by_bin, bin_centers, price_min, bin_size).
    """
    import numpy as np

    price_min = float(hist["Low"].min())
    price_max = float(hist["High"].max())
    if price_max <= price_min:
        return None, None, None, None

    bin_size = (price_max - price_min) / n_bins
    volume_by_bin = np.zeros(n_bins)

    for _, row in hist.iterrows():
        bar_low  = float(row["Low"])
        bar_high = float(row["High"])
        bar_vol  = float(row["Volume"])
        bar_range = bar_high - bar_low

        if bar_range <= 0:
            # No range — assign all volume to close bin
            b = int((float(row["Close"]) - price_min) / bin_size)
            b = max(0, min(n_bins - 1, b))
            volume_by_bin[b] += bar_vol
            continue

        first_bin = max(0, int((bar_low  - price_min) / bin_size))
        last_bin  = min(n_bins - 1, int((bar_high - price_min) / bin_size))

        for b in range(first_bin, last_bin + 1):
            bin_lo = price_min + b * bin_size
            bin_hi = bin_lo + bin_size
            overlap = max(0.0, min(bar_high, bin_hi) - max(bar_low, bin_lo))
            proportion = overlap / bar_range
            volume_by_bin[b] += bar_vol * proportion

    bin_centers = [price_min + (b + 0.5) * bin_size for b in range(n_bins)]
    return volume_by_bin, bin_centers, price_min, bin_size


def _find_peaks(volume_arr, window=PEAK_WINDOW, min_pct=MIN_PEAK_PCT):
    """Local maxima: each point must be highest in ±window range."""
    max_vol = max(volume_arr) if volume_arr else 0
    threshold = max_vol * min_pct
    peaks = []
    n = len(volume_arr)
    for i in range(window, n - window):
        if volume_arr[i] < threshold:
            continue
        if all(volume_arr[i] >= volume_arr[i - j] for j in range(1, window + 1)) and \
           all(volume_arr[i] >= volume_arr[i + j] for j in range(1, window + 1)):
            peaks.append(i)
    return peaks


def _find_troughs(volume_arr, window=PEAK_WINDOW):
    """Local minima (Low Volume Nodes)."""
    troughs = []
    n = len(volume_arr)
    for i in range(window, n - window):
        if all(volume_arr[i] <= volume_arr[i - j] for j in range(1, window + 1)) and \
           all(volume_arr[i] <= volume_arr[i + j] for j in range(1, window + 1)):
            troughs.append(i)
    return troughs


def _compute_value_area(volume_by_bin, bin_centers, poc_bin, target_pct=VALUE_AREA_PCT):
    """
    Expand outward from POC until target_pct of total volume is captured.
    Returns (va_low_price, va_high_price).
    """
    total_vol = sum(volume_by_bin)
    target_vol = total_vol * target_pct
    n = len(volume_by_bin)

    lo = poc_bin
    hi = poc_bin
    accumulated = volume_by_bin[poc_bin]

    while accumulated < target_vol:
        add_above = volume_by_bin[hi + 1] if hi + 1 < n else 0
        add_below = volume_by_bin[lo - 1] if lo - 1 >= 0 else 0

        if add_above == 0 and add_below == 0:
            break
        if add_above >= add_below:
            hi += 1
            accumulated += add_above
        else:
            lo -= 1
            accumulated += add_below

    return bin_centers[lo], bin_centers[hi]


def _merge_nearby(levels, merge_pct=MERGE_PCT):
    """Merge HVNs that are within merge_pct% of each other (keep stronger one)."""
    if not levels:
        return []
    levels = sorted(levels, key=lambda x: x["price"])
    merged = [levels[0]]
    for lvl in levels[1:]:
        gap_pct = (lvl["price"] - merged[-1]["price"]) / merged[-1]["price"] * 100
        if gap_pct < merge_pct:
            # Keep the stronger (higher volume) level
            if lvl["volume"] > merged[-1]["volume"]:
                merged[-1] = lvl
        else:
            merged.append(lvl)
    return merged


def _compute_vwap(hist, period_days):
    """Rolling VWAP over last N trading days."""
    recent = hist.tail(period_days)
    if recent.empty:
        return None
    typical = (recent["High"] + recent["Low"] + recent["Close"]) / 3
    vwap = float((typical * recent["Volume"]).sum() / recent["Volume"].sum())
    return round(vwap, 4)


# ── Main function ─────────────────────────────────────────────────────────────

def get_volume_profile(
    ticker: str,
    period: str = DEFAULT_PERIOD,
    n_bins: int = DEFAULT_BINS,
    n_levels: int = DEFAULT_N_LEVELS,
) -> dict:
    """
    Compute volume profile support/resistance for a ticker.
    Returns dict with all S/R levels, or empty dict on failure.
    """
    try:
        import yfinance as yf
        import numpy as np
    except ImportError:
        return {}

    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=period, auto_adjust=True)
    except Exception:
        return {}

    if hist is None or len(hist) < 30:
        return {}

    # Build volume profile
    volume_by_bin, bin_centers, price_min, bin_size = _build_volume_profile(hist, n_bins)
    if volume_by_bin is None:
        return {}

    current_price = float(hist["Close"].iloc[-1])
    max_vol = float(max(volume_by_bin))

    # Point of Control
    poc_bin   = int(np.argmax(volume_by_bin))
    poc_price = round(bin_centers[poc_bin], 4)

    # Value Area
    va_low, va_high = _compute_value_area(list(volume_by_bin), bin_centers, poc_bin)
    va_low  = round(va_low,  4)
    va_high = round(va_high, 4)

    # HVN peaks
    peak_bins = _find_peaks(list(volume_by_bin))
    hvn_levels = []
    for b in peak_bins:
        price  = round(bin_centers[b], 4)
        vol    = float(volume_by_bin[b])
        strength_pct = round(vol / max_vol * 100, 1)
        hvn_levels.append({"price": price, "volume": vol, "strength_pct": strength_pct})

    hvn_levels = _merge_nearby(hvn_levels)

    # LVN troughs
    trough_bins = _find_troughs(list(volume_by_bin))
    lvn_levels = []
    for b in trough_bins:
        lvn_levels.append({"price": round(bin_centers[b], 4)})

    # Split into support / resistance relative to current price
    supports    = sorted(
        [l for l in hvn_levels if l["price"] < current_price],
        key=lambda x: x["price"],
        reverse=True  # closest first
    )[:n_levels]

    resistances = sorted(
        [l for l in hvn_levels if l["price"] > current_price],
        key=lambda x: x["price"]  # closest first
    )[:n_levels]

    # Add distance_pct to each level
    def _enrich(levels):
        enriched = []
        for l in levels:
            dist = round((l["price"] - current_price) / current_price * 100, 2)
            enriched.append({
                "price":        l["price"],
                "distance_pct": dist,
                "strength_pct": l.get("strength_pct", 0),
            })
        return enriched

    supports    = _enrich(supports)
    resistances = _enrich(resistances)

    nearest_support    = supports[0]    if supports    else None
    nearest_resistance = resistances[0] if resistances else None

    # VWAPs
    vwap_20d = _compute_vwap(hist, 20)
    vwap_50d = _compute_vwap(hist, 50)

    # POC distance
    poc_dist_pct = round((poc_price - current_price) / current_price * 100, 2)

    # Plain-language interpretation
    interp_parts = []
    if nearest_support:
        interp_parts.append(
            f"Nearest support ${nearest_support['price']} ({nearest_support['distance_pct']}%, "
            f"strength {nearest_support['strength_pct']}%)"
        )
    if nearest_resistance:
        interp_parts.append(
            f"nearest resistance ${nearest_resistance['price']} ({nearest_resistance['distance_pct']:+.2f}%, "
            f"strength {nearest_resistance['strength_pct']}%)"
        )
    if poc_dist_pct >= 0:
        interp_parts.append(f"POC ${poc_price} is {poc_dist_pct}% above (acts as resistance)")
    else:
        interp_parts.append(f"POC ${poc_price} is {abs(poc_dist_pct)}% below (acts as support)")

    if vwap_20d:
        label = "above" if current_price > vwap_20d else "below"
        interp_parts.append(f"price is {label} 20d VWAP ${vwap_20d}")

    return {
        "current_price":      round(current_price, 4),
        "poc_price":          poc_price,
        "poc_distance_pct":   poc_dist_pct,
        "value_area_high":    va_high,
        "value_area_low":     va_low,
        "vwap_20d":           vwap_20d,
        "vwap_50d":           vwap_50d,
        "support_levels":     supports,
        "resistance_levels":  resistances,
        "nearest_support":    nearest_support,
        "nearest_resistance": nearest_resistance,
        "lvn_levels":         [l["price"] for l in lvn_levels[:5]],
        "interpretation":     ". ".join(interp_parts) + ".",
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Volume Profile — Support & Resistance")
    parser.add_argument("ticker",           help="Ticker symbol (e.g. AAPL, GME)")
    parser.add_argument("--period",  default=DEFAULT_PERIOD, help="yfinance period (default: 1y)")
    parser.add_argument("--bins",    type=int, default=DEFAULT_BINS, help="Price bins (default: 60)")
    args = parser.parse_args()

    ticker = args.ticker.upper()
    print(f"\nBuilding volume profile for {ticker}...")
    result = get_volume_profile(ticker, period=args.period, n_bins=args.bins)

    if not result:
        print("  ERROR: Could not compute (insufficient data or download failed).")
        sys.exit(1)

    cur = result["current_price"]
    print(f"\n  Current price:   ${cur}")
    print(f"  POC:             ${result['poc_price']}  ({result['poc_distance_pct']:+.2f}%)")
    print(f"  Value Area:      ${result['value_area_low']} — ${result['value_area_high']}")
    print(f"  VWAP 20d:        ${result['vwap_20d']}")
    print(f"  VWAP 50d:        ${result['vwap_50d']}")

    print(f"\n  RESISTANCE (above ${cur}):")
    for r in result["resistance_levels"]:
        print(f"    ${r['price']:>10.2f}  dist: {r['distance_pct']:+.2f}%  strength: {r['strength_pct']}%")

    print(f"\n  SUPPORT (below ${cur}):")
    for s in result["support_levels"]:
        print(f"    ${s['price']:>10.2f}  dist: {s['distance_pct']:+.2f}%  strength: {s['strength_pct']}%")

    if result["lvn_levels"]:
        print(f"\n  LVN (thin air zones): {result['lvn_levels']}")

    print(f"\n  → {result['interpretation']}\n")


if __name__ == "__main__":
    main()
