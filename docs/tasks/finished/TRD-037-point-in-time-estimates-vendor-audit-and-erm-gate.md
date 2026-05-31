# Task: Point-In-Time Estimates Vendor Audit And ERM Gate

Status: completed
Stage: done
Type: research
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: research
Risk: trading-logic
Effort: M
Target Release: pre-breakout-v1
Due Date: TBD
Dependencies: TRD-033
Blocked By: none
Links: TRD-032
Success Metric: the team reaches a documented go/no-go decision on whether a valid point-in-time estimates source exists for `ERM`.

## Problem Statement

`ERM` is the highest-value missing signal candidate, but any implementation without true point-in-time estimate history would create look-ahead bias and invalidate the research.

## User Impact

Without a vendor-validation gate, the team risks spending time and money on a signal that cannot be backtested honestly.

## Objective

Audit candidate data providers and explicitly decide whether `ERM` can proceed, remains blocked, or needs an alternative proxy plan.

## Proposed Solution

Define the minimum data contract for valid `ERM`, test sample vendor responses against it, document the result, and create a go/no-go recommendation for implementation.

## Scope

- vendor/data-contract review document under `reports/` or `docs/`
- sample validation script/notebook if helpful
- no production signal code

## Non-Goals

- Do not implement `ERM` scoring in this ticket.
- Do not purchase a vendor automatically.
- Do not backfill approximate/reconstructed data and call it valid.

## Constraints

- The audit must distinguish point-in-time individual analyst revisions from reconstructed/current consensus.
- If the data contract fails, `ERM` remains blocked.
- Cost, coverage, and operational burden must be included in the recommendation.

## Acceptance Criteria

- Observable behavior:
  - A written audit exists covering candidate providers and the required data contract.
  - The audit states whether `ERM` is:
    - proceed
    - blocked
    - or requires proxy redesign
- Tests:
  - If any sample-validation code is written, it runs on the sample payloads used.
- Documentation:
  - The audit includes exact minimum fields and red flags.

## Verification Plan

- Review sample vendor payloads against the required contract.
- Ensure the recommendation is explicit rather than implied.

## QA Notes

- Test scenarios: valid PIT sample, current-consensus-only sample, partially reconstructed sample.
- Edge cases: analyst coverage sparse, stale publication dates, missing analyst IDs.
- Regression risks: false confidence from marketing-language documentation instead of actual sample payloads.

## Launch / Release Notes

- User-facing change summary: none; research gate only.
- Operational notes: PM/human cost decision required before any `ERM` implementation.
- Rollback notes: not applicable.

## Post-Launch Validation

- What to monitor: whether later `ERM` work obeys the audit result.
- How success will be confirmed: no one implements `ERM` on invalid data.
- Follow-up decision date: immediately after vendor review.

## Handoff Notes

PM team recommendation summary:

- Quant PM: `ERM` is still the best candidate signal, but only on valid PIT data.
- Data PM: require real sample payloads, not vendor sales claims.
- Risk PM: if PIT data fails, treat `ERM` as blocked and design an alternative later rather than sneaking in biased proxies.

Paste-ready Claude implementation prompt:

```text
Implement TRD-037, "Point-In-Time Estimates Vendor Audit And ERM Gate."

Goal:
- Determine whether ERM can be implemented honestly in this repo.

Scope:
- Define the minimum data contract for valid ERM:
  - point-in-time history
  - individual analyst identifier
  - estimate value
  - publication timestamp
  - sufficient history and coverage
- Evaluate candidate providers against that contract using real sample payloads if available.
- Write a recommendation report:
  - proceed
  - blocked
  - or alternative-proxy research required

Constraints:
- Do not implement ERM scoring.
- Do not approve any source based on vendor marketing copy alone.
- If the source is not true point-in-time history, say blocked plainly.

Verification:
- If you write sample-validation code, run it against the payloads you review.

Non-goals:
- No production code changes beyond small validation helpers if needed.
```


## Implementation Notes (2026-05-31)

### Files created
- `reports/erm_vendor_audit_TRD037.md` — full audit report

### Decision: BLOCKED

No freely accessible vendor provides true point-in-time individual analyst revision history. Vendors evaluated:

| Vendor | PIT compliant | Available |
|---|---|---|
| yfinance | ❌ current consensus only | ✓ |
| Alpha Vantage | ❌ reporting-time consensus only | ✓ |
| WRDS / I/B/E/S | ✅ full PIT revision history | ❌ ~$10k+/yr |
| FactSet / Bloomberg / Refinitiv | ✅ full PIT | ❌ ~$5k-50k+/yr |

ERM score remains NULL in all setup_watchlist rows. The schema field `erm_score` is present but always NULL.

### Verification
- Audit report at `reports/erm_vendor_audit_TRD037.md`
- No sample-validation code written (no valid PIT source available to validate against)

## QA Result: PASS (gate correctly set to BLOCKED)
