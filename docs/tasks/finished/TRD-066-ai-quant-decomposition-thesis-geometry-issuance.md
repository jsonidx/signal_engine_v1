# Task: AI Quant Decomposition into Thesis, Geometry, and Issuance Layers

Status: completed
Stage: done
Type: feature
Priority: P0
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: trading-logic
Category: automation
Risk: trading-logic
Effort: L
Target Release: ai-quant-v2
Due Date: TBD
Dependencies: TRD-057, TRD-058, TRD-065
Blocked By: none
Links: `ai_quant.py`, `utils/ticker_selector.py`, `reports/quarterly_reviews/2026-Q2-win-rate-deep-dive.md`, `reports/quarterly_reviews/2026-Q2-pm-review-extension.md`
Success Metric: AI Quant stops behaving like a single all-in-one thesis generator and becomes a layered issuance engine with cleaner directional outputs, deterministic execution geometry, and explicit suppression states.

## Problem Statement

From a hedge-fund PM perspective, `ai_quant.py` currently tries to do too much in one step:

- directional thesis generation
- conviction assignment
- position sizing
- entry / stop / target geometry
- event-risk interpretation
- analog interpretation
- expected-move output
- issuance decision

That design is useful for research synthesis but too loose for institutional trade issuance. It allows the LLM to control both forecast and monetization geometry at once, which creates avoidable variance. It also treats `NEUTRAL` as a first-class thesis outcome, even though the quarterly review showed that `NEUTRAL` pollutes accuracy analysis and should function as a suppression state rather than a trade idea.

## User Impact

- Directionally correct names can still become poor trades because the model sets weak geometry.
- `NEUTRAL` outputs pollute the thesis dataset and distort win-rate analysis.
- PM review cannot cleanly distinguish:
  - a suppressed name
  - a forecast with no executable setup
  - a real tradable thesis
- Final multi-name prioritization is weaker than it should be because conviction and narrative quality are mixed with execution decisions.

## Objective

Redesign `ai_quant` into three explicit layers:

1. directional thesis layer
2. deterministic execution geometry layer
3. issuance / suppression gate

## Proposed Solution

### Layer 1: Directional thesis

The LLM should focus on:

- `direction`: `BULL` or `BEAR`
- qualitative rationale
- primary vs counter scenario
- risks and catalysts
- confidence context
- explanation of why the directional prior should or should not deviate from `prob_combined`

The LLM should no longer be the primary source of final trade geometry.

### Layer 2: Deterministic execution geometry

After directional thesis generation, a deterministic layer should set or normalize:

- entry band
- stop loss
- T1 / T2
- size caps
- event-risk constraints

This layer should use:

- market regime
- lane / tradability profile
- realized volatility
- liquidity tier
- event timing
- explicit PM geometry rules from the quarterly review

### Layer 3: Issuance / suppression gate

The final publishing step should decide whether the name becomes:

- `ACTIVE_THESIS`
- `WATCH_ONLY`
- `SUPPRESSED`
- `NO_TRADE`

`NEUTRAL` should not remain a first-class tradable thesis category.

## Scope

Files or modules likely affected:

- `ai_quant.py`
- `utils/ticker_selector.py`
- `utils/supabase_persist.py`
- `schema.sql`
- `migrations/`
- `docs/INTERNALS.md`
- `tests/test_ai_quant_schema.py`
- `tests/test_ticker_selector.py`

## Non-Goals

- Do not redesign the whole signal-collection stack here.
- Do not add paid vendor dependencies.
- Do not implement broad portfolio construction logic.
- Do not widen AI universe coverage in this ticket.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.
- Preserve deterministic pre-screening and conflict resolver behavior unless explicitly improved within scope.
- Maintain backward compatibility for stored thesis records where feasible, using additive schema changes or compatibility shims.

## Acceptance Criteria

- Observable behavior:
  - `ai_quant` no longer treats `NEUTRAL` as a normal tradable thesis state.
  - Directional synthesis is separated from deterministic trade geometry.
  - Geometry is normalized or produced by deterministic rules after the LLM response.
  - Final output explicitly distinguishes tradable theses from suppressed or watch-only states.
  - Multi-name ranking can use issuance quality, not just conviction.
- Tests:
  - Add targeted tests for:
    - suppression-state handling
    - geometry normalization
    - backward compatibility for existing thesis records where needed
    - selector behavior on the new issuance states
- Documentation:
  - `docs/INTERNALS.md` documents the three-layer architecture and new issuance states.

## Verification Plan

- Targeted tests:
  - `pytest -q tests/test_ai_quant_schema.py tests/test_ticker_selector.py`
- Additional verification:
  - run relevant `ai_quant.py` dry-run or controlled-path checks if feasible
  - inspect sample outputs to confirm `NEUTRAL` no longer behaves like a live thesis

## QA Notes

- Test scenarios:
  - clear bullish thesis that becomes `ACTIVE_THESIS`
  - valid direction but poor geometry that becomes `WATCH_ONLY`
  - hard-blocked name that becomes `SUPPRESSED`
  - thin / contradictory setup that becomes `NO_TRADE`
- Edge cases:
  - regime caps
  - earnings-risk caps
  - force-ai override path
  - old cached thesis records
- Regression risks:
  - broken cache compatibility
  - inconsistent state transitions
  - overly rigid geometry harming otherwise good setups

## Launch / Release Notes

- User-facing change summary: AI Quant is now a layered issuance engine with clearer suppression and execution handling.
- Operational notes: compare thesis-state distribution, geometry consistency, and selected-name quality before and after rollout.
- Rollback notes: revert to the prior all-in-one thesis-generation path if necessary.

## Post-Launch Validation

- What to monitor:
  - state distribution (`ACTIVE_THESIS`, `WATCH_ONLY`, `SUPPRESSED`, `NO_TRADE`)
  - geometry consistency
  - directional win rate versus issued-thesis win rate
  - reduction in `NEUTRAL` pollution
- How success will be confirmed:
  - cleaner issuance dataset and better alignment between signal quality and execution quality
- Follow-up decision date:
  - after 2-4 weeks of daily runs

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-066: redesign ai_quant into three explicit layers: directional thesis, deterministic execution geometry, and issuance/suppression gating.

Goal:
- Make ai_quant behave less like an all-in-one research narrator and more like a structured trade issuance engine.
- Remove NEUTRAL as a first-class tradable thesis state.

Scope:
- ai_quant.py
- utils/ticker_selector.py
- utils/supabase_persist.py
- schema.sql
- migrations/
- docs/INTERNALS.md
- tests/test_ai_quant_schema.py
- tests/test_ticker_selector.py

Required changes:
- Separate the LLM’s role from deterministic execution geometry.
- Have the LLM focus mainly on:
  - BULL/BEAR direction
  - rationale
  - primary/counter scenario
  - risks/catalysts
  - confidence context
- Add a deterministic post-LLM layer that sets or normalizes:
  - entry band
  - stop
  - T1 / T2
  - size caps
  - event-risk constraints
- Replace NEUTRAL-as-thesis with explicit issuance states such as:
  - ACTIVE_THESIS
  - WATCH_ONLY
  - SUPPRESSED
  - NO_TRADE
- Ensure selector / persistence logic handles the new states correctly.
- Preserve backward compatibility where feasible for existing stored records.

Non-goals:
- No full signal-stack redesign
- No paid data-vendor integrations
- No broad universe expansion inside this ticket

Constraints:
- Risk is trading-logic
- Preserve deterministic pre-screening and conflict-resolver intent
- Avoid broad refactors outside the listed files

Tests / verification:
- pytest -q tests/test_ai_quant_schema.py tests/test_ticker_selector.py
- run relevant ai_quant dry-run or controlled-path checks if feasible
- confirm sample outputs no longer treat NEUTRAL as a live tradable thesis
```
