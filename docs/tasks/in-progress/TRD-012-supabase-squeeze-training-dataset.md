# Task: Supabase Squeeze Training Dataset

Status: qa
Stage: qa
Type: feature
Priority: P0
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: automation
Risk: infra
Effort: L
Target Release: squeeze roadmap
Due Date: TBD
Dependencies: TRD-011
Blocked By: none
Links: none
Success Metric: point-in-time-safe training snapshots and outcomes are persisted for every eligible squeeze alert.

## Problem Statement

The current persistence path stores operational squeeze outputs, but it does not maintain a dedicated training dataset that cleanly links point-in-time features with later realized outcomes.

## User Impact

Without a reusable labeled dataset, calibration, false-positive reduction, and future model training stay manual and slow.

## Objective

Persist a machine-learning-ready squeeze setup dataset in Supabase so successful and failed early setups can be studied, labeled, and reused for future model training and probability calibration.

## Proposed Solution

Add dedicated training snapshot and outcome tables in Supabase, write signal-date-safe features into them, and extend replay or backfill flows so closed-window labels can be computed consistently.

## Scope

- `utils/supabase_persist.py`
- `utils/db.py`
- `schema.sql`
- `migrations/`
- `backtest.py`
- `squeeze_screener.py`
- `squeeze_alerts.py`
- `tests/test_squeeze_persistence_schema.py`
- `tests/test_squeeze_replay.py`

## Non-Goals

- Do not train an online model inside the live pipeline.
- Do not store secrets, embeddings, or opaque blobs that cannot be queried.
- Do not overwrite existing `squeeze_scores`; add dedicated tables or views for ML-ready records.

## Constraints

- Store both positive and negative examples. Failed setups are required.
- Preserve point-in-time safety: only features known on the signal date may be stored in the training row.
- Keep schema normalized enough for SQL replay and model export.
- Favor explicit columns over JSON-only storage for the most important model features.

## Acceptance Criteria

- A new Supabase table or table pair exists for squeeze-model training, for example:
  - `squeeze_training_snapshots`
  - `squeeze_training_outcomes`
- Training snapshots persist feature columns needed for early-squeeze learning, including at minimum:
  - signal date
  - ticker
  - alert/state type (`EARLY_ARMED`, `ARMED`, `ACTIVE`)
  - final score and component scores
  - short interest, DTC, compression-recovery, volume confirmation
  - effective-float fields
  - options / IV fields
  - risk / dilution fields
  - explanatory tags
- Outcome rows persist realized forward results and labels, including at minimum:
  - `fwd_5d`, `fwd_10d`, `fwd_20d`, `fwd_30d`
  - max forward return
  - outcome label
  - binary success labels suitable for supervised learning, such as `hit_15pct_10d`, `hit_25pct_20d`
- Replay can populate or backfill labeled outcomes for already-closed windows.
- Tests: verify schema, save path, read path, and backward compatibility.
- Documentation: add a short note describing how the training dataset differs from `squeeze_scores`.

## Verification Plan

- `pytest tests/test_squeeze_persistence_schema.py tests/test_squeeze_replay.py -v`
- Run a local replay window and confirm training snapshots plus outcomes are written for every eligible row.
- Query Supabase and verify both successful and failed setups are present.
- `make verify`

## QA Notes

- Test scenarios: fresh writes, replay backfill, mixed old/new rows, and missing optional fields.
- Edge cases: null CHUNK fields, incomplete forward windows, and duplicate replay runs.
- Regression risks: schema drift, replay incompatibility, or storing forward-looking data by accident.

## Launch / Release Notes

- User-facing change summary: none directly; this enables future squeeze-learning workflows.
- Operational notes: migrations and Supabase table documentation are required.
- Rollback notes: revert new training tables and write paths if persistence proves unstable.

## Post-Launch Validation

- What to monitor: row counts, null-rate on critical features, and outcome population after windows close.
- How success will be confirmed: snapshots and outcomes can be queried reliably for both winning and failed setups.
- Follow-up decision date: after the first meaningful post-launch replay and backfill cycle.

## Handoff Notes

The goal is to make future learning possible, not to promise a 90% classifier immediately. The dataset must support later work on:

- early setup precision
- false-positive reduction
- probability calibration
- state transition analysis

Design the schema so a future offline trainer can export clean tabular data directly from Supabase.

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
Covers: squeeze_training_snapshots and squeeze_training_outcomes tables, migration 003, persistence helpers, backfill path.
Status: implemented and on main. Migration 003 fully applied. Snapshot write path proven — 5 real pipeline rows in squeeze_training_snapshots (2026-05-30). Pending: first squeeze_training_outcomes rows (time-blocked on 30-trading-day forward window).
Action required: run `python3 scripts/squeeze_calibration.py --backfill` after ~2026-06-05 to populate outcomes; verify taxonomy labels; then move to finished.

## QA Verification Summary (2026-05-30) — REMAIN IN QA

**Live DB state confirmed (2026-05-30):**
- `squeeze_training_snapshots`: **5 real pipeline-generated rows** (signal_date=2026-05-30) — written by a live `run_screener()` call on real tickers using the production code path at `squeeze_screener.py:1825–1867`:
  - HPK (ACTIVE, score=65.3, SI=57.0%, DTC=14.2)
  - SLS (ACTIVE, score=58.3, SI=30.8%, DTC=6.5)
  - DDD (ACTIVE, score=61.0, SI=31.1%, DTC=9.6)
  - FLNC (EARLY_ARMED, score=50.3, SI=38.2%, DTC=3.3)
  - ABEO (EARLY_ARMED, score=48.4, SI=27.3%, DTC=9.4)
- `squeeze_training_outcomes`: 0 rows — time-blocked (see below).
- `approval_requests`: 0 rows (table exists).
- `squeeze_scores`: 653 rows (2026-04-16 → 2026-05-30) — untouched history.

**Migration 003:** All 3 tables confirmed present in live Supabase.

**Snapshot write path verified:** The production code path `squeeze_screener.py:1825–1867` fires for every ARMED/ACTIVE/EARLY_ARMED result. The 5 rows above prove it end-to-end — real tickers, real live data, real Supabase write.

**Forward window blocker (time-blocked, not implementation-blocked):**
- 32 meaningful ARMED/ACTIVE rows in `squeeze_scores`, earliest 2026-04-26.
- yfinance confirms 24 trading bars available after 2026-04-26 as of 2026-05-29 (last close). Need 30. 6 more trading days required.
- `python3 scripts/squeeze_calibration.py --backfill` loaded 653 snapshots, skipped 38 (null/NOT_SETUP state — correct), persisted 0 outcomes — expected behavior.
- First fwd_30d computable for 2026-04-26 signals after the 2026-06-05 close (approximately).

**Blocker for done:** `squeeze_training_outcomes` has 0 rows. First labeled outcome rows cannot exist until the 30-trading-day window closes for 2026-04-26 signals (~2026-06-05). Re-run `python3 scripts/squeeze_calibration.py --backfill` after that date to populate and verify.
