#!/usr/bin/env python3
# Simplified per Grok+Claude consensus. Can be deleted entirely in the future with zero downstream impact.
"""
================================================================================
DARK POOL FLOW — FINRA ATS Accumulation Flag (simplified)
================================================================================
Single signal: short_ratio_zscore < -1.5  →  ACCUMULATION, else NEUTRAL.
Keeps full FINRA download/cache pipeline intact.

DATA SOURCE:
    FINRA Consolidated Short Volume (CNMS) — daily files
    URL: https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt
    Release: ~8pm ET on the trading day

SIGNAL LOGIC:
    short_ratio = ShortVolume / TotalVolume per FINRA file
    short_ratio_zscore = (today - 20d_mean) / 20d_std
    zscore < -1.5  →  ACCUMULATION  (unusually low shorting vs recent history)
    else           →  NEUTRAL

CACHE STRATEGY:
    Raw FINRA files: data/finra_cache/{YYYYMMDD}.csv — immutable
    Scan results:    data/dark_pool_latest.json       — date-stamped

USAGE:
    python3 dark_pool_flow.py --ticker AAPL
    python3 dark_pool_flow.py --scan --output data/dark_pool_latest.json

OUTPUT (per ticker):
    signal             : ACCUMULATION | NEUTRAL
    short_ratio_zscore : float
    short_ratio_today  : float
    days_of_data       : int

ERROR HANDLING:
    - FINRA CDN 404 → silently fall back up to 3 prior business days
    - <5 days of data → return None
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

# Z-score threshold: below this → ACCUMULATION signal
_ACCUMULATION_ZSCORE = -1.5

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
# SECTION 3: SIGNAL COMPUTATION (simplified)
# ==============================================================================

def compute_dark_pool_signal(
    ticker: str,
    lookback_days: int = 20,
    _preloaded_frames: Dict[str, pd.DataFrame] = None,
) -> Optional[dict]:
    """
    Compute institutional accumulation flag for a single ticker.

    Returns ACCUMULATION if short_ratio_zscore < -1.5 (unusually low shorting
    vs the recent 20-day window), else NEUTRAL.

    Returns None if fewer than 5 days of FINRA data are available.
    """
    ticker = ticker.upper().strip()
    candidates = _prev_business_days(lookback_days + 7)

    records: List[dict] = []
    for day in candidates:
        date_str = day.strftime("%Y%m%d")
        day_df = _preloaded_frames.get(date_str) if _preloaded_frames is not None else _fetch_single_date(day)
        if day_df is None:
            continue
        row = day_df[day_df["symbol"] == ticker]
        if row.empty:
            continue
        records.append({
            "date":        day.strftime("%Y-%m-%d"),
            "short_ratio": float(row["short_ratio"].iloc[0]),
        })

    records = records[-lookback_days:]
    if len(records) < 5:
        return None

    short_ratios = np.array([r["short_ratio"] for r in records], dtype=float)
    sr_mean   = float(short_ratios.mean())
    sr_std    = float(short_ratios.std(ddof=0))
    sr_today  = float(short_ratios[-1])
    sr_zscore = (sr_today - sr_mean) / sr_std if sr_std > 1e-9 else 0.0

    signal = "ACCUMULATION" if sr_zscore < _ACCUMULATION_ZSCORE else "NEUTRAL"

    return {
        "ticker":             ticker,
        "signal":             signal,
        "short_ratio_zscore": round(sr_zscore, 3),
        "short_ratio_today":  round(sr_today,  4),
        "days_of_data":       len(records),
    }


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
    logger.info("dark_pool_flow: %d tickers scanned, %d ACCUMULATION", n, k)
    print(f"  Dark Pool Scan: {n} tickers processed | {k} ACCUMULATION | {n - k} NEUTRAL")

    return sorted(results, key=lambda r: r["short_ratio_zscore"])


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
    # Persist to Supabase for historical record
    try:
        from utils.supabase_persist import save_dark_pool_snapshot
        save_dark_pool_snapshot(results)
    except Exception as exc:
        logger.warning("dark_pool_flow: Supabase persist failed (non-fatal): %s", exc)


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

    Returns
    -------
    dict with keys:
        score  : int   (+2 for ACCUMULATION, 0 for NEUTRAL)
        max    : int   (always 2)
        flags  : list  (human-readable flag strings)
        signal : str   (ACCUMULATION | NEUTRAL)
        detail : dict  (full compute_dark_pool_signal output, or None)
    """
    try:
        result = None
        if result_cache:
            result = result_cache.get(ticker.upper())
        if result is None:
            result = compute_dark_pool_signal(ticker, _preloaded_frames=preloaded_frames)

        if result is None:
            return {"score": 0, "max": 2, "flags": [], "signal": "NEUTRAL", "detail": None}

        sig = result["signal"]
        if sig == "ACCUMULATION":
            score = 2
            flags = [
                f"Dark pool ACCUMULATION: short_ratio_zscore={result['short_ratio_zscore']:+.2f} "
                f"(today={result['short_ratio_today']:.3%})"
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
    sig_icon = {"ACCUMULATION": "↑", "NEUTRAL": "─"}.get(result["signal"], "─")
    print(
        f"\n  {result['ticker']:<8} {sig_icon} {result['signal']:<14}"
        f"  zscore {result['short_ratio_zscore']:+.2f}"
    )
    print(f"    Short ratio today   : {result['short_ratio_today']:.3%}")
    print(f"    Short ratio z-score : {result['short_ratio_zscore']:+.2f}")
    print(f"    Days of data        : {result['days_of_data']}")


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
