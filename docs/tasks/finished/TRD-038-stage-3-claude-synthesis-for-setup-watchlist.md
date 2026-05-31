# Task: Stage 3 Claude Synthesis For Setup Watchlist

Status: completed
Stage: done
Type: feature
Priority: P2
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: alerts
Category: automation
Risk: trading-logic
Effort: M
Target Release: pre-breakout-v1
Due Date: TBD
Dependencies: TRD-034, TRD-035, TRD-036
Blocked By: none
Links: TRD-032
Success Metric: the pre-breakout pipeline can enrich a bounded shortlist with structured archetype and invalidation metadata without using Claude for ranking or signal generation.

## Problem Statement

The program needs a narrow place for Claude to add judgment without allowing the LLM to contaminate deterministic screening.

## User Impact

Without a bounded Stage 3, the team either gets no qualitative synthesis on shortlisted setups or overuses the LLM as an uncontrolled screener.

## Objective

Add a constrained Stage 3 Claude synthesis step for setup-watchlist names that clear deterministic thresholds.

## Proposed Solution

Create a structured prompt/input-output contract where Claude receives ranked deterministic setup rows and returns only archetype, invalidation condition, setup grade, and key risk.

## Scope

- Stage 3 runner/prompt wiring
- persistence fields for Stage 3 outputs if not already present
- tests for schema/contract behavior

## Non-Goals

- Do not let Claude rank or override deterministic scores.
- Do not produce trade recommendations, price targets, or free-form essays.
- Do not run on more than the approved daily cap.

## Constraints

- Maximum 10 names per day.
- Structured JSON in and structured JSON out.
- Failure-safe behavior if Claude is unavailable must keep deterministic scores intact.

## Acceptance Criteria

- Observable behavior:
  - Stage 3 accepts deterministic shortlist rows and returns structured metadata.
  - Stored setup rows reflect Stage 3 fields separately from numeric ranking scores.
  - Claude cannot change component scores or rank order.
- Tests:
  - schema validation tests
  - failure-safe behavior
  - cap enforcement
- Documentation:
  - Prompt contract and allowed output schema are explicit.

## Verification Plan

- Run targeted tests for schema/cap behavior.
- Dry-run Stage 3 on a small synthetic shortlist.

## QA Notes

- Test scenarios: normal shortlist, empty shortlist, oversized shortlist, malformed output from model.
- Edge cases: model timeout, missing fields, repeated ticker on consecutive days.
- Regression risks: silent drift from structured synthesis into free-text ranking.

## Launch / Release Notes

- User-facing change summary: internal enriched setup-watchlist metadata.
- Operational notes: disable safely if the model is unavailable.
- Rollback notes: skip Stage 3 and keep deterministic shortlist only.

## Post-Launch Validation

- What to monitor: Stage 3 throughput, malformed-output rate, token/cost discipline.
- How success will be confirmed: reviewers get useful structured context without ranking drift.
- Follow-up decision date: after first month of parallel run.

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-038, "Stage 3 Claude Synthesis For Setup Watchlist."

Goal:
- Add a constrained LLM synthesis layer after deterministic pre-breakout filtering.

Scope:
- Accept a Stage 2 shortlist from the setup-watchlist pipeline.
- Send structured input to Claude.
- Require structured output with only:
  - `archetype`
  - `invalidation_condition`
  - `setup_grade`
  - `key_risk`
- Persist those fields without changing deterministic ranks/scores.

Constraints:
- Max 10 names/day.
- Claude cannot rank, override scores, or produce trade targets.
- Add failure-safe behavior if the model call fails.

Tests:
- schema validation
- daily cap enforcement
- fallback behavior on malformed/unavailable output

Non-goals:
- No ranking logic in Claude.
- No expansion of v1 signal set.
```


## Implementation Notes (2026-05-31)

### Files created
- `utils/stage3_synthesis.py` — `run_stage3_synthesis()`, `_parse_stage3_response()`, `_sanitize_row()`

### Contract
- Input: list of stage2_passed=True setup_watchlist rows (max 10/day, enforced)
- Output: list of dicts with ticker, archetype, invalidation_condition, setup_grade (A/B/C), key_risk
- Claude CANNOT change composite_score, pfs_score, psc_score, or rank order
- Failure-safe: any API error / malformed output → all Stage 3 fields = NULL, deterministic scores untouched
- Sanitize: Stage 3 fields stripped from input before sending to Claude (no feedback loop)

### Integration
- `pre_breakout_pipeline.py --stage3` invokes Stage 3 after Stage 2 gate
- `update_setup_watchlist_stage3()` writes fields back to setup_watchlist without touching scores

### Verification
```
pytest tests/test_pre_breakout_pipeline.py::TestStage3Synthesis -v
11 passed
```
Including: dry_run, cap enforcement, cap keeps highest scores, parse valid/invalid/malformed response, missing tickers → nulls, sanitize row, no API key → nulls.

## QA Result: PASS
