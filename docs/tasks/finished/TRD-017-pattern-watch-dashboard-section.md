# Task: Pattern Watch Dashboard Section

Status: done
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Codex
Product Area: dashboard
Category: growth
Risk: trading-logic
Effort: M
Target Release: next dashboard iteration
Due Date: 2026-06-05
Dependencies: TRD-001, TRD-004, TRD-006, TRD-007, TRD-009
Blocked By: none
Links: none
Success Metric: Morning Brief shows a dedicated Pattern Watch section with all current SNOW/CRSR/DELL-like candidates, a matched archetype, setup flags, case-based upside, and case-based probability/confidence.

## Problem Statement

The dashboard now has separate signals for catalyst setups, early momentum breakouts, and event-queue candidates, but the user still has to look across multiple sections to understand: “Which tickers look like the previous big winners such as `SNOW`, `CRSR`, or `DELL`?”

The current UI can hide empty sections, and it does not summarize similarity to historical archetypes or show case-based upside/probability in one place.

## User Impact

The user may still miss high-momentum or pre-catalyst setups because they are split between Hot Entry, Catalyst Setup, Candidate Pool, Daily Rankings, and Telegram summaries. The user wants a single dashboard section where all such tickers are gathered, flagged, and ranked by similarity to known successful setup patterns.

## Objective

Create a new Morning Brief section named **Pattern Watch** that aggregates all SNOW/CRSR/DELL-style candidates and displays:

- ticker,
- matched historical archetype: `SNOW`, `CRSR`, `DELL`, or mixed,
- trigger flags,
- setup similarity score,
- case-based upside potential,
- case-based probability/confidence,
- sample-size warning,
- reason why the ticker is included.

## Proposed Solution

Add a backend endpoint, suggested path:

- `GET /api/pattern-watch`

The endpoint should aggregate candidates from:

- `/api/watch-setup` source data / `catalyst_scores`,
- `candidate_snapshots` rows where `selection_reason` contains `fresh_catalyst_breakout`,
- event queue rows where available,
- latest `daily_rankings` context where available.

Then score each candidate against historical pattern archetypes:

- **SNOW pattern:** pre-earnings / catalyst re-rating setup.
  - Evidence examples: earnings soon, serial earnings beats, call demand, volume expansion, technical strength, stale or neutral thesis, not a squeeze.
- **CRSR pattern:** early catalyst momentum / product-news breakout.
  - Evidence examples: fresh catalyst tag, early momentum breakout, catalyst price expansion, elevated short interest optional, not yet old `VOL_BREAKOUT`.
- **DELL pattern:** large-cap early momentum continuation.
  - Evidence examples: 5d return above early threshold, new high / strong technical momentum, high dollar volume, event/ranking context, not necessarily earnings.

Use static historical archetype baselines from the current postmortems:

- `SNOW`: approximate case upside +42%.
- `CRSR`: approximate case upside +22% to +48%, depending on trigger date.
- `DELL`: approximate case upside +7% to +49%, depending on trigger date.

Probability must be presented as **case-based / prototype probability**, not a statistically validated model. Use conservative wording and include sample size.

Suggested response shape:

```json
{
  "data_available": true,
  "as_of": "2026-05-29",
  "count": 3,
  "method_note": "Case-based similarity using SNOW/CRSR/DELL archetypes; low sample size.",
  "data": [
    {
      "ticker": "XYZ",
      "matched_pattern": "SNOW",
      "similarity_pct": 82,
      "case_probability_pct": 67,
      "case_upside_pct": 34,
      "confidence": "LOW_SAMPLE",
      "sample_size": 1,
      "flags": ["earnings_imminent", "call_demand_elevated", "volume_expansion_confirmed"],
      "reason": "Pre-earnings catalyst setup similar to SNOW May 2026",
      "source": ["watch_setup", "candidate_snapshots"],
      "current_price": 123.45,
      "days_to_earnings": 5,
      "raw_score": 51.4
    }
  ]
}
```

Add a frontend section to the Morning Brief:

- Title: `Pattern Watch`
- Subtitle: `Setups resembling SNOW / CRSR / DELL case studies`
- Keep visible even when empty, with an empty state explaining that no current ticker matches the archetypes.
- Use badges for pattern match and trigger flags.
- Show upside and probability side-by-side.
- Show a small warning label: `case-based, low sample`.

## Scope

Files or modules likely affected:

- `dashboard/api/main.py`
- `dashboard/api/tests/test_endpoints.py`
- `dashboard/frontend/src/lib/api.ts`
- `dashboard/frontend/src/pages/HomePage.tsx`
- Optional: `utils/pattern_watch.py`
- Optional: `tests/test_pipeline_defects.py`
- Optional: `tests/fixtures/snow_may2026.py`
- Optional: `tests/fixtures/crsr_may2026.py`
- Optional: new `tests/fixtures/dell_may2026.py`

## Non-Goals

- Do not replace Hot Entry.
- Do not call this a buy signal.
- Do not present the probability as statistically proven.
- Do not require AI synthesis or paid LLM calls.
- Do not create a full portfolio backtest in this ticket.
- Do not change trade execution or position sizing.

## Constraints

- Must work after the no-AI daily GitHub workflow.
- Must use existing persisted data where possible.
- Must degrade gracefully if `candidate_snapshots`, `catalyst_scores`, or event queue data is empty.
- Must include a low-sample warning because the historical archetype set is currently only `SNOW`, `CRSR`, and `DELL`.
- Do not hardcode today’s tickers as output; only hardcode archetype baselines and scoring weights.

## Acceptance Criteria

- Observable behavior: Morning Brief has a dedicated **Pattern Watch** section.
- Observable behavior: all current candidates matching SNOW/CRSR/DELL-style setups are gathered in that one section.
- Observable behavior: each row shows ticker, matched pattern, similarity %, case-based probability %, case-based upside %, flags, and reason.
- Observable behavior: empty state remains visible when no candidates qualify.
- Observable behavior: endpoint works without AI synthesis.
- Tests: API endpoint returns expected shape for mocked SNOW-like, CRSR-like, DELL-like candidates.
- Tests: empty data returns `data_available: true`, `count: 0`, and an empty list, not a 404 or UI disappearance.
- Tests: frontend/API types include the new fields.
- Documentation: method note clearly says probabilities are low-sample case-based estimates.

## Verification Plan

- `pytest dashboard/api/tests/test_endpoints.py -q -k 'pattern_watch'`
- `pytest tests/test_pipeline_defects.py -q -k 'pattern_watch or snow or crsr or dell'`
- `cd dashboard/frontend && npm run build`
- `python3 -m py_compile dashboard/api/main.py`
- Manual check:
  - Start dashboard.
  - Open Morning Brief.
  - Confirm **Pattern Watch** section is visible.
  - Confirm empty state is visible if endpoint returns zero candidates.
  - Mock or seed one SNOW-like row and confirm it appears with `SNOW` pattern.

## QA Notes

- Test scenarios:
  - SNOW-like pre-earnings catalyst candidate.
  - CRSR-like catalyst price expansion candidate.
  - DELL-like large-cap early momentum candidate.
  - Mixed candidate with both earnings and momentum evidence.
  - Empty dataset.
- Edge cases:
  - Candidate appears in both `watch_setup` and `candidate_snapshots`; deduplicate by ticker.
  - Missing price or missing earnings date.
  - Existing thesis is absent.
  - Blacklisted ticker should not appear.
- Regression risks:
  - Overstating probability from tiny sample size.
  - Hiding the section when no rows exist.
  - Confusing Pattern Watch with Hot Entry or a buy signal.

## Launch / Release Notes

- User-facing change summary:
  - Adds a Morning Brief **Pattern Watch** section for tickers resembling prior SNOW/CRSR/DELL-style breakout setups.
- Operational notes:
  - Works with no-AI daily pipeline if `catalyst_scores` / candidate snapshots are populated.
  - Better historical calibration should be added later from full-universe replay data.
- Rollback notes:
  - Remove the frontend section and `/api/pattern-watch` endpoint.

## Post-Launch Validation

- What to monitor:
  - Number of candidates shown per day.
  - Whether Telegram and dashboard agree on event-queue candidates.
  - Forward 1d/5d/10d/20d returns of displayed Pattern Watch names.
- How success will be confirmed:
  - User can find all SNOW/CRSR/DELL-like setups in one section after each daily pipeline run.
- Follow-up decision date: 2026-06-12

## Handoff Notes

The first implementation should be pragmatic and transparent. It is acceptable to use a simple weighted similarity model initially, as long as the UI clearly labels the result as case-based and low-sample.

Suggested initial archetype features:

- `SNOW`: earnings soon, earnings score high, options score high, volume score high, technical score high, low squeeze evidence.
- `CRSR`: `EARLY_MOMENTUM_BREAKOUT`, `CATALYST_PRICE_EXPANSION`, catalyst/news tag, optional elevated short interest.
- `DELL`: `EARLY_MOMENTUM_BREAKOUT`, large dollar volume, technical strength, continuation after recent 5d price expansion.

Future follow-up: replace the hardcoded archetype baselines with a real historical replay table once enough examples have been collected.

## Implementation Notes

- **Shipped commit:** 143fbc2 — "Add pattern watch dashboard section"
- **Backend endpoint:** `GET /api/pattern-watch` (dashboard/api/main.py)
  - Aggregates catalyst_scores and candidate_snapshots; scores each ticker against SNOW/CRSR/DELL archetypes
  - Snapshot-only fallback for tickers not yet in today's catalyst_scores
  - Returns `data_available: true` with `data: []` when no candidates qualify; never hides the section
- **Frontend section:** `PatternWatchPanel` in `dashboard/frontend/src/pages/HomePage.tsx`
  - Title: "Pattern Watch", subtitle: "Setups resembling SNOW / CRSR / DELL case studies"
  - Shows matched pattern badge, flags, upside %, probability %, similarity %
  - Labeled "case-based · low sample" — not a buy signal
- **Helper module:** `utils/pattern_watch.py` — SNOW/CRSR/DELL scorers + archetype baselines
- **API types:** `PatternWatchItem`, `PatternWatchResponse` in `dashboard/frontend/src/lib/api.ts`
- **Compatibility:** recognises `fresh_catalyst_breakout`, `catalyst_price_expansion`, `early_momentum_breakout`, and `NEWS_CATALYST` (TRD-018) selection_reason rows
- **Tests:** 9 pattern_watch tests in `dashboard/api/tests/test_endpoints.py` — all pass
- **QA fix:** `c9ac3f5` snapshot-only candidate fix (NEWS_CATALYST source routing)
