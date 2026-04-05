import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import * as Tabs from '@radix-ui/react-tabs'
import { Check, Minus } from 'lucide-react'
import { format } from 'date-fns'
import { Shell } from '../components/layout/Shell'
import { MetricCard } from '../components/ui/MetricCard'
import { MonoNumber } from '../components/ui/MonoNumber'
import { DirectionBadge } from '../components/ui/DirectionBadge'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { EmptyState } from '../components/ui/EmptyState'
import { api, type AccuracyMatrixCell, type ThesisOutcome, type ThesisAccuracyMonth } from '../lib/api'
import { clsx } from 'clsx'

// ─── Override flag badge ───────────────────────────────────────────────────────

function OverrideBadge({ flag }: { flag: string }) {
  return (
    <span className="inline-block font-mono text-[9px] px-1.5 py-0.5 bg-accent-amber/15 text-accent-amber border border-accent-amber/30 rounded mr-1 mb-0.5">
      {flag.replace(/_/g, ' ')}
    </span>
  )
}

// ─── Accuracy matrix ──────────────────────────────────────────────────────────

type RegimeFilter    = 'ALL' | 'RISK_ON' | 'TRANSITIONAL' | 'RISK_OFF'
type ConvFilter      = 'ALL' | '1' | '2' | '3' | '4' | '5'
type AgreementFilter = 'ALL' | 'high' | 'mid' | 'low'

const AGREEMENT_LABEL: Record<string, string> = {
  high: '≥0.70',
  mid:  '0.50–0.70',
  low:  '<0.50',
  unknown: '?',
}

const REGIME_COLOR: Record<string, string> = {
  RISK_ON:     'text-accent-green',
  TRANSITIONAL:'text-accent-amber',
  RISK_OFF:    'text-accent-red',
}

function winRateColor(rate: number | null): string {
  if (rate == null) return 'text-text-tertiary'
  if (rate >= 0.60) return 'text-accent-green'
  if (rate >= 0.45) return 'text-accent-amber'
  return 'text-accent-red'
}

function AccuracyMatrixPanel() {
  const [days,      setDays]      = useState(180)
  const [regime,    setRegime]    = useState<RegimeFilter>('ALL')
  const [conv,      setConv]      = useState<ConvFilter>('ALL')
  const [agreement, setAgreement] = useState<AgreementFilter>('ALL')

  const { data, isLoading } = useQuery({
    queryKey: ['accuracy-matrix', days],
    queryFn:  () => api.accuracyMatrix(days),
    retry: 1,
  })

  const cells: AccuracyMatrixCell[] = (data?.cells ?? []).filter(c => {
    if (regime    !== 'ALL' && c.regime            !== regime)    return false
    if (conv      !== 'ALL' && String(c.conviction) !== conv)     return false
    if (agreement !== 'ALL' && c.agreement_bucket   !== agreement) return false
    return true
  })

  const totalN     = cells.reduce((s, c) => s + c.sample_size, 0)
  const totalWin   = cells.reduce((s, c) => s + Math.round((c.win_rate ?? 0) * c.sample_size), 0)
  const overallWin = totalN > 0 ? totalWin / totalN : null

  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      {/* Header + filters */}
      <div className="px-4 py-3 border-b border-border-subtle flex flex-wrap items-center gap-3">
        <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
          Thesis Accuracy Matrix
        </span>

        <select
          value={days}
          onChange={e => setDays(Number(e.target.value))}
          className="ml-auto px-2 py-1 bg-bg-elevated border border-border-subtle rounded font-mono text-xs text-text-secondary focus:outline-none"
        >
          {[90, 180, 365].map(d => (
            <option key={d} value={d}>Last {d}d</option>
          ))}
        </select>

        <select
          value={regime}
          onChange={e => setRegime(e.target.value as RegimeFilter)}
          className="px-2 py-1 bg-bg-elevated border border-border-subtle rounded font-mono text-xs text-text-secondary focus:outline-none"
        >
          {(['ALL', 'RISK_ON', 'TRANSITIONAL', 'RISK_OFF'] as RegimeFilter[]).map(r => (
            <option key={r} value={r}>{r === 'ALL' ? 'All Regimes' : r}</option>
          ))}
        </select>

        <select
          value={conv}
          onChange={e => setConv(e.target.value as ConvFilter)}
          className="px-2 py-1 bg-bg-elevated border border-border-subtle rounded font-mono text-xs text-text-secondary focus:outline-none"
        >
          {(['ALL', '1', '2', '3', '4', '5'] as ConvFilter[]).map(c => (
            <option key={c} value={c}>{c === 'ALL' ? 'All Conviction' : `Conv ${c}`}</option>
          ))}
        </select>

        <select
          value={agreement}
          onChange={e => setAgreement(e.target.value as AgreementFilter)}
          className="px-2 py-1 bg-bg-elevated border border-border-subtle rounded font-mono text-xs text-text-secondary focus:outline-none"
        >
          {(['ALL', 'high', 'mid', 'low'] as AgreementFilter[]).map(a => (
            <option key={a} value={a}>{a === 'ALL' ? 'All Agreement' : `Agree ${AGREEMENT_LABEL[a]}`}</option>
          ))}
        </select>

        {overallWin !== null && (
          <span className={clsx('font-mono text-xs font-semibold', winRateColor(overallWin))}>
            {(overallWin * 100).toFixed(0)}% win ({totalN})
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="p-4"><LoadingSkeleton rows={5} /></div>
      ) : !data?.data_available ? (
        <div className="px-4 py-6 text-center font-mono text-xs text-text-tertiary">
          No resolved theses yet — matrix populates after 8+ weeks of data.
        </div>
      ) : cells.length === 0 ? (
        <div className="px-4 py-6 text-center font-mono text-xs text-text-tertiary">
          No results match the selected filters.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-border-subtle">
                {['Regime', 'Conv', 'Agreement', 'n', 'Win Rate', 'Hit T1', 'Avg 30d'].map(h => (
                  <th key={h} className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cells.map((c, i) => (
                <tr key={i} className="border-b border-border-subtle/50 hover:bg-bg-elevated transition-colors">
                  <td className={clsx('px-4 py-2.5 font-mono text-xs font-medium', REGIME_COLOR[c.regime] ?? 'text-text-secondary')}>
                    {c.regime}
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">{c.conviction}</td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                    {AGREEMENT_LABEL[c.agreement_bucket] ?? c.agreement_bucket}
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-tertiary">{c.sample_size}</td>
                  <td className={clsx('px-4 py-2.5 font-mono text-xs font-semibold', winRateColor(c.win_rate))}>
                    {c.win_rate != null ? `${(c.win_rate * 100).toFixed(0)}%` : '—'}
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                    {c.hit_t1_rate != null ? `${(c.hit_t1_rate * 100).toFixed(0)}%` : '—'}
                  </td>
                  <td className={clsx('px-4 py-2.5 font-mono text-xs', c.avg_return_30d != null ? (c.avg_return_30d >= 0 ? 'text-accent-green' : 'text-accent-red') : 'text-text-tertiary')}>
                    {c.avg_return_30d != null ? `${c.avg_return_30d >= 0 ? '+' : ''}${c.avg_return_30d.toFixed(1)}%` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="px-4 py-2.5 border-t border-border-subtle/50 bg-bg-elevated">
        <p className="font-mono text-[10px] text-text-tertiary">
          Win Rate = direction correct at 30d · Hit T1 = target_1 reached · Avg 30d = mean % return vs entry
        </p>
      </div>
    </div>
  )
}

// ─── Accuracy helpers (absorbed from AccuracyPage) ────────────────────────────

function pct(v: number | null, decimals = 1): string {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${v.toFixed(decimals)}%`
}

function accuracyColor(v: number | null): string {
  if (v == null) return 'text-text-tertiary'
  if (v >= 65)  return 'text-accent-green'
  if (v >= 50)  return 'text-accent-amber'
  return 'text-accent-red'
}

function outcomeChip(outcome: ThesisOutcome['outcome']) {
  const map: Record<string, { label: string; color: string }> = {
    HIT_TARGET1: { label: 'Hit T1',  color: 'bg-accent-green/15 text-accent-green  border-accent-green/30' },
    HIT_TARGET2: { label: 'Hit T2',  color: 'bg-accent-green/25 text-accent-green  border-accent-green/50' },
    HIT_STOP:    { label: 'Stop',    color: 'bg-accent-red/15   text-accent-red    border-accent-red/30'   },
    EXPIRED:     { label: 'Expired', color: 'bg-text-tertiary/10 text-text-tertiary border-text-tertiary/20' },
    OPEN:        { label: 'Open',    color: 'bg-accent-blue/10  text-accent-blue   border-accent-blue/20'  },
  }
  const { label, color } = map[outcome] ?? map.OPEN
  return (
    <span className={clsx('font-mono text-[10px] px-1.5 py-0.5 rounded border', color)}>
      {label}
    </span>
  )
}

function correctBadge(v: 1 | 0 | null) {
  if (v === 1) return <span className="text-accent-green font-mono text-xs">✓</span>
  if (v === 0) return <span className="text-accent-red   font-mono text-xs">✗</span>
  return <span className="text-text-tertiary font-mono text-xs">—</span>
}

function StatCard({ label, value, sub, color }: {
  label: string
  value: string
  sub?: string
  color?: string
}) {
  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-4">
      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">{label}</div>
      <div className={clsx('font-mono text-[28px] font-semibold leading-none', color ?? 'text-text-primary')}>
        {value}
      </div>
      {sub && <div className="font-mono text-[10px] text-text-tertiary mt-1">{sub}</div>}
    </div>
  )
}

function MonthlyTable({ months }: { months: ThesisAccuracyMonth[] }) {
  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      <div className="px-4 py-3 border-b border-border-subtle">
        <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
          Monthly Breakdown
        </div>
      </div>
      <table className="w-full">
        <thead>
          <tr className="border-b border-border-subtle">
            {['Month', 'Total', 'Direction %', 'Hit T1 %', 'Stop %', 'Avg 30d', 'Avg vs T1', 'Traded'].map(h => (
              <th key={h} className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {months.map(m => (
            <tr key={m.month} className="border-b border-border-subtle/50 hover:bg-bg-elevated transition-colors">
              <td className="px-4 py-3 font-mono text-xs text-text-primary font-semibold">{m.month}</td>
              <td className="px-4 py-3 font-mono text-xs text-text-secondary">
                {m.total}
                <span className="text-text-tertiary ml-1 text-[10px]">({m.open} open)</span>
              </td>
              <td className="px-4 py-3">
                <span className={clsx('font-mono text-sm font-semibold', accuracyColor(m.direction_accuracy_pct))}>
                  {m.direction_accuracy_pct != null ? `${m.direction_accuracy_pct.toFixed(0)}%` : '—'}
                </span>
              </td>
              <td className="px-4 py-3 font-mono text-xs text-text-secondary">
                {m.target_hit_rate_pct != null ? `${m.target_hit_rate_pct.toFixed(0)}%` : '—'}
              </td>
              <td className="px-4 py-3 font-mono text-xs text-accent-red/80">
                {m.stop_hit_rate_pct != null ? `${m.stop_hit_rate_pct.toFixed(0)}%` : '—'}
              </td>
              <td className="px-4 py-3">
                <MonoNumber value={m.avg_return_30d} suffix="%" decimals={1} colorBySign />
              </td>
              <td className="px-4 py-3">
                <MonoNumber value={m.avg_vs_target_1_pct} suffix="%" decimals={1} colorBySign />
              </td>
              <td className="px-4 py-3 font-mono text-xs text-text-secondary">{m.traded}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function OutcomesTable({ outcomes }: { outcomes: ThesisOutcome[] }) {
  const [show, setShow] = useState<'all' | 'resolved' | 'open'>('all')

  const filtered = outcomes.filter(o => {
    if (show === 'resolved') return o.outcome !== 'OPEN'
    if (show === 'open')     return o.outcome === 'OPEN'
    return true
  })

  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      <div className="px-4 py-3 border-b border-border-subtle flex items-center justify-between">
        <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
          Individual Thesis Outcomes
        </div>
        <div className="flex gap-1">
          {(['all', 'resolved', 'open'] as const).map(f => (
            <button
              key={f}
              onClick={() => setShow(f)}
              className={clsx(
                'font-mono text-[10px] px-2 py-1 rounded border transition-colors',
                show === f
                  ? 'bg-accent-blue/15 text-accent-blue border-accent-blue/30'
                  : 'text-text-tertiary border-text-tertiary/20 hover:text-text-secondary'
              )}
            >
              {f}
            </button>
          ))}
        </div>
      </div>
      <table className="w-full">
        <thead>
          <tr className="border-b border-border-subtle">
            {['Date', 'Ticker', 'Dir', 'Conv', 'Entry', 'T1', 'Stop', '7d', '14d', '30d', 'vs T1', 'Outcome', 'OK', 'Traded'].map(h => (
              <th key={h} className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {filtered.map((o, i) => (
            <tr key={i} className="border-b border-border-subtle/50 hover:bg-bg-elevated transition-colors">
              <td className="px-3 py-2.5 font-mono text-[11px] text-text-tertiary">{o.thesis_date}</td>
              <td className="px-3 py-2.5 font-mono text-xs text-text-primary font-semibold">{o.ticker}</td>
              <td className="px-3 py-2.5">
                <DirectionBadge direction={o.direction} size="sm" />
              </td>
              <td className="px-3 py-2.5 font-mono text-xs text-text-secondary">{o.conviction ?? '—'}</td>
              <td className="px-3 py-2.5 font-mono text-[11px] text-text-secondary">
                {o.entry_price != null ? o.entry_price.toFixed(2) : '—'}
              </td>
              <td className="px-3 py-2.5 font-mono text-[11px] text-accent-green/80">
                {o.target_1 != null ? o.target_1.toFixed(2) : '—'}
              </td>
              <td className="px-3 py-2.5 font-mono text-[11px] text-accent-red/80">
                {o.stop_loss != null ? o.stop_loss.toFixed(2) : '—'}
              </td>
              <td className="px-3 py-2.5">
                <MonoNumber value={o.return_7d}  suffix="%" decimals={1} colorBySign />
              </td>
              <td className="px-3 py-2.5">
                <MonoNumber value={o.return_14d} suffix="%" decimals={1} colorBySign />
              </td>
              <td className="px-3 py-2.5">
                <MonoNumber value={o.return_30d} suffix="%" decimals={1} colorBySign />
              </td>
              <td className="px-3 py-2.5">
                <MonoNumber value={o.vs_target_1_pct} suffix="%" decimals={1} colorBySign />
              </td>
              <td className="px-3 py-2.5">{outcomeChip(o.outcome)}</td>
              <td className="px-3 py-2.5">{correctBadge(o.claude_correct)}</td>
              <td className="px-3 py-2.5 font-mono text-[11px] text-text-tertiary">
                {o.was_traded ? <span className="text-accent-blue">Y</span> : 'N'}
              </td>
            </tr>
          ))}
          {filtered.length === 0 && (
            <tr>
              <td colSpan={14} className="px-4 py-6 text-center font-mono text-xs text-text-tertiary">
                No outcomes yet
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

// ─── Tab style ────────────────────────────────────────────────────────────────

const TAB_STYLE =
  'px-4 py-2.5 font-mono text-xs uppercase tracking-widest border-b-2 transition-colors cursor-pointer ' +
  'data-[state=active]:border-accent-blue data-[state=active]:text-text-primary ' +
  'data-[state=inactive]:border-transparent data-[state=inactive]:text-text-tertiary data-[state=inactive]:hover:text-text-secondary'

// ─── Page ──────────────────────────────────────────────────────────────────────

export function ResolutionPage() {
  const [selectedDate, setSelectedDate] = useState(() => format(new Date(), 'yyyy-MM-dd'))

  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['resolution', 'stats'],
    queryFn: api.resolutionStats,
    retry: 1,
  })

  const logDateParam = selectedDate.replace(/-/g, '')
  const { data: richLog, isLoading: logLoading } = useQuery({
    queryKey: ['resolution', 'log', 'rich', logDateParam],
    queryFn: () => api.resolutionLogRich(logDateParam, 100),
    retry: 1,
  })

  const { data: basicLog } = useQuery({
    queryKey: ['resolution', 'log', 'basic', 100],
    queryFn: () => api.resolutionLog(100),
    enabled: !richLog,
    retry: 1,
  })

  // Accuracy tab data
  const { data: accuracy, isLoading: aLoading } = useQuery({
    queryKey: ['thesis', 'accuracy'],
    queryFn: api.thesisAccuracy,
  })
  const { data: outcomes, isLoading: oLoading } = useQuery({
    queryKey: ['thesis', 'outcomes', 90],
    queryFn: () => api.thesisOutcomes(90),
  })

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

  const rows: NormRow[] = (richLog ?? basicLog ?? []).map((r: any) => ({
    ticker: r.ticker,
    timestamp: r.timestamp,
    pre_resolved: r.pre_resolved ?? r.input_direction ?? '',
    confidence: r.confidence ?? 0,
    bull_weight: r.bull_weight ?? 0,
    bear_weight: r.bear_weight ?? 0,
    overrides: Array.isArray(r.overrides)
      ? r.overrides
      : r.override_reason
        ? [r.override_reason]
        : [],
    skip_claude: r.skip_claude ?? false,
  }))

  const at = accuracy?.all_time
  const months = accuracy?.by_month ?? []
  const outcomeRows = outcomes?.data ?? []

  return (
    <Shell title="Resolution & Accuracy">
      <Tabs.Root defaultValue="resolution">
        <Tabs.List className="flex border-b border-border-subtle mb-5 -mx-6 px-6">
          <Tabs.Trigger value="resolution" className={TAB_STYLE}>
            Resolution Log
          </Tabs.Trigger>
          <Tabs.Trigger value="accuracy" className={TAB_STYLE}>
            Claude Accuracy
          </Tabs.Trigger>
        </Tabs.List>

        {/* ── Resolution Log tab ── */}
        <Tabs.Content value="resolution">
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

            {/* Accuracy matrix */}
            <AccuracyMatrixPanel />
          </div>
        </Tabs.Content>

        {/* ── Claude Accuracy tab ── */}
        <Tabs.Content value="accuracy">
          {(aLoading || oLoading) ? (
            <LoadingSkeleton rows={6} />
          ) : !accuracy?.data_available ? (
            <EmptyState
              message="No thesis outcomes yet"
              command="python thesis_checker.py"
            />
          ) : (
            <div className="space-y-5">
              {/* All-time stat cards */}
              <div className="grid grid-cols-4 gap-4">
                <StatCard
                  label="Direction Accuracy"
                  value={at?.direction_accuracy_pct != null ? `${at.direction_accuracy_pct.toFixed(0)}%` : '—'}
                  sub={`${at?.correct ?? 0} correct / ${at?.wrong ?? 0} wrong`}
                  color={accuracyColor(at?.direction_accuracy_pct ?? null)}
                />
                <StatCard
                  label="Target 1 Hit Rate"
                  value={at?.target_hit_rate_pct != null ? `${at.target_hit_rate_pct.toFixed(0)}%` : '—'}
                  sub={`${at?.hit_target_1 ?? 0} of ${at?.resolved ?? 0} resolved`}
                  color={at?.target_hit_rate_pct != null && at.target_hit_rate_pct >= 40 ? 'text-accent-green' : 'text-accent-amber'}
                />
                <StatCard
                  label="Avg 30d Return"
                  value={at?.avg_return_30d != null ? pct(at.avg_return_30d) : '—'}
                  sub="vs entry price on thesis date"
                  color={at?.avg_return_30d != null ? (at.avg_return_30d >= 0 ? 'text-accent-green' : 'text-accent-red') : undefined}
                />
                <StatCard
                  label="Avg vs Target 1"
                  value={at?.avg_vs_target_1_pct != null ? pct(at.avg_vs_target_1_pct) : '—'}
                  sub="negative = price fell short of target"
                  color={at?.avg_vs_target_1_pct != null ? (at.avg_vs_target_1_pct >= 0 ? 'text-accent-green' : 'text-text-secondary') : undefined}
                />
              </div>

              {/* Secondary stat row */}
              {at && (
                <div className="grid grid-cols-4 gap-4">
                  <div className="bg-bg-surface border border-border-subtle rounded p-3 flex justify-between items-center">
                    <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Total Theses</span>
                    <span className="font-mono text-sm text-text-primary">{at.total}</span>
                  </div>
                  <div className="bg-bg-surface border border-border-subtle rounded p-3 flex justify-between items-center">
                    <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Resolved</span>
                    <span className="font-mono text-sm text-text-secondary">{at.resolved}</span>
                  </div>
                  <div className="bg-bg-surface border border-border-subtle rounded p-3 flex justify-between items-center">
                    <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Stop Hit Rate</span>
                    <span className="font-mono text-sm text-accent-red">
                      {at.stop_hit_rate_pct != null ? `${at.stop_hit_rate_pct.toFixed(0)}%` : '—'}
                    </span>
                  </div>
                  <div className="bg-bg-surface border border-border-subtle rounded p-3 flex justify-between items-center">
                    <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Stops Hit</span>
                    <span className="font-mono text-sm text-accent-red">{at.hit_stop_first}</span>
                  </div>
                </div>
              )}

              {/* Monthly breakdown */}
              {months.length > 0 && <MonthlyTable months={months} />}

              {/* Individual outcomes */}
              <OutcomesTable outcomes={outcomeRows} />
            </div>
          )}
        </Tabs.Content>
      </Tabs.Root>
    </Shell>
  )
}
