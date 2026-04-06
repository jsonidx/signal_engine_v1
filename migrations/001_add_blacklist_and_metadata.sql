-- =============================================================================
-- Migration 001 — Blacklist + Ticker Metadata + Fundamentals Cache
-- =============================================================================
-- Run this in the Supabase SQL Editor (or via psql) against your project DB.
--
-- What this adds:
--   blacklist         — permanent/temporary ticker exclusions with reason + TTL
--   ticker_metadata   — IPO/delist dates, sector, status; eliminates per-ticker
--                       yf.Ticker().info calls in the backtest universe filter
--   fundamentals      — quarterly fundamental data cache (migrated from local
--                       SQLite fundamentals_cache.db; same schema, same 30d TTL)
--
-- To apply:
--   1. Open Supabase SQL Editor → paste and run this file, OR
--   2. psql "$DATABASE_URL" -f migrations/001_add_blacklist_and_metadata.sql
--
-- Safe to re-run: all statements use IF NOT EXISTS / DO NOTHING.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. Blacklist — tickers excluded from all pipeline steps
-- ---------------------------------------------------------------------------
-- expires_at NULL  → permanent (confirmed delisted, reverse-split junk, etc.)
-- expires_at set   → temporary (transient data failure, re-evaluated after TTL)
--
-- Checked at the start of universe_builder._apply_liquidity_filter() so
-- blacklisted tickers skip the yfinance download entirely.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS blacklist (
    ticker      TEXT        PRIMARY KEY,
    reason      TEXT        NOT NULL,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ             -- NULL = permanent exclusion
);

-- Fast lookup: "give me all active (non-expired) entries"
CREATE INDEX IF NOT EXISTS idx_blacklist_expires_at
    ON blacklist (expires_at)
    WHERE expires_at IS NOT NULL;


-- ---------------------------------------------------------------------------
-- 2. Ticker Metadata — IPO dates, status, sector/industry
-- ---------------------------------------------------------------------------
-- Primary consumer: backtest._get_ipo_date() — fetches all known IPO dates in
-- one SELECT instead of 185 individual yf.Ticker().info calls per run.
--
-- status values:
--   active    — currently trading, passes liquidity filter
--   delisted  — confirmed delist; also add to blacklist (permanent)
--   suspect   — returned no yfinance data ≥2 times; monitor before blacklisting
--   unknown   — default; hasn't been evaluated yet
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ticker_metadata (
    ticker          TEXT        PRIMARY KEY,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    ipo_date        DATE,           -- firstTradingDay from yfinance
    delisted_date   DATE,           -- set when status → delisted
    status          TEXT        NOT NULL DEFAULT 'unknown'
                                CHECK (status IN ('active','delisted','suspect','unknown')),
    sector          TEXT,
    industry        TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Backtest uses ipo_date in a range filter; partial index covers the common case
CREATE INDEX IF NOT EXISTS idx_ticker_metadata_ipo_date
    ON ticker_metadata (ipo_date)
    WHERE ipo_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ticker_metadata_status
    ON ticker_metadata (status);


-- ---------------------------------------------------------------------------
-- 3. Fundamentals Cache — migrated from local SQLite fundamentals_cache.db
-- ---------------------------------------------------------------------------
-- Quarterly fundamental data (PE, margins, revenue growth, analyst ratings).
-- Default TTL: 30 days (same as the old SQLite cache).
-- fundamentals_cache.py now reads/writes this table via get_connection().
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fundamentals (
    ticker      TEXT        PRIMARY KEY,
    data_json   TEXT        NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- TTL queries filter by fetched_at; index makes expiry checks fast
CREATE INDEX IF NOT EXISTS idx_fundamentals_fetched_at
    ON fundamentals (fetched_at);


-- ---------------------------------------------------------------------------
-- 4. Row Level Security (consistent with schema.sql pattern)
-- ---------------------------------------------------------------------------

ALTER TABLE blacklist          ENABLE ROW LEVEL SECURITY;
ALTER TABLE ticker_metadata    ENABLE ROW LEVEL SECURITY;
ALTER TABLE fundamentals       ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['blacklist', 'ticker_metadata', 'fundamentals'] LOOP
        EXECUTE format(
            'DROP POLICY IF EXISTS "authenticated_full_access" ON %I;
             CREATE POLICY "authenticated_full_access" ON %I
                 FOR ALL TO authenticated
                 USING (true) WITH CHECK (true);',
            tbl, tbl
        );
    END LOOP;
END $$;


-- ---------------------------------------------------------------------------
-- 5. Seed: migrate any existing liquidity_failed.log entries
-- ---------------------------------------------------------------------------
-- Run this from Python instead:
--   python3 -c "from db_cache import migrate_liquidity_failed_log; migrate_liquidity_failed_log()"
-- ---------------------------------------------------------------------------
