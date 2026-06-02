# Task: Fix Stale Thesis Refresh CI Working Directory

Status: done
Stage: finished
Type: bug
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: reliability
Risk: infra
Effort: XS
Target Release: next patch
Due Date: 2026-06-02
Dependencies: none
Blocked By: none
Links: GitHub Actions run `26800971462`; workflow `.github/workflows/manual_pipeline.yml`
Success Metric: the manual pipeline completes stale thesis refresh on GitHub Actions without failing due to machine-specific path resolution.

## Problem Statement

The `Manual Signal Pipeline (with AI cost)` workflow failed on June 2, 2026 after the main pipeline completed. The failure occurred in `refresh_stale_theses.py` during stale thesis refresh batch execution because the script launches `ai_quant.py` with a hard-coded local macOS working directory.

Current code uses:

`subprocess.run(cmd, cwd="/Users/jason/signal_engine_v1")`

That path exists on one local machine but not on GitHub Actions Ubuntu runners, where the repository is checked out under `/home/runner/work/...`. As a result, the workflow crashes with `FileNotFoundError` even though the upstream pipeline steps succeeded.

## User Impact

Operators see the full manual signal pipeline marked as failed in GitHub Actions even when signal generation and AI synthesis complete successfully. This breaks automated stale-thesis maintenance in CI and creates noise in workflow monitoring, Telegram notifications, and operational follow-up.

## Objective

Make stale thesis refresh path handling environment-independent so the workflow runs correctly both locally and on GitHub Actions.

## Proposed Solution

Replace the hard-coded `cwd` in `refresh_stale_theses.py` with a repo-relative path derived from the script location using `pathlib.Path`.

Implementation should:

- derive a stable project root from `__file__`
- use that derived root for `subprocess.run(...)`
- preserve the current command behavior and batching logic
- scan for closely related hard-coded local repo paths in adjacent workflow helper scripts and remove any obvious duplicate risk if found

## Scope

Files or modules likely affected:

- `refresh_stale_theses.py`
- `.github/workflows/manual_pipeline.yml`
- related scripts only if they contain the same machine-specific path pattern

## Non-Goals

- Do not change trading logic, thesis-generation logic, or stale-thesis selection rules.
- Do not redesign the workflow structure or split the refresh step into a different job.
- Do not perform broad refactors unrelated to path handling.
- Do not change secrets, environment-variable contracts, or external API behavior unless strictly required for the path fix.

## Constraints

- Use `pathlib.Path` for path resolution.
- Avoid machine-specific absolute paths.
- Keep the patch minimal and focused on the CI failure.
- No secrets or generated artifacts in git.

## Acceptance Criteria

- Observable behavior: `refresh_stale_theses.py` no longer depends on `/Users/jason/signal_engine_v1` or any other user-specific absolute path.
- Observable behavior: stale thesis refresh can launch `ai_quant.py` correctly from a GitHub Actions checkout path.
- Observable behavior: the manual pipeline no longer fails at the stale-thesis refresh step because of missing local path resolution.
- Tests: a reasonable local verification step is run for the updated script behavior, or the limitation is documented if full end-to-end CI reproduction is not possible locally.
- Documentation: none required unless another path contract is clarified in comments.

## Verification Plan

- `rg -n "Users/jason/signal_engine_v1|cwd=" refresh_stale_theses.py scripts .github/workflows`
- `python3 refresh_stale_theses.py --help`
- Run a targeted local invocation or dry run that exercises command construction without requiring full live workflow execution
- If feasible, run the narrowest relevant test or script path-resolution check after the patch

## QA Notes

- Test scenarios: local repo execution, CI-style checkout path assumptions, and dry-run batch command generation.
- Edge cases: script invoked from a working directory other than repo root; future script moves that still require robust root derivation.
- Regression risks: low, limited to subprocess launch location for stale thesis refresh.

## Launch / Release Notes

- User-facing change summary: the manual signal pipeline no longer fails during stale thesis refresh because of a machine-specific repo path.
- Operational notes: after deployment, re-run the manual pipeline workflow to confirm the refresh step completes on GitHub Actions.
- Rollback notes: revert the narrow path-resolution patch if unexpected local execution regressions appear.

## Post-Launch Validation

- What to monitor: the next GitHub Actions run of `Manual Signal Pipeline (with AI cost)`.
- How success will be confirmed: the workflow reaches and completes stale thesis refresh batches without `FileNotFoundError` for the repo path.
- Follow-up decision date: 2026-06-03

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Task: Fix stale thesis refresh CI failure caused by hard-coded working directory

Goal:
Patch the repo so `refresh_stale_theses.py` works both locally and on GitHub Actions, without relying on a machine-specific absolute path.

Context:
- The failure occurred in the GitHub Actions workflow `.github/workflows/manual_pipeline.yml` on June 2, 2026.
- The workflow ran successfully through the main pipeline and AI synthesis, then failed during stale thesis refresh.
- The root cause is in `refresh_stale_theses.py`, which currently launches a subprocess with:
  `subprocess.run(cmd, cwd="/Users/jason/signal_engine_v1")`
- On GitHub Actions, that path does not exist, and the job crashes with:
  `FileNotFoundError: [Errno 2] No such file or directory: '/Users/jason/signal_engine_v1'`

Exact scope:
- `refresh_stale_theses.py`
- Check nearby workflow/helper scripts for the same hard-coded local repo path pattern and fix obvious duplicates if present
- Read `.github/workflows/manual_pipeline.yml` only as context; do not redesign the workflow unless truly necessary

Required change:
- Replace the hard-coded `cwd` with a dynamically derived repo root using `pathlib.Path`
- Preserve current batching and command behavior
- Keep the patch narrow and pragmatic

Implementation preferences:
- Use `Path(__file__).resolve()` and derive the project root from the script location
- Pass `cwd=str(PROJECT_ROOT)` to `subprocess.run(...)`
- If needed for robustness, make any script path references explicit and repo-relative, but avoid broad refactors

Non-goals:
- No trading-logic changes
- No stale-thesis algorithm changes
- No workflow restructure
- No unrelated refactors
- Do not change secrets or environment contracts

Verification:
- Search for any remaining `/Users/jason/signal_engine_v1` hard-coded paths in the relevant workflow/script area
- Run:
  `python3 refresh_stale_theses.py --help`
- Run a narrow local verification or dry run that confirms path resolution and subprocess command setup still work
- Summarize any verification limitations if full CI reproduction is not practical locally

Deliverable:
- Minimal code patch fixing the CI path issue
- Concise summary covering:
  - root cause
  - exact code changes
  - verification performed
  - any remaining risks
```

## Lifecycle

- Create new tickets in `docs/tasks/new/` with `Status: proposed`.
- If the ticket is intended for Claude Code implementation, add the initial paste-ready implementation prompt in `## Handoff Notes` when the ticket is created.
- When Claude starts implementation, set `Status: in progress`, update `Stage: in progress`, and move the file to `docs/tasks/in-progress/`.
- After QA passes and the work is complete, set `Status: done` or `Status: completed` and move the file to `docs/tasks/finished/`.
- Run `python3 scripts/sync_task_status.py` to move files automatically and validate that `Status:` and `Stage:` match the workflow.
