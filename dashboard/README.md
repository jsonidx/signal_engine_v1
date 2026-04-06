# Signal Engine Dashboard

React + FastAPI dashboard for the signal_engine_v1 quant terminal.

## Current Capabilities & Dashboard Overview

### Active Screeners

| Screener | Tab | Description | Data source |
|---|---|---|---|
| **Equity Rankings** | Screeners → Rankings | Multi-factor composite Z-scores (mom 12-1, mom 6-1, mean-rev, vol-qual, risk-mom). Top 20 long + bottom 5 short. | `equity_signals_YYYYMMDD.csv` |
| **Short Squeeze** | Screeners → Squeeze | Float short %, days-to-cover, volume surge, borrow cost, EV score. Scored 0–100. | `squeeze_signals_*.csv` |
| **Catalysts** | Screeners → Catalysts | Squeeze setup + volume breakout + dark pool + social sentiment composite. | `catalyst_screen_*.csv` |
| **Options Heat** | Screeners → Options | IV rank, vol spike, expected move %, put/call ratio. | `ai_quant_cache.db` (options_flow signals) |
| **Red Flags** | Screeners → Red Flags | Accounting quality: GAAP vs adjusted earnings, accruals, payout sustainability, revenue quality. | `red_flag_*.csv` |
| **Fundamentals** | Screeners → Fundamentals | PE, growth, margins, ROE, FCF, analyst consensus. Composite 0–100. | `fundamentals_*.csv` |

### Active Dashboard Features

| Feature | Location | Description |
|---|---|---|
| **Pipeline Status card** | Home (top) | Last run time, runtime, mode (full/data-only), cost, warm cache keys |
| **AI Quant Selection** | Home | Top 5 tickers by priority score sent to Claude synthesis + open positions |
| **Candidate Pool** | Home | Full 50-ticker scored pool before AI selection, sortable, filterable |
| **Signal Heatmap** | /heatmap | Per-ticker module scores across 6+ dimensions with agreement matrix |
| **Ticker Deep Dive** | /ticker/:symbol | AI thesis, price chart, action zones, module scores, historical analogs |
| **Portfolio** | /portfolio | Open positions, P&L chart, NAV, weekly return vs SPY |
| **Resolution Log** | /resolution | Conflict resolution log, Claude accuracy matrix by regime × conviction |
| **Backtest** | /backtest | Walk-forward Sharpe, factor IC table, weight recommendations |
| **Daily Top-20** | /rankings | Ranked by composite Z-score with day-over-day rank changes |
| **AI Deep Dive** | /deep-dive | Claude thesis synthesis on demand, scenario analysis, accuracy tracking |

### Performance After Caching (Supabase + yfinance bulk)

| Mode | Typical runtime | API cost |
|---|---|---|
| Full run (Monday, Claude synthesis) | ~35–50 min | ~€0.03–0.05 (5 × sonnet-4-6) |
| Data-only (`--skip-ai`) | ~20–30 min | €0.00 |
| Expected savings vs cold run | ~10–15 min | — |

Warm cache hits (Supabase price cache + bulk yfinance) avoid redundant HTTP calls across screener modules sharing the same universe.

## Stack

- **Frontend**: React 19, Vite, TypeScript, Tailwind CSS, Recharts, Radix UI, TanStack Query
- **Backend**: FastAPI (Python), serving JSON over HTTP
- **Font**: IBM Plex Mono (monospace), Inter (sans)

## Setup

### 1. Start the API server

```bash
cd dashboard/api
pip install -r requirements.txt   # or: pip install fastapi uvicorn
uvicorn main:app --reload --port 8000
```

The API runs on `http://localhost:8000`. All 20+ endpoints are under `/api/`.

### 2. Start the frontend dev server

```bash
cd dashboard/frontend
npm install
npm run dev
```

Frontend runs on `http://localhost:5173` and proxies `/api/*` to `:8000`.

### 3. Production build

```bash
cd dashboard/frontend
npm run build
# dist/ is ready to serve
```

## Pages

| Route | Page | Key features |
|---|---|---|
| `/` | Morning Brief | Pipeline status, AI selection, candidate pool, top signals, portfolio mini |
| `/portfolio` | Portfolio | PnL chart, positions table, weekly stats vs SPY |
| `/heatmap` | Signal Heatmap | Multi-factor score matrix, sector/direction filters |
| `/ticker/:symbol` | Ticker Deep Dive | AI thesis, price chart, action zones, module scores, historical analogs |
| `/screeners` | Screeners | Rankings, Squeeze, Catalysts, Options, Red Flags, Fundamentals tabs |
| `/deep-dive` | AI Deep Dive | Claude thesis synthesis, scenario analysis, thesis accuracy tracking |
| `/backtest` | Backtest | Factor IC table, walk-forward Sharpe timeline, weight recommendations |
| `/resolution` | Resolution Log | Conflict resolver log, Claude accuracy matrix |
| `/rankings` | Daily Top-20 | Ranked by composite Z-score with day-over-day changes |

## Screenshots

```
Portfolio page   — PnL chart + open positions with unrealised P&L
Signal Heatmap   — colour-coded module scores for all watchlist tickers
Ticker Deep Dive — two-column: price ladder + AI thesis + module scores
Screeners        — tabbed: squeeze ranks, catalyst scores, options heat
Dark Pool        — card grid: donut gauge + 20-day short ratio sparkline
Backtest         — walk-forward Sharpe bars + factor IC table
Resolution Log   — conflict resolver log with override flags
```

## Keyboard shortcuts

| Key | Page |
|---|---|
| `g` | Morning Brief (home) |
| `p` | Portfolio |
| `h` | Signal Heatmap |
| `t` | AI Deep Dive |
| `s` | Screeners |
| `k` | Daily Top-20 |
| `b` | Backtest |
| `r` | Resolution Log |

Shortcuts are shown as small key badges in the sidebar nav. They do not fire when an input is focused.

## Environment variables

None required. All data comes from the local FastAPI server at `http://localhost:8000`.

If you need to point the frontend at a different API host, set the `VITE_API_BASE` variable and update `src/lib/api.ts`:

```typescript
const client = axios.create({ baseURL: import.meta.env.VITE_API_BASE ?? '/' })
```

## Data generation

All API data is generated by running the signal engine pipeline:

```bash
./run_master.sh        # full 18-step pipeline (signals + screeners + backtest)
python backtest.py     # walk-forward backtest only
python dark_pool_flow.py --update   # dark pool update only
```

Stale data shows a warning in the sidebar ("Last run" timestamp).
