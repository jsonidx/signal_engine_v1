-- Migration 017: issuance-time governance state on thesis_cache
-- Captures the ticker's governance state (A_LIST/STANDARD/PROBATION/QUARANTINE)
-- at the moment a thesis is issued, enabling outcome attribution by governance state
-- independently of whatever the ticker's live state is today.
-- Idempotent: uses ALTER TABLE ... ADD COLUMN IF NOT EXISTS.

ALTER TABLE thesis_cache
    ADD COLUMN IF NOT EXISTS governance_state TEXT;

COMMENT ON COLUMN thesis_cache.governance_state IS
    'Ticker governance state at thesis-issuance time (A_LIST, STANDARD, PROBATION, QUARANTINE).
     NULL for historical rows that predate this migration — treated as "unknown" in analytics.
     Distinct from issuance_state, which describes the thesis verdict (ACTIVE_THESIS etc.).';
