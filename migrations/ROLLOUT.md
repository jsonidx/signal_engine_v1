# Options Feature Rollout — TRD-021 through TRD-031

## Fresh environments

Apply migrations in order:

```
psql $DATABASE_URL -f migrations/004_option_candidate_snapshots_and_outcomes.sql
psql $DATABASE_URL -f migrations/005_option_execution_guidance.sql
```

Migration 004 creates `option_candidate_snapshots` and `option_candidate_outcomes` with RLS enabled.
Migration 005 adds 8 TRD-031 execution-guidance columns to `option_candidate_snapshots`. Both are idempotent.

## Environments that ran an older version of migration 004

If you ran migration 004 before 2026-05-30 it would have failed at the DDL level because the table
definition used `right CHAR(1)` — a reserved SQL keyword — causing a syntax error. The table was
therefore never created. Run both migrations fresh:

```
psql $DATABASE_URL -f migrations/004_option_candidate_snapshots_and_outcomes.sql
psql $DATABASE_URL -f migrations/005_option_execution_guidance.sql
```

If migration 004 did somehow succeed on a patched DB, run 005 to add the TRD-031 columns — it is
safe to re-run (`ADD COLUMN IF NOT EXISTS` throughout).

## RLS note

Both new tables have `authenticated_full_access` policy applied by migration 004. Anon access is
denied by default. No extra Supabase dashboard steps required.

---

# Screener / PM-Analytics Wave — TRD-055 through TRD-069

Shipped 2026-06-08. Migrations 011–017 add lane attribution, funnel metrics, ticker governance,
broad-source health, and thesis attribution columns.

**Workflows do not apply these migrations automatically.** Run them manually against your Supabase
instance before the next pipeline run.

## Apply order

All seven migrations are fully idempotent (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`,
DO-block guards on policies). Re-running a migration that already ran is safe.

```bash
# 011 and 012 must run first — 014 and 015 add columns to tables they create.
psql $DATABASE_URL -f migrations/011_lane_and_issuance.sql
psql $DATABASE_URL -f migrations/012_funnel_metrics.sql
psql $DATABASE_URL -f migrations/013_ticker_governance.sql
psql $DATABASE_URL -f migrations/014_source_lane_attribution.sql
psql $DATABASE_URL -f migrations/015_broad_source_health.sql
psql $DATABASE_URL -f migrations/016_thesis_attribution.sql
psql $DATABASE_URL -f migrations/017_thesis_governance_state.sql
```

### What each migration does

| Migration | Tables affected | Notes |
|---|---|---|
| 011 | `thesis_cache`, `candidate_snapshots`; creates `research_lane_candidates` | Adds `issuance_state`, `candidate_lane`; new lane table with RLS |
| 012 | Creates `funnel_metrics` | Run-level funnel counters with RLS |
| 013 | Creates `ticker_governance` | A-list / probation / quarantine state table with RLS |
| 014 | `research_lane_candidates`, `funnel_metrics` | Adds source attribution columns — requires 011 and 012 |
| 015 | `funnel_metrics` | Adds `broad_source_health` JSONB column — requires 012 |
| 016 | `thesis_cache` | Adds `candidate_lane`, `sources`, `broad_source_only` attribution columns |
| 017 | `thesis_cache` | Adds `governance_state` column |

### Dependency ordering

`014` adds columns to `research_lane_candidates` (created by `011`) and `funnel_metrics` (created
by `012`). Apply `011` and `012` before `014`. All other migrations are independent of each other
and can be applied in numeric order.

## Post-rollout verification checklist

Run the daily pipeline once after applying migrations, then check:

- [ ] `daily_rankings` still populates — confirm rows exist for today's date
- [ ] `thesis_cache` writes succeed — no missing-column errors in pipeline log
- [ ] `funnel_metrics` has a new row for today's run date (`SELECT * FROM funnel_metrics ORDER BY run_date DESC LIMIT 1`)
- [ ] `broad_source_health` is non-null in that row if broad-source tickers were present
- [ ] `ticker_governance` inserts/updates without error when governance logic fires
- [ ] `thesis_cache.governance_state`, `candidate_lane`, `sources` fields are populated for new theses
- [ ] `GET /api/funnel/summary` returns HTTP 200 with `candidates_total`, `ai_selected`, lane breakdowns
- [ ] `GET /api/outcome/attribution` returns HTTP 200 (may be sparse until outcomes accumulate)
- [ ] Dashboard Screeners / Funnel tab renders without console errors
- [ ] If AI synthesis ran: `issuance_state` and `candidate_lane` are set on new `thesis_cache` rows

## Outcome resolution (manual operational step)

`POST /api/options/resolve-outcomes?resolution_type=1d` resolves yesterday's snapshots into
outcome records. No scheduler exists yet. Call it manually each morning or wire it into
`run_master.sh` after the pipeline completes.

Example:

```bash
curl -X POST "https://<your-api>/api/options/resolve-outcomes?resolution_type=1d&limit=200"
curl -X POST "https://<your-api>/api/options/resolve-outcomes?resolution_type=5d&limit=200"
curl -X POST "https://<your-api>/api/options/resolve-outcomes?resolution_type=10d&limit=200"
```

The accuracy dashboard (`/options` → Accuracy tab) becomes meaningful after ~5 trading days of
resolved snapshots.
