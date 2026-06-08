# Task: Add Broad Nasdaq-Listed Common Stocks as Research-Lane Expansion

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
Dependencies: TRD-056, TRD-057, TRD-060
Blocked By: none
Links: `config.py`, `universe_builder.py`, `docs/INTERNALS.md`, `https://www.nasdaq.com/solutions/global-indexes/nasdaq-composite`, `https://indexes.nasdaq.com/Index/Overview/COMP`
Success Metric: the system can use a broad Nasdaq-listed common-stock pool for research-lane discovery without flooding the execution lane.

## Problem Statement

Broad Nasdaq coverage is one of the highest-value missing research sources, but it is too noisy to add directly to the execution lane. Nasdaq’s own materials describe the Nasdaq Composite as a 3,000+ stock broad measure of Nasdaq-listed common-type stocks, which makes it useful for discovery but too broad for direct trade issuance without strict filters.

## User Impact

- Off-index Nasdaq opportunities may never enter the research funnel.
- The system remains biased toward the current benchmark set.
- Broad Nasdaq coverage is either absent or would be dangerous if added without lane separation.

## Objective

Add a broader Nasdaq-listed common-stock source that feeds the research lane only, subject to hard instrument and liquidity filters.

## Proposed Solution

- Add a broad Nasdaq-listed source path or equivalent source abstraction
- Restrict it to the research lane
- Apply strict instrument hygiene and liquidity filters before qualification
- Ensure these names do not automatically expand the execution lane
- Redesign broad-source intake policy so raw listing feeds are not arbitrarily truncated before a defensible pre-rank
- If a cap is required for runtime reasons, rank first by a defensible quality proxy such as dollar volume, market cap, or another explicit tradability metric before truncating
- If no defensible pre-rank is available, prefer full eligible breadth with downstream filtering over alphabetical or otherwise arbitrary source-level truncation

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

- Do not route the raw broad Nasdaq set into the LLM.
- Do not relax liquidity filters.
- Do not change prompt logic.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.
- Preserve the research-vs-execution separation introduced in `TRD-057`.

## Acceptance Criteria

- Observable behavior:
  - Broad Nasdaq-listed common stocks can feed discovery and the research lane.
  - Instrument hygiene and tradability filters apply before qualification.
  - These names do not automatically expand the execution-lane AI set.
  - Broad-source intake does not rely on arbitrary source-order truncation.
  - Any runtime cap on a broad listing feed is applied only after an explicit, documented pre-rank by a defensible metric.
- Tests:
  - Add or update universe-builder and selector tests.
- Documentation:
  - `docs/INTERNALS.md` documents broad Nasdaq as research-lane only.
  - `docs/INTERNALS.md` documents the broad-source intake rule and any ranking/cap limitation honestly.

## Verification Plan

- `pytest -q tests/test_universe_builder.py tests/test_ticker_selector.py`

## QA Notes

- Test scenarios: broad Nasdaq enabled with research lane, broad Nasdaq disabled, high-overlap names, junk-instrument exclusions.
- Edge cases: international listings on Nasdaq, funds masquerading as common stock, duplicate source tagging, arbitrary source-order bias from raw listing files.
- Regression risks: accidental AI-lane expansion, excess noise, poor filter hygiene, alphabetical or otherwise non-economic truncation of broad listing feeds.

## Launch / Release Notes

- User-facing change summary: broad Nasdaq coverage is available for research-lane discovery only.
- Operational notes: compare off-index Nasdaq entrants and filter fallout before considering any execution-lane promotion.
- Rollback notes: disable the source in config.

## Post-Launch Validation

- What to monitor: new off-index entrants, research-lane counts, exclusion-rate by reason, AI-lane leakage, and any evidence that intake is skewed by raw source ordering.
- How success will be confirmed: broader Nasdaq names are captured for learning without blowing out AI issuance, and the intake set is not just an arbitrary alphabetical subset.
- Follow-up decision date: after two to four weeks.

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-063: add broad Nasdaq-listed common-stock coverage as a research-lane expansion source only.

Scope:
- config.py
- universe_builder.py
- ai_quant.py
- utils/ticker_selector.py
- docs/INTERNALS.md
- tests/test_universe_builder.py
- tests/test_ticker_selector.py

Required changes:
- Add a broad Nasdaq-listed common-stock source or equivalent abstraction.
- Restrict it to the research lane.
- Apply hard instrument hygiene and tradability filters before qualification.
- Ensure the broad source does not automatically expand the execution-lane AI set.
- Do not arbitrarily cap a broad raw listing feed before ranking it.
- If runtime requires a cap, add an explicit pre-rank using a defensible metric such as dollar volume or market cap before truncating.
- If no defensible pre-rank is available, prefer full eligible breadth with downstream lane/liquidity filtering over arbitrary source-order truncation.

Non-goals:
- No raw full-Nasdaq LLM routing
- No liquidity-threshold relaxation
- No prompt changes

Tests:
- pytest -q tests/test_universe_builder.py tests/test_ticker_selector.py
```
