# TRD-033: Baseline Study — Current Confirmation Pipeline

*Generated: 2026-05-31*

This report is the required benchmark for the pre-breakout detection program.
All subsequent tickets (TRD-034 through TRD-040) must reference these metrics
when claiming improvement.

## 1. Data Summary

| Metric | Value |
|---|---|
| Analysis date | 2026-05-31 |
| Pipeline start | 2026-04-08 |
| Pipeline end | 2026-05-30 |
| Calendar span | 52 calendar days (~48 trading days) |
| Total alert rows | 1,310 |
| Distinct tickers ever ranked | 237 |
| Avg tickers per run day | 27.3 |
| BULL alerts | 16 (1.2%) |
| BEAR alerts | 102 (7.8%) |
| NEUTRAL alerts | 1192 (91.0%) |
| Directional alerts (BULL+BEAR) | 118 (9.0%) |
| Avg directional alerts per run day | 2.46 (median 2.00) |

**⚠ Data limitation**: The system has been live for ~8 weeks, far short of the 18 months
requested in TRD-033. All metrics in this report are based on this constrained sample.
Results should be treated as directional indicators only, not statistically robust benchmarks.

**Horizon maturability**: Trading-day forward windows by available data:
- **5d**: 919 alert-rows with mature forward data
- **10d**: 750 alert-rows with mature forward data
- **20d**: 463 alert-rows with mature forward data
- **40d**: 0 alert-rows with mature forward data

## 2. Forward Returns by Direction

Median forward return (%) for each horizon and direction subset. Only mature observations.

### BULL alerts (n=16)

| Horizon | N mature | Median raw ret % | Median sector-adj % | % positive |
|---|---|---|---|---|
| 5d | 7 | +2.91% | +3.79% | 100% |
| 10d | 7 | +1.71% | +3.21% | 71% |
| 20d | 6 | -2.66% | -2.46% | 17% |
| 40d | 0 | — | — | — |

### BEAR alerts (n=102)

| Horizon | N mature | Median raw ret % | Median sector-adj % | % positive |
|---|---|---|---|---|
| 5d | 69 | +1.30% | +0.80% | 38% |
| 10d | 55 | -1.67% | -0.05% | 53% |
| 20d | 28 | +4.82% | +1.80% | 18% |
| 40d | 0 | — | — | — |

### NEUTRAL alerts (n=1192)

| Horizon | N mature | Median raw ret % | Median sector-adj % | % positive |
|---|---|---|---|---|
| 5d | 804 | +0.28% | -0.19% | 54% |
| 10d | 652 | +0.34% | -0.83% | 53% |
| 20d | 386 | +1.35% | -0.96% | 58% |
| 40d | 0 | — | — | — |

### ALL alerts (n=1310)

| Horizon | N mature | Median raw ret % | Median sector-adj % | % positive |
|---|---|---|---|---|
| 5d | 880 | +0.36% | -0.07% | 55% |
| 10d | 714 | +0.34% | -0.80% | 53% |
| 20d | 420 | +1.44% | -0.77% | 59% |
| 40d | 0 | — | — | — |

## 3. Precision by Rank Bucket

Precision = fraction of directional alerts (BULL/BEAR) where the direction-adjusted
return exceeds +5% within the given horizon. NEUTRAL alerts excluded.

### 5d horizon (N=76 mature directional alerts)

| Rank bucket | N alerts | Precision | FP rate |
|---|---|---|---|
| Ranks 1–5 | 69 | 14% | 86% |
| Ranks 6–10 | 2 | 0% | 100% |
| Ranks 11–20 | 3 | 0% | 100% |
| **All ranks** | **76** | **13%** | **87%** |

### 10d horizon (N=62 mature directional alerts)

| Rank bucket | N alerts | Precision | FP rate |
|---|---|---|---|
| Ranks 1–5 | 55 | 27% | 73% |
| Ranks 6–10 | 2 | 0% | 100% |
| Ranks 11–20 | 3 | 33% | 67% |
| **All ranks** | **62** | **26%** | **74%** |

### 20d horizon (N=34 mature directional alerts)

| Rank bucket | N alerts | Precision | FP rate |
|---|---|---|---|
| Ranks 1–5 | 28 | 11% | 89% |
| Ranks 6–10 | 2 | 0% | 100% |
| Ranks 11–20 | 2 | 50% | 50% |
| **All ranks** | **34** | **12%** | **88%** |

### 40d horizon — no mature observations

## 4. Alert Volume

| Metric | Value |
|---|---|
| Mean tickers/day (all) | 27.3 |
| Median tickers/day (all) | 28.0 |
| Max tickers/day | 40 |
| Min tickers/day | 16 |
| Mean directional alerts/day | 2.46 |
| Days with zero directional alerts | 4 of 48 |

## 5. Lead-Time Proxy

A confirmation pipeline that surfaces names too late will show most of its move
already captured in the first 5 days. We proxy this with:  
**5d-capture ratio** = ret_5d / ret_10d for alerts where ret_10d > 10%.

A ratio near 1.0 means the move was nearly complete within 5 days of the alert;
a ratio near 0.5 means the alert still had 5 days of comparable upside remaining.

| Metric | Value |
|---|---|
| Large-mover alerts (>10% in 10d) | 72 |
| Median 5d-capture ratio | 0.63 |
| Mean 5d-capture ratio | 0.63 |
| Fraction where 5d-capture > 0.8 (mostly done) | 35% |
| Fraction where 5d-capture < 0.3 (still early) | 19% |

## 6. Score Distributions

Agreement score and priority score describe how confident and multi-signal each alert is.

| Metric | Mean | Median | P25 | P75 |
|---|---|---|---|---|
| Priority score | 18.92 | 10.34 | 5.24 | 18.16 |
| Agreement score | 0.14 | 0.00 | 0.00 | 0.00 |
| prob_combined | — | — | — | — |

Agreement score for directional alerts only — higher agreement should predict precision:

| Direction | N | Mean agreement | Median agreement |
|---|---|---|---|
| BULL | 16 | 1.000 | 1.000 |
| BEAR | 102 | 0.560 | 0.500 |

## 7. Key Findings and Benchmark Table

These findings form the benchmark that the pre-breakout pipeline (TRD-034 onwards)
must beat or complement.

### Critical Limitations

1. **Data span is ~8 weeks (~48 trading days), not 18 months.** All metrics below are
   preliminary estimates. They will require revisiting once ≥6 months of data exist.

2. **91.0% of all alerts are NEUTRAL direction.** The confirmation pipeline rarely
   commits to a directional view. Only 118 of 1,310 total alert-rows are BULL or BEAR.
   Precision and false-positive metrics apply only to that small directional subset.

3. **40-day forward data is immature for all alerts.** The first alert dates are
   ~2026-04-08; 40 trading days from that date falls in mid-June 2026, which is
   after today (2026-05-31).

### Benchmark Table

| Metric | Current value | Target for pre-breakout pipeline |
|---|---|---|
| Trading-day span in sample | ~48 days | ≥120 trading days to revisit |
| Directional alert rate | 9.0% | TBD (comparison only) |
| Avg tickers/day | 27.3 | TBD |
| Median 20d raw return (all, N=420) | +1.44% | Outperform by >2% sector-adj |
| False-positive rate (directional, 10d) | requires directional sample | <40% target |
| 40d horizon data | immature | revisit when ≥40 trading days past all alerts |

### Implication for the Pre-Breakout Program

- The confirmation pipeline's main structural characteristic is that it outputs a high
  fraction of NEUTRAL alerts — the system is conservative rather than directional.
- A pre-breakout pipeline that surfaces BULL setups earlier must beat a near-neutral
  baseline. That is not a high precision bar in absolute terms, but the lead-time
  advantage is the primary value proposition.
- This baseline should be re-run at ~90 and ~120 trading days of history to get
  statistically meaningful precision estimates (target: N ≥ 50 mature directional alerts).

---
*Report generated by `scripts/baseline_study_033.py` (TRD-033).*
