# Signal Engine v1 — Internal Documentation

> This file is **partially auto-generated** by `doc_generator.py`.
> Sections wrapped in `<!-- AUTO:* -->` markers are overwritten on each run.
> All other sections are hand-written — edit freely.
>
> To refresh: `python3 doc_generator.py`
> To regenerate fully: `python3 doc_generator.py --full`

---

<!-- AUTO:HEADER -->
**Last updated:** 2026-03-22
**Generator version:** 1.0
**Modules tracked:** 17
<!-- /AUTO:HEADER -->

---

## Table of Contents

1. [Module Inventory](#1-module-inventory)
2. [Data Sources](#2-data-sources)
3. [Ticker Universe & Discovery](#3-ticker-universe--discovery)
4. [Scoring & Signal Logic](#4-scoring--signal-logic)
5. [Buy/Sell Decision Flow](#5-buysell-decision-flow)
6. [Live Configuration](#6-live-configuration)
7. [Output & Reporting](#7-output--reporting)
8. [Known Issues](#8-known-issues)
9. [Daily Top-20 Ranking Feature](#9-daily-top-20-ranking-feature)

---

## 1. Module Inventory

<!-- AUTO:MODULE_INVENTORY -->
### Screeners

| File | Lines | Description |
|------|-------|-------------|
| [signal_engine.py](../signal_engine.py) | 755 | WEEKLY SIGNAL ENGINE v1.0 |
| [catalyst_screener.py](../catalyst_screener.py) | 1332 | CATALYST SCREENER v1.0 — "Hidden Gems" Detector |
| [options_flow.py](../options_flow.py) | 752 | OPTIONS FLOW SCREENER v1.0 — "Options Heat" Detector |
| [squeeze_screener.py](../squeeze_screener.py) | 1027 | SQUEEZE SCREENER v1.0 — Short Squeeze Candidate Detector |
| [polymarket_screener.py](../polymarket_screener.py) | 1165 | POLYMARKET SCREENER v1.0 — Prediction Market Signal Extractor |
| [fundamental_analysis.py](../fundamental_analysis.py) | 756 | FUNDAMENTAL ANALYSIS MODULE v1.0 |
| [midweek_scan.py](../midweek_scan.py) | 367 | MIDWEEK SCAN v1.0 |

### Analysis & Signal Synthesis

| File | Lines | Description |
|------|-------|-------------|
| [ai_quant.py](../ai_quant.py) | 1833 | AI QUANT ANALYST v1.0 — Claude-Powered Signal Synthesis |
| [sec_module.py](../sec_module.py) | 592 | SEC EDGAR MODULE v1.0 |
| [volume_profile.py](../volume_profile.py) | 354 | VOLUME PROFILE — Support & Resistance via Volume-at-Price |

### Paper Trading & Journaling

| File | Lines | Description |
|------|-------|-------------|
| [paper_trader.py](../paper_trader.py) | 619 | PAPER TRADING TRACKER v1.0 |
| [trade_journal.py](../trade_journal.py) | 835 | TRADE JOURNAL v1.0 |

### Utilities & Data

| File | Lines | Description |
|------|-------|-------------|
| [fx_rates.py](../fx_rates.py) | 359 | FX RATES MODULE v1.0 |
| [fundamentals_cache.py](../fundamentals_cache.py) | 218 | FUNDAMENTALS CACHE  — SQLite-backed cache for quarterly fundamental data |

### Configuration & Scripts

| File | Description |
|------|-------------|
| [config.py](../config.py) | Master configuration: portfolio parameters, factor weights, thresholds, API settings |
| [run_master.sh](../run_master.sh) | 10-step Sunday evening orchestration pipeline |
| [run_midweek.sh](../run_midweek.sh) | Wednesday midweek scan launcher |
| [run_weekly.sh](../run_weekly.sh) | Simplified weekly report runner |

### Public Functions Per Module

#### signal_engine.py
- `fetch_price_data(tickers, lookback_days, label)`
- `zscore_cross_sectional(series)`
- `compute_momentum(prices, lookback, skip)`
- `compute_realized_vol(prices, lookback)`
- `compute_risk_adjusted_momentum(prices, mom_lookback, vol_lookback)`
- `compute_equity_composite(prices)`
- `compute_ema(series, span)`
- `compute_rsi(series, period)`
- `compute_crypto_signals(prices)`
- `compute_position_sizes(signals, prices, asset_type, total_allocation_eur)`
- `print_header()`
- `print_equity_report(signals, positions)`
- `print_crypto_report(signals, positions)`
- `print_portfolio_summary(eq_pos, cr_pos)`
- `export_to_csv(equity_signals, crypto_signals, equity_positions, crypto_positions)`
- `run_equity_module()`
- `run_crypto_module()`
- `main()`

#### catalyst_screener.py
- `get_stock_data(ticker)`
- `score_short_squeeze(data)`
- `score_volume_breakout(data)`
- `score_volatility_squeeze(data)`
- `score_options_activity(data)`
- `score_technical_setup(data)`
- `score_polymarket_signal(ticker, poly_signals)`
- `scan_reddit_mentions(tickers)`
- `score_social_momentum(ticker, mentions)`
- `screen_universe(tickers, include_social, include_polymarket, include_congress)`
- `print_results(df, top_n)`
- `deep_dive(ticker, include_social, include_polymarket, include_congress)`
- `update_watchlist(results_df, watchlist_path, history_path, auto_add_threshold)`
- `main()`

#### options_flow.py
- `analyze_ticker(ticker, verbose)`
- `screen_universe(tickers, min_heat, verbose)`
- `print_summary_table(results)`
- `print_deep_dive(r)`
- `print_results(results, top)`
- `get_options_heat(ticker)`
- `get_options_heat_batch(tickers)`
- `main()`

#### squeeze_screener.py
- class `SqueezeScore`
- `fetch_stock_data(ticker)`
- `fetch_finviz_data(ticker)`
- `fetch_sec_ftd_data(force_refresh)`
- `get_ftd_for_ticker(ticker, ftd_df)`
- `score_positioning(data, finviz)`
- `score_mechanics(data, finviz, ftd_df)`
- `score_structure(data)`
- `detect_recent_squeeze(data, lookback_days)`
- `estimate_juice_target(short_pct, days_to_cover, price)`
- `compute_squeeze_score(ticker, data, finviz, ftd_df)`
- `run_screener(tickers, min_score, top_n, include_finviz, include_ftd, sort_by, verbose)`
- `print_results(results, top_n, sort_by)`
- `save_results(results, output_dir)`
- `main()`

#### polymarket_screener.py
- `fetch_active_markets(force_refresh)`
- `search_markets(query, markets, force_refresh)`
- `match_ticker_markets(ticker, catalyst_type, markets, force_refresh)`
- `search_by_catalyst_type(catalyst_type, markets)`
- `extract_signal(market, ticker, catalyst_type)`
- class `PolymarketScreener`
- `print_results(signals, top_n)`
- `export_signals_csv(signals, label)`
- `enrich_catalyst_results(catalyst_df, top_n_tickers)`
- `main()`

#### fundamental_analysis.py
- `fetch_fundamentals(ticker, use_cache)`
- `score_valuation(raw)`
- `score_growth(raw)`
- `score_quality(raw)`
- `score_balance_sheet(raw)`
- `score_earnings_catalyst(raw)`
- `score_analyst_consensus(raw)`
- `analyze_ticker(ticker, use_cache)`
- `print_summary_table(results, top_n)`
- `print_deep_dive(result)`
- `read_watchlist_tickers(path)`
- `main()`

#### midweek_scan.py
- `market_pulse()`
- `print_pulse(pulse)`
- `check_sunday_positions()`
- `print_watchlist_alerts(alerts)`
- `run_catalyst_scan(include_social)`
- `save_midweek_report(pulse, alerts, catalyst_results)`
- `main()`

#### ai_quant.py
- `get_cached_thesis(ticker, date)`
- `save_thesis(thesis)`
- `print_cache_table(days)`
- `collect_all_signals(ticker, verbose)`
- `screen_tickers(tickers, min_score, top_n, verbose, regime_filter)`
- `print_screen_table(results, watchlist_tickers)`
- `update_watchlist_from_screen(results, watchlist_path, min_tier1, min_tier2, min_tier3)`
- `analyze_ticker(ticker, verbose, raw_output, use_cache)`
- `analyze_tickers(tickers, verbose, raw_output, use_cache)`
- `print_thesis(t)`
- `print_summary_table(results)`
- `print_full_report(results)`
- `analyze_report_file(report_path, verbose)`
- `main()`

#### sec_module.py
- `get_cik(ticker)`
- `get_company_filings(cik, form_type, count)`
- `get_insider_transactions(ticker, days_back)`
- `get_insider_detail_from_search(ticker, days_back)`
- `get_institutional_filings(ticker)`
- `get_activist_filings(ticker, days_back)`
- `get_material_events(ticker, days_back)`
- `score_sec_signals(ticker)`
- `print_sec_report(ticker)`
- `scan_watchlist_insiders()`
- `main()`

#### volume_profile.py
- `get_volume_profile(ticker, period, n_bins, n_levels)`
- `main()`

#### paper_trader.py
- `init_db()`
- `compute_btc_signal()`
- `load_latest_signals()`
- `load_latest_positions()`
- `fetch_current_prices(tickers)`
- `record_snapshot(conn)`
- `compute_returns(conn)`
- `show_report(conn, weeks)`
- `show_positions(conn)`
- `reset_db(conn)`
- `main()`

#### trade_journal.py
- `init_db()`
- `compute_action_zones(ticker)`
- `show_zones(tickers)`
- `log_buy(conn, ticker, price, size_eur, notes)`
- `log_sell(conn, ticker, price, size_eur, notes)`
- `show_status(conn)`
- `show_history(conn)`
- `generate_report_section(conn)`
- `show_report(conn)`
- `main()`

#### fx_rates.py
- `get_eur_rate(currency, verbose)`
- `get_all_rates(verbose)`
- `convert_to_eur(amount, from_currency, verbose)`
- `get_ticker_currency(ticker)`
- `convert_ticker_price_to_eur(price, ticker, verbose)`
- `main()`

#### fundamentals_cache.py
- `get_cached(ticker, ttl_days)`
- `save_to_cache(ticker, data)`
- `clear_ticker(ticker)`
- `clear_all()`
- `cache_status()`
- `main()`
<!-- /AUTO:MODULE_INVENTORY -->

---

## 2. Data Sources

| Source | Modules | Auth | Rate Limit | Lag | Cost |
|--------|---------|------|-----------|-----|------|
| **yfinance** (Yahoo Finance) | All modules | None | ~100 req/min (soft) | 15 min | Free |
| **Yahoo Finance options chains** | `options_flow.py` | None | ~50 req/min | Near real-time | Free |
| **SEC EDGAR** (Form 4, 13F, 13D, 8-K, FTD CSVs) | `sec_module.py`, `squeeze_screener.py` | None (User-Agent required) | 10 req/sec (hard) | 2–7 days (filings), ~2 weeks (FTD) | Free |
| **Polymarket Gamma API** `gamma-api.polymarket.com` | `polymarket_screener.py` | None | ~1,000 req/hr | Real-time | Free |
| **Finviz** (HTML scraping) | `squeeze_screener.py`, `catalyst_screener.py` | None | Rate-limited (1s delays enforced) | ~2 weeks | Free (scraping) |
| **ECB / Frankfurter API** | `fx_rates.py` | None | Free | Daily 16:00 CET | Free |
| **Anthropic Claude API** (`claude-opus-4-6`) | `ai_quant.py` | `ANTHROPIC_API_KEY` env var | Per pricing | < 1 sec | ~$0.02–$0.04/ticker |

### Caching Layers

| Cache | File / DB | TTL | Populated by |
|-------|-----------|-----|-------------|
| AI thesis results | `ai_quant_cache.db` | Until tomorrow (date-keyed) | `ai_quant.py` |
| Quarterly fundamentals | `fundamentals_cache.db` | 30 days | `fundamentals_cache.py` |
| FX rates | `fx_cache.json` | 30 min (market hours), 24h (weekends) | `fx_rates.py` |
| Polymarket listings | `polymarket_cache.json` | 1 hour | `polymarket_screener.py` |

### Key Endpoints

```
# Polymarket
GET https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&offset=N

# SEC EDGAR
GET https://efts.sec.gov/LATEST/search-index?q=...          # Full-text search
GET https://data.sec.gov/submissions/CIK{cik}.json          # Company filings
GET https://www.sec.gov/cgi-bin/browse-edgar?...            # FTD CSV files

# Congressional trading
GET https://house-stock-watcher-data.s3.amazonaws.com/data/all_transactions.json
GET https://senate-stock-watcher-data.s3.amazonaws.com/aggregate/all_transactions.json
```

---

## 3. Ticker Universe & Discovery

### Universe Layers (evaluated in order)

1. **`EQUITY_WATCHLIST` in `config.py`** — 70 hardcoded US mega/large cap + EU ADRs. Fallback if scraping fails.
2. **`CUSTOM_WATCHLIST` in `config.py`** — User additions (empty by default).
3. **`watchlist.txt`** — Dynamic tiered list updated weekly by `ai_quant.py --watchlist` universe screener.
4. **Hardcoded screener universes in `catalyst_screener.py`:**
   - `SMALL_CAP_UNIVERSE` (45 tickers) — meme/biotech/growth
   - `MEME_UNIVERSE` (16 tickers) — high retail interest
   - `LARGE_CAP_WATCH` (10 tickers) — NVDA, TSLA, AMD, META, etc.
5. **Options flow fixed universe** in `options_flow.py` — 14 high-vol, retail-active tickers.
6. **CLI override** — `--watchlist PLTR,SOFI,RIVN` on most modules.

### Watchlist Tier Logic

```
Tier 1: score ≥ 18/25  (Grade A — high conviction)
Tier 2: score ≥ 13/25  (Grade B — monitor)
Tier 3: score ≥  8/25  (Grade C — weak signal)
Below 8: excluded from watchlist.txt
```

Score is a 0–25 breadth score across 10 signal modules, updated weekly by `ai_quant.py`.

### Net-New Ticker Discovery

- **Primary:** `ai_quant.py --watchlist` scans a broad universe (S&P 500 + Russell 1000 + custom lists), scores every ticker, and writes new entrants to `watchlist.txt` if they hit Tier 3+.
- **Volume breakout:** Any ticker in `SMALL_CAP_UNIVERSE` surging > 3× avg volume with price up will bubble to the top of `catalyst_screener.py` output regardless of prior watchlist presence.
- **Polymarket:** 92 manually-maintained keyword mappings surface tickers mentioned in prediction markets. See `polymarket_screener.py` for the full mapping dict.

### Pre-Screen Filters

Before scoring, tickers are silently dropped if:
- Fewer than 252 bars of price history available
- Average daily volume below minimum threshold (illiquid filter)
- Options chain unavailable (for options-dependent modules)
- yfinance returns all-NaN for required fields

---

## 4. Scoring & Signal Logic

### 4.1 Equity Multi-Factor Composite (`signal_engine.py`)

**Pipeline:**
1. Fetch OHLCV history (`DATA_LOOKBACK_DAYS = 400` days)
2. Compute each factor raw value
3. Z-score cross-sectionally: `(x − mean(x)) / std(x)`
4. Winsorize at ±3σ
5. Weighted sum → `composite_z`
6. Rank descending; top `max_equity_positions` = 15 form portfolio

<!-- AUTO:EQUITY_FACTORS -->
| Factor | Weight | Lookback | Logic |
|--------|--------|----------|-------|
| `momentum_12_1` | 35% | 252d long, 21d skip | 12-month return minus last month (Jegadeesh-Titman) |
| `momentum_6_1` | 20% | 126d long, 21d skip | 6-month return minus last month |
| `mean_reversion_5d` | 15% | 5d (inverted) | Short-term losers bounce |
| `volatility_quality` | 15% | 63d (inverted) | Low realized vol = quality proxy |
| `risk_adjusted_momentum` | 15% | 126d mom / 63d vol | Sharpe-like ratio |
<!-- /AUTO:EQUITY_FACTORS -->

**Position sizing:** Inverse-volatility weighted. Hard cap 8% per position. Min €500. Kelly fraction 0.25.

---

### 4.2 Crypto Trend/Momentum (`signal_engine.py`)

Signal range: −1.0 to +1.0

| Component | Weight | Logic |
|-----------|--------|-------|
| Trend score | 40% | Price vs EMA21/50/200: +1 per EMA above, normalized [−1, +1] |
| Momentum score | 30% | Weighted ROC: periods [7,14,30,60], weights [0.4,0.3,0.2,0.1], clipped [−1,+1] |
| RSI timing | 15% | RSI < 30 → +1.0; RSI > 70 → −0.5; else 0.0 |
| Trend confirmation | 15% | Trend + momentum both positive → +0.15 bonus |

**Volatility regime gate:**

<!-- AUTO:CRYPTO_VOL_GATE -->
| Ann. Vol | `vol_scale` | Effect |
|----------|-------------|--------|
| < 80% | 1.0 | Full position |
| 80–120% | 0.5 | Half position |
| > 120% | 0.0 | Cash — no position |
<!-- /AUTO:CRYPTO_VOL_GATE -->

**Action thresholds:** `> +0.3` → BUY · `> 0.0` → HOLD · `> −0.3` → REDUCE · `≤ −0.3` → SELL

---

### 4.3 Catalyst Screener (`catalyst_screener.py`)

#### Short Squeeze Setup (0–9 pts)
```
short_pct_float > 40%          +3
short_pct_float 20–40%         +2
short_pct_float 10–20%         +1
days_to_cover > 8              +2
days_to_cover 4–8              +1
float < 50M shares             +1
volume 2x surge + price +5%    +2
volume 1.5x surge + price up   +1
```

#### Volume Breakout (0–7 pts)
```
vol_ratio (5d/20d) > 3.0x      +3  EXTREME
vol_ratio 2.0–3.0x             +2  HIGH
vol_ratio 1.5–2.0x             +1  ABOVE AVG
OBV rising + price rising       +2
OBV rising + price flat         +1  hidden accumulation
up-vol > 2x down-vol (10d)     +2
up-vol > 1.3x down-vol         +1
```

---

### 4.4 Options Flow (`options_flow.py`) — "Heat Score" (0–100)

| Component | Max | Thresholds |
|-----------|-----|-----------|
| Volume spike | 30 | > 5x avg → 30 · 3–5x → 20 · 2–3x → 10 |
| IV rank (estimated) | 25 | > 80 → 25 · 50–80 → 15 · < 50 → 5 |
| Expected move | 25 | > 5% weekly → 25 · 3–5% → 15 · 1–3% → 5 |
| Put/call lean | 20 | PC ratio < 0.5 or > 2.0 → 20 |

> IV rank is estimated from 30-day realized vol vs its 52-week range (true historical IV not available free).

---

### 4.5 Squeeze Screener (`squeeze_screener.py`) — Score (0–100)

| Bucket | Weight | Factors |
|--------|--------|---------|
| Positioning | ~45% | `pct_float_short` (20%), `short_pnl_estimate` (15%) |
| Mechanics | ~35% | `days_to_cover` (15%), `volume_surge` (10%), `ftd_vs_float` (5%), `cost_to_borrow` proxy (5%) |
| Structure | ~20% | `market_cap` log-scaled (7%), `float_size` log-scaled (7%), `price_divergence` (6%) |

**Hard override:** `recent_squeeze = True` → `final_score = 0`

---

### 4.6 Fundamental Analysis (`fundamental_analysis.py`) — Score (0–100%)

6 sub-categories, each 0–4 pts (max 24 → normalized):

| Category | Key thresholds |
|----------|---------------|
| Valuation | Fwd P/E < 15 → +2; 15–25 → +1; > 60 → −1. P/S < 2 → +1. EV/EBITDA < 10 → +1 |
| Growth | Rev growth > 30% → +2; 10–30% → +1; negative → −1. EPS growth > 30% → +2 |
| Quality | Gross margin > 50% → +1. Op margin > 20% → +1. ROE > 15% → +1. ROA > 5% → +1 |
| Balance sheet | FCF positive → +1. Current ratio > 1.5 → +1. D/E < 1.0 → +1. Cash > Debt → +1 |
| Earnings | 4-quarter beat streak → +2. 1–2 beats → +1. Earnings < 30d away → +1 |
| Analyst | Rating < 2.5 → +1. > 10 analysts → +1. Target upside > 20% → +1. Strong buy → +1 |

---

### 4.7 Volume Profile (`volume_profile.py`)

```python
DEFAULT_BINS     = 60
MERGE_PCT        = 1.5    # Merge HVNs within 1.5% of each other
VALUE_AREA_PCT   = 0.70   # Value area captures 70% of volume
MIN_PEAK_PCT     = 0.08   # HVN must be ≥ 8% of max bin
```

Output: `poc_price`, `value_area_high/low`, `support_levels`, `resistance_levels`, `nearest_support/resistance`, `vwap_20d`, `vwap_50d`

---

### 4.8 Polymarket Signal (`polymarket_screener.py`)

| Component | Weight | Logic |
|-----------|--------|-------|
| Consensus strength | 30% | Distance from 50%; ≥ 70% or ≤ 30% = max |
| 24h volume | 25% | High ≥ $50k · Medium ≥ $10k · Low ≥ $1k |
| Liquidity | 25% | High ≥ $100k · Medium ≥ $10k · Low ≥ $1k |
| Time to resolution | 20% | Closer = higher relevance |

Blocks: sports, entertainment, celebrity markets. Political markets require a financial keyword anchor.

---

## 5. Buy/Sell Decision Flow

### Module Dependency Graph

```
yfinance / SEC EDGAR / Gamma API / FINRA ATS
        │
        ├─► signal_engine.py ──────────────────────────────────────────┐
        ├─► catalyst_screener.py   ──────────────────────────────────── │
        ├─► options_flow.py   ───────────────────────────────────────── │
        ├─► squeeze_screener.py  ──────────────────────────────────────►│
        ├─► dark_pool_flow.py  ─────────────────────────────────────────►│
        ├─► volume_profile.py  ─────────────────────────────────────────►│
        ├─► fundamental_analysis.py  ───────────────────────────────────►│
        ├─► sec_module.py  ─────────────────────────────────────────────►│
        └─► polymarket_screener.py  ─────────────────────────────────────►│
                                                                         │
                                                                   ai_quant.py
                                                             (aggregation layer)
                                                                         │
                                                              Structured JSON thesis
                                                          direction · conviction · levels
                                                                         │
                                                               ► paper_trader.py
                                                               ► trade_journal.py
                                                               ► signal_report_YYYYMMDD.txt
```

### Conflict Resolution

There is no automated conflict resolution. Contradictory signals are passed raw into `ai_quant.py`, where Claude is explicitly prompted to flag conflicts and produce a single directional view. The operator makes the final decision.

**Hard overrides (code-level):**
- Crypto: `vol_scale = 0.0` when annualized vol > 120% — zeroes position regardless of signal
- Squeeze: `final_score = 0` when `recent_squeeze = True` — protects against entering post-squeeze

---

## 6. Live Configuration

<!-- AUTO:CONFIG -->
### Portfolio Parameters
| Parameter | Value |
|-----------|-------|
| `PORTFOLIO_NAV` | 50,000 EUR |
| `EQUITY_ALLOCATION` | 65.0% |
| `CRYPTO_ALLOCATION` | 25.0% |
| `CASH_BUFFER` | 10.0% |

### Risk Parameters
| Parameter | Value |
|-----------|-------|
| `kelly_fraction` | 0.25 |
| `max_position_equity_pct` | 8.0% |
| `max_position_crypto_pct` | 10.0% |
| `max_equity_positions` | 15 |
| `max_crypto_positions` | 5 |
| `equity_cost_bps` | 15 bps |
| `crypto_cost_bps` | 30 bps |
| `weekly_dd_warning` | -3.0% |
| `monthly_dd_stop` | -8.0% |
| `min_position_eur` | €500 |

### Equity Factor Weights
| Factor | Weight | Lookback |
|--------|--------|----------|
| `momentum_12_1` | 35% | 252d long, 21d skip |
| `momentum_6_1` | 20% | 126d long, 21d skip |
| `mean_reversion_5d` | 15% | 5d (inverted) |
| `volatility_quality` | 15% | 63d (inverted) |
| `risk_adjusted_momentum` | 15% | 126d mom / 63d vol |

### Crypto Parameters
| Parameter | Value |
|-----------|-------|
| `ema_fast` | 21 |
| `ema_slow` | 50 |
| `ema_trend` | 200 |
| `roc_periods` | [7, 14, 30, 60] |
| `roc_weights` | [0.4, 0.3, 0.2, 0.1] |
| `rsi_period` | 14 |
| `rsi_oversold` | 30 |
| `rsi_overbought` | 70 |
| `vol_threshold_high` | 80% |
| `vol_threshold_extreme` | 120% |
| `vol_scale_factor` | 0.5 |

### Polymarket Parameters
| Parameter | Value |
|-----------|-------|
| `api_base_url` | https://gamma-api.polymarket.com |
| `cache_ttl_hours` | 1 |
| `min_volume_24h` | $500 |
| `min_liquidity` | $500 |
| `max_days_to_resolution` | 180 |
| `strong_consensus_high` | 70% |
| `strong_consensus_low` | 30% |
| `volume_high` | $50,000 |
| `liquidity_high` | $100,000 |

### Data Settings
| Parameter | Value |
|-----------|-------|
| `DATA_LOOKBACK_DAYS` | 400 |
| `YAHOO_FINANCE_TIMEOUT` | 30s |
| `OUTPUT_DIR` | ./signals_output |
<!-- /AUTO:CONFIG -->

---

## 7. Output & Reporting

### Files Produced Per Run

| Output | Path | Format | Producer |
|--------|------|--------|---------|
| Master signal report | `signal_reports/signal_report_YYYYMMDD.txt` | Plain text | `run_master.sh` |
| Equity signals | `signals_output/equity_signals_YYYYMMDD.csv` | CSV | `signal_engine.py` |
| Equity positions | `signals_output/equity_positions_YYYYMMDD.csv` | CSV | `signal_engine.py` |
| Crypto signals | `signals_output/crypto_signals_YYYYMMDD.csv` | CSV | `signal_engine.py` |
| Crypto positions | `signals_output/crypto_positions_YYYYMMDD.csv` | CSV | `signal_engine.py` |
| AI quant theses | `ai_quant_cache.db` | SQLite | `ai_quant.py` |
| Paper trade snapshot | `paper_trades.db` | SQLite | `paper_trader.py` |
| Trade log | `trade_journal.db` | SQLite | `trade_journal.py` |

### AI Quant Thesis Schema (per ticker)

```json
{
  "direction":        "BULL | BEAR | NEUTRAL",
  "conviction":       "1–5",
  "time_horizon":     "days | weeks | months",
  "entry_low":        182.50,
  "entry_high":       185.00,
  "stop_loss":        176.00,
  "target_1":         195.00,
  "target_2":         210.00,
  "position_size_pct": 5,
  "catalysts":        ["...", "...", "..."],
  "risks":            ["...", "...", "..."],
  "thesis":           "2–3 sentence narrative"
}
```

### Equity Signal CSV Columns

`ticker, rank, composite_z, momentum_12_1, momentum_6_1, mean_reversion_5d, volatility_quality, risk_adj_momentum, weight_pct, position_eur`

### Paper Trader Terminal Output

- Weekly return % (portfolio vs SPY benchmark)
- Cumulative P&L (EUR)
- Sharpe ratio (annualized)
- Max drawdown
- Hit rate (% winning weeks)
- Forward return analysis at 1/2/3/4 weeks post-signal

### No Frontend Dashboard

No React or web frontend exists in this codebase. All output is terminal, CSV, and flat text/markdown report files.

---

## 9. Daily Top-20 Ranking Feature

> Added 2026-04-03. Covers backend, API, and dashboard end-to-end.

### What it does

Every morning, Step 13c in `run_master.sh` calls `run_daily_top20_pipeline()` which:

1. Pulls the full 50+ candidate universe (same pool as Step 13a) from `select_top_tickers()`.
2. Applies hard filters: ADV ≥ $12M, price ≥ $8, volatility cap (ATR or hist-vol), optional event-risk exclusion.
3. Clusters survivors via greedy selection: max 2 names per GICS sector, pairwise 60-day correlation < 0.65.
4. Ranks the top 20 survivors by `priority_score` and computes volatility-parity weights (1.5% portfolio vol target, 2% hard cap).
5. Compares to the previous day's ranking to produce `rank_change` strings (`+3`, `-2`, `NEW`, `—`).
6. Upserts 20 rows into the Supabase `daily_rankings` table (primary key: `run_date, rank`).

### Files changed / added

| File | Change |
|------|--------|
| `utils/trade_selector_4w.py` | Added `generate_daily_top20_ranking()`, `run_daily_top20_pipeline()`, `get_latest_top20()`, `get_top20_history()` |
| `run_master.sh` | Added Step 13c (after Step 13a, before 13b) |
| `dashboard/api/main.py` | Added `GET /api/rankings/latest` and `GET /api/rankings/history` |
| `dashboard/frontend/src/lib/api.ts` | Added `Top20RankingRow`, `RankingsLatestResponse`, `RankingsHistoryResponse` interfaces + `rankingsLatest()`, `rankingsHistory()` methods |
| `dashboard/frontend/src/components/Top20RankingTable.tsx` | New component — table, rank-change pills, weight tooltip, side panel with Recharts rank-history chart, CSV export |
| `dashboard/frontend/src/pages/RankingsPage.tsx` | New page wrapper |
| `dashboard/frontend/src/App.tsx` | Route `/rankings`, keyboard shortcut `k` |
| `dashboard/frontend/src/components/layout/Shell.tsx` | Nav item "Daily Top-20" with `ListOrdered` icon |
| `test_rankings_endpoints.sh` | Smoke-test script for the two new endpoints |

### Supabase table

```sql
CREATE TABLE IF NOT EXISTS daily_rankings (
  run_date       date     NOT NULL,
  rank           smallint NOT NULL,
  ticker         text     NOT NULL,
  priority_score real, final_score real,
  weight real, raw_weight real, cap_hit boolean,
  sector text, hist_vol_60d real, adv_20d real,
  rank_change text, rank_yesterday smallint,
  PRIMARY KEY (run_date, rank)
);
```

### API endpoints

| Endpoint | Cache | Description |
|----------|-------|-------------|
| `GET /api/rankings/latest` | 5 min | Full 20-row snapshot for the most recent `run_date` |
| `GET /api/rankings/history?ticker=NVDA&days=30` | 5 min | Rank history, optionally filtered to one ticker |

Force-flush both after a backfill: `curl -X POST http://localhost:8000/api/cache/invalidate`

### Dashboard usage

Navigate to `/rankings` (sidebar shortcut `k`). Click any row to open a side panel with a 30/60/90-day rank-history line chart (rank 1 at top). Hover the **Weight** cell to see uncapped raw weight; amber **CAP** badge appears when the 2% hard limit was hit. Use the download icon to export the current snapshot as CSV.

### Monitoring

- `SELECT MAX(run_date), COUNT(*) FROM daily_rankings;` — should advance by one date each morning with exactly 20 rows.
- If `rank_change` is `—` for all rows after day 2, the `WHERE run_date < %s` date comparison may have a timezone mismatch — cast with `::date` in the query.
- All DB errors in the pipeline are non-fatal: the ranking is returned regardless and a `WARNING` is logged to `logs/`.

---

## 8. Known Issues

> See [../KNOWN_ISSUES.md](../KNOWN_ISSUES.md) for the full living issues tracker.

**Critical gaps (free-data limitations):**
- True historical IV unavailable → IV rank is approximated
- % float on loan and cost-to-borrow are proprietary (Ortex/S3)
- Point-in-time fundamentals not available → backtest look-ahead bias

**Potentially broken:**
- `--social` flag in `catalyst_screener.py` — Reddit API changes may have broken this
- Multi-asset crypto signals retired (−0.20 Sharpe); BTC-only 200-MA binary used instead

**Architecture debt:**
- No automated conflict resolution between modules
- SQLite concurrency risk if modules run in parallel
- Polymarket ticker mappings are manually maintained (92 entries, no auto-update)
- `watchlist_history.json` is write-only (nothing reads it downstream)

---

## 9b. Lane-Based Universe Routing (TRD-065)

### Lane values

| Lane | Meaning | Selection behavior |
|---|---|---|
| `execution_core` | High-quality, immediately tradeable | Full priority weight (1.0×) |
| `execution_high_beta` | Wider vol/beta tolerance | Slight discount (0.9×) |
| `research_broad` | Passes research thresholds; coverage universe | Significant discount (0.6×) |
| `lane_excluded` | Below all lane thresholds (too cheap/illiquid) | Not persisted to research_lane_candidates; **hard-gated out of AI selection** |
| `hard_excluded` | Extreme ATR% (>15) or beta (>6.0) | Dropped at quality gate; **hard-gated out of AI selection** |

`research_broad` is only assigned when the ticker **actually passes** the `LANE_THRESHOLDS["research_broad"]` thresholds. A ticker that passes the hard-drop ceiling but fails every lane threshold is `lane_excluded`, not `research_broad`.

### Selection eligibility and priority score multipliers

`lane_excluded` and `hard_excluded` tickers are **hard-gated out** of the AI selection funnel in `ticker_selector.py` — they are skipped before scoring, not just discounted.

**Open-position override policy** (implemented in Phase 2 hardening):

| Lane | Non-open-position | Open position (`always_include`) |
|---|---|---|
| `lane_excluded` | Hard-gated out | **Allowed** — live positions need review; flagged as `open_position_lane_override=True` and `degraded_position_review_required=True` in selection output; `logger.warning` emitted |
| `hard_excluded` | Hard-gated out | **Also blocked** — hard_excluded is a PM hard stop; always_include does not override it; `logger.warning` emitted to alert PM to resolve the discrepancy |

Rationale: `lane_excluded` is a routing classification (threshold failure) — the underlying may still have an open position worth reviewing. `hard_excluded` is an extreme-volatility ceiling — trading it is dangerous regardless of open position status. The PM must resolve `hard_excluded` open positions manually.

When `open_position_lane_override=True` is set, the ticker appears in the printed selection table with the label `← DEGRADED OPEN POS — lane_excluded, review required`. The count and ticker list are emitted at `INFO` level after the scoring loop.

For eligible lanes, priority score multipliers are applied after `compute_priority_score`: `always_include` tickers are exempt.

### Nasdaq-100 source (TRD-062)

There is no reliable free live endpoint for Nasdaq-100 constituents. The curated `_NASDAQ100_CORE` list in `universe_builder.py` (updated 2026-Q2, ~80 tickers) is the **primary source**. On first fetch it is written to disk cache and subsequent runs use the cache. This is clearly logged: `"[nasdaq100] Using curated snapshot"`. The list should be refreshed quarterly by checking `https://www.invesco.com/qqq-etf/en/about.html` or the Nasdaq official constituent list.

### S&P SmallCap 600 source (TRD-064)

`sp600` (iShares Core S&P Small-Cap ETF, ticker IJR) is an active entry in `UNIVERSE_INDICES`. It uses the same BlackRock fund-document fetch path as `sp500`, `sp400`, and `russell2000`. The S&P 600 applies quality screens (profitability, liquidity) at index construction time, making it a cleaner small-cap input than undifferentiated small-cap breadth. Tickers proceed through the lane model — the index origin grants no execution bypass. Source label `"sp600"` appears in `_TICKER_SOURCES` and `ranked_universe.json`.

**Portfolio ID note (important):** The iShares product page URL for IJR contains the number `239765` (e.g. `ishares.com/us/products/239765/...`). This is **not** the same as the BlackRock fund-document API `portfolioId`. The correct `portfolioId` for the `get-fund-document` API endpoint is **`239774`** — that is the value set in `_INDEX_PORTFOLIO_IDS["sp600"]`. Using `239765` in the API incorrectly returns the "iShares Core 40/60 Moderate Allocation ETF" (a multi-asset fund with ~32,500 rows including currencies, MBS, and futures). This was identified and fixed during the 2026-06-07 PM validation run.

`sp1500` (virtual composite of sp500+sp400+sp600) remains commented out in `UNIVERSE_INDICES` to avoid double-counting while sp600 is active standalone.

### Broad research sources (TRD-056 / TRD-063)

Two FTP-sourced discovery tiers expand the candidate pool beyond the quality indices:

| Source | FTP file | Exchanges covered |
|---|---|---|
| `nasdaq_broad` | `nasdaqlisted.txt` | Nasdaq (all tiers) |
| `nyse_listed` | `otherlisted.txt` | NYSE, NYSE American, NYSE Arca, BATS, IEX, other non-Nasdaq US |

Both are **additive, research-only discovery sources** — they expand the momentum pre-screen candidate pool but do not bypass the lane model. Both are listed in `_BROAD_RESEARCH_SOURCES`.

**Shared instrument-hygiene constant (`_JUNK_NAME_TERMS`):**  All name-based exclusions use a shared tuple. `_NASDAQ_BROAD_EXCLUDE_NAME_TERMS` is a backwards-compat alias pointing to the same object.

| Term class | Examples |
|---|---|
| Warrants | `WARRANT`, ` WTS`, ` WT ` |
| Preferreds | `PREFERRED`, ` PREF `, ` PRF `, ` PFD ` |
| Rights/Units | ` RIGHT `, ` RIGHTS`, ` UNIT `, ` UNITS` |
| Exchange-traded products | `EXCHANGE TRADED`, ` ETF`, ` ETN `, ` ETP ` |
| Debt instruments | `NOTE DUE`, `NOTES DUE`, `DEBENTURE`, `SENIOR NOTE`, `SUBORDINATED` |
| Closed-end funds | `CLOSED-END` |

**Filters applied** (in `_fetch_nasdaq_broad` / `_fetch_nyse_listed`):

| Filter | `nasdaqlisted.txt` | `otherlisted.txt` |
|---|---|---|
| ETF exclusion | `ETF` col == `"N"` | `ETF` col == `"N"` |
| Test issue | `Test Issue` col == `"N"` | `Test Issue` col == `"N"` |
| Financial health | `Financial Status` col == `"N"` | *(column absent — not available)* |
| Symbol length | ≤ 5 characters | ≤ 5 characters |
| Name-based exclusion | `_JUNK_NAME_TERMS` applied | `_JUNK_NAME_TERMS` applied |

**Coverage policy:** The full eligible set is ingested — no arbitrary ticker cap. Downstream liquidity filters (`_apply_liquidity_filter`) and lane classification (`classify_ticker_lane`) provide the real quality gate.

**Source-aware lane clamping:** Both sources are declared in `_BROAD_RESEARCH_SOURCES`. In `_compute_prescreen_scores`, if a ticker's entire source set is a subset of `_BROAD_RESEARCH_SOURCES` (i.e. it comes from `nasdaq_broad` and/or `nyse_listed` only), its lane is clamped to `research_broad` even if price/ADV/history would qualify it for `execution_core` or `execution_high_beta`. Tickers that also appear in a core quality index (sp500, russell1000, etc.) are not affected.

**Graceful failure:** Network failure with no cache returns `[]`. Both sources are additive — the pipeline runs normally without them.

**Source attribution:** Tickers contributed by `nasdaq_broad` or `nyse_listed` get those source labels in `_TICKER_SOURCES` and in `ranked_universe.json["sources"]`.

### Source attribution (`_TICKER_SOURCES`)

`universe_builder._TICKER_SOURCES` is a module-level dict populated as a side effect of `build_master_universe()`. It maps `ticker → [list of index names]`, recording every index that contributed the ticker. Used in `ranked_universe.json` as the `sources` field. Example:

```json
"AAPL": {"lane": "execution_core", "sources": ["russell1000", "sp500", "nasdaq_broad"], ...}
"SMCX": {"lane": "research_broad", "sources": ["sp600"], ...}
```

This enables PM-level comparison: which tickers come only from broad Nasdaq vs. established quality indices.

### Issuance states (TRD-066)

| State | Condition |
|---|---|
| `ACTIVE_THESIS` | BULL/BEAR direction, conviction ≥ 2, **and** executable geometry present after normalization |
| `WATCH_ONLY` | Low conviction (1), pre-earnings hold, or geometry missing/non-executable after normalization |
| `NO_TRADE` | NEUTRAL direction, or conviction 0 |
| `SUPPRESSED` | `skip_ai_synthesis=True` — no AI call was made |

`_apply_deterministic_geometry` normalizes stop/targets **before** `_get_issuance_state` is called. If geometry cannot be made executable (e.g. all price fields are None), the thesis is downgraded to `WATCH_ONLY` rather than `ACTIVE_THESIS`.

### Research-lane advancement marker (TRD-057)

`research_lane_candidates` records the full prescreened cohort. When a ticker proceeds to actual AI synthesis (not suppressed), `advanced_to_ai=TRUE` is set via `mark_research_candidate_advanced()` in `ai_quant.analyze_ticker()`. The call degrades silently on DB unavailability.

---

## 9c. Universe Coverage and Qualification Analytics (TRD-059)

### Funnel metrics table

`funnel_metrics` (PK: `run_date`) captures a daily snapshot of how many tickers pass each stage of the pipeline:

| Column | Written by | Meaning |
|---|---|---|
| `raw_universe_count` | `universe_builder.py` | Total tickers in the built universe before any filtering |
| `hard_excluded_count` | `universe_builder.py` | Dropped at quality gate (extreme ATR%/beta) |
| `lane_excluded_count` | `universe_builder.py` | Below all lane thresholds |
| `execution_core_count` | `universe_builder.py` | Assigned to execution_core lane |
| `execution_high_beta_count` | `universe_builder.py` | Assigned to execution_high_beta lane |
| `research_broad_count` | `universe_builder.py` | Assigned to research_broad lane |
| `prescreened_count` | `universe_builder.py` | Tickers sent forward to screeners (after lane routing) |
| `agreement_eligible_count` | `ai_quant.py` | Passed min_agreement threshold |
| `ai_selected_count` | `ai_quant.py` | Tickers that reached AI synthesis (post-gate: QUARANTINE skips not included) |
| `active_thesis_count` | `ai_quant.py` | Issued as ACTIVE_THESIS |
| `watch_only_count` | `ai_quant.py` | Issued as WATCH_ONLY |
| `suppressed_count` | `ai_quant.py` | Suppressed (skip_ai_synthesis) |
| `no_trade_count` | `ai_quant.py` | Issued as NO_TRADE |
| `bull_count` / `bear_count` / `neutral_count` | `ai_quant.py` | Direction breakdown of issued theses |
| `excluded_by_source` | `universe_builder.py` | JSONB — `{"hard_excluded": N, "lane_excluded": N}` breakdown |
| `suppression_reasons` | `ai_quant.py` | JSONB — per-reason count of non-ACTIVE_THESIS outcomes |
| `candidates_by_lane` | `universe_builder.py` | JSONB — prescreened candidate count per lane (execution_core, research_broad, …) |
| `candidates_by_source` | `universe_builder.py` | JSONB — prescreened candidate count per source index; a ticker in N sources contributes to N buckets |
| `broad_source_only_candidates` | `universe_builder.py` | Count of prescreened candidates whose entire source set is within `_BROAD_RESEARCH_SOURCES` |
| `ai_selected_by_lane` | `ai_quant.py` | JSONB — post-gate synthesized tickers by lane (excludes QUARANTINE skips) |
| `ai_selected_by_source` | `ai_quant.py` | JSONB — post-gate synthesized tickers by source (excludes QUARANTINE skips) |
| `broad_source_only_ai_selected` | `ai_quant.py` | Count of post-gate synthesized tickers that were broad-source-only (excludes QUARANTINE skips) |

`suppression_reasons` keys (all tickers here were in the pre-gate shortlist but did not produce ACTIVE_THESIS):
- `governance_quarantine` — ticker blocked at synthesis stage by PM governance; counted in pre-gate shortlist but excluded from `ai_selected_count` and all `ai_selected_by_*` fields
- `skip_ai_synthesis` — `skip_ai_synthesis` / `skip_claude` flag set on resolved signal
- `neutral_direction` — direction is NEUTRAL → NO_TRADE
- `no_conviction` — conviction is 0 or missing → NO_TRADE
- `low_conviction` — conviction ≤ 1 → WATCH_ONLY
- `pre_earnings_hold` — `pre_earnings_hold` override flag active → WATCH_ONLY
- `bear_below_threshold` — BEAR with conviction < `BEAR_MIN_CONVICTION` → WATCH_ONLY
- `no_geometry` — conviction meets threshold but geometry is not executable → WATCH_ONLY

WATCH_ONLY reason classification is done by `_watch_only_reason(thesis, resolved)` in `ai_quant.py`, stored as `thesis["issuance_reason"]` at synthesis time. The reason mirrors `_get_issuance_state()` order exactly so it is always accurate.

### Partial-write COALESCE pattern

Both `universe_builder.py` and `ai_quant.py` call `persist_funnel_metrics()` at different pipeline stages. The upsert uses `COALESCE(EXCLUDED.col, funnel_metrics.col)` so the second write preserves columns set by the first write rather than overwriting them with NULL. Each caller only provides the columns it knows about.

### API endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/funnel/summary` | Today's funnel row; degrades gracefully if missing |
| `GET /api/funnel/history?days=N` | Last N days of rows (max 90), newest-first |

### Dashboard

A "Funnel" tab on the Screeners page (`ScreenersPage.tsx`) visualises the daily funnel. It shows raw→prescreened→AI→active flow cards, lane distribution bars, direction/issuance bars, and a 7-day history table.

---

## 9d. AI Qualification Gate Recalibration and Adaptive Capacity (TRD-058)

### Bear direction penalty

Tickers with `pre_resolved_direction == "BEAR"` receive a `AI_QUANT_BEAR_DIRECTION_PENALTY = 0.85` multiplier applied to their `priority_score` in `compute_priority_score()`. This reflects lower structural edge on the short side and reduces their competition for AI slots against bullish signals.

### Adaptive capacity

| Config constant | Default | Meaning |
|---|---|---|
| `AI_QUANT_CAPACITY_MIN` | 2 | Floor: never select fewer than this many normal-mode tickers |
| `AI_QUANT_CAPACITY_MAX` | 8 | Ceiling: cap expanded capacity at this on strong-signal days |
| `AI_QUANT_SCORE_THRESHOLD_HIGH` | 70.0 | Priority score above which a ticker is counted as "strong" |

Formula in `select_top_tickers()` (Option A — max_tickers as hard cap):
```
_caller_cap    = min(max_tickers, CAPACITY_MAX)
_floor         = min(CAPACITY_MIN, max_tickers)
n_normal_slots = max(_floor, min(_caller_cap, strong_count))
```

`max_tickers` is a **hard upper bound**. Adaptive logic operates within `[_floor, _caller_cap]`:
- weak day (strong_count = 0): selects `_floor` = min(CAPACITY_MIN, max_tickers)
- normal day: selects `strong_count`, bounded by the range above
- strong day (strong_count ≥ _caller_cap): selects `_caller_cap` = min(max_tickers, CAPACITY_MAX)

`always_include` (open positions) are additive on top of `n_normal_slots` and are not bounded by the adaptive formula.

---

## 9e. Short-Side PM Operating Policy (TRD-067)

### Direction-specific conviction threshold

BEAR theses are held to a higher conviction bar than BULL theses before issuance as `ACTIVE_THESIS`:

| Direction | `min_conviction` | Below threshold → |
|---|---|---|
| BULL | `BULL_MIN_CONVICTION = 2` | `WATCH_ONLY` |
| BEAR | `BEAR_MIN_CONVICTION = 3` | `WATCH_ONLY` (auto-downgrade) |

Logic lives in `_get_issuance_state()` in `ai_quant.py`. A BEAR thesis with conviction=2 is automatically downgraded to `WATCH_ONLY` even if geometry is otherwise executable. The PM must manually upgrade it by re-running synthesis or overriding.

The issuance state table from TRD-066 is now amended:

| State | BULL condition | BEAR condition |
|---|---|---|
| `ACTIVE_THESIS` | direction=BULL, conviction ≥ 2, executable geometry | direction=BEAR, conviction ≥ 3, executable geometry |
| `WATCH_ONLY` | conviction=1, pre-earnings, or geometry missing | conviction ≤ 2, pre-earnings, or geometry missing |

---

## 9f. Ticker Governance Policy (TRD-068)

### Governance states

| State | Meaning | Effect on AI selection |
|---|---|---|
| `A_LIST` | PM-promoted; elevated attention | Priority score × 1.15 (non-open positions only) |
| `STANDARD` | Default; no adjustment | None |
| `PROBATION` | PM-flagged for concern; reduced weight | Priority score × 0.70 (non-open positions only) |
| `QUARANTINE` | Hard gate; explicitly blocked | Excluded from scoring AND from AI synthesis — no `always_include` override |

### Governance vs. lane routing

Lane routing (`hard_excluded`, `lane_excluded`) is a structural data-quality gate — it reflects whether a ticker is too illiquid or volatile to trade. Governance is a PM intent gate — it reflects deliberate trading decisions about specific tickers. The two systems are independent:
- `lane_excluded` gates can be overridden by `always_include` (open positions) — see open-position override policy above.
- `hard_excluded` gates **cannot** be overridden by `always_include` — hard_excluded is an extreme-volatility hard stop. See open-position override policy above.
- `QUARANTINE` **cannot** be overridden by `always_include` — if the PM quarantines a ticker, AI synthesis is blocked even for open positions. This is intentional and explicit.

### Where governance is applied

1. `ticker_selector.py` → `select_top_tickers()`: QUARANTINE tickers are hard-gated before scoring; A_LIST/PROBATION multipliers are applied after lane multiplier, non-open-positions only.
2. `ai_quant.py` → `_run_top_n_mode()`: QUARANTINE tickers are skipped before API call even if they appeared in the selected list.

### Storage and API

`ticker_governance` table (PK: `ticker`). Managed via:
- `utils/supabase_persist.py`: `fetch_ticker_governance()`, `set_ticker_governance()`, `remove_ticker_governance()`
- `GET /api/governance` — all non-STANDARD entries with metadata
- `POST /api/governance/{ticker}` — set state (accepts `governance_state`, `reason`, `notes`)
- `DELETE /api/governance/{ticker}` — remove (reverts to STANDARD behavior)
