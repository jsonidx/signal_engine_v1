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
