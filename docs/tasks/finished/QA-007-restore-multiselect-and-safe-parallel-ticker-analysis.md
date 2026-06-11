# Task: Restore Ticker Multi-Select And Safe Parallel Analysis

Status: done
Stage: done
Type: bug
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Codex
Product Area: dashboard
Category: reliability
Risk: api
Effort: M
Target Release: next patch
Due Date: 2026-06-12
Dependencies: none
Blocked By: none
Links: `dashboard/frontend/src/pages/TickerPage.tsx`, `dashboard/api/main.py`, `ai_quant.py`, `utils/supabase_persist.py`
Success Metric: users can select multiple LLMs on one ticker page and launch them together without frontend rejection, backend deadlock, or generic failed status noise

## Problem Statement

The ticker page originally supported selecting multiple LLMs and launching them together for the same ticker. A defensive patch removed that behavior by blocking multi-select launches on the frontend and rejecting concurrent same-ticker runs on the backend.

That avoids one contention path, but it breaks the intended product behavior. The user requirement is to keep multi-select and start several models at the same time for one ticker.

Recent investigation showed the underlying issue is not the multi-select UI itself. The real problem is backend contention during concurrent same-ticker analysis, including at least one observed deadlock while SEC filing catalyst data was being persisted.

## User Impact

Users can no longer use the ticker page the way it was intended:

- they cannot compare Claude, GPT, and Grok in one action for the same ticker
- they receive a blocker message instead of analysis starting
- the UI regressed from product intent to a workaround

## Objective

Restore ticker-page multi-select launches for same-ticker analysis and make the backend concurrency-safe enough that multiple selected models can run together without spurious failures or deadlocks.

## Proposed Solution

Implement a narrow fix that preserves concurrent same-ticker multi-model analysis rather than prohibiting it.

Expected direction:

- restore multi-select behavior in the ticker page analyze control
- keep per-model job tracking and per-model status polling
- identify and remove the backend contention path that breaks concurrent runs
- make persistence or shared mutable state safe when multiple models analyze the same ticker simultaneously
- keep clear launch/status errors for true provider-key or runtime failures

Potential implementation options:

- serialize only the contested persistence step while allowing the model calls themselves to run in parallel
- use ticker-scoped or table-scoped locking around shared writes instead of blocking the entire run launch
- make SEC filing persistence and any other shared write path idempotent and contention-safe under concurrent same-ticker subprocesses
- if necessary, split read-heavy analysis from write-heavy persistence and merge results afterward

## Scope

Files or modules likely affected:

- `dashboard/frontend/src/pages/TickerPage.tsx`
- `dashboard/api/main.py`
- `ai_quant.py`
- `utils/supabase_persist.py`

## Non-Goals

- Do not redesign the ticker page visual language.
- Do not remove per-model status reporting.
- Do not change trading logic or thesis calculation rules.
- Do not broaden this into a general workflow/orchestration refactor.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No trading logic changes unless risk is `trading-logic`.
- No secrets or generated artifacts in git.
- Keep the fix narrow and focused on restoring intended behavior safely.
- Preserve current provider-key validation and useful launch/status error messages.

## Acceptance Criteria

- Observable behavior:
- On `http://localhost:5173/ticker/<symbol>`, the model picker allows selecting multiple LLMs again.
- Clicking analyze with multiple selected LLMs starts all selected model runs for the same ticker in one action.
- The page shows distinct running/done/failed states per selected model.
- The UI no longer blocks the action with “Run one model at a time for a ticker...”.
- Backend no longer rejects same-ticker multi-model launches with a `409` solely because another selected model is already running.
- Concurrent same-ticker runs do not deadlock or wedge the API in the known persistence path.

- Tests:
- Add or update targeted coverage for multi-model ticker analyze launch/status behavior.
- Add or update backend coverage for concurrent same-ticker job handling if practical at unit/integration level.

- Documentation:
- Update any inline comments or task notes needed to explain how concurrent same-ticker launches are made safe.

## Verification Plan

- Targeted manual verification:
- Start dashboard and API locally.
- Open one ticker page, select at least 3 models, launch them together, confirm all three enter running state.
- Confirm status polling resolves per model without generic shared failure behavior.
- Repeat on at least one ticker that exercises SEC filing enrichment.
- Targeted tests:
- `cd dashboard/frontend && npm test -- --runInBand` only for relevant ticker-page tests if touched
- `pytest -q dashboard/api/tests/test_endpoints.py` if backend endpoint behavior changes are covered there
- `make verify-full` only if required by the implementation chosen

## QA Notes

- Test scenarios:
- multi-select `claude-opus-4-8`, `gpt-5.5`, `grok-4.3` on the same ticker
- repeat launch while one model is already running
- verify true missing-key errors still surface clearly per provider
- verify one failing model does not incorrectly mark the others failed

- Edge cases:
- same ticker launched twice quickly
- stale existing job state from a prior page session
- persistence collision on SEC filing catalyst writes

- Regression risks:
- API event loop blockage during subprocess-heavy bursts
- status polling confusion if jobs are keyed incorrectly
- accidental serialization of all analyses globally instead of only the contested resource

## Launch / Release Notes

- User-facing change summary:
- Ticker page multi-model analysis is restored; multiple selected LLMs can be launched together again for one ticker.

- Operational notes:
- Watch API responsiveness and subprocess completion on same-ticker multi-launches after deploy.

- Rollback notes:
- If concurrency remains unstable, roll back to the prior safe state and reopen this ticket with concrete failure logs.

## Post-Launch Validation

- What to monitor:
- same-ticker multi-model launch success rate
- analysis job completion versus failed status count
- API responsiveness during concurrent ticker analysis

- How success will be confirmed:
- manual browser verification passes on at least one real ticker
- no observed deadlock or wedged API during parallel same-ticker runs

- Follow-up decision date:
- 2026-06-13

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement QA-007, "Restore Ticker Multi-Select And Safe Parallel Analysis," in this repo.

Goal:
- restore ticker-page multi-select for same-ticker analysis
- allow several selected LLMs to start at the same time for one ticker
- fix the backend contention path so this works safely instead of blocking it in the UI/API

Scope:
- `dashboard/frontend/src/pages/TickerPage.tsx`
- `dashboard/api/main.py`
- `ai_quant.py`
- `utils/supabase_persist.py`

Requirements:
- restore multi-select UI behavior on the ticker page
- preserve per-model job status and polling
- remove the frontend one-model-only blocker
- remove the backend same-ticker `409` guard once the underlying contention is fixed
- keep provider-key validation and clear real error reporting
- make the shared persistence path safe when multiple models analyze the same ticker concurrently

Non-goals:
- no visual redesign
- no trading-logic changes
- no broad architecture refactor beyond what is required to make same-ticker parallel analysis safe

Constraints:
- keep the fix narrow
- do not commit secrets or generated artifacts
- avoid unrelated refactors
- maintain API behavior for single-model runs

Verification:
- manual browser verification on `/ticker/BB` or `/ticker/NOK` with 3 selected models
- targeted frontend test updates if applicable
- targeted backend test updates if practical
- run only the minimal relevant verification commands and report any unrelated pre-existing failures separately

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
