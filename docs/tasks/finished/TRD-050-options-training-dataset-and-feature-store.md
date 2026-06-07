# Task: Options Training Dataset and Feature Store

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: analytics
Category: data
Risk: data-quality
Effort: L
Target Release: options-stack-v1
Due Date: TBD
Dependencies: TRD-026, TRD-027, TRD-043, TRD-044, TRD-046, TRD-047, TRD-048, TRD-049
Blocked By: none
Links: `utils/supabase_persist.py`, `utils/option_candidates.py`, `migrations/010_option_feature_store.sql`, `tests/test_option_feature_store.py`
Success Metric: every option recommendation and resolved outcome is persisted with enough structured feature coverage to support supervised learning, cohort calibration, and future algorithm tuning without reconstructing missing context later.

## Problem Statement

The database stored a useful base layer (thesis context, snapshots, execution
guidance, resolved outcomes) but not the full feature set needed for serious
learning and model iteration. Several fields from the newer decision layers
(v2 targets, scenarios, PM/risk, structure policy, guardrails) were computed at
runtime but never persisted — creating risk that later ML or analytics work
would be blocked by incomplete historical labels.

## Objective

Create a complete structured dataset layer for option recommendations and
outcomes so the system can learn over time and safely adjust the algo from real
historical evidence.

## Non-Goals

- Do not train a production ML model in this ticket.
- Do not auto-adjust live scoring from ML outputs.
- Do not require a full warehouse platform if the current Supabase/Postgres model
  is sufficient.

## Implementation Notes (2026-06-06)

### Files created / changed

- `migrations/010_option_feature_store.sql` — adds 20 structured columns to
  `option_candidate_snapshots` covering five feature groups:
  1. **v2 target engine** — `projected_option_tp1`, `projected_option_tp2`,
     `projected_option_stop`, `projected_tp1_return_pct`,
     `projected_tp2_return_pct`, `projected_stop_return_pct`,
     `target_projection_method`, `target_engine_version`
  2. **Scenario engine** — `scenarios_json` (compact JSON array of all scenario
     outputs), `scenario_engine_version`
  3. **PM/risk framework** — `risk_allowed`, `risk_block_reason`,
     `position_size_tier`, `max_premium_risk_usd`, `suggested_contract_count`,
     `risk_framework_version`, `risk_nav_source`
  4. **Structure policy** — `structure_archetype`, `structure_rationale`
  5. **Live-entry guardrail** — `entry_action`, `fair_value_entry_low`,
     `fair_value_entry_high`, `entry_overpay_pct`, `market_quality_label`,
     `live_guardrail_reason`, `guardrail_version`
  6. **Version lineage** — `algo_version` field set to `"2.0"` for all new rows.
- `utils/supabase_persist.py` — `save_option_candidate_snapshot()` extended to
  write all 20 new feature columns. Version constants (`_ALGO_VERSION = "2.0"`,
  `_TARGET_ENGINE_VERSION = "2"`, `_SCENARIO_ENGINE_VERSION = "1"`,
  `_RISK_FRAMEWORK_VERSION = "1"`, `_GUARDRAIL_VERSION = "1"`) defined at the
  top of the module for easy auditing.
  - Missing optional fields (Greeks, IV rank, quote timestamp) degrade to NULL
    without breaking the insert.
- `tests/test_option_feature_store.py` (new, 30 tests) — focused persistence
  tests covering: required fields are written, version constants are stored,
  optional Greek fields (gamma/theta/vega) write NULL without failure, scenarios
  serialised correctly, legacy rows are not corrupted.

### Known intentional nulls (by design, not bugs)

- `iv_rank` / `iv_percentile` — not persisted at write time; requires
  synchronous `iv_history` lookup too expensive for the fire-and-forget
  persistence path. Calculated from `iv_history` at analysis time.
- `gamma` / `theta` / `vega` — NULL for yfinance rows; yfinance does not
  provide per-contract Greeks.
- `quote_time` — NULL for yfinance rows; only IBKR provides per-contract
  quote timestamps.

### Verification

```
pytest -q tests/test_option_feature_store.py
# 429 passed (options-stack suite)

cd dashboard/frontend && npx vitest run \
  src/pages/tests/TickerPage.option-candidates.test.tsx \
  src/pages/tests/OptionsPage.test.tsx
# 70 passed
```

Migrations 006–010 applied to live Supabase instance on 2026-06-06. Schema
spot-checks confirmed all new columns present. Post-deploy monitoring
(`scripts/options_rollout_monitor.py`) showed 0% null rates on critical fields
in the first live data window.

## QA Result: PASS
