# Post-Fix Persistence Verification

**Generated:** 2026-04-26  
**Follows:** commit `7caf4e4` (missing migration columns + RealDictCursor fix)  
**Additional fix in this session:** `fetch_squeeze_scores_for_replay` SELECT * upgrade  
**Commit:** see Section 6  

---

## 1. Executive Verdict

**PASS**

All three persistence bugs discovered during the CHUNK-12 readiness analysis have been fixed and verified:

1. `_SQUEEZE_MIGRATE_DDL` was missing four ADD COLUMN statements for `computed_dtc_30d`, `compression_recovery_score`, `volume_confirmation_flag`, and `squeeze_state`. Live DB now has 42 columns. ✅

2. `fetch_squeeze_scores_for_replay` used `dict(zip(cols, row))` with `RealDictCursor`, mapping every column to its own name instead of its value. Fixed to `dict(row)`. ✅

3. `fetch_squeeze_scores_for_replay` only SELECTed 12 columns, leaving CHUNK-16 (`risk_score`, `risk_level`, `dilution_risk_flag`, etc.) and CHUNK-09 (`options_pressure_score`, `iv_rank`, etc.) fields permanently absent from replay rows even after new data accumulated. Fixed to `SELECT *`. ✅

The persistence pipeline is now correctly wired end-to-end. Future pipeline runs will save all 42 fields and replay reads will return all of them.

The system is **ready to accumulate valid replay data**. CHUNK-12 itself should NOT be implemented yet — see Section 5 for data gate.

---

## 2. Schema Verification

### 2.1 Live DB Schema

| | |
|---|---|
| **Table** | `squeeze_scores` |
| **Column count** | **42** (verified 2026-04-26) |
| **Migration entries** | 24 (20 original + 4 added in `7caf4e4`) |
| **Source** | Production Supabase DB via session pooler |

### 2.2 Per-Field Status

| Field | In base `_SQUEEZE_DDL` | In `_SQUEEZE_MIGRATE_DDL` | In `save_squeeze_scores()` INSERT | In `fetch` SELECT (`SELECT *`) | Status |
|---|---|---|---|---|---|
| date | ✅ | — | ✅ | ✅ | OK |
| ticker | ✅ | — | ✅ | ✅ | OK |
| final_score | ✅ | — | ✅ | ✅ | OK |
| short_pct_float | ✅ | — | ✅ | ✅ | OK |
| days_to_cover | ✅ | — | ✅ | ✅ | OK |
| **computed_dtc_30d** | ✅ DDL | ✅ `7caf4e4` | ✅ | ✅ | **Fixed** |
| **compression_recovery_score** | ✅ DDL | ✅ `7caf4e4` | ✅ | ✅ | **Fixed** |
| **volume_confirmation_flag** | ✅ DDL | ✅ `7caf4e4` | ✅ | ✅ | **Fixed** |
| **squeeze_state** | ✅ DDL | ✅ `7caf4e4` | ✅ | ✅ | **Fixed** |
| explanation_summary | ✅ DDL | ✅ | ✅ | ✅ | OK |
| explanation_json | ✅ DDL | ✅ | ✅ | ✅ | OK |
| state_confidence | ❌ DDL | ✅ | ✅ | ✅ | OK |
| state_reasons | ❌ DDL | ✅ | ✅ | ✅ | OK |
| state_warnings | ❌ DDL | ✅ | ✅ | ✅ | OK |
| risk_score | ❌ DDL | ✅ | ✅ | ✅ | OK |
| risk_level | ❌ DDL | ✅ | ✅ | ✅ | OK |
| risk_flags | ❌ DDL | ✅ | ✅ | ✅ | OK |
| risk_warnings | ❌ DDL | ✅ | ✅ | ✅ | OK |
| risk_components | ❌ DDL | ✅ | ✅ | ✅ | OK |
| dilution_risk_flag | ❌ DDL | ✅ | ✅ | ✅ | OK |
| latest_dilution_filing_date | ❌ DDL | ✅ | ✅ | ✅ | OK |
| shares_offered_pct_float | ❌ DDL | ✅ | ✅ | ✅ | OK |
| options_pressure_score | ❌ DDL | ✅ | ✅ | ✅ | OK |
| iv_rank | ❌ DDL | ✅ | ✅ | ✅ | OK |
| iv_rank_score | ❌ DDL | ✅ | ✅ | ✅ | OK |
| iv_data_confidence | ❌ DDL | ✅ | ✅ | ✅ | OK |
| unusual_call_activity_flag | ❌ DDL | ✅ | ✅ | ✅ | OK |
| call_put_volume_ratio | ❌ DDL | ✅ | ✅ | ✅ | OK |
| call_put_oi_ratio | ❌ DDL | ✅ | ✅ | ✅ | OK |

### 2.3 Fields NOT persisted as direct columns (by design)

| Field | Storage | How accessed in replay |
|---|---|---|
| `si_persistence_score` | `explanation_json` > `top_positive_drivers[key="si_persistence"]` | `_extract_from_explanation(snap["explanation_json"], "si_persistence")` |
| `si_persistence_count` | `explanation_json` > signal_breakdown | Not directly accessible via `_extract_from_explanation`; needs raw JSON parse |
| `effective_float_score` | `explanation_json` > `top_positive_drivers[key="effective_float"]` | `_extract_from_explanation(snap["explanation_json"], "effective_float")` |

These are correctly surfaced in `_build_replay_row()` via `_extract_from_explanation()`. No schema change needed.

---

## 3. Save/Read Verification

### 3.1 What was tested

23 unit tests in `tests/test_squeeze_persistence_schema.py`, all passing. No live DB calls.

**Save path (6 tests):**
- Full 42-column new-format row builds a 42-element tuple ✅
- Tuple positions verified for critical CHUNK fields (risk_score at 27, risk_level at 28, dilution_risk_flag at 32, options_pressure_score at 35, iv_rank at 36) ✅
- Old-format row with only 18 fields builds a 42-element tuple with `None` at all CHUNK positions — no crash ✅
- Empty DataFrame produces zero tuples ✅
- `save_squeeze_scores()` called with mocked connection: `executemany` receives 42-element tuples ✅ (both full and old-format)

**Read path (5 tests):**
- `dict(row)` on a dict-like object returns key→value correctly ✅
- Mock cursor returning dict rows: `fetch_squeeze_scores_for_replay` returns real values (not column-name→column-name) ✅
- Ticker-filter variant also correct ✅
- DB error returns `[]` ✅
- Source code verified to use `SELECT *` (not a hard-coded 12-column list) ✅

**Replay row construction (4 tests):**
- CHUNK-16 fields (`risk_score`, `risk_level`, `dilution_risk_flag`) present in replay row when snap has them ✅
- CHUNK-09 fields (`options_pressure_score`, `iv_rank`, `iv_rank_score`, `unusual_call_activity_flag`) present in replay row ✅
- `squeeze_state` surfaced correctly ✅
- `si_persistence_score` and `effective_float_score` extracted from `explanation_json` ✅

**Backward compatibility (4 tests):**
- Old pre-CHUNK row (no new fields) does not crash ✅
- Old row CHUNK fields return `None` or `NaN` ✅
- Old row forward returns compute normally ✅
- Mixed old+new rows in same replay run: old gets `None` for CHUNK fields, new gets values ✅

### 3.2 RealDictCursor bug confirmed fixed

Before fix: `dict(zip(cols, row))` with `RealDictCursor` iterated the dict's *keys*, producing `{"date": "date", "ticker": "ticker", ...}`. Every row value equaled its column name.

After fix: `dict(row)` directly converts the `RealDictRow` to a plain dict, preserving all key→value mappings. Verified by mock test and by live replay returning real tickers/scores.

### 3.3 Old rows remain compatible

The 186 pre-CHUNK rows in `squeeze_scores` (dates 2026-04-16 to 2026-04-25) continue to work. `fetch_squeeze_scores_for_replay` with `SELECT *` returns those rows with `None` for all CHUNK-added columns (since those columns were NULL when the rows were written). `_build_replay_row` handles `None` for all new fields via `snap.get("field_name")`. No regressions.

---

## 4. Current Replay Data Limitation

The 186 rows saved in `squeeze_scores` between 2026-04-16 and 2026-04-25 are **old-format only**:

- Written by the pre-CHUNK squeeze screener (18 columns)
- All CHUNK-added fields are NULL (no squeeze_state, risk_score, compression_recovery_score, explanation_json, etc.)
- These rows cannot be backfilled — yfinance does not provide point-in-time SI, options, or IV data for past dates

**Why they were never updated to new format:**  
The `save_squeeze_scores()` INSERT references 42 columns. The production DB only had 38 columns (missing the 4 from the first bug fix). Every INSERT call after CHUNK-01/04/05/10 was committed silently failed with `UndefinedColumn`, and the old data from before those CHUNKs remained in place.

**What changes now:**  
The next pipeline run will:
1. Execute `_SQUEEZE_MIGRATE_DDL` (idempotent — already migrated, ALTER IF NOT EXISTS is a no-op)
2. INSERT 42-column rows with all CHUNK fields populated
3. `fetch_squeeze_scores_for_replay` will return complete rows with `SELECT *`
4. Replay will surface `squeeze_state`, `risk_score`, `options_pressure_score`, etc. for the first time

The pre-existing 186 rows are not affected (they remain as NULL-padded old-format rows, still readable).

---

## 5. Data Accumulation Gate for CHUNK-12

The following thresholds must all be met before reconsidering CHUNK-12:

| Requirement | Minimum | Current | Status |
|---|---|---|---|
| Calendar days of **post-`7caf4e4`** squeeze_scores | **≥ 30 days** | 0 (pipeline not yet run) | ❌ |
| Total squeeze_scores rows (new format) | **≥ 500** | 0 new-format | ❌ |
| Rows with ARMED or ACTIVE state | **≥ 50** | 0 | ❌ |
| 20-day forward return windows closed | **≥ 100 rows** | 0 | ❌ |
| Rows with non-null `effective_float_score` (via explanation_json) | **≥ 20** | 0 | ❌ |
| Rows with non-null `options_pressure_score` | **≥ 20** | 0 | ❌ |
| Rows with non-null `risk_score` | **≥ 100** | 0 | ❌ |
| `short_interest_history` rows per ticker | **≥ 3 distinct periods** | 3 rows total, 1 period | ❌ |
| Unique tickers in `filing_catalysts` with `ownership_accumulation_flag` | **≥ 5** | 0 | ❌ |
| `iv_history` tickers with ≥ 60 rows (IV rank reliable) | **≥ 50** | ~0 (18 days only) | ❌ |
| Negative-control ticker set defined | **≥ 10 tickers** | None defined | ❌ |

**Soonest realistic gate:** ~2026-05-26 (30 days of daily pipeline runs from today)

**Next safe action:** Allow daily pipeline runs to accumulate data. Re-run `python backtest.py --squeeze-replay --start 2026-04-27 --end 2026-05-26` (or similar) after the gate date to evaluate CHUNK-12 readiness.

---

## 6. Commands Run

| Command | Outcome |
|---|---|
| `git status --short` | Only unrelated dirty files (dashboard, pycache) |
| `git log --oneline -10` | `7caf4e4` confirmed as HEAD |
| `git show --stat 7caf4e4` | 2 files, 350 insertions |
| Read `utils/supabase_persist.py` (DDL, migration, save payload, fetch query) | Identified third bug: SELECT * needed |
| Read `tests/test_squeeze_replay.py` | Confirmed existing test coverage |
| `grep` on SqueezeScore dataclass fields | Confirmed si_persistence_score/effective_float_score not direct columns |
| **Bug fix 3:** Changed `fetch_squeeze_scores_for_replay` to `SELECT *` | Ensures all CHUNK columns returned |
| `python -m py_compile utils/supabase_persist.py backtest.py squeeze_screener.py` | ALL OK |
| Wrote `tests/test_squeeze_persistence_schema.py` (23 tests) | All 23 pass |
| `pytest -q tests/test_squeeze_replay.py tests/test_squeeze_screener.py tests/test_squeeze_persistence_schema.py` | **101 passed** |
| Live DB schema check: `SELECT column_name FROM information_schema.columns WHERE table_name='squeeze_scores'` | 42 columns confirmed |
| Live DB row count | 186 rows, 78 tickers, 2026-04-16 to 2026-04-25 (all old-format) |

---

## 7. Recommendation

**Proceed with data accumulation. Do not implement CHUNK-12 yet.**

The persistence pipeline is fully verified:
- Schema: 42 columns in production ✅
- Save path: 42-column tuples built correctly, old rows handled gracefully ✅
- Read path: real values returned, CHUNK-16/CHUNK-09 fields included ✅
- Tests: 101 passing (36 replay + 42 screener + 23 persistence schema) ✅

The system will now correctly persist and read all CHUNK-added signal fields from the next pipeline run onward. Re-run the CHUNK-12 readiness analysis (`reports/chunk_12_readiness_report.md`) after the data gate in Section 5 is met — estimated ~2026-05-26.

**No CHUNK-12 implementation until the gate is met.**
