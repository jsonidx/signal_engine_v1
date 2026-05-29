# Task: Telegram Approval Requests For Trading Logic

Status: implemented
Stage: awaiting QA
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

## Tracking Note

Code shipped in commit **c8f3481** ("Add EARLY_ARMED squeeze training, calibration, and approval workflows", 2026-05-29).
Covers: approval_requests table, save/fetch/update helpers, /approve /reject /pending bot commands, notify_approval_request() notifier.
Status: implemented and on main, but no live approval workflow has been tested end-to-end in production.
Action required: confirm at least one approval request has been created, notified, and resolved through Telegram before moving to finished.
