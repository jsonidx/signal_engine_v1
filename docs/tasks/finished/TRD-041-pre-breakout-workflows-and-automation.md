# Task: Pre-Breakout Workflows And Automation

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: infra
Category: automation
Risk: trading-logic
Effort: M
Target Release: pre-breakout-v1
Due Date: TBD
Dependencies: TRD-034, TRD-038, TRD-039, TRD-040
Blocked By: none
Links: TRD-032, `pre_breakout_pipeline.py`, `scripts/collect_options_si_state.py`
Success Metric: the pre-breakout pipeline and its research data collectors run on a repeatable GitHub Actions schedule without being coupled into the confirmation-pipeline workflow.

## Problem Statement

The pre-breakout system is implemented, but it is still a standalone set of scripts. Nothing in GitHub Actions or the current scheduled pipeline runs it automatically.

## User Impact

Without automation:

- `setup_watchlist` stops being a daily research surface and becomes a manual-only tool
- `options_state_history` and related pre-breakout datasets will not accumulate reliably
- the team cannot compare the pre-breakout pipeline with the confirmation pipeline on a stable cadence

## Objective

Create dedicated automation for the pre-breakout pipeline so it can run daily in a controlled, logically separate workflow.

## Proposed Solution

Add one or more GitHub Actions workflows dedicated to the pre-breakout system:

- a daily deterministic pre-breakout workflow with no LLM requirement by default
- an optional manual Stage 3 workflow for Claude enrichment
- an automated options / short-interest collection step or companion workflow

Keep this separate from `run_master.sh` and the current daily/manual confirmation-pipeline workflows until the pre-breakout path proves operationally stable.

Scheduling recommendation:

- Prefer `workflow_run` triggered from successful completion of `Daily Signal Pipeline (no AI cost)` rather than a blind cron.
- Reason: the pre-breakout workflow depends on fresh upstream ranking/universe data and should run only after the deterministic confirmation pipeline has completed successfully.
- If a cron fallback is ever needed, schedule it roughly 45-60 minutes after the current daily pipeline start, but `workflow_run` is the preferred design.

## Scope

- new workflow file(s) under `.github/workflows/`
- any minimal script flags / logging improvements needed for automation
- Telegram notification / artifact upload if lightweight and useful
- targeted verification for the new workflow-owned commands

## Non-Goals

- Do not merge pre-breakout execution into `run_master.sh` in this ticket.
- Do not redesign the dashboard/UI surface for pre-breakout results.
- Do not change the deterministic scoring logic or benchmark methodology.

## Constraints

- Keep the pre-breakout workflow operationally separate from the confirmation pipeline.
- Default scheduled execution should avoid AI cost unless explicitly enabled.
- Stage 3 Claude synthesis must remain bounded and manual or separately controlled.
- Workflow commands must match the actual runtime contracts documented in the finished tickets.

## Acceptance Criteria

- Observable behavior:
  - a scheduled GitHub Actions workflow runs the deterministic pre-breakout pipeline on a daily cadence
  - the preferred trigger is successful completion of `Daily Signal Pipeline (no AI cost)` via `workflow_run`
  - options / short-interest collection is also automated on a repeatable cadence
  - a separate manual path exists for optional Stage 3 Claude synthesis
  - both workflow paths send a concise Telegram notification on success/failure using the repo's existing notification pattern
  - the existing `daily_pipeline.yml`, `manual_pipeline.yml`, and `run_master.sh` remain unchanged unless a minimal safe hook is absolutely necessary
- Tests:
  - targeted verification of the commands invoked by the workflow(s)
- Documentation:
  - workflow purpose, AI-cost expectations, and operational separation are clear in the ticket notes and workflow names/descriptions

## Verification Plan

- validate the new workflow YAML
- run the exact commands used by the workflow(s) locally where practical
- confirm the no-AI path uses only deterministic scripts

## QA Notes

- Test scenarios:
  - scheduled deterministic pre-breakout run
  - manual Stage 3 dispatch
  - options / SI collection with explicit ticker override
- Edge cases:
  - empty universe
  - missing `DATABASE_URL`
  - Stage 3 disabled / missing API key
- Regression risks:
  - accidental coupling to confirmation-pipeline success/failure
  - misleading workflow naming around AI cost

## Launch / Release Notes

- User-facing change summary: the pre-breakout research pipeline gains dedicated automation.
- Operational notes: keep monitoring separate from the confirmation pipeline until stability is proven.
- Rollback notes: disable the new workflow(s) without touching `run_master.sh`.

## Post-Launch Validation

- What to monitor: workflow success rate, row counts in `setup_watchlist`, row counts in `options_state_history`, and any Stage 3 usage frequency.
- How success will be confirmed: the pre-breakout datasets accumulate daily without manual intervention and without affecting the confirmation pipeline.
- Follow-up decision date: after 2-4 weeks of daily runs.

## Implementation Notes (2026-05-31)

### Files created
- `.github/workflows/daily_pre_breakout.yml` — deterministic daily workflow
- `.github/workflows/manual_pre_breakout_stage3.yml` — manual AI-enabled workflow

### Workflow design

**`daily_pre_breakout.yml`**
- Trigger: `workflow_run` from `Daily Signal Pipeline (no AI cost)` with `conclusion == 'success'`
- Steps: checkout → Python 3.12 → install deps → `python3 pre_breakout_pipeline.py` → `python3 scripts/collect_options_si_state.py` → Telegram (`--skip-ai`) → upload logs
- No Anthropic API key required
- `if: ${{ github.event.workflow_run.conclusion == 'success' }}` guards the entire job

**`manual_pre_breakout_stage3.yml`**
- Trigger: `workflow_dispatch` with cost-warning confirm input
- Steps: checkout → Python 3.12 → install deps → `python3 pre_breakout_pipeline.py --stage3` → Telegram (no `--skip-ai`) → upload logs
- Requires `ANTHROPIC_API_KEY` secret; bounded to ≤10 names/day by `stage3_synthesis.py`

### Telegram notification
Both workflows reuse `scripts/notify_pipeline_result.py` exactly as the confirmation-pipeline workflows do. The daily path passes `--skip-ai`; the Stage 3 path does not.

### Verification (2026-05-31)

#### YAML structure (universally reproducible)

Both files parse correctly. Key structural properties confirmed:

```
daily_pre_breakout.yml
  trigger: workflow_run
  job.if: ${{ github.event.workflow_run.conclusion == 'success' }}
  steps: 8

manual_pre_breakout_stage3.yml
  trigger: workflow_dispatch
  steps: 7
```

#### Local command verification (reproducible without DB credentials)

All commands use `--dry-run` or `--tickers` override to avoid DB dependency:

```
python3 pre_breakout_pipeline.py --dry-run --tickers AAPL MSFT GOOGL NVDA META
→ Universe: 5 tickers, scored: 5, [dry-run] Would write 5 rows to setup_watchlist
exit=0

python3 scripts/collect_options_si_state.py --dry-run --tickers AAPL GME
→ options=2 skipped=0 | SI=2 skipped=0, [dry-run] Would write 2 options + 2 SI rows
exit=0

python3 pre_breakout_pipeline.py --stage3 --dry-run --tickers AAPL MSFT NVDA
→ status: ok, stage3_run: False (dry-run)
exit=0

python3 scripts/notify_pipeline_result.py \
  --status success --workflow "Pre-Breakout Pipeline (Deterministic)" \
  --run-url "..." --duration-min 1.5 --skip-ai
→ Telegram notification sent (7669 chars)
exit=0
```

#### Live persistence (requires DATABASE_URL — author's environment only)

In the author's environment (DATABASE_URL set), live runs without `--dry-run` persisted
correctly: `setup_watchlist` received 43 rows, `options_state_history` and
`short_interest_history` each received 3 rows in a 3-ticker test. These runs are not
independently reproducible without credentials; the dry-run evidence above covers the
workflow command paths and exit-code correctness.

#### GitHub Actions runtime

No GHA run logs exist yet — these workflows have not been dispatched in Actions.
First live validation will occur when `Daily Signal Pipeline (no AI cost)` next
completes successfully after this commit lands on `main`.

### Separation guarantee
- `run_master.sh`, `daily_pipeline.yml`, and `manual_pipeline.yml` are unchanged
- No coupling to `daily_rankings`, `thesis_cache`, or any confirmation-pipeline table
- `workflow_run` trigger ensures pre-breakout only fires after upstream success; it cannot affect confirmation-pipeline outcome

### Residual risks
- `collect_options_si_state.py` runs sequentially after scoring. If scoring fails (DB error), collection is skipped — intentional, since both share the same DB dependency.
- Stage 3 workflow has no daily-cap enforcement at the workflow level; the cap (≤10 names) is enforced inside `stage3_synthesis.py`.

## QA Result: PASS

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-041, "Pre-Breakout Workflows And Automation."

Goal:
- Automate the pre-breakout system with dedicated GitHub Actions workflows while keeping it logically separate from the existing confirmation pipeline.

Context:
- The pre-breakout program is already implemented:
  - `pre_breakout_pipeline.py`
  - `scripts/collect_options_si_state.py`
  - `utils/stage3_synthesis.py`
  - `utils/setup_outcome_resolver.py`
- Current repo review shows none of those are wired into `.github/workflows/` or `run_master.sh`.
- Existing workflows (`daily_pipeline.yml`, `manual_pipeline.yml`) still only run `run_master.sh`.

Required outcome:
- Add a dedicated scheduled workflow for deterministic pre-breakout runs.
- Add automation for the options / short-interest collection path.
- Add a separate manual workflow path for optional Stage 3 Claude synthesis.
- Keep all of this separate from `run_master.sh` and the current confirmation-pipeline workflows.

Recommended shape:
1. `daily_pre_breakout.yml`
   - triggered by `workflow_run` after `Daily Signal Pipeline (no AI cost)` succeeds
   - runs deterministic `pre_breakout_pipeline.py`
   - runs `scripts/collect_options_si_state.py`
   - no Stage 3 / no AI by default
   - sends Telegram success/failure notification
2. `manual_pre_breakout_stage3.yml`
   - `workflow_dispatch`
   - runs `pre_breakout_pipeline.py --stage3`
   - clearly labeled as AI-enabled/manual
   - sends Telegram success/failure notification

Constraints:
- Do not fold pre-breakout into `run_master.sh` in this ticket.
- Do not change scoring logic or thresholds.
- Do not invent a dashboard feature here.
- Workflow commands must use the actual documented runtime contracts from the finished tickets.
- Keep names and descriptions explicit about AI cost and purpose.
- Reuse the repo's existing Telegram notification pattern rather than inventing a parallel notification system.

Verification:
- Validate the workflow YAML.
- Re-run the exact commands the workflows use where practical.
- If you add any minimal helper changes, add targeted tests only as needed.

Non-goals:
- No dashboard/API exposure in this ticket.
- No broad workflow refactor of the existing confirmation pipeline.
```
