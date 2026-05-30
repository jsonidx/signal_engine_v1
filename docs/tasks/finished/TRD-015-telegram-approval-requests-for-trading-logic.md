# Task: Telegram Approval Requests For Trading Logic

Status: done
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: api
Category: compliance
Risk: api
Effort: M
Target Release: squeeze roadmap
Due Date: TBD
Dependencies: TRD-013
Blocked By: none
Links: docs/workflows/ai-agent-workflow.md
Success Metric: approval requests can be created, reviewed, and resolved through Telegram with auditable state changes.

## Problem Statement

The roadmap requires human approval for trading-logic changes, but there is no structured approval-request workflow that connects proposed changes, stored evidence, and Telegram actions.

## User Impact

Without an approval gate, autonomous analysis cannot safely progress into actionable change proposals, and human review remains manual and easy to lose track of.

## Objective

Add an approval-request workflow so Claude can notify the user through Telegram when a proposed trading-logic or model-calibration change needs human approval, and the user can approve or reject that proposal from Telegram.

## Proposed Solution

Persist approval requests in Supabase with explicit statuses, add notifier and bot command flows for Telegram, and document the approval lifecycle so trading-logic changes remain auditable.

## Scope

- `scripts/telegram_bot.py`
- `scripts/notify_pipeline_result.py` or a new approval notifier script
- `utils/supabase_persist.py`
- `utils/db.py`
- `schema.sql`
- `migrations/`
- `docs/workflows/ai-agent-workflow.md`
- `tests/test_squeeze_persistence_schema.py`
- bot-specific tests if added

## Non-Goals

- Do not auto-apply live trading-logic changes without human approval.
- Do not expose secrets or approval tokens in Telegram messages.
- Do not require Telegram as the only approval path; DB or dashboard approval can remain possible later.

## Constraints

- Approval requests must be persisted in Supabase with stable IDs and explicit status fields.
- Telegram messages must be concise and include:
  - proposal ID
  - proposal type
  - short evidence summary
  - approve / reject command syntax
- The workflow must distinguish low-risk informational notifications from approval-gated trading-logic changes.
- Approval state transitions must be auditable.

## Acceptance Criteria

- A Supabase-backed approval-request table exists, for example `approval_requests`, with fields at minimum:
  - `request_id`
  - `created_at`
  - `category`
  - `risk_level`
  - `title`
  - `summary`
  - `evidence_ref`
  - `proposed_change_json`
  - `status` (`PENDING`, `APPROVED`, `REJECTED`, `EXPIRED`, `APPLIED`)
  - `approved_by`
  - `approved_at`
- Claude or the calibration workflow can create a pending approval request when a trading-logic change is proposed.
- Telegram can notify the user when a new pending approval request is created.
- The Telegram bot accepts commands such as:
  - `/approve <request_id>`
  - `/reject <request_id>`
  - optional `/pending`
- Approval or rejection updates Supabase and sends confirmation back to Telegram.
- Documentation: the human approval gate is documented clearly in the AI workflow doc.

## Verification Plan

- Targeted tests for request creation, status transitions, and Telegram command parsing.
- Create a dummy pending request in local/dev DB and verify the bot can approve and reject it.
- Verify that a rejected request cannot be applied.
- `make verify`

## QA Notes

- Test scenarios: create request, notify, approve, reject, list pending, and block duplicate state transitions.
- Edge cases: invalid request IDs, expired requests, and repeated approve/reject commands.
- Regression risks: approval state inconsistency or accidental bypass of the human gate.

## Launch / Release Notes

- User-facing change summary: Telegram can now surface and resolve approval-needed trading-logic requests.
- Operational notes: env vars, bot permissions, and DB migrations must be documented before rollout.
- Rollback notes: disable Telegram approval commands and fall back to manual approval if needed.

## Post-Launch Validation

- What to monitor: creation-to-resolution flow, failed command rates, and approval audit completeness.
- How success will be confirmed: pending requests can be approved or rejected end-to-end without manual DB edits.
- Follow-up decision date: after the first real trading-logic approval cycle.

## Handoff Notes

This task is the control point for safe autonomous learning:

- Claude may collect data, label outcomes, and prepare evidence automatically.
- Claude may propose threshold or scoring changes automatically.
- Claude may notify automatically through Telegram.
- Claude must not apply live trading-logic changes until the user explicitly approves them.

This workflow should support future squeeze-learning tasks such as:

- `EARLY_ARMED` threshold changes
- score-weight changes
- taxonomy label rule updates
- promotion of a calibrated probability model into live scoring

Paste-ready Codex QA prompt:

```text
Codex QA for TRD-012, TRD-013, TRD-014, and TRD-015.

Ticket summary:
- TRD-012: verify the live Supabase training dataset path is working end-to-end.
- TRD-013: verify the calibration workflow can run on real labeled data and produce a report.
- TRD-014: verify taxonomy labels are persisted correctly in live training outcomes.
- TRD-015: verify the Telegram approval-request workflow works end-to-end with auditable DB state transitions.

Combined objective:
Use repo-local tests plus live environment checks to determine whether these four tickets are truly ready to move from `qa` to `done`. Do not mark any ticket done unless its external acceptance evidence is present.

Exact scope:
- `docs/tasks/in-progress/TRD-012-supabase-squeeze-training-dataset.md`
- `docs/tasks/in-progress/TRD-013-squeeze-probability-calibration-and-review-gate.md`
- `docs/tasks/in-progress/TRD-014-squeeze-alert-outcome-taxonomy.md`
- `docs/tasks/in-progress/TRD-015-telegram-approval-requests-for-trading-logic.md`
- `migrations/003_squeeze_training_and_approvals.sql`
- `utils/supabase_persist.py`
- `backtest.py`
- `scripts/squeeze_calibration.py`
- `scripts/telegram_bot.py`
- `scripts/notify_pipeline_result.py`
- related tests under `tests/test_squeeze_persistence_schema.py`, `tests/test_squeeze_replay.py`, and `tests/test_telegram_notifications.py`

Required verification:
1. Run local automated coverage:
   `pytest tests/test_squeeze_state_machine.py tests/test_squeeze_alerts.py tests/test_squeeze_replay.py tests/test_squeeze_persistence_schema.py tests/test_telegram_notifications.py -q`
2. TRD-012:
   - Confirm migration `003_squeeze_training_and_approvals.sql` is applied in the live Supabase environment.
   - Confirm at least one live `squeeze_training_snapshots` row exists from the pipeline.
   - Confirm at least one related `squeeze_training_outcomes` row exists or clearly document that forward windows are not yet closed.
3. TRD-013:
   - Run `python3 scripts/squeeze_calibration.py` against real labeled data if available.
   - Confirm a real calibration report is written under `reports/`.
   - If sample size is insufficient, leave the ticket in `qa` and record the exact blocker.
4. TRD-014:
   - Query live `squeeze_training_outcomes` rows and verify taxonomy labels are being written as expected.
   - Confirm labels are reproducible from the code rules, not manual edits.
5. TRD-015:
   - Create a real or controlled test `approval_requests` row.
   - Verify notification formatting.
   - Verify `/pending`, `/approve <id>`, and `/reject <id>` or equivalent handler flow updates DB state correctly.
   - Confirm auditable status transitions in Supabase.

Non-goals:
- Do not change trading logic, thresholds, schema, or Telegram bot behavior while doing QA.
- Do not mark a ticket done from unit tests alone when its acceptance criteria require live DB or Telegram evidence.
- Do not refactor implementation code.

Risk constraints:
- Treat TRD-013 and TRD-014 as `trading-logic`-adjacent verification work; do not alter scoring behavior.
- Treat TRD-015 as approval-gate infrastructure; verify that rejected or non-pending requests cannot bypass the guard.

Required output:
- For each ticket, explicitly state `done` or `remain in qa`.
- Cite the exact evidence used.
- If blocked, state the missing evidence in one sentence.
- If QA passes, update `Status:` to `done`, `Stage:` to `done`, add the verification summary in the ticket, and run `python3 scripts/sync_task_status.py`.
```

## Tracking Note

Code shipped in commit **c8f3481** ("Add EARLY_ARMED squeeze training, calibration, and approval workflows", 2026-05-29).
Covers: approval_requests table, save/fetch/update helpers, /approve /reject /pending bot commands, notify_approval_request() notifier.
Status: implemented and on main, but no live approval workflow has been tested end-to-end in production.
Action required: confirm at least one approval request has been created, notified, and resolved through Telegram before moving to finished.

## QA Verification Summary

- 2026-05-30: local coverage passed via `pytest tests/test_squeeze_state_machine.py tests/test_squeeze_alerts.py tests/test_squeeze_replay.py tests/test_squeeze_persistence_schema.py tests/test_telegram_notifications.py -q` with `282 passed in 5.11s`.
- 2026-05-30: created controlled live Supabase requests `qa-trd015-ad505633-a` and `qa-trd015-ad505633-r`, exercised `scripts.telegram_bot.handle_command()` for `/pending`, `/approve <id>`, and `/reject <id>`, and verified DB transitions to `APPROVED` and `REJECTED` with `approved_by='telegram'`, non-null `approved_at`, and updated audit timestamps.
- 2026-05-30: verified duplicate-state guard by attempting `/reject qa-trd015-ad505633-a` after approval; the handler returned the expected failure message and the row stayed `APPROVED`.
- 2026-05-30: verified live Telegram delivery for notifier path with controlled request `qa-trd015-19b12e30-live`; `scripts.notify_pipeline_result.tg_send()` returned `True`, the approval-request message included `/approve qa-trd015-19b12e30-live` and `/reject qa-trd015-19b12e30-live`, and the QA row was then cleaned up to `REJECTED` in Supabase.
