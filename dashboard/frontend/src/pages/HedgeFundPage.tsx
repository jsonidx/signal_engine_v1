import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Shell } from '../components/layout/Shell'
import {
  fetchHedgeFunds,
  fetchHedgeFundPositions,
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

const CHANGE_META: Record<ChangeType, { label: string; badge: string }> = {
  new:       { label: 'New',       badge: 'bg-accent-green/15 text-accent-green border border-accent-green/30' },
  added:     { label: 'Added',     badge: 'bg-accent-blue/15 text-accent-blue border border-accent-blue/30' },
  trimmed:   { label: 'Trimmed',   badge: 'bg-amber-500/15 text-amber-400 border border-amber-500/30' },
  closed:    { label: 'Closed',    badge: 'bg-accent-red/15 text-accent-red border border-accent-red/30' },
  unchanged: { label: 'Unch.',     badge: 'bg-bg-elevated text-text-tertiary border border-border-subtle' },
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
  const sign  = delta > 0 ? '+' : ''
  const color = delta > 0 ? 'text-accent-green' : delta < 0 ? 'text-accent-red' : 'text-text-tertiary'
  const str   = type === 'value' ? fmtValue(Math.abs(delta)) : fmtShares(Math.abs(delta))
  return <span className={clsx('font-mono', color)}>{sign}{delta < 0 ? '-' : ''}{str}</span>
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
          <div className="text-[10px] text-text-tertiary truncate max-w-[160px]">{pos.name_of_issuer}</div>
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

// ─── Positions table ──────────────────────────────────────────────────────────

const CHANGE_FILTERS: Array<{ value: string; label: string }> = [
  { value: '',          label: 'All' },
  { value: 'new',       label: '🟢 New' },
  { value: 'added',     label: '➕ Added' },
  { value: 'trimmed',   label: '✂️ Trimmed' },
  { value: 'closed',    label: '🔴 Closed' },
  { value: 'unchanged', label: 'Unchanged' },
]

const INSTRUMENT_FILTERS: Array<{ value: string; label: string }> = [
  { value: '',       label: 'All' },
  { value: 'equity', label: 'Equity' },
  { value: 'call',   label: 'Call Options' },
  { value: 'put',    label: 'Put Options' },
]

function PositionsTable({ slug, fundName }: { slug: string; fundName: string }) {
  const [changeFilter, setChangeFilter]     = useState('')
  const [instrumentFilter, setInstrumentFilter] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['hf-positions', slug, changeFilter, instrumentFilter],
    queryFn: () => fetchHedgeFundPositions(slug, {
      change_type: changeFilter  || undefined,
      instrument:  instrumentFilter || undefined,
    }),
    staleTime: 60 * 60 * 1000,
  })

  const positions = data?.positions ?? []
  const period    = data?.period

  const totalValue = positions.reduce((s, p) => s + (p.value_usd ?? 0), 0)
  const counts = Object.fromEntries(
    (['new', 'added', 'trimmed', 'closed', 'unchanged'] as ChangeType[]).map(ct => [
      ct, positions.filter(p => p.change_type === ct).length
    ])
  )

  return (
    <div className="space-y-4">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium text-text-primary">{fundName}</h2>
          {period && (
            <div className="text-[11px] text-text-tertiary font-mono mt-0.5">
              Period: {period} · {positions.length} positions · {fmtValue(totalValue)} total
            </div>
          )}
        </div>

        {/* Change type pills */}
        <div className="flex gap-1.5 text-[10px] font-mono text-text-tertiary">
          {(['new', 'added', 'trimmed', 'closed'] as ChangeType[]).map(ct => (
            <span key={ct} className={clsx('px-1.5 py-0.5 rounded border', CHANGE_META[ct].badge)}>
              {CHANGE_META[ct].label}: {counts[ct] ?? 0}
            </span>
          ))}
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <div className="flex gap-1">
          {CHANGE_FILTERS.map(f => (
            <button
              key={f.value}
              onClick={() => setChangeFilter(f.value)}
              className={clsx(
                'px-2.5 py-1 text-[11px] font-mono rounded border transition-colors',
                changeFilter === f.value
                  ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/40'
                  : 'text-text-secondary border-border-subtle hover:border-border-active hover:text-text-primary'
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="flex gap-1">
          {INSTRUMENT_FILTERS.map(f => (
            <button
              key={f.value}
              onClick={() => setInstrumentFilter(f.value)}
              className={clsx(
                'px-2.5 py-1 text-[11px] font-mono rounded border transition-colors',
                instrumentFilter === f.value
                  ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/40'
                  : 'text-text-secondary border-border-subtle hover:border-border-active hover:text-text-primary'
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="shimmer h-10 rounded" />
          ))}
        </div>
      ) : positions.length === 0 ? (
        <div className="text-center py-12 text-text-tertiary font-mono text-sm">
          No positions found.{' '}
          {!data?.period && 'Run scripts/fetch_13f.py to ingest filings.'}
        </div>
      ) : (
        <div className="overflow-x-auto rounded border border-border-subtle">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-elevated">
                <th className="text-left py-2 px-3 text-[11px] font-mono text-text-tertiary uppercase tracking-wider">Issuer</th>
                <th className="text-left py-2 px-3 text-[11px] font-mono text-text-tertiary uppercase tracking-wider">Type</th>
                <th className="text-right py-2 px-3 text-[11px] font-mono text-text-tertiary uppercase tracking-wider">Value</th>
                <th className="text-right py-2 px-3 text-[11px] font-mono text-text-tertiary uppercase tracking-wider">Shares</th>
                <th className="text-right py-2 px-3 text-[11px] font-mono text-text-tertiary uppercase tracking-wider">Δ Value</th>
                <th className="text-right py-2 px-3 text-[11px] font-mono text-text-tertiary uppercase tracking-wider">Δ Shares</th>
                <th className="text-left py-2 px-3 text-[11px] font-mono text-text-tertiary uppercase tracking-wider">Change</th>
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

  const [selectedSlug, setSelectedSlug] = useState<string | null>(null)
  const activeFund = funds?.find(f => f.slug === selectedSlug) ?? funds?.[0] ?? null
  const activeSlug = activeFund?.slug ?? null

  return (
    <Shell title="Hedge Fund 13F Monitor">
      <div className="space-y-6 max-w-6xl mx-auto">

        {/* Fund selector */}
        <div className="space-y-3">
          <h1 className="text-base font-semibold text-text-primary">Tracked Funds</h1>

          {isLoading ? (
            <div className="flex gap-3">
              {Array.from({ length: 2 }).map((_, i) => (
                <div key={i} className="shimmer h-20 w-48 rounded-lg" />
              ))}
            </div>
          ) : (
            <div className="flex gap-3 flex-wrap">
              {(funds ?? []).map(fund => (
                <button
                  key={fund.slug}
                  onClick={() => setSelectedSlug(fund.slug)}
                  className={clsx(
                    'text-left px-4 py-3 rounded-lg border transition-colors w-56',
                    (activeSlug === fund.slug)
                      ? 'border-accent-blue bg-accent-blue/10'
                      : 'border-border-subtle bg-bg-surface hover:border-border-active hover:bg-bg-elevated'
                  )}
                >
                  <div className="text-xs font-medium text-text-primary truncate">{fund.name}</div>
                  <div className="text-[10px] text-text-tertiary font-mono mt-1">
                    {fund.latest_period ?? 'No data yet'}
                  </div>
                  <div className="text-[10px] text-text-secondary font-mono">
                    {fund.position_count} positions · {fund.total_value_usd > 0 ? fmtValue(fund.total_value_usd) : '—'}
                  </div>
                </button>
              ))}

              {(!funds || funds.length === 0) && (
                <div className="text-sm text-text-tertiary font-mono">
                  No funds configured. Add entries to <code>config/hedge_funds.json</code>.
                </div>
              )}
            </div>
          )}
        </div>

        {/* Info bar */}
        <div className="text-[11px] text-text-tertiary font-mono bg-bg-surface border border-border-subtle rounded px-3 py-2">
          Data sourced from SEC EDGAR 13F-HR filings. Updated weekly.
          Δ columns show change vs prior quarter. Positions in thousands USD as filed.
        </div>

        {/* Positions table */}
        {activeSlug && activeFund && (
          <PositionsTable slug={activeSlug} fundName={activeFund.name} />
        )}
      </div>
    </Shell>
  )
}
