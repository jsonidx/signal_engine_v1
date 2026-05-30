# Task: Option Recommendation Snapshot Persistence

Status: proposed
Stage: ready
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: options
Risk: schema
Effort: L
Target Release: backlog
Due Date: TBD
Dependencies: TRD-022
Blocked By: none
Links: `docs/tasks/new/TRD-025-options-screener-learning-roadmap.md`, `utils/option_candidates.py`, `utils/supabase_persist.py`, `schema.sql`
Success Metric: every generated option recommendation can be stored and queried later with thesis context, contract fields, score, and suppression state.

## Problem Statement

Option recommendations are currently generated on request and cached briefly in API memory, but they are not persisted to Supabase. This makes them unsuitable for historical review, learning, or resolution analytics.

## User Impact

Without persistence:

- recommendation history disappears
- outcomes cannot be tied back to the original contract features
- there is no dataset for future model or scoring improvements

## Objective

Persist option recommendation snapshots, including successful candidates and defensible no-trade/suppressed results, into Supabase.

## Proposed Solution

Add a new Supabase table, likely `option_candidate_snapshots`, and persistence helpers in `utils/supabase_persist.py`. Whenever option candidates are generated for a ticker, write snapshot rows with full selection context and ranking fields.

## Scope

- schema update / migration for `option_candidate_snapshots`
- persistence helper(s) in `utils/supabase_persist.py`
- integration at the option-candidate generation point
- focused tests for serialization, upsert/insert semantics, and suppression-state persistence

## Non-Goals

- Do not compute realized outcomes in this ticket.
- Do not build the options screener UI.
- Do not redesign the candidate engine itself unless a minimal persistence hook is needed.

## Constraints

- Preserve enough context to reconstruct why a recommendation was made.
- Persist suppression/no-trade outcomes in a queryable way.
- Keep schema extensible for future outcome joins and analytics.
- Persist target and exit-plan fields so later analysis can compare intended versus realized trade behavior.

## Acceptance Criteria

- Observable behavior: generated option candidate results are written to Supabase.
- Observable behavior: persisted rows include ticker, thesis link/context, contract fields, score, rank, source, rationale, and target/holding/exit-plan fields.
- Observable behavior: suppressed/no-trade states are also persisted with `suppressed` and `suppression_reason`.
- Tests:
  - candidate snapshot row serializes correctly
  - multiple ranked candidates persist for one ticker event
  - suppressed result persists cleanly without candidate rows being required
  - holding-window and target/exit fields persist correctly
  - read/write behavior is stable under repeated generation for the same ticker/date when designed

## Verification Plan

- focused persistence tests
- local smoke test that generates one candidate set and confirms rows exist
- `make verify` if practical

## QA Notes

- Test scenarios:
  - bullish ticker with 3 candidates
  - bearish ticker with 1 candidate
  - suppressed ticker with no candidates
- Edge cases:
  - missing Greeks
  - yfinance fallback source
  - duplicate generation on same day
- Regression risks:
  - schema drift
  - storing insufficient context for later learning

## Launch / Release Notes

- User-facing change summary: none yet; persistence foundation only.
- Operational notes: increases Supabase write volume for option recommendation generation.
- Rollback notes: disable persistence hook and preserve runtime behavior.

## Post-Launch Validation

- What to monitor: row counts, write failures, duplicate patterns.
- How success will be confirmed: recommendation history is queryable and consistent.
- Follow-up decision date: after outcome tracking begins.

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-026, "Option Recommendation Snapshot Persistence," in this repo.

Goal:
- Persist generated option recommendation snapshots to Supabase so they can be used later for analytics and learning.

Scope:
- schema / migration for a new `option_candidate_snapshots` table
- persistence helpers in `utils/supabase_persist.py`
- integration at the candidate-generation flow
- focused tests for persistence behavior

Requirements:
- Store ticker, thesis context, contract fields, ranking score, rationale, source, suppression state, and target/holding/exit-plan fields.
- Persist both candidate recommendations and defensible no-trade/suppressed results.
- Keep schema suitable for later joins to outcome records.

Non-goals:
- No realized outcome tracking yet.
- No new UI.
- No scoring changes.

Tests and verification:
- Add focused persistence tests.
- Run the tests you add.
- Run `make verify` if practical.
