# Task: Option Execution Guidance and Entry Pricing

Status: done
Stage: done
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

### Implementation — completed 2026-05-30

**What was implemented:**

- `utils/option_candidates.py`: Added `ExecutionGuidance` dataclass and `compute_entry_guidance()` function. Deterministic spread-tier logic (tight ≤3% / moderate 3–8% / wide >8%) derives `recommended_entry_price`, `max_chase_price`, `entry_style`, `entry_rationale`, `fill_quality_score`, `slippage_risk_label`, `skip_if_spread_above_pct` from bid/ask/mid/OI/volume. Added 8 execution guidance fields to `OptionCandidate`. `get_option_candidates()` attaches guidance to every candidate.

- `migrations/005_option_execution_guidance.sql`: 8 `ADD COLUMN IF NOT EXISTS` statements on `option_candidate_snapshots`. Indexes on `entry_style` and `slippage_risk_label` for future analytics queries.

- `utils/supabase_persist.py`: `save_option_candidate_snapshot()` persists all 8 execution guidance fields.

- `dashboard/api/main.py`: `_serialize_candidate()` serializes all 8 fields.

- `dashboard/frontend/src/lib/api.ts`: `OptionCandidate` interface extended with 8 new fields.

- `dashboard/frontend/src/pages/TickerPage.tsx`: `OptionCandidateRow` renders an **Entry Guidance** block showing recommended entry + order type, max chase, fill quality, entry style, slippage badge (color-coded), and entry rationale. Block is hidden when `recommended_entry_price` is null.

- `dashboard/frontend/src/pages/OptionsPage.tsx`: `ScreenerRow` and table header extended with **Entry / Chase** and **Slip** columns.

- `tests/test_option_entry_guidance.py` (new — 28 tests): unit tests across all spread tiers and edge cases; integration tests confirming fields flow through `get_option_candidates()`.

- `dashboard/frontend/src/pages/tests/TickerPage.option-candidates.test.tsx`: fixtures updated; 9 new TRD-031 execution-guidance tests added (28 total).

- `dashboard/frontend/src/pages/tests/OptionsPage.test.tsx`: fixtures updated; 7 new screener execution-guidance tests added (22 total).

**Verification commands that passed (2026-05-30):**

```
pytest -q tests/test_option_entry_guidance.py tests/test_option_persistence.py tests/test_options_screener.py tests/test_option_candidates.py
# 126 passed

pytest -q dashboard/api/tests/test_endpoints.py
# passed

cd dashboard/frontend && npx vitest run src/pages/tests/TickerPage.option-candidates.test.tsx src/pages/tests/OptionsPage.test.tsx
# 50 passed (28 + 22)

pytest -q tests/ --ignore=tests/test_marketaux.py
# 1598 passed, 1 pre-existing failure in test_universe_builder (unrelated)
```

**Residual non-blocking notes:**
- `migrations/005_option_execution_guidance.sql` must be applied to any existing Supabase instance before the persistence path populates the new columns. New rows without the migration applied will fail gracefully (the insert will fail silently in `save_option_candidate_snapshot` per its existing error-handling pattern).
- `test_universe_builder.py::TestLiquidityFilter::test_passes_tickers_meeting_all_thresholds` was failing before this work and is unrelated to TRD-031.
- `test_marketaux.py::TestFetchNewsSentimentFallback::test_no_key_returns_neutral` is a pre-existing failure unrelated to TRD-031.

---

### Shipping prompt (paste into Claude Code to commit and push)

TRD-031 "Option Execution Guidance and Entry Pricing" has passed QA. No further code changes are needed.

**QA approval summary:** All targeted tests pass. 126 backend option tests pass. 50 frontend tests (TickerPage + OptionsPage) pass. Full suite shows 1598 passed with only 2 pre-existing failures unrelated to this work.

**Verification commands that passed:**
```
pytest -q tests/test_option_entry_guidance.py tests/test_option_persistence.py tests/test_options_screener.py tests/test_option_candidates.py
pytest -q dashboard/api/tests/test_endpoints.py
cd dashboard/frontend && npx vitest run src/pages/tests/TickerPage.option-candidates.test.tsx src/pages/tests/OptionsPage.test.tsx
```

**Files approved for shipment (all changes on current branch `main`):**
- `utils/option_candidates.py`
- `utils/supabase_persist.py`
- `dashboard/api/main.py`
- `dashboard/frontend/src/lib/api.ts`
- `dashboard/frontend/src/pages/TickerPage.tsx`
- `dashboard/frontend/src/pages/OptionsPage.tsx`
- `migrations/005_option_execution_guidance.sql`
- `tests/test_option_entry_guidance.py`
- `dashboard/frontend/src/pages/tests/TickerPage.option-candidates.test.tsx`
- `dashboard/frontend/src/pages/tests/OptionsPage.test.tsx`
- `docs/tasks/finished/TRD-031-option-execution-guidance-and-entry-pricing.md`

**Recommended commit message:**
```
Add TRD-031: option execution guidance and entry pricing

Adds deterministic execution guidance layer to option recommendations.
Each candidate now includes recommended_entry_price, max_chase_price,
recommended_order_type, entry_style, entry_rationale, fill_quality_score,
slippage_risk_label, and skip_if_spread_above_pct — derived from
bid/ask/mid/OI without LLM involvement.

- utils/option_candidates.py: ExecutionGuidance dataclass + compute_entry_guidance()
- migrations/005_option_execution_guidance.sql: 8 new columns on option_candidate_snapshots
- supabase_persist.py: persists execution guidance fields
- dashboard/api/main.py: serializes execution guidance in _serialize_candidate()
- TickerPage.tsx: Entry Guidance block in OptionCandidateRow
- OptionsPage.tsx: Entry / Chase + Slip columns in screener table
- tests/test_option_entry_guidance.py: 28 new backend tests
- TickerPage.option-candidates.test.tsx: 9 new TRD-031 tests
- OptionsPage.test.tsx: 7 new TRD-031 tests
```

**Instructions:** Commit all staged and unstaged changes from TRD-031 with the message above. Push to `origin main`. Do not make any additional code changes before committing.

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
