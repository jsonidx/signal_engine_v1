-- ============================================================================
-- Migration 009: Option Target Calibration and Comparator Fields
-- TRD-044 — adds v2 hit markers and method label to option_candidate_outcomes
--           so legacy-vs-v2 target accuracy can be measured side-by-side.
-- ============================================================================
-- Safe to run repeatedly (ADD COLUMN IF NOT EXISTS is idempotent).
-- All new columns are nullable so existing outcome rows are unaffected.
-- ============================================================================

-- Add target method label (copied from snapshot at resolution time)
ALTER TABLE option_candidate_outcomes
    ADD COLUMN IF NOT EXISTS target_projection_method  TEXT;

-- V2 hit markers (compare against projected_option_tp1/2 / projected_option_stop)
ALTER TABLE option_candidate_outcomes
    ADD COLUMN IF NOT EXISTS hit_v2_tp1   BOOLEAN,
    ADD COLUMN IF NOT EXISTS hit_v2_tp2   BOOLEAN,
    ADD COLUMN IF NOT EXISTS hit_v2_stop  BOOLEAN;

-- Index: split by method in comparator queries
CREATE INDEX IF NOT EXISTS idx_oco_projection_method
    ON option_candidate_outcomes (target_projection_method)
    WHERE target_projection_method IS NOT NULL;
