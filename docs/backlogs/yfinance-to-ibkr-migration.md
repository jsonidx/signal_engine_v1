# yfinance to IBKR Migration Backlog

## Goal

Prepare a staged path to replace `yfinance` with `IBKR` where broker-grade
market data materially improves trade quality, while keeping the rest of the
data stack intact where IBKR is not the right provider.

This is a backlog note, not an implementation ticket.

## Executive Summary

IBKR should replace `yfinance` first for:

- US stock and ETF live quotes
- US options chains
- option quotes
- option Greeks
- option candidate selection for the ticker deep-dive page

IBKR should **not** be treated as a full replacement for:

- SEC / insider filings
- dark-pool / FINRA ATS data
- social sentiment
- macro enrichment
- broad fundamentals and analyst-history enrichment

## Monthly Cost Baseline

For the currently discussed US non-professional setup:

- `US Securities Snapshot and Futures Value Bundle (NP)`: `USD 10.00/month`
- `US Equity and Options Add-On Streaming Bundle (NP)`: `USD 4.50/month`

Expected recurring total:

- `USD 14.50/month`

Possible future extras only if needed:

- index data subscriptions for `SPX`, `RUT`, or similar
- news/research products such as `Benzinga`
- event-calendar products if IBKR-hosted event data is later preferred

## Current yfinance Footprint

The current repo uses `yfinance` broadly across these categories:

### Highest-priority replacement candidates

- [options_flow.py](/Users/jason/signal_engine_v1/options_flow.py)
- [utils/iv_calculator.py](/Users/jason/signal_engine_v1/utils/iv_calculator.py)
- [dashboard/api/main.py](/Users/jason/signal_engine_v1/dashboard/api/main.py)
- [ai_quant.py](/Users/jason/signal_engine_v1/ai_quant.py)

These directly affect:

- options heat
- implied volatility / IV rank support
- ticker deep-dive live prices
- max pain
- option-candidate generation

### Medium-priority market-data replacements

- [signal_engine.py](/Users/jason/signal_engine_v1/signal_engine.py)
- [regime_filter.py](/Users/jason/signal_engine_v1/regime_filter.py)
- [trade_journal.py](/Users/jason/signal_engine_v1/trade_journal.py)
- [paper_trader.py](/Users/jason/signal_engine_v1/paper_trader.py)
- [thesis_checker.py](/Users/jason/signal_engine_v1/thesis_checker.py)
- [refresh_stale_theses.py](/Users/jason/signal_engine_v1/refresh_stale_theses.py)
- [universe_builder.py](/Users/jason/signal_engine_v1/universe_builder.py)
- [yf_cache.py](/Users/jason/signal_engine_v1/yf_cache.py)

These mostly depend on:

- OHLCV history
- current prices
- ADV / ATR style calculations
- benchmark ETF comparisons

### Low-priority or non-IBKR-fit replacements

- [fundamental_analysis.py](/Users/jason/signal_engine_v1/fundamental_analysis.py)
- [red_flag_screener.py](/Users/jason/signal_engine_v1/red_flag_screener.py)
- [utils/dcf_model.py](/Users/jason/signal_engine_v1/utils/dcf_model.py)
- [utils/peer_benchmarking.py](/Users/jason/signal_engine_v1/utils/peer_benchmarking.py)
- parts of [catalyst_screener.py](/Users/jason/signal_engine_v1/catalyst_screener.py)

These use `yfinance` for:

- fundamentals
- sector / industry metadata
- analyst target data
- upgrades / downgrades
- earnings-history style enrichment

IBKR is not the clean first choice for replacing those.

## Field-by-Field Source Decision

| Field / capability | Current source | Move to IBKR | Notes |
|---|---|---:|---|
| US stock live quote | `yfinance` | Yes | High-value replacement |
| US ETF live quote | `yfinance` | Yes | High-value replacement |
| US options chain | `yfinance` | Yes | Core replacement target |
| Option bid/ask | `yfinance` | Yes | Core replacement target |
| Option delta / Greeks | `yfinance` | Yes | Main reason to migrate |
| Option IV / ATM IV | `yfinance` | Yes | Should be broker-sourced |
| Option OI / volume | `yfinance` | Yes | Needed for screening quality |
| Max pain inputs | `yfinance` | Yes | Can be recomputed from IBKR chain |
| Intraday / daily OHLCV | `yfinance` | Mostly yes | Good future consolidation target |
| FX spot | `yfinance` + ECB/Frankfurter | Optional | Existing free sources are adequate |
| Fundamentals | `yfinance` | No | Keep separate provider path |
| Sector / industry metadata | `yfinance` | No | Better handled elsewhere |
| Analyst targets / revisions | `yfinance` | No | Better handled elsewhere |
| Earnings history | `yfinance` | No | Keep existing or dedicated provider |
| SEC filings | SEC | No | Already better than broker data |
| Dark pool / ATS | FINRA | No | Not an IBKR domain |
| Social sentiment | StockTwits / Trends | No | Not an IBKR domain |

## Recommended Migration Order

### Phase 1: Option-market replacement

Replace `yfinance` in the options path first.

Target modules:

- [options_flow.py](/Users/jason/signal_engine_v1/options_flow.py)
- [utils/iv_calculator.py](/Users/jason/signal_engine_v1/utils/iv_calculator.py)
- new `IBKR` adapter / option candidate modules from `TRD-021` to `TRD-024`

Outcome:

- broker-grade option chains
- real bid/ask
- broker-grade Greeks
- deterministic option candidate screening

### Phase 2: Ticker-page live market data

Replace `yfinance` calls used by deep-dive and dashboard endpoints for:

- last price
- ADV / volume context
- live ticker-price refreshes
- max-pain chain sourcing

Primary file:

- [dashboard/api/main.py](/Users/jason/signal_engine_v1/dashboard/api/main.py)

Outcome:

- ticker page uses one market-data provider path for both stock and option ideas

### Phase 3: ai_quant market-data dependencies

Move the market-data parts of `ai_quant.py` away from `yfinance` while keeping:

- SEC
- dark pool
- social
- LLM synthesis

Target replacements:

- current price
- price history
- expected move / options context
- max pain

Do **not** force IBKR into the fundamentals or analyst-enrichment sections.

### Phase 4: General OHLCV consolidation

Review whether these should move to IBKR historical bars:

- [signal_engine.py](/Users/jason/signal_engine_v1/signal_engine.py)
- [regime_filter.py](/Users/jason/signal_engine_v1/regime_filter.py)
- [trade_journal.py](/Users/jason/signal_engine_v1/trade_journal.py)
- [paper_trader.py](/Users/jason/signal_engine_v1/paper_trader.py)
- [thesis_checker.py](/Users/jason/signal_engine_v1/thesis_checker.py)
- [universe_builder.py](/Users/jason/signal_engine_v1/universe_builder.py)

This phase is optional and should happen only after the option workflow is stable.

## What Should Stay Non-IBKR

Keep these providers unless there is a separate reason to change them:

- `SEC EDGAR` for filings and insider activity
- `FINRA ATS` for dark-pool style data
- `Polymarket`
- `Google Trends`
- `StockTwits`
- `ECB / Frankfurter` for FX backup/reference
- `Supabase` for persistence and dashboard auth
- current `LLM` providers

## Architecture Direction

Preferred end state:

- `IBKR` as primary for market data used in trading decisions
- dedicated non-broker providers for fundamentals, filings, sentiment, and macro
- deterministic scoring before any LLM recommendation step

## Risks

- IBKR market-data line limits can break naive broad scanning.
- IBKR sessions are operationally more complex than `yfinance`.
- Full replacement of `yfinance` everywhere would add complexity without improving the weakest areas first.
- Fundamentals and analyst-history regressions are likely if those paths are migrated too early.

## Trigger to Split Into Tickets

Split this backlog into task tickets when one of these becomes true:

- you subscribe to the IBKR bundles and want live testing
- the option candidate engine is ready for implementation
- the ticker page is ready to consume broker-grade option candidates
- `yfinance` options quality becomes an active blocker in production use

## Suggested Future Ticket Sequence

Already prepared:

- [TRD-021-ibkr-option-chain-adapter.md](/Users/jason/signal_engine_v1/docs/tasks/new/TRD-021-ibkr-option-chain-adapter.md)
- [TRD-022-option-candidate-engine-and-api.md](/Users/jason/signal_engine_v1/docs/tasks/new/TRD-022-option-candidate-engine-and-api.md)
- [TRD-023-ticker-page-option-candidates-card.md](/Users/jason/signal_engine_v1/docs/tasks/new/TRD-023-ticker-page-option-candidates-card.md)
- [TRD-024-options-prompt-mode-and-ai-ranking.md](/Users/jason/signal_engine_v1/docs/tasks/new/TRD-024-options-prompt-mode-and-ai-ranking.md)

Potential later tickets:

- `Replace dashboard live stock prices with IBKR`
- `Replace ai_quant options-context fetches with IBKR`
- `Evaluate IBKR historical bars for regime and journal workflows`
