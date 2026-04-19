import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import * as Tabs from '@radix-ui/react-tabs'
import { Check, Minus, RefreshCw } from 'lucide-react'
import { format } from 'date-fns'
import { useNavigate } from 'react-router-dom'
import { Shell } from '../components/layout/Shell'
import { MetricCard } from '../components/ui/MetricCard'
import { MonoNumber } from '../components/ui/MonoNumber'
import { DirectionBadge } from '../components/ui/DirectionBadge'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { EmptyState } from '../components/ui/EmptyState'
import { api, type AccuracyMatrixCell, type ThesisOutcome, type ThesisAccuracyMonth, type BenchmarkModelSummary, type BenchmarkOutcomeRow, type LivePerformanceRow, type BuyHoldRow } from '../lib/api'
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

// ─── Accuracy helpers ─────────────────────────────────────────────────────────

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

// ─── By-Model benchmark ───────────────────────────────────────────────────────

function modelLabel(model: string): string {
  if (model === 'unknown') return 'Unknown (legacy)'
  return model.replace('claude-', 'Claude ').replace('grok-', 'Grok ')
}

function modelColor(model: string): string {
  if (model.includes('claude')) return 'text-accent-purple border-accent-purple/30 bg-accent-purple/10'
  if (model.includes('grok'))   return 'text-accent-blue   border-accent-blue/30   bg-accent-blue/10'
  return 'text-text-tertiary border-border-subtle bg-bg-elevated'
}

function ModelCard({ m, capital }: { m: BenchmarkModelSummary; capital: number }) {
  const resolved = m.wins + m.losses
  const wr = m.win_rate_pct
  const wrColor = wr == null ? 'text-text-tertiary' : wr >= 55 ? 'text-accent-green font-semibold' : wr >= 40 ? 'text-accent-amber' : 'text-accent-red'
  const t1Color = m.t1_hit_rate_pct == null ? 'text-text-tertiary' : m.t1_hit_rate_pct >= 40 ? 'text-accent-green font-semibold' : m.t1_hit_rate_pct >= 25 ? 'text-accent-amber' : 'text-accent-red'

  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-4 space-y-3">
      <div className={clsx('inline-flex items-center px-2 py-0.5 rounded border font-mono text-[10px] font-semibold', modelColor(m.model))}>
        {modelLabel(m.model)}
      </div>
      <div className="grid grid-cols-3 gap-2 text-center">
        <div>
          <div className="font-mono text-lg font-bold text-text-primary">{m.theses}</div>
          <div className="font-mono text-[9px] uppercase text-text-tertiary">theses</div>
        </div>
        <div>
          <div className={clsx('font-mono text-lg font-bold', wrColor)}>
            {wr != null ? `${wr}%` : '—'}
          </div>
          <div className="font-mono text-[9px] uppercase text-text-tertiary">win rate</div>
          <div className="font-mono text-[9px] text-text-tertiary">{m.wins}W / {m.losses}L · {m.open_count} open</div>
        </div>
        <div>
          <div className={clsx('font-mono text-lg font-bold', t1Color)}>
            {m.t1_hit_rate_pct != null ? `${m.t1_hit_rate_pct}%` : '—'}
          </div>
          <div className="font-mono text-[9px] uppercase text-text-tertiary">T1 hit rate</div>
        </div>
      </div>
      <div className="border-t border-border-subtle/50 pt-2 grid grid-cols-2 gap-x-4 gap-y-1">
        {[
          ['T2 hit rate',   m.t2_hit_rate_pct   != null ? `${m.t2_hit_rate_pct}%`   : '—'],
          ['Stop rate',     m.stop_rate_pct      != null ? `${m.stop_rate_pct}%`     : '—'],
          ['Avg vs T1',     m.avg_vs_t1_pct      != null ? `${m.avg_vs_t1_pct > 0 ? '+' : ''}${m.avg_vs_t1_pct}%` : '—'],
          ['Avg days→T1',   m.avg_days_to_t1     != null ? `${m.avg_days_to_t1}d`   : '—'],
          ['Avg 30d return',m.avg_return_30d      != null ? `${m.avg_return_30d > 0 ? '+' : ''}${m.avg_return_30d}%` : '—'],
          ['Bull / Bear',   `${m.bull_count} / ${m.bear_count}`],
        ].map(([label, value]) => (
          <div key={label} className="flex justify-between gap-2">
            <span className="font-mono text-[10px] text-text-tertiary">{label}</span>
            <span className="font-mono text-[10px] text-text-secondary">{value}</span>
          </div>
        ))}
      </div>
      {/* Capital forecast */}
      {m.avg_return_30d != null && (
        <div className="border-t border-border-subtle/50 pt-2 space-y-1">
          <div className="font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
            If €{capital.toLocaleString()} per trade
          </div>
          {(() => {
            const avgProfit = Math.round(capital * m.avg_return_30d / 100)
            const totalProfit = Math.round(capital * m.avg_return_30d / 100 * (m.wins + m.losses))
            return (
              <div className="flex justify-between gap-2">
                <div className="text-center">
                  <div className={clsx('font-mono text-sm font-bold', avgProfit >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                    {avgProfit >= 0 ? '+' : ''}€{avgProfit}
                  </div>
                  <div className="font-mono text-[9px] text-text-tertiary">avg per trade</div>
                </div>
                <div className="text-center">
                  <div className={clsx('font-mono text-sm font-bold', totalProfit >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                    {totalProfit >= 0 ? '+' : ''}€{totalProfit}
                  </div>
                  <div className="font-mono text-[9px] text-text-tertiary">total ({m.wins + m.losses} resolved)</div>
                </div>
              </div>
            )
          })()}
        </div>
      )}
    </div>
  )
}

function BenchmarkOutcomesTable({ rows, modelFilter, setModelFilter, models, capital }: {
  rows: BenchmarkOutcomeRow[]
  modelFilter: string
  setModelFilter: (m: string) => void
  models: string[]
  capital: number
}) {
  const navigate = useNavigate()
  const filtered = modelFilter === 'all' ? rows : rows.filter(r => r.model === modelFilter)
  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
        <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
          Per-Thesis Outcomes <span className="normal-case text-text-tertiary/60">({filtered.length})</span>
        </span>
        <div className="flex gap-1.5">
          {models.map(m => (
            <button key={m} onClick={() => setModelFilter(m)}
              className={clsx('font-mono text-[9px] px-2 py-0.5 rounded border transition-colors',
                modelFilter === m
                  ? 'bg-accent-blue/20 border-accent-blue/40 text-accent-blue'
                  : 'border-border-subtle text-text-tertiary hover:text-text-secondary'
              )}
            >
              {m === 'all' ? 'All' : modelLabel(m)}
            </button>
          ))}
        </div>
      </div>
      {filtered.length === 0 ? (
        <div className="py-8 text-center font-mono text-xs text-text-tertiary">No outcomes for selected model.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-border-subtle">
                {['Date','Ticker','Model','Dir','Conv','Outcome','T1','T2','Stop','7d ret','30d ret',`Profit (30d)`, 'vs T1','Days→T1'].map(h => (
                  <th key={h} className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((r, i) => (
                <tr key={i} onClick={() => navigate(`/ticker/${r.ticker}`)}
                  className="border-b border-border-subtle/40 last:border-0 hover:bg-bg-elevated cursor-pointer transition-colors"
                >
                  <td className="px-3 py-2 font-mono text-[10px] text-text-tertiary">{r.thesis_date}</td>
                  <td className="px-3 py-2 font-mono text-xs font-semibold text-text-primary">{r.ticker}</td>
                  <td className="px-3 py-2">
                    <span className={clsx('font-mono text-[9px] px-1.5 py-0.5 rounded border', modelColor(r.model))}>
                      {modelLabel(r.model)}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <span className={clsx('font-mono text-[10px] font-semibold',
                      r.direction === 'BULL' ? 'text-accent-green' : r.direction === 'BEAR' ? 'text-accent-red' : 'text-text-tertiary'
                    )}>{r.direction}</span>
                  </td>
                  <td className="px-3 py-2 font-mono text-[10px] text-text-secondary text-center">{r.conviction ?? '—'}</td>
                  <td className="px-3 py-2">{outcomeChip(r.outcome as ThesisOutcome['outcome'])}</td>
                  <td className="px-3 py-2 text-center font-mono text-xs">{r.hit_target_1 ? '✓' : '·'}</td>
                  <td className="px-3 py-2 text-center font-mono text-xs">{r.hit_target_2 ? '✓' : '·'}</td>
                  <td className="px-3 py-2 text-center font-mono text-xs">{r.hit_stop ? '✗' : '·'}</td>
                  <td className={clsx('px-3 py-2 font-mono text-[10px]', r.return_7d == null ? 'text-text-tertiary' : r.return_7d >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                    {r.return_7d != null ? `${r.return_7d >= 0 ? '+' : ''}${r.return_7d.toFixed(1)}%` : '—'}
                  </td>
                  <td className={clsx('px-3 py-2 font-mono text-[10px]', r.return_30d == null ? 'text-text-tertiary' : r.return_30d >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                    {r.return_30d != null ? `${r.return_30d >= 0 ? '+' : ''}${r.return_30d.toFixed(1)}%` : '—'}
                  </td>
                  <td className={clsx('px-3 py-2 font-mono text-[10px] font-semibold', r.outcome_return_pct == null ? 'text-text-tertiary' : r.outcome_return_pct >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                    {r.outcome_return_pct != null ? `${r.outcome_return_pct >= 0 ? '+' : ''}€${Math.round(capital * r.outcome_return_pct / 100)}` : '—'}
                  </td>
                  <td className={clsx('px-3 py-2 font-mono text-[10px]', r.vs_target_1_pct == null ? 'text-text-tertiary' : r.vs_target_1_pct >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                    {r.vs_target_1_pct != null ? `${r.vs_target_1_pct >= 0 ? '+' : ''}${r.vs_target_1_pct.toFixed(1)}%` : '—'}
                  </td>
                  <td className="px-3 py-2 font-mono text-[10px] text-text-tertiary">
                    {r.days_to_target_1 != null ? `${r.days_to_target_1}d` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ─── Live Performance panel ───────────────────────────────────────────────────

const STATUS_COLOR: Record<string, string> = {
  HIT_T2:     'bg-accent-green/25 text-accent-green  border-accent-green/50',
  HIT_T1:     'bg-accent-green/15 text-accent-green  border-accent-green/30',
  ADVANCING:  'bg-accent-blue/15  text-accent-blue   border-accent-blue/30',
  FLAT:       'bg-text-tertiary/10 text-text-tertiary border-text-tertiary/20',
  RETREATING: 'bg-accent-amber/15 text-accent-amber  border-accent-amber/30',
  AT_STOP:    'bg-accent-red/15   text-accent-red    border-accent-red/30',
}

function LiveStatusBadge({ status }: { status: string }) {
  return (
    <span className={clsx('font-mono text-[10px] px-1.5 py-0.5 rounded border whitespace-nowrap', STATUS_COLOR[status] ?? STATUS_COLOR.FLAT)}>
      {status.replace('_', ' ')}
    </span>
  )
}

function ProgressBar({ progress, status }: { progress: number | null; status: string }) {
  const pct = Math.min(100, Math.max(0, (progress ?? 0) * 100))
  const color = status === 'HIT_T1' || status === 'HIT_T2'
    ? 'bg-accent-green'
    : status === 'AT_STOP' ? 'bg-accent-red'
    : status === 'ADVANCING' ? 'bg-accent-blue'
    : status === 'RETREATING' ? 'bg-accent-amber'
    : 'bg-text-tertiary/40'
  return (
    <div className="w-full h-1.5 bg-bg-elevated rounded-full overflow-hidden">
      <div className={clsx('h-full rounded-full transition-all', color)} style={{ width: `${pct}%` }} />
    </div>
  )
}

function LivePerformancePanel() {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['thesis', 'live-performance'],
    queryFn: api.thesisLivePerformance,
    staleTime: 0,
    refetchInterval: 5 * 60 * 1000,
  })

  const rows: LivePerformanceRow[] = data?.data ?? []

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
          Open Theses — Live Price vs Targets
        </span>
        <button onClick={() => refetch()} className="p-1.5 rounded border border-border-subtle text-text-tertiary hover:text-text-primary transition-colors ml-auto">
          <RefreshCw size={11} />
        </button>
        {data?.as_of && (
          <span className="font-mono text-[10px] text-text-tertiary">
            {data.as_of}
          </span>
        )}
      </div>

      {isLoading ? (
        <LoadingSkeleton rows={6} />
      ) : rows.length === 0 ? (
        <EmptyState message="No open theses tracked" command="python thesis_checker.py" />
      ) : (
        <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border-subtle">
                  {['Ticker', 'Dir', 'Model', 'Date', 'Entry', 'Now', 'P&L', 'Progress→T1', '→T1', '→T2', '→Stop', 'Status'].map(h => (
                    <th key={h} className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const pnlPos = r.pnl_pct != null && r.pnl_pct >= 0
                  return (
                    <tr key={i} className="border-b border-border-subtle/40 last:border-0 hover:bg-bg-elevated transition-colors">
                      <td className="px-3 py-2.5 font-mono text-xs font-semibold text-text-primary">{r.ticker}</td>
                      <td className="px-3 py-2.5">
                        <span className={clsx('font-mono text-[10px] font-semibold',
                          r.direction === 'BULL' ? 'text-accent-green' : r.direction === 'BEAR' ? 'text-accent-red' : 'text-text-tertiary'
                        )}>{r.direction}</span>
                      </td>
                      <td className="px-3 py-2.5">
                        <span className={clsx('font-mono text-[9px] px-1.5 py-0.5 rounded border', modelColor(r.model))}>
                          {modelLabel(r.model)}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 font-mono text-[10px] text-text-tertiary">{r.thesis_date}</td>
                      <td className="px-3 py-2.5 font-mono text-[11px] text-text-secondary">
                        {r.entry_price != null ? r.entry_price.toFixed(2) : '—'}
                      </td>
                      <td className="px-3 py-2.5 font-mono text-[11px] text-text-primary font-semibold">
                        {r.current_price != null ? r.current_price.toFixed(2) : '—'}
                      </td>
                      <td className={clsx('px-3 py-2.5 font-mono text-xs font-semibold',
                        r.pnl_pct == null ? 'text-text-tertiary' : pnlPos ? 'text-accent-green' : 'text-accent-red'
                      )}>
                        {r.pnl_pct != null ? `${pnlPos ? '+' : ''}${r.pnl_pct.toFixed(1)}%` : '—'}
                      </td>
                      <td className="px-3 py-2.5 w-28">
                        <ProgressBar progress={r.progress_t1} status={r.status} />
                      </td>
                      <td className={clsx('px-3 py-2.5 font-mono text-[10px]',
                        r.pct_to_t1 == null ? 'text-text-tertiary' : r.pct_to_t1 <= 0 ? 'text-accent-green' : 'text-accent-amber'
                      )}>
                        {r.pct_to_t1 != null ? `${r.pct_to_t1 > 0 ? '+' : ''}${r.pct_to_t1.toFixed(1)}%` : '—'}
                      </td>
                      <td className="px-3 py-2.5 font-mono text-[10px] text-text-tertiary">
                        {r.pct_to_t2 != null ? `${r.pct_to_t2 > 0 ? '+' : ''}${r.pct_to_t2.toFixed(1)}%` : '—'}
                      </td>
                      <td className={clsx('px-3 py-2.5 font-mono text-[10px]',
                        r.pct_to_stop == null ? 'text-text-tertiary' : r.pct_to_stop <= 5 ? 'text-accent-red' : 'text-text-tertiary'
                      )}>
                        {r.pct_to_stop != null ? `${r.pct_to_stop.toFixed(1)}%` : '—'}
                      </td>
                      <td className="px-3 py-2.5">
                        <LiveStatusBadge status={r.status} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="font-mono text-[10px] text-text-tertiary">
        P&amp;L = direction-adjusted · Progress = how far price has moved toward T1 from entry · →T1/T2/Stop = remaining % to reach level
      </div>
    </div>
  )
}

// ─── Buy & Hold comparison ────────────────────────────────────────────────────

const MAG7 = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA']

function pctColor(v: number | null) {
  if (v == null) return 'text-text-tertiary'
  return v >= 0 ? 'text-accent-green' : 'text-accent-red'
}

function advantageColor(v: number | null): string {
  if (v == null) return 'text-text-tertiary'
  if (v > 2)   return 'text-accent-green font-semibold'
  if (v > 0)   return 'text-accent-green'
  if (v > -2)  return 'text-accent-red'
  return 'text-accent-red font-semibold'
}

function verdictStyle(verdict: string) {
  if (verdict.includes('AI')) return 'text-accent-green bg-accent-green/10 border-accent-green/30'
  if (verdict.includes('Buy')) return 'text-accent-amber bg-accent-amber/10 border-accent-amber/30'
  return 'text-text-tertiary bg-bg-elevated border-border-subtle'
}

function BuyHoldPanel({ tickerFilter }: { tickerFilter: string }) {
  const tickers = tickerFilter || MAG7.join(',')
  const { data, isLoading } = useQuery({
    queryKey: ['thesis', 'buyhold', tickers],
    queryFn: () => api.thesisBuyHold(tickers),
    staleTime: 0,
  })

  const rows: BuyHoldRow[] = (data?.data ?? []).filter(r => r.theses > 0)
  const agg = data?.aggregate

  if (isLoading) return <LoadingSkeleton rows={4} />
  if (!data?.data_available || rows.length === 0) {
    return (
      <div className="py-6 text-center font-mono text-xs text-text-tertiary">
        No thesis data for selected tickers yet.
      </div>
    )
  }

  const fmt = (v: number | null) => v != null ? `${v > 0 ? '+' : ''}${v.toFixed(1)}%` : '—'

  return (
    <div className="space-y-4">
      {/* Verdict + aggregate banner */}
      {agg && (
        <div className="space-y-3">
          <div className={clsx('flex items-center justify-between px-4 py-3 rounded border font-mono', verdictStyle(agg.verdict))}>
            <div className="space-y-0.5">
              <div className="text-[9px] uppercase tracking-widest opacity-70">
                Since {agg.earliest_thesis_date} · {agg.total_theses} theses on {agg.tickers_with_data} tickers
              </div>
              <div className="text-sm font-bold">{agg.verdict}</div>
            </div>
            <div className="flex gap-8 text-right">
              <div>
                <div className="text-[9px] uppercase opacity-70 mb-0.5">Buy &amp; Hold avg</div>
                <div className={clsx('text-lg font-bold', pctColor(agg.avg_bh_return))}>{fmt(agg.avg_bh_return)}</div>
              </div>
              <div>
                <div className="text-[9px] uppercase opacity-70 mb-0.5">AI trading total</div>
                <div className={clsx('text-lg font-bold', pctColor(agg.avg_ai_total_return))}>{fmt(agg.avg_ai_total_return)}</div>
              </div>
              <div>
                <div className="text-[9px] uppercase opacity-70 mb-0.5">AI advantage</div>
                <div className={clsx('text-lg font-bold', advantageColor(agg.avg_advantage))}>{fmt(agg.avg_advantage)}</div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Per-ticker table */}
      <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border-subtle bg-bg-elevated">
              {['Ticker', 'Since', 'Entry→Now', 'Buy & Hold', 'AI Total Return', 'Avg/Trade', 'Win Rate', 'AI Advantage', 'Trades'].map(h => (
                <th key={h} className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.ticker} className="border-b border-border-subtle/40 last:border-0 hover:bg-bg-elevated transition-colors">
                <td className="px-3 py-3 font-mono text-sm font-bold text-text-primary">{r.ticker}</td>
                <td className="px-3 py-3 font-mono text-[10px] text-text-tertiary whitespace-nowrap">{r.first_thesis_date ?? '—'}</td>
                <td className="px-3 py-3 font-mono text-[10px] text-text-tertiary">
                  {r.first_entry_price != null && r.current_price != null
                    ? `${r.first_entry_price.toFixed(0)} → ${r.current_price.toFixed(0)}`
                    : '—'}
                </td>
                <td className={clsx('px-3 py-3 font-mono text-sm font-semibold', pctColor(r.bh_return))}>
                  {fmt(r.bh_return)}
                </td>
                <td className={clsx('px-3 py-3 font-mono text-sm font-semibold', pctColor(r.ai_total_return))}>
                  {fmt(r.ai_total_return)}
                </td>
                <td className={clsx('px-3 py-3 font-mono text-xs', pctColor(r.ai_avg_return))}>
                  {fmt(r.ai_avg_return)}
                </td>
                <td className={clsx('px-3 py-3 font-mono text-xs', winRateColor(r.ai_win_rate != null ? r.ai_win_rate / 100 : null))}>
                  {r.ai_win_rate != null ? `${r.ai_win_rate}%` : '—'}
                </td>
                <td className={clsx('px-3 py-3 font-mono text-sm font-bold', advantageColor(r.advantage))}>
                  {r.advantage != null ? `${r.advantage > 0 ? '+' : ''}${r.advantage.toFixed(1)}%` : '—'}
                </td>
                <td className="px-3 py-3 font-mono text-[10px] text-text-tertiary whitespace-nowrap">
                  {r.theses} <span className="opacity-60">({r.wins}W·{r.losses}L·{r.open_count}O)</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="px-4 py-2.5 border-t border-border-subtle/50 bg-bg-elevated">
          <p className="font-mono text-[10px] text-text-tertiary">
            Buy &amp; Hold = bought at first thesis entry price, held to today ·
            AI Total Return = sum of all trade returns (resolved: outcome-implied; open: current P&amp;L) ·
            AI Advantage = AI total − B&amp;H
          </p>
        </div>
      </div>
    </div>
  )
}

// ─── Collapsible section ──────────────────────────────────────────────────────

function CollapsibleSection({ label, children, defaultOpen = false }: {
  label: string
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-bg-elevated transition-colors"
      >
        <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">{label}</span>
        <span className="font-mono text-[10px] text-text-tertiary">{open ? '▲ hide' : '▼ show'}</span>
      </button>
      {open && <div className="border-t border-border-subtle">{children}</div>}
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

  const { data: accuracy } = useQuery({
    queryKey: ['thesis', 'accuracy'],
    queryFn: api.thesisAccuracy,
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

  const months = accuracy?.by_month ?? []

  // By Model tab
  const [benchDays, setBenchDays] = useState(90)
  const [modelFilter, setModelFilter] = useState('all')
  const [capital, setCapital] = useState(2000)
  const [tickerInput, setTickerInput]   = useState('')
  const [tickerFilter, setTickerFilter] = useState('')
  const isMag7 = tickerFilter === MAG7.join(',')

  const { data: bench, isLoading: benchLoading, refetch: refetchBench } = useQuery({
    queryKey: ['thesis', 'benchmark', benchDays, tickerFilter],
    queryFn: () => api.thesisBenchmark(benchDays, tickerFilter),
    staleTime: 0,
  })
  const benchSummary = bench?.summary ?? []
  const benchRecent  = bench?.recent  ?? []
  const benchModels  = ['all', ...Array.from(new Set(benchRecent.map(r => r.model)))]

  return (
    <Shell title="Resolution & Accuracy">
      <Tabs.Root defaultValue="benchmark">
        <Tabs.List className="flex border-b border-border-subtle mb-5 -mx-6 px-6">
          <Tabs.Trigger value="benchmark" className={TAB_STYLE}>
            By Model
          </Tabs.Trigger>
          <Tabs.Trigger value="live" className={TAB_STYLE}>
            Live
          </Tabs.Trigger>
          <Tabs.Trigger value="resolution" className={TAB_STYLE}>
            Resolution Log
          </Tabs.Trigger>
        </Tabs.List>

        {/* ── Live Performance tab ── */}
        <Tabs.Content value="live">
          <LivePerformancePanel />
        </Tabs.Content>

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

          </div>
        </Tabs.Content>

        {/* ── By Model tab ── */}
        <Tabs.Content value="benchmark">
          <div className="space-y-5">
            {/* Controls row */}
            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[10px] text-text-tertiary">Window:</span>
                {[30, 60, 90, 180].map(d => (
                  <button key={d} onClick={() => setBenchDays(d)}
                    className={clsx('font-mono text-[10px] px-2 py-1 rounded border transition-colors',
                      benchDays === d
                        ? 'bg-accent-blue/20 border-accent-blue/40 text-accent-blue'
                        : 'border-border-subtle text-text-tertiary hover:text-text-secondary hover:border-border-active'
                    )}
                  >
                    {d}d
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-1.5">
                <span className="font-mono text-[10px] text-text-tertiary">Capital €</span>
                <input
                  type="number"
                  value={capital}
                  onChange={e => setCapital(Math.max(1, Number(e.target.value) || 2000))}
                  className="w-24 px-2 py-0.5 font-mono text-xs bg-bg-elevated border border-border-subtle rounded text-text-primary text-right focus:outline-none focus:border-accent-blue"
                />
              </div>
              {/* Ticker filter */}
              <div className="flex items-center gap-1.5 ml-auto">
                <button
                  onClick={() => {
                    const next = isMag7 ? '' : MAG7.join(',')
                    setTickerFilter(next)
                    setTickerInput(next)
                  }}
                  className={clsx('font-mono text-[10px] px-2 py-1 rounded border transition-colors',
                    isMag7
                      ? 'bg-accent-purple/20 border-accent-purple/40 text-accent-purple'
                      : 'border-border-subtle text-text-tertiary hover:text-text-secondary'
                  )}
                >
                  Mag-7
                </button>
                <input
                  type="text"
                  placeholder="AAPL,TSLA,..."
                  value={tickerInput}
                  onChange={e => setTickerInput(e.target.value.toUpperCase())}
                  onKeyDown={e => {
                    if (e.key === 'Enter') setTickerFilter(tickerInput.trim())
                    if (e.key === 'Escape') { setTickerFilter(''); setTickerInput('') }
                  }}
                  className="w-36 px-2 py-0.5 font-mono text-xs bg-bg-elevated border border-border-subtle rounded text-text-primary focus:outline-none focus:border-accent-blue placeholder-text-tertiary/40"
                />
                {tickerFilter && (
                  <button onClick={() => { setTickerFilter(''); setTickerInput('') }}
                    className="font-mono text-[10px] text-text-tertiary hover:text-text-primary px-1"
                  >✕</button>
                )}
              </div>
              <button onClick={() => refetchBench()} className="p-1.5 rounded border border-border-subtle text-text-tertiary hover:text-text-primary transition-colors">
                <RefreshCw size={11} />
              </button>
            </div>

            {benchLoading ? (
              <LoadingSkeleton rows={6} />
            ) : benchSummary.length === 0 ? (
              <EmptyState message="No thesis outcomes yet" command="python thesis_checker.py" />
            ) : (
              <>
                <div className={clsx('grid gap-4',
                  benchSummary.length === 1 ? 'grid-cols-1 max-w-sm'
                  : benchSummary.length === 2 ? 'grid-cols-2'
                  : 'grid-cols-3'
                )}>
                  {benchSummary.map(m => <ModelCard key={m.model} m={m} capital={capital} />)}
                </div>
                {benchRecent.length > 0 && (
                  <BenchmarkOutcomesTable
                    rows={benchRecent}
                    modelFilter={modelFilter}
                    setModelFilter={setModelFilter}
                    models={benchModels}
                    capital={capital}
                  />
                )}

                {/* Buy & Hold comparison — always shown, defaults to Mag-7 */}
                <CollapsibleSection label={`vs Buy & Hold${tickerFilter ? ` — ${tickerFilter}` : ' — Mag-7'}`} defaultOpen={true}>
                  <div className="p-4">
                    <BuyHoldPanel tickerFilter={tickerFilter} />
                  </div>
                </CollapsibleSection>

                {/* Collapsible: Monthly Breakdown */}
                {months.length > 0 && (
                  <CollapsibleSection label="Monthly Breakdown">
                    <MonthlyTable months={months} />
                  </CollapsibleSection>
                )}

                {/* Collapsible: Accuracy Matrix */}
                <CollapsibleSection label="Accuracy Matrix (regime × conviction)">
                  <AccuracyMatrixPanel />
                </CollapsibleSection>
              </>
            )}
          </div>
        </Tabs.Content>
      </Tabs.Root>
    </Shell>
  )
}
