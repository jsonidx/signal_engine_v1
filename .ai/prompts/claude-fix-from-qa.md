# Claude Fix Prompt From QA

Use this immediately after Codex finishes a QA pass that found issues Claude Code needs to fix.

## Task

State the task, bug, or QA follow-up title.

## Why You Are Being Asked

Codex completed QA and found implementation issues that require code changes. Fix the findings below, keep the scope narrow, and hand the result back for another QA pass.

## Findings To Fix

- `path/to/file`: describe the bug, regression, or missing coverage
- `path/to/file`: describe the expected behavior

## Scope

Files or modules you may need to edit:

- `path/to/file`

## Non-Goals

- Do not change unrelated behavior.
- Do not refactor beyond what is required to fix the findings.
- Do not change trading logic unless the task explicitly allows `risk:trading-logic`.

## Constraints

- Preserve existing public APIs unless the finding explicitly requires an API change.
- Add or update tests for each fixed finding.
- Keep the diff minimal and explain any unavoidable broader edits.

## Required Verification

- Targeted tests:
- `make verify`
- `make verify-full` only if the touched behavior is live integration or DB-dependent

## Handoff Back

Report:

- changed files
- findings fixed
- tests run
- anything still unverified or blocked
