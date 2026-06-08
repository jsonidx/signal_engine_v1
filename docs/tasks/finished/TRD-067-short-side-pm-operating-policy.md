# Task: Short-Side PM Operating Policy and Gating

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: trading-logic
Category: research
Risk: trading-logic
Effort: M
Target Release: pm-policy-v1
Due Date: TBD
Dependencies: TRD-058, TRD-066
Blocked By: none
Links: `ai_quant.py`, `utils/ticker_selector.py`, `reports/quarterly_reviews/2026-Q2-win-rate-deep-dive.md`, `reports/quarterly_reviews/2026-Q2-pm-review-extension.md`
Success Metric: short theses are no longer treated as routine throughput; they are issued only under stricter policy conditions, reducing low-quality short exposure.

## Problem Statement

The quarterly review shows the engine is structurally stronger on longs than shorts. From a PM perspective, that means the short side should not be treated as a normal symmetric output stream.

Current risk:

- weak shorts can be issued simply because a name is not bullish
- shorts may enter the same funnel as longs without enough catalyst discipline
- bearish throughput can dilute both hit rate and capital efficiency

## User Impact

- More low-quality short trades
- Higher event-risk and squeeze-risk exposure
- Reduced trust in bearish theses
- Weaker overall portfolio quality if shorts are treated as routine output

## Objective

Create an explicit short-side operating policy so that bearish theses are gated more strictly than bullish theses.

## Proposed Solution

Introduce a PM policy layer for `BEAR` theses with explicit requirements.

Recommended PM rules:

- Shorts are `special situations`, not default throughput
- Require all or most of:
  - conviction `>= 3`
  - stronger `prob_combined` / agreement threshold than longs
  - clear catalyst or regime justification
  - relative weakness confirmation
  - no obvious squeeze-risk conflict
- Optionally downgrade some otherwise-bearish names to:
  - `WATCH_ONLY`
  - `NO_TRADE`

Add a separate short-policy scorecard so PM review can judge:

- short issuance count
- short win rate
- short suppressions
- main rejection reasons

## Scope

Files or modules likely affected:

- `ai_quant.py`
- `utils/ticker_selector.py`
- `utils/supabase_persist.py`
- `dashboard/api/main.py`
- `docs/INTERNALS.md`
- `tests/test_ticker_selector.py`

## Non-Goals

- Do not eliminate shorting entirely.
- Do not redesign the entire screener.
- Do not change long-side thresholds unless necessary for symmetry separation.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.
- Keep the short policy deterministic and auditable.

## Acceptance Criteria

- Observable behavior:
  - bearish theses are subject to stricter issuance rules than bullish theses
  - some bearish candidates are explicitly downgraded or suppressed under the new policy
  - short-side reasons are recorded clearly enough for PM review
- Tests:
  - add targeted tests for bearish gating behavior
  - add tests that bullish logic remains unchanged unless explicitly intended
- Documentation:
  - `docs/INTERNALS.md` documents the short-side policy and thresholds

## Verification Plan

- Targeted tests:
  - `pytest -q tests/test_ticker_selector.py tests/test_ai_quant_schema.py`

## QA Notes

- Test scenarios:
  - weak bearish candidate rejected
  - catalyst-backed bearish candidate accepted
  - high squeeze-risk bearish candidate downgraded
- Edge cases:
  - risk-off regime
  - counter-trend short in strong market
  - open-position force include
- Regression risks:
  - overblocking legitimate shorts
  - hidden coupling with long-side gates

## Launch / Release Notes

- User-facing change summary: short theses now follow a stricter PM issuance policy.
- Operational notes: monitor short issuance rate and post-change hit rate.
- Rollback notes: revert to prior symmetric gating if needed.

## Post-Launch Validation

- What to monitor:
  - short thesis count
  - short win rate
  - short rejection reasons
  - short-side drawdown behavior
- How success will be confirmed:
  - fewer low-quality shorts with no material loss of genuinely high-quality bearish setups
- Follow-up decision date:
  - after 2-4 weeks of data

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-067: add an explicit PM operating policy for the short side.

Goal:
- Stop treating BEAR theses as routine symmetric throughput.
- Require stricter conditions before a bearish thesis is issued as tradable.

Scope:
- ai_quant.py
- utils/ticker_selector.py
- utils/supabase_persist.py
- dashboard/api/main.py
- docs/INTERNALS.md
- tests/test_ticker_selector.py

Required changes:
- Add stricter deterministic gating for BEAR theses than for BULL theses.
- Require stronger quality conditions such as higher conviction / higher probability / clearer catalyst or relative weakness confirmation.
- Support explicit downgrades to WATCH_ONLY or NO_TRADE for weak bearish setups.
- Persist or expose enough metadata to review short-side issuance and rejection behavior separately.

Non-goals:
- Do not remove shorting entirely
- Do not redesign the whole screener

Constraints:
- Risk is trading-logic
- Keep the short policy deterministic and auditable

Tests / verification:
- pytest -q tests/test_ticker_selector.py tests/test_ai_quant_schema.py
```
