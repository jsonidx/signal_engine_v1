# Task: Thesis Refresh Triggers For Breakouts And Catalysts

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: alerts
Category: automation
Risk: trading-logic
Effort: M
Target Release: completed
Due Date: completed
Dependencies: none
Blocked By: none
Links: none
Success Metric: stale theses can be selectively refreshed when setup conditions materially change.

## Problem Statement

Stored AI theses could become outdated when price, ranking, or catalyst conditions changed materially after the original analysis.

## User Impact

Users risked acting on stale framing even when the market setup had already shifted beyond the original thesis context.

## Objective

Refresh stale AI theses when market conditions invalidate the old entry frame, especially when price breaks above the stored entry zone, rankings jump, or an earnings catalyst approaches.

## Proposed Solution

Introduce bounded refresh triggers based on concrete market events so only materially changed setups are re-analyzed.

## Scope

- `ai_quant.py`
- `refresh_stale_theses.py`
- `scripts/refresh_stale_and_notify.py`
- `utils/ticker_selector.py`
- `dashboard/api/main.py`
- `tests/test_ai_quant_schema.py`
- `tests/test_ticker_selector.py`

## Non-Goals

- Do not delete or overwrite thesis history.
- Do not refresh all tickers every run.
- Do not bypass cost controls without an explicit cap.

## Constraints

- Add bounded refresh rules with clear reasons persisted or logged.
- Avoid repeated refresh loops for the same ticker on the same day.
- Preserve existing cache behavior unless a refresh rule explicitly fires.

## Acceptance Criteria

- Observable behavior: a ticker with current price more than a configurable percentage above `entry_high` can be queued for thesis refresh.
- Observable behavior: a top-5 `daily_rankings` ticker with stale or neutral thesis can be queued for refresh.
- Observable behavior: a ticker with earnings within a configurable window can be queued for refresh if setup evidence changes materially.
- The refresh reason is visible in logs or persisted metadata.
- Tests: cover price breakout, ranking jump, near-catalyst refresh, and no-op cases.
- Documentation: include default thresholds and cost-control behavior.

## Verification Plan

- `pytest tests/test_ai_quant_schema.py tests/test_ticker_selector.py -v`
- Run dry-run refresh against `SNOW` and verify it is selected for a reason like `price_above_entry_zone` or `near_earnings_catalyst`.
- `make verify`

## Handoff Notes

`SNOW` had a May 15 thesis with an entry zone of `$147-$153`, then traded into the `$160s-$170s` before the May 28 earnings gap. The stale entry zone prevented the UI from reframing it as a continuation/catalyst setup.
