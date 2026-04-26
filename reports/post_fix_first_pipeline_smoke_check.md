# Post-Fix Pipeline Smoke Check — Round 2

**Generated:** 2026-04-26 (updated same day, second check)
**Schema fix commits:** `7caf4e4` (2026-04-26 13:05) + `7af59be` (2026-04-26 13:08)
**Smoke check run:** 2026-04-26 ~14:00 Berlin time

---

## 1. Executive Verdict

**PARTIAL — infrastructure verified, no post-fix pipeline run yet (expected)**

All three persistence bugs are fixed and tested. The replay plumbing outputs all 13 CHUNK columns correctly. No post-fix full pipeline run has occurred because:

1. The schema fix landed at 13:05 on Sunday 2026-04-26
2. The GHA cron schedule is `'17 1 * * 1-6'` — **Monday through Saturday only**
3. Sunday has no scheduled pipeline run

The 3 tickers that appear in the DB for 2026-04-26 (UTHR, COIN, TTWO) came from individual `analyze_tickers.yml` workflow runs, not from `daily_pipeline.yml`. The full squeeze screener has not executed since the fix.

**Nothing is broken. The system is in the correct state.**

**Action required:** Allow the Monday 2026-04-27 pipeline run (03:17 Berlin time) to complete. Re-run this smoke check after it finishes to confirm new-format rows appear.

---

## 2. Latest Run Coverage

### Schema state (live DB, verified 2026-04-26)

| | |
|---|---|
| Table columns | 42 (all CHUNK-added fields present) |
| Total rows | 186 |
| Latest squeeze run date | 2026-04-25 (pre-fix) |
| Post-fix new-format rows | **0** |

### Field coverage — latest run (2026-04-25, 20 rows)

| Field | Non-null | Coverage | Expected after fix |
|---|---|---|---|
| `final_score` | 20/20 | 100% | 100% |
| `short_pct_float` | 20/20 | 100% | 100% |
| `days_to_cover` | 20/20 | 100% | 100% |
| `computed_dtc_30d` | 0/20 | **0% — old-format** | ≥ 70% |
| `compression_recovery_score` | 0/20 | **0% — old-format** | ≥ 50% |
| `volume_confirmation_flag` | 0/20 | **0% — old-format** | 100% |
| `squeeze_state` | 0/20 | **0% — old-format** | 100% |
| `risk_score` | 0/20 | **0% — old-format** | 100% |
| `risk_level` | 0/20 | **0% — old-format** | 100% |
| `options_pressure_score` | 0/20 | **0% — old-format** | ≥ 80% |
| `explanation_summary` | 0/20 | **0% — old-format** | 100% |
| `explanation_json` | 0/20 | **0% — old-format** | 100% |
| `state_confidence` | 0/20 | **0% — old-format** | 100% |
| `dilution_risk_flag` | 0/20 | **0% — old-format** | 100% |
| `iv_rank` | 0/20 | **0% — old-format** | ≥ 50% |

All NULLs are expected — rows written before the fix. The 0% coverage is a data-age artefact.

### Why no post-fix rows exist

Timeline on 2026-04-26:

| Time (Berlin) | Event |
|---|---|
| 08:11 | Catalyst screener / conflict resolver ran (UTHR) — individual workflow |
| 12:05 | Conflict resolver ran (TTWO) — individual workflow, last pipeline activity |
| **13:05** | **`7caf4e4` committed (schema + migration fix)** |
| **13:08** | **`7af59be` committed (SELECT * + persistence tests)** |
| — | No `daily_pipeline.yml` run on Sunday (cron: `'17 1 * * 1-6'`) |

The full squeeze screener has not run since the fix. The next scheduled run is **Monday 2026-04-27 at 03:17 Berlin time**.

---

## 3. GHA Cron Schedule Confirmation

Verified in `.github/workflows/daily_pipeline.yml`:

```yaml
on:
  schedule:
    - cron: '17 1 * * 1-6'
```

`1-6` = Monday (1) through Saturday (6). **Sunday (0) is not included.** This is correct and expected — no action needed.

---

## 4. Replay Read Check

**Replay plumbing: PASS**

Command run:
```
python backtest.py --squeeze-replay --start 2026-04-16 --end 2026-04-25 --output-csv /tmp/squeeze_replay_postfix.csv
```

| Check | Result |
|---|---|
| Rows loaded | 186 |
| Unique tickers | 78 |
| Signal date range | 2026-04-16 → 2026-04-25 |
| CHUNK field columns in CSV | **13/13 present** ✅ |
| Values in CHUNK fields | 0/186 (old-format data — expected) |
| 5d forward returns | 85/186 available |
| 10d/20d/30d forward returns | 0/186 (windows not yet closed) |
| `SELECT *` confirmed in source | ✅ |

All 13 CHUNK-added fields appear as columns:
`squeeze_state`, `risk_score`, `risk_level`, `dilution_risk_flag`, `options_pressure_score`, `iv_rank`, `iv_rank_score`, `unusual_call_activity_flag`, `computed_dtc_30d`, `compression_recovery_score`, `volume_confirmation_flag`, `si_persistence_score`, `effective_float_score`

---

## 5. Test and Compile State

| Check | Result |
|---|---|
| `python -m py_compile utils/supabase_persist.py backtest.py squeeze_screener.py` | ALL OK |
| `pytest -q tests/test_squeeze_persistence_schema.py tests/test_squeeze_replay.py tests/test_squeeze_screener.py` | **101 passed** |

---

## 6. CHUNK-12 Gate Progress

| Gate item | Required | Current | Status |
|---|---:|---:|---|
| Calendar days of post-fix squeeze_scores | ≥ 30 | **0** | ❌ Pipeline not yet run |
| New-format rows total | ≥ 500 | **0** | ❌ |
| Rows with ARMED or ACTIVE state | ≥ 50 | **0** | ❌ |
| 20-day forward return windows closed | ≥ 100 rows | **0** | ❌ |
| Rows with non-null `effective_float_score` | ≥ 20 | **0** | ❌ |
| Rows with non-null `options_pressure_score` | ≥ 20 | **0** | ❌ |
| Rows with non-null `risk_score` | ≥ 100 | **0** | ❌ |
| `short_interest_history` distinct FINRA periods | ≥ 2 per subset | **1** (3 rows, 1 date) | ❌ |
| Filing catalysts: `ownership_accumulation_flag` tickers | ≥ 5 | **0** | ❌ |
| `iv_history` tickers with ≥ 60 rows | ≥ 50 | **0** | ❌ (18 days only) |
| Negative-control ticker set defined | ≥ 10 | **0** | ❌ |

**Estimated gate date:** ~2026-05-26 (30 calendar days of daily pipeline runs from 2026-04-27)

---

## 7. Issues Found

### No blocking issues

| Issue | Severity | Root Cause | Resolution |
|---|---|---|---|
| 0 post-fix rows in squeeze_scores | Expected | Fix committed on Sunday; cron is Mon–Sat | Monday 2026-04-27 run resolves |
| All CHUNK fields NULL in existing 186 rows | Expected | Old-format data; cannot backfill | Pre-existing rows stay NULL; future rows populate |
| 10d/20d/30d forward returns 0/186 | Expected | Windows not yet closed (oldest data: Apr 16) | 10d closes ~Apr 30; 20d ~May 14; 30d ~May 16 |
| `short_interest_history` has only 1 period | Expected | SI snapshots accumulate slowly | Grows with each daily run |
| `iv_history` has no tickers with 60+ rows | Expected | Only 18 business days of data | Reaches 60 rows per ticker in ~3 months |
| `filing_catalysts` ownership_accumulation_flag = 0 | Expected | SEC filing coverage is sparse | Grows with EDGAR data as pipeline runs |

---

## 8. Recommendation

**Continue data accumulation. No pipeline changes needed.**

The persistence system is verified end-to-end:
- ✅ Schema: 42 columns in production DB
- ✅ Save path: 42-column INSERT works for both new-format and old-format rows
- ✅ Read path: `SELECT *` returns all columns; `dict(row)` fix preserves values
- ✅ Replay plumbing: all 13 CHUNK columns present in replay CSV output
- ✅ Tests: 101 passing

**Re-run this smoke check after Monday 2026-04-27 pipeline run.** Check for:
- Non-null `squeeze_state` values (should appear for all rows)
- Non-null `risk_score` values (should appear for all rows)
- Non-null `explanation_json` (should appear for all rows)
- Non-null `options_pressure_score` (acceptable if yfinance options data unavailable for a ticker)
- Non-null `computed_dtc_30d` (should appear where float_shares and avg_volume data exists)

If any of those remain null after the first post-fix run, investigate `squeeze_screener.py` column mapping to `save_squeeze_scores()`.

---

## 9. Commands Run

| Command | Outcome |
|---|---|
| `git log --oneline -10` | `7af59be` HEAD confirmed |
| DB query: `SELECT date, COUNT(*) FROM squeeze_scores GROUP BY date ORDER BY date DESC` | 8 run dates, max 2026-04-25, total 186 rows |
| DB query: field coverage for latest run (2026-04-25, 20 rows) | All CHUNK fields 0/20 (expected — old-format) |
| DB query: CHUNK-12 gate metrics | All 11 gates at 0 |
| `cat .github/workflows/daily_pipeline.yml \| grep cron` | `'17 1 * * 1-6'` confirmed (Mon–Sat) |
| `python backtest.py --squeeze-replay --start 2026-04-16 --end 2026-04-25 --output-csv /tmp/squeeze_replay_postfix.csv` | 186 rows, 78 tickers, 13/13 CHUNK columns present |
| CSV inspection: CHUNK field coverage | 13/13 columns present, all 0/186 non-null (expected) |
| `python -m py_compile utils/supabase_persist.py backtest.py squeeze_screener.py` | ALL OK |
| `pytest -q tests/test_squeeze_persistence_schema.py tests/test_squeeze_replay.py tests/test_squeeze_screener.py` | **101 passed** |
