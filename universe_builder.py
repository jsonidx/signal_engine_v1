#!/usr/bin/env python3
"""
universe_builder.py
===================
Dynamic multi-index universe construction for the Signal Engine.

Fetches constituent lists from iShares ETF holdings CSVs, applies
liquidity/price/history pre-screen filters, then runs a fast 3-signal
momentum pre-screen to narrow the field to top_n candidates before deep
scoring.

PUBLIC API:
    fetch_index_constituents(index)         -> list[str]
    build_master_universe(indices=None)     -> list[str]
    fast_momentum_prescreen(tickers, top_n) -> list[str]

CLI:
    python3 universe_builder.py --build-cache          # fetch + cache all indices
    python3 universe_builder.py --list-universe        # print filtered universe
    python3 universe_builder.py --prescreen --top 200  # run momentum prescreen

FALLBACK CHAIN (per index):
    1. Fresh HTTP fetch from iShares (< 24hr TTL)
    2. Cached JSON in data/universe_cache/ (up to 7 days on network failure)
    3. Hardcoded _HARDCODED_FALLBACK list (if no usable cache exists)
"""

import argparse
import io
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# iShares ETF holdings CSV endpoints
# ---------------------------------------------------------------------------
_INDEX_URLS: dict = {
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
    # nasdaq100 removed — CNDX URL is the London UCITS version and 404s on iShares US.
    # Russell 1000 + S&P 500 already cover all Nasdaq 100 constituents.
    # "nasdaq100": "...",
    "sp400": (
        "https://www.ishares.com/us/products/239763/ISHARES-SP-MIDCAP-400-ETF"
        "/1467271812596.ajax?fileType=csv&fileName=IJH_holdings&dataType=fund"
    ),
}

# ---------------------------------------------------------------------------
# Hardcoded fallback (mirrors catalyst_screener.py legacy universe)
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Config — import from config.py with safe defaults
# ---------------------------------------------------------------------------
try:
    from config import (
        UNIVERSE_INDICES,
        UNIVERSE_PRESCREEN_TOP_N,
        UNIVERSE_MIN_DOLLAR_VOLUME,
        UNIVERSE_MIN_PRICE,
        UNIVERSE_CACHE_TTL_HOURS,
    )
except ImportError:
    UNIVERSE_INDICES = ["russell1000", "russell2000", "sp500", "sp400"]
    UNIVERSE_PRESCREEN_TOP_N = 200
    UNIVERSE_MIN_DOLLAR_VOLUME = 3_000_000
    UNIVERSE_MIN_PRICE = 2.0
    UNIVERSE_CACHE_TTL_HOURS = 24

_CACHE_DIR = Path(__file__).parent / "data" / "universe_cache"
_WATCHLIST_PATH = Path(__file__).parent / "watchlist.txt"


# ===========================================================================
# Internal helpers
# ===========================================================================

def _fetch_with_retry(
    url: str,
    attempts: int = 3,
    backoff: float = 5.0,
    timeout: int = 30,
) -> Optional[requests.Response]:
    """GET *url* with up to *attempts* retries and *backoff* seconds between them."""
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
                logger.error(
                    "All %d fetch attempts failed for %s: %s",
                    attempts, url, exc,
                )
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
        return data["tickers"]
    except Exception as exc:
        logger.warning("Failed to read cache for %s: %s", index, exc)
        return None


def _save_cache(index: str, tickers: list) -> None:
    """Write ticker list to JSON cache."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(index)
    try:
        with open(path, "w") as fh:
            json.dump({"cached_at": datetime.now().isoformat(), "tickers": tickers}, fh)
        logger.debug("Cached %d tickers for %s → %s", len(tickers), index, path)
    except Exception as exc:
        logger.warning("Failed to write cache for %s: %s", index, exc)


def _parse_ishares_csv(text: str) -> list:
    """
    Parse an iShares ETF holdings CSV.

    Format: 9 metadata rows, then a header row containing a 'Ticker' column,
    then one data row per holding.  Non-equity rows (cash, "-", blank) are
    filtered out.
    """
    try:
        df = pd.read_csv(io.StringIO(text), skiprows=9, header=0, on_bad_lines="skip")
    except Exception as exc:
        logger.error("CSV parse error: %s", exc)
        return []

    # Find 'Ticker' column case-insensitively
    ticker_col = next(
        (c for c in df.columns if str(c).strip().lower() == "ticker"),
        None,
    )
    if ticker_col is None:
        logger.error(
            "No 'Ticker' column found in iShares CSV. Columns found: %s",
            list(df.columns),
        )
        return []

    tickers = []
    for raw in df[ticker_col].dropna():
        t = str(raw).strip()
        # Exclude cash rows, blank rows, placeholder "-"
        if t and t != "-" and not t.upper().startswith("CASH") and len(t) <= 6:
            tickers.append(t)
    return tickers


def _batch_close_volume(
    tickers: list,
    period: str,
    batch_size: int = 100,
) -> tuple[dict, dict]:
    """
    Download Close and Volume for *tickers* in batches.

    Returns (close_dict, volume_dict) mapping ticker → pd.Series (dropna applied).
    Uses pattern from paper_trader.py to handle both single- and multi-ticker
    yf.download responses.
    """
    all_close: dict = {}
    all_volume: dict = {}

    import io, contextlib, logging as _logging
    # Silence yfinance's own ERROR/WARNING log spam for delisted/non-US tickers
    _yf_logger = _logging.getLogger("yfinance")
    _prev_level = _yf_logger.level
    _yf_logger.setLevel(_logging.CRITICAL)

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            _buf = io.StringIO()
            with contextlib.redirect_stdout(_buf), contextlib.redirect_stderr(_buf):
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
                close_df = df["Close"]
                vol_df = df["Volume"]
            else:
                # Single-ticker fallback — wrap as one-column DataFrame
                t = batch[0]
                close_df = df[["Close"]].rename(columns={"Close": t})
                vol_df = df[["Volume"]].rename(columns={"Volume": t})

            for t in batch:
                if t in close_df.columns:
                    c = close_df[t].dropna()
                    v = vol_df[t].dropna()
                    if not c.empty:
                        all_close[t] = c
                        all_volume[t] = v
        except Exception as exc:
            logger.warning(
                "Batch download failed (batch starting %s): %s", batch[0], exc
            )

    _yf_logger.setLevel(_prev_level)
    return all_close, all_volume


# ===========================================================================
# Public API
# ===========================================================================

def fetch_index_constituents(index: str) -> list:
    """
    Return constituent tickers for a named index.

    index options: 'russell1000', 'russell2000', 'sp500', 'nasdaq100', 'sp400'

    Fallback chain:
      1. Fresh HTTP fetch + parse (saves to 24hr cache)
      2. Stale cache (up to 7 days) if network fails
      3. _HARDCODED_FALLBACK if no usable cache

    Never raises — always returns a list.
    """
    if index not in _INDEX_URLS:
        raise ValueError(
            f"Unknown index: {index!r}. Valid options: {sorted(_INDEX_URLS)}"
        )

    # 1. Try warm cache first (avoids unnecessary HTTP when fresh)
    cached_fresh = _load_cache(index, max_age_hours=UNIVERSE_CACHE_TTL_HOURS)
    if cached_fresh is not None:
        logger.info(
            "Using fresh cache for %s (%d tickers)", index, len(cached_fresh)
        )
        return cached_fresh

    # 2. Attempt HTTP fetch
    url = _INDEX_URLS[index]
    resp = _fetch_with_retry(url)
    if resp is not None:
        tickers = _parse_ishares_csv(resp.text)
        if tickers:
            logger.info(
                "Fetched %d constituents for %s from iShares", len(tickers), index
            )
            _save_cache(index, tickers)
            return tickers
        logger.warning(
            "iShares CSV for %s parsed 0 tickers — check CSV format", index
        )

    # 3. Fall back to stale cache (up to 7 days)
    stale = _load_cache(index, max_age_hours=7 * 24)
    if stale:
        logger.warning(
            "[WARN] Network failed for %s — using stale cache (%d tickers)",
            index, len(stale),
        )
        print(
            f"  [WARN] Network failed for {index} — using stale cache "
            f"({len(stale)} tickers)"
        )
        return stale

    # 4. Last resort: hardcoded fallback
    logger.warning(
        "[WARN] No usable cache for %s — falling back to hardcoded universe "
        "(%d tickers)",
        index, len(_HARDCODED_FALLBACK),
    )
    print(
        f"  [WARN] No cache for {index} — falling back to hardcoded universe "
        f"({len(_HARDCODED_FALLBACK)} tickers)"
    )
    return list(_HARDCODED_FALLBACK)


def _apply_liquidity_filter(tickers: list, batch_size: int = 100) -> list:
    """
    Filter *tickers* by price, 30-day avg dollar volume, and history length.

    Thresholds (from config):
        price               > UNIVERSE_MIN_PRICE          (default $2.00)
        30d avg dollar vol  > UNIVERSE_MIN_DOLLAR_VOLUME   (default $3 M)
        history length      ≥ 126 bars
    """
    all_close, all_volume = _batch_close_volume(tickers, period="3mo", batch_size=batch_size)

    passed = []
    for t in tickers:
        c = all_close.get(t)
        v = all_volume.get(t)
        if c is None or v is None or len(c) < 20:   # 3mo period ≈ 63 bars; require ≥20
            continue
        price = float(c.iloc[-1])
        if price < UNIVERSE_MIN_PRICE:
            continue
        # 30-day avg dollar volume — align close/volume by index before multiplying
        tail_c = c.iloc[-30:]
        tail_v = v.reindex(tail_c.index).fillna(0)
        avg_dv = float((tail_c * tail_v).mean())
        if avg_dv < UNIVERSE_MIN_DOLLAR_VOLUME:
            continue
        passed.append(t)
    return passed


def build_master_universe(indices: list = None) -> list:
    """
    Combine multiple index constituent lists, deduplicate, and apply
    liquidity/price/history pre-screen filters.

    Default indices: UNIVERSE_INDICES from config.py
    (russell1000, russell2000, sp500, nasdaq100)

    Filters applied via yfinance batch download (no individual Ticker() calls):
        - min price > UNIVERSE_MIN_PRICE ($2.00)
        - min 30d avg dollar volume > UNIVERSE_MIN_DOLLAR_VOLUME ($3 M)
        - no '.' in ticker symbol (excludes ADRs / preferred share suffixes)
        - at least 126 bars of history

    Logs: total raw, after dedup+dot-filter, final count.
    Returns: deduplicated, filtered list of ticker strings.
    """
    if indices is None:
        indices = UNIVERSE_INDICES

    # --- Collect raw tickers from all indices ---
    raw: list = []
    for idx in indices:
        constituents = fetch_index_constituents(idx)
        logger.info("  %s: %d constituents", idx, len(constituents))
        raw.extend(constituents)

    # --- Deduplicate, strip dot-tickers ---
    seen: set = set()
    deduped: list = []
    for t in raw:
        if t not in seen and "." not in t:
            seen.add(t)
            deduped.append(t)

    logger.info(
        "Universe: raw=%d  after dedup+dot-filter=%d", len(raw), len(deduped)
    )
    print(
        f"  Universe: {len(raw)} raw → {len(deduped)} after dedup / dot-filter"
    )

    # --- Liquidity filter ---
    t0 = time.time()
    passed = _apply_liquidity_filter(deduped)
    elapsed = time.time() - t0
    logger.info(
        "Liquidity filter: %d → %d in %.1fs", len(deduped), len(passed), elapsed
    )
    print(
        f"  Liquidity filter: {len(deduped)} → {len(passed)} in {elapsed:.1f}s"
    )
    return passed


def _compute_prescreen_scores(tickers: list, batch_size: int = 100) -> dict:
    """
    Download 1yr of data in batches and compute 3-signal momentum scores.

    Signals:
        RS_rank:       20-day return, cross-sectionally ranked (percentile 0–1)
        vol_ratio:     5-day avg volume / 20-day avg volume (clipped 0–3, /3)
        price_vs_52wk: current price / 52-week high

    Score = 0.5 * RS_rank_pct + 0.3 * clip(vol_ratio,0,3)/3 + 0.2 * price_vs_52wk

    Returns dict mapping ticker → score.
    """
    all_close, all_volume = _batch_close_volume(tickers, period="1y", batch_size=batch_size)

    if not all_close:
        return {}

    returns_20d: dict = {}
    vol_ratios: dict = {}
    p52wk: dict = {}

    for t, c in all_close.items():
        if len(c) < 22:
            continue
        ret_20d = float(c.iloc[-1] / c.iloc[-21] - 1)
        returns_20d[t] = ret_20d

        v = all_volume.get(t, pd.Series(dtype=float))
        if len(v) >= 20:
            v5 = float(v.iloc[-5:].mean())
            v20 = float(v.iloc[-20:].mean())
            vol_ratios[t] = v5 / v20 if v20 > 0 else 1.0
        else:
            vol_ratios[t] = 1.0

        window = min(252, len(c))
        high_52 = float(c.iloc[-window:].max())
        p52wk[t] = float(c.iloc[-1]) / high_52 if high_52 > 0 else 0.5

    if not returns_20d:
        return {}

    # Cross-sectional RS rank (percentile within universe)
    ticker_list = list(returns_20d.keys())
    rets = np.array([returns_20d[t] for t in ticker_list])
    rs_percentiles = pd.Series(rets).rank(pct=True).values

    scores: dict = {}
    for i, t in enumerate(ticker_list):
        rs_pct = float(rs_percentiles[i])
        vr = float(np.clip(vol_ratios.get(t, 1.0), 0, 3)) / 3.0
        p52 = p52wk.get(t, 0.5)
        scores[t] = round(0.5 * rs_pct + 0.3 * vr + 0.2 * p52, 6)

    return scores


def _get_favorites(watchlist_path: Path = None) -> list:
    """
    Parse watchlist.txt and return all tickers from the FAVORITES section.
    Returns [] if file not found or FAVORITES section is empty.
    """
    path = watchlist_path or _WATCHLIST_PATH
    if not path.exists():
        return []
    try:
        tickers: list = []
        in_favorites = False
        with open(path) as fh:
            for line in fh:
                line = line.rstrip()
                stripped = line.strip()
                if "FAVORITES" in stripped.upper() and stripped.startswith("#"):
                    in_favorites = True
                    continue
                # Stop at next section header (# ── UNIVERSE or similar)
                if in_favorites and stripped.startswith("# ──"):
                    break
                if not in_favorites:
                    continue
                if not stripped or stripped.startswith("#"):
                    continue
                ticker = stripped.split()[0].strip()
                if ticker:
                    tickers.append(ticker.upper())
        return tickers
    except Exception as exc:
        logger.warning("Failed to parse watchlist.txt for favorites: %s", exc)
        return []


def fast_momentum_prescreen(tickers: list, top_n: int = None) -> list:
    """
    Narrow *tickers* to the top *top_n* candidates using a fast 3-signal score.

    Signals (computed via batch yf.download — no individual Ticker() calls):
        RS_rank:       20-day return cross-sectionally ranked (percentile)
        vol_ratio:     5-day / 20-day average volume
        price_vs_52wk: current price / 52-week high

    Score = 0.5 * RS_rank_pct + 0.3 * clip(vol_ratio,0,3)/3 + 0.2 * price_vs_52wk

    Tier 1 watchlist tickers are always preserved regardless of score.
    Logs: "Pre-screen: {total} → {final} in {elapsed:.1f}s"
    """
    if top_n is None:
        top_n = UNIVERSE_PRESCREEN_TOP_N

    tier1 = set(t.upper() for t in _get_favorites())
    t0 = time.time()

    scores = _compute_prescreen_scores(tickers)

    # Select top_n by score
    sorted_by_score = sorted(scores, key=lambda t: scores[t], reverse=True)
    top_set = set(sorted_by_score[:top_n])

    # Force-include Tier 1 tickers that didn't make the cut
    forced = tier1 - top_set
    if forced:
        logger.info(
            "Forcing %d Tier 1 tickers into prescreen: %s",
            len(forced), sorted(forced),
        )

    result_set = top_set | tier1

    # Sort final list by score descending; Tier 1 tickers without scores go last
    result = sorted(result_set, key=lambda t: scores.get(t, -1.0), reverse=True)

    elapsed = time.time() - t0
    total = len(tickers)
    final = len(result)
    msg = f"Pre-screen: {total} → {final} in {elapsed:.1f}s"
    print(f"  {msg}")
    logger.info(msg)

    return result


# ===========================================================================
# WATCHLIST SEED
# ===========================================================================

def _write_watchlist_from_universe(indices: list = None, top_n: int = None) -> None:
    """
    Build master universe (2000+ tickers), run momentum prescreen to pick the
    top trending candidates, then rewrite the UNIVERSE (auto) block in
    watchlist.txt.

    FAVORITES section is always preserved unchanged.
    The UNIVERSE (auto) block is replaced with the latest momentum prescreen
    results (default top 200 by 20d RS rank / volume surge / 52-week proximity).

    Run via:
        python3 universe_builder.py --update-watchlist [--top 300]
    """
    if top_n is None:
        top_n = UNIVERSE_PRESCREEN_TOP_N

    watchlist_path = _WATCHLIST_PATH

    print("\n  Building master universe (liquidity filter)...")
    universe = build_master_universe(indices)
    print(f"  Liquidity-filtered universe: {len(universe)} tickers")

    print(f"  Running momentum prescreen → top {top_n}...")
    top_tickers = fast_momentum_prescreen(universe, top_n=top_n)

    # Read existing watchlist lines verbatim
    existing_lines: list[str] = []
    if watchlist_path.exists():
        existing_lines = watchlist_path.read_text().splitlines()

    # Collect FAVORITES tickers (keep them out of the auto block)
    favorites: set[str] = set(t.upper() for t in _get_favorites(watchlist_path))

    # Tickers for the auto block = prescreen results minus favorites
    auto_tickers = [t for t in top_tickers if t.upper() not in favorites]

    # Strip old auto block; keep everything up to it
    filtered_lines: list[str] = []
    in_auto_block = False
    for line in existing_lines:
        stripped = line.strip()
        if stripped.startswith("# ── UNIVERSE (auto)"):
            in_auto_block = True
            continue
        if in_auto_block and (stripped.startswith("# ──") or stripped.startswith("# ==")):
            in_auto_block = False
        if not in_auto_block:
            filtered_lines.append(line)

    # Strip trailing blank lines from existing content
    while filtered_lines and not filtered_lines[-1].strip():
        filtered_lines.pop()

    # Append new dynamic block
    from datetime import datetime as _dt
    stamp = _dt.now().strftime("%Y-%m-%d %H:%M")
    filtered_lines.append("")
    filtered_lines.append(
        f"# ── UNIVERSE (auto) — top {len(auto_tickers)} by momentum prescreen  [{stamp}] ──"
    )
    for t in auto_tickers:
        filtered_lines.append(t)
    filtered_lines.append("")

    watchlist_path.write_text("\n".join(filtered_lines) + "\n")
    print(
        f"  Watchlist updated: {len(favorites)} favorites + {len(auto_tickers)} dynamic tickers"
        f"  →  watchlist.txt"
    )


# ===========================================================================
# CLI
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Universe Builder — dynamic multi-index ticker universe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--build-cache",
        action="store_true",
        help="Fetch all index constituents from iShares and write to cache",
    )
    parser.add_argument(
        "--list-universe",
        action="store_true",
        help="Print full filtered master universe (applies liquidity filter)",
    )
    parser.add_argument(
        "--prescreen",
        action="store_true",
        help="Run fast momentum pre-screen and print top tickers",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=UNIVERSE_PRESCREEN_TOP_N,
        help=f"Top N for pre-screen (default: {UNIVERSE_PRESCREEN_TOP_N})",
    )
    parser.add_argument(
        "--indices",
        nargs="+",
        default=None,
        metavar="INDEX",
        help="Indices to use (default: config.UNIVERSE_INDICES)",
    )
    parser.add_argument(
        "--update-watchlist",
        action="store_true",
        help="Refresh UNIVERSE (auto) block in watchlist.txt with top momentum tickers",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress INFO logging",
    )
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
            tickers = fetch_index_constituents(idx)
            print(f"{len(tickers)} tickers cached")
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
        print(f"\nTop {len(top)} tickers by momentum pre-screen score:")
        for i in range(0, len(top), 10):
            print("  " + "  ".join(top[i : i + 10]))
        return

    # Default with no flags: just build the cache
    print(f"\nBuilding universe cache for: {indices}")
    for idx in indices:
        print(f"  Fetching {idx}...", end=" ", flush=True)
        tickers = fetch_index_constituents(idx)
        print(f"{len(tickers)} tickers cached")
    print("  Done.\n")


if __name__ == "__main__":
    main()
