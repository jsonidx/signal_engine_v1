# Task: Fix ticker page infinite loading during concurrent LLM jobs

Status: completed
Stage: done
Type: bug
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Codex
Product Area: dashboard | api
Category: reliability | performance | ux
Risk: api
Effort: S
Target Release: next
Due Date: 2026-06-12
Dependencies: none
Blocked By: none
Links: `dashboard/api/main.py`, `dashboard/frontend/src/hooks/useHeatmap.ts`, `dashboard/frontend/src/lib/api.ts`, `dashboard/frontend/src/lib/queryClient.ts`, `dashboard/frontend/src/pages/TickerPage.tsx`
Success Metric: Opening `/ticker/:symbol` during 6 concurrent LLM jobs returns either usable content or an explicit error state within 15 seconds; the API event loop stays responsive while price fetches are in flight.

## Implementation Notes

### Root cause

`_fetch_current_prices()` called `yf.download(...)` synchronously inside two `async def` FastAPI endpoints:
- `signals_ticker()` at `dashboard/api/main.py` (around line 1429)
- `ticker_option_candidates()` at `dashboard/api/main.py` (around line 6680)

Both calls blocked the FastAPI event loop. Under concurrent LLM jobs, the same host was already issuing multiple yfinance requests, so rate limiting or slow upstream responses stretched the block long enough for the frontend to appear frozen indefinitely.

### What shipped (via TRD-071)

This bug was closed as part of the TRD-071 centralized market-data service implementation.

**Backend fix** — both blocking hot paths replaced:

- `GET /api/signals/ticker/{ticker}` — now uses `await asyncio.to_thread(_md_get_prices, [ticker])` via `utils/market_data.py`
- `GET /api/ticker/{symbol}/option-candidates` — same pattern

Both paths fall back to the original `_fetch_current_prices` if the `utils.market_data` import fails.

**Frontend hardening (TickerPage.tsx)**:
- `isError` from `useSignalsTicker()` now drives an explicit error state on the main ticker query
- axios timeout set in `dashboard/frontend/src/lib/api.ts` to bound client-side wait

**`utils/market_data.py`** enforces additional guards for all callers:
- `yf.download(..., timeout=10)` on every outbound call
- `threading.Semaphore(3)` caps concurrent Yahoo fetches process-wide
- SWR: stale prices served immediately while background refresh runs
- Request coalescing: concurrent fetches for the same symbol set share one outbound call
- Circuit breaker: after 3 consecutive failures, skips outbound calls for 60 s

### Test coverage

Two regression tests in `dashboard/api/tests/test_endpoints.py` (line 642):

| Test | Assertion |
|---|---|
| `test_signals_ticker_price_fetch_uses_to_thread` | `signals_ticker` passes `_md_get_prices` to `asyncio.to_thread`, not inline |
| `test_option_candidates_price_fetch_uses_to_thread` | `ticker_option_candidates` does the same |

Frontend error-state test in `TickerPage.option-candidates.test.tsx` (line 1024) confirms the page renders an explicit error banner when `signalsTicker` rejects.

All 16 `tests/test_market_data.py` tests pass, covering SWR, coalescing, semaphore, and circuit breaker behavior.

### Review fixes (landed in same PR as TRD-071)

Two high-severity bugs found during review and fixed before close:

1. **Cache poisoning**: failed fetches (`{}` / `None`) unconditionally overwrote valid stale cache entries. Fixed by `_is_valid_result()` guard in `_fetch_coalesced`.
2. **Circuit breaker bypass for background refreshes**: `_schedule_bg_refresh` did not call `_circuit_is_open()` before submitting, so background Yahoo calls still fired during cooldown. Fixed by adding early `if _circuit_is_open(): return`.

## Acceptance Criteria (all met)

- [x] `signals_ticker()` and `ticker_option_candidates()` no longer call blocking yfinance code directly on the event loop
- [x] Backend timeout (`timeout=10`) prevents a slow Yahoo response from hanging a worker thread indefinitely
- [x] Frontend renders an explicit error state instead of an endless skeleton when `signalsTicker` fails
- [x] `axios` timeout set; frontend stops waiting after bounded duration
- [x] `staleTime` raised in `useSignalsTicker()` to reduce cold-fetch frequency on repeat visits
- [x] Backend coverage verifies offload via `asyncio.to_thread`
- [x] Frontend coverage verifies error-state render when `signalsTicker` rejects
