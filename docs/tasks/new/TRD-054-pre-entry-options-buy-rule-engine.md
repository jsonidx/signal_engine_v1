# Task: Pre-Entry Options Buy Rule Engine

Status: proposed
Stage: ready
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: execution
Category: options
Risk: trading-logic
Effort: M
Target Release: backlog
Due Date: TBD
Dependencies: TRD-046, TRD-049
Blocked By: none
Links: `utils/option_risk.py`, `utils/option_entry_guardrail.py`, `utils/option_candidates.py`, `dashboard/api/main.py`, `dashboard/frontend/src/pages/TickerPage.tsx`, `dashboard/frontend/src/lib/api.ts`
Success Metric: the system exposes one explicit pre-entry buy decision for each option candidate, so a user can follow a clear `buy_now` / `do_not_buy` rule without IBKR integration or manual interpretation of multiple candidate metrics.

## Problem Statement

The current options stack already computes multiple pre-entry signals:

- PM/risk eligibility
- quote and entry-quality guardrails
- recommended entry and chase price
- projected exits and path scenarios

But the user still has to combine these manually to answer the practical
question:

- should I buy this option now or not?

That makes the product harder to use than it needs to be for live trade entry.

## User Impact

Without a dedicated pre-entry buy rule:

- users may enter trades that pass one gate but fail another
- users must mentally combine several fields before acting
- execution discipline depends too much on interpretation
- buy logic is delayed unnecessarily behind unrelated broker-sync work

## Objective

Add one deterministic pre-entry buy-rule engine for option candidates that works
entirely from existing candidate, PM/risk, and entry-guardrail outputs.

This ticket must not depend on IBKR portfolio sync or position memory.

## Proposed Solution

Create a small deterministic rule layer for option candidates that outputs:

- `buy_decision`
  - `buy_now`
  - `do_not_buy`
- `buy_decision_reason`
- optional `buy_decision_blocker`
  - `risk_policy`
  - `entry_quality`
  - `both`

Recommended v1 decision rule:

- `buy_now` only when:
  - `risk_allowed == true`
  - `entry_action == "enter_now"`
- otherwise:
  - `do_not_buy`

This rule should be presented as the top-level pre-entry decision on the ticker
page and any related options surfaces.

## Scope

- deterministic buy-rule logic in the candidate flow
- API serialization and typed response updates
- ticker-page rendering for a clear buy/no-buy status
- focused tests for truth-table behavior and UI rendering

## Non-Goals

- Do not add IBKR account, portfolio, or position sync.
- Do not require saved position memory or actual fills.
- Do not define hold / take-profit / sell-now logic here.
- Do not add autonomous order execution.
- Do not replace lower-level metrics; preserve them for audit/debugging.

## Constraints

- Keep the rule deterministic and inspectable.
- Keep the first version intentionally simple.
- Preserve `risk_allowed` and `entry_action` as separate fields.
- The top-level decision must explain why a candidate is blocked.

## Acceptance Criteria

- Observable behavior: each option candidate exposes one top-level pre-entry buy
  decision.
- Observable behavior: `buy_now` occurs only when `risk_allowed = true` and
  `entry_action = enter_now`.
- Observable behavior: all other combinations resolve to `do_not_buy`.
- Observable behavior: the UI shows the decision clearly and explains the
  blocker briefly.
- Tests:
  - truth table covers all combinations of `risk_allowed` and `entry_action`
  - serialization includes new fields
  - ticker page renders buy/no-buy state correctly
  - legacy rows degrade safely

## Verification Plan

- focused unit tests for combined decision logic
- endpoint or serialization tests for new fields
- frontend tests for ticker-page rendering
- `make verify` if practical

## QA Notes

- Test scenarios:
  - `risk_allowed=true`, `entry_action=enter_now` → `buy_now`
  - `risk_allowed=true`, `entry_action=enter_if_repriced` → `do_not_buy`
  - `risk_allowed=false`, `entry_action=enter_now` → `do_not_buy`
  - `risk_allowed=false`, `entry_action=skip_for_now` → `do_not_buy`
- Edge cases:
  - missing `entry_action`
  - missing `risk_allowed`
  - historical rows without new fields
- Regression risks:
  - UI confusion if component fields and top-level decision appear to disagree
  - over-compression of useful nuance
  - accidental mixing of post-entry logic into the buy rule

## Launch / Release Notes

- User-facing change summary: option candidates now show one explicit pre-entry
  buy decision based on risk policy and live entry quality.
- Operational notes: this ticket is deliberately independent of IBKR or saved
  positions so it can ship first.
- Rollback notes: hide the top-level buy decision and continue exposing only the
  underlying component fields.

## Post-Launch Validation

- What to monitor:
  - distribution of `buy_now` vs `do_not_buy`
  - blocker mix (`risk_policy` vs `entry_quality` vs `both`)
  - whether users still rely on lower-level fields for routine entry decisions
- How success will be confirmed:
  - users can make a buy decision without manually combining multiple metrics
- Follow-up decision date: after first live usage review of pre-entry buy
  decisions.

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-054, "Pre-Entry Options Buy Rule Engine," in this repo.

Goal:
- Add one deterministic pre-entry buy decision for option candidates that does
  not depend on IBKR integration or saved positions.

Required rule:
- `buy_now` only if `risk_allowed == true` AND `entry_action == "enter_now"`
- otherwise `do_not_buy`

Scope:
- `utils/option_candidates.py`
- any small helper module if needed
- `dashboard/api/main.py`
- `dashboard/frontend/src/lib/api.ts`
- `dashboard/frontend/src/pages/TickerPage.tsx`
- focused tests

Requirements:
- Add fields such as `buy_decision` and `buy_decision_reason`
- Preserve `risk_allowed` and `entry_action`
- Render the pre-entry decision clearly on the ticker page
- Keep behavior deterministic and simple
- Do not add post-entry hold/take-profit/sell logic
- Do not require IBKR position sync

Tests and verification:
- Add backend truth-table tests for all input combinations
- Add serialization coverage
- Add frontend rendering tests
- Run the targeted tests you add
