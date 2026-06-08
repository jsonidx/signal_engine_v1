# Task: Provider-Neutral AI Synthesis Naming Cleanup

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: api
Category: reliability
Risk: api
Effort: M
Target Release: ai-quant-v2
Due Date: TBD
Dependencies: TRD-066
Blocked By: none
Links: `ai_quant.py`, `conflict_resolver.py`, `utils/ticker_selector.py`, `schema.sql`, `dashboard/api/main.py`, `dashboard/frontend/src/lib/api.ts`, `dashboard/frontend/src/pages/ResolutionPage.tsx`, `docs/INTERNALS.md`, `docs/HELP.md`, `run_master.sh`
Success Metric: active code, schema fields, logs, UI labels, and operational docs no longer imply that AI Quant is Claude-specific when the runtime model may be Grok, OpenAI, or Anthropic.

## Problem Statement

The repo has evolved from a Claude-specific AI Quant path into a multi-provider model-selection system, but naming has not kept up. The active stack still uses provider-specific names such as:

- `skip_claude`
- `claude_skipped`
- `Claude synthesis`
- `Claude API call budgeting`
- `Claude-Powered Signal Synthesis`

This is now semantically wrong. It causes operator confusion, misleading logs, stale documentation, and architectural drift between the actual runtime design and the names persisted in code and schema.

## User Impact

- Operators can misread what was skipped and what provider was actually involved.
- API and UI semantics are tied to a legacy provider name.
- Future multi-model work becomes harder to reason about because business meaning is encoded with a stale vendor label.
- Documentation no longer matches the configurable model-routing reality.

## Objective

Make the active AI Quant stack provider-neutral in naming while preserving backward compatibility during the transition.

## Proposed Solution

Rename provider-specific concepts to function-specific concepts.

Recommended naming:

- `skip_claude` → `skip_ai_synthesis`
- `claude_skipped` → `ai_synthesis_skipped`
- `Claude synthesis` → `AI synthesis`
- `Claude API call budgeting` → `LLM call budgeting` or `AI synthesis budgeting`
- `Claude-Powered Signal Synthesis` → `LLM-Powered Signal Synthesis` or `AI Signal Synthesis`

Implementation approach:

### Phase 1: additive compatibility

- read both old and new field names
- prefer writing the new neutral field names
- update logs, CLI messages, UI labels, and active docs

### Phase 2: storage cleanup

- migrate schema and persistence to new field names where practical
- keep compatibility shims until consumers are updated

This ticket should focus on active operational code and docs, not historical ticket archives.

## Scope

Files or modules likely affected:

- `ai_quant.py`
- `conflict_resolver.py`
- `utils/ticker_selector.py`
- `utils/usage.py`
- `schema.sql`
- `utils/supabase_persist.py`
- `dashboard/api/main.py`
- `dashboard/api/tests/test_endpoints.py`
- `dashboard/frontend/src/lib/api.ts`
- `dashboard/frontend/src/pages/ResolutionPage.tsx`
- `dashboard/frontend/src/components/AiSelectionTable.tsx`
- `dashboard/frontend/src/components/CandidateSnapshotsTable.tsx`
- `docs/INTERNALS.md`
- `docs/HELP.md`
- `dashboard/README.md`
- `run_master.sh`
- `DEPLOY.md`
- relevant selector / resolver tests

## Non-Goals

- Do not rewrite historical finished tickets or archival docs unless they are still operationally relied upon.
- Do not change actual provider-routing logic here unless required for compatibility safety.
- Do not redesign AI Quant architecture in this ticket.

## Constraints

- No refactoring unless owner is Claude Code and the task explicitly says refactor.
- No secrets or generated artifacts in git.
- Preserve backward compatibility for existing DB rows, caches, and API consumers during transition.

## Acceptance Criteria

- Observable behavior:
  - active code paths no longer present provider-specific wording when referring to generic AI synthesis behavior
  - new neutral field names are supported in active API / persistence / UI paths
  - old field names remain readable during the migration window where necessary
- Tests:
  - add or update tests for compatibility with both old and new field names where applicable
  - add regression tests for API responses if response shape changes
- Documentation:
  - active operational docs reflect model/provider-neutral naming for AI Quant

## Verification Plan

- Targeted tests:
  - `pytest -q tests/test_conflict_resolver.py tests/test_ticker_selector.py tests/test_supabase_integration.py dashboard/api/tests/test_endpoints.py`
- Additional verification:
  - inspect representative UI/API payloads to confirm neutral naming is exposed
  - inspect CLI/log output for renamed messages

## QA Notes

- Test scenarios:
  - old persisted `skip_claude` data still loads
  - new writes prefer `skip_ai_synthesis`
  - API and UI can consume either shape during transition
- Edge cases:
  - mixed old/new rows
  - logs and analytics still referencing `claude_skipped`
  - schema migration ordering
- Regression risks:
  - breaking old dashboard consumers
  - migration drift between DB, API, and frontend

## Launch / Release Notes

- User-facing change summary: AI Quant terminology is now provider-neutral and better reflects multi-model routing.
- Operational notes: legacy field names remain readable during the transition window where necessary.
- Rollback notes: continue reading legacy names and disable new-field-only writes if compatibility issues arise.

## Post-Launch Validation

- What to monitor:
  - API/frontend compatibility
  - migration success for renamed fields
  - lingering stale provider-specific labels in active surfaces
- How success will be confirmed:
  - operators no longer see misleading Claude-specific terminology in generic AI Quant flows
- Follow-up decision date:
  - after the first stable release using neutral naming

## Handoff Notes

Paste-ready Claude implementation prompt:

```text
Implement TRD-069: clean up provider-specific naming in the active AI Quant stack and replace it with provider-neutral AI/LLM synthesis terminology.

Goal:
- Stop encoding generic AI synthesis semantics with the legacy Claude label.
- Preserve backward compatibility while moving active code and docs to neutral naming.

Scope:
- ai_quant.py
- conflict_resolver.py
- utils/ticker_selector.py
- utils/usage.py
- schema.sql
- utils/supabase_persist.py
- dashboard/api/main.py
- dashboard/api/tests/test_endpoints.py
- dashboard/frontend/src/lib/api.ts
- dashboard/frontend/src/pages/ResolutionPage.tsx
- dashboard/frontend/src/components/AiSelectionTable.tsx
- dashboard/frontend/src/components/CandidateSnapshotsTable.tsx
- docs/INTERNALS.md
- docs/HELP.md
- dashboard/README.md
- run_master.sh
- DEPLOY.md
- relevant tests

Required changes:
- Introduce neutral names such as:
  - skip_ai_synthesis
  - ai_synthesis_skipped
  - AI synthesis / LLM synthesis
- Continue reading legacy names like skip_claude where needed for compatibility.
- Prefer writing new neutral names in active code paths where practical.
- Update user-facing logs, CLI text, UI labels, and active operational docs to remove stale Claude-specific wording for generic AI Quant behavior.

Non-goals:
- No historical archive cleanup unless the file is still operationally used
- No architecture redesign in this ticket
- No provider-routing redesign unless needed for compatibility

Constraints:
- Preserve backward compatibility for persisted rows, caches, and API consumers during transition
- Avoid broad refactors outside the listed files

Tests / verification:
- pytest -q tests/test_conflict_resolver.py tests/test_ticker_selector.py tests/test_supabase_integration.py dashboard/api/tests/test_endpoints.py
- inspect representative API/UI payloads and CLI/log text for neutral naming
```
