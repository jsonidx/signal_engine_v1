# Task: Remove Request-Time Writers and Tighten Dashboard Refresh Policy

Status: done
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: dashboard
Category: performance
Risk: frontend
Effort: M
Target Release: next
Due Date: TBD
Dependencies: TRD-082, PERF-006
Blocked By: none
Links: `dashboard/api/main.py`, `dashboard/frontend/src/lib/queryClient.ts`, `dashboard/frontend/src/hooks/useHeatmap.ts`, `dashboard/frontend/src/hooks/usePortfolio.ts`, `dashboard/frontend/src/hooks/useRegime.ts`, `dashboard/frontend/src/pages/HomePage.tsx`, `dashboard/frontend/src/components/AiSelectionTable.tsx`, `dashboard/frontend/src/components/CandidateSnapshotsTable.tsx`
Success Metric: dashboard pages stop creating snapshots on GET and unnecessary polling is reduced, with refresh behavior driven by data-producing events or explicit user actions.

## Problem Statement

Some API read endpoints still create or persist data as a side effect of page traffic, and the frontend polls more aggressively than the underlying data changes.

Examples:

- `hot-entry/rankings` writes a daily snapshot on first GET of the day.
- `ticker/{symbol}/option-candidates` persists option candidate snapshots during a read path.
- many React Query hooks inherit a global background refetch interval even for data that only changes after pipeline or AI runs.
- ticker detail queries currently use a very short freshness window compared with how often the thesis actually changes.

This blurs producer/consumer responsibilities and causes avoidable backend load and frontend churn.

## User Impact

- Users can trigger backend writes just by opening a page.
- The dashboard spends resources refetching data that is effectively static between pipeline or AI runs.
- Freshness behavior is implicit rather than being tied to meaningful update events.

## Objective

Remove request-time snapshot writes from user-facing GET paths and tighten frontend refresh policy so queries refresh when the data actually changes, not on a generic polling cadence.

## Proposed Solution

- Move request-time writers to explicit producer paths:
  - pipeline jobs
  - post-AI hooks
  - manual refresh endpoints where warranted
- Reduce or remove generic global polling defaults in React Query.
- Keep polling only for truly active flows such as analyze-job status.
- Use targeted invalidation after mutations or analysis completion instead of passive periodic refetches.

## Scope

Files or modules likely affected:

- `dashboard/api/main.py`
- `run_master.sh`
- `dashboard/frontend/src/lib/queryClient.ts`
- `dashboard/frontend/src/hooks/useHeatmap.ts`
- `dashboard/frontend/src/hooks/usePortfolio.ts`
- `dashboard/frontend/src/hooks/useRegime.ts`
- `dashboard/frontend/src/pages/HomePage.tsx`
- `dashboard/frontend/src/components/AiSelectionTable.tsx`
- `dashboard/frontend/src/components/CandidateSnapshotsTable.tsx`
- `dashboard/frontend/src/pages/TickerPage.tsx`

## Non-Goals

- Do not redesign page layouts.
- Do not remove manual refresh affordances where they are useful.
- Do not change portfolio mutation semantics.
- Do not add real-time push infrastructure.

## Constraints

- Refresh policy must reflect actual producer cadence:
  - pipeline refresh
  - AI rerun
  - manual invalidate
  - active job polling only where necessary
- Preserve explicit invalidation after mutations.
- Keep analyze-job polling intact.

## Acceptance Criteria

- Observable behavior:
  - request-time GET handlers no longer create persisted snapshots as side effects
  - React Query no longer applies a blanket refetch interval to broad dashboard reads
  - snapshot-backed pages rely on explicit invalidation or meaningful refresh intervals instead of generic polling
- Tests:
  - backend tests cover removal of request-time writes where practical
  - frontend tests or targeted assertions cover updated query behavior where existing tests exist
- Documentation:
  - freshness policy is described for major query groups

## Verification Plan

- `pytest -q dashboard/api/tests/test_endpoints.py`
- `cd dashboard/frontend && npm test -- --runInBand`
- targeted manual smoke on Home, Ticker, and Options flows

## QA Notes

- Test scenarios: page load without producer-side writes, manual analyze completion invalidates the right queries, options page still refreshes after explicit rerun
- Edge cases: no snapshot available yet, analysis job still running, user revisits page after pipeline cache invalidation
- Regression risks: stale UI if invalidation coverage is incomplete, over-aggressive query suppression, hidden assumptions on global React Query polling

## Launch / Release Notes

- User-facing change summary: dashboard refresh behavior better matches actual data updates and reduces unnecessary background churn.
- Operational notes: writes happen from pipeline or explicit refresh paths, not incidental page traffic.
- Rollback notes: restore individual polling intervals or legacy request-time writers if adoption uncovers missing producer hooks.

## Post-Launch Validation

- What to monitor: frontend request volume, API read latency, request-time write frequency, stale-data reports after analysis reruns
- How success will be confirmed: page traffic no longer triggers producer writes and background refetch volume drops without freshness regressions
- Follow-up decision date: after one week of normal dashboard usage

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement PERF-007: Remove Request-Time Writers and Tighten Dashboard Refresh Policy.

Goal:
- stop user-facing GET requests from creating persisted snapshots and reduce unnecessary frontend polling so refresh behavior aligns with actual producer events

Scope:
- dashboard/api/main.py
- run_master.sh
- dashboard/frontend/src/lib/queryClient.ts
- dashboard/frontend/src/hooks/useHeatmap.ts
- dashboard/frontend/src/hooks/usePortfolio.ts
- dashboard/frontend/src/hooks/useRegime.ts
- dashboard/frontend/src/pages/HomePage.tsx
- dashboard/frontend/src/components/AiSelectionTable.tsx
- dashboard/frontend/src/components/CandidateSnapshotsTable.tsx
- dashboard/frontend/src/pages/TickerPage.tsx

Requirements:
1. Remove request-time persisted writes from GET handlers such as hot-entry and any remaining similar paths.
2. Move those writes to producer-time hooks in the pipeline or explicit refresh flows.
3. Remove the blanket React Query default refetch interval if it is no longer appropriate.
4. Keep polling only for truly active flows, especially analyze-job status.
5. Prefer targeted invalidation after analysis completion or mutations over passive polling.

Non-goals:
- no layout redesign
- no trading-logic changes
- no websocket/push work

Constraints:
- preserve explicit invalidation behavior after mutations
- keep manual refresh affordances where already useful
- do not break options screener rerun flow

Tests:
- pytest -q dashboard/api/tests/test_endpoints.py
- cd dashboard/frontend && npm test -- --runInBand
```
