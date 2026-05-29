# Task: Accumulation And Options Setup Alert

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: alerts
Category: growth
Risk: trading-logic
Effort: M
Target Release: completed
Due Date: completed
Dependencies: none
Blocked By: none
Links: none
Success Metric: watchlist-worthy accumulation and options setups can be surfaced independently from Hot Entry and squeeze alerts.

## Problem Statement

The product needed a way to surface non-entry watch setups with strong accumulation or options evidence before a clearer trade trigger existed.

## User Impact

Without a separate watch setup alert, users either missed these names entirely or saw them only through categories with the wrong semantics.

## Objective

Add a non-entry alert class for accumulation/catalyst setups that are worth watching before a move, distinct from Hot Entry and short squeeze.

## Proposed Solution

Create a separate watch-style alert using structured dark-pool, options, and catalyst evidence with distinct API and UI labeling.

## Scope

- `dark_pool_flow.py`
- `options_flow.py`
- `catalyst_screener.py`
- `dashboard/api/main.py`
- `dashboard/frontend/src/pages/HomePage.tsx`
- `dashboard/frontend/src/pages/DeepDivePage.tsx`
- `dashboard/frontend/src/lib/api.ts`
- `tests/test_dark_pool_flow.py`
- `tests/test_options_iv_integration.py`
- `dashboard/api/tests/test_endpoints.py`

## Non-Goals

- Do not rename or change the semantics of Hot Entry.
- Do not make dark-pool `ACCUMULATION` the only path into the alert.
- Do not add trade execution behavior.

## Constraints

- The alert must explain why it fired using component evidence.
- Keep the UI label separate from `HOT` and `IN_ZONE`; suggested label: `Catalyst Setup` or `Watch Setup`.
- Avoid LLM-only classifications; the API response should include structured reasons.

## Acceptance Criteria

- Observable behavior: dashboard/API can return tickers with watchlist-worthy setup evidence even when price is not inside the AI entry zone.
- Structured reasons can include dark-pool accumulation, unusual call activity, call/put volume ratio, volume expansion, relative-strength improvement, and upcoming catalyst.
- `SNOW`-like cases can show as a watch setup before earnings without being called a squeeze.
- Tests: endpoint test for the new alert payload and module tests for at least two alert paths.
- Documentation: frontend copy must make clear this is a watch/setup alert, not an immediate buy signal.

## Verification Plan

- `pytest tests/test_dark_pool_flow.py tests/test_options_iv_integration.py dashboard/api/tests/test_endpoints.py -v`
- Start the dashboard API locally and verify the new setup appears in the response shape.
- `make verify`

## Handoff Notes

The existing dark-pool model only flags one-day low short-ratio z-score below `-1.5`. That caught `SNOW` on Apr 13 but not near the May earnings move. This task should combine broader accumulation and options evidence into a watch alert rather than force it into Hot Entry.
