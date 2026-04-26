# Post-Fix Pipeline Smoke Check — Round 4

**Generated:** 2026-04-26 20:15 UTC  
**Schema fix commits:** `7caf4e4` (2026-04-26 13:05) + `7af59be` (2026-04-26 13:08)  

---

## 1. Executive Verdict

**PASS ✅**

squeeze_state 100% / risk_score 100% / explanation_json 100%

> **Next milestone:** Continue accumulating data toward the CHUNK-12 gate (~2026-05-26).

---

## 2. Latest Run Coverage

### Recent run dates

| Date | Row count |
|---|---|
| 2026-04-26 | 20 ← latest |
| 2026-04-25 | 20 |
| 2026-04-24 | 20 |
| 2026-04-23 | 20 |
| 2026-04-21 | 20 |

**Unique tickers in latest run:** 20  
**Post-fix run?** Yes ✅ (2026-04-26 run contains all CHUNK fields)

### CHUNK field coverage — latest run

| Field | Non-null | Coverage |
|---|---|---|
| `computed_dtc_30d` | 20/20 | 20/20 (100%) |
| `compression_recovery_score` | 20/20 | 20/20 (100%) |
| `volume_confirmation_flag` | 20/20 | 20/20 (100%) |
| `squeeze_state` | 20/20 | 20/20 (100%) |
| `risk_score` | 20/20 | 20/20 (100%) |
| `risk_level` | 20/20 | 20/20 (100%) |
| `options_pressure_score` | 20/20 | 20/20 (100%) |
| `explanation_summary` | 20/20 | 20/20 (100%) |
| `explanation_json` | 20/20 | 20/20 (100%) |
| `state_confidence` | 20/20 | 20/20 (100%) |
| `state_reasons` | 20/20 | 20/20 (100%) |
| `state_warnings` | 0/20 | 0/20 (0%) — by design: NULL when no warnings triggered |
| `dilution_risk_flag` | 20/20 | 20/20 (100%) |
| `iv_rank` | 9/20 | 9/20 (45%) |
| `si_persistence_score` (via explanation_json) | 0/20 | 0/20 (0%) — structural gap: not stored in explanation_json (pre-CHUNK-12 follow-up) |
| `effective_float_score` (via explanation_json) | 20/20 | 20/20 (100%) |

---

## 3. Compile and Test State

| Check | Result |
|---|---|
| `py_compile` (supabase_persist, backtest, squeeze_screener) | ✅ OK |
| `pytest` (persistence schema + replay + screener) | ✅ ============================= 105 passed in 5.08s ============================== |

---

## 4. CHUNK-12 Gate Progress

| Gate item | Required | Current | Status |
|---|---:|---:|---|
| Calendar days of post-fix squeeze_scores | ≥ 30 | **1** | ❌ |
| New-format rows total | ≥ 500 | **20** | ❌ |
| Rows with ARMED or ACTIVE state | ≥ 50 | **2** | ❌ |
| 20-day forward return windows closed | ≥ 100 rows | **0** | ❌ |
| Rows with non-null `risk_score` | ≥ 100 | **20** | ❌ |
| Rows with non-null `options_pressure_score` | ≥ 20 | **20** | ✅ |
| `short_interest_history` distinct FINRA periods | ≥ 2 | **0** | ❌ |
| `filing_catalysts` ownership_accumulation_flag tickers | ≥ 5 | **0** | ❌ |
| `iv_history` tickers with ≥ 60 rows | ≥ 50 | **0** | ❌ |

---

## 5. Data Quality Notes

| Item | Severity | Detail |
|---|---|---|
| `state_warnings` NULL 0/20 | Expected | `_lifecycle_json` returns NULL for empty arrays by design. Populates when warnings fire. |
| `iv_rank` 9/20 (45%) | Expected | Needs 60+ rows in `iv_history` per ticker (~3 months to accumulate). |
| `si_persistence_score` not in `explanation_json` | Follow-up | Score is computed and used in `final_score` but not written to `explanation_json`. `_extract_from_explanation` in `backtest.py` looks for `top_positive_drivers[key="si_persistence"]` — that key doesn't exist in current JSON format. Replay rows will show NaN for `si_persistence_score`. Non-blocking for CHUNK-12 gate but should be addressed before backtesting SI-persistence signal effectiveness. |
| `effective_float_score` via JSON | OK | 20/20 rows match — stored in `data_quality_notes[key="effective_float_confidence"]`. |
| `short_interest_history` distinct FINRA periods | Expected | 0 periods — SI snapshot table empty. Populates as daily pipeline accumulates FINRA data. |

---

## 6. Recommendation

**Pipeline persistence is working correctly. Continue data accumulation toward the CHUNK-12 gate.**

All critical CHUNK fields are being written on every run. The system is accumulating valid replay data.
Re-check gate progress in this report after each daily run.
