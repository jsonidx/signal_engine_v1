# Task: Lane-Based Adaptive Filter Redesign for Existing and New Listings

Status: completed
Stage: done
Type: feature
Priority: P0
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: research
Risk: trading-logic
Effort: L
Target Release: universe-v2
Due Date: TBD
Dependencies: TRD-056, TRD-057, TRD-060
Blocked By: none
Links: `config.py`, `universe_builder.py`, `ai_quant.py`, `utils/ticker_selector.py`, `docs/INTERNALS.md`
Success Metric: the universe no longer relies on one blunt global filter set; instead, listings are filtered by lane-appropriate tradability standards that improve both research coverage and execution quality.

## Problem Statement

The current filter model adapts to existing listings only in a limited daily-refresh sense. It re-runs liquidity and volatility checks when the universe is rebuilt, but the logic is still a single blunt filter profile for very different kinds of names.

Current issues:

- liquidity floor is too low for a serious U.S. swing-trading execution universe
- listing seasoning is too permissive for production-quality coverage
- volatility and beta are treated too bluntly as exclusion signals
- high-beta names that may be excellent swing trades are handled the same way as junk
- the same filter model is implicitly applied to both research coverage and execution-quality names

This is not state of the art for a modern hedge-fund-style swing universe.

## User Impact

- Good high-beta names can be wrongly excluded.
- Weak listings can still enter the universe too easily.
- Research coverage and execution quality are fighting each other inside one filter model.
- PM review cannot tell whether a name failed because it was untradable, too new, too noisy for execution, or simply in the wrong lane.

## Objective

Replace the single global filter profile with a lane-based adaptive tradability model for both existing and newly discovered listings.

## Proposed Solution

Introduce lane-specific filters with explicit tradability intent.

Recommended PM lane design:

1. `execution_core`
   - intended for core live AI issuance
   - stricter liquidity and seasoning
   - suitable for `S&P 1500`-style names

2. `execution_high_beta`
   - intended for liquid, volatile growth / momentum names
   - allows higher ATR / beta than core
   - suitable for `Nasdaq-100` and selected high-beta names

3. `research_broad`
   - intended for wider candidate capture and learning
   - looser than execution lanes, but still instrument-hygienic

4. `special_situations`
   - optional lane for event-driven or unusual names
   - strict explicit tagging, not default inclusion

Recommended initial thresholds:

- `execution_core`
  - price `>= $5`
  - 30d avg dollar volume `>= $10M`
  - history `>= 252` bars
- `execution_high_beta`
  - price `>= $3`
  - 30d avg dollar volume `>= $10M`
  - history `>= 126` bars
  - wider ATR / beta allowance than core
- `research_broad`
  - price `>= $2`
  - 30d avg dollar volume `>= $5M`
  - history `>= 126` bars

The main PM principle:

- `beta` and `ATR%` should not be universal kill switches
- they should help route names into the appropriate lane
- only obviously untradable names should be fully excluded

## Scope

Files or modules likely affected:

- `config.py`
- `universe_builder.py`
- `ai_quant.py`
- `utils/ticker_selector.py`
- `docs/INTERNALS.md`
- `tests/test_universe_builder.py`
- `tests/test_ticker_selector.py`

## Non-Goals

- Do not redesign thesis prompts.
- Do not change target or stop geometry.
- Do not add paid market-data dependencies in this ticket.
- Do not force all lanes to feed the LLM.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.
- Preserve deterministic screening before AI.
- Preserve safe fallback behavior when richer metadata is unavailable.

## Acceptance Criteria

- Observable behavior:
  - Listings are filtered according to an explicit lane model rather than one global profile.
  - Existing listings can move between lanes or be excluded based on current tradability characteristics.
  - High-beta liquid names are no longer treated the same as junk low-quality names.
  - Config exposes lane-specific thresholds for price, liquidity, seasoning, and volatility/beta treatment.
- Tests:
  - Add targeted tests for lane assignment and lane-specific exclusion behavior.
  - Add regression tests showing execution-core remains stricter than research-broad.
- Documentation:
  - `docs/INTERNALS.md` documents lane definitions and filter semantics.

## Verification Plan

- Targeted tests:
  - `pytest -q tests/test_universe_builder.py tests/test_ticker_selector.py`
- Additional verification:
  - run relevant universe-builder flows locally if feasible
  - inspect summary output for lane counts and filter fallout by lane

## QA Notes

- Test scenarios:
  - liquid mature large-cap
  - liquid high-beta Nasdaq growth name
  - new listing with short history
  - thin low-dollar-volume small cap
  - special-situation/event-driven name
- Edge cases:
  - protected or force-included tickers
  - missing beta or ATR metadata
  - names moving from execution lane to research lane after liquidity deterioration
- Regression risks:
  - lane confusion
  - accidental AI-lane expansion
  - over-complex configuration with unclear defaults

## Launch / Release Notes

- User-facing change summary: filtering is now lane-aware and better aligned with tradability.
- Operational notes: compare lane counts, AI throughput, and rejected-name quality before and after rollout.
- Rollback notes: revert to the prior single-profile filter defaults.

## Post-Launch Validation

- What to monitor:
  - lane population counts
  - rejection reasons by lane
  - AI-lane call count
  - share of high-beta liquid names retained
  - quality of research-lane expansion
- How success will be confirmed:
  - broader learning coverage and cleaner live tradability without obvious junk inflation
- Follow-up decision date:
  - after 2-4 weeks of daily runs

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-065: redesign the current listing filter into a lane-based adaptive tradability model.

Goal:
- Replace the current blunt global filter with lane-specific filtering for execution-core, execution-high-beta, research-broad, and optionally special-situations.
- Improve handling of existing listings as well as newly discovered names.

Scope:
- config.py
- universe_builder.py
- ai_quant.py
- utils/ticker_selector.py
- docs/INTERNALS.md
- tests/test_universe_builder.py
- tests/test_ticker_selector.py

Required changes:
- Add explicit lane-aware filter configuration.
- Support lane-specific thresholds for:
  - minimum price
  - minimum 30d avg dollar volume
  - minimum history / seasoning
  - volatility / beta handling
- Ensure beta and ATR are used for lane routing where appropriate, not only universal hard exclusion.
- Preserve deterministic pre-AI screening.
- Preserve safe fallback behavior if quality metadata is missing.
- Expose enough summary information to inspect lane assignment and lane-based fallout.

Recommended PM defaults:
- execution_core: price >= $5, ADV >= $10M, history >= 252 bars
- execution_high_beta: price >= $3, ADV >= $10M, history >= 126 bars, wider vol/beta tolerance
- research_broad: price >= $2, ADV >= $5M, history >= 126 bars

Non-goals:
- No prompt redesign
- No target/stop changes
- No paid data vendor additions
- Do not route all lanes to the LLM

Constraints:
- Risk is trading-logic
- Preserve current deterministic architecture
- Avoid broad refactors outside the listed files

Tests / verification:
- pytest -q tests/test_universe_builder.py tests/test_ticker_selector.py
- run relevant universe-builder flows if feasible
- verify lane counts and lane-specific fallout summaries
```
