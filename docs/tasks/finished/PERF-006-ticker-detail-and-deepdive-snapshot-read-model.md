# Task: Ticker Detail and Deep Dive Snapshot Read Model

Status: done
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: dashboard
Category: performance
Risk: api
Effort: L
Target Release: next
Due Date: TBD
Dependencies: TRD-082, TRD-080
Blocked By: none
Links: `dashboard/api/main.py`, `ai_quant.py`, `run_master.sh`, `dashboard/frontend/src/pages/TickerPage.tsx`, `dashboard/frontend/src/pages/DeepDivePage.tsx`
Success Metric: ticker-page and deep-dive base payloads load from persisted snapshots, cutting cold-load request-time assembly and live enrichment work materially.

## Problem Statement

The highest-value dashboard reads still do too much work inside GET handlers.

- `GET /api/signals/ticker/{ticker}` assembles a large detail payload by combining `thesis_cache` with live price fetches, max pain, ADV, catalyst joins, model consensus, and other lookups.
- `GET /api/deepdive/tickers` rebuilds a broad list payload and only caches it in-process with stale-while-revalidate.
- `GET /api/deepdive/live-zones` and related deep-dive reads still depend on request-time assembly patterns.

This keeps critical pages slower than they need to be and ties page latency to live enrichment paths that are not necessary for most dashboard use.

## User Impact

- Ticker pages can feel slow or inconsistent despite most thesis data being stable until the next AI run.
- Deep Dive list performance depends on request-time rebuilds rather than producer-time generation.
- The same expensive enrichments are repeated across requests even when the underlying thesis has not changed.

## Objective

Move ticker-detail base payloads and deep-dive list payloads to snapshot-backed delivery, with selective live overlays only where they materially improve the experience.

## Proposed Solution

- Create producer-side snapshot writers for:
  - per-ticker detail base payload
  - deep-dive ticker list payload
  - optional hot-entry snapshot if needed as part of the same producer chain
- Trigger ticker snapshot refresh after `ai_quant.py` saves a thesis, after stale-thesis refresh runs, and after pipeline steps that materially change dependent sections.
- Make `signals/ticker/{ticker}` read a prebuilt base snapshot and optionally layer a small live overlay if required.
- Make `deepdive/tickers` read a persisted snapshot instead of rebuilding on the request path.
- Keep explicitly live endpoints separate and small.

## Scope

Files or modules likely affected:

- `dashboard/api/main.py`
- `ai_quant.py`
- `utils/supabase_persist.py`
- `run_master.sh`
- `dashboard/api/tests/test_endpoints.py`
- `dashboard/frontend/src/pages/TickerPage.tsx`
- `dashboard/frontend/src/pages/DeepDivePage.tsx`

## Non-Goals

- Do not redesign the ticker page UI.
- Do not remove explicit live endpoints such as analyze status.
- Do not change option-chain scoring logic.
- Do not build a websocket or streaming model.

## Constraints

- Base snapshot should contain stable or near-stable sections only.
- Live overlays must be small and optional, not the default page-load path.
- Snapshot refreshes should be tied to producer events:
  - AI quant run
  - stale-thesis refresh
  - relevant pipeline data refresh
- Avoid GET-handler writes for the new snapshot path.

## Acceptance Criteria

- Observable behavior:
  - `signals/ticker/{ticker}` reads a persisted base snapshot for core payload fields
  - `deepdive/tickers` reads a persisted snapshot instead of rebuilding the full payload on request
  - both responses expose explicit freshness metadata
- Tests:
  - backend tests cover snapshot-backed read behavior and fallback when snapshot missing
- Documentation:
  - implementation notes document which fields are snapshot-backed vs still live

## Verification Plan

- `pytest -q dashboard/api/tests/test_endpoints.py`
- targeted manual smoke on ticker page and deep-dive page after generating fresh snapshots

## QA Notes

- Test scenarios: snapshot present, snapshot absent, AI rerun updates ticker snapshot, stale-thesis refresh updates dependent payloads
- Edge cases: ticker with no thesis, stale snapshot with missing overlay data, DB unavailable fallback
- Regression risks: missing fields in ticker payload, deep-dive list drift, stale UI without visible freshness indicators

## Launch / Release Notes

- User-facing change summary: ticker and deep-dive pages serve precomputed base payloads more consistently and expose clearer freshness.
- Operational notes: snapshot generation is tied to AI and pipeline producer events rather than page traffic.
- Rollback notes: revert individual endpoints to legacy compute paths if snapshot production fails.

## Post-Launch Validation

- What to monitor: ticker endpoint latency, deep-dive endpoint latency, snapshot write failures, snapshot age after AI runs
- How success will be confirmed: base page payloads no longer require full request-time assembly on cold loads
- Follow-up decision date: after observing one full daily pipeline cycle and one manual AI rerun

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement PERF-006: Ticker Detail and Deep Dive Snapshot Read Model.

Goal:
- move the base ticker-detail and deep-dive payloads to producer-time snapshots so page loads read persisted data instead of rebuilding large payloads on demand

Scope:
- dashboard/api/main.py
- ai_quant.py
- utils/supabase_persist.py
- run_master.sh
- dashboard/api/tests/test_endpoints.py
- dashboard/frontend/src/pages/TickerPage.tsx
- dashboard/frontend/src/pages/DeepDivePage.tsx

Requirements:
1. Add producer-side snapshot generation for:
   - per-ticker detail base payload
   - deep-dive ticker list payload
2. Trigger ticker snapshot refresh after thesis writes in `ai_quant.py` and after stale-thesis refresh paths where practical.
3. Change `GET /api/signals/ticker/{ticker}` to prefer a persisted base snapshot and only layer small live data if necessary.
4. Change `GET /api/deepdive/tickers` to read a persisted snapshot rather than rebuilding the full list on request.
5. Return explicit freshness metadata from both endpoints.

Non-goals:
- no UI redesign
- no websocket/streaming work
- no trading-logic changes
- no option-chain logic changes

Constraints:
- keep live overlays small and optional
- do not write snapshots from GET handlers
- preserve response compatibility where practical

Tests:
- pytest -q dashboard/api/tests/test_endpoints.py
- add targeted coverage for snapshot present/missing behavior
```
