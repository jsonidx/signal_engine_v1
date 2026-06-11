# Task: Options Screener, Persistence, and Learning Roadmap

Status: completed
Stage: done
Type: product
Priority: P1
Severity: medium
Owner: Codex
Reviewer: Human
Product Area: dashboard
Category: options
Risk: trading-logic
Effort: XL
Target Release: options-stack-v1
Due Date: N/A
Dependencies: TRD-021, TRD-022, TRD-023, TRD-024

## Implementation Notes

This was a roadmap/design ticket. All five phases it described shipped as
downstream implementation tickets.

### Phase delivery summary

| Phase | Description | Delivered by |
|-------|-------------|--------------|
| 1 | Recommendation snapshot persistence | TRD-026 |
| 2 | Outcome tracking | TRD-027 |
| 3 | Options screener API + dashboard tab | TRD-028 |
| 4 | Resolution & Accuracy analytics | TRD-029 |
| 5 | Scoring review workflow | TRD-030 |

All downstream tickets are now closed.

### Architecture delivered

The system was built exactly as the roadmap proposed:

```
Ticker thesis
  → option candidate engine  (TRD-022, TRD-023)
  → options screener ranking  (TRD-028)
  → snapshot persistence       (TRD-026)
  → outcome tracking           (TRD-027)
  → accuracy analytics         (TRD-029)
  → scoring review workflow    (TRD-030)
```

The LLM-based prompt ranking for a single ticker was added as TRD-024.
The options stack was subsequently extended with the v2 target engine
(TRD-043), risk/sizing (TRD-046), scenarios (TRD-047), structure policy
(TRD-048), entry guardrails (TRD-049), feature store (TRD-050), and the
buy-rule gate (TRD-054).

### Key architectural decisions reflected in delivered code

- Thesis-driven: screener operates on top-N thesis rows, not raw chain scan.
- Persistence first: every recommendation (and suppressed result) is stored
  before analytics are exposed.
- Claude proposes, human approves scoring changes — not autonomous.
- `option_candidate_snapshots` and `option_candidate_outcomes` are the central
  learning tables.

### Follow-up

TRD-080 (Options Screener Snapshot Architecture) addresses the runtime
performance problem discovered after this roadmap shipped: the live IBKR
fan-out is too slow for synchronous page-load use. That is the next generation
of the screener, not a gap in this roadmap.
