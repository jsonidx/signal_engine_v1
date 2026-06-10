-- TRD-080: Options Screener Snapshot Architecture
-- Stores precomputed screener results so page loads read a DB row instead of
-- fanning out across all thesis tickers on demand.

CREATE TABLE IF NOT EXISTS options_screener_snapshot (
    id                  BIGSERIAL PRIMARY KEY,
    run_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    min_conviction      INT NOT NULL DEFAULT 2,
    tickers_evaluated   INT,
    tickers_completed   INT,
    partial             BOOLEAN DEFAULT FALSE,
    timed_out_tickers   TEXT[],
    count               INT NOT NULL DEFAULT 0,
    data                JSONB NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_options_screener_snapshot_lookup
    ON options_screener_snapshot (min_conviction, run_at DESC);

COMMENT ON TABLE options_screener_snapshot IS
    'Precomputed options screener results, refreshed daily by the pipeline and on-demand via POST /api/options/screener/refresh.';
