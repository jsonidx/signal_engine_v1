# Task: Option Recommendation Outcome Tracking

Status: proposed
Stage: ready
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: analytics
Category: options
Risk: data-quality
Effort: L
Target Release: backlog
Due Date: TBD
Dependencies: TRD-026
Blocked By: none
Links: `docs/tasks/new/TRD-025-options-screener-learning-roadmap.md`, `utils/ibkr_options.py`, `utils/supabase_persist.py`, `dashboard/api/main.py`
Success Metric: persisted option recommendation snapshots can be resolved into measurable outcomes over standard holding windows and at expiry.

## Problem Statement

Storing recommendation snapshots is not enough. The system also needs realized outcomes to measure whether the recommended contracts were actually good trades.

## User Impact

Without tracked outcomes:

- there is no win-rate measurement
- no ability to compare presets or delta/DTE cohorts
- no evidence base for improving the scoring engine

## Objective

Persist outcome records for historical option recommendations using clear resolution windows and reproducible metrics.

## Proposed Solution

Add a new table, likely `option_candidate_outcomes`, plus a resolution job or endpoint that evaluates past recommendation snapshots after fixed windows such as `1d`, `5d`, `10d`, and `expiry` where possible.

Track both:

- underlying movement
- option mark movement
- realized exit path versus the originally planned hold window / targets / stop rules

## Scope

- schema / migration for `option_candidate_outcomes`
- outcome-resolution logic
- scheduled or manual resolution entry point
- focused tests for return calculations and resolution-state handling

## Non-Goals

- Do not redesign the candidate engine.
- Do not build the options screener UI.
- Do not add automated score reweighting in this ticket.

## Constraints

- Keep the resolution math deterministic and inspectable.
- Define how to handle missing future marks or expired contracts.
- Avoid ambiguous success metrics; use explicit windows and fields.
- Compare actual trade path against stored planned exits when those fields exist.

## Acceptance Criteria

- Observable behavior: stored recommendation snapshots can generate outcome rows.
- Observable behavior: outcome rows include at least:
  - option return over standard windows
  - underlying return over standard windows
  - whether planned option targets/stops were hit
  - whether planned underlying targets/stops were hit
  - exit reason and days held where measurable
  - max runup / max drawdown when practical
  - hit/miss markers
- Observable behavior: unresolved snapshots remain identifiable until enough time has passed.
- Tests:
  - 1d/5d/10d return calculations are correct
  - missing marks are handled cleanly
  - expiry resolution behaves predictably
  - repeated resolution runs do not corrupt prior outcomes

## Verification Plan

- focused outcome-calculation tests
- local smoke test with mocked recommendation snapshots and mocked mark history
- `make verify` if practical

## QA Notes

- Test scenarios:
  - winning call
  - losing put
  - flat/no-move contract
  - expired ITM / expired OTM
- Edge cases:
  - missing chain marks
  - zero volume after recommendation date
  - contract unavailable after expiry
- Regression risks:
  - inconsistent mark sourcing
  - overstating performance from stale marks

## Launch / Release Notes

- User-facing change summary: none directly; enables future analytics.
- Operational notes: depends on consistent market-data access for mark reconstruction.
- Rollback notes: disable the resolution job and keep recommendation snapshots only.

## Post-Launch Validation

- What to monitor: resolution coverage %, missing-mark rate, and delayed resolution rows.
- How success will be confirmed: historical recommendations consistently receive outcome records.
- Follow-up decision date: before exposing performance analytics broadly.

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-027, "Option Recommendation Outcome Tracking," in this repo.

Goal:
- Persist realized outcomes for historical option recommendation snapshots over standard holding windows and, where possible, at expiry.

Scope:
- schema / migration for `option_candidate_outcomes`
- deterministic resolution logic
- entry point for resolving past recommendations
- focused tests for outcome calculations

Requirements:
- Track option and underlying returns over fixed windows.
- Track whether stored target and holding rules would have succeeded or failed.
- Handle missing data and unresolved cases explicitly.
- Keep calculations reproducible and testable.

Non-goals:
- No UI yet.
- No autonomous scoring changes.
- No broad provider migration.

Tests and verification:
- Add focused tests for resolution math and missing-data cases.
- Run the tests you add.
- Run `make verify` if practical.
