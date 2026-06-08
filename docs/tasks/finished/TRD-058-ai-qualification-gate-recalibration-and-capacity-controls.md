# Task: AI Qualification Gate Recalibration and Capacity Controls

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: trading-logic
Category: automation
Risk: trading-logic
Effort: M
Target Release: universe-v2
Due Date: TBD
Dependencies: TRD-055, TRD-056, TRD-057
Blocked By: none
Links: `config.py`, `utils/ticker_selector.py`, `ai_quant.py`, `reports/quarterly_reviews/2026-Q2-pm-review-extension.md`
Success Metric: AI synthesis capacity becomes adaptive and better aligned with thesis quality, increasing useful directional throughput without materially diluting win-rate quality.

## Problem Statement

The current AI lane is bounded by a hard cap of 5 tickers per run and qualification is mainly driven by `signal_agreement_score >= 0.60` or `prob_combined >= 0.55`, after suppression rules. From a PM perspective, that is a reasonable safety default but too rigid for a maturing research engine.

Some days, five names may be too many. Other days, five may be too few. A hard constant can under-sample strong days and over-sample weak days.

## User Impact

- Strong trading days may be underrepresented in the AI thesis set.
- Weak days may still consume the full daily AI budget.
- The system cannot cleanly distinguish between a sparse high-conviction day and a crowded high-opportunity day.

## Objective

Replace the purely static AI cap with a configurable, quality-aware capacity model while preserving strict deterministic gating.

## Proposed Solution

Recalibrate the AI selector so that capacity is dynamic within a safe range.

Recommended PM design:

- Keep the hard suppressions:
  - `skip_claude`
  - earnings blocks
  - acquisition-pin blocks
- Keep the directional quality gates, but make the AI lane more adaptive:
  - base gate remains agreement / `prob_combined`
  - add stronger ranking priority on directional quality and liquidity
  - allow daily AI capacity to float within a bounded range, for example `5-15`
- Consider separate thresholds:
  - normal threshold for longs
  - stricter threshold for bears
- Prefer a rule like:
  - if very few names qualify, do not force-fill to capacity
  - if many names clear a higher quality bar, allow more than 5

This is an execution-lane ticket only. The goal is to improve thesis throughput quality, not to broaden raw discovery.

## Scope

Files or modules likely affected:

- `config.py`
- `utils/ticker_selector.py`
- `ai_quant.py`
- `docs/INTERNALS.md`

## Non-Goals

- Do not broaden the discovery universe in this ticket.
- Do not change thesis prompt content.
- Do not alter target/stop normalization here.
- Do not remove hard suppressions.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.
- Preserve backward-compatible defaults where possible.
- Keep the selector deterministic.

## Acceptance Criteria

- Observable behavior:
  - AI capacity can float within a configured safe range instead of a single rigid fixed number.
  - Low-quality days do not force extra AI calls.
  - High-quality days can process more than the current cap when enough names clear the stronger gate.
  - Configuration clearly separates research-lane capacity from execution-lane AI capacity.
- Tests:
  - Add targeted selector tests for low-, medium-, and high-quality candidate sets.
  - Add tests for bear-thesis stricter gating if implemented.
- Documentation:
  - `docs/INTERNALS.md` documents the new AI-lane capacity and qualification behavior.

## Verification Plan

- Targeted tests:
  - `pytest -q tests/test_ticker_selector.py tests/test_ai_quant_schema.py`
- Additional verification:
  - run dry-run selection scenarios if feasible
  - confirm high-quality synthetic inputs can yield more than 5 selections while weak sets do not

## QA Notes

- Test scenarios: 0 qualifiers, 3 qualifiers, 8 strong qualifiers, 20 mixed qualifiers, strong-bear subset.
- Edge cases: always-include open positions, event queue additions, force_tickers overrides.
- Regression risks: silent jump in AI cost, weaker-name leakage into execution lane, unstable selection counts run to run.

## Launch / Release Notes

- User-facing change summary: AI thesis issuance becomes quality-aware rather than fixed-cap only.
- Operational notes: monitor call count, selection quality, and distribution of selected directions after launch.
- Rollback notes: revert to prior static cap and threshold constants.

## Post-Launch Validation

- What to monitor:
  - AI calls per day
  - selection count distribution
  - directional mix
  - win-rate by selection rank bucket
- How success will be confirmed:
  - more useful directional throughput without visible quality collapse
- Follow-up decision date:
  - after 2-4 weeks of outcome data

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-058: recalibrate the AI qualification gate and replace the rigid single daily cap with bounded adaptive capacity.

Goal:
- Keep AI synthesis selective.
- Allow stronger days to process more than the current fixed cap.
- Avoid forcing weak days to consume the full daily budget.

Scope:
- config.py
- utils/ticker_selector.py
- ai_quant.py
- docs/INTERNALS.md
- tests/test_ticker_selector.py

Required changes:
- Introduce config for bounded adaptive AI capacity, for example min/max or base/max slots.
- Preserve deterministic gating and existing hard suppressions.
- Keep agreement/probability as core gates, but allow stronger qualified sets to exceed the old hard cap when they clear the stronger bar.
- Do not force-fill weak days.
- If practical and well-supported by existing logic, add stricter bear gating than bull gating.

Non-goals:
- No discovery-universe expansion in this ticket
- No prompt changes
- No target/stop changes

Constraints:
- Risk is trading-logic
- Preserve backward-compatible defaults where possible
- Keep selector behavior deterministic and testable

Tests / verification:
- pytest -q tests/test_ticker_selector.py tests/test_ai_quant_schema.py
- run dry-run selection scenarios if feasible
- verify high-quality synthetic inputs can produce >5 selections without weak sets doing so
```
