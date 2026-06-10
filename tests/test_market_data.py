"""
Tests for utils/market_data.py  (TRD-071)

Coverage:
  - cache hit (fresh): no outbound fetch
  - stale-while-revalidate: stale cache returned immediately, yfinance not called
  - cold miss: fetch is triggered
  - request coalescing: concurrent same-key requests share one outbound call
  - semaphore: at most MAX_CONCURRENT_FETCHES fetches run concurrently
  - circuit breaker: opens after N failures, skips fetch when open
  - get_history: basic SWR path
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import utils.market_data as md


# ─── Fixture: reset all module state between tests ───────────────────────────

@pytest.fixture(autouse=True)
def reset_md_state():
    """Replace module-level state with clean instances for each test."""
    # Swap out mutable state
    old = {
        "_cache": md._cache,
        "_in_flight": md._in_flight,
        "_fetch_semaphore": md._fetch_semaphore,
        "_cb_failure_count": md._cb_failure_count,
        "_cb_open_until": md._cb_open_until,
        "_stats": md._stats,
    }
    md._cache = {}
    md._in_flight = {}
    md._fetch_semaphore = threading.Semaphore(md.MAX_CONCURRENT_FETCHES)
    md._cb_failure_count = 0
    md._cb_open_until = 0.0
    md._stats = {k: 0 for k in old["_stats"]}

    yield

    # Restore
    md._cache = old["_cache"]
    md._in_flight = old["_in_flight"]
    md._fetch_semaphore = old["_fetch_semaphore"]
    md._cb_failure_count = old["_cb_failure_count"]
    md._cb_open_until = old["_cb_open_until"]
    md._stats = old["_stats"]


# ─── Fresh cache hit ──────────────────────────────────────────────────────────

def test_fresh_cache_hit_skips_fetch():
    """A fresh cache entry is returned without calling yfinance."""
    md._cache_set("prices:AAPL", {"AAPL": 150.0})

    with patch.object(md, "_do_fetch_prices") as mock_fetch:
        result = md.get_prices(["AAPL"])

    assert result == {"AAPL": 150.0}
    mock_fetch.assert_not_called()
    assert md._stats["cache_hits"] == 1


# ─── Stale-while-revalidate ───────────────────────────────────────────────────

def test_stale_cache_returned_immediately_without_blocking():
    """
    When cached data exists but is stale, get_prices returns it immediately
    and schedules a background refresh — it does NOT block on a live fetch.
    """
    stale_ts = time.monotonic() - (md.FRESH_TTL + 30)
    with md._cache_lock:
        md._cache["prices:MSFT"] = ({"MSFT": 300.0}, stale_ts)

    refresh_targets: list[str] = []

    def capture_refresh(cache_key, *args):
        refresh_targets.append(cache_key)

    with patch.object(md, "_schedule_bg_refresh", side_effect=capture_refresh):
        with patch.object(md, "_do_fetch_prices") as mock_yf:
            result = md.get_prices(["MSFT"])

    assert result == {"MSFT": 300.0}           # stale data served immediately
    mock_yf.assert_not_called()                 # did NOT block on live fetch
    assert "prices:MSFT" in refresh_targets     # background refresh scheduled
    assert md._stats["cache_stale_hits"] == 1


def test_expired_cache_still_served_while_refreshing():
    """
    Even data older than STALE_TTL is returned immediately (never block the caller),
    and a background refresh is triggered.
    """
    very_old_ts = time.monotonic() - (md.STALE_TTL + 60)
    with md._cache_lock:
        md._cache["prices:GOOG"] = ({"GOOG": 180.0}, very_old_ts)

    with patch.object(md, "_schedule_bg_refresh") as mock_refresh:
        with patch.object(md, "_do_fetch_prices"):
            result = md.get_prices(["GOOG"])

    assert result == {"GOOG": 180.0}
    mock_refresh.assert_called_once()


# ─── Cold miss ────────────────────────────────────────────────────────────────

def test_cold_miss_triggers_fetch():
    """With no cache entry, get_prices calls _do_fetch_prices."""
    with patch.object(md, "_do_fetch_prices", return_value={"TSLA": 200.0}) as mock_fetch:
        result = md.get_prices(["TSLA"])

    assert result == {"TSLA": 200.0}
    mock_fetch.assert_called_once_with(["TSLA"])
    assert md._stats["fetches"] == 0  # _do_fetch_prices is mocked so stats not hit
    # Verify the result was cached
    hit = md._cache_get("prices:TSLA")
    assert hit is not None
    assert hit[0] == {"TSLA": 200.0}


# ─── Request coalescing ───────────────────────────────────────────────────────

def test_concurrent_requests_for_same_key_share_one_fetch():
    """
    Two threads requesting the same symbol set simultaneously must produce
    exactly one outbound fetch — the follower coalesces onto the leader's result.
    """
    fetch_count = 0
    start_barrier = threading.Barrier(2)

    def slow_fetch(symbols):
        nonlocal fetch_count
        fetch_count += 1
        time.sleep(0.05)
        return {"NVDA": 800.0}

    results: list[dict] = []

    def worker():
        start_barrier.wait()  # both start at the same time
        results.append(md.get_prices(["NVDA"]))

    with patch.object(md, "_do_fetch_prices", side_effect=slow_fetch):
        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

    assert fetch_count == 1, f"Expected 1 outbound fetch, got {fetch_count}"
    assert all(r == {"NVDA": 800.0} for r in results)


# ─── Semaphore concurrency limiting ──────────────────────────────────────────

def test_semaphore_limits_concurrent_fetches():
    """
    When more than MAX_CONCURRENT_FETCHES cold-miss requests arrive simultaneously,
    the semaphore ensures at most MAX_CONCURRENT_FETCHES yfinance calls run at once.
    """
    concurrent: list[int] = []
    active = threading.local()
    lock = threading.Lock()
    current_active = 0

    def counting_download(*args, **kwargs):
        nonlocal current_active
        with lock:
            current_active += 1
            concurrent.append(current_active)
        time.sleep(0.04)
        with lock:
            current_active -= 1
        return pd.DataFrame()

    # Each thread fetches a distinct symbol so coalescing does not apply
    N = md.MAX_CONCURRENT_FETCHES + 3
    symbols_list = [[f"SYM{i}"] for i in range(N)]

    with patch("utils.market_data.yf") as mock_yf:
        mock_yf.download.side_effect = counting_download

        threads = [
            threading.Thread(target=md.get_prices, args=(syms,))
            for syms in symbols_list
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

    max_observed = max(concurrent) if concurrent else 0
    assert max_observed <= md.MAX_CONCURRENT_FETCHES, (
        f"Semaphore violated: {max_observed} concurrent fetches observed, "
        f"limit is {md.MAX_CONCURRENT_FETCHES}"
    )


# ─── Circuit breaker ─────────────────────────────────────────────────────────

def test_circuit_opens_after_consecutive_failures():
    """After CIRCUIT_FAILURE_THRESHOLD failures the circuit is open."""
    assert not md._circuit_is_open()

    for _ in range(md.CIRCUIT_FAILURE_THRESHOLD):
        md._circuit_record_failure()

    assert md._circuit_is_open()


def test_circuit_open_skips_fetch_and_returns_empty():
    """When the circuit is open and there is no cached data, get_prices returns {} immediately."""
    # Force circuit open
    with md._cb_lock:
        md._cb_open_until = time.monotonic() + 60

    with patch.object(md, "_do_fetch_prices") as mock_fetch:
        result = md.get_prices(["AMZN"])

    assert result == {}
    mock_fetch.assert_not_called()
    assert md._stats["circuit_open_skips"] == 1


def test_circuit_closes_after_cooldown():
    """After the cooldown window passes the circuit re-closes."""
    with md._cb_lock:
        md._cb_open_until = time.monotonic() - 1  # already elapsed

    assert not md._circuit_is_open()


def test_circuit_serves_stale_data_when_open():
    """Even with the circuit open, stale cached data is returned (never empty)."""
    # Inject stale price data
    stale_ts = time.monotonic() - (md.FRESH_TTL + 10)
    with md._cache_lock:
        md._cache["prices:META"] = ({"META": 520.0}, stale_ts)

    # Force circuit open
    with md._cb_lock:
        md._cb_open_until = time.monotonic() + 60

    with patch.object(md, "_do_fetch_prices") as mock_fetch:
        with patch.object(md, "_schedule_bg_refresh"):  # suppress bg thread
            result = md.get_prices(["META"])

    assert result == {"META": 520.0}    # stale served
    mock_fetch.assert_not_called()      # no outbound call


# ─── get_history ─────────────────────────────────────────────────────────────

def test_get_history_fresh_cache_skips_fetch():
    """Fresh history cache entry is returned without fetching."""
    df = pd.DataFrame({"Close": [100.0, 101.0]})
    md._cache_set("history:AAPL:6mo:1d", df)

    with patch.object(md, "_do_fetch_history") as mock_fetch:
        result = md.get_history("AAPL", "6mo")

    assert result is df
    mock_fetch.assert_not_called()


def test_get_history_stale_returns_immediately():
    """Stale history entry is returned immediately with background refresh scheduled."""
    df = pd.DataFrame({"Close": [99.0]})
    stale_ts = time.monotonic() - (md.FRESH_TTL + 5)
    with md._cache_lock:
        md._cache["history:AAPL:6mo:1d"] = (df, stale_ts)

    with patch.object(md, "_schedule_bg_refresh") as mock_refresh:
        with patch.object(md, "_do_fetch_history"):
            result = md.get_history("AAPL", "6mo")

    assert result is df
    mock_refresh.assert_called_once()


# ─── Bug fixes: failed refresh must not poison cache (reviewed findings) ──────

def test_failed_bg_refresh_does_not_replace_stale_prices():
    """
    When a background refresh fetch returns {} (failure), the existing stale
    cached value must be preserved — not overwritten with the empty result.
    """
    stale_ts = time.monotonic() - (md.FRESH_TTL + 10)
    with md._cache_lock:
        md._cache["prices:AAPL"] = ({"AAPL": 155.0}, stale_ts)

    # Simulate a failed fetch (returns empty dict, which _do_fetch_prices does on error)
    with patch.object(md, "_do_fetch_prices", return_value={}):
        md._fetch_coalesced("prices:AAPL", md._do_fetch_prices, ["AAPL"])

    hit = md._cache_get("prices:AAPL")
    assert hit is not None, "Cache entry must still exist after a failed refresh"
    assert hit[0] == {"AAPL": 155.0}, "Stale good value must not be overwritten by a failed fetch"


def test_failed_bg_refresh_does_not_replace_stale_history():
    """
    When a background refresh fetch returns None (failure), the existing stale
    cached DataFrame must be preserved — not overwritten with None.
    """
    df = pd.DataFrame({"Close": [99.0, 100.0]})
    stale_ts = time.monotonic() - (md.FRESH_TTL + 10)
    with md._cache_lock:
        md._cache["history:AAPL:6mo:1d"] = (df, stale_ts)

    with patch.object(md, "_do_fetch_history", return_value=None):
        md._fetch_coalesced("history:AAPL:6mo:1d", md._do_fetch_history, "AAPL", "6mo", "1d")

    hit = md._cache_get("history:AAPL:6mo:1d")
    assert hit is not None, "Cache entry must still exist after a failed history refresh"
    assert hit[0] is df, "Stale good DataFrame must not be overwritten by a None fetch result"


def test_stale_hit_does_not_schedule_refresh_when_circuit_open():
    """
    When the circuit breaker is open, a stale cache hit must return the cached
    value immediately but must NOT submit a background refresh job.
    """
    stale_ts = time.monotonic() - (md.FRESH_TTL + 10)
    with md._cache_lock:
        md._cache["prices:META"] = ({"META": 520.0}, stale_ts)
    with md._cb_lock:
        md._cb_open_until = time.monotonic() + 60

    submitted: list = []
    with patch.object(md._bg_executor, "submit", side_effect=lambda fn, *a, **kw: submitted.append(fn)):
        result = md.get_prices(["META"])

    assert result == {"META": 520.0}, "Stale value must still be served when circuit is open"
    assert len(submitted) == 0, "No background refresh must be submitted while the circuit breaker is open"


def test_cold_miss_skips_fetch_when_circuit_open():
    """
    An existing test reproduced here under the reviewed findings label:
    cold miss with an open circuit breaker returns {} without any outbound fetch.
    """
    with md._cb_lock:
        md._cb_open_until = time.monotonic() + 60

    with patch.object(md, "_do_fetch_prices") as mock_fetch:
        result = md.get_prices(["AMZN"])

    assert result == {}
    mock_fetch.assert_not_called()
    assert md._stats["circuit_open_skips"] == 1
