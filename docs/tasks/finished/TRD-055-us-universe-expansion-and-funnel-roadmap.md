# Task: US Universe Expansion and Two-Lane Funnel Roadmap

Status: completed
Stage: done
Type: research
Priority: P0
Severity: high
Owner: Codex
Reviewer: Human
Product Area: data-pipeline
Category: research
Risk: trading-logic
Effort: M
Target Release: universe-v2
Due Date: TBD
Dependencies: none
Blocked By: none
Links: `config.py`, `universe_builder.py`, `ai_quant.py`, `utils/ticker_selector.py`, `reports/quarterly_reviews/2026-Q2-win-rate-deep-dive.md`, `reports/quarterly_reviews/2026-Q2-pm-review-extension.md`
Success Metric: the repo has an approved implementation plan for a broader US equity discovery universe and a separate research-vs-execution funnel without weakening current AI cost controls.

## Problem Statement

The current architecture is mostly correct at the gating layer but too narrow at the discovery layer. It is built around index-heavy universes and then aggressively constrains AI synthesis to a very small number of names. From a hedge-fund PM perspective, that design is acceptable for large-cap monitoring but suboptimal for pattern discovery in catalyst, pre-breakout, squeeze, and high-retail-attention names.

The result is a structural mismatch:

- the discovery layer is too benchmark-shaped
- the execution layer is too tightly coupled to the research layer
- the system may miss high-value non-index US listings or see them too late
- the team cannot easily learn from a broader daily candidate set without spending unnecessary LLM budget

## User Impact

- High-opportunity US names outside the current index-centric pool may never reach the screener.
- Research coverage is narrower than the opportunity set.
- Live execution gating is forced to do double duty as both cost control and research sampling.
- Learning speed is slower because too few directional candidates mature into labeled outcomes.

## Objective

Define an implementation-ready program that:

1. expands discovery to a broader US listed universe
2. keeps execution selective
3. creates a separate research lane for richer daily candidate capture
4. improves observability around which names qualified, why they were filtered, and which cohorts produce edge

## Proposed Solution

Adopt a two-lane architecture:

1. Discovery / research lane
   - broader US listed common-stock universe
   - strict instrument hygiene and liquidity filters
   - larger daily qualified set persisted for later outcome analysis

2. Execution / AI lane
   - narrower, high-confidence subset only
   - hard gating by agreement/probability/liquidity
   - dynamic daily capacity rather than a fixed tiny cap where appropriate

Program work should be split into four implementation tickets:

1. `TRD-056` universe expansion and instrument hygiene
2. `TRD-057` research-vs-execution lane split
3. `TRD-058` AI selection gate recalibration and capacity controls
4. `TRD-059` universe coverage and qualification analytics

## Scope

Files or modules likely affected:

- `config.py`
- `universe_builder.py`
- `ai_quant.py`
- `signal_engine.py`
- `utils/ticker_selector.py`
- `utils/supabase_persist.py`
- `dashboard/api/main.py`
- `dashboard/frontend/src/pages/ScreenersPage.tsx`
- `docs/INTERNALS.md`

## Non-Goals

- Do not send the full expanded universe through the LLM.
- Do not remove existing suppressions such as `skip_claude`.
- Do not loosen liquidity standards just to inflate coverage.
- Do not change thesis direction logic and target geometry in this ticket set.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.
- Keep live trading behavior stable until the execution-lane ticket explicitly changes it.
- Preserve a deterministic, low-cost pre-screen before any AI call.

## Acceptance Criteria

- Observable behavior:
  - A documented roadmap exists with implementation tickets sequenced by dependency.
  - The roadmap clearly separates research-lane goals from execution-lane goals.
  - Each dependent ticket includes a paste-ready Claude prompt.
- Tests:
  - Not applicable for this roadmap-only ticket.
- Documentation:
  - The roadmap records the PM rationale for universe expansion and for keeping execution selective.

## Verification Plan

- Review the dependent tickets for consistency of scope, sequencing, and risk boundaries.
- Run `python3 scripts/sync_task_status.py` after ticket creation if status normalization is needed.

## QA Notes

- Test scenarios: review whether any dependent ticket accidentally routes the full universe into AI synthesis.
- Edge cases: stale watchlist fallback, missing exchange metadata, microcap contamination.
- Regression risks: turning a research-lane change into an execution-lane behavior change too early.

## Launch / Release Notes

- User-facing change summary: none; planning artifact only.
- Operational notes: use this roadmap as the control document for Claude execution order.
- Rollback notes: not applicable.

## Post-Launch Validation

- What to monitor: whether dependent tickets preserve the two-lane design.
- How success will be confirmed: Claude can implement the sequence without re-scoping the project.
- Follow-up decision date: after `TRD-059` first-pass coverage analytics are available.

## Handoff Notes

Hedge-fund PM recommendation:

- Expand discovery materially, not execution indiscriminately.
- Build the universe from broad US listings, then filter hard on instrument quality and tradability.
- Separate the learning funnel from the capital-allocation funnel.
- Prefer more deterministic candidate capture over more LLM calls.

Program execution order:

1. `TRD-056` universe expansion and hygiene
2. `TRD-057` research and execution lane split
3. `TRD-058` AI gate recalibration and capacity controls
4. `TRD-059` qualification analytics and monitoring

Paste-ready Claude program prompt:

```text
Implement the broader US universe and two-lane AI funnel program defined by TRD-055 and its dependent tickets.

Execution rules:
- Preserve the current architectural principle: deterministic broad screening first, AI on a narrower qualified subset.
- Do not route the full expanded universe into the LLM.
- Keep live execution behavior stable until the specific execution-lane ticket changes it.
- Preserve existing hard suppressions such as skip_claude.
- Use each dependent ticket's exact scope, tests, and constraints.

Tickets in order:
1. TRD-056 — broaden the US discovery universe and tighten instrument hygiene
2. TRD-057 — split research-lane candidate capture from execution-lane AI selection
3. TRD-058 — recalibrate AI qualification gates and daily capacity controls
4. TRD-059 — add analytics for universe coverage, qualification, and filter fallout

Deliver work ticket by ticket with verification at each stage.
```
