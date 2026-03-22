# Known Issues & TODOs

Last updated: 2026-03-22

---

## Data Availability Gaps

These are structural limitations of free data sources — not bugs, but deliberate trade-offs.

| # | Issue | Affected Module | Workaround in Use |
|---|-------|----------------|-------------------|
| 1 | **% float on loan** not freely available (Ortex/S3 proprietary) | `squeeze_screener.py` | `shortPercentOfFloat` from yfinance used as proxy (lower number) |
| 2 | **Cost-to-borrow actual rate** is proprietary (Ortex/IB/Fidelity) | `squeeze_screener.py` | Finviz hard-to-borrow flag used as binary proxy |
| 3 | **Historical implied volatility** not in yfinance free tier | `options_flow.py` | IV rank estimated from 30-day realized vol vs 52-week range — diverges from true IV in event-driven environments |
| 4 | **Short vs loan ratio** is proprietary | `squeeze_screener.py` | Not implemented |

---

## Data Quality Issues

| # | Issue | Affected Module | Notes |
|---|-------|----------------|-------|
| 5 | **Yahoo Finance EU ticker gaps** — `AIR.PA`, `SIE.DE` etc. occasionally return NaN for short interest, options chains, or historical bars | All modules | Handled gracefully (NaN → skip) but data is silently missing with no alerting |
| 6 | **52-week high/low NaN for tickers with < 252 bars of history** | `signal_engine.py` | Fixed in commit `e50d754` but may still affect newly listed tickers |
| 7 | **Finviz scraping fragility** — rate-limited, layout-dependent, may silently return empty data during high traffic | `squeeze_screener.py`, `catalyst_screener.py` | No alerting when this fails; data gaps are silent |

---

## Broken / Unimplemented Features

| # | Issue | Affected Module | Status |
|---|-------|----------------|--------|
| 8 | **Multi-asset crypto signals retired** — original module had −0.20 Sharpe | `paper_trader.py` | Replaced with BTC-only 200-MA binary signal; no altcoin signal currently |
| 9 | **`--social` flag in catalyst_screener.py** — Reddit sentiment component exists in CLI and scoring logic but implementation is uncertain after Reddit API policy changes (2023) | `catalyst_screener.py` | Unknown — needs audit; may silently return empty results |
| 10 | **`catalyst_backtest.py` referenced but may not exist** — `run_master.sh` Step 7 calls `catalyst_backtest.py --universe full` | `run_master.sh` | Needs verification; if missing, Step 7 silently fails |
| 11 | **No React/frontend dashboard** — referenced in planning docs but no frontend code exists in the repo | — | Not started |

---

## Architecture Limitations

| # | Issue | Affected Module | Notes |
|---|-------|----------------|-------|
| 12 | **No conflict resolution layer** — conflicting signals from different modules are not automatically reconciled; delegated entirely to Claude in `ai_quant.py` or manual review | `ai_quant.py` | By design for now; consider a weighted-vote aggregator |
| 13 | **No real-time intraday capability** — all data via yfinance 15-min delayed quotes; system is weekly/end-of-day only | All modules | Would require Polygon.io or similar for intraday |
| 14 | **Survivorship bias** — universe defined from current ticker lists; delisted stocks excluded from all analysis and backtests | `signal_engine.py`, backtest modules | Affects backtest validity |
| 15 | **Point-in-time fundamental data missing** — yfinance returns current values, not as-reported historical values; backtests using fundamental scores have look-ahead bias | `fundamental_analysis.py`, backtest modules | Requires a point-in-time data provider to fix |
| 16 | **SQLite concurrency risk** — if modules run in parallel, write locks on `ai_quant_cache.db` and `fundamentals_cache.db` may error | `ai_quant.py`, `fundamentals_cache.py` | `run_master.sh` runs modules sequentially as mitigation |
| 17 | **FX conversion not applied to crypto P&L** — `fx_rates.py` maps 54 equity tickers to currencies but crypto prices are USD throughout; EUR P&L for crypto positions may be inconsistent | `fx_rates.py`, `paper_trader.py` | Needs explicit USD→EUR conversion in crypto position sizing |

---

## Maintenance TODOs

| # | Issue | Affected Module | Notes |
|---|-------|----------------|-------|
| 18 | **Polymarket ticker mapping is manually maintained** — 92 keyword mappings hardcoded; any new watchlist ticker needs a manual entry to get prediction market signals | `polymarket_screener.py` | Consider auto-generating from watchlist.txt + company name lookup |
| 19 | **`watchlist_history.json` is write-only** — written by the universe screener but no module reads it; historical watchlist evolution is not consumed downstream | `ai_quant.py` | Could feed a drift/turnover analysis module |
| 20 | **Backtest coverage is incomplete** — `backtest.py` and `catalyst_backtest.py` exist but are not fully documented or integrated into `run_master.sh` output | `backtest.py`, `catalyst_backtest.py` | Needs audit of what is actually tested vs stubbed |

---

## Signal Engine v1 — Integration Notes (appended 2026-03-22)

### Data & signal quality

**A. EDGAR XBRL point-in-time fundamentals — not implemented**
Backtest uses a 45-day lag proxy for fundamental data to approximate filing dates.
True point-in-time data requires SEC EDGAR XBRL Companion API integration.
Risk: residual look-ahead bias remains in backtests using fundamental factors.
Fix: scheduled for v2 after backtest module stabilises.

**B. Google Trends (pytrends) — unofficial API**
pytrends is an unofficial wrapper. Google may break it without notice.
Monitor `social_sentiment.py` logs for repeated None returns from `get_google_trends_score()`.
Fallback: StockTwits-only scoring activates automatically when Trends fails.

**C. iShares ETF CSV URLs — may change quarterly**
Universe builder depends on iShares holding CSV download URLs.
If `universe_builder.py` fails with HTTP errors, check:
  https://www.ishares.com/us/products/239707/ (IWB — Russell 1000)
  https://www.ishares.com/us/products/239710/ (IWM — Russell 2000)
Fallback: cached universe file used if < 7 days old.
Action: validate URLs quarterly (add to calendar).

**D. IV history — requires 60 weekly runs before true IV rank is available**
`options_flow.py` falls back to estimated IV rank (from realised vol) until
`iv_history.db` accumulates 60 days of data per ticker.
Check progress:
  sqlite3 data/iv_history.db "SELECT ticker, COUNT(*) FROM iv_history GROUP BY ticker ORDER BY COUNT(*) DESC LIMIT 20;"
Expected: true IV rank available ~15 weeks after first run.

**E. MODULE_WEIGHTS in conflict_resolver.py — currently heuristic**
Weights are set by prior knowledge, not empirical P&L attribution.
Recalibrate after 12 weeks using `logs/conflict_resolution_YYYYMMDD.csv`:
  - Count correct directional calls per module vs 1-week forward return
  - Increase weight for modules with accuracy > 60%, decrease < 45%
Schedule: first recalibration due 12 weeks from first run.

### Architecture

**F. AI Quant capped at top 10 tickers per run**
By design to control Anthropic API cost (~€0.20–0.40/run).
Open positions included dynamically via `_get_open_positions()` in `ai_quant.py`,
which reads `trade_journal.db` at runtime. Static fallback: `AI_QUANT_ALWAYS_INCLUDE`.
To increase cap: change `AI_QUANT_MAX_TICKERS` in `config.py`.
To run full universe: `python3 ai_quant.py --no-limit` (expect ~€4–8/run).
