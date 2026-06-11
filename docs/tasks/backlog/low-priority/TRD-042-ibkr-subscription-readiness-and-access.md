# Task: IBKR Subscription Readiness and Access

Status: proposed
Stage: ready
Type: chore
Priority: P3
Severity: high
Owner: Human
Reviewer: Human
Product Area: infra
Category: automation
Risk: secrets
Effort: S
Target Release: backlog
Due Date: TBD
Dependencies: none
Blocked By: none
Links: `docs/backlogs/yfinance-to-ibkr-migration.md`, `docs/tasks/new/TRD-020-ibkr-options-roadmap.md`, `docs/tasks/new/TRD-021-ibkr-option-chain-adapter.md`
Success Metric: the project has an IBKR environment with the required account entitlements and a verified API session that can return option market data for one US equity ticker.

## Problem Statement

The IBKR/options backlog assumes broker access exists, but the team decided to wait until the required IBKR subscriptions are active. Without that prerequisite, implementation would be based on assumptions about entitlements, quotes, and Greeks behavior instead of the real account path.

## User Impact

If this prerequisite is skipped, engineering time will be spent building and testing against incomplete assumptions. That creates avoidable rework risk for the adapter, the candidate engine, and the options UI.

## Objective

Establish the minimum IBKR account, permissions, subscriptions, and runtime access needed so the IBKR-backed options tickets can move into implementation.

## Proposed Solution

Complete the account and environment setup first, then verify the setup with one controlled API connectivity check before starting any IBKR-dependent product work.

## Scope

Files or modules likely affected:

- `docs/backlogs/yfinance-to-ibkr-migration.md`
- `docs/tasks/new/TRD-020-ibkr-options-roadmap.md`
- `docs/tasks/new/TRD-021-ibkr-option-chain-adapter.md`

## Non-Goals

- Do not implement the adapter in this ticket.
- Do not build any options API or UI features in this ticket.
- Do not commit credentials, session artifacts, or secrets to git.

## Constraints

- Use the actual intended IBKR account path, not a hypothetical setup.
- Confirm both account permissions and market-data entitlements.
- Capture only reusable setup notes, never credentials.

## Acceptance Criteria

- Observable behavior: an IBKR Pro account path is confirmed for this project.
- Observable behavior: required options permissions are enabled for the target account.
- Observable behavior: required subscriptions for US equity and options market data are active.
- Observable behavior: a paper or live API session can authenticate through the chosen runtime path.
- Observable behavior: one known US options ticker can be queried successfully enough to confirm the entitlement path is usable for TRD-021.
- Documentation: the relevant backlog/task notes state that TRD-021 through TRD-024 can start only after this ticket is complete.

## Verification Plan

- Human verification in IBKR account management
- One manual API connectivity check against a known liquid US options ticker
- Update the dependent task files once access is confirmed

## QA Notes

- Test scenarios:
- Edge cases:
- Regression risks:

## Launch / Release Notes

- User-facing change summary: none; prerequisite only.
- Operational notes: store account and runtime details outside git.
- Rollback notes: if subscriptions are delayed, keep IBKR implementation tickets blocked.

## Post-Launch Validation

- What to monitor: entitlement status, session stability, and whether quotes/Greeks are actually available.
- How success will be confirmed: TRD-021 can start using real account behavior rather than assumptions.
- Follow-up decision date: immediately after the first successful connectivity check.

## Handoff Notes

This is a PM / operations gate. Do not start TRD-021, TRD-022, TRD-023, or TRD-024 until this ticket is complete.
