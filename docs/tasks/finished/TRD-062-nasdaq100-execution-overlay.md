# Task: Add Nasdaq-100 as High-Liquidity Execution Overlay

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: research
Risk: trading-logic
Effort: S
Target Release: universe-v2
Due Date: TBD
Dependencies: TRD-056, TRD-060
Blocked By: none
Links: `config.py`, `universe_builder.py`, `docs/INTERNALS.md`, `https://www.nasdaq.com/solutions/global-indexes/nasdaq-100`, `https://www.nasdaq.com/docs/nasdaq-100-index-product-guide`
Success Metric: the system can explicitly include Nasdaq-100 constituents as a liquid growth/momentum overlay in the execution funnel.

## Problem Statement

The current setup under-represents one of the cleanest pools of liquid, institutionally traded swing names: the Nasdaq-100. These names are often central to growth, AI, semis, software, and high-beta momentum trading.

## User Impact

- The engine may under-sample some of the best liquid swing names.
- PM review cannot explicitly reason about a Nasdaq-100 overlay as a separate source cohort.

## Objective

Add `Nasdaq-100` as a first-class universe source and track it as an execution overlay.

## Proposed Solution

- Add an `nasdaq100` source path to `universe_builder.py`
- Allow it to be enabled separately from the broader universe backbone
- Preserve deduplication and quality filters
- Expose source-tagging where practical so analytics can distinguish NDX names

## Scope

Files or modules likely affected:

- `config.py`
- `universe_builder.py`
- `docs/INTERNALS.md`
- `tests/test_universe_builder.py`

## Non-Goals

- Do not add the full Nasdaq broad universe here.
- Do not change AI selection thresholds.
- Do not change watchlist tier scoring in this ticket.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.

## Acceptance Criteria

- Observable behavior:
  - `Nasdaq-100` is supported as a universe source.
  - The overlay can be enabled without duplicating downstream names.
  - Source labeling is available where practical for later analytics.
- Tests:
  - Add or update `tests/test_universe_builder.py`.
- Documentation:
  - `docs/INTERNALS.md` documents the role of `Nasdaq-100` as an execution overlay.

## Verification Plan

- `pytest -q tests/test_universe_builder.py`

## QA Notes

- Test scenarios: source alone, source with `S&P 1500`, heavy overlap handling.
- Edge cases: symbol-share-class overlap, fetch failure.
- Regression risks: duplication and source-precedence confusion.

## Launch / Release Notes

- User-facing change summary: Nasdaq-100 names can now be tracked explicitly as a high-liquidity overlay.
- Operational notes: compare selection rates of NDX names before and after enablement.
- Rollback notes: disable the source in config.

## Post-Launch Validation

- What to monitor: NDX overlap, qualified counts, selected-name distribution.
- How success will be confirmed: NDX becomes a clean explicit source cohort.
- Follow-up decision date: after one to two weeks.

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-062: add Nasdaq-100 as a first-class execution overlay source.

Scope:
- config.py
- universe_builder.py
- docs/INTERNALS.md
- tests/test_universe_builder.py

Required changes:
- Add support for a nasdaq100 source.
- Preserve deduplication and existing quality filters.
- Expose source labeling where practical for later analytics.
- Document Nasdaq-100 as a liquid growth/momentum overlay.

Non-goals:
- No full Nasdaq broad-source addition in this ticket
- No AI threshold changes

Tests:
- pytest -q tests/test_universe_builder.py
```
