# Task: Pre-Breakout Detection Program Roadmap

Status: completed
Stage: done
Type: research
Priority: P0
Severity: high
Owner: Codex
Reviewer: Human
Product Area: data-pipeline
Category: research
Risk: trading-logic
Effort: L
Target Release: pre-breakout-v1
Due Date: TBD
Dependencies: TRD-033, TRD-034, TRD-035, TRD-036, TRD-037, TRD-038, TRD-039, TRD-040
Blocked By: none
Links: none
Success Metric: the team has an approved, sequenced implementation plan for a standalone pre-breakout setup pipeline with explicit go/no-go gates.

## Problem Statement

The current system is structurally optimized to confirm visible moves, not to discover pre-breakout setups early enough to be useful as an institutional-quality research input.

## User Impact

Without a separate early-detection pipeline, the system surfaces many names only after the easy part of the move is already visible, which reduces entry quality and makes alerts less actionable.

## Objective

Finalize a buildable roadmap for a separate pre-breakout detection program, including sequencing, gating decisions, and explicit boundaries between v1 production logic and research backlog.

## Proposed Solution

Run the work as a small program rather than a single ticket:

1. Measure the current confirmation pipeline baseline first.
2. Build the pre-breakout pipeline skeleton and persistence layer.
3. Implement two v1 deterministic signals on existing data:
   - Peer-Group First-Mover Sympathy (`PFS`)
   - Price-Structure Compression (`PSC`)
4. Treat EPS Estimate Revision Momentum (`ERM`) as blocked until point-in-time vendor validation passes.
5. Persist options and short-interest history now for later research, but keep them out of v1 scoring.
6. Add Claude only as a bounded Stage 3 synthesis layer after deterministic filtering.

## Scope

- `docs/tasks/new/TRD-033-baseline-study-current-confirmation-pipeline.md`
- `docs/tasks/new/TRD-034-setup-watchlist-schema-and-pre-breakout-pipeline-skeleton.md`
- `docs/tasks/new/TRD-035-peer-first-mover-sympathy-signal.md`
- `docs/tasks/new/TRD-036-price-structure-compression-signal.md`
- `docs/tasks/new/TRD-037-point-in-time-estimates-vendor-audit-and-erm-gate.md`
- `docs/tasks/new/TRD-038-stage-3-claude-synthesis-for-setup-watchlist.md`
- `docs/tasks/new/TRD-039-options-and-short-interest-persistence-for-future-research.md`
- `docs/tasks/new/TRD-040-pre-breakout-outcome-tracking-and-learning-loop.md`

## Non-Goals

- Do not weaken the existing breakout confirmation logic to make it "earlier."
- Do not add options, dark-pool, or narrative signals to v1 scoring.
- Do not allow Claude to rank or override deterministic Stage 1/2 scores.

## Constraints

- Baseline study is Priority 0 and must complete before go/no-go decisions.
- All signal definitions must use trading days consistently.
- `ERM` is blocked until true point-in-time revision history is verified.
- The pre-breakout pipeline is a separate watchlist/discovery path, not a position-sizing engine.

## Acceptance Criteria

- Observable behavior:
  - A documented program roadmap exists with explicit sequencing and dependency order.
  - Each implementation workstream has its own task file.
  - Each task contains a paste-ready Claude prompt.
- Tests:
  - Not applicable for this roadmap-only ticket.
- Documentation:
  - The roadmap states what is build-now, blocked, and research-first.

## Verification Plan

- Review all dependent tickets for consistency of sequencing and scope.
- Run `python3 scripts/sync_task_status.py` after ticket creation if any status normalization is needed.

## QA Notes

- Test scenarios: review dependency chain end-to-end for execution order clarity.
- Edge cases: `ERM` vendor rejection, baseline sample too small, Stage 2 alert volume too high.
- Regression risks: roadmap drift if Claude implements out of order.

## Launch / Release Notes

- User-facing change summary: none; planning artifact only.
- Operational notes: use this roadmap as the control document for Claude execution order.
- Rollback notes: not applicable.

## Post-Launch Validation

- What to monitor: whether implementation tickets stay within the approved sequence and risk boundaries.
- How success will be confirmed: Claude can execute the ticket set without re-scoping the project.
- Follow-up decision date: after TRD-033 baseline results and TRD-037 vendor validation.

## Handoff Notes

PM team recommendation summary:

- Quant PM: `ERM` remains the highest-value missing feature, but only with genuine point-in-time data.
- Systematic Trading PM: keep v1 deterministic and bounded; do not let Stage 2 alert volume exceed the research team's review capacity.
- Event-Driven PM: peer sympathy is worth building now, but only with strict anti-laggard-chasing constraints and manual QA.
- Technical Analysis PM: use `PSC` as a filter/amplifier, not as a standalone alpha source.
- Derivatives PM: persist options and short-interest data immediately, but keep them out of v1 scoring until enough history exists.

Program execution order:

1. `TRD-033` baseline study
2. `TRD-039` persistence tasks
3. `TRD-034` setup-watchlist schema and pipeline skeleton
4. `TRD-035` peer sympathy signal
5. `TRD-036` price-structure compression signal
6. `TRD-040` outcome tracking and learning loop
7. `TRD-037` point-in-time estimates vendor audit
8. `TRD-038` Claude Stage 3 synthesis

Paste-ready Claude program prompt:

```text
Implement the pre-breakout detection program in the approved order defined by TRD-032 and its dependent tickets.

Execution rules:
- Do not collapse tickets together unless the dependency chain explicitly allows it.
- Treat TRD-033 as Priority 0; report its results before making any go/no-go claims.
- Treat TRD-037 as a hard gate for ERM. Do not implement ERM scoring unless vendor validation proves point-in-time integrity.
- Keep v1 Stage 1 deterministic. No LLM use before Stage 3.
- Keep options, dark-pool, short-interest, and narrative features out of v1 scoring.
- Use each dependent ticket's exact scope, tests, and constraints.

Tickets in order:
1. TRD-033 — baseline study
2. TRD-039 — persistence only
3. TRD-034 — pipeline skeleton + setup_watchlist persistence
4. TRD-035 — PFS
5. TRD-036 — PSC
6. TRD-040 — outcome tracking and learning loop
7. TRD-037 — ERM vendor gate
8. TRD-038 — Claude Stage 3 synthesis

Deliver work ticket by ticket with verification at each stage.
```
