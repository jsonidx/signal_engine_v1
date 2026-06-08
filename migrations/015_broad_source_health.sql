-- Migration 015: broad source health column
-- Adds a JSONB column to funnel_metrics for per-run broad-source fetch health metadata.
-- Idempotent: uses ALTER TABLE ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE funnel_metrics
    ADD COLUMN IF NOT EXISTS broad_source_health JSONB;

COMMENT ON COLUMN funnel_metrics.broad_source_health IS
    'Per-source health snapshot for broad FTP sources (nasdaq_broad, nyse_listed).
     Keys are source names; each value has: fetch_mode, raw_rows, eligible_count, warning, fetched_at.
     fetch_mode: "live_fetch" | "fresh_cache" | "stale_cache" | "empty_fallback"';
