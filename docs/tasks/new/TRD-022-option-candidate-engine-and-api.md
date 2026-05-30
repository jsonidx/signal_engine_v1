# Task: Option Candidate Engine and API

Status: proposed
Stage: ready
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: api
Category: options
Risk: trading-logic
Effort: L
Target Release: backlog
Due Date: TBD
Dependencies: TRD-021
Blocked By: none
Links: `docs/tasks/new/TRD-020-ibkr-options-roadmap.md`, `docs/tasks/new/TRD-021-ibkr-option-chain-adapter.md`, `dashboard/api/main.py`, `ai_quant.py`
Success Metric: the API can return 1-3 deterministic option candidates for a ticker based on the existing stock thesis and contract-level chain data.

## Problem Statement

Even after contract-level chain data exists, the application still needs a deterministic engine that converts a stock thesis into a small, tradable option shortlist. Without that layer, the UI would either dump the raw chain or rely on an LLM to guess from too many contracts.

## User Impact

Users do not need the full chain first. They need a small set of valid candidates that match the stock thesis, hold period, and risk constraints. Without filtering and scoring, the product will overwhelm the user or surface low-quality contracts.

## Objective

Build a deterministic option-candidate engine plus API endpoint that scores contracts for a ticker and returns a compact shortlist with explicit pass/fail reasons.

## Proposed Solution

Use the existing stock thesis as the first filter, then run contract-level scoring on the normalized IBKR chain. Add an API endpoint such as `/api/ticker/{symbol}/option-candidates` that returns:

- candidate list
- rejection reasons
- suppression reason when no option should be traded

Candidate selection should be rules-first. The LLM is not part of this ticket.

## Scope

- new candidate-scoring module, likely `utils/option_candidates.py`
- backend endpoint in `dashboard/api/main.py`
- typed response shape updates in `dashboard/frontend/src/lib/api.ts`
- focused tests for filtering, scoring, and suppression logic

## Non-Goals

- Do not add UI rendering in this ticket.
- Do not ask the LLM to rank contracts yet.
- Do not support every complex options strategy in v1.
- Do not scan the entire ticker universe.

## Constraints

- Reuse existing ticker thesis context where possible:
  - direction
  - time horizon
  - entry/stop/targets
  - earnings proximity
  - current price
- Keep the scoring inspectable and deterministic.
- Reject illiquid or structurally poor contracts before returning candidates.
- Return a clear `no-trade` result when the ticker thesis is weak or the chain quality is poor.

## Acceptance Criteria

- Observable behavior: API returns a normalized response with:
  - `candidates[]`
  - `rejection_reasons[]`
  - `suppressed`
  - `suppression_reason`
  - any top-level context needed by the UI
- Observable behavior: first-pass filters enforce practical quality constraints such as:
  - DTE range by strategy preset
  - delta band
  - spread threshold
  - minimum open interest and/or volume
  - premium budget bounds
  - event-risk suppression when appropriate
- Observable behavior: ticker with weak thesis or poor liquidity yields a clean `no candidates` result with explanation.
- Tests:
  - bullish swing thesis returns bullish contracts only
  - bearish swing thesis returns bearish contracts only
  - illiquid contracts are filtered out
  - over-wide spreads are filtered out
  - weak or low-confidence thesis can suppress output
  - earnings or event-risk suppression works when configured

## Verification Plan

- focused unit tests for candidate scoring and filters
- API endpoint tests using mocked chain data
- `make verify` if practical after endpoint/test additions

## QA Notes

- Test scenarios:
  - bullish swing setup
  - bearish swing setup
  - LEAPS-eligible setup
  - no-trade due to chain quality
- Edge cases:
  - missing Greeks on otherwise valid quotes
  - ties between similar strikes
  - very cheap options with unusable spreads
- Regression risks:
  - overfitting the rules to a single market regime
  - returning candidates that contradict the stock thesis

## Launch / Release Notes

- User-facing change summary: backend now supports option-candidate recommendations per ticker.
- Operational notes: depends on the IBKR adapter and available chain data.
- Rollback notes: disable the endpoint and keep using aggregate options context only.

## Post-Launch Validation

- What to monitor: candidate count distribution, suppression rate, and frequent rejection reasons.
- How success will be confirmed: actionable tickers consistently return a small, defensible candidate set.
- Follow-up decision date: after ticker-page UI integration.

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-022, "Option Candidate Engine and API," in this repo.

Goal:
- Build deterministic contract filtering/scoring on top of the IBKR chain adapter and expose the result via `/api/ticker/{symbol}/option-candidates`.

Scope:
- new scoring module, likely `utils/option_candidates.py`
- endpoint wiring in `dashboard/api/main.py`
- response types in `dashboard/frontend/src/lib/api.ts`
- focused tests for scoring and endpoint behavior

Requirements:
- Use the existing stock thesis as the first filter.
- Score contracts deterministically; do not use any LLM in this ticket.
- Return a compact shortlist plus rejection/suppression reasons.
- Enforce practical tradability rules such as DTE bands, delta bands, spread thresholds, and minimum liquidity.
- Support a clean no-trade response when the ticker or chain quality is poor.

Non-goals:
- No UI rendering.
- No raw full-chain dump to the frontend.
- No order placement.
- No universe-wide scanning.

Tests and verification:
- Add focused unit tests for filter/scoring behavior.
- Add endpoint tests with mocked chain data.
- Run the focused tests you add.
- Run `make verify` if practical.

Implementation note:
- Keep strategy presets narrow in v1, for example long call, long put, and one LEAPS-oriented preset if the chain supports it.
