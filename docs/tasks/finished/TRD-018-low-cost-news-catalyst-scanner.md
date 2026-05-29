# Task: Low-Cost News Catalyst Scanner

Status: implemented
Stage: finished
Type: feature
Priority: P1
Severity: medium
Owner: Claude Code
Reviewer: Codex
Product Area: data-pipeline
Category: growth
Risk: trading-logic
Effort: M
Target Release: news-catalyst sprint
Due Date: 2026-06-05
Dependencies: TRD-017, TRD-008
Blocked By: none
Links: utils/news_catalyst_scanner.py
Success Metric: Morning Brief Pattern Watch and Telegram Catalyst Watch surface news-catalyst tickers without any AI synthesis cost.

## Problem Statement

Pattern Watch (TRD-017) and the event queue can surface early catalyst candidates, but they depend on the pipeline having already
run catalyst_scores for the current day.  News-driven catalyst setups (product launches, guidance beats, analyst upgrades) may
appear in headlines before they show up in any screener output.  There was no zero-cost way to route those tickers into the queue.

## Objective

Add a free RSS headline scanner that runs before AI synthesis (Step 12b in run_master.sh), classifies headlines with existing
keyword rules, and enqueues eligible tickers into event_queue under a `NEWS_CATALYST:<tags>` reason so Pattern Watch and
Telegram can surface them without waiting for catalyst_scores or paid AI calls.

## Implementation Notes

- **Shipped commit:** f5d4c18 — "Add low-cost news catalyst scanner"
- **Workflow enablement commit:** 1403472 — "Enable news catalyst scanner in workflows"

### Module

- `utils/news_catalyst_scanner.py` — full scanner (~310 lines)
  - Free RSS sources: Yahoo Finance RSS (`feeds.finance.yahoo.com`) and Google News RSS
  - Headline cache: `data/news_headline_cache.json`, default TTL 6 hours
  - Classification: delegates entirely to `utils/catalyst_enrichment.classify_headline()` and `score_catalyst_bundle()` — pure keyword matching, zero LLM calls
  - Eligibility gate: at least one fresh catalyst tag + momentum_5d >= 5% + avg_dv_20d >= $5M
  - Queue reason format: `NEWS_CATALYST:<tag1,tag2,...>`
  - event_queue source_fields include: headline titles, dates, source names, catalyst tags, scanner score, momentum_5d, avg_dv_20d
  - RSS parser uses `_first_found()` helper to avoid ElementTree false-element bug
  - URLs are properly encoded via `urllib.parse.quote_plus`

### CLI

```
python3 utils/news_catalyst_scanner.py --max-tickers 200 --max-headlines-per-ticker 5
  --dry-run            score but do not write to event_queue
  --use-exa            placeholder — not yet implemented
  --cache-hours 6      headline cache TTL
  --watchlist PATH     override ticker source
```

### Pipeline integration

- `run_master.sh` Step 12b — opt-in via `ENABLE_NEWS_CATALYST_SCANNER=true`
- Failure of the scanner does NOT abort the pipeline (`|| { echo "failed — continuing" }`)
- Enabled in `daily_pipeline.yml` and `manual_pipeline.yml` via `ENABLE_NEWS_CATALYST_SCANNER: "true"` environment variable

### Pattern Watch compatibility

- `utils/pattern_watch.py` CRSR scorer: `"news_catalyst"` added to catalyst_kws
- `dashboard/api/main.py` `_snapshot_proxy_cs`: `"news_catalyst"` triggers technical_score=3.0 and volume_score=3.0
- `dashboard/api/main.py` `_is_pattern_snapshot`: `"news_catalyst"` included in gating keywords

### Telegram compatibility

- `scripts/notify_pipeline_result.py` `fetch_catalyst_watch_candidates` DB query extended:
  `AND (cs.selection_reason ILIKE '%fresh_catalyst_breakout%' OR cs.selection_reason ILIKE '%NEWS_CATALYST%')`

### Cost guarantee

- No AI/Claude/Grok/Exa/OpenAI calls by default
- `--use-exa` flag accepted but currently a no-op with logged warning
- Requires only: `urllib` (stdlib), `xml.etree` (stdlib), `yfinance` (already a pipeline dep)

### Tests

- `tests/test_news_catalyst_scanner.py` — 21 tests, all pass
  - AI_INFRASTRUCTURE_LAUNCH tag from launch headline
  - Stale headline returns empty tag set
  - No catalyst tag → not queue-eligible
  - Catalyst tag + weak momentum → not queue-eligible
  - Catalyst tag + low liquidity → not queue-eligible
  - Valid setup → enqueue called and event_queue.json written
  - dry-run does not write event_queue.json
  - Scanner runs with zero API keys in environment
  - `_fetch_rss` parses standard RSS item (patched urlopen, no network)
  - Pattern Watch score_ticker returns CRSR match for NEWS_CATALYST snap
  - `_is_pattern_snapshot` logic recognises NEWS_CATALYST
  - Telegram fetch includes NEWS_CATALYST rows alongside fresh_catalyst_breakout rows

## Non-Goals

- Does not call any LLM by default
- Does not replace catalyst_screener.py or the existing catalyst_scores pipeline
- Does not require Exa or paid search APIs
- Does not implement full-text search or NLP

## Risks

1. Yahoo Finance / Google News RSS availability — mitigated by silent fallback to empty list on any error
2. yfinance column layout changes — guarded with try/except; missing price data results in ticker being skipped
3. Score thresholds are shared with catalyst_enrichment — any tightening there tightens the scanner automatically
