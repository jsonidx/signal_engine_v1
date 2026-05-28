# Implementation Task Template

Use this when assigning a scoped code change to an implementation agent.

## Task

Describe the concrete change.

## Scope

Files or modules likely affected:

- `path/to/file.py`

## Non-Goals

- List what must not change.

## Constraints

- Preserve existing public APIs unless explicitly stated.
- Do not change trading logic unless this task is labelled `risk:trading-logic`.
- Do not commit generated artifacts, local caches, or secrets.

## Required Verification

- Backend: `pytest`
- Frontend: `cd dashboard/frontend && npm test && npm run build`
- Full local gate: `make verify`

## Handoff

Report changed files, behavior changes, commands run, and residual risk.
