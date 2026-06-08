# Task: Ticker Governance Policy with A-List, Probation, and Quarantine

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: trading-logic
Category: automation
Risk: trading-logic
Effort: M
Target Release: pm-policy-v1
Due Date: TBD
Dependencies: TRD-059, TRD-066
Blocked By: none
Links: `ai_quant.py`, `utils/ticker_selector.py`, `utils/supabase_persist.py`, `reports/quarterly_reviews/2026-Q2-win-rate-deep-dive.md`, `reports/quarterly_reviews/2026-Q2-pm-review-extension.md`
Success Metric: the engine no longer treats all tickers as equally trustworthy; repeated winners and repeated offenders are governed explicitly in selection and review.

## Problem Statement

The quarterly review already shows that ticker-level edge is real. Some names behave repeatedly well under the engine’s framework, while others repeatedly waste capital or create unstable outcomes.

Without governance, the system keeps treating all names as peers even when history says otherwise.

## User Impact

- Capital keeps being allocated to repeat offenders
- High-quality repeat names do not receive appropriate trust or prioritization
- PM review remains descriptive rather than policy-driven

## Objective

Add an explicit ticker-governance layer with categories such as:

- `A_LIST`
- `STANDARD`
- `PROBATION`
- `QUARANTINE`

## Proposed Solution

Define deterministic ticker-governance states based on historical behavior and PM overrides.

Recommended PM design:

- `A_LIST`
  - repeated high-quality edge
  - can receive ranking or trust uplift within limits
- `STANDARD`
  - no special treatment
- `PROBATION`
  - repeated weak performance
  - requires stricter conviction / probability / catalyst rules
- `QUARANTINE`
  - repeated severe underperformance or structurally untradeable behavior
  - excluded or near-excluded absent explicit override

Governance sources can include:

- historical thesis outcomes
- filter-failure patterns
- event-driven pathologies
- manual PM overrides when needed

## Scope

Files or modules likely affected:

- `utils/supabase_persist.py`
- `ai_quant.py`
- `utils/ticker_selector.py`
- `dashboard/api/main.py`
- `docs/INTERNALS.md`
- `tests/test_ticker_selector.py`

## Non-Goals

- Do not hardcode a permanent opinion on every ticker.
- Do not bypass all normal quality gates for A-list names.
- Do not turn this into a discretionary black-box override system.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.
- Governance must remain auditable and explainable.

## Acceptance Criteria

- Observable behavior:
  - tickers can be assigned governance states
  - governance state affects selection or issuance behavior in a bounded, explainable way
  - PM review can see which names are A-list, on probation, or quarantined
- Tests:
  - add targeted tests for governance-state effects on selector behavior
  - add tests that governance does not bypass hard suppressions
- Documentation:
  - `docs/INTERNALS.md` documents the governance model and state meanings

## Verification Plan

- Targeted tests:
  - `pytest -q tests/test_ticker_selector.py tests/test_supabase_integration.py`

## QA Notes

- Test scenarios:
  - A-list name gets modest bounded uplift
  - probation name needs stronger evidence
  - quarantine name is excluded unless explicitly overridden
- Edge cases:
  - sparse outcome history
  - name with mixed long/short results
  - governance state changing over time
- Regression risks:
  - hardcoded bias
  - opaque selector behavior
  - governance overpowering real-time setup quality

## Launch / Release Notes

- User-facing change summary: the engine now applies explicit ticker-governance policy based on historical behavior.
- Operational notes: review governance-state assignments regularly; do not let them become stale.
- Rollback notes: disable governance effects while preserving stored governance metadata.

## Post-Launch Validation

- What to monitor:
  - count of A-list / probation / quarantine names
  - selection-rate shifts by governance state
  - outcome quality by governance state
- How success will be confirmed:
  - repeat offenders are filtered more effectively and high-quality repeat names are used more intelligently
- Follow-up decision date:
  - after 2-4 weeks of usage

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-068: add explicit ticker governance with A-list, standard, probation, and quarantine states.

Goal:
- Stop treating all tickers as equally trustworthy.
- Use repeated historical behavior to apply bounded selection and issuance policy.

Scope:
- utils/supabase_persist.py
- ai_quant.py
- utils/ticker_selector.py
- dashboard/api/main.py
- docs/INTERNALS.md
- tests/test_ticker_selector.py

Required changes:
- Add governance-state support for tickers.
- Support states such as A_LIST, STANDARD, PROBATION, and QUARANTINE.
- Make governance state affect selection/issuance in a bounded, explainable way.
- Ensure governance does not bypass hard suppressions or core risk controls.
- Expose or persist enough metadata for PM review.

Non-goals:
- No discretionary opaque override system
- No unconditional bypass for favored names

Constraints:
- Risk is trading-logic
- Keep the model auditable and deterministic

Tests / verification:
- pytest -q tests/test_ticker_selector.py tests/test_supabase_integration.py
```
