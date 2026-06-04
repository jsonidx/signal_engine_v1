# Task: Options Prompt Mode and AI Candidate Ranking

Status: proposed
Stage: discovery
Type: feature
Priority: P2
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: dashboard
Category: automation
Risk: frontend
Effort: M
Target Release: backlog
Due Date: TBD
Dependencies: TRD-022, TRD-023, TRD-042
Blocked By: TRD-022, TRD-023, TRD-042
Links: `docs/tasks/new/TRD-020-ibkr-options-roadmap.md`, `dashboard/frontend/src/pages/TickerPage.tsx`, `ai_quant.py`
Success Metric: the app can generate an options-specific prompt or AI explanation that ranks pre-filtered candidates without inventing contracts.

## Problem Statement

Once deterministic candidates exist, the current `Copy Prompt` flow is still equity-oriented. It asks for trade commentary on the stock setup, not contract-level reasoning. If the app asks an LLM to choose options without pre-filtered candidates, the model may hallucinate or overfit sparse context.

## User Impact

Users want help understanding which recommended contract is best and why, but they should not have to trust an LLM to invent strikes, expiries, or liquidity assumptions.

## Objective

Add an options-specific prompt mode and optional AI ranking layer that consumes only the pre-filtered candidate set and explains the strike/expiry tradeoffs.

This ticket should remain blocked until the subscription gate and the deterministic candidate flow are already in place.

## Proposed Solution

Extend the existing ticker-page prompt flow so the user can choose between:

- `Equity Thesis`
- `Options Contract Selection`

The options prompt should include:

- the stock thesis context
- the deterministic candidate list
- contract-level fields needed for comparison
- explicit instruction that the model must rank only the provided candidates

Optionally, if the repo already supports internal LLM analysis patterns cleanly, add a lightweight backend prompt helper or reuse `ai_quant.py` conventions for ranking candidates.

## Scope

- `dashboard/frontend/src/pages/TickerPage.tsx`
- any shared prompt-builder helper used by the ticker page
- optional minimal backend helper if needed for internal AI ranking
- focused tests for prompt construction and guardrails

## Non-Goals

- Do not let the LLM inspect the raw chain directly.
- Do not add autonomous order recommendations without deterministic candidates.
- Do not redesign the existing AI thesis pipeline broadly.

## Constraints

- Preserve the current copy-prompt workflow; extend it rather than duplicating buttons unnecessarily.
- Ensure the prompt explicitly forbids selecting contracts outside the supplied candidate set.
- Keep the model’s role explanatory and comparative, not generative over the full chain.

## Acceptance Criteria

- Observable behavior: ticker page offers an options-specific prompt mode or equivalent UI control.
- Observable behavior: the copied prompt contains the stock thesis plus the provided candidate contracts and asks the LLM to rank only those contracts.
- Observable behavior: prompt text clearly constrains the LLM from inventing unsupported strikes/expiries.
- Tests:
  - prompt builder includes candidate rows and required fields
  - prompt builder includes ranking-only guardrail language
  - fallback behavior is clean when no candidates exist

## Verification Plan

- focused prompt-builder tests
- manual copy-prompt inspection from the ticker page
- `make verify` if practical after changes

## QA Notes

- Test scenarios:
  - one candidate only
  - three candidates with different deltas/DTE
  - no candidates available
- Edge cases:
  - missing optional Greeks
  - suppressed ticker
- Regression risks:
  - making the existing equity prompt harder to use
  - leaking raw chain complexity into the user-facing prompt

## Launch / Release Notes

- User-facing change summary: the existing ticker prompt flow can generate options-specific analysis from real candidates.
- Operational notes: AI ranking quality still depends on the deterministic candidate engine.
- Rollback notes: remove the options prompt mode and keep the existing equity-thesis prompt only.

## Post-Launch Validation

- What to monitor: whether the generated prompt stays bounded to provided candidates and produces usable trade comparisons.
- How success will be confirmed: the LLM explains candidate differences without inventing unsupported contracts.
- Follow-up decision date: after the first live user pass on the option-candidate card.

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-024, "Options Prompt Mode and AI Candidate Ranking," in this repo.

Goal:
- Extend the existing ticker-page copy-prompt flow so it can generate an options-specific prompt that ranks only the pre-filtered option candidates.

Start condition:
- Do not begin implementation until `TRD-042`, `TRD-022`, and `TRD-023` are complete.

Scope:
- `dashboard/frontend/src/pages/TickerPage.tsx`
- any local prompt-building helpers involved in the ticker page
- optional minimal backend helper only if absolutely needed
- focused tests for prompt generation

Requirements:
- Reuse the existing prompt entry point rather than adding a redundant second button unless the code structure truly requires it.
- Add an options-specific mode that includes stock thesis context and the deterministic candidate rows.
- Explicitly constrain the LLM to rank only the supplied candidates.
- Handle the no-candidate case gracefully.

Non-goals:
- No raw chain browsing by the LLM.
- No autonomous trade execution.
- No broad redesign of `ai_quant.py`.

Tests and verification:
- Add focused tests for prompt construction and guardrails.
- Manually inspect the copied prompt text.
- Run the focused tests you add.
- Run `make verify` if practical.

Implementation note:
- Keep the model in an explanation/ranking role. Contract discovery and tradability checks must remain deterministic.
