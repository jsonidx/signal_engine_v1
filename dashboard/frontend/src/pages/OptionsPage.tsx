/**
 * OptionsPage — Cross-ticker options screener and accuracy analytics.
 * TRD-028 (screener tab) + TRD-029 (accuracy tab)
 *
 * Layout:
 *   Tab 1: Screener — ranked option candidates across thesis-filtered tickers
 *   Tab 2: Accuracy — cohort analytics from persisted snapshots + outcomes
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import * as Tabs from '@radix-ui/react-tabs'
import { useNavigate } from 'react-router-dom'
import { Shell } from '../components/layout/Shell'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { EmptyState } from '../components/ui/EmptyState'
import { DirectionBadge } from '../components/ui/DirectionBadge'
import { api } from '../lib/api'
import type {
  OptionsCrossTickerRow,
  OptionsCohortRow,
  OptionsFreqRow,
  OptionsAccuracyResponse,
} from '../lib/api'
import { clsx } from 'clsx'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmt(v: number | null | undefined, d = 2): string {
  return v != null ? v.toFixed(d) : '—'
}
function fmtPct(v: number | null | undefined): string {
  return v != null ? `${v.toFixed(1)}%` : '—'
}
function rightColor(right: 'C' | 'P' | string): string {
  return right === 'C' ? 'text-accent-green' : 'text-accent-red'
}
function scoreColor(s: number): string {
  if (s >= 70) return 'text-accent-green'
  if (s >= 50) return 'text-text-secondary'
  return 'text-accent-red'
}
function winRateColor(r: number | null): string {
  if (r == null) return 'text-text-tertiary'
  if (r >= 55) return 'text-accent-green'
  if (r >= 40) return 'text-accent-amber'
  return 'text-accent-red'
}

// ─── Screener tab (TRD-028) ───────────────────────────────────────────────────

function ScreenerRow({ row, rank }: { row: OptionsCrossTickerRow; rank: number }) {
  const navigate = useNavigate()
  const isCall = row.right === 'C'

  return (
    <tr
      onClick={() => navigate(`/ticker/${row.ticker}`)}
      className="border-b border-border-subtle hover:bg-bg-elevated transition-colors cursor-pointer"
    >
      <td className="px-3 py-2 font-mono text-[10px] text-text-tertiary">{rank}</td>
      <td className="px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-bold text-text-primary">{row.ticker}</span>
          <DirectionBadge direction={row.thesis_direction} />
          <span className="font-mono text-[9px] text-text-tertiary">cv{row.thesis_conviction}</span>
        </div>
      </td>
      <td className="px-3 py-2">
        <span className="font-mono text-[9px] text-text-secondary bg-bg-surface px-1.5 py-0.5 rounded border border-border-subtle">
          {row.strategy_preset.replace('_', ' ')}
        </span>
      </td>
      <td className="px-3 py-2">
        <span className={clsx('font-mono text-xs font-semibold', rightColor(row.right))}>
          {row.right === 'C' ? 'CALL' : 'PUT'}
        </span>
        <span className="font-mono text-xs text-text-primary ml-1">${row.strike.toFixed(0)}</span>
        <span className="font-mono text-[9px] text-text-tertiary ml-1">{row.expiry}</span>
        <span className="font-mono text-[9px] text-text-tertiary ml-1">{row.dte}d</span>
      </td>
      <td className="px-3 py-2 font-mono text-xs text-text-primary">
        {row.mid != null ? `$${row.mid.toFixed(2)}` : '—'}
      </td>
      {/* Execution guidance: recommended entry + max chase (TRD-031) */}
      <td className="px-3 py-2">
        {row.recommended_entry_price != null ? (
          <div>
            <div className="font-mono text-xs font-semibold text-text-primary">
              ${row.recommended_entry_price.toFixed(2)}
            </div>
            {row.max_chase_price != null && (
              <div className="font-mono text-[9px] text-text-tertiary">
                ≤${row.max_chase_price.toFixed(2)}
              </div>
            )}
          </div>
        ) : (
          <span className="font-mono text-[10px] text-text-tertiary">—</span>
        )}
      </td>
      <td className="px-3 py-2">
        {row.slippage_risk_label ? (
          <span className={clsx(
            'font-mono text-[9px] px-1 py-0.5 rounded',
            row.slippage_risk_label === 'low'      ? 'bg-accent-green/10 text-accent-green' :
            row.slippage_risk_label === 'moderate' ? 'bg-accent-amber/10 text-accent-amber' :
            'bg-accent-red/10 text-accent-red'
          )}>
            {row.slippage_risk_label}
          </span>
        ) : (
          <span className="font-mono text-[9px] text-text-tertiary">—</span>
        )}
      </td>
      <td className="px-3 py-2 font-mono text-xs">
        <span className={clsx(isCall ? 'text-accent-green' : 'text-accent-red')}>
          {row.delta != null ? `${row.delta >= 0 ? '+' : ''}${row.delta.toFixed(2)}` : '—'}
        </span>
      </td>
      <td className="px-3 py-2 font-mono text-[10px] text-text-secondary">
        {fmtPct(row.spread_pct)}
      </td>
      <td className="px-3 py-2">
        <span className={clsx('font-mono text-xs font-semibold', scoreColor(row.score))}>
          {row.score.toFixed(0)}
        </span>
      </td>
      <td className="px-3 py-2 font-mono text-[10px] text-text-secondary">
        {row.holding_window_days != null ? `${row.holding_window_days}d` : '—'}
      </td>
      <td className="px-3 py-2">
        <div className="font-mono text-[9px] text-text-tertiary max-w-[160px] truncate" title={row.rationale}>
          {row.rationale || '—'}
        </div>
      </td>
    </tr>
  )
}

function ScreenerPanel() {
  const [minConv, setMinConv] = useState(2)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['options_screener', minConv],
    queryFn: () => api.optionsScreener({ minConviction: minConv }),
    staleTime: 15 * 60 * 1000,
    retry: 1,
  })

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] text-text-tertiary uppercase">Min Conviction</span>
          <select
            value={minConv}
            onChange={e => setMinConv(Number(e.target.value))}
            className="bg-bg-elevated border border-border-subtle rounded px-2 py-1 font-mono text-xs text-text-primary"
          >
            {[1, 2, 3, 4].map(v => (
              <option key={v} value={v}>{v}/5</option>
            ))}
          </select>
        </div>
        <button
          onClick={() => refetch()}
          className="font-mono text-[10px] px-2.5 py-1 rounded border border-border-subtle text-text-tertiary hover:text-text-secondary transition-colors"
        >
          Refresh
        </button>
        {data && (
          <span className="font-mono text-[10px] text-text-tertiary">
            {data.count} candidates across {data.tickers_evaluated} tickers
          </span>
        )}
      </div>

      {isLoading && <LoadingSkeleton rows={6} />}

      {isError && (
        <EmptyState message="Failed to load options screener. Chain data may be unavailable." />
      )}

      {data && !isLoading && data.count === 0 && (
        <EmptyState message="No option candidates found for current thesis universe. Try lowering the conviction filter or wait for thesis refresh." />
      )}

      {data && data.count > 0 && (
        <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-elevated">
                {['#', 'Ticker', 'Preset', 'Contract', 'Mid', 'Entry / Chase', 'Slip', 'Δ', 'Spread', 'Score', 'Hold', 'Rationale'].map(h => (
                  <th key={h} className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.data.map((row, i) => (
                <ScreenerRow key={`${row.ticker}-${row.expiry}-${row.strike}-${row.right}`} row={row} rank={i + 1} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="font-mono text-[9px] text-text-tertiary">
        Thesis-driven. Candidates are deterministically scored — LLM does not search raw chains.
        Click any row to open the full ticker deep-dive.
      </div>
    </div>
  )
}

// ─── Accuracy tab (TRD-029) ───────────────────────────────────────────────────

function CohortTable({ title, rows }: { title: string; rows: OptionsCohortRow[] }) {
  if (!rows.length) return null
  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      <div className="px-4 py-2 border-b border-border-subtle font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
        {title}
      </div>
      <table className="w-full">
        <thead>
          <tr className="border-b border-border-subtle">
            {['Cohort', 'N', 'Win Rate', 'TP1 Hit', 'Stop Hit', 'Opt Ret 5d', 'Und Ret 5d'].map(h => (
              <th key={h} className="px-3 py-1.5 text-left font-mono text-[9px] text-text-tertiary">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.cohort} className="border-b border-border-subtle last:border-0">
              <td className="px-3 py-1.5 font-mono text-xs text-text-primary">{r.cohort ?? '—'}</td>
              <td className="px-3 py-1.5 font-mono text-xs text-text-secondary">{r.sample_size}</td>
              <td className={clsx('px-3 py-1.5 font-mono text-xs font-semibold', winRateColor(r.win_rate_pct))}>
                {fmtPct(r.win_rate_pct)}
              </td>
              <td className="px-3 py-1.5 font-mono text-xs text-text-secondary">{fmtPct(r.tp1_rate_pct)}</td>
              <td className="px-3 py-1.5 font-mono text-xs text-text-secondary">{fmtPct(r.stop_rate_pct)}</td>
              <td className={clsx('px-3 py-1.5 font-mono text-xs',
                r.avg_option_return_5d != null && r.avg_option_return_5d >= 0 ? 'text-accent-green' : 'text-accent-red'
              )}>
                {r.avg_option_return_5d != null ? `${r.avg_option_return_5d >= 0 ? '+' : ''}${fmt(r.avg_option_return_5d)}%` : '—'}
              </td>
              <td className={clsx('px-3 py-1.5 font-mono text-xs',
                r.avg_underlying_return_5d != null && r.avg_underlying_return_5d >= 0 ? 'text-accent-green' : 'text-accent-red'
              )}>
                {r.avg_underlying_return_5d != null ? `${r.avg_underlying_return_5d >= 0 ? '+' : ''}${fmt(r.avg_underlying_return_5d)}%` : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function FreqTable({ title, rows }: { title: string; rows: OptionsFreqRow[] }) {
  if (!rows.length) return null
  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      <div className="px-4 py-2 border-b border-border-subtle font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
        {title}
      </div>
      <div className="divide-y divide-border-subtle">
        {rows.slice(0, 10).map(r => (
          <div key={r.reason} className="flex items-center justify-between px-3 py-1.5">
            <span className="font-mono text-[10px] text-text-secondary truncate max-w-[320px]" title={r.reason}>
              {r.reason}
            </span>
            <span className="font-mono text-xs text-text-tertiary ml-4">{r.count}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function AccuracyPanel() {
  const [days, setDays] = useState(90)

  const { data, isLoading } = useQuery<OptionsAccuracyResponse>({
    queryKey: ['options_accuracy', days],
    queryFn: () => api.optionsAccuracy(days),
    staleTime: 30 * 60 * 1000,
    retry: 1,
  })

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] text-text-tertiary uppercase">Period</span>
          <select
            value={days}
            onChange={e => setDays(Number(e.target.value))}
            className="bg-bg-elevated border border-border-subtle rounded px-2 py-1 font-mono text-xs text-text-primary"
          >
            {[30, 60, 90, 180, 365].map(d => (
              <option key={d} value={d}>Last {d}d</option>
            ))}
          </select>
        </div>
        {data && (
          <span className="font-mono text-[10px] text-text-tertiary">
            {data.total_snapshots} snapshots · {data.total_resolved} resolved
          </span>
        )}
      </div>

      {isLoading && <LoadingSkeleton rows={4} />}

      {data && !data.data_available && (
        <EmptyState message="No option accuracy data yet. Recommendations must be persisted and resolved before analytics appear." />
      )}

      {data && data.data_available && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <CohortTable title="By Strategy Preset" rows={data.by_preset} />
            <CohortTable title="By Delta Bucket" rows={data.by_delta_bucket} />
            <CohortTable title="By DTE Bucket" rows={data.by_dte_bucket} />
            <CohortTable title="By IV Bucket" rows={data.by_iv_bucket} />
            <CohortTable title="By Spread" rows={data.by_spread_bucket} />
            <CohortTable title="By Chain Source" rows={data.by_chain_source} />
            <CohortTable title="By Holding Window" rows={data.by_holding_window} />
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <FreqTable title="Suppression Reasons" rows={data.suppression_reasons} />
            <FreqTable title="Rejection Reasons" rows={data.rejection_reasons} />
          </div>
          <div className="font-mono text-[9px] text-text-tertiary">
            Win rate = option target 1 hit or underlying target 1 hit within the 5-day window.
            Delta-approximation used for option P&amp;L. Sample sizes shown per cohort.
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Tab style — matches ResolutionPage pattern ───────────────────────────────

const tabCls =
  'font-mono text-xs px-4 py-2 border-b-2 transition-colors ' +
  'data-[state=inactive]:border-transparent data-[state=inactive]:text-text-tertiary ' +
  'data-[state=active]:border-accent-blue data-[state=active]:text-text-primary ' +
  'hover:text-text-secondary'

// ─── Page ──────────────────────────────────────────────────────────────────────

export function OptionsPage() {
  return (
    <Shell title="Options">
      <div className="mb-4">
        <h1 className="font-mono text-lg font-semibold text-text-primary">Options</h1>
        <p className="font-mono text-[10px] text-text-tertiary mt-0.5">
          Thesis-driven option opportunities across the current watchlist
        </p>
      </div>

      <Tabs.Root defaultValue="screener" className="space-y-4">
        <Tabs.List className="flex border-b border-border-subtle">
          <Tabs.Trigger value="screener" className={tabCls}>Screener</Tabs.Trigger>
          <Tabs.Trigger value="accuracy" className={tabCls}>Accuracy</Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="screener">
          <ScreenerPanel />
        </Tabs.Content>

        <Tabs.Content value="accuracy">
          <AccuracyPanel />
        </Tabs.Content>
      </Tabs.Root>
    </Shell>
  )
}
