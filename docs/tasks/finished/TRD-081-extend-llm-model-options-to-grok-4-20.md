# Task: Extend LLM Model Options to Include Grok 4.20

Status: completed
Stage: done
Type: feature
Priority: P2
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: dashboard
Category: ux
Risk: api
Effort: S
Target Release: backlog
Due Date: TBD
Dependencies: QA-003, QA-007
Blocked By: none
Links: `dashboard/api/main.py`, `dashboard/frontend/src/pages/TickerPage.tsx`, `dashboard/frontend/src/pages/DeepDivePage.tsx`, `dashboard/frontend/src/lib/api.ts`, `ai_quant.py`, `config.py`, `utils/usage.py`, `https://docs.x.ai/developers/models/grok-4.20`, `https://docs.x.ai/developers/models`, `https://docs.x.ai/developers/pricing`
Success Metric: operators can select Grok 4.20 anywhere first-party LLM choices are surfaced, and the selected model flows through API, CLI, settings, and UI without falling back to `grok-4.3`.

## Problem Statement

The product currently exposes `grok-4.3` as the only first-class xAI model option in active dashboard/API selectors, even though the repo already contains references to other Grok-family model IDs and older cost/default metadata.

The immediate request is to support "Grok 4.2", but current xAI documentation as of June 11, 2026 shows the live model ID as `grok-4.20`, not `grok-4.2`. If the codebase adds a literal `grok-4.2` option, it would likely encode an invalid or non-canonical slug into UI and backend surfaces.

## User Impact

- Users cannot explicitly choose `grok-4.20` from the main model pickers even when they want to compare Grok variants.
- Settings and dashboard surfaces present an incomplete view of supported LLM options.
- Model-selection behavior becomes inconsistent across ticker analysis, saved defaults, CLI entrypoints, and cost displays.

## Objective

Add Grok 4.20 as a first-class selectable LLM option across active first-party model selectors and supporting metadata, while preserving exact model-ID correctness.

## Proposed Solution

- Extend the allowed-model lists and selectors to include `grok-4.20` or the exact dated slug chosen for support.
- Standardize friendly labels so UI surfaces present this as `Grok 4.20`.
- Update settings, ticker-page and deep-dive dashboard picker(s), API validation, and CLI choices so the same exact model ID is accepted everywhere.
- Align cost metadata and default/fallback assumptions where needed so estimates and badges do not rely on stale retired-model pricing.
- Explicitly avoid adding a raw `grok-4.2` slug unless xAI documentation later confirms that exact ID exists.

## Scope

Files or modules likely affected:

- `dashboard/api/main.py`
- `dashboard/frontend/src/pages/TickerPage.tsx`
- `dashboard/frontend/src/pages/DeepDivePage.tsx`
- `dashboard/frontend/src/lib/api.ts`
- `ai_quant.py`
- `config.py`
- `utils/usage.py`
- `dashboard/api/tests/test_endpoints.py`

## Non-Goals

- Do not redesign provider routing.
- Do not add retired `grok-4-1-fast-*` models as fresh first-class options.
- Do not change trading logic, thesis-generation logic, or concurrency behavior outside model selection.
- Do not introduce speculative `grok-4.2` IDs that are not documented by xAI.

## Constraints

- Preserve backward compatibility for existing legacy aliases such as `grok` and `grok-premium`.
- Keep exact model IDs consistent across frontend, API validation, settings persistence, and CLI choices.
- Prefer canonical xAI slugs and current pricing over stale repo-local assumptions.

## Acceptance Criteria

- Observable behavior:
  - ticker-page LLM selection includes `Grok 4.20`
  - deep-dive page LLM selection includes `Grok 4.20`
  - settings selectors include `Grok 4.20`
  - API accepts the same exact `grok-4.20` model ID end-to-end
  - CLI `--llm` choices allow the same exact model ID
- Tests:
  - add or update targeted endpoint and model-selection tests for the new Grok option
- Documentation:
  - any user-facing or operator-facing model lists reflect the new supported option and exact slug

## Verification Plan

- `pytest -q dashboard/api/tests/test_endpoints.py`
- `pytest -q tests/test_ai_quant_schema.py`
- `cd dashboard/frontend && npm test -- --runInBand`

## QA Notes

- Test scenarios: launch analysis with `grok-4.20`, save it as default in settings, relaunch from ticker page, trigger deep-dive bulk action with the same model, compare label and status output
- Edge cases: invalid `grok-4.2` input, legacy alias behavior, existing saved default values, stale cost table entries
- Regression risks: silent fallback to `grok-4.3`, UI/API option drift, misleading cost estimates

## Launch / Release Notes

- User-facing change summary: Grok 4.20 can now be selected alongside the existing LLM options across dashboard and API surfaces.
- Operational notes: use the exact xAI model ID `grok-4.20` or a documented supported dated variant; do not configure `grok-4.2` unless the provider adds it.
- Rollback notes: remove the model from whitelists/selectors and restore prior cost/default metadata if regressions appear.

## Post-Launch Validation

- What to monitor: analysis launches using `grok-4.20`, model labels shown in UI, error rates for invalid model IDs, cost-estimate consistency
- How success will be confirmed: operators can select Grok 4.20 everywhere relevant and runs complete without forced fallback to `grok-4.3`
- Follow-up decision date: after first production use of the new model option

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-081: Extend LLM model options to include Grok 4.20.

Goal:
- add Grok 4.20 as a first-class selectable model across the active product surfaces
- keep the implementation technically correct by using the exact supported xAI slug `grok-4.20` or the chosen documented dated variant, not an invented `grok-4.2` slug

Scope:
- dashboard/api/main.py
- dashboard/frontend/src/pages/TickerPage.tsx
- dashboard/frontend/src/pages/DeepDivePage.tsx
- dashboard/frontend/src/lib/api.ts
- ai_quant.py
- config.py
- utils/usage.py
- dashboard/api/tests/test_endpoints.py
- any minimal related frontend tests if needed

Required changes:
- extend backend LLM allowlists and settings options to include Grok 4.20
- extend ticker-page LLM options and friendly labels to include Grok 4.20
- extend deep-dive page LLM options and friendly labels to include Grok 4.20
- allow the same exact model ID in ai_quant CLI `--llm` choices
- ensure cost estimation and model-label metadata are not stale for the new Grok option
- preserve legacy aliases such as `grok` and `grok-premium`

Non-goals:
- no provider-routing redesign
- no trading-logic changes
- no concurrency/job-isolation changes
- do not add undocumented `grok-4.2` as a literal slug
- do not reintroduce retired `grok-4-1-fast-*` models as first-class choices

Tests:
- pytest -q dashboard/api/tests/test_endpoints.py
- pytest -q tests/test_ai_quant_schema.py
- cd dashboard/frontend && npm test -- --runInBand
```
