-- Migration 023: canonical thesis_outcomes table creation + RLS
-- Idempotent: CREATE TABLE IF NOT EXISTS + IF NOT EXISTS policy guard.
-- All columns that were previously added via runtime migrations or
-- _migrate_outcomes_table() are included here for fresh-install correctness.

CREATE TABLE IF NOT EXISTS thesis_outcomes (
    id                  SERIAL PRIMARY KEY,
    thesis_id           INTEGER NOT NULL,
    ticker              TEXT    NOT NULL,
    thesis_date         TEXT    NOT NULL,
    direction           TEXT,
    conviction          INTEGER,
    time_horizon        TEXT,
    -- reference prices from Claude
    entry_price         REAL,
    target_1            REAL,
    target_2            REAL,
    stop_loss           REAL,
    -- price snapshots (closing price N calendar days after thesis_date)
    price_1d            REAL,
    price_7d            REAL,
    price_14d           REAL,
    price_30d           REAL,
    -- return % vs entry_price at each snapshot
    return_1d           REAL,
    return_7d           REAL,
    return_14d          REAL,
    return_30d          REAL,
    -- gap between Claude target and actual price at resolution/30d
    vs_target_1_pct     REAL,
    vs_target_2_pct     REAL,
    vs_stop_pct         REAL,
    -- hit flags (checked using OHLC highs/lows)
    hit_target_1        BOOLEAN DEFAULT FALSE,
    hit_target_2        BOOLEAN DEFAULT FALSE,
    hit_stop            BOOLEAN DEFAULT FALSE,
    -- days from thesis_date to first hit
    days_to_target_1    INTEGER,
    days_to_target_2    INTEGER,
    days_to_stop        INTEGER,
    -- outcome
    outcome             TEXT,
    claude_correct      INTEGER,
    -- trade linkage
    was_traded          BOOLEAN DEFAULT FALSE,
    trade_id            INTEGER,
    -- metadata
    last_checked        TEXT,
    resolved_at         TEXT,
    created_at          TEXT,
    -- added via migrations 018-022
    return_attribution  JSONB,
    attribution_model   TEXT,
    attribution_run_id  TEXT,
    signal_scores_snapshot JSONB,
    UNIQUE(thesis_id)
);

ALTER TABLE thesis_outcomes ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'thesis_outcomes'
          AND policyname = 'thesis_outcomes_open_access'
    ) THEN
        CREATE POLICY thesis_outcomes_open_access ON thesis_outcomes
            USING (true) WITH CHECK (true);
    END IF;
END
$$;
