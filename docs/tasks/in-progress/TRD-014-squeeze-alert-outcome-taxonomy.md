# Task: Squeeze Alert Outcome Taxonomy

Status: implemented
Stage: awaiting QA
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: research
Risk: trading-logic
Effort: M
Target Release: squeeze roadmap
Due Date: TBD
Dependencies: TRD-012
Blocked By: none
Links: none
Success Metric: each eligible closed-window squeeze alert receives a persisted taxonomy label that can be reused in replay and calibration.

## Problem Statement

Historical squeeze alerts currently lack a stable first-class taxonomy that distinguishes early good alerts from late chases and outright false positives.

## User Impact

Without this labeling layer, PM review, offline learning, and threshold tuning rely on ad hoc interpretation instead of consistent historical categories.

## Objective

Persist a first-class outcome taxonomy for squeeze alerts so each historical alert can be labeled as `EARLY_ENOUGH`, `LATE_CHASE`, or `FALSE_POSITIVE` for model training, PM review, and future threshold tuning.

## Proposed Solution

Define explicit label rules based on closed-window outcomes and alert purpose, persist those labels alongside supporting metrics, and expose them to replay and calibration workflows.

## Scope

- `backtest.py`
- `utils/supabase_persist.py`
- `schema.sql`
- `migrations/`
- `squeeze_alerts.py`
- `tests/test_squeeze_replay.py`
- `tests/test_squeeze_persistence_schema.py`

## Non-Goals

- Do not replace raw forward-return fields with taxonomy labels; keep both.
- Do not hardcode one universal definition if the code cannot support parameterization.
- Do not use subjective manual review as the only source of labels.

## Constraints

- Taxonomy labels must be derived from explicit, reproducible rules.
- Labels must be computed only after the relevant forward window closes.
- The taxonomy must distinguish alert purpose:
  - entry-oriented alerts: `EARLY_ARMED`, `ARMED`
  - chase / continuation alerts: `ACTIVE_SQUEEZE`

## Acceptance Criteria

- A Supabase-persisted label exists for each eligible closed-window alert, at minimum:
  - `EARLY_ENOUGH`
  - `LATE_CHASE`
  - `FALSE_POSITIVE`
- The labeling logic is documented and test-covered.
- The persisted record keeps supporting metrics such as:
  - alert type
  - state at alert time
  - forward returns
  - max forward return
  - success thresholds hit
- Replay / calibration reports can group historical alerts by taxonomy label.
- Tests: cover at least one example of each label type.

## Verification Plan

- `pytest tests/test_squeeze_replay.py tests/test_squeeze_persistence_schema.py -v`
- Run replay on the current post-fix dataset and verify labels persist for closed windows.
- Query Supabase and confirm all three label classes are present when data exists.
- `make verify`

## QA Notes

- Test scenarios: one fixture each for `EARLY_ENOUGH`, `LATE_CHASE`, and `FALSE_POSITIVE`.
- Edge cases: borderline outcomes near threshold cutoffs and alert types with different intended use.
- Regression risks: unstable definitions that shift too often or labels that fail to reflect alert purpose.

## Launch / Release Notes

- User-facing change summary: none directly; this improves historical labeling and PM analysis.
- Operational notes: taxonomy definitions should be documented and reused consistently across reports.
- Rollback notes: revert only the labeling layer if rules prove misleading.

## Post-Launch Validation

- What to monitor: label distribution by alert type and whether each class is populated as expected.
- How success will be confirmed: replay and calibration workflows can group alerts by taxonomy without manual relabeling.
- Follow-up decision date: after the first calibration review that consumes taxonomy labels.

## Handoff Notes

This task operationalizes the PM review framework:

1. early enough
2. late/chase
3. false positive

Without these labels persisted in the database, Claude can report on past behavior but cannot cleanly train or recalibrate future squeeze logic from a stable taxonomy.

## Tracking Note

Code shipped in commit **c8f3481** ("Add EARLY_ARMED squeeze training, calibration, and approval workflows", 2026-05-29).
Covers: compute_taxonomy_label() in backtest.py, EARLY_ENOUGH / LATE_CHASE / FALSE_POSITIVE labels, hit_15pct_10d and hit_25pct_20d binary flags.
Status: implemented and on main, but taxonomy labels accumulate over time as squeeze outcomes close.
Action required: verify taxonomy labels are being written correctly to squeeze_training_outcomes in the live pipeline before moving to finished.
