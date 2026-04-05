# Signal Engine v1 — Help Center

> **What is Signal Engine?**
> A systematic, rules-based weekly signal generator for equities and crypto. It does **not** execute trades automatically — it surfaces high-conviction ideas, sizes positions, and tracks outcomes so you make the final call.

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Running the Pipeline](#2-running-the-pipeline)
3. [Understanding Signals & Scores](#3-understanding-signals--scores)
4. [Market Regime](#4-market-regime)
5. [Screeners](#5-screeners)
6. [AI Thesis (Claude)](#6-ai-thesis-claude)
7. [Dashboard](#7-dashboard)
8. [Portfolio & Trade Tracking](#8-portfolio--trade-tracking)
9. [Backtesting](#9-backtesting)
10. [Data Sources & Caches](#10-data-sources--caches)
11. [Configuration](#11-configuration)
12. [Scheduling & Automation](#12-scheduling--automation)
13. [Troubleshooting](#13-troubleshooting)
14. [Glossary](#14-glossary)

---

## 1. Quick Start

### Prerequisites

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install frontend dependencies
cd dashboard/frontend && npm install && cd ../..

# Set environment variables
cp .env.example .env   # then fill in DATABASE_URL, ANTHROPIC_API_KEY
```

### Run your first signal scan

```bash
# Full weekly run (equities + crypto, no AI)
python signal_engine.py

# Full run including Claude thesis synthesis (~$0.03–0.05)
python run_master.sh

# Watchlist only, with your NAV
python signal_engine.py --watchlist AAPL,NVDA,COIN --nav 35000
```

### Start the dashboard

```bash
bash start_dashboard.sh
# Frontend: http://localhost:5173
# Backend API: http://localhost:8000
```

---

## 2. Running the Pipeline

The pipeline has two modes: **data-only** (free) and **full with AI** (small Claude cost).

### Equity signals

```bash
python signal_engine.py                        # Full universe scan
python signal_engine.py --equity-only          # Skip crypto
python signal_engine.py --watchlist GME,SOFI   # Specific tickers
python signal_engine.py --nav 50000            # Set portfolio size for sizing
```

Output files written to `signals_output/`:
- `equity_signals_YYYYMMDD.csv` — All factor scores + composite Z-score
- `equity_positions_YYYYMMDD.csv` — Recommended allocations in €

### Crypto signals

```bash
python signal_engine.py --crypto-only
```

Output: `crypto_signals_YYYYMMDD.csv` and `crypto_positions_YYYYMMDD.csv`

### Midweek lightweight scan

```bash
bash run_midweek.sh
```

Refreshes regime, dark pool, and catalyst data without re-running full factor scoring. Free (no Claude calls).

### Full weekly pipeline

```bash
bash run_master.sh
```

Orchestrates: universe refresh → regime → factors → screeners → conflict resolution → Claude synthesis → report generation.

---

## 3. Understanding Signals & Scores

### Composite Z-score (equity)

Each ticker receives a **composite Z-score** — a weighted combination of 7 price/fundamental factors, cross-sectionally standardized so the score tells you where a ticker ranks *relative to the universe*, not in isolation.

| Factor | Weight | What it measures |
|--------|--------|-----------------|
| `momentum_12_1` | 28% | 12-month price return minus the most recent month |
| `earnings_revision` | 18% | Direction and size of analyst EPS estimate changes |
| `momentum_6_1` | 16% | 6-month momentum with 1-month skip |
| `ivol` | 12% | Idiosyncratic volatility (lower = cleaner trend) |
| `52wk_high_proximity` | 10% | How close to 52-week high (breakout proximity) |
| `mean_reversion_5d` | 8% | Short-term mean reversion (inverted — dip signal) |
| `volatility_quality` | 8% | Overall low-vol quality proxy (inverted) |

A composite Z-score above **+1.5** is strong. Scores are winsorized at ±3σ to prevent outliers from distorting rankings.

### Signal agreement score

Produced by `conflict_resolver.py`. Aggregates directional votes from all modules (technical, fundamental, options, dark pool, etc.) into a single 0–1 score.

- **0.70+** → High agreement; Claude uses extended thinking
- **0.50–0.69** → Moderate agreement; standard Claude call
- **Below 0.50** → Conflicted signals; conviction reduced automatically

### Conviction (1–5)

The Claude-assigned conviction level:

| Level | Meaning |
|-------|---------|
| 5 | Very high confidence, multiple confirming signals |
| 4 | Strong signal, minor conflicts |
| 3 | Moderate — trade if regime supports |
| 2 | Weak — watch only |
| 1 | Conflicted — avoid or very small size |

Conviction is **capped by market regime**: max 5 in RISK_ON, max 4 in TRANSITIONAL, max 3 in RISK_OFF.

### Position sizing

Positions are sized using **quarter-Kelly** with inverse-volatility weighting:

- Max 8% of NAV per equity position
- Max 10% of NAV per crypto position
- Minimum position: €500 (smaller ignored)
- All positions scaled down by regime multiplier (see §4)

---

## 4. Market Regime

The regime determines how aggressively the system sizes positions.

```bash
python regime_filter.py              # Current regime
python regime_filter.py --sectors    # All 11 sector regimes
python regime_filter.py --refresh    # Force fresh data
```

### How regime is scored

Four components add up to a score (range roughly -4 to +5):

| Component | Bullish | Bearish |
|-----------|---------|---------|
| SPY vs 50/200 MA | +1 each | -1 each |
| VIX level | <15 = +1 | >35 = -2 |
| HYG credit Z-score | tightening = +1 | widening = -1 |
| T10Y2Y yield curve | steepening = +1 | inverted = -1 |

**Score → Regime:**
- **≥ 3 → RISK_ON** — full position sizing, normal factor weights
- **1–2 → TRANSITIONAL** — 70% sizing, conviction capped at 4
- **≤ 0 → RISK_OFF** — 40% sizing, conviction capped at 3, momentum weights reduced

### Sector regimes

Each of the 11 GICS sectors is classified BULL / BEAR / NEUTRAL via MA crossover and relative strength vs the broad market. The dashboard shows sector regime badges on every ticker card.

---

## 5. Screeners

Screeners are independent modules that detect specific setups. They feed into the signal agreement score and the Claude prompt.

### Short Squeeze (`squeeze_screener.py`)

Scores how "squeezable" a stock is (0–100):

```bash
python squeeze_screener.py                    # Default universe
python squeeze_screener.py --universe meme    # Meme stocks only
python squeeze_screener.py --ticker GME AMC   # Specific tickers
python squeeze_screener.py --top 15 --min-score 55
```

Score breakdown:
- **Positioning (45%)** — % float short, estimated short P&L
- **Mechanics (35%)** — Days-to-cover, volume surge, fail-to-deliver, borrow cost
- **Structure (20%)** — Market cap, float size, price diverging from short interest

> A `recent_squeeze` flag zeros out the score — the squeeze already fired.

### Catalyst Setups (`catalyst_screener.py`)

Identifies stocks with energy for explosive moves — the kindling, not the spark:

```bash
python catalyst_screener.py
python catalyst_screener.py --universe small   # Small caps
python catalyst_screener.py --ticker GME       # Deep dive
python catalyst_screener.py --social           # Include sentiment scan
```

Criteria: squeeze setup, volume breakout, Bollinger squeeze, options heat, social momentum.

### Options Flow (`options_flow.py`)

Detects elevated options activity (0–100 heat score):

```bash
python options_flow.py --watchlist
python options_flow.py --ticker COIN
python options_flow.py --top 10 --min-heat 40
```

Heat components: volume spike vs 20d avg (30%), IV rank (25%), expected move (25%), put/call lean (20%).

### Dark Pool (`dark_pool_flow.py`)

Reads daily FINRA ATS data to detect institutional accumulation or distribution:

```bash
python dark_pool_flow.py --ticker AAPL
python dark_pool_flow.py --scan              # All watchlist
```

Outputs: `dark_pool_score` (0–100, 50=neutral), signal (ACCUMULATION / DISTRIBUTION / NEUTRAL), short ratio trend, dark pool intensity.

> FINRA data releases ~8pm ET. Runs on prior day's data during market hours.

### Polymarket (`polymarket_screener.py`)

Extracts crowd probability scores from prediction markets. Useful for binary catalyst events (FDA decisions, earnings beats, macro events):

```bash
python polymarket_screener.py --ticker TSLA
python polymarket_screener.py --query "Fed rate"
python polymarket_screener.py --top 15 --export
```

Only markets with ≥$500 volume, ≥$500 liquidity, and 1–180 days to resolution are included.

### Volume Profile (`volume_profile.py`)

Finds support/resistance via volume-at-price:

```bash
python volume_profile.py AAPL
python volume_profile.py GME --bins 75 --period 6mo
```

Outputs: Point of Control (highest-volume price), Value Area (70% of volume), High/Low Volume Nodes, anchored VWAPs.

### Fundamental Analysis (`fundamental_analysis.py`)

Composite 0–100 score across 6 dimensions (valuation, growth, quality, balance sheet, earnings beat streak, analyst consensus):

```bash
python fundamental_analysis.py --watchlist
python fundamental_analysis.py --ticker GME
python fundamental_analysis.py --watchlist --top 10
```

### Red Flag Screener (`red_flag_screener.py`)

Detects accounting risks that should block bullish thesis:

```bash
python red_flag_screener.py --ticker MU
python red_flag_screener.py --watchlist
```

Risk levels: **CLEAN** (0–20) → **CAUTION** (21–45) → **WARNING** (46–70) → **CRITICAL** (71–100).

A CRITICAL score automatically vetoes any BULL direction in the conflict resolver.

### SEC Module (`sec_module.py`)

Pulls insider Form 4 buys, 13F institutional holdings, 13D/13G activist stakes, and 8-K material events from SEC EDGAR:

```bash
python sec_module.py --ticker GME
python sec_module.py --insider-buys       # Recent buys across universe
python sec_module.py --scan               # Full watchlist
```

### Social Sentiment (`social_sentiment.py`)

Two unauthenticated sources — no API keys required:

```bash
# Used internally by catalyst_screener.py and ai_quant.py
# Not typically run standalone
```

- **Google Trends** — 30-day search volume momentum
- **StockTwits** — Bullish/bearish ratio from public stream (>0.65 = BULLISH, <0.35 = BEARISH)

---

## 6. AI Thesis (Claude)

### How it works

`ai_quant.py` collects outputs from every screener module and sends them to Claude with a structured prompt. Claude returns a complete quant thesis:

```
Direction: BULL | BEAR | NEUTRAL
Conviction: 1–5
Entry range: $120–$140
Target 1: $160
Target 2: $180
Stop loss: $100
Time horizon: 2–4 weeks
Thesis: 2–3 sentence narrative
Catalysts: [3 items]
Risks: [3 items]
```

### Running AI synthesis

```bash
python ai_quant.py --ticker COIN                   # Single ticker
python ai_quant.py --tickers COIN GME SOFI         # Multiple
python ai_quant.py --watchlist                     # All TIER 1/2
python ai_quant.py --ticker COIN --raw             # Show raw Claude response
python ai_quant.py --dump-prompt --ticker COIN     # Show what Claude receives
```

### Cost guide

| Mode | Cost/ticker | When used |
|------|-------------|-----------|
| Standard (Sonnet) | ~$0.026 | signal_agreement_score < 0.70 |
| Extended thinking | ~$0.071 | signal_agreement_score ≥ 0.70 |
| 5-ticker run | ~$0.13–0.35 | depending on agreement scores |

Claude calls are **skipped entirely** when:
- `skip_claude = True` from conflict resolver (post-squeeze guard, near-zero agreement)
- Ticker is below Claude budget priority threshold

### How signal agreement affects Claude

Before Claude is called, `conflict_resolver.py` runs a weighted vote across all modules. The result tells Claude whether signals agree or conflict:

- **High agreement (≥0.70)** → Extended thinking enabled; Claude given more latitude
- **Low agreement (<0.50)** → Claude prompted to weight conflicting evidence carefully; conviction capped

---

## 7. Dashboard

```bash
bash start_dashboard.sh
# http://localhost:5173
```

### Pages

| Page | Shortcut | What you'll find |
|------|----------|-----------------|
| **Portfolio** | `p` | NAV, P&L history chart, current positions, regime badge |
| **Heatmap** | `h` | Signal strength matrix across all tickers |
| **Deep Dive** | `t` | Full ticker analysis — price chart, Claude thesis, analogs, R/R |
| **Screeners** | `s` | All screener results ranked (squeeze, catalyst, options heat) |
| **Dark Pool** | `d` | Institutional flow signals (FINRA ATS) |
| **Backtest** | `b` | Walk-forward results, factor IC table, weight suggestions |
| **Resolution** | `r` | Claude thesis outcome tracking and accuracy stats |
| **Crypto** | `c` | BTC trend + crypto signals |
| **Accuracy** | `a` | Historical win/loss ratios by strategy |
| **Rankings** | `k` | Universe leaderboard — sort by any factor |

### Deep Dive page

The most useful page for any individual ticker. Shows:
- **Price chart** with trendlines, Heikin Ashi mode, volume overlay
- **Claude thesis** — direction, entry/target/stop, catalysts, risks
- **Signal breakdown** — which modules agree/disagree
- **Historical analogs** — past setups most similar to current (12-feature match)
- **Risk/reward bar** — visual R/R ratio
- **Earnings Reaction Model** — expected move based on options market
- **Price Ladder** — key levels (support, resistance, max pain, VWAP)

### Heatmap page

Color-coded matrix showing signal strength per ticker. Green = BULL signal, red = BEAR. Intensity reflects conviction level. Best used to scan the full universe at a glance before drilling into individual tickers.

### Rankings page

Sort the full universe by any factor — composite Z-score, momentum, fundamentals, squeeze score, options heat. Use this to build your own shortlist independent of the automated top-N selection.

---

## 8. Portfolio & Trade Tracking

### Paper trading (hypothetical P&L)

```bash
python paper_trader.py --record       # Snapshot today's signals + prices
python paper_trader.py --report       # Full performance report
python paper_trader.py --report --weeks 8
python paper_trader.py --positions    # Current hypothetical holdings
python paper_trader.py --reset        # Wipe history
```

Tracks top-ranked signals each week against actual price moves. Compares portfolio P&L vs SPY benchmark. Reports Sharpe ratio, max drawdown, hit rate, average win/loss.

**Crypto position:** BTC > 200-day EMA = LONG, below = CASH. Simple and historically robust.

### Trade journal (real trades)

```bash
python trade_journal.py --zones                   # Buy/sell zones for watchlist
python trade_journal.py --buy GME 23.50 500       # Log a buy (ticker, price, €)
python trade_journal.py --sell GME 28.00          # Log a sell
python trade_journal.py --status                  # Open positions + P&L
python trade_journal.py --history                 # All trades + outcomes
python trade_journal.py --report                  # Weekly summary
```

**Action zones** are computed automatically per ticker from support/resistance levels and ATR:
- **Buy zone** — support ± ATR buffer
- **Sell zone 1** — resistance level
- **Sell zone 2** — 3:1 risk/reward target
- **Stop loss** — support minus one ATR

**Outcome tracking** automatically checks price at 1d / 7d / 14d / 30d after entry, records whether target 1, target 2, or stop was hit first (using OHLC, not just close). Links to the Claude thesis if one was generated within 3 days of entry.

---

## 9. Backtesting

```bash
python backtest.py --run-full         # All historical windows
python backtest.py --run-latest       # Most recent window only
python backtest.py --factor-ic        # Factor IC (information coefficient) table
python backtest.py --suggest-weights  # Recommended factor weights
```

### Design

- **Training window:** 504 trading days (~2 years)
- **Test window:** 126 trading days (~6 months)
- **Step size:** 63 trading days (~3 months rolling)
- Survivorship bias mitigation: IPO dates filtered per window

### What it measures

**Information Coefficient (IC)** = Spearman rank correlation between factor Z-score and 1-month forward return. An IC of 0.05+ is considered useful for a quantitative factor.

Factors tested: `momentum_12_1`, `momentum_6_1`, `mean_reversion_5d`, `volatility_quality`, `52wk_high_proximity`, `ivol`.

> `earnings_revision` is excluded from backtests — yfinance only returns current values, not historical point-in-time estimates.

---

## 10. Data Sources & Caches

All primary data sources are **free and require no API keys** except Claude (AI synthesis).

| Source | Module | Data | Cost |
|--------|--------|------|------|
| Yahoo Finance (yfinance) | signal_engine, options_flow, most screeners | Prices, options chains, fundamentals | Free |
| FINRA ATS daily files | dark_pool_flow | Institutional routing data | Free |
| SEC EDGAR APIs | sec_module, red_flag_screener | Filings, insider trades, 8-K | Free |
| Polymarket Gamma API | polymarket_screener | Prediction market probabilities | Free |
| Google Trends (pytrends) | social_sentiment | Search volume momentum | Free |
| StockTwits public stream | social_sentiment | Retail sentiment | Free |
| ECB / Frankfurter API | fx_rates | EUR FX rates | Free |
| iShares CSV holdings | universe_builder | ETF constituents | Free |
| **Anthropic Claude API** | ai_quant | AI synthesis | **~$0.03–0.05/run** |

### Cache locations

| Cache | Location | TTL | Notes |
|-------|----------|-----|-------|
| Regime | `data/regime_cache.json` | 24h | Force refresh with `--refresh` |
| Universe | `data/universe_cache.json` | 24h | ETF holdings |
| FINRA files | `data/finra_cache/` | Permanent | Immutable daily files |
| Google Trends | `data/trends_cache.json` | 24h | |
| StockTwits | `data/twits_cache.json` | 4h | |
| Fundamentals | `fundamentals_cache.db` | 30 days | SQLite, clears quarterly |
| IV history | Supabase `iv_history` | Permanent | Needs 60+ rows for valid rank |
| Thesis | Supabase `thesis_cache` | Permanent | Re-generated on request |

### FX rates

Prices in the dashboard and position sizes are converted to EUR automatically. Source priority: Yahoo Finance → ECB → Frankfurter API → local cache. During market hours: 30-min TTL. Weekends: 24h TTL.

---

## 11. Configuration

All system parameters live in `config.py`. Key settings:

```python
# Universe
MIN_PRICE = 1.50                # Minimum stock price
MIN_30D_VOLUME = 500_000        # Minimum daily $ volume
MAX_ATR_PCT = 0.06              # Max ATR% (volatility gate)
MAX_BETA = 2.0                  # Max 60-day beta

# Position sizing
MAX_EQUITY_POSITION_PCT = 0.08  # 8% cap per stock
MAX_CRYPTO_POSITION_PCT = 0.10  # 10% cap per crypto
MIN_POSITION_EUR = 500          # Minimum position size

# Factor weights (adjusted by regime)
FACTOR_WEIGHTS = {
    "momentum_12_1": 0.28,
    "earnings_revision": 0.18,
    "momentum_6_1": 0.16,
    ...
}

# AI budget
CLAUDE_MODEL = "claude-sonnet-4-6"
EXTENDED_THINKING_THRESHOLD = 0.70  # agreement score to trigger
MAX_CLAUDE_TICKERS_PER_RUN = 10     # budget cap
```

### Watchlist

`watchlist.txt` has three sections:

- **TIER 1** — Core watchlist; always included in screeners and Claude calls
- **TIER 2** — Secondary; included if budget allows
- **UNIVERSE** — Auto-managed by `universe_builder.py`; do not edit manually
- **PERSISTENT_AUTO_FAVORITES** — Tickers that repeatedly appear in top-50; auto-promoted

To manually add a stock to TIER 1, edit `watchlist.txt` directly. The system will include it in every subsequent run.

---

## 12. Scheduling & Automation

### Recommended schedule

| Time (Berlin) | Command | Cost | Purpose |
|---------------|---------|------|---------|
| Daily 06:00 | `run_master.sh --skip-ai` | Free | Data refresh: regime, FINRA, universe |
| Monday 14:45 | `run_master.sh` | ~$0.03–0.05 | Full weekly signal run with Claude |
| As needed | `run_midweek.sh` | Free | Midweek lightweight scan |

### GitHub Actions

A daily pipeline workflow runs automatically on GitHub Actions (`.github/workflows/`). It uses `--skip-ai` to avoid Claude costs on the free daily run, and connects to Supabase via the session pooler URL (required for IPv4 GitHub runners).

### Manual runs

You can trigger any module independently at any time. The system is stateless — each run reads from Supabase/cache and writes fresh outputs.

---

## 13. Troubleshooting

### "No data returned for ticker X"

- Yahoo Finance rate-limits heavy batch requests. Wait 60 seconds and retry.
- Try a single ticker first: `python signal_engine.py --watchlist X`
- Check if the ticker is delisted or has a different symbol (e.g. ADR suffixes).

### "FINRA data not available"

FINRA CDN files for the current trading day release ~8pm ET. The system automatically falls back to the 3 most recent prior business days. If all fail, dark pool scores are omitted from that run (non-fatal).

### "Claude API call failed"

- Verify `ANTHROPIC_API_KEY` is set in `.env`
- Check API credit balance at console.anthropic.com
- Run with `--dump-prompt --ticker X` to inspect what would be sent to Claude
- Individual ticker failures are non-fatal — the rest of the run continues

### "Supabase connection refused"

- Verify `DATABASE_URL` in `.env` uses the **session pooler** URL (port 5432 via Supabase pooler, not direct connection)
- Format: `postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:5432/postgres`
- Run `python -c "from utils.db import get_connection; get_connection()"` to test

### Dashboard shows no data

- Confirm the FastAPI backend is running: `curl http://localhost:8000/api/regime/current`
- Check that signal CSV files exist in `signals_output/`
- Verify Supabase connection (thesis data, IV history, trades all live there)

### IV rank shows "N/A"

IV rank requires at least 60 daily snapshots in the `iv_history` Supabase table. On a fresh install this takes ~3 months of daily runs to populate. IV rank will show "N/A" until enough history accumulates — this is expected.

### Regime reads as stale

```bash
python regime_filter.py --refresh
```

Forces a fresh fetch, bypassing the 24h cache.

### Universe seems too small

```bash
python universe_builder.py --build-cache --list-universe
```

Rebuilds all 7 ETF CSVs from iShares and prints the filtered universe. If iShares CSVs are unreachable, the system falls back to the hardcoded list.

---

## 14. Glossary

| Term | Definition |
|------|-----------|
| **Composite Z-score** | Cross-sectional weighted score combining all equity factors; standardized relative to the universe |
| **Signal agreement score** | 0–1 weighted vote across all modules on directional consensus |
| **Conviction** | Claude's 1–5 confidence rating for the thesis |
| **RISK_ON / RISK_OFF / TRANSITIONAL** | Market regime based on SPY trend, VIX, credit spreads, yield curve |
| **Quarter-Kelly** | Position sizing method using 25% of the Kelly criterion for conservative sizing |
| **IC (Information Coefficient)** | Spearman rank correlation between a factor score and forward return; measures factor predictiveness |
| **Dark pool intensity** | FINRA off-exchange volume as a fraction of total volume; >40% = heavy institutional use |
| **Max pain** | Options expiry price where total in-the-money option value is minimized |
| **POC (Point of Control)** | Highest-volume price in the volume profile; acts as price magnet |
| **Value Area** | Price range containing 70% of total trading volume |
| **Days-to-cover (DTC)** | Short interest divided by average daily volume; higher = harder to unwind |
| **FTD (Fail-to-Deliver)** | SEC-reported shares that failed to settle; elevated FTD = potential squeeze fuel |
| **IV rank** | Current implied volatility relative to its 52-week range (0–100%) |
| **ATR (Average True Range)** | Measure of average daily price range; used for stop-loss placement |
| **Jegadeesh-Titman momentum** | Classic 12-month minus 1-month return momentum factor from academic literature |
| **Idiosyncratic volatility (iVol)** | Volatility unexplained by market movement; low iVol = cleaner, less noisy trend |
| **Walk-forward backtest** | Out-of-sample testing where the model is retrained on rolling windows to avoid look-ahead bias |
| **GICS** | Global Industry Classification Standard; 11-sector taxonomy used for sector regime classification |
| **ATS (Alternative Trading System)** | Dark pool / off-exchange venue where institutional orders route to avoid market impact |
