# TRD-037: ERM Vendor Audit — Point-In-Time Estimates Gate

*Generated: 2026-05-31*

## Decision: BLOCKED

ERM (EPS Estimate Revision Momentum) cannot be implemented in this repo at this time.
No vendor currently accessible to this project provides true point-in-time individual
analyst revision history that satisfies the minimum data contract defined below.

---

## 1. Minimum Data Contract for Valid ERM

For ERM to be backtestable without look-ahead bias, each revision record must include:

| Field | Requirement |
|---|---|
| `analyst_id` | Unique, persistent identifier per analyst (not firm-level aggregate) |
| `estimate_type` | EPS, revenue, or other (must be labeled) |
| `estimate_value` | The point estimate at time of revision |
| `publication_timestamp` | Exact date (and ideally time) the revision was published |
| `fiscal_period` | The period being estimated (FY1, FY2, Q1, etc.) |
| `prior_estimate` | Previous estimate from same analyst to compute the revision delta |
| `history_depth` | ≥ 3 years of rolling history; broader coverage preferred |

**What does NOT satisfy the contract:**
- Current-consensus-only feeds (no historical point-in-time view)
- Reconstructed estimates that aggregate or interpolate individual analyst history
- "As-of-date" consensus values derived from current-latest analyst records
- Vendor data that was backfilled from a later snapshot date

---

## 2. Candidate Vendors Evaluated

### 2a. yfinance (free, no subscription)

**What it provides:**
- `earningsEstimate` — current analyst consensus for the current and next quarter
- `revenueEstimate` — same
- `earningsHistory` — **actual** vs **estimated** EPS for the last four quarters

**Point-in-time status:** ❌ FAILS

yfinance returns only the **current** analyst consensus view. There is no API
for retrieving what the consensus was on a prior date, and no individual analyst
identifiers are exposed. The `earningsHistory` field shows what the consensus was
at time-of-earnings — not at each revision event.

**Look-ahead risk:** HIGH. Using yfinance `earningsEstimate` in a backtest would
embed today's consensus revision in all historical periods — a textbook look-ahead
bias. The resulting ERM signal would be completely invalid for research.

---

### 2b. Alpha Vantage (free tier / $50/month paid)

**What it provides:**
- `EARNINGS` endpoint: reported EPS vs. estimated EPS per quarter
- `EARNINGS_CALENDAR` endpoint: upcoming earnings dates

**Point-in-time status:** ❌ FAILS

Alpha Vantage's estimate history reflects analyst consensus at reporting time, not
at each intra-quarter revision date. Individual analyst IDs are not available.
No "as-of-date" query parameter exists for the earnings estimates endpoint.

---

### 2c. WRDS / I/B/E/S (institutional, subscription required)

**What it provides:**
- Full individual analyst revision history with `ANNDATS` (announcement date)
- Analyst ID (`ANALYS`), broker, estimate value, fiscal period
- True point-in-time revision timestamps going back decades

**Point-in-time status:** ✅ SATISFIES the contract

**Availability for this project:** ❌ NOT AVAILABLE

WRDS access requires an institutional license (~$10,000+/year for commercial use,
or university affiliation). This project does not have WRDS access. Implementing
ERM on WRDS data would require a procurement decision outside the scope of
automated implementation.

---

### 2d. FactSet / Bloomberg / Refinitiv

**What they provide:**
- True PIT analyst revision history with full I/B/E/S-equivalent fields
- Individual analyst IDs, revision timestamps, prior estimates

**Point-in-time status:** ✅ SATISFIES the contract

**Availability for this project:** ❌ NOT AVAILABLE

All three require enterprise subscriptions ($5,000–$50,000+/year). Not accessible
without a procurement decision.

---

### 2e. Estimize (now part of FactSet)

Previously offered crowd-sourced estimates with historical point-in-time access
via API. The standalone Estimize API has been deprecated and integrated into FactSet.
No longer accessible independently.

---

## 3. Alternative Proxy Approaches Considered and Rejected

**Proxy: price-implied revision (stock move around earnings)**
- Using post-earnings price moves as a proxy for revision surprise is valid for
  event studies but does not capture pre-announcement revision momentum.
- Does not satisfy the revision-timing requirement for ERM.

**Proxy: inferred consensus change from quarterly scraping**
- Even if consensus values were scraped on multiple dates, this creates a
  reconstructed estimate series, not true PIT data.
- Backtest validity is compromised whenever scrape cadence misses actual revision dates.
- Rejected: fails the "no reconstructed data" constraint.

**Proxy: SEC 8-K earnings surprise series**
- SEC filings report actual vs. prior guidance but not analyst revision series.
- Rejected: does not provide revision momentum.

---

## 4. Go / No-Go Decision

| Dimension | Status |
|---|---|
| Valid PIT data available without procurement | ❌ No |
| Cost of qualifying vendor | $5,000–$50,000+/year |
| Bias risk if implemented on available data | HIGH (look-ahead) |
| Implementation recommendation | **BLOCKED** |

**ERM remains blocked** until a procurement decision is made to acquire a qualifying
vendor dataset (WRDS, FactSet, Bloomberg, or Refinitiv). Do not implement ERM on
yfinance or Alpha Vantage data — the resulting signal would be invalid.

---

## 5. What to Do Instead

- Accumulate short-interest and options-state history (TRD-039) — these are
  collectable now and do not suffer from PIT-history deficits.
- Re-evaluate ERM at the next program review once dataset options are known.
- A future ERM proxy based on post-earnings price surprise (strictly backward-looking)
  could be designed without look-ahead bias but would not capture the pre-announcement
  revision signal that makes ERM valuable. This is a separate research question.

---

*Report generated for TRD-037. Permanent record; do not implement ERM until this gate is explicitly re-evaluated with real sample payloads from a qualifying vendor.*
