-- Migration 019: Dashboard Segment Snapshot Foundation (TRD-082)
--
-- Generic persisted snapshot store for dashboard payload segments.
-- Producer-time writes (ai_quant, post-run pipeline steps) feed this table.
-- GET handlers read from it as L2 behind the in-process L1 cache.
--
-- Usage:
--   apply once: psql $DATABASE_URL -f migrations/019_dashboard_segment_snapshots.sql

CREATE TABLE IF NOT EXISTS dashboard_segment_snapshots (
    segment       TEXT        NOT NULL,
    snapshot_key  TEXT        NOT NULL,
    run_date      TEXT,
    source_step   TEXT,
    payload_json  JSONB       NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    stale_after   TIMESTAMPTZ,
    version       INTEGER     DEFAULT 1,
    meta_json     JSONB,
    PRIMARY KEY (segment, snapshot_key)
);

CREATE INDEX IF NOT EXISTS dss_segment_created_idx
    ON dashboard_segment_snapshots (segment, created_at DESC);

-- RLS: allow service-role reads/writes (same pattern as other tables)
ALTER TABLE dashboard_segment_snapshots ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'dashboard_segment_snapshots'
      AND policyname = 'service_role_all'
  ) THEN
    CREATE POLICY service_role_all ON dashboard_segment_snapshots
        FOR ALL TO service_role USING (true) WITH CHECK (true);
  END IF;
END $$;
