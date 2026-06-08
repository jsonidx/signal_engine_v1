-- =============================================================================
-- Migration 012: Universe Coverage and Qualification Analytics
-- Ticket: TRD-059
-- Idempotent: safe to re-run.
-- =============================================================================

-- Daily funnel snapshot: raw universe → lane routing → AI selection → issuance
CREATE TABLE IF NOT EXISTS funnel_metrics (
    run_date                   DATE        NOT NULL PRIMARY KEY,
    raw_universe_count         INTEGER,
    hard_excluded_count        INTEGER,
    lane_excluded_count        INTEGER,
    execution_core_count       INTEGER,
    execution_high_beta_count  INTEGER,
    research_broad_count       INTEGER,
    prescreened_count          INTEGER,
    agreement_eligible_count   INTEGER,
    ai_selected_count          INTEGER,
    active_thesis_count        INTEGER,
    watch_only_count           INTEGER,
    suppressed_count           INTEGER,
    no_trade_count             INTEGER,
    bull_count                 INTEGER,
    bear_count                 INTEGER,
    neutral_count              INTEGER,
    excluded_by_source         JSONB,
    suppression_reasons        JSONB,
    created_at                 TIMESTAMPTZ DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_funnel_metrics_run_date ON funnel_metrics (run_date DESC);

ALTER TABLE funnel_metrics ENABLE ROW LEVEL SECURITY;

-- CREATE POLICY does not support IF NOT EXISTS; guard with a DO block.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = current_schema()
          AND tablename  = 'funnel_metrics'
          AND policyname = 'allow_all_funnel_metrics'
    ) THEN
        CREATE POLICY "allow_all_funnel_metrics"
            ON funnel_metrics FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;
