-- Migration 014: source/lane attribution columns
-- Adds source and lane attribution metadata to research_lane_candidates and funnel_metrics.
-- Idempotent: all changes use ALTER TABLE ... ADD COLUMN IF NOT EXISTS.

-- ── research_lane_candidates ─────────────────────────────────────────────────
ALTER TABLE research_lane_candidates
    ADD COLUMN IF NOT EXISTS sources          JSONB,
    ADD COLUMN IF NOT EXISTS broad_source_only BOOLEAN;

COMMENT ON COLUMN research_lane_candidates.sources IS
    'Source index labels that contributed this ticker, e.g. ["sp500","nasdaq_broad"]';
COMMENT ON COLUMN research_lane_candidates.broad_source_only IS
    'TRUE when the ticker''s entire source set is within _BROAD_RESEARCH_SOURCES (e.g. nasdaq_broad-only)';

-- ── funnel_metrics ────────────────────────────────────────────────────────────
ALTER TABLE funnel_metrics
    ADD COLUMN IF NOT EXISTS candidates_by_lane          JSONB,
    ADD COLUMN IF NOT EXISTS candidates_by_source        JSONB,
    ADD COLUMN IF NOT EXISTS broad_source_only_candidates INTEGER,
    ADD COLUMN IF NOT EXISTS ai_selected_by_lane         JSONB,
    ADD COLUMN IF NOT EXISTS ai_selected_by_source       JSONB,
    ADD COLUMN IF NOT EXISTS broad_source_only_ai_selected INTEGER;

COMMENT ON COLUMN funnel_metrics.candidates_by_lane IS
    'Lane breakdown of prescreened candidates, e.g. {"execution_core":40,"research_broad":120}';
COMMENT ON COLUMN funnel_metrics.candidates_by_source IS
    'Source contribution counts for prescreened candidates (a ticker with N sources contributes to N buckets)';
COMMENT ON COLUMN funnel_metrics.broad_source_only_candidates IS
    'Number of prescreened candidates whose entire source set is broad-research-only';
COMMENT ON COLUMN funnel_metrics.ai_selected_by_lane IS
    'Lane breakdown of AI-selected tickers';
COMMENT ON COLUMN funnel_metrics.ai_selected_by_source IS
    'Source contribution counts for AI-selected tickers';
COMMENT ON COLUMN funnel_metrics.broad_source_only_ai_selected IS
    'Number of AI-selected tickers that were broad-source-only';
