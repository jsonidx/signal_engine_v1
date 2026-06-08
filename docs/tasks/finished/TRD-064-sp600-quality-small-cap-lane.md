# Task: Add S&P SmallCap 600 as Quality Small-Cap Swing Lane

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
Effort: M
Target Release: universe-v2
Due Date: TBD
Dependencies: TRD-056, TRD-060
Blocked By: none
Links: `config.py`, `universe_builder.py`, `docs/INTERNALS.md`, `https://www.spglobal.com/spdji/en/indices/equity/sp-600/`
Success Metric: the system gains a cleaner small-cap swing-trading pool than a raw broad small-cap source, improving small-cap opportunity capture without adding excessive junk.

## Problem Statement

Small caps matter for swing trading, but broad small-cap pools can become noisy quickly. S&P describes the S&P SmallCap 600 as including companies that meet liquidity and financial-viability criteria, making it a better small-cap swing source than a looser raw breadth expansion.

## User Impact

- The engine may miss high-quality small-cap swing names.
- Existing small-cap exposure may be too dependent on rougher benchmark or custom pools.

## Objective

Add `S&P SmallCap 600` as a first-class source for a cleaner small-cap swing lane.

## Proposed Solution

- Add an `sp600` source path to `universe_builder.py`
- Use it as a preferred quality small-cap lane
- Preserve downstream quality gates and deduplication
- Document the distinction between:
  - quality small-cap lane: `S&P 600`
  - more speculative small-cap pools: separate and stricter

## Scope

Files or modules likely affected:

- `config.py`
- `universe_builder.py`
- `docs/INTERNALS.md`
- `tests/test_universe_builder.py`

## Non-Goals

- Do not add a raw microcap lane here.
- Do not weaken liquidity filters.
- Do not change AI issue logic.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.

## Acceptance Criteria

- Observable behavior:
  - `S&P 600` is available as a supported source.
  - It can be enabled as a quality small-cap lane.
  - Deduplication and quality filtering remain intact.
- Tests:
  - Add or update `tests/test_universe_builder.py`.
- Documentation:
  - `docs/INTERNALS.md` documents `S&P 600` as the preferred quality small-cap lane.

## Verification Plan

- `pytest -q tests/test_universe_builder.py`

## QA Notes

- Test scenarios: source alone, source with `Russell 2000`, overlap handling, source failure fallback.
- Edge cases: names overlapping with broader core sources, liquidity-filter fallout.
- Regression risks: duplicate inflation and unwanted speculative drift.

## Launch / Release Notes

- User-facing change summary: the engine can now use `S&P 600` as a cleaner small-cap swing source.
- Operational notes: compare qualified small-cap names before and after enablement.
- Rollback notes: disable the source in config.

## Post-Launch Validation

- What to monitor: small-cap qualified counts, overlap with existing pools, win-rate by source after enough history accrues.
- How success will be confirmed: small-cap coverage improves without obvious junk inflation.
- Follow-up decision date: after two to four weeks.

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-064: add S&P SmallCap 600 as a first-class quality small-cap source.

Scope:
- config.py
- universe_builder.py
- docs/INTERNALS.md
- tests/test_universe_builder.py

Required changes:
- Add support for an sp600 source.
- Preserve deduplication and existing quality filters.
- Document S&P 600 as the preferred quality small-cap swing lane.

Non-goals:
- No microcap lane
- No AI gate changes
- No liquidity-threshold weakening

Tests:
- pytest -q tests/test_universe_builder.py
```
