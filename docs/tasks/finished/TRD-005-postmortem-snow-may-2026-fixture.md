# Task: SNOW May 2026 Postmortem Fixture

Status: completed
Stage: done
Type: research
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: trading-logic
Category: research
Risk: trading-logic
Effort: S
Target Release: completed
Due Date: completed
Dependencies: TRD-001, TRD-004
Blocked By: none
Links: tests/fixtures/
Success Metric: the `SNOW` May 2026 failure mode is reproducible in a static regression fixture.

## Problem Statement

The team needed a stable regression case for a missed catalyst setup that could not rely on live market or external data.

## User Impact

Without a fixture, future changes could repeat the same miss without detection and postmortem learning would stay informal.

## Objective

Create a reproducible postmortem fixture for the `SNOW` May 2026 move so future changes can be tested against the exact failure mode: good ranking and partial catalyst evidence, but no clear pre-breakout alert.

## Proposed Solution

Create a small static fixture and regression tests that preserve the relevant pre-breakout context for the `SNOW` case.

## Scope

- `tests/fixtures/`
- `tests/test_pipeline_defects.py`
- `docs/`
- Optional: `reports/`

## Non-Goals

- Do not depend on live yfinance, Supabase, or SEC during the fixture test.
- Do not assert exact future market prices beyond the fixture window.
- Do not backfit a rule that only works for `SNOW`.

## Constraints

- Fixture must be static and small.
- Include only fields needed to reproduce the classification issue.
- The expected outcome should be “watch/catalyst setup,” not necessarily “buy.”

## Acceptance Criteria

- A static fixture captures relevant `SNOW` rows around Apr 13, May 15, and May 25-28, 2026.
- A test verifies that the new detector/alert path would have flagged a setup before the May 28 gap.
- The fixture documents why short-squeeze logic should remain low-confidence for this case.
- Documentation: add a short postmortem note explaining what the engine missed and what the new rules should catch.

## Verification Plan

- `pytest tests/test_pipeline_defects.py -v`
- `make verify`

## Handoff Notes

Useful source facts from the audit:

- Apr 13 thesis: `BULL`, conviction `2`, dark-pool z-score around `-1.60`.
- May 15 thesis: `NEUTRAL`, conviction `2`, entry `$147-$153`, earnings risk noted for May 27.
- May 25-28 rankings: `SNOW` was rank `3`.
- Squeeze score stayed low around `11.5`; short interest was about `5%-6%` of float and days-to-cover around `2.3-2.5`.
- Options showed unusual call activity before the gap, but this did not become a strong alert.
