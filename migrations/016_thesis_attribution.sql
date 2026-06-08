-- Migration 016: thesis attribution columns
-- Persists candidate_lane, sources, broad_source_only onto thesis_cache so that
-- outcome attribution (win rate by source/lane) can be computed without requiring
-- a JOIN to research_lane_candidates.
-- Idempotent: uses ALTER TABLE ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE thesis_cache
    ADD COLUMN IF NOT EXISTS candidate_lane    TEXT,
    ADD COLUMN IF NOT EXISTS sources           JSONB,
    ADD COLUMN IF NOT EXISTS broad_source_only BOOLEAN;

COMMENT ON COLUMN thesis_cache.candidate_lane IS
    'Lane assigned by ticker_selector at AI-selection time: execution_core, execution_high_beta, research_broad, lane_excluded, hard_excluded.';

COMMENT ON COLUMN thesis_cache.sources IS
    'JSON array of universe source names (e.g. ["sp500","russell1000"]) at thesis-issuance time.';

COMMENT ON COLUMN thesis_cache.broad_source_only IS
    'TRUE if all sources are in the broad-research set (nasdaq_broad, nyse_listed) — no quality-index membership.';
