# Task: IBKR Option-Chain Adapter

Status: proposed
Stage: discovery
Type: feature
Priority: P3
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: automation
Risk: api
Effort: L
Target Release: backlog
Due Date: TBD
Dependencies: TRD-042
Blocked By: TRD-042
Links: `docs/tasks/new/TRD-020-ibkr-options-roadmap.md`, `dashboard/api/main.py`, `options_flow.py`
Success Metric: the backend can fetch and normalize a ticker-scoped IBKR options chain with quotes and Greeks for at least one US equity ticker.

## Problem Statement

The current system only exposes aggregate options metrics such as `iv_rank`, `expected_move_pct`, `put_call_ratio`, and `max_pain`. It has no broker-backed contract-level options data source, so the app cannot choose real expiries, strikes, deltas, or tradable contracts.

## User Impact

Users can identify a good stock setup but still need to leave the product and manually search the option chain in a broker platform. That creates friction and introduces manual errors when choosing the contract.

## Objective

Build a dedicated IBKR adapter that can retrieve and normalize option-chain metadata, quotes, and Greeks for a single underlying ticker.

This ticket must not start until `TRD-042` confirms the live subscription and access prerequisites.

## Proposed Solution

Add a new backend adapter module, likely `utils/ibkr_options.py`, that connects to `IB Gateway` or `TWS API`, resolves the underlying contract, fetches expiries and strikes, builds option contracts, and retrieves market data fields needed for contract screening.

Prefer `TWS API` or `IB Gateway` as the primary runtime path. Do not make `Client Portal API` the primary data path for continuous screening because of brokerage-session fragility, though it may be useful for reference or fallback.

Do not substitute a mock-only integration for the real entitlement check. The point of this ticket is to normalize actual broker behavior once subscriptions are live.

## Scope

- new adapter module, likely `utils/ibkr_options.py`
- any minimal config wiring needed in backend settings or env handling
- focused tests for chain normalization and error handling
- minimal documentation/comments for setup and runtime expectations

## Non-Goals

- Do not implement scoring or contract recommendation logic in this ticket.
- Do not add ticker-page UI.
- Do not wire LLM prompts yet.
- Do not place trades or submit orders.

## Constraints

- Keep the adapter ticker-scoped in v1; do not scan the full universe.
- Normalize output into a broker-agnostic internal shape where practical.
- Handle missing subscriptions, missing Greeks, and stale/partial quotes gracefully.
- Avoid hard-coding a single underlying exchange or only one expiration convention.

## Acceptance Criteria

- Observable behavior: for one US equity ticker, the adapter can return:
  - underlying contract identifier
  - expiries
  - strikes
  - right (`C`/`P`)
  - bid/ask/last or mid when available
  - open interest and volume when available
  - Greeks including at least `delta`, with other Greeks when subscribed
- Observable behavior: adapter failures are classified cleanly, for example:
  - auth/session unavailable
  - no market data entitlement
  - no chain for ticker
  - partial quote data only
- Tests:
  - normalized contract rows are produced from mocked IBKR payloads
  - missing quote fields do not crash normalization
  - missing Greeks do not crash normalization
  - unsupported ticker or empty chain returns a clean empty/error result
- Documentation: setup notes explain expected account, subscription, and runtime prerequisites.

## Verification Plan

- focused unit tests for adapter normalization and error cases
- one manual end-to-end pull against paper/live IBKR for a known US options ticker
- `make verify` if the test additions remain practical

## QA Notes

- Test scenarios:
  - liquid large-cap ticker with many expiries
  - ticker with LEAPS listed
  - ticker with thinner chain
- Edge cases:
  - expired or invalid contract metadata
  - zero bid or ask
  - missing open interest
  - market closed versus market open snapshots
- Regression risks:
  - blocking API calls freezing backend endpoints
  - fragile assumptions about market-data fields or exchange routing

## Launch / Release Notes

- User-facing change summary: none yet; backend capability only.
- Operational notes: requires local or deployed IBKR runtime credentials/session.
- Rollback notes: disable adapter usage and fall back to current aggregate options flow only.

## Post-Launch Validation

- What to monitor: adapter success rate, latency, and common failure reasons.
- How success will be confirmed: at least one ticker can consistently produce normalized chain rows with expected fields.
- Follow-up decision date: after the first integrated API endpoint is built.

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-021, "IBKR Option-Chain Adapter," in this repo.

Goal:
- Add a backend adapter that fetches and normalizes a ticker-scoped IBKR option chain with contract metadata, quotes, and Greeks.

Start condition:
- Do not begin implementation until `TRD-042` is complete and the project has verified IBKR subscriptions plus a working API session.

Scope:
- new module, likely `utils/ibkr_options.py`
- any minimal config/env wiring required for local runtime
- focused tests for normalization and failure handling

Requirements:
- Prefer `IB Gateway` / `TWS API` as the primary integration path.
- Keep v1 ticker-scoped; do not build a universe-wide scanner here.
- Return normalized rows with underlying contract id, expiry, strike, right, quote fields, OI/volume when available, and Greeks when available.
- Handle missing subscriptions or partial data without crashing.
- Keep the internal shape reusable for later scoring and UI work.

Non-goals:
- No scoring logic.
- No UI.
- No order placement.
- No LLM integration.

Tests and verification:
- Add focused adapter tests with mocked IBKR responses.
- Verify empty-chain, partial-data, and missing-Greeks cases.
- Run the focused tests you add.
- Run `make verify` if practical.

Implementation note:
- Keep the adapter interface narrow and deterministic so later tickets can consume it without knowing IBKR-specific payload details.
