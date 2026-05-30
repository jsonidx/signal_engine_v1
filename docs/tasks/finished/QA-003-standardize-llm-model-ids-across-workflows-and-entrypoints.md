# Task: Standardize LLM Model IDs Across Workflows and Entrypoints

Status: done
Stage: done
Type: bug
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: infra
Category: reliability
Risk: api
Effort: S
Target Release: next patch
Due Date: 2026-06-02
Dependencies: QA-002-verify-fails-on-tickerpage-llm-default
Blocked By: none
Links: https://github.com/jsonidx/signal_engine_v1/actions/runs/26654094698, https://github.com/jsonidx/signal_engine_v1/commit/73163598b2c59aba35a458891fe8a2108e969c04
Success Metric: all first-party workflow/script entrypoints use exact current model IDs by default, and no internal path depends on legacy aliases for normal operation.

## Problem Statement

The recent LLM migration moved the dashboard picker to exact model IDs such as `grok-4.3`, `gpt-5.1`, `gpt-5.5`, `gpt-5.5-pro`, `claude-sonnet-4-6`, and `claude-opus-4-8`. The failed `Verify` workflow exposed one hard regression in the frontend, but repository review shows a broader consistency gap: several internal entrypoints still default to legacy aliases like `grok`, `grok-premium`, `claude`, and `chatgpt`.

This does not currently fail everywhere because backend and CLI compatibility for legacy aliases still exists. However, normal operation across scripts, workflows, and API boundaries still relies on those aliases in multiple places, which creates hidden migration debt and future break risk.

Examples found during repo audit:

- `dashboard/frontend/src/lib/api.ts` defaults analyze requests to `grok`
- `dashboard/api/main.py` defaults `AnalyzeRequest.llm` to `grok`
- `ai_quant.py` defaults `--llm` and internal analysis functions to `grok`
- refresh scripts and workflow-triggered scripts often omit `--llm` entirely and therefore inherit legacy defaults transitively

## User Impact

The team cannot rely on a single clean LLM contract across the product. Future refactors that tighten validation, remove aliases, or add model-specific behavior could silently break GitHub workflows, dashboard actions, or batch scripts even if the UI appears correct.

## Objective

Standardize first-party internal defaults and workflow-driven entrypoints on exact supported model IDs while preserving backward compatibility for legacy aliases at external/API boundaries where necessary.

## Proposed Solution

Audit the main analysis entrypoints and convert internal defaults from legacy aliases to exact model IDs. Keep alias acceptance as a compatibility layer, but stop depending on it internally.

At minimum, align:

- frontend analyze request defaults
- dashboard API analyze request defaulting and estimated-model logic
- `ai_quant.py` CLI/default parameter values
- workflow-invoked scripts that currently inherit old defaults implicitly

Where backward compatibility is intentionally preserved, make that explicit in comments or validation logic rather than leaving legacy aliases as the operational default.

## Scope

Files or modules likely affected:

- `dashboard/frontend/src/lib/api.ts`
- `dashboard/api/main.py`
- `ai_quant.py`
- `scripts/refresh_stale_and_notify.py`
- `refresh_stale_theses.py`
- `run_master.sh`
- `.github/workflows/analyze_tickers.yml`
- `.github/workflows/daily_pipeline.yml`
- `.github/workflows/manual_pipeline.yml`
- `.github/workflows/refresh_stale_theses.yml`

## Non-Goals

- Do not remove legacy alias support outright unless all first-party callers are migrated and compatibility impact is explicitly handled.
- Do not redesign the LLM catalog or pricing model.
- Do not change trading logic or thesis-generation logic.
- Do not refactor unrelated workflow structure.

## Constraints

- Keep backward compatibility for existing stored values, external callers, or manual commands where reasonable.
- Prefer exact model IDs for all first-party defaults and workflow-owned calls.
- No secrets or generated artifacts in git.
- Keep the change narrow to LLM identifier consistency, not provider behavior changes.

## Acceptance Criteria

- Observable behavior: dashboard analyze actions and workflow/script-driven analysis runs use exact current model IDs by default.
- Observable behavior: legacy aliases remain accepted where needed for backward compatibility, or any removal is explicitly documented and validated.
- Tests: `make verify` passes.
- Tests: any targeted command or test coverage added for default LLM routing passes.
- Documentation: help text or usage examples that present legacy aliases as the normal default are updated where touched.

## Verification Plan

- `make verify`
- Targeted checks:
- `cd dashboard/frontend && npm run build`
- Validate the Ticker analyze flow default request value uses an exact model ID.
- Run or inspect the main `ai_quant.py` entrypoint with default arguments and confirm the resolved default path is an exact model ID.
- Confirm workflow-owned scripts no longer depend on implicit legacy alias defaults for normal operation.

## QA Notes

- Test scenarios: dashboard-triggered analyze, CLI-triggered analyze, stale-thesis refresh path, and workflow entrypoints that run AI analysis.
- Edge cases: backward compatibility for legacy aliases submitted manually or from stored state.
- Regression risks: medium, because this touches multiple entrypoints and request/defaulting layers.

## Launch / Release Notes

- User-facing change summary: internal LLM model selection is standardized on exact model IDs to reduce workflow and dashboard drift.
- Operational notes: rerun `Verify` and manually inspect one workflow-owned analysis path after merge.
- Rollback notes: revert the narrow model-ID normalization changes if any entrypoint stops launching analysis jobs.

## Post-Launch Validation

- What to monitor: the next `Verify` run and the next workflow-triggered analysis or stale-refresh execution.
- How success will be confirmed: no first-party path relies on `grok`/`claude`/`chatgpt` aliases as the default operational value.
- Follow-up decision date: 2026-06-03

## QA Result (2026-05-30)

QA passed. All first-party defaults now use exact model IDs (`grok-4.3`). Legacy aliases preserved as accepted inputs at API and CLI boundaries. `make verify` completed cleanly: 623 Python tests passed, 37 frontend tests passed, frontend build clean, import smoke checks all green. Ticket closed and moved to `docs/tasks/finished/`.

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Task: Standardize internal LLM defaults on exact model IDs across workflows and entrypoints

Goal:
Remove first-party reliance on legacy LLM aliases (`grok`, `grok-premium`, `claude`, `chatgpt`) as operational defaults, while preserving backward compatibility where needed.

Context:
- The frontend picker was migrated to exact model IDs, but repo audit found several internal defaults still using legacy aliases.
- This caused one hard CI failure already in TickerPage, and there is broader migration debt in API, CLI, and workflow-triggered paths.
- Workflow YAML files mostly do not pass model names directly; they invoke scripts that still inherit legacy defaults.

Findings with file paths:
- `dashboard/frontend/src/lib/api.ts` still defaults `tickerAnalyze(..., llm='grok')`
- `dashboard/api/main.py` still defaults `AnalyzeRequest.llm` to `grok` and still routes compatibility aliases
- `ai_quant.py` still defaults internal analysis functions and CLI `--llm` to `grok`
- `scripts/refresh_stale_and_notify.py` calls `ai_quant.py` without an explicit `--llm`
- `refresh_stale_theses.py` docs/help still present legacy alias examples like `grok-premium`
- `run_master.sh` and workflow-owned analysis paths rely on inherited defaults

Exact scope:
- `dashboard/frontend/src/lib/api.ts`
- `dashboard/api/main.py`
- `ai_quant.py`
- `scripts/refresh_stale_and_notify.py`
- `refresh_stale_theses.py`
- `run_master.sh`
- `.github/workflows/analyze_tickers.yml`
- `.github/workflows/daily_pipeline.yml`
- `.github/workflows/manual_pipeline.yml`
- `.github/workflows/refresh_stale_theses.yml`

Required outcome:
- First-party defaults use exact current model IDs.
- Workflow-owned or script-owned analysis paths should not depend on legacy aliases implicitly.
- Keep legacy aliases accepted only as backward-compatibility inputs where appropriate.

Non-goals:
- No provider/routing redesign.
- No trading logic changes.
- No broad workflow refactor.
- Do not remove backward compatibility unless you can prove no first-party or persisted caller still depends on it.

Constraints:
- Keep the fix targeted to identifier consistency.
- Preserve external/manual compatibility where reasonable.
- No secrets or generated artifacts in git.

Verification:
- `cd dashboard/frontend && npm run build`
- `make verify`
- Add or run targeted checks if needed to prove default request/default CLI behavior now resolves to exact model IDs.

Deliverable:
- A narrow set of code updates standardizing internal defaults, plus a concise summary of the remaining backward-compatibility layer and verification results.
```

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
