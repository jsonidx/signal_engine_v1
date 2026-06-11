# Task: Fix AI-cost scheduled pipeline skip when GitHub cron starts late

Status: done
Stage: done
Type: bug
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Codex
Product Area: infra
Category: reliability | automation
Risk: ops
Effort: S
Target Release: next
Due Date: 2026-06-12
Dependencies: none
Blocked By: none
Links: `.github/workflows/manual_pipeline.yml`, `https://github.com/jsonidx/signal_engine_v1/actions/runs/27328870977`
Success Metric: the scheduled `Daily Signal Pipeline (with AI cost)` executes exactly once per Berlin calendar day across DST changes even when GitHub Actions dispatches the cron run hours late.

## Problem Statement

The scheduled AI-cost workflow started on June 11, 2026, but the main `run-pipeline` job was skipped because the schedule guard compares the runner's current Berlin hour to a hardcoded value of `04`.

This makes the workflow sensitive to GitHub Actions cron delay. If GitHub dispatches the scheduled run after `04:59` Berlin time, the guard emits `should_run=false` and the pipeline never runs that day.

## Evidence

- Workflow: `Daily Signal Pipeline (with AI cost)`
- Run ID: `27328870977`
- Event: `schedule`
- Run started: `2026-06-11T06:40:45Z` = `2026-06-11 08:40:45 CEST` in Berlin
- Job results:
  - `berlin-schedule-guard`: success
  - `run-pipeline`: skipped

Guard logic in `.github/workflows/manual_pipeline.yml`:

```yaml
- name: Check Berlin local time
  id: berlin-time
  run: |
    BERLIN_HOUR=$(TZ=Europe/Berlin date +%H)
    if [ "${{ github.event_name }}" = "workflow_dispatch" ] || [ "$BERLIN_HOUR" = "04" ]; then
      echo "should_run=true" >> "$GITHUB_OUTPUT"
    else
      echo "should_run=false" >> "$GITHUB_OUTPUT"
    fi
```

Relevant log lines from the guard job:

```text
Run BERLIN_HOUR=$(TZ=Europe/Berlin date +%H)
if [ "schedule" = "workflow_dispatch" ] || [ "$BERLIN_HOUR" = "04" ]; then
  echo "should_run=true" >> "$GITHUB_OUTPUT"
else
  echo "should_run=false" >> "$GITHUB_OUTPUT"
fi
```

## User Impact

- The daily AI-cost pipeline can silently fail to execute on scheduled days.
- Operators may see a green workflow run even though the actual pipeline job never ran.
- Daily research or downstream dependent workflows can miss a day without an explicit failure signal.

## Objective

Make the scheduled AI-cost workflow robust to normal GitHub Actions cron delay while still preventing duplicate daily runs across DST changes.

## Proposed Solution

Replace the current "current Berlin hour must equal 04" guard with a dispatch-stable condition.

Preferred direction:

- Use `github.event.schedule` to identify which cron expression triggered the run instead of comparing the current runner clock.
- Allow exactly one of the two cron entries to execute for the current DST mode:
  - run on `0 2 * * *` when Berlin local offset is `UTC+2`
  - run on `0 3 * * *` when Berlin local offset is `UTC+1`
- Keep `workflow_dispatch` always allowed.

Acceptable alternative:

- Remove the dual-cron/hour-guard approach and replace it with a single schedule plus an explicit seasonal adjustment strategy that is simpler and observable.

Also harden observability:

- if a scheduled run is intentionally suppressed, make that reason explicit in logs
- avoid reporting full workflow success when the only substantive job was skipped unless that outcome is intentional and clearly surfaced

## Scope

- `.github/workflows/manual_pipeline.yml`

## Non-Goals

- Do not redesign the pipeline itself.
- Do not change `run_master.sh`.
- Do not modify the manual no-AI workflow unless needed for consistency.

## Constraints

- Preserve exactly-once-per-day behavior for scheduled runs.
- Preserve DST correctness for Europe/Berlin.
- Preserve `workflow_dispatch` behavior.

## Acceptance Criteria

- A scheduled run dispatched late by GitHub still executes the pipeline if it corresponds to the intended daily Berlin schedule.
- The workflow does not run twice on the same day because both cron entries fired.
- The skip decision is based on trigger metadata or explicit dedupe logic, not the runner's wall clock at dispatch time.
- Logs clearly state why a scheduled run is allowed or suppressed.

## Verification Plan

- Trigger the workflow manually with simulated guard inputs where possible.
- Validate the guard decision for both cron expressions:
  - `0 2 * * *` during CEST
  - `0 3 * * *` during CET
- Confirm that a delayed scheduled run would still set `should_run=true` for the valid daily trigger.

## QA Notes

- Test scenarios: CEST day, CET day, manual dispatch, delayed scheduled dispatch
- Edge cases: DST transition days, both cron events present, late GitHub scheduler start
- Regression risks: accidental double-run or accidental no-run on DST boundary

## Launch / Release Notes

- User-facing change summary: the daily AI-cost scheduled pipeline no longer skips execution when GitHub runs the cron late.
- Operational notes: monitor the first scheduled run after rollout and verify exactly one pipeline execution for the day.
- Rollback notes: revert `.github/workflows/manual_pipeline.yml` if schedule behavior becomes unstable.

## Post-Launch Validation

- What to monitor: next several scheduled AI-cost runs, especially recorded trigger cron vs actual dispatch time
- How success will be confirmed: one scheduled pipeline execution per day with no skipped `run-pipeline` job caused by late dispatch
- Follow-up decision date: after the next DST change or one week of scheduled runs, whichever comes first

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement BUG-002 in `.github/workflows/manual_pipeline.yml`.

Problem:
- the scheduled `Daily Signal Pipeline (with AI cost)` uses two UTC cron entries
- it currently decides whether to run by checking whether the runner's Berlin local hour equals `04`
- GitHub dispatched run `27328870977` on 2026-06-11 at 08:40 CEST, so the guard set `should_run=false` and skipped `run-pipeline`

Goal:
- scheduled runs must still execute exactly once per Berlin day even if GitHub dispatches the cron late
- preserve DST handling and preserve manual `workflow_dispatch`

Required changes:
- stop using the runner's current Berlin hour as the gating condition
- base the scheduled allow/skip decision on stable trigger metadata or explicit dedupe logic
- keep logs explicit about why the run is allowed or suppressed

Constraints:
- no double-run across the two cron entries
- no changes to `run_master.sh`

Validation:
- explain how the new guard behaves for CEST, CET, manual dispatch, and delayed cron dispatch
```
