# Task: Harden Nasdaq-100 Source Freshness and Authority

Status: done
Stage: done
Type: tech-debt
Priority: P3
Severity: low
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: data-quality
Risk: low
Effort: S
Target Release: backlog
Due Date: TBD
Dependencies: TRD-062
Blocked By: none
Links: `universe_builder.py`, `docs/INTERNALS.md`, `https://www.nasdaq.com/solutions/global-indexes/nasdaq-100`
Success Metric: `nasdaq100` is either backed by a more trustworthy live membership source or explicitly governed as a maintained snapshot with a clear refresh/verification process.

## Problem Statement

The current `nasdaq100` source is operationally a curated snapshot, not a trustworthy live constituent feed. That is acceptable as a temporary approximation, but it introduces source-quality risk:

- membership can become stale
- entries can be incomplete or outdated
- accuracy depends on manual refresh discipline

This is not a filtering bug like `nasdaq_broad`; it is a source-authority and freshness issue.

## User Impact

- The execution overlay may drift from the real Nasdaq-100 membership over time.
- PM review can overestimate the authority of the current source.
- Source attribution and downstream analytics may treat an approximate maintained list like a live benchmark feed.

## Objective

Make the `nasdaq100` source either more authoritative or more explicitly governed as a maintained approximation.

## Proposed Solution

- Investigate whether a more reliable live or periodically downloadable Nasdaq-100 constituent source is available within repo constraints
- If a reliable free live source is not available, formalize the current curated snapshot approach:
  - explicit refresh cadence
  - explicit verification procedure
  - explicit documentation that it is a maintained approximation
- Ensure logs/docs/operator expectations match the real source quality

## Scope

Files or modules likely affected:

- `universe_builder.py`
- `docs/INTERNALS.md`
- `tests/test_universe_builder.py`

## Non-Goals

- Do not redesign lane routing here.
- Do not change AI selection behavior.
- Do not reopen `nasdaq_broad` intake work here.

## Constraints

- No paid vendor dependency unless explicitly approved later.
- Preserve existing fallback behavior unless a clearly better source is implemented.

## Acceptance Criteria

- Observable behavior:
  - `nasdaq100` source quality is explicitly classified as either authoritative or maintained snapshot
  - if still snapshot-based, refresh expectations are documented
- Tests:
  - add or update targeted source-behavior tests if implementation changes
- Documentation:
  - `docs/INTERNALS.md` states the true source quality clearly

## Verification Plan

- `pytest -q tests/test_universe_builder.py`

## QA Notes

- Test scenarios: cache hit, stale cache, curated snapshot fallback, refresh metadata if added
- Edge cases: stale constituents, missing new additions, outdated removals
- Regression risks: false confidence in benchmark authority

## Launch / Release Notes

- User-facing change summary: Nasdaq-100 source handling is clarified or improved for accuracy and maintenance.
- Operational notes: if snapshot-based, track refresh date and verification cadence.
- Rollback notes: revert to current curated-snapshot behavior.

## Post-Launch Validation

- What to monitor: membership drift vs expected Nasdaq-100 names, refresh age, operator confusion in logs/docs
- How success will be confirmed: the source is either more accurate or more honestly governed
- Follow-up decision date: after next universe-source review

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-070: harden the Nasdaq-100 source quality model.

Goal:
- either improve the authority/freshness of the nasdaq100 source
- or formalize the current curated snapshot approach with clear refresh and verification rules

Scope:
- universe_builder.py
- docs/INTERNALS.md
- tests/test_universe_builder.py

Required changes:
- assess whether a more trustworthy live/free Nasdaq-100 constituent source is available within repo constraints
- if not, keep the curated snapshot but document and govern it explicitly as a maintained approximation
- keep logs/docs aligned with the real source quality

Non-goals:
- no lane redesign
- no nasdaq_broad redesign here
- no AI gate changes

Tests:
- pytest -q tests/test_universe_builder.py
```
