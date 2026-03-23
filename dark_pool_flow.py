#!/usr/bin/env python3
"""
================================================================================
DARK POOL FLOW v1.0 — FINRA ATS Institutional Accumulation/Distribution Signal
================================================================================
Ingests FINRA OTC/ATS consolidated short volume data to detect institutional
accumulation and distribution patterns not visible in standard retail tools.

WHY THIS IS DIFFERENTIATED:
    FINRA publishes daily consolidated short volume files (free, public) that
    capture ALL reported short sales across NASDAQ, NYSE, OTC, and ATS venues.
    The ratio of off-exchange to total volume, combined with the trend in short
    interest, gives a unique view into institutional routing behaviour that no
    retail screener aggregates automatically.

DATA SOURCE:
    FINRA Consolidated Short Volume (CNMS) — daily files
    URL:     https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt
    Format:  pipe-delimited
    Columns: Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
    Release: ~8pm ET on the trading day

SIGNAL LOGIC:
    Short ratio  = ShortVolume / TotalVolume  (FINRA-reported short fraction)
    Declining short ratio over time  → institutional covering / accumulation
    Unusually low short ratio today  → potential stealth accumulation signal
    High off-exchange routing ratio  → heavy dark pool / ATS institutional use
    Rising / unusually high short ratio → distribution / increased bearish flow

CACHE STRATEGY:
    Raw FINRA files: data/finra_cache/{YYYYMMDD}.csv  — immutable, never expire
    Scan results   : data/dark_pool_latest.json        — date-stamped result cache

USAGE:
    python3 dark_pool_flow.py --ticker AAPL
    python3 dark_pool_flow.py --tickers AAPL MSFT NVDA
    python3 dark_pool_flow.py --scan --output data/dark_pool_latest.json
    python3 dark_pool_flow.py --scan                  # scans all tickers in watchlist.txt

OUTPUT (per ticker):
    dark_pool_score      : 0-100  (50 = neutral baseline)
    signal               : ACCUMULATION | DISTRIBUTION | NEUTRAL
    short_ratio_today    : float  (today's short/total, e.g. 0.46 = 46%)
    short_ratio_mean     : float  (period average)
    short_ratio_trend    : float  (slope per day; negative = shorts declining)
    short_ratio_zscore   : float  (today vs lookback mean/std)
    dark_pool_intensity  : float  (finra total vol / exchange vol; >0.40 = heavy off-exchange)
    days_of_data         : int
    interpretation       : str   (one-sentence summary)

REQUIREMENTS:
    pip install requests numpy pandas yfinance

ERROR HANDLING:
    - FINRA CDN 404 for current day  → silently fall back up to 3 prior business days
    - No data for last 3 business days → return None, log warning
    - Never raises; pipeline-safe for missing dark pool signal
================================================================================
"""

import argparse
import json
import logging
import os
import sys
import warnings
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds — imported from config.py, fallback to defaults
# ---------------------------------------------------------------------------
try:
    from config import (
        DARK_POOL_ACCUMULATION_THRESHOLD,
        DARK_POOL_DISTRIBUTION_THRESHOLD,
        DARK_POOL_INTENSITY_HIGH,
    )
except ImportError:
    DARK_POOL_ACCUMULATION_THRESHOLD = 65
    DARK_POOL_DISTRIBUTION_THRESHOLD = 35
    DARK_POOL_INTENSITY_HIGH = 0.45

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_MODULE_DIR = Path(__file__).parent
_CACHE_DIR  = _MODULE_DIR / "data" / "finra_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_RESULT_CACHE_PATH = _MODULE_DIR / "data" / "dark_pool_latest.json"

# FINRA CDN base URL
_FINRA_BASE = "https://cdn.finra.org/equity/regsho/daily"

# Requests session (connection pooling across date fetches)
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "SignalEngine/1.0 (educational research)"})


# ==============================================================================
# SECTION 1: DATE UTILITIES
# ==============================================================================

def _prev_business_days(n: int, reference: datetime = None) -> List[datetime]:
    """
    Return the n most recent business days (Mon–Fri), ending on or before
    reference (defaults to now).  Oldest first in the returned list.
    """
    if reference is None:
        reference = datetime.now()
    days: List[datetime] = []
    d = reference
    while len(days) < n:
        if d.weekday() < 5:   # 0=Mon … 4=Fri
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()   # oldest → newest
    return days


# ==============================================================================
# SECTION 2: RAW FILE FETCHING
# ==============================================================================

def _normalize_df(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    Normalise a raw FINRA pipe-delimited DataFrame (or a previously cached CSV)
    into (symbol, short_volume, total_volume, short_ratio).

    Handles:
      • Both raw column names (ShortVolume, TotalVolume) and our cached names
      • Multiple Market rows per symbol (NASDAQ + NYSE for the same ticker)
      • Numeric coercion and footer/blank rows dropped
    """
    try:
        df = df.copy()
        df.columns = [c.strip().lower() for c in df.columns]

        # Map raw and cached column names → canonical targets
        _aliases: Dict[str, List[str]] = {
            "symbol":       ["symbol"],
            "short_volume": ["shortvolume", "short_volume"],
            "total_volume": ["totalvolume", "total_volume"],
        }
        rename_map: Dict[str, str] = {}
        for target, aliases in _aliases.items():
            for alias in aliases:
                if alias in df.columns:
                    rename_map[alias] = target
                    break

        df = df.rename(columns=rename_map)

        required = {"symbol", "short_volume", "total_volume"}
        if not required.issubset(df.columns):
            logger.debug(
                "dark_pool_flow._normalize_df: missing columns %s (have %s)",
                required - set(df.columns),
                df.columns.tolist(),
            )
            return None

        # Drop footer / blank rows
        df = df.dropna(subset=["symbol"])
        df = df[df["symbol"].astype(str).str.strip() != ""]

        # Coerce numerics
        df["short_volume"] = pd.to_numeric(df["short_volume"], errors="coerce").fillna(0)
        df["total_volume"] = pd.to_numeric(df["total_volume"], errors="coerce").fillna(0)

        # Aggregate across markets (FINRA may list NASDAQ + NYSE separately)
        df = (
            df.groupby("symbol", as_index=False)
            .agg(
                short_volume=("short_volume", "sum"),
                total_volume=("total_volume", "sum"),
            )
        )

        # Remove zero-volume rows (avoid div-by-zero)
        df = df[df["total_volume"] > 0]

        df["short_ratio"] = df["short_volume"] / df["total_volume"]
        df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()

        return df[["symbol", "short_volume", "total_volume", "short_ratio"]].reset_index(drop=True)

    except Exception as exc:
        logger.debug("dark_pool_flow._normalize_df: %s", exc)
        return None


def _fetch_single_date(date: datetime) -> Optional[pd.DataFrame]:
    """
    Fetch (or load from disk cache) the FINRA CNMS short volume file for one date.

    Cache policy:
      • Files are immutable historical data once published — never re-fetch if cached.
      • A corrupt cache file is deleted and re-fetched automatically.

    Returns None on HTTP 404, network error, or parse failure.
    """
    date_str  = date.strftime("%Y%m%d")
    cache_path = _CACHE_DIR / f"{date_str}.csv"

    # ── serve from disk cache ────────────────────────────────────────────────
    if cache_path.exists():
        try:
            df = pd.read_csv(cache_path, dtype=str)
            normalized = _normalize_df(df)
            if normalized is not None and not normalized.empty:
                return normalized
        except Exception as exc:
            logger.debug("dark_pool_flow: corrupt cache for %s (%s) — re-fetching", date_str, exc)
        # Corrupt or empty — delete and re-fetch
        try:
            cache_path.unlink()
        except OSError:
            pass

    # ── fetch from FINRA CDN ─────────────────────────────────────────────────
    url = f"{_FINRA_BASE}/CNMSshvol{date_str}.txt"
    try:
        resp = _SESSION.get(url, timeout=25)
        if resp.status_code == 404:
            logger.debug("dark_pool_flow: 404 for %s", url)
            return None
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.debug("dark_pool_flow: HTTP error for %s: %s", url, exc)
        return None

    # ── parse ────────────────────────────────────────────────────────────────
    try:
        df = pd.read_csv(StringIO(resp.text), sep="|", dtype=str)
    except Exception as exc:
        logger.warning("dark_pool_flow: parse error for %s: %s", date_str, exc)
        return None

    normalized = _normalize_df(df)
    if normalized is None or normalized.empty:
        logger.debug("dark_pool_flow: empty/unparseable file for %s", date_str)
        return None

    # ── write to cache ───────────────────────────────────────────────────────
    try:
        normalized.to_csv(cache_path, index=False)
    except OSError as exc:
        logger.debug("dark_pool_flow: cache write failed for %s: %s", date_str, exc)

    return normalized


def fetch_finra_weekly_short_volume(date: datetime = None) -> Optional[pd.DataFrame]:
    """
    Fetch the FINRA consolidated short volume file for the given date.

    Falls back up to 3 prior business days if the requested date's file is not
    yet published (FINRA releases ~8pm ET) or returns a 404.

    Parameters
    ----------
    date : datetime
        Target date (defaults to today).

    Returns
    -------
    pd.DataFrame with columns: symbol, short_volume, total_volume, short_ratio
    None if no file is available within the 4-day fallback window.
    """
    if date is None:
        date = datetime.now()

    # Try the given date then up to 3 prior business days
    candidates = _prev_business_days(4, reference=date)
    candidates_desc = sorted(candidates, reverse=True)   # newest first for fallback

    for candidate in candidates_desc:
        df = _fetch_single_date(candidate)
        if df is not None:
            return df

    logger.warning(
        "dark_pool_flow: no FINRA data available for %s or 3 prior business days",
        date.strftime("%Y-%m-%d"),
    )
    return None


# ==============================================================================
# SECTION 3: EXCHANGE VOLUME (yfinance)
# ==============================================================================

def _get_exchange_volumes(ticker: str, dates: List[str]) -> Dict[str, float]:
    """
    Fetch daily exchange-reported volume from yfinance for the given date strings
    (format: YYYY-MM-DD).  Used to compute the dark pool intensity ratio.

    Returns dict of {YYYY-MM-DD: volume_float}.
    Returns {} silently on any error — dark pool intensity defaults to 0.
    """
    if not dates:
        return {}
    try:
        import yfinance as yf
        start = min(dates)
        end_dt = datetime.strptime(max(dates), "%Y-%m-%d") + timedelta(days=2)
        end = end_dt.strftime("%Y-%m-%d")
        hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
        if hist.empty:
            return {}
        return {
            idx.strftime("%Y-%m-%d"): float(row["Volume"])
            for idx, row in hist.iterrows()
        }
    except Exception:
        return {}


# ==============================================================================
# SECTION 4: SIGNAL COMPUTATION
# ==============================================================================

def compute_dark_pool_signal(
    ticker: str,
    lookback_days: int = 20,
    _preloaded_frames: Dict[str, pd.DataFrame] = None,
) -> Optional[dict]:
    """
    Compute a dark pool / institutional flow signal for a single ticker.

    Parameters
    ----------
    ticker           : Equity symbol (e.g. "AAPL")
    lookback_days    : Number of business days to analyse (default 20 ≈ 4 weeks)
    _preloaded_frames: Optional dict of {date_str_YYYYMMDD: DataFrame} for batch
                       mode.  Avoids re-downloading the same FINRA files per ticker.

    Algorithm
    ---------
    1. Collect short_ratio for the last lookback_days from cached FINRA files.
    2. Fetch yfinance exchange volume for the same window.
    3. Compute:
         short_ratio_mean   : average short_ratio over the period
         short_ratio_trend  : linear regression slope of short_ratio (per day)
                              negative slope = short interest declining = bullish
         short_ratio_zscore : today's ratio vs period mean/std
         dark_pool_intensity: finra_total_volume / exchange_volume
                              > 0.40 means > 40% of reported volume is off-exchange
    4. Score (0–100, 50 = neutral):
         slope < -0.005/day (declining shorts fast) → +25
         slope < -0.002/day (declining shorts)      → +15
         zscore < -1.5 (unusually low shorting)     → +20
         dpi > DARK_POOL_INTENSITY_HIGH (0.45)       → +20
         dpi > 0.35                                  → +10
         zscore > +1.5 (unusually high shorting)    → −20
         slope > +0.005/day (shorts building fast)  → −25
         slope > +0.002/day (shorts building)       → −10

    Returns
    -------
    dict  — see module docstring for fields
    None  — if fewer than 5 days of data are available for the ticker
    """
    ticker = ticker.upper().strip()

    # Fetch more days than needed to survive weekends, holidays, missing files
    candidates = _prev_business_days(lookback_days + 7)

    # ── 1. Collect per-day records ─────────────────────────────────────────
    records: List[dict] = []
    for day in candidates:   # already oldest→newest
        date_str = day.strftime("%Y%m%d")
        if _preloaded_frames is not None:
            day_df = _preloaded_frames.get(date_str)
        else:
            day_df = _fetch_single_date(day)

        if day_df is None:
            continue

        row = day_df[day_df["symbol"] == ticker]
        if row.empty:
            continue

        records.append({
            "date":         day.strftime("%Y-%m-%d"),
            "short_volume": float(row["short_volume"].iloc[0]),
            "total_volume": float(row["total_volume"].iloc[0]),
            "short_ratio":  float(row["short_ratio"].iloc[0]),
        })

    # Trim to actual requested lookback window (newest N days)
    records = records[-lookback_days:]

    if len(records) < 5:
        return None

    # ── 2. Fetch exchange volumes for dark pool intensity ─────────────────
    dates = [r["date"] for r in records]
    exchange_vols = _get_exchange_volumes(ticker, dates)

    # ── 3. Build arrays ───────────────────────────────────────────────────
    short_ratios = np.array([r["short_ratio"] for r in records], dtype=float)
    n = len(short_ratios)
    x = np.arange(n, dtype=float)

    # Linear trend (slope per calendar day)
    slope = float(np.polyfit(x, short_ratios, 1)[0]) if n >= 2 else 0.0

    # Z-score of today's short ratio vs period
    sr_mean = float(short_ratios.mean())
    sr_std  = float(short_ratios.std(ddof=0))
    sr_today = float(short_ratios[-1])
    sr_zscore = (sr_today - sr_mean) / sr_std if sr_std > 1e-9 else 0.0

    # Dark pool intensity: average of (finra_vol / exchange_vol) over matched days
    intensities: List[float] = []
    for rec in records:
        ev = exchange_vols.get(rec["date"])
        if ev and ev > 0:
            intensities.append(rec["total_volume"] / ev)
    dpi = float(np.mean(intensities)) if intensities else 0.0

    # ── 4. Score (0-100, baseline 50) ─────────────────────────────────────
    score = 50

    # Trend contribution
    if slope < -0.005:
        score += 25
    elif slope < -0.002:
        score += 15
    elif slope > 0.005:
        score -= 25
    elif slope > 0.002:
        score -= 10

    # Z-score contribution
    if sr_zscore < -1.5:
        score += 20
    elif sr_zscore > 1.5:
        score -= 20

    # Dark pool intensity contribution
    if dpi > DARK_POOL_INTENSITY_HIGH:
        score += 20
    elif dpi > 0.35:
        score += 10

    score = max(0, min(100, int(round(score))))

    # ── 5. Classify ───────────────────────────────────────────────────────
    if score >= DARK_POOL_ACCUMULATION_THRESHOLD:
        signal = "ACCUMULATION"
    elif score <= DARK_POOL_DISTRIBUTION_THRESHOLD:
        signal = "DISTRIBUTION"
    else:
        signal = "NEUTRAL"

    interpretation = _make_interpretation(signal, slope, sr_zscore, dpi, sr_today)

    return {
        "ticker":                ticker,
        "dark_pool_score":       score,
        "signal":                signal,
        "short_ratio_today":     round(sr_today,  4),
        "short_ratio_mean":      round(sr_mean,   4),
        "short_ratio_trend":     round(slope,     6),
        "short_ratio_zscore":    round(sr_zscore, 3),
        "dark_pool_intensity":   round(dpi,       4),
        "days_of_data":          n,
        "interpretation":        interpretation,
        "short_ratio_history":   [
            {"date": r["date"], "short_ratio": round(r["short_ratio"] * 100, 2)}
            for r in records
        ],
    }


def _make_interpretation(
    signal: str,
    slope: float,
    zscore: float,
    dpi: float,
    sr_today: float,
) -> str:
    """One-sentence human-readable interpretation of the dark pool signal."""
    dpi_str = f"{dpi:.0%} off-exchange routing" if dpi > 0.01 else "exchange-reported routing"
    if slope < -0.002:
        trend_str = "declining short interest"
    elif slope > 0.002:
        trend_str = "rising short interest"
    else:
        trend_str = "flat short interest"

    if signal == "ACCUMULATION":
        return (
            f"Institutional accumulation likely: {trend_str}, "
            f"short ratio {sr_today:.1%} today ({dpi_str})."
        )
    elif signal == "DISTRIBUTION":
        return (
            f"Distribution risk detected: {trend_str}, "
            f"short ratio {sr_today:.1%} today ({dpi_str})."
        )
    else:
        return (
            f"No clear institutional bias: {trend_str}, "
            f"short ratio {sr_today:.1%} today ({dpi_str})."
        )


# ==============================================================================
# SECTION 5: BATCH SCAN
# ==============================================================================

def batch_scan(tickers: List[str], lookback_days: int = 20) -> List[dict]:
    """
    Run compute_dark_pool_signal for a list of tickers.

    Downloads all required FINRA files once before processing (not per-ticker).
    Tickers with fewer than 5 days of data are silently skipped.

    Parameters
    ----------
    tickers       : List of equity symbols
    lookback_days : Days of FINRA data per ticker (default 20)

    Returns
    -------
    List of result dicts sorted descending by dark_pool_score.
    Logs: "{n} tickers scanned, {k} with ACCUMULATION signal, {j} with DISTRIBUTION"
    """
    if not tickers:
        return []

    # ── Pre-load all required FINRA files once ────────────────────────────
    logger.info("dark_pool_flow: pre-loading FINRA files for %d-day lookback...", lookback_days)
    business_days = _prev_business_days(lookback_days + 7)
    preloaded: Dict[str, pd.DataFrame] = {}
    for day in business_days:
        date_str = day.strftime("%Y%m%d")
        df = _fetch_single_date(day)
        if df is not None:
            preloaded[date_str] = df

    logger.info("dark_pool_flow: loaded %d FINRA date files", len(preloaded))

    # ── Process tickers ───────────────────────────────────────────────────
    results: List[dict] = []
    for ticker in tickers:
        try:
            result = compute_dark_pool_signal(
                ticker,
                lookback_days=lookback_days,
                _preloaded_frames=preloaded,
            )
            if result is not None:
                results.append(result)
        except Exception as exc:
            logger.warning("dark_pool_flow: error processing %s: %s", ticker, exc)

    # ── Summary ───────────────────────────────────────────────────────────
    n = len(results)
    k = sum(1 for r in results if r["signal"] == "ACCUMULATION")
    j = sum(1 for r in results if r["signal"] == "DISTRIBUTION")
    logger.info(
        "dark_pool_flow: %d tickers scanned, %d ACCUMULATION, %d DISTRIBUTION", n, k, j
    )
    print(
        f"  Dark Pool Scan: {n} tickers processed | "
        f"{k} ACCUMULATION | {j} DISTRIBUTION | "
        f"{n - k - j} NEUTRAL"
    )

    return sorted(results, key=lambda r: r["dark_pool_score"], reverse=True)


# ==============================================================================
# SECTION 6: RESULT CACHE (data/dark_pool_latest.json)
# ==============================================================================

def save_result_cache(results: List[dict]) -> None:
    """
    Write batch_scan results to data/dark_pool_latest.json with a date stamp.
    Used by catalyst_screener and ai_quant to avoid re-fetching within the
    same run_master.sh pipeline execution.
    """
    try:
        _RESULT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated": datetime.now().isoformat(),
            "results":   results,
        }
        with open(_RESULT_CACHE_PATH, "w") as fh:
            json.dump(payload, fh, indent=2)
        logger.info("dark_pool_flow: result cache saved to %s", _RESULT_CACHE_PATH)
    except OSError as exc:
        logger.warning("dark_pool_flow: could not write result cache: %s", exc)


def load_result_cache() -> Dict[str, dict]:
    """
    Load today's pre-computed dark pool results from data/dark_pool_latest.json.

    Returns dict of {ticker: result_dict} if the file was generated today,
    else returns {} (caller should compute live).
    """
    try:
        if not _RESULT_CACHE_PATH.exists():
            return {}
        with open(_RESULT_CACHE_PATH) as fh:
            payload = json.load(fh)
        generated = payload.get("generated", "")
        # Stale if not from today
        if generated[:10] != datetime.now().strftime("%Y-%m-%d"):
            return {}
        return {r["ticker"]: r for r in payload.get("results", []) if "ticker" in r}
    except Exception as exc:
        logger.debug("dark_pool_flow.load_result_cache: %s", exc)
        return {}


# ==============================================================================
# SECTION 7: CATALYST SCREENER INTEGRATION HELPER
# ==============================================================================

def score_dark_pool(
    ticker: str,
    preloaded_frames: Dict[str, pd.DataFrame] = None,
    result_cache: Dict[str, dict] = None,
) -> dict:
    """
    Thin wrapper for catalyst_screener.py integration.

    Tries result_cache first (populated from data/dark_pool_latest.json),
    then pre-loaded FINRA frames, then live computation.

    Returns
    -------
    dict with keys:
        score  : int   (+2 for ACCUMULATION, -1 for DISTRIBUTION, 0 for NEUTRAL)
        max    : int   (always 2 — caps the bonus contribution)
        flags  : list  (human-readable flag strings for the report)
        signal : str   (ACCUMULATION | DISTRIBUTION | NEUTRAL)
        detail : dict  (full compute_dark_pool_signal output, or None)
    """
    try:
        # 1. Result cache (pre-computed by run_master.sh)
        result = None
        if result_cache:
            result = result_cache.get(ticker.upper())

        # 2. Pre-loaded FINRA frames (batch mode in screen_universe)
        if result is None:
            result = compute_dark_pool_signal(
                ticker, _preloaded_frames=preloaded_frames
            )

        if result is None:
            return {"score": 0, "max": 2, "flags": [], "signal": "NEUTRAL", "detail": None}

        sig = result["signal"]
        if sig == "ACCUMULATION":
            score = 2
            flags = [
                f"Dark pool ACCUMULATION (score {result['dark_pool_score']}/100): "
                f"{result['interpretation']}"
            ]
        elif sig == "DISTRIBUTION":
            score = -1
            flags = [
                f"Dark pool DISTRIBUTION (score {result['dark_pool_score']}/100): "
                f"{result['interpretation']}"
            ]
        else:
            score = 0
            flags = []

        return {"score": score, "max": 2, "flags": flags, "signal": sig, "detail": result}

    except Exception as exc:
        logger.debug("dark_pool_flow.score_dark_pool(%s): %s", ticker, exc)
        return {"score": 0, "max": 2, "flags": [], "signal": "NEUTRAL", "detail": None}


# ==============================================================================
# SECTION 8: CLI
# ==============================================================================

def _print_result(result: dict) -> None:
    """Pretty-print a single ticker's dark pool result to stdout."""
    sig_icon = {"ACCUMULATION": "↑", "DISTRIBUTION": "↓", "NEUTRAL": "─"}.get(
        result["signal"], "─"
    )
    print(
        f"\n  {result['ticker']:<8} {sig_icon} {result['signal']:<14}"
        f"  score {result['dark_pool_score']:>3}/100"
    )
    print(f"    Short ratio today   : {result['short_ratio_today']:.3%}")
    print(f"    Short ratio mean    : {result['short_ratio_mean']:.3%}")
    print(f"    Short ratio trend   : {result['short_ratio_trend']:+.5f}/day")
    print(f"    Short ratio z-score : {result['short_ratio_zscore']:+.2f}")
    print(f"    Dark pool intensity : {result['dark_pool_intensity']:.1%}")
    print(f"    Days of data        : {result['days_of_data']}")
    print(f"    Interpretation      : {result['interpretation']}")


def _load_watchlist() -> List[str]:
    """Load all tickers from watchlist.txt (FAVORITES + UNIVERSE auto block)."""
    paths = [_MODULE_DIR / "watchlist.txt", Path("watchlist.txt")]
    for path in paths:
        if not path.exists():
            continue
        tickers: List[str] = []
        seen: set = set()
        with open(path) as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                t = stripped.split("#")[0].strip().upper()
                if t and t not in seen:
                    seen.add(t)
                    tickers.append(t)
        return tickers
    return []


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FINRA ATS dark pool / institutional flow signal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--ticker",   type=str,           help="Single ticker deep dive")
    parser.add_argument("--tickers",  nargs="+",          help="Multiple tickers")
    parser.add_argument("--scan",     action="store_true", help="Scan all tickers in watchlist.txt")
    parser.add_argument("--output",   type=str,           help="Save JSON results to file")
    parser.add_argument("--lookback", type=int, default=20, help="Lookback days (default 20)")
    parser.add_argument("--verbose",  action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    tickers: List[str] = []
    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.tickers:
        tickers = [t.upper() for t in args.tickers]
    elif args.scan:
        tickers = _load_watchlist()

    if not tickers:
        parser.print_help()
        sys.exit(0)

    print(f"\n  Dark Pool Flow Scanner — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Tickers: {len(tickers)} | Lookback: {args.lookback} days")

    results = batch_scan(tickers, lookback_days=args.lookback)

    for r in results:
        _print_result(r)

    # Save to output file (defaults to data/dark_pool_latest.json for --scan)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        save_result_cache(results)   # always write to canonical path
        if str(out_path) != str(_RESULT_CACHE_PATH):
            with open(out_path, "w") as fh:
                json.dump({"generated": datetime.now().isoformat(), "results": results}, fh, indent=2)
        print(f"\n  Saved {len(results)} results → {out_path}")
    elif args.scan:
        # --scan without --output still writes to canonical cache path
        save_result_cache(results)
        print(f"\n  Cache written → {_RESULT_CACHE_PATH}")

    print()


if __name__ == "__main__":
    main()
