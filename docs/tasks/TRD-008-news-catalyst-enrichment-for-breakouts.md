# Task: News Catalyst Enrichment For Breakouts

Status: done
Owner: Claude Code
Risk: trading-logic

## Objective

Enrich breakout detection with structured fresh-catalyst evidence from company news, press releases, and optionally social/news velocity, so narrative changes can trigger Deep Dive review earlier.

## Scope

- `catalyst_screener.py`
- `quant_report.py`
- `social_sentiment.py`
- `utils/ticker_selector.py`
- `dashboard/api/main.py`
- `tests/test_marketaux.py`
- `tests/test_social_sentiment.py`
- `tests/test_pipeline_defects.py`

## Non-Goals

- Do not make LLM-only news interpretation the source of truth.
- Do not scrape paid or blocked sources.
- Do not treat every AI-related headline as bullish without price/liquidity confirmation.

## Constraints

- Use structured keyword/category scoring first; optional LLM summary can be secondary.
- Catalyst reasons must be persisted or included in candidate metadata.
- Suggested catalyst tags:
  - `AI_INFRASTRUCTURE_LAUNCH`
  - `GUIDANCE_OR_MARGIN_BEAT`
  - `ANALYST_TARGET_CLUSTER`
  - `SOCIAL_ATTENTION_SPIKE`
- Combine catalyst evidence with price/liquidity before queueing Deep Dive candidates.

## Acceptance Criteria

- Observable behavior: a fresh company catalyst can add score/reason metadata to an event candidate.
- Observable behavior: a ticker with catalyst tag + short interest above 10% + early momentum can be queued for Deep Dive.
- Tests: include fixture headlines for a CRSR-like AI infrastructure launch and a false-positive generic AI mention.
- Tests: verify stale headlines outside the configured lookback do not trigger.
- Documentation: include default catalyst keyword groups and lookback window.

## Verification Plan

- `pytest tests/test_marketaux.py tests/test_social_sentiment.py tests/test_pipeline_defects.py -v`
- Dry-run a CRSR-like catalyst payload and confirm `AI_INFRASTRUCTURE_LAUNCH` appears as a structured reason.
- `make verify`

## Implementation Notes

### What changed
- New `utils/catalyst_enrichment.py`:
  - `classify_headline(headline, published_at, lookback_days) -> set[str]` — keyword-based
    tag classification with staleness gate.  Returns empty set for headlines older than
    `CATALYST_LOOKBACK_DAYS` (default 7).
  - `score_catalyst_bundle(headlines, short_float, momentum_5d, avg_dv_20d) -> dict` —
    combines catalyst tags with price/liquidity/short-interest into a 0–1 readiness score.
    `queue_eligible=True` requires ≥1 tag + `momentum_5d >= 5%` + `avg_dv_20d >= $5M`.
  - 4 tags: `AI_INFRASTRUCTURE_LAUNCH`, `GUIDANCE_OR_MARGIN_BEAT`, `ANALYST_TARGET_CLUSTER`,
    `SOCIAL_ATTENTION_SPIKE`.
  - `AI_INFRASTRUCTURE_LAUNCH` false-positive guard: generic "conference/participation"
    mentions are explicitly rejected even when the headline contains "AI".
- `config.py`: `CATALYST_LOOKBACK_DAYS=7`.

### Default catalyst keyword groups
| Tag | Key patterns |
|---|---|
| AI_INFRASTRUCTURE_LAUNCH | "launches ai", "ai workstation", "ai server", "nvidia blackwell", "corsair pro ai", "accelerated computing" |
| GUIDANCE_OR_MARGIN_BEAT | "beats estimates", "profit beat", "margin expansion", "raised guidance", "turns profitable" |
| ANALYST_TARGET_CLUSTER | "price target", "analyst upgrade", "raises price target", "initiates coverage" |
| SOCIAL_ATTENTION_SPIKE | "unusual options activity", "short squeeze", "wallstreetbets", "options sweep" |

### Tests run
```
pytest tests/test_pipeline_defects.py::TestCatalystEnrichment -v
# 10 passed (1 AI launch, 1 false-positive guard, 2 other tags,
#            2 staleness, 4 bundle scoring)
```

### QA round 2 changes (2026-05-29)
- Removed `"gross margin"` (too broad) and `"reports q"` (catches generic quarterly reports)
  from GUIDANCE_OR_MARGIN_BEAT patterns.
- Added specific expansions: `"gross margin expansion"`, `"gross margin improvement"`,
  `"gross margin record"`, `"gross margin beat"`, `"record gross margin"`, `"margin beat"`,
  `"margin improvement"`, `"strong quarterly results"` (not bare "strong quarterly").
- Removed `"strong quarterly"` bare pattern.
- Added 6 regression tests (`test_generic_earnings_report_no_beat_tag`,
  `test_bare_gross_margin_mention_no_beat_tag`, `test_gross_margin_expansion_fires_tag`,
  `test_record_gross_margin_fires_tag`, `test_beats_estimates_still_fires`,
  `test_raised_guidance_fires_tag`).

### Residual risk
- Keyword matching is substring-based; adversarial headlines with misspellings or
  unusual phrasing may be missed.  LLM-based secondary classification is not yet wired
  but can be added as an optional layer in `score_catalyst_bundle`.
- `classify_headline` uses `date.today()` for the staleness check; pipeline runs near
  midnight may have off-by-one on the cutoff.  This is acceptable for a 7-day window.
- No live news feed integration yet; headlines must be passed in from external sources
  (e.g. marketaux.py, social_sentiment.py).  The enrichment module is a pure classifier.

### Original note
CRSR had two catalyst layers before the late breakout: strong Q1 profitability/margin results on May 7, 2026, then the CORSAIR PRO AI workstation/server launch around May 21-22. The existing pipeline treated price/volume as the main inclusion mechanism and did not route the fresh narrative shift into Deep Dive early enough.
