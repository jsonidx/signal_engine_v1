# Task: Hedge Fund 13F Portfolio Monitor

Status: proposed
Stage: discovery
Type: feature
Priority: P2
Severity: medium
Owner: Claude Code
Reviewer: Jason
Product Area: data-pipeline | dashboard | api
Category: research | automation
Risk: api
Effort: L
Target Release: CHUNK-16
Due Date: 2026-07-31
Dependencies: none
Blocked By: none
Links: none
Success Metric: Dashboard page shows per-fund position history; a daily job detects position size changes across quarters and flags new buys, add-ons, and liquidations within 24h of a new 13F filing

## Problem Statement

Smart-money 13F filings contain high-signal position data — entry dates, sizing changes, full liquidations — but parsing and tracking them manually across funds is not scalable. There is no automated way to know when Situational Awareness LP (or any tracked fund) added to BE, trimmed CRWV, or opened a new put on SMH relative to their prior quarterly filing.

The pipeline currently has no awareness of institutional positioning from 13F data. This is a missed context signal for AI thesis generation and for standalone research.

## User Impact

Jason manually checks Whalewisdom / 13f.info to see what tracked funds are holding. There is no diff view ("what changed since last quarter"), no alerting on new filings, and no integration with the signal engine universe. High-conviction hedge fund positions are a strong signal that is currently invisible to the system.

## Objective

1. Ingest 13F filings for a configurable list of tracked funds (starting with Situational Awareness LP).
2. Store each quarterly snapshot in Supabase so quarter-over-quarter diffs are computable.
3. Expose a dashboard page showing per-fund position history: entry quarter, shares/value per quarter, change direction (new / added / trimmed / closed).
4. Send a Telegram alert when a new filing is detected with a plain-English diff summary.

## Proposed Solution

### Step 1 — Data ingestion (`scripts/fetch_13f.py`)

- Query SEC EDGAR full-text search API (`efts.sec.gov/LATEST/search-index?q=...&dateRange=custom&startdt=...&category=form-type&forms=13F-HR`) for each tracked fund CIK.
- Parse the XML holding report (`infotable.xml`) to extract: `ticker`, `cusip`, `shares`, `value`, `put_call`, `investment_discretion`, `period_of_report`.
- Resolve CUSIP → ticker using SEC's CUSIP mapping or a fallback yfinance lookup.
- Write one row per `(fund_slug, ticker, period_of_report)` to a new `hedge_fund_positions` table.
- Run on a weekly cron (Sundays, before `run_master.sh`) — 13F filings are quarterly but checking weekly ensures new filings are caught within days.

### Step 2 — Supabase schema (`hedge_fund_positions` table)

```sql
CREATE TABLE hedge_fund_positions (
    id              BIGSERIAL PRIMARY KEY,
    fund_slug       TEXT NOT NULL,          -- e.g. 'situational-awareness-lp'
    fund_name       TEXT NOT NULL,
    cik             TEXT NOT NULL,
    period          DATE NOT NULL,          -- end of quarter: 2026-03-31
    filed_at        DATE,                   -- SEC filing date
    ticker          TEXT,
    cusip           TEXT,
    name_of_issuer  TEXT,
    shares          BIGINT,
    value_usd       BIGINT,                 -- in thousands (as filed)
    put_call        TEXT,                   -- NULL | 'Put' | 'Call'
    change_type     TEXT,                   -- 'new' | 'added' | 'trimmed' | 'closed' | 'unchanged'
    shares_delta    BIGINT,                 -- vs prior quarter
    value_delta_usd BIGINT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (fund_slug, ticker, cusip, period, put_call)
);
```

### Step 3 — Diff computation

On each ingestion run, after writing the new quarter rows, compute `change_type` and deltas by joining current quarter against the prior quarter for the same fund. Logic:

- `new` — ticker not present in prior quarter
- `added` — shares increased vs prior
- `trimmed` — shares decreased vs prior, still > 0
- `closed` — shares = 0 or ticker absent this quarter but was present before
- `unchanged` — shares identical

### Step 4 — Dashboard page (`HedgeFundPage`)

New route `/hedge-funds` in the React frontend:

- Fund selector (dropdown, multi-select) — initially just Situational Awareness LP
- Per-fund table: columns = Ticker | Type (equity/put/call) | Shares | Value | Q-o-Q Change | Entry Quarter | # Quarters Held
- Color-coded change badges: green = added/new, red = trimmed/closed, grey = unchanged
- Sparkline of value per quarter per position (last 4 quarters)
- Filter by change type, long/put/call

### Step 5 — Telegram alert on new filing

When `fetch_13f.py` detects a new period not yet in the DB:

```
📋 NEW 13F — Situational Awareness LP (Q1 2026)
Filed: 2026-05-18 | Positions: 42 | Total value: $13.7B

🟢 NEW (3): SNDK, IREN, CRWV
➕ ADDED (5): BE +12%, ...
✂️ TRIMMED (4): ...
🔴 CLOSED (2): ...
```

### Step 6 — Fund config (`config/hedge_funds.json`)

Configurable fund list so new funds can be added without code changes:

```json
[
  {
    "slug": "situational-awareness-lp",
    "name": "Situational Awareness LP",
    "cik": "0002045724",
    "notes": "Leopold Aschenbrenner AI macro fund"
  }
]
```

## Scope

Files / modules to create or change:

- `scripts/fetch_13f.py` — new: ingestion script, diff computation, Telegram alert
- `config/hedge_funds.json` — new: tracked fund registry
- `migrations/XXX_hedge_fund_positions.sql` — new: Supabase schema migration
- `dashboard/api/main.py` — new API endpoints: `GET /api/hedge-funds`, `GET /api/hedge-funds/{slug}/positions`
- `dashboard/frontend/src/pages/HedgeFundPage.tsx` — new: dashboard page
- `dashboard/frontend/src/App.tsx` — add route + nav link
- `.github/workflows/manual_pipeline.yml` — add weekly 13F fetch step (before run_master.sh)
- `tests/test_13f_ingestion.py` — new: unit tests for diff logic and CUSIP resolution

## Non-Goals

- Real-time 13F parsing (13Fs are quarterly; near-real-time is not achievable)
- Short position inference beyond what is in the 13F (13Fs only capture long + options)
- Feeding 13F positions directly into `resolved_signals.json` or AI thesis generation (separate ticket if warranted)
- Backtesting fund returns

## Constraints

- No changes to trading logic, `ai_quant.py` prompts, or `conflict_resolver.py`.
- No refactoring of existing pipeline steps.
- SEC EDGAR API is public and rate-limited at ~10 req/s — respect rate limits, add 0.1s sleep between requests.
- CUSIP data is licensed; use EDGAR's own issuer name + ticker field first; fall back to yfinance only for gaps.
- No paid data APIs — use SEC EDGAR (free) only.

## Acceptance Criteria

- Observable behavior:
  - `python3 scripts/fetch_13f.py` runs without error, writes rows to `hedge_fund_positions`
  - Re-running the script on the same quarter is idempotent (upsert, no duplicates)
  - A second run after a new quarter's data is available computes correct `change_type` for all positions
  - Dashboard `/hedge-funds` page loads without error and shows Situational Awareness LP Q1 2026 positions
  - Telegram alert fires on the first ingestion of a new period (not on re-runs)
- Tests:
  - `tests/test_13f_ingestion.py` covers: diff logic (new/added/trimmed/closed/unchanged), CUSIP→ticker resolution, idempotency, empty prior-quarter edge case
- Documentation:
  - `config/hedge_funds.json` has a comment block explaining CIK lookup procedure

## Verification Plan

- `pytest tests/test_13f_ingestion.py -v`
- Manual: `python3 scripts/fetch_13f.py --dry-run` (print rows, no DB writes)
- Manual: load `/hedge-funds` in browser, confirm Q1 2026 table renders with correct change badges
- Manual: confirm re-run does not create duplicate rows

## QA Notes

- Test scenarios:
  - Fund with 0 prior quarters (first-ever ingestion) — all rows should be `change_type = 'new'`
  - Fund that held a put one quarter and an equity the next — separate rows, not merged
  - Ticker resolution failure (CUSIP not resolvable) — row still written with `ticker = NULL`, `name_of_issuer` populated
  - SEC EDGAR rate limit hit — script backs off and retries, does not fail silently
- Edge cases:
  - 13F filed late (after the quarter end + 45-day deadline) — `filed_at` vs `period` stored separately
  - Fund CIK returns 0 filings — log warning, do not crash
  - Position where `put_call = 'Put'` and `put_call = NULL` exist for the same underlying in the same quarter — treated as separate rows (different instruments)
- Regression risks:
  - `manual_pipeline.yml` changes: ensure new 13F step does not extend wall-clock runtime beyond 2.5h limit; run it with a 5-minute timeout and `continue-on-error: true`

## Launch / Release Notes

- User-facing change summary: New "Hedge Funds" nav item; daily monitoring of 13F filings with Telegram alerts on changes
- Operational notes: First run will ingest all available historical quarters from EDGAR (typically 4–8 quarters back); this may take ~10 minutes on first run
- Rollback notes: Table is additive; dropping `hedge_fund_positions` and removing the nav route fully reverts

## Post-Launch Validation

- What to monitor: Row count in `hedge_fund_positions` grows each quarter; Telegram alert fires within 1 week of Q2 2026 13F filing deadline (mid-August 2026)
- How success will be confirmed: At least 1 new-quarter alert received and verified against 13f.info manually
- Follow-up decision date: 2026-08-20

## Handoff Notes

### Implementation prompt (paste-ready for Claude Code)

Implement TRD-083: Hedge Fund 13F Portfolio Monitor.

**Goal:** Ingest SEC EDGAR 13F filings for tracked hedge funds, store quarterly position snapshots in Supabase, compute quarter-over-quarter diffs, expose a dashboard page and Telegram alert.

**Start with `config/hedge_funds.json`** — create the file with Situational Awareness LP (CIK 0002045724) as the only entry.

**Then `migrations/XXX_hedge_fund_positions.sql`** — create the `hedge_fund_positions` table exactly as specified in the schema section of TRD-083.

**Then `scripts/fetch_13f.py`** — SEC EDGAR full-text search to get 13F-HR filings for each CIK, parse `infotable.xml`, resolve tickers, compute change_type diffs vs prior quarter, upsert to Supabase, send Telegram alert on new period. Add `--dry-run` flag (print rows, skip DB writes and Telegram). Add `--fund` flag to run for a single fund slug.

**Then API** — add `GET /api/hedge-funds` (list of funds + latest period) and `GET /api/hedge-funds/{slug}/positions` (all positions for a fund, optional `?period=` filter) to `dashboard/api/main.py`.

**Then `dashboard/frontend/src/pages/HedgeFundPage.tsx`** — fund selector, position table with change badges, filter by change_type and put_call. Wire to `/hedge-funds` route in `App.tsx` and add nav link.

**Tests:** `tests/test_13f_ingestion.py` — mock EDGAR HTTP responses, test diff logic for all 5 change_type cases, test idempotency (run twice, row count unchanged), test NULL ticker fallback.

**Non-goals:** No changes to `ai_quant.py`, `conflict_resolver.py`, or any existing pipeline steps. No paid data APIs. No real-time feeds.

**Constraints:** SEC EDGAR rate limit — 0.1s sleep between requests. No secrets in git. `continue-on-error: true` on the GitHub Actions step.

**Verification:** `pytest tests/test_13f_ingestion.py -v` must pass. `python3 scripts/fetch_13f.py --dry-run` must print rows without error.
