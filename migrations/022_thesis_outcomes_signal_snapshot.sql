-- Migration 022: signal_scores_snapshot on thesis_outcomes
-- Stores a compact JSON snapshot of the module scores that were active at
-- thesis-issuance time. Enables outcome attribution analysis to correlate
-- results against the signal state at the moment the thesis was written,
-- rather than the live state today.
-- Idempotent: ADD COLUMN IF NOT EXISTS.

ALTER TABLE thesis_outcomes
    ADD COLUMN IF NOT EXISTS signal_scores_snapshot JSONB;

COMMENT ON COLUMN thesis_outcomes.signal_scores_snapshot IS
    'Compact signal score snapshot captured when the thesis_outcome row is first
     created. Keys: signal_agreement_score, prob_combined, prob_technical,
     prob_options, prob_catalyst, prob_news, fundamentals_pct, squeeze_score_100,
     options_heat, dark_pool_signal, dark_pool_zscore, sec_score, market_regime,
     market_regime_score, above_ma200, ticker_sector_regime.
     NULL for rows created before migration 022.';
