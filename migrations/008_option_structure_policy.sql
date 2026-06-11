-- ============================================================================
-- Migration 008: Option Structure Selection Policy (TRD-048)
-- Adds archetype classification fields to option_candidate_snapshots.
-- ============================================================================
-- Safe to run repeatedly (ADD COLUMN IF NOT EXISTS is idempotent on Postgres).
-- Both columns are nullable so existing rows are unaffected.
-- structure_policy_reason is free text — no length cap to preserve full context.
-- ============================================================================

ALTER TABLE option_candidate_snapshots
    ADD COLUMN IF NOT EXISTS structure_archetype      TEXT,   -- short_breakout | medium_swing | slow_macro | event_sensitive | default_swing
    ADD COLUMN IF NOT EXISTS structure_policy_reason  TEXT;   -- human-readable reason for the chosen archetype

-- Index for analytics: breakdown by archetype, hit rates, sizing distributions
CREATE INDEX IF NOT EXISTS idx_ocs_structure_archetype
    ON option_candidate_snapshots (structure_archetype)
    WHERE NOT suppressed;
