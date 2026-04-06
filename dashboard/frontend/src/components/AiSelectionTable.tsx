/**
 * AiSelectionTable
 *
 * Displays today's AI Quant selection: top 5 dynamic tickers by priority score
 * plus all open positions (always included, flagged in orange).
 *
 * Data source: GET /api/signals/selection  →  candidate_snapshots (Supabase)
 *
 * Usage:
 *   import { AiSelectionTable } from '../components/AiSelectionTable'
 *   <AiSelectionTable />
 */

import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Brain, RefreshCw } from 'lucide-react'
import { clsx } from 'clsx'
import { api, type AiSelectionRow } from '../lib/api'
import { LoadingSkeleton } from './ui/LoadingSkeleton'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function directionColor(dir: string): string {
  if (dir === 'BULL')    return 'text-accent-green'
  if (dir === 'BEAR')    return 'text-accent-red'
  return 'text-text-tertiary'
}

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

// ─── Row component ────────────────────────────────────────────────────────────

function SelectionRow({
  row,
  onClick,
}: {
  row: AiSelectionRow
  onClick: () => void
}) {
  return (
    <tr
      onClick={onClick}
      className={clsx(
        'border-b border-border-subtle/50 cursor-pointer transition-colors hover:bg-bg-elevated',
        row.is_open_position && 'bg-accent-amber/5'
      )}
    >
      {/* Rank */}
      <td className="px-3 py-2.5">
        <span className={clsx(
          'font-mono text-xs font-semibold',
          row.rank <= 5 ? 'text-text-secondary' : 'text-text-tertiary'
        )}>
          {row.rank}
        </span>
      </td>

      {/* Ticker */}
      <td className="px-3 py-2.5">
        <span className="font-mono text-xs font-semibold text-accent-blue">
          {row.ticker}
        </span>
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
        <span className={clsx('font-mono text-xs font-semibold', directionColor(row.direction))}>
          {row.direction}
        </span>
      </td>

      {/* Eq. Rank */}
      <td className="px-3 py-2.5 hidden sm:table-cell">
        <span className="font-mono text-xs text-text-tertiary">
          {row.equity_rank != null ? `#${row.equity_rank}` : '—'}
        </span>
      </td>

      {/* Open position badge */}
      <td className="px-3 py-2.5">
        {row.is_open_position && (
          <span className="inline-block px-2 py-0.5 rounded font-mono text-[10px] font-semibold bg-accent-amber/15 text-accent-amber border border-accent-amber/30 whitespace-nowrap">
            ← open position
          </span>
        )}
      </td>
    </tr>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export function AiSelectionTable() {
  const navigate = useNavigate()

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey:        ['signals', 'selection'],
    queryFn:         api.signalsSelection,
    staleTime:       5  * 60 * 1000,
    refetchInterval: 15 * 60 * 1000,
    retry: 2,
  })

  // ── Loading ───────────────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div className="bg-bg-surface border border-border-subtle rounded p-4">
        <div className="flex items-center gap-2 mb-3">
          <Brain size={13} className="text-text-tertiary" />
          <span className="font-mono text-xs text-text-tertiary uppercase tracking-widest">AI Quant Selection</span>
        </div>
        <LoadingSkeleton rows={5} />
      </div>
    )
  }

  // ── Error / no data ───────────────────────────────────────────────────────
  if (isError || !data?.data_available) {
    return (
      <div className="bg-bg-surface border border-border-subtle rounded p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Brain size={13} className="text-text-tertiary" />
            <span className="font-mono text-xs text-text-tertiary uppercase tracking-widest">AI Quant Selection</span>
          </div>
          <button
            onClick={() => refetch()}
            className="font-mono text-[10px] text-text-tertiary hover:text-text-secondary transition-colors"
          >
            <RefreshCw size={11} />
          </button>
        </div>
        <p className="font-mono text-[11px] text-text-tertiary">
          {isError ? 'Failed to load selection.' : 'No selection data yet — run the pipeline first.'}
        </p>
      </div>
    )
  }

  const rows    = data.data ?? []
  const nDyn    = data.n_dynamic ?? 0
  const nOpen   = data.n_open    ?? 0
  const subtitle = nOpen > 0
    ? `Top ${nDyn} dynamic + ${nOpen} open position${nOpen !== 1 ? 's' : ''} (high attention)`
    : `Top ${nDyn} dynamic tickers for Claude synthesis`

  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
        <div className="flex items-center gap-2">
          <Brain size={13} className="text-accent-blue/70" />
          <div>
            <h2 className="font-mono text-xs font-semibold text-text-primary">
              AI Quant Selection
            </h2>
            <p className="font-mono text-[10px] text-text-tertiary mt-0.5">
              {subtitle}
            </p>
            {data.as_of && (
              <p className="font-mono text-[10px] text-text-tertiary/70 mt-0.5">
                Last updated: {new Date(data.as_of).toLocaleString()}
              </p>
            )}
          </div>
        </div>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="p-1.5 rounded border border-border-subtle text-text-tertiary hover:text-text-primary hover:border-text-tertiary transition-colors disabled:opacity-40"
          aria-label="Refresh selection"
        >
          <RefreshCw size={12} className={isFetching ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full" aria-label="AI Quant selection">
          <thead>
            <tr className="border-b border-border-subtle bg-bg-elevated/40">
              <th className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary w-8">#</th>
              <th className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary">Ticker</th>
              <th className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary" title="Priority score (0–100): weights signal agreement, conviction, regime fit, and open-position status.">Priority</th>
              <th className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary" title="Percentage of signal modules agreeing on direction (signal_engine, squeeze, catalyst, dark pool, etc.)">Agreement</th>
              <th className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary">Direction</th>
              <th className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary hidden sm:table-cell" title="Rank in today's equity screener by composite Z-score.">Eq.Rank</th>
              <th className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary">Note</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <SelectionRow
                key={row.ticker}
                row={row}
                onClick={() => navigate(`/ticker/${row.ticker}`)}
              />
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center font-mono text-xs text-text-tertiary">
                  No candidates yet — run <code>./run_master.sh</code>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
