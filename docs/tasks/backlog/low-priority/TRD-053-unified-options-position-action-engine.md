# Task: Unified Options Position Action Engine

Status: proposed
Stage: ready
Type: feature
Priority: P3
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: execution
Category: options
Risk: trading-logic
Effort: L
Target Release: backlog
Due Date: TBD
Dependencies: TRD-043, TRD-046, TRD-047, TRD-049, TRD-051, TRD-052
Blocked By: TRD-052
Links: `utils/option_risk.py`, `utils/option_entry_guardrail.py`, `utils/option_scenario.py`, `utils/option_candidates.py`, `dashboard/api/main.py`, `dashboard/frontend/src/pages/TickerPage.tsx`
Success Metric: the system exposes one explicit post-entry action for a live long option position, so the user can follow a clear hold / take-profit / sell-now rule without scanning many lower-level metrics, with graceful degradation when live options market-data subscriptions are unavailable.

## Problem Statement

The current system is getting better at the pre-entry decision:

- candidate quality
- PM/risk gating
- entry guardrails
- projected exits
- path scenarios

But once a position exists, the user still has to infer the real next action
from multiple fields.

This ticket is explicitly post-entry only. Pre-entry buy logic should live in a
separate ticket so buy decisions can be implemented before IBKR-backed position
memory exists.

What users actually need is one explicit post-entry instruction such as:

- hold
- take profit
- sell now

Without that layer, the product still requires too much manual synthesis at the
moment of live trade management.

## User Impact

Without a unified position-action engine:

- users may hold too long while theta decay accelerates
- users may fail to realize gains at thesis milestones
- stop and time-exit discipline remains inconsistent
- dashboard users still need to inspect many metrics to manage one position

## Objective

Add a deterministic post-entry rule engine for long single-leg options that
outputs one top-level action for a saved position:

- `hold`
- `take_profit`
- `sell_now`

and optionally `watch_closely` if needed as a bounded intermediate state.

The engine must support two decision tiers:

- a base tier that works from saved position memory, thesis context, and broker
  portfolio data without assuming live options market-data subscriptions
- an enhanced tier that uses live option quotes, spreads, freshness, and Greeks
  when available

## Proposed Solution

Build a position-aware rule engine that uses saved position memory plus current
quote and thesis context.

At minimum, evaluate:

- actual saved entry price or average cost
- current option mark / bid / ask / mid
- current underlying price
- thesis `T1`, `T2`, and `SL`
- days held
- DTE remaining
- scenario/path context
- PM/risk and quote-quality overrides where still relevant

The rule engine should explicitly separate:

### Base mode: no live quote assumption

Must still work using:

- saved entry / average cost
- current underlying price
- thesis `T1`, `T2`, and `SL`
- days held
- DTE remaining
- saved thesis and position metadata

This mode should be able to return a valid `hold` / `take_profit` / `sell_now`
output even when no live option bid/ask/mid is available.

### Enhanced mode: live quote-aware

When live option market data is available, improve the decision using:

- live bid / ask / mid
- spread quality
- quote freshness
- live mark-vs-entry P&L
- Greeks when available

This mode should refine the action or reason, but not be mandatory for the
engine to function.

Recommended v1 rule families:

### 1. Hard sell rules

Trigger `sell_now` when any of the following occur:

- underlying hits thesis `SL`
- option premium falls below a configured premium stop from actual entry
- DTE remaining falls below a configured exit threshold
- thesis status becomes broken

### 2. Take-profit rules

Trigger `take_profit` when any of the following occur:

- underlying hits `T1`
- underlying hits `T2`
- actual option return from saved entry reaches configured profit thresholds
- upside remaining is no longer attractive relative to time/theta risk

### 3. Hold rules

Trigger `hold` only when:

- no hard sell condition is hit
- no take-profit condition is hit
- thesis remains intact
- enough DTE remains

Output fields should include at least:

- `position_action`
  - `hold`
  - `take_profit`
  - `sell_now`
- `position_action_reason`
- `next_trigger`
- optional `thesis_status`
  - `intact`
  - `weakening`
  - `broken`

## Scope

- deterministic position-action engine
- API serialization and typed response updates
- ticker-page rendering for saved positions
- focused tests for rule ordering and threshold behavior

## Non-Goals

- Do not define or change the pre-entry buy rule in this ticket.
- Do not add broker-side auto-liquidation or order routing.
- Do not support every multi-leg or advanced options strategy in v1.
- Do not rely on LLM narrative to determine action state.
- Do not replace the existing candidate engine; this is post-entry logic.

## Constraints

- Keep rule order deterministic and inspectable.
- Use actual saved entry / average cost when available, not candidate entry.
- Separate hard-stop logic from softer take-profit/hold guidance.
- Preserve lower-level fields for debugging and auditability.
- Do not assume live options market-data subscriptions exist.

## Acceptance Criteria

- Observable behavior: a saved long option position exposes one top-level action:
  `hold`, `take_profit`, or `sell_now`.
- Observable behavior: hard stop and expiry-related rules take precedence over
  softer hold logic.
- Observable behavior: take-profit decisions use actual saved entry context, not
  only candidate-time estimates.
- Observable behavior: base mode works without live option quote subscriptions.
- Observable behavior: enhanced mode uses live quote fields only when available.
- Observable behavior: the UI can show one concise reason and next trigger.
- Tests:
  - stop-loss breach triggers `sell_now`
  - time/DTE rule triggers `sell_now` when appropriate
  - T1/T2 or profit-threshold hit triggers `take_profit`
  - intact thesis with adequate time remaining returns `hold`
  - missing optional data degrades safely

## Verification Plan

- focused unit tests for rule ordering and thresholds
- endpoint or serialization tests for new action fields
- frontend tests for saved-position rendering
- smoke tests for base mode and enhanced mode behavior
- `make verify` if practical

## QA Notes

- Test scenarios:
  - profitable call at T1
  - profitable call at T2
  - flat position with accelerating theta and low DTE
  - losing put where underlying hits stop
  - broker-linked position with average cost from IBKR
  - broker-linked position without live option quote subscriptions
  - broker-linked position with live option quote subscriptions
- Edge cases:
  - stale quote
  - partial position after scale-out
  - manual fallback position without broker linkage
  - expired option with lingering saved state
- Regression risks:
  - conflicting signals between old candidate fields and live position action
  - overcomplicated rule order
  - hidden assumptions about exact fill quality

## Launch / Release Notes

- User-facing change summary: saved option positions now show one clear next
  action: hold, take profit, or sell now.
- Operational notes: this is deterministic guidance only and does not send
  orders. Base mode must work from saved position and thesis data alone; enhanced
  quote-aware mode activates only when live market data exists.
- Rollback notes: disable the position-action layer and continue showing only
  lower-level fields.

## Post-Launch Validation

- What to monitor:
- distribution of `hold`, `take_profit`, and `sell_now`
- action reason mix
- time-stop frequency
- base-mode vs enhanced-mode usage rates
- false-positive exit complaints from users
- How success will be confirmed:
  - users can manage live options positions without scanning many separate
    metrics
- Follow-up decision date: after first live review of action recommendations on
  saved positions.

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-053, "Unified Options Position Action Engine," in this repo.

Goal:
- Add a deterministic post-entry action engine for saved long option positions
  that outputs one explicit action: `hold`, `take_profit`, or `sell_now`.

Requirements:
- Use saved position memory from TRD-052
- Use actual saved entry or average cost when available
- Combine thesis stop/targets, DTE/time risk, and premium-based thresholds
- Support a base mode that still returns valid actions without live option quote
  subscriptions
- Add an enhanced mode that uses live option quote quality and mark-based logic
  when data is available
- Return one concise reason and one next trigger
- Keep the logic deterministic and easy to audit
- Do not implement pre-entry buy gating here; that belongs in the dedicated
  buy-rule ticket.

Scope:
- position-action engine logic
- backend serialization
- frontend rendering for saved positions
- focused tests

Tests and verification:
- Add focused truth-table / rule-order tests
- Add frontend rendering coverage
- Run the targeted tests you add
