# Post-Fix Pipeline Smoke Check — Round 3

**Generated:** 2026-04-26 20:05 UTC  
**Schema fix commits:** `7caf4e4` (2026-04-26 13:05) + `7af59be` (2026-04-26 13:08)  

---

## 1. Executive Verdict

**PARTIAL ⚠️**

No post-fix pipeline run yet

> **Action required:** Allow the next scheduled pipeline run to execute, then re-check.

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
**Post-fix run?** No ❌ (pre-fix data only)

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
| `state_warnings` | 0/20 | 0/20 (0%) — old-format |
| `dilution_risk_flag` | 20/20 | 20/20 (100%) |
| `iv_rank` | 9/20 | 9/20 (45%) |
| `si_persistence_score` (via explanation_json) | 0/20 | 0/20 (0%) |
| `effective_float_score` (via explanation_json) | 20/20 | 20/20 (100%) |

---

## 3. Compile and Test State

| Check | Result |
|---|---|
| `py_compile` (supabase_persist, backtest, squeeze_screener) | ✅ OK |
| `pytest` (persistence schema + replay + screener) | ✅ ============================= 105 passed in 4.96s ============================== |

---

## 4. CHUNK-12 Gate Progress

| Gate item | Required | Current | Status |
|---|---:|---:|---|
| Calendar days of post-fix squeeze_scores | ≥ 30 | **0** | ❌ |
| New-format rows total | ≥ 500 | **20** | ❌ |
| Rows with ARMED or ACTIVE state | ≥ 50 | **2** | ❌ |
| 20-day forward return windows closed | ≥ 100 rows | **0** | ❌ |
| Rows with non-null `risk_score` | ≥ 100 | **20** | ❌ |
| Rows with non-null `options_pressure_score` | ≥ 20 | **20** | ✅ |
| `short_interest_history` distinct FINRA periods | ≥ 2 | **0** | ❌ |
| `filing_catalysts` ownership_accumulation_flag tickers | ≥ 5 | **0** | ❌ |
| `iv_history` tickers with ≥ 60 rows | ≥ 50 | **0** | ❌ |

---

## 5. Recommendation

**Continue data accumulation. No pipeline changes needed.**

The persistence infrastructure is verified. Waiting for post-fix pipeline runs to accumulate data.
