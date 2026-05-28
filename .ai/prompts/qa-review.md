# QA Review Template

Use this for Codex review of an AI-generated branch or diff.

## Review Target

- Branch or diff:
- Task/spec:

## Findings

List bugs, regressions, missing tests, security risks, and architecture drift first.

## Verification

Commands run:

- `pytest`
- `cd dashboard/frontend && npm test`
- `cd dashboard/frontend && npm run build`

## Risk Classification

- `risk:none`
- `risk:frontend`
- `risk:api`
- `risk:trading-logic`
- `risk:infra`
- `risk:secrets`

## Merge Recommendation

State one:

- Approve
- Approve with follow-ups
- Block
