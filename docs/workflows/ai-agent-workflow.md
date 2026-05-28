# AI Agent Workflow

This repository uses separated AI responsibilities.

## Roles

Codex owns QA, testing, task orchestration, CI, repo hygiene, regression analysis, and review.

Claude Code owns implementation and refactoring. Refactoring is Claude-only unless the human explicitly says otherwise.

ChatGPT owns strategy, architecture, prompt design, and quant-research advice.

The human owns final approval for trading logic, deployment, secrets, and production runs.

## Standard Flow

1. Create or update a task in `docs/tasks/`.
2. Classify risk.
3. Assign implementation to Claude Code if code changes or refactoring are needed.
4. Assign Codex to verify, review, test, and document findings.
5. Run `make verify` before merge.
6. Run `make verify-full` for live DB or integration changes.
7. Require human approval for `trading-logic` risk.

## Refactoring Rule

Codex does not refactor. Codex may create task specs, tests, CI, documentation, and QA reports. Claude Code performs refactoring when explicitly assigned.

## Trading Logic Rule

Any change touching signal scoring, rankings, conflict resolution, risk logic, position sizing, backtesting, or AI thesis behavior requires:

- a stated hypothesis
- targeted tests
- benchmark or regression evidence
- human approval

## Public Repo Rule

This repository may be public for GitHub Actions usage. Never commit:

- `.env`
- `dashboard/frontend/.env.local`
- API keys
- private portfolio exports
- generated logs containing sensitive runtime data
- local caches or virtual environments

