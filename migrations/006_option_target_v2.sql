-- ============================================================================
-- Migration 006: Option Target Engine v2 Fields
-- TRD-043 — adds deterministic thesis-linked projected option level columns
-- to option_candidate_snapshots.
-- ============================================================================
-- Safe to run repeatedly (ADD COLUMN IF NOT EXISTS is idempotent on Postgres).
-- All columns are nullable so existing rows are unaffected.
-- Legacy flat-multiplier fields (option_take_profit_1/2, option_stop_loss)
-- are retained for backward compatibility; projected_* columns supersede them.
-- ============================================================================

ALTER TABLE option_candidate_snapshots
    ADD COLUMN IF NOT EXISTS projected_option_tp1       REAL,
    ADD COLUMN IF NOT EXISTS projected_option_tp2       REAL,
    ADD COLUMN IF NOT EXISTS projected_option_stop      REAL,
    ADD COLUMN IF NOT EXISTS projected_tp1_return_pct   REAL,
    ADD COLUMN IF NOT EXISTS projected_tp2_return_pct   REAL,
    ADD COLUMN IF NOT EXISTS projected_stop_return_pct  REAL,
    ADD COLUMN IF NOT EXISTS target_projection_method   TEXT;   -- delta_only | delta_dte_adjusted | insufficient_inputs

-- Index to support cohort comparison analytics by method (TRD-044)
CREATE INDEX IF NOT EXISTS idx_ocs_projection_method
    ON option_candidate_snapshots (target_projection_method)
    WHERE NOT suppressed;
