# Task: Analyst Price-Target Change Detection

Status: proposed
Stage: ready
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: research
Risk: trading-logic
Effort: M
Target Release: backlog
Due Date: TBD
Dependencies: none
Blocked By: none
Links: none
Success Metric: catalyst scoring detects explicit analyst price-target raises and cuts, not just generic upgrade/downgrade labels.

## Problem Statement

The analyst momentum portion of the catalyst screener currently relies on `yfinance.stock.upgrades_downgrades` labels and broad action text. That means a bank can raise or cut a price target and the event may not be handled reliably unless the feed labels it in a specific way.

## User Impact

Users can miss meaningful analyst-driven catalysts or bearish revisions. That weakens the reliability of catalyst scoring and can mis-rank names that are reacting to fresh analyst changes.

## Objective

Extend analyst momentum scoring so explicit price-target raises are counted as bullish catalyst signals and explicit price-target cuts are counted as bearish signals, while preserving the existing upgrade/downgrade clustering behavior.

## Proposed Solution

Add a small normalization or parsing layer around the analyst feed so the screener can identify target raises and target cuts directly when the source data exposes enough structure. Keep the current scoring model intact, avoid double-counting the same event, and add clear flags explaining whether the score was driven by a target raise, target cut, upgrade, or downgrade.

## Scope

- `catalyst_screener.py`
- `tests/test_pipeline_defects.py`
- new or updated focused unit tests for analyst scoring if needed

## Non-Goals

- Do not refactor the full catalyst scoring pipeline.
- Do not change unrelated catalyst modules such as earnings, social, options, or dark pool scoring.
- Do not introduce a new market data provider.
- Do not alter composite score weighting beyond what is needed to incorporate the target-change signal cleanly.

## Constraints

- Keep the change localized to analyst momentum detection and its tests.
- Do not double-count a single analyst row as both a rating change and a target change if the feed already implies the same event.
- Preserve the current output shape for catalyst scoring unless a minimal extension is necessary for new flags.
- Avoid adding brittle parsing logic that depends on a single provider quirk without a fallback path.

## Acceptance Criteria

- Observable behavior: a price-target raise by a bank contributes a positive catalyst signal even when the feed label is ambiguous.
- Observable behavior: a price-target cut contributes a negative analyst signal or a negative flag.
- Observable behavior: existing analyst upgrade clustering still works as before.
- Tests:
  - target raise is detected
  - target cut is detected
  - mixed analyst activity in the same 7-day window is handled without double-counting
  - no false positive when only rating text changes without a target change
- Documentation: the new behavior is reflected in code comments or inline notes where the analyst scoring logic lives.

## Verification Plan

- `pytest tests/test_pipeline_defects.py -v`
- any new focused analyst-scoring unit tests added by the implementation
- `make verify` if the change stays local and the suite cost is acceptable

## QA Notes

- Test scenarios:
  - explicit target raise with neutral rating text
  - explicit target cut with neutral rating text
  - upgrade plus target raise in the same week
  - downgrade plus target cut in the same week
- Edge cases:
  - ambiguous `Action` labels
  - missing or null target fields
  - multiple analyst rows from the same bank in the same window
- Regression risks:
  - accidental double-counting of the same analyst event
  - overfitting to one yfinance schema variant

## Launch / Release Notes

- User-facing change summary: catalyst scoring becomes more reliable for analyst price-target revisions.
- Operational notes: no new external service is required.
- Rollback notes: revert the analyst normalization and scoring extension if provider parsing proves unstable.

## Post-Launch Validation

- What to monitor: analyst-scoring flags on live catalyst scans and whether target-revision names move in rank as expected.
- How success will be confirmed: target raises and cuts show up in catalyst output without inflating duplicate analyst counts.
- Follow-up decision date: after the first verification pass on the updated screener.

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-019, "Analyst Price-Target Change Detection," in this repo.

Goal:
- Extend `catalyst_screener.py` so analyst catalyst scoring detects explicit price-target raises and cuts, not just generic upgrade/downgrade labels.

Scope:
- `catalyst_screener.py`
- `tests/test_pipeline_defects.py`
- add focused unit tests if the existing test file is not enough

Requirements:
- Preserve the current analyst momentum clustering behavior.
- Detect target raises as bullish and target cuts as bearish when the feed exposes enough structure to compare changes directly.
- Avoid double-counting a single analyst row as both a rating event and a target-change event.
- Keep the composite score bounded and the public output shape stable unless a minimal extension is needed for flags.
- Add explicit flags that explain whether the score came from a target raise, target cut, upgrade, or downgrade.

Non-goals:
- Do not refactor unrelated catalyst modules.
- Do not change earnings, social, options, or dark-pool scoring.
- Do not add a new market data provider.
- Do not widen the change into a full screener redesign.

Tests and verification:
- Update or add tests for:
  - target raise
  - target cut
  - mixed analyst activity in a 7-day window
  - no false positive when only rating text changes without a target change
- Run `pytest tests/test_pipeline_defects.py -v`
- Run any new focused analyst tests you add
- Run `make verify` if the change remains local and the suite is practical

Implementation note:
- If `yfinance` does not provide enough structured history for a direct comparison, add a small normalization/parsing layer before changing scoring logic.
- Prefer a minimal, localized fix that keeps the existing scoring model intact.
