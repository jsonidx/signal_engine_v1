# Verification Workflow

Use `make verify` as the default daily gate.

It runs:

- deterministic backend pytest subset
- frontend Vitest tests
- frontend TypeScript/Vite build
- backend import smoke checks

Use `make verify-full` before releases or when changing live integration behavior. It runs the full pytest suite and may require Supabase/network access.

Known full-suite caveats:

- Supabase integration tests require `DATABASE_URL` and network access.
- Some external-data tests depend on live provider availability.
- Historical squeeze replay/persistence tests currently expose existing failures and should be handled as separate QA tasks.

Do not use paid LLM calls as part of the default verification gate.

