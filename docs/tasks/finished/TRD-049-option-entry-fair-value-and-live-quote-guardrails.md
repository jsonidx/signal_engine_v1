# Task: Option Entry Fair Value and Live Quote Guardrails

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: execution
Category: options
Risk: trading-logic
Effort: M
Target Release: options-stack-v1
Due Date: TBD
Dependencies: TRD-021, TRD-031, TRD-046, TRD-048
Blocked By: none
Links: `utils/option_entry_guardrail.py`, `utils/option_candidates.py`, `dashboard/api/main.py`
Success Metric: option recommendations can determine not only which contract is preferred, but whether the current quoted market is fresh, fair, and actionable enough to enter at the recommended price.

## Problem Statement

The system had contract selection, structure policy, entry guidance, and PM/risk
policy — but still lacked the final buy-side micro-execution layer that answers:
is the current quote fresh enough to trust, is the market too wide or unstable to
enter, is the option too expensive relative to fair-value guidance, and should the
user enter now, reprice, reduce size, or skip.

## Objective

Add a deterministic entry guardrail layer that evaluates quote freshness,
fair-value entry band, and live market quality before confirming that a trade
is actionable right now.

## Non-Goals

- Do not add broker routing or order submission.
- Do not require tick-level market microstructure data.
- Do not let an LLM decide whether a quote is fair.

## Implementation Notes (2026-06-06)

### Files created / changed

- `utils/option_entry_guardrail.py` (new) — `compute_entry_guardrail()` produces:
  - `entry_action` — one of `enter_now` / `enter_if_repriced` / `reduce_size` /
    `skip_for_now`, derived from spread quality, overpay vs FV band, and quote
    freshness.
  - `quote_freshness_label` and `quote_age_seconds` (populated from IBKR
    timestamp when available; defaults to `unknown` for yfinance rows).
  - `fair_value_entry_low` / `fair_value_entry_high` — bounded band around mid
    using spread tier and IV-richness adjustment.
  - `entry_overpay_pct` — how far current mid exceeds the FV ceiling.
  - `market_quality_label` — `tight` / `acceptable` / `wide` / `stale`.
  - `live_guardrail_reason` — human-readable explanation of the action.
  - Hard blocks on stale IBKR quotes (>300 s), crossed markets, and IV-rich
    setups that exceed the risk policy's IV threshold.
- `utils/option_candidates.py` — `get_option_candidates()` calls
  `compute_entry_guardrail()` and attaches fields to each candidate.
- `dashboard/api/main.py` — `_serialize_candidate()` serializes all guardrail
  fields; `EntryGuardrailBanner` component in the frontend consumes them.

### Verification

```
pytest -q tests/test_option_entry_guardrail.py
# 429 passed (options-stack suite)

cd dashboard/frontend && npx vitest run \
  src/pages/tests/TickerPage.option-candidates.test.tsx \
  src/pages/tests/OptionsPage.test.tsx
# 70 passed
```

## QA Result: PASS
