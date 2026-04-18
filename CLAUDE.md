# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Python backend
pytest                                          # full test suite
pytest tests/test_ticker_selector.py -v        # single file
pytest tests/test_ai_quant_schema.py::test_foo # single test
pytest --tb=short                              # short tracebacks (default in pytest.ini)

# Pipeline
bash run_master.sh                             # full run (with AI cost)
bash run_master.sh --skip-ai                   # data-only, €0.00
bash run_master.sh --force-ai                  # bypass skip_claude blocks
python ai_quant.py --tickers AAPL MSFT         # analyze specific tickers

# Dashboard
bash start_dashboard.sh                        # API (8000) + frontend (5173) together
cd dashboard/api && uvicorn main:app --port 8000
cd dashboard/frontend && npm run dev           # Vite dev server :5173
cd dashboard/frontend && npm run build         # production build → dist/
cd dashboard/frontend && npm test              # Vitest unit tests
```

## Architecture

### Pipeline orchestration (`run_master.sh`)

18 numbered steps, executed sequentially each run:

| Steps | Modules | Role |
|---|---|---|
| 0 | `universe_builder.py` | Builds dynamic ticker universe from Russell 1000/2000, S&P 500, iShares ETFs; outputs `watchlist.txt` |
| 1–2 | `dark_pool_flow.py`, `regime_filter.py` | Pre-market context: institutional flow (FINRA ATS) + market regime (SPY MA, VIX, HYG, yield curve) |
| 3–8b | `signal_engine.py`, `catalyst_screener.py`, `options_flow.py`, `squeeze_screener.py`, `fundamental_analysis.py`, `sec_module.py`, `red_flag_screener.py` | Independent screeners; each scores tickers 0–1 on their domain |
| 12 | `conflict_resolver.py` | Aggregates all module votes → `signal_agreement_score` per ticker; outputs `resolved_signals.json` |
| 13 | `ai_quant.py` | Claude synthesis (skipped with `--skip-ai`); writes theses to `thesis_cache` Supabase table |
| 13a–13c | `candidate_archive.py`, `trade_selector_4w.py`, `thesis_checker.py` | Archive candidates → `candidate_snapshots`; generate `daily_rankings` top-20; check prior thesis outcomes |
| 15–19 | `volume_profile.py`, `paper_trader.py`, `trade_journal.py`, `iv_calculator.py`, `backtest.py` | Post-signal: vol profile, paper P&L, IV history, backtest |

### Key data flows

- **Universe filtering:** `universe_builder.py` → checks `blacklist` table (Supabase) at build time; blacklisted tickers are excluded before any screener runs
- **Signal aggregation:** Each screener writes a score dict; `conflict_resolver.py` reads all, normalises, outputs one agreement score per ticker
- **AI selection:** `utils/ticker_selector.py` picks top N by `priority_score`; `ai_quant.py` calls Claude (claude-sonnet-4-6 with extended thinking) per ticker
- **Rankings:** `utils/trade_selector_4w.py` simulates a 4-week rolling window to produce `daily_rankings` with `t1_price`, `t2_price`, `prob_t1`, `prob_t2`
- **Cache invalidation:** Final step POSTs to `/api/cache/invalidate` so the dashboard reflects the new run

### Supabase tables (key ones)

| Table | Purpose |
|---|---|
| `thesis_cache` | Claude theses keyed by `(ticker, date)`; has `entry_low/high`, `target_1/2`, `stop_loss`, `conviction`, `direction` |
| `daily_rankings` | Top-20 daily; has `rank`, `prob_t1/t2`, `t1_price/t2_price`, `weight`, `direction` |
| `candidate_snapshots` | Historical candidate pools with `priority_score`, `signal_agreement_score` |
| `blacklist` | Excluded tickers; `expires_at NULL` = permanent; checked by `universe_builder.py` |
| `iv_history` | Daily ATM IV per ticker; needs 60+ rows for IV rank (takes ~3 months to populate) |
| `portfolio_settings` | Key/value; `cash_eur` drives live portfolio NAV |
| `screener_signals` | Equity factor Z-scores per ticker per date |

### Database connection

`utils/db.py` → `managed_connection()` context manager (psycopg2, RealDictCursor). Always use the **session pooler** URL (port 5432) — not port 6543 — for GitHub Actions IPv4 compatibility. Set via `DATABASE_URL` in `.env`.

### Dashboard (React + FastAPI)

- **API:** `dashboard/api/main.py` — FastAPI, port 8000; in-process cache (`_cache` dict) with per-key TTLs; invalidated by `/api/cache/invalidate`
- **Frontend:** `dashboard/frontend/src/` — React 18 + TypeScript + TanStack Query + Tailwind; pages in `pages/`, shared components in `components/`
- **Key pages:** `HomePage` (Morning Brief), `RankingsPage` (Top-20), `DeepDivePage` (thesis + entry zones), `ScreenersPage`, `PortfolioPage`, `BacktestPage`
- **Live prices in Deep Dive:** `staleTime: 0` on the tickers query forces a refetch on every mount; live-zones cached 5 min

### Configuration

- `config.py` — factor weights, Kelly fraction (0.25), max position (8% equity / 10% crypto), `DATA_LOOKBACK_DAYS` (400)
- `strategy_config` Supabase table — runtime overrides to factor weights without redeployment
- `--skip-ai` flag propagates through `run_master.sh`; `pipeline_status.json` records whether AI was used (shown in dashboard)

### Environment variables (`.env`)

```
DATABASE_URL          # session pooler postgres URL (port 5432)
SUPABASE_JWT_SECRET   # for dashboard auth
ANTHROPIC_API_KEY     # Claude API (ai_quant.py)
XAI_API_KEY           # Grok fallback (optional)
TELEGRAM_BOT_TOKEN    # alerts (optional)
TELEGRAM_CHAT_ID      # alerts (optional)
```

### Known gotchas

- **FINRA data lag:** Released ~8 pm ET; pipeline uses 3 most recent business days and silently skips if all missing
- **Earnings revision factor:** yfinance returns current values only — point-in-time history unavailable, so backtest has look-ahead bias on this factor
- **Crypto:** Only BTC-USD (multi-asset retired due to poor Sharpe); signal is binary via 200-day EMA
- **IV rank:** Needs 60+ rows in `iv_history` per ticker before it's meaningful — shows "N/A" on fresh installs
