import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { ScreenersPage } from '../ScreenersPage'
import { api } from '../../lib/api'
import type { EquityScreenerRow } from '../../lib/api'

// ─── Module mocks ─────────────────────────────────────────────────────────────

const mockNavigate = vi.fn()

vi.mock('react-router-dom', async (importOriginal) => {
  const mod = await importOriginal<typeof import('react-router-dom')>()
  return { ...mod, useNavigate: () => mockNavigate }
})

vi.mock('../../lib/api', () => ({
  api: {
    // Screeners
    screenerEquity:       vi.fn(),
    screenerSqueezeRich:  vi.fn().mockResolvedValue([]),
    screenerCatalystRich: vi.fn().mockResolvedValue([]),
    screenerOptionsRich:  vi.fn().mockResolvedValue([]),
    // Shell — regime sidebar + portfolio status bar
    regimeCurrent: vi.fn().mockResolvedValue({
      regime: 'RISK_ON',
      score:  70,
      as_of:  '2026-03-23T00:00:00Z',
    }),
    portfolioSummary: vi.fn().mockResolvedValue({
      nav_eur:            50000,
      weekly_return_pct:  1.5,
      spy_return_pct:     0.8,
      sharpe_ratio:       1.2,
      max_drawdown_pct:  -5.0,
      hit_rate_pct:       60,
      total_pnl_eur:      2000,
      open_positions:     5,
      as_of: '2026-03-23',
    }),
  },
}))

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeRows(n: number): EquityScreenerRow[] {
  return Array.from({ length: n }, (_, i) => ({
    ticker:             `T${String(i).padStart(2, '0')}`,
    rank:               i + 1,
    composite_z:        parseFloat((2.0 - i * 0.15).toFixed(2)),
    momentum_12_1:      0.5,
    momentum_6_1:       0.3,
    mean_reversion_5d:  -0.1,
    volatility_quality: 0.8,
    risk_adj_momentum:  0.6,
    weight_pct:         i < 20 ? 5.0 : null,
    position_eur:       i < 20 ? 2500 : null,
    as_of:              '20260323',
  }))
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/screeners']}>
        <ScreenersPage />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('ScreenersPage — Rankings tab', () => {
  let user: ReturnType<typeof userEvent.setup>

  beforeEach(() => {
    mockNavigate.mockClear()
    user = userEvent.setup()
    vi.mocked(api.screenerEquity).mockResolvedValue({
      data:         makeRows(25),
      generated_at: '2026-03-23T10:00:00Z',
      as_of:        '20260323',
    })
  })

  async function openRankingsTab() {
    renderPage()
    // Use userEvent (fires proper pointer events that Radix UI responds to)
    await user.click(screen.getByRole('tab', { name: /rankings/i }))
    // Wait for RankingsTab to mount and data to load
    return await screen.findByRole('table', { name: /top 20 long candidates/i }, { timeout: 5000 })
  }

  it('a) Top 20 table renders 20 data rows when API returns ≥ 20 signals', async () => {
    await openRankingsTab()

    const top20 = screen.getByRole('table', { name: /top 20 long candidates/i })
    const [, tbody] = within(top20).getAllByRole('rowgroup') // [thead, tbody]
    expect(within(tbody).getAllByRole('row')).toHaveLength(20)
  })

  it('b) Bottom 5 table renders exactly 5 data rows', async () => {
    await openRankingsTab()

    const bottom5 = screen.getByRole('table', { name: /bottom 5 short candidates/i })
    const [, tbody] = within(bottom5).getAllByRole('rowgroup')
    expect(within(tbody).getAllByRole('row')).toHaveLength(5)
  })

  it('c) Weight % column is absent from the Bottom 5 table', async () => {
    await openRankingsTab()

    const bottom5 = screen.getByRole('table', { name: /bottom 5 short candidates/i })
    expect(within(bottom5).queryByText(/weight\s*%/i)).not.toBeInTheDocument()
  })

  it('d) Clicking a ticker cell fires navigate with the correct path', async () => {
    await openRankingsTab()

    // T00 has the highest composite_z (2.0) so it is first in the Top 20
    await user.click(screen.getByTestId('ticker-T00'))

    expect(mockNavigate).toHaveBeenCalledWith('/ticker/T00')
  })
})
