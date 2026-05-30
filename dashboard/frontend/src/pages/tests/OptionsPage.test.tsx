/**
 * Frontend tests for OptionsPage (TRD-028 screener + TRD-029 accuracy).
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { OptionsPage } from '../OptionsPage'
import { api } from '../../lib/api'
import type {
  OptionsScreenerResponse,
  OptionsAccuracyResponse,
  OptionsCrossTickerRow,
  OptionsCohortRow,
} from '../../lib/api'

// ─── Module mocks ─────────────────────────────────────────────────────────────

vi.mock('react-router-dom', async (importOriginal) => {
  const mod = await importOriginal<typeof import('react-router-dom')>()
  return { ...mod, useNavigate: () => vi.fn() }
})

vi.mock('../../lib/api', () => ({
  api: {
    optionsScreener:  vi.fn(),
    optionsAccuracy:  vi.fn(),
    regimeCurrent: vi.fn().mockResolvedValue({
      regime: 'RISK_ON', score: 70, as_of: '2026-05-30T00:00:00Z',
    }),
    portfolioSummary: vi.fn().mockResolvedValue({
      nav_eur: 50000, weekly_return_pct: 1.0, spy_return_pct: 0.5,
      sharpe_ratio: 1.1, max_drawdown_pct: -4.0, hit_rate_pct: 60,
      total_pnl_eur: 1000, open_positions: 2, as_of: '2026-05-30',
    }),
    portfolioPositions: vi.fn().mockResolvedValue([]),
    portfolioSparklines: vi.fn().mockResolvedValue({}),
    favoritesGet: vi.fn().mockResolvedValue({ favorites: [] }),
  },
}))

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeScreenerRow(overrides: Partial<OptionsCrossTickerRow> = {}): OptionsCrossTickerRow {
  const exp = new Date()
  exp.setDate(exp.getDate() + 21)
  return {
    ticker:               'AAPL',
    expiry:               exp.toISOString().slice(0, 10),
    strike:               150,
    right:                'C',
    dte:                  21,
    bid:                  1.90,
    ask:                  2.10,
    mid:                  2.00,
    spread_pct:           9.5,
    delta:                0.40,
    implied_vol:          35,
    open_interest:        500,
    volume:               100,
    breakeven:            152.0,
    score:                72,
    rationale:            'bullish long call — Δ+0.40, IV 35%, 21d DTE',
    strategy_preset:      'long_call',
    source:               'yfinance',
    holding_window_days:  10,
    exit_by_date:         exp.toISOString().slice(0, 10),
    underlying_target_1:  160,
    underlying_target_2:  170,
    underlying_stop:      140,
    option_take_profit_1: 3.00,
    option_take_profit_2: 4.00,
    option_stop_loss:     1.00,
    max_holding_rule:     'Close 7d before expiry',
    event_exit_rule:      null,
    rank_global:          1,
    rank_within_ticker:   1,
    thesis_direction:     'BULL',
    thesis_conviction:    4,
    thesis_agreement:     0.75,
    underlying_price:     148,
    chain_source:         'yfinance',
    composite_rank_score: 74.8,
    // Execution guidance (TRD-031)
    recommended_entry_price:  1.96,
    recommended_order_type:   'limit',
    max_chase_price:          2.00,
    entry_style:              'passive',
    entry_rationale:          'Wide 9.5% spread — enter conservatively at $1.96.',
    fill_quality_score:       0.38,
    slippage_risk_label:      'high',
    skip_if_spread_above_pct: 12.0,
    ...overrides,
  }
}

function makeScreenerResponse(
  rows: OptionsCrossTickerRow[],
): OptionsScreenerResponse {
  return {
    data_available: true,
    count: rows.length,
    tickers_evaluated: rows.length,
    generated_at: '2026-05-30T12:00:00',
    data: rows,
  }
}

function makeAccuracyResponse(opts: {
  snaps?: number
  resolved?: number
  preset?: OptionsCohortRow[]
} = {}): OptionsAccuracyResponse {
  return {
    data_available: true,
    days: 90,
    total_snapshots: opts.snaps ?? 20,
    total_resolved: opts.resolved ?? 15,
    generated_at: '2026-05-30T12:00:00',
    by_preset: opts.preset ?? [
      { cohort: 'long_call', sample_size: 10, win_rate_pct: 58, tp1_rate_pct: 52, stop_rate_pct: 22, avg_option_return_5d: 7.5, avg_underlying_return_5d: 3.1 },
      { cohort: 'long_put',  sample_size: 5,  win_rate_pct: 45, tp1_rate_pct: 40, stop_rate_pct: 30, avg_option_return_5d: -2.0, avg_underlying_return_5d: -1.5 },
    ],
    by_delta_bucket:   [],
    by_dte_bucket:     [],
    by_iv_bucket:      [],
    by_spread_bucket:  [],
    by_chain_source:   [],
    by_holding_window: [],
    suppression_reasons: [{ reason: 'Conviction too low', count: 5 }],
    rejection_reasons:   [],
  }
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <OptionsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// ─── Tests: Screener tab ──────────────────────────────────────────────────────

describe('OptionsPage — Screener tab', () => {
  beforeEach(() => {
    vi.mocked(api.optionsAccuracy).mockResolvedValue(makeAccuracyResponse())
  })

  it('shows page heading', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue(makeScreenerResponse([]))
    renderPage()
    // "Options" appears in both the nav link and the h1; confirm at least one exists
    const els = await screen.findAllByText('Options')
    expect(els.length).toBeGreaterThan(0)
  })

  it('shows empty state when no candidates', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue(
      makeScreenerResponse([]),
    )
    renderPage()
    expect(await screen.findByText(/no option candidates/i)).toBeInTheDocument()
  })

  it('renders a candidate row with ticker and strategy', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue(
      makeScreenerResponse([makeScreenerRow()]),
    )
    renderPage()
    expect(await screen.findByText('AAPL')).toBeInTheDocument()
    const longCallEls = screen.getAllByText(/long call/i)
    expect(longCallEls.length).toBeGreaterThan(0)
  })

  it('renders CALL label in green', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue(
      makeScreenerResponse([makeScreenerRow({ right: 'C' })]),
    )
    renderPage()
    const callLabel = await screen.findByText('CALL')
    expect(callLabel).toHaveClass('text-accent-green')
  })

  it('renders PUT label in red', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue(
      makeScreenerResponse([makeScreenerRow({ right: 'P', strategy_preset: 'long_put', thesis_direction: 'BEAR' })]),
    )
    renderPage()
    const putLabel = await screen.findByText('PUT')
    expect(putLabel).toHaveClass('text-accent-red')
  })

  it('shows score for each candidate', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue(
      makeScreenerResponse([makeScreenerRow({ score: 72 })]),
    )
    renderPage()
    expect(await screen.findByText('72')).toBeInTheDocument()
  })

  it('shows holding window', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue(
      makeScreenerResponse([makeScreenerRow({ holding_window_days: 10 })]),
    )
    renderPage()
    expect(await screen.findByText('10d')).toBeInTheDocument()
  })

  it('shows rationale text', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue(
      makeScreenerResponse([makeScreenerRow()]),
    )
    renderPage()
    expect(await screen.findByText(/bullish long call/i)).toBeInTheDocument()
  })

  it('shows tickers evaluated count', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue({
      ...makeScreenerResponse([makeScreenerRow()]),
      tickers_evaluated: 5,
    })
    renderPage()
    expect(await screen.findByText(/5 tickers/i)).toBeInTheDocument()
  })

  it('shows error state when API fails', async () => {
    vi.mocked(api.optionsScreener).mockRejectedValue(new Error('network error'))
    renderPage()
    // EmptyState renders the error message; wait for it after query fails
    expect(
      await screen.findByText(/failed to load options screener/i, {}, { timeout: 5000 })
    ).toBeInTheDocument()
  })

  it('renders multiple candidates', async () => {
    const rows = [
      makeScreenerRow({ ticker: 'AAPL', rank_global: 1 }),
      makeScreenerRow({ ticker: 'MSFT', rank_global: 2 }),
      makeScreenerRow({ ticker: 'NVDA', rank_global: 3 }),
    ]
    vi.mocked(api.optionsScreener).mockResolvedValue(makeScreenerResponse(rows))
    renderPage()
    await screen.findByText('AAPL')
    expect(screen.getByText('MSFT')).toBeInTheDocument()
    expect(screen.getByText('NVDA')).toBeInTheDocument()
  })

  // ─── Execution guidance in screener (TRD-031) ──────────────────────────────

  it('shows recommended entry price in screener row', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue(
      makeScreenerResponse([makeScreenerRow({ recommended_entry_price: 1.96 })]),
    )
    renderPage()
    expect(await screen.findByText('$1.96')).toBeInTheDocument()
  })

  it('shows max chase price below entry', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue(
      makeScreenerResponse([makeScreenerRow({ recommended_entry_price: 1.96, max_chase_price: 2.00 })]),
    )
    renderPage()
    await screen.findByText('$1.96')
    expect(screen.getByText('≤$2.00')).toBeInTheDocument()
  })

  it('renders slippage risk badge', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue(
      makeScreenerResponse([makeScreenerRow({ slippage_risk_label: 'high' })]),
    )
    renderPage()
    expect(await screen.findByText('high')).toBeInTheDocument()
  })

  it('renders low slippage badge in green', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue(
      makeScreenerResponse([makeScreenerRow({ slippage_risk_label: 'low', recommended_entry_price: 2.03 })]),
    )
    renderPage()
    const badge = await screen.findByText('low')
    expect(badge).toHaveClass('text-accent-green')
  })

  it('shows entry/chase dash when recommended_entry_price is null', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue(
      makeScreenerResponse([makeScreenerRow({ recommended_entry_price: null, max_chase_price: null })]),
    )
    renderPage()
    await screen.findByText('AAPL')
    // The "—" dash should appear in the entry column
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThan(0)
  })

  it('shows Entry / Chase column header', async () => {
    vi.mocked(api.optionsScreener).mockResolvedValue(
      makeScreenerResponse([makeScreenerRow()]),
    )
    renderPage()
    expect(await screen.findByText(/entry \/ chase/i)).toBeInTheDocument()
  })
})

// ─── Tests: Accuracy tab ─────────────────────────────────────────────────────

describe('OptionsPage — Accuracy tab', () => {
  beforeEach(() => {
    vi.mocked(api.optionsScreener).mockResolvedValue(makeScreenerResponse([]))
  })

  async function switchToAccuracy() {
    renderPage()
    const accTab = await screen.findByRole('tab', { name: /accuracy/i })
    await userEvent.click(accTab)
  }

  it('switches to accuracy tab on click', async () => {
    vi.mocked(api.optionsAccuracy).mockResolvedValue(makeAccuracyResponse())
    await switchToAccuracy()
    expect(await screen.findByText(/by strategy preset/i)).toBeInTheDocument()
  })

  it('shows empty state when no data', async () => {
    vi.mocked(api.optionsAccuracy).mockResolvedValue({
      ...makeAccuracyResponse(),
      data_available: false,
      total_snapshots: 0,
      total_resolved: 0,
      by_preset: [],
    })
    await switchToAccuracy()
    expect(await screen.findByText(/no option accuracy data/i)).toBeInTheDocument()
  })

  it('shows snapshot and resolved counts', async () => {
    vi.mocked(api.optionsAccuracy).mockResolvedValue(
      makeAccuracyResponse({ snaps: 20, resolved: 15 }),
    )
    await switchToAccuracy()
    expect(await screen.findByText(/20 snapshots/i)).toBeInTheDocument()
    expect(screen.getByText(/15 resolved/i)).toBeInTheDocument()
  })

  it('shows win rate for preset cohort', async () => {
    vi.mocked(api.optionsAccuracy).mockResolvedValue(makeAccuracyResponse())
    await switchToAccuracy()
    // 58% win rate for long_call
    expect(await screen.findByText('58.0%')).toBeInTheDocument()
  })

  it('shows suppression reason', async () => {
    vi.mocked(api.optionsAccuracy).mockResolvedValue(
      makeAccuracyResponse(),
    )
    await switchToAccuracy()
    expect(await screen.findByText(/suppression reasons/i)).toBeInTheDocument()
    expect(screen.getByText(/conviction too low/i)).toBeInTheDocument()
  })
})
