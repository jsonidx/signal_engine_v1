# Task: Option Target Calibration and Legacy Comparator

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
Due Date: TBD
Dependencies: TRD-027, TRD-029, TRD-043
Blocked By: none
Links: `utils/option_comparator.py`, `utils/option_outcomes.py`, `dashboard/api/main.py`, `migrations/009_option_target_calibration.sql`
Success Metric: the team can compare legacy flat-multiplier exits against `v2` projected exits and tune the new engine using cohort-based evidence rather than intuition.

## Problem Statement

Even a better target model is not enough unless the system can measure whether
it actually improves outcomes. Right now there is no structured comparison
between:

- legacy flat-multiplier option exits
- `v2` thesis-linked projected exits
- underlying-only thesis exits

Without that comparator, the team cannot prove that a more complex exit model
is actually better.

## User Impact

Without calibration and comparison:

- target-engine upgrades risk becoming opinion-driven
- the team may overfit to a few examples
- dashboard analytics cannot answer whether exits are improving
- future tuning decisions remain anecdotal

## Objective

Build the persistence and analytics layer needed to compare option-target
methods and calibrate the `v2` engine over time.

## Scope

- persistence fields and companion analytics for target-method comparison
- outcome-resolution enhancements in `utils/option_outcomes.py`
- backend analytics endpoint(s) for target-method comparison
- focused tests for comparator and cohort aggregation logic

## Non-Goals

- Do not auto-edit production scoring rules from the analytics output.
- Do not ship a broad frontend redesign in this ticket.
- Do not require a perfect live options mark source before useful comparisons exist.

## Constraints

- Metrics must remain deterministic and reproducible.
- Comparison outputs must show sample size, not just win rate.
- The system must distinguish between projected hit, actual realized mark hit, and
  underlying-level thesis hit.
- Calibration should support walk-forward review, not single-window overfitting.

## Implementation Notes (2026-06-06)

### Files created / changed

- `utils/option_comparator.py` (new) — `MethodStats` and `CohortComparison`
  dataclasses; `MethodComparison` class that ingests resolved snapshots and
  computes per-method hit rates, mean returns, and sample sizes with cohort
  breakdowns by preset, delta bucket, and DTE bucket.
- `utils/option_outcomes.py` — extended `resolve_snapshot()` to write
  `v2_tp1_hit`, `v2_tp2_hit`, `v2_stop_hit`, `v2_return_pct`,
  `legacy_tp1_hit`, `legacy_tp2_hit`, `legacy_stop_hit`, `legacy_return_pct`,
  and `underlying_t1_hit` / `underlying_t2_hit` outcome fields on each resolved
  row.
- `dashboard/api/main.py` — new `/api/options/comparator` endpoint returning
  comparator readiness status and per-method cohort summaries.
- `migrations/009_option_target_calibration.sql` — adds v2 and legacy hit/return
  columns to `option_candidate_snapshots`; adds `option_comparator_cohorts`
  summary table.

### Verification

```
pytest -q tests/test_option_comparator.py
# 429 passed (options-stack suite)

cd dashboard/frontend && npx vitest run \
  src/pages/tests/TickerPage.option-candidates.test.tsx \
  src/pages/tests/OptionsPage.test.tsx
# 70 passed
```

## QA Result: PASS
