# Task: Options Screener, Persistence, and Learning Roadmap

Status: proposed
Stage: ready
Type: product
Priority: P1
Severity: medium
Owner: Codex
Reviewer: Human
Product Area: dashboard
Category: options
Risk: trading-logic
Effort: XL
Target Release: backlog
Due Date: TBD
Dependencies: TRD-021, TRD-022, TRD-023, TRD-024
Blocked By: none
Links: `docs/tasks/new/TRD-020-ibkr-options-roadmap.md`, `dashboard/api/main.py`, `dashboard/frontend/src/pages/TickerPage.tsx`, `utils/option_candidates.py`, `utils/supabase_persist.py`
Success Metric: the system can rank the best option opportunities across top stock theses, persist recommendation snapshots and outcomes, and expose accuracy analytics that support iterative scoring improvements.

## Problem Statement

The current options work is ticker-page-centric: it can recommend option candidates for one ticker at a time. That solves contract selection for a known stock thesis, but it does not yet provide:

- a cross-ticker `best options to trade today` view
- historical persistence of option recommendations
- outcome tracking for recommended contracts
- an analytics surface to measure which presets, deltas, DTE windows, and liquidity rules actually work

Without persistence and outcomes, there is no reliable feedback loop for improving the option-scoring engine.

## User Impact

- Users can see a recommended contract on a single ticker page, but cannot compare the best option setups across the active watchlist.
- Recommended contracts are ephemeral; once the API cache expires, the recommendation is lost.
- The team cannot learn systematically from wins, losses, false positives, or contract-quality patterns.

## Objective

Build an options module that:

1. ranks the best option setups across already-selected ticker theses
2. persists recommendation snapshots and eventual outcomes
3. exposes accuracy and resolution analytics for option trading
4. supports a safe review loop where Claude can propose scoring changes from historical evidence

## Recommendation

Build the options system as a `second-stage screener` on top of the stock thesis engine.

Do:

1. use the existing ticker/thesis ranking as the first filter
2. evaluate option candidates only for that curated ticker set
3. persist recommendation snapshots
4. persist outcomes and resolution metrics
5. add a dedicated options tab in the dashboard and in `Resolution & Accuracy`

Do not:

- scan the raw full option universe before selecting tickers
- let the LLM search all contracts directly
- let Claude autonomously mutate production scoring logic

## Product Decision

### Best design

`Ticker thesis -> option candidate engine -> options screener ranking -> persistence -> analytics`

### Worse design

`Full option universe scan -> LLM guesses best contracts`

### Why

- The stock thesis remains the strongest source of directional signal.
- Option quality is largely about selecting the right contract for a valid underlying setup.
- IBKR line limits and chain complexity make full-universe first-pass screening inefficient.
- Historical learning is far cleaner when each recommendation can be traced back to a stock thesis, preset, and contract feature set.

## Proposed Architecture

### 1. Recommendation snapshot persistence

Persist every recommended option candidate (and every suppressed/no-trade decision when useful) into Supabase.

Recommended table:

- `option_candidate_snapshots`

Suggested fields:

- `id`
- `created_at`
- `run_date`
- `ticker`
- `thesis_id`
- `thesis_date`
- `direction`
- `conviction`
- `time_horizon`
- `chain_source`
- `underlying_price`
- `strategy_preset`
- `rank`
- `expiry`
- `dte`
- `holding_window_days`
- `exit_by_date`
- `strike`
- `right`
- `bid`
- `ask`
- `mid`
- `spread_pct`
- `delta`
- `gamma`
- `theta`
- `vega`
- `iv`
- `open_interest`
- `volume`
- `breakeven`
- `underlying_target_1`
- `underlying_target_2`
- `underlying_stop`
- `option_take_profit_1`
- `option_take_profit_2`
- `option_stop_loss`
- `max_holding_rule`
- `event_exit_rule`
- `score`
- `rationale`
- `features_json`
- `suppressed`
- `suppression_reason`

### 2. Outcome tracking

Persist realized outcomes for each recommended contract.

Recommended table:

- `option_candidate_outcomes`

Suggested fields:

- `id`
- `candidate_snapshot_id`
- `resolved_at`
- `resolution_type`
- `underlying_close_1d`
- `underlying_close_5d`
- `underlying_close_10d`
- `option_mid_1d`
- `option_mid_5d`
- `option_mid_10d`
- `option_return_1d_pct`
- `option_return_5d_pct`
- `option_return_10d_pct`
- `days_held_to_exit`
- `exit_reason`
- `hit_option_tp1`
- `hit_option_tp2`
- `hit_option_stop`
- `hit_underlying_t1`
- `hit_underlying_t2`
- `hit_underlying_stop`
- `max_runup_pct`
- `max_drawdown_pct`
- `hit_target`
- `expired_itm`
- `notes`

### 3. Options screener

Add a new engine that runs on top-ranked ticker theses only and returns the best option opportunities across that set.

Input set:

- top `N` actionable tickers from Deep Dive / watchlist / current day thesis set

Output:

- ranked list of best option setups today
- each row linked back to the ticker thesis and candidate snapshot
- each row includes recommended hold window and target/exit structure

### 4. Accuracy and resolution analytics

Extend the dashboard’s resolution/accuracy area with option-specific analytics:

- win rate by `strategy_preset`
- win rate by `delta` bucket
- win rate by `DTE` bucket
- win rate by `IV` bucket
- win rate by `spread` bucket
- win rate by `chain_source`
- top rejection reasons
- top suppression reasons

### 5. Claude-assisted review loop

Claude should use persisted evidence to `propose` scoring changes, not automatically deploy them.

Loop:

1. collect recommendation snapshots
2. collect realized outcomes
3. analyze segment performance
4. propose rule changes
5. human reviews
6. Claude implements approved updates

## Roadmap

### Phase 1: Persistence layer

- create Supabase schema for option recommendation snapshots
- create persistence helpers
- write recommendation snapshots whenever option candidates are generated

Exit criteria:

- every option recommendation can be reconstructed later from stored data

### Phase 2: Outcome capture

- define resolution windows and metrics
- build a job or endpoint that evaluates stored recommendation snapshots after 1d / 5d / 10d or expiry
- persist outcomes into Supabase

Exit criteria:

- the system can measure realized performance of prior recommendations

### Phase 3: Options screener API and dashboard module

- build a new options screener service over top-ranked stock theses only
- add a new dashboard page / tab for `Options`
- rank the best candidates across tickers

Exit criteria:

- dashboard can show best option opportunities of the day across the active ticker set

### Phase 4: Resolution & Accuracy integration

- add an options-specific tab or section in `Resolution & Accuracy`
- expose aggregate outcome metrics and cohort analysis

Exit criteria:

- users can see what kinds of option recommendations are working or failing

### Phase 5: Scoring review workflow

- create a repeatable analysis path for Claude to review outcomes and recommend scoring changes
- keep actual rule changes human-approved

Exit criteria:

- the team has an evidence-based improvement loop instead of ad hoc tuning

## Risks

- naive option-outcome tracking can overfit short windows or noisy marks
- contract marks may be missing or stale without a consistent market-data path
- storing only winners or only chosen contracts would bias the dataset
- allowing the LLM to self-modify scoring logic would be unsafe

## Non-Goals

- Do not build a raw full-universe options scanner as v1.
- Do not automate trading decisions without human oversight.
- Do not rely on LLM text alone as the source of truth for performance.
- Do not replace unrelated providers just because IBKR is being added for options.

## Acceptance Criteria

- Option recommendations are persisted to Supabase with enough context to reconstruct why they were selected.
- Option outcomes are persisted and queryable by recommendation cohort.
- A new dashboard options module can rank cross-ticker option setups from a thesis-filtered universe.
- Resolution & Accuracy exposes option-specific performance analytics.
- Claude can review historical outcomes and propose rule changes from evidence, but does not autonomously alter production logic.

## Verification Plan

- unit tests for persistence serialization
- API tests for snapshot and screener endpoints
- integration tests for outcome resolution logic with mocked historical marks
- frontend tests for options screener and accuracy views

## Handoff Direction

This roadmap should be implemented as separate tickets, in order:

1. persist recommendation snapshots
2. persist outcomes
3. build screener API + dashboard module
4. add resolution/accuracy analytics
5. add Claude review workflow support
