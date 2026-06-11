# Task: LLM analysis job isolation and concurrency guard

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: high
Owner: Claude Code
Reviewer: Codex
Product Area: api
Category: reliability | automation
Risk: api
Effort: M
Target Release: next
Due Date: 2026-06-19
Dependencies: BUG-001
Blocked By: none
Links: `dashboard/api/main.py`, `dashboard/frontend/src/pages/TickerPage.tsx`, `dashboard/frontend/src/lib/api.ts`, `ai_quant.py`
Success Metric: Interactive ticker-page traffic remains responsive while analysis jobs are queued or running; no more than the configured number of heavy LLM analysis subprocesses execute concurrently in one process.

## Implementation Notes

### What shipped

**`dashboard/api/main.py`**:

- `MAX_CONCURRENT_ANALYSIS = 3` constant
- `_analysis_semaphore = asyncio.Semaphore(3)` — process-local concurrency gate
- `_run_queued_analysis(job_key, symbol, llm, ...)` — async helper that waits for a semaphore slot, sets status to `running`, launches the subprocess, and resets the slot on completion or failure
- `POST /api/ticker/{symbol}/analyze` — always returns `{"status": "queued"}` immediately, then fires `asyncio.create_task(_run_queued_analysis(...))` in the background
- Per-symbol+LLM dedupe: if a job is already `queued` or `running`, the endpoint returns its current status without spawning a duplicate task

**`dashboard/frontend/src/lib/api.ts`**:

- `AnalyzeStatus.status` extended with `"queued"` variant
- `queued_at?: string` field added to `AnalyzeStatus`

**`dashboard/frontend/src/pages/TickerPage.tsx`**:

- `ModelRerunButton` — `isActive = job?.status === 'queued' || job?.status === 'running'`, `refetchInterval: isActive ? 5000 : false`, explicit `"○ queued…"` render branch
- `AnalyzeButton` — queued status renders as `"grok 4.3: queued"` indicator
- Critical infinite render loop fixed: `setJobs` now returns `prev` when no status changed, preventing `useMemo([jobs])` → `useEffect` → `poll()` → perpetual cycle

### Test coverage

**Backend** — `dashboard/api/tests/test_endpoints.py`, `TestAnalysisGate` class (line 511), 5 tests:

| Test | Behavior covered |
|---|---|
| `test_returns_queued_status` | POST /analyze always returns `status=queued` immediately |
| `test_dedupes_already_queued_job` | Duplicate submit for same symbol+LLM does not spawn a second task |
| `test_status_endpoint_returns_queued` | GET /analyze/status surfaces `queued` + `queued_at` |
| `test_semaphore_gate_holds_overflow_and_advances_on_release` | Job stays `queued` while all slots occupied; advances when one is released |
| *(additional gate coverage)* | Slot behavior on subprocess failure/completion |

**Frontend** — `dashboard/frontend/src/pages/tests/TickerPage.option-candidates.test.tsx`:

| Test | Behavior covered |
|---|---|
| `ModelRerunButton — queued state` (line 1071) | Renders `"○ queued…"` when `tickerAnalyze` returns `status=queued` |
| `AnalyzeButton — queued state` (line 1048) | Renders `"grok 4.3: queued"` when `tickerAnalyze` returns `status=queued` |

### Bugs fixed during implementation

- **Infinite render loop in `AnalyzeButton`**: `setJobs(prev => ({ ...prev }))` always returned a new object, invalidating `useMemo([jobs])` and re-firing the status-polling `useEffect` endlessly. Fixed by returning `prev` when no status field changed.

## Acceptance Criteria (all met)

- [x] Only up to `MAX_CONCURRENT_ANALYSIS` (3) analysis subprocesses run concurrently in one API process
- [x] Excess analysis jobs are queued instead of starting immediately
- [x] Analyze status clearly distinguishes `queued` from `running`
- [x] Duplicate analyze requests for the same symbol+model while already queued/running do not launch duplicate work
- [x] Backend coverage verifies the concurrency gate and queued status
- [x] Frontend coverage verifies queued jobs surface a distinct status to the user
