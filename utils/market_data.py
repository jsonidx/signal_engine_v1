"""
utils/market_data.py — Centralized market-data access layer (TRD-071).

Enforces for all callers:
  - bounded upstream timeout
  - process-wide concurrency limit (threading.Semaphore)
  - SWR (stale-while-revalidate): cached data returned immediately; background refresh when stale
  - request coalescing: concurrent fetches for the same cache key share one outbound call
  - circuit breaker: after N consecutive failures, stops outbound calls for a cooldown window

Public API
----------
    from utils.market_data import get_prices, get_history, get_service_stats

    prices  = get_prices(["AAPL", "MSFT"])          # {symbol: float}
    history = get_history("AAPL", "6mo")            # pd.DataFrame or None
    stats   = get_service_stats()                   # observability dict
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

# ─── Configuration ─────────────────────────────────────────────────────────────

FETCH_TIMEOUT_SECONDS: int = 10
MAX_CONCURRENT_FETCHES: int = 3
FRESH_TTL: int = 300        # seconds — return cache directly, no refresh
STALE_TTL: int = 3600       # seconds — serve stale + trigger background refresh
CIRCUIT_FAILURE_THRESHOLD: int = 3
CIRCUIT_COOLDOWN_SECONDS: int = 60

# ─── Module-level state ────────────────────────────────────────────────────────

_fetch_semaphore = threading.Semaphore(MAX_CONCURRENT_FETCHES)
_bg_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="md_bg")

# SWR cache: key → (value, monotonic_timestamp)
_cache: dict[str, tuple[Any, float]] = {}
_cache_lock = threading.Lock()

# Coalescing: maps cache_key → Event set when the in-flight fetch completes
_in_flight: dict[str, threading.Event] = {}
_in_flight_lock = threading.Lock()

# Circuit breaker state
_cb_lock = threading.Lock()
_cb_failure_count: int = 0
_cb_open_until: float = 0.0  # epoch monotonic; 0 = circuit closed

# Observability counters
_stats: dict[str, int] = {
    "cache_hits": 0,
    "cache_stale_hits": 0,
    "fetches": 0,
    "fetch_errors": 0,
    "coalesced": 0,
    "circuit_open_skips": 0,
    "bg_refreshes": 0,
}
_stats_lock = threading.Lock()


# ─── Helpers: stats, cache, circuit breaker ───────────────────────────────────

def _inc(key: str, n: int = 1) -> None:
    with _stats_lock:
        _stats[key] += n


def get_service_stats() -> dict[str, int]:
    with _stats_lock:
        return dict(_stats)


def _cache_get(key: str) -> tuple[Any, float] | None:
    """Return (value, age_seconds) or None."""
    with _cache_lock:
        entry = _cache.get(key)
    if entry is None:
        return None
    value, ts = entry
    return value, time.monotonic() - ts


def _cache_set(key: str, value: Any) -> None:
    with _cache_lock:
        _cache[key] = (value, time.monotonic())


def _circuit_is_open() -> bool:
    with _cb_lock:
        return time.monotonic() < _cb_open_until


def _circuit_record_success() -> None:
    global _cb_failure_count
    with _cb_lock:
        _cb_failure_count = 0


def _circuit_record_failure() -> None:
    global _cb_failure_count, _cb_open_until
    with _cb_lock:
        _cb_failure_count += 1
        if _cb_failure_count >= CIRCUIT_FAILURE_THRESHOLD:
            _cb_open_until = time.monotonic() + CIRCUIT_COOLDOWN_SECONDS
            log.warning(
                "market_data: circuit breaker opened after %d consecutive failures; "
                "cooldown %ds",
                _cb_failure_count,
                CIRCUIT_COOLDOWN_SECONDS,
            )
            _cb_failure_count = 0


# ─── Low-level fetches (run inside semaphore) ─────────────────────────────────

def _do_fetch_prices(symbols: list[str]) -> dict[str, float]:
    """Blocking yfinance close-price fetch. Acquires semaphore."""
    prices: dict[str, float] = {}
    with _fetch_semaphore:
        _inc("fetches")
        try:
            data = yf.download(
                symbols,
                period="5d",
                auto_adjust=True,
                progress=False,
                threads=True,
                timeout=FETCH_TIMEOUT_SECONDS,
            )
            if data.empty:
                _circuit_record_failure()
                return prices
            if isinstance(data.columns, pd.MultiIndex):
                close = data["Close"]
            else:
                close = data[["Close"]].rename(columns={"Close": symbols[0]})
            for sym in symbols:
                if sym in close.columns:
                    series = close[sym].dropna()
                    if not series.empty:
                        prices[sym] = float(series.iloc[-1])
            _circuit_record_success()
        except Exception as exc:
            log.warning("market_data: price fetch error for %s: %s", symbols, exc)
            _inc("fetch_errors")
            _circuit_record_failure()
    return prices


def _do_fetch_history(symbol: str, period: str, interval: str) -> pd.DataFrame | None:
    """Blocking yfinance OHLCV history fetch. Acquires semaphore."""
    with _fetch_semaphore:
        _inc("fetches")
        try:
            df = yf.download(
                [symbol],
                period=period,
                interval=interval,
                auto_adjust=True,
                progress=False,
                timeout=FETCH_TIMEOUT_SECONDS,
            )
            if df.empty:
                _circuit_record_failure()
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df = df.droplevel(1, axis=1)
            _circuit_record_success()
            return df
        except Exception as exc:
            log.warning("market_data: history fetch error for %s: %s", symbol, exc)
            _inc("fetch_errors")
            _circuit_record_failure()
            return None


# ─── Coalescing wrapper ───────────────────────────────────────────────────────

def _is_valid_result(result: Any) -> bool:
    """True if a fetch produced usable data that should replace what is in cache."""
    if result is None:
        return False
    if isinstance(result, dict):
        return bool(result)  # empty dict means the fetch found nothing / failed
    return True  # pd.DataFrame or any other non-None type is considered valid


def _fetch_coalesced(cache_key: str, fetch_fn, *args) -> Any:
    """
    Ensure only one outbound fetch per cache_key runs at a time.
    Concurrent callers become followers: they wait on the leader's Event
    and read the result from cache once it lands.

    Failed fetches ({} or None) do NOT overwrite an existing cached value so
    that a last-known-good stale entry is preserved through transient failures.
    """
    with _in_flight_lock:
        if cache_key in _in_flight:
            event = _in_flight[cache_key]
            is_leader = False
        else:
            event = threading.Event()
            _in_flight[cache_key] = event
            is_leader = True

    if is_leader:
        try:
            result = fetch_fn(*args)
            if _is_valid_result(result):
                _cache_set(cache_key, result)
            return result
        finally:
            with _in_flight_lock:
                _in_flight.pop(cache_key, None)
            event.set()
    else:
        _inc("coalesced")
        event.wait(timeout=FETCH_TIMEOUT_SECONDS + 5)
        hit = _cache_get(cache_key)
        return hit[0] if hit else None


# ─── Background refresh ───────────────────────────────────────────────────────

def _schedule_bg_refresh(cache_key: str, fetch_fn, *args) -> None:
    """Submit a background cache refresh; skips if the circuit is open or a fetch is already in-flight."""
    if _circuit_is_open():
        return  # circuit open — respect the cooldown, do not schedule outbound calls
    with _in_flight_lock:
        if cache_key in _in_flight:
            return  # already refreshing

    def _run():
        _inc("bg_refreshes")
        _fetch_coalesced(cache_key, fetch_fn, *args)
        log.debug("market_data: background refresh done for %s", cache_key)

    try:
        _bg_executor.submit(_run)
    except RuntimeError:
        pass  # executor shut down (test teardown / process exit)


# ─── Public API ───────────────────────────────────────────────────────────────

def get_prices(symbols: list[str]) -> dict[str, float]:
    """
    Get last-close prices for symbols.

    SWR semantics:
    - FRESH  (age < FRESH_TTL): return cache, no outbound fetch.
    - STALE  (age < STALE_TTL): return cache immediately, trigger background refresh.
    - EXPIRED or cold: block on one live fetch (semaphore-bounded, timeout-guarded).

    Concurrent requests for the same symbol set are coalesced.
    Circuit breaker returns an empty dict and skips the outbound call when open.
    """
    if not symbols:
        return {}

    cache_key = "prices:" + ",".join(sorted(symbols))
    hit = _cache_get(cache_key)

    if hit is not None:
        value, age = hit
        if age < FRESH_TTL:
            _inc("cache_hits")
            return value
        # Stale or expired — serve immediately and refresh in background
        _inc("cache_stale_hits")
        _schedule_bg_refresh(cache_key, _do_fetch_prices, symbols)
        return value

    # Cold miss
    if _circuit_is_open():
        _inc("circuit_open_skips")
        log.warning("market_data: circuit open; skipping fetch for %s", symbols)
        return {}

    result = _fetch_coalesced(cache_key, _do_fetch_prices, symbols)
    return result or {}


def get_history(symbol: str, period: str, interval: str | None = None) -> pd.DataFrame | None:
    """
    Get OHLCV history for symbol.

    Cached per (symbol, period, interval) with the same SWR semantics as get_prices.
    """
    ivl = interval or "1d"
    cache_key = f"history:{symbol}:{period}:{ivl}"
    hit = _cache_get(cache_key)

    if hit is not None:
        value, age = hit
        if age < FRESH_TTL:
            _inc("cache_hits")
            return value
        _inc("cache_stale_hits")
        _schedule_bg_refresh(cache_key, _do_fetch_history, symbol, period, ivl)
        return value

    if _circuit_is_open():
        _inc("circuit_open_skips")
        return None

    return _fetch_coalesced(cache_key, _do_fetch_history, symbol, period, ivl)
