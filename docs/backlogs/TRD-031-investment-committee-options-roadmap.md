# TRD-031: GPT + Claude Investment Committee for Options Trade Support

Status: proposed  
Stage: discovery  
Type: feature  
Priority: P1  
Severity: high  
Owner: Claude Code  
Reviewer: Human  
Product Area: options / ai  
Category: roadmap  
Risk: trading-logic  
Effort: XL  
Target Release: TBD

## Executive Call

This is a strong idea, but not because "more LLMs = more alpha." The value is adversarial decision support on top of a deterministic options pipeline:

- thesis builder reduces missed opportunity context
- skeptic catches hidden risk, liquidity, event, and IV issues
- deterministic validator prevents soft LLM agreement from overriding hard constraints
- bounded debate produces better auditability than a single monologue model

From a PM perspective: **build it, but build it conservatively**. The MVP should be a rules-first committee attached to the current option candidate engine, not a free-form AI debate layer. The module should optimize for **false-positive reduction**, not trade frequency.

## Repo Audit Summary

### What already exists

- Deterministic option candidate engine in [utils/option_candidates.py](/Users/jason/signal_engine_v1/utils/option_candidates.py)
- Normalized option chain adapter with IBKR-first and yfinance fallback in [utils/ibkr_options.py](/Users/jason/signal_engine_v1/utils/ibkr_options.py)
- Option candidate snapshot and outcome persistence in [migrations/004_option_candidate_snapshots_and_outcomes.sql](/Users/jason/signal_engine_v1/migrations/004_option_candidate_snapshots_and_outcomes.sql) and [utils/supabase_persist.py](/Users/jason/signal_engine_v1/utils/supabase_persist.py)
- Cross-ticker screener and accuracy analytics in [dashboard/api/main.py](/Users/jason/signal_engine_v1/dashboard/api/main.py) and [dashboard/frontend/src/pages/OptionsPage.tsx](/Users/jason/signal_engine_v1/dashboard/frontend/src/pages/OptionsPage.tsx)
- Existing thesis cache, signal bundle, and portfolio settings in [schema.sql](/Users/jason/signal_engine_v1/schema.sql)
- Existing LLM integrations in [ai_quant.py](/Users/jason/signal_engine_v1/ai_quant.py)
- Existing configurable model settings in [dashboard/api/main.py](/Users/jason/signal_engine_v1/dashboard/api/main.py)

### What is missing

- No committee workflow, run orchestration, or bounded debate state machine
- No provider-native strict structured output path in the current `ai_quant` flow
- No deterministic validator for traceability of model claims to source fields
- No committee audit tables for prompts, outputs, decisions, retries, and costs
- No committee dashboard controls or review UI
- No production queue for debate jobs; current queueing is file-based in [utils/event_queue.py](/Users/jason/signal_engine_v1/utils/event_queue.py)
- No benchmark harness for repeatable multi-model evaluation on frozen snapshots

### Current repo gaps that matter for options accuracy

1. `OptionContract` already supports `gamma`, `theta`, `vega`, and `implied_vol`, but the candidate engine output and persistence path do not carry the full Greek set through to stored candidate records. See [utils/ibkr_options.py](/Users/jason/signal_engine_v1/utils/ibkr_options.py) vs [utils/option_candidates.py](/Users/jason/signal_engine_v1/utils/option_candidates.py) and [utils/supabase_persist.py](/Users/jason/signal_engine_v1/utils/supabase_persist.py).
2. The schema for `option_candidate_snapshots` includes `gamma`, `theta`, `vega`, but `save_option_candidate_snapshot()` does not write them.
3. Current `ai_quant` is prompt-based and model-parsed, not schema-enforced structured output.
4. Current cost metadata in [utils/usage.py](/Users/jason/signal_engine_v1/utils/usage.py) is stale relative to current provider docs.
5. yfinance fallback gives only partial chain quality; Greek quality and freshness are materially weaker than IBKR.

## 1. Product Objective

### Purpose

Create a bounded, auditable, multi-model investment committee for **options swing-trade decision support** after a ticker has already passed earlier scanner and option-candidate gates.

### Problems it should solve

- reduce overconfidence from single-model theses
- catch hidden options risks
- catch IV crush and event-window errors
- reject poor strike / expiration choices
- downgrade trades with weak liquidity or wide spreads
- enforce portfolio and position-size caps
- reduce false positives from upstream scanners
- separate "interesting ticker" from "tradable option setup"

### Non-goals

- no broker execution
- no automatic trade placement
- no guarantee of profitability
- no unsupported market-data assumptions
- no unbounded multi-turn debate
- no use of LLMs to invent missing chain, news, filing, or flow data

## 2. End-to-End Workflow

```text
Scanner / thesis / option engine
        |
        v
Stage A: Candidate intake package
        |
        v
Stage B: Lead analyst (GPT)
        |
        v
Stage C: Skeptic / risk officer (Claude)
        |
        v
Stage D: Lead rebuttal / revision (GPT)
        |
        v
Stage E: Deterministic validator
        |
        +--> reject
        +--> retry_same_model
        +--> human_review_required
        +--> escalate_to_final_judge
        |
        v
Stage F: Final decision gate
        |
        v
Stage G: Audit log + dashboard + benchmark storage
```

### Stage A: Candidate intake

Run only after:

- ticker has latest thesis context
- deterministic option candidate exists
- market data freshness passes minimum checks

Package source should be:

- thesis context from `thesis_cache`
- deterministic contract shortlist from `option_candidate_snapshots` or live `get_option_candidates()`
- signals bundle from `signals_json`
- portfolio/risk settings from `portfolio_settings` and `strategy_config`

### Stage B: GPT lead analyst

Responsibilities:

- build the strongest conservative trade thesis from supplied fields only
- choose preferred contract from provided candidate list, not from the raw full chain
- specify trade structure, invalidation, event handling, and sizing rationale

### Stage C: Claude skeptic

Responsibilities:

- attack assumptions
- identify fatal flaws, uncertainty, missing data, liquidity concerns, IV/event conflicts
- recommend reject / reduce / modify / approve-with-warning

### Stage D: GPT rebuttal

Responsibilities:

- respond point-by-point
- accept or reject each critique item
- revise strike, expiry, size, or decision
- downgrade confidence where evidence is weak

### Stage E: Deterministic validator

Hard-code all invariant logic:

- schema validity
- cited-number traceability
- ticker and timestamp consistency
- risk constraints
- spread, OI, volume, DTE, event-window, and sizing rules

### Stage F: Final decision

For MVP: rules-based.

Decision classes:

- `reject`
- `watch`
- `paper_trade`
- `trade_candidate`
- `human_review_required`

### Stage G: Logging and audit

Store:

- exact prompts
- exact model IDs
- raw model outputs
- parsed JSON
- validation failures
- final decision
- latency
- token usage
- cost
- escalation reason

## 3. Architecture Proposal

### Recommended module boundary

Add a new package:

- `committee/`
  - `schemas.py`
  - `prompts.py`
  - `providers.py`
  - `orchestrator.py`
  - `validator.py`
  - `decision.py`
  - `costing.py`
  - `feature_flags.py`
  - `repository.py`

### Service boundaries

1. **Signal/data assembly**
   - Reuse existing signal collectors and option candidate engine
   - Output a frozen `candidate_intake` object

2. **Committee orchestration**
   - Executes bounded debate steps
   - Tracks retries, model fallbacks, and latency

3. **Validation engine**
   - Pure deterministic checks
   - No provider coupling

4. **Persistence layer**
   - Inserts committee runs, messages, decisions, cost rows

5. **API layer**
   - Trigger run
   - Get run by ID
   - List run history
   - Fetch benchmark reports

### Data flow

```text
thesis_cache + signals_json + option_candidate_snapshots + portfolio_settings
        |
        v
candidate_intake builder
        |
        v
committee_orchestrator.run()
        |
        +--> openai client
        +--> anthropic client
        |
        v
validator
        |
        v
decision engine
        |
        v
committee tables + dashboard API
```

### Queue / jobs

MVP:

- synchronous internal API endpoint for single-run evaluation
- optional background task using FastAPI executor for UI-triggered runs

Production:

- Redis-backed queue or Postgres job table
- bounded concurrency per provider/model
- retry/backoff policy per stage

### Where components should live

- prompts: `committee/prompts.py`
- JSON schemas: `committee/schemas.py`
- validation logic: `committee/validator.py`
- provider clients: `committee/providers.py`
- workflow/state machine: `committee/orchestrator.py`
- cost estimation and cost accounting: `committee/costing.py`
- feature flags + settings resolution: `committee/feature_flags.py`

### Database tables

Add:

- `committee_runs`
- `committee_messages`
- `committee_validation_reports`
- `committee_final_decisions`
- `committee_benchmarks`

Suggested core columns:

#### `committee_runs`

- `id`
- `created_at`
- `ticker`
- `candidate_snapshot_id`
- `thesis_date`
- `run_mode` (`live`, `paper`, `benchmark`)
- `lead_model`
- `skeptic_model`
- `final_judge_type`
- `status`
- `input_hash`
- `input_json`
- `total_input_tokens`
- `total_output_tokens`
- `total_cost_usd`
- `total_latency_ms`

#### `committee_messages`

- `id`
- `run_id`
- `stage`
- `provider`
- `model`
- `prompt_version`
- `request_json`
- `response_json`
- `parsed_json`
- `input_tokens`
- `output_tokens`
- `latency_ms`
- `cost_usd`
- `retry_index`
- `success`
- `error_code`

#### `committee_validation_reports`

- `id`
- `run_id`
- `status`
- `report_json`
- `fatal_flag_count`
- `warning_count`

#### `committee_final_decisions`

- `id`
- `run_id`
- `decision`
- `confidence`
- `decision_reason`
- `sizing_action`
- `requires_human_review`
- `output_json`

### Logging / observability

- log every model call with stage + run_id
- record parse failure rate
- record schema failure rate
- record validator reject reasons
- record cost and latency histograms per model pair

### Retry behavior

- retry only on transient transport/provider errors
- do not retry on deterministic validator fatal failures
- allow 1 same-model retry for malformed structured output
- allow 1 fallback-model retry only if user enabled it

### Error handling

- provider timeout -> `retry_same_model`
- repeated structured-output failure -> `human_review_required`
- missing critical data -> `reject` or `human_review_required`
- contradiction between model output and deterministic data -> `reject`

## 4. Dashboard Requirements

Add a new "Committee" section with:

- module enable/disable
- lead analyst model selector
- skeptic model selector
- final judge selector: `rules`, `gpt`, `claude`, `manual`
- max daily budget
- max daily calls
- confidence thresholds
- human review thresholds
- escalation thresholds
- position-size and portfolio exposure thresholds
- debate history table
- side-by-side thesis / critique / rebuttal diff
- validation report panel
- final decision panel
- cost per ticker
- latency per stage
- rejection and downgrade analytics
- export watchlist / paper-trade candidates

### Reuse from existing dashboard

- existing settings framework in [dashboard/api/main.py](/Users/jason/signal_engine_v1/dashboard/api/main.py)
- existing options analytics surfaces in [dashboard/frontend/src/pages/OptionsPage.tsx](/Users/jason/signal_engine_v1/dashboard/frontend/src/pages/OptionsPage.tsx)

## 5. Model and Provider Requirements

### Current official provider status

#### OpenAI

Official docs currently show:

- `gpt-5.1`
- `gpt-5.5`
- `gpt-5.5-pro`

OpenAI recommends `gpt-5.5` as the current flagship for complex reasoning.

#### Anthropic

Official docs currently show:

- `claude-opus-4-8`
- `claude-sonnet-4-6`
- `claude-haiku-4-5`

### Recommended support matrix

| Provider | Model ID | Context | Structured outputs | Tool / function support | Pricing in / out per 1M | Best role | Recommendation |
|---|---|---:|---|---|---:|---|---|
| OpenAI | `gpt-5.1` | 400k | Yes | Yes | $1.25 / $10 | lead analyst, rebuttal | support |
| OpenAI | `gpt-5.5` | 1.05M | Yes | Yes | $5 / $30 | premium lead, premium rebuttal | default premium OpenAI |
| OpenAI | `gpt-5.5-pro` | 1.05M | Yes | Yes | $30 / $180 | not for MVP debate loop | defer |
| Anthropic | `claude-opus-4-8` | 1M | JSON consistency strong, use tool schema when possible | Yes | $5 / $25 | premium skeptic / final judge | support |
| Anthropic | `claude-sonnet-4-6` | 1M | JSON consistency strong, use tool schema when possible | Yes | $3 / $15 | default skeptic | default Anthropic |
| Anthropic | `claude-haiku-4-5` | 200k | acceptable for narrow checks | Yes | $1 / $5 | cheap classifier / triage | optional later |
| xAI | `grok-4.3` | 1M | Yes | Yes | $1.25 / $2.50 | upstream scanner or low-cost thesis generation | keep out of MVP committee |

### Position on xAI / Grok

Recommendation: keep Grok in the **earlier scanner layer**, not the committee MVP.

Reason:

- the repo already uses Grok in `ai_quant`
- Grok is cost-efficient and broad-context capable
- committee MVP should minimize heterogeneity in validation behavior
- for the investment committee, OpenAI + Anthropic gives the clearest independent model-family split

### Default pairings

- **MVP default:** `gpt-5.1` lead + `claude-sonnet-4-6` skeptic
- **Premium mode:** `gpt-5.5` lead + `claude-opus-4-8` skeptic
- **Do not default to pro models** in a 3-4 call bounded debate loop

## 6. Credentials and Environment Variables

### Required for MVP

- `DATABASE_URL`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- primary options-chain provider credentials:
  - IBKR session / gateway access, or equivalent provider key if replacing it

### Required for production

- `REDIS_URL` or equivalent queue backend
- monitoring / logging key:
  - `SENTRY_DSN` or equivalent
- alerting key:
  - Slack / Discord / Telegram webhook
- market/news provider keys with stronger freshness guarantees than yfinance alone

### Optional later

- `XAI_API_KEY`
- dark pool provider key
- premium options flow provider key
- event calendar provider key
- insider / short-interest vendor key

## 7. Cost Model

### Baseline tokens

- input: `3,558`
- output: `1,300`

### Approximate per-call cost

| Model | Per-call cost |
|---|---:|
| `gpt-5.1` | $0.0174 |
| `gpt-5.5` | $0.0568 |
| `gpt-5.5-pro` | $0.3407 |
| `claude-opus-4-8` | $0.0503 |
| `claude-sonnet-4-6` | $0.0302 |

### Full debate cost, rules-based final judge

Formula:

- `lead thesis + skeptic critique + lead rebuttal`
- cost = `2 * lead_model_call + 1 * skeptic_model_call`

| Pairing | Cost / candidate |
|---|---:|
| `gpt-5.1` + `claude-opus-4-8` | $0.0852 |
| `gpt-5.5` + `claude-opus-4-8` | $0.1639 |
| `gpt-5.1` + `claude-sonnet-4-6` | $0.0651 |
| `gpt-5.5` + `claude-sonnet-4-6` | $0.1438 |

### Add-on if final judge is an LLM

| Final judge model | Add per candidate |
|---|---:|
| rules-based | $0.0000 |
| `gpt-5.1` | $0.0174 |
| `gpt-5.5` | $0.0568 |
| `claude-sonnet-4-6` | $0.0302 |
| `claude-opus-4-8` | $0.0503 |

### Daily and monthly cost, rules-based judge

#### Scenario 1: 10 candidates/day

| Pairing | Day | 30-day month |
|---|---:|---:|
| `gpt-5.1` + `claude-opus-4-8` | $0.85 | $25.56 |
| `gpt-5.5` + `claude-opus-4-8` | $1.64 | $49.16 |
| `gpt-5.1` + `claude-sonnet-4-6` | $0.65 | $19.52 |
| `gpt-5.5` + `claude-sonnet-4-6` | $1.44 | $43.13 |

#### Scenario 2: 25 candidates/day

| Pairing | Day | 30-day month |
|---|---:|---:|
| `gpt-5.1` + `claude-opus-4-8` | $2.13 | $63.89 |
| `gpt-5.5` + `claude-opus-4-8` | $4.10 | $122.90 |
| `gpt-5.1` + `claude-sonnet-4-6` | $1.63 | $48.80 |
| `gpt-5.5` + `claude-sonnet-4-6` | $3.59 | $107.82 |

#### Scenario 3: 50 candidates/day

| Pairing | Day | 30-day month |
|---|---:|---:|
| `gpt-5.1` + `claude-opus-4-8` | $4.26 | $127.78 |
| `gpt-5.5` + `claude-opus-4-8` | $8.19 | $245.80 |
| `gpt-5.1` + `claude-sonnet-4-6` | $3.25 | $97.60 |
| `gpt-5.5` + `claude-sonnet-4-6` | $7.19 | $215.63 |

#### Scenario 4: 100 candidates/day

| Pairing | Day | 30-day month |
|---|---:|---:|
| `gpt-5.1` + `claude-opus-4-8` | $8.52 | $255.56 |
| `gpt-5.5` + `claude-opus-4-8` | $16.39 | $491.61 |
| `gpt-5.1` + `claude-sonnet-4-6` | $6.51 | $195.21 |
| `gpt-5.5` + `claude-sonnet-4-6` | $14.38 | $431.26 |

### Retry and failure impact

- one malformed-output retry adds roughly one full extra call for that stage
- 5% structured-output retry rate increases cost roughly 3%-6% depending on which stage fails
- 10% provider timeout retry rate can push monthly cost up about 5%-10%

### Prompt caching impact

- OpenAI: cached-input discount exists on standard GPT models
- Anthropic: cache hits are 10% of normal input price after a cache write
- committee prompts are a good prompt-caching fit because system prompt, schemas, and rules are mostly static

### Cost guardrails

- daily budget cap
- per-model budget cap
- skip committee on low-priority candidates
- stop final-judge call unless validator returns `escalate_to_final_judge`
- require rules-first reject path before any expensive judge escalation

## 8. JSON Schema Drafts

Store these in `committee/schemas.py`.

### A. Candidate intake object

Key fields:

- `ticker`
- `snapshot_id`
- `as_of`
- `underlying`
- `candidate_contracts[]`
- `selected_contract_ids[]`
- `signal_context`
- `risk_rules`
- `portfolio_context`
- `missing_data_flags[]`
- `source_trace`

Strict rules:

- `additionalProperties: false`
- all numeric values nullable but explicitly typed
- all traceable fields carry `source_path`

### B. Lead analyst output

- `trade_action`
- `recommended_contract_id`
- `thesis_summary`
- `supporting_factors[]`
- `risk_factors[]`
- `entry_plan`
- `exit_plan`
- `sizing_recommendation`
- `confidence`
- `citations[]`
- `uncertainty_flags[]`

### C. Skeptic critique output

- `overall_stance`
- `critique_items[]`
- `fatal_flaws[]`
- `missing_data_concerns[]`
- `recommended_change_set[]`
- `downgrade_recommendation`

### D. Rebuttal / modified trade output

- `disposition`
- `responses_to_critique[]`
- `modified_trade`
- `residual_risks[]`
- `final_confidence`

### E. Validation report

- `status`
- `schema_valid`
- `traceability_valid`
- `hard_rule_results[]`
- `fatal_flags[]`
- `warnings[]`
- `retryable_errors[]`

### F. Final committee decision

- `decision`
- `decision_reason`
- `approved_contract_id`
- `approved_size_pct`
- `required_human_review`
- `paper_trade_only`
- `watch_conditions[]`

### G. Audit log object

- `run_id`
- `input_hash`
- `stage_logs[]`
- `provider_usage[]`
- `validation_summary`
- `final_decision`

## 9. Prompt Design

### A. Lead analyst prompt

Core instructions:

- use only supplied package fields
- do not invent data
- choose only from supplied candidate contracts
- prefer conservative trade expression
- explicitly discuss IV, liquidity, event risk, and invalidation
- return strict JSON matching schema

### B. Skeptic prompt

Core instructions:

- act as independent risk officer
- attack unsupported assumptions
- identify missing data and stale-data risk
- prefer downgrade or reject when evidence quality is weak
- return strict JSON matching schema

### C. Rebuttal prompt

Core instructions:

- respond to every critique item
- accept, modify, or reject each critique explicitly
- lower confidence if data quality is borderline
- return strict JSON matching schema

### D. Final judge prompt

Use only for later phases. Input:

- thesis
- critique
- rebuttal
- validation report

Output:

- one decision
- concise reason
- no fresh trade analysis

## 10. Deterministic Validation Logic

Validation must check:

- valid JSON
- schema compliance
- no extra fields
- ticker consistency
- timestamp consistency
- all cited numbers map to known input fields
- all contract identifiers exist in input
- no unsupported news / SEC / options-flow / dark-pool claims
- probability arithmetic integrity
- confidence cap
- position-size cap
- spread threshold
- volume threshold
- open-interest threshold
- DTE bounds
- event-window rules
- portfolio exposure rules
- max loss present
- stop / invalidation present
- minimum risk/reward threshold
- final decision coherence with fatal flags

Validation outcomes:

- `pass`
- `reject`
- `retry_same_model`
- `escalate_to_final_judge`
- `human_review_required`

## 11. Gate Conditions

### Run committee when

- premium model score or upstream rank is high
- a deterministic option candidate exists
- candidate is in top N for the day
- IV rank is extreme
- event risk exists but is not auto-reject
- liquidity is borderline, not terrible
- size recommendation is large enough to matter
- earlier models disagree materially

### Skip committee when

- spread too wide
- no meaningful OI / volume
- missing critical data
- event too close per strategy rules
- poor risk/reward
- tiny position not worth premium analysis
- yfinance-only chain quality is below threshold for the selected strategy

## 12. Benchmark Plan

### MVP benchmark

- 20-50 frozen historical candidate packages
- 3 repeated runs per config
- same exact intake JSON each run
- no look-ahead leakage

### Expansion benchmark

- 200+ cases
- include both winners and obvious losers
- include earnings-adjacent and wide-spread failures

### Compare

- single-model thesis only
- GPT thesis + Claude critique
- rules-only decision
- rules + final LLM judge

### Metrics

- JSON validity rate
- schema adherence
- hallucinated-data rate
- numeric drift rate
- hard-rule violation rate
- fatal-risk detection rate
- downgrade/reject rate
- false-positive reduction
- false-negative rate
- agreement with human review
- realized return
- average R multiple
- MAE / MFE
- IV crush failure rate
- spread/slippage failure rate
- latency
- cost per accepted candidate

## 13. Missing Datapoints for High Accuracy

### Already available in repo or DB

- ticker, direction, conviction, entry / stop / targets
- option contract identity, bid/ask, mid, spread, OI, volume, IV, delta
- IV rank / percentile
- expected move
- max pain
- earnings timing
- recent news sentiment
- SEC filing summaries
- dark pool snapshots
- fundamental scores
- macro / regime context
- portfolio sizing settings

### Available in code but not consistently surfaced/persisted

- `gamma`, `theta`, `vega`
- exact chain source quality (`ibkr` vs `yfinance`) as a decision-grade gating feature
- per-contract quote completeness / stale-field flags
- contract-level `in_the_money`

### Missing and important for higher options accuracy

1. **Quote freshness timestamp per contract**
   - required to detect stale chain snapshots
2. **Greek quality / provenance**
   - broker-supplied vs approximated
3. **IV term structure**
   - front-month vs next-month IV
4. **Smile / skew context**
   - selected strike IV vs ATM IV
5. **Open-interest change and volume/OI ratio**
   - current repo has level data, not day-over-day flow quality
6. **Contract slippage estimate**
   - realized or estimated fill quality based on spread and size
7. **Corporate event calendar beyond earnings**
   - FDA dates, investor day, lockup expiry, product launch, legal rulings
8. **Short-interest freshness metadata**
   - report date, settlement lag, source timestamp
9. **Portfolio Greeks / factor overlap**
   - exposure clustering by sector, tenor, direction, and event date
10. **Historical point-in-time option chain snapshots**
    - necessary for benchmark realism
11. **News citation extraction**
    - article IDs / timestamps, not only aggregated sentiment
12. **Dark pool freshness + confidence score**
    - to distinguish same-day signal from stale read

## 14. Roadmap Phases

### Phase 0: Discovery and repo audit

Goals:

- map existing data and attach points
- define required data-quality gates

Deliverables:

- this roadmap
- gap matrix
- provider verification

Acceptance:

- approved scope and MVP architecture

### Phase 1: MVP design and backend flow

Goals:

- bounded committee workflow
- strict schemas
- deterministic validator
- rules-based final decision

Deliverables:

- `committee/` package
- DB migrations
- internal endpoint / CLI runner

Complexity: high

Risks:

- schema discipline
- provider output variance

Acceptance:

- one candidate can run end-to-end with full audit log

### Phase 2: Dashboard integration

Goals:

- operator control and visibility

Deliverables:

- settings UI
- committee run detail page
- cost / latency / reject analytics

Complexity: medium

Acceptance:

- user can configure, trigger, inspect, and export results

### Phase 3: Benchmark harness

Goals:

- measure whether committee improves decision quality

Deliverables:

- frozen snapshot runner
- repeated-run analyzer
- comparison reports

Complexity: high

Acceptance:

- benchmark can compare at least 3 model configurations on same historical set

### Phase 4: Production hardening

Goals:

- reliability and spend control

Deliverables:

- queue
- retries
- budget caps
- monitoring
- alerts

Complexity: high

Acceptance:

- graceful degradation on provider failure and hard daily spend caps

### Phase 5: Advanced committee

Goals:

- more nuanced governance

Deliverables:

- optional final LLM judge
- multiple skeptic personas
- paper-trade feedback loop
- calibration against realized outcomes

Complexity: very high

Acceptance:

- measurable improvement over MVP without large cost blowout

## 15. Implementation Tickets

### First-wave tickets

1. **Create committee DB schema**
   - Files: new migration, `schema.sql`
   - Acceptance: committee runs/messages/validation/decision tables exist

2. **Build candidate intake assembler**
   - Files: `committee/repository.py`, `committee/schemas.py`
   - Acceptance: frozen intake JSON can be built from current repo data

3. **Add provider-native structured output clients**
   - Files: `committee/providers.py`
   - Acceptance: OpenAI and Anthropic both return schema-conforming JSON

4. **Implement validator and rules-based decision engine**
   - Files: `committee/validator.py`, `committee/decision.py`
   - Acceptance: hard-rule violations force deterministic outcomes

5. **Add committee orchestrator**
   - Files: `committee/orchestrator.py`
   - Acceptance: thesis -> critique -> rebuttal -> validate -> decide runs end-to-end

### Second-wave tickets

6. **Persist gamma/theta/vega through candidate pipeline**
   - Files: `utils/option_candidates.py`, `utils/supabase_persist.py`, API serializers, tests
   - Acceptance: stored snapshots contain full Greeks when available

7. **Add committee settings to dashboard**
   - Files: `dashboard/api/main.py`, frontend settings page, API typings
   - Acceptance: user can configure model pair, budgets, thresholds

8. **Add committee run APIs**
   - Files: `dashboard/api/main.py`, `dashboard/frontend/src/lib/api.ts`
   - Acceptance: history, detail, rerun endpoints work

9. **Build committee result UI**
   - Files: new frontend page/components
   - Acceptance: side-by-side thesis, critique, rebuttal, validation, decision visible

10. **Benchmark harness**
   - Files: `committee/benchmark.py`, tests, report output
   - Acceptance: repeated-run benchmark works on frozen snapshots

## 16. Risks and Safeguards

### Major risks

- hallucination
- false confidence
- stale option chain data
- bad spread / fill assumptions
- IV crush around events
- benchmark overfitting
- cost overruns
- schema drift
- prompt injection from news text

### Safeguards

- strict schemas
- deterministic validation
- rules-based final gate for MVP
- human review on ambiguity
- no broker execution path
- full audit logs
- daily budget caps
- model disagreement flags
- source-trace checks
- stale-data and partial-data downgrade rules
- paper-trading before live decision support reliance

## 17. Final Recommendation

### Should we build it?

Yes, with one condition: treat it as a **false-positive reduction and governance module**, not a trade idea generator.

### MVP should include

- candidate intake object from current repo data
- `gpt-5.1` lead analyst
- `claude-sonnet-4-6` skeptic
- one rebuttal pass
- deterministic validator
- rules-based final gate
- full audit persistence

### Delay until later

- `gpt-5.5-pro`
- final LLM judge
- multiple skeptic personas
- autonomous retries beyond one retry
- complex tool-calling inside debate

### Default model pairing

- default: `gpt-5.1` + `claude-sonnet-4-6`
- premium toggle: `gpt-5.5` + `claude-opus-4-8`

### Final judge for MVP

- **rules-based**, not LLM-based

### First 5 tickets to implement

1. create committee tables
2. build candidate intake assembler
3. add structured-output provider clients
4. implement validator + rules gate
5. implement end-to-end orchestrator

### Expected cost range

- MVP default pairing: roughly `$20-$195/month` at `10-100` candidates/day with a rules-based final judge
- premium pairing: roughly `$49-$492/month` at `10-100` candidates/day

### Biggest technical risk

- weak data traceability and stale options-chain quality, especially when falling back to yfinance

### Biggest product risk

- users may interpret polished committee output as higher certainty than the underlying data deserves

## Sources

- OpenAI pricing: https://developers.openai.com/api/docs/pricing
- OpenAI model docs: https://developers.openai.com/api/docs/models
- OpenAI GPT-5.5: https://developers.openai.com/api/docs/models/gpt-5.5/
- OpenAI GPT-5.5 pro: https://developers.openai.com/api/docs/models/gpt-5.5-pro
- OpenAI structured outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- Anthropic models overview: https://platform.claude.com/docs/en/about-claude/models/overview
- Anthropic pricing: https://platform.claude.com/docs/en/about-claude/pricing
- Anthropic prompt caching: https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching
- Anthropic models API: https://platform.claude.com/docs/en/api/models/list
- xAI pricing: https://docs.x.ai/developers/pricing
- xAI structured outputs: https://docs.x.ai/developers/model-capabilities/text/structured-outputs
