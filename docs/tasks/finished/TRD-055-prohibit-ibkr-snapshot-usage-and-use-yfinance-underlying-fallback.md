# Task: Prohibit IBKR Snapshot Usage and Use yfinance Underlying Fallback

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: execution
Category: options
Risk: cost-control
Effort: M
Target Release: options-stack-v1
Due Date: TBD
Dependencies: TRD-021, TRD-022, TRD-054
Blocked By: none
Links: `utils/ibkr_options.py`, `tests/test_ibkr_options.py`
Success Metric: the repo no longer makes IBKR snapshot market-data requests for the underlying stock price; yfinance is used for that lookup instead; no other hidden IBKR snapshot usage remains.

## Problem Statement

`IBKROptionsAdapter.get_chain` called `self._ib.reqMktData(stock, "", snapshot=True)` to
fetch the underlying stock price before selecting ATM strikes. IBKR Pro charges a
per-snapshot entitlement fee for each such request, making routine chain fetches
unnecessarily expensive. yfinance already provides an equivalent last-close price
at zero cost and was already a hard project dependency.

## Implementation

### `utils/ibkr_options.py`

**Removed** the IBKR snapshot block (5 lines):
```python
# before
self._ib.reqMktData(stock, "", snapshot=True)
self._ib.sleep(1.5)
td_stock = self._ib.ticker(stock)
underlying_price = float(td_stock.marketPrice()) if td_stock ...
```

**Added** `_get_underlying_price_yf(ticker) -> Optional[float]` helper above
the IBKR import block:
- Calls `yf.Ticker(ticker).history(period="5d")` and returns the last Close price.
- Returns `None` on empty history, zero/negative price, or any exception.
- Logged at DEBUG level on failure; callers degrade safely.

**Replaced** the snapshot block with one line:
```python
underlying_price = _get_underlying_price_yf(sym)
```

**Preserved intact:** the live option-contract market-data path
(`self._ib.reqMktData(opt, "100,101,106", False, False)`) — this ticket does
not touch option-contract requests, only the underlying stock snapshot.

Strike selection when `underlying_price is None` was already robust (falls back
to the middle window of available strikes); no change needed there.

### `tests/test_ibkr_options.py` — 55 tests (was 44, +11)

| New class | Coverage |
|---|---|
| `TestNoIBKRSnapshot` | source-level guard that `get_chain` contains no `snapshot=True` in executable lines; module-wide guard; option-contract `reqMktData` path still present |
| `TestGetUnderlyingPriceYf` | returns last-close price; returns `None` on empty history; returns `None` on yfinance exception; returns `None` for zero/negative price |
| `TestIBKRAdapterUnderlyingFallback` | `None` underlying price does not crash chain fetch; valid price used for ATM strike selection; no `reqMktData` call in the chain flow uses `snapshot=True` |

## Non-Goals

- Does not remove the live IBKR option-contract market-data path.
- Does not convert the repo to yfinance-only options data.
- Does not change TRD-052 / TRD-053 / TRD-054 scope.

## Verification

```
pytest -q tests/test_ibkr_options.py   → 55 passed
grep -rn "snapshot=True" utils/        → 0 results
```

Post-change audit confirmed: the only remaining occurrences of `snapshot=True`
in the repo are inside test docstrings and assertion error messages in
`tests/test_ibkr_options.py` — zero executable production-code matches.
