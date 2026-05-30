# Task: Option Execution Guidance and Entry Pricing

Status: proposed
Stage: ready
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: dashboard
Category: ux
Risk: trading-logic
Effort: M
Target Release: backlog
Due Date: TBD
Dependencies: TRD-022, TRD-023, TRD-026, TRD-028, TRD-029
Blocked By: none
Links: `docs/backlogs/options-execution-gap-analysis.md`, `utils/option_candidates.py`, `dashboard/api/main.py`, `dashboard/frontend/src/pages/TickerPage.tsx`, `dashboard/frontend/src/pages/OptionsPage.tsx`
Success Metric: option recommendations include execution-ready entry guidance so a user can act without manually inferring an entry price from raw bid/ask quotes.

## Problem Statement

The current options recommendation system is strong at selecting contracts and
planning exits, but it still leaves the user to infer the actual entry terms
from bid/ask/mid.

That means the product can identify a good contract while still failing to
answer the practical execution question:

`What exact option price should I try to pay, with what order style, and when
should I stop chasing the trade?`

## User Impact

Users get a high-quality contract recommendation, but the trade is still not
fully actionable. That slows decision-making, increases manual guesswork, and
raises the risk of poor fills or overpaying on wide-spread contracts.

## Objective

Add execution-guidance fields to the options recommendation flow so both the
ticker page and options overview can show:

- recommended option entry price
- order type guidance
- max chase price
- entry rationale
- basic fill/slippage quality context

## Proposed Solution

Extend the deterministic option recommendation layer with an execution-guidance
sub-model derived from:

- bid / ask / mid
- spread %
- liquidity quality
- strategy preset
- thesis urgency / conviction

The new fields should be deterministic first. The LLM may explain them, but
must not invent unsupported entry prices.

Persist the execution-guidance fields in the option snapshot dataset so later
resolution/accuracy analysis can review whether the entry guidance improved
outcomes.

## Scope

Files or modules likely affected:

- `utils/option_candidates.py`
- `dashboard/api/main.py`
- `dashboard/frontend/src/lib/api.ts`
- `dashboard/frontend/src/pages/TickerPage.tsx`
- `dashboard/frontend/src/pages/OptionsPage.tsx`
- `utils/supabase_persist.py`
- `migrations/004_option_candidate_snapshots_and_outcomes.sql` or follow-up migration if required
- targeted tests in `tests/` and `dashboard/frontend/src/pages/tests/`

## Non-Goals

- Do not add order routing or trade placement.
- Do not add autonomous position sizing.
- Do not redesign the existing contract-selection scoring model beyond what is minimally required for execution guidance.
- Do not let the LLM generate free-form unsupported entries.

## Constraints

- Keep entry guidance deterministic and explainable.
- Use quote-aware fields from the actual candidate.
- Label recommendation fields clearly as guidance, not guaranteed fills.
- No secrets or generated artifacts in git.

## Acceptance Criteria

- Observable behavior: ticker-page option recommendations show:
  - bid / ask / mid
  - recommended entry price
  - recommended order type
  - max chase price
  - entry rationale
- Observable behavior: options overview rows/cards show a compact execution summary including recommended entry and max chase.
- Observable behavior: suppressed / no-trade states remain intact when execution guidance is unavailable or the contract is not actionable.
- Observable behavior: snapshot persistence stores the execution-guidance fields for later analytics.
- Tests:
  - deterministic entry guidance is computed from quote/liquidity inputs
  - wide-spread contracts produce more conservative entry guidance or no-trade behavior as intended
  - API serialization includes the new entry fields
  - ticker page and options page render the new execution-guidance fields cleanly
- Documentation:
  - concise comments or task notes clarify that the fields are execution guidance, not guaranteed fills

## Verification Plan

- `make verify`
- Targeted tests:
  - backend option-candidate / persistence / screener tests
  - frontend ticker/options page tests
- `make verify-full` only if live integration behavior changed materially
- manual browser verification of ticker page and options overview

## QA Notes

- Test scenarios:
  - tight spread liquid contract
  - moderate spread contract
  - wide-spread low-quality contract
  - bullish and bearish examples
- Edge cases:
  - missing bid/ask with only mid available
  - zero or null OI / volume
  - stale or partial chain source
- Regression risks:
  - over-aggressive entry recommendations
  - UI confusion between `mid` and `recommended entry`
  - persistence/schema drift for new execution fields

## Launch / Release Notes

- User-facing change summary: option recommendations now include execution-ready entry guidance instead of requiring users to infer an entry from raw quotes.
- Operational notes: guidance is deterministic and quote-based; it does not place trades.
- Rollback notes: hide the execution-guidance fields and revert persistence additions.

## Post-Launch Validation

- What to monitor:
  - frequency of no-trade outcomes due to spread quality
  - distribution of recommended-entry vs mid
  - later outcome quality by entry-style bucket
- How success will be confirmed:
  - users can act on a recommendation without manually deriving an entry price
  - persisted data supports later evaluation of entry guidance quality
- Follow-up decision date: after first live review cycle with persisted outcomes

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-031, "Option Execution Guidance and Entry Pricing," in this repo.

Goal:
- Make the current options recommendations execution-ready by adding deterministic entry guidance.

Required outcome:
- Both the ticker page and options overview should show:
  - recommended entry price
  - recommended order type
  - max chase price
  - entry rationale
- Persist those fields for later outcome analysis.

Scope:
- `utils/option_candidates.py`
- `dashboard/api/main.py`
- `dashboard/frontend/src/lib/api.ts`
- `dashboard/frontend/src/pages/TickerPage.tsx`
- `dashboard/frontend/src/pages/OptionsPage.tsx`
- `utils/supabase_persist.py`
- snapshot schema / migration if needed
- focused backend and frontend tests

Implementation guidance:
- Derive entry guidance from:
  - bid / ask / mid
  - spread %
  - liquidity quality
  - strategy preset
  - conviction / urgency if already available
- Keep the logic deterministic and explainable.
- Prefer fields like:
  - `recommended_entry_price`
  - `recommended_order_type`
  - `max_chase_price`
  - `entry_style`
  - `entry_rationale`
  - optionally `fill_quality_score` or `slippage_risk_label` if practical in scope
- Do not let the LLM invent entries not backed by the deterministic layer.
- Clearly distinguish `mid` from `recommended entry`.

Non-goals:
- no order placement
- no autonomous sizing
- no broad scoring redesign

Tests and verification:
- add focused backend tests for entry-guidance logic and API serialization
- add focused frontend tests for ticker/options page rendering
- run the tests you add
- run `make verify` if practical

Risk constraints:
- this touches trade decision support, so be conservative
- if a contract is not actionable due to spread/liquidity, prefer explicit caution or no-trade behavior over forced precision

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
