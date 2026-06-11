-- ============================================================================
-- Migration 010: Option Feature Store  (TRD-050)
-- Persists the full deterministic decision-stack context for every option
-- recommendation so cohort calibration and future ML tuning have a clean,
-- structured feature set to work from.
--
-- Adds to option_candidate_snapshots:
--   1. TRD-049 live-entry guardrail fields (8 columns)
--   2. TRD-047 scenario engine compact summary  (1 JSONB column)
--   3. Thesis enrichment not previously captured  (5 columns)
--   4. Algorithm / engine versioning metadata  (5 columns)
--   5. Per-contract quote timestamp (1 column)
--
-- All columns are nullable — existing rows are unaffected.
-- Safe to re-run (ADD COLUMN IF NOT EXISTS is idempotent on Postgres).
-- ============================================================================

-- ─── 1. Live-entry guardrail fields (TRD-049) ────────────────────────────────

ALTER TABLE option_candidate_snapshots
    ADD COLUMN IF NOT EXISTS entry_action           TEXT,   -- enter_now | reduce_size | enter_if_repriced | skip_for_now
    ADD COLUMN IF NOT EXISTS quote_freshness_label  TEXT,   -- live | recent | stale | unknown
    ADD COLUMN IF NOT EXISTS quote_age_seconds      REAL,   -- seconds between quote and recommendation
    ADD COLUMN IF NOT EXISTS fair_value_entry_low   REAL,
    ADD COLUMN IF NOT EXISTS fair_value_entry_high  REAL,
    ADD COLUMN IF NOT EXISTS entry_overpay_pct      REAL,   -- % above fair_value_entry_high at entry
    ADD COLUMN IF NOT EXISTS market_quality_label   TEXT,   -- tight | acceptable | wide | very_wide | one_sided
    ADD COLUMN IF NOT EXISTS live_guardrail_reason  TEXT;   -- human-readable guardrail explanation

-- Index: block-rate and entry-quality analytics
CREATE INDEX IF NOT EXISTS idx_ocs_entry_action
    ON option_candidate_snapshots (entry_action)
    WHERE NOT suppressed;

CREATE INDEX IF NOT EXISTS idx_ocs_quote_freshness
    ON option_candidate_snapshots (quote_freshness_label)
    WHERE NOT suppressed;

CREATE INDEX IF NOT EXISTS idx_ocs_market_quality
    ON option_candidate_snapshots (market_quality_label)
    WHERE NOT suppressed;

-- ─── 2. Scenario engine compact summary (TRD-047) ───────────────────────────

ALTER TABLE option_candidate_snapshots
    ADD COLUMN IF NOT EXISTS scenarios_json  JSONB;
-- Stores compact scenario array, e.g.:
--   [{"id":"fast_target","ret_pct":55.2,"days":7,"method":"delta_approx","price":3.25}, ...]
-- Intentionally compact; full reconstruction available via scenario engine.

-- ─── 3. Thesis enrichment fields ─────────────────────────────────────────────
-- Entry zone and catalyst timing from the linked thesis row.
-- underlying_target_1/2 and underlying_stop already exist from TRD-026.

ALTER TABLE option_candidate_snapshots
    ADD COLUMN IF NOT EXISTS thesis_entry_low       REAL,   -- thesis entry zone lower bound
    ADD COLUMN IF NOT EXISTS thesis_entry_high      REAL,   -- thesis entry zone upper bound
    ADD COLUMN IF NOT EXISTS days_to_earnings       INTEGER,-- calendar days to next earnings at rec time
    ADD COLUMN IF NOT EXISTS heat_score             REAL,   -- signal heat score at rec time (0–1)
    ADD COLUMN IF NOT EXISTS expected_move_pct      REAL;   -- expected move % (from options market)

-- Index: earnings proximity bucket (useful for calibration)
CREATE INDEX IF NOT EXISTS idx_ocs_days_to_earnings
    ON option_candidate_snapshots (days_to_earnings)
    WHERE days_to_earnings IS NOT NULL AND NOT suppressed;

-- ─── 4. Per-contract quote timestamp ─────────────────────────────────────────
-- IBKR provides a per-contract quote time; yfinance rows will be NULL here.
-- Complement to quote_freshness_label for precise audit/replay.

ALTER TABLE option_candidate_snapshots
    ADD COLUMN IF NOT EXISTS quote_time  TEXT;   -- ISO-8601 UTC; NULL for yfinance

-- ─── 5. Algorithm / engine versioning (lineage) ──────────────────────────────
-- Static version strings written at persistence time.  Allows future cohort
-- splits by algorithm generation without relying on created_at ranges.

ALTER TABLE option_candidate_snapshots
    ADD COLUMN IF NOT EXISTS algo_version            TEXT,   -- overall recommendation algo version
    ADD COLUMN IF NOT EXISTS target_engine_version   TEXT,   -- v2 target projection engine version
    ADD COLUMN IF NOT EXISTS scenario_engine_version TEXT,   -- scenario path-analysis engine version
    ADD COLUMN IF NOT EXISTS risk_framework_version  TEXT,   -- PM/risk sizing framework version
    ADD COLUMN IF NOT EXISTS guardrail_version       TEXT;   -- live-entry guardrail version

-- Composite index: cohort analytics by algo_version
CREATE INDEX IF NOT EXISTS idx_ocs_algo_version
    ON option_candidate_snapshots (algo_version)
    WHERE NOT suppressed;
