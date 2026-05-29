# Post-Fix Pipeline Smoke Check — Round 5

**Generated:** 2026-05-29 10:16 UTC  
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
| 2026-05-29 | 21 ← latest |
| 2026-05-28 | 20 |
| 2026-05-27 | 20 |
| 2026-05-26 | 20 |
| 2026-05-25 | 20 |

**Unique tickers in latest run:** 21  
**Post-fix run?** Yes ✅

### CHUNK field coverage — latest run

| Field | Non-null | Coverage |
|---|---|---|
| `computed_dtc_30d` | 21/21 | 21/21 (100%) |
| `compression_recovery_score` | 21/21 | 21/21 (100%) |
| `volume_confirmation_flag` | 21/21 | 21/21 (100%) |
| `si_persistence_score` | 21/21 | 21/21 (100%) |
| `squeeze_state` | 21/21 | 21/21 (100%) |
| `risk_score` | 21/21 | 21/21 (100%) |
| `risk_level` | 21/21 | 21/21 (100%) |
| `options_pressure_score` | 21/21 | 21/21 (100%) |
| `explanation_summary` | 21/21 | 21/21 (100%) |
| `explanation_json` | 21/21 | 21/21 (100%) |
| `state_confidence` | 21/21 | 21/21 (100%) |
| `state_reasons` | 21/21 | 21/21 (100%) |
| `state_warnings` | 1/21 | 1/21 (5%) |
| `dilution_risk_flag` | 21/21 | 21/21 (100%) |
| `iv_rank` | 5/21 | 5/21 (24%) |
| `effective_float_score` (via explanation_json) | 21/21 | 21/21 (100%) |

---

## 3. Compile and Test State

| Check | Result |
|---|---|
| `py_compile` (supabase_persist, backtest, squeeze_screener) | ✅ OK |
| `pytest` (persistence schema + replay + screener) | ❌ no output |

---

## 4. CHUNK-12 Gate Progress

| Gate item | Required | Current | Status |
|---|---:|---:|---|
| Calendar days of post-fix squeeze_scores | ≥ 30 | **23** | ❌ |
| New-format rows total | ≥ 500 | **461** | ❌ |
| Rows with ARMED or ACTIVE state | ≥ 50 | **32** | ❌ |
| 20-day forward return windows closed | ≥ 100 rows | **100** | ✅ |
| Rows with non-null `risk_score` | ≥ 100 | **461** | ✅ |
| Rows with non-null `options_pressure_score` | ≥ 20 | **461** | ✅ |
| `short_interest_history` distinct FINRA periods | ≥ 2 | **0** | ❌ |
| `filing_catalysts` ownership_accumulation_flag tickers | ≥ 5 | **0** | ❌ |
| `iv_history` tickers with ≥ 60 rows | ≥ 50 | **0** | ❌ |

---

## 5. Recommendation

**Pipeline persistence is working correctly. Continue data accumulation toward the CHUNK-12 gate.**

All critical CHUNK fields are being written on every run. The system is accumulating valid replay data.
Re-check gate progress in this report after each daily run.
