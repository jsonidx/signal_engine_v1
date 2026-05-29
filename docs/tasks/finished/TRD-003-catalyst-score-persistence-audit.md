# Task: Catalyst Score Persistence Audit

Status: completed
Stage: done
Type: bug
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: reliability
Risk: trading-logic
Effort: S
Target Release: completed
Due Date: completed
Dependencies: none
Blocked By: none
Links: none
Success Metric: persisted catalyst composite scores match the calculated signal evidence instead of silently zeroing out.

## Problem Statement

Persisted catalyst composite values could be `0.0` even when the underlying catalyst components were nonzero.

## User Impact

Users and downstream ranking logic could lose meaningful catalyst evidence, reducing trust in alert quality and historical analysis.

## Objective

Audit and fix why `catalyst_scores.composite` can persist as `0.0` while component scores and flags are nonzero, so useful catalyst evidence is not lost before ranking and alerting.

## Proposed Solution

Trace the persistence path, fix the composite write behavior, and add regression coverage for nonzero-component rows.

## Scope

- `catalyst_screener.py`
- `utils/supabase_persist.py`
- `schema.sql`
- `tests/test_pipeline_defects.py`
- `tests/test_supabase_integration.py`

## Non-Goals

- Do not redesign catalyst scoring weights in this task.
- Do not change squeeze scoring thresholds.
- Do not require live Supabase for unit tests.

## Constraints

- Preserve backward compatibility with existing `catalyst_scores` columns.
- If the current `0.0` is intentional, document the exact reason and expose a separate actionable score.
- Add a regression test that would have caught nonzero components with zero composite.

## Acceptance Criteria

- Observable behavior: persisted `composite` matches the calculated catalyst composite or a clearly named normalized value.
- Rows with nonzero volume/options/technical/dark-pool components do not silently persist as zero unless explicitly disqualified with a reason.
- Tests: add a mock persistence test for a row with nonzero components.
- Documentation: add handoff notes explaining whether historical rows need backfill.

## Verification Plan

- `pytest tests/test_pipeline_defects.py tests/test_supabase_integration.py -v`
- Run `python3 catalyst_screener.py --ticker SNOW` and inspect the computed vs persisted values in dry-run or test mode.
- `make verify`

## Handoff Notes

For `SNOW`, May 25-28 `catalyst_scores` had nonzero component fields such as volume/options/technical and many flags, but `composite` was `0.0`. That likely suppressed useful pre-catalyst evidence downstream.
