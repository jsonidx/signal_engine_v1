# Task: Analyst Price-Target Change Detection

Status: completed
Stage: done
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Human
Product Area: data-pipeline
Category: research
Risk: trading-logic
Effort: M
Target Release: backlog
Due Date: TBD
Dependencies: none
Blocked By: none
Links: `catalyst_screener.py`, `tests/test_analyst_target_detection.py`
Success Metric: catalyst scoring detects explicit analyst price-target raises and cuts, not just generic upgrade/downgrade labels.

## Implementation Notes

### What shipped

**`catalyst_screener.py`** — two new functions plus extensions to `score_analyst_momentum`:

**`_is_numeric(val) -> bool`** — safe numeric check used by the classifier.

**`_classify_analyst_row(row) -> str`** — normalizes a single analyst feed row into one of:
- `"upgrade"` — rating improvement (including reiterations that count as upgrades)
- `"downgrade"` — rating cut
- `"target_raise"` — explicit price-target raise with no concurrent rating change (no double-counting)
- `"target_cut"` — explicit price-target cut with no concurrent rating change
- `"neutral"` — no actionable signal

Classification rules (line ~1108):
- Rating changes (upgrade/downgrade) take priority over price-target direction — a row cannot count as both
- `target_raise` requires a numeric `currentPriceTarget > priorPriceTarget` with a neutral or missing rating action
- `target_cut` requires `currentPriceTarget < priorPriceTarget` with a neutral or missing rating action

**`score_analyst_momentum`** extended output shape (line ~1158):

New fields added to the return dict:
- `target_raise_flag` — `True` if ≥1 pure PT raise in 7d
- `target_cut_flag` — `True` if ≥1 pure PT cut in 7d
- `target_raises_7d` — count of pure PT raises in 7d
- `target_cuts_7d` — count of pure PT cuts in 7d

Scoring changes:
- Pure target raises in 7d contribute `+0.5` per raise to the analyst score (separate from upgrade cluster score; no double-count)
- Pure target cuts apply `-1` per cut (floor 0)
- Existing upgrade clustering behavior is unchanged

### Test coverage

**`tests/test_analyst_target_detection.py`** — 39 tests:

| Test group | Coverage |
|---|---|
| `_classify_analyst_row` | target raise detected, target cut detected, upgrade wins over target raise, reiteration + PT raise = upgrade, downgrade wins over target cut, ambiguous action falls back correctly |
| `score_analyst_momentum` | single target raise contributes positive score, two raises score higher, target raise flag set, target cut flag set, mixed activity in 7-day window no double-counting, no false positive on rating-only change without PT field |

All 39 tests pass.

## Acceptance Criteria (all met)

- [x] A price-target raise by a bank contributes a positive catalyst signal even when the feed label is ambiguous
- [x] A price-target cut contributes a negative analyst signal
- [x] Existing analyst upgrade clustering still works as before
- [x] Target raise detected in isolation
- [x] Target cut detected in isolation
- [x] Mixed analyst activity in same 7-day window handled without double-counting
- [x] No false positive when only rating text changes without a numeric PT change
