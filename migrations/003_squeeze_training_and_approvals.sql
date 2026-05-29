-- =============================================================================
-- 003_squeeze_training_and_approvals.sql
-- =============================================================================
-- Purpose:
--   Add three new tables introduced by TRD-012, TRD-014, and TRD-015:
--     1. squeeze_training_snapshots  — ML-ready point-in-time signal features
--     2. squeeze_training_outcomes   — realized forward returns + taxonomy labels
--     3. approval_requests           — human-approval gate for trading-logic changes
--
-- These tables are safe to run on an existing schema; all statements use
-- CREATE TABLE IF NOT EXISTS and IF NOT EXISTS guards.
-- =============================================================================

-- ----------------------------------------------------------------------------
-- 1. squeeze_training_snapshots
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS squeeze_training_snapshots (
    id                          BIGSERIAL PRIMARY KEY,
    signal_date                 DATE        NOT NULL,
    ticker                      TEXT        NOT NULL,
    alert_type                  TEXT,
    final_score                 REAL,
    short_pct_float             REAL,
    computed_dtc_30d            REAL,
    compression_recovery_score  REAL,
    volume_confirmation_flag    BOOLEAN,
    si_persistence_score        REAL,
    effective_float_score       REAL,
    effective_short_float_ratio REAL,
    large_holder_ownership_pct  REAL,
    options_pressure_score      REAL,
    iv_rank                     REAL,
    unusual_call_activity_flag  BOOLEAN,
    risk_score                  REAL,
    risk_level                  TEXT,
    dilution_risk_flag          BOOLEAN,
    explanation_tags            JSONB,
    explanation_summary         TEXT,
    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (signal_date, ticker, alert_type)
);

ALTER TABLE squeeze_training_snapshots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "authenticated_full_access" ON squeeze_training_snapshots;
CREATE POLICY "authenticated_full_access" ON squeeze_training_snapshots
    FOR ALL TO authenticated USING (true) WITH CHECK (true);

-- ----------------------------------------------------------------------------
-- 2. squeeze_training_outcomes
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS squeeze_training_outcomes (
    id              BIGSERIAL PRIMARY KEY,
    signal_date     DATE    NOT NULL,
    ticker          TEXT    NOT NULL,
    alert_type      TEXT,
    fwd_5d          REAL,
    fwd_10d         REAL,
    fwd_20d         REAL,
    fwd_30d         REAL,
    max_fwd_return  REAL,
    hit_15pct_10d   BOOLEAN,
    hit_25pct_20d   BOOLEAN,
    outcome_label   TEXT,
    taxonomy_label  TEXT,
    labeled_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (signal_date, ticker, alert_type)
);

ALTER TABLE squeeze_training_outcomes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "authenticated_full_access" ON squeeze_training_outcomes;
CREATE POLICY "authenticated_full_access" ON squeeze_training_outcomes
    FOR ALL TO authenticated USING (true) WITH CHECK (true);

-- ----------------------------------------------------------------------------
-- 3. approval_requests
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approval_requests (
    request_id           TEXT        NOT NULL PRIMARY KEY,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    category             TEXT        NOT NULL,
    risk_level           TEXT        NOT NULL DEFAULT 'LOW',
    title                TEXT        NOT NULL,
    summary              TEXT,
    evidence_ref         TEXT,
    proposed_change_json JSONB,
    status               TEXT        NOT NULL DEFAULT 'PENDING',
    approved_by          TEXT,
    approved_at          TIMESTAMPTZ,
    expires_at           TIMESTAMPTZ,
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE approval_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "authenticated_full_access" ON approval_requests;
CREATE POLICY "authenticated_full_access" ON approval_requests
    FOR ALL TO authenticated USING (true) WITH CHECK (true);
