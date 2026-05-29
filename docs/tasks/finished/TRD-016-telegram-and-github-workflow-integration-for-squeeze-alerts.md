# Task: Telegram And GitHub Workflow Integration For Squeeze Alerts

Status: done
Stage: done
Type: feature
Priority: P2
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: alerts
Category: automation
Risk: api
Effort: M
Target Release: squeeze roadmap
Due Date: TBD
Dependencies: TRD-011, TRD-015
Blocked By: none
Links: docs/workflows/ai-agent-workflow.md
Success Metric: Telegram and workflow-driven notifications accurately reflect the final squeeze state semantics and approval flow.

## Problem Statement

Core squeeze-state changes and approval workflows will create new notification requirements, but the Telegram and GitHub workflow integration layer is not yet aligned to those future semantics.

## User Impact

If integration work lags behind the core logic, users will receive misleading alert language or incomplete approval notifications even when the underlying product behavior is correct.

## Objective

Update Telegram notifications and GitHub workflow integration so the new squeeze alert states and approval-request flow are surfaced correctly after the core squeeze-ticket implementation is complete.

## Proposed Solution

Update notifier formatting, bot behavior, and workflow environment wiring so delivery channels reflect the final state definitions and approval lifecycle without redefining core trading logic.

## Scope

- `scripts/notify_pipeline_result.py`
- `scripts/telegram_bot.py`
- `.github/workflows/` if present in the active deployment path
- `docs/workflows/ai-agent-workflow.md`
- `dashboard/api/main.py` if approval notifications or dry-run endpoints need parity
- tests covering notifier / bot formatting and command behavior

## Non-Goals

- Do not redesign squeeze logic in this ticket.
- Do not change alert thresholds here unless required by a formatting contract fix.
- Do not implement core Supabase training-dataset logic here; assume prior tickets handle it.

## Constraints

- Telegram output must reflect final state semantics from the core squeeze tickets:
  - `EARLY_ARMED` = early setup / entry hunting
  - `ARMED` = stronger setup / pre-breakout watch
  - `ACTIVE_SQUEEZE` = move in progress / chase risk high
- Workflow-driven messages and bot-driven messages must stay consistent in terminology.
- Any new environment variables required by approval flow or notifier behavior must be documented and wired through the workflow runtime.
- Message formatting must remain compact enough for Telegram limits and existing chunking logic.

## Acceptance Criteria

- Pipeline Telegram summaries can display `EARLY_ARMED` alerts with appropriate wording.
- `ACTIVE_SQUEEZE` text is updated so it no longer reads like the preferred fresh-entry alert when shown in Telegram.
- Approval-needed Telegram notifications are supported for pending trading-logic requests created by the approval workflow.
- GitHub workflow runtime has access to any required Telegram / approval env vars, with docs updated accordingly.
- Tests cover at minimum:
  - `EARLY_ARMED` squeeze message formatting
  - `ARMED` message formatting
  - `ACTIVE_SQUEEZE` chase-risk wording
  - approval-needed notification formatting
  - approve / reject bot command path if changed by integration work

## Verification Plan

- Run targeted notifier / bot tests.
- Run a dry-run of `scripts/notify_pipeline_result.py` and verify final Telegram text for the new squeeze states.
- Run a dry-run or mocked bot flow for an approval request.
- Verify workflow docs list required env vars and runtime expectations.
- `make verify`

## QA Notes

- Test scenarios: notifier summaries, approval-needed notifications, workflow env propagation, and bot command compatibility.
- Edge cases: message length limits, missing env vars, and inconsistent state names across producers.
- Regression risks: misleading Telegram copy or broken workflow notifications after schema changes.

## Launch / Release Notes

- User-facing change summary: Telegram messages and workflow-triggered notifications match the new squeeze semantics.
- Operational notes: confirm deployment runtime has all required env vars before enabling.
- Rollback notes: revert notifier formatting or disable approval notifications if integration breaks.

## Post-Launch Validation

- What to monitor: message formatting quality, approval notification delivery, and workflow runtime errors.
- How success will be confirmed: dry-run and production-like notification paths produce consistent text for the same alert state.
- Follow-up decision date: after the first end-to-end workflow run using the new semantics.

## Handoff Notes

This ticket should be worked only after the core squeeze tickets settle the final schema and alert semantics. Its purpose is to keep Telegram and workflow-triggered notifications aligned with the new product behavior, not to define that behavior itself.
