# Task: IBKR Options Integration Roadmap

Status: proposed
Stage: discovery
Type: research
Priority: P1
Severity: medium
Owner: Human
Reviewer: Human
Product Area: dashboard
Category: research
Risk: api
Effort: L
Target Release: backlog
Due Date: TBD
Dependencies: TRD-042
Blocked By: TRD-042
Links: `dashboard/frontend/src/pages/TickerPage.tsx`, `dashboard/api/main.py`, `options_flow.py`, IBKR API docs
Success Metric: the ticker deep dive can surface 1-3 structured option candidates per actionable ticker with contract-level fields and clear risk constraints.

## Problem Statement

The current stack is strong at finding stock setups, but weak at turning a stock thesis into a concrete options trade. The dashboard shows only aggregate options context such as `iv_rank`, `expected_move_pct`, `put_call_ratio`, and `max_pain`. It does not expose contract-level data needed to choose an expiry, strike, delta, or structure.

Building a full cross-universe options screener first would create a large search space, high market-data load, and a weak signal-to-noise ratio. The better path is to derive option candidates from already-ranked stock theses.

## User Impact

- The operator can identify a strong stock setup but still has to leave the product to find the actual option to trade.
- The current `Copy Prompt` flow can produce qualitative commentary, but it cannot reliably recommend a tradable contract because the prompt lacks chain-level data.
- Without deterministic filtering, an LLM may suggest attractive-looking but illiquid or misaligned contracts.

## Objective

Add an IBKR-backed option-candidate engine that converts an existing stock thesis into a small set of tradable option ideas, then surface those ideas directly on the ticker deep-dive page.

This roadmap remains planning-only until `TRD-042` is complete and the required IBKR subscriptions are live.

## Recommendation

Build this in three layers:

1. `Underlying thesis filter`
   Reuse the existing stock-ranking workflow from `ai_quant.py` and Deep Dive. Do not scan the entire option universe first.

2. `Option candidate engine`
   For a single ticker, fetch the option chain from IBKR, compute deterministic scores, and return the best candidates that match the thesis.

3. `Ticker deep-dive recommendation section`
   Add an `Option Candidates` section on the ticker page showing 1-3 recommended contracts plus the reason each candidate passed.

Do not launch a standalone options screener UI first.

Reason:

- The existing product is thesis-driven by underlying ticker.
- IBKR option-chain retrieval is multi-step and market-data intensive.
- Ranking option contracts across the whole universe is a combinatorial problem and will quickly hit data-line and latency constraints.
- A ticker-scoped engine is reusable later for a standalone screener.

## Current State

- `options_flow.py` computes only aggregate options heat metrics from Yahoo Finance:
  - `heat_score`
  - `iv_rank`
  - `expected_move_pct`
  - `put_call_ratio`
  - `expiry`
- `dashboard/api/main.py` exposes those summary metrics in `/api/signals/ticker/{ticker}`.
- `dashboard/frontend/src/lib/api.ts` defines `TickerDetail` with no contract-level option chain fields.
- `dashboard/frontend/src/pages/TickerPage.tsx` uses those summary metrics in the `Options Flow` card and in the existing LLM copy prompt.

## Delivery Gate

The team chose to wait for real IBKR subscriptions before implementation. That means:

- discovery and sequencing can continue now
- adapter and product implementation should not start yet
- any task in this chain must stay blocked on `TRD-042`

## Why IBKR

IBKR is the right broker/API target for this feature because it provides:

- broad listed options coverage
- programmatic option-chain discovery
- market data and Greeks
- paper trading support for API testing
- enough API depth to validate tradability, not just theory

Key implementation facts from IBKR official docs:

- The `TWS API` can retrieve option chains with `reqSecDefOptParams`; IBKR notes this is preferable to `reqContractDetails` for full chains because `reqContractDetails` is throttled for ambiguous chain requests.
- `Client Portal API` option-chain handling is a sequential `/iserver/secdef/search` -> `/iserver/secdef/strikes` -> `/iserver/secdef/info` process.
- Live options Greeks require market data subscriptions for both the option and the underlying.
- API market data is treated as off-platform and requires the appropriate subscriptions.
- Client Portal API requires an opened, funded `IBKR Pro` account and an authenticated brokerage session for `/iserver` market-data access.

## Product Decision

### Best first product

Add an `Option Candidates` section to the ticker deep-dive page.

### Not the best first product

A separate options screener page that scans contracts across the whole universe.

### Rationale

- The stock thesis is already the core ranking object in this codebase.
- Option selection should inherit direction, time horizon, invalidation level, and confidence from the underlying thesis.
- Most bad option trades come from choosing the wrong contract on the right stock. The ticker page is the natural place to solve that.
- A standalone options screener can be added later by reusing the same candidate engine over the top-N tickers only.

## Proposed Architecture

### 1. IBKR adapter

Create a dedicated adapter module, for example `utils/ibkr_options.py`, with:

- `search_underlying(symbol) -> conid`
- `get_option_expirations_and_strikes(symbol, conid)`
- `build_contracts(symbol, expiries, strikes, rights)`
- `get_market_data(contracts)`
- `get_greeks(contracts)`

Recommendation:

- Use `TWS API` or `IB Gateway` for the production data path.
- Avoid making `Client Portal API` the primary market-data path for this feature because its brokerage-session behavior is more fragile for continuous screening.

### 2. Option candidate schema

Add a typed schema for contract-level candidates:

- `symbol`
- `conid`
- `right`
- `expiry`
- `dte`
- `strike`
- `multiplier`
- `bid`
- `ask`
- `mid`
- `last`
- `spread_pct`
- `volume`
- `open_interest`
- `iv`
- `delta`
- `gamma`
- `theta`
- `vega`
- `intrinsic`
- `extrinsic`
- `breakeven`
- `max_loss`
- `score`
- `score_reasons[]`
- `strategy_type`

### 3. Deterministic candidate engine

Build scoring first, LLM second.

For a bullish stock thesis, the engine should:

- filter expiries by thesis horizon
- filter illiquid contracts
- filter contracts with too-wide bid/ask spreads
- filter out contracts with delta outside the allowed band
- compare premium cost against expected move and target path
- score candidates based on liquidity, alignment with target horizon, leverage efficiency, and risk clarity

Example first-pass rules:

- `swing`: `30 <= DTE <= 90`
- `LEAPS`: `DTE >= 540`
- `delta band`: `0.25 <= abs(delta) <= 0.45` for directional longs
- `spread_pct <= 5%`
- `open_interest >= 500`
- `volume >= 50`
- `premium <= max_budget_per_trade`
- skip expiries that place earnings inside the hold window unless explicitly allowed

### 4. AI overlay

After deterministic filtering returns a small candidate set, the LLM can:

- rank the top 3 candidates
- explain strike/expiry tradeoffs
- choose between `long call`, `long put`, `debit spread`, or `LEAPS`
- generate a compact rationale for the deep-dive page

The LLM should not invent contracts or search the raw chain itself.

### 5. Dashboard integration

Add a new card to `TickerPage.tsx`:

- title: `Option Candidates`
- states:
  - `No data`
  - `Chain loading`
  - `Candidates ready`
  - `Suppressed by low-confidence thesis / poor liquidity / event risk`

Each candidate row should show:

- `Call/Put`
- `strike`
- `expiry`
- `DTE`
- `delta`
- `mid`
- `breakeven`
- `OI`
- `volume`
- `spread %`
- `why this contract`

## Roadmap

### Phase 0: Broker and permissions

- Open and fund `IBKR Pro`
- Enable options trading permissions
- Subscribe to required market data
  - `OPRA` for US options
  - underlying market data subscriptions required for Greeks
- Decide runtime path: `IB Gateway` preferred over full TWS for service operation

### Phase 1: Backend contract discovery

- Build the IBKR adapter
- Fetch expiries and strikes
- Materialize a ticker-scoped option chain cache
- Persist short-lived chain snapshots locally or in Supabase

Exit criteria:

- For one ticker, the system can return a normalized chain with quotes and Greeks.

### Phase 2: Candidate scoring

- Implement deterministic contract filters
- Add strategy presets:
  - `swing_long_call`
  - `swing_long_put`
  - `bull_call_spread`
  - `bear_put_spread`
  - `leaps_call`
- Produce `top_candidates` plus `rejection_reasons`

Exit criteria:

- For one actionable ticker, the engine returns 1-3 sensible contracts without LLM help.

### Phase 3: Ticker page integration

- Add `/api/ticker/{symbol}/option-candidates`
- Add `OptionCandidate` types in `dashboard/frontend/src/lib/api.ts`
- Render `Option Candidates` card in `TickerPage.tsx`
- Add a prompt mode to the existing `Copy Prompt` control:
  - `Equity Thesis`
  - `Options Contract Selection`

Exit criteria:

- The deep-dive page can show recommended contracts for a ticker without leaving the app.

### Phase 4: AI ranking and explanation

- Feed only pre-filtered candidates to `ai_quant.py` or a dedicated options prompt builder
- Ask the model to rank and explain, not to search the full chain
- Store rationale alongside candidate scores

Exit criteria:

- Each candidate has both deterministic metrics and an AI explanation layer.

### Phase 5: Multi-ticker options screener

- Reuse the same engine only on top-ranked tickers from Deep Dive or watchlist
- Add a page showing `best option setups today`
- Do not scan every contract on every ticker in the universe

Exit criteria:

- The screener ranks option opportunities across a limited, already-curated ticker set.

## Technical Risks

- `Market data lines`: IBKR enforces concurrent line limits; naive full-chain scanning will not scale.
- `Session management`: Client Portal brokerage sessions are single-session and fragile for continuous jobs.
- `Liquidity traps`: many contracts look cheap but are untradable due to wide spreads and low OI.
- `Earnings distortion`: pre-event IV can make naive delta/expiry scoring misleading.
- `LLM hallucination`: the model must not select contracts outside the filtered candidate set.

## Non-Goals

- Do not automate order placement in the first version.
- Do not let the LLM free-form browse the entire option chain.
- Do not replace the current stock screener with an option-first screener.
- Do not support every complex strategy in v1.

## Acceptance Criteria

- A ticker with a valid thesis can display 1-3 option candidates directly in the deep-dive page.
- Each candidate includes contract-level tradability fields and deterministic rejection logic.
- The recommendation engine can explain why no option should be traded when liquidity or event risk is poor.
- The existing `Copy Prompt` flow can produce an options-specific prompt mode using real candidate data.

## Verification Plan

- Local adapter tests with mocked IBKR responses
- Paper-account end-to-end tests for one US equity ticker
- Snapshot tests for ticker page rendering
- Manual validation against IBKR option-chain UI for:
  - strike
  - expiry
  - delta
  - bid/ask
  - open interest

## Sources

- IBKR Campus, TWS API Documentation:
  https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/
- IBKR Campus, Client Portal API v1:
  https://ibkrcampus.com/campus/ibkr-api-page/cpapi-v1/
- IBKR Campus, Market Data Subscriptions:
  https://ibkrcampus.com/campus/ibkr-api-page/market-data-subscriptions/
- IBKR Campus, Handling Options Chains:
  https://ibkrcampus.com/campus/ibkr-quant-news/handling-options-chains/
- IBKR Campus, Getting Started / API overview:
  https://ibkrcampus.com/campus/ibkr-api-page/getting-started/
- IBKR Campus, Paper Trading Account:
  https://ibkrcampus.com/campus/glossary-terms/paper-trading-account/
