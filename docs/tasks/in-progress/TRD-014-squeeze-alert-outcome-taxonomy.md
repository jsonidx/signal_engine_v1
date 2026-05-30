# Task: Squeeze Alert Outcome Taxonomy

Status: qa
Stage: qa
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

Paste-ready Codex QA prompt:

```text
Codex QA for TRD-012, TRD-013, TRD-014, and TRD-015.

Ticket summary:
- TRD-012: verify the live Supabase training dataset path is working end-to-end.
- TRD-013: verify the calibration workflow can run on real labeled data and produce a report.
- TRD-014: verify taxonomy labels are persisted correctly in live training outcomes.
- TRD-015: verify the Telegram approval-request workflow works end-to-end with auditable DB state transitions.

Combined objective:
Use repo-local tests plus live environment checks to determine whether these four tickets are truly ready to move from `qa` to `done`. Do not mark any ticket done unless its external acceptance evidence is present.

Exact scope:
- `docs/tasks/in-progress/TRD-012-supabase-squeeze-training-dataset.md`
- `docs/tasks/in-progress/TRD-013-squeeze-probability-calibration-and-review-gate.md`
- `docs/tasks/in-progress/TRD-014-squeeze-alert-outcome-taxonomy.md`
- `docs/tasks/in-progress/TRD-015-telegram-approval-requests-for-trading-logic.md`
- `migrations/003_squeeze_training_and_approvals.sql`
- `utils/supabase_persist.py`
- `backtest.py`
- `scripts/squeeze_calibration.py`
- `scripts/telegram_bot.py`
- `scripts/notify_pipeline_result.py`
- related tests under `tests/test_squeeze_persistence_schema.py`, `tests/test_squeeze_replay.py`, and `tests/test_telegram_notifications.py`

Required verification:
1. Run local automated coverage:
   `pytest tests/test_squeeze_state_machine.py tests/test_squeeze_alerts.py tests/test_squeeze_replay.py tests/test_squeeze_persistence_schema.py tests/test_telegram_notifications.py -q`
2. TRD-012:
   - Confirm migration `003_squeeze_training_and_approvals.sql` is applied in the live Supabase environment.
   - Confirm at least one live `squeeze_training_snapshots` row exists from the pipeline.
   - Confirm at least one related `squeeze_training_outcomes` row exists or clearly document that forward windows are not yet closed.
3. TRD-013:
   - Run `python3 scripts/squeeze_calibration.py` against real labeled data if available.
   - Confirm a real calibration report is written under `reports/`.
   - If sample size is insufficient, leave the ticket in `qa` and record the exact blocker.
4. TRD-014:
   - Query live `squeeze_training_outcomes` rows and verify taxonomy labels are being written as expected.
   - Confirm labels are reproducible from the code rules, not manual edits.
5. TRD-015:
   - Create a real or controlled test `approval_requests` row.
   - Verify notification formatting.
   - Verify `/pending`, `/approve <id>`, and `/reject <id>` or equivalent handler flow updates DB state correctly.
   - Confirm auditable status transitions in Supabase.

Non-goals:
- Do not change trading logic, thresholds, schema, or Telegram bot behavior while doing QA.
- Do not mark a ticket done from unit tests alone when its acceptance criteria require live DB or Telegram evidence.
- Do not refactor implementation code.

Risk constraints:
- Treat TRD-013 and TRD-014 as `trading-logic`-adjacent verification work; do not alter scoring behavior.
- Treat TRD-015 as approval-gate infrastructure; verify that rejected or non-pending requests cannot bypass the guard.

Required output:
- For each ticket, explicitly state `done` or `remain in qa`.
- Cite the exact evidence used.
- If blocked, state the missing evidence in one sentence.
- If QA passes, update `Status:` to `done`, `Stage:` to `done`, add the verification summary in the ticket, and run `python3 scripts/sync_task_status.py`.
```

## Tracking Note

Code shipped in commit **c8f3481** ("Add EARLY_ARMED squeeze training, calibration, and approval workflows", 2026-05-29).
Covers: compute_taxonomy_label() in backtest.py, EARLY_ENOUGH / LATE_CHASE / FALSE_POSITIVE labels, hit_15pct_10d and hit_25pct_20d binary flags.
Status: implemented and on main, but taxonomy labels accumulate over time as squeeze outcomes close.
Action required: verify taxonomy labels are being written correctly to squeeze_training_outcomes in the live pipeline before moving to finished.

## QA Verification Summary (2026-05-30) — REMAIN IN QA

**Taxonomy rule code confirmed:**
- `compute_taxonomy_label()` at `backtest.py:1099–1155` defines explicit reproducible rules:
  - `EARLY_ENOUGH`: entry state (EARLY_ARMED/ARMED) + hit_15pct_10d OR hit_25pct_20d
  - `LATE_CHASE`: ACTIVE state with any move ≥5%; or entry state with move below threshold
  - `FALSE_POSITIVE`: max_fwd_return < 5% across all windows
- Called in `_persist_training_outcomes()` at backtest.py:1513–1526. Labels derive from code rules, not manual edits.
- `squeeze_training_snapshots` has 5 real rows (signal_date=2026-05-30) confirming the write path is live and correctly captures ACTIVE and EARLY_ARMED states.

**Live evidence:**
- `squeeze_training_outcomes`: 0 rows — time-blocked, not implementation-blocked.
- 24 trading bars available after 2026-04-26 as of 2026-05-29 close; 30 needed for fwd_30d.
- Blocker: first labeled outcome rows with taxonomy_label require ~2026-06-05. Leave in qa until `squeeze_training_outcomes` has at least 1 live row with a non-null `taxonomy_label` matching the code rules above.
