# Task: Unified Options Buy Gate from Risk and Entry Action

Status: superseded
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
Due Date: N/A
Dependencies: TRD-046, TRD-049
Superseded By: TRD-054
Links: `utils/option_candidates.py`, `utils/option_entry_guardrail.py`, `dashboard/api/main.py`, `dashboard/frontend/src/pages/TickerPage.tsx`, `dashboard/frontend/src/lib/api.ts`

## Supersession Note

**This ticket is superseded by TRD-054 (Pre-Entry Options Buy Rule Engine), which
delivered the identical scope and is the authoritative implementation record.**

TRD-051 was written to propose a unified `buy_now` / `do_not_buy` gate combining
`risk_allowed` and `entry_action`. TRD-054 was later created to carry that proposal
into implementation, and it shipped the feature in full. TRD-051 was never
separately implemented and should not be treated as pending work.

**Do not re-implement or re-open TRD-051.  Refer to TRD-054 for all implementation
details, test coverage, and handoff notes.**

## What TRD-054 Delivered (Summary)

- `compute_buy_decision(risk_allowed, entry_action)` in `utils/option_candidates.py`
- `buy_decision` / `buy_decision_reason` / `buy_decision_blocker` fields on `OptionCandidate`
- `_serialize_candidate` includes new fields with `getattr` fallback for legacy rows
- `BuyDecisionBadge` component and rendering in `TickerPage.tsx`
- Full truth-table tests in `tests/test_option_buy_rule.py`
- Frontend tests in `src/pages/tests/TickerPage.option-candidates.test.tsx`

## Known Gap (not blocking closure)

`buy_decision` is not currently rendered in the cross-ticker `/options` screener
table — it is only shown on the per-ticker deep-dive page. This is addressed in
TRD-080 (Options Screener Snapshot Architecture), which will surface the field
when the screener result row is redesigned.

## Original Problem Statement

The options stack exposed `risk_allowed` (PM/risk gate) and `entry_action`
(live-entry quality gate) as separate fields, leaving users to combine them
manually. TRD-054 resolved this by adding a single top-level `buy_decision`
field that combines both gates deterministically.

## Acceptance Criteria (all met via TRD-054)

- [x] Each option candidate exposes one top-level `buy_decision` field
- [x] `buy_now` only when `risk_allowed = true` AND `entry_action = "enter_now"`
- [x] All other combinations resolve to `do_not_buy`
- [x] `buy_decision_reason` explains the blocker in one sentence
- [x] `buy_decision_blocker` encodes `risk_policy` / `entry_quality` / `both` / `None`
- [x] UI renders the decision on the ticker page
- [x] Legacy rows degrade safely via `getattr` fallback
- [x] Truth-table tests cover all combinations
