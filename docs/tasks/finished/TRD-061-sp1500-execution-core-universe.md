# Task: Add S&P Composite 1500 as Execution-Core Universe Backbone

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
Links: `config.py`, `universe_builder.py`, `docs/INTERNALS.md`, `https://www.spglobal.com/spdji/en/indices/equity/sp-composite-1500/`
Success Metric: the system can use the S&P Composite 1500 as a broad but still tradable execution-core universe source.

## Problem Statement

The current universe is assembled from multiple benchmark fragments. The S&P Composite 1500 is a cleaner backbone for a tradable U.S. swing universe because it combines the S&P 500, S&P 400, and S&P 600 and is explicitly described by S&P as covering about 90% of U.S. market capitalization in a representative tradable set.

## User Impact

- The execution core lacks a single coherent U.S. market backbone.
- Duplicate source logic is doing work that one better core source could do more cleanly.
- The PM team cannot easily reason about the “main tradable universe.”

## Objective

Add `S&P Composite 1500` as a first-class universe source and allow it to serve as the default execution-core backbone.

## Proposed Solution

- Add an `sp1500` source path to `universe_builder.py`
- Make it configurable in `UNIVERSE_INDICES` or equivalent source-selection config
- Preserve deduplication and downstream quality filters
- Update internal docs to distinguish:
  - execution-core backbone: `S&P 1500`
  - overlays / research expansions: separate sources

## Scope

Files or modules likely affected:

- `config.py`
- `universe_builder.py`
- `docs/INTERNALS.md`
- `tests/test_universe_builder.py`

## Non-Goals

- Do not remove legacy sources unless necessary.
- Do not change AI selection logic.
- Do not broaden research-lane Nasdaq coverage in this ticket.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.
- Keep symbol deduplication stable.

## Acceptance Criteria

- Observable behavior:
  - `S&P 1500` is available as a supported universe source.
  - The source can be enabled as the main execution-core backbone.
  - Deduplication and quality filtering continue to work.
- Tests:
  - Add or update `tests/test_universe_builder.py` for source loading and deduplication.
- Documentation:
  - `docs/INTERNALS.md` documents `S&P 1500` as the main execution-core universe.

## Verification Plan

- `pytest -q tests/test_universe_builder.py`

## QA Notes

- Test scenarios: source enabled alone, source enabled with existing index inputs, duplicate overlap with other sources.
- Edge cases: source fetch failure, fallback behavior, symbol overlap explosions.
- Regression risks: duplicate-count inflation and unexpected source precedence.

## Launch / Release Notes

- User-facing change summary: the core tradable U.S. universe can now be anchored on `S&P 1500`.
- Operational notes: compare pre/post qualified counts and overlap with existing benchmark inputs.
- Rollback notes: disable the source in config.

## Post-Launch Validation

- What to monitor: qualified-name count, overlap ratio, share of selected names sourced via `S&P 1500`.
- How success will be confirmed: the universe backbone becomes simpler and more representative.
- Follow-up decision date: after one to two weeks of runs.

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-061: add S&P Composite 1500 as a first-class execution-core universe source.

Scope:
- config.py
- universe_builder.py
- docs/INTERNALS.md
- tests/test_universe_builder.py

Required changes:
- Add support for an sp1500 source in the universe builder.
- Make it configurable as part of the universe source set.
- Preserve symbol deduplication and downstream quality filters.
- Document S&P 1500 as the default execution-core backbone.

Non-goals:
- No AI gate changes
- No broad Nasdaq research-lane changes
- No removal of existing sources unless required

Tests:
- pytest -q tests/test_universe_builder.py
```
