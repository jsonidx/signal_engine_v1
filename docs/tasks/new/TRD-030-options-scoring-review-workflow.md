# Task: Options Scoring Review Workflow

Status: proposed
Stage: ready
Type: feature
Priority: P2
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: ai
Category: options
Risk: governance
Effort: M
Target Release: backlog
Due Date: TBD
Dependencies: TRD-027, TRD-029
Blocked By: none
Links: `docs/tasks/new/TRD-025-options-screener-learning-roadmap.md`, `utils/option_candidates.py`, `dashboard/api/main.py`
Success Metric: the system can produce evidence-based scoring review inputs so Claude can propose option-engine changes without autonomously altering production logic.

## Problem Statement

Historical recommendation and outcome data are only useful if the team has a repeatable way to review them and convert them into better scoring logic.

## User Impact

Without a structured review workflow:

- tuning remains manual and inconsistent
- Claude has no stable artifact to analyze
- scoring changes risk becoming ad hoc or ungoverned

## Objective

Create a workflow and data surface that lets Claude review option recommendation performance and propose scoring changes safely.

## Proposed Solution

Add a structured review dataset or endpoint that summarizes:

- cohort performance
- frequent failure modes
- score vs realized return relationships
- rule-hit frequencies

Claude should consume that evidence and produce a proposal, but not automatically write scoring changes into production without approval.

## Scope

- review dataset / endpoint
- documentation or workflow notes for how Claude should use it
- optional admin/report surface if minimal and useful

## Non-Goals

- Do not allow automatic self-modifying production scoring.
- Do not bypass human approval for trading-logic changes.

## Constraints

- Keep the workflow explicit and auditable.
- Prefer structured data outputs over prompt-only free text.
- Tie recommendations back to persisted snapshots and outcomes.

## Acceptance Criteria

- Observable behavior: a structured review input exists for Claude to analyze.
- Observable behavior: the review output can support human-approved scoring updates.
- Documentation: workflow clearly states that Claude proposes, human approves, and code changes remain explicit.
- Tests:
  - review aggregation endpoint or artifact is generated as expected

## Verification Plan

- focused tests for review aggregation output
- manual inspection of one generated review artifact

## QA Notes

- Test scenarios:
  - mixed success/failure dataset
  - sparse dataset
  - one-preset-only dataset
- Regression risks:
  - opaque review outputs
  - accidental drift toward autonomous scoring changes

## Launch / Release Notes

- User-facing change summary: none directly; internal scoring-governance workflow.
- Operational notes: depends on accumulated snapshot and outcome history.
- Rollback notes: disable the review endpoint/artifact and preserve persistence and analytics.

## Post-Launch Validation

- What to monitor: whether review artifacts are understandable and action-oriented.
- How success will be confirmed: Claude can propose defensible scoring changes from the dataset.
- Follow-up decision date: after the first full scoring review cycle.

## Handoff Notes

Paste-ready Claude implementation prompt:

Implement TRD-030, "Options Scoring Review Workflow," in this repo.

Goal:
- Create a structured evidence surface that lets Claude review historical option recommendation performance and propose scoring updates safely.

Scope:
- review dataset / endpoint
- minimal workflow documentation or code comments where needed
- focused tests for the review output

Requirements:
- Keep the output structured and tied to persisted snapshots/outcomes.
- Make it suitable for human-reviewed score adjustments.
- Do not enable autonomous production scoring changes.

Non-goals:
- No direct self-modifying algorithm behavior.
- No broad redesign of the current AI workflow.

Tests and verification:
- Add focused tests for the review artifact/endpoint.
- Run the tests you add.
- Run `make verify` if practical.
