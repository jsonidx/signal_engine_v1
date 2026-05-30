# Squeeze Calibration Report — 2026-05-30

**No labeled outcomes found.**

Possible causes:
- No squeeze training snapshots have been persisted yet.
- Forward windows have not yet closed (need >= 30 trading days of history).
- squeeze_training_outcomes table is empty.

Run the pipeline to accumulate training data, then re-run calibration.
