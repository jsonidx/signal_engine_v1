# Task: Options Scoring Review Workflow

Status: completed
Stage: done
Type: feature
Priority: P2
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: ai
Category: options
Risk: governance
Effort: M
Target Release: options-stack-v1
Due Date: N/A
Dependencies: TRD-027, TRD-029
Links: `dashboard/api/main.py`, `tests/test_options_screener.py`

## Implementation Notes

### Backend

`dashboard/api/main.py:7125` — `GET /api/options/scoring-review`:
- aggregates option recommendation and outcome data into a structured review
  artifact: cohort performance summary, scoring calibration signals, frequent
  failure modes, and rule-hit frequencies
- includes a `governance_note` field that states explicitly: "Claude proposes
  scoring changes from this data; human must approve before production edits"
- cached 30 minutes; DB failure returns a structured error JSON (never a 500)
- accepts `days` param (default 90, range 14–365)

### Review workflow

The endpoint is designed as a structured input for a human-supervised scoring
review cycle:

1. Generate review artifact via `GET /api/options/scoring-review`
2. Claude analyzes cohort performance, suggests rule changes
3. Human reviews proposed changes
4. Implementation happens explicitly in `utils/option_candidates.py` only after
   approval

Autonomous self-modification of production scoring is explicitly blocked by
the governance note and by the fact that the review endpoint is read-only.

### Tests

`tests/test_options_screener.py` — `TestOptionsScoringReviewEndpoint`:
- returns 200
- response has `governance_note` field
- expected sections present in output
- `review_questions` list present
- DB failure returns error JSON (not exception)
- days parameter respected

## Original Acceptance Criteria (all met)

- [x] Structured review input exists for Claude to analyze
- [x] Review output supports human-approved scoring updates
- [x] Workflow documentation: Claude proposes, human approves, changes are explicit
- [x] Review aggregation endpoint is tested
