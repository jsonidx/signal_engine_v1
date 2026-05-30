-- ============================================================================
-- Migration 005: Option Execution Guidance Fields
-- TRD-031 — adds deterministic entry-guidance columns to option_candidate_snapshots
-- ============================================================================
-- Safe to run repeatedly (ADD COLUMN IF NOT EXISTS is idempotent on Postgres).
-- All columns are nullable so existing rows are unaffected.
-- ============================================================================

ALTER TABLE option_candidate_snapshots
    ADD COLUMN IF NOT EXISTS recommended_entry_price  REAL,
    ADD COLUMN IF NOT EXISTS recommended_order_type   TEXT,   -- always 'limit'
    ADD COLUMN IF NOT EXISTS max_chase_price          REAL,
    ADD COLUMN IF NOT EXISTS entry_style              TEXT,   -- passive | balanced | aggressive
    ADD COLUMN IF NOT EXISTS entry_rationale          TEXT,
    ADD COLUMN IF NOT EXISTS fill_quality_score       REAL,   -- 0.0–1.0
    ADD COLUMN IF NOT EXISTS slippage_risk_label      TEXT,   -- low | moderate | high | very_high
    ADD COLUMN IF NOT EXISTS skip_if_spread_above_pct REAL;

-- Index to support later analytics querying by entry_style and slippage_risk
CREATE INDEX IF NOT EXISTS idx_ocs_entry_style
    ON option_candidate_snapshots (entry_style)
    WHERE NOT suppressed;

CREATE INDEX IF NOT EXISTS idx_ocs_slippage_risk
    ON option_candidate_snapshots (slippage_risk_label)
    WHERE NOT suppressed;
