# Task: Option Candidate Engine and API

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: api
Category: automation
Risk: trading-logic
Effort: L
Target Release: backlog
Due Date: TBD
Dependencies: TRD-021, TRD-042
Blocked By: none
Links: `utils/option_candidates.py`, `dashboard/api/main.py`, `dashboard/frontend/src/lib/api.ts`, `tests/test_option_candidates.py`
Success Metric: the API can return 1-3 deterministic option candidates for a ticker based on the existing stock thesis and contract-level chain data.

## Implementation Notes

### What shipped

**`utils/option_candidates.py`** — full deterministic candidate engine:

Key types:
- `OptionCandidate` dataclass (line 81) — normalized contract with strike, expiry, DTE, delta, mid, spread_pct, open_interest, volume, right, rationale, recommended_entry_price, max_chase_price, fill_quality_score, recommended_order_type, slippage_risk_label, and exit/scenario fields
- `OptionCandidatesResult` dataclass (line 184) — wraps `candidates[]`, `rejection_reasons[]`, `suppressed`, `suppression_reason`, and context metadata

First-pass thesis gate (`_should_suppress`, various suppression checks):
- Neutral direction suppressed
- Conviction < 2 suppressed
- Earnings within 3 days suppressed
- Bull thesis with T1 below current price suppressed
- Bear thesis with T1 above current price suppressed

Contract quality filters:
- Missing or zero mid rejected
- Spread > threshold rejected
- Open interest below minimum rejected
- Delta outside configured band rejected
- DTE outside strategy-preset range rejected

Candidate scoring, ranking, and top-N selection via `get_option_candidates(symbol, thesis, portfolio_context)` (line 787):
- Scores each contract deterministically on delta centrality, liquidity, DTE fit, and spread quality
- Returns top 1-3 with explicit per-contract rejection reasons for filtered contracts

Execution guidance layer (added as part of the same implementation):
- `recommended_entry_price`, `max_chase_price`, `recommended_order_type`, `fill_quality_score`, `slippage_risk_label`
- `projected_exits`, `scenario_strip` for the UI

**`dashboard/api/main.py`** — endpoint wired at line 6465:
- `GET /api/ticker/{symbol}/option-candidates` (line 6670)
- Requires `utils.option_candidates` module; returns 503 with explanation if unavailable
- Underlying price fetched via `_md_get_prices` / `asyncio.to_thread` (BUG-001 fix)
- Response cached per symbol with a short TTL in the in-process `_cache`

**`dashboard/frontend/src/lib/api.ts`**:
- `OptionCandidate` interface (line 1475) — full typed shape matching backend dataclass
- `OptionCandidatesResponse` interface (line 1542)
- `tickerOptionCandidates(symbol)` API call (line 1330)

### Test coverage

**`tests/test_option_candidates.py`** — 47 tests covering:

| Area | Tests |
|---|---|
| Thesis suppression | neutral direction, conviction < 2, earnings within 3d, bull T1 below current, bear T1 above current, stale-check skip when no price |
| Contract quality filters | valid contract passes, missing/zero mid rejected, wide spread rejected, low OI rejected, delta outside band rejected |
| Scoring and selection | bullish thesis returns call candidates only, bearish returns puts only, illiquid contracts filtered |
| Edge cases | None OI allowed past filter, missing Greeks handled, no candidates result is clean |

All 47 tests pass.

## Acceptance Criteria (all met)

- [x] API returns normalized response with `candidates[]`, `rejection_reasons[]`, `suppressed`, `suppression_reason`
- [x] First-pass filters enforce DTE range, delta band, spread threshold, minimum OI/volume, and event-risk suppression
- [x] Weak thesis or poor chain quality yields a clean `no candidates` result with explanation
- [x] Bullish swing thesis returns bullish contracts only
- [x] Bearish swing thesis returns bearish contracts only
- [x] Illiquid and wide-spread contracts filtered out
- [x] Earnings / event-risk suppression works
- [x] Focused unit tests for all major filter and scoring paths
