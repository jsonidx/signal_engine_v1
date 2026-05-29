# Task: Early Armed Pre-Squeeze Alerts

Status: implemented
Stage: in progress
Type: feature
Priority: P0
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: trading-logic
Category: research
Risk: trading-logic
Effort: L
Target Release: squeeze roadmap
Due Date: TBD
Dependencies: none
Blocked By: none
Links: tests/fixtures/ddd_apr_may_2026.py
Success Metric: the DDD-style replay produces an earlier `EARLY_ARMED` alert without weakening `ACTIVE_SQUEEZE` chase semantics.

## Problem Statement

The current squeeze workflow is stronger at confirming moves already in progress than identifying high-quality early setups before they become late chases.

## User Impact

Users see the signal too late on some of the best squeeze cases, which reduces entry quality and weakens confidence in the alert ladder for fresh setup discovery.

## Objective

Add a new pre-ignition alert class, `EARLY_ARMED`, designed to catch DDD-style squeeze setups before the move becomes an `ACTIVE_SQUEEZE` chase.

## Proposed Solution

Add an explicit early-state layer in the squeeze state machine, scorer, and alert copy so strong pre-breakout setups can be surfaced earlier with structured reasons and replay-backed semantics.

## Scope

- `squeeze_screener.py`
- `squeeze_state_machine.py`
- `squeeze_alerts.py`
- `squeeze_risk_analyzer.py`
- `tests/test_squeeze_screener.py`
- `tests/test_squeeze_state_machine.py`
- `tests/test_squeeze_alerts.py`
- `tests/fixtures/ddd_apr_may_2026.py` or equivalent new fixture

## Non-Goals

- Do not weaken or remove existing `ARMED` / `ACTIVE` semantics until replay evidence supports it.
- Do not emit buy instructions or position-sizing rules.
- Do not claim a fixed 90% win probability.
- Do not treat `ACTIVE_SQUEEZE` as the primary fresh-entry alert.

## Constraints

- `EARLY_ARMED` must be explicitly framed as a watch / preparation alert, not an execution signal.
- The logic must favor earlier setup detection over late squeeze confirmation.
- The state / alert must be explainable with structured factors, not LLM text.
- Avoid using post-move information. Tests must only use data available on the simulated alert date.
- `ACTIVE_SQUEEZE` should be treated as "move in progress / chase risk elevated" unless later replay evidence proves it is also a strong fresh-entry state.

## Acceptance Criteria

- Observable behavior: a ticker can trigger `EARLY_ARMED` before `ACTIVE_SQUEEZE` when it shows a DDD-style profile:
  - short interest already elevated, preferably `>= 20%`
  - DTC already elevated, preferably `>= 8`
  - compression-recovery has started, without requiring a fully mature recovery
  - price is off the low but not already extended into a late-stage squeeze
  - optional volume improvement is helpful but not mandatory
- Observable behavior: `ACTIVE_SQUEEZE` remains reserved for in-progress / ignition-confirmed moves.
- Observable behavior: state semantics are explicit in code comments and alert copy:
  - `EARLY_ARMED` = early setup / entry hunting
  - `ARMED` = stronger setup / pre-breakout watch
  - `ACTIVE_SQUEEZE` = move in progress / chase risk high
- Alerts include structured reasons such as: elevated SI, elevated DTC, early compression recovery, price off low, float constraint, options confirmation.
- Tests: add at least one DDD-like fixture where `EARLY_ARMED` fires materially earlier than the current May 11, 2026 `ARMED` signal.
- Documentation: comments and alert copy make clear that `EARLY_ARMED` is an early setup alert and may have lower hit rate than later confirmation states.

## Verification Plan

- `pytest tests/test_squeeze_screener.py tests/test_squeeze_state_machine.py tests/test_squeeze_alerts.py -v`
- Replay the DDD fixture and confirm the first alert moves from late-stage `ARMED` toward an earlier pre-breakout setup date.
- `make verify`

## QA Notes

- Test scenarios: DDD-style replay, non-DDD squeeze candidates, and existing `ARMED` / `ACTIVE_SQUEEZE` regressions.
- Edge cases: noisy rebounds with high SI but weak structure, and early setups with limited volume confirmation.
- Regression risks: misclassifying too many weak rebounds as early setups or diluting current alert semantics.

## Launch / Release Notes

- User-facing change summary: adds an earlier squeeze-setup alert tier for pre-ignition monitoring.
- Operational notes: alert text must clearly distinguish setup-hunting from chase-risk states.
- Rollback notes: revert the added state and related alert routing if replay quality degrades.

## Post-Launch Validation

- What to monitor: alert mix across `EARLY_ARMED`, `ARMED`, and `ACTIVE_SQUEEZE`, plus replay timing on known squeeze cases.
- How success will be confirmed: earlier, explainable alerts appear on reference cases without a sharp increase in false positives.
- Follow-up decision date: after enough closed-window samples exist for calibration review.

## Handoff Notes

The current system is better at confirming a squeeze than surfacing the earliest coiled setup. `DDD` is the reference case:

- low near `1.78` on April 7, 2026
- recovery already visible by mid-April
- persisted squeeze row only appears on May 11, 2026

This task should introduce an earlier alert layer without turning every high-SI rebound into noise.

The current PM takeaway should be preserved in implementation:

- `EARLY_ARMED` and secondarily `ARMED` are the primary entry-hunting states
- `ACTIVE_SQUEEZE` is primarily a continuation / chase-risk / management state
- `DDD` on May 11, 2026 is the cleanest profitable reference case, but still later than the desired first setup detection
