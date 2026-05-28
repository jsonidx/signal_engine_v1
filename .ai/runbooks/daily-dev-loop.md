# Daily Development Loop

1. Pull latest branch state.
2. Check `git status --short`.
3. Read the task spec or create one under `docs/tasks/`.
4. Implement only the scoped change.
5. Run the smallest relevant test first.
6. Run `make verify` before handoff when feasible.
7. Review the diff for generated files, secrets, and unrelated edits.
8. Summarize behavior changes, verification, and risk.
