# Task: Fix LLM provider routing for stale-refresh workflow

Status: done
Stage: done
Type: bug
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Jason
Product Area: data-pipeline
Category: reliability
Risk: api
Effort: M
Target Release: 2026-06
Due Date: 2026-06-05
Dependencies: none
Blocked By: none
Links: workflow run `26972007769`, workflow run `26936457530`
Success Metric: stale-thesis refresh updates thesis timestamps when dashboard default LLM is set to an OpenAI or Anthropic model

## Problem Statement

The stale-thesis refresh workflow is coupled incorrectly to the xAI execution path. The workflow calls `scripts/refresh_stale_and_notify.py`, which launches `ai_quant.py` without `--llm`. In `ai_quant.py`, the CLI default for `--llm` is hard-coded to `grok-4.3`, so the analysis path enters the Grok/xAI branch. However, the actual model name used inside `_call_claude()` comes from `strategy_config.ai_model_default`, which can be changed in the dashboard settings.

When `ai_model_default` was changed in the dashboard from Grok to `gpt-5.5`, the workflow continued using the xAI branch but attempted to call model `gpt-5.5` against the xAI endpoint. GitHub Actions run `26972007769` completed with repeated `404` errors:

- `The model gpt-5.5 does not exist or your team ... does not have access to it`

The stale set remained unchanged at 54 tickers after the run. This is a real regression in provider/model routing, not a secret problem.

## User Impact

Anyone changing the default LLM in dashboard settings can silently break automated thesis refreshes. The pipeline appears healthy at the workflow level while stale AI theses accumulate in the Deep Dive table.

## Objective

Make automated stale-thesis refresh use the correct provider and credentials for the configured default model, regardless of whether the default model is Grok, OpenAI, or Anthropic.

## Proposed Solution

Fix the contract between selected model and provider routing in `ai_quant.py` and the stale-refresh entrypoints.

Recommended approach:

- Treat the resolved effective model as the single source of truth.
- Route provider strictly from the selected model in settings or explicit `--llm` override:
  - Grok models like `grok-4.3` must use xAI transport and `XAI_API_KEY`
  - OpenAI models like `gpt-5.5` must use OpenAI transport and `OPENAI_API_KEY`
  - Anthropic models like `claude-sonnet-4-6` or `claude-opus-4-8` must use Anthropic transport and `ANTHROPIC_API_KEY`
- Do not allow provider routing to follow one value while the model name comes from another source.
- Do not use a separate hard-coded CLI default branch to imply provider when the effective model came from dashboard settings.
- Ensure the stale-refresh workflow forwards the correct secret set for whichever provider is selected.
- Ensure batch refresh exits non-zero when no theses were updated because provider/model routing failed.

The key point is to remove the split-brain behavior where:

- CLI default says `grok-4.3`
- DB setting says `gpt-5.5`
- provider routing follows one value
- model name follows the other

## Scope

Files or modules likely affected:

- `ai_quant.py`
- `scripts/refresh_stale_and_notify.py`
- `refresh_stale_theses.py`
- `.github/workflows/refresh_stale_theses.yml`
- `dashboard/api/main.py`
- `dashboard/frontend/src/pages/SettingsPage.tsx`

## Non-Goals

- Do not redesign the settings UI.
- Do not change thesis logic, prompt content, or ranking logic.
- Do not migrate all workflows to a new secret-management system.
- Do not remove support for multi-provider manual runs from the ticker page.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No trading logic changes unless risk is `trading-logic`.
- No secrets or generated artifacts in git.

## Acceptance Criteria

- Observable behavior:
- If `strategy_config.ai_model_default` is `gpt-5.5`, stale-refresh uses the OpenAI codepath and does not call xAI with `gpt-5.5`.
- If `strategy_config.ai_model_default` is `claude-sonnet-4-6`, stale-refresh uses the Anthropic codepath.
- If `strategy_config.ai_model_default` is `grok-4.3`, stale-refresh uses the xAI codepath.
- If the dashboard setting is changed from one provider family to another, automated refresh follows that provider change on the next run without any manual workflow edit.
- The stale-refresh workflow fails visibly if the chosen provider credentials are missing or invalid.
- Manual dashboard-triggered reruns and CLI-triggered reruns follow the same provider/model routing rules.
- Tests:
- Add targeted tests for model-to-provider routing and effective-model resolution.
- Add a regression test covering stale-refresh execution when DB default model is OpenAI while CLI default would otherwise imply Grok.
- Documentation:
- Update operational docs or ticket notes explaining that dashboard default LLM affects automated stale refreshes.

## Verification Plan

- `pytest -q tests/test_stale_refresh_guardrails.py`
- Targeted tests:
- `pytest -q` for any new `ai_quant` / workflow-routing tests added for this fix
- Dry-run or targeted execution:
- `python3 scripts/refresh_stale_and_notify.py --days 7 --dry-run`
- Confirm a real workflow run refreshes stale theses after setting `ai_model_default` to one model from each provider family.
- `make verify-full` only if live integration behavior changed.

## QA Notes

- Test scenarios:
- `ai_model_default=grok-4.3`
- `ai_model_default=gpt-5.5`
- `ai_model_default=claude-sonnet-4-6`
- missing `OPENAI_API_KEY` with OpenAI default
- missing `ANTHROPIC_API_KEY` with Claude default
- missing `XAI_API_KEY` with Grok default
- Edge cases:
- CLI explicit `--llm` should override DB default consistently.
- Legacy aliases like `chatgpt`, `claude`, and `grok-premium` should still route correctly or be rejected consistently.
- Regression risks:
- manual ticker analyze endpoint
- stale-refresh batch runs
- cost-estimation / model-label reporting in dashboard API

## Launch / Release Notes

- User-facing change summary:
- Automated stale-thesis refresh now follows the provider implied by the configured default LLM instead of assuming Grok transport.
- Operational notes:
- Re-run `Refresh Stale Theses + Hot Entry Alerts (daily)` after deployment and confirm stale thesis count decreases.
- Rollback notes:
- Revert the routing fix and reset `ai_model_default` to a Grok model as a temporary mitigation.

## Post-Launch Validation

- What to monitor:
- GitHub Actions stale-refresh workflow logs
- stale thesis count from `python3 scripts/refresh_stale_and_notify.py --days 7 --dry-run`
- thesis writes in `thesis_cache`
- How success will be confirmed:
- No `404 model does not exist` errors for cross-provider settings
- stale backlog decreases after workflow run
- Follow-up decision date:
- 2026-06-06

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Task: Fix LLM provider routing for stale-refresh workflow

Goal:
Make automated stale-thesis refresh use the correct provider and credentials for the configured default model from dashboard settings. Today the workflow can route into the xAI path while trying to use an OpenAI model name from strategy_config, which breaks refresh.

Verified root cause:
- `scripts/refresh_stale_and_notify.py` launches `ai_quant.py` without `--llm`
- `ai_quant.py` CLI default for `--llm` is hard-coded to `grok-4.3`
- `analyze_ticker()` routes by `llm.startswith(...)`
- `_call_claude()` then uses `AI_MODEL_DEFAULT` loaded from `strategy_config`
- with `ai_model_default=gpt-5.5`, the Grok/xAI path tries to call xAI model `gpt-5.5`
- GitHub workflow run `26972007769` shows repeated 404 errors: model `gpt-5.5` does not exist on xAI

Scope:
- `ai_quant.py`
- `scripts/refresh_stale_and_notify.py`
- `refresh_stale_theses.py`
- `.github/workflows/refresh_stale_theses.yml`
- `dashboard/api/main.py` only if needed for consistency in labels / estimates

Requirements:
- Resolve one effective model before provider routing
- Route provider strictly from the effective model, not from a separate hard-coded branch
- Explicit routing contract:
  - `grok-*` => xAI + `XAI_API_KEY`
  - `gpt-*` => OpenAI + `OPENAI_API_KEY`
  - `claude-*` => Anthropic + `ANTHROPIC_API_KEY`
- Ensure stale-refresh uses DB-configured default model unless explicit `--llm` override is passed
- Ensure the workflow fails clearly when provider credentials for the selected model are missing or invalid
- Preserve manual per-run explicit `--llm` override behavior

Non-goals:
- no prompt redesign
- no ranking / thesis logic changes
- no settings UI redesign
- no broad refactor outside the routing path

Tests / verification:
- add targeted regression tests for provider routing with:
  - `grok-4.3`
  - `gpt-5.5`
  - `claude-sonnet-4-6`
- include a regression test for stale-refresh path with DB default OpenAI model
- run targeted pytest for new tests
- run `python3 scripts/refresh_stale_and_notify.py --days 7 --dry-run`

Risk constraints:
- API behavior freeze outside provider/model routing
- no secrets in git
- keep changes surgical
```

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
