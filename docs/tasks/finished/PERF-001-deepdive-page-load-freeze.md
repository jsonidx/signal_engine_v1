# Task: Fix Deep Dive page freeze on cache-cold load

Status: done
Stage: done
Type: bug
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Codex
Product Area: dashboard | api
Category: performance | reliability
Risk: api
Effort: S
Target Release: next
Due Date: 2026-05-30
Dependencies: none
Blocked By: none
Links: none
Success Metric: `GET /api/deepdive/tickers` returns usable data in under 2s on a cold cache and never blocks longer than 25s in the live-price fetch path.

## Problem Statement

The Deep Dive page freezes when `GET /api/deepdive/tickers` is served from a cold cache. In `dashboard/api/main.py`, `deepdive_tickers()` misses `_cache`, builds the full ticker payload, and then waits for `_fetch_live_prices()` before returning. `_fetch_live_prices()` calls `yf.download()` across the full universe, which blocks the response for 17-30s or longer on a miss.

Current flow:

- `deepdive_tickers()` checks `_cache.get("deepdive_tickers")` at [dashboard/api/main.py](/Users/jason/signal_engine_v1/dashboard/api/main.py:1596).
- On miss, it builds the list and calls `_fetch_live_prices()` via `run_in_executor` at [dashboard/api/main.py](/Users/jason/signal_engine_v1/dashboard/api/main.py:1748).
- `_fetch_live_prices()` calls `yf.download(...)` for all tickers at [dashboard/api/main.py](/Users/jason/signal_engine_v1/dashboard/api/main.py:323).
- The final payload is not cached until after that blocking fetch completes at [dashboard/api/main.py](/Users/jason/signal_engine_v1/dashboard/api/main.py:1763).

Because the primary cache TTL is 5 minutes, this freeze recurs on every cold request after expiry.

## User Impact

Users hit a blank loading state on `/deepdive` for 17-30s or more every time the 5-minute cache expires. If yfinance stalls instead of merely being slow, the page can appear frozen indefinitely because the endpoint waits on the bulk live-price call before returning any rows.

## Objective

Return Deep Dive ticker rows immediately from the last known payload when the primary cache is cold, while refreshing live prices in the background and hard-limiting the yfinance wait time.

## Proposed Solution

Implement a stale-while-revalidate path inside `deepdive_tickers()` and add a timeout guard inside `_fetch_live_prices()`.

- Keep the existing hot cache key, `deepdive_tickers`, for the normal 5-minute response.
- Add a second stale cache key, such as `deepdive_tickers:stale`, with a longer TTL that stores the last successful full payload.
- On a primary-cache miss, return the stale payload immediately if present and trigger one background refresh task to rebuild the fresh payload.
- If no stale payload exists yet, keep the current synchronous build path so the endpoint can populate both caches on first success.
- Wrap the underlying `yf.download()` call in a `concurrent.futures` timeout so `_fetch_live_prices()` returns `{}` after 25s instead of hanging indefinitely.

## Scope

Files or modules likely affected:

- `dashboard/api/main.py`

Functions in scope:

- `deepdive_tickers()` at [dashboard/api/main.py](/Users/jason/signal_engine_v1/dashboard/api/main.py:1593)
- `_fetch_live_prices()` at [dashboard/api/main.py](/Users/jason/signal_engine_v1/dashboard/api/main.py:308)

## Non-Goals

- Do not change the data provider.
- Do not change frontend query behavior.
- Do not refactor the cache implementation.
- Do not change database schema, trading logic, or unrelated endpoints.
- Do not expand scope beyond `dashboard/api/main.py` unless required for the fix to compile.

## Constraints

- Keep the implementation limited to `deepdive_tickers()` and `_fetch_live_prices()`.
- `_fetch_live_prices()` is reused by other endpoints, so its interface must remain compatible.
- The background refresh path must avoid launching duplicate refreshes concurrently.
- No secrets or generated artifacts in git.

## Acceptance Criteria

- Observable behavior: when `deepdive_tickers` is cold but stale data exists, `GET /api/deepdive/tickers` returns immediately with the last known payload and a `stale: true` marker.
- Observable behavior: a background refresh rebuilds the fresh payload and repopulates both the hot cache and stale cache.
- Observable behavior: on first-ever load with no stale payload, the endpoint still completes successfully and seeds both caches.
- Observable behavior: `_fetch_live_prices()` returns within 25s even if yfinance stalls.
- Tests: targeted manual verification confirms cold-cache and warm-cache behavior; add automated tests only if they can be done cheaply without widening scope.
- Documentation: this ticket contains the implementation prompt and verification script.

## Verification Plan

- Start the API locally: `cd dashboard/api && uvicorn main:app --port 8000`
- Warm the endpoint once: `curl -s http://localhost:8000/api/deepdive/tickers > /dev/null`
- Clear in-memory cache: `curl -s -X POST http://localhost:8000/api/cache/invalidate`
- Cold request check: `time curl -s --max-time 3 http://localhost:8000/api/deepdive/tickers | python3 -c "import sys,json; d=json.load(sys.stdin); print('stale=', d.get('stale'), 'count=', d['count'])"`
- Warm request check: `time curl -s http://localhost:8000/api/deepdive/tickers > /dev/null`

## QA Notes

- Test scenarios: cold cache with stale payload present, cold cache with no stale payload yet, warm cache hit, yfinance timeout path, and yfinance exception path.
- Edge cases: background refresh already running, empty live-price result from timeout, and fallback close-price enrichment when live prices are unavailable.
- Regression risks: other callers of `_fetch_live_prices()` now receive `{}` on timeout instead of waiting indefinitely; verify this only changes latency, not schema.

## Launch / Release Notes

- User-facing change summary: Deep Dive loads immediately after cache expiry using last known data while fresh prices refresh in the background.
- Operational notes: timeout warnings from `_fetch_live_prices()` become expected diagnostic signals instead of long hangs.
- Rollback notes: revert the stale-cache branch and timeout wrapper to restore the old blocking behavior.

## Post-Launch Validation

- What to monitor: API latency for `GET /api/deepdive/tickers` and warning frequency from live-price fetch timeouts.
- How success will be confirmed: cold-cache requests return quickly and the page no longer freezes on the first load after cache expiry.
- Follow-up decision date: after the first production deploy using the stale-while-revalidate path.

## Handoff Notes

### Claude implementation prompt

```text
Task: Fix Deep Dive page freeze on cache-cold load.

Goal:
- `GET /api/deepdive/tickers` should return immediately on a cold primary cache by serving the last known payload and refreshing in the background.
- `_fetch_live_prices()` must never block indefinitely; cap the underlying yfinance wait at 25 seconds.

Scope:
- Only edit `dashboard/api/main.py`.
- Keep the implementation limited to:
  - `deepdive_tickers()` around lines 1593-1764
  - `_fetch_live_prices()` around lines 308-341

Implementation requirements:
1. In `deepdive_tickers()`, keep the existing hot cache key `deepdive_tickers`.
2. Add a second stale cache key, for example `deepdive_tickers:stale`, with a longer TTL.
3. On hot-cache miss:
   - if stale payload exists, return it immediately with `stale: true`
   - trigger one background refresh to rebuild the fresh payload
   - prevent duplicate concurrent refreshes with a small module-level guard such as `asyncio.Lock` or an equivalent boolean/flag pattern
4. When a fresh payload is successfully built, write it to both:
   - `deepdive_tickers` with the existing 5-minute TTL
   - `deepdive_tickers:stale` with a longer TTL so a stale payload is available on the next miss
5. If there is no stale payload yet, preserve the current synchronous build path so the first request can seed both caches.
6. In `_fetch_live_prices()`, wrap the blocking `yf.download()` call in a `concurrent.futures` timeout pattern:
   - submit the actual download call to an executor
   - read the result with `.result(timeout=25)`
   - on timeout, log a warning and return `{}`
   - keep the existing return shape and downstream behavior compatible for other callers

Suggested line anchors:
- Hot cache check: `dashboard/api/main.py:1596-1599`
- Blocking live-price call from endpoint: `dashboard/api/main.py:1744-1749`
- Final result cache write: `dashboard/api/main.py:1762-1763`
- yfinance bulk download: `dashboard/api/main.py:321-340`

Non-goals:
- Do not change the provider.
- Do not refactor the cache class.
- Do not modify frontend code.
- Do not change database schema, trading logic, or unrelated endpoints.

Verification:
1. `cd dashboard/api && uvicorn main:app --port 8000`
2. `curl -s http://localhost:8000/api/deepdive/tickers > /dev/null`
3. `curl -s -X POST http://localhost:8000/api/cache/invalidate`
4. `time curl -s --max-time 3 http://localhost:8000/api/deepdive/tickers | python3 -c "import sys,json; d=json.load(sys.stdin); print('stale=', d.get('stale'), 'count=', d['count'])"`
5. `time curl -s http://localhost:8000/api/deepdive/tickers > /dev/null`

Expected result:
- Step 4 returns in under 3 seconds and prints a valid payload; if stale data was seeded earlier it should include `stale=True`.
- Step 5 returns quickly from the refreshed hot cache.
```

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
