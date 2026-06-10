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
  OptionsComparatorResponse,
  ComparatorMethodStats,
  ComparatorCohort,
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
      <td className="px-3 py-2">
        {row.buy_decision === 'buy_now' ? (
          <span className="font-mono text-[9px] font-semibold px-1.5 py-0.5 rounded bg-accent-green/10 text-accent-green border border-accent-green/30">
            BUY NOW
          </span>
        ) : (
          <span className="font-mono text-[9px] px-1.5 py-0.5 rounded bg-bg-elevated text-text-tertiary border border-border-subtle">
            WAIT
          </span>
        )}
      </td>
    </tr>
  )
}

function formatSnapshotAge(snapshotTime: string | null): string {
  if (!snapshotTime) return ''
  const diffMs = Date.now() - new Date(snapshotTime).getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const diffH = Math.floor(diffMin / 60)
  if (diffH < 24) return `${diffH}h ago`
  return `${Math.floor(diffH / 24)}d ago`
}

function ScreenerPanel() {
  const [minConv, setMinConv] = useState(2)
  const [refreshMsg, setRefreshMsg] = useState<string | null>(null)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['options_screener', minConv],
    queryFn: () => api.optionsScreener({ minConviction: minConv }),
    staleTime: 5 * 60 * 1000,
    retry: 1,
  })

  async function handleRerun() {
    setRefreshMsg(null)
    try {
      const res = await api.optionsScreenerRefresh({ minConviction: minConv })
      setRefreshMsg(res.message)
      if (res.queued) {
        setTimeout(() => { refetch(); setRefreshMsg(null) }, 75000)
      }
    } catch {
      setRefreshMsg('Refresh request failed.')
    }
  }

  const ageLabel = data?.snapshot_time ? formatSnapshotAge(data.snapshot_time) : null

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex items-center gap-4 flex-wrap">
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
          onClick={handleRerun}
          className="font-mono text-[10px] px-2.5 py-1 rounded border border-border-subtle text-text-tertiary hover:text-text-secondary transition-colors"
        >
          Re-run screener
        </button>
        {data?.data_available && (
          <span className="font-mono text-[10px] text-text-tertiary">
            {data.count} candidates across {data.tickers_completed ?? data.tickers_evaluated ?? 0} tickers
          </span>
        )}
        {ageLabel && (
          <span className="font-mono text-[10px] text-text-tertiary">
            · as of {ageLabel}
          </span>
        )}
      </div>

      {/* Refresh queued toast */}
      {refreshMsg && (
        <div className="font-mono text-[10px] text-accent-amber bg-accent-amber/5 border border-accent-amber/20 rounded px-2.5 py-2">
          {refreshMsg}
        </div>
      )}

      {/* Partial result detail (informational, not a warning) */}
      {data?.partial && data.timed_out_tickers && data.timed_out_tickers.length > 0 && (
        <div className="font-mono text-[10px] text-text-tertiary">
          Note: {data.timed_out_tickers.join(', ')} timed out during last screener run.
        </div>
      )}

      {isLoading && <LoadingSkeleton rows={6} />}

      {isError && (
        <EmptyState message="Failed to load options screener. Chain data may be unavailable." />
      )}

      {data && !isLoading && !data.data_available && (
        <EmptyState message={data.message ?? 'No screener snapshot available. Use "Re-run screener" to generate the first snapshot, or wait for the daily pipeline.'} />
      )}

      {data && !isLoading && data.data_available && data.count === 0 && (
        <EmptyState message="No option candidates found for current thesis universe. Try lowering the conviction filter or wait for thesis refresh." />
      )}

      {data && data.data_available && data.count > 0 && (
        <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-elevated">
                {['#', 'Ticker', 'Preset', 'Contract', 'Mid', 'Entry / Chase', 'Slip', 'Δ', 'Spread', 'Score', 'Hold', 'Rationale', 'Buy'].map(h => (
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

// ─── Calibration tab (TRD-044 / TRD-045) ─────────────────────────────────────

function pctOrNA(v: number | null | undefined): string {
  return v != null ? `${v.toFixed(1)}%` : '—'
}
function retColor(v: number | null): string {
  if (v == null) return 'text-text-tertiary'
  return v >= 0 ? 'text-accent-green' : 'text-accent-red'
}

function MethodStatsRow({ label, ms }: { label: string; ms: ComparatorMethodStats }) {
  return (
    <tr className="border-b border-border-subtle last:border-0">
      <td className="px-3 py-1.5 font-mono text-xs text-text-secondary">{label}</td>
      <td className="px-3 py-1.5 font-mono text-xs text-text-tertiary">{ms.n}</td>
      <td className="px-3 py-1.5 font-mono text-xs text-text-secondary">
        {ms.sparse ? <span className="text-text-tertiary italic">sparse</span> : pctOrNA(ms.tp1_hit_rate)}
      </td>
      <td className="px-3 py-1.5 font-mono text-xs text-text-secondary">
        {ms.sparse ? '—' : pctOrNA(ms.tp2_hit_rate)}
      </td>
      <td className="px-3 py-1.5 font-mono text-xs text-text-secondary">
        {ms.sparse ? '—' : pctOrNA(ms.stop_hit_rate)}
      </td>
      <td className={clsx('px-3 py-1.5 font-mono text-xs', retColor(ms.mean_return_pct))}>
        {ms.sparse ? '—' : (ms.mean_return_pct != null ? `${ms.mean_return_pct >= 0 ? '+' : ''}${ms.mean_return_pct.toFixed(1)}%` : '—')}
      </td>
    </tr>
  )
}

function OverallComparatorTable({ data }: { data: OptionsComparatorResponse }) {
  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      <div className="px-4 py-2 border-b border-border-subtle flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Overall — Legacy vs V2 vs Underlying</span>
        <span className="font-mono text-[9px] text-text-tertiary">{data.total_rows} rows · {data.v2_eligible_rows} v2-eligible · {data.resolution_type}</span>
      </div>
      <table className="w-full">
        <thead>
          <tr className="border-b border-border-subtle">
            {['Method', 'N', 'TP1 Hit', 'TP2 Hit', 'Stop Hit', 'Mean Ret 5d'].map(h => (
              <th key={h} className="px-3 py-1.5 text-left font-mono text-[9px] text-text-tertiary">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          <MethodStatsRow label="Legacy (flat ×)" ms={data.overall_legacy} />
          <MethodStatsRow label="V2 (Δ-projected)" ms={data.overall_v2} />
          <MethodStatsRow label="Underlying thesis" ms={data.overall_underlying} />
        </tbody>
      </table>
    </div>
  )
}

function CohortComparatorTable({ title, cohorts }: { title: string; cohorts: ComparatorCohort[] }) {
  if (!cohorts.length) return null
  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      <div className="px-4 py-2 border-b border-border-subtle font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
        {title}
      </div>
      <table className="w-full">
        <thead>
          <tr className="border-b border-border-subtle">
            {['Cohort', 'N', 'Leg TP1', 'V2 TP1', 'Und T1', 'Leg Stop', 'V2 Stop'].map(h => (
              <th key={h} className="px-3 py-1.5 text-left font-mono text-[9px] text-text-tertiary">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cohorts.map(cc => (
            <tr key={cc.cohort_label} className="border-b border-border-subtle last:border-0">
              <td className="px-3 py-1.5 font-mono text-xs text-text-primary">
                {cc.cohort_label ?? '—'}
                {cc.sparse && <span className="ml-1 text-[9px] text-text-tertiary italic">(small)</span>}
              </td>
              <td className="px-3 py-1.5 font-mono text-xs text-text-secondary">{cc.n}</td>
              <td className="px-3 py-1.5 font-mono text-xs text-text-secondary">{cc.sparse ? '—' : pctOrNA(cc.legacy.tp1_hit_rate)}</td>
              <td className="px-3 py-1.5 font-mono text-xs text-text-secondary">{cc.v2.sparse ? '—' : pctOrNA(cc.v2.tp1_hit_rate)}</td>
              <td className="px-3 py-1.5 font-mono text-xs text-text-secondary">{cc.sparse ? '—' : pctOrNA(cc.underlying.tp1_hit_rate)}</td>
              <td className="px-3 py-1.5 font-mono text-xs text-text-secondary">{cc.sparse ? '—' : pctOrNA(cc.legacy.stop_hit_rate)}</td>
              <td className="px-3 py-1.5 font-mono text-xs text-text-secondary">{cc.v2.sparse ? '—' : pctOrNA(cc.v2.stop_hit_rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function CalibrationPanel() {
  const [days, setDays] = useState(90)

  const { data, isLoading } = useQuery<OptionsComparatorResponse>({
    queryKey: ['options_comparator', days],
    queryFn: () => api.optionsComparator(days, '5d'),
    staleTime: 30 * 60 * 1000,
    retry: 1,
  })

  return (
    <div className="space-y-4">
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
      </div>

      {isLoading && <LoadingSkeleton rows={4} />}

      {data && !data.data_available && (
        <EmptyState message={data.message ?? 'No resolved outcomes available for comparison. Outcomes must be resolved before calibration data appears.'} />
      )}

      {data && data.data_available && (
        <div className="space-y-4">
          <OverallComparatorTable data={data} />
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <CohortComparatorTable title="By Strategy Preset" cohorts={data.by_preset} />
            <CohortComparatorTable title="By Delta Bucket" cohorts={data.by_delta_bucket} />
            <CohortComparatorTable title="By DTE Bucket" cohorts={data.by_dte_bucket} />
          </div>
          <div className="font-mono text-[9px] text-text-tertiary space-y-1">
            <div>Legacy = flat multiplier exits (mid × 1.5 / 2.0). V2 = delta-projected exits from underlying thesis levels.</div>
            <div>Sparse cohorts (n &lt; 5) show "—" to avoid misleading small-sample rates.</div>
            <div>Option P&L approximated via delta. 5-day resolution window.</div>
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
          <Tabs.Trigger value="calibration" className={tabCls}>Calibration</Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="screener">
          <ScreenerPanel />
        </Tabs.Content>

        <Tabs.Content value="accuracy">
          <AccuracyPanel />
        </Tabs.Content>

        <Tabs.Content value="calibration">
          <CalibrationPanel />
        </Tabs.Content>
      </Tabs.Root>
    </Shell>
  )
}
