import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Shell } from '../components/layout/Shell'
import { MonoNumber } from '../components/ui/MonoNumber'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { EmptyState } from '../components/ui/EmptyState'
import { DirectionBadge } from '../components/ui/DirectionBadge'
import { api, type ThesisOutcome, type ThesisAccuracyMonth } from '../lib/api'
import { clsx } from 'clsx'

// ─── Helpers ──────────────────────────────────────────────────────────────────

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

// ─── All-time stat card ───────────────────────────────────────────────────────

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

// ─── Monthly table ────────────────────────────────────────────────────────────

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

// ─── Individual thesis detail table ───────────────────────────────────────────

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

// ─── Page ──────────────────────────────────────────────────────────────────────

export function AccuracyPage() {
  const { data: accuracy, isLoading: aLoading } = useQuery({
    queryKey: ['thesis', 'accuracy'],
    queryFn: api.thesisAccuracy,
  })

  const { data: outcomes, isLoading: oLoading } = useQuery({
    queryKey: ['thesis', 'outcomes', 90],
    queryFn: () => api.thesisOutcomes(90),
  })

  const isLoading = aLoading || oLoading
  const noData = !isLoading && !accuracy?.data_available

  if (noData) {
    return (
      <Shell title="Claude Accuracy">
        <EmptyState
          message="No thesis outcomes yet"
          command="python thesis_checker.py"
        />
      </Shell>
    )
  }

  const at = accuracy?.all_time
  const months = accuracy?.by_month ?? []
  const rows = outcomes?.data ?? []

  return (
    <Shell title="Claude Accuracy">
      <div className="space-y-5">

        {/* All-time summary */}
        {isLoading ? (
          <LoadingSkeleton rows={2} />
        ) : (
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
        )}

        {/* Secondary stats */}
        {!isLoading && at && (
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
        {isLoading ? (
          <LoadingSkeleton rows={4} />
        ) : months.length > 0 ? (
          <MonthlyTable months={months} />
        ) : null}

        {/* Individual outcomes */}
        {isLoading ? (
          <LoadingSkeleton rows={8} />
        ) : (
          <OutcomesTable outcomes={rows} />
        )}

      </div>
    </Shell>
  )
}
