# Task: Option Recommendation Outcome Tracking

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: analytics
Category: options
Risk: data-quality
Effort: L
Target Release: options-stack-v1
Due Date: N/A
Dependencies: TRD-026
Links: `utils/option_outcomes.py`, `utils/supabase_persist.py`, `dashboard/api/main.py`

## Implementation Notes

### Outcome resolution logic

`utils/option_outcomes.py` — `resolve_batch()`:
- fetches unresolved snapshots from `option_candidate_snapshots` via
  `supabase_persist.fetch_unresolved_snapshots()` (line 3088)
- resolves each snapshot over `1d` / `5d` / `10d` windows using yfinance
  historical closes
- computes option and underlying returns, hit markers (`hit_option_tp1`,
  `hit_option_tp2`, `hit_option_stop`, `hit_underlying_t1/t2/stop`),
  and `max_runup_pct` / `max_drawdown_pct`
- persists outcome rows to `option_candidate_outcomes` via
  `supabase_persist.save_option_candidate_outcome()`

### API endpoint

`dashboard/api/main.py:6631` — `POST /api/options/resolve-outcomes`:
- accepts `resolution_type` (`1d` | `5d` | `10d`) and `limit` params
- runs `resolve_batch()` in executor, returns resolved and skipped counts
- exposed via `api.optionsResolveOutcomes()` in `api.ts`

### Schema

`migrations/004_option_candidate_snapshots_and_outcomes.sql` — the
`option_candidate_outcomes` table was created alongside the snapshots table
as part of TRD-026's migration, with FK to `option_candidate_snapshots.id`.

### Tests

`tests/test_option_outcomes.py` — covers:
- 1d / 5d / 10d return calculations
- hit-marker logic (TP1, TP2, stop, underlying targets)
- missing-mark handling (None propagation without crash)
- repeated resolution runs are idempotent (no duplicate outcome rows)

## Original Acceptance Criteria (all met)

- [x] Stored snapshots can generate outcome rows
- [x] Outcome rows include option and underlying returns over standard windows
- [x] Planned target and stop hit markers are tracked
- [x] Unresolved snapshots remain identifiable
- [x] Repeated resolution runs do not corrupt prior outcomes
