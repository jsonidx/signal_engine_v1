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
  has_thesis: boolean
  name: string
  sector: string
  date: string | null
  direction: string | null
  conviction: number | null
  signal_agreement_score: number | null
  time_horizon: string | null
  data_quality: string | null
  thesis_short: string | null
  bull_probability: number | null
  bear_probability: number | null
  entry_low: number | null
  entry_high: number | null
  target_1: number | null
  target_2: number | null
  stop_loss: number | null
}

type DirectionFilter = 'ALL' | 'BULL' | 'BEAR' | 'NEUTRAL' | 'ANALYZED'

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

// Sort analyzed: BULL first, then NEUTRAL, then BEAR; within each group by conviction desc
const DIRECTION_ORDER: Record<string, number> = { BULL: 0, NEUTRAL: 1, BEAR: 2 }
function sortByBullFirst(a: DeepDiveTicker, b: DeepDiveTicker) {
  const da = DIRECTION_ORDER[a.direction ?? 'NEUTRAL'] ?? 1
  const db = DIRECTION_ORDER[b.direction ?? 'NEUTRAL'] ?? 1
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

function TradeSetupCells({ t }: { t: DeepDiveTicker }) {
  const entry =
    t.entry_low != null && t.entry_high != null
      ? (t.entry_low + t.entry_high) / 2
      : t.entry_low ?? t.entry_high

  if (entry == null) {
    return (
      <>
        <div className="w-20 text-center font-mono text-xs text-text-tertiary">—</div>
        <div className="w-20 text-center font-mono text-xs text-text-tertiary">—</div>
        <div className="w-20 text-center font-mono text-xs text-text-tertiary">—</div>
        <div className="w-20 text-center font-mono text-xs text-text-tertiary">—</div>
      </>
    )
  }

  const pct = (price: number) => ((price - entry) / entry) * 100
  const fmt = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`

  const t1p = t.target_1 != null ? pct(t.target_1) : null
  const t2p = t.target_2 != null ? pct(t.target_2) : null
  const sp  = t.stop_loss != null ? pct(t.stop_loss) : null
  const rr  = t1p != null && sp != null && Math.abs(sp) > 0 ? Math.abs(t1p / sp) : null

  return (
    <>
      {/* Entry */}
      <div className="w-20 text-center space-y-0.5">
        <div className="font-mono text-xs text-text-primary">${entry.toFixed(2)}</div>
        {t.entry_low != null && t.entry_high != null && (
          <div className="font-mono text-[9px] text-text-tertiary">{t.entry_low}–{t.entry_high}</div>
        )}
      </div>
      {/* T1 */}
      <div className="w-20 text-center space-y-0.5">
        {t1p != null ? (
          <>
            <div className={clsx('font-mono text-xs font-semibold', t1p >= 0 ? 'text-accent-green' : 'text-accent-red')}>{fmt(t1p)}</div>
            <div className="font-mono text-[9px] text-text-tertiary">${t.target_1!.toFixed(2)}</div>
          </>
        ) : <div className="font-mono text-xs text-text-tertiary">—</div>}
      </div>
      {/* T2 */}
      <div className="w-20 text-center space-y-0.5">
        {t2p != null ? (
          <>
            <div className={clsx('font-mono text-xs font-semibold', t2p >= 0 ? 'text-accent-green' : 'text-accent-red')}>{fmt(t2p)}</div>
            <div className="font-mono text-[9px] text-text-tertiary">${t.target_2!.toFixed(2)}</div>
          </>
        ) : <div className="font-mono text-xs text-text-tertiary">—</div>}
      </div>
      {/* Risk + R:R */}
      <div className="w-20 text-center space-y-0.5">
        {sp != null ? (
          <>
            <div className="font-mono text-xs font-semibold text-accent-red">{fmt(sp)}</div>
            {rr != null && (
              <div className={clsx('font-mono text-[9px]', rr >= 2 ? 'text-accent-green' : rr < 1 ? 'text-accent-amber' : 'text-text-tertiary')}>
                R:R {rr.toFixed(1)}
              </div>
            )}
          </>
        ) : <div className="font-mono text-xs text-text-tertiary">—</div>}
      </div>
    </>
  )
}

function TickerRow({ t, isOpen }: { t: DeepDiveTicker; isOpen: boolean }) {
  const navigate = useNavigate()

  if (!t.has_thesis) {
    // Simplified row for unanalyzed universe tickers
    return (
      <button
        onClick={() => navigate(`/ticker/${t.ticker}`)}
        className="w-full text-left bg-bg-surface/60 border border-border-subtle hover:border-border-active rounded p-3 transition-colors group opacity-70 hover:opacity-100"
      >
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-3 w-44 flex-shrink-0">
            <span className="font-mono text-base font-semibold text-text-secondary group-hover:text-accent-blue transition-colors">
              {t.ticker}
            </span>
            {isOpen && (
              <span className="font-mono text-[9px] uppercase tracking-widest text-accent-amber border border-accent-amber/40 rounded px-1 py-0.5">
                open
              </span>
            )}
          </div>
          <div className="flex-1 min-w-0 flex items-center gap-3">
            {t.name && (
              <span className="font-mono text-xs text-text-tertiary truncate">{t.name}</span>
            )}
            {t.sector && (
              <span className="font-mono text-[10px] text-text-tertiary/60 truncate hidden sm:block">{t.sector}</span>
            )}
          </div>
          <div className="font-mono text-[10px] text-text-tertiary/60 flex-shrink-0 flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-text-tertiary/40 inline-block" />
            no analysis — click to run
          </div>
        </div>
      </button>
    )
  }

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
          <DirectionBadge direction={t.direction ?? 'NEUTRAL'} size="sm" />
          {isOpen && (
            <span className="font-mono text-[9px] uppercase tracking-widest text-accent-amber border border-accent-amber/40 rounded px-1 py-0.5">
              open
            </span>
          )}
        </div>

        {/* Conviction + agreement */}
        <div className="flex items-center gap-6 w-44 flex-shrink-0">
          <ConvictionDots conviction={t.conviction ?? 0} />
          <AgreementBar score={t.signal_agreement_score} />
        </div>

        {/* Trade setup: entry / T1 / T2 / risk */}
        <div className="flex items-center gap-1 flex-shrink-0">
          <TradeSetupCells t={t} />
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
  showHeaders = true,
}: {
  label: string
  rows: DeepDiveTicker[]
  openTickers: Set<string>
  showHeaders?: boolean
}) {
  if (!rows.length) return null
  return (
    <div className="space-y-2">
      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary pt-2 pb-1 border-b border-border-subtle">
        {label} — {rows.length}
      </div>
      {showHeaders && (
        <div className="flex items-center gap-4 px-4 pb-1">
          <div className="w-44 flex-shrink-0" />
          <div className="w-44 flex-shrink-0" />
          <div className="flex items-center gap-1 flex-shrink-0">
            {(['Entry', 'T1 +%', 'T2 +%', 'Risk %'] as const).map(h => (
              <div key={h} className="w-20 text-center font-mono text-[9px] uppercase tracking-wide text-text-tertiary">{h}</div>
            ))}
          </div>
          <div className="flex-1" />
        </div>
      )}
      {rows.map(t => (
        <TickerRow key={t.ticker} t={t} isOpen={openTickers.has(t.ticker)} />
      ))}
    </div>
  )
}

const FILTER_OPTIONS: { value: DirectionFilter; label: string }[] = [
  { value: 'ALL',      label: 'All' },
  { value: 'ANALYZED', label: 'Analyzed' },
  { value: 'BULL',    label: 'Bull' },
  { value: 'BEAR',    label: 'Bear' },
  { value: 'NEUTRAL', label: 'Neutral' },
]

export function DeepDivePage() {
  const [filter, setFilter] = useState<DirectionFilter>('ALL')
  const { data: tickers, isLoading: loadingTickers } = useDeepDiveTickers()
  const { data: openTickers = [] } = useOpenPositionTickers()

  const openSet = useMemo(() => new Set(openTickers), [openTickers])

  const { analyzedRows, universeRows } = useMemo(() => {
    const all = tickers ?? []

    // Split into analyzed and unanalyzed
    const analyzed = all.filter(t => t.has_thesis)
    const universe = all.filter(t => !t.has_thesis)

    // Apply direction filter only to analyzed tickers
    const filteredAnalyzed =
      filter === 'ALL' || filter === 'ANALYZED'
        ? analyzed
        : analyzed.filter(t => t.direction === filter)

    // Universe tickers: show when filter is ALL or ANALYZED (hide for direction filters)
    const filteredUniverse =
      filter === 'ALL' ? universe : []

    // Sort analyzed section
    const sortedAnalyzed = [...filteredAnalyzed].sort(sortByBullFirst)

    // Sort universe: open positions first, then alphabetical
    const sortedUniverse = [...filteredUniverse].sort((a, b) => {
      const aOpen = openSet.has(a.ticker) ? 0 : 1
      const bOpen = openSet.has(b.ticker) ? 0 : 1
      if (aOpen !== bOpen) return aOpen - bOpen
      return a.ticker.localeCompare(b.ticker)
    })

    return { analyzedRows: sortedAnalyzed, universeRows: sortedUniverse }
  }, [tickers, filter, openSet])

  const totalShown = analyzedRows.length + universeRows.length

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
          {totalShown} ticker{totalShown !== 1 ? 's' : ''}
        </span>
      </div>

      {loadingTickers ? (
        <LoadingSkeleton rows={8} />
      ) : totalShown === 0 ? (
        <div className="font-mono text-sm text-text-tertiary py-12 text-center">
          {(tickers?.length ?? 0) === 0
            ? 'No tickers yet. Run signal_engine.py to populate the universe.'
            : `No ${filter.toLowerCase()} tickers.`}
        </div>
      ) : (
        <div className="space-y-6">
          {/* Analyzed tickers with full thesis data */}
          {analyzedRows.length > 0 && (
            <>
              {/* Split open positions from watchlist within analyzed */}
              {(() => {
                const openA = analyzedRows.filter(t => openSet.has(t.ticker))
                const watchA = analyzedRows.filter(t => !openSet.has(t.ticker))
                return (
                  <>
                    <Section label="Open Positions (Analyzed)" rows={openA} openTickers={openSet} />
                    <Section label="Watchlist (Analyzed)" rows={watchA} openTickers={openSet} />
                  </>
                )
              })()}
            </>
          )}

          {/* Universe tickers without AI analysis */}
          {universeRows.length > 0 && (
            <Section
              label="Universe — no AI analysis yet"
              rows={universeRows}
              openTickers={openSet}
              showHeaders={false}
            />
          )}
        </div>
      )}
    </Shell>
  )
}
