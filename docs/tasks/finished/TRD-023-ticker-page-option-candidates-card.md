# Task: Ticker Page Option Candidates Card

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: dashboard
Category: ux
Risk: ui
Effort: M
Target Release: backlog
Due Date: TBD
Dependencies: TRD-022, TRD-042
Blocked By: none
Links: `dashboard/frontend/src/pages/TickerPage.tsx`, `dashboard/frontend/src/lib/api.ts`, `dashboard/frontend/src/pages/tests/TickerPage.option-candidates.test.tsx`
Success Metric: the ticker deep-dive page shows a clear `Option Candidates` section with recommended contracts or a defensible no-trade state.

## Implementation Notes

### What shipped

**`dashboard/frontend/src/pages/TickerPage.tsx`**:

- `OptionCandidatesCard` component (line 1775) — fetches `/api/ticker/{symbol}/option-candidates` via `useQuery`, renders loading/error/suppression/candidates states
- `OptionCandidateRow` (line 2261) — renders a single candidate with all execution-oriented fields: right, strike, expiry, DTE, delta, mid, spread %, OI/volume, breakeven, rationale
- Supporting sub-components:
  - `EntryGuardrailBanner` (line 1878) — shows entry price and order-type guidance
  - `_EntryCol` (line 1925) — compact entry zone display
  - `ProjectedExitsSection` (line 1944) — target exits with probability
  - `UnderlyingLevelsRow` (line 2050) — underlying entry/stop/target context
  - `ScenarioStrip` (line 2093) — bull/base/bear scenario grid
  - `OptionTradeSetupGrid` (line 2127) — full trade setup summary
  - `BuyDecisionBadge` (line 2239) — go/wait/no-trade badge
- Equity / Options toggle (shown when candidates are available)
- Card integrated into the main `TickerPage` render at line 2778 (`optionCandidates` query)

**`dashboard/frontend/src/lib/api.ts`**:
- `OptionCandidate` interface (line 1475) — complete typed shape
- `OptionCandidatesResponse` interface (line 1542)
- `tickerOptionCandidates(symbol)` API call (line 1330)
- `OptionsCrossTickerRow` extension type for cross-ticker screener view (line 1558)

### States handled

| State | Render |
|---|---|
| Loading | Skeleton / spinner |
| Error | `"Failed to load option candidates."` banner |
| Suppressed (`suppressed=true`) | Suppression reason message, no contract rows |
| Empty candidates | No-trade message with `suppression_reason` |
| 1-3 candidates | Full candidate card(s) with all execution fields |
| Missing optional fields (OI, delta, etc.) | Graceful null handling; field omitted or shown as `—` |

### Test coverage

**`dashboard/frontend/src/pages/tests/TickerPage.option-candidates.test.tsx`** — 60 tests total, including:

| Describe block | Coverage |
|---|---|
| `OptionCandidatesCard` (line 189) | Renders header, renders single candidate, suppression state, empty no-trade state, API error state, missing optional fields, three candidates |
| `OptionCandidatesCard — Execution Guidance` | Entry price, max chase price, order type, slippage risk, projected exits, scenario strip, missing entry fields |
| Toggle | Shows equity/options toggle when candidates present; hides when no data |
| `ModelRerunButton — queued state` | TRD-072 coverage (queued indicator) |
| `AnalyzeButton — queued state` | TRD-072 coverage (queued label) |

All 60 tests pass.

## Acceptance Criteria (all met)

- [x] Ticker page shows an `Option Candidates` card when the endpoint returns data
- [x] Each candidate renders essential contract fields plus a concise rationale
- [x] Suppression/no-trade state renders clearly instead of broken or empty card
- [x] Loading and error states are visually distinct and non-blocking
- [x] Frontend coverage: renders candidates when API returns 1-3 rows
- [x] Frontend coverage: renders suppression state cleanly
- [x] Frontend coverage: handles missing optional fields gracefully
