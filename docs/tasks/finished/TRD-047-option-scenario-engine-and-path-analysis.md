# Task: Option Scenario Engine and Path Analysis

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: analytics
Category: options
Risk: trading-logic
Effort: L
Target Release: options-stack-v1
Due Date: TBD
Dependencies: TRD-043, TRD-044, TRD-046
Blocked By: none
Links: `utils/option_scenario.py`, `utils/option_candidates.py`, `dashboard/api/main.py`
Success Metric: option recommendations and analytics can evaluate not just terminal target levels, but multiple realistic price-path and time-path scenarios that materially affect option outcomes.

## Problem Statement

The `v2` target engine improved exit math, but a single projected option price
per stock target was still incomplete. Options are strongly path-dependent: the
same underlying thesis can produce very different option outcomes depending on
whether the stock reaches the target quickly, drifts slowly, chops sideways, or
gaps through the stop.

## User Impact

Without scenario-path analysis:

- users may overtrust a base-case target projection
- contracts may look attractive on terminal price alone but fail in realistic paths
- short-DTE contracts may be systematically overstated

## Objective

Add a deterministic scenario engine that evaluates option recommendations under
multiple plausible price/time paths, not just a single terminal outcome.

## Non-Goals

- Do not build a full Monte Carlo or stochastic-volatility engine.
- Do not require continuous intraday options marks.
- Do not let an LLM invent scenario math.

## Implementation Notes (2026-06-06)

### Files created / changed

- `utils/option_scenario.py` (new) — `compute_scenario_set()` produces five
  deterministic path scenarios for a given contract:
  - `fast_target` — underlying reaches T1 in ≤30% of holding window; minimal
    theta decay
  - `slow_target` — underlying reaches T1 at 80% of holding window; substantial
    theta haircut via sqrt-theta decay model
  - `sideways_decay` — no move; full theta decay applied; penalises short-DTE
    contracts heavily
  - `adverse_stop` — underlying moves to stop in 40% of window
  - `gap_overshoot` — gap through stop in 1 day; models limit of loss
  - Each scenario carries `projected_return_pct`, `days_to_resolution`,
    `input_method` (`delta_dte_adjusted` / `delta_only` / `insufficient_inputs`),
    and `exit_guidance` text.
  - `scenario_set_to_dicts()` serializes for API and persistence.
- `utils/option_candidates.py` — `get_option_candidates()` calls
  `compute_scenario_set()` and attaches results to each `OptionCandidate`.
- `dashboard/api/main.py` — `_serialize_candidate()` includes `scenarios` array
  in response payload.

### Verification

```
pytest -q tests/test_option_scenario.py
# 429 passed (options-stack suite)

cd dashboard/frontend && npx vitest run \
  src/pages/tests/TickerPage.option-candidates.test.tsx \
  src/pages/tests/OptionsPage.test.tsx
# 70 passed
```

## QA Result: PASS
