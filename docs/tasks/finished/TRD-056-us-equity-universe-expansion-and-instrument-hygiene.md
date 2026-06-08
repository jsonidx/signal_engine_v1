# Task: US Equity Universe Expansion and Instrument Hygiene

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
Dependencies: TRD-055
Blocked By: none
Links: `config.py`, `universe_builder.py`, `docs/INTERNALS.md`
Success Metric: discovery coverage expands from the current index-heavy universe to a broader US listed common-stock universe while maintaining or improving average tradability quality.

## Problem Statement

The current discovery universe is dominated by benchmark constituent lists plus ADR injections. That is useful for large-cap monitoring but too narrow for event-driven, pre-breakout, squeeze, and retail-flow opportunity discovery. Many tradable names appear outside the current index-heavy pool or become relevant before they are represented in the current watchlist path.

At the same time, simply adding more symbols is dangerous. A broader universe without instrument hygiene will flood the system with ETFs, preferreds, warrants, rights, units, shells, and microcaps that degrade research quality and produce false positives.

## User Impact

- Users miss real US opportunities outside the current benchmark-centric universe.
- The engine over-learns from benchmark names and under-learns from off-index winners and losers.
- Expanding naively would create noise, API load, and poor-quality labels.

## Objective

Expand the discovery universe to a broad US listed common-stock pool while enforcing strict instrument-quality and tradability gates.

## Proposed Solution

Update `universe_builder.py` and related config to support a broader US universe with hard exclusions and stronger quality filters.

Recommended PM design:

- Universe sources:
  - broad US listings on `NASDAQ`, `NYSE`, and `NYSE American`
  - retain existing benchmark and ADR paths as secondary inputs, not the sole source
- Explicitly exclude:
  - ETFs
  - ETNs
  - mutual funds / closed-end funds
  - SPAC units, rights, and warrants
  - preferred shares
  - ADR suffix junk already filtered by current dot-suffix logic
  - obvious shells and no-history symbols
- Raise tradability floor for research quality:
  - price `>= $2.00`
  - 30d avg dollar volume `>= $5M` minimum, with config support for `$10M` stricter mode
  - history `>= 252` bars where feasible for production discovery
  - keep volatility and beta quality gates, but make them configurable by lane
- Preserve force-include logic for unusual momentum/catalyst names, but only after instrument hygiene passes

Implementation detail:

- Add a clearer notion of `universe source` and `instrument type`
- Persist or at least expose why a name was excluded
- Keep the output as a deterministic filtered candidate set, not an LLM-selected set

## Scope

Files or modules likely affected:

- `config.py`
- `universe_builder.py`
- `docs/INTERNALS.md`

## Non-Goals

- Do not change AI thesis prompting.
- Do not modify target/stop logic.
- Do not change live AI selection rules in this ticket.
- Do not introduce external paid vendors unless already available in repo infrastructure.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.
- Preserve current fallback behavior when remote constituent fetches fail.
- Keep the change deterministic and reproducible.

## Acceptance Criteria

- Observable behavior:
  - The discovery universe can include broad US listed common stocks beyond the current benchmark constituent lists.
  - Obvious non-common-stock instruments are excluded before scoring.
  - Config exposes clear thresholds for price, liquidity, and quality gates.
  - Universe-builder output can report counts by source and counts dropped by exclusion reason.
- Tests:
  - Add targeted tests for instrument exclusion and tradability filters.
  - Add targeted tests for fallback behavior when source fetches fail.
- Documentation:
  - `docs/INTERNALS.md` documents the new universe sources and exclusion rules.

## Verification Plan

- Targeted tests:
  - `pytest -q tests/test_universe_builder.py`
- Additional verification:
  - run the relevant `universe_builder.py` CLI paths locally where feasible
  - verify logs or printed summaries for inclusion/exclusion counts
- `make verify-full` only if broader integration behavior changes materially.

## QA Notes

- Test scenarios: broad US listing input, benchmark-only fallback, mixed junk tickers, missing metadata rows.
- Edge cases: symbols with dots that are valid non-US listings, renamed symbols, acquisition-pinned names, symbols with incomplete histories.
- Regression risks: accidentally shrinking the current usable universe, or admitting too many thin names.

## Launch / Release Notes

- User-facing change summary: discovery coverage expands to a broader US listed equity pool with stricter instrument hygiene.
- Operational notes: compare pre/post candidate counts and quality distributions before changing downstream execution gates.
- Rollback notes: revert to prior universe-source selection and threshold defaults.

## Post-Launch Validation

- What to monitor:
  - candidate-count growth
  - percent of names dropped for instrument hygiene
  - average dollar-volume quality of survivors
  - number of off-index names entering the watchlist
- How success will be confirmed:
  - broader coverage without a collapse in quality metrics
- Follow-up decision date:
  - after 2-4 weeks of daily runs

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-056: broaden the discovery universe to a larger US listed common-stock pool while tightening instrument hygiene.

Goal:
- Expand beyond the current benchmark-heavy universe so the system can discover more high-value US names.
- Preserve deterministic screening and maintain or improve tradability quality.

Scope:
- config.py
- universe_builder.py
- docs/INTERNALS.md
- tests/test_universe_builder.py

Required changes:
- Add support for a broader US discovery source set centered on NASDAQ, NYSE, and NYSE American common stocks.
- Retain existing benchmark and ADR inputs where useful, but do not rely on them as the sole discovery path.
- Add explicit exclusions for ETFs, ETNs, funds, preferreds, warrants, rights, units, shells, and other non-common-stock instruments where metadata allows.
- Keep or strengthen tradability gates:
  - price >= $2.00
  - avg 30d dollar volume >= $5M minimum
  - configurable option for stricter liquidity thresholds later
- Preserve fallback behavior when remote universe-source fetches fail.
- Expose summary counts for included names and excluded names by reason.

Non-goals:
- No changes to AI prompt construction
- No changes to target/stop geometry
- No changes to downstream AI selection gates in this ticket

Constraints:
- Risk is trading-logic; keep downstream behavior stable
- No paid-vendor additions
- No broad refactor outside the named files

Tests / verification:
- pytest -q tests/test_universe_builder.py
- run the relevant universe_builder CLI path(s) locally if feasible
- confirm summary output includes source and exclusion counts
```
