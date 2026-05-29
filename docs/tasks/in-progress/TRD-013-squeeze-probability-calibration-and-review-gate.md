# Task: Squeeze Probability Calibration And Review Gate

Status: implemented
Stage: in progress
Type: research
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: trading-logic
Category: research
Risk: trading-logic
Effort: M
Target Release: squeeze roadmap
Due Date: TBD
Dependencies: TRD-012
Blocked By: none
Links: reports/
Success Metric: a reproducible calibration report exists with sample-aware probability evidence by alert type and score bucket.

## Problem Statement

Raw squeeze scores and thresholds are not the same as calibrated probabilities, and the system currently lacks an evidence-backed review workflow for that distinction.

## User Impact

Without calibration, users can over-trust thresholds, misread late squeeze states as early-entry opportunities, and make decisions from weak statistical grounding.

## Objective

Add an offline evaluation and calibration workflow for early squeeze setups so the system can estimate realistic probabilities from historical outcomes instead of treating raw score thresholds as sufficient.

## Proposed Solution

Use replayed historical snapshots with closed forward windows to generate labeled outcomes, bucket performance by state and signal quality, and publish a reproducible report before any probability claims are promoted.

## Scope

- `backtest.py`
- new calibration script under `scripts/` or `utils/`
- `reports/`
- `tests/test_squeeze_replay.py`
- optionally `dashboard/api/main.py` if a calibrated probability field is exposed later

## Non-Goals

- Do not expose a probability in the live UI unless calibration quality is verified.
- Do not claim or hardcode a 90% success probability target.
- Do not train on future information.

## Constraints

- Calibration must be based on closed forward windows only.
- Report precision by alert/state type separately:
  - `EARLY_ARMED`
  - `ARMED`
  - `ACTIVE`
- Evaluate `ACTIVE` separately as a continuation / chase-risk state, not as the default fresh-entry state.
- Evaluate both hit rate and expectancy, not hit rate alone.
- If sample size is too small, the report must say so explicitly rather than overfit.

## Acceptance Criteria

- A reproducible workflow exists to score historical squeeze snapshots against realized outcomes.
- The report breaks down performance by:
  - alert/state type
  - score bucket
  - SI bucket
  - DTC bucket
  - sector or ticker cohort when sample allows
- The report identifies:
  - early-enough alerts
  - late/chase alerts
  - false positives
- The report explicitly compares entry-state quality versus chase-state quality:
  - `EARLY_ARMED` / `ARMED` for fresh-entry timing
  - `ACTIVE` for continuation or too-late detection
- A calibrated success label is defined for model work, for example:
  - `success_10d_15pct`
  - `success_20d_25pct`
- Tests cover the row-building and label-generation logic.
- Documentation: save a report in `reports/` summarizing whether `EARLY_ARMED` improves timing versus current `ARMED` / `ACTIVE`.

## Verification Plan

- `pytest tests/test_squeeze_replay.py -v`
- Run the calibration workflow on the current post-fix history and write a report under `reports/`.
- Confirm the report explicitly shows sample counts and warns when evidence is weak.
- `make verify`

## QA Notes

- Test scenarios: label generation, bucketed reporting, small-sample warnings, and state-by-state comparisons.
- Edge cases: very low sample sizes, incomplete forward windows, and cohorts with no positive outcomes.
- Regression risks: overstating precision from noisy samples or leaking future information into labels.

## Launch / Release Notes

- User-facing change summary: no direct UI change unless later approved; this adds evidence for future probability claims.
- Operational notes: reports should be versioned and referenced in any approval request.
- Rollback notes: disable use of the calibration output if sampling quality is inadequate.

## Post-Launch Validation

- What to monitor: whether reports remain reproducible and whether new samples materially shift bucket performance.
- How success will be confirmed: PM and engineering can review probability evidence without manual spreadsheet work.
- Follow-up decision date: after the first full calibration run on adequate closed-window history.

## Handoff Notes

This task is the guardrail against wishful thinking. The user goal is to learn from successful squeezes and eventually approach very high precision on early setups, but the code should only emit probability claims that are supported by closed-window evidence and adequate sample size.
