-- migration 020: add pipeline_run_id to thesis_cache
-- Lets the Telegram notifier filter theses by the current pipeline run,
-- excluding same-day stale-refresh rows from earlier runs.
ALTER TABLE thesis_cache ADD COLUMN IF NOT EXISTS pipeline_run_id TEXT;
CREATE INDEX IF NOT EXISTS idx_thesis_cache_pipeline_run_id
    ON thesis_cache (pipeline_run_id)
    WHERE pipeline_run_id IS NOT NULL;
