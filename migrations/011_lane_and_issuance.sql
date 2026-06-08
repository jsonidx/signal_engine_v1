-- =============================================================================
-- Migration 011: Lane-based routing + Issuance States + Research-Lane Funnel
-- Tickets: TRD-065, TRD-066, TRD-057
-- =============================================================================

-- TRD-066: Add issuance_state to thesis_cache
-- Values: ACTIVE_THESIS | WATCH_ONLY | SUPPRESSED | NO_TRADE
ALTER TABLE thesis_cache
    ADD COLUMN IF NOT EXISTS issuance_state TEXT;

-- TRD-065: Add candidate_lane to candidate_snapshots (if table exists)
-- Lane values: execution_core | execution_high_beta | research_broad | hard_excluded
ALTER TABLE candidate_snapshots
    ADD COLUMN IF NOT EXISTS candidate_lane TEXT;

-- TRD-057: Research-lane candidates table
-- Persists the full prescreened cohort before AI selection narrows the funnel.
CREATE TABLE IF NOT EXISTS research_lane_candidates (
    id              BIGSERIAL   PRIMARY KEY,
    date            TEXT        NOT NULL,
    ticker          TEXT        NOT NULL,
    rank            INTEGER,
    total           INTEGER,
    lane            TEXT,
    status          TEXT,
    force_tags      TEXT[],
    score           REAL,
    advanced_to_ai  BOOLEAN     DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_rlc_date      ON research_lane_candidates (date);
CREATE INDEX IF NOT EXISTS idx_rlc_lane_date ON research_lane_candidates (lane, date);

-- Enable RLS (matches project policy)
ALTER TABLE research_lane_candidates ENABLE ROW LEVEL SECURITY;

-- CREATE POLICY does not support IF NOT EXISTS; guard with a DO block.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'research_lane_candidates'
          AND policyname = 'allow_all_research_lane_candidates'
    ) THEN
        CREATE POLICY "allow_all_research_lane_candidates"
            ON research_lane_candidates
            FOR ALL
            USING (true)
            WITH CHECK (true);
    END IF;
END $$;
