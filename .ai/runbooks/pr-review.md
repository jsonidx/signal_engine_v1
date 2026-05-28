# PR Review Runbook

1. Identify changed files.
2. Classify risk:
   - frontend
   - api
   - trading-logic
   - infra
   - secrets
3. Read the task/spec and compare with the diff.
4. Review for correctness before style.
5. Run targeted tests.
6. Run broader verification when risk warrants it.
7. Lead the review with findings.
8. Block merge for untested trading logic or possible secret exposure.
