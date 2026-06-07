# Task: Options Risk and Position Sizing Framework

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: portfolio
Category: risk
Risk: trading-logic
Effort: L
Target Release: options-stack-v1
Due Date: TBD
Dependencies: TRD-022, TRD-028, TRD-031, TRD-043, TRD-044
Blocked By: none
Links: `utils/option_risk.py`, `dashboard/api/main.py`, `migrations/007_option_risk_sizing.sql`
Success Metric: option recommendations become portfolio-aware and risk-bounded, with explicit sizing, event-risk gating, volatility regime filters, and exit hierarchy guidance that make the system materially more actionable for real trading decisions.

## Problem Statement

The options stack was improving at thesis selection, contract selection, entry
guidance, and projected exits — but still lacked the PM/risk layer that
determines whether a trade should be taken at all, how much capital should be
deployed, and how the trade fits inside the broader options book.

## User Impact

Without a portfolio-aware risk framework:

- users still needed to manually decide whether the trade size is appropriate
- contract recommendations could be actionable in isolation but unsafe in a book
- high-IV and event-heavy trades could be overrepresented
- the product could not enforce consistent discipline across trades

## Objective

Add a deterministic PM/risk framework for options that answers: should this
trade be allowed, how much premium can be risked, how many contracts are
appropriate, whether event/IV conditions make the setup unattractive, and how
the exit hierarchy should behave once the trade is live.

## Non-Goals

- Do not add live order routing or broker integration.
- Do not implement autonomous portfolio rebalancing.
- Do not let an LLM choose position size.

## Implementation Notes (2026-06-06)

### Files created / changed

- `utils/option_risk.py` (new) — deterministic PM/risk layer. Computes:
  - `risk_allowed` / `risk_block_reason` (hard blocks for event-risk, IV-rich,
    wide-spread conditions)
  - `max_premium_risk_usd` and `suggested_contract_count` from NAV proxy and
    per-trade premium-at-risk caps keyed to `position_size_tier`
  - `position_size_tier` (`full` / `standard` / `reduced` / `skip`)
  - `event_risk_policy` from days-to-earnings proximity
  - `iv_regime_label` from IV vs 30-day average
  - `portfolio_concentration_warning`
  - `exit_hierarchy` list with thesis-invalidation, option-target, time-stop,
    event-exit, and scale-out guidance
  - `risk_nav_source` — tracks whether NAV came from `portfolio_settings` DB
    row or a conservative proxy default
- `dashboard/api/main.py` — `_build_portfolio_context()` reads `cash_eur` from
  `portfolio_settings`; `_serialize_candidate()` includes all PM/risk fields.
- `migrations/007_option_risk_sizing.sql` — adds risk sizing columns to
  `option_candidate_snapshots`.

### Verification

```
pytest -q tests/test_option_risk.py
# 429 passed (options-stack suite)

cd dashboard/frontend && npx vitest run \
  src/pages/tests/TickerPage.option-candidates.test.tsx \
  src/pages/tests/OptionsPage.test.tsx
# 70 passed
```

## QA Result: PASS
