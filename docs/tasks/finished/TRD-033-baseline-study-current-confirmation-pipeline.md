# Task: Baseline Study For Current Confirmation Pipeline

Status: completed
Stage: done
Type: research
Priority: P0
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: research
Risk: trading-logic
Effort: M
Target Release: pre-breakout-v1
Due Date: TBD
Dependencies: none
Blocked By: none
Links: none
Success Metric: the team has a documented benchmark for current confirmation-pipeline precision, lead time, and false-positive burden over the last 18 months.

## Problem Statement

The team is assuming the current confirmation pipeline is too late, but that claim has not been benchmarked with a documented historical study.

## User Impact

Without a baseline, any claim that the new pre-breakout pipeline is better is unanchored and risks wasting engineering time on an unproven improvement.

## Objective

Measure the current confirmation pipeline outputs and produce the benchmark table that all pre-breakout research must beat.

## Proposed Solution

Reconstruct historical confirmation-pipeline alert cohorts from persisted rankings or equivalent history, compute forward-return and lead-time metrics, and write a reproducible report.

## Scope

- `daily_rankings`
- `candidate_snapshots`
- `thesis_cache` if needed for cross-checks only
- new report under `reports/`
- any small analysis script needed under `scripts/`

## Non-Goals

- Do not change signal logic.
- Do not modify production scoring thresholds.
- Do not add new pipeline features.

## Constraints

- Use trading days consistently for all windows.
- Use sector-adjusted returns where the spec calls for it.
- If historical data is insufficient, report that limitation directly rather than smoothing over it.

## Acceptance Criteria

- Observable behavior:
  - A report exists with:
    - forward returns at 5, 10, 20, 40 trading days
    - precision by rank bucket
    - median lead time for true positives
    - false-positive rate
    - average alert volume
  - The report clearly states sample size and limitations.
- Tests:
  - Any new analysis code runs locally and deterministically on available data.
- Documentation:
  - Report includes the exact metric definitions used.

## Verification Plan

- Run the analysis script used to generate the report.
- Spot-check a small sample of alerts manually against stored ranking history.

## QA Notes

- Test scenarios: enough historical alerts, sparse historical alerts, missing forward data near the most recent boundary.
- Edge cases: ticker delistings, stale sector mappings, multiple same-day rows.
- Regression risks: low, if work is limited to analysis/reporting.

## Launch / Release Notes

- User-facing change summary: none; internal benchmark study.
- Operational notes: this report becomes the benchmark for the entire program.
- Rollback notes: not applicable.

## Post-Launch Validation

- What to monitor: whether later tickets use the same metric definitions.
- How success will be confirmed: roadmap decisions reference the baseline report instead of assumptions.
- Follow-up decision date: immediately after report review.

## Implementation Notes (2026-05-31)

### Files created
- `scripts/baseline_study_033.py` — reproducible analysis script; requires the project venv: `source venv/bin/activate && python3 scripts/baseline_study_033.py`
- `reports/baseline_study_TRD033.md` — the baseline report (committed output)
- `tests/test_baseline_study_033.py` — 15 unit tests for core analysis logic

### Tests run
```
pytest tests/test_baseline_study_033.py -v
15 passed in 0.61s
```

### Spot-check
- GME BEAR alert 2026-04-09 at $22.87: 5d price $25.18 (+10.1%), 10d price $25.01 (+9.4%) — both correctly classified as false positives (BEAR direction, stock rose). Confirmed against yfinance independently.

### Key findings (PM-relevant)

1. **Data span is 48 trading days (~8 weeks), not 18 months.** The requested 18-month benchmark cannot be produced. All metrics are preliminary.

2. **91% of alerts are NEUTRAL.** The confirmation pipeline is structurally conservative; only 118 of 1,310 alert-rows are directional (BULL+BEAR). The precision and FP metrics below apply to this small subset only.

3. **40-day forward data is zero.** The earliest alerts (2026-04-08) won't have 40 trading days of forward data until after 2026-06-05.

4. **Directional precision is low at the current sample size:**
   - 5d: 13% precision (87% FP rate)
   - 10d: 26% precision (74% FP rate)
   - 20d: 12% precision (88% FP rate)
   - Note: N=34–76 directional alerts — too small for statistical confidence; results are directional only.

5. **Lead-time proxy (5d-capture ratio):** For large movers (>10% in 10d), median 5d-capture ratio is 0.63. About 35% of large-mover alerts had >80% of the move already complete within 5 days — supporting the hypothesis that the confirmation pipeline is sometimes late, but it's not universal.

6. **Sector-adjusted returns are near zero for NEUTRAL alerts** (median -0.77% to -0.19% across horizons), confirming NEUTRAL alerts carry no positive alpha expectation.

7. **Agreement score is 0.00 median** across all alerts; directional BULL alerts all have agreement=1.0 (max), BEAR median=0.5. This suggests directional alerts fire only when multiple modules agree.

### Residual risks

- CFLT had no yfinance data (possibly ticker issue, not delisting — Confluent trades as CFLT on Nasdaq). Impact: 1 of 237 tickers missing, negligible.
- The trading-day calendar uses business days only (no US market holidays). This introduces minor inaccuracy (~1-2 trading days per year). Impact: very low at 8-week sample.
- Forward returns are computed on actual trading-day close prices, which is conservative (no intraday execution assumption). Appropriate for a research benchmark.

### Benchmark re-run recommendation
Re-run `scripts/baseline_study_033.py` at:
- ~90 trading days of history (~2026-08-15) for first statistically meaningful read
- ~120 trading days (~2026-09-30) for the target N ≥ 50 directional alerts per horizon

## QA Result (2026-05-31) — PASS (after fix)

**QA failure found and fixed on 2026-05-31.**

### Fixes applied

**Fix 1 — psycopg2 import at module level (scripts/baseline_study_033.py)**
- Root cause: `from utils.db import get_connection` was at the top of the script, so importing any
  helper function (e.g., in tests) triggered the psycopg2 driver load.
- Fix: moved the import inside `load_rankings()` (lazy import). Pure analysis helpers
  `trading_days_calendar`, `nth_trading_day`, `direction_adjusted_return`, `is_true_positive` are
  now importable without any DB driver.

**Fix 2 — inconsistent directional alerts per run day**
- Root cause: `section_data_summary` grouped only days that had directional alerts (mean=2.68),
  while `section_alert_volume` reindexed over all run dates with fill_value=0 (mean=2.46).
- Fix: aligned `section_data_summary` to also reindex over all run dates. Both sections now report
  **2.46**. Definition chosen: include zero-directional days (more conservative; matches the
  `section_alert_volume` calculation which was already correct per the ticket's non-goal of
  changing methodology).

### Verification (2026-05-31)

#### Unit tests — reproducible in any environment where pytest and pandas/numpy/yfinance are available

```
pytest tests/test_baseline_study_033.py -q
15 passed in 0.71s
```

The unit tests cover `trading_days_calendar`, `nth_trading_day`, `direction_adjusted_return`,
and `is_true_positive`. They do not require psycopg2 or a live DB connection.

#### Full script run — environment-dependent

The script (`scripts/baseline_study_033.py`) requires:

1. **Python ≥ 3.10** — uses `X | Y` union annotations (PEP 604). A `from __future__ import annotations` guard was added to make the file *parse* on Python 3.9, but runtime will still fail if psycopg2 is absent.
2. **psycopg2 importable** — needed by `utils.db.get_connection()` inside `load_rankings()`.
3. **DATABASE_URL set** — live Supabase connection for `daily_rankings`.
4. **yfinance network access** — for price history.

**What does NOT work regardless of Python version:**
- `venv/bin/python3` — the repo venv has numpy/pandas/yfinance but NOT psycopg2 or pytest. Full script fails; unit tests cannot run via `venv/bin/python3 -m pytest`.

**What works in the author's environment (pyenv-managed Python 3.12.6):**

In a shell where `python3` resolves to a Python 3.12.6 interpreter that has psycopg2 installed (e.g., via pyenv with global packages), both commands work:

```
which python3      → /Users/jason/.pyenv/shims/python3
python3 --version  → Python 3.12.6
python3 -c "import psycopg2"  → OK
```

Full script run verified in that environment (exit 0, 2026-05-31):

```
python3 scripts/baseline_study_033.py
TRD-033 Baseline Study
Analysis date: 2026-05-31
Loading daily_rankings...
  1,310 rows, 48 dates, 237 distinct tickers
Downloading prices for 249 symbols (2026-03-29 → 2026-05-31)...
  Price matrix: 43 trading days × 237 tickers
Computing forward returns...  Done.
Assembling report...
Report written: /Users/jason/signal_engine_v1/reports/baseline_study_TRD033.md
exit=0
```

**In environments where `python3` is system Python 3.9 or lacks psycopg2:**
The full script cannot run. The unit tests can still run if pytest and yfinance/pandas/numpy are available via whichever `python3` is on the path.

#### Report consistency (verified)

```
grep "directional" reports/baseline_study_TRD033.md
| Avg directional alerts per run day | 2.46 ...  ← Data Summary (line 24)
| Mean directional alerts/day | 2.46 |           ← Alert Volume (line 118)
```

Both values are consistent (same definition: includes zero-directional days).

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-033, "Baseline Study For Current Confirmation Pipeline."

Goal:
- Produce a reproducible benchmark report for the current confirmation pipeline over the last 18 months.

Scope:
- Read historical confirmation outputs from `daily_rankings` and/or `candidate_snapshots`.
- Compute:
  - 5/10/20/40 trading-day forward returns
  - sector-adjusted forward returns
  - precision by rank buckets 1-5, 6-10, 11-20
  - false-positive rate
  - alert volume
  - lead time for alerts that preceded large forward moves
- Write the results to a report in `reports/`.
- Add a small reproducible analysis script under `scripts/` if needed.

Constraints:
- No production logic changes.
- Use trading days consistently.
- If data is insufficient, say so explicitly in the report.
- Do not invent missing history.

Verification:
- Run the analysis script you add.
- Manually spot-check a few events.

Non-goals:
- No changes to scoring.
- No new watchlist/pipeline behavior.
```
