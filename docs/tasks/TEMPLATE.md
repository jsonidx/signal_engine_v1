# Task: <short title>

Status: proposed
Stage: discovery | ready | in progress | blocked | qa | done
Type: feature | bug | chore | research | qa | docs
Priority: P0 | P1 | P2 | P3
Severity: critical | high | medium | low
Owner: Claude Code | Codex | ChatGPT | Human
Reviewer: <name or role>
Product Area: dashboard | api | data-pipeline | alerts | trading-logic | infra
Category: growth | reliability | ux | performance | compliance | research | automation
Risk: none | frontend | api | trading-logic | infra | secrets
Effort: XS | S | M | L | XL
Target Release: <sprint / milestone / date>
Due Date: YYYY-MM-DD
Dependencies: none | <ticket ids>
Blocked By: none | <ticket ids>
Links: none | <PR / issue / doc / report>
Success Metric: <one measurable outcome>

## Problem Statement

Describe the problem, why it matters, and what is currently not working.

## User Impact

Describe who is affected and what pain, risk, or missed opportunity this creates.

## Objective

State the concrete outcome this task should deliver.

## Proposed Solution

Summarize the expected implementation or product change at a high level.

## Scope

Files or modules likely affected:

- `path/to/file`

## Non-Goals

- List what must not change.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No trading logic changes unless risk is `trading-logic`.
- No secrets or generated artifacts in git.

## Acceptance Criteria

- Observable behavior:
- Tests:
- Documentation:

## Verification Plan

- `make verify`
- Targeted tests:
- `make verify-full` only if live integration behavior changed.

## QA Notes

- Test scenarios:
- Edge cases:
- Regression risks:

## Launch / Release Notes

- User-facing change summary:
- Operational notes:
- Rollback notes:

## Post-Launch Validation

- What to monitor:
- How success will be confirmed:
- Follow-up decision date:

## Handoff Notes

Use this section for implementation notes, QA findings, or residual risk.

When creating a new ticket that is expected to be implemented by Claude Code, add a paste-ready Claude implementation prompt here immediately in the same update. Include:

- the task title and goal
- exact scope with file paths if known
- explicit non-goals and constraints
- required tests or verification commands
- any risk constraints such as `trading-logic`, API behavior freeze, or no-refactor limits
- if the prompt covers more than one ticket, a short summary of each ticket and the combined objective

If Codex QA finds issues that Claude Code must fix, add a paste-ready Claude prompt here immediately in the same update. Include:

- findings with file paths
- exact scope and non-goals
- required tests
- any risk constraints
- if the prompt covers more than one ticket, a short summary of each ticket and how the fixes are grouped

If Codex QA passes and the work is ready to ship, add a paste-ready Claude shipping prompt here immediately in the same update. Include:

- QA approval summary
- verification commands that passed
- files or scope approved for shipment
- recommended commit message
- branch / remote / PR instructions if known
- explicit instruction to commit and push without adding new code changes
- if the prompt covers more than one ticket, a short summary of the tickets included in the shipment

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
