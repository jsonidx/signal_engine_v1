# Task: Dashboard route-level code splitting

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
Due Date: 2026-06-17
Dependencies: none
Blocked By: none
Links: `dashboard/frontend/src/App.tsx`, `dashboard/frontend/src/main.tsx`, `dashboard/frontend/package.json`
Success Metric: Initial JavaScript loaded for first dashboard paint drops materially by route-splitting page bundles; cold navigation to `/` or `/ticker/:symbol` no longer eagerly downloads every page module.

## Problem Statement

The frontend currently imports all top-level pages eagerly in [App.tsx](/Users/jason/signal_engine_v1/dashboard/frontend/src/App.tsx:1). That means the initial dashboard bundle includes code for pages the user may never visit in the session, including heavy views such as ticker deep-dive, options, backtest, and resolution.

This is unnecessary work on every cold load and slows time-to-interactive, especially on the first authenticated navigation.

## User Impact

Users pay a startup cost for the entire application even when they only need one route. The dashboard feels heavier than it should on initial load, and route transitions do not fully benefit from modern bundle splitting.

## Objective

Split top-level routes into separate chunks so the app loads only the code needed for the current page, with acceptable loading fallbacks during route transitions.

## Proposed Solution

Use `React.lazy()` and `Suspense` at the route level in `App.tsx`.

- Convert page imports to lazy imports for major route modules.
- Keep auth and shell behavior intact.
- Add a concise route-level fallback UI that matches the existing dashboard loading style.
- Preserve error boundaries and private-route handling.
- Capture a pre/post build baseline so the change is measurable, not anecdotal.
- Confirm `vite.config.ts` has no `manualChunks` override or equivalent bundling config that would interfere with route-level splitting.

This is a frontend-only performance improvement and should not change business logic or API behavior.

## Scope

Files or modules likely affected:

- `dashboard/frontend/src/App.tsx`
- `dashboard/frontend/src/main.tsx`
- possibly a shared fallback/loading component if needed

## Non-Goals

- Do not redesign page layouts.
- Do not change route structure or URLs.
- Do not modify data-fetching logic.
- Do not optimize component-level heavy subtrees in this ticket; that belongs in follow-up work.

## Constraints

- Preserve the current auth gate and error-boundary behavior.
- Keep the loading fallback visually consistent with the current app.
- No secrets or generated artifacts in git.

## Acceptance Criteria

- Observable behavior: initial app load no longer eagerly bundles every page component.
- Observable behavior: pre/post build output documents the bundle-size delta for the initial route payload.
- Observable behavior: route navigation still works correctly across all existing routes.
- Observable behavior: users see a stable loading fallback while a lazy route chunk is loading.
- Tests: frontend tests that exercise routing still pass.
- Documentation: ticket contains the implementation prompt and verification steps.

## Verification Plan

- Capture a baseline build before the change and compare it after the change: `cd dashboard/frontend && npm run build`
- Record the relevant `dist/assets/*.js` file sizes before/after in the implementation notes or QA summary.
- Run targeted frontend tests that cover app routing or page mounting.
- Manual: load `/`, `/ticker/AAPL`, `/options`, `/resolution` and confirm route transitions still work with the lazy fallback.

## QA Notes

- Test scenarios: cold load on home route, direct load to ticker route, route-to-route navigation, and auth-protected route rendering.
- Edge cases: failed chunk load, error boundary behavior, and fallback rendering during navigation.
- Regression risks: lazy routes can accidentally break Suspense placement or error-boundary order; keep the wrapper stack explicit.

## Launch / Release Notes

- User-facing change summary: dashboard initial load is lighter because page code is loaded on demand.
- Operational notes: browser network waterfalls should show multiple route chunks instead of one monolithic page bundle.
- Rollback notes: revert lazy imports back to eager imports in `App.tsx`.

## Post-Launch Validation

- What to monitor: build chunk sizes and cold-load time for the initial dashboard route.
- How success will be confirmed: initial JS payload is smaller and first-route responsiveness improves without route regressions.
- Follow-up decision date: after verifying bundle reduction in the next deployment.

## Handoff Notes

### Claude implementation prompt

```text
Task: Implement PERF-002, "Dashboard route-level code splitting."

Goal:
- Reduce initial dashboard JS payload by lazily loading top-level pages instead of importing every page eagerly.

Scope:
- Edit only:
  - `dashboard/frontend/src/App.tsx`
  - `dashboard/frontend/src/main.tsx` if needed
  - optionally a small shared fallback component if that is cleaner

Requirements:
1. Replace eager page imports in `App.tsx` with `React.lazy()` for major routes.
2. Wrap lazy route rendering with `Suspense` and a concise loading fallback.
3. Before changing code, capture a baseline production build and record the relevant generated JS asset sizes. After the change, record the same sizes so the bundle reduction is explicit.
4. Confirm `dashboard/frontend/vite.config.ts` has no `manualChunks` override or related bundling customization that would conflict with route-level splitting. If it does, document it and adjust minimally.
3. Preserve:
   - existing route paths
   - `PrivateRoute`
   - `ErrorBoundary`
   - current auth flow
4. Keep the fallback consistent with the existing dashboard style; do not redesign the app.

Non-goals:
- Do not change API calls.
- Do not refactor page internals.
- Do not redesign routing.

Verification:
- `cd dashboard/frontend && npm run build`
- run targeted frontend tests covering route rendering
- manually verify `/`, `/ticker/AAPL`, `/options`, and `/resolution`
- report pre/post bundle sizes from `dist/assets`
```

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
