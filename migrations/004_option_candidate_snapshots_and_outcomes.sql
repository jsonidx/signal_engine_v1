-- ============================================================================
-- Migration 004: Option Candidate Snapshots and Outcomes
-- TRD-026 (persistence) + TRD-027 (outcomes)
-- ============================================================================

-- ---------------------------------------------------------------------------
-- option_candidate_snapshots
-- Stores every generated option recommendation (candidates + suppressed states)
-- with full thesis context, contract fields, scoring, and exit-plan fields.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS option_candidate_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_date            DATE        NOT NULL,

    -- Thesis link
    ticker              TEXT        NOT NULL,
    thesis_id           BIGINT,                                 -- soft link; thesis_cache has composite PK (ticker, date), no surrogate id
    thesis_date         DATE,
    direction           TEXT,           -- BULL | BEAR | NEUTRAL
    conviction          INTEGER,
    time_horizon        TEXT,
    signal_agreement    REAL,

    -- Chain metadata
    chain_source        TEXT,           -- ibkr | yfinance | mock
    underlying_price    REAL,

    -- Contract identity (NULL for suppressed rows)
    strategy_preset     TEXT,           -- long_call | long_put | leaps_call | leaps_put
    rank                INTEGER,        -- 1-3 within this run; NULL for suppressed
    expiry              DATE,
    dte                 INTEGER,
    strike              REAL,
    contract_right      CHAR(1),        -- C | P  (renamed: 'right' is a SQL reserved keyword)

    -- Quotes
    bid                 REAL,
    ask                 REAL,
    mid                 REAL,
    spread_pct          REAL,

    -- Greeks
    delta               REAL,
    gamma               REAL,
    theta               REAL,
    vega                REAL,
    iv                  REAL,           -- decimal (0.35 = 35%)

    -- Liquidity
    open_interest       INTEGER,
    volume              INTEGER,
    breakeven           REAL,

    -- Exit plan (mandatory fields per product spec)
    holding_window_days INTEGER,
    exit_by_date        DATE,
    underlying_target_1 REAL,
    underlying_target_2 REAL,
    underlying_stop     REAL,
    option_take_profit_1 REAL,
    option_take_profit_2 REAL,
    option_stop_loss    REAL,
    max_holding_rule    TEXT,
    event_exit_rule     TEXT,

    -- Execution guidance (TRD-031)
    recommended_entry_price  REAL,
    recommended_order_type   TEXT,      -- 'limit' (always for options)
    max_chase_price          REAL,
    entry_style              TEXT,      -- 'passive' | 'balanced' | 'aggressive'
    entry_rationale          TEXT,
    fill_quality_score       REAL,      -- 0.0–1.0
    slippage_risk_label      TEXT,      -- 'low' | 'moderate' | 'high' | 'very_high'
    skip_if_spread_above_pct REAL,

    -- Scoring
    score               REAL,
    rationale           TEXT,
    features_json       JSONB,          -- raw contract features for later ML use

    -- State
    suppressed          BOOLEAN NOT NULL DEFAULT FALSE,
    suppression_reason  TEXT,

    -- Rejection reasons for candidates that didn't make the cut
    rejection_reasons_json JSONB
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_ocs_ticker_date
    ON option_candidate_snapshots (ticker, run_date DESC);
CREATE INDEX IF NOT EXISTS idx_ocs_run_date
    ON option_candidate_snapshots (run_date DESC);
CREATE INDEX IF NOT EXISTS idx_ocs_strategy_preset
    ON option_candidate_snapshots (strategy_preset)
    WHERE NOT suppressed;
CREATE INDEX IF NOT EXISTS idx_ocs_suppressed
    ON option_candidate_snapshots (suppressed, run_date DESC);


-- ---------------------------------------------------------------------------
-- option_candidate_outcomes
-- Realized outcome for each persisted recommendation snapshot.
-- Populated by the resolution job after 1d / 5d / 10d windows and at expiry.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS option_candidate_outcomes (
    id                      BIGSERIAL PRIMARY KEY,
    candidate_snapshot_id   BIGINT NOT NULL REFERENCES option_candidate_snapshots(id) ON DELETE CASCADE,
    resolved_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolution_type         TEXT NOT NULL, -- '1d' | '5d' | '10d' | 'expiry' | 'manual'

    -- Underlying price at resolution windows
    underlying_close_1d     REAL,
    underlying_close_5d     REAL,
    underlying_close_10d    REAL,
    underlying_close_expiry REAL,

    -- Underlying returns
    underlying_return_1d_pct    REAL,
    underlying_return_5d_pct    REAL,
    underlying_return_10d_pct   REAL,
    underlying_return_expiry_pct REAL,

    -- Option marks (approximated via delta when live marks unavailable)
    option_mid_1d           REAL,
    option_mid_5d           REAL,
    option_mid_10d          REAL,

    -- Option returns
    option_return_1d_pct    REAL,
    option_return_5d_pct    REAL,
    option_return_10d_pct   REAL,

    -- Exit tracking
    days_held_to_exit       INTEGER,
    exit_reason             TEXT,       -- 'tp1' | 'tp2' | 'stop' | 'expiry' | 'max_hold' | 'event'

    -- Target/stop hit markers
    hit_option_tp1          BOOLEAN,
    hit_option_tp2          BOOLEAN,
    hit_option_stop         BOOLEAN,
    hit_underlying_t1       BOOLEAN,
    hit_underlying_t2       BOOLEAN,
    hit_underlying_stop     BOOLEAN,

    -- Performance extremes within window
    max_runup_pct           REAL,
    max_drawdown_pct        REAL,

    -- Summary
    hit_target              BOOLEAN,    -- did the trade reach any profit target?
    expired_itm             BOOLEAN,
    notes                   TEXT,

    UNIQUE (candidate_snapshot_id, resolution_type)
);

CREATE INDEX IF NOT EXISTS idx_oco_snapshot_id
    ON option_candidate_outcomes (candidate_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_oco_resolved_at
    ON option_candidate_outcomes (resolved_at DESC);

-- ---------------------------------------------------------------------------
-- RLS: apply same policy as migration 002 for the new tables created above.
-- Deny anon; grant authenticated full access.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'option_candidate_snapshots',
        'option_candidate_outcomes'
    ] LOOP
        IF to_regclass(format('public.%I', tbl)) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
            EXECUTE format('DROP POLICY IF EXISTS authenticated_full_access ON public.%I', tbl);
            EXECUTE format(
                'CREATE POLICY authenticated_full_access ON public.%I
                 FOR ALL TO authenticated
                 USING (true) WITH CHECK (true)',
                tbl
            );
        END IF;
    END LOOP;
END $$;
