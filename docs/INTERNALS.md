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
| [congress_trades.py](../congress_trades.py) | 605 | CONGRESSIONAL TRADING MODULE v1.0 |
| [max_pain.py](../max_pain.py) | 264 | MAX PAIN — Options Expiration Price Target |
| [volume_profile.py](../volume_profile.py) | 354 | VOLUME PROFILE — Support & Resistance via Volume-at-Price |
| [cross_asset_divergence.py](../cross_asset_divergence.py) | 368 | CROSS-ASSET DIVERGENCE SIGNAL |

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

#### congress_trades.py
- `fetch_house_trades(days_back)`
- `fetch_senate_trades(days_back)`
- `get_all_trades(days_back)`
- `get_trades_for_ticker(ticker, days_back)`
- `score_congress_signal(ticker, days_back)`
- `print_ticker_report(ticker)`
- `print_watchlist_scan()`
- `print_top_traders()`
- `main()`

#### max_pain.py
- `get_max_pain(ticker, n_expirations)`
- `main()`

#### volume_profile.py
- `get_volume_profile(ticker, period, n_bins, n_levels)`
- `main()`

#### cross_asset_divergence.py
- `get_cross_asset_signal(ticker, period, ref_symbols, di_length, ma_length, threshold_bot, threshold_top, spike_ratio)`
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
| **Yahoo Finance options chains** | `options_flow.py`, `max_pain.py` | None | ~50 req/min | Near real-time | Free |
| **SEC EDGAR** (Form 4, 13F, 13D, 8-K, FTD CSVs) | `sec_module.py`, `squeeze_screener.py` | None (User-Agent required) | 10 req/sec (hard) | 2–7 days (filings), ~2 weeks (FTD) | Free |
| **House/Senate Stock Watcher** | `congress_trades.py` | None | ~50 req/min | 30–45 days (STOCK Act) | Free |
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

### 4.7 Max Pain (`max_pain.py`)

```python
# For each candidate strike S:
call_pain(S) = Σ (S − K) × OI_calls   for all K < S
put_pain(S)  = Σ (K − S) × OI_puts    for all K > S
total_pain(S) = call_pain(S) + put_pain(S)
max_pain = argmin(total_pain)
```

Signal strength: HIGH if ≤ 7 DTE and total OI > 10,000. Pin risk zone: ±0.5% around max pain.

---

### 4.8 Volume Profile (`volume_profile.py`)

```python
DEFAULT_BINS     = 60
MERGE_PCT        = 1.5    # Merge HVNs within 1.5% of each other
VALUE_AREA_PCT   = 0.70   # Value area captures 70% of volume
MIN_PEAK_PCT     = 0.08   # HVN must be ≥ 8% of max bin
```

Output: `poc_price`, `value_area_high/low`, `support_levels`, `resistance_levels`, `nearest_support/resistance`, `vwap_20d`, `vwap_50d`

---

### 4.9 Cross-Asset Divergence (`cross_asset_divergence.py`)

```python
# References: RSP (equity breadth), HYG (risk appetite), UUP (dollar)
bot_line = weighted_avg(ref_plusDI  / stock_plusDI)   # refs surge, stock lags → bottom
top_line = weighted_avg(ref_minusDI / stock_minusDI)  # refs drop, stock doesn't → top

# Signal fires when:
# 1. line > 0.65
# 2. line > 1.8 × 20-bar SMA of that line
```

Output signal: `"BOTTOM"` | `"TOP"` | `"NEUTRAL"`

---

### 4.10 Polymarket Signal (`polymarket_screener.py`)

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
yfinance / SEC EDGAR / Gamma API / House-Senate Watcher
        │
        ├─► signal_engine.py ──────────────────────────────────────────┐
        ├─► catalyst_screener.py   ──────────────────────────────────── │
        ├─► options_flow.py   ───────────────────────────────────────── │
        ├─► squeeze_screener.py  ──────────────────────────────────────►│
        ├─► max_pain.py   ──────────────────────────────────────────────►│
        ├─► volume_profile.py  ─────────────────────────────────────────►│
        ├─► cross_asset_divergence.py  ─────────────────────────────────►│
        ├─► fundamental_analysis.py  ───────────────────────────────────►│
        ├─► sec_module.py  ─────────────────────────────────────────────►│
        ├─► congress_trades.py  ────────────────────────────────────────►│
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
- `catalyst_backtest.py` referenced in `run_master.sh` Step 7 — existence unconfirmed
- Multi-asset crypto signals retired (−0.20 Sharpe); BTC-only 200-MA binary used instead

**Architecture debt:**
- No automated conflict resolution between modules
- SQLite concurrency risk if modules run in parallel
- Polymarket ticker mappings are manually maintained (92 entries, no auto-update)
- `watchlist_history.json` is write-only (nothing reads it downstream)
