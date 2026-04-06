/**
 * CandidateSnapshotsTable
 *
 * Displays the full priority-scored candidate pool from the most recent
 * pipeline run (candidate_snapshots Supabase table, Step 13a).
 *
 * This is the raw pool *before* the final AI Quant Selection.
 * Rows marked `selected=true` made it into the final Top 5 + open positions.
 *
 * Data source: GET /api/signals/candidates
 *
 * Usage:
 *   import { CandidateSnapshotsTable } from '../components/CandidateSnapshotsTable'
 *   <CandidateSnapshotsTable />
 */

import { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, ChevronsUpDown, RefreshCw, ListFilter } from 'lucide-react'
import { clsx } from 'clsx'
import { api, type CandidateRow } from '../lib/api'
import { LoadingSkeleton } from './ui/LoadingSkeleton'

// ─── Types ────────────────────────────────────────────────────────────────────

type SortKey = 'rank' | 'priority_score' | 'agreement_pct' | 'equity_rank' | 'composite_z'
type SortDir = 'asc' | 'desc'
type DirectionFilter = 'ALL' | 'BULL' | 'BEAR' | 'NEUTRAL'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function priorityColor(score: number): string {
  if (score >= 60) return 'text-accent-green'
  if (score >= 30) return 'text-accent-amber'
  return 'text-text-secondary'
}

function agreementColor(pct: number): string {
  if (pct >= 70) return 'text-accent-green'
  if (pct >= 50) return 'text-accent-amber'
  return 'text-text-tertiary'
}

function directionColor(dir: string): string {
  if (dir === 'BULL')    return 'text-accent-green'
  if (dir === 'BEAR')    return 'text-accent-red'
  return 'text-text-tertiary'
}

function zColor(z: number): string {
  if (z >= 0.5)  return 'text-accent-green'
  if (z <= -0.5) return 'text-accent-red'
  return 'text-text-secondary'
}

// ─── Sort header ──────────────────────────────────────────────────────────────

function SortTh({
  label,
  col,
  active,
  dir,
  onSort,
  className,
  title,
}: {
  label: string
  col: SortKey
  active: SortKey
  dir: SortDir
  onSort: (c: SortKey) => void
  className?: string
  title?: string
}) {
  const isActive = col === active
  const Icon = isActive ? (dir === 'desc' ? ChevronDown : ChevronUp) : ChevronsUpDown
  return (
    <th
      className={clsx(
        'px-3 py-2.5 text-left cursor-pointer select-none group',
        className
      )}
      title={title}
      onClick={() => onSort(col)}
    >
      <div className="flex items-center gap-1">
        <span className={clsx(
          'font-mono text-[9px] uppercase tracking-widest transition-colors',
          isActive ? 'text-accent-blue' : 'text-text-tertiary group-hover:text-text-secondary'
        )}>
          {label}
        </span>
        <Icon size={9} className={clsx(
          'flex-shrink-0 transition-colors',
          isActive ? 'text-accent-blue' : 'text-text-tertiary/50 group-hover:text-text-tertiary'
        )} />
      </div>
    </th>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export function CandidateSnapshotsTable() {
  const navigate = useNavigate()

  const [sortKey,  setSortKey]  = useState<SortKey>('rank')
  const [sortDir,  setSortDir]  = useState<SortDir>('asc')
  const [dirFilter, setDirFilter] = useState<DirectionFilter>('ALL')
  const [showAllRows, setShowAllRows] = useState(false)

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey:        ['signals', 'candidates'],
    queryFn:         api.signalsCandidates,
    staleTime:       5  * 60 * 1000,
    refetchInterval: 15 * 60 * 1000,
    retry: 2,
  })

  const handleSort = (col: SortKey) => {
    if (col === sortKey) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(col); setSortDir(col === 'rank' ? 'asc' : 'desc') }
  }

  const allRows: CandidateRow[] = data?.data ?? []

  const filtered = useMemo(() => {
    let rows = dirFilter === 'ALL' ? allRows : allRows.filter(r => r.direction === dirFilter)
    rows = [...rows].sort((a, b) => {
      const av = a[sortKey] ?? 0
      const bv = b[sortKey] ?? 0
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortDir === 'asc' ? av - bv : bv - av
      }
      return 0
    })
    return rows
  }, [allRows, dirFilter, sortKey, sortDir])

  const displayed = showAllRows ? filtered : filtered.slice(0, 20)
  const nSelected = data?.n_selected ?? 0

  // ── Loading ─────────────────────────��─────────────────────────���───────────
  if (isLoading) {
    return (
      <div className="bg-bg-surface border border-border-subtle rounded p-4">
        <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-3">
          Candidate Pool
        </div>
        <LoadingSkeleton rows={8} />
      </div>
    )
  }

  // ── Error / no data ───────────────────────────────────────────────────────
  if (isError || !data?.data_available) {
    return (
      <div className="bg-bg-surface border border-border-subtle rounded p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
            Candidate Pool
          </span>
          <button onClick={() => refetch()} className="text-text-tertiary hover:text-text-secondary">
            <RefreshCw size={11} />
          </button>
        </div>
        <p className="font-mono text-[11px] text-text-tertiary">
          {isError ? 'Failed to load candidates.' : 'No candidate data yet — run the pipeline first.'}
        </p>
      </div>
    )
  }

  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">

      {/* ── Header ────────────────────���────────────────────────────────── */}
      <div className="flex items-start justify-between px-4 py-3 border-b border-border-subtle">
        <div>
          <h2 className="font-mono text-xs font-semibold text-text-primary">
            Candidate Pool — Full Scored List
          </h2>
          <p className="font-mono text-[10px] text-text-tertiary mt-0.5">
            Raw scored pool before final AI selection · {data.count} candidates
            {data.as_of && ` · ${data.as_of}`}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {/* Direction filter */}
          <div className="flex items-center gap-1">
            <ListFilter size={11} className="text-text-tertiary" />
            {(['ALL', 'BULL', 'BEAR', 'NEUTRAL'] as DirectionFilter[]).map(f => (
              <button
                key={f}
                onClick={() => setDirFilter(f)}
                className={clsx(
                  'font-mono text-[9px] px-1.5 py-0.5 rounded border transition-colors',
                  dirFilter === f
                    ? f === 'BULL'    ? 'bg-accent-green/15 text-accent-green border-accent-green/30'
                    : f === 'BEAR'    ? 'bg-accent-red/15 text-accent-red border-accent-red/30'
                    : f === 'NEUTRAL' ? 'bg-text-tertiary/10 text-text-secondary border-text-tertiary/20'
                    : 'bg-accent-blue/15 text-accent-blue border-accent-blue/30'
                    : 'text-text-tertiary border-border-subtle hover:text-text-secondary'
                )}
              >
                {f === 'ALL' ? 'All' : f}
              </button>
            ))}
          </div>

          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-1.5 rounded border border-border-subtle text-text-tertiary hover:text-text-primary transition-colors disabled:opacity-40"
          >
            <RefreshCw size={11} className={isFetching ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* ── Table ──────────────────────────────────────────────────────── */}
      <div className="overflow-x-auto">
        <table className="w-full" aria-label="Candidate snapshots">
          <thead>
            <tr className="border-b border-border-subtle bg-bg-elevated/30">
              {/* Static columns */}
              <th className="px-3 py-2.5 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary w-8">
                #
              </th>
              <th className="px-3 py-2.5 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
                Ticker
              </th>
              {/* Sortable columns */}
              <SortTh label="Priority" col="priority_score" active={sortKey} dir={sortDir} onSort={handleSort} title="Priority score (0–100): weights agreement, conviction, regime fit, and open-position status." />
              <SortTh label="Agreement" col="agreement_pct"  active={sortKey} dir={sortDir} onSort={handleSort} title="% of signal modules agreeing on direction (signal_engine, squeeze, catalyst, dark pool, etc.)" />
              <th className="px-3 py-2.5 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
                Dir
              </th>
              <SortTh label="Eq.Rank"  col="equity_rank"    active={sortKey} dir={sortDir} onSort={handleSort} className="hidden md:table-cell" title="Rank in today's equity screener by composite Z-score." />
              <SortTh label="Z-Score"  col="composite_z"    active={sortKey} dir={sortDir} onSort={handleSort} className="hidden lg:table-cell" title="Multi-factor composite Z-score from the equity screener. Positive = above cross-sectional median." />
              <th className="px-3 py-2.5 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary hidden xl:table-cell" title="Why this ticker was included: high agreement, open position, squeeze setup, etc.">
                Reason
              </th>
              <th className="px-3 py-2.5 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary" title="Whether this ticker was chosen for final Claude synthesis (top 5 + open positions).">
                Selected
              </th>
            </tr>
          </thead>
          <tbody>
            {displayed.map(row => (
              <tr
                key={row.ticker}
                onClick={() => navigate(`/ticker/${row.ticker}`)}
                className={clsx(
                  'border-b border-border-subtle/50 cursor-pointer transition-colors',
                  row.selected
                    ? 'hover:bg-accent-blue/8'
                    : 'hover:bg-bg-elevated'
                )}
              >
                {/* Rank */}
                <td className="px-3 py-2.5">
                  <span className="font-mono text-[11px] text-text-tertiary">{row.rank}</span>
                </td>

                {/* Ticker */}
                <td className="px-3 py-2.5">
                  <span className="font-mono text-xs font-semibold text-accent-blue">{row.ticker}</span>
                  {row.is_open_position && (
                    <span className="ml-1.5 inline-block font-mono text-[9px] px-1 py-0.5 rounded bg-accent-amber/15 text-accent-amber border border-accent-amber/30">
                      open
                    </span>
                  )}
                </td>

                {/* Priority */}
                <td className="px-3 py-2.5">
                  <span className={clsx('font-mono text-xs', priorityColor(row.priority_score))}>
                    {row.priority_score.toFixed(1)}
                  </span>
                </td>

                {/* Agreement */}
                <td className="px-3 py-2.5">
                  <span className={clsx('font-mono text-xs', agreementColor(row.agreement_pct))}>
                    {row.agreement_pct}%
                  </span>
                </td>

                {/* Direction */}
                <td className="px-3 py-2.5">
                  <span className={clsx('font-mono text-[11px] font-semibold', directionColor(row.direction))}>
                    {row.direction}
                  </span>
                </td>

                {/* Eq Rank */}
                <td className="px-3 py-2.5 hidden md:table-cell">
                  <span className="font-mono text-[11px] text-text-tertiary">
                    {row.equity_rank != null ? `#${row.equity_rank}` : '—'}
                  </span>
                </td>

                {/* Composite Z */}
                <td className="px-3 py-2.5 hidden lg:table-cell">
                  <span className={clsx('font-mono text-[11px]', zColor(row.composite_z))}>
                    {row.composite_z > 0 ? '+' : ''}{row.composite_z.toFixed(2)}
                  </span>
                </td>

                {/* Selection reason */}
                <td className="px-3 py-2.5 hidden xl:table-cell">
                  <span className="font-mono text-[10px] text-text-tertiary truncate block max-w-[180px]">
                    {row.selection_reason || '—'}
                  </span>
                </td>

                {/* Selected badge */}
                <td className="px-3 py-2.5">
                  {row.selected ? (
                    <span className="inline-flex items-center gap-1 font-mono text-[9px] px-1.5 py-0.5 rounded border bg-accent-blue/15 text-accent-blue border-accent-blue/30 font-semibold">
                      ✓ IN
                    </span>
                  ) : (
                    <span className="font-mono text-[10px] text-text-tertiary/40">—</span>
                  )}
                </td>
              </tr>
            ))}

            {filtered.length === 0 && (
              <tr>
                <td colSpan={9} className="px-4 py-8 text-center font-mono text-xs text-text-tertiary">
                  No candidates match the selected filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* ── Footer: show more / legend ──────────────────��───────────────── */}
      <div className="px-4 py-2.5 border-t border-border-subtle/50 bg-bg-elevated/30 flex items-center justify-between gap-4 flex-wrap">
        <p className="font-mono text-[10px] text-text-tertiary">
          <span className="inline-flex items-center gap-1 mr-3">
            <span className="font-mono text-[9px] px-1.5 py-0.5 rounded border bg-accent-blue/15 text-accent-blue border-accent-blue/30">✓ IN</span>
            = entered final AI selection
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="font-mono text-[9px] px-1 py-0.5 rounded bg-accent-amber/15 text-accent-amber border border-accent-amber/30">open</span>
            = open position (always included)
          </span>
        </p>

        {filtered.length > 20 && (
          <button
            onClick={() => setShowAllRows(v => !v)}
            className="font-mono text-[10px] text-accent-blue hover:underline flex-shrink-0"
          >
            {showAllRows
              ? `Show top 20`
              : `Show all ${filtered.length} candidates`}
          </button>
        )}

        <span className="font-mono text-[10px] text-text-tertiary ml-auto">
          {nSelected} selected · {data.count} total
        </span>
      </div>
    </div>
  )
}
