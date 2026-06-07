# Task: Option Target Engine v2

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
Dependencies: TRD-022, TRD-026, TRD-027, TRD-031
Blocked By: none
Links: `utils/option_candidates.py`, `utils/option_outcomes.py`, `dashboard/api/main.py`, `migrations/006_option_target_v2.sql`
Success Metric: option recommendations no longer use flat `1.5x / 2.0x / 0.5x` premium multipliers as the primary exit engine and instead produce thesis-linked projected targets and stop levels derived from the underlying setup and contract characteristics.

## Problem Statement

The option exit plan applied the same premium multipliers to every contract:

- `option_take_profit_1 = mid * 1.50`
- `option_take_profit_2 = mid * 2.00`
- `option_stop_loss = mid * 0.50`

That ignored distance to the underlying thesis targets, delta sensitivity, DTE
decay profile, IV regime, moneyness, and event risk. Exit levels were useful
as placeholders but not reliable enough for execution-grade trade planning.

## Objective

Replace the flat premium-multiplier exit logic with a deterministic `v2`
projection engine that maps the stock thesis into option-level TP1 / TP2 / stop
guidance. The engine should remain rules-first, inspectable, and testable.

## Non-Goals

- Do not let AI invent option exits.
- Do not build a full stochastic pricing model.
- Do not require real-time Greeks if unavailable.

## Implementation Notes (2026-06-06 / 2026-06-07)

### v2 engine (`utils/option_candidates.py`)

- `compute_target_projections()` maps underlying thesis targets to option-level
  projected exits using two methods:
  - `delta_dte_adjusted` — primary; uses contract delta and remaining DTE to
    project option price change from thesis target move, with a sqrt-theta
    decay haircut applied over the holding window.
  - `delta_only` — fallback when DTE is missing or zero; linear delta
    approximation with no time-decay adjustment.
  - `insufficient_inputs` — when delta and/or underlying targets are
    unavailable; triggers flat-estimate fallback in the UI.
- Returns `projected_option_tp1`, `projected_option_tp2`, `projected_option_stop`,
  `projected_tp1_return_pct`, `projected_tp2_return_pct`, `projected_stop_return_pct`,
  `target_projection_method`.
- `migration/006_option_target_v2.sql` adds the projected-exit columns to
  `option_candidate_snapshots`.

### Legacy flat fields retention (analytics-only)

The legacy fields (`option_take_profit_1/2`, `option_stop_loss`) remain computed
and persisted solely for the TRD-044 comparator — so historical rows can
compare legacy vs v2 exit math. They are no longer the primary recommendation
output and are not rendered as the main exit plan in the UI.

### UI: v2 as the primary exit display

- `ProjectedExitsSection` in `TickerPage.tsx` is now the sole exit display
  component in `OptionCandidateRow`.
- For `delta_only` / `delta_dte_adjusted` rows: full v2 grid with method badge
  (`Δ-linear` / `Δ+DTE`), return percentages, and entry column.
- For `insufficient_inputs` rows: explicit fallback section labelled
  **Exits (estimated)** with `flat` badge and explanatory text
  ("Insufficient chain data for v2 projection — using 1.5× / 2× / 0.5×
  flat estimates."). Values rendered at reduced opacity to distinguish
  from v2 projections.
- `OptionTradeSetupGrid` (the legacy Trade Setup scale-bar component) removed
  from `OptionCandidateRow`. The scale bar anchored users to flat multipliers
  as if they were the primary exit plan.

### Files changed

- `utils/option_candidates.py` — `compute_target_projections()`, v2 fields
  added to candidate dict; legacy fields retained with "analytics-only" comment.
- `migrations/006_option_target_v2.sql` — projected-exit columns.
- `dashboard/api/main.py` — v2 fields serialized; legacy fields retained for
  comparator endpoint.
- `dashboard/frontend/src/pages/TickerPage.tsx` — `ProjectedExitsSection`
  updated with flat-estimate fallback branch; `_EntryCol` helper extracted;
  `OptionTradeSetupGrid` call removed from `OptionCandidateRow`.
- `dashboard/frontend/src/pages/tests/TickerPage.option-candidates.test.tsx` —
  test updated to reflect fallback section renders for `insufficient_inputs`;
  new test added for flat-estimate fallback content.

### Verification

```
pytest -q tests/test_option_target_v2.py tests/test_option_comparator.py \
  tests/test_option_risk.py tests/test_option_scenario.py \
  tests/test_option_structure.py tests/test_option_entry_guardrail.py \
  tests/test_option_feature_store.py
# 429 passed

cd dashboard/frontend && npx vitest run \
  src/pages/tests/TickerPage.option-candidates.test.tsx \
  src/pages/tests/OptionsPage.test.tsx
# 71 passed
```

## QA Result: PASS
