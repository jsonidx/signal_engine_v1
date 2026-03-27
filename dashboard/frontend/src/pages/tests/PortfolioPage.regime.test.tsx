import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { PortfolioPage } from '../PortfolioPage'
import { api } from '../../lib/api'

// ─── Module mocks ─────────────────────────────────────────────────────────────

vi.mock('react-router-dom', async (importOriginal) => {
  const mod = await importOriginal<typeof import('react-router-dom')>()
  return { ...mod, useNavigate: () => vi.fn() }
})

const BASE_REGIME = {
  regime:           'RISK_ON' as const,
  score:            4,
  size_multiplier:  1.0,
  vix:              15.2,
  spy_vs_200ma:     0.04,
  yield_curve_spread: 0.6,
  components: {
    trend:       2,
    volatility:  1,
    credit:      1,
    yield_curve: 1,
  },
  computed_at:  '2026-03-24T06:00:00Z',
  sector_regimes: {
    tech:        'BULL',
    financials:  'BULL',
    healthcare:  'NEUTRAL',
    energy:      'BEAR',
    utilities:   'BEAR',
    materials:   'NEUTRAL',
    industrials: 'BULL',
    consumer_disc: 'BULL',
    consumer_stap: 'NEUTRAL',
    real_estate: 'BEAR',
    communication: 'BULL',
  },
  as_of: '2026-03-24T06:00:00Z',
}

const BASE_SUMMARY = {
  nav_eur:           50000,
  weekly_return_pct: 1.5,
  spy_return_pct:    0.8,
  sharpe_ratio:      1.2,
  max_drawdown_pct: -5.0,
  hit_rate_pct:      60,
  total_pnl_eur:     2000,
  open_positions:    2,
  as_of:            '2026-03-24',
}

vi.mock('../../lib/api', () => ({
  api: {
    portfolioSummary:   vi.fn(),
    portfolioHistory:   vi.fn().mockResolvedValue([]),
    portfolioPositions: vi.fn().mockResolvedValue([]),
    screenerEquity:     vi.fn().mockResolvedValue({ data: [] }),
    cashGet:            vi.fn().mockResolvedValue({ cash_eur: 10000, updated_at: null }),
    regimeCurrent:      vi.fn(),
  },
}))

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

describe('PortfolioPage — Regime Panel', () => {
  beforeEach(() => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(BASE_SUMMARY as any)
    vi.mocked(api.regimeCurrent).mockResolvedValue(BASE_REGIME as any)
  })

  it('a) Panel renders when regime data is present', async () => {
    renderPage()
    const panel = await screen.findByTestId('regime-panel', {}, { timeout: 5000 })
    expect(panel).toBeInTheDocument()

    // Regime badge text is in the header
    expect(panel).toHaveTextContent('RISK ON')

    // Size multiplier visible
    expect(panel).toHaveTextContent('1.0×')

    // All four component rows
    expect(panel).toHaveTextContent('Trend')
    expect(panel).toHaveTextContent('Volatility')
    expect(panel).toHaveTextContent('Credit Spread')
    expect(panel).toHaveTextContent('Yield Curve')
  })

  it('b) Panel shows "N/A" rows for any component absent from the mock API response', async () => {
    vi.mocked(api.regimeCurrent).mockResolvedValue({
      ...BASE_REGIME,
      components: {
        trend:      1,
        // volatility omitted
        // credit omitted
        yield_curve: -1,
      },
    } as any)

    renderPage()
    const panel = await screen.findByTestId('regime-panel', {}, { timeout: 5000 })

    // Volatility and Credit rows must still exist but show N/A
    const table = within(panel).getByRole('table', { name: /regime component breakdown/i })
    const rows = within(table).getAllByRole('row')

    // 4 data rows + 1 header = 5 rows
    expect(rows.length).toBe(5)

    // Volatility row has N/A
    const volRow = rows.find(r => r.textContent?.includes('Volatility'))
    expect(volRow).toBeDefined()
    expect(volRow!.textContent).toMatch(/N\/A/)

    // Credit Spread row has N/A
    const creditRow = rows.find(r => r.textContent?.includes('Credit Spread'))
    expect(creditRow).toBeDefined()
    expect(creditRow!.textContent).toMatch(/N\/A/)
  })

  it('c) Size multiplier is displayed correctly from the mocked response', async () => {
    vi.mocked(api.regimeCurrent).mockResolvedValue({
      ...BASE_REGIME,
      regime:          'RISK_OFF',
      size_multiplier:  0.4,
    } as any)

    renderPage()
    const panel = await screen.findByTestId('regime-panel', {}, { timeout: 5000 })
    expect(panel).toHaveTextContent('0.4×')
    // Should not show 1.0× or 0.7×
    expect(panel).not.toHaveTextContent('1.0×')
  })

  it('d) Panel collapses and expands on header click', async () => {
    renderPage()

    // Panel starts open — size multiplier visible
    const panel = await screen.findByTestId('regime-panel', {}, { timeout: 5000 })
    expect(panel).toHaveTextContent('1.0×')

    // Click the toggle button to collapse
    const toggle = within(panel).getByRole('button')
    await userEvent.click(toggle)

    // Multiplier and component table no longer visible
    expect(panel).not.toHaveTextContent('1.0×')
    expect(panel).not.toHaveTextContent('Trend')

    // Click again to re-expand
    await userEvent.click(toggle)
    expect(panel).toHaveTextContent('1.0×')
    expect(panel).toHaveTextContent('Trend')
  })
})
