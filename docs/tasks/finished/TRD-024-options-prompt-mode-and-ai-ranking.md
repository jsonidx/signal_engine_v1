# Task: Options Prompt Mode and AI Candidate Ranking

Status: completed
Stage: done
Type: feature
Priority: P2
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: dashboard
Category: options
Risk: frontend
Effort: M
Target Release: options-stack-v1
Due Date: N/A
Dependencies: TRD-022, TRD-023, TRD-042
Links: `dashboard/frontend/src/pages/TickerPage.tsx`, `dashboard/frontend/src/pages/tests/TickerPage.option-candidates.test.tsx`

## Implementation Notes

The options prompt mode was added to `TickerPage.tsx` as an extension of the
existing copy-prompt flow rather than a new button.

### What shipped

- `buildOptionsPrompt()` function in `TickerPage.tsx` (line 1005): constructs a
  structured prompt from the stock thesis context plus all pre-filtered
  candidate rows; includes explicit model instructions.
- Prompt mode selector: when option candidates exist the copy-prompt action
  defaults to `'options'` mode and renders the label
  *"Copy options-candidate prompt (pre-filtered contracts only)"* (line 1150).
- Prompt guardrail: the generated prompt explicitly instructs the model to rank
  only the supplied candidates and not invent contracts outside the provided
  set (see `buildOptionsPrompt` and no-candidate fallback path at line 1116).

### Tests (TickerPage.option-candidates.test.tsx)

```
it('copies options prompt containing candidate data when Options mode selected')
it('options prompt contains ranking guardrail instruction')
it('options prompt with no candidates instructs LLM not to invent contracts')
```

All three tests pass as part of the broader 55-test TickerPage option-candidates
suite.

## Original Acceptance Criteria (all met)

- [x] Ticker page offers an options-specific prompt mode
- [x] Copied prompt contains stock thesis plus candidate contracts
- [x] Prompt explicitly constrains LLM to rank only supplied candidates
- [x] Fallback is clean when no candidates exist
- [x] Prompt builder tests cover candidate rows, required fields, guardrail language
