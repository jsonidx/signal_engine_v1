# Task: Pre-Earnings Breakout Detector

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: trading-logic
Category: research
Risk: trading-logic
Effort: M
Target Release: completed
Due Date: completed
Dependencies: none
Blocked By: none
Links: none
Success Metric: high-quality pre-earnings setups can be detected without requiring squeeze conditions.

## Problem Statement

The engine needed a distinct way to surface strong pre-earnings setups that did not fit the short-squeeze framework.

## User Impact

Without a dedicated detector, users could miss catalyst-driven setups such as `SNOW` even when supporting evidence was present.

## Objective

Add a dedicated detector for high-quality pre-earnings re-rating setups, so names like `SNOW` can be flagged before a catalyst move even when they are not short-squeeze candidates and are not currently inside a Hot Entry zone.

## Proposed Solution

Add explicit pre-earnings breakout logic using structured earnings, price, and options evidence instead of relying on squeeze or generic entry-zone logic.

## Scope

- `ai_quant.py`
- `catalyst_screener.py`
- `conflict_resolver.py`
- `utils/prob_engine.py`
- `tests/test_new_factors.py`
- `tests/test_conflict_resolver.py`

## Non-Goals

- Do not weaken the existing short-squeeze thresholds.
- Do not make every upcoming earnings name bullish.
- Do not change portfolio sizing or execution logic.

## Constraints

- The detector must be explicit and inspectable, not hidden inside an LLM prompt only.
- Treat earnings as bullish only when multiple independent conditions align.
- Preserve the existing pre-earnings risk warning in AI theses.

## Acceptance Criteria

- Observable behavior: a ticker can receive a `pre_earnings_breakout` or equivalent catalyst flag when earnings are near and supporting evidence is present.
- Required inputs: days to earnings, historical beat/surprise quality, relative strength improvement, volume/price confirmation, and options/call-demand evidence where available.
- The detector should not require high short interest.
- `SNOW`-like setup around May 2026 is documented as the motivating fixture/case in a test or handoff note.
- Tests: add unit coverage for bullish, neutral, and false-positive earnings cases.
- Documentation: update internal notes or comments explaining how this differs from squeeze and Hot Entry.

## Verification Plan

- `pytest tests/test_new_factors.py tests/test_conflict_resolver.py -v`
- Run a manual signal collection for `SNOW` and confirm the new flag can appear without forcing direction to `BULL` by itself.
- `make verify`

## Handoff Notes

The `SNOW` miss was not a squeeze miss. It had serial earnings beats, improving relative strength, options hints, and an upcoming earnings event, but the system mostly treated earnings as risk. This task should create a separate re-rating/catalyst setup signal.
