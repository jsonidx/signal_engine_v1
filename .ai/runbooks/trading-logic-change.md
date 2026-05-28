# Trading Logic Change Runbook

Use this for changes to:

- `config.py`
- `signal_engine.py`
- `conflict_resolver.py`
- `backtest.py`
- `utils/prob_engine.py`
- screener modules
- ranking or position sizing logic

Required controls:

1. State the hypothesis.
2. Identify affected output fields.
3. Add or update unit tests.
4. Run regression fixtures where available.
5. Compare benchmark outputs before and after.
6. Check top-ranked ticker churn.
7. Check direction and conviction changes.
8. Require human approval before merge.

Default decision rule:

Reject changes that improve code aesthetics but alter ranking, risk, or sizing without benchmark evidence.
