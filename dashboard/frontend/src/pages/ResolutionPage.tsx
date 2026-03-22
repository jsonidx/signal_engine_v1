import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, Minus } from 'lucide-react'
import { format } from 'date-fns'
import { Shell } from '../components/layout/Shell'
import { MetricCard } from '../components/ui/MetricCard'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { EmptyState } from '../components/ui/EmptyState'
import { api } from '../lib/api'
import { clsx } from 'clsx'

// ─── Override flag badge ───────────────────────────────────────────────────────

function OverrideBadge({ flag }: { flag: string }) {
  return (
    <span className="inline-block font-mono text-[9px] px-1.5 py-0.5 bg-accent-amber/15 text-accent-amber border border-accent-amber/30 rounded mr-1 mb-0.5">
      {flag.replace(/_/g, ' ')}
    </span>
  )
}

// ─── Module accuracy placeholder ──────────────────────────────────────────────

const MODULE_NAMES = [
  'signal_engine',
  'squeeze',
  'options',
  'dark_pool',
  'fundamentals',
  'social',
  'polymarket',
  'cross_asset',
]

function ModuleAccuracyTable() {
  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      <div className="px-4 py-3 border-b border-border-subtle">
        <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
          Module Directional Accuracy — vs 1-Week Forward Return
        </div>
      </div>
      <table className="w-full opacity-40">
        <thead>
          <tr className="border-b border-border-subtle">
            {['Module', 'Correct', 'Wrong', 'Accuracy %', 'Suggested Δ Weight'].map(h => (
              <th key={h} className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {MODULE_NAMES.map(name => (
            <tr key={name} className="border-b border-border-subtle/50">
              <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">{name.replace(/_/g, ' ')}</td>
              <td className="px-4 py-2.5 font-mono text-xs text-text-tertiary">—</td>
              <td className="px-4 py-2.5 font-mono text-xs text-text-tertiary">—</td>
              <td className="px-4 py-2.5 font-mono text-xs text-text-tertiary">—</td>
              <td className="px-4 py-2.5 font-mono text-xs text-text-tertiary">—</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="px-4 py-3 border-t border-border-subtle/50 bg-bg-elevated">
        <p className="font-mono text-[11px] text-text-tertiary leading-relaxed">
          Accuracy data populates after 8+ weeks of resolved signals.
          Weight recalibration is suggested after 12 weeks.
        </p>
      </div>
    </div>
  )
}

// ─── Page ──────────────────────────────────────────────────────────────────────

export function ResolutionPage() {
  const [selectedDate, setSelectedDate] = useState(() => format(new Date(), 'yyyy-MM-dd'))

  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['resolution', 'stats'],
    queryFn: api.resolutionStats,
    retry: 1,
  })

  // Try rich log first, fall back to basic log
  const logDateParam = selectedDate.replace(/-/g, '')
  const { data: richLog, isLoading: logLoading } = useQuery({
    queryKey: ['resolution', 'log', 'rich', logDateParam],
    queryFn: () => api.resolutionLogRich(selectedDate, 100),
    retry: 1,
  })

  const { data: basicLog } = useQuery({
    queryKey: ['resolution', 'log', 'basic', 100],
    queryFn: () => api.resolutionLog(100),
    enabled: !richLog,
    retry: 1,
  })

  // Normalise: either rich log entries or basic log entries mapped to compatible shape
  type NormRow = {
    ticker: string
    timestamp: string
    pre_resolved: string
    confidence: number
    bull_weight: number
    bear_weight: number
    overrides: string[]
    skip_claude: boolean
  }

  const rows: NormRow[] = richLog
    ? (richLog as NormRow[])
    : (basicLog ?? []).map(r => ({
        ticker: r.ticker,
        timestamp: r.timestamp,
        pre_resolved: r.input_direction,
        confidence: 0,
        bull_weight: 0,
        bear_weight: 0,
        overrides: r.override_reason ? [r.override_reason] : [],
        skip_claude: r.skip_claude,
      }))

  return (
    <Shell title="Conflict Resolution Log">
      <div className="space-y-5">
        {/* Stats row */}
        {statsLoading ? (
          <div className="grid grid-cols-4 gap-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="shimmer h-24 rounded" />
            ))}
          </div>
        ) : stats ? (
          <div className="grid grid-cols-4 gap-4">
            <MetricCard
              label="Claude Skip Rate"
              value={stats.claude_skip_rate_pct}
              unit="%"
              colorBySign={false}
            />
            <MetricCard
              label="Avg Agreement Score"
              value={(stats.avg_agreement_score * 100).toFixed(1)}
              unit="%"
            />
            <div className="bg-bg-surface border border-border-subtle rounded p-4">
              <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
                Most Common Override
              </div>
              <div className="font-mono text-sm font-semibold text-accent-amber break-words leading-tight">
                {stats.most_common_override?.replace(/_/g, ' ') || '—'}
              </div>
            </div>
            <MetricCard
              label="Bear CB Hits (30d)"
              value={stats.bear_cb_hits_30d}
              colorBySign={false}
              sentiment={stats.bear_cb_hits_30d > 5 ? 'negative' : 'neutral'}
            />
          </div>
        ) : null}

        {/* Log table */}
        <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
          <div className="px-4 py-3 border-b border-border-subtle flex items-center gap-4 flex-wrap">
            <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
              Signal Arbitration Log
            </span>
            <div className="flex items-center gap-2 ml-auto">
              <span className="font-mono text-xs text-text-tertiary">Date:</span>
              <input
                type="date"
                value={selectedDate}
                onChange={e => setSelectedDate(e.target.value)}
                className="px-2 py-1 bg-bg-elevated border border-border-subtle rounded font-mono text-xs text-text-secondary focus:outline-none focus:border-border-active"
              />
            </div>
            <span className="font-mono text-xs text-text-tertiary">{rows.length} entries</span>
          </div>

          {logLoading ? (
            <div className="p-4"><LoadingSkeleton rows={8} /></div>
          ) : rows.length === 0 ? (
            <EmptyState message="No resolution log entries" command="./run_master.sh" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-border-subtle">
                    {['Ticker', 'Time', 'Pre-Resolved', 'Confidence', 'Bull Wt', 'Bear Wt', 'Overrides', 'Claude Skip'].map(h => (
                      <th key={h} className="px-3 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, i) => {
                    const hasOverride = row.overrides.length > 0
                    return (
                      <tr
                        key={i}
                        className={clsx(
                          'border-b border-border-subtle/50 transition-colors',
                          hasOverride ? 'bg-accent-red/5 hover:bg-accent-red/10' : 'hover:bg-bg-elevated'
                        )}
                      >
                        <td className="px-3 py-2.5">
                          <span className="font-mono text-sm font-semibold text-accent-blue">{row.ticker}</span>
                        </td>
                        <td className="px-3 py-2.5 font-mono text-xs text-text-secondary whitespace-nowrap">
                          {row.timestamp}
                        </td>
                        <td className="px-3 py-2.5">
                          <span className={clsx(
                            'font-mono text-xs font-medium',
                            row.pre_resolved === 'BULL' ? 'text-accent-green'
                              : row.pre_resolved === 'BEAR' ? 'text-accent-red'
                              : 'text-text-secondary'
                          )}>
                            {row.pre_resolved}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 font-mono text-xs text-text-secondary">
                          {row.confidence > 0 ? `${(row.confidence * 100).toFixed(0)}%` : '—'}
                        </td>
                        <td className="px-3 py-2.5 font-mono text-xs text-text-secondary">
                          {row.bull_weight > 0 ? row.bull_weight.toFixed(2) : '—'}
                        </td>
                        <td className="px-3 py-2.5 font-mono text-xs text-text-secondary">
                          {row.bear_weight > 0 ? row.bear_weight.toFixed(2) : '—'}
                        </td>
                        <td className="px-3 py-2.5">
                          {row.overrides.length > 0
                            ? row.overrides.map(f => <OverrideBadge key={f} flag={f} />)
                            : <span className="font-mono text-xs text-text-tertiary">—</span>
                          }
                        </td>
                        <td className="px-3 py-2.5">
                          {row.skip_claude ? (
                            <Check size={13} className="text-accent-green" />
                          ) : (
                            <Minus size={13} className="text-text-tertiary" />
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Module accuracy tracker */}
        <ModuleAccuracyTable />
      </div>
    </Shell>
  )
}
