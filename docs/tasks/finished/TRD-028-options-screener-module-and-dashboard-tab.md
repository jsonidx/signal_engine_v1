# Task: Options Screener Module and Dashboard Tab

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: dashboard
Category: options
Risk: ui
Effort: L
Target Release: options-stack-v1
Due Date: N/A
Dependencies: TRD-022, TRD-026
Links: `dashboard/api/main.py`, `dashboard/frontend/src/pages/OptionsPage.tsx`, `dashboard/frontend/src/lib/api.ts`

## Implementation Notes

### Backend

`dashboard/api/main.py:6707` — `GET /api/options/screener`:
- selects up to N thesis rows filtered by `conviction >= min_conviction` and
  `direction IN ('BULL', 'BEAR')`, ordered by `signal_agreement_score`
- runs the deterministic option candidate engine per ticker via thread pool
  (`max_workers=1` when IBKR active; `max_workers=6` with yfinance fallback)
- returns a flat ranked list with `composite_rank_score` (score × conviction
  weight), `partial` flag, and `timed_out_tickers` for observability
- cached 15 minutes; persists screener results fire-and-forget via TRD-026

`api.ts` — `api.optionsScreener()` sends `min_conviction` and `max_tickers`
(frontend default: `max_tickers=8`, down-scoped from original 20).

### Frontend

`dashboard/frontend/src/pages/OptionsPage.tsx` — new `Options` page with three
tabs: Screener, Accuracy, Calibration.

`ScreenerPanel` renders:
- conviction filter control and refresh button
- per-row columns: rank, ticker + direction + conviction, preset, contract
  (strike/expiry/DTE), mid, recommended entry / max-chase price, slippage
  risk, delta, spread %, score, holding window, rationale
- partial-result banner when `data.partial === true`
- click-to-navigate to ticker deep-dive

Page is wired into the app router; `api.optionsScreener()` uses
`staleTime: 15 min` and `retry: 1` on the frontend query.

### Tests

`tests/test_options_screener.py` — backend screener tests:
- ranked across tickers, required fields present, empty-universe case,
  all-suppressed case, DB failure case, cache behavior, conviction filter,
  persistence call counts

### Known follow-up

TRD-080 replaces the synchronous live IBKR fan-out with a precomputed snapshot
model to eliminate partial-result behavior. TRD-028's original scope (the
screener module and dashboard tab) is complete; TRD-080 is a performance
architecture improvement, not a gap in this ticket.

## Original Acceptance Criteria (all met)

- [x] Dashboard has a new options screener tab/page
- [x] Page shows ranked option opportunities across multiple tickers
- [x] Each row includes ticker, direction/conviction, preset, strike/expiry/DTE,
      holding window, delta, spread %, score, rationale
- [x] Empty/unavailable state renders cleanly
- [x] Filtered thesis universe respected
