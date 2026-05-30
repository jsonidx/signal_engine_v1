-- =============================================================================
-- 002_enable_rls_on_public_tables.sql
-- =============================================================================
-- Purpose:
--   1. Remediate Supabase `rls_disabled_in_public` findings on existing tables
--   2. Deny anon access across all known application-managed public tables
--   3. Preserve current app behavior by granting `authenticated` full access
--
-- Run this in Supabase SQL Editor against each affected project.
-- =============================================================================

DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'trades',
        'trade_returns',
        'snapshots',
        'equity_positions',
        'weekly_returns',
        'portfolio_settings',
        'user_watchlists',
        'user_favorites',
        'thesis_cache',
        'transcript_cache',
        'iv_history',
        'strategy_config',
        'thesis_outcomes',
        'resolution_cache',
        'blacklist',
        'ticker_metadata',
        'fundamentals',
        'catalyst_scores',
        'regime_snapshots',
        'dark_pool_snapshots',
        'screener_signals',
        'backtest_runs',
        'squeeze_scores',
        'short_interest_history',
        'filing_catalysts',
        'red_flag_scores',
        'fundamental_scores',
        'catalyst_history',
        'pipeline_reports'
    ] LOOP
        IF to_regclass(format('public.%I', tbl)) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
            EXECUTE format('DROP POLICY IF EXISTS authenticated_full_access ON public.%I', tbl);
            EXECUTE format(
                'CREATE POLICY authenticated_full_access ON public.%I
                 FOR ALL TO authenticated
                 USING (true) WITH CHECK (true)',
                tbl
            );
        END IF;
    END LOOP;
END $$;
