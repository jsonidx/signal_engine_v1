# Task: Option Recommendation Snapshot Persistence

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: options
Risk: schema
Effort: L
Target Release: options-stack-v1
Due Date: N/A
Dependencies: TRD-022
Links: `utils/supabase_persist.py`, `migrations/004_option_candidate_snapshots_and_outcomes.sql`, `dashboard/api/main.py`

## Implementation Notes

### Schema

`migrations/004_option_candidate_snapshots_and_outcomes.sql` — creates:
- `option_candidate_snapshots`: stores per-ticker recommendation events with
  full thesis context, contract fields, score, rank, source, rationale, exit
  plan fields, and suppression state.
- `option_candidate_outcomes`: stores resolution records keyed to snapshot rows
  (outcome tracking implemented in TRD-027).

### Persistence helper

`utils/supabase_persist.py` — `save_option_candidate_snapshot()` (line 2220):
- accepts a `CandidateResult` plus optional `thesis_id` and `thesis_context`
- writes all candidate rows and suppressed events with full contract fields
- gracefully skips if the table does not exist (migration gate check at line 2261)
- returns list of inserted row IDs

### Integration point

`dashboard/api/main.py` — the screener endpoint and the per-ticker
`/api/ticker/{sym}/option-candidates` endpoint both call
`save_option_candidate_snapshot` fire-and-forget after returning results.
Persistence never blocks the API response.

### Tests

`tests/test_option_persistence.py` — covers:
- snapshot row serialization
- multiple ranked candidates for one ticker event
- suppressed result persists without requiring candidate rows
- holding-window and exit-plan fields round-trip correctly
- thesis context fields (thesis_date, signal_agreement, entry_low/high) are
  included in persisted rows

## Original Acceptance Criteria (all met)

- [x] Generated candidates written to Supabase
- [x] Rows include ticker, thesis context, contract fields, score, rank, source, rationale, exit-plan fields
- [x] Suppressed/no-trade states persisted with `suppressed` and `suppression_reason`
- [x] Schema extensible for outcome joins
