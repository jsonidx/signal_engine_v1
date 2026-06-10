# Task: Market-data hot-path cleanup and shared-service adoption

Status: completed
Stage: done
Type: feature
Priority: P2
Severity: medium
Owner: Claude Code
Reviewer: Codex
Product Area: api
Category: performance | reliability
Risk: api
Effort: M
Target Release: next
Due Date: 2026-06-25
Dependencies: TRD-071
Blocked By: none
Links: `dashboard/api/main.py`, `utils/market_data.py`, `ai_quant.py`
Success Metric: remaining ticker-page and analysis hot paths stop bypassing the centralized market-data layer; duplicate direct yfinance usage in hot user-facing flows is materially reduced, and latency spikes under concurrent load become less likely.

## Problem Statement

`TRD-071` established a shared market-data service and wired the main ticker-price paths through it, but the repo still contains direct yfinance usage in adjacent hot paths. One example is the `adv_20d` lookup inside `signals_ticker()` in [main.py](/Users/jason/signal_engine_v1/dashboard/api/main.py:1601), which still calls `yf.Ticker(...).history(...)` directly.

This creates inconsistent caching, timeout, and concurrency behavior across adjacent user-facing flows. The result is less predictable performance and partial duplication of upstream calls. This ticket is more about reliability and load-behavior consistency than dramatic happy-path speed gains.

## User Impact

Users may still see avoidable latency spikes or inconsistent behavior on otherwise optimized pages because not all market-data access on hot paths goes through the same bounded, cached service.

## Objective

Finish adoption of the centralized market-data service for the remaining hot user-facing paths that still bypass it, without trying to migrate the entire repo in one sweep.

## Proposed Solution

Audit the remaining direct yfinance usage in:

- ticker-page adjacent fields
- analysis-job hot paths
- any user-facing endpoints that still perform uncached direct market-data calls

Then migrate the clearly hot and safe candidates to `utils/market_data.py` or an equivalent shared interface.

Initial candidates:

- `signals_ticker()` `adv_20d` lookup
- other ticker-page adjacent fetches that still do direct live provider access
- analysis-job read paths that frequently overlap with interactive traffic, if they can safely consume the same cached/shared service
- audit the earnings-related ticker endpoints and helpers that still use direct `yf.Ticker(...)` calls, even if some are ultimately deferred due to richer API semantics

This should remain an adoption/cleanup pass, not a full repo-wide migration crusade.

## Scope

Files or modules likely affected:

- `dashboard/api/main.py`
- `utils/market_data.py`
- `ai_quant.py`
- targeted tests in `dashboard/api/tests/` and `tests/`

## Non-Goals

- Do not migrate every historical yfinance caller in the codebase.
- Do not redesign `market_data.py` architecture unless needed for a small hot-path extension.
- Do not change trading logic or thesis logic.

## Constraints

- Focus on hot user-facing or analysis-adjacent paths first.
- Preserve existing endpoint schemas.
- Keep provider semantics compatible unless the ticket explicitly documents a behavioral change.
- No secrets or generated artifacts in git.

## Acceptance Criteria

- Observable behavior: identified hot-path direct yfinance calls are routed through the shared market-data service.
- Observable behavior: migrated paths inherit bounded timeout, concurrency limiting, and cache reuse.
- Tests: targeted coverage verifies migrated paths use the shared layer.
- Documentation: ticket records which hot paths were migrated and which were intentionally deferred, including deferred earnings-related paths if they remain outside the shared service.

## Verification Plan

- Targeted backend tests for the migrated hot paths.
- Manual: verify ticker-page and analysis-adjacent paths still return expected data after the shared-service migration.
- Optional observability check: inspect `get_service_stats()` or logs to confirm shared-layer reuse increases.

## QA Notes

- Test scenarios: normal fetch, cached fetch, stale fetch, provider failure, and concurrent access where relevant.
- Edge cases: empty data from provider, missing symbols, and stale-but-acceptable cache values.
- Regression risks: moving fields into a shared layer can subtly change freshness or fallback semantics; document any intentional differences.

## Launch / Release Notes

- User-facing change summary: remaining hot-path market-data lookups now reuse the shared service for more consistent page and analysis performance.
- Operational notes: centralized service stats/logs become a better proxy for market-data pressure after migration.
- Rollback notes: revert individual hot-path migrations if they introduce incompatibilities.

## Post-Launch Validation

- What to monitor: shared market-data service usage, timeout warnings, and latency variance on ticker-page endpoints.
- How success will be confirmed: fewer direct provider calls on hot paths and more stable endpoint performance under concurrent use.
- Follow-up decision date: after confirming the cleanup meaningfully reduces hot-path bypasses.

## Handoff Notes

### Claude implementation prompt

```text
Task: Implement PERF-005, "Market-data hot-path cleanup and shared-service adoption."

Goal:
- Finish migrating the remaining hot user-facing and analysis-adjacent market-data calls onto the shared `utils/market_data.py` service.

Scope:
- Focus on:
  - `dashboard/api/main.py`
  - `ai_quant.py`
  - `utils/market_data.py`
- Add only targeted tests needed to prove shared-service adoption for the selected hot paths.

Requirements:
1. Identify the remaining direct yfinance calls in hot user-facing or analysis-adjacent paths.
2. Migrate the safe, high-value candidates to the shared market-data layer.
3. Preserve current endpoint schemas and semantics as much as possible.
4. Include earnings-related direct yfinance paths in the audit scope, even if some remain deferred because they use richer/non-history APIs.
5. Document which hot paths were migrated and which were intentionally deferred.

Non-goals:
- Do not attempt a repo-wide migration of every yfinance call.
- Do not redesign trading or thesis logic.
- Do not widen the shared service beyond what is needed for the chosen hot paths.

Verification:
- run targeted backend tests for the migrated paths
- manually verify ticker-page behavior remains correct
```

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
