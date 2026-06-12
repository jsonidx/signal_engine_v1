-- migration 021: hedge fund 13F position tracker (TRD-083)
-- Stores quarterly 13F snapshots for tracked funds with Q-o-Q diff columns.
-- Safe to re-run (CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS hedge_fund_positions (
    id              BIGSERIAL PRIMARY KEY,
    fund_slug       TEXT        NOT NULL,
    fund_name       TEXT        NOT NULL,
    cik             TEXT        NOT NULL,
    period          DATE        NOT NULL,   -- quarter end: 2026-03-31
    filed_at        DATE,                   -- SEC filing date
    ticker          TEXT,                   -- NULL when CUSIP unresolvable
    cusip           TEXT,
    name_of_issuer  TEXT,
    shares          BIGINT,
    value_usd       BIGINT,                 -- in thousands as filed (multiply by 1000 for USD)
    put_call        TEXT,                   -- NULL | 'Put' | 'Call'
    change_type     TEXT,                   -- new | added | trimmed | closed | unchanged
    shares_delta    BIGINT,                 -- shares vs prior quarter (NULL on first ingestion)
    value_delta_usd BIGINT,                 -- value_usd vs prior quarter (NULL on first ingestion)
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (fund_slug, cusip, period, put_call)
);

CREATE INDEX IF NOT EXISTS idx_hfp_fund_period
    ON hedge_fund_positions (fund_slug, period DESC);

CREATE INDEX IF NOT EXISTS idx_hfp_ticker
    ON hedge_fund_positions (ticker)
    WHERE ticker IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_hfp_change_type
    ON hedge_fund_positions (fund_slug, change_type, period DESC);
