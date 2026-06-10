# Task: Snapshot remaining expensive read endpoints

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Codex
Product Area: api | dashboard
Category: performance | reliability
Risk: api
Effort: M
Target Release: next
Due Date: 2026-06-24
Dependencies: TRD-080
Blocked By: TRD-080
Links: `dashboard/api/main.py`, `dashboard/frontend/src/pages/OptionsPage.tsx`, `dashboard/frontend/src/pages/ScreenersPage.tsx`, `docs/tasks/new/TRD-080-options-screener-snapshot-architecture.md`
Success Metric: user-facing read endpoints that still perform expensive live fan-out on request are converted to snapshot/read-through delivery, reducing cold response latency from multi-second live compute to sub-second snapshot reads, with explicit freshness UI on the consuming page.

## Problem Statement

Some high-cost dashboard surfaces already use cached or persisted data, but others still compute too much work in request time. The clearest example is the options screener path, which still fans out through live candidate-generation logic in [main.py](/Users/jason/signal_engine_v1/dashboard/api/main.py:6891) unless the snapshot architecture from `TRD-080` is completed.

This keeps some pages structurally slower than necessary and leaves latency sensitive to upstream market-data paths and per-request compute.

This ticket is blocked until `TRD-080` is complete enough to provide the options screener snapshot foundation.

## User Impact

Users encounter slower loads and more variable performance on read-heavy pages whose data changes far less frequently than the request cadence. The dashboard does avoid some stalls already, but the remaining live-compute pages still feel less responsive than they should.

## Objective

Move the remaining expensive read endpoints to snapshot or precomputed delivery models where the page primarily reads stored results instead of doing heavy work on load.

## Proposed Solution

Finish the architectural move from request-time fan-out to snapshot-backed reads for the remaining expensive endpoints.

Initial target, and current blocker:

- complete `TRD-080` for `/api/options/screener`

Then evaluate other remaining read-heavy endpoints and move any structurally similar fan-out paths to:

- scheduled/pipeline snapshot generation
- background refresh endpoints
- lightweight read APIs with timestamps and stale-state indicators

This ticket is intentionally about page-read performance, not real-time trading execution.
It is primarily an API/read-model performance ticket with required dashboard freshness affordances.

## Scope

Files or modules likely affected:

- `dashboard/api/main.py`
- `dashboard/frontend/src/pages/OptionsPage.tsx`
- `dashboard/frontend/src/pages/ScreenersPage.tsx`
- snapshot persistence code and migrations already implied by `TRD-080`

## Non-Goals

- Do not redesign the options or screeners UI beyond what snapshot delivery requires.
- Do not introduce real-time streaming architecture.
- Do not change trading logic or ranking logic in this ticket.
- Do not widen into a generic background-job platform.

## Constraints

- Favor persisted snapshot reads over live fan-out on request.
- Preserve existing response shapes where possible, with additive snapshot metadata if needed.
- No secrets or generated artifacts in git.

## Acceptance Criteria

- Observable behavior: targeted expensive read endpoints return from stored snapshot data instead of request-time heavy fan-out.
- Observable behavior: pages display snapshot freshness clearly, for example with a visible "last updated" / freshness badge and refresh affordance where applicable.
- Observable behavior: manual or scheduled refresh paths do not block the page load.
- Tests: targeted backend/frontend tests verify snapshot-backed read behavior.
- Documentation: the ticket and linked tickets capture the read-model shift and verification steps.

## Verification Plan

- Complete `TRD-080` targeted verification for options screener snapshotting.
- Run targeted API tests for any additional read endpoints moved to snapshots.
- Manual: verify targeted pages load quickly from stored results and expose freshness metadata.

## QA Notes

- Test scenarios: snapshot exists, snapshot missing, refresh queued, partial snapshot, and stale snapshot.
- Edge cases: empty snapshot tables, failed refresh jobs, and outdated timestamps.
- Regression risks: switching to snapshots trades freshness for speed; freshness markers must remain explicit.

## Launch / Release Notes

- User-facing change summary: expensive screener-style pages load from precomputed snapshots rather than doing heavy live work on every request.
- Operational notes: refresh actions become background tasks; stale/freshness timestamps become more important than raw live-query latency.
- Rollback notes: revert individual endpoints to their live-fetch path if snapshot generation fails, though this restores slower loads.

## Post-Launch Validation

- What to monitor: response times for targeted endpoints, snapshot freshness age, and refresh success rate.
- How success will be confirmed: read-heavy pages stop showing multi-second live-compute latency on cold loads.
- Follow-up decision date: after validating snapshot freshness and user-visible latency improvements.

## Handoff Notes

### Claude implementation prompt

```text
Task: Implement PERF-004, "Snapshot remaining expensive read endpoints."

Goal:
- Eliminate remaining request-time heavy fan-out on user-facing read pages by serving precomputed snapshots instead.

Scope:
- Start with the existing `TRD-080` options screener snapshot architecture.
- Extend only to additional endpoints if they clearly fit the same pattern and can be handled without widening architecture too far.

Requirements:
1. Complete snapshot-backed delivery for remaining expensive read endpoints, beginning with `/api/options/screener`.
2. Ensure page load reads stored results rather than computing the expensive payload on request.
3. Surface freshness metadata clearly in the consuming UI, not just in the API payload. For the options screener path, include a visible freshness badge/label plus refresh affordance.
4. Keep refresh behavior asynchronous/background where possible.

Non-goals:
- Do not redesign screeners UX beyond freshness/snapshot affordances.
- Do not add real-time infra.
- Do not change trading logic.

Verification:
- run targeted backend/frontend tests for snapshot-backed endpoints
- manually confirm targeted pages read quickly from stored results
```

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
