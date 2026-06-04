# Ship Report

Date: 2026-05-30

## Commits

| Commit | Hash | Content |
| --- | --- | --- |
| Runtime/feature | `b3f3fca` | Options integration, `utils/db.py`, `utils/supabase_persist.py`, migrations `002` and `004`, `universe_builder.py`, `sync_task_status.py` |
| Tests + docs | `f89854c` | Test isolation fix, new test suites, TRD-012/013/014 QA notes, task doc moves/additions |

Branch pushed: `main` -> `013db13..f89854c`

## Files Committed By Category

### Commit 1 — runtime-impacting

- `utils/supabase_persist.py` — option candidate persistence (TRD-026); no squeeze training logic changed
- `utils/db.py` — adds `ensure_public_table_rls()` helper; no schema changes
- `dashboard/api/main.py`, `dashboard/frontend/*` — options tab, `OptionsPage`
- `utils/ibkr_options.py`, `utils/option_candidates.py`, `utils/option_outcomes.py` — new options utilities
- `migrations/002_*.sql`, `migrations/004_*.sql` — RLS and option snapshot DDL
- `universe_builder.py`, `thesis_checker.py`, `favorites.py`, `scripts/*` — minor updates

### Commit 2 — tests and docs only

- `tests/test_squeeze_persistence_schema.py` — isolation fix: 4 tests now patch `save_squeeze_training_snapshot_backfill` to prevent `TSTZ` leak to live DB
- New test files (options, universe, conflict resolver, supabase integration)
- `docs/tasks/in-progress/TRD-012/013/014` — QA notes only, status remains `qa`
- `TRD-011/015` renamed to `finished/`, `TRD-020–031` added as `new/`
- `reports/squeeze_calibration_2026-05-30.md` — empty-data fallback report

## Operational Impact On TRD-012 / TRD-013 / TRD-014

This push does not unblock TRD-012/013/014. The squeeze training and calibration code is unchanged. The blocker remains `squeeze_training_outcomes` having `0` rows, which is a data maturity issue (`~2026-06-05` earliest). The push aligns GitHub Actions with local state and ensures the test isolation fix (no synthetic `TSTZ` writes) is in the CI environment for future runs.
