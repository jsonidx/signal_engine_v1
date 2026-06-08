-- =============================================================================
-- Migration 013: Ticker Governance Policy
-- Ticket: TRD-068
-- Idempotent: safe to re-run.
-- =============================================================================

-- Per-ticker governance classification for PM oversight.
-- States: A_LIST | STANDARD | PROBATION | QUARANTINE
--
-- A_LIST    — priority boost; PM has high confidence in this name
-- STANDARD  — default; no adjustment
-- PROBATION — priority penalty; under review; higher conviction required
-- QUARANTINE — hard-gated out of AI selection; not for trading

CREATE TABLE IF NOT EXISTS ticker_governance (
    ticker           TEXT        NOT NULL PRIMARY KEY,
    governance_state TEXT        NOT NULL DEFAULT 'STANDARD',
    reason           TEXT,
    notes            TEXT,
    set_by           TEXT        DEFAULT 'pm',
    set_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT chk_governance_state
        CHECK (governance_state IN ('A_LIST', 'STANDARD', 'PROBATION', 'QUARANTINE'))
);

CREATE INDEX IF NOT EXISTS idx_ticker_governance_state ON ticker_governance (governance_state);

ALTER TABLE ticker_governance ENABLE ROW LEVEL SECURITY;

-- CREATE POLICY does not support IF NOT EXISTS; guard with a DO block.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = current_schema()
          AND tablename  = 'ticker_governance'
          AND policyname = 'allow_all_ticker_governance'
    ) THEN
        CREATE POLICY "allow_all_ticker_governance"
            ON ticker_governance FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;
