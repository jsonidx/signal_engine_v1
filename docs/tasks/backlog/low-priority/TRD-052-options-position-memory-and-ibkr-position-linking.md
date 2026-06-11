# Task: IBKR Portfolio Ingestion and Options Position Memory

Status: proposed
Stage: ready
Type: feature
Priority: P3
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: portfolio
Category: options
Risk: integration
Effort: L
Target Release: backlog
Due Date: TBD
Dependencies: TRD-021, TRD-026, TRD-046, TRD-049, TRD-051
Blocked By: none
Links: `utils/ibkr_options.py`, `dashboard/api/main.py`, `dashboard/frontend/src/pages/TickerPage.tsx`, `utils/supabase_persist.py`
Success Metric: the system can ingest live IBKR portfolio/account state, detect active option positions automatically, persist them using IBKR `conid` as the canonical key, and use the actual position context rather than generic candidate assumptions for downstream hold / take-profit / sell decisions, while still working in a reduced mode when no live market-data subscription is available.

## Problem Statement

The current options recommendation flow is candidate-oriented. It can evaluate:

- contract quality
- entry quality
- PM/risk gating
- target projections
- path scenarios

But once a user actually buys an option, the system does not yet persist a
position-aware state anchored to the real live contract and fill context.

That creates a gap:

- the engine can say whether a candidate looks buyable
- but after entry, the user still has to mentally track
  - what exact option was bought
  - what the actual entry price was
  - how much time has elapsed
  - whether the option is now at a profit / stop / theta-risk state

This is exactly where long-option decision quality usually degrades.

## User Impact

Without IBKR-backed position memory and portfolio sync:

- take-profit and sell rules remain generic instead of position-specific
- users must manually re-enter or remember option fills
- the dashboard cannot distinguish a candidate from an active position
- hold/sell decisions remain harder than they need to be

## Objective

Add an IBKR-backed portfolio ingestion and position-memory layer for options so
the system can sync real account state, detect active option positions, and,
when available, hydrate actual position details from IBKR instead of relying
only on candidate-time assumptions.

The architecture must explicitly separate:

- portfolio/position sync that should work without assuming live options market-data subscriptions
- richer decision support that becomes available when live quote data exists

The preferred architecture is:

- pull accessible IBKR accounts
- pull active portfolio positions
- detect active option positions automatically
- use `conid` as the canonical option contract key
- hydrate position state from IBKR account/portfolio data
- fall back to manual save only when broker sync is unavailable

## Proposed Solution

Use the IBKR contract identifier (`conid`) as the primary position key.

Per current IBKR documentation:

- IBKR recommends using `conid` as the contract identifier
- portfolio APIs can return position details by `conid`
- contract APIs can return full contract details by `conid`
- portfolio/account APIs can expose accounts, open positions, pricing context,
  and P&L fields needed for active-position sync

At minimum, the design should account for these IBKR surfaces:

- `GET /portfolio/accounts`
- `GET /portfolio/{acctId}/positions`
- `GET /portfolio/{acctId}/position/{conid}`
- contract lookup by `conid`
- optional TWS API streaming via `reqPositions` / `reqPositionsMulti`

Design the feature around three acquisition modes:

### 1. IBKR portfolio-ingestion mode

The system connects to IBKR, discovers accessible accounts, reads active
portfolio positions, and automatically identifies active option positions.

The system then resolves and stores:

- account identifier when relevant
- account accessibility / linkage state
- `conid`
- ticker / expiry / strike / right / multiplier
- exchange / currency
- average cost / average price from IBKR when available
- live position size when available
- market value when available
- realized P&L when available
- unrealized P&L when available
- current order context when available
- quote snapshot / market data linkage when available

This should be the preferred path over manual entry.

### 2. Direct single-position linking mode

User supplies or selects one IBKR option `conid` directly when full portfolio
sync is not yet available or not desired.

This mode should still hydrate the contract and position context where possible,
but is secondary to full active-position sync.

### 3. Manual fallback mode

When IBKR account linking is unavailable, allow manual save of a position using
the best available contract identity and entry details.

### 4. Two operating modes

The feature should support two explicit runtime modes:

#### Base mode: portfolio-linked, no live market-data assumption

Available from broker/account data alone:

- account and portfolio membership
- open position detection
- canonical option identity via `conid`
- contract metadata
- average cost / average price when IBKR provides it
- size / active status
- market value when available
- realized and unrealized P&L when available from portfolio endpoints
- current orders when available from the chosen integration path

This mode should not assume the user has options market-data subscriptions.

#### Enhanced mode: live quote-aware

Activated only when the account has the required market-data permissions and
subscriptions for the relevant instruments.

May include:

- live bid / ask / mid
- quote freshness
- spread quality
- current mark quality
- Greeks when available

This mode improves downstream decisioning but must not be required for the
position-memory system to function.

## Scope

- schema additions for persisted option positions or a companion position table
- IBKR portfolio-ingestion path for accounts and active positions
- IBKR integration path to load contract and position details by `conid`
- position save/load APIs
- ticker-page or portfolio-facing position memory UX
- focused tests for identity resolution, persistence, and safe fallback behavior

## Non-Goals

- Do not place or modify live orders from this ticket.
- Do not require broker sync for all users; manual fallback remains valid.
- Do not build a full portfolio management system here.
- Do not implement final hold/sell/take-profit state logic in this ticket
  beyond what is necessary to support downstream consumers.

## Constraints

- Keep `conid` as the canonical broker-side contract identity when IBKR is used.
- Prefer automatic active-position sync over manual contract entry when broker
  integration is available.
- Preserve manual fallback for users without active IBKR linkage.
- Distinguish clearly between:
  - candidate-time estimated entry
  - actual saved fill / average cost
  - current mark / quote
- Do not assume live quote subscriptions exist for all users.
- Handle partial or multi-lot positions safely.
- Account for current orders and partial exits where the data source supports it.

## Acceptance Criteria

- Observable behavior: the system can read accessible IBKR accounts and active
  portfolio positions.
- Observable behavior: active option positions can be detected automatically and
  keyed by IBKR `conid`.
- Observable behavior: a user can still attach or save an option position using
  a direct IBKR `conid` when needed.
- Observable behavior: the system can retrieve contract details and, when
  available, position details such as average cost from IBKR.
- Observable behavior: saved position state is persisted locally/backend for
  later decisioning.
- Observable behavior: base portfolio-linked mode works without requiring live
  options market-data subscriptions.
- Observable behavior: enhanced quote-aware fields activate only when live data
  is available.
- Observable behavior: manual fallback remains possible when IBKR linkage is
  unavailable.
- Tests:
  - `conid`-based contract hydration works
  - position persistence stores the required identity and pricing fields
  - missing broker data degrades safely
  - historical candidate-only flows do not break

## Verification Plan

- focused tests for IBKR account and active-position ingestion
- focused tests for IBKR contract/position lookup adapters
- focused persistence tests for saved option positions
- endpoint smoke tests for save/load position memory
- smoke test behavior with and without live quote fields present
- `make verify` if practical

## QA Notes

- Test scenarios:
  - connected IBKR account with multiple active positions, only some of which are options
  - connected IBKR account with one live long call
  - connected IBKR account with one live long put
  - connected IBKR account with no live market-data subscription but valid positions
  - connected IBKR account with live options market-data subscription
  - manual entry without broker linkage
  - position with no current market data but valid contract identity
- Edge cases:
  - partial fills
  - open orders against an existing position
  - multiple accounts
  - rolled position to a new expiry / strike
  - stale position that no longer exists at broker
- Regression risks:
  - confusing average cost with candidate recommended entry
  - poor handling of expired options
  - identity mismatch between saved row and live IBKR contract

## Launch / Release Notes

- User-facing change summary: the system can now ingest IBKR portfolio state and
  remember real option positions using IBKR contract identity and actual position
  context.
- Operational notes: broker-linked mode uses IBKR `conid`; the preferred flow is
  active-position sync from IBKR accounts and portfolio endpoints; portfolio-linked
  base mode works without assuming live market-data subscriptions; enhanced
  quote-aware mode activates only when live market data is available.
- Rollback notes: disable position-memory writes and fall back to candidate-only
  logic.

## Post-Launch Validation

- What to monitor:
  - account sync success rate
  - active option-position detection rate
  - percentage of saved positions linked by `conid`
  - broker hydration failure rate
  - percentage of positions with actual average cost vs manual fallback
  - percentage of positions running in base mode vs enhanced quote-aware mode
  - stale or unmatched saved-position rate
- How success will be confirmed:
  - downstream hold/sell logic can use actual position context instead of
    candidate assumptions
- Follow-up decision date: after first live usage of saved option positions.

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-052, "IBKR Portfolio Ingestion and Options Position Memory," in
this repo.

Goal:
- Add an IBKR-backed portfolio ingestion and position-memory layer for options
  using IBKR `conid` as the canonical contract identity when broker linkage is
  available.

Requirements:
- Ingest accessible IBKR accounts
- Ingest active portfolio positions
- Detect active option positions automatically
- Support saving/loading an option position by `conid`
- Hydrate contract details and, when available, position details such as
  average cost from IBKR
- Explicitly support a base mode that works from portfolio/position data without
  assuming live options market-data subscriptions
- Add an enhanced mode that uses live quotes/Greeks only when subscriptions and
  permissions exist
- Preserve manual fallback for users without broker linkage
- Persist enough data for downstream hold / take-profit / sell decisioning

Recommended IBKR approach:
- Prefer `conid` as the unique contract identity
- Prefer portfolio/account ingestion first, then direct single-position lookup
- Use IBKR contract-detail lookup for contract metadata
- Use IBKR portfolio/position lookup for actual position state when available
- Consider optional TWS position streams for active updates

Scope:
- schema / persistence for saved option positions
- backend APIs for account/position sync plus save/load/hydrate
- minimal frontend controls to attach a saved position
- focused tests

Tests and verification:
- Add focused adapter, persistence, and endpoint tests
- Run the targeted tests you add
