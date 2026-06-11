-- ============================================================================
-- Migration 007: Option Risk and Position Sizing Framework
-- TRD-046 — adds PM/risk columns to option_candidate_snapshots
-- ============================================================================
-- Safe to run repeatedly (ADD COLUMN IF NOT EXISTS is idempotent on Postgres).
-- All columns are nullable so existing rows are unaffected.
-- exit_hierarchy stored as JSON (ordered list of prose rules).
-- ============================================================================

ALTER TABLE option_candidate_snapshots
    ADD COLUMN IF NOT EXISTS risk_allowed                    BOOLEAN,
    ADD COLUMN IF NOT EXISTS risk_block_reason               TEXT,
    ADD COLUMN IF NOT EXISTS max_premium_risk_usd            REAL,
    ADD COLUMN IF NOT EXISTS suggested_contract_count        INTEGER,
    ADD COLUMN IF NOT EXISTS position_size_tier              TEXT,   -- skip | reduced | standard | max
    ADD COLUMN IF NOT EXISTS event_risk_policy               TEXT,
    ADD COLUMN IF NOT EXISTS iv_regime_label                 TEXT,
    ADD COLUMN IF NOT EXISTS portfolio_concentration_warning TEXT,
    ADD COLUMN IF NOT EXISTS exit_hierarchy_json             JSONB,  -- ordered list of prose exit rules
    ADD COLUMN IF NOT EXISTS risk_nav_source                 TEXT;   -- account | model

-- Index for analytics: block-rate by reason, size tier distribution
CREATE INDEX IF NOT EXISTS idx_ocs_position_size_tier
    ON option_candidate_snapshots (position_size_tier)
    WHERE NOT suppressed;

CREATE INDEX IF NOT EXISTS idx_ocs_risk_allowed
    ON option_candidate_snapshots (risk_allowed)
    WHERE NOT suppressed;

CREATE INDEX IF NOT EXISTS idx_ocs_iv_regime
    ON option_candidate_snapshots (iv_regime_label)
    WHERE NOT suppressed;
