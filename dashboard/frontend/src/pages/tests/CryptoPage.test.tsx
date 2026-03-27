import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { CryptoPage } from '../CryptoPage'
import { api } from '../../lib/api'
import type { CryptoResponse } from '../../lib/api'

// ─── Module mocks ─────────────────────────────────────────────────────────────

vi.mock('../../lib/api', () => ({
  api: {
    screenerCrypto: vi.fn(),
    // Shell deps
    regimeCurrent: vi.fn().mockResolvedValue({
      regime: 'RISK_ON', score: 70, as_of: '2026-03-24T00:00:00Z',
    }),
    portfolioSummary: vi.fn().mockResolvedValue({
      nav_eur: 50000, weekly_return_pct: 1.5, spy_return_pct: 0.8,
      sharpe_ratio: 1.2, max_drawdown_pct: -5.0, hit_rate_pct: 60,
      total_pnl_eur: 2000, open_positions: 5, as_of: '2026-03-24',
    }),
  },
}))

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const BASE_TICKERS = [
  {
    ticker: 'BTC-USD', price_usd: 70494.12, price_eur: 64673.50,
    signal_score: 37.4, trend: 'DOWN', momentum: -0.029,
    rsi: 50.5, vol_pct: 44.7, action: 'REDUCE',
  },
  {
    ticker: 'ETH-USD', price_usd: 2131.06, price_eur: 1955.10,
    signal_score: 37.5, trend: 'DOWN', momentum: -0.028,
    rsi: 51.3, vol_pct: 60.5, action: 'REDUCE',
  },
  {
    ticker: 'LTC-USD', price_usd: 55.25, price_eur: 50.69,
    signal_score: 24.5, trend: 'DOWN', momentum: -0.024,
    rsi: 49.3, vol_pct: 46.7, action: 'SELL',
  },
  {
    ticker: 'SOL-USD', price_usd: 89.83, price_eur: 82.41,
    signal_score: 38.2, trend: 'DOWN', momentum: -0.019,
    rsi: 51.6, vol_pct: 57.6, action: 'HOLD',
  },
  {
    ticker: 'NEAR-USD', price_usd: 1.30, price_eur: 1.19,
    signal_score: 60.5, trend: 'UP', momentum: 0.002,
    rsi: 51.4, vol_pct: 93.1, action: 'HOLD',
  },
] as const

function makeResponse(
  override: Partial<CryptoResponse> = {}
): CryptoResponse {
  return {
    generated_at:     '2026-03-23T10:00:00Z',
    btc_200ma_signal: 'CASH',
    tickers:          [...BASE_TICKERS] as any,
    ...override,
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/crypto']}>
        <CryptoPage />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('CryptoPage', () => {
  let user: ReturnType<typeof userEvent.setup>

  beforeEach(() => {
    user = userEvent.setup()
    vi.mocked(api.screenerCrypto).mockResolvedValue(makeResponse())
  })

  // ── a) BTC is always first ─────────────────────────────────────────────────

  it('a) BTC card is always first in the rendered grid', async () => {
    renderPage()
    const grid = await screen.findByTestId('crypto-grid', {}, { timeout: 5000 })
    const cards = within(grid).getAllByTestId(/^coin-card-/)
    expect(cards[0]).toHaveAttribute('data-testid', 'coin-card-BTC-USD')
  })

  // ── b) BUY action renders as MONITOR badge ─────────────────────────────────

  it('b) BUY action from API renders as MONITOR badge, not BUY', async () => {
    const tickers = BASE_TICKERS.map(t =>
      t.ticker === 'ETH-USD' ? { ...t, action: 'BUY' } : t
    )
    vi.mocked(api.screenerCrypto).mockResolvedValue(makeResponse({ tickers: tickers as any }))
    renderPage()

    await screen.findByTestId('crypto-grid', {}, { timeout: 5000 })

    // MONITOR badge should exist
    expect(screen.getByTestId('action-badge-MONITOR')).toBeInTheDocument()
    // BUY badge must NOT appear
    expect(screen.queryByTestId('action-badge-BUY')).not.toBeInTheDocument()
  })

  // ── c) Red banner when btc_200ma_signal is CASH ───────────────────────────

  it('c) Red banner appears when btc_200ma_signal is CASH', async () => {
    vi.mocked(api.screenerCrypto).mockResolvedValue(
      makeResponse({ btc_200ma_signal: 'CASH' })
    )
    renderPage()

    const banner = await screen.findByTestId('btc-banner', {}, { timeout: 5000 })
    expect(banner).toHaveClass('bg-accent-red/15')
    expect(banner.textContent).toMatch(/BELOW/i)
  })

  it('c) Green banner appears when btc_200ma_signal is ACTIVE', async () => {
    vi.mocked(api.screenerCrypto).mockResolvedValue(
      makeResponse({ btc_200ma_signal: 'ACTIVE' })
    )
    renderPage()

    const banner = await screen.findByTestId('btc-banner', {}, { timeout: 5000 })
    expect(banner).toHaveClass('bg-accent-green/15')
    expect(banner.textContent).toMatch(/ABOVE/i)
  })

  // ── d) Sort by RSI re-orders the card grid ─────────────────────────────────

  it('d) Sort by RSI re-orders the non-BTC cards by RSI descending', async () => {
    renderPage()
    await screen.findByTestId('crypto-grid', {}, { timeout: 5000 })

    // Click the RSI sort button
    const rsiBtn = screen.getByRole('button', { name: /^RSI$/i })
    await user.click(rsiBtn)

    const grid = screen.getByTestId('crypto-grid')
    const cards = within(grid).getAllByTestId(/^coin-card-/)

    // BTC still pinned first
    expect(cards[0]).toHaveAttribute('data-testid', 'coin-card-BTC-USD')

    // Remaining cards should be in descending RSI order
    // RSI values: SOL=51.6, ETH=51.3, NEAR=51.4, LTC=49.3
    // Sorted desc: SOL(51.6), NEAR(51.4), ETH(51.3), LTC(49.3)
    const nonBtcTickers = cards.slice(1).map(c => c.getAttribute('data-testid'))
    const rsiMap: Record<string, number> = {
      'coin-card-ETH-USD':  51.3,
      'coin-card-LTC-USD':  49.3,
      'coin-card-SOL-USD':  51.6,
      'coin-card-NEAR-USD': 51.4,
    }
    const renderedRSIs = nonBtcTickers.map(id => rsiMap[id!] ?? 0)
    expect(renderedRSIs).toEqual([...renderedRSIs].sort((a, b) => b - a))
  })

  // ── additional: filter controls ────────────────────────────────────────────

  it('Actionable filter shows only REDUCE and SELL coins (BTC excluded too if REDUCE)', async () => {
    renderPage()
    await screen.findByTestId('crypto-grid', {}, { timeout: 5000 })

    await user.click(screen.getByRole('button', { name: /actionable/i }))

    const grid = screen.getByTestId('crypto-grid')
    const cards = within(grid).getAllByTestId(/^coin-card-/)
    const tickers = cards.map(c => c.getAttribute('data-testid'))

    // HOLD-only coins (SOL, NEAR) should not appear
    expect(tickers).not.toContain('coin-card-SOL-USD')
    expect(tickers).not.toContain('coin-card-NEAR-USD')
  })
})
