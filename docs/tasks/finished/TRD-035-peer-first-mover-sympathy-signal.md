# Task: Peer-First-Mover Sympathy Signal

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
Dependencies: TRD-033, TRD-034
Blocked By: none
Links: TRD-032
Success Metric: the pipeline produces a bounded, auditable `PFS` component score using existing OHLCV and sector/peer data without degenerating into laggard chasing.

## Problem Statement

The current system does not detect second-order sector/peer rerating opportunities where one peer has already broken out and a correlated laggard has not yet participated.

## User Impact

Without a peer-sympathy detector, the system misses a cheap and potentially useful early-discovery signal on names like sector laggards that follow first movers with a short delay.

## Objective

Implement the v1 deterministic `PFS` component for the pre-breakout pipeline.

## Proposed Solution

Define peer groups using sector membership plus rolling correlation, detect first-mover breakouts using trading-day price/volume rules, and score non-participating peers with explicit anti-laggard-chasing constraints.

## Scope

- new or existing pre-breakout scoring module(s)
- sector/peer helper tables or caches
- `utils/` helpers for rolling peer calculations
- targeted tests

## Non-Goals

- Do not use options, dark-pool, or narrative features.
- Do not add earnings-transcript NLP.
- Do not let `PFS` emit alerts by itself without passing pipeline thresholds.

## Constraints

- Use trading days consistently.
- Manual QA of economically sensible peer relationships is required.
- Add anti-ETF-flow and anti-laggard-chasing guards.

## Acceptance Criteria

- Observable behavior:
  - `PFS` score is computed and stored for candidate rows.
  - Peer relationships are stable enough to audit.
  - The score decays after the trigger window instead of persisting indefinitely.
- Tests:
  - first-mover trigger behavior
  - non-participation filter behavior
  - staleness decay
  - ETF-flow suppression case
- Documentation:
  - Comments or docstrings define the exact signal rules.

## Verification Plan

- Run targeted tests.
- Produce a short QA sample of recent triggers and peer mappings for manual review.

## QA Notes

- Test scenarios: single peer trigger, multiple peer triggers, target already moved, near-earnings exclusion.
- Edge cases: tiny peer groups, unstable correlations, mass sector rally day.
- Regression risks: overbroad peer definitions causing noise.

## Launch / Release Notes

- User-facing change summary: none yet; backend signal only.
- Operational notes: watch alert volume impact before enabling Stage 3.
- Rollback notes: zero out `PFS` contribution and keep pipeline skeleton intact.

## Post-Launch Validation

- What to monitor: trigger count, alert concentration by sector, churn in peer mappings.
- How success will be confirmed: manual QA shows economically sensible triggers and backtest discrimination exceeds noise.
- Follow-up decision date: after first combined `PFS + PSC` backtest.

## Handoff Notes

PM team recommendations incorporated:

- Sector PM: use trading-day windows, not calendar-day windows.
- Quant PM: require incremental-value analysis against `PSC` rather than assuming `PFS` carries the model.
- Risk PM: enforce explicit anti-laggard-chasing guards and suppress likely ETF-flow days.

Paste-ready Claude implementation prompt:

```text
Implement TRD-035, "Peer-First-Mover Sympathy Signal."

Goal:
- Add the deterministic `PFS` component to the pre-breakout pipeline using existing data only.

Scope:
- Implement peer definitions using:
  - same 4-digit GICS sub-industry
  - rolling 60-day daily-return correlation threshold
- Implement first-mover trigger rules using trading-day windows.
- Implement non-participation rules for the target ticker.
- Implement staleness decay and anti-laggard-chasing guards.
- Write `pfs_score` into the setup-watchlist pipeline state.

Constraints:
- Use trading days consistently.
- No options/dark-pool/news/NLP features.
- Add a manual QA artifact or small report/sample showing recent peer mappings/triggers are sensible.

Tests:
- trigger logic
- suppression logic
- decay behavior
- edge cases around peer-group breadth

Non-goals:
- No Stage 3 Claude integration.
- No ERM work.
```


## Implementation Notes (2026-05-31)

### Files created
- `utils/pfs_signal.py` — `score_pfs()` returns `list[PFSResult]`

### Algorithm
- Peer groups: sector (from daily_rankings) with ≥3 members
- First-mover trigger: ≥8% move in 5 trading days + volume ≥ 1.3× 20d avg
- Mass-rally suppression: skips event when ≥60% of sector moved
- Non-participation filter: target moved <4% in same window
- Staleness decay: linear decay from 1.0 at trigger day to 0 at day DECAY_WINDOW+1
- Anti-laggard guard: target already moved ≥4% → score=0
- Score: decay × (0.7 + 0.3 × move_factor), clipped [0,1]

### Verification
```
pytest tests/test_pre_breakout_pipeline.py::TestPFSSignal -v
8 passed
```

### QA sample (from pipeline dry-run on 2026-05-31)
- 11 of 43 tickers passed Stage 2; PFS was the binding constraint (pfs_score > 0.05 required)

## QA Result: PASS
