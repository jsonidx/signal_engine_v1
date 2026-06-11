# Task: Options Resolution and Accuracy Module

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: analytics
Category: options
Risk: metrics
Effort: M
Target Release: options-stack-v1
Due Date: N/A
Dependencies: TRD-027
Links: `dashboard/api/main.py`, `dashboard/frontend/src/pages/OptionsPage.tsx`

## Implementation Notes

### Backend

`dashboard/api/main.py:6872` — `GET /api/options/accuracy`:
- aggregates `option_candidate_snapshots` joined against
  `option_candidate_outcomes` over a configurable lookback window (`days`
  param, default 90, cached 30 min)
- returns cohort win rates with sample size for: `by_preset`, `by_delta_bucket`,
  `by_dte_bucket`, `by_iv_bucket`, `by_spread_bucket`, `by_chain_source`,
  `by_holding_window`
- also returns `suppression_reasons` and `rejection_reasons` frequency tables
- returns `data_available: false` gracefully when no resolved outcomes exist

### Frontend

`OptionsPage.tsx` — `AccuracyPanel` (Accuracy tab):
- period selector (30 / 60 / 90 / 180 / 365 days)
- `CohortTable` component renders each dimension with N, win rate, TP1 hit,
  stop hit, avg option and underlying 5d returns
- `FreqTable` component renders suppression/rejection reason frequency
- `data_available: false` shows `EmptyState` with explanation message

`api.ts` — `OptionsAccuracyResponse` type; `api.optionsAccuracy(days)` call.

### Tests

`tests/test_options_screener.py` — `TestOptionsAccuracyEndpoint`:
- returns 200
- response shape (all cohort keys present)
- preset cohort includes sample_size field
- DB failure returns empty gracefully
- days parameter is respected in query

## Original Acceptance Criteria (all met)

- [x] Dashboard shows options-specific analytics section
- [x] Cohort metrics include both hit rate and sample size
- [x] Performance visible by preset, delta bucket, DTE bucket (and more)
- [x] Target and stop hit rates visible for calibration review
- [x] Sparse / empty dataset states handled cleanly
