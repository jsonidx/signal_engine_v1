# Task: Universe Coverage and Qualification Analytics

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: dashboard
Category: research
Risk: api
Effort: M
Target Release: universe-v2
Due Date: TBD
Dependencies: TRD-055, TRD-056, TRD-057
Blocked By: none
Links: `utils/supabase_persist.py`, `dashboard/api/main.py`, `dashboard/frontend/src/pages/ScreenersPage.tsx`, `reports/baseline_study_TRD033.md`
Success Metric: the team can see daily coverage, qualification fallout, and cohort-level filter behavior well enough to decide whether the expanded universe is producing better learnable signal.

## Problem Statement

A broader universe and a two-lane funnel are only useful if the team can see what is happening inside them. Today it is difficult to answer:

- how many symbols entered discovery
- how many passed instrument hygiene
- how many passed deterministic screening
- how many were suppressed before AI
- how many advanced to the AI lane
- which filters are excluding the most names

Without this observability, the team cannot judge whether expansion improved opportunity capture or just increased noise.

## User Impact

- PM review remains anecdotal instead of systematic.
- Filter decisions cannot be audited quickly.
- The team cannot tell whether missed opportunities were screened out, suppressed, or simply never covered.

## Objective

Add persistent analytics and a simple dashboard/API surface for universe coverage, filter fallout, and lane qualification rates.

## Proposed Solution

Persist and expose daily cohort metrics such as:

- raw discovery universe size
- included names by source
- excluded names by exclusion reason
- screened names count
- qualified research-lane count
- suppressed count and top suppression reasons
- execution-lane AI count
- counts by direction bucket where available

Preferred design:

- store daily rollups and, where useful, row-level qualification metadata
- expose API endpoints for summary and trend views
- surface a compact dashboard section on the screener or rankings page

This should support PM questions like:

- Are off-index names now entering the funnel?
- Are we over-filtering by liquidity?
- Are bears being suppressed more often than longs?
- Which filter is doing the most work?

## Scope

Files or modules likely affected:

- `utils/supabase_persist.py`
- `schema.sql`
- `migrations/`
- `dashboard/api/main.py`
- `dashboard/api/tests/test_endpoints.py`
- `dashboard/frontend/src/pages/ScreenersPage.tsx`
- `docs/INTERNALS.md`

## Non-Goals

- Do not build a large new dashboard module.
- Do not change trade-ranking logic here.
- Do not add outcome-based weight changes in this ticket.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.
- Prefer lightweight summary views over heavy bespoke analytics infrastructure.

## Acceptance Criteria

- Observable behavior:
  - Daily coverage and qualification metrics are persisted and queryable.
  - API exposes at least one summary endpoint and one recent-history endpoint.
  - Dashboard shows the key funnel counts and top exclusion/suppression reasons.
- Tests:
  - Add API endpoint tests.
  - Add targeted persistence tests where applicable.
- Documentation:
  - `docs/INTERNALS.md` documents the new analytics surfaces and their definitions.

## Verification Plan

- Targeted tests:
  - `pytest -q dashboard/api/tests/test_endpoints.py tests/test_supabase_integration.py`
- Additional verification:
  - verify recent-history API output against a seeded or fixture-backed dataset where feasible

## QA Notes

- Test scenarios: no data yet, one day of data, multiple days, missing optional rollup fields.
- Edge cases: duplicate daily writes, partial persistence failures, empty suppression reasons.
- Regression risks: slow endpoints, schema drift, analytics definitions not matching selector behavior.

## Launch / Release Notes

- User-facing change summary: screener coverage and qualification analytics are now visible in API/dashboard form.
- Operational notes: use these analytics before making further universe or gate changes.
- Rollback notes: disable the new endpoints/UI while preserving underlying pipeline behavior.

## Post-Launch Validation

- What to monitor:
  - endpoint latency
  - rollup freshness
  - dashboard correctness versus stored counts
- How success will be confirmed:
  - PM review can answer funnel questions from data instead of anecdotes
- Follow-up decision date:
  - after 2 weeks of daily cohort data

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-059: add persistence and analytics for universe coverage, qualification fallout, and lane counts.

Goal:
- Make the broader universe and two-lane funnel observable.
- Allow PM review to see what the filters and suppressions are doing.

Scope:
- utils/supabase_persist.py
- schema.sql
- migrations/
- dashboard/api/main.py
- dashboard/api/tests/test_endpoints.py
- dashboard/frontend/src/pages/ScreenersPage.tsx
- docs/INTERNALS.md

Required changes:
- Persist daily funnel rollups and/or row-level qualification metadata sufficient to answer:
  - discovery count
  - included-by-source count
  - excluded-by-reason count
  - research-lane qualified count
  - suppressed count by reason
  - execution-lane AI count
- Add at least one API summary endpoint and one recent-history endpoint.
- Add a compact dashboard surface to display key counts and top reasons.

Non-goals:
- No large standalone analytics product surface
- No ranking-logic changes
- No weight recalibration here

Constraints:
- Keep the implementation lightweight
- Avoid slow or overly complex endpoints
- Keep definitions aligned with actual selector behavior

Tests / verification:
- pytest -q dashboard/api/tests/test_endpoints.py tests/test_supabase_integration.py
- verify API responses on empty and non-empty datasets where feasible
```
