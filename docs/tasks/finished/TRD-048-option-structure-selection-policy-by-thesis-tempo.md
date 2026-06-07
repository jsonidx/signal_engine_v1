# Task: Option Structure Selection Policy by Thesis Tempo

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: analytics
Category: options
Risk: trading-logic
Effort: M
Target Release: options-stack-v1
Due Date: TBD
Dependencies: TRD-022, TRD-043, TRD-046
Blocked By: none
Links: `utils/option_structure.py`, `utils/option_candidates.py`, `dashboard/api/main.py`, `migrations/008_option_structure_policy.sql`
Success Metric: the options engine explicitly maps thesis tempo and setup type to the appropriate contract structure, reducing mismatches between the stock thesis and the selected option expression.

## Problem Statement

A contract can pass liquidity and delta filters and still be the wrong structure
for the thesis. Short-term breakout theses defaulted into slow expensive LEAPS
structures; multi-month macro theses were expressed with fragile short-dated
options. Without explicit structure-selection policy the engine returned valid
contracts that were poorly matched to the intended holding pattern.

## Objective

Add a deterministic policy layer that maps thesis tempo and setup archetype to
the preferred option structure family before final contract scoring.

## Non-Goals

- Do not add complex multi-leg strategies unless already planned elsewhere.
- Do not let an LLM choose the structure family.
- Do not redesign the whole options screener UX.

## Implementation Notes (2026-06-06)

### Files created / changed

- `utils/option_structure.py` (new) — `classify_structure_archetype()` maps
  thesis time horizon and conviction to one of five archetypes:
  - `short_breakout` (≤10 days) — tight DTE 7–21, delta 0.40–0.60, no LEAPS
  - `medium_swing` (11–30 days) — DTE 21–45, delta 0.35–0.55
  - `slow_directional` (31–90 days) — DTE 45–90, LEAPS allowed
  - `event_trade` — catalyst-gated DTE and IV-tolerance rules
  - `macro_thematic` (>90 days) — LEAPS preferred, wider delta band
  - Returns `StructurePolicy` dataclass with `archetype`, preferred DTE range,
    delta band, max-IV-richness, `leaps_allowed`, and `structure_rationale`.
  - Degrades safely to `medium_swing` conservative defaults on missing inputs.
- `utils/option_candidates.py` — `get_option_candidates()` calls
  `classify_structure_archetype()` and attaches `structure_archetype` and
  `structure_rationale` to each candidate before scoring.
- `dashboard/api/main.py` — `_serialize_candidate()` includes `structure_archetype`
  and `structure_rationale`.
- `migrations/008_option_structure_policy.sql` — adds `structure_archetype` and
  `structure_rationale` columns to `option_candidate_snapshots`.

### Verification

```
pytest -q tests/test_option_structure.py
# 429 passed (options-stack suite)

cd dashboard/frontend && npx vitest run \
  src/pages/tests/TickerPage.option-candidates.test.tsx \
  src/pages/tests/OptionsPage.test.tsx
# 70 passed
```

## QA Result: PASS
