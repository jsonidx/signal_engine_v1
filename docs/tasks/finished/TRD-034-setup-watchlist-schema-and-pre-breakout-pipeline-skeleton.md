# Task: Setup Watchlist Schema And Pre-Breakout Pipeline Skeleton

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: automation
Risk: trading-logic
Effort: M
Target Release: pre-breakout-v1
Due Date: TBD
Dependencies: TRD-033
Blocked By: none
Links: TRD-032
Success Metric: a standalone pre-breakout pipeline skeleton can score the universe daily, persist ranked setup-watchlist rows, and remain logically separate from the existing confirmation pipeline.

## Problem Statement

The repo lacks a dedicated persistence model and pipeline boundary for pre-breakout setups, which makes it too easy to contaminate the existing confirmation path.

## User Impact

Without a separate schema and runner, research outputs cannot be audited cleanly and the team risks mixing early-detection logic with confirmation logic.

## Objective

Create the structural foundation for a separate pre-breakout setup-watchlist pipeline.

## Proposed Solution

Add a dedicated `setup_watchlist` persistence path, a daily pipeline entrypoint/skeleton, and deterministic score storage for `PFS` and `PSC` components without changing existing confirmation outputs.

## Scope

- `schema.sql` or migration files as appropriate
- `utils/supabase_persist.py`
- new pipeline module(s) under `utils/` or top-level runner as appropriate
- `run_master.sh` only if a safe parallel/non-invasive hook is needed
- targeted tests for persistence and separation behavior

## Non-Goals

- Do not implement `PFS` or `PSC` scoring logic in this ticket beyond placeholders/interfaces.
- Do not wire Claude Stage 3 yet.
- Do not change `daily_rankings`.

## Constraints

- The new pipeline must be logically separate from the current confirmation pipeline.
- Stored rows must include date, ticker, composite score, component scores, and source version metadata.
- No trade recommendation fields.

## Acceptance Criteria

- Observable behavior:
  - A dedicated storage path exists for setup-watchlist rows.
  - A pipeline skeleton can run daily and persist empty or stubbed results safely.
  - Existing confirmation outputs remain unchanged.
- Tests:
  - Persistence tests for insert/upsert behavior.
  - Separation test proving confirmation data is not modified.
- Documentation:
  - Handoff notes or comments explain the storage schema and boundary.

## Verification Plan

- Run targeted tests for persistence.
- Dry-run the new pipeline skeleton locally.

## QA Notes

- Test scenarios: empty output day, repeated run same day, overlapping ticker already in confirmation pipeline.
- Edge cases: duplicate tickers, missing component scores, null archetype fields pre-Stage-3.
- Regression risks: accidental coupling to existing ranking tables.

## Launch / Release Notes

- User-facing change summary: none yet; backend foundation only.
- Operational notes: safe to run in parallel with current pipeline.
- Rollback notes: disable the new runner and ignore the new table.

## Post-Launch Validation

- What to monitor: write volume, duplicate handling, accidental writes to confirmation tables.
- How success will be confirmed: later signal tickets can write into `setup_watchlist` without schema churn.
- Follow-up decision date: after TRD-035 and TRD-036 land.

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-034, "Setup Watchlist Schema And Pre-Breakout Pipeline Skeleton."

Goal:
- Create the standalone persistence and pipeline skeleton for the new pre-breakout setup watchlist.

Scope:
- Add schema/migration support for a `setup_watchlist` storage model.
- Extend persistence helpers in `utils/supabase_persist.py` as needed.
- Add a daily pipeline skeleton that can score/store pre-breakout rows independently of the confirmation pipeline.
- Store at minimum:
  - date
  - ticker
  - composite score
  - component scores (`pfs_score`, `psc_score`, and placeholder `erm_score`)
  - version/metadata fields needed for audit

Constraints:
- Do not implement real PFS/PSC math here beyond interfaces/placeholders.
- Do not change `daily_rankings` or confirmation scoring.
- No Claude/LLM integration in this ticket.

Tests:
- Add targeted persistence/separation tests.
- Run the tests you add.

Non-goals:
- No trade logic changes.
- No Stage 3 synthesis yet.
```


## Implementation Notes (2026-05-31)

### Files created/changed
- `utils/supabase_persist.py` — added `setup_watchlist` DDL + `save_setup_watchlist_rows()`, `update_setup_watchlist_stage3()`, `fetch_setup_watchlist()`
- `pre_breakout_pipeline.py` — standalone pipeline runner; added `--tickers` override for DB-free runs

### Dependency contract

| Invocation | DB required | yfinance required |
|---|---|---|
| `python3 pre_breakout_pipeline.py` | ✓ (universe + sector) | ✓ |
| `python3 pre_breakout_pipeline.py --dry-run` | ✓ (universe + sector) | ✓ |
| `python3 pre_breakout_pipeline.py --dry-run --tickers A B C` | ✗ none | ✓ |

When `--tickers` is supplied, `load_sector_map()` is NOT called. Sector defaults to
`{}` (all tickers treated as "Unknown"), which is correct for testing. DB calls happen
only when persisting (no `--dry-run`) or when loading the universe from DB (no `--tickers`).

### Verified: truly DB-free offline dry-run (exit 0, no warnings)

Confirmed clean with `DATABASE_URL=""` (no DB at all):

```text
DATABASE_URL="" python3 pre_breakout_pipeline.py \
  --dry-run --tickers AAPL MSFT NVDA META GOOGL AMZN TSLA JPM UNH XOM
Pre-breakout pipeline starting: 2026-05-31 (stage3=False, dry_run=True)
Universe: 10 tickers from --tickers override
Fetching OHLCV for 10 tickers (2026-02-03 → 2026-05-31)...
Price matrix: 81 days × 10 tickers
Scoreable tickers: 10 of 10
Running PFS scoring...
Running PSC scoring...
Stage 2: 0 of 10 tickers pass (composite ≥ 0.40 AND pfs > 0.05)
[dry-run] Would write 10 rows to setup_watchlist
Pipeline complete: {'status': 'ok', 'run_date': '2026-05-31', 'universe_size': 10,
  'scored': 10, 'stage2_passed': 0, 'stage3_run': False, 'pipeline_version': 'v1'}
exit=0
```

No DB warnings. No `load_sector_map` call.

```text
pytest tests/test_pre_breakout_pipeline.py::TestPipelineOfflineDryRun -v
5 passed   (includes test_override_mode_never_calls_load_sector_map)
pytest tests/test_pre_breakout_pipeline.py::TestPersistenceHelpers -v
5 passed
```

### Schema
- Table: `setup_watchlist` PK=(run_date, ticker)
- Fields: composite_score, pfs_score, psc_score, erm_score (NULL), stage2_passed, pipeline_version
- Stage 3 fields added idempotently: archetype, invalidation_condition, setup_grade, key_risk, stage3_run_at

### Isolation from confirmation pipeline
- `pre_breakout_pipeline.py` does NOT touch daily_rankings, candidate_snapshots, or thesis_cache
- Confirmed: pipeline runs in --dry-run --tickers mode, no confirmation tables touched

### PSC-only guard
- Stage 2 gate: `composite_score >= 0.40 AND pfs_score > 0.05`
- A ticker with pfs_score=0 cannot pass Stage 2 regardless of psc_score — enforced in pipeline

## QA Result: PASS (2026-05-31, post QA-gap fix)
