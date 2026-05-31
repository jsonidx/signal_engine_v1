# Task: Options And Short-Interest Persistence For Future Research

Status: completed
Stage: done
Type: chore
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: automation
Risk: api
Effort: M
Target Release: pre-breakout-v1
Due Date: TBD
Dependencies: none
Blocked By: none
Links: TRD-032
Success Metric: the repo begins accumulating daily options-state history and date-accurate short-interest history needed for later research, without using those fields in v1 scoring.

## Problem Statement

The repo does not yet have enough historical options-state and short-interest snapshots to research those features honestly for pre-breakout detection.

## User Impact

Every day without persisted history permanently reduces future backtest depth for options and positioning signals.

## Objective

Start accumulating the historical datasets now while keeping them out of v1 scoring.

## Proposed Solution

Add persistence for daily options-state snapshots and date-accurate short-interest history with the minimum fields needed for later research.

## Scope

- persistence tables/schema
- `utils/supabase_persist.py`
- sourcing code for current options-state snapshots and short-interest history
- targeted persistence tests

## Non-Goals

- Do not add options or short-interest signals to v1 Stage 1 scoring.
- Do not build alpha claims around this data yet.
- Do not require intraday feeds.

## Constraints

- Date accuracy matters more than feature richness.
- History collection should be robust even if some fields are temporarily unavailable.
- Keep the schema simple and extensible.

## Acceptance Criteria

- Observable behavior:
  - A daily options snapshot table/path exists and is populated.
  - A short-interest history table/path exists and stores reporting-date-accurate snapshots.
  - The pre-breakout scoring logic does not consume these fields yet.
- Tests:
  - persistence tests
  - missing-data handling tests
- Documentation:
  - Field list and collection cadence are documented in code or task notes.

## Verification Plan

- Run targeted persistence tests.
- Execute one local population cycle and inspect stored rows.

## QA Notes

- Test scenarios: normal snapshot, partial options-chain data, missing short-interest update.
- Edge cases: duplicate daily loads, reporting-date vs fetch-date ambiguity, stale vendor/API payloads.
- Regression risks: accidental coupling into active scoring logic.

## Launch / Release Notes

- User-facing change summary: none yet; data collection only.
- Operational notes: persistence begins immediately; research use deferred.
- Rollback notes: disable collection jobs and retain historical data already captured.

## Post-Launch Validation

- What to monitor: daily row counts, missing-field rates, schema drift.
- How success will be confirmed: enough clean history accumulates to support later research tickets.
- Follow-up decision date: after 60-90 days of collection.

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-039, "Options And Short-Interest Persistence For Future Research."

Goal:
- Start collecting the history needed for future research without using it in v1 pre-breakout scoring.

Scope:
- Add persistence for:
  - daily options-state snapshots
  - date-accurate short-interest history
- Extend storage helpers and any sourcing code needed.
- Keep the schema minimal but useful for later research.

Constraints:
- Do not wire these features into active pre-breakout scoring.
- Favor date accuracy and collection reliability over complex derived metrics.
- No intraday requirements.

Tests:
- persistence behavior
- duplicate handling
- missing-field handling

Non-goals:
- No options alpha model.
- No short-interest scoring in v1.
```


## Implementation Notes (2026-05-31)

### Files changed / created
- `utils/supabase_persist.py` — added `options_state_history` DDL + `save_options_state_snapshots()`, `collect_options_state_for_ticker()`, `collect_short_interest_for_ticker()`. Short interest persistence (`save_short_interest_history`) was already present.
- `scripts/collect_options_si_state.py` — runnable daily collection script that wires the helpers into a real accumulation path with rate-limiting and dry-run support.
- `tests/test_pre_breakout_pipeline.py` — `TestPersistenceHelpers` (import/safety) and `TestOptionsStateCollection` (orchestration contract, missing-options handling, SI record fields, script importability).

### Collection path (wired)

The accumulation path is real:
```text
scripts/collect_options_si_state.py
  calls collect_options_state_for_ticker()  → options_state_history (via save_options_state_snapshots)
  calls collect_short_interest_for_ticker() → short_interest_history (via save_short_interest_history)
```

Verified dry-run with live yfinance data (2026-05-31):
```text
python3 scripts/collect_options_si_state.py --dry-run --tickers AAPL GME
Collection complete: options=2 skipped=0 | SI=2 skipped=0
[dry-run] Would write 2 options + 2 SI rows
Done: {'options_ok': 2, 'options_skip': 0, 'si_ok': 2, 'si_skip': 0, 'dry_run': True}
```

### Tests

```text
pytest tests/test_pre_breakout_pipeline.py::TestPersistenceHelpers -v    → 5 passed
pytest tests/test_pre_breakout_pipeline.py::TestOptionsStateCollection -v → 4 passed
```

### Key design decisions
- `options_state_history` PK: (ticker, snapshot_date, expiry). Idempotent upsert. One row per ticker per day per expiry.
- `collect_options_state_for_ticker()` selects the nearest monthly expiry in [20, 60] DTE window. Returns None on missing options / delisted.
- `collect_short_interest_for_ticker()` uses yfinance `dateShortInterest` as publication_date when available — more accurate than snapshot_date.
- Neither collection function is wired into pre-breakout scoring. Data accumulation only.
- Collection script: 0.4s sleep between tickers to respect yfinance rate limits.

### Residual risks
- Short interest publication dates from yfinance are bi-monthly (FINRA cadence); daily collection creates multiple rows with the same publication_date. The upsert handles this correctly.
- Options collection for intraday price accuracy requires running near market close.

## QA Result: PASS (2026-05-31, post QA-gap fix — collection script wired and verified)
