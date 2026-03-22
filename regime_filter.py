#!/usr/bin/env python3
"""
================================================================================
REGIME FILTER — Macro + Sector Regime Classification
================================================================================
Classifies market-wide regime as RISK_ON | RISK_OFF | TRANSITIONAL using:
  - SPY trend (price vs 50MA and 200MA)
  - VIX volatility level
  - HYG credit 20-day return z-score vs 1-year history
  - T10Y2Y yield curve spread (FRED)

Also classifies 11 GICS sectors as BULL | BEAR | NEUTRAL via MA crossover + RS.

REGIME MODIFIERS (applied across all modules):
  Position multiplier:  RISK_ON=1.0x  TRANSITIONAL=0.7x  RISK_OFF=0.4x
  Factor weights:       shift toward quality/mean-rev in RISK_OFF
  Max conviction cap:   RISK_ON=5     TRANSITIONAL=4      RISK_OFF=3

USAGE:
    python3 regime_filter.py              # Print current market regime
    python3 regime_filter.py --sectors    # Also print all 11 sector regimes
    python3 regime_filter.py --refresh    # Force cache refresh

CACHE:
    data/regime_cache.json  (24-hr TTL — market regime + sector regimes + FRED)
    data/sector_cache.json  (7-day TTL — ticker → sector mapping)
================================================================================
"""

import argparse
import io
import json
import logging
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

logger = logging.getLogger(__name__)

# ─── Config imports ───────────────────────────────────────────────────────────
try:
    from config import (
        REGIME_CACHE_TTL_HOURS,
        FRED_YIELD_CURVE_SERIES,
        FRED_USER_AGENT,
        REGIME_RISK_ON_THRESHOLD,
        REGIME_RISK_OFF_THRESHOLD,
    )
except ImportError:
    REGIME_CACHE_TTL_HOURS    = 24
    FRED_YIELD_CURVE_SERIES   = "T10Y2Y"
    FRED_USER_AGENT           = "SignalEngine/1.0 (research)"
    REGIME_RISK_ON_THRESHOLD  = 3
    REGIME_RISK_OFF_THRESHOLD = 0

# ─── Paths ────────────────────────────────────────────────────────────────────
_DATA_DIR          = Path(__file__).parent / "data"
_REGIME_CACHE_PATH = _DATA_DIR / "regime_cache.json"
_SECTOR_CACHE_PATH = _DATA_DIR / "sector_cache.json"

# ─── Sector ETF map ───────────────────────────────────────────────────────────
SECTOR_ETFS: Dict[str, str] = {
    "tech":             "XLK",
    "financials":       "XLF",
    "energy":           "XLE",
    "healthcare":       "XLV",
    "consumer_disc":    "XLY",
    "consumer_staples": "XLP",
    "industrials":      "XLI",
    "materials":        "XLB",
    "utilities":        "XLU",
    "real_estate":      "XLRE",
    "comm_services":    "XLC",
}

# ─── Regime-adjusted factor weights ───────────────────────────────────────────
# Keys match the keys in config.EQUITY_FACTORS.
# TRANSITIONAL → caller uses config defaults (get_factor_weights returns {}).
_FACTOR_WEIGHTS: Dict[str, Dict[str, float]] = {
    "RISK_ON": {
        "momentum_12_1":          0.38,
        "momentum_6_1":           0.22,
        "mean_reversion_5d":      0.12,
        "volatility_quality":     0.13,
        "risk_adjusted_momentum": 0.15,
    },
    "RISK_OFF": {
        "momentum_12_1":          0.20,
        "momentum_6_1":           0.12,
        "mean_reversion_5d":      0.28,
        "volatility_quality":     0.22,
        "risk_adjusted_momentum": 0.18,
    },
}


# ==============================================================================
# SECTION 1: CACHE HELPERS
# ==============================================================================

def _load_json_cache(path: Path) -> dict:
    """Load JSON cache from disk; return empty dict on any error."""
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}


def _save_json_cache(path: Path, data: dict) -> None:
    """Write JSON cache to disk; silently suppress errors."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, default=str))
    except Exception as exc:
        logger.warning("Could not write cache %s: %s", path, exc)


def _cache_is_fresh(timestamp_iso: Optional[str], ttl_hours: float) -> bool:
    """Return True if *timestamp_iso* is within *ttl_hours* of now."""
    if not timestamp_iso:
        return False
    try:
        ts = datetime.fromisoformat(timestamp_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(tz=timezone.utc) - ts).total_seconds() / 3600.0
        return age_hours < ttl_hours
    except Exception:
        return False


# ==============================================================================
# SECTION 2: FRED YIELD CURVE
# ==============================================================================

def _fetch_fred_yield_curve() -> Optional[float]:
    """
    Fetch the T10Y2Y spread from FRED.

    Returns the latest float value (percentage points), or None on failure.
    Result is cached inside data/regime_cache.json for REGIME_CACHE_TTL_HOURS.
    """
    cache    = _load_json_cache(_REGIME_CACHE_PATH)
    fc       = cache.get("fred_yield_curve", {})
    if _cache_is_fresh(fc.get("computed_at"), REGIME_CACHE_TTL_HOURS):
        return fc.get("value")

    url     = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={FRED_YIELD_CURVE_SERIES}"
    headers = {"User-Agent": FRED_USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        df.columns = [c.strip() for c in df.columns]
        # FRED CSV: columns are DATE and <series_id>; missing values represented as "."
        val_col = [c for c in df.columns if c.upper() != "DATE"][0]
        df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
        df = df.dropna(subset=[val_col])
        if df.empty:
            return None
        value = float(df[val_col].iloc[-1])
        # Persist to cache
        cache["fred_yield_curve"] = {
            "value":       value,
            "series":      FRED_YIELD_CURVE_SERIES,
            "computed_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        _save_json_cache(_REGIME_CACHE_PATH, cache)
        return value
    except Exception as exc:
        logger.warning("FRED fetch failed: %s", exc)
        return None


# ==============================================================================
# SECTION 3: MARKET-WIDE REGIME
# ==============================================================================

def get_market_regime(force_refresh: bool = False) -> dict:
    """
    Compute market-wide regime (RISK_ON | RISK_OFF | TRANSITIONAL).

    Uses 4 signals scored as integers:
      Signal 1 — SPY trend vs 50MA / 200MA        : -1 to +2
      Signal 2 — VIX level                         : -2 to +2
      Signal 3 — HYG 20d return z-score (1-yr hist): -1 to +1
      Signal 4 — T10Y2Y yield curve spread (FRED)  : -2 to +1

    Total score range: -6 to +6
    Classification: score >= REGIME_RISK_ON_THRESHOLD  → RISK_ON
                    score <= REGIME_RISK_OFF_THRESHOLD  → RISK_OFF
                    else                                → TRANSITIONAL

    Returns dict:
      {
        'regime':             'RISK_ON' | 'TRANSITIONAL' | 'RISK_OFF',
        'score':              int,
        'components':         {'trend': int, 'volatility': int,
                               'credit': int, 'yield_curve': int},
        'vix':                float | None,
        'spy_vs_200ma':       float | None,   # % above/below 200MA
        'yield_curve_spread': float | None,
        'computed_at':        ISO-8601 timestamp,
      }
    Cache: stored under key 'market_regime' in data/regime_cache.json.
    """
    cache    = _load_json_cache(_REGIME_CACHE_PATH)
    mr_cache = cache.get("market_regime", {})
    if not force_refresh and _cache_is_fresh(mr_cache.get("computed_at"), REGIME_CACHE_TTL_HOURS):
        return mr_cache

    result                  = _compute_market_regime()
    cache["market_regime"]  = result
    _save_json_cache(_REGIME_CACHE_PATH, cache)
    return result


def _compute_market_regime() -> dict:
    """Internal: compute market regime without cache check."""
    components   = {"trend": 0, "volatility": 0, "credit": 0, "yield_curve": 0}
    vix_val      = None
    spy_vs_200ma = None

    # ── Download SPY, VIX, HYG in a single batch ─────────────────────────────
    spy = vix = hyg = pd.Series(dtype=float)
    try:
        raw = yf.download(
            ["SPY", "^VIX", "HYG"],
            period="2y",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if not raw.empty:
            close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
            spy = close["SPY"].dropna()   if "SPY"  in close.columns else spy
            vix = close["^VIX"].dropna()  if "^VIX" in close.columns else vix
            hyg = close["HYG"].dropna()   if "HYG"  in close.columns else hyg
    except Exception as exc:
        logger.warning("Market regime download failed: %s", exc)

    # ── Signal 1: SPY trend (50MA vs 200MA) ───────────────────────────────────
    if len(spy) >= 200:
        price        = float(spy.iloc[-1])
        ma50         = float(spy.iloc[-50:].mean())
        ma200        = float(spy.iloc[-200:].mean())
        spy_vs_200ma = round((price / ma200 - 1) * 100, 2)
        above_50     = price > ma50
        above_200    = price > ma200
        if above_50 and above_200:
            components["trend"] = 2
        elif above_50 or above_200:
            components["trend"] = 1
        else:
            components["trend"] = -1

    # ── Signal 2: VIX level ────────────────────────────────────────────────────
    if len(vix) >= 1:
        vix_val = round(float(vix.iloc[-1]), 2)
        if vix_val < 18:
            components["volatility"] = 2
        elif vix_val < 25:
            components["volatility"] = 1
        elif vix_val <= 30:
            components["volatility"] = 0
        else:
            components["volatility"] = -2

    # ── Signal 3: HYG credit (20d return z-score vs 1-year history) ───────────
    if len(hyg) >= 41:   # need at least 20+21 bars
        ret_20d = float(hyg.iloc[-1] / hyg.iloc[-21] - 1)
        # 1-year rolling window of 20-day returns
        window   = hyg.iloc[-min(len(hyg), 252 + 21):]
        hist_ret = [
            float(window.iloc[i] / window.iloc[i - 20] - 1)
            for i in range(20, len(window))
        ]
        if len(hist_ret) >= 10:
            mean_r = float(np.mean(hist_ret))
            std_r  = float(np.std(hist_ret, ddof=1))
            if std_r > 0:
                z = (ret_20d - mean_r) / std_r
                if z > 0.5:
                    components["credit"] = 1
                elif z < -0.5:
                    components["credit"] = -1

    # ── Signal 4: Yield curve spread (FRED T10Y2Y) ────────────────────────────
    yield_curve_spread = _fetch_fred_yield_curve()
    if yield_curve_spread is not None:
        if yield_curve_spread > 0.50:
            components["yield_curve"] = 1
        elif yield_curve_spread >= 0:
            components["yield_curve"] = 0
        else:                              # inverted
            components["yield_curve"] = -2

    # ── Classify ──────────────────────────────────────────────────────────────
    score = sum(components.values())
    if score >= REGIME_RISK_ON_THRESHOLD:
        regime = "RISK_ON"
    elif score <= REGIME_RISK_OFF_THRESHOLD:
        regime = "RISK_OFF"
    else:
        regime = "TRANSITIONAL"

    return {
        "regime":             regime,
        "score":              score,
        "components":         components,
        "vix":                vix_val,
        "spy_vs_200ma":       spy_vs_200ma,
        "yield_curve_spread": round(yield_curve_spread, 3) if yield_curve_spread is not None else None,
        "computed_at":        datetime.now(tz=timezone.utc).isoformat(),
    }


# ==============================================================================
# SECTION 4: SECTOR REGIMES
# ==============================================================================

def get_sector_regimes(force_refresh: bool = False) -> dict:
    """
    Classify 11 GICS sectors as BULL | BEAR | NEUTRAL.

    Criteria per sector ETF:
      BULL: 50MA > 200MA  AND  20-day RS vs SPY > 0
      BEAR: 50MA < 200MA  AND  20-day RS vs SPY < 0
      NEUTRAL: mixed signals or insufficient data

    All sector ETFs + SPY downloaded in a single yf.download() call.

    Returns dict of the form {'tech': 'BULL', 'financials': 'NEUTRAL', ...,
                               'computed_at': <ISO timestamp>}
    Cache: stored under key 'sector_regimes' in data/regime_cache.json.
    """
    cache    = _load_json_cache(_REGIME_CACHE_PATH)
    sr_cache = cache.get("sector_regimes", {})
    if not force_refresh and _cache_is_fresh(sr_cache.get("computed_at"), REGIME_CACHE_TTL_HOURS):
        return sr_cache

    result                   = _compute_sector_regimes()
    cache["sector_regimes"]  = result
    _save_json_cache(_REGIME_CACHE_PATH, cache)
    return result


def _compute_sector_regimes() -> dict:
    """Internal: compute sector regimes without cache check."""
    result = {"computed_at": datetime.now(tz=timezone.utc).isoformat()}

    etf_list = list(SECTOR_ETFS.values()) + ["SPY"]
    close    = pd.DataFrame()
    try:
        raw = yf.download(
            etf_list,
            period="1y",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if not raw.empty:
            close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    except Exception as exc:
        logger.warning("Sector regime download failed: %s", exc)

    spy = close["SPY"].dropna() if "SPY" in close.columns else pd.Series(dtype=float)

    for sector, etf in SECTOR_ETFS.items():
        if etf not in close.columns:
            result[sector] = "NEUTRAL"
            continue
        px = close[etf].dropna()
        if len(px) < 200:
            result[sector] = "NEUTRAL"
            continue

        ma50  = float(px.iloc[-50:].mean())
        ma200 = float(px.iloc[-200:].mean())
        cross_bull = ma50 > ma200   # 50MA above 200MA → Golden Cross

        rs_bull: Optional[bool] = None
        if len(spy) >= 21 and len(px) >= 21:
            etf_ret = float(px.iloc[-1] / px.iloc[-21] - 1)
            spy_ret = float(spy.iloc[-1] / spy.iloc[-21] - 1)
            rs_bull = etf_ret > spy_ret

        if cross_bull and rs_bull is True:
            result[sector] = "BULL"
        elif not cross_bull and rs_bull is False:
            result[sector] = "BEAR"
        else:
            result[sector] = "NEUTRAL"

    return result


# ==============================================================================
# SECTION 5: TICKER → SECTOR MAPPING
# ==============================================================================

def get_ticker_sector(ticker: str) -> Optional[str]:
    """
    Map *ticker* to its GICS sector string using yfinance Ticker.info.

    Sector is cached in data/sector_cache.json with a 7-day TTL.
    Returns None if the sector is unavailable or the fetch fails.
    """
    ticker = ticker.upper().strip()
    cache  = _load_json_cache(_SECTOR_CACHE_PATH)
    entry  = cache.get(ticker, {})
    if _cache_is_fresh(entry.get("cached_at"), 24 * 7):   # 7-day TTL
        return entry.get("sector")

    sector: Optional[str] = None
    try:
        info   = yf.Ticker(ticker).info
        sector = info.get("sector") or info.get("sectorKey") or None
    except Exception:
        pass

    cache[ticker] = {
        "sector":    sector,
        "cached_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    _save_json_cache(_SECTOR_CACHE_PATH, cache)
    return sector


# ==============================================================================
# SECTION 6: REGIME MODIFIERS
# ==============================================================================

def get_position_size_multiplier(market_regime: str) -> float:
    """
    Return the position-size scalar for the given regime:
      RISK_ON       → 1.0  (full sizing)
      TRANSITIONAL  → 0.7  (reduce 30 %)
      RISK_OFF      → 0.4  (reduce 60 %)
    """
    return {"RISK_ON": 1.0, "TRANSITIONAL": 0.7, "RISK_OFF": 0.4}.get(market_regime, 0.7)


def get_factor_weights(market_regime: str) -> dict:
    """
    Return adjusted equity-factor weights for signal_engine.py.

    Keys match config.EQUITY_FACTORS top-level keys:
      momentum_12_1, momentum_6_1, mean_reversion_5d,
      volatility_quality, risk_adjusted_momentum

    TRANSITIONAL returns {} → caller should use config.py defaults unchanged.
    """
    return dict(_FACTOR_WEIGHTS.get(market_regime, {}))


def get_max_conviction(market_regime: str) -> int:
    """
    Maximum conviction score ai_quant.py is allowed to assign:
      RISK_ON       → 5
      TRANSITIONAL  → 4
      RISK_OFF      → 3
    """
    return {"RISK_ON": 5, "TRANSITIONAL": 4, "RISK_OFF": 3}.get(market_regime, 4)


# ==============================================================================
# SECTION 7: FORMATTED OUTPUT HELPERS
# ==============================================================================

def format_regime_line(market_regime: dict, sector_regime: Optional[str] = None) -> str:
    """
    Return a single-line regime summary suitable for embedding in reports:
      "Regime: RISK_ON (score: +4) | Sector: BULL | Position multiplier: 1.0x"
    """
    regime = market_regime.get("regime", "UNKNOWN")
    score  = market_regime.get("score", 0)
    mult   = get_position_size_multiplier(regime)
    parts  = [f"Regime: {regime} (score: {score:+d})", f"Position multiplier: {mult:.1f}x"]
    if sector_regime:
        parts.insert(1, f"Sector: {sector_regime}")
    return " | ".join(parts)


def _print_regime_summary(mr: dict, sr: Optional[dict] = None) -> None:
    """Pretty-print market + optional sector regime to stdout."""
    regime  = mr.get("regime", "UNKNOWN")
    score   = mr.get("score", "?")
    comp    = mr.get("components", {})
    vix     = mr.get("vix")
    spy200  = mr.get("spy_vs_200ma")
    yc      = mr.get("yield_curve_spread")

    icon    = {"RISK_ON": "GREEN", "TRANSITIONAL": "YELLOW", "RISK_OFF": "RED"}.get(regime, "?")
    mult    = get_position_size_multiplier(regime)
    max_c   = get_max_conviction(regime)

    print()
    print("=" * 60)
    print(f"  MARKET REGIME: [{icon}] {regime}  (score: {score:+d})")
    print(f"  Position multiplier: {mult:.1f}x  |  Max conviction cap: {max_c}/5")
    print("=" * 60)
    spy_str = f"  (SPY {spy200:+.1f}% vs 200MA)" if spy200 is not None else ""
    print(f"  Signal 1 — Trend (SPY 50/200MA):  {comp.get('trend', '?'):+d}{spy_str}")
    vix_str = f"  (VIX = {vix:.1f})" if vix is not None else ""
    print(f"  Signal 2 — Volatility (VIX):      {comp.get('volatility', '?'):+d}{vix_str}")
    print(f"  Signal 3 — Credit (HYG 20d z):    {comp.get('credit', '?'):+d}")
    yc_str  = f"  (spread = {yc:+.3f}%)" if yc is not None else ""
    print(f"  Signal 4 — Yield curve (T10Y2Y):  {comp.get('yield_curve', '?'):+d}{yc_str}")
    print()

    fw = get_factor_weights(regime)
    if fw:
        print(f"  Active factor weights ({regime}):")
        for k, v in fw.items():
            print(f"    {k:<28} {v:.2f}")
    else:
        print("  Factor weights: config.py defaults (TRANSITIONAL)")

    if sr:
        print()
        print("  SECTOR REGIMES:")
        for sector, st in sorted(sr.items()):
            if sector == "computed_at":
                continue
            icon_s = {"BULL": "UP  ", "BEAR": "DOWN", "NEUTRAL": "FLAT"}.get(st, "?   ")
            print(f"    {sector:<20} {icon_s} {st}")


# ==============================================================================
# SECTION 8: CLI ENTRY POINT
# ==============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regime Filter — macro + sector regime classification"
    )
    parser.add_argument("--sectors", action="store_true",
                        help="Also display all 11 sector regimes")
    parser.add_argument("--refresh", action="store_true",
                        help="Force cache refresh (ignore TTL)")
    args = parser.parse_args()

    mr = get_market_regime(force_refresh=args.refresh)
    sr = get_sector_regimes(force_refresh=args.refresh) if args.sectors else None
    _print_regime_summary(mr, sr)
    print()


if __name__ == "__main__":
    main()
