# Task: Verify Failure on TickerPage LLM Default

Status: done
Stage: done
Type: bug
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: dashboard
Category: reliability
Risk: frontend
Effort: XS
Target Release: next patch
Due Date: 2026-05-31
Dependencies: none
Blocked By: none
Links: https://github.com/jsonidx/signal_engine_v1/actions/runs/26654094698, https://github.com/jsonidx/signal_engine_v1/actions/runs/26652586442, https://github.com/jsonidx/signal_engine_v1/commit/73163598b2c59aba35a458891fe8a2108e969c04
Success Metric: `make verify` and the `Verify` GitHub Actions workflow pass on `main` without TypeScript errors in the dashboard build.

## Problem Statement

The latest failed GitHub workflow was the `Verify` run started on 2026-05-29 at 18:11:04Z. It failed in the `Run local verification gate` step during the frontend build, not during dependency install or test setup.

The concrete compiler error was:

- `dashboard/frontend/src/pages/TickerPage.tsx(1120,45): error TS2345`

The root cause is a stale default state value in `AnalyzeButton`. The LLM picker was migrated in commit `73163598b2c59aba35a458891fe8a2108e969c04` from legacy aliases (`grok`, `claude`, `chatgpt`) to exact model IDs (`grok-4.3`, `gpt-5.1`, `gpt-5.5`, `gpt-5.5-pro`, `claude-sonnet-4-6`, `claude-opus-4-8`), but the component still initializes:

- `const [llm, setLlm] = useState<LLMChoice>('grok')`

That literal is no longer part of `LLMChoice`, so `tsc` fails and the entire `Verify` workflow turns red.

## User Impact

Every push that runs `Verify` against this code path fails before the dashboard build completes. This blocks confidence in frontend changes, hides unrelated regressions behind a hard compile stop, and slows delivery because CI cannot be trusted as a green gate.

## Objective

Restore a passing `Verify` workflow by removing the stale legacy LLM default from the frontend and aligning the affected dashboard code with the current exact model ID contract.

## Proposed Solution

Update the Ticker page analyze control so its default selected LLM is a valid `LLMChoice` value. Audit the immediate analyze flow for any remaining frontend-side legacy aliases that should now use exact model IDs, but keep the fix narrow and avoid unrelated refactors.

## Scope

Files or modules likely affected:

- `dashboard/frontend/src/pages/TickerPage.tsx`
- `dashboard/frontend/src/lib/api.ts`

## Non-Goals

- Do not change backend routing behavior for legacy aliases unless required to keep current callers working.
- Do not refactor unrelated dashboard components or query logic.
- Do not change trading logic, ranking logic, or model availability beyond fixing this regression.

## Constraints

- Keep the fix narrowly scoped to the failing verify/build regression.
- Preserve current supported model IDs in the picker.
- No secrets or generated artifacts in git.

## Acceptance Criteria

- Observable behavior: the Ticker page analyze control initializes with a valid currently supported model ID and still submits analyze requests successfully.
- Tests: `cd dashboard/frontend && npm run build` passes, and `make verify` passes locally.
- Documentation: no documentation updates required unless the implementation changes a user-visible default label or behavior.

## Verification Plan

- `cd dashboard/frontend && npm run build`
- `make verify`
- Targeted checks:
- Confirm `dashboard/frontend/src/pages/TickerPage.tsx` no longer uses legacy `'grok'` as `LLMChoice` state.
- Confirm the analyze request path uses a supported current model ID by default.

## QA Notes

- Test scenarios: open the Ticker page, inspect the default LLM selection, trigger analyze with the default value, and verify no TypeScript build errors remain.
- Edge cases: ensure any remaining API helper default does not silently reintroduce a legacy alias path from the frontend.
- Regression risks: low, but the analyze button default and request payload must remain aligned.

## Launch / Release Notes

- User-facing change summary: fixes a dashboard regression that blocked frontend verification after the LLM model-ID migration.
- Operational notes: rerun the `Verify` workflow after merge.
- Rollback notes: revert only the narrow frontend fix if unexpected analyze behavior appears.

## Post-Launch Validation

- What to monitor: the next `Verify` GitHub Actions run on `main`.
- How success will be confirmed: the workflow reaches and passes the frontend build step without `TS2345` in `TickerPage.tsx`.
- Follow-up decision date: 2026-06-01

## QA Result (2026-05-30)

QA passed. `make verify` completed cleanly: 623 Python tests passed, 37 frontend tests passed, frontend build clean (no TS2345), import smoke checks all green. Ticket closed and moved to `docs/tasks/finished/`.

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Task: Fix Verify failure caused by stale TickerPage LLM default

Goal:
Make `make verify` pass by fixing the frontend TypeScript regression introduced during the LLM model-ID migration.

Findings:
- GitHub Actions `Verify` failed on 2026-05-29 in the frontend build step.
- Error: `dashboard/frontend/src/pages/TickerPage.tsx(1120,45): error TS2345`
- Current code defines `LLMChoice` from exact model IDs:
  - `grok-4.3`
  - `gpt-5.1`
  - `gpt-5.5`
  - `gpt-5.5-pro`
  - `claude-sonnet-4-6`
  - `claude-opus-4-8`
- But `AnalyzeButton` still initializes state with legacy `'grok'`, which is no longer assignable.
- There is also an API helper default in `dashboard/frontend/src/lib/api.ts` that still uses legacy `'grok'`; audit whether that should be aligned as part of this same narrow fix.

Exact scope:
- `dashboard/frontend/src/pages/TickerPage.tsx`
- `dashboard/frontend/src/lib/api.ts`

Required outcome:
- Replace the stale frontend default(s) so the analyze flow uses a valid current model ID by default.
- Keep the fix small and targeted.

Non-goals:
- No backend refactor.
- No trading logic changes.
- No LLM catalog redesign.
- No unrelated dashboard cleanup.

Constraints:
- Preserve current supported picker options and labels unless a tiny adjustment is required for consistency.
- Do not remove backend backward compatibility for legacy aliases unless strictly necessary.
- No generated artifacts or secrets in git.

Verification:
- `cd dashboard/frontend && npm run build`
- `make verify`

Deliverable:
- Code changes only for the regression fix, with a concise summary of what changed and the verification results.
```

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
