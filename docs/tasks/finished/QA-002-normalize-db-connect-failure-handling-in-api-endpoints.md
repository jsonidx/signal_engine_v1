# Task: Normalize DB connect failure handling in API endpoints

Status: completed
Stage: done
Type: bug
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Codex
Product Area: api
Category: reliability
Risk: api
Effort: M
Target Release: next
Due Date: 2026-06-13
Dependencies: none
Blocked By: none
Links: `dashboard/api/main.py`, `dashboard/api/tests/test_endpoints.py`
Success Metric: endpoints that currently call `_db_connect()` outside a protective `try/except` no longer fail with unhandled 500s when the database is unavailable; they return controlled no-data or service-unavailable responses consistent with each endpoint’s contract.

## Problem Statement

The CRDO ticker-page bug exposed a broader API reliability issue: `_db_connect()` now raises on failure, but several endpoints in `dashboard/api/main.py` still contain legacy patterns that assumed it could return `None`.

Typical broken pattern:

```python
conn = _db_connect()
if conn is None:
    return _no_data(...)
```

Because `_db_connect()` raises instead of returning `None`, those guards are dead code. Any endpoint that performs a bare `_db_connect()` outside a `try/except` can return an unhandled 500 when `DATABASE_URL` is unset or PostgreSQL is unavailable.

The CRDO fix correctly handled `signals_ticker()`, but the same bug class still exists at multiple other call sites.

## User Impact

During database misconfiguration, restarts, or transient Postgres outages, users can see hard failures on unrelated dashboard pages instead of controlled empty/error states. The result is inconsistent behavior across the API and opaque frontend failures.

## Objective

Audit the remaining `_db_connect()` call sites in `dashboard/api/main.py` and normalize failure handling so database connection errors are converted into deliberate endpoint responses rather than uncaught 500s.

## Proposed Solution

Perform a targeted audit of `_db_connect()` usage in `dashboard/api/main.py`.

For each endpoint:

- move `_db_connect()` inside an existing or new `try/except` when needed
- remove dead `if conn is None` guards where `_db_connect()` cannot return `None`
- return the correct endpoint-specific fallback:
  - `_no_data(...)` for endpoints that already use that contract
  - or an explicit HTTP error only where that is the established API behavior

Keep the change mechanical and narrow. This is a reliability hardening pass, not an API redesign.

## Scope

Files or modules likely affected:

- `dashboard/api/main.py`
- `dashboard/api/tests/test_endpoints.py`

## Non-Goals

- Do not redesign `_db_connect()` itself.
- Do not refactor endpoint business logic beyond the minimal error-handling fix.
- Do not change frontend code unless an endpoint contract must be clarified.
- Do not change trading logic or data semantics.

## Constraints

- Preserve existing response schemas wherever possible.
- Keep fallback behavior consistent with each endpoint’s current contract.
- Prefer targeted test coverage over broad speculative refactors.
- No secrets or generated artifacts in git.

## Acceptance Criteria

- Observable behavior: audited endpoints no longer throw unhandled 500s solely because `_db_connect()` raises during connection setup.
- Observable behavior: dead `if conn is None` branches tied to `_db_connect()` are removed or replaced with valid control flow.
- Tests: targeted backend tests cover representative `_db_connect()` failure handling across more than one endpoint class.
- Documentation: implementation summary lists which endpoints were fixed and which, if any, were intentionally deferred.

## Verification Plan

- Run targeted backend tests covering `_db_connect()` failure behavior:
  - `python -m pytest dashboard/api/tests/test_endpoints.py -q`
- Manually review the remaining `_db_connect()` call sites in `dashboard/api/main.py` after the change to confirm the dead-pattern no longer exists on the audited paths.

## QA Notes

- Test scenarios: `_db_connect()` raises `EnvironmentError`, `_db_connect()` raises connection/operational error, endpoint returns controlled no-data response, and unaffected happy-path behavior still works.
- Edge cases: endpoints with different contracts (`_no_data`, raw JSON payloads, or HTTP exceptions).
- Regression risks: accidentally masking real downstream logic errors as “database unavailable”; keep the `try/except` scope tight around connection setup unless broader wrapping is already established.

## Launch / Release Notes

- User-facing change summary: dashboard API endpoints fail more gracefully when the database is unavailable.
- Operational notes: frontend pages should show controlled empty/error states rather than inconsistent hard failures during DB outages.
- Rollback notes: revert the per-endpoint error-handling wrappers if they cause unexpected schema drift.

## Post-Launch Validation

- What to monitor: 500-rate reduction on dashboard API endpoints during DB fault scenarios.
- How success will be confirmed: DB-unavailable scenarios produce controlled endpoint responses instead of uncaught exceptions.
- Follow-up decision date: after validating the main audited endpoints under a simulated DB failure.

## Handoff Notes

### Claude implementation prompt

```text
Task: Implement QA-002, "Normalize DB connect failure handling in API endpoints."

Goal:
- Remove the remaining bug class where `_db_connect()` raises before an endpoint reaches its legacy `if conn is None` fallback, causing an unhandled 500.

Scope:
- Primary files:
  - `dashboard/api/main.py`
  - `dashboard/api/tests/test_endpoints.py`

Context:
- `signals_ticker()` was already fixed after the CRDO bug.
- This ticket is the follow-up audit for the same pattern elsewhere in `dashboard/api/main.py`.

Requirements:
1. Audit the remaining `_db_connect()` call sites in `dashboard/api/main.py`.
2. For endpoints where `_db_connect()` is called outside protective error handling:
   - move the connect call inside a tight `try/except`, or
   - otherwise normalize the flow so connection failure does not cause an unhandled 500.
3. Remove dead `if conn is None` branches that assumed `_db_connect()` could return `None`.
4. Preserve each endpoint’s existing response contract as much as possible:
   - use `_no_data(...)` where that is already the endpoint pattern
   - use explicit HTTP errors only where that is already the established behavior
5. Add targeted backend tests covering representative `_db_connect()` failure handling across multiple endpoint types.
6. Document which endpoints were fixed and which were intentionally deferred, if any.

Non-goals:
- Do not redesign `_db_connect()`.
- Do not refactor unrelated endpoint logic.
- Do not change frontend code unless absolutely necessary.
- Do not change trading logic.

Verification:
- `python -m pytest dashboard/api/tests/test_endpoints.py -q`

Required output:
- exact endpoints fixed
- exact files changed
- exact tests run and results
- any deferred call sites and rationale
```

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
