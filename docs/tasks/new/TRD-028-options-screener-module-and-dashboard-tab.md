# Task: Options Screener Module and Dashboard Tab

Status: proposed
Stage: ready
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: dashboard
Category: options
Risk: ui
Effort: L
Target Release: backlog
Due Date: TBD
Dependencies: TRD-022, TRD-026
Blocked By: none
Links: `docs/tasks/new/TRD-025-options-screener-learning-roadmap.md`, `dashboard/api/main.py`, `dashboard/frontend/src/App.tsx`, `dashboard/frontend/src/pages`
Success Metric: the dashboard can rank and display the best option opportunities across a thesis-filtered ticker universe.

## Problem Statement

The product can recommend option candidates for a single ticker, but it still lacks a cross-ticker options module that answers `which options are the best buys today?`

## User Impact

Users must visit ticker pages one by one instead of seeing the best option setups across the current universe in one place.

## Objective

Build an options screener module that ranks the best option opportunities across a curated set of stock theses and expose it as a new dashboard tab/page.

## Proposed Solution

Create a new API path and dashboard page that:

- selects a curated universe of actionable tickers
- runs the option candidate engine for those tickers
- ranks the resulting candidates across tickers
- displays the best opportunities with contract and thesis context

This must remain thesis-driven; do not scan every option contract across every available ticker.

## Scope

- backend screener service / endpoint
- new frontend page / tab for `Options`
- focused tests for API ranking and UI rendering

## Non-Goals

- Do not build a raw chain browser.
- Do not replace the ticker page as the detailed contract view.
- Do not introduce autonomous order routing.

## Constraints

- Use only a thesis-filtered ticker set.
- Keep the ranking explainable.
- Show enough context to compare opportunities without recreating the entire ticker page.

## Acceptance Criteria

- Observable behavior: dashboard has a new options screener tab/page.
- Observable behavior: page shows ranked option opportunities across multiple tickers.
- Observable behavior: each row/card includes:
  - ticker
  - direction / conviction
  - strategy preset
  - strike / expiry / DTE
  - holding window
  - option target/stop plan
  - delta
  - spread %
  - score
  - rationale
- Tests:
  - screener ranks multiple ticker candidates
  - empty/unavailable state renders cleanly
  - filtered universe behavior is respected

## Verification Plan

- focused API tests
- focused frontend tests
- manual browser verification

## QA Notes

- Test scenarios:
  - mixed bullish/bearish opportunity set
  - no candidates available
  - repeated tickers with multiple presets
- Edge cases:
  - only one ticker with candidates
  - all tickers suppressed
- Regression risks:
  - excessive latency from per-ticker chain fetches
  - ranking inconsistencies across presets

## Launch / Release Notes

- User-facing change summary: new options screener module for cross-ticker opportunity ranking.
- Operational notes: depends on candidate engine and persistence layers.
- Rollback notes: hide the tab and disable the endpoint.

## Post-Launch Validation

- What to monitor: endpoint latency, candidate volume, and click-through into ticker pages.
- How success will be confirmed: the screen surfaces a small, useful set of option opportunities each session.
- Follow-up decision date: after the first live usage cycle.

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-028, "Options Screener Module and Dashboard Tab," in this repo.

Goal:
- Add a new dashboard options screener that ranks the best option opportunities across a thesis-filtered ticker set.

Scope:
- backend screener endpoint/service
- new frontend page/tab
- focused API and frontend tests

Requirements:
- Remain thesis-driven; do not scan the raw full option universe.
- Rank candidates across tickers using the existing deterministic scoring outputs.
- Render compact, comparable option opportunity cards/rows with target and hold-plan context.

Non-goals:
- No raw chain browser.
- No order placement.
- No scoring redesign unless minimally required.

Tests and verification:
- Add focused API tests and frontend tests.
- Run the tests you add.
- Run `make verify` if practical.
