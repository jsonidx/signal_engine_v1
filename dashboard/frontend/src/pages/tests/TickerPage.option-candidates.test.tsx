/**
 * Tests for the Option Candidates card and options prompt mode on TickerPage.
 * TRD-023 (card rendering) and TRD-024 (options prompt mode / guardrail).
 */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { TickerPage } from '../TickerPage'
import { api } from '../../lib/api'
import type { OptionCandidatesResponse, OptionCandidate, TickerDetail } from '../../lib/api'

// ─── Module-level mocks ────────────────────────────────────────────────────────

vi.mock('react-router-dom', async (importOriginal) => {
  const mod = await importOriginal<typeof import('react-router-dom')>()
  return { ...mod, useNavigate: () => vi.fn() }
})

// PriceChart uses lightweight-charts which accesses canvas APIs not available in jsdom.
// Stub it out so the option-candidate tests don't trigger unhandled canvas errors.
vi.mock('../../components/charts/PriceChart', () => ({
  PriceChart: () => null,
}))

// These components make secondary API calls or reference chart libs; stub to keep
// the test surface focused on option-candidate and prompt-mode behavior.
vi.mock('../../components/charts/PriceLadder', () => ({
  PriceLadder: () => null,
}))
vi.mock('../../components/RiskRewardBar', () => ({
  RiskRewardBar: () => null,
}))
vi.mock('../../components/HistoricalAnalogs', () => ({
  HistoricalAnalogs: () => null,
}))
vi.mock('../../components/EarningsReactionModel', () => ({
  EarningsReactionModel: () => null,
}))

vi.mock('../../lib/api', () => ({
  api: {
    signalsTicker:          vi.fn(),
    tickerOptionCandidates: vi.fn(),
    tickerActionZones:      vi.fn().mockResolvedValue(null),
    tickerSecFilings:       vi.fn().mockResolvedValue([]),
    tickerEarnings:         vi.fn().mockResolvedValue(null),
    tickerOHLCV:            vi.fn().mockResolvedValue(null),
    tickerEarningsReactions:vi.fn().mockResolvedValue(null),
    tickerAnalogs:          vi.fn().mockResolvedValue(null),
    tickerAnalyzeStatus:    vi.fn().mockResolvedValue({ status: 'idle', symbol: 'AAPL' }),
    darkpoolTicker:         vi.fn().mockResolvedValue([]),
    signalsHeatmap:         vi.fn().mockResolvedValue([]),
    regimeCurrent:          vi.fn().mockResolvedValue({
      regime: 'RISK_ON', score: 70, as_of: '2026-05-29T00:00:00Z',
    }),
    portfolioSummary: vi.fn().mockResolvedValue({
      nav_eur: 50000, weekly_return_pct: 1.0, spy_return_pct: 0.5,
      sharpe_ratio: 1.1, max_drawdown_pct: -4.0, hit_rate_pct: 60,
      total_pnl_eur: 1000, open_positions: 2, as_of: '2026-05-29',
    }),
    portfolioPositions: vi.fn().mockResolvedValue([]),
    portfolioSparklines: vi.fn().mockResolvedValue({}),
    favoritesGet: vi.fn().mockResolvedValue({ favorites: [] }),
    patternWatch: vi.fn().mockResolvedValue({ data_available: false, data: [] }),
    hotEntryHistory: vi.fn().mockResolvedValue({ data_available: false, data: [] }),
    rankingsHistory: vi.fn().mockResolvedValue({ data_available: false, data: [] }),
  },
}))

// ─── Helpers ──────────────────────────────────────────────────────────────────

const TODAY = new Date()
const EXPIRY_21D = new Date(TODAY.getTime() + 21 * 86400_000)
  .toISOString()
  .slice(0, 10)

function makeCandidate(overrides: Partial<OptionCandidate> = {}): OptionCandidate {
  return {
    ticker:              'AAPL',
    expiry:              EXPIRY_21D,
    strike:              150,
    right:               'C',
    dte:                 21,
    bid:                 2.00,
    ask:                 2.20,
    mid:                 2.10,
    spread_pct:          9.5,
    delta:               0.40,
    implied_vol:         35.0,
    open_interest:       500,
    volume:              100,
    breakeven:           152.10,
    score:               68,
    rationale:           'bullish long call — Δ+0.40, IV 35%, 21d DTE, spread 9.5%',
    strategy_preset:     'long_call',
    source:              'yfinance',
    // Exit plan fields (TRD-026)
    holding_window_days: 10,
    exit_by_date:        EXPIRY_21D,
    underlying_target_1: 160,
    underlying_target_2: 170,
    underlying_stop:     140,
    option_take_profit_1: 3.15,
    option_take_profit_2: 4.20,
    option_stop_loss:    1.05,
    max_holding_rule:    'Close 7d before expiry',
    event_exit_rule:     null,
    // Execution guidance (TRD-031)
    recommended_entry_price:  2.05,
    recommended_order_type:   'limit',
    max_chase_price:          2.12,   // deliberately ≠ mid (2.10) to avoid DOM ambiguity
    entry_style:              'passive',
    entry_rationale:          'Wide 9.5% spread — enter conservatively at $2.05 (below mid $2.10).',
    fill_quality_score:       0.38,
    slippage_risk_label:      'high',
    skip_if_spread_above_pct: 12.0,
    // Pre-entry buy rule (TRD-054)
    buy_decision:         'do_not_buy' as const,
    buy_decision_reason:  'Do not buy: wait for a better entry.',
    buy_decision_blocker: 'entry_quality' as const,
    ...overrides,
  }
}

function makeCandidatesResponse(
  overrides: Partial<OptionCandidatesResponse> = {},
): OptionCandidatesResponse {
  return {
    ticker:            'AAPL',
    generated_at:      '2026-05-29T12:00:00',
    suppressed:        false,
    suppression_reason: null,
    candidates:        [makeCandidate()],
    rejection_reasons: ['C 155.0 ' + EXPIRY_21D + ': OI 10 < 50 minimum'],
    underlying_price:  148.0,
    chain_source:      'yfinance',
    chain_error:       null,
    thesis_direction:  'BULL',
    thesis_conviction: 4,
    ...overrides,
  }
}

function makeSignal(overrides: Partial<TickerDetail> = {}): TickerDetail {
  return {
    data_available:         true,
    ticker:                 'AAPL',
    direction:              'BULL',
    conviction:             4,
    signal_agreement_score: 0.78,
    ai_synthesis:           'Strong bullish setup.',
    modules:                {},
    as_of:                  '2026-05-29',
    thesis:                 'Apple shows strong momentum.',
    entry_low:              145,
    entry_high:             150,
    target_1:               165,
    target_2:               175,
    stop_loss:              140,
    current_price:          148,
    bull_probability:       65,
    bear_probability:       20,
    neutral_probability:    15,
    time_horizon:           '2-4 weeks',
    ...overrides,
  } as TickerDetail
}

function renderTickerPage(symbol = 'AAPL') {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/ticker/${symbol}`]}>
        <Routes>
          <Route path="/ticker/:symbol" element={<TickerPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// ─── Tests: OptionCandidatesCard ──────────────────────────────────────────────

describe('OptionCandidatesCard', () => {
  beforeEach(() => {
    vi.mocked(api.signalsTicker).mockResolvedValue(makeSignal() as any)
  })

  it('shows loading skeleton while fetching', async () => {
    vi.mocked(api.tickerOptionCandidates).mockReturnValue(new Promise(() => {}))
    renderTickerPage()
    // Wait for signal data to render, then the loading skeleton should appear
    expect(await screen.findByText(/loading chain data/i)).toBeInTheDocument()
  })

  it('renders candidate card when API returns data', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(makeCandidatesResponse())
    renderTickerPage()

    // Card header
    expect(await screen.findByText('Option Candidates')).toBeInTheDocument()
    // Contract identifier elements
    expect(await screen.findByText('CALL')).toBeInTheDocument()
    expect(await screen.findByText('$150')).toBeInTheDocument()
  })

  it('renders mid premium value', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(makeCandidatesResponse())
    renderTickerPage()

    expect(await screen.findByText('$2.10')).toBeInTheDocument()
  })

  it('renders breakeven field', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(makeCandidatesResponse())
    renderTickerPage()

    expect(await screen.findByText('BE $152.10')).toBeInTheDocument()
  })

  it('renders strategy preset label', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(makeCandidatesResponse())
    renderTickerPage()

    expect(await screen.findByText('LONG CALL')).toBeInTheDocument()
  })

  it('renders DTE', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(makeCandidatesResponse())
    renderTickerPage()

    expect(await screen.findByText('21d')).toBeInTheDocument()
  })

  it('renders candidate rationale', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(makeCandidatesResponse())
    renderTickerPage()

    expect(
      await screen.findByText(/bullish long call/i)
    ).toBeInTheDocument()
  })

  it('renders suppression message when suppressed=true', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        suppressed: true,
        suppression_reason: 'Thesis direction is NEUTRAL — no directional option trade warranted',
        candidates: [],
      }),
    )
    renderTickerPage()

    expect(
      await screen.findByText(/thesis direction is NEUTRAL/i)
    ).toBeInTheDocument()
  })

  it('renders no-trade state when candidates list is empty', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        suppressed: false,
        suppression_reason: 'No contracts passed quality filters.',
        candidates: [],
      }),
    )
    renderTickerPage()

    expect(
      await screen.findByText(/no contracts passed quality filters/i)
    ).toBeInTheDocument()
  })

  it('renders error state when API call fails', async () => {
    vi.mocked(api.tickerOptionCandidates).mockRejectedValue(new Error('network error'))
    renderTickerPage()

    expect(await screen.findByText(/failed to load option candidates/i)).toBeInTheDocument()
  })

  it('shows yfinance chain-source banner when source is yfinance', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({ chain_source: 'yfinance' }),
    )
    renderTickerPage()

    expect(
      await screen.findByText(/Greeks approx/i)
    ).toBeInTheDocument()
  })

  it('does not show yfinance banner when source is ibkr', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({ chain_source: 'ibkr' }),
    )
    renderTickerPage()

    // Wait for card to render, then check banner absent
    await screen.findByText('Option Candidates')
    expect(screen.queryByText(/Greeks approx/i)).not.toBeInTheDocument()
  })

  it('handles missing optional fields gracefully (OI/volume null)', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({ open_interest: null, volume: null, delta: null })],
      }),
    )
    renderTickerPage()

    // Should render without throwing — mid and strike should still appear
    expect(await screen.findByText('$2.10')).toBeInTheDocument()
    expect(await screen.findByText('$150')).toBeInTheDocument()
  })

  it('renders three candidates when API returns three', async () => {
    const threeExpiries = ['2026-06-20', '2026-07-18', '2026-08-15']
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: threeExpiries.map((expiry, i) =>
          makeCandidate({ expiry, strike: 150 + i * 5, dte: 21 + i * 28 }),
        ),
      }),
    )
    renderTickerPage()

    await screen.findByText('Option Candidates')
    const rankLabels = screen.getAllByText(/^#[123]$/)
    expect(rankLabels).toHaveLength(3)
  })
})

// ─── Tests: Execution Guidance (TRD-031) ─────────────────────────────────────

describe('OptionCandidatesCard — Execution Guidance (TRD-031)', () => {
  beforeEach(() => {
    vi.mocked(api.signalsTicker).mockResolvedValue(makeSignal() as any)
  })

  it('renders recommended entry price', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({ recommended_entry_price: 2.05 })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText('$2.05')).toBeInTheDocument()
  })

  it('renders Entry Guidance section heading', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(makeCandidatesResponse())
    renderTickerPage()
    expect(await screen.findByText(/entry guidance/i)).toBeInTheDocument()
  })

  it('renders max chase price', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({ max_chase_price: 2.15, recommended_entry_price: 2.05 })],
      }),
    )
    renderTickerPage()
    await screen.findByText('$2.05')
    expect(screen.getByText('$2.15')).toBeInTheDocument()
  })

  it('renders order type label (limit)', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({ recommended_entry_price: 2.05, recommended_order_type: 'limit' })],
      }),
    )
    renderTickerPage()
    await screen.findByText('$2.05')
    expect(screen.getByText('limit')).toBeInTheDocument()
  })

  it('renders slippage risk badge', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({ slippage_risk_label: 'high', recommended_entry_price: 2.05 })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText(/high slip/i)).toBeInTheDocument()
  })

  it('renders low slippage badge in green', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          slippage_risk_label: 'low',
          recommended_entry_price: 2.03,
        })],
      }),
    )
    renderTickerPage()
    const badge = await screen.findByText(/low slip/i)
    expect(badge).toHaveClass('text-accent-green')
  })

  it('renders entry rationale text', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          entry_rationale: 'Wide 9.5% spread — enter conservatively at $2.05.',
          recommended_entry_price: 2.05,
        })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText(/wide 9\.5%/i)).toBeInTheDocument()
  })

  it('renders fill quality score', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({ fill_quality_score: 0.38, recommended_entry_price: 2.05 })],
      }),
    )
    renderTickerPage()
    await screen.findByText('$2.05')
    // fill_quality_score 0.38 → "38%"
    expect(screen.getByText('38%')).toBeInTheDocument()
  })

  it('hides entry guidance section when recommended_entry_price is null', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({ recommended_entry_price: null, max_chase_price: null })],
      }),
    )
    renderTickerPage()
    await screen.findByText('Option Candidates')
    expect(screen.queryByText(/entry guidance/i)).not.toBeInTheDocument()
  })
})

// ─── Tests: Options prompt mode (TRD-024) ─────────────────────────────────────

describe('Options prompt mode', () => {
  beforeEach(() => {
    vi.mocked(api.signalsTicker).mockResolvedValue(makeSignal() as any)
  })

  it('shows Equity / Options toggle when candidates are available', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(makeCandidatesResponse())
    renderTickerPage()

    // The toggle appears inside the CopyPromptButton compact view (AI Thesis card)
    const equityBtn = await screen.findByRole('button', { name: /^Equity$/i })
    const optionsBtn = screen.getByRole('button', { name: /^Options$/i })
    expect(equityBtn).toBeInTheDocument()
    expect(optionsBtn).toBeInTheDocument()
  })

  it('does not show toggle when candidates data is absent', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(null as any)
    renderTickerPage()

    // Wait for page to settle, then check no toggle
    await screen.findByText('Option Candidates')
    expect(screen.queryByRole('button', { name: /^Options$/i })).not.toBeInTheDocument()
  })

  it('copies options prompt containing candidate data when Options mode selected', async () => {
    const clipboardData: string[] = []
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn(async (text: string) => { clipboardData.push(text) }) },
      configurable: true,
    })

    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(makeCandidatesResponse())
    renderTickerPage()

    // Switch to options mode
    const optionsBtn = await screen.findByRole('button', { name: /^Options$/i })
    await userEvent.click(optionsBtn)

    // Click the Copy Prompt button
    const copyBtns = screen.getAllByRole('button', { name: /copy prompt/i })
    await userEvent.click(copyBtns[0])

    expect(clipboardData.length).toBeGreaterThan(0)
    const prompt = clipboardData[clipboardData.length - 1]

    // Must contain candidate identity fields
    expect(prompt).toContain('150')        // strike
    expect(prompt).toContain('long call')  // preset (lowercased in prompt)
    expect(prompt).toContain('AAPL')
  })

  it('options prompt contains ranking guardrail instruction', async () => {
    const clipboardData: string[] = []
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn(async (text: string) => { clipboardData.push(text) }) },
      configurable: true,
    })

    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(makeCandidatesResponse())
    renderTickerPage()

    const optionsBtn = await screen.findByRole('button', { name: /^Options$/i })
    await userEvent.click(optionsBtn)

    const copyBtns = screen.getAllByRole('button', { name: /copy prompt/i })
    await userEvent.click(copyBtns[0])

    const prompt = clipboardData[clipboardData.length - 1]

    // The guardrail MUST be present — LLM should rank only supplied candidates
    expect(prompt.toLowerCase()).toContain('only')
    expect(prompt).toMatch(/rank.*only|only.*rank/i)
    expect(prompt).toMatch(/do not.*(suggest|invent|introduce)/i)
  })

  it('options prompt with no candidates instructs LLM not to invent contracts', async () => {
    const clipboardData: string[] = []
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn(async (text: string) => { clipboardData.push(text) }) },
      configurable: true,
    })

    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        suppressed: true,
        suppression_reason: 'No thesis context available.',
        candidates: [],
      }),
    )
    renderTickerPage()

    const optionsBtn = await screen.findByRole('button', { name: /^Options$/i })
    await userEvent.click(optionsBtn)

    const copyBtns = screen.getAllByRole('button', { name: /copy prompt/i })
    await userEvent.click(copyBtns[0])

    const prompt = clipboardData[clipboardData.length - 1]
    // When no candidates, prompt must tell LLM NOT to suggest specific contracts.
    // The text may span two lines in the prompt, so check with a flexible pattern.
    expect(prompt).toMatch(/do NOT suggest any specific[\s\S]*option contract/i)
  })
})

// ─── Tests: V2 Projected Exits (TRD-045) ──────────────────────────────────────

describe('OptionCandidatesCard — V2 Projected Exits (TRD-045)', () => {
  beforeEach(() => {
    vi.mocked(api.signalsTicker).mockResolvedValue(makeSignal() as any)
  })

  it('renders V2 projected exits section when projected_option_tp1 and method are present', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          projected_option_tp1:   3.20,
          projected_option_tp2:   4.50,
          projected_option_stop:  1.40,
          target_projection_method: 'delta_only',
        })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText('V2 Projected Exits')).toBeInTheDocument()
    expect(screen.getByText('$3.20')).toBeInTheDocument()
    expect(screen.getByText('$4.50')).toBeInTheDocument()
    expect(screen.getByText('$1.40')).toBeInTheDocument()
  })

  it('renders method badge Δ-linear for delta_only', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          projected_option_tp1:     3.20,
          target_projection_method: 'delta_only',
        })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText('Δ-linear')).toBeInTheDocument()
  })

  it('renders method badge Δ+DTE for delta_dte_adjusted', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          projected_option_tp1:     3.20,
          target_projection_method: 'delta_dte_adjusted',
        })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText('Δ+DTE')).toBeInTheDocument()
  })

  it('does not render V2 section header when method is insufficient_inputs', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          projected_option_tp1:     3.20,
          target_projection_method: 'insufficient_inputs',
        })],
      }),
    )
    renderTickerPage()
    await screen.findByText('Option Candidates')
    expect(screen.queryByText('V2 Projected Exits')).not.toBeInTheDocument()
  })

  it('renders flat-estimate fallback section when method is insufficient_inputs', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          target_projection_method: 'insufficient_inputs',
          option_take_profit_1: 3.15,
          option_take_profit_2: 4.20,
          option_stop_loss:     1.05,
        })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText('Exits (estimated)')).toBeInTheDocument()
    expect(screen.getByText('flat')).toBeInTheDocument()
    expect(screen.getByText(/insufficient chain data for v2 projection/i)).toBeInTheDocument()
    expect(screen.getByText('$3.15')).toBeInTheDocument()
    expect(screen.getByText('$4.20')).toBeInTheDocument()
    expect(screen.getByText('$1.05')).toBeInTheDocument()
  })

  it('does not render V2 section when projected_option_tp1 is null', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          projected_option_tp1:     null,
          target_projection_method: 'delta_only',
        })],
      }),
    )
    renderTickerPage()
    await screen.findByText('Option Candidates')
    expect(screen.queryByText('V2 Projected Exits')).not.toBeInTheDocument()
  })

  it('renders return percentage alongside projected tp1 when provided', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          projected_option_tp1:      3.20,
          projected_tp1_return_pct:  56.1,
          target_projection_method:  'delta_only',
        })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText('+56.1%')).toBeInTheDocument()
  })
})

// ─── Tests: Underlying Levels Row (TRD-045) ───────────────────────────────────

describe('OptionCandidatesCard — Underlying Levels Row (TRD-045)', () => {
  beforeEach(() => {
    vi.mocked(api.signalsTicker).mockResolvedValue(makeSignal() as any)
  })

  it('renders underlying stop and target levels', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          underlying_target_1: 162,
          underlying_target_2: 175,
          underlying_stop:     138,
        })],
      }),
    )
    renderTickerPage()
    // The option card's UnderlyingLevelsRow uses "Underlying" as the row label
    const underlyingLabels = await screen.findAllByText(/^Underlying$/i)
    // at least one matches our option-card row (uppercase monospace label)
    expect(underlyingLabels.length).toBeGreaterThan(0)
    expect(screen.getByText('T1 $162.00')).toBeInTheDocument()
    expect(screen.getByText('T2 $175.00')).toBeInTheDocument()
    expect(screen.getByText('SL $138.00')).toBeInTheDocument()
  })

  it('renders holding window days when present', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({ underlying_target_1: 162, holding_window_days: 14 })],
      }),
    )
    renderTickerPage()
    await screen.findAllByText(/^Underlying$/i)
    expect(screen.getByText('hold 14d')).toBeInTheDocument()
  })

  it('hides underlying row when both t1 and stop are null', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({ underlying_target_1: null, underlying_stop: null })],
      }),
    )
    renderTickerPage()
    await screen.findByText('Option Candidates')
    // UnderlyingLevelsRow shouldn't render; "hold Xd" is its unique child
    expect(screen.queryByText(/hold \d+d/)).not.toBeInTheDocument()
  })
})

// ─── Tests: Entry Guardrail Banner (TRD-045 / TRD-049) ───────────────────────

describe('OptionCandidatesCard — Entry Guardrail Banner (TRD-045)', () => {
  beforeEach(() => {
    vi.mocked(api.signalsTicker).mockResolvedValue(makeSignal() as any)
  })

  it('renders constrained action badge for enter_if_repriced', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          entry_action:           'enter_if_repriced',
          live_guardrail_reason:  'Mid $2.10 is above FV ceiling $1.95.',
        })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText('enter if repriced')).toBeInTheDocument()
    expect(screen.getByText('Mid $2.10 is above FV ceiling $1.95.')).toBeInTheDocument()
  })

  it('renders FV band when fair_value_entry_low/high are set', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          entry_action:          'reduce_size',
          fair_value_entry_low:  1.85,
          fair_value_entry_high: 2.05,
        })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText('FV $1.85–$2.05')).toBeInTheDocument()
  })

  it('renders overpay percentage when entry_overpay_pct > 0', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          entry_action:     'enter_now',
          entry_overpay_pct: 7.5,
        })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText('overpay +7.5%')).toBeInTheDocument()
  })

  it('hides banner for enter_now when no overpay', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          entry_action:      'enter_now',
          entry_overpay_pct: null,
        })],
      }),
    )
    renderTickerPage()
    await screen.findByText('Option Candidates')
    expect(screen.queryByText('enter now')).not.toBeInTheDocument()
  })
})

// ─── Tests: Scenario Strip (TRD-047 / TRD-045) ────────────────────────────────

describe('OptionCandidatesCard — Scenario Strip (TRD-045)', () => {
  beforeEach(() => {
    vi.mocked(api.signalsTicker).mockResolvedValue(makeSignal() as any)
  })

  function makeScenario(overrides: Partial<{
    scenario_id: string; scenario_label: string; projected_return_pct: number | null;
    days_to_resolution: number; input_method: string; projected_option_price: number | null;
    scenario_weight_label: string; exit_guidance: string;
  }> = {}) {
    return {
      scenario_id:           'fast_target',
      scenario_label:        'Fast Target',
      projected_return_pct:  55,
      days_to_resolution:    7,
      input_method:          'delta_approx',
      projected_option_price: 3.25,
      scenario_weight_label: 'medium',
      exit_guidance:         'Take profit at T1.',
      ...overrides,
    }
  }

  it('renders scenario strip with path labels', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          scenarios: [
            makeScenario({ scenario_id: 'fast_target', projected_return_pct: 55 }),
            makeScenario({ scenario_id: 'slow_target', projected_return_pct: 30 }),
            makeScenario({ scenario_id: 'sideways_decay', projected_return_pct: -40 }),
            makeScenario({ scenario_id: 'adverse_stop', projected_return_pct: -50 }),
          ],
        })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText('Paths')).toBeInTheDocument()
    expect(screen.getByText('Fast')).toBeInTheDocument()
    expect(screen.getByText('Slow')).toBeInTheDocument()
    expect(screen.getByText('Sideways')).toBeInTheDocument()
    expect(screen.getByText('Adverse')).toBeInTheDocument()
  })

  it('renders return percentages for each scenario', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          scenarios: [
            makeScenario({ scenario_id: 'fast_target', projected_return_pct: 55 }),
            makeScenario({ scenario_id: 'adverse_stop', projected_return_pct: -50 }),
          ],
        })],
      }),
    )
    renderTickerPage()
    await screen.findByText('Paths')
    expect(screen.getByText('+55%')).toBeInTheDocument()
    expect(screen.getByText('-50%')).toBeInTheDocument()
  })

  it('hides scenario strip when scenarios array is empty', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({ candidates: [makeCandidate({ scenarios: [] })] }),
    )
    renderTickerPage()
    await screen.findByText('Option Candidates')
    expect(screen.queryByText('Paths')).not.toBeInTheDocument()
  })

  it('omits insufficient_inputs scenarios from strip', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          scenarios: [
            makeScenario({ scenario_id: 'fast_target', input_method: 'insufficient_inputs' }),
            makeScenario({ scenario_id: 'slow_target', projected_return_pct: 28 }),
          ],
        })],
      }),
    )
    renderTickerPage()
    await screen.findByText('Paths')
    expect(screen.queryByText('Fast')).not.toBeInTheDocument()
    expect(screen.getByText('Slow')).toBeInTheDocument()
  })

  it('renders days_to_resolution when > 0', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          scenarios: [makeScenario({ scenario_id: 'fast_target', days_to_resolution: 5 })],
        })],
      }),
    )
    renderTickerPage()
    await screen.findByText('Paths')
    expect(screen.getByText('5d')).toBeInTheDocument()
  })
})

// ─── Tests: Legacy rows not broken by TRD-045 changes ────────────────────────

describe('OptionCandidatesCard — legacy rows unaffected by TRD-045 (regression)', () => {
  beforeEach(() => {
    vi.mocked(api.signalsTicker).mockResolvedValue(makeSignal() as any)
  })

  it('still renders Trade Setup section for legacy candidates with no v2 fields', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          projected_option_tp1: null,
          projected_option_tp2: null,
          projected_option_stop: null,
          target_projection_method: null,
          scenarios: [],
          entry_action: 'enter_now',
          entry_overpay_pct: null,
        })],
      }),
    )
    renderTickerPage()
    // "T1 Profit" appears in both equity strip and option trade setup; at least one should be present
    const t1Labels = await screen.findAllByText('T1 Profit')
    expect(t1Labels.length).toBeGreaterThanOrEqual(1)
  })

  it('still renders execution guidance for legacy candidates', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          projected_option_tp1: null,
          target_projection_method: null,
          scenarios: [],
        })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText(/entry guidance/i)).toBeInTheDocument()
    expect(screen.getByText(/high slip/i)).toBeInTheDocument()
  })
})

// ─── Tests: Pre-entry Buy Decision Badge (TRD-054) ────────────────────────────

describe('OptionCandidatesCard — Buy Decision Badge (TRD-054)', () => {
  beforeEach(() => {
    vi.mocked(api.signalsTicker).mockResolvedValue(makeSignal() as any)
  })

  it('renders BUY NOW badge when buy_decision is buy_now', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          buy_decision:         'buy_now',
          buy_decision_reason:  'Buy allowed: portfolio risk policy passed and entry quality is actionable now.',
          buy_decision_blocker: null,
        })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText('BUY NOW')).toBeInTheDocument()
  })

  it('renders DO NOT BUY badge when buy_decision is do_not_buy', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          buy_decision:         'do_not_buy',
          buy_decision_reason:  'Do not buy: wait for a better entry.',
          buy_decision_blocker: 'entry_quality',
        })],
      }),
    )
    renderTickerPage()
    expect(await screen.findByText('DO NOT BUY')).toBeInTheDocument()
  })

  it('renders buy_decision_reason text alongside the badge', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          buy_decision:         'do_not_buy',
          buy_decision_reason:  'Do not buy: blocked by portfolio risk policy.',
          buy_decision_blocker: 'risk_policy',
        })],
      }),
    )
    renderTickerPage()
    await screen.findByText('DO NOT BUY')
    expect(screen.getByText('Do not buy: blocked by portfolio risk policy.')).toBeInTheDocument()
  })

  it('renders BUY NOW reason text for buy_now', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          buy_decision:         'buy_now',
          buy_decision_reason:  'Buy allowed: portfolio risk policy passed and entry quality is actionable now.',
          buy_decision_blocker: null,
        })],
      }),
    )
    renderTickerPage()
    await screen.findByText('BUY NOW')
    expect(
      screen.getByText('Buy allowed: portfolio risk policy passed and entry quality is actionable now.')
    ).toBeInTheDocument()
  })

  it('legacy row without buy_decision field does not crash', async () => {
    const candidate = makeCandidate({
      buy_decision:         undefined as any,
      buy_decision_reason:  undefined as any,
      buy_decision_blocker: undefined as any,
    })
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({ candidates: [candidate] }),
    )
    renderTickerPage()
    // Should render without throwing — option card should still show the contract
    expect(await screen.findByText('$150')).toBeInTheDocument()
  })

  it('shows DO NOT BUY for both-blocked candidate', async () => {
    vi.mocked(api.tickerOptionCandidates).mockResolvedValue(
      makeCandidatesResponse({
        candidates: [makeCandidate({
          buy_decision:         'do_not_buy',
          buy_decision_reason:  'Do not buy: blocked by both portfolio risk policy and entry quality.',
          buy_decision_blocker: 'both',
        })],
      }),
    )
    renderTickerPage()
    await screen.findByText('DO NOT BUY')
    expect(
      screen.getByText('Do not buy: blocked by both portfolio risk policy and entry quality.')
    ).toBeInTheDocument()
  })
})
