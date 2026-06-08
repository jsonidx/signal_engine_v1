# Task: Index Source Expansion for Swing-Trading Universe

Status: completed
Stage: done
Type: research
Priority: P1
Severity: medium
Owner: Codex
Reviewer: Human
Product Area: data-pipeline
Category: research
Risk: trading-logic
Effort: M
Target Release: universe-v2
Due Date: TBD
Dependencies: TRD-055, TRD-056
Blocked By: none
Links: `universe_builder.py`, `config.py`, `docs/INTERNALS.md`, `https://www.nasdaq.com/solutions/global-indexes/nasdaq-100`, `https://www.nasdaq.com/solutions/global-indexes/nasdaq-composite`, `https://www.spglobal.com/spdji/en/indices/equity/sp-600/`, `https://www.spglobal.com/spdji/en/indices/equity/sp-composite-1500/`
Success Metric: the repo has an approved implementation plan for adding the highest-value missing index sources for swing-trading discovery and execution.

## Problem Statement

The current discovery universe is benchmark-heavy but still misses several high-value index sources for swing trading. In particular, the system underweights:

- liquid high-beta Nasdaq leaders
- quality-screened small caps
- broader tradable U.S. market coverage beyond the current pieced-together benchmark set

The result is that the engine may miss or delay coverage of some of the best swing-trading names, especially in technology, growth, and liquid small-cap cohorts.

## User Impact

- Some of the cleanest swing names never enter the funnel early enough.
- The universe remains too dependent on existing benchmark membership rather than on tradable opportunity.
- The PM team cannot distinguish which missing source adds genuine value versus duplicate noise.

## Objective

Document and sequence the implementation of the highest-value additional index sources for swing trading:

1. `S&P Composite 1500`
2. `Nasdaq-100`
3. broader `Nasdaq-listed common stocks` research lane
4. `S&P SmallCap 600`

## Proposed Solution

Treat these additions as a compact program:

1. `TRD-061` add `S&P Composite 1500` as the main execution-core backbone
2. `TRD-062` add `Nasdaq-100` as a liquid high-beta execution overlay
3. `TRD-063` add a broader Nasdaq-listed common-stock research lane with hard filters
4. `TRD-064` add `S&P SmallCap 600` as the preferred quality small-cap swing universe

## Scope

Files or modules likely affected:

- `config.py`
- `universe_builder.py`
- `docs/INTERNALS.md`

## Non-Goals

- Do not add every possible index source.
- Do not route raw index additions directly into the LLM.
- Do not broaden the execution lane without tradability filters.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.
- Maintain deterministic screening before any AI call.

## Acceptance Criteria

- Observable behavior:
  - A roadmap exists for the missing index sources with explicit priorities and roles.
  - Each dependent ticket distinguishes execution-core use from research-lane use.
  - Each dependent ticket includes a paste-ready Claude prompt.
- Tests:
  - Not applicable for this roadmap-only ticket.
- Documentation:
  - The roadmap states why each index source is being added.

## Verification Plan

- Review dependent tickets for scope clarity and overlap control.

## QA Notes

- Test scenarios: check whether any dependent ticket duplicates work already covered by `TRD-056`.
- Edge cases: index source overlap, duplicate symbol handling, stale source feeds.
- Regression risks: adding redundant sources with no new edge.

## Launch / Release Notes

- User-facing change summary: none; planning artifact only.
- Operational notes: use this roadmap to implement missing high-value source sets in order.
- Rollback notes: not applicable.

## Post-Launch Validation

- What to monitor: whether each source adds incremental qualified names rather than redundant noise.
- How success will be confirmed: the team can identify coverage gains by source.
- Follow-up decision date: after `TRD-064` analytics are live.

## Handoff Notes

PM source-priority view:

- `S&P 1500`: best core executable U.S. market backbone
- `Nasdaq-100`: best high-liquidity growth and momentum overlay
- `Nasdaq broad`: best research-lane expansion source, not a raw execution set
- `S&P 600`: best quality small-cap swing-trading pool

Paste-ready Claude program prompt:

```text
Implement the missing high-value index source additions defined by TRD-060 and its dependent tickets.

Execution rules:
- Treat each source according to its intended role: execution core, execution overlay, or research-lane expansion.
- Do not route raw broad-source additions directly into the LLM.
- Preserve deterministic screening and instrument hygiene.
- Use each dependent ticket's exact scope and constraints.

Tickets in order:
1. TRD-061 — add S&P Composite 1500 as execution-core backbone
2. TRD-062 — add Nasdaq-100 as execution overlay
3. TRD-063 — add broad Nasdaq-listed common stocks as research expansion
4. TRD-064 — add S&P SmallCap 600 as quality small-cap lane
```
