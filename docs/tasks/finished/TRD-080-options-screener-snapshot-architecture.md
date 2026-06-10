# Task: Options Screener Snapshot Architecture

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: dashboard
Category: options
Risk: architecture
Effort: M
Target Release: options-stack-v2
Due Date: TBD
Dependencies: TRD-028, TRD-054
Blocked By: none
Links: `dashboard/api/main.py`, `dashboard/frontend/src/pages/OptionsPage.tsx`, `dashboard/frontend/src/lib/api.ts`, `utils/option_candidates.py`, `utils/ibkr_options.py`
Success Metric: `/options` page loads in < 500ms from a precomputed snapshot; live IBKR fan-out is no longer triggered by a page load; partial-result banner is replaced by an "as of N min ago" timestamp; a Refresh button triggers a non-blocking background re-run.

## Problem Statement

The current `/api/options/screener` endpoint fans out across all thesis-filtered
tickers on every page load, calling `get_option_chain()` per ticker.  With IBKR
active (`IBKR_PORT=4002`), this architecture has structural performance
problems that cannot be resolved by tuning timeouts:

- IBKR's ib_insync client is synchronous and requires `sleep(2.5s)` per batch
  of 50 market-data requests (`ibkr_options.py:350`).
- With default params (4 expiries × 16 strikes × 2 rights = 128 contracts),
  each ticker takes **15–25s**: 7.5s sleep + connect overhead + qualification.
- To avoid IBKR client-id conflicts, the screener uses `max_workers=1` (serial).
- The global `concurrent.futures.wait()` timeout is 12s, which is always
  exceeded by a single IBKR ticker fetch — so the screener returns 0–1 results.
- When the timeout fires on a running future, `f.cancel()` is a no-op on the
  active thread; the IBKR connection continues in the background as a zombie.
- The yfinance fallback (6 workers) is faster but still borderline at 12s for
  8 tickers with 4 expiry chains each.

The `partial` result banner and `timed_out_tickers` field are defensive
mitigations added to handle this, but they are not the final architecture.

## Objective

Replace the synchronous live-fetch screener with a precomputed snapshot model:

- The screener computation runs as a **background job** (pipeline step or
  scheduled cron), not on page load.
- The API reads from a persisted snapshot table — a trivial DB query.
- The `/options` page loads instantly and shows an "as of N minutes ago"
  timestamp.
- A manual "Refresh" button triggers a background re-run without blocking the
  UI.
- `buy_decision` is surfaced in the screener table alongside the existing
  score/rationale columns (closing the gap noted in TRD-051).
- Universe pre-filtering excludes tickers unlikely to have viable options
  (e.g., ADV < 500k, market cap < $500M) before the chain fetch loop.

## Proposed Solution

### 1. Snapshot table

Create `options_screener_snapshot` in Supabase:

```sql
CREATE TABLE options_screener_snapshot (
    id              BIGSERIAL PRIMARY KEY,
    run_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    min_conviction  INT NOT NULL DEFAULT 2,
    tickers_evaluated INT,
    tickers_completed INT,
    partial         BOOLEAN DEFAULT FALSE,
    timed_out_tickers TEXT[],
    count           INT NOT NULL DEFAULT 0,
    data            JSONB NOT NULL DEFAULT '[]'
);
CREATE INDEX ON options_screener_snapshot (min_conviction, run_at DESC);
```

### 2. Background screener runner

Extract the current screener fan-out logic from `options_screener()` in
`main.py` into a standalone function `run_options_screener_job(min_conviction,
max_tickers)` that writes to `options_screener_snapshot`.

Increase the per-run IBKR timeout to **90s** (not the response timeout —
there is no request waiting) and raise `max_workers` to `1` when IBKR is
active (unchanged), but remove the 12s artificial cap.

Add a `run_options_screener_job()` call to the pipeline (after step 13c in
`run_master.sh`) so the snapshot is refreshed each daily run.

### 3. Updated API endpoint

Change `GET /api/options/screener` to:

1. Read the most recent row from `options_screener_snapshot` for the requested
   `min_conviction`.
2. Return the stored `data` JSON plus `run_at` as `snapshot_time`.
3. Cache the response 5 minutes (reduced from 15 min, since the data is
   already precomputed — the DB query is cheap).
4. If no snapshot exists: return `data_available: false` with a helpful
   message.

Add `POST /api/options/screener/refresh` that:
- Triggers `run_options_screener_job()` in a background thread (fire-and-forget).
- Returns `{"queued": true, "message": "Screener refresh queued. Check back in ~60s."}`.
- Rate-limits to one background run at a time (check if a run is already active).

### 4. Frontend changes

In `OptionsPage.tsx` / `ScreenerPanel`:
- Replace the partial-result banner with an "as of {N} min ago" label derived
  from `snapshot_time`.
- Rename "Refresh" button to "Re-run screener" and point it to the POST
  endpoint; show a brief "Refresh queued — check back in ~60s" toast.
- Add a `Buy` column to the screener table (`ScreenerRow`) that renders
  `buy_decision` as a compact badge: `BUY NOW` (green) / `WAIT` (grey).
- Show `timed_out_tickers` as a small detail line if `partial: true` in the
  snapshot (informational; not a red-warning path anymore).

### 5. Universe pre-filter

In `_fetch_screener_tickers`, add an ADV / market-cap floor join against
`daily_rankings` or `screener_signals` to skip tickers with:
- `adv_20d < 500_000` (insufficient options liquidity)
- or known non-optionable status

This reduces the chain-fetch universe to liquid names only.

## Non-Goals

- Do not remove the per-ticker `/api/ticker/{sym}/option-candidates` endpoint.
  That live path remains for the deep-dive page.
- Do not change the IBKR chain fetch logic in `ibkr_options.py`.
- Do not add automated order placement.
- Do not change the yfinance fallback path.
- Do not precompute per-minute or real-time snapshots — daily pipeline
  cadence is sufficient.

## Acceptance Criteria

- `GET /api/options/screener` returns in < 200ms (DB read + in-process cache).
- No IBKR connection is opened on page load.
- Page shows "as of {run_at}" timestamp in the Screener panel.
- "Re-run screener" button triggers background job without blocking the UI.
- Screener table shows `buy_decision` badge per row.
- Snapshot is written by the daily pipeline run.
- If no snapshot exists, the page shows an `EmptyState` message explaining
  that the pipeline must run first.
- `partial` flag and `timed_out_tickers` are preserved in the snapshot record
  for observability.
- Universe pre-filter excludes ADV < 500k tickers before the chain-fetch loop.

## Verification Plan

- Unit test: `GET /api/options/screener` with a mock snapshot row returns
  instantly with the stored data.
- Unit test: `POST /api/options/screener/refresh` queues a background job and
  is rate-limited correctly.
- Unit test: `_fetch_screener_tickers` with an ADV filter excludes illiquid
  tickers.
- Integration smoke: run `run_options_screener_job()` locally against dev DB
  with yfinance (not IBKR) and confirm a snapshot row is written.
- Frontend: confirm "as of" timestamp renders correctly, "Re-run screener"
  button is reachable, `buy_decision` badge appears in the table.
- Manual: `/options` page load time < 500ms after first snapshot written.

## Constraints

- The background job must not block the FastAPI event loop.  Use
  `asyncio.get_event_loop().run_in_executor(None, ...)` as already used for
  screener persistence.
- The snapshot write must be idempotent (upsert or insert-only; multiple runs
  per day are fine).
- Do not introduce a new scheduler dependency (no APScheduler, Celery, etc.).
  The pipeline cron and the manual refresh button are sufficient.

## Migration

No schema migration is required for the existing `options_screener_snapshot`
if it does not yet exist (new table).  The existing in-process `_cache` key
`"options_screener:{min_conviction}:{max_tickers}"` can be removed once the
snapshot path is live.

## Launch / Release Notes

- User-facing: `/options` screener no longer has a spinner or partial-result
  warning on load.  Shows "as of {time}" label and a manual refresh button.
- Operational: screener runs once daily in the pipeline.  A manual re-run
  takes ~60–90s with IBKR active; yfinance re-run takes ~30–40s.
- Rollback: revert `GET /api/options/screener` to the live-fetch path;
  snapshot table can remain unused.

## Handoff Notes

Key files to change:
- `dashboard/api/main.py` — `options_screener()`, new `options_screener_refresh()`, new `run_options_screener_job()`
- `dashboard/frontend/src/pages/OptionsPage.tsx` — `ScreenerPanel`, `ScreenerRow`
- `dashboard/frontend/src/lib/api.ts` — `OptionsScreenerResponse` (add `snapshot_time`), new `optionsScreenerRefresh()`
- `run_master.sh` — add `run_options_screener_job` call after step 13c
- `migrations/` — add SQL for `options_screener_snapshot` table

Paste-ready Claude implementation prompt:

Implement TRD-080, "Options Screener Snapshot Architecture," in this repo.

Goal:
- Replace the synchronous live-fetch `/api/options/screener` with a snapshot
  model: precomputed daily, instant read on page load, manual refresh button.

Key changes:
1. Add `options_screener_snapshot` table (migration SQL).
2. Extract screener fan-out logic into `run_options_screener_job()` that
   writes to the new table.  Remove the 12s response timeout; no HTTP request
   is waiting.
3. Change `GET /api/options/screener` to read from the snapshot table.
4. Add `POST /api/options/screener/refresh` for manual background re-runs.
5. Add universe pre-filter (ADV > 500k) in `_fetch_screener_tickers`.
6. Update `ScreenerPanel` / `ScreenerRow` in `OptionsPage.tsx`:
   - "as of {run_at}" timestamp instead of partial-result banner.
   - `buy_decision` badge column in the screener table.
   - "Re-run screener" button → POST refresh endpoint.
7. Wire `run_options_screener_job()` into `run_master.sh` after step 13c.
8. Add tests: snapshot read endpoint, refresh rate-limit, ADV pre-filter.

Do not change `utils/ibkr_options.py` or per-ticker chain fetch logic.
