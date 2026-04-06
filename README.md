# Weekly Signal Engine v1.0

## Multi-Factor Equity Screener + Crypto Trend/Momentum Signal Generator

A systematic screening tool designed for weekly (Sunday evening) signal generation with sub-€50K portfolios. Two independent modules under one risk framework.

---

## ⚠️ IMPORTANT DISCLAIMERS

- **This is NOT investment advice.** All signals are informational and for research/educational purposes only.
- **No automated execution.** This tool generates signals — YOU decide whether and how to act.
- **Consult a licensed financial advisor** before making investment decisions.
- **Past performance does not predict future results.**
- **Yahoo Finance data** is used for prototyping. For production use, consider Bloomberg, Refinitiv, or Polygon.io.

---

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Your Universe

Edit `config.py`:
- Add tickers to `CUSTOM_WATCHLIST`
- Adjust `PORTFOLIO_NAV` to your actual portfolio size
- Tune factor weights in `EQUITY_FACTORS` if you have conviction
- Set `CRYPTO_TICKERS` to the coins you want to track

### 3. Run

```bash
# Full run — both equity and crypto modules
python signal_engine.py

# Equity screener only
python signal_engine.py --equity-only

# Crypto signals only
python signal_engine.py --crypto-only

# Add custom tickers on the fly
python signal_engine.py --watchlist PLTR,SOFI,RIVN,COIN

# Override portfolio size
python signal_engine.py --nav 30000
```

### 4. Review Output

- Console output shows ranked signals + position sizing
- CSV files are saved to `./signals_output/` with date stamps
- Files generated:
  - `equity_signals_YYYYMMDD.csv` — Full factor scores for all equities
  - `crypto_signals_YYYYMMDD.csv` — Trend/momentum scores for all crypto
  - `equity_positions_YYYYMMDD.csv` — Recommended equity allocations
  - `crypto_positions_YYYYMMDD.csv` — Recommended crypto allocations

---

## Signal Methodology

### Equity Multi-Factor Composite

| Factor | Weight | Logic |
|---|---|---|
| 12-1 Month Momentum | 35% | Jegadeesh-Titman: 12m return, skip last month |
| 6-1 Month Momentum | 20% | Medium-term momentum confirmation |
| 5-Day Mean Reversion | 15% | Short-term contrarian (inverted) |
| Low Volatility | 15% | Quality proxy — lower vol scores higher |
| Risk-Adjusted Momentum | 15% | Momentum / Volatility (Sharpe-like) |

All factors are Z-scored cross-sectionally and winsorized at ±3σ.

### Crypto Trend/Momentum

| Component | Weight | Logic |
|---|---|---|
| Trend Score | 40% | Price vs 21/50/200 EMA stack |
| Multi-Period Momentum | 30% | Weighted ROC: 7d/14d/30d/60d |
| RSI Timing | 15% | Oversold = better entry |
| Trend Confirmation | 15% | Bonus when trend + momentum agree |

**Volatility Regime Filter:**
- Normal (< 80% ann. vol): Full position
- High (80-120% ann. vol): Half position
- Extreme (> 120% ann. vol): Zero position — cash

### Position Sizing

- Quarter-Kelly (conservative)
- Inverse-volatility weighted within the selected positions
- Hard caps: 8% per equity, 10% per crypto
- Minimum position size: €500 (below this, frictional costs dominate)

---

## Recommended Weekly Workflow

1. **Sunday evening:** Run `python signal_engine.py`
2. **Review signals:** Check the top-ranked equities and crypto BUY signals
3. **Cross-reference:** Validate against your own thesis / news / catalysts
4. **Monday morning:** Execute any changes through your broker
5. **Log decisions:** Track what you bought/sold and WHY (for future review)

---

## What This Tool Does NOT Do

- ❌ Automatically place trades
- ❌ Monitor positions in real-time
- ❌ Account for your tax situation
- ❌ Guarantee any level of returns
- ❌ Replace professional financial advice
- ❌ Use point-in-time fundamental data (price-only signals)

---

## Known Limitations & Honest Caveats

1. **Survivorship bias:** The equity universe is defined as of today. Stocks that delisted or went bankrupt are not in the sample. This flatters historical signal quality.
2. **Yahoo Finance data quality:** Occasional gaps, missing adjustments for some EU tickers. Validate any signal that looks anomalous.
3. **No fundamental data:** All signals are price-based. Value and quality signals derived from price (low-vol proxy) are weaker than those using proper accounting data.
4. **Transaction costs are estimates.** Your actual costs depend on your broker, order type, and execution timing.
5. **Crypto signals during regime transitions** are noisy. The trend model will whipsaw during range-bound markets. This is inherent to trend-following.

---

## Extending the Engine

### Adding a New Equity Factor

1. Add the factor config to `EQUITY_FACTORS` in `config.py`
2. Implement the computation function in `signal_engine.py`
3. Add the Z-scored column to the composite calculation
4. Ensure all weights sum to 1.0

### Connecting to a Broker API

The signal output (CSV or DataFrame) can be fed into:
- **Interactive Brokers** — via `ib_insync` Python library
- **Alpaca** — via their REST API
- **QuantConnect** — upload the signal logic as a LEAN algorithm

This requires additional engineering and is NOT included in this tool.

---

## Dashboard

Start the React dashboard and FastAPI backend together:

```bash
bash start_dashboard.sh
# API docs:  http://localhost:8000/docs
# Dashboard: http://localhost:5173
```

### Production build (no hot-reload)

```bash
cd dashboard/frontend && npm run build
```

Then serve the `dist/` folder:

```bash
# Option A — simple static server on port 5173
python3 -m http.server 5173 --directory dashboard/frontend/dist/

# Option B — mount into FastAPI (single port, no separate process)
# Add to dashboard/api/main.py:
# from fastapi.staticfiles import StaticFiles
# app.mount("/", StaticFiles(directory="dashboard/frontend/dist", html=True), name="static")
# Then: uvicorn dashboard.api.main:app --host 0.0.0.0 --port 8000
```

---

## Phase 2 — Centralized yfinance Wrapper (Completed)

`yf_cache.py` provides two optimizations used by the screener scan loops:

### `bulk_history(tickers, period, interval)`
One `yf.download()` call replaces N individual `stock.history()` calls.
Returns `{TICKER: DataFrame}` — drop-in compatible with `stock.history()`.

Used by `catalyst_screener.screen_universe()` and `squeeze_screener.run_screener()`
before their main loops. Each screener pre-fetches all ~185 histories in one
HTTP roundtrip, then passes the slice in via `prefetched_hist=`.

Savings: ~3–4 min per screener on each run (~6–8 min total).

### `filter_blacklisted(tickers)`
Removes blacklisted tickers before any download. Thin wrapper around
`db_cache.get_active_blacklist()`. Fails open (returns full list on DB error).

### What was NOT abstracted (and why)

`yf.Ticker().info` has no bulk API — each call is a separate HTTP request.
The correct cache for it is `fundamentals_cache` (Supabase, 30-day TTL),
which `fundamental_analysis.py` already populates at Step 7. On warm runs,
screeners calling `yf.Ticker(t).info` benefit automatically because
`fundamentals_cache.get_cached()` short-circuits before yfinance is called.

`options_flow.py` uses `.options` and `.option_chain()` which are inherently
per-ticker — no bulk optimization is possible there.

### Runtime savings summary (warm run, 185 tickers)

| Optimization | Savings/run |
|---|---|
| `fundamentals_cache` → Supabase (Phase 1) | ~5 min |
| IPO dates → `ticker_metadata` (Phase 1) | ~2 min |
| Universe snapshot → `ticker_metadata` (Phase 1) | ~3 min |
| `bulk_history` in catalyst_screener (Phase 2) | ~3–4 min |
| `bulk_history` in squeeze_screener (Phase 2) | ~3–4 min |
| `bulk_history` in options_flow screener (Phase 2) | ~1–2 min |
| `filter_blacklisted` in fundamental_analysis + signal_engine (Phase 2) | negligible |
| **Total warm-run savings** | **~17–20 min** |

---

## Database Caching & Blacklist System (Phase 1)

Three Supabase tables replace local SQLite/JSON caches that were lost on every
GitHub Actions run.  All changes are backward-compatible — no callers outside
the patched modules need to change.

### New Tables

| Table | Purpose | TTL |
|-------|---------|-----|
| `blacklist` | Tickers excluded from all pipeline steps | permanent or custom |
| `ticker_metadata` | IPO/delist dates, sector, status | indefinite (updated on change) |
| `fundamentals` | Quarterly fundamental data from yfinance | 30 days |

### Apply the Migration

```bash
# Option A — Supabase SQL Editor (paste and run)
cat migrations/001_add_blacklist_and_metadata.sql

# Option B — psql
psql "$DATABASE_URL" -f migrations/001_add_blacklist_and_metadata.sql
```

### Migrate Existing Data

```bash
# Import any tickers from liquidity_failed.log into the blacklist (7-day TTL)
python3 -c "from db_cache import migrate_liquidity_failed_log; migrate_liquidity_failed_log()"
```

### Managing the Blacklist

```bash
# Show active entries
python3 db_cache.py blacklist --list

# Permanently blacklist a confirmed delist
python3 db_cache.py blacklist --add LILM --reason confirmed_delist

# Temporarily blacklist with 14-day TTL
python3 db_cache.py blacklist --add XYZ --reason no_yfinance_data --days 14

# Remove an entry
python3 db_cache.py blacklist --remove XYZ
```

### Managing the Fundamentals Cache

```bash
# Show all cached tickers and their age
python3 fundamentals_cache.py --list

# Force-expire one ticker (re-fetched on next run)
python3 fundamentals_cache.py --clear GME

# Force re-fetch one ticker now
python3 fundamentals_cache.py --refresh GME
```

### What Changed in Each Module

- **`universe_builder.py`** — Checks blacklist before liquidity filter; auto-adds
  tickers with zero yfinance data (7-day TTL); warm-starts from Supabase snapshot.
- **`backtest.py`** — Prefetches all IPO dates from `ticker_metadata` in one DB
  query before `_filter_universe`; saves new results back for subsequent runs.
- **`fundamentals_cache.py`** — Migrated from local SQLite to Supabase. Public
  API unchanged (`get_cached`, `save_to_cache`, CLI commands all identical).
- **`catalyst_screener.py`, `squeeze_screener.py`, `options_flow.py`** — Call
  `filter_blacklisted()` + `bulk_history()` before their per-ticker loops.
- **`fundamental_analysis.py`, `signal_engine.py`** — Call `filter_blacklisted()`
  before their main loops (no bulk history needed — `.info` has no bulk API).

---

## Phase 3 — GitHub Actions Cache + Observability (Completed)

### GitHub Actions Cache for iShares Constituents

The workflow (`.github/workflows/daily_pipeline.yml`) caches `data/universe_cache/`
between runs using a date-based key:

```yaml
key: universe-cache-2026-04-06        # new key each calendar day
restore-keys: universe-cache-         # falls back to any previous day's cache
```

**Effect on a typical day:**

| Run | GHA cache | `universe_builder` disk cache | iShares HTTP fetches |
|-----|-----------|-------------------------------|----------------------|
| First daily run | miss | miss | 7 × iShares CSV requests |
| Same-day re-run | hit — files restored | fresh (< 24h) | 0 |
| Next calendar day | new key (miss) | stale (> 24h) | 7 × iShares CSV requests |

The fallback chain in `fetch_index_constituents()` is unchanged: fresh disk cache
→ iShares HTTP → stale disk cache → hardcoded fallback list.  The GHA cache simply
pre-populates the disk cache for same-day re-runs.

### Observability Improvements

All INFO-level messages now appear in GitHub Actions logs. Key messages:

```
# db_cache.py
get_cached_universe: warm cache HIT — 183 tickers (age < 25h)   ← skip 1000-ticker download
get_cached_universe: cache miss — 0 tickers in snapshot           ← first/stale run
get_active_blacklist: 12 tickers on blacklist
bulk_get_ipo_dates: cache hit — 171/183 tickers have stored IPO dates
save_universe_results: upserted 183 tickers into ticker_metadata

# yf_cache.py
filter_blacklisted: removed 12/195 blacklisted tickers
bulk_history: 179/183 tickers with valid history (period=6mo, 4 skipped)
```

### Verification Checklist

Run these after any change to the caching stack:

```bash
# 1. Blacklist round-trip
python3 db_cache.py blacklist --add TEST --reason smoke_test --days 1
python3 db_cache.py blacklist --list            # TEST should appear
python3 -c "from yf_cache import filter_blacklisted; print(filter_blacklisted(['TEST','AAPL']))"  # ['AAPL']
python3 db_cache.py blacklist --remove TEST

# 2. Universe warm-start
python3 -c "
from db_cache import get_cached_universe, save_universe_results
save_universe_results(['AAPL','MSFT','GOOG'], sector_map={})
result = get_cached_universe(max_age_hours=1)
print('warm-start OK:', result)
"

# 3. bulk_history smoke test (uses real yfinance)
python3 -c "
from yf_cache import bulk_history
m = bulk_history(['AAPL','MSFT'], period='1mo')
assert 'AAPL' in m and len(m['AAPL']) >= 15, 'bulk_history failed'
print('bulk_history OK:', {k: len(v) for k, v in m.items()})
"

# 4. Full pipeline dry-run (no AI cost)
bash run_master.sh --skip-ai
# Expected log lines:
#   get_cached_universe: warm cache HIT (second+ run)
#   filter_blacklisted: removed N tickers
#   bulk_history: X/Y tickers with valid history
```

---

## License

For personal, non-commercial use only. No warranty expressed or implied.
