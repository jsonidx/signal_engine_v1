# Task: Remove fixed Top-N caps from Telegram pipeline summary

Status: done
Stage: done
Type: bug
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Codex
Product Area: alerts
Category: reliability | ux | automation
Risk: none
Effort: S
Target Release: next
Due Date: 2026-06-12
Dependencies: none
Blocked By: none
Links: `scripts/notify_pipeline_result.py`, `run_master.sh`, `utils/ticker_selector.py`, `config.py`
Success Metric: the Telegram pipeline notification no longer uses any hardcoded `Top 5`, `Top 10`, or `Top 20` caps or labels, and instead shows every ticker that actually passed the relevant run filter.

## Problem Statement

The Telegram summary generated after the GitHub workflow still uses old fixed-cap language and query limits:

- `── TOP 10 RANKINGS ──`
- `── AI DEEP DIVE — Top 5 ──`
- `fetch_top10_rankings()` hard-limits rankings to 10 rows
- `fetch_top5_thesis()` hard-limits AI theses to 5 rows
- `run_master.sh` still logs `AI Quant synthesis (top 20 tickers...)`

That behavior no longer matches the current pipeline design. The repo now uses adaptive AI selection in `utils/ticker_selector.py`, where the actual count is driven by filters, quality gates, open-position inclusion, and event-queue additions. A hardcoded `Top N` message is therefore inaccurate even when the underlying run behaved correctly.

## User Impact

- Telegram can under-report what the pipeline actually processed.
- Operators cannot trust the count shown in the message.
- The workflow log suggests `top 20`, while the notifier shows `top 5`, and the selector itself is adaptive, creating unnecessary confusion during run review.

## Objective

Make the Telegram pipeline summary fully count-agnostic: no fixed `Top 5/10/20` wording and no fixed SQL limits. The summary should surface all tickers that actually made it through the relevant filter for that run.

## Proposed Solution

Update the Telegram notifier and related workflow wording so the message reflects real run output instead of stale static caps.

Expected behavior:

- Rankings section:
  - stop labeling the section as `Top 10`
  - stop truncating to 10 if more ranked rows are available for the latest run
  - show all rows returned by the current ranking dataset for that run
- AI deep-dive section:
  - stop labeling the section as `Top 5`
  - stop truncating to 5
  - show every thesis row associated with the current run's qualified AI output
- Workflow/log wording:
  - remove or reword any `top 20` phrasing that no longer reflects actual adaptive-cap behavior

Implementation should be driven by actual persisted run data, not by a new hardcoded replacement cap.

## Scope

Files or modules likely affected:

- `scripts/notify_pipeline_result.py`
- `run_master.sh`
- tests covering Telegram pipeline summary behavior, likely:
  - `tests/test_pipeline_defects.py`
  - any other notifier-focused tests if more appropriate

## Non-Goals

- Do not change ranking methodology.
- Do not change AI filtering thresholds, adaptive capacity, or trading logic.
- Do not redesign the Telegram message format beyond removing misleading fixed-cap behavior.
- Do not add a new arbitrary cap as a replacement for `5`, `10`, or `20`.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No trading logic changes.
- No secrets or generated artifacts in git.
- Keep the notifier robust against long messages by preserving or improving existing chunked Telegram sending.

## Acceptance Criteria

- Observable behavior:
  - Telegram message contains no fixed-cap labels like `Top 5`, `Top 10`, or `Top 20`.
  - The rankings section includes all ranking rows available for the latest run instead of an arbitrary first 10.
  - The AI section includes all theses that qualified for the run instead of an arbitrary first 5.
  - If only 4 names qualified, the message shows 4 without implying a missing cap.
  - If more than 20 names qualified, the message still shows all of them, split safely across Telegram chunks if needed.
- Tests:
  - add targeted tests proving the thesis/rankings section no longer uses fixed-cap labels
  - add tests covering message assembly when more than 5 thesis rows and more than 10 ranking rows exist
  - verify chunked sending still works for longer summaries
- Documentation:
  - inline comments only if needed to explain how run-scoped row selection works

## Verification Plan

- Targeted tests:
  - `pytest -q tests/test_pipeline_defects.py tests/test_telegram_notifications.py`
- Manual validation:
  - run the notifier against mocked data with 4, 12, and 25 qualifying rows
  - confirm the rendered section titles remain count-agnostic and all rows appear

## QA Notes

- Test scenarios:
  - 0 AI theses
  - 4 AI theses
  - 12 AI theses
  - 25 AI theses
  - 20 ranking rows from `daily_rankings`
- Edge cases:
  - open positions added outside normal dynamic slots
  - event-queue additions
  - stale-thesis refresh rows written on the same date
  - Telegram chunk boundaries near `TG_LIMIT`
- Regression risks:
  - duplicate rows if the run-scoping query is wrong
  - message order becoming unstable
  - longer messages being truncated if chunking is not respected

## Launch / Release Notes

- User-facing change summary: Telegram pipeline notifications now show all qualifying ranking and AI thesis rows for the run instead of stale `Top N` subsets.
- Operational notes: inspect the first live AI-cost run after rollout to confirm row counts and chunk ordering look correct.
- Rollback notes: revert notifier/query changes if message size or row attribution proves incorrect.

## Post-Launch Validation

- What to monitor:
  - first several Telegram pipeline summaries
  - row counts versus persisted `daily_rankings` and `thesis_cache`
  - chunk ordering for long messages
- How success will be confirmed:
  - Telegram count matches persisted run output with no misleading `Top N` copy
- Follow-up decision date:
  - after 3 live pipeline runs

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement BUG-003: remove fixed Top-N caps from the Telegram pipeline summary.

Problem:
- `scripts/notify_pipeline_result.py` still uses fixed-cap behavior:
  - rankings section header says `TOP 10`
  - AI section header says `Top 5`
  - rankings query is capped at 10
  - thesis query is capped at 5
- `run_master.sh` still says `AI Quant synthesis (top 20 tickers...)`
- current repo behavior is adaptive, driven by filters and persisted results, so the message is misleading

Goal:
- no fixed `Top 5`, `Top 10`, or `Top 20` labels
- no fixed SQL limits in the Telegram summary path
- show every ticker that actually qualified for that run
- preserve safe Telegram chunking for long messages

Scope:
- `scripts/notify_pipeline_result.py`
- `run_master.sh`
- targeted tests, likely in:
  - `tests/test_pipeline_defects.py`
  - `tests/test_telegram_notifications.py` if useful

Required changes:
- remove hardcoded `Top 10` and `Top 5` wording from the rendered message
- remove hardcoded query limits for rankings/theses in the notifier
- ensure ordering stays deterministic
- ensure the notifier still sends long messages safely using existing chunking
- reword `run_master.sh` logging so it does not claim a static `top 20` behavior if that is no longer true

Important behavior requirement:
- do not replace the old cap with a new arbitrary cap
- if 4 names qualify, show 4
- if 25 names qualify, show 25
- wording should stay count-agnostic or use the actual count, but must not imply a static ceiling

Non-goals:
- no trading-logic changes
- no ranking-methodology changes
- no selector-threshold changes

Tests / verification:
- `pytest -q tests/test_pipeline_defects.py tests/test_telegram_notifications.py`
- add coverage for >5 thesis rows, >10 ranking rows, and chunk-safe long messages
```

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
