# Task: Uncap AI Quant selection for all filter-qualified tickers

Status: done
Stage: done
Type: bug
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Codex
Product Area: trading-logic
Category: automation | reliability
Risk: trading-logic
Effort: M
Target Release: next
Due Date: 2026-06-12
Dependencies: none
Blocked By: none
Links: `config.py`, `utils/ticker_selector.py`, `ai_quant.py`, `run_master.sh`, `scripts/notify_pipeline_result.py`, `docs/tasks/new/BUG-003-remove-fixed-topn-caps-from-telegram-pipeline-summary.md`
Success Metric: every ticker that passes the normal AI filter is selected for AI Quant analysis in full-AI runs, with no fixed cap at 5, 8, 20, or any other static ceiling.

## Problem Statement

The repo still imposes a real hard cap on AI Quant selection even after a ticker passes the normal filter.

Current behavior:

- `run_master.sh` calls `python3 ai_quant.py --top-n 20`
- `ai_quant.py` passes that into `select_top_tickers()`
- `utils/ticker_selector.py` clamps normal selection using:
  - `AI_QUANT_MAX_TICKERS`
  - `AI_QUANT_CAPACITY_MIN`
  - `AI_QUANT_CAPACITY_MAX`
- `config.py` currently sets:
  - `AI_QUANT_MAX_TICKERS = 5`
  - `AI_QUANT_CAPACITY_MAX = 8`
  - `EVENT_QUEUE_MAX_AI_SLOTS = 3`

As a result, the normal full-AI workflow does not analyze all qualifying names. It analyzes only a bounded subset of filtered names, plus additive open positions and limited event-queue entries.

Observed runtime history confirms this is not just a display problem. Uploaded pipeline reports show `AI Quant: processing X tickers` values such as `6`, `8`, `9`, `12`, `15`, and `16`, rather than “all tickers that passed the filter.”

## User Impact

- Strong qualifying names can be filtered in but never analyzed by AI Quant because they fall below the hard cap.
- The pipeline gives a partial AI view of the qualified set rather than full coverage of the filtered cohort.
- Operators cannot rely on the full-AI workflow to analyze every name that made it through the filter.

## Objective

Remove the AI Quant selection cap so that, in the normal full-AI workflow, every ticker that passes the intended AI filter is analyzed by AI Quant.

## Proposed Solution

Change the AI selection path so that filter qualification, not a static or adaptive capacity ceiling, determines which tickers are analyzed.

Target behavior:

- if a ticker passes the normal AI filter, it should be included in the AI Quant run
- no artificial truncation to 5, 8, 20, or similar
- no separate additive side-paths that effectively preserve an old capped core and only add exceptions on top

Implementation direction:

- remove or neutralize the hard-cap logic in `utils/ticker_selector.py`
- retire config-driven ceilings that constrain normal AI selection
- make `run_master.sh` and `ai_quant.py` wording reflect that selection is now filter-driven rather than top-N driven
- ensure downstream reporting reflects the true selected set

This ticket is about uncapping selection in the actual AI pipeline, not just updating Telegram copy.

## Scope

Files or modules likely affected:

- `config.py`
- `utils/ticker_selector.py`
- `ai_quant.py`
- `run_master.sh`
- `scripts/notify_pipeline_result.py`
- targeted tests for selector / AI run behavior, likely:
  - `tests/test_ticker_selector.py`
  - `tests/test_ai_quant_schema.py`
  - additional notifier/report tests if selection count wording is asserted

## Non-Goals

- Do not change the filter criteria themselves unless needed only to preserve current behavior after cap removal.
- Do not redesign prompt content.
- Do not change ranking methodology.
- Do not add a new replacement cap under a different name.
- Do not address API cost management by silently reintroducing other selection ceilings.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- Risk is `trading-logic`; changes must stay tightly scoped to selection/cap behavior.
- No secrets or generated artifacts in git.
- Preserve deterministic filter behavior and ordering.

## Acceptance Criteria

- Observable behavior:
  - a full-AI run analyzes every ticker that passes the intended AI filter
  - no config or selector hard ceiling truncates the qualified set
  - `AI Quant: processing X tickers` equals the number of filter-qualified tickers for that run, plus any intentionally always-included names that already satisfy the workflow contract
  - messaging/logging no longer implies `top-N` behavior where none exists
- Tests:
  - add selector tests showing that when 25 names pass the filter, all 25 are selected
  - add tests confirming weak days still select only the names that truly qualify, without force-filling
  - add tests for open-position and event-queue handling after cap removal
- Documentation:
  - update inline comments and any nearby docs that still describe adaptive-cap or top-N AI selection

## Verification Plan

- Targeted tests:
  - `pytest -q tests/test_ticker_selector.py tests/test_ai_quant_schema.py`
- Manual validation:
  - run a dry-run or equivalent selection path with a synthetic qualified set larger than 20
  - confirm the selected count matches the number of qualified names
  - inspect pipeline/log output for accurate count reporting

## QA Notes

- Test scenarios:
  - 0 qualified names
  - 3 qualified names
  - 8 qualified names
  - 25 qualified names
  - 25 qualified names plus open positions
  - 25 qualified names plus event-queue additions
- Edge cases:
  - same-day cache hits
  - governance quarantine
  - force ticker mode
  - `--no-limit` behavior after normal mode becomes effectively uncapped
- Regression risks:
  - unintended explosion in API cost
  - duplicate selection paths
  - stale comments/docs continuing to describe capped behavior

## Launch / Release Notes

- User-facing change summary: full-AI runs now analyze all tickers that pass the AI filter instead of a capped subset.
- Operational notes: monitor the first live uncapped run for total selected count, API cost, and runtime.
- Rollback notes: revert selection-cap removal if operational cost or runtime becomes unacceptable.

## Post-Launch Validation

- What to monitor:
  - `AI Quant: processing X tickers` in pipeline reports
  - run duration
  - total AI cost
  - selected-count distribution after rollout
- How success will be confirmed:
  - selected count matches the full qualified set rather than a capped subset
- Follow-up decision date:
  - after 3 live full-AI runs

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement BUG-004: uncap AI Quant selection so every ticker that passes the normal AI filter is analyzed.

Problem:
- the repo still has a real selection cap in the normal AI path
- `run_master.sh` calls `python3 ai_quant.py --top-n 20`
- `ai_quant.py` forwards into `select_top_tickers()`
- `utils/ticker_selector.py` clamps selection using config ceilings
- current config includes:
  - `AI_QUANT_MAX_TICKERS = 5`
  - `AI_QUANT_CAPACITY_MAX = 8`
  - bounded event-queue additive logic
- this means qualified names can still be filtered in but never analyzed by AI Quant

Goal:
- in normal full-AI runs, every ticker that passes the intended AI filter must be analyzed by AI Quant
- no hard cap at 5, 8, 20, or any replacement arbitrary ceiling

Scope:
- `config.py`
- `utils/ticker_selector.py`
- `ai_quant.py`
- `run_master.sh`
- `scripts/notify_pipeline_result.py` if needed so reporting matches actual selection behavior
- targeted tests, especially:
  - `tests/test_ticker_selector.py`
  - `tests/test_ai_quant_schema.py`

Required changes:
- remove or neutralize capped normal-slot logic in `select_top_tickers()`
- stop truncating the qualified set in normal AI mode
- keep deterministic ordering of selected names
- preserve existing filter rules unless strictly necessary to adjust for cap removal
- update logs/comments/help text that still describe top-N or adaptive-cap behavior

Important behavior requirement:
- if 25 names pass the normal AI filter, select 25
- if 3 names pass, select 3
- do not force-fill weak days
- do not add a new cap under a different config name

Non-goals:
- no prompt redesign
- no ranking-methodology changes
- no unrelated trading-logic refactor

Risk constraints:
- this is `trading-logic`, so keep the patch narrow and test-backed

Tests / verification:
- `pytest -q tests/test_ticker_selector.py tests/test_ai_quant_schema.py`
- add coverage for qualified-set sizes above the old cap
- verify logs/reporting show the real selected count
```

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
