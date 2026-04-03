#!/usr/bin/env python3
"""
================================================================================
UNIVERSE BUILDER — Global dynamic equity universe, high-quality ~220 tickers
================================================================================
Builds a quality-filtered watchlist by:
  1.  Pulling 7 iShares ETF holdings CSVs (Russell 1000/2000, S&P 500/400, IEFA,
      IEMG, ACWI) with 24-hour disk cache.
  2.  Injecting a curated list of liquid ADRs not covered by US-only ETF scans.
  3.  Deduplicating + removing dot-tickers (ADRs/preferreds with exchange suffixes).
  4.  Applying a liquidity pre-filter (price, 30-day avg dollar vol, history ≥ 63d).
  5.  Scoring surviving tickers with a 5-factor momentum pre-screen.
  6.  Applying a volatility / beta quality gate (drops ATR%>6 or beta>2).
  7.  Force-including Tier 1 watchlist pins and 3-day top-50 streak tickers.
  8.  Rewriting the UNIVERSE (auto) and PERSISTENT_AUTO_FAVORITES blocks in
      watchlist.txt.

PUBLIC API:
    fetch_index_constituents(index)         -> list[str]
    build_master_universe(indices=None)     -> list[str]
    fast_momentum_prescreen(tickers, top_n) -> list[str]

CLI:
    python3 universe_builder.py --build-cache           # cache all 7 index CSVs
    python3 universe_builder.py --list-universe         # print filtered universe
    python3 universe_builder.py --prescreen --top 200   # run momentum prescreen
    python3 universe_builder.py --update-watchlist      # full pipeline refresh

FALLBACK CHAIN (per index):
    1. Fresh HTTP fetch from iShares (< UNIVERSE_CACHE_TTL_HOURS)
    2. Stale disk cache (up to 7 days on network failure)
    3. Hardcoded _HARDCODED_FALLBACK list
================================================================================
"""

import argparse
import contextlib
import hashlib
import io
import json
import logging
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# ==============================================================================
# CONFIGURABLE CONSTANTS — import from config.py with safe defaults
# ==============================================================================

try:
    from config import (
        UNIVERSE_INDICES,
        UNIVERSE_PRESCREEN_TOP_N,
        UNIVERSE_MIN_DOLLAR_VOLUME,
        UNIVERSE_MIN_PRICE,
        UNIVERSE_CACHE_TTL_HOURS,
    )
except ImportError:
    UNIVERSE_INDICES = [
        "russell1000", "russell2000", "sp500", "sp400",
        "iefa", "iemg", "acwi",
    ]
    UNIVERSE_PRESCREEN_TOP_N    = 200
    UNIVERSE_MIN_DOLLAR_VOLUME  = 3_000_000   # 30-day avg dollar volume ($)
    UNIVERSE_MIN_PRICE          = 2.0          # Minimum share price ($)
    UNIVERSE_CACHE_TTL_HOURS    = 24           # Cache TTL for index constituents

try:
    from config import UNIVERSE_ATR_PCT_MAX, UNIVERSE_BETA_MAX
except ImportError:
    UNIVERSE_ATR_PCT_MAX = 6.0   # drop if 20-day ATR% > 6 % (excessively volatile)
    UNIVERSE_BETA_MAX    = 2.0   # drop if 60-day rolling beta vs SPY > 2.0

# ---------------------------------------------------------------------------
# Exchange-suffix classification — used in dot-ticker filtering
# ---------------------------------------------------------------------------
# Known international exchange suffixes that should PASS the dot-filter.
# Tickers like 2330.TW, NOVO-B.CO, ASML.AS, 7203.T are valid yfinance symbols.
_INTL_EXCHANGE_SUFFIXES: frozenset = frozenset({
    "TW", "TWO",                        # Taiwan
    "HK",                               # Hong Kong
    "L",                                # London
    "CO",                               # Copenhagen
    "T",                                # Tokyo
    "SW",                               # Switzerland
    "DE", "F", "XETRA",                 # Germany
    "PA",                               # Paris
    "AS",                               # Amsterdam
    "MI",                               # Milan
    "MC",                               # Madrid
    "LS",                               # Lisbon
    "BR",                               # Brussels
    "VI",                               # Vienna
    "WA",                               # Warsaw
    "HE",                               # Helsinki
    "OL",                               # Oslo
    "ST",                               # Stockholm
    "SI",                               # Singapore
    "AX",                               # Australia (ASX)
    "NZ",                               # New Zealand
    "TO",                               # Toronto
    "V",                                # TSX Venture
    "KS", "KQ",                         # Korea
    "SS", "SZ",                         # Shanghai / Shenzhen
    "NS", "BO",                         # NSE / BSE India
    "BK",                               # Bangkok
    "JK",                               # Jakarta
    "KL",                               # Kuala Lumpur
    "ME",                               # Moscow
    "SA",                               # São Paulo
    "MX",                               # Mexico
    "IL",                               # Tel Aviv
    "TA",                               # Tel Aviv (alt)
    "AT",                               # Athens
    "JO",                               # Johannesburg
})

# US preferreds / units / warrants — these dot-suffixes are junk for our purposes.
_US_JUNK_SUFFIXES: frozenset = frozenset({
    "PR", "P", "U", "WS", "W", "R", "RT", "CL",
})

# Pre-screen factor weights — must sum to 1.0
WEIGHT_MOM_20D             = 0.35   # 20-day return cross-sectional rank
WEIGHT_VOL_SURGE           = 0.20   # 5-day / 20-day volume ratio (clipped 0–3)
WEIGHT_NEAR_HIGH           = 0.15   # price / 52-week high
WEIGHT_EARNINGS_SURPRISE   = 0.15   # best 1-day return in last 60 days (proxy)
WEIGHT_REL_STRENGTH_SECTOR = 0.15   # 20-day momentum rank within sector peers

# Persistent favourites: tickers in top-50 for this many consecutive days auto-include
TOP50_STREAK_MIN   = 3   # days of consecutive top-50 membership → auto-include
TOP50_HISTORY_DAYS = 5   # rolling history window to maintain

# Curated liquid ADRs to inject before the ETF constituent scan.
# These are frequently absent from US-only index ETFs.
LIQUID_ADRS: list = [
    "TSM", "BABA", "PDD", "NTES", "JD", "BIDU",
    "LI",  "NIO",  "XPEV", "SE",  "MELI", "CPNG",
    "ASML", "SAP", "NVO",  "TM",  "SONY", "HMC",
    "UL",  "BP",   "VALE", "ITUB",
]

# ==============================================================================
# iShares ETF holdings CSV endpoints
# ==============================================================================

_INDEX_URLS: dict = {
    # ── US large / mid / small-cap ────────────────────────────────────────────
    "russell1000": (
        "https://www.ishares.com/us/products/239707/ISHARES-RUSSELL-1000-ETF"
        "/1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund"
    ),
    "russell2000": (
        "https://www.ishares.com/us/products/239710/ISHARES-RUSSELL-2000-ETF"
        "/1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund"
    ),
    "sp500": (
        "https://www.ishares.com/us/products/239726/ISHARES-CORE-SP-500-ETF"
        "/1467271812596.ajax?fileType=csv&fileName=IVV_holdings&dataType=fund"
    ),
    "sp400": (
        "https://www.ishares.com/us/products/239763/ISHARES-SP-MIDCAP-400-ETF"
        "/1467271812596.ajax?fileType=csv&fileName=IJH_holdings&dataType=fund"
    ),
    # ── International / global ────────────────────────────────────────────────
    "iefa": (
        "https://www.ishares.com/us/products/244049/ishares-core-msci-eafe-etf"
        "/1467271812596.ajax?fileType=csv&fileName=IEFA_holdings&dataType=fund"
    ),
    "iemg": (
        "https://www.ishares.com/us/products/244050/ishares-core-msci-emerging-markets-etf"
        "/1467271812596.ajax?fileType=csv&fileName=IEMG_holdings&dataType=fund"
    ),
    "acwi": (
        "https://www.ishares.com/us/products/239600/ishares-msci-acwi-etf"
        "/1467271812596.ajax?fileType=csv&fileName=ACWI_holdings&dataType=fund"
    ),
}

# ==============================================================================
# Hardcoded fallback (used when network + cache both fail)
# ==============================================================================

_HARDCODED_FALLBACK: list = [
    # Meme / retail
    "GME", "AMC", "BB", "CLOV", "SOFI", "PLTR", "RIVN", "LCID", "NIO",
    "MARA", "RIOT", "COIN", "HOOD", "DKNG", "SKLZ",
    # Biotech
    "MRNA", "BNTX", "NVAX", "SAVA", "ATOS",
    # Tech growth
    "SNOW", "NET", "CRWD", "DDOG", "ZS", "BILL", "HUBS", "CFLT",
    "PATH", "U", "RBLX", "AFRM", "UPST", "IONQ", "RGTI", "QUBT",
    # AI / Semi
    "SMCI", "ARM", "MRVL", "ON", "SOUN", "BBAI", "AI",
    # Space / EV
    "JOBY", "LILM", "LUNR", "RKLB", "ASTS",
    # Large-cap
    "NVDA", "TSLA", "AMD", "META", "NFLX", "GOOGL", "AMZN", "AAPL",
    "MSFT", "CRM", "SHOP", "SQ", "ROKU", "SNAP", "PINS", "ABNB",
]

# ==============================================================================
# Paths
# ==============================================================================

_BASE_DIR        = Path(__file__).parent
_CACHE_DIR       = _BASE_DIR / "data" / "universe_cache"
_WATCHLIST_PATH  = _BASE_DIR / "watchlist.txt"
_TOP50_HIST_PATH = _BASE_DIR / "data" / "top50_history.json"

# Module-level sector map populated during iShares CSV parsing.
# { ticker: sector_string }  — used by _compute_prescreen_scores for rel-strength.
_SECTOR_MAP: dict = {}

# Module-level quality metrics populated by _compute_prescreen_scores.
# { ticker: {"atr_pct": float, "beta": float} }
_QUALITY_CACHE: dict = {}


# ==============================================================================
# Internal helpers — HTTP / disk caching
# ==============================================================================

def _fetch_with_retry(
    url: str,
    attempts: int = 3,
    backoff: float = 5.0,
    timeout: int = 30,
) -> Optional[requests.Response]:
    """GET *url* with up to *attempts* retries and exponential-ish backoff."""
    headers = {"User-Agent": "Mozilla/5.0 (signal_engine/1.0)"}
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(url, timeout=timeout, headers=headers)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            if attempt < attempts:
                logger.warning(
                    "Fetch attempt %d/%d failed for %s: %s — retrying in %.0fs",
                    attempt, attempts, url, exc, backoff,
                )
                time.sleep(backoff)
            else:
                logger.error("All %d fetch attempts failed for %s: %s", attempts, url, exc)
    return None


def _cache_path(index: str) -> Path:
    return _CACHE_DIR / f"{index}_constituents.json"


def _load_cache(index: str, max_age_hours: float) -> Optional[list]:
    """Return cached ticker list if it exists and is younger than *max_age_hours*."""
    path = _cache_path(index)
    if not path.exists():
        return None
    try:
        with open(path) as fh:
            data = json.load(fh)
        cached_at = datetime.fromisoformat(data["cached_at"])
        age_h = (datetime.now() - cached_at).total_seconds() / 3600
        if age_h > max_age_hours:
            logger.debug("Cache for %s is %.1fh old (TTL=%.0fh) — stale", index, age_h, max_age_hours)
            return None
        # Restore sector map from cache if available
        if "sector_map" in data:
            _SECTOR_MAP.update(data["sector_map"])
        return data["tickers"]
    except Exception as exc:
        logger.warning("Failed to read cache for %s: %s", index, exc)
        return None


def _save_cache(index: str, tickers: list, sector_map: dict = None) -> None:
    """Write ticker list (and optional sector map) to JSON cache."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(index)
    try:
        payload: dict = {
            "cached_at": datetime.now().isoformat(),
            "tickers": tickers,
        }
        if sector_map:
            payload["sector_map"] = sector_map
        with open(path, "w") as fh:
            json.dump(payload, fh)
        logger.debug("Cached %d tickers for %s → %s", len(tickers), index, path)
    except Exception as exc:
        logger.warning("Failed to write cache for %s: %s", index, exc)


_LIQUIDITY_CACHE_PATH = _CACHE_DIR / "liquidity_passed.json"


def _load_liquidity_cache(ticker_hash: str, max_age_hours: float = 24.0) -> Optional[list]:
    """Return cached liquidity-passed list if hash matches and cache is fresh."""
    if not _LIQUIDITY_CACHE_PATH.exists():
        return None
    try:
        with open(_LIQUIDITY_CACHE_PATH) as fh:
            data = json.load(fh)
        if data.get("ticker_hash") != ticker_hash:
            return None
        age_h = (datetime.now() - datetime.fromisoformat(data["cached_at"])).total_seconds() / 3600
        if age_h > max_age_hours:
            return None
        logger.info("Liquidity cache hit (%.1fh old, %d passed)", age_h, len(data["passed"]))
        return data["passed"]
    except Exception as exc:
        logger.warning("Failed to read liquidity cache: %s", exc)
        return None


def _save_liquidity_cache(ticker_hash: str, passed: list) -> None:
    """Write liquidity-passed list to disk cache."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(_LIQUIDITY_CACHE_PATH, "w") as fh:
            json.dump({"cached_at": datetime.now().isoformat(), "ticker_hash": ticker_hash, "passed": passed}, fh)
    except Exception as exc:
        logger.warning("Failed to write liquidity cache: %s", exc)


def _parse_ishares_csv(text: str) -> tuple:
    """
    Parse an iShares ETF holdings CSV.

    Format: 9 metadata rows, then header row containing a 'Ticker' column
    (and optionally a 'Sector' column), then one holding per row.

    Returns:
        (tickers: list[str], sector_map: dict[str, str])

    Filters out: cash rows, '-' placeholders, blank tickers, len > 6, dot-tickers.
    """
    try:
        df = pd.read_csv(io.StringIO(text), skiprows=9, header=0, on_bad_lines="skip")
    except Exception as exc:
        logger.error("CSV parse error: %s", exc)
        return [], {}

    ticker_col = next(
        (c for c in df.columns if str(c).strip().lower() == "ticker"), None
    )
    if ticker_col is None:
        logger.error("No 'Ticker' column found. Columns: %s", list(df.columns))
        return [], {}

    sector_col = next(
        (c for c in df.columns if str(c).strip().lower() == "sector"), None
    )

    tickers: list = []
    sector_map: dict = {}

    for _, row in df.iterrows():
        raw = str(row.get(ticker_col, "")).strip()
        # Skip blanks, cash rows, placeholders, and unusually long garbage
        if not raw or raw == "-" or raw.upper().startswith("CASH") or len(raw) > 15:
            continue
        # Dot-ticker filtering: keep international exchange suffixes, drop US junk
        if "." in raw:
            suffix = raw.rsplit(".", 1)[-1].upper()
            if suffix in _US_JUNK_SUFFIXES and suffix not in _INTL_EXCHANGE_SUFFIXES:
                continue
            # All other dot-tickers (international symbols) are kept and passed
            # through to the liquidity filter for yfinance to validate.
        tickers.append(raw)
        if sector_col:
            sec = str(row.get(sector_col, "")).strip()
            if sec and sec.lower() not in ("nan", "-", ""):
                sector_map[raw] = sec

    return tickers, sector_map


# ==============================================================================
# Internal helpers — yfinance batch OHLCV download
# ==============================================================================

def _batch_ohlcv(
    tickers: list,
    period: str,
    batch_size: int = 100,
) -> tuple:
    """
    Download OHLCV data for *tickers* in batches of *batch_size*.

    Returns:
        (close_dict, volume_dict, high_dict, low_dict)
        Each dict: { ticker -> pd.Series (NaN rows dropped) }
    """
    all_close: dict  = {}
    all_volume: dict = {}
    all_high: dict   = {}
    all_low: dict    = {}

    _yf_log = logging.getLogger("yfinance")
    _prev_level = _yf_log.level
    _yf_log.setLevel(logging.CRITICAL)

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                df = yf.download(
                    batch,
                    period=period,
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )
            if df.empty:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                close_df  = df["Close"]
                vol_df    = df["Volume"]
                high_df   = df["High"]
                low_df    = df["Low"]
            else:
                # Single-ticker fallback — wrap as one-column DataFrame
                t = batch[0]
                close_df = df[["Close"]].rename(columns={"Close": t})
                vol_df   = df[["Volume"]].rename(columns={"Volume": t})
                high_df  = df[["High"]].rename(columns={"High": t})
                low_df   = df[["Low"]].rename(columns={"Low": t})

            for t in batch:
                if t in close_df.columns:
                    c = close_df[t].dropna()
                    if not c.empty:
                        all_close[t]  = c
                        all_volume[t] = vol_df[t].dropna()   if t in vol_df.columns  else pd.Series(dtype=float)
                        all_high[t]   = high_df[t].dropna()  if t in high_df.columns else c
                        all_low[t]    = low_df[t].dropna()   if t in low_df.columns  else c
        except Exception as exc:
            logger.warning("Batch OHLCV failed (batch[0]=%s): %s", batch[0], exc)

    _yf_log.setLevel(_prev_level)
    return all_close, all_volume, all_high, all_low


def _batch_close_volume(
    tickers: list,
    period: str,
    batch_size: int = 100,
) -> tuple:
    """Return (close_dict, volume_dict) — thin wrapper around _batch_ohlcv."""
    c, v, _, _ = _batch_ohlcv(tickers, period=period, batch_size=batch_size)
    return c, v


# ==============================================================================
# Liquidity filter
# ==============================================================================

def _apply_liquidity_filter(tickers: list, batch_size: int = 250) -> list:
    """
    Keep tickers that meet all three criteria:
      - last price               > UNIVERSE_MIN_PRICE          (default $1.50)
      - 30-day avg dollar volume > UNIVERSE_MIN_DOLLAR_VOLUME   (default $3 M)
      - valid bars (Close & Vol) ≥ 20  (≈ 1 month of trading days)

    Downloads in chunks of *batch_size* (default 250) for yfinance efficiency.
    Strong NaN resilience: uses a joint valid-row mask so partial data from
    international names (many gaps, late IPOs) still scores correctly.
    Writes failed tickers to liquidity_failed.log for debugging.
    Results are cached to disk for 24 hours keyed by the input ticker set.
    """
    _MIN_BARS = 20

    ticker_hash = hashlib.md5(",".join(sorted(tickers)).encode()).hexdigest()
    cached = _load_liquidity_cache(ticker_hash)
    if cached is not None:
        print(f"  Liquidity filter: cache hit — {len(cached)} passed (skipping downloads)")
        return cached

    passed:   list = []
    fail_price: int = 0
    fail_vol:   int = 0
    fail_bars:  int = 0
    failed_tickers: list = []

    _yf_log = logging.getLogger("yfinance")
    _prev_level = _yf_log.level
    _yf_log.setLevel(logging.CRITICAL)

    for i in range(0, len(tickers), batch_size):
        chunk = tickers[i : i + batch_size]
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                data = yf.download(
                    chunk,
                    period="3mo",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                )

            if data is None or data.empty:
                failed_tickers.extend(chunk)
                continue

            # --- Normalise to (field → DataFrame[tickers]) regardless of yfinance version
            if isinstance(data.columns, pd.MultiIndex):
                lvl0 = data.columns.get_level_values(0).unique().tolist()
                close_df  = data["Close"]  if "Close"  in lvl0 else pd.DataFrame()
                volume_df = data["Volume"] if "Volume" in lvl0 else pd.DataFrame()
            else:
                # Single ticker returned as flat DataFrame
                t0 = chunk[0]
                close_df  = (data[["Close"]].rename(columns={"Close":  t0})
                             if "Close"  in data.columns else pd.DataFrame())
                volume_df = (data[["Volume"]].rename(columns={"Volume": t0})
                             if "Volume" in data.columns else pd.DataFrame())

            if close_df.empty or volume_df.empty:
                failed_tickers.extend(chunk)
                continue

            for t in chunk:
                # yfinance may upper-case the symbol in column names
                col = t if t in close_df.columns else (
                      t.upper() if t.upper() in close_df.columns else None)
                if col is None:
                    failed_tickers.append(t)
                    continue

                c_ser = close_df[col]
                v_col = col if col in volume_df.columns else None
                v_ser = volume_df[v_col] if v_col else pd.Series(
                    dtype=float, index=c_ser.index)

                # Joint valid mask: both Close and Volume must be non-NaN
                valid = c_ser.notna() & v_ser.notna()
                bars  = int(valid.sum())

                if bars < _MIN_BARS:
                    fail_bars += 1
                    failed_tickers.append(t)
                    continue

                # Use last valid close price
                price = float(c_ser[valid].iloc[-1])
                if price < UNIVERSE_MIN_PRICE:
                    fail_price += 1
                    failed_tickers.append(t)
                    continue

                # 30-day rolling avg dollar volume (min_periods=15 for sparse data)
                dv_rolling = (c_ser * v_ser).rolling(30, min_periods=15).mean()
                dv_clean   = dv_rolling.dropna()
                avg_dv = float(dv_clean.iloc[-1]) if not dv_clean.empty else float("nan")

                if pd.isna(avg_dv) or avg_dv < UNIVERSE_MIN_DOLLAR_VOLUME:
                    fail_vol += 1
                    failed_tickers.append(t)
                    continue

                passed.append(t)

        except Exception as exc:
            logger.warning(
                "Liquidity filter batch failed (chunk[0]=%s, size=%d): %s",
                chunk[0], len(chunk), exc,
            )
            failed_tickers.extend(chunk)

    _yf_log.setLevel(_prev_level)

    total = len(tickers)
    logger.info(
        "Liquidity filter: %d candidates → %d passed "
        "(price<$%.2f: -%d, vol<$%dM: -%d, <%d bars: -%d)",
        total, len(passed),
        UNIVERSE_MIN_PRICE, fail_price,
        UNIVERSE_MIN_DOLLAR_VOLUME // 1_000_000, fail_vol,
        _MIN_BARS, fail_bars,
    )
    print(
        f"  Liquidity filter: {total} candidates → {len(passed)} passed "
        f"(price<${UNIVERSE_MIN_PRICE}: -{fail_price}, "
        f"vol<${UNIVERSE_MIN_DOLLAR_VOLUME//1_000_000}M: -{fail_vol}, "
        f"<{_MIN_BARS} bars: -{fail_bars})"
    )

    # Write failed tickers to disk for post-run inspection
    _failed_log = _BASE_DIR / "liquidity_failed.log"
    try:
        with open(_failed_log, "w") as fh:
            fh.write(f"# liquidity_filter run: {datetime.now().isoformat()}\n")
            fh.write(f"# {total} candidates → {len(passed)} passed\n")
            for ft in sorted(set(failed_tickers)):
                fh.write(ft + "\n")
    except Exception:
        pass

    _save_liquidity_cache(ticker_hash, passed)
    return passed


# ==============================================================================
# Watchlist helpers
# ==============================================================================

def _get_tier1_watchlist(watchlist_path: Path = None) -> list:
    """
    Return tickers from the # TIER 1 section of watchlist.txt.

    Parsing rules:
      - Enter TIER 1 section on any line matching `# ... TIER 1 ...`
      - Exit on `# TIER 2`, `# ──`, `# ==`, or end of file
      - Skip blank lines and comment lines within the section
      - Ticker is the first whitespace-delimited token on each data line

    Returns [] if file is missing or section is empty.
    """
    path = watchlist_path or _WATCHLIST_PATH
    if not path.exists():
        return []
    try:
        tickers: list = []
        in_tier1 = False
        with open(path) as fh:
            for line in fh:
                stripped = line.strip()
                # Enter on TIER 1 header
                if stripped.startswith("#") and "TIER 1" in stripped.upper():
                    in_tier1 = True
                    continue
                if in_tier1:
                    # Stop at next named section
                    if stripped.startswith("#") and any(
                        kw in stripped.upper()
                        for kw in ("TIER 2", "TIER 3", "UNIVERSE", "──", "==", "PERSISTENT")
                    ):
                        break
                    if not stripped or stripped.startswith("#"):
                        continue
                    ticker = stripped.split()[0].strip()
                    if ticker:
                        tickers.append(ticker.upper())
        return tickers
    except Exception as exc:
        logger.warning("Failed to parse watchlist.txt for Tier 1: %s", exc)
        return []


# Backward-compat alias (used in _write_watchlist_from_universe + old callers)
def _get_favorites(watchlist_path: Path = None) -> list:
    return _get_tier1_watchlist(watchlist_path)


# ==============================================================================
# Persistent favourites — top-50 streak tracking
# ==============================================================================

def _load_top50_history() -> list:
    """
    Load top50_history.json.

    Returns list of {"date": "YYYY-MM-DD", "tickers": [...]} dicts,
    sorted by date descending.  Returns [] on missing / corrupt file.
    """
    if not _TOP50_HIST_PATH.exists():
        return []
    try:
        with open(_TOP50_HIST_PATH) as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Failed to load top50 history: %s", exc)
        return []


def _save_top50_history(top50: list) -> None:
    """
    Append today's top-50 to history, prune to TOP50_HISTORY_DAYS entries.
    Silently ignores I/O errors so a read-only FS never kills the pipeline.
    """
    try:
        history = _load_top50_history()
        today = date.today().isoformat()
        history = [h for h in history if h.get("date") != today]
        history.append({"date": today, "tickers": list(top50[:50])})
        history = sorted(history, key=lambda h: h["date"], reverse=True)[:TOP50_HISTORY_DAYS]
        _TOP50_HIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_TOP50_HIST_PATH, "w") as fh:
            json.dump(history, fh, indent=2)
    except Exception as exc:
        logger.warning("Failed to save top50 history: %s", exc)


def _get_persistent_favorites() -> list:
    """
    Return tickers that have appeared in the top-50 for TOP50_STREAK_MIN or more
    consecutive most-recent days (intersection of the last N day sets).

    Returns [] if insufficient history exists.
    """
    history = _load_top50_history()
    if len(history) < TOP50_STREAK_MIN:
        return []

    recent = sorted(history, key=lambda h: h["date"], reverse=True)[:TOP50_STREAK_MIN]
    sets = [set(h["tickers"]) for h in recent]
    streak = set.intersection(*sets) if sets else set()
    return sorted(streak)


# ==============================================================================
# Pre-screen scoring — 5-factor composite
# ==============================================================================

def _compute_prescreen_scores(tickers: list, batch_size: int = 100) -> dict:
    """
    5-factor momentum pre-screen. Returns { ticker: score ∈ [0, 1] }.

    Factors — all cross-sectional percentile ranks 0-1:
      0.35  mom_20d_rank            — 20-day total return
      0.20  vol_surge               — 5d/20d volume ratio (clipped 0-3, /3)
      0.15  near_high               — price / 52-week high
      0.15  earnings_surprise_rank  — best 1-day return in last 60d (proxy)
      0.15  rel_strength_vs_sector  — 20-day rank within sector peer group

    Side-effect: populates module-level _QUALITY_CACHE with
    { ticker: {"atr_pct": float, "beta": float} } for the quality gate.
    """
    global _QUALITY_CACHE  # noqa: PLW0603

    # One batch download: 1 year of OHLCV + SPY for beta calculation
    download_list = list(dict.fromkeys(list(tickers) + ["SPY"]))
    all_close, all_volume, all_high, all_low = _batch_ohlcv(
        download_list, period="1y", batch_size=batch_size
    )

    spy_close = all_close.get("SPY", pd.Series(dtype=float))
    spy_ret   = spy_close.pct_change().dropna() if not spy_close.empty else pd.Series(dtype=float)

    returns_20d:  dict = {}
    vol_ratios:   dict = {}
    near_highs:   dict = {}
    earn_surprise: dict = {}
    quality:      dict = {}

    for t in tickers:
        c  = all_close.get(t)
        v  = all_volume.get(t)
        h  = all_high.get(t)
        lo = all_low.get(t)

        if c is None or len(c) < 22:
            continue

        # ── mom_20d ────────────────────────────────────────────────────────────
        returns_20d[t] = float(c.iloc[-1] / c.iloc[-21] - 1)

        # ── vol_surge ──────────────────────────────────────────────────────────
        if v is not None and len(v) >= 20:
            v5, v20 = float(v.iloc[-5:].mean()), float(v.iloc[-20:].mean())
            vol_ratios[t] = v5 / v20 if v20 > 0 else 1.0
        else:
            vol_ratios[t] = 1.0

        # ── near_high (52-week) ────────────────────────────────────────────────
        window = min(252, len(c))
        high_52 = float(c.iloc[-window:].max())
        near_highs[t] = float(c.iloc[-1]) / high_52 if high_52 > 0 else 0.5

        # ── earnings_surprise proxy ────────────────────────────────────────────
        # Best single-day return in last 60 trading days.
        # Captures post-earnings gaps without needing earnings dates in batch.
        if len(c) >= 60:
            daily_rets = c.iloc[-60:].pct_change().dropna()
            earn_surprise[t] = float(daily_rets.max()) if not daily_rets.empty else 0.0
        else:
            earn_surprise[t] = 0.0

        # ── Quality gate metrics ───────────────────────────────────────────────
        # ATR% (20-day)
        try:
            if h is not None and lo is not None and len(h) >= 21 and len(lo) >= 21:
                h20  = h.iloc[-20:]
                lo20 = lo.iloc[-20:]
                cp   = c.shift(1).iloc[-20:]
                tr   = pd.concat([
                    h20 - lo20,
                    (h20 - cp).abs(),
                    (lo20 - cp).abs(),
                ], axis=1).max(axis=1)
                atr     = float(tr.mean())
                last_px = float(c.iloc[-1])
                atr_pct = atr / last_px * 100.0 if last_px > 0 else 99.0
            else:
                atr_pct = 99.0
        except Exception:
            atr_pct = 99.0

        # Beta vs SPY (60-day rolling)
        try:
            if not spy_ret.empty and len(c) >= 62:
                t_ret  = c.pct_change().dropna()
                aligned = pd.concat([t_ret, spy_ret], axis=1, join="inner").dropna()
                aligned = aligned.iloc[-60:]
                if len(aligned) >= 20:
                    cov  = float(aligned.iloc[:, 0].cov(aligned.iloc[:, 1]))
                    var  = float(aligned.iloc[:, 1].var())
                    beta = cov / var if var > 0 else 1.0
                else:
                    beta = 1.0
            else:
                beta = 1.0
        except Exception:
            beta = 1.0

        quality[t] = {"atr_pct": round(atr_pct, 2), "beta": round(beta, 4)}

    _QUALITY_CACHE = quality

    if not returns_20d:
        return {}

    ticker_list = list(returns_20d.keys())
    n = len(ticker_list)

    # ── Cross-sectional percentile ranks ──────────────────────────────────────
    rets_arr  = np.array([returns_20d[t]          for t in ticker_list])
    earn_arr  = np.array([earn_surprise.get(t, 0.) for t in ticker_list])
    mom_ranks  = pd.Series(rets_arr).rank(pct=True).to_numpy()
    earn_ranks = pd.Series(earn_arr).rank(pct=True).to_numpy()

    # ── Sector-relative momentum rank ─────────────────────────────────────────
    sector_groups: dict = {}
    for t in ticker_list:
        sector = _SECTOR_MAP.get(t, "Unknown")
        sector_groups.setdefault(sector, []).append(t)

    sector_rank_map: dict = {}
    for sector, members in sector_groups.items():
        member_rets = [returns_20d.get(m, 0.) for m in members]
        if len(members) >= 3:
            ranks_arr = pd.Series(member_rets).rank(pct=True).to_numpy()
        else:
            ranks_arr = np.full(len(members), 0.5)  # too few peers → neutral
        for m, r in zip(members, ranks_arr):
            sector_rank_map[m] = float(r)

    # ── Composite score ───────────────────────────────────────────────────────
    scores: dict = {}
    for i, t in enumerate(ticker_list):
        mom_r    = float(mom_ranks[i])
        vol_s    = min(float(vol_ratios.get(t, 1.0)), 3.0) / 3.0
        near_h   = float(np.clip(near_highs.get(t, 0.5), 0.0, 1.0))
        earn_r   = float(earn_ranks[i])
        sector_r = sector_rank_map.get(t, 0.5)

        scores[t] = round(
            WEIGHT_MOM_20D              * mom_r
            + WEIGHT_VOL_SURGE          * vol_s
            + WEIGHT_NEAR_HIGH          * near_h
            + WEIGHT_EARNINGS_SURPRISE  * earn_r
            + WEIGHT_REL_STRENGTH_SECTOR * sector_r,
            6,
        )

    logger.debug(
        "Pre-screen scored %d/%d tickers (dropped %d with < 22 bars)",
        len(scores), len(tickers), len(tickers) - len(scores),
    )
    return scores


# ==============================================================================
# Volatility / beta quality gate
# ==============================================================================

def _apply_quality_gate(candidates: set, protected: set) -> set:
    """
    Return the subset of *candidates* that FAIL the quality gate:
      - ATR% > UNIVERSE_ATR_PCT_MAX  (default 6 %)
      - beta > UNIVERSE_BETA_MAX     (default 2.0)

    *protected* tickers (Tier 1 pins + persistent favourites) are never dropped.
    If a ticker has no entry in _QUALITY_CACHE it passes through (benefit of doubt).
    """
    dropped: set = set()
    for t in candidates:
        if t in protected:
            continue
        q = _QUALITY_CACHE.get(t)
        if q is None:
            continue
        atr_pct = q.get("atr_pct", 0.0)
        beta    = q.get("beta",    1.0)
        if atr_pct > UNIVERSE_ATR_PCT_MAX:
            logger.debug("Quality gate: dropping %s (ATR%% %.1f > %.1f)", t, atr_pct, UNIVERSE_ATR_PCT_MAX)
            dropped.add(t)
        elif beta > UNIVERSE_BETA_MAX:
            logger.debug("Quality gate: dropping %s (beta %.2f > %.2f)", t, beta, UNIVERSE_BETA_MAX)
            dropped.add(t)
    return dropped


# ==============================================================================
# Public API — constituent fetching
# ==============================================================================

def fetch_index_constituents(index: str) -> list:
    """
    Return constituent tickers for a named index.

    Valid index names:
        'russell1000', 'russell2000', 'sp500', 'sp400',
        'iefa', 'iemg', 'acwi'

    Fallback chain:
        1. Fresh cache (< UNIVERSE_CACHE_TTL_HOURS)
        2. HTTP fetch from iShares (+ write cache)
        3. Stale cache (up to 7 days)
        4. _HARDCODED_FALLBACK

    Never raises — always returns a non-empty list.
    """
    if index not in _INDEX_URLS:
        raise ValueError(
            f"Unknown index: {index!r}. Valid options: {sorted(_INDEX_URLS)}"
        )

    # 1. Fresh cache
    cached = _load_cache(index, max_age_hours=UNIVERSE_CACHE_TTL_HOURS)
    if cached is not None:
        logger.info("Using fresh cache for %s (%d tickers)", index, len(cached))
        return cached

    # 2. HTTP fetch
    url = _INDEX_URLS[index]
    resp = _fetch_with_retry(url)
    if resp is not None:
        tickers, sector_map = _parse_ishares_csv(resp.text)
        if tickers:
            _SECTOR_MAP.update(sector_map)
            _save_cache(index, tickers, sector_map)
            logger.info("Fetched %d constituents for %s from iShares", len(tickers), index)
            return tickers
        logger.warning("iShares CSV for %s parsed 0 tickers — check CSV format", index)

    # 3. Stale cache (up to 7 days)
    stale = _load_cache(index, max_age_hours=7 * 24)
    if stale:
        logger.warning(
            "[WARN] Network failed for %s — using stale cache (%d tickers)", index, len(stale)
        )
        print(f"  [WARN] Network failed for {index} — using stale cache ({len(stale)} tickers)")
        return stale

    # 4. Hardcoded fallback
    logger.warning("[WARN] No usable cache for %s — falling back to hardcoded universe", index)
    print(f"  [WARN] No cache for {index} — falling back to hardcoded universe")
    return list(_HARDCODED_FALLBACK)


# ==============================================================================
# Public API — universe construction
# ==============================================================================

def build_master_universe(indices: list = None) -> list:
    """
    Combine multiple index constituent lists, inject liquid ADRs, deduplicate,
    remove dot-tickers, and apply the liquidity/history filter.

    Returns a deduplicated, liquidity-filtered list of ticker strings.
    """
    if indices is None:
        indices = UNIVERSE_INDICES

    # Seed with curated ADRs, then expand with index constituents
    raw: list = list(LIQUID_ADRS)
    for idx in indices:
        constituents = fetch_index_constituents(idx)
        logger.info("  %s: %d constituents", idx, len(constituents))
        raw.extend(constituents)

    # Deduplicate preserving order.
    # For dot-tickers: keep international exchange suffixes, drop US junk.
    seen: set = set()
    deduped: list = []
    for t in raw:
        t_clean = str(t).strip()
        if not t_clean or t_clean in seen:
            continue
        if "." in t_clean:
            suffix = t_clean.rsplit(".", 1)[-1].upper()
            if suffix in _US_JUNK_SUFFIXES and suffix not in _INTL_EXCHANGE_SUFFIXES:
                continue   # drop US preferreds / units / warrants
            # International exchange suffix or ambiguous — keep, let yfinance decide
        seen.add(t_clean)
        deduped.append(t_clean)

    logger.info("Universe: raw=%d  after dedup+dot-filter=%d", len(raw), len(deduped))
    print(f"  Universe: {len(raw)} raw → {len(deduped)} after dedup / smart dot-filter")

    t0 = time.time()
    passed = _apply_liquidity_filter(deduped)
    elapsed = time.time() - t0
    logger.info("Liquidity filter: %d → %d in %.1fs", len(deduped), len(passed), elapsed)
    print(f"  Liquidity filter: {len(deduped)} → {len(passed)} in {elapsed:.1f}s")
    return passed


# ==============================================================================
# Public API — momentum pre-screen
# ==============================================================================

def fast_momentum_prescreen(tickers: list, top_n: int = None) -> list:
    """
    Narrow *tickers* to top *top_n* quality-filtered momentum candidates.

    Pipeline:
      1. Score all tickers with 5-factor composite (also populates _QUALITY_CACHE).
      2. Take top top_n by score.
      3. Drop high-volatility / high-beta tickers (ATR%>6 or beta>2) unless protected.
      4. Force-include Tier 1 watchlist tickers.
      5. Force-include persistent favourites (3-day top-50 streak).
      6. Save today's top-50 to top50_history.json for streak tracking.

    Returns tickers sorted by score descending;
    protected tickers not in the scored set appear at the end.
    """
    if top_n is None:
        top_n = UNIVERSE_PRESCREEN_TOP_N

    tier1      = {t.upper() for t in _get_tier1_watchlist()}
    persistent = {t.upper() for t in _get_persistent_favorites()}
    protected  = tier1 | persistent

    t0 = time.time()
    scores = _compute_prescreen_scores(tickers)

    # Save top-50 for persistence tracking (before quality gate)
    sorted_by_score = sorted(scores, key=lambda t: scores[t], reverse=True)
    _save_top50_history(sorted_by_score[:50])

    top_set = set(sorted_by_score[:top_n])

    # Quality gate — drop volatile tickers unless they're protected
    to_drop = _apply_quality_gate(top_set, protected)
    if to_drop:
        logger.info("Quality gate dropped %d tickers: %s", len(to_drop), sorted(to_drop))
        print(f"  Quality gate: dropped {len(to_drop)} high-vol/beta ticker(s)")
    top_set -= to_drop

    # Force-inject protected tickers that may not have made the cut
    top_set |= protected

    # Sort final list by score; unscored protected tickers go to end
    result = sorted(top_set, key=lambda t: scores.get(t, -1.0), reverse=True)

    elapsed = time.time() - t0
    msg = f"Pre-screen: {len(tickers)} → {len(result)} in {elapsed:.1f}s"
    print(f"  {msg}")
    logger.info(msg)
    return result


# ==============================================================================
# Watchlist update
# ==============================================================================

def _write_watchlist_from_universe(indices: list = None, top_n: int = None) -> None:
    """
    Full pipeline: build universe → 5-factor prescreen → rewrite watchlist.txt.

    Preserved sections:     FAVORITES header, TIER 1, TIER 2 (and any others
                            before the first ── auto block).
    Written/replaced:       # PERSISTENT_AUTO_FAVORITES block (if any streak tickers)
                            # UNIVERSE (auto) block

    Run via: python3 universe_builder.py --update-watchlist [--top 300]
    """
    if top_n is None:
        top_n = UNIVERSE_PRESCREEN_TOP_N

    print("\n  Building master universe (liquidity filter)...")
    universe = build_master_universe(indices)
    print(f"  Liquidity-filtered universe: {len(universe)} tickers")

    print(f"  Running 5-factor momentum prescreen → top {top_n}...")
    top_tickers = fast_momentum_prescreen(universe, top_n=top_n)

    tier1_set      = {t.upper() for t in _get_tier1_watchlist()}
    persistent     = _get_persistent_favorites()
    persistent_set = {t.upper() for t in persistent}
    protected_set  = tier1_set | persistent_set

    # Auto block excludes all protected tickers (they appear in their own sections)
    auto_tickers = [t for t in top_tickers if t.upper() not in protected_set]

    # Read existing watchlist verbatim
    watchlist_path = _WATCHLIST_PATH
    existing_lines: list = []
    if watchlist_path.exists():
        existing_lines = watchlist_path.read_text().splitlines()

    # Strip the old auto + persistent blocks, keep everything before them
    filtered_lines: list = []
    skip = False
    for line in existing_lines:
        stripped = line.strip()
        if stripped.startswith("# ── UNIVERSE (auto)") or stripped.startswith("# ── PERSISTENT"):
            skip = True
            continue
        if skip and (stripped.startswith("# ──") or stripped.startswith("# ==")):
            skip = False
        if not skip:
            filtered_lines.append(line)

    while filtered_lines and not filtered_lines[-1].strip():
        filtered_lines.pop()

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Persistent auto-favourites block (only if any exist outside Tier 1)
    new_persistents = [t for t in sorted(persistent) if t not in tier1_set]
    if new_persistents:
        filtered_lines.append("")
        filtered_lines.append(
            f"# ── PERSISTENT_AUTO_FAVORITES — {len(new_persistents)} tickers "
            f"({TOP50_STREAK_MIN}+ day top-50 streak)  [{stamp}] ──"
        )
        for t in new_persistents:
            filtered_lines.append(t)

    # Universe (auto) block
    filtered_lines.append("")
    filtered_lines.append(
        f"# ── UNIVERSE (auto) — top {len(auto_tickers)} by momentum prescreen  [{stamp}] ──"
    )
    for t in auto_tickers:
        filtered_lines.append(t)
    filtered_lines.append("")

    watchlist_path.write_text("\n".join(filtered_lines) + "\n")
    total_written = len(tier1_set) + len(new_persistents) + len(auto_tickers)
    print(
        f"  Watchlist updated: {len(tier1_set)} Tier 1 + {len(new_persistents)} persistent "
        f"+ {len(auto_tickers)} dynamic  →  watchlist.txt"
    )
    print(
        f"\nINFO: Final universe: {len(auto_tickers)} dynamic + "
        f"{len(new_persistents)} favorites + {len(tier1_set)} persistent = {total_written} total"
    )

    # === NEW: UNIVERSE RANK EXPORT FOR AI QUANT ===
    ranked_universe: dict = {}
    for rank, ticker in enumerate(top_tickers, start=1):
        t = ticker.upper()
        if t in persistent_set:
            status = "Persistent favorite"
        elif t in tier1_set:
            status = "Tier-1"
        else:
            status = "Dynamic only"
        ranked_universe[t] = {
            "rank": rank,
            "total": len(top_tickers),
            "status": status,
            "factors": "mom/vol-surge/near-high/earnings/sector-RS",
        }
    ranked_path = _BASE_DIR / "ranked_universe.json"
    ranked_path.write_text(json.dumps(ranked_universe, indent=2))
    print(f"INFO: Saved ranked_universe.json with {len(ranked_universe)} tickers for AI Quant")


# ==============================================================================
# CLI
# ==============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Universe Builder — global dynamic equity universe (~220 tickers)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--build-cache", action="store_true",
                        help="Fetch all 7 index constituent CSVs from iShares and cache them")
    parser.add_argument("--list-universe", action="store_true",
                        help="Print full liquidity-filtered master universe")
    parser.add_argument("--prescreen", action="store_true",
                        help="Run 5-factor momentum pre-screen and print top tickers")
    parser.add_argument("--top", type=int, default=UNIVERSE_PRESCREEN_TOP_N,
                        help=f"Top N for pre-screen (default: {UNIVERSE_PRESCREEN_TOP_N})")
    parser.add_argument("--indices", nargs="+", default=None, metavar="INDEX",
                        help="Indices to use (default: all 7 from config.UNIVERSE_INDICES)")
    parser.add_argument("--update-watchlist", action="store_true",
                        help="Full pipeline refresh — rewrites UNIVERSE (auto) in watchlist.txt")
    parser.add_argument("--quiet", action="store_true", help="Suppress INFO logging")
    args = parser.parse_args()

    log_level = logging.WARNING if args.quiet else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    indices = args.indices or UNIVERSE_INDICES

    if args.update_watchlist:
        _write_watchlist_from_universe(indices, top_n=args.top)
        return

    if args.build_cache:
        print(f"\nBuilding universe cache for: {indices}")
        for idx in indices:
            print(f"  Fetching {idx}...", end=" ", flush=True)
            t = fetch_index_constituents(idx)
            print(f"{len(t)} tickers cached")
        print("  Done.\n")
        return

    if args.list_universe:
        universe = build_master_universe(indices)
        print(f"\nMaster universe: {len(universe)} tickers")
        for i in range(0, len(universe), 10):
            print("  " + "  ".join(universe[i : i + 10]))
        return

    if args.prescreen:
        universe = build_master_universe(indices)
        top = fast_momentum_prescreen(universe, top_n=args.top)
        print(f"\nTop {len(top)} tickers by 5-factor momentum pre-screen:")
        for i in range(0, len(top), 10):
            print("  " + "  ".join(top[i : i + 10]))
        return

    # Default (no flags): build + cache all indices
    print(f"\nBuilding universe cache for: {indices}")
    for idx in indices:
        print(f"  Fetching {idx}...", end=" ", flush=True)
        t = fetch_index_constituents(idx)
        print(f"{len(t)} tickers cached")
    print("  Done.\n")


if __name__ == "__main__":
    main()
