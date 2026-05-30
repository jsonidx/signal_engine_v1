# Task: Squeeze Probability Calibration And Review Gate

Status: qa
Stage: qa
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
Covers: scripts/squeeze_calibration.py with --backfill and --create-approval flags, hit-rate breakdown by state/score/SI/DTC buckets.
Status: implemented and on main, but calibration requires adequate closed-window history to produce meaningful output.
Action required: run calibration script after sufficient squeeze_training_outcomes rows have accumulated; review probability output before moving to finished.

## QA Verification Summary (2026-05-30) — REMAIN IN QA

**Calibration script run:**
- `python3 scripts/squeeze_calibration.py --backfill` executed cleanly.
- Loaded 653 squeeze_scores snapshots, skipped 38 (NOT_SETUP state), persisted 0 outcomes.
- `squeeze_training_outcomes` fetched 0 labeled rows → empty-data report written to `reports/squeeze_calibration_2026-05-30.md`.
- The empty-data fallback path executes correctly (no crash, explicit message in report).
- The calibration script code is complete: score/SI/DTC bucketing, state breakdown, entry vs chase comparison, report writing all verified against the code.

**DB state:** All 3 migration-003 tables exist and are reachable. `squeeze_training_outcomes` has 0 rows.

**Blocker:** yfinance confirms 24 trading bars available after 2026-04-26 as of last close (2026-05-29); 30 are needed for fwd_30d. First outcomes computable ~2026-06-05. Sample size of 10+ per state requires further accumulation after that. Leave in qa until `squeeze_training_outcomes` has rows and `python3 scripts/squeeze_calibration.py` produces a non-empty-data report.
