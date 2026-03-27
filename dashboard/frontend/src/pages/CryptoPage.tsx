import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Shell } from '../components/layout/Shell'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { EmptyState } from '../components/ui/EmptyState'
import { api, type CryptoTicker } from '../lib/api'
import { clsx } from 'clsx'

// ─── Types ────────────────────────────────────────────────────────────────────

type SortKey = 'signal_score' | 'rsi' | 'momentum'
type FilterMode = 'all' | 'actionable' | 'hold'

// ─── Helpers ──────────────────────────────────────────────────────────────────

const ACTION_STYLES: Record<string, string> = {
  HOLD:    'bg-accent-blue/15 text-accent-blue border-accent-blue/30',
  REDUCE:  'bg-accent-amber/15 text-accent-amber border-accent-amber/30',
  SELL:    'bg-accent-red/15 text-accent-red border-accent-red/30',
  MONITOR: 'bg-text-tertiary/10 text-text-secondary border-text-tertiary/20',
}

function ActionBadge({ action }: { action: string }) {
  const up = action?.toUpperCase() ?? 'HOLD'
  const label = up === 'BUY' ? 'MONITOR' : up
  const style = ACTION_STYLES[label] ?? ACTION_STYLES.MONITOR
  return (
    <span
      className={clsx('font-mono text-[10px] px-1.5 py-0.5 rounded border', style)}
      data-testid={`action-badge-${label}`}
    >
      {label}
    </span>
  )
}

function TrendArrow({ trend }: { trend: string }) {
  if (trend === 'UP')   return <span className="text-accent-green font-mono">↑</span>
  if (trend === 'DOWN') return <span className="text-accent-red font-mono">↓</span>
  return <span className="text-text-tertiary font-mono">→</span>
}

function CoinCard({ coin }: { coin: CryptoTicker }) {
  const label = coin.ticker.replace(/-USD$/, '')
  const momPct = (coin.momentum * 100).toFixed(2)
  const momSign = coin.momentum >= 0 ? '+' : ''

  return (
    <div
      className="bg-bg-surface border border-border-subtle rounded p-4 space-y-3"
      data-testid={`coin-card-${coin.ticker}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-mono text-sm font-semibold text-text-primary">{label}</div>
          <div className="font-mono text-xs text-text-secondary mt-0.5">
            €{coin.price_eur.toFixed(2)}
          </div>
        </div>
        <ActionBadge action={coin.action} />
      </div>

      {/* RSI + trend */}
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs text-text-tertiary">
          RSI {coin.rsi.toFixed(1)}
        </span>
        <TrendArrow trend={coin.trend} />
      </div>

      {/* Vol + momentum */}
      <div className="flex items-center gap-4 font-mono text-xs text-text-secondary">
        <span>Vol {coin.vol_pct.toFixed(1)}%</span>
        <span>Mom {momSign}{momPct}%</span>
      </div>

      {/* Score */}
      <div className="font-mono text-xs text-text-tertiary">
        Score: {coin.signal_score.toFixed(0)}
      </div>
    </div>
  )
}

// ─── Sort / filter controls ───────────────────────────────────────────────────

function SortButton({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        'px-3 py-1 text-xs font-mono rounded border transition-colors',
        active
          ? 'bg-accent-blue/20 border-accent-blue text-accent-blue'
          : 'bg-bg-surface border-border-subtle text-text-secondary hover:border-border-active'
      )}
    >
      {label}
    </button>
  )
}

function FilterButton({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        'px-3 py-1 text-xs font-mono rounded border transition-colors',
        active
          ? 'bg-accent-blue/20 border-accent-blue text-accent-blue'
          : 'bg-bg-surface border-border-subtle text-text-secondary hover:border-border-active'
      )}
    >
      {label}
    </button>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function CryptoPage() {
  const [sortKey, setSortKey] = useState<SortKey>('signal_score')
  const [filterMode, setFilterMode] = useState<FilterMode>('all')

  const { data, isLoading } = useQuery({
    queryKey: ['screeners', 'crypto'],
    queryFn:  api.screenerCrypto,
    retry:    1,
  })

  const sortedFiltered = useMemo(() => {
    if (!data?.tickers?.length) return []

    // pin BTC first; sort the rest by chosen key
    const [btc, others] = data.tickers.reduce<[CryptoTicker[], CryptoTicker[]]>(
      ([b, o], t) => (t.ticker === 'BTC-USD' ? [[...b, t], o] : [b, [...o, t]]),
      [[], []]
    )

    const sorted = [...others].sort((a, b) => {
      if (sortKey === 'signal_score') return b.signal_score - a.signal_score
      if (sortKey === 'rsi')          return b.rsi - a.rsi
      if (sortKey === 'momentum')     return b.momentum - a.momentum
      return 0
    })

    const all: CryptoTicker[] = [...btc, ...sorted]

    if (filterMode === 'actionable') {
      return all.filter(t => {
        const up = t.action?.toUpperCase()
        return up === 'REDUCE' || up === 'SELL'
      })
    }
    if (filterMode === 'hold') {
      return all.filter(t => t.action?.toUpperCase() === 'HOLD')
    }
    return all
  }, [data, sortKey, filterMode])

  const isActive = data?.btc_200ma_signal === 'ACTIVE'

  return (
    <Shell title="Crypto Signals">
      <div className="space-y-4">

        {/* BTC 200MA Banner */}
        {data && (
          <div
            className={clsx(
              'w-full rounded px-4 py-3 font-mono text-sm font-medium',
              isActive
                ? 'bg-accent-green/15 text-accent-green border border-accent-green/30'
                : 'bg-accent-red/15 text-accent-red border border-accent-red/30'
            )}
            data-testid="btc-banner"
          >
            {isActive
              ? 'BTC 200MA: ABOVE — Signal active.'
              : '⚠ BTC 200MA: BELOW — Cash signal active. No crypto exposure recommended.'}
          </div>
        )}

        {/* Disclaimer */}
        <div className="w-full rounded px-4 py-2.5 bg-bg-elevated border border-border-subtle font-mono text-xs text-text-tertiary">
          Crypto signals are for monitoring only. Multi-asset strategy retired (Sharpe −0.20).
          No individual altcoin signals are generated.
        </div>

        {/* Controls */}
        {!isLoading && !!data?.tickers?.length && (
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-xs text-text-tertiary">Sort:</span>
              <SortButton label="Score"    active={sortKey === 'signal_score'} onClick={() => setSortKey('signal_score')} />
              <SortButton label="RSI"      active={sortKey === 'rsi'}          onClick={() => setSortKey('rsi')} />
              <SortButton label="Momentum" active={sortKey === 'momentum'}     onClick={() => setSortKey('momentum')} />
            </div>
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-xs text-text-tertiary">Filter:</span>
              <FilterButton label="All"            active={filterMode === 'all'}        onClick={() => setFilterMode('all')} />
              <FilterButton label="Actionable"     active={filterMode === 'actionable'} onClick={() => setFilterMode('actionable')} />
              <FilterButton label="Hold only"      active={filterMode === 'hold'}       onClick={() => setFilterMode('hold')} />
            </div>
            {data.generated_at && (
              <span className="ml-auto font-mono text-[10px] text-text-tertiary">
                {new Date(data.generated_at).toLocaleString()}
              </span>
            )}
          </div>
        )}

        {/* Grid */}
        {isLoading ? (
          <LoadingSkeleton rows={8} />
        ) : !data || !data.tickers?.length ? (
          <EmptyState message="No crypto signals found" command="./run_master.sh" />
        ) : sortedFiltered.length === 0 ? (
          <EmptyState message="No coins match the current filter" command="" />
        ) : (
          <div
            className="grid grid-cols-1 md:grid-cols-2 gap-4"
            data-testid="crypto-grid"
          >
            {sortedFiltered.map(coin => (
              <CoinCard key={coin.ticker} coin={coin} />
            ))}
          </div>
        )}
      </div>
    </Shell>
  )
}
