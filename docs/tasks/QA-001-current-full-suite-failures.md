# Task: Current Full-Suite Failure Audit

Status: proposed
Owner: Codex
Risk: api

## Objective

Audit the current `make verify-full` failures and separate environment-dependent failures from real regressions.

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

