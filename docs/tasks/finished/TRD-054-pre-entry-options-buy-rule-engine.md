# Task: Pre-Entry Options Buy Rule Engine

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: execution
Category: options
Risk: trading-logic
Effort: M
Target Release: options-stack-v1
Due Date: TBD
Dependencies: TRD-046, TRD-049
Blocked By: none
Links: `utils/option_candidates.py`, `dashboard/api/main.py`, `dashboard/frontend/src/lib/api.ts`, `dashboard/frontend/src/pages/TickerPage.tsx`, `tests/test_option_buy_rule.py`, `dashboard/frontend/src/pages/tests/TickerPage.option-candidates.test.tsx`
Success Metric: each option candidate exposes one explicit pre-entry buy decision (`buy_now` / `do_not_buy`) so the user can act without manually combining multiple candidate metrics.

## Problem Statement

The options stack already computed PM/risk eligibility (`risk_allowed`) and
live entry quality (`entry_action`) independently, but users still had to
combine these manually to answer: "should I buy this option now or not?"
That made the product harder to use and increased the risk of entering trades
that pass one gate but fail another.

## Objective

Add one deterministic pre-entry buy-rule layer to the option candidate pipeline
that outputs a clear `buy_now` / `do_not_buy` decision with a short reason and
an optional blocker label, and surface it prominently on the ticker page.

## Implementation

### Core rule (`utils/option_candidates.py`)

Added `compute_buy_decision(risk_allowed, entry_action) → dict` — a pure,
deterministic function with no external dependencies.

**V1 truth table:**

| `risk_allowed` | `entry_action` | `buy_decision` | `buy_decision_blocker` |
|---|---|---|---|
| `True` | `"enter_now"` | `buy_now` | `None` |
| `False` | `"enter_now"` | `do_not_buy` | `risk_policy` |
| `True` | anything else | `do_not_buy` | `entry_quality` |
| `False` | anything else | `do_not_buy` | `both` |
| missing / ambiguous | any | `do_not_buy` | `both` (safe fallback) |

Three new fields on `OptionCandidate` dataclass:
- `buy_decision: str` — `"buy_now"` | `"do_not_buy"`
- `buy_decision_reason: str` — one-sentence explanation
- `buy_decision_blocker: Optional[str]` — `None` | `"risk_policy"` | `"entry_quality"` | `"both"`

Fields are computed in `get_option_candidates` immediately after TRD-049
guardrail attachment, before the candidate is added to the scored list.

### API serialization (`dashboard/api/main.py`)

`_serialize_candidate` extended with the three new fields. All use `getattr`
with safe defaults so historical / legacy candidate objects serialize without
error.

### TypeScript interface (`dashboard/frontend/src/lib/api.ts`)

`OptionCandidate` interface updated with typed union literals:
- `buy_decision: 'buy_now' | 'do_not_buy'`
- `buy_decision_reason: string`
- `buy_decision_blocker: 'risk_policy' | 'entry_quality' | 'both' | null`

### Ticker page UI (`dashboard/frontend/src/pages/TickerPage.tsx`)

Added `BuyDecisionBadge` component:
- `buy_now` → green badge `BUY NOW` with reason text
- `do_not_buy` → red badge `DO NOT BUY` with reason text

Badge rendered at the top of each `OptionCandidateRow`, above the metrics grid,
so it is the first thing the user sees when evaluating a candidate. Existing
fields (`risk_allowed`, `entry_action`, `EntryGuardrailBanner`) are preserved
for audit/debugging context.

## Non-Goals

- Does not implement hold / take-profit / sell-now logic.
- Does not require IBKR portfolio sync or position memory.
- Does not replace `risk_allowed` or `entry_action` as separate fields.
- Does not touch TRD-052 or TRD-053.

## Verification

### Backend — 22 tests (`tests/test_option_buy_rule.py`)

```
pytest -q tests/test_option_buy_rule.py → 22 passed
```

Covers:
- Full truth table for all (risk_allowed × entry_action) input combinations
- Edge cases: `None`, `""`, unknown action values
- Invariants: reason always non-empty, decision always in valid set, blocker
  is `None` only for `buy_now`
- `OptionCandidate` field defaults
- `_serialize_candidate` round-trip including legacy-object getattr fallback

### Frontend — 55 tests (`TickerPage.option-candidates.test.tsx`)

```
npm test -- --run src/pages/tests/TickerPage.option-candidates.test.tsx → 55 passed
```

New TRD-054 suite (6 tests):
- `buy_now` renders `BUY NOW`
- `do_not_buy` renders `DO NOT BUY`
- Reason text appears for both states
- Both-blocked candidate renders correctly
- Legacy rows without new fields do not crash

All 49 pre-existing tests continue to pass.

## Handoff Notes

- Rule is intentionally simple (v1). Future versions can extend the truth table
  to incorporate regime, IV regime, or portfolio concentration without changing
  the field contract.
- `buy_decision_blocker` is included now so analytics can track the distribution
  of `risk_policy` vs `entry_quality` vs `both` blocks over time.
- If IBKR sync (TRD-052) ships later, it may add a third gate; that would be
  a v2 update to `compute_buy_decision` only — no schema change needed.
