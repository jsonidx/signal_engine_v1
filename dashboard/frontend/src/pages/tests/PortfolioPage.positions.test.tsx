import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { PortfolioPage, getSizingStatus } from '../PortfolioPage'
import { api } from '../../lib/api'
import type { EquityScreenerRow } from '../../lib/api'

// ─── Module mocks ─────────────────────────────────────────────────────────────

vi.mock('react-router-dom', async (importOriginal) => {
  const mod = await importOriginal<typeof import('react-router-dom')>()
  return { ...mod, useNavigate: () => vi.fn() }
})

vi.mock('../../lib/api', () => ({
  api: {
    portfolioSummary:     vi.fn(),
    portfolioHistory:     vi.fn().mockResolvedValue([]),
    portfolioPositions:   vi.fn(),
    screenerEquity:       vi.fn(),
    // Shell deps
    regimeCurrent: vi.fn().mockResolvedValue({
      regime: 'RISK_ON',
      score:  70,
      as_of:  '2026-03-23T00:00:00Z',
    }),
  },
}))

// ─── Helpers ──────────────────────────────────────────────────────────────────

const BASE_SUMMARY = {
  nav_eur:           50000,
  weekly_return_pct: 1.5,
  spy_return_pct:    0.8,
  sharpe_ratio:      1.2,
  max_drawdown_pct: -5.0,
  hit_rate_pct:      60,
  total_pnl_eur:     2000,
  open_positions:    2,
  as_of:            '2026-03-23',
}

function makeEquityRow(overrides: Partial<EquityScreenerRow> = {}): EquityScreenerRow {
  return {
    ticker:             'AAPL',
    rank:               1,
    composite_z:        1.5,
    momentum_12_1:      0.4,
    momentum_6_1:       0.3,
    mean_reversion_5d: -0.1,
    volatility_quality: 0.8,
    risk_adj_momentum:  0.6,
    weight_pct:         5.0,
    position_eur:       2500,
    as_of:             '20260323',
    ...overrides,
  }
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <PortfolioPage />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('PortfolioPage — Position Sizes section', () => {
  beforeEach(() => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(BASE_SUMMARY as any)
  })

  it('a) Status badge shows OVERWEIGHT when current > recommended × 1.20', async () => {
    // recommended = 2500, current = 3100 → 3100 > 2500 * 1.2 (3000) → OVERWEIGHT
    vi.mocked(api.portfolioPositions).mockResolvedValue([
      {
        ticker: 'AAPL', direction: 'BULL', entry_price: 170, current_price: 180,
        unrealized_pnl_eur: 300, unrealized_pnl_pct: 5.9, size_eur: 3100,
        days_held: 10, conviction: 4,
      },
    ] as any)
    vi.mocked(api.screenerEquity).mockResolvedValue({
      data: [makeEquityRow({ ticker: 'AAPL', weight_pct: 5.0, position_eur: 2500 })],
    })

    renderPage()
    await userEvent.click(await screen.findByRole('tab', { name: /position sizes/i }, { timeout: 5000 }))
    const badge = await screen.findByText('OVERWEIGHT', {}, { timeout: 5000 })
    expect(badge).toBeInTheDocument()
  })

  it('b) Status badge shows NOT HELD when ticker is absent from positions API', async () => {
    vi.mocked(api.portfolioPositions).mockResolvedValue([] as any)
    vi.mocked(api.screenerEquity).mockResolvedValue({
      data: [makeEquityRow({ ticker: 'MSFT', weight_pct: 4.0, position_eur: 2000 })],
    })

    renderPage()
    await userEvent.click(await screen.findByRole('tab', { name: /position sizes/i }, { timeout: 5000 }))
    const badge = await screen.findByText('NOT HELD', {}, { timeout: 5000 })
    expect(badge).toBeInTheDocument()
  })

  it('c) Warning banner appears when any ticker exceeds 8% recommended weight', async () => {
    vi.mocked(api.portfolioPositions).mockResolvedValue([] as any)
    vi.mocked(api.screenerEquity).mockResolvedValue({
      data: [makeEquityRow({ ticker: 'NVDA', weight_pct: 9.5, position_eur: 4750 })],
    })

    renderPage()
    await userEvent.click(await screen.findByRole('tab', { name: /position sizes/i }, { timeout: 5000 }))
    const banner = await screen.findByRole('alert', {}, { timeout: 5000 })
    expect(banner).toBeInTheDocument()
    expect(banner).toHaveTextContent('NVDA')
    expect(banner).toHaveTextContent('9.5%')
    expect(banner).toHaveTextContent('exceeds 8% max concentration rule')
  })

  it('d) Footnote text is present in the DOM', async () => {
    vi.mocked(api.portfolioPositions).mockResolvedValue([] as any)
    vi.mocked(api.screenerEquity).mockResolvedValue({ data: [] })

    renderPage()
    await userEvent.click(await screen.findByRole('tab', { name: /position sizes/i }, { timeout: 5000 }))
    const footnote = await screen.findByText(
      /Position sizes are Quarter-Kelly estimates\. Not financial advice\./i,
      {},
      { timeout: 5000 }
    )
    expect(footnote).toBeInTheDocument()
  })

  it('e) "Overview" tab is active by default on mount', async () => {
    vi.mocked(api.portfolioPositions).mockResolvedValue([] as any)
    vi.mocked(api.screenerEquity).mockResolvedValue({ data: [] })

    renderPage()
    const overviewTab = await screen.findByRole('tab', { name: /overview/i }, { timeout: 5000 })
    expect(overviewTab).toHaveAttribute('data-state', 'active')
    const positionSizesTab = screen.getByRole('tab', { name: /position sizes/i })
    expect(positionSizesTab).toHaveAttribute('data-state', 'inactive')
  })

  it('f) Clicking "Position Sizes" tab shows sizing table; switching back to "Overview" hides it', async () => {
    vi.mocked(api.portfolioPositions).mockResolvedValue([] as any)
    vi.mocked(api.screenerEquity).mockResolvedValue({ data: [] })

    renderPage()
    const positionSizesTab = await screen.findByRole('tab', { name: /position sizes/i }, { timeout: 5000 })

    // Click Position Sizes tab — table should appear
    await userEvent.click(positionSizesTab)
    const table = await screen.findByRole('table', { name: /position sizes/i }, { timeout: 5000 })
    expect(table).toBeInTheDocument()

    // Switch back to Overview — table should be gone
    await userEvent.click(screen.getByRole('tab', { name: /overview/i }))
    expect(screen.queryByRole('table', { name: /position sizes/i })).not.toBeInTheDocument()
  })
})

// ─── Unit tests for getSizingStatus helper ────────────────────────────────────

describe('getSizingStatus', () => {
  it('returns NOT_HELD when currentEur is 0', () => {
    expect(getSizingStatus(0, 2500)).toBe('NOT_HELD')
  })

  it('returns OVERWEIGHT when current > recommended * 1.2', () => {
    expect(getSizingStatus(3001, 2500)).toBe('OVERWEIGHT')
  })

  it('returns UNDERWEIGHT when current < recommended * 0.8', () => {
    expect(getSizingStatus(1999, 2500)).toBe('UNDERWEIGHT')
  })

  it('returns ALIGNED when current is within ±20% of recommended', () => {
    expect(getSizingStatus(2500, 2500)).toBe('ALIGNED')
    expect(getSizingStatus(3000, 2500)).toBe('ALIGNED')  // exactly 1.2x → not over
    expect(getSizingStatus(2000, 2500)).toBe('ALIGNED')  // exactly 0.8x → not under
  })
})
