# Task: Research-Lane and Execution-Lane Funnel Split

Status: completed
Stage: done
Type: feature
Priority: P0
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: automation
Risk: trading-logic
Effort: L
Target Release: universe-v2
Due Date: TBD
Dependencies: TRD-055, TRD-056
Blocked By: none
Links: `ai_quant.py`, `utils/ticker_selector.py`, `utils/supabase_persist.py`, `reports/quarterly_reviews/2026-Q2-win-rate-deep-dive.md`
Success Metric: the system can persist and learn from a broader daily qualified candidate set without increasing LLM usage proportionally.

## Problem Statement

Today the same funnel is trying to do two jobs:

- research capture
- execution-quality AI selection

That is the wrong abstraction. From a PM perspective, you want more names entering the research record than you want entering the live LLM and execution path. Otherwise the dataset stays too small and too biased toward only the very top-ranked names.

## User Impact

- Too few directional candidates become labeled data.
- Learning is constrained by LLM budget rather than by deterministic screening quality.
- The system struggles to answer whether missed names were correctly filtered or simply never observed deeply enough.

## Objective

Split the funnel into two explicit lanes:

1. research lane: broader qualified candidate capture and persistence
2. execution lane: narrower AI-selected set for actionable theses

## Proposed Solution

Add a research-lane persistence path before final AI selection.

Recommended PM design:

- Research lane:
  - persist the daily qualified deterministic candidates after screening/resolution
  - record qualification metrics, direction, agreement, probability, exclusion reasons, and whether the name advanced to AI
  - target a materially larger daily cohort than the live AI lane
- Execution lane:
  - continue to route only a smaller subset into `ai_quant`
  - keep `skip_claude` and similar hard suppressions

Suggested initial shape:

- research lane target: top `25-50` qualified directional names/day, configurable
- execution lane target: top `5-15` names/day, configurable

The key is not that both lanes must call the LLM. The research lane should primarily persist deterministic candidate state and later outcomes.

## Scope

Files or modules likely affected:

- `ai_quant.py`
- `utils/ticker_selector.py`
- `utils/supabase_persist.py`
- `schema.sql`
- `migrations/`
- `docs/INTERNALS.md`

## Non-Goals

- Do not call the LLM for every research-lane candidate.
- Do not alter thesis prompt content in this ticket.
- Do not change target/stop rules.
- Do not remove current open-position force-inclusion behavior.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.
- New persistence must be idempotent where practical.
- Preserve current behavior when persistence is unavailable; failures should degrade safely.

## Acceptance Criteria

- Observable behavior:
  - The pipeline can persist a wider daily qualified research cohort separate from AI-issued theses.
  - Each persisted row records whether the candidate advanced to AI or was filtered out before AI.
  - Research-lane capacity is configurable independently from execution-lane capacity.
- Tests:
  - Add targeted persistence and selection tests.
  - Add regression tests showing execution-lane selection remains bounded.
- Documentation:
  - `docs/INTERNALS.md` documents the two-lane funnel and persistence semantics.

## Verification Plan

- Targeted tests:
  - `pytest -q tests/test_ai_quant_schema.py tests/test_supabase_integration.py tests/test_ticker_selector.py`
- Additional verification:
  - dry-run relevant pipeline entrypoints where feasible
  - verify research-lane rows can be written without forcing extra AI calls

## QA Notes

- Test scenarios: research-lane candidate persisted and not sent to AI, candidate persisted and sent to AI, persistence unavailable, duplicate run on same date.
- Edge cases: open-position force include, no qualified candidates, all candidates suppressed, stale resolution cache.
- Regression risks: accidental increase in AI calls or accidental suppression of live actionable names.

## Launch / Release Notes

- User-facing change summary: the engine now retains a broader daily research cohort for learning while keeping live AI issuance selective.
- Operational notes: compare research-lane candidate counts with execution-lane counts daily.
- Rollback notes: disable the research-lane persistence path while preserving the execution-lane flow.

## Post-Launch Validation

- What to monitor:
  - research-lane candidate volume per day
  - execution-lane AI call count
  - percent of research-lane names later appearing as AI names
  - persistence failure rates
- How success will be confirmed:
  - labeled coverage increases without a matching increase in AI cost
- Follow-up decision date:
  - after 2-4 weeks of runs

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-057: split the pipeline into a research lane and an execution lane.

Goal:
- Capture a broader qualified daily candidate cohort for learning.
- Keep live AI issuance selective and bounded.

Scope:
- ai_quant.py
- utils/ticker_selector.py
- utils/supabase_persist.py
- schema.sql
- migrations/
- docs/INTERNALS.md
- relevant tests for selector and persistence behavior

Required changes:
- Introduce a research-lane persistence path for deterministic qualified candidates prior to final AI selection.
- Persist enough metadata to analyze why a candidate qualified, whether it advanced to AI, and if not, why not.
- Keep execution-lane AI selection as a smaller bounded subset.
- Make research-lane capacity configurable independently from execution-lane AI capacity.
- Ensure persistence is idempotent where practical and degrades safely on failure.

Non-goals:
- Do not call the LLM for every research-lane candidate
- Do not change prompt wording
- Do not change target/stop logic

Constraints:
- Risk is trading-logic
- Preserve current skip_claude suppressions
- Preserve current open-position force-inclusion behavior
- Avoid broad refactors outside the listed files

Tests / verification:
- pytest -q tests/test_ai_quant_schema.py tests/test_supabase_integration.py tests/test_ticker_selector.py
- dry-run relevant pipeline entrypoints if feasible
- verify research-lane writes do not increase AI-call volume
```
