# Task: Current Full-Suite Failure Audit

Status: proposed
Stage: ready
Type: qa
Priority: P1
Severity: medium
Owner: Codex
Reviewer: Human
Product Area: infra
Category: reliability
Risk: api
Effort: M
Target Release: backlog
Due Date: TBD
Dependencies: none
Blocked By: none
Links: none
Success Metric: every current full-suite failure is classified and each product bug has a follow-up ticket.

## Problem Statement

`make verify-full` currently has unresolved failures, but they are not separated cleanly between environment issues, external dependency flakiness, test defects, and real product regressions.

## User Impact

Without a clean audit, the team cannot trust the full verification signal, which slows releases and makes genuine regressions harder to prioritize.

## Objective

Audit the current `make verify-full` failures and separate environment-dependent failures from real regressions.

## Proposed Solution

Run the current full suite, classify each failure into a stable bucket, document the evidence for that classification, and create follow-up implementation tickets only where product defects are confirmed.

## Scope

- `tests/test_supabase_integration.py`
- `tests/test_db_wal.py`
- `tests/test_iv_calculator.py`
- `tests/test_iv_rank.py`
- `tests/test_marketaux.py`
- `tests/test_squeeze_explanations.py`
- `tests/test_squeeze_persistence_schema.py`
- `tests/test_squeeze_replay.py`
- `tests/test_universe_builder.py`

## Non-Goals

- Do not refactor implementation modules.
- Do not change trading logic.
- Do not change database schema.

## Constraints

- Codex may classify failures and propose fixes.
- Claude Code should implement any required code changes later.

## Acceptance Criteria

- Each failure is classified as environment, flaky external dependency, test defect, or product bug.
- A follow-up task exists for each product bug.
- No trading logic changes are made during the audit.

## Verification Plan

- `make verify`
- `make verify-full` where network and Supabase access are available.

## QA Notes

- Test scenarios: local `make verify`, full-suite run with network and Supabase access where possible.
- Edge cases: flaky third-party APIs, missing env vars, and local-only infra assumptions.
- Regression risks: low, because this task should not change product logic.

## Launch / Release Notes

- User-facing change summary: none; internal QA triage only.
- Operational notes: use findings to drive follow-up bug tickets and test-hardening work.
- Rollback notes: not applicable.

## Post-Launch Validation

- What to monitor: whether follow-up bugs are closed and the full suite becomes stable again.
- How success will be confirmed: subsequent `make verify-full` runs fail only on known, documented blockers or pass cleanly.
- Follow-up decision date: after the next full verification cycle.
