# Options Execution Gap Analysis

## Goal

Identify what the current options recommendation system already covers, what is
still missing for fast execution, and what should be added next to make the
recommendations actionable rather than just informative.

This is a backlog note, not an implementation ticket.

## Executive Summary

The current options work is strong on:

- contract selection
- thesis alignment
- liquidity/quality screening
- hold-window planning
- target/stop planning

The main missing layer is `execution guidance`.

Today the system can answer:

- which option contract is preferred
- why it was preferred
- how long to hold it
- where the planned exits are

But it does not yet fully answer:

- what exact option price should I try to enter at
- how aggressive should the order be
- when is the trade no longer worth chasing
- what fill/slippage quality should I expect
- what is the expected reward/risk from the proposed entry

That gap matters because good contract selection alone is not enough for fast
and repeatable trade execution.

## Current Coverage

The current ticket set already covers these recommendation datapoints.

### Underlying thesis context

- ticker
- direction
- conviction
- time horizon
- underlying target 1 / target 2 / stop
- event-risk context

### Contract identity

- call / put
- strike
- expiry
- DTE
- strategy preset

### Quote and liquidity

- bid
- ask
- mid
- spread %
- open interest
- volume

### Option characteristics

- delta
- implied volatility
- breakeven

### Recommendation and ranking

- deterministic score
- rationale
- rank
- suppression / no-trade reason

### Exit and hold planning

- holding window
- exit-by date
- option take profit 1 / 2
- option stop loss
- underlying target 1 / 2
- underlying stop
- max holding rule
- event exit rule

## Missing Execution Datapoints

These are the highest-value gaps for making the recommendation execution-ready.

### Entry guidance

- `recommended_entry_price`
- `recommended_order_type`
- `entry_style`
- `max_chase_price`
- `do_not_enter_above`
- `entry_rationale`

### Fill and slippage awareness

- `fill_quality_score`
- `slippage_risk_label`
- `quote_freshness_ts`
- `skip_if_spread_above_pct`

### Entry-based risk/reward

- `expected_return_tp1_pct`
- `expected_return_tp2_pct`
- `expected_loss_stop_pct`
- `reward_to_risk_ratio`

### Basic position-sizing guidance

- `max_premium_per_contract`
- `suggested_contract_count` or
- `suggested_notional_risk`

This should remain bounded and optional. It is not a request for autonomous
position sizing or live trade routing.

## Recommended Priority

### Phase 1: Must-have for actionability

Add:

- `recommended_entry_price`
- `recommended_order_type`
- `max_chase_price`
- `entry_rationale`

These are the most important missing fields.

### Phase 2: Better execution quality

Add:

- `entry_style`
- `fill_quality_score`
- `slippage_risk_label`
- `skip_if_spread_above_pct`

### Phase 3: Better risk framing

Add:

- `expected_return_tp1_pct`
- `expected_return_tp2_pct`
- `expected_loss_stop_pct`
- `reward_to_risk_ratio`

### Phase 4: Optional sizing support

Add:

- `max_premium_per_contract`
- simple `suggested_contract_count` guidance

This phase should remain conservative and clearly separated from any automated
trading logic.

## Recommended Product Behavior

### Ticker page

Show the full execution block:

- bid / ask / mid
- recommended entry
- order type
- max chase
- hold window
- TP1 / TP2 / stop
- entry rationale
- fill quality / spread warning

### Options overview

Show a compact execution summary:

- contract
- score
- recommended entry
- max chase
- hold window
- TP1
- stop

### Persistence / learning loop

Store the execution guidance fields too, so later analysis can answer:

- did trades entered near the recommended entry perform better
- were max-chase limits too loose or too strict
- do wide-spread contracts underperform after entry slippage

## Risks

- Overstating execution precision when quotes are stale
- Recommending entries that are too optimistic on thin contracts
- Mixing selection logic and execution logic without clear separation
- Drifting into autonomous trading behavior if sizing/routing becomes too aggressive

## Recommendation

Build the missing execution layer as a separate follow-up ticket rather than
silently expanding the current candidate engine scope.

The right next step is:

- keep contract selection deterministic
- add entry guidance derived from quotes/liquidity
- persist the execution guidance
- expose it on both ticker page and options overview
- analyze it later in the resolution/accuracy workflow
