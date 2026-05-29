# Task: Event-Driven Deep Dive Candidate Queue

Status: done
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: automation
Risk: trading-logic
Effort: M
Target Release: completed
Due Date: completed
Dependencies: none
Blocked By: none
Links: none
Success Metric: fresh catalyst candidates can be queued into Deep Dive without first appearing in the traditional watchlist path.

## Problem Statement

Fresh event-driven setups could be missed because the Deep Dive pipeline depended too heavily on pre-existing watchlist and ranking entry points.

## User Impact

Users received late or incomplete coverage on new catalyst names that should have been routed into deeper analysis earlier.

## Objective

Add a bounded event-driven candidate queue so fresh catalyst breakouts can be routed into Deep Dive thesis generation even when the ticker is not already present in `watchlist.txt`, `resolved_signals.json`, or `daily_rankings`.

## Proposed Solution

Introduce a capped, auditable event queue and wire it into candidate selection for downstream AI thesis generation.

## Scope

- `universe_builder.py`
- `ai_quant.py`
- `run_master.sh`
- `utils/ticker_selector.py`
- `utils/candidate_archive.py`
- `schema.sql`
- `tests/test_ticker_selector.py`
- `tests/test_pipeline_defects.py`

## Non-Goals

- Do not remove the existing watchlist-first pipeline.
- Do not run AI synthesis on the full Russell universe.
- Do not bypass existing max-cost controls without a separate explicit cap.

## Constraints

- The queue must be deterministic and inspectable: persist or log `ticker`, `reason`, `score`, `source_fields`, and `queued_at`.
- The queue must have a strict daily cap, suggested default: 10 fresh event candidates.
- A queued ticker should be eligible for `ai_quant.py --tickers` or equivalent Deep Dive generation even when absent from resolved signals.
- Avoid duplicate AI calls for the same ticker on the same day.

## Acceptance Criteria

- Observable behavior: a ticker can be queued for Deep Dive from event/catalyst evidence without first appearing in `watchlist.txt`.
- Observable behavior: Step 13 or a new bounded step can include queued names in AI thesis generation with a visible reason such as `fresh_catalyst_breakout`.
- Observable behavior: queued candidates are archived in `candidate_snapshots` or a clearly named queue table/file for later audit.
- Tests: cover queue insertion, daily cap behavior, de-duplication, and ticker handoff into AI selection.
- Documentation: add handoff notes explaining how this differs from normal ranked candidate selection.

## Verification Plan

- `pytest tests/test_ticker_selector.py tests/test_pipeline_defects.py -v`
- Dry-run a queue containing `CRSR` and confirm it would be passed to AI synthesis without requiring `resolved_signals.json`.
- `make verify`

## Implementation Notes

### What changed
- New `utils/event_queue.py` — bounded JSON-backed queue with `enqueue()`, `get_queue_for_date()`,
  `get_all_pending()`, `clear_stale_entries()`.  Persists to `data/event_queue.json`.
- `config.py`: `EVENT_QUEUE_DAILY_CAP=10`, `EVENT_QUEUE_MAX_AI_SLOTS=3`.
- `utils/ticker_selector.py`: `select_top_tickers()` gains `event_queue` and
  `event_queue_max_slots` parameters.  Event queue tickers are **additive** (not displacing)
  to the normal top-N selection, bounded by `EVENT_QUEUE_MAX_AI_SLOTS`.
  De-duplication: tickers already selected from resolved_signals are never added twice.
- `universe_builder.py` (`fast_momentum_prescreen`): after OHLCV force-include block,
  auto-enqueues tickers with EARLY_MOMENTUM_BREAKOUT or CATALYST_PRICE_EXPANSION tags.

### Difference from normal candidate selection
Normal selection: ticker must be in `resolved_signals.json` with signal_agreement_score ≥ 0.60.
Event queue path: ticker enters via detected early-momentum or catalyst tag; no resolved_signals
entry required.  Metadata falls back gracefully (direction=NEUTRAL, agreement=0) with
selection_reason explicitly stating `fresh_catalyst_breakout | <tag>`.

### Tests run
```
pytest tests/test_ticker_selector.py::TestEventQueue tests/test_ticker_selector.py::TestEventQueueTickerSelectorHandoff -v
# 11 passed
pytest tests/test_pipeline_defects.py::TestEventQueueIntegration -v
# 1 passed
```

### QA round 2 changes (2026-05-29) — pipeline wiring
- **ai_quant.py**: `select_top_tickers()` in top-N mode now calls `get_all_pending(max_age_days=2)`
  and `clear_stale_entries(keep_days=3)` before selection; passes `event_queue` arg.
- **run_master.sh Steps 13a, 13c, 18**: all three `select_top_tickers()` calls now import and
  pass `get_all_pending(max_age_days=2)` as `event_queue`.
- **utils/event_queue.py date fix**: `get_queue_for_date()` now defaults to UTC (matching
  `enqueue()` stamps).  `get_all_pending()` and `clear_stale_entries()` use UTC anchoring.
  Production callers use `get_all_pending(max_age_days=2)` which tolerates Berlin UTC+2 skew.
- **New tests**: `TestEventQueueDateBoundary` (4 tests) — yesterday UTC entry in 2-day window,
  dropped from 1-day window, UTC default in `get_queue_for_date()`.

### Residual risk
- The event queue currently auto-populates only from `fast_momentum_prescreen`. Manual enqueue
  (e.g. from catalyst_screener.py or a news watcher) would need an explicit `enqueue()` call.
- `data/event_queue.json` is not in schema.sql; it is a local pipeline artefact. If the data/
  directory is not present (fresh checkout), the first enqueue creates it.
- Queue entries do not persist across runs unless `clear_stale_entries(keep_days=...)` is called.

### Original note
`CRSR` was present in the Russell 2000 constituent cache but absent from `watchlist.txt`, `resolved_signals.json`, and local equity signal CSVs. The current pipeline only lets Deep Dive see names that survived the upstream watchlist/screener path. This task creates a separate bounded path for urgent catalyst names.
