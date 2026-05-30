# Task: Options Resolution and Accuracy Module

Status: proposed
Stage: ready
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: analytics
Category: options
Risk: metrics
Effort: M
Target Release: backlog
Due Date: TBD
Dependencies: TRD-027
Blocked By: none
Links: `docs/tasks/new/TRD-025-options-screener-learning-roadmap.md`, `dashboard/frontend/src/pages/ResolutionPage.tsx`, `dashboard/api/main.py`
Success Metric: users can inspect option-recommendation performance by preset, delta, DTE, IV, spread, and source in the dashboard.

## Problem Statement

Even after recommendations and outcomes are persisted, the product still needs a decision-support surface that shows which kinds of option recommendations are actually working.

## User Impact

Without an accuracy module:

- users cannot evaluate recommendation quality
- performance tuning remains anecdotal
- the team cannot tell whether one preset or contract profile outperforms another

## Objective

Add option-specific resolution and accuracy analytics to the dashboard.

## Proposed Solution

Extend the existing resolution/accuracy area with an options-focused tab or section that aggregates persisted recommendation and outcome data into practical performance views.

Suggested analytics:

- win rate by `strategy_preset`
- win rate by `delta` bucket
- win rate by `DTE` bucket
- win rate by `IV` bucket
- win rate by `spread` bucket
- win rate by `chain_source`
- win rate by `holding_window_days` bucket
- hit rate of `option_take_profit_1` / `option_take_profit_2`
- hit rate of `option_stop_loss`
- hit rate of `underlying_target_1` / `underlying_target_2`
- suppression reason frequency
- rejection reason frequency

## Scope

- backend analytics endpoint(s)
- frontend resolution/accuracy UI
- focused tests for metric aggregation and rendering

## Non-Goals

- Do not mutate scoring rules in this ticket.
- Do not add free-form AI commentary as the source of truth for metrics.

## Constraints

- Metrics must be derived from persisted snapshots and outcomes.
- Present both sample size and performance to avoid misleading sparse cohorts.
- Keep analytics readable and decision-oriented.

## Acceptance Criteria

- Observable behavior: dashboard shows an options-specific analytics section.
- Observable behavior: cohort metrics include both hit rate and sample size.
- Observable behavior: users can inspect performance by at least preset, delta bucket, and DTE bucket.
- Observable behavior: users can inspect whether target and holding rules are calibrated well.
- Tests:
  - backend aggregates expected cohort metrics
  - frontend renders the analytics states cleanly

## Verification Plan

- focused analytics tests
- manual browser verification
- `make verify` if practical

## QA Notes

- Test scenarios:
  - mixed preset dataset
  - sparse cohort dataset
  - empty analytics dataset
- Edge cases:
  - missing outcome rows
  - all candidates unresolved
- Regression risks:
  - misleading percentages on tiny sample sizes
  - inconsistent bucket definitions

## Launch / Release Notes

- User-facing change summary: new option-recommendation accuracy analytics in the dashboard.
- Operational notes: depends on historical snapshots and outcomes existing.
- Rollback notes: hide the analytics tab and preserve persistence only.

## Post-Launch Validation

- What to monitor: whether enough outcome data accumulates for useful cohorts.
- How success will be confirmed: users can identify which option-selection patterns are working best.
- Follow-up decision date: after the first meaningful sample size is reached.

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-029, "Options Resolution and Accuracy Module," in this repo.

Goal:
- Add option-specific performance analytics to the resolution/accuracy area using persisted recommendation snapshots and outcomes.

Scope:
- backend analytics endpoint(s)
- frontend resolution/accuracy UI
- focused tests for metric aggregation and rendering

Requirements:
- Show performance by preset and key contract-quality buckets.
- Include sample size in analytics outputs.
- Keep metrics derived from structured persisted data, not LLM text.

Non-goals:
- No automatic scoring changes.
- No new recommendation engine in this ticket.

Tests and verification:
- Add focused analytics tests.
- Run the tests you add.
- Run `make verify` if practical.
