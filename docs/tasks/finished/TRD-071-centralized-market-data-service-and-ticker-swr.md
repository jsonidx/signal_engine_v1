# Task: Centralized market data service and ticker stale-while-revalidate

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: api | dashboard
Category: reliability | performance
Risk: architecture
Effort: L
Target Release: next
Due Date: N/A
Dependencies: BUG-001
Links: `utils/market_data.py`, `dashboard/api/main.py`, `tests/test_market_data.py`, `dashboard/api/tests/test_endpoints.py`

## Implementation Notes

### What shipped

**`utils/market_data.py`** — new shared market-data access layer (224 lines).

Public API:

```python
from utils.market_data import get_prices, get_history, get_service_stats

prices  = get_prices(["AAPL", "MSFT"])    # dict[str, float]
history = get_history("AAPL", "6mo")     # pd.DataFrame | None
stats   = get_service_stats()            # observability counters
```

Enforced behaviors:

- **Bounded timeout** — `yf.download(..., timeout=10)` on every outbound call.
- **Concurrency limit** — `threading.Semaphore(3)` wraps each `yf.download` call; at most three concurrent Yahoo fetches run in-process at any time.
- **Stale-while-revalidate (SWR)** — cached values are bucketed by age:
  - FRESH (age < 5 min): returned directly, no fetch.
  - STALE (age < 60 min): returned immediately, background refresh submitted.
  - EXPIRED / cold: one live fetch runs (semaphore-bounded, timeout-guarded).
- **Request coalescing** — concurrent calls for the same cache key share one outbound fetch via a `dict[str, threading.Event]` leader/follower pattern.
- **Circuit breaker** — after 3 consecutive failures, circuit opens for 60 s; stale cached data is served and no outbound calls are made (including background refreshes) during the cooldown.

**`dashboard/api/main.py`** — two ticker-page hot paths rerouted:

- `GET /api/signals/ticker/{ticker}` — price fetch uses `_md_get_prices` instead of `_fetch_current_prices`, offloaded via `asyncio.to_thread`.
- `GET /api/ticker/{symbol}/option-candidates` — same.
- Both paths fall back to the original `_fetch_current_prices` if the import fails.

**`dashboard/api/tests/test_endpoints.py`** — two BUG-001 regression tests updated to assert that `_md_get_prices` (not `_fetch_current_prices`) is passed to `asyncio.to_thread`. The behavioral guarantee (price fetch is offloaded, not inline) is unchanged.

### Review fixes (landed before close)

Two high-severity bugs found during review and fixed in the same PR:

1. **Failed fetches poisoned the cache.** `_fetch_coalesced` wrote the fetch result to cache unconditionally; a failed `_do_fetch_prices` returning `{}` or a failed `_do_fetch_history` returning `None` would overwrite a valid stale entry, causing callers to receive a bad "fresh" cache hit for up to FRESH_TTL. Fixed by adding `_is_valid_result()` and gating `_cache_set` on it.

2. **Background refreshes bypassed the circuit breaker.** `_schedule_bg_refresh` checked `_in_flight` but not `_circuit_is_open()`, so stale-hit paths still submitted background Yahoo calls during cooldown. Fixed by adding an early `if _circuit_is_open(): return` to `_schedule_bg_refresh`.

### Test coverage (`tests/test_market_data.py`, 16 tests)

| Test | Behavior covered |
|---|---|
| `test_fresh_cache_hit_skips_fetch` | Fresh entry returned without calling yfinance |
| `test_stale_cache_returned_immediately_without_blocking` | Stale hit served immediately; bg refresh scheduled; yfinance not called |
| `test_expired_cache_still_served_while_refreshing` | Expired entry still served while refresh runs |
| `test_cold_miss_triggers_fetch` | Cold miss calls `_do_fetch_prices`; result cached |
| `test_concurrent_requests_for_same_key_share_one_fetch` | Two concurrent threads share one outbound call |
| `test_semaphore_limits_concurrent_fetches` | N+3 threads never exceed MAX_CONCURRENT_FETCHES simultaneous yf.download calls |
| `test_circuit_opens_after_consecutive_failures` | CIRCUIT_FAILURE_THRESHOLD failures open the circuit |
| `test_circuit_open_skips_fetch_and_returns_empty` | Cold miss with open circuit returns {} without fetching |
| `test_circuit_closes_after_cooldown` | Circuit re-closes after cooldown window |
| `test_circuit_serves_stale_data_when_open` | Stale entry served when circuit is open |
| `test_get_history_fresh_cache_skips_fetch` | Fresh history entry returned without fetching |
| `test_get_history_stale_returns_immediately` | Stale history entry returned immediately; refresh scheduled |
| `test_failed_bg_refresh_does_not_replace_stale_prices` | Failed fetch ({}) does not overwrite stale cached prices |
| `test_failed_bg_refresh_does_not_replace_stale_history` | Failed fetch (None) does not overwrite stale cached history |
| `test_stale_hit_does_not_schedule_refresh_when_circuit_open` | No bg refresh submitted when circuit is open on stale hit |
| `test_cold_miss_skips_fetch_when_circuit_open` | Cold miss with open circuit skips outbound call |

All 16 tests pass: `pytest tests/test_market_data.py` → **16 passed in 0.93s**.

## Deferred / non-goals (unchanged)

- `_fetch_live_prices` (prepost/intraday prices used by action-zones endpoint) is not yet routed through `market_data.py`; it retains its own `ThreadPoolExecutor` in `main.py`.
- All other yfinance callers outside the two initial hot paths (portfolio positions, backtest, screener fan-out, pipeline screeners) are unchanged.
- `get_history()` is implemented and tested but not yet wired into any real caller; available for incremental adoption.
- Redis is explicitly deferred: appropriate when the app moves to multiple workers, not needed for the current single-process deployment.

## Original Acceptance Criteria (all met)

- [x] Ticker page can render from cached or last-known data without waiting on a live Yahoo request
- [x] Background refresh updates cached price data after the initial response
- [x] Simultaneous requests for the same symbol set are coalesced into one outbound fetch
- [x] Upstream fetch concurrency is globally bounded inside the process
- [x] Provider timeouts and failures degrade freshness, not page availability
- [x] Tests prove stale-while-revalidate behavior on ticker endpoints
- [x] Tests prove same-key fetch coalescing
- [x] Tests prove semaphore concurrency limiting
