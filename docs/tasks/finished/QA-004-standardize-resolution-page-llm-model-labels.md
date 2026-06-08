# Task: Standardize Resolution Page LLM Model Labels

Status: done
Stage: done
Type: bug
Priority: P2
Severity: low
Owner: Claude Code
Reviewer: Human
Product Area: dashboard
Category: ux
Risk: frontend
Effort: XS
Target Release: next patch
Due Date: 2026-06-02
Dependencies: QA-003-standardize-llm-model-ids-across-workflows-and-entrypoints
Blocked By: none
Links: `http://localhost:5173/resolution`
Success Metric: `/resolution` shows consistent, friendly labels for all currently supported first-party LLM model IDs.

## Problem Statement

The `/resolution` page has a local `modelLabel()` mapping in `dashboard/frontend/src/pages/ResolutionPage.tsx`, but that mapping is not fully aligned with the current supported LLM IDs used elsewhere in the product.

The page now receives a `model` field from the API, but the display layer still mixes explicit mappings with fallback formatting. As a result, newer IDs such as `grok-4.3`, `gpt-5.1`, `gpt-5.5`, and `gpt-5.5-pro` are not explicitly standardized on the page, and some fallback output is inconsistent or awkward. For example, `gpt-5.5-pro` would render as `GPT-5.5-pro` rather than a canonical friendly label.

There is also naming drift risk between the ticker page LLM picker, backend accepted IDs, and the resolution page benchmark cards and filters.

## User Impact

Operators reviewing benchmark results on `/resolution` cannot rely on a single, clean display vocabulary for model names. That makes comparison across pages harder and increases the chance of misreading which model produced which thesis.

## Objective

Make `/resolution` use the same friendly, canonical LLM labels as the rest of the dashboard for all currently supported first-party model IDs.

## Proposed Solution

Update the `modelLabel()` logic on the resolution page to explicitly cover the current supported model IDs rather than relying on partial fallback formatting.

The implementation should:

- add explicit mappings for currently supported IDs such as `grok-4.3`, `gpt-5.1`, `gpt-5.5`, `gpt-5.5-pro`, `claude-sonnet-4-6`, and `claude-opus-4-8`
- keep legacy or historic IDs readable for old rows already stored in the database
- preserve a fallback path for unknown future IDs, but make it clearly secondary
- keep label wording consistent with the ticker page and any other first-party LLM selectors

If a shared utility is the smallest clean solution, it is allowed, but avoid broad refactors.

## Scope

Files or modules likely affected:

- `dashboard/frontend/src/pages/ResolutionPage.tsx`
- `dashboard/frontend/src/pages/TickerPage.tsx`
- `dashboard/api/main.py`

## Non-Goals

- Do not redesign model routing, pricing, or provider behavior.
- Do not change thesis generation, trading logic, or stored database values.
- Do not remove support for legacy/historic model IDs already present in outcomes data.
- Do not perform a broad frontend refactor just to centralize labels unless the change stays clearly narrow.

## Constraints

- Keep the fix narrow and focused on label standardization.
- Preserve readability for older historic model IDs already present in stored outcomes.
- No secrets or generated artifacts in git.

## Acceptance Criteria

- Observable behavior: `/resolution` benchmark cards, filters, and tables show friendly labels for all currently supported first-party LLM IDs.
- Observable behavior: `grok-4.3`, `gpt-5.1`, `gpt-5.5`, `gpt-5.5-pro`, `claude-sonnet-4-6`, and `claude-opus-4-8` render with canonical display names.
- Observable behavior: older IDs already present in historical data still render clearly.
- Tests: affected frontend tests pass, or targeted coverage is added if the page currently lacks direct label assertions.
- Documentation: none required unless a shared label source is introduced or behavior changes materially.

## Verification Plan

- `cd dashboard/frontend && npm run build`
- Targeted checks:
- Load `http://localhost:5173/resolution`
- Confirm model chips, filters, and by-model sections display canonical labels for current IDs and readable labels for legacy IDs
- Run any targeted frontend tests covering the resolution page if available

## QA Notes

- Test scenarios: current supported IDs, older historic IDs, and unknown fallback IDs.
- Edge cases: mixed old and new model IDs in the same result set; `unknown`/legacy rows.
- Regression risks: low, limited to dashboard display naming.

## Launch / Release Notes

- User-facing change summary: the resolution page now uses standardized friendly labels for current LLM model IDs.
- Operational notes: none.
- Rollback notes: revert the narrow label-mapping change if any regression appears in `/resolution` rendering.

## Post-Launch Validation

- What to monitor: manual inspection of `/resolution` after deploy.
- How success will be confirmed: current IDs render consistently with the ticker page and no awkward fallback labels appear for supported models.
- Follow-up decision date: 2026-06-03

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Task: Standardize `/resolution` LLM model labels

Goal:
Make the Resolution page show the same friendly, canonical labels for current first-party LLM model IDs that the rest of the dashboard uses.

Context:
- The Resolution page has its own `modelLabel()` mapping in `dashboard/frontend/src/pages/ResolutionPage.tsx`.
- The page now receives a `model` field from the API, but its display mapping is only partially updated.
- Current supported first-party IDs elsewhere in the product include `grok-4.3`, `gpt-5.1`, `gpt-5.5`, `gpt-5.5-pro`, `claude-sonnet-4-6`, and `claude-opus-4-8`.
- Some older/historic IDs may still appear in stored outcomes and should remain readable.

Exact scope:
- `dashboard/frontend/src/pages/ResolutionPage.tsx`
- Read `dashboard/frontend/src/pages/TickerPage.tsx` for the canonical current model set
- Touch `dashboard/api/main.py` only if needed to preserve the `model` field contract already being used by `/resolution`

Required outcome:
- `/resolution` cards, filters, and tables show canonical friendly labels for current supported model IDs.
- Explicitly cover at least:
  - `grok-4.3`
  - `gpt-5.1`
  - `gpt-5.5`
  - `gpt-5.5-pro`
  - `claude-sonnet-4-6`
  - `claude-opus-4-8`
- Keep older IDs readable.
- Keep a fallback for unknown future IDs, but do not rely on fallback for current supported IDs.

Non-goals:
- No provider/routing changes.
- No trading logic changes.
- No broad refactor just to centralize labels.
- Do not rewrite stored historical model IDs.

Constraints:
- Keep the change narrow.
- Preserve support for legacy/historic IDs in display.
- No secrets or generated artifacts in git.

Verification:
- `cd dashboard/frontend && npm run build`
- Manually inspect `http://localhost:5173/resolution`
- Run targeted frontend tests if available

Deliverable:
- A narrow frontend update standardizing Resolution page labels, plus a concise summary of what labels were added and how legacy/fallback behavior remains handled.
```

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
