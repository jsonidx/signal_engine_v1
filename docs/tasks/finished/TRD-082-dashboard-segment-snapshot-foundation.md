# Task: Dashboard Segment Snapshot Foundation

Status: done
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: api
Category: performance
Risk: api
Effort: M
Target Release: next
Due Date: TBD
Dependencies: TRD-080, TRD-059
Blocked By: none
Links: `dashboard/api/main.py`, `utils/supabase_persist.py`, `run_master.sh`, `docs/tasks/finished/TRD-080-options-screener-snapshot-architecture.md`
Success Metric: at least three major dashboard payloads are served from persisted segment snapshots instead of request-time assembly, and each response exposes explicit snapshot freshness metadata.

## Problem Statement

The dashboard currently uses three inconsistent read models:

- persisted snapshots for a few surfaces such as options screener
- raw artifact reads from CSV/JSON files
- request-time assembly with in-process TTL caching

This inconsistency keeps page latency coupled to request-time compute and makes freshness behavior hard to reason about. The API already has evidence that persisted snapshots work well, but there is no general snapshot abstraction for broader dashboard segments.

## User Impact

- Cold page loads remain slower and less predictable than necessary.
- Dashboard freshness semantics vary by endpoint and are not consistently visible to users.
- Snapshot-worthy payloads are rebuilt in the API process instead of being produced by the pipeline that already owns most data generation.

## Objective

Create a reusable persisted segment-snapshot foundation in Supabase/Postgres so the API can read precomputed dashboard payloads by segment key, return freshness metadata consistently, and extend the model incrementally to additional pages.

## Proposed Solution

- Add a generic persisted snapshot table for dashboard segments.
- Add read/write helpers in `utils/supabase_persist.py`.
- Standardize snapshot envelope fields such as `segment`, `snapshot_key`, `created_at`, `stale_after`, `version`, `source_step`, and `payload_json`.
- Wire the first producer calls into the existing pipeline or post-run jobs rather than GET handlers.
- Add a lightweight API helper pattern so endpoints can read snapshot-backed payloads consistently and fall back gracefully when missing.

## Scope

Files or modules likely affected:

- `utils/supabase_persist.py`
- `dashboard/api/main.py`
- `run_master.sh`
- `migrations/`
- `dashboard/api/tests/test_endpoints.py`
- `tests/test_supabase_integration.py`

## Non-Goals

- Do not migrate every dashboard endpoint in this ticket.
- Do not redesign portfolio or trading logic.
- Do not introduce Redis, Celery, APScheduler, or streaming infrastructure.
- Do not remove the existing in-memory `_cache`; it can remain as L1.

## Constraints

- Use Supabase/Postgres as the persisted snapshot store.
- Keep the design modular; do not create one giant dashboard blob.
- Preserve existing endpoint response shapes where practical, adding snapshot metadata rather than breaking consumers.
- Producer-side writes must happen in background jobs, pipeline steps, or explicit post-run hooks, not on GET handlers.

## Acceptance Criteria

- Observable behavior:
  - a generic dashboard segment snapshot table exists with lookup by segment and key
  - at least one API helper path can read a snapshot and return payload plus freshness metadata
  - snapshot-backed responses expose `snapshot_time` or equivalent explicit freshness fields
- Tests:
  - backend tests cover snapshot write/read helpers and at least one API read path
- Documentation:
  - migration and operational notes describe how segment snapshots are produced and consumed

## Verification Plan

- `pytest -q dashboard/api/tests/test_endpoints.py`
- `pytest -q tests/test_supabase_integration.py`
- targeted manual smoke against one snapshot-backed endpoint after seeding a snapshot row

## QA Notes

- Test scenarios: snapshot exists, snapshot missing, stale snapshot, malformed payload JSON
- Edge cases: multiple snapshots for same segment, fallback when DB unavailable, additive metadata compatibility
- Regression risks: endpoint shape drift, stale data served without freshness label, producer/consumer key mismatch

## Launch / Release Notes

- User-facing change summary: snapshot freshness becomes an explicit first-class API concept for dashboard pages.
- Operational notes: snapshot data is produced by existing pipeline/post-run flows and read cheaply by the API.
- Rollback notes: endpoints can fall back to legacy compute paths while leaving the generic snapshot table unused.

## Post-Launch Validation

- What to monitor: endpoint latency, snapshot age, snapshot write success rate, snapshot miss rate
- How success will be confirmed: snapshot-backed pages return from stored payloads instead of rebuilding equivalent data on demand
- Follow-up decision date: after the first two downstream adoption tickets ship

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-082: Dashboard Segment Snapshot Foundation.

Goal:
- add a reusable persisted snapshot foundation for dashboard payload segments so additional pages can serve precomputed data with explicit freshness metadata

Scope:
- utils/supabase_persist.py
- dashboard/api/main.py
- run_master.sh
- migrations/
- dashboard/api/tests/test_endpoints.py
- tests/test_supabase_integration.py

Requirements:
1. Add a generic snapshot table in Postgres/Supabase for dashboard segments.
   Suggested columns: segment, snapshot_key, run_date, source_step, payload_json, created_at, stale_after, version, meta_json.
2. Add persistence helpers in `utils/supabase_persist.py` to upsert and fetch snapshots by `(segment, snapshot_key)`.
3. Add a minimal API helper pattern in `dashboard/api/main.py` for reading snapshot-backed payloads and returning explicit freshness metadata.
4. Keep the existing in-memory `_cache` as L1, but make persisted snapshots the source of truth for adopted segments.
5. Do not move every endpoint yet; just build the shared foundation and wire one narrow API proof path if useful.

Non-goals:
- no Redis
- no Celery / APScheduler
- no giant all-dashboard blob
- no portfolio or trading-logic redesign
- no GET-handler side-effect writes

Tests:
- pytest -q dashboard/api/tests/test_endpoints.py
- pytest -q tests/test_supabase_integration.py

Constraints:
- preserve existing endpoint response shapes where practical
- add explicit freshness metadata rather than hiding snapshot age
- keep the design modular so later tickets can adopt it incrementally
```
