# Task: Prohibit IBKR Snapshot Usage and Use yfinance Underlying Fallback

Status: proposed
Stage: ready
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: execution
Category: options
Risk: cost-control
Effort: M
Target Release: backlog
Due Date: TBD
Dependencies: TRD-021, TRD-022, TRD-054
Blocked By: none
Links: `utils/ibkr_options.py`, `dashboard/api/main.py`, `docs/tasks/new/TRD-042-ibkr-subscription-readiness-and-access.md`
Success Metric: the repo no longer makes IBKR snapshot market-data requests for the underlying stock price, relies on non-snapshot sources such as yfinance for that fallback value instead, and is audited so no other hidden IBKR snapshot usage remains that could create avoidable per-request costs.

## Problem Statement

The current IBKR options adapter makes a snapshot market-data request for the
underlying stock price before fetching option-chain data.

That request path can create incremental per-snapshot costs, even though the
current system does not materially depend on IBKR stock snapshots for the
pre-entry buy rule or for basic option candidate generation.

The actual option-contract data path already relies on non-snapshot option
market-data requests, which are the more important source of option quote and
Greek quality.

As a result, the repo currently pays for a small but unnecessary extra data path:

- IBKR stock snapshot for underlying price

when a cheaper fallback is acceptable:

- yfinance underlying price

## User Impact

Without removing IBKR snapshot usage:

- users may incur avoidable per-request snapshot costs
- cost behavior remains harder to reason about
- the repo may silently retain small paid data paths that do not materially
  improve the current product stage

## Objective

Remove or prohibit IBKR snapshot usage for underlying stock-price lookup in the
options path, and replace it with a non-IBKR-snapshot fallback such as yfinance.

Also audit the repo so any current or future IBKR snapshot usage is explicit and
not accidental.

## Current Findings

Current repo scan found one actual IBKR snapshot call:

- `utils/ibkr_options.py`
  - `self._ib.reqMktData(stock, "", snapshot=True)`

Current repo scan did **not** find other active IBKR snapshot calls outside this
path.

The option-contract requests in the same adapter are normal market-data requests,
not snapshot requests:

- `reqMktData(opt, "100,101,106", False, False)`

This ticket should preserve the live option-contract data path while removing
the paid underlying snapshot path.

## Proposed Solution

Change the IBKR options adapter so it no longer requests the underlying stock
price via IBKR snapshot.

Recommended approach:

1. Remove or bypass the IBKR stock snapshot request in `utils/ibkr_options.py`.
2. Use a non-snapshot fallback for the underlying price, with this priority:
   - yfinance latest price
   - cached/recent underlying price if already available in the flow
   - `None` with graceful degradation if neither is available
3. Preserve the existing IBKR option-contract market-data path.
4. Add a small guardrail or documented rule to prevent future `snapshot=True`
   usage from being introduced casually.

## Scope

- `utils/ibkr_options.py`
- any small helper extraction needed for underlying-price fallback
- focused tests for no-snapshot behavior and graceful degradation
- repo audit note or test coverage preventing hidden snapshot reintroduction

## Non-Goals

- Do not remove the IBKR live option-contract market-data path.
- Do not force the entire repo onto yfinance-only options data.
- Do not redesign the subscription strategy for all future broker features.
- Do not change post-entry IBKR portfolio sync work in TRD-052/TRD-053.

## Constraints

- Preserve the existing normalized adapter contract.
- Keep the option-chain IBKR path intact except for the underlying snapshot removal.
- Degrade safely when the yfinance underlying price is unavailable.
- Avoid introducing extra hidden network calls that are more fragile than the current flow.

## Acceptance Criteria

- Observable behavior: the options adapter no longer issues IBKR snapshot
  requests for the underlying stock price.
- Observable behavior: underlying price is sourced from yfinance or another
  non-snapshot fallback instead.
- Observable behavior: the IBKR option-contract requests remain intact.
- Observable behavior: no other active IBKR snapshot usage remains in the repo
  after the implementation audit.
- Tests:
  - adapter path works without `snapshot=True`
  - missing fallback underlying price degrades safely
  - option-chain fetch still returns usable contracts
  - targeted coverage or assertion guards against accidental reintroduction of
    IBKR snapshot usage in this path

## Verification Plan

- repo-wide search for `snapshot=True` and related IBKR snapshot patterns
- focused adapter tests for underlying-price fallback behavior
- smoke test on one or more option-chain fetches
- `make verify` if practical

## QA Notes

- Test scenarios:
  - IBKR connected, yfinance underlying price available
  - IBKR connected, yfinance underlying price unavailable
  - IBKR unavailable, existing fallback path still works
  - option-chain fetch with valid live option quotes and no underlying snapshot
- Edge cases:
  - yfinance underlying price stale or empty
  - ticker with thin options chain
  - no current underlying price but chain still partially usable
- Regression risks:
  - worse strike-centering when underlying fallback is stale
  - accidental broadening into full yfinance-only option sourcing
  - hidden future reintroduction of `snapshot=True`

## Launch / Release Notes

- User-facing change summary: the repo no longer uses IBKR stock snapshot
  requests for option-chain underlying-price lookup, reducing avoidable
  snapshot-fee exposure.
- Operational notes: live IBKR option-contract data remains unchanged; only the
  underlying stock-price snapshot path is removed.
- Rollback notes: restore the prior underlying snapshot path if fallback quality
  proves materially worse.

## Post-Launch Validation

- What to monitor:
  - repo search remains clean for IBKR snapshot usage
  - option candidate quality vs prior behavior
  - null-rate for underlying price in the adapter output
  - any user-reported degradation in strike centering or projections
- How success will be confirmed:
  - avoidable IBKR snapshot usage is removed without materially hurting current
    pre-entry option workflows
- Follow-up decision date: after live use of option candidates without IBKR stock
  snapshots.

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-055, "Prohibit IBKR Snapshot Usage and Use yfinance Underlying
Fallback," in this repo.

Goal:
- Remove IBKR snapshot usage for underlying stock-price lookup in the options
  adapter so the repo avoids avoidable per-snapshot costs.

Current finding:
- The active snapshot call is in `utils/ibkr_options.py`:
  `self._ib.reqMktData(stock, "", snapshot=True)`
- The option-contract data path should remain intact.

Requirements:
- Remove or bypass the IBKR underlying snapshot call
- Replace it with a non-snapshot fallback, preferably yfinance latest price
- Preserve the live IBKR option-contract market-data path
- Audit the repo for any other active IBKR snapshot usage and eliminate it if found
- Add focused tests for no-snapshot behavior and graceful degradation

Scope:
- `utils/ibkr_options.py`
- small helper logic if needed
- focused tests

Tests and verification:
- Search the repo for `snapshot=True` and related patterns before and after
- Add focused adapter tests
- Run the targeted tests you add
