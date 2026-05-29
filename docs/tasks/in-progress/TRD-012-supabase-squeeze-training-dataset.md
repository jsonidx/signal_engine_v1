# Task: Supabase Squeeze Training Dataset

Status: implemented
Stage: in progress
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
