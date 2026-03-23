import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Shell } from '../components/layout/Shell'
import { DirectionBadge } from '../components/ui/DirectionBadge'
import { ConvictionDots } from '../components/ui/ConvictionDots'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { api } from '../lib/api'
import axios from 'axios'
import { clsx } from 'clsx'

interface DeepDiveTicker {
  ticker: string
  date: string
  direction: string
  conviction: number
  signal_agreement_score: number
  time_horizon: string
  data_quality: string
  thesis_short: string
  bull_probability: number | null
  bear_probability: number | null
}

type DirectionFilter = 'ALL' | 'BULL' | 'BEAR' | 'NEUTRAL'

function useDeepDiveTickers() {
  return useQuery({
    queryKey: ['deepdive', 'tickers'],
    queryFn: () =>
      axios.get('/api/deepdive/tickers').then(r => (r.data?.data ?? []) as DeepDiveTicker[]),
    staleTime: 5 * 60 * 1000,
  })
}

function useOpenPositionTickers() {
  return useQuery({
    queryKey: ['portfolio', 'positions'],
    queryFn: () =>
      api.portfolioPositions().then(rows =>
        Array.from(new Set(rows.map((r: { ticker: string }) => r.ticker)))
      ) as Promise<string[]>,
    staleTime: 5 * 60 * 1000,
  })
}

// Sort: BULL first, then NEUTRAL, then BEAR; within each group by conviction desc
const DIRECTION_ORDER: Record<string, number> = { BULL: 0, NEUTRAL: 1, BEAR: 2 }
function sortByBullFirst(a: DeepDiveTicker, b: DeepDiveTicker) {
  const da = DIRECTION_ORDER[a.direction] ?? 1
  const db = DIRECTION_ORDER[b.direction] ?? 1
  if (da !== db) return da - db
  return (b.conviction ?? 0) - (a.conviction ?? 0)
}

function AgreementBar({ score }: { score: number | null }) {
  if (score == null) return <span className="font-mono text-xs text-text-tertiary">—</span>
  const pct = Math.round(score * 100)
  const color = pct >= 70 ? '#22c55e' : pct >= 40 ? '#f59e0b' : '#ef4444'
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 bg-bg-elevated rounded overflow-hidden">
        <div style={{ width: `${pct}%`, background: color }} className="h-full rounded" />
      </div>
      <span className="font-mono text-xs text-text-secondary">{pct}%</span>
    </div>
  )
}

function TickerRow({ t, isOpen }: { t: DeepDiveTicker; isOpen: boolean }) {
  const navigate = useNavigate()
  return (
    <button
      onClick={() => navigate(`/ticker/${t.ticker}`)}
      className="w-full text-left bg-bg-surface border border-border-subtle hover:border-border-active rounded p-4 transition-colors group"
    >
      <div className="flex items-center gap-4">
        {/* Ticker + badges */}
        <div className="flex items-center gap-3 w-44 flex-shrink-0">
          <span className="font-mono text-lg font-semibold text-text-primary group-hover:text-accent-blue transition-colors">
            {t.ticker}
          </span>
          <DirectionBadge direction={t.direction} size="sm" />
          {isOpen && (
            <span className="font-mono text-[9px] uppercase tracking-widest text-accent-amber border border-accent-amber/40 rounded px-1 py-0.5">
              open
            </span>
          )}
        </div>

        {/* Conviction + agreement */}
        <div className="flex items-center gap-6 w-44 flex-shrink-0">
          <ConvictionDots conviction={t.conviction} />
          <AgreementBar score={t.signal_agreement_score} />
        </div>

        {/* Thesis snippet */}
        <div className="flex-1 min-w-0">
          <p className="font-mono text-xs text-text-tertiary leading-relaxed line-clamp-2">
            {t.thesis_short || 'No thesis available'}
            {t.thesis_short?.length === 160 ? '…' : ''}
          </p>
        </div>

        {/* Meta */}
        <div className="text-right flex-shrink-0 space-y-1 w-20">
          <div className="font-mono text-[10px] text-text-tertiary">{t.date}</div>
          {t.time_horizon && (
            <div className="font-mono text-[10px] text-text-tertiary">{t.time_horizon}</div>
          )}
          {t.data_quality && (
            <div className={clsx(
              'font-mono text-[10px]',
              t.data_quality === 'HIGH' ? 'text-accent-green'
                : t.data_quality === 'MEDIUM' ? 'text-accent-amber'
                : 'text-accent-red'
            )}>
              {t.data_quality}
            </div>
          )}
        </div>
      </div>
    </button>
  )
}

function Section({
  label,
  rows,
  openTickers,
}: {
  label: string
  rows: DeepDiveTicker[]
  openTickers: Set<string>
}) {
  if (!rows.length) return null
  return (
    <div className="space-y-2">
      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary pt-2 pb-1 border-b border-border-subtle">
        {label} — {rows.length}
      </div>
      {rows.map(t => (
        <TickerRow key={t.ticker} t={t} isOpen={openTickers.has(t.ticker)} />
      ))}
    </div>
  )
}

const FILTER_OPTIONS: { value: DirectionFilter; label: string }[] = [
  { value: 'ALL', label: 'All' },
  { value: 'BULL', label: 'Bull' },
  { value: 'BEAR', label: 'Bear' },
  { value: 'NEUTRAL', label: 'Neutral' },
]

export function DeepDivePage() {
  const [filter, setFilter] = useState<DirectionFilter>('ALL')
  const { data: tickers, isLoading: loadingTickers } = useDeepDiveTickers()
  const { data: openTickers = [] } = useOpenPositionTickers()

  const openSet = useMemo(() => new Set(openTickers), [openTickers])

  const filtered = useMemo(() => {
    const all = tickers ?? []
    return filter === 'ALL' ? all : all.filter(t => t.direction === filter)
  }, [tickers, filter])

  const { openRows, watchRows } = useMemo(() => {
    const open = filtered.filter(t => openSet.has(t.ticker)).sort(sortByBullFirst)
    const watch = filtered.filter(t => !openSet.has(t.ticker)).sort(sortByBullFirst)
    return { openRows: open, watchRows: watch }
  }, [filtered, openSet])

  return (
    <Shell title="Deep Dive">
      {/* Filter bar */}
      <div className="flex items-center gap-2 mb-5">
        {FILTER_OPTIONS.map(({ value, label }) => (
          <button
            key={value}
            onClick={() => setFilter(value)}
            className={clsx(
              'font-mono text-xs px-3 py-1.5 rounded border transition-colors',
              filter === value
                ? value === 'BULL'
                  ? 'bg-accent-green/20 text-accent-green border-accent-green/40'
                  : value === 'BEAR'
                    ? 'bg-accent-red/20 text-accent-red border-accent-red/40'
                    : value === 'NEUTRAL'
                      ? 'bg-text-tertiary/20 text-text-secondary border-text-tertiary/30'
                      : 'bg-bg-elevated text-text-primary border-border-active'
                : 'text-text-tertiary border-border-subtle hover:text-text-secondary hover:border-border-active'
            )}
          >
            {label}
          </button>
        ))}
        <span className="font-mono text-[10px] text-text-tertiary ml-2">
          {filtered.length} ticker{filtered.length !== 1 ? 's' : ''}
        </span>
      </div>

      {loadingTickers ? (
        <LoadingSkeleton rows={8} />
      ) : !filtered.length ? (
        <div className="font-mono text-sm text-text-tertiary py-12 text-center">
          {(tickers?.length ?? 0) === 0
            ? 'No analyzed tickers yet. Run python ai_quant.py --ticker TICKER to add one.'
            : `No ${filter.toLowerCase()} tickers.`}
        </div>
      ) : (
        <div className="space-y-6">
          <Section label="Open Positions" rows={openRows} openTickers={openSet} />
          <Section label="Watchlist" rows={watchRows} openTickers={openSet} />
        </div>
      )}
    </Shell>
  )
}
