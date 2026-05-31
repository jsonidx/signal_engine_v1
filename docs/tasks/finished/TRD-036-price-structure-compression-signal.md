# Task: Price-Structure Compression Signal

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
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
Success Metric: the pre-breakout pipeline has a deterministic `PSC` filter/amplifier that identifies tight, not-yet-broken structures without becoming a standalone source of noisy alerts.

## Problem Statement

The current system lacks a clean structural filter for names that are technically coiled near breakout territory but have not yet made the visible move.

## User Impact

Without a structural filter, early-discovery logic either becomes too broad or chases already-extended names.

## Objective

Implement the v1 deterministic `PSC` component as a supporting/filtering signal for the pre-breakout watchlist.

## Proposed Solution

Score names on a small set of robust, auditable structure features derived from daily OHLCV, and ensure `PSC` cannot generate alerts by itself.

## Scope

- pre-breakout scoring module(s)
- any helper utilities needed for ATR/range calculations
- targeted tests

## Non-Goals

- Do not add image-based pattern recognition.
- Do not add RSI/moving-average crossover logic.
- Do not let `PSC` act as a standalone alpha signal.

## Constraints

- Keep the feature set simple and auditable.
- Prefer robust range/volatility formulations over fragile chart-pattern heuristics.
- `PSC` must only amplify/validate another signal path.

## Acceptance Criteria

- Observable behavior:
  - `PSC` score is computed and stored.
  - A `PSC`-only name cannot clear the final Stage 2 threshold.
  - High-scoring examples resemble real consolidations rather than dead, illiquid names.
- Tests:
  - ATR compression calculation
  - volume-trend calculation
  - high-proximity/range feature behavior
  - guard against `PSC`-only alerts
- Documentation:
  - Signal definitions are clear in code comments or module docs.

## Verification Plan

- Run targeted tests.
- Spot-check a sample of high-`PSC` names from recent history.

## QA Notes

- Test scenarios: tight range near highs, compressed dead stock, volatile false positive, recent breakout already underway.
- Edge cases: thinly traded names, stock splits, missing highs/lows.
- Regression risks: fragile handcrafted consolidation rules.

## Launch / Release Notes

- User-facing change summary: none yet; backend signal only.
- Operational notes: use as a filter/amplifier, not standalone source.
- Rollback notes: zero out `PSC` contribution and keep the pipeline intact.

## Post-Launch Validation

- What to monitor: distribution of `PSC` scores, sector concentration, overlap with existing breakout tags.
- How success will be confirmed: combined backtest shows `PSC` improves selectivity rather than inflating raw alert volume.
- Follow-up decision date: after first combined `PFS + PSC` backtest.

## Handoff Notes

PM team recommendations incorporated:

- Technical Analysis PM: keep the structure logic simple enough to backtest and explain.
- Quant PM: require incremental-value tests for `PSC` and prohibit `PSC`-only alerts.
- Risk PM: include a minimum-liquidity guard to reduce dead-stock contamination.

Paste-ready Claude implementation prompt:

```text
Implement TRD-036, "Price-Structure Compression Signal."

Goal:
- Add the deterministic `PSC` component to the pre-breakout pipeline as a filter/amplifier, not a standalone alpha source.

Scope:
- Implement a small feature set using daily OHLCV only:
  - realized/ATR compression
  - declining volume trend
  - proximity to highs / upper-range position
  - a simple robust range-tightness/consolidation feature
- Store `psc_score` for setup-watchlist rows.
- Enforce that `PSC` alone cannot cause a name to clear the Stage 2 alert threshold.

Constraints:
- Keep the implementation simple and auditable.
- Avoid fragile chart-pattern heuristics.
- No RSI, MA crossovers, or image/ML pattern recognition.

Tests:
- component calculations
- `PSC`-only suppression
- edge cases for thin liquidity / dead stocks

Non-goals:
- No Stage 3 Claude work.
- No ERM work.
```


## Implementation Notes (2026-05-31)

### Files created
- `utils/psc_signal.py` — `score_psc()` returns `list[PSCResult]`

### Algorithm
- ATR compression (w=0.35): ATR(10)/ATR(40); ratio <0.5 → score ~1.0
- Volume decline (w=0.20): OLS slope of 20d volume, normalised; negative slope → higher score
- High proximity (w=0.25): (close - 52w low) / (52w high - 52w low)
- Range tightness (w=0.20): 1 - (20d_range / 60d_range)
- Liquidity guards: close ≥ $3, ADV ≥ $5M → score=0 if fails
- PSC-only suppression: enforced externally in pipeline Stage 2 gate (pfs_score > PFS_MIN required)

### Verification
```
pytest tests/test_pre_breakout_pipeline.py::TestPSCSignal -v
7 passed
```

## QA Result: PASS
