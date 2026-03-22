Signal Engine v1 — Internal Documentation Report
Generated: 2026-03-22 | Codebase: /Users/jason/Documents/GitHub/signal_engine_v1

1. Module Inventory
Screeners
File	Description
signal_engine.py	Master weekly multi-factor screener; produces ranked equity + crypto signals with position sizing
catalyst_screener.py	Detects high-momentum setups: short squeeze, volume breakout, volatility compression, social momentum
options_flow.py	Options heat ranking via IV rank, volume spike, expected move, and put/call ratio
squeeze_screener.py	Dedicated short squeeze candidate ranker (0–100 score) across positioning, mechanics, and structure
polymarket_screener.py	Extracts financial catalyst signals from Polymarket prediction markets via Gamma API
fundamental_analysis.py	Quant-grade fundamental scorecard: valuation, growth, quality, balance sheet, earnings, analyst
midweek_scan.py	Wednesday evening mid-cycle update: market pulse, watchlist moves, fresh catalyst check
Analysis & Signal Synthesis
File	Description
ai_quant.py	Claude Opus 4.6 (adaptive thinking) signal synthesis: aggregates all 12 modules into a per-ticker investment thesis
sec_module.py	Tracks insider transactions (Form 4), activist stakes (13D/G), and material events (8-K) via SEC EDGAR
congress_trades.py	Tracks STOCK Act disclosures from House and Senate members and spouses
max_pain.py	Computes options max pain strike per expiry; identifies expiration-driven price gravity zones
volume_profile.py	Volume-at-price analysis: POC, value area, HVN/LVN support/resistance levels, anchored VWAPs
cross_asset_divergence.py	Macro divergence "bottom and top finder" using +DI/-DI relative to RSP, HYG, UUP
Paper Trading & Journaling
File	Description
paper_trader.py	Records weekly signals into SQLite, tracks P&L vs SPY benchmark, Sharpe, drawdown
trade_journal.py	Computes ATR-based buy/sell zones; logs trades and unrealized P&L to trade_journal.db
Utilities & Data
File	Description
fx_rates.py	Multi-source EUR FX conversion: Yahoo Finance → ECB → Frankfurter API, with 30-min cache
fundamentals_cache.py	30-day SQLite cache for yfinance quarterly fundamentals to avoid redundant API calls
config.py	Master configuration: portfolio parameters, factor weights, thresholds, API settings
Shell Scripts
File	Description
run_master.sh	10-step Sunday evening orchestration pipeline; runs all modules in sequence
run_weekly.sh	Simplified weekly report runner
run_midweek.sh	Wednesday midweek scan launcher
setup_schedules.sh	Cron/automation setup for scheduled runs
Data Files
File	Description
watchlist.txt	Tiered ticker list (Tier 1/2/3) updated weekly by ai_quant.py universe screener
fx_cache.json	Cached EUR/USD/GBP/CHF/SEK/JPY rates with timestamp
polymarket_cache.json	Cached Polymarket market listings (1-hour TTL)
ai_quant_cache.db	SQLite cache of Claude thesis results (keyed by ticker + date)
fundamentals_cache.db	SQLite cache of quarterly fundamentals (30-day TTL)
trade_journal.db	Trade log, entries, and P&L history
paper_trades.db	Paper trading snapshots and weekly benchmark returns
2. Data Sources
yfinance (Yahoo Finance)
Used by: All modules
Data fetched: OHLCV history (download()), fundamentals (Ticker().info), options chains (Ticker().option_chain()), EPS estimates
Specific fields: shortPercentOfFloat, sharesShort, shortRatio, marketCap, floatShares, forwardPE, priceToSalesTrailingTwelveMonths, enterpriseToEbitda, revenueGrowth, grossMargins, operatingMargins, returnOnEquity, currentRatio, debtToEquity, freeCashflow
Caching: Fundamentals cached 30 days in fundamentals_cache.db. Price data fetched live each run.
Rate limit: ~100 requests/minute (soft limit, unenforced)
Cost: Free
Known issues: Occasional NaN for EU tickers, options chains missing for small/illiquid tickers, no historical IV
Polymarket Gamma API
Used by: polymarket_screener.py
Base URL: https://gamma-api.polymarket.com
Endpoints: /markets (paginated, 100/page), filtered by active=true, closed=false
Fields consumed: question, outcomePrices, volume24hr, liquidity, endDate, tags
Caching: polymarket_cache.json, TTL = 1 hour (from config.py)
Auth: None required
Rate limit: ~1,000 calls/hour
Cost: Free
SEC EDGAR
Used by: sec_module.py, squeeze_screener.py
Endpoints: Full-text search API, Company Filings API, XBRL Companion API, FTD CSV files (bi-monthly release)
Forms tracked: Form 4 (insider transactions), 13F (institutional), 13D/G (activist 5%+), 8-K (material events)
Auth: None; requires User-Agent: SignalEngine/1.0 (educational research) header
Rate limit: 10 requests/second (SEC hard limit; enforced in code)
Data lag: Filings 2–7 days; FTD data ~2-week lag, bi-monthly cadence
Cost: Free
House/Senate Stock Watcher
Used by: congress_trades.py
URLs:
House: https://house-stock-watcher-data.s3.amazonaws.com/data/all_transactions.json
Senate: https://senate-stock-watcher-data.s3.amazonaws.com/aggregate/all_transactions.json
Data lag: 30–45 days (statutory STOCK Act disclosure window)
Auth: None
Cost: Free
Anthropic Claude API
Used by: ai_quant.py
Model: claude-opus-4-6 with extended thinking (adaptive)
Auth: ANTHROPIC_API_KEY environment variable (optional; module skips gracefully if absent)
Caching: Results cached in ai_quant_cache.db keyed by (ticker, date) — skips API call if entry exists for today
Cost: ~$0.02–$0.04 per ticker; full watchlist run ~$0.40–$0.50
Finviz
Used by: squeeze_screener.py, catalyst_screener.py
Method: HTML scraping via BeautifulSoup4
Data fetched: Hard-to-borrow proxy, supplemental short data
Rate limiting: 1-second delays enforced in code; may fail during high traffic
Cost: Free (scraping)
ECB / Frankfurter API
Used by: fx_rates.py
ECB endpoint: Official daily reference rates
Frankfurter endpoint: ECB-backed free API
Cache: fx_cache.json, TTL = 30 min during market hours, 24h on weekends
Cost: Free
3. Ticker Universe & Discovery
Starting Universe
The system does not use a single static universe. Instead, it has multiple overlapping pools:

A. Hardcoded universes in catalyst_screener.py:


SMALL_CAP_UNIVERSE  # 45 tickers — meme/biotech/growth names (GME, AMC, BBBY, SPCE, etc.)
MEME_UNIVERSE       # 16 tickers — high retail interest (GME, AMC, COIN, HOOD, RIVN, etc.)
LARGE_CAP_WATCH     # 10 tickers — NVDA, TSLA, AMD, META, AAPL, MSFT, GOOGL, AMZN, NFLX, SHOP
B. Dynamic tiered watchlist in watchlist.txt:

Tier 1: score ≥ 18/25 (high conviction)
Tier 2: score ≥ 13/25 (monitor)
Tier 3: score ≥ 8/25 (weak signal)
Updated weekly by ai_quant.py --watchlist universe screener
C. Per-module custom universes:

options_flow.py: 14 hardcoded high-volatility, retail-active tickers
signal_engine.py: Accepts --watchlist PLTR,SOFI,RIVN CLI override or defaults to watchlist.txt
Pre-Screen Filters
Before scoring, tickers are filtered by:

Minimum 30-day average volume (illiquid names dropped)
Minimum price (penny stock filter, configurable)
Data availability: at least 252 bars of history for momentum factors
Valid options chain for options-dependent modules
Discovery Mechanisms for Net-New Tickers
The primary discovery mechanism is ai_quant.py --universe-screen, which:

Pulls a broad universe (reportedly S&P 500 + Russell 1000 + custom lists) from yfinance
Runs all 10 signal modules on each ticker
Scores 0–25 across signal breadth and quality
Writes tickers scoring ≥ 8 into watchlist.txt with tier assignment
Commits the result via run_master.sh (most recent commit: 2db3a8b — 2026-03-18 universe screener update)
Catalyst screener surfaces net-new tickers via:

Volume breakout screening across SMALL_CAP_UNIVERSE — a ticker surging 3x average volume with rising price will score to the top regardless of prior watchlist presence
Short squeeze screen: any ticker with short_pct_float > 20% + days_to_cover > 4 enters scoring
Polymarket: tickers matched via 92-keyword mapping — a prediction market mentioning a new ticker name will surface it
4. Scoring & Signal Logic
4.1 Signal Engine — Equity Multi-Factor Composite
File: signal_engine.py

Factors and weights:

Factor	Weight	Lookback	Logic
momentum_12_1	35%	252d, skip 21d	12-month return minus last month (Jegadeesh-Titman)
momentum_6_1	20%	126d, skip 21d	6-month return minus last month
mean_reversion_5d	15%	5d	5-day return, inverted (losers bounce)
volatility_quality	15%	63d	Realized vol, inverted (low vol = quality)
risk_adj_momentum	15%	126d mom / 63d vol	Sharpe-like: momentum ÷ volatility
Normalization:

Compute each factor raw value per ticker
Z-score cross-sectionally: (x - mean(x)) / std(x)
Winsorize at ±3σ to remove outliers
Weighted sum → composite_z
Rank all tickers by composite_z descending (rank 1 = best)
No explicit BUY/SELL labels — top 15 by rank are included in the portfolio; the rest are excluded.

Position sizing:

Inverse-volatility weighting (lower realized vol → larger weight)
Hard cap: 8% per position (max_position_equity_pct)
Minimum: €500 (min_position_eur)
Kelly fraction: 0.25
4.2 Signal Engine — Crypto Trend/Momentum
File: signal_engine.py

Signal computation (−1.0 to +1.0):

Component	Weight	Logic
Trend score	40%	Price vs EMA21/50/200: +1 per EMA above, normalized to [−1, +1]
Momentum score	30%	Weighted ROC across [7, 14, 30, 60] days with weights [0.4, 0.3, 0.2, 0.1], clipped to [−1, +1]
RSI timing	15%	RSI < 30 → +1.0; RSI > 70 → −0.5; else 0.0
Trend confirmation	15%	Trend + momentum both positive → +0.15 bonus
Volatility regime gate (applied after scoring):

Ann. vol < 80%: vol_scale = 1.0 (full position)
Ann. vol 80–120%: vol_scale = 0.5 (half position)
Ann. vol > 120%: vol_scale = 0.0 (no position — cash)
Signal → Action thresholds:


signal > +0.3   → BUY
signal > 0.0    → HOLD
signal > −0.3   → REDUCE
signal ≤ −0.3   → SELL / NO POSITION
4.3 Catalyst Screener
File: catalyst_screener.py

Short Squeeze Setup (0–9 pts)

short_pct_float > 40%         → +3
short_pct_float 20–40%        → +2
short_pct_float 10–20%        → +1
days_to_cover > 8             → +2
days_to_cover 4–8             → +1
float < 50M shares            → +1
volume surge 2x + price +5%   → +2
volume surge 1.5x + price up  → +1
Volume Breakout (0–7 pts)

vol_ratio (5d / 20d avg) > 3.0x   → +3  (EXTREME)
vol_ratio 2.0–3.0x                 → +2  (HIGH)
vol_ratio 1.5–2.0x                 → +1  (ABOVE AVG)
OBV rising + price rising           → +2
OBV rising + price flat             → +1  (hidden accumulation)
up-volume > 2x down-volume (10d)   → +2
up-volume > 1.3x down-volume       → +1
Volatility Compression
Uses Bollinger Band width percentile to flag contracting volatility before potential breakouts. (No numeric threshold documented in code comments — fires when BB width is at a multi-month low.)

4.4 Options Flow Screener
File: options_flow.py

"Options Heat" score (0–100):

Component	Max pts	Thresholds
Volume spike	30	> 5x avg → 30; 3–5x → 20; 2–3x → 10
IV rank	25	IV rank > 80 → 25; 50–80 → 15; < 50 → 5
Expected move	25	EM > 5% weekly → 25; 3–5% → 15; 1–3% → 5
Put/call lean	20	PC ratio < 0.5 or > 2.0 → 20 (extreme directional)
Note: True historical IV unavailable for free; IV rank is estimated from 30-day realized volatility vs its 52-week range.

4.5 Squeeze Screener
File: squeeze_screener.py

Scoring model — three buckets (sum to 100):

Bucket	Weight	Sub-factors
Positioning	~45%	pct_float_short (20%), short_pnl_estimate (15%) — are shorts underwater?
Mechanics	~35%	days_to_cover (15%), volume_surge (10%), ftd_vs_float (5%), cost_to_borrow proxy (5%)
Structure	~20%	market_cap log-scaled (7%), float_size log-scaled (7%), price_divergence — price up while short high (6%)
Hard override: recent_squeeze = True → final_score = 0 (protects against entering after squeeze has already fired)

Output: SqueezeScore dataclass; ev_score = (final_score / 100) × juice_target

4.6 Fundamental Analysis
File: fundamental_analysis.py

Six sub-scores, each 0–4 points (max total = 24 → normalized to 0–100%):

Category	Key thresholds
Valuation	Forward P/E < 15 → +2; 15–25 → +1; > 60 → −1. P/S < 2 → +1. EV/EBITDA < 10 → +1
Growth	Rev growth > 30% YoY → +2; 10–30% → +1; declining → −1. EPS growth > 30% → +2
Quality	Gross margin > 50% → +1. Op margin > 20% → +1. ROE > 15% → +1. ROA > 5% → +1
Balance sheet	FCF positive → +1. Current ratio > 1.5 → +1. D/E < 1.0 → +1. Cash > Debt → +1
Earnings	4-quarter beat streak → +2. 1–2 beats → +1. Next earnings < 30 days → +1
Analyst	Rating < 2.5 (bullish) → +1. > 10 analysts → +1. Target upside > 20% → +1. Strong buy consensus → +1
4.7 Max Pain
File: max_pain.py


# For each candidate strike S:
call_pain(S) = Σ (S - K) × OI_calls   for all K < S
put_pain(S)  = Σ (K - S) × OI_puts    for all K > S
total_pain(S) = call_pain(S) + put_pain(S)
max_pain = argmin(total_pain)
Signal strength:

HIGH: ≤ 7 days to expiry AND total OI > 10,000 contracts
MEDIUM: ≤ 14 days OR high OI
LOW: far from expiry or thin market
Pin risk zone: ±0.5% around max pain strike

4.8 Volume Profile
File: volume_profile.py


DEFAULT_BINS = 60
MERGE_PCT    = 1.5    # Merge HVNs within 1.5% of each other
VALUE_AREA_PCT = 0.70 # Value area = 70% of total volume
MIN_PEAK_PCT = 0.08   # HVN must be ≥ 8% of the max bin volume
Output keys: poc_price, value_area_high, value_area_low, support_levels, resistance_levels, nearest_support, nearest_resistance, vwap_20d, vwap_50d, interpretation

4.9 Cross-Asset Divergence
File: cross_asset_divergence.py

Uses log-normalized Directional Movement Index (+DI/−DI) vs three macro references (RSP, HYG, UUP):


bot_line = weighted_avg(ref_plusDI / stock_plusDI)   # refs surge, stock lags → bottom divergence
top_line = weighted_avg(ref_minusDI / stock_minusDI) # refs drop, stock doesn't → top divergence
Signal fires when:

line > 0.65 (threshold)
line > 1.8 × 20-bar SMA (spike in relative weakness)
Output signal: "BOTTOM" | "TOP" | "NEUTRAL"

4.10 Polymarket Signal
File: polymarket_screener.py

Signal score (0–1) components:

Component	Weight	Logic
Consensus strength	30%	Probability distance from 50%; ≥ 70% or ≤ 30% = max
24h volume	25%	Volume tiers: high ≥ $50k, medium ≥ $10k, low ≥ $1k
Liquidity	25%	Tiers: high ≥ $100k, medium ≥ $10k, low ≥ $1k
Time to resolution	20%	Closer to resolution = higher relevance
5. Buy/Sell Decision Flow
Full Decision Path

                    ┌─────────────────────────────┐
                    │  Ticker enters system via:  │
                    │  • watchlist.txt (tiered)   │
                    │  • Hardcoded universes       │
                    │  • CLI --watchlist flag      │
                    └──────────────┬──────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     signal_engine.py      catalyst_screener.py    options_flow.py
     (momentum Z-score)    (squeeze/volume/vol)    (IV/volume heat)
              │                    │                    │
              ▼                    ▼                    ▼
     equity rank + weight   setup score 0–9+      heat score 0–100
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
       max_pain.py        volume_profile.py    cross_asset_divergence.py
       (pin zone)         (POC/S&R levels)     (macro bottom/top)
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     fundamental_analysis  sec_module.py       congress_trades.py
     (0–100% scorecard)    (Form 4/13D/8-K)   (STOCK Act disclosures)
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     polymarket_screener   squeeze_screener.py   trade_journal.py
     (market probability)  (0–100 score)         (buy/sell zones)
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   ▼
                           ┌───────────────┐
                           │  ai_quant.py  │  ← AGGREGATION LAYER
                           │ (Claude Opus) │
                           └───────┬───────┘
                                   │
                     Structured JSON thesis:
                     direction, conviction,
                     entry/stop/target,
                     catalysts, risks
                                   │
                           ┌───────▼───────┐
                           │  run_master   │
                           │    .sh output │
                           │  report file  │
                           └───────────────┘
Module Dependencies
Module	Depends on
ai_quant.py	All other modules (calls each one's public API and aggregates)
paper_trader.py	signal_engine.py (reads its output)
midweek_scan.py	paper_trades.db (reads Sunday's snapshot), polymarket_screener.py, congress_trades.py
trade_journal.py	yfinance directly; writes to trade_journal.db
signal_engine.py	config.py, fx_rates.py
All screeners	fundamentals_cache.py (via get_cached())
Conflict Resolution
There is no automated conflict resolution layer. Conflicting signals (e.g., strong momentum Z-score but high squeeze score + bearish Polymarket) are passed as raw structured data to Claude in ai_quant.py, which is explicitly prompted to "flag contradictory signals" and produce a clear directional view. The human operator makes the final call.

The only hard overrides are:

Crypto vol_scale = 0.0 when annualized vol > 120% (zeroes out position regardless of signal)
Squeeze final_score = 0 when recent_squeeze = True
6. Output & Reporting
Signal Engine CSV Outputs
Written to signals_output/:

equity_signals_YYYYMMDD.csv — all ranked equities with composite_z, factor scores, individual factor values
equity_positions_YYYYMMDD.csv — top 15 tickers with EUR position sizes and weights
crypto_signals_YYYYMMDD.csv — crypto tickers with signal score, regime, vol
crypto_positions_YYYYMMDD.csv — top 5 crypto positions
Typical equity output columns:
ticker, rank, composite_z, momentum_12_1, momentum_6_1, mean_reversion_5d, volatility_quality, risk_adj_momentum, weight_pct, position_eur

AI Quant Thesis Output (per ticker)

{
  "direction": "BULL | BEAR | NEUTRAL",
  "conviction": 1–5,
  "time_horizon": "days / weeks / months",
  "entry_low": 182.50,
  "entry_high": 185.00,
  "stop_loss": 176.00,
  "target_1": 195.00,
  "target_2": 210.00,
  "position_size_pct": 5,
  "catalysts": ["...", "...", "..."],
  "risks": ["...", "...", "..."],
  "thesis": "2–3 sentence narrative"
}
Also stored in ai_quant_cache.db with full signals_json and raw_response.

Master Report
run_master.sh produces: signal_reports/signal_report_YYYYMMDD.txt

This is a concatenated plaintext/markdown file combining all module outputs in sequence: portfolio status → equity signals → options heat → catalyst setups → squeeze rankings → fundamentals → SEC/congress → per-ticker deep dives → AI quant portfolio briefing → trade zones.

Paper Trader Output
Terminal output from python3 paper_trader.py --report:

Weekly return % (portfolio vs SPY)
Cumulative P&L in EUR
Sharpe ratio (annualized)
Max drawdown
Hit rate (% winning weeks)
Forward return analysis at 1/2/3/4 weeks post-signal
No React Dashboard
Despite the exploration prompt referencing a React dashboard, no React/TypeScript/frontend code exists in this codebase. All output is terminal, CSV, and flat text/markdown report files.

7. Known Issues & TODOs
Data Availability Gaps (explicitly acknowledged in code comments)
% float on loan — Not freely available. Requires Ortex or S3 Partners subscription. squeeze_screener.py uses shortPercentOfFloat as a proxy, which is a different (lower) number.
Cost-to-borrow actual rate — Proprietary (Ortex/IB/Fidelity). Using Finviz "hard-to-borrow" flag as a binary proxy only.
Historical implied volatility — Not in yfinance free tier. IV rank in options_flow.py is estimated from 30-day realized vol vs its 52-week range — this is an approximation and will diverge from true IV rank in events-driven environments.
Short vs loan ratio — Proprietary. Not implemented.
Data Quality Issues
Yahoo Finance EU ticker gaps — Tickers like AIR.PA, SIE.DE occasionally return NaN for short interest, options chains, or historical bars. Code handles NaN gracefully but the data is silently missing.
52-week high/low NaN for < 252 bars — Fixed in commit e50d754 but may still affect newly listed tickers with very short history.
Finviz scraping fragility — Rate-limited, layout-dependent. May silently fail and return empty data during high traffic. No alerting when this happens.
Known Broken / Unimplemented
Multi-asset crypto signals — The original multi-asset crypto screener was retired (−0.20 Sharpe). paper_trader.py uses a simple BTC-only 200-MA binary signal as replacement. There is no altcoin signal currently.
--social flag in catalyst_screener.py — Reddit/social sentiment is listed as a component and the CLI flag exists, but the implementation status is uncertain (the scraping target and API method for Reddit have changed multiple times due to Reddit API policy changes in 2023).
run_master.sh Step 7 (catalyst_backtest.py --universe full) — References catalyst_backtest.py but this file is not listed among the tracked source files and may not exist or may be a stub.
No React dashboard — Referenced in README.md or planning docs but no frontend code exists in the repo.
Architecture Limitations
No conflict resolution layer — Conflicting signals from different modules are not automatically resolved; this is delegated entirely to Claude in ai_quant.py or to manual review.
No real-time intraday capability — All data via yfinance 15-minute delayed quotes; system is designed for end-of-day / weekly cadence only.
Survivorship bias — Universe is defined from current ticker lists; delisted stocks are not in sample.
Point-in-time fundamental data — yfinance returns current fundamental values, not as-reported historical values. Backtests using fundamental scores have look-ahead bias.
SQLite concurrency — If multiple modules run concurrently (e.g., parallel deep-dive loop in run_master.sh), SQLite write locks on ai_quant_cache.db and fundamentals_cache.db may produce errors. run_master.sh runs modules sequentially to avoid this.
FX rates not applied to crypto — fx_rates.py has a 54-ticker currency mapping for equities but crypto prices are handled in USD throughout; EUR conversion for crypto P&L may be inconsistent.
TODOs Implied by Code Structure
Polymarket ticker mapping is manually maintained — 92 keyword mappings hardcoded in polymarket_screener.py; any new watchlist ticker needs a manual mapping entry to get prediction market signals.
watchlist_history.json — Exists as a file but no module was identified that reads it; appears to be written-only logging from the universe screener, not yet consumed downstream.
backtest.py and catalyst_backtest.py — Referenced but not fully documented; backtest coverage of catalyst and squeeze signals is incomplete.
End of Report | Next suggested action: add a KNOWN_ISSUES.md to track items 8–20 above as a living document.