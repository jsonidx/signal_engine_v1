# AI Agent Workflow

This repository uses separated AI responsibilities.

## Roles

Codex owns QA, testing, task orchestration, CI, repo hygiene, regression analysis, and review.

Claude Code owns implementation and refactoring. Refactoring is Claude-only unless the human explicitly says otherwise.

ChatGPT owns strategy, architecture, prompt design, and quant-research advice.

The human owns final approval for trading logic, deployment, secrets, and production runs.

## Standard Flow

1. Create or update tasks under `docs/tasks/` using:
   - `docs/tasks/new/` for `proposed` or not-started tasks
   - `docs/tasks/in-progress/` for tasks with active implementation work
   - `docs/tasks/finished/` for `done` or `completed` tasks
   - Run `python3 scripts/sync_task_status.py` after status changes to move files automatically
2. Classify risk.
3. Assign implementation to Claude Code if code changes or refactoring are needed.
4. Assign Codex to verify, review, test, and document findings.
5. If Codex finds issues that require code changes, Codex must immediately write a Claude Code fix prompt in the task file or linked handoff doc before stopping.
6. Claude Code uses that prompt to implement the fixes, then hands the branch back to Codex for another QA pass.
7. Run `make verify` before merge.
8. After QA succeeds and the task is complete, Codex must immediately write a paste-ready Claude Code shipping prompt that covers commit, push, and PR/update steps, then set `Status: done` or `Status: completed` and run `python3 scripts/sync_task_status.py`.
9. Run `make verify-full` for live DB or integration changes.
10. Require human approval for `trading-logic` risk.

## Refactoring Rule

Codex does not refactor. Codex may create task specs, tests, CI, documentation, and QA reports. Claude Code performs refactoring when explicitly assigned.

## QA Handoff Rule

When Codex finishes a QA pass with blocking or follow-up findings that require implementation work, Codex must not stop at the review summary alone. Codex must immediately produce a Claude Code prompt that is ready to paste into Claude.

The prompt should include:

- the task or bug title
- the exact findings to fix, with file paths and failing behavior
- explicit scope boundaries and non-goals
- required tests or verification commands
- any risk constraints such as `trading-logic` or API behavior freeze

Preferred locations:

1. `## Handoff Notes` in the active task under `docs/tasks/`
2. a linked file under `.ai/prompts/` when the handoff is large or reused

The intent is immediate execution: QA finding first, Claude-ready fix prompt second, in the same Codex cycle.

## Release Handoff Rule

When Codex finishes a QA pass with no blocking findings and the work is ready to ship, Codex must not stop at "QA passed" alone. Codex must immediately produce a Claude Code prompt that is ready to paste into Claude for the shipment mechanics.

The shipping prompt should include:

- the task or release title
- a short QA summary confirming the work is approved to ship
- the exact verification commands that passed
- the files or scope intended for shipment
- the recommended commit message
- the branch and remote if known
- explicit instructions to commit, push, and update or open the PR without making extra code changes

Preferred locations:

1. `## Handoff Notes` in the active task under `docs/tasks/`
2. `.ai/prompts/claude-ship-from-qa.md` as the base template for the prompt

The intent is immediate execution: QA pass first, Claude-ready ship prompt second, in the same Codex cycle.

## Trading Logic Rule

Any change touching signal scoring, rankings, conflict resolution, risk logic, position sizing, backtesting, or AI thesis behavior requires:

- a stated hypothesis
- targeted tests
- benchmark or regression evidence
- human approval

## Human Approval Gate (TRD-015)

Claude may automatically:
- collect data and label outcomes
- run calibration and generate reports
- propose threshold or scoring changes
- create approval requests in Supabase
- notify via Telegram

Claude must NOT automatically:
- apply live trading-logic changes without explicit human approval
- modify signal scoring thresholds, squeeze state machine thresholds, or
  taxonomy labeling rules without an approved approval_request

### Approval workflow

1. Automation (calibration script, replay, etc.) calls `save_approval_request()`.
2. `notify_approval_request()` sends a Telegram message with the proposal ID.
3. Human reviews via `/pending` (list), then either:
   - `/approve <request_id>` — records APPROVED in Supabase
   - `/reject <request_id>` — records REJECTED in Supabase
4. APPROVED status is required before a developer applies the change in code.
5. All status transitions are recorded in the `approval_requests` table
   (auditable trail: created_at, approved_by, approved_at).

### Approval request categories

| Category | Description |
|---|---|
| `SQUEEZE_CALIBRATION` | Threshold or labeling change proposed from calibration data |
| `SQUEEZE_STATE_MACHINE` | Change to EARLY_ARMED/ARMED/ACTIVE thresholds |
| `TAXONOMY_RULE` | Change to EARLY_ENOUGH/LATE_CHASE/FALSE_POSITIVE label rules |
| `MODEL_PROMOTION` | Promoting a calibrated model into live scoring |

### Risk levels

| Level | Meaning |
|---|---|
| `LOW` | Informational or documentation change |
| `MEDIUM` | Threshold or scoring weight change |
| `HIGH` | Live trading-logic, model replacement, or position-sizing change |

## Telegram Notifier Environment Variables

The following secrets must be set in the GitHub repository (Settings → Secrets) for
Telegram notifications and the approval workflow to function in CI:

| Secret | Used by | Required for |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `notify_pipeline_result.py`, `telegram_bot.py` | All Telegram notifications |
| `TELEGRAM_CHAT_ID` | `notify_pipeline_result.py`, `telegram_bot.py` | All Telegram notifications |
| `DATABASE_URL` | `notify_pipeline_result.py`, `telegram_bot.py` | Squeeze alerts, approval requests |
| `GITHUB_TOKEN` | `telegram_bot.py` | `/run`, `/analyze` pipeline triggers (auto-provided in Actions) |

Workflows that call `notify_pipeline_result.py` must pass all three secrets:
```yaml
env:
  DATABASE_URL:       ${{ secrets.DATABASE_URL }}
  TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
  TELEGRAM_CHAT_ID:   ${{ secrets.TELEGRAM_CHAT_ID }}
```

The `telegram_bot.py` process is a long-running daemon started separately (not via
GitHub Actions). It requires all four variables in `.env` at the project root.

## Squeeze Alert Terminology

Squeeze alert state names are canonical and must be consistent across all producers,
notifiers, and documentation:

| Alert type constant | State semantics | Message context line |
|---|---|---|
| `EARLY_ARMED_ALERT` | Early setup / entry-hunting (lower hit rate) | "WATCH / entry-hunting — early setup forming" |
| `SQUEEZE_ARMED` | Structural setup confirmed / pre-breakout entry watch | "ARMED — structural setup confirmed, pre-breakout entry watch" |
| `ACTIVE_SQUEEZE` | Move in progress — **not** a fresh-entry signal | "CHASE RISK — move in progress, not a fresh-entry signal" |

`ACTIVE_SQUEEZE` must never be presented as the preferred fresh-entry alert. It is a
continuation / chase-risk management alert.

## Public Repo Rule

This repository may be public for GitHub Actions usage. Never commit:

- `.env`
- `dashboard/frontend/.env.local`
- API keys
- private portfolio exports
- generated logs containing sensitive runtime data
- local caches or virtual environments
