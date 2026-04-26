# First Post-Fix Pipeline Smoke Check

**Generated:** 2026-04-26  
**Schema fix commits:** `7caf4e4` (2026-04-26 13:05) + `7af59be` (2026-04-26 13:08)  
**Smoke check run:** 2026-04-26  

---

## 1. Executive Verdict

**PARTIAL**

The persistence infrastructure is fully verified and correct. All three bugs are fixed. The replay plumbing outputs all 13 CHUNK-added field columns correctly. However, no post-fix pipeline run has occurred yet — the schema fix was committed at 13:05 on 2026-04-26 and the last pipeline activity (conflict_resolution) ran at 12:05 on the same day, before the fix. All 186 existing squeeze_scores rows remain old-format with every CHUNK field NULL.

**What this means:**
- Nothing is broken. The system is in the correct state.
- The first post-fix pipeline run will produce new-format rows with all CHUNK fields populated.
- This smoke check confirms the plumbing is ready; it cannot validate data it hasn't seen yet.

**Action required:** Allow the next scheduled pipeline run to execute. Re-run this smoke check after the first post-fix run to confirm new-format rows appear.

---

## 2. Latest Run Coverage

### Schema state (live DB, verified 2026-04-26)

| | |
|---|---|
| Table columns | 42 (all CHUNK-added fields present) |
| Total rows | 186 |
| Latest run date | 2026-04-25 (pre-fix) |
| Post-fix rows (date > 2026-04-26) | **0** |

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

All NULLs are expected and correct — these rows were written before the schema fix. The 0% coverage is a data-age artefact, not a persistence failure.

### Why no post-fix rows exist yet

Timeline on 2026-04-26:
- 08:11 — Catalyst screener / conflict resolver ran (UTHR)
- 12:05 — Conflict resolver ran (TTWO) — last pipeline activity
- **13:05 — `7caf4e4` committed (schema fix)**
- **13:08 — `7af59be` committed (SELECT * + tests)**

The pipeline ran ~1 hour before the fix landed. No full squeeze screener run has executed since the fix.

---

## 3. Replay Read Check

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

All 13 CHUNK-added fields appear as columns in the replay output:
`squeeze_state`, `risk_score`, `risk_level`, `dilution_risk_flag`, `options_pressure_score`, `iv_rank`, `iv_rank_score`, `unusual_call_activity_flag`, `computed_dtc_30d`, `compression_recovery_score`, `volume_confirmation_flag`, `si_persistence_score`, `effective_float_score`

The `SELECT *` fix ensures that when new-format rows are saved, every column will be returned without requiring code changes to the fetch query. The plumbing is verified correct.

---

## 4. CHUNK-12 Gate Progress

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

**Estimated gate date:** ~2026-05-26 (30 calendar days of daily pipeline runs from tomorrow)

---

## 5. Issues Found

### No blocking issues

All issues below are expected data-age artefacts, not bugs.

| Issue | Severity | Root Cause | Resolution |
|---|---|---|---|
| 0 post-fix rows in squeeze_scores | Expected | Fix committed after last pipeline run | Next pipeline run resolves |
| All CHUNK fields NULL in existing 186 rows | Expected | Old-format data, cannot backfill | Pre-existing rows stay NULL; future rows populate |
| 10d/20d/30d forward returns 0/186 | Expected | Windows not yet closed (oldest data: Apr 16) | 10d closes ~Apr 30; 20d ~May 14; 30d ~May 16 |
| `short_interest_history` has only 1 period | Expected | SI snapshots accumulate slowly | Grows with each daily run |
| `iv_history` has no tickers with 60+ rows | Expected | Only 18 business days of data | Reaches 60 rows per ticker in ~3 months |
| `filing_catalysts` ownership_accumulation_flag = 0 | Expected | SEC filing coverage is sparse | Grows with EDGAR data as pipeline runs |

### Note on 10d forward return window

The oldest signal dates (Apr 16) should have 10d returns available by ~Apr 30 (6 business days away as of Apr 26). After the next pipeline run and once those windows close, partial 10d coverage will appear in the replay. 20d coverage appears ~May 14.

---

## 6. Recommendation

**Continue data accumulation. No pipeline changes needed.**

The persistence system is verified end-to-end:
- ✅ Schema: 42 columns in production DB
- ✅ Save path: 42-column INSERT works for both new-format and old-format rows
- ✅ Read path: `SELECT *` returns all columns; `dict(row)` fix preserves values
- ✅ Replay plumbing: all 13 CHUNK columns present in replay CSV output
- ✅ Tests: 101 passing

The only thing missing is a post-fix pipeline run. The next scheduled GitHub Actions run will be the first to:
1. Execute `save_squeeze_scores()` with the corrected 42-column schema
2. Populate `squeeze_state`, `risk_score`, `explanation_json`, `options_pressure_score`, etc.
3. Allow replay to show non-null CHUNK field values for the first time

**Re-run this smoke check the day after the next pipeline run.** Check for:
- Non-null `squeeze_state` values (should appear for all rows)
- Non-null `risk_score` values (should appear for all rows)
- Non-null `explanation_json` (should appear for all rows)
- Non-null `options_pressure_score` (should appear for most rows; None acceptable when yfinance options data unavailable)
- Non-null `computed_dtc_30d` (should appear where float_shares and avg_volume data exists)

If any of those remain null after the first post-fix run, that is a new issue to investigate in `squeeze_screener.py` or `utils/supabase_persist.py`.

---

## 7. Commands Run

| Command | Outcome |
|---|---|
| `git status --short` | 4 unrelated dirty files (dashboard, pycache) — not touched |
| `git log --oneline -10` | `7af59be` HEAD confirmed |
| DB query: `SELECT date, COUNT(*) FROM squeeze_scores GROUP BY date` | 8 run dates, max 2026-04-25, total 186 rows |
| DB query: field coverage for latest run | All 15 CHUNK fields 0/20 (expected — old-format) |
| DB query: CHUNK-12 gate metrics | All gates at 0 |
| `ls -la logs/` | Last conflict_resolution at 12:05 (pre-fix) |
| `python backtest.py --squeeze-replay --start 2026-04-16 --end 2026-04-25 --output-csv /tmp/squeeze_replay_postfix.csv` | 186 rows, 78 tickers, 13/13 CHUNK columns present |
| CSV inspection: CHUNK field coverage in replay output | 13/13 columns present, all 0/186 non-null (expected) |
| `python -m py_compile utils/supabase_persist.py backtest.py squeeze_screener.py` | ALL OK |
| `pytest -q tests/test_squeeze_persistence_schema.py tests/test_squeeze_replay.py tests/test_squeeze_screener.py` | **101 passed** |
