# Claude Ship Prompt From Successful QA

Use this immediately after Codex finishes a QA pass that passed and the work is ready to ship.

## Task

State the task or release title.

## Why You Are Being Asked

Codex completed QA, the acceptance criteria passed, and the branch is ready for shipping. Complete the git and release mechanics without expanding scope.

## Shipping Checklist

- Review the final diff and keep the commit scope limited to the approved task.
- Confirm the required verification already passed, and rerun only the minimum command set if needed to validate the final tree state.
- Create the commit with a clear message.
- Push the branch to the correct remote.
- Open or update the PR if that is part of the current workflow.

## Inputs From Codex

- Task file: `docs/tasks/...`
- QA result summary:
- Verification commands that passed:
- Files intended for shipment:
- Recommended commit message:
- Branch name:
- Remote name:

## Non-Goals

- Do not make new feature or refactor changes.
- Do not fix unrelated issues discovered during final git review without a separate task.
- Do not ship secrets, generated artifacts, local caches, or `.env` files.
- Do not change trading logic unless that work was already approved and verified.

## Required Output Back

Report:

- exact commit SHA
- push destination
- PR link or status
- anything blocking shipment
