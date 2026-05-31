# Task: Pre-Breakout Outcome Tracking And Learning Loop

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: research
Risk: trading-logic
Effort: M
Target Release: pre-breakout-v1
Due Date: TBD
Dependencies: TRD-033, TRD-034, TRD-035, TRD-036
Blocked By: none
Links: TRD-032
Success Metric: every setup-watchlist entry can be resolved into dated forward-outcome records that support threshold tuning, signal attribution, and later statistical/ML research.

## Problem Statement

The current pre-breakout program plan persists setup candidates, but it does not yet define a dedicated feedback loop that converts those candidates into labeled outcomes for learning and recalibration.

## User Impact

Without explicit outcome tracking, the team cannot tell which setup types actually worked, which factors added value, or how to recalibrate the algorithm over time. Any future ML work would be built on incomplete labels.

## Objective

Persist point-in-time setup records and resolve them into standardized forward-outcome labels so the pipeline can learn from results and support later model refinement.

## Proposed Solution

Add a dedicated outcome-tracking layer for `setup_watchlist` entries that stores forward returns, sector-adjusted returns, drawdown, confirmation-pipeline overlap, and outcome labels at fixed horizons. Use that dataset for threshold tuning and later statistical/ML experimentation, while keeping v1 production scoring deterministic.

## Scope

- schema/migration support for setup outcomes
- persistence helpers in `utils/supabase_persist.py`
- resolution logic in a new helper/module under `utils/`
- targeted tests
- optional small reporting helper for summary metrics

## Non-Goals

- Do not introduce ML into production scoring in this ticket.
- Do not replace deterministic `PFS`/`PSC` scoring with a trained model.
- Do not use future data in live scoring logic.

## Constraints

- Outcome labels must be computed strictly after the setup date.
- Use trading days consistently.
- Labels must support both deterministic threshold tuning and later offline ML/statistical work.
- Keep the schema point-in-time safe and audit-friendly.

## Acceptance Criteria

- Observable behavior:
  - Each setup-watchlist entry can be resolved into standardized outcome records.
  - Stored outcome fields include raw and sector-adjusted returns at multiple horizons.
  - The system records whether a setup later appeared in the confirmation pipeline and how many days later.
  - A simple summary output can report hit rate and factor attribution by setup cohort.
- Tests:
  - outcome calculation for 5/10/20/40 trading-day horizons
  - sector-adjusted return calculation
  - max drawdown / adverse excursion calculation
  - confirmation-pipeline overlap calculation
  - boundary cases near the end of available data
- Documentation:
  - exact label definitions and horizon rules are documented in code comments or task notes.

## Verification Plan

- Run targeted tests for label calculation and persistence.
- Resolve a small synthetic sample and manually inspect expected outcomes.

## QA Notes

- Test scenarios: positive breakout, failed setup, delayed breakout, setup that later becomes a confirmation candidate.
- Edge cases: delisting/missing forward prices, ticker symbol changes, overlapping repeated setup entries.
- Regression risks: look-ahead leakage if resolution logic reads future data during active scoring.

## Launch / Release Notes

- User-facing change summary: none; internal research and learning infrastructure.
- Operational notes: this dataset becomes the basis for future threshold recalibration and ML research.
- Rollback notes: disable the resolver job and retain existing historical rows.

## Post-Launch Validation

- What to monitor: row counts, missing outcome fields, late-resolution behavior, duplicate resolutions.
- How success will be confirmed: the team can query historical setups and evaluate which factor combinations worked.
- Follow-up decision date: after 60 trading days of accumulated setup outcomes.

## Handoff Notes

PM team recommendation summary:

- Quant PM: this is the minimum viable learning loop. Without labels, there is no honest way to improve the model.
- ML PM: keep production deterministic; use this dataset first for offline feature evaluation, calibration, and only later for ML.
- Risk PM: include drawdown/adverse excursion, not just forward return, or the learning loop will reward fragile setups.
- Execution PM: record overlap with the confirmation pipeline and lead time, because "worked" is not enough; it must have worked early enough to matter.

Recommended stored fields per resolved setup:

- setup date
- ticker
- composite setup score
- component scores: `pfs_score`, `psc_score`, `erm_score` if present
- Stage 3 fields if present: `archetype`, `setup_grade`, `key_risk`
- forward raw returns: `ret_5d`, `ret_10d`, `ret_20d`, `ret_40d`
- forward sector-adjusted returns: `ret_5d_excess`, `ret_10d_excess`, `ret_20d_excess`, `ret_40d_excess`
- max adverse excursion over 20d and 40d
- max favorable excursion over 20d and 40d
- binary outcome labels at 20d and 40d
- whether the name later hit the confirmation pipeline
- days from setup alert to confirmation alert
- active market regime on setup date

Suggested label set for offline learning:

- `success_20d`: sector-adjusted 20d return > +10%
- `success_40d`: sector-adjusted 40d return > +5%
- `failed_20d`: sector-adjusted 20d return < 0%
- `confirmed_later`: appeared in confirmation pipeline after setup date

This creates the minimal dataset needed for:

- threshold tuning
- signal-weight calibration
- factor attribution
- archetype-level analysis
- later logistic-regression / tree-model / ranking-model research

Paste-ready Claude implementation prompt:

```text
Implement TRD-040, "Pre-Breakout Outcome Tracking And Learning Loop."

Goal:
- Convert setup-watchlist entries into standardized historical outcome labels so the pre-breakout system can learn from results.

Scope:
- Add schema/persistence support for resolved setup outcomes.
- Implement resolution logic that computes, for each setup entry:
  - raw returns at 5/10/20/40 trading days
  - sector-adjusted returns at 5/10/20/40 trading days
  - max adverse excursion / max favorable excursion
  - binary success/failure labels
  - whether and when the setup later appeared in the confirmation pipeline
- Keep the output point-in-time safe and auditable.
- Add targeted tests.

Constraints:
- No ML in production scoring.
- No future-data leakage into active setup scoring.
- Use trading days consistently.
- Include drawdown/adverse-excursion metrics, not just forward returns.

Tests:
- horizon-return calculations
- sector-adjusted outcomes
- overlap-with-confirmation logic
- boundary cases near unavailable future data

Non-goals:
- Do not replace deterministic scoring.
- Do not add new alpha signals in this ticket.
```


## Implementation Notes (2026-05-31)

### Files created/changed
- `utils/supabase_persist.py` — added `setup_watchlist_outcomes` DDL + `save_setup_outcome()`, `fetch_unresolved_setup_watchlist_rows()`
- `utils/setup_outcome_resolver.py` — `resolve_outcome()` (single row) + `run_resolution_batch()` (batch with yfinance)

### Stored fields
- Raw returns: ret_5d, ret_10d, ret_20d, ret_40d
- Sector-adjusted excess: ret_5d_excess … ret_40d_excess (vs sector ETF or SPY fallback)
- MAE/MFE over 20d and 40d (adverse/favorable excursion)
- Labels: success_20d (adj_ret_20d > 10%), success_40d (adj_ret_40d > 5%), failed_20d (adj_ret_20d < 0%)
- Confirmation overlap: confirmed_later (bool), days_to_confirmation (trading days)
- Maturity flags: mature_20d, mature_40d (based on whether fwd date <= today)

### Point-in-time safety
- `resolve_outcome()` accepts `today` parameter; all price lookups use prices at or after setup_date
- No future-data leakage: entry price is taken from the first trading day ≥ setup_date

### Verification
```
pytest tests/test_pre_breakout_pipeline.py::TestOutcomeResolver -v
7 passed
```

### Residual risks
- 40d outcomes immature for all current setup_watchlist rows (system has <48 trading days of history)
- `_fetch_confirmation_dates()` and `_fetch_regime()` make live DB calls; mock in future integration tests

## QA Result: PASS
