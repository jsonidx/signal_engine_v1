"""
yf_cache.py — Smart yfinance fetch helpers.
================================================================================
Two concrete optimizations for pipeline screeners:

  bulk_history(tickers, period, interval)
      One yf.download() call replaces N individual stock.history() calls.
      Used by catalyst_screener and squeeze_screener before their scan loops.
      Returns {ticker: OHLCV_DataFrame} so callers get the same shape as
      stock.history() — drop-in compatible.

  filter_blacklisted(tickers)
      Removes tickers on the active blacklist before any network call.
      Thin wrapper around db_cache.get_active_blacklist().

NOTE on .info caching:
  yf.Ticker().info has no bulk API — each call is a separate HTTP request.
  The right cache for it is fundamentals_cache (Supabase, 30-day TTL).
  fundamental_analysis.py already populates it at Step 7. On the second+
  run, any screener that calls yf.Ticker(t).info will get fundamentals_cache
  data instead — that's handled in fundamental_analysis.fetch_fundamentals(),
  not here. We don't duplicate that logic.

PUBLIC API
----------
    from yf_cache import bulk_history, filter_blacklisted

    tickers = filter_blacklisted(raw_tickers)
    hist_map = bulk_history(tickers, period="6mo")  # {TICKER: DataFrame}

    for ticker in tickers:
        hist = hist_map.get(ticker)       # None if download failed for this ticker
        data = get_stock_data(ticker, prefetched_hist=hist)
================================================================================
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Minimum valid trading bars required to consider a ticker's history usable.
_MIN_BARS = 20


def filter_blacklisted(tickers: list[str]) -> list[str]:
    """
    Return *tickers* with any active blacklist entries removed.

    Fails open: if the DB is unavailable, returns the full list unchanged
    so the pipeline never stalls because of a Supabase outage.

    Usage:
        tickers = filter_blacklisted(raw_tickers)
    """
    try:
        from db_cache import get_active_blacklist
        bl = set(get_active_blacklist())
        if not bl:
            logger.info("filter_blacklisted: blacklist empty — all %d tickers passed", len(tickers))
            return tickers
        before = len(tickers)
        filtered = [t for t in tickers if t.upper() not in bl]
        skipped = before - len(filtered)
        if skipped:
            logger.info("filter_blacklisted: removed %d/%d blacklisted tickers", skipped, before)
            print(f"  Blacklist: skipped {skipped} tickers")
        else:
            logger.info("filter_blacklisted: 0/%d tickers on blacklist — all passed", before)
        return filtered
    except Exception as exc:
        logger.info("filter_blacklisted: DB unavailable (%s) — returning full list", exc)
        return tickers


def bulk_history(
    tickers: list[str],
    period: str = "6mo",
    interval: str = "1d",
    batch_size: int = 200,
) -> dict[str, pd.DataFrame]:
    """
    Download OHLCV history for all *tickers* in one or more yf.download() calls.

    Returns a dict {TICKER_UPPER: DataFrame} where each DataFrame has the same
    column structure as yf.Ticker(t).history() — Open, High, Low, Close, Volume
    — so it can be passed directly as `prefetched_hist` to screener functions.

    Tickers with fewer than _MIN_BARS valid rows are excluded from the result;
    callers that receive None for a ticker fall back to their normal per-ticker
    fetch path.

    Args:
        tickers:    List of ticker symbols.
        period:     yfinance period string, e.g. "6mo", "1y", "3mo".
        interval:   Bar interval, e.g. "1d", "1h".
        batch_size: Max tickers per yf.download() call. 200 is safe; larger
                    batches may trigger Yahoo Finance rate limits.

    Usage:
        hist_map = bulk_history(watchlist, period="6mo")
        for ticker in watchlist:
            df = hist_map.get(ticker.upper())   # None = no data / delisted
    """
    if not tickers:
        return {}

    result: dict[str, pd.DataFrame] = {}
    upper_tickers = [t.upper() for t in tickers]

    # Suppress yfinance progress output
    import contextlib, io
    _yf_log = logging.getLogger("yfinance")
    _prev_level = _yf_log.level
    _yf_log.setLevel(logging.CRITICAL)

    for i in range(0, len(upper_tickers), batch_size):
        batch = upper_tickers[i : i + batch_size]
        try:
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                raw = yf.download(
                    batch,
                    period=period,
                    interval=interval,
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )

            if raw is None or raw.empty:
                logger.warning("bulk_history: empty result for batch starting %s", batch[0])
                continue

            _extract_batch(raw, batch, result)

        except Exception as exc:
            logger.warning("bulk_history: download failed (batch[0]=%s): %s", batch[0], exc)

    _yf_log.setLevel(_prev_level)

    valid = sum(1 for df in result.values() if df is not None and len(df) >= _MIN_BARS)
    skipped = len(tickers) - valid
    logger.info(
        "bulk_history: %d/%d tickers with valid history (period=%s%s)",
        valid, len(tickers), period,
        f", {skipped} skipped (no data / <{_MIN_BARS} bars)" if skipped else "",
    )
    return result


def _extract_batch(
    raw: pd.DataFrame,
    tickers: list[str],
    out: dict[str, pd.DataFrame],
) -> None:
    """
    Extract per-ticker DataFrames from a yf.download() result and add to *out*.
    Handles both MultiIndex (multi-ticker) and flat (single-ticker) layouts.
    """
    _OHLCV = ["Open", "High", "Low", "Close", "Volume"]

    if isinstance(raw.columns, pd.MultiIndex):
        # Multi-ticker layout: columns are (field, ticker)
        top_fields = raw.columns.get_level_values(0).unique().tolist()
        if "Close" not in top_fields:
            return

        close_df = raw["Close"]

        for t in tickers:
            # yfinance may return original or upper-cased column names
            col = t if t in close_df.columns else (
                  t.upper() if t.upper() in close_df.columns else None)
            if col is None:
                continue

            frames = {}
            for field in _OHLCV:
                if field in top_fields and col in raw[field].columns:
                    frames[field] = raw[field][col]

            if "Close" not in frames:
                continue

            df = pd.DataFrame(frames)
            # Drop rows where Close is NaN (no trading that day)
            df = df[df["Close"].notna()]

            if len(df) >= _MIN_BARS:
                out[t] = df

    else:
        # Single-ticker layout: flat DataFrame
        if len(tickers) != 1:
            return
        t = tickers[0]
        cols_present = [c for c in _OHLCV if c in raw.columns]
        if "Close" not in cols_present:
            return
        df = raw[cols_present].copy()
        df = df[df["Close"].notna()]
        if len(df) >= _MIN_BARS:
            out[t] = df
