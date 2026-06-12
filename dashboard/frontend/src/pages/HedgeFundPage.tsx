import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Shell } from '../components/layout/Shell'
import {
  fetchHedgeFunds,
  fetchHedgeFundPositions,
  HedgeFund,
  HedgeFundPosition,
} from '../lib/api'
import { clsx } from 'clsx'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtValue(usd: number): string {
  if (usd >= 1_000_000_000) return `$${(usd / 1_000_000_000).toFixed(2)}B`
  if (usd >= 1_000_000)     return `$${(usd / 1_000_000).toFixed(1)}M`
  if (usd >= 1_000)         return `$${(usd / 1_000).toFixed(0)}K`
  return `$${usd.toFixed(0)}`
}

function fmtShares(n: number | null): string {
  if (n === null || n === undefined) return '—'
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (Math.abs(n) >= 1_000)     return `${(n / 1_000).toFixed(1)}K`
  return n.toLocaleString()
}

type ChangeType = HedgeFundPosition['change_type']

const CHANGE_META: Record<ChangeType, { label: string; short: string; badge: string }> = {
  new:       { label: 'New',     short: 'New',  badge: 'bg-accent-green/15 text-accent-green border border-accent-green/30' },
  added:     { label: 'Added',   short: '+',    badge: 'bg-accent-blue/15 text-accent-blue border border-accent-blue/30' },
  trimmed:   { label: 'Trimmed', short: '↓',    badge: 'bg-amber-500/15 text-amber-400 border border-amber-500/30' },
  closed:    { label: 'Closed',  short: 'Out',  badge: 'bg-accent-red/15 text-accent-red border border-accent-red/30' },
  unchanged: { label: 'Unch.',   short: '—',    badge: 'bg-bg-elevated text-text-tertiary border border-border-subtle' },
}

function ChangeBadge({ type }: { type: ChangeType }) {
  const m = CHANGE_META[type] ?? CHANGE_META.unchanged
  return (
    <span className={clsx('text-[10px] font-mono px-1.5 py-0.5 rounded', m.badge)}>
      {m.label}
    </span>
  )
}

function DeltaCell({ delta, type }: { delta: number | null; type: 'shares' | 'value' }) {
  if (delta === null || delta === undefined) return <span className="text-text-tertiary">—</span>
  const color = delta > 0 ? 'text-accent-green' : delta < 0 ? 'text-accent-red' : 'text-text-tertiary'
  const prefix = delta > 0 ? '+' : ''
  const str = type === 'value' ? fmtValue(Math.abs(delta)) : fmtShares(Math.abs(delta))
  return <span className={clsx('font-mono', color)}>{prefix}{delta < 0 ? '-' : ''}{str}</span>
}

// ─── Position row ─────────────────────────────────────────────────────────────

function PositionRow({ pos }: { pos: HedgeFundPosition }) {
  const label = pos.ticker ?? pos.name_of_issuer ?? pos.cusip ?? '—'
  const instrument = pos.put_call ? `${pos.put_call} Option` : 'Equity'

  return (
    <tr className="border-b border-border-subtle hover:bg-bg-elevated/40 transition-colors">
      <td className="py-2.5 px-3">
        <div className="font-mono text-sm text-text-primary">{label}</div>
        {pos.ticker && pos.name_of_issuer && (
          <div className="text-[10px] text-text-tertiary truncate max-w-[180px]">{pos.name_of_issuer}</div>
        )}
      </td>
      <td className="py-2.5 px-3">
        <span className={clsx(
          'text-[10px] font-mono px-1.5 py-0.5 rounded border',
          pos.put_call === 'Put'  ? 'text-accent-red bg-accent-red/10 border-accent-red/30' :
          pos.put_call === 'Call' ? 'text-accent-green bg-accent-green/10 border-accent-green/30' :
                                   'text-text-tertiary bg-bg-elevated border-border-subtle'
        )}>
          {instrument}
        </span>
      </td>
      <td className="py-2.5 px-3 text-right font-mono text-sm text-text-primary">
        {fmtValue(pos.value_usd)}
      </td>
      <td className="py-2.5 px-3 text-right font-mono text-sm text-text-secondary">
        {fmtShares(pos.shares)}
      </td>
      <td className="py-2.5 px-3 text-right">
        <DeltaCell delta={pos.value_delta_usd} type="value" />
      </td>
      <td className="py-2.5 px-3 text-right">
        <DeltaCell delta={pos.shares_delta} type="shares" />
      </td>
      <td className="py-2.5 px-3">
        <ChangeBadge type={pos.change_type} />
      </td>
    </tr>
  )
}

// ─── Change summary pills (shown in collapsed header) ─────────────────────────

function ChangeSummaryPills({ counts }: { counts: Partial<Record<ChangeType, number>> }) {
  const active = (['new', 'added', 'trimmed', 'closed'] as ChangeType[]).filter(
    ct => (counts[ct] ?? 0) > 0
  )
  if (active.length === 0) return null
  return (
    <div className="flex gap-1.5 flex-wrap">
      {active.map(ct => (
        <span key={ct} className={clsx('text-[10px] font-mono px-1.5 py-0.5 rounded', CHANGE_META[ct].badge)}>
          {CHANGE_META[ct].label} {counts[ct]}
        </span>
      ))}
    </div>
  )
}

// ─── Filters ─────────────────────────────────────────────────────────────────

const CHANGE_FILTERS = [
  { value: '',          label: 'All changes' },
  { value: 'new',       label: '🟢 New' },
  { value: 'added',     label: '➕ Added' },
  { value: 'trimmed',   label: '✂️ Trimmed' },
  { value: 'closed',    label: '🔴 Closed' },
  { value: 'unchanged', label: 'Unchanged' },
]

const INSTRUMENT_FILTERS = [
  { value: '',       label: 'All' },
  { value: 'equity', label: 'Equity' },
  { value: 'call',   label: 'Calls' },
  { value: 'put',    label: 'Puts' },
]

function FilterBar({
  changeFilter, setChangeFilter,
  instrumentFilter, setInstrumentFilter,
}: {
  changeFilter: string; setChangeFilter: (v: string) => void
  instrumentFilter: string; setInstrumentFilter: (v: string) => void
}) {
  return (
    <div className="flex gap-3 flex-wrap items-center">
      <div className="flex gap-1">
        {CHANGE_FILTERS.map(f => (
          <button key={f.value} onClick={() => setChangeFilter(f.value)}
            className={clsx(
              'px-2.5 py-1 text-[11px] font-mono rounded border transition-colors',
              changeFilter === f.value
                ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/40'
                : 'text-text-secondary border-border-subtle hover:border-border-active hover:text-text-primary'
            )}>
            {f.label}
          </button>
        ))}
      </div>
      <div className="w-px h-4 bg-border-subtle" />
      <div className="flex gap-1">
        {INSTRUMENT_FILTERS.map(f => (
          <button key={f.value} onClick={() => setInstrumentFilter(f.value)}
            className={clsx(
              'px-2.5 py-1 text-[11px] font-mono rounded border transition-colors',
              instrumentFilter === f.value
                ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/40'
                : 'text-text-secondary border-border-subtle hover:border-border-active hover:text-text-primary'
            )}>
            {f.label}
          </button>
        ))}
      </div>
    </div>
  )
}

// ─── Ticker overlap panel ──────────────────────────────────────────────────────

function TickerOverlapPanel({ allPositions }: { allPositions: Array<{ slug: string; positions: HedgeFundPosition[] }> }) {
  if (allPositions.length < 2) return null

  // Count how many funds hold each ticker (equity only, ignore puts on same name)
  const tickerFunds: Record<string, Set<string>> = {}
  for (const { slug, positions } of allPositions) {
    for (const p of positions) {
      const key = p.ticker ?? p.cusip ?? p.name_of_issuer
      if (!key) continue
      if (!tickerFunds[key]) tickerFunds[key] = new Set()
      tickerFunds[key].add(slug)
    }
  }

  const overlaps = Object.entries(tickerFunds)
    .filter(([, funds]) => funds.size >= 2)
    .sort((a, b) => b[1].size - a[1].size)

  if (overlaps.length === 0) return null

  return (
    <div className="rounded-lg border border-accent-blue/30 bg-accent-blue/5 p-4 space-y-2">
      <div className="text-xs font-semibold text-accent-blue font-mono uppercase tracking-wider">
        Cross-Fund Overlap — {overlaps.length} shared position{overlaps.length !== 1 ? 's' : ''}
      </div>
      <div className="flex flex-wrap gap-2">
        {overlaps.map(([ticker, funds]) => (
          <div key={ticker} className="flex items-center gap-1.5 bg-bg-surface border border-border-subtle rounded px-2 py-1">
            <span className="font-mono text-sm text-text-primary">{ticker}</span>
            <span className="text-[10px] text-text-tertiary font-mono">{funds.size} funds</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Fund accordion section ───────────────────────────────────────────────────

function FundSection({
  fund,
  isOpen,
  onToggle,
  onPositionsLoaded,
}: {
  fund: HedgeFund
  isOpen: boolean
  onToggle: () => void
  onPositionsLoaded: (slug: string, positions: HedgeFundPosition[]) => void
}) {
  const [changeFilter, setChangeFilter]         = useState('')
  const [instrumentFilter, setInstrumentFilter] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['hf-positions', fund.slug, changeFilter, instrumentFilter],
    queryFn: () =>
      fetchHedgeFundPositions(fund.slug, {
        change_type: changeFilter    || undefined,
        instrument:  instrumentFilter || undefined,
      }).then(r => {
        // Pass unfiltered positions up for overlap detection (only when no filters active)
        if (!changeFilter && !instrumentFilter) {
          onPositionsLoaded(fund.slug, r.positions)
        }
        return r
      }),
    enabled: isOpen,
    staleTime: 60 * 60 * 1000,
  })

  const positions  = data?.positions ?? []
  const period     = data?.period ?? fund.latest_period
  const totalValue = positions.reduce((s, p) => s + (p.value_usd ?? 0), 0)

  // Count by change type from full (unfiltered) load for header pills
  const { data: fullData } = useQuery({
    queryKey: ['hf-positions', fund.slug, '', ''],
    queryFn:  () => fetchHedgeFundPositions(fund.slug),
    staleTime: 60 * 60 * 1000,
  })
  const allPositions = fullData?.positions ?? []
  const counts = Object.fromEntries(
    (['new', 'added', 'trimmed', 'closed', 'unchanged'] as ChangeType[]).map(ct => [
      ct, allPositions.filter(p => p.change_type === ct).length,
    ])
  ) as Partial<Record<ChangeType, number>>

  const hasData = fund.latest_period !== null

  return (
    <div className={clsx(
      'rounded-lg border transition-colors',
      isOpen ? 'border-border-active bg-bg-surface' : 'border-border-subtle bg-bg-surface hover:border-border-active'
    )}>
      {/* Accordion header */}
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 px-4 py-3.5 text-left"
      >
        <span className="text-text-tertiary flex-shrink-0">
          {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>

        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-sm font-medium text-text-primary">{fund.name}</span>
            {hasData ? (
              <span className="text-[11px] font-mono text-text-tertiary">
                {period} · {fund.position_count} pos · {fmtValue(fund.total_value_usd)}
              </span>
            ) : (
              <span className="text-[11px] font-mono text-text-tertiary italic">No data — run fetch_13f.py</span>
            )}
          </div>
        </div>

        {/* Change summary pills — visible even when collapsed */}
        {hasData && (
          <div className="flex-shrink-0">
            <ChangeSummaryPills counts={counts} />
          </div>
        )}
      </button>

      {/* Accordion body */}
      {isOpen && (
        <div className="px-4 pb-4 space-y-3 border-t border-border-subtle pt-3">
          {!hasData ? (
            <div className="text-center py-8 text-text-tertiary font-mono text-sm">
              No filings ingested yet.{' '}
              <code className="text-accent-blue">python3 scripts/fetch_13f.py --fund {fund.slug}</code>
            </div>
          ) : (
            <>
              {/* Stats row */}
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div className="text-[11px] font-mono text-text-tertiary">
                  Period: <span className="text-text-secondary">{period}</span>
                  {changeFilter || instrumentFilter ? (
                    <span className="ml-2">· {positions.length} shown (filtered)</span>
                  ) : (
                    <span className="ml-2">· {positions.length} positions · {fmtValue(totalValue)} total</span>
                  )}
                </div>
                <ChangeSummaryPills counts={counts} />
              </div>

              <FilterBar
                changeFilter={changeFilter} setChangeFilter={setChangeFilter}
                instrumentFilter={instrumentFilter} setInstrumentFilter={setInstrumentFilter}
              />

              {isLoading ? (
                <div className="space-y-2 pt-2">
                  {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="shimmer h-10 rounded" />
                  ))}
                </div>
              ) : positions.length === 0 ? (
                <div className="text-center py-8 text-text-tertiary font-mono text-sm">
                  No positions match the current filters.
                </div>
              ) : (
                <div className="overflow-x-auto rounded border border-border-subtle">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border-subtle bg-bg-elevated">
                        {['Issuer', 'Type', 'Value', 'Shares', 'Δ Value', 'Δ Shares', 'Change'].map((h, i) => (
                          <th key={h} className={clsx(
                            'py-2 px-3 text-[11px] font-mono text-text-tertiary uppercase tracking-wider',
                            i >= 2 && i <= 5 ? 'text-right' : 'text-left'
                          )}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {positions.map((pos, i) => (
                        <PositionRow key={`${pos.cusip}-${pos.put_call ?? 'eq'}-${i}`} pos={pos} />
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function HedgeFundPage() {
  const { data: funds, isLoading } = useQuery({
    queryKey: ['hedge-funds'],
    queryFn: fetchHedgeFunds,
    staleTime: 60 * 60 * 1000,
  })

  // Which fund slugs are expanded — first fund open by default once loaded
  const [openSlugs, setOpenSlugs] = useState<Set<string>>(new Set())
  const [initialised, setInitialised] = useState(false)

  if (!initialised && funds && funds.length > 0) {
    setOpenSlugs(new Set([funds[0].slug]))
    setInitialised(true)
  }

  // Positions per fund for overlap detection (populated when accordion opens with no filters)
  const [fundPositions, setFundPositions] = useState<Record<string, HedgeFundPosition[]>>({})

  const handlePositionsLoaded = (slug: string, positions: HedgeFundPosition[]) => {
    setFundPositions(prev => ({ ...prev, [slug]: positions }))
  }

  const toggleFund = (slug: string) => {
    setOpenSlugs(prev => {
      const next = new Set(prev)
      next.has(slug) ? next.delete(slug) : next.add(slug)
      return next
    })
  }

  const allLoadedPositions = Object.entries(fundPositions).map(([slug, positions]) => ({ slug, positions }))

  return (
    <Shell title="Hedge Fund 13F Monitor">
      <div className="space-y-4 max-w-6xl mx-auto">

        {/* Page header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-base font-semibold text-text-primary">Hedge Fund 13F Monitor</h1>
            <p className="text-[11px] text-text-tertiary font-mono mt-0.5">
              SEC EDGAR 13F-HR filings · updated weekly · Δ = vs prior quarter
            </p>
          </div>
          {funds && funds.length > 1 && (
            <div className="flex gap-2">
              <button
                onClick={() => setOpenSlugs(new Set(funds.map(f => f.slug)))}
                className="px-2.5 py-1 text-[11px] font-mono border border-border-subtle rounded text-text-secondary hover:text-text-primary hover:border-border-active transition-colors"
              >
                Expand all
              </button>
              <button
                onClick={() => setOpenSlugs(new Set())}
                className="px-2.5 py-1 text-[11px] font-mono border border-border-subtle rounded text-text-secondary hover:text-text-primary hover:border-border-active transition-colors"
              >
                Collapse all
              </button>
            </div>
          )}
        </div>

        {/* Cross-fund overlap panel */}
        <TickerOverlapPanel allPositions={allLoadedPositions} />

        {/* Accordion list */}
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="shimmer h-14 rounded-lg" />
            ))}
          </div>
        ) : !funds || funds.length === 0 ? (
          <div className="text-center py-16 text-text-tertiary font-mono text-sm space-y-1">
            <div>No funds configured.</div>
            <div className="text-[11px]">Add entries to <code>config/hedge_funds.json</code>.</div>
          </div>
        ) : (
          <div className="space-y-2">
            {funds.map(fund => (
              <FundSection
                key={fund.slug}
                fund={fund}
                isOpen={openSlugs.has(fund.slug)}
                onToggle={() => toggleFund(fund.slug)}
                onPositionsLoaded={handlePositionsLoaded}
              />
            ))}
          </div>
        )}
      </div>
    </Shell>
  )
}
