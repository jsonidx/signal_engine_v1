# Task: Ticker Page Option Candidates Card

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
Effort: M
Target Release: backlog
Due Date: TBD
Dependencies: TRD-022
Blocked By: none
Links: `docs/tasks/new/TRD-020-ibkr-options-roadmap.md`, `dashboard/frontend/src/pages/TickerPage.tsx`, `dashboard/frontend/src/lib/api.ts`
Success Metric: the ticker deep-dive page shows a clear `Option Candidates` section with recommended contracts or a defensible no-trade state.

## Problem Statement

Even if the backend can generate option candidates, the current ticker deep-dive page has no place to present them. Users still need to inspect raw broker screens or copy data elsewhere.

## User Impact

The user should be able to open one ticker page and immediately see whether there is an options trade worth considering, plus which contract best matches the thesis.

## Objective

Add an `Option Candidates` card to the ticker deep-dive page that renders the top backend candidates and explains why the product recommends them or why it recommends no option trade.

## Proposed Solution

Extend the frontend API types and `TickerPage.tsx` to fetch `/api/ticker/{symbol}/option-candidates` and render a dedicated card below the existing options context. Reuse the established page style; do not introduce a separate options workflow yet.

The card should emphasize the few fields that matter for execution:

- call/put
- strike
- expiry
- DTE
- delta
- mid
- spread %
- OI / volume
- breakeven
- short rationale

## Scope

- `dashboard/frontend/src/lib/api.ts`
- `dashboard/frontend/src/pages/TickerPage.tsx`
- focused frontend tests if the page already has relevant coverage patterns

## Non-Goals

- Do not add chain browsing or a full option table.
- Do not build a new page route.
- Do not add LLM ranking in this ticket.
- Do not place broker orders from the UI.

## Constraints

- Preserve the current visual language of the ticker page.
- Keep the card compact and decision-oriented.
- Handle loading, missing data, and suppression states cleanly.
- Do not overwhelm the page with raw contract detail.

## Acceptance Criteria

- Observable behavior: ticker page shows an `Option Candidates` card when the endpoint returns data.
- Observable behavior: each candidate renders the essential contract fields plus a concise rationale.
- Observable behavior: when no candidates are returned, the page shows a clear suppression/no-trade message instead of a broken or empty card.
- Observable behavior: loading and error states are visually distinct and non-blocking.
- Tests:
  - renders candidates when API returns 1-3 rows
  - renders suppression state cleanly
  - handles missing optional fields gracefully

## Verification Plan

- focused frontend tests for the new card
- manual browser verification on a ticker with mocked or real candidate data
- `make verify` if practical after the frontend changes

## QA Notes

- Test scenarios:
  - single top candidate
  - three candidates with distinct expiries
  - no-trade suppression state
- Edge cases:
  - missing volume or OI
  - missing Greeks in one candidate row
  - stale chain timestamp if exposed
- Regression risks:
  - overcrowding the ticker page
  - card positioning making the flow harder to scan

## Launch / Release Notes

- User-facing change summary: the ticker deep-dive page can show recommended option contracts directly.
- Operational notes: data quality depends on the backend candidate engine and IBKR availability.
- Rollback notes: remove the card fetch/render and leave the existing options-flow summary intact.

## Post-Launch Validation

- What to monitor: card render success, suppression frequency, and whether users still need to leave the page for contract lookup.
- How success will be confirmed: ticker deep dive can present a self-contained option idea for actionable names.
- Follow-up decision date: after the AI overlay ticket is evaluated.

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-023, "Ticker Page Option Candidates Card," in this repo.

Goal:
- Render the backend option candidates directly on the ticker deep-dive page in a compact, execution-oriented card.

Scope:
- `dashboard/frontend/src/lib/api.ts`
- `dashboard/frontend/src/pages/TickerPage.tsx`
- focused frontend tests if needed

Requirements:
- Fetch the new `/api/ticker/{symbol}/option-candidates` endpoint.
- Render an `Option Candidates` card with key fields: right, strike, expiry, DTE, delta, mid, spread %, OI/volume, breakeven, and rationale.
- Show a clean no-trade/suppression state when appropriate.
- Keep the UI compact and aligned with the existing ticker-page visual language.

Non-goals:
- No full chain browser.
- No new route/page.
- No order placement.
- No new LLM logic in this ticket.

Tests and verification:
- Add focused frontend coverage for candidate and suppression states.
- Manually verify the card in the browser if practical.
- Run the focused tests you add.
- Run `make verify` if practical.

Implementation note:
- Favor scanability over density. This card should answer “is there an option worth trading here?” in a few seconds.
