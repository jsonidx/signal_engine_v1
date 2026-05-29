# Task: Telegram Event Queue Alerts

Status: done
Stage: done
Type: feature
Priority: P2
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: alerts
Category: automation
Risk: api
Effort: S
Target Release: completed
Due Date: completed
Dependencies: TRD-006
Blocked By: none
Links: none
Success Metric: Telegram pipeline summaries can show event-queue catalyst candidates with correct watch semantics.

## Problem Statement

Event-queue candidates were being generated, but the Telegram delivery layer did not surface them clearly in pipeline summaries.

## User Impact

Users could miss fresh catalyst-watch names even when the backend had already identified them.

## Objective

Extend Telegram pipeline notifications so event-queue tickers found during the workflow are surfaced explicitly as catalyst/watch candidates, even when they are not yet Hot Entry names.

## Proposed Solution

Add a compact event-queue section to Telegram summaries using existing structured reasons and pipeline outputs.

## Scope

- `scripts/notify_pipeline_result.py`
- `utils/event_queue.py`
- `utils/candidate_archive.py`
- `dashboard/api/main.py`
- `tests/test_pipeline_defects.py`
- `dashboard/api/tests/test_endpoints.py`
- Optional: `run_master.sh`

## Non-Goals

- Do not change Hot Entry alert semantics.
- Do not send a separate Telegram message per event-queue ticker.
- Do not label event-queue candidates as buy signals.

## Constraints

- The message must clearly distinguish:
  - `Hot Entry` / entry-zone alerts
  - `Event Queue` / catalyst-watch candidates
- Use structured reasons already available in `selection_reason`, event queue `reason`, or archived metadata.
- Keep the Telegram message compact enough to fit inside the current chunking logic.
- Prefer using persisted pipeline outputs (`candidate_snapshots`, `daily_rankings`, `thesis_cache`, or `event_queue`) rather than recomputing screeners inside the notifier.

## Acceptance Criteria

- Observable behavior: pipeline Telegram summary includes a short `Event Queue` or `Catalyst Watch` section when fresh event-queue candidates were present in the most recent run.
- Each line includes at minimum:
  - ticker
  - queue reason / catalyst tag(s)
  - whether it received a thesis this run
  - optional rank / priority if available
- Tickers that are only queued but not analyzed still appear in the new section.
- Tickers that are both queued and analyzed are not presented misleadingly as Hot Entry unless they separately satisfy Hot Entry logic.
- If no event-queue candidates exist, the section is omitted cleanly.
- Tests: cover
  - queued but not analyzed ticker
  - queued and analyzed ticker
  - empty queue
  - message formatting stays within current notifier behavior
- Documentation: handoff notes explain where the notifier sources the event-queue data from.

## Verification Plan

- `pytest tests/test_pipeline_defects.py dashboard/api/tests/test_endpoints.py -v`
- Run `python3 scripts/notify_pipeline_result.py --status success --workflow "Daily Signal Pipeline" --run-url "https://example.com" --duration-min 1 --skip-ai`
  against a fixture or mocked DB/event-queue state and verify the message contains the new section.
- `make verify`

## Implementation Notes

### What changed
**`scripts/notify_pipeline_result.py`** — two new functions + wiring:

- `fetch_catalyst_watch_candidates(conn) -> list[dict]`
  - **Primary source**: queries `candidate_snapshots` for rows where
    `selection_reason ILIKE '%fresh_catalyst_breakout%'` (the tag written by
    `utils/ticker_selector.py` for event-queue entries).  Left-joins
    `daily_rankings` for rank.  Checks `thesis_cache` for today's date to set
    `has_thesis`.
  - **Fallback**: if no snapshot rows exist, reads `data/event_queue.json` via
    `utils.event_queue.get_all_pending(max_age_days=2)`.  De-duplicates by ticker.
  - Returns `[{ticker, reason, rank, has_thesis}]` sorted by priority_score.

- `build_catalyst_watch_section(candidates) -> str`
  - Returns `""` when candidates is empty (section cleanly omitted).
  - Formats as HTML with `• <b>TICKER</b>  <code>REASON</code>  #rank  ✅/📋`.
  - Sorted by `rank ASC NULLS LAST`, then ticker alphabetically.
  - Includes "Not a buy signal" disclaimer.
  - Capped: even 10 candidates stay well under `TG_LIMIT` (4000 chars).

- `main()`: new `catalyst_watch_section` variable; placed between thesis section
  and squeeze alerts in the final message assembly.  Falls back to event_queue.json
  even when no DB connection is available.

### Where the data comes from
| Source | When used |
|---|---|
| `candidate_snapshots` (Supabase) | Normal pipeline run — Step 13a archives all selected candidates including event-queue ones |
| `event_queue.json` (local file) | Fallback: step 13a not yet run, or DB unavailable |
| `thesis_cache` (Supabase) | Enrichment: did the ticker get an AI thesis today? |
| `daily_rankings` (Supabase) | Enrichment: what rank, if any? |

### Tests run
```
pytest tests/test_pipeline_defects.py -v  # 72 passed (18 new in TestTelegramCatalystWatchSection)
pytest dashboard/api/tests/test_endpoints.py -q  # 39 passed, 7 pre-existing failures
python -m py_compile scripts/notify_pipeline_result.py  # OK
```

### Residual risk
- `fetch_catalyst_watch_candidates` uses `ILIKE` on `selection_reason` (varchar). If the
  format of `selection_reason` changes in `ticker_selector.py`, the query will miss rows.
  The magic string `"fresh_catalyst_breakout"` should be treated as a contract between the
  two modules.
- Fallback reads `event_queue.json` from the project root path — this path is relative to
  `_ROOT` in the notifier but `_QUEUE_PATH` in `utils/event_queue.py` is anchored to
  `__file__`. Both resolve to the same file in production but may differ in test environments
  that relocate the project root.
- Hot Entry logic remains completely separate and untouched — confirmed by test
  `test_no_hot_entry_label_in_section`.

## Original Handoff Notes

Current behavior:

- Dashboard can expose event-driven candidates through `candidate_snapshots` and selection views.
- Telegram pipeline notifications summarize rankings and theses, but do not explicitly call out event-queue discoveries.
- Hot Entry notifications are a separate mechanism and only fire when price is inside both the AI entry zone and the live buy zone.

Desired behavior:

- When the workflow finds a CRSR-like catalyst breakout and queues it for Deep Dive, Telegram should mention it as a `watch/setup` candidate in the standard pipeline summary, even before it becomes a Hot Entry.
