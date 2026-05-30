# Post-Fix Pipeline Smoke Check — Round 9

**Generated:** 2026-05-30 17:21 UTC  
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
| 2026-05-30 | 23 ← latest |
| 2026-05-29 | 21 |
| 2026-05-28 | 20 |
| 2026-05-27 | 20 |
| 2026-05-26 | 20 |

**Unique tickers in latest run:** 23  
**Post-fix run?** Yes ✅

### CHUNK field coverage — latest run

| Field | Non-null | Coverage |
|---|---|---|
| `computed_dtc_30d` | 23/23 | 23/23 (100%) |
| `compression_recovery_score` | 23/23 | 23/23 (100%) |
| `volume_confirmation_flag` | 23/23 | 23/23 (100%) |
| `si_persistence_score` | 23/23 | 23/23 (100%) |
| `squeeze_state` | 23/23 | 23/23 (100%) |
| `risk_score` | 23/23 | 23/23 (100%) |
| `risk_level` | 23/23 | 23/23 (100%) |
| `options_pressure_score` | 23/23 | 23/23 (100%) |
| `explanation_summary` | 23/23 | 23/23 (100%) |
| `explanation_json` | 23/23 | 23/23 (100%) |
| `state_confidence` | 23/23 | 23/23 (100%) |
| `state_reasons` | 23/23 | 23/23 (100%) |
| `state_warnings` | 0/23 | 0/23 (0%) — old-format |
| `dilution_risk_flag` | 23/23 | 23/23 (100%) |
| `iv_rank` | 5/23 | 5/23 (22%) |
| `effective_float_score` (via explanation_json) | 23/23 | 23/23 (100%) |

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
| Calendar days of post-fix squeeze_scores | ≥ 30 | **24** | ❌ |
| New-format rows total | ≥ 500 | **484** | ❌ |
| Rows with ARMED or ACTIVE state | ≥ 50 | **40** | ❌ |
| 20-day forward return windows closed | ≥ 100 rows | **120** | ✅ |
| Rows with non-null `risk_score` | ≥ 100 | **484** | ✅ |
| Rows with non-null `options_pressure_score` | ≥ 20 | **484** | ✅ |
| `short_interest_history` distinct FINRA periods | ≥ 2 | **0** | ❌ |
| `filing_catalysts` ownership_accumulation_flag tickers | ≥ 5 | **0** | ❌ |
| `iv_history` tickers with ≥ 60 rows | ≥ 50 | **0** | ❌ |

---

## 5. Recommendation

**Pipeline persistence is working correctly. Continue data accumulation toward the CHUNK-12 gate.**

All critical CHUNK fields are being written on every run. The system is accumulating valid replay data.
Re-check gate progress in this report after each daily run.
