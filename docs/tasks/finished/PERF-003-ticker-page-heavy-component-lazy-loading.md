# Task: Ticker page heavy component lazy loading

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Codex
Product Area: dashboard
Category: performance | ux
Risk: frontend
Effort: S
Target Release: next
Due Date: 2026-06-18
Dependencies: PERF-002
Blocked By: none
Links: `dashboard/frontend/src/pages/TickerPage.tsx`, `dashboard/frontend/src/components/charts/PriceChart.tsx`, `dashboard/frontend/src/components/charts/PriceLadder.tsx`, `dashboard/frontend/src/components/HistoricalAnalogs.tsx`, `dashboard/frontend/src/components/EarningsReactionModel.tsx`
Success Metric: Ticker page first render no longer waits on all heavy chart/analytics components to be parsed and mounted; above-the-fold content becomes visible before below-the-fold visualizations finish loading.

## Problem Statement

Even after route-level splitting, the ticker page still imports several heavy components eagerly, including charting and analytics modules. In [TickerPage.tsx](/Users/jason/signal_engine_v1/dashboard/frontend/src/pages/TickerPage.tsx:1), `PriceChart`, `PriceLadder`, and other large subtrees are loaded as part of the page bundle even though the user first needs the top summary, thesis, and pricing context.

This makes the deep-dive page heavier than necessary on first render.

## User Impact

Users opening a ticker page wait on code for secondary visualizations before the page reaches its fastest possible first paint. The page feels more sluggish than the underlying cached API response warrants.

## Objective

Defer loading of the heaviest ticker-page components so the top-of-page content renders first and non-critical visuals load progressively.

## Proposed Solution

Introduce component-level lazy loading for the heaviest ticker-page subtrees.

- Lazy-load charting and large analytical widgets that are not required for first paint.
- Use stable, compact skeletons or placeholders in their place until loaded.
- Preserve existing layout dimensions where practical to avoid large layout shifts.
- Because these components are exported as named exports, use the `React.lazy(() => import(...).then(m => ({ default: m.NamedExport })))` shim pattern rather than assuming default exports.
- Placeholder heights should deliberately match the approximate final component heights so deferred loading does not introduce visible layout jump.

Primary candidates:

- `PriceChart`
- `PriceLadder`
- `HistoricalAnalogs`
- `EarningsReactionModel`

## Scope

Files or modules likely affected:

- `dashboard/frontend/src/pages/TickerPage.tsx`
- possibly a small lazy wrapper / fallback component
- targeted ticker-page tests if they depend on eager component mounting assumptions

## Non-Goals

- Do not redesign the ticker page information architecture.
- Do not remove charts or analytical components.
- Do not change API queries in this ticket.
- Do not rewrite the heavy components themselves unless needed for a minimal lazy-load integration.

## Constraints

- Above-the-fold content must remain stable and readable while deferred components load.
- Avoid visible layout thrash when heavy components resolve.
- Placeholder/skeleton height should be chosen to closely match the actual rendered chart/widget height.
- Keep test stubs and mocking behavior straightforward.
- No secrets or generated artifacts in git.

## Acceptance Criteria

- Observable behavior: ticker summary and thesis content render before heavy chart modules finish loading.
- Observable behavior: deferred components show sensible placeholders or skeletons.
- Observable behavior: ticker page remains functionally identical after deferred components load.
- Tests: ticker-page tests continue to pass or are updated minimally for the new loading boundaries.
- Documentation: ticket includes implementation prompt and verification plan.

## Verification Plan

- Build the frontend: `cd dashboard/frontend && npm run build`
- Run targeted ticker-page tests: `cd dashboard/frontend && npm test -- --run src/pages/tests/TickerPage.option-candidates.test.tsx`
- Manual: load `/ticker/AAPL` on a cold frontend cache and confirm top content appears before charts fully hydrate.

## QA Notes

- Test scenarios: direct ticker navigation, repeat navigation, slow network/chunk load, and failed deferred-component load.
- Edge cases: placeholders remaining visible too long, layout shifts, and component mocks in tests.
- Regression risks: overly aggressive deferral can hide content users expect immediately; keep the scope limited to clearly non-critical heavy subtrees.

## Launch / Release Notes

- User-facing change summary: ticker pages render core analysis faster while heavier charts and analytics load progressively.
- Operational notes: network traces should show smaller initial ticker-page chunks plus follow-up chunk requests for deferred components.
- Rollback notes: revert lazy imports in `TickerPage.tsx`.

## Post-Launch Validation

- What to monitor: ticker page first-contentful render and chunk waterfall shape in browser devtools.
- How success will be confirmed: summary/thesis content appears sooner without breaking chart availability.
- Follow-up decision date: after validating ticker-page cold-load behavior in the next deployment.

## Handoff Notes

### Claude implementation prompt

```text
Task: Implement PERF-003, "Ticker page heavy component lazy loading."

Goal:
- Make the ticker page render core summary/thesis content sooner by deferring non-critical heavy components.

Scope:
- Primary file:
  - `dashboard/frontend/src/pages/TickerPage.tsx`
- Secondary component files only if needed for clean lazy integration.

Requirements:
1. Lazy-load the heaviest non-critical ticker-page subtrees, especially:
   - `PriceChart`
   - `PriceLadder`
   - `HistoricalAnalogs`
   - `EarningsReactionModel`
2. These are named exports today, so use the shim pattern for `React.lazy()`, for example:
   - `lazy(() => import('...').then(m => ({ default: m.PriceChart })))`
3. Use compact placeholders or skeletons while those components load.
4. Size placeholders to closely match the eventual rendered component height so layout shift is minimized.
5. Keep the existing ticker-page UX and data flow unchanged after components load.

Non-goals:
- Do not change API behavior.
- Do not redesign the ticker page.
- Do not rewrite chart internals unless required for the lazy-load boundary.

Verification:
- `cd dashboard/frontend && npm run build`
- `cd dashboard/frontend && npm test -- --run src/pages/tests/TickerPage.option-candidates.test.tsx`
- manual cold-load check on `/ticker/AAPL`
```

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
