# Task: Option Target Bands and UI Explanation

Status: completed
Stage: done
Type: feature
Priority: P2
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: dashboard
Category: ux
Risk: frontend
Effort: M
Target Release: options-stack-v1
Due Date: TBD
Dependencies: TRD-023, TRD-031, TRD-043, TRD-044, TRD-046, TRD-047, TRD-049
Blocked By: none
Links: `dashboard/frontend/src/pages/TickerPage.tsx`, `dashboard/frontend/src/pages/OptionsPage.tsx`, `dashboard/frontend/src/lib/api.ts`, `dashboard/api/main.py`
Success Metric: the UI presents projected option exits as bounded scenario guidance tied to the stock thesis, without implying false precision or guaranteed fills.

## Problem Statement

Even if `v2` target math is better, the product can still mislead users if it
renders a single precise-looking option target without context. Options are too
path-dependent for a UI that suggests certainty where only scenario guidance
exists.

## User Impact

Without a better presentation layer:

- users may treat projected exits as exact promises
- confidence in the system may be damaged by normal option-path variance
- the UI will not communicate why a target exists or how it relates to the
  underlying thesis

## Objective

Expose `v2` projected exits in the UI and API as scenario-aware guidance rather
than fake exactness, clearly separating underlying thesis levels, projected
option levels, and execution guidance.

## Scope

- API response updates for v2 projected and scenario fields
- frontend rendering on ticker page and options overview
- focused frontend tests for labels, empty states, and method messaging

## Non-Goals

- Do not redesign the broader dashboard information architecture.
- Do not add open-ended AI narrative as the source of the explanation.
- Do not introduce autonomous trade recommendations.

## Implementation Notes (2026-06-06)

### Files changed

- `dashboard/frontend/src/pages/TickerPage.tsx` — added four new components to
  `OptionCandidateRow`:
  - `EntryGuardrailBanner` — coloured badge showing `entry_action` state
    (`enter_now` / `enter_if_repriced` / `skip_for_now`) with FV band and
    overpay % when guardrail is active; hidden for clean `enter_now` rows.
  - `ProjectedExitsSection` — 4-column grid (Entry | T1 | T2 | SL) showing v2
    projected prices with return-pct sub-labels and a method badge
    (`Δ-linear` / `Δ+DTE`). Returns null for `insufficient_inputs`.
  - `UnderlyingLevelsRow` — inline strip showing underlying SL / T1 / T2 and
    holding window; clearly separated from option-level exits.
  - `ScenarioStrip` — compact path display (Fast / Slow / Sideways / Adverse)
    with return % and days-to-resolution; filtered to resolved scenarios only.
  - `OptionTradeSetupGrid` — reworked scale bar footer: switched from
    `justify-between` to absolute-positioned labels so ENTRY label aligns under
    the white dot; added ENTRY column (order type + bid-ask span) to match V2
    grid layout.
- `dashboard/frontend/src/pages/OptionsPage.tsx` — comparator/help text added
  to options overview for projection-method context.
- `dashboard/frontend/src/lib/api.ts` — `OptionCandidate` interface extended
  with all v2 projected, scenario, risk, structure, and guardrail fields.

### Frontend tests

- `dashboard/frontend/src/pages/tests/TickerPage.option-candidates.test.tsx`
  — TRD-045 tests covering v2 projected exits, scenario strip, underlying levels,
  guardrail banner, and graceful fallback for missing fields.

### Verification

```
pytest -q tests/test_option_target_v2.py tests/test_option_scenario.py \
  tests/test_option_comparator.py tests/test_option_risk.py \
  tests/test_option_structure.py tests/test_option_entry_guardrail.py \
  tests/test_option_feature_store.py
# 429 passed

cd dashboard/frontend && npx vitest run \
  src/pages/tests/TickerPage.option-candidates.test.tsx \
  src/pages/tests/OptionsPage.test.tsx
# 70 passed
```

## QA Result: PASS
