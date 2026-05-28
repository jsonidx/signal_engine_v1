# Local Verification Runbook

Use `make verify` for normal AI-assisted development. It runs deterministic backend tests, frontend tests, frontend build, and import smoke checks without paid LLM calls.

The default offline subset intentionally excludes live Supabase tests and unstable historical replay/persistence checks. Use targeted pytest commands when working directly on those modules.

Use `make verify-full` before releases or infrastructure/database work. It runs the complete pytest suite, including tests that require live Supabase/network access.

Expected failure classes in `make verify-full`:

- Supabase DNS or credential failures when `DATABASE_URL` is unavailable.
- External API/network failures under sandboxed or offline execution.
- Tests that intentionally validate live persistence state.

Do not treat those as workflow-file failures unless the task changed database, network, or integration behavior.
