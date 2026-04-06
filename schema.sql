-- =============================================================================
-- Signal Engine v1 — Supabase PostgreSQL Schema
-- =============================================================================
-- All persistent data (user trades, AI cache, config) lives here.
-- Local SQLite is no longer used for any cache.
--
-- To recreate from scratch in a new Supabase project:
--   1. Open Supabase SQL Editor
--   2. Run this file in full
--   3. Run scripts/seed_supabase.py to populate user_watchlists + strategy_config
--
-- Tables:
--   trades              — real buy/sell journal entries
--   trade_returns       — realised P&L per closed trade
--   snapshots           — weekly paper-trading snapshots
--   equity_positions    — per-snapshot equity holdings
--   weekly_returns      — per-snapshot weekly P&L vs SPY
--   portfolio_settings  — key/value store for portfolio parameters
--   thesis_cache        — Claude AI quant theses (global, shared by date)
--   transcript_cache    — earnings call analyses (global, 7-day TTL)
--   iv_history          — daily ATM IV per ticker (replaces iv_history.db)
--   user_watchlists     — tickers with category (equity/crypto/watched)
--   strategy_config     — key/value strategy parameters (module weights, etc.)
--   thesis_outcomes     — post-hoc performance of Claude theses
--   resolution_cache    — daily conflict-resolver output per ticker
--   blacklist           — ticker exclusions with reason + optional TTL
--   ticker_metadata     — IPO/delist dates, sector; eliminates per-ticker yf calls
--   fundamentals        — quarterly fundamental data cache (30-day TTL)
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. Trade Journal
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS trades (
    id          SERIAL PRIMARY KEY,
    ticker      TEXT        NOT NULL,
    action      TEXT        NOT NULL CHECK (action IN ('BUY', 'SELL')),
    price       FLOAT,
    size_eur    FLOAT,
    shares      FLOAT,
    date        TEXT        NOT NULL,
    status      TEXT        DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    created_at  TIMESTAMP   DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trade_returns (
    id          SERIAL PRIMARY KEY,
    ticker      TEXT,
    entry_date  TEXT,
    exit_date   TEXT,
    entry_price FLOAT,
    exit_price  FLOAT,
    shares      FLOAT,
    pnl_eur     FLOAT,
    return_pct  FLOAT,
    created_at  TIMESTAMP   DEFAULT NOW()
);


-- ---------------------------------------------------------------------------
-- 2. Paper Trader
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS snapshots (
    id                  SERIAL PRIMARY KEY,
    date                TEXT        NOT NULL UNIQUE,
    created_at          TIMESTAMP   DEFAULT NOW(),
    portfolio_nav       FLOAT,
    equity_allocation   FLOAT,
    crypto_allocation   FLOAT,
    cash_allocation     FLOAT,
    spy_price           FLOAT,
    btc_price           FLOAT,
    btc_ma200           FLOAT,
    btc_signal          TEXT
);

CREATE TABLE IF NOT EXISTS equity_positions (
    id              SERIAL PRIMARY KEY,
    snapshot_id     INTEGER     REFERENCES snapshots(id) ON DELETE CASCADE,
    ticker          TEXT        NOT NULL,
    rank            INTEGER,
    composite_z     FLOAT,
    weight_pct      FLOAT,
    position_eur    FLOAT,
    entry_price     FLOAT,
    transaction_cost_eur FLOAT
);

CREATE TABLE IF NOT EXISTS weekly_returns (
    id                  SERIAL PRIMARY KEY,
    snapshot_id         INTEGER     REFERENCES snapshots(id) ON DELETE CASCADE,
    week_ending         TEXT,
    portfolio_return    FLOAT,
    benchmark_return    FLOAT,
    equity_return       FLOAT,
    crypto_return       FLOAT,
    btc_return          FLOAT
);

CREATE TABLE IF NOT EXISTS portfolio_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TIMESTAMP DEFAULT NOW()
);


-- ---------------------------------------------------------------------------
-- 3. AI Quant Cache (global — shared across all users for same ticker+date)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS thesis_cache (
    ticker                  TEXT        NOT NULL,
    date                    TEXT        NOT NULL,
    direction               TEXT,
    conviction              INTEGER,
    time_horizon            TEXT,
    entry_low               FLOAT,
    entry_high              FLOAT,
    stop_loss               FLOAT,
    target_1                FLOAT,
    target_2                FLOAT,
    position_size_pct       FLOAT,
    thesis                  TEXT,
    data_quality            TEXT,
    notes                   TEXT,
    catalysts_json          JSONB,
    risks_json              JSONB,
    raw_response            TEXT,
    signals_json            JSONB,
    created_at              TIMESTAMP,
    bull_probability        FLOAT,
    bear_probability        FLOAT,
    neutral_probability     FLOAT,
    signal_agreement_score  FLOAT,
    key_invalidation        TEXT,
    primary_scenario        TEXT,
    bear_scenario           TEXT,
    expected_moves_json     JSONB,
    PRIMARY KEY (ticker, date)
);


-- ---------------------------------------------------------------------------
-- 4. Earnings Transcript Cache (global — 7-day TTL)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS transcript_cache (
    ticker              TEXT        NOT NULL,
    filing_date         TEXT        NOT NULL,
    analysis_json       JSONB,
    transcript_snippet  TEXT,
    created_at          TIMESTAMP,
    PRIMARY KEY (ticker, filing_date)
);


-- ---------------------------------------------------------------------------
-- 5. IV History (replaces data/iv_history.db SQLite)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS iv_history (
    ticker      TEXT    NOT NULL,
    date        TEXT    NOT NULL,
    iv30        FLOAT,
    atm_strike  FLOAT,
    near_expiry TEXT,
    far_expiry  TEXT,
    computed_at TIMESTAMP,
    PRIMARY KEY (ticker, date)
);


-- ---------------------------------------------------------------------------
-- 6. User Watchlists & Strategy Config (multi-user product config)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_watchlists (
    id          SERIAL PRIMARY KEY,
    ticker      TEXT    NOT NULL,
    category    TEXT    DEFAULT 'equity',   -- equity | crypto | watched
    added_at    TIMESTAMP DEFAULT NOW(),
    UNIQUE (ticker, category)
);

CREATE TABLE IF NOT EXISTS user_favorites (
    id        SERIAL PRIMARY KEY,
    symbol    TEXT NOT NULL UNIQUE,
    added_at  TIMESTAMPTZ DEFAULT NOW(),
    notes     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS strategy_config (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TIMESTAMP DEFAULT NOW()
);


-- ---------------------------------------------------------------------------
-- 7. Thesis Outcomes — post-hoc performance tracking
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS thesis_outcomes (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT,
    thesis_date     TEXT,
    direction       TEXT,
    conviction      INTEGER,
    target_1        FLOAT,
    stop_loss       FLOAT,
    entry_price     FLOAT,
    outcome_price   FLOAT,
    outcome_date    TEXT,
    outcome         TEXT,       -- HIT_TARGET | HIT_STOP | EXPIRED | OPEN
    pnl_pct         FLOAT,
    days_held       INTEGER,
    created_at      TIMESTAMP DEFAULT NOW()
);


-- ---------------------------------------------------------------------------
-- 8. Resolution Cache — conflict resolver daily output (global)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS resolution_cache (
    ticker                  TEXT    NOT NULL,
    date                    TEXT    NOT NULL,
    regime                  TEXT,
    pre_resolved_direction  TEXT,
    confidence              FLOAT,
    signal_agreement_score  FLOAT,
    override_flags          JSONB,
    module_votes            JSONB,
    bull_weight             FLOAT,
    bear_weight             FLOAT,
    skip_claude             BOOLEAN,
    max_conviction_override INTEGER,
    position_size_override  FLOAT,
    created_at              TIMESTAMP,
    PRIMARY KEY (ticker, date)
);


-- ---------------------------------------------------------------------------
-- 9. Blacklist — pipeline-wide ticker exclusions (Phase 1 cache layer)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS blacklist (
    ticker      TEXT        PRIMARY KEY,
    reason      TEXT        NOT NULL,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ             -- NULL = permanent exclusion
);

CREATE INDEX IF NOT EXISTS idx_blacklist_expires_at
    ON blacklist (expires_at)
    WHERE expires_at IS NOT NULL;


-- ---------------------------------------------------------------------------
-- 10. Ticker Metadata — IPO dates, delist status, sector/industry
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ticker_metadata (
    ticker          TEXT        PRIMARY KEY,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    ipo_date        DATE,
    delisted_date   DATE,
    status          TEXT        NOT NULL DEFAULT 'unknown'
                                CHECK (status IN ('active','delisted','suspect','unknown')),
    sector          TEXT,
    industry        TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ticker_metadata_ipo_date
    ON ticker_metadata (ipo_date)
    WHERE ipo_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ticker_metadata_status
    ON ticker_metadata (status);


-- ---------------------------------------------------------------------------
-- 11. Fundamentals Cache — quarterly data, 30-day TTL (was local SQLite)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fundamentals (
    ticker      TEXT        PRIMARY KEY,
    data_json   TEXT        NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_fetched_at
    ON fundamentals (fetched_at);


-- =============================================================================
-- ROW LEVEL SECURITY (RLS)
-- =============================================================================
-- All tables are protected. The Python backend connects via the postgres
-- superuser (DATABASE_URL) which bypasses RLS — no code changes needed.
--
-- Security model:
--   anon role        → DENIED on all tables (no policy = default deny)
--   authenticated    → ALLOWED on all tables (Phase 5 tightens user-specific
--                      tables to: USING (auth.uid() = user_id))
--   postgres/service → Bypasses RLS entirely (backend is safe)
--
-- Phase 5 migration: change the user-specific table policies to:
--   USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id)
-- =============================================================================

-- User-specific tables (will become per-user in Phase 5)
ALTER TABLE trades             ENABLE ROW LEVEL SECURITY;
ALTER TABLE trade_returns      ENABLE ROW LEVEL SECURITY;
ALTER TABLE snapshots          ENABLE ROW LEVEL SECURITY;
ALTER TABLE equity_positions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE weekly_returns     ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_watchlists    ENABLE ROW LEVEL SECURITY;

-- Global shared cache tables
ALTER TABLE thesis_cache       ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcript_cache   ENABLE ROW LEVEL SECURITY;
ALTER TABLE iv_history         ENABLE ROW LEVEL SECURITY;
ALTER TABLE resolution_cache   ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_config    ENABLE ROW LEVEL SECURITY;
ALTER TABLE thesis_outcomes    ENABLE ROW LEVEL SECURITY;
ALTER TABLE blacklist          ENABLE ROW LEVEL SECURITY;
ALTER TABLE ticker_metadata    ENABLE ROW LEVEL SECURITY;
ALTER TABLE fundamentals       ENABLE ROW LEVEL SECURITY;

-- Policies: authenticated users can access all tables
-- (replace USING (true) with USING (auth.uid() = user_id) in Phase 5 for user tables)
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'trades','trade_returns','snapshots','equity_positions',
        'weekly_returns','portfolio_settings','user_watchlists',
        'thesis_cache','transcript_cache','iv_history',
        'resolution_cache','strategy_config','thesis_outcomes',
        'blacklist','ticker_metadata','fundamentals'
    ] LOOP
        EXECUTE format(
            'DROP POLICY IF EXISTS "authenticated_full_access" ON %I;
             CREATE POLICY "authenticated_full_access" ON %I
                 FOR ALL TO authenticated
                 USING (true) WITH CHECK (true);',
            tbl, tbl
        );
    END LOOP;
END $$;

-- user_id columns on user-specific tables (populated by auth.uid() in Phase 5)
ALTER TABLE trades             ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE trade_returns      ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE snapshots          ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE equity_positions   ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE weekly_returns     ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE portfolio_settings ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE user_watchlists    ADD COLUMN IF NOT EXISTS user_id UUID;
