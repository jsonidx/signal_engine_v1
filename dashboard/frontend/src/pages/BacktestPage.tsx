import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
  ResponsiveContainer,
} from 'recharts'
import { format } from 'date-fns'
import { Shell } from '../components/layout/Shell'
import { MonoNumber } from '../components/ui/MonoNumber'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { EmptyState } from '../components/ui/EmptyState'
import { api, type BacktestResult, type FactorIC } from '../lib/api'
import { clsx } from 'clsx'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function barColor(sharpe: number): string {
  if (sharpe > 0.5) return '#22c55e'
  if (sharpe >= 0) return '#f59e0b'
  return '#ef4444'
}

function icirBadge(icir: number): { label: string; color: string } {
  if (icir > 1.0) return { label: 'Increase weight', color: 'bg-accent-green/15 text-accent-green border-accent-green/30' }
  if (icir >= 0.3) return { label: 'Keep weight', color: 'bg-text-tertiary/10 text-text-secondary border-text-tertiary/20' }
  if (icir >= 0) return { label: 'Reduce weight', color: 'bg-accent-amber/15 text-accent-amber border-accent-amber/30' }
  return { label: 'Remove / invert', color: 'bg-accent-red/15 text-accent-red border-accent-red/30' }
}

// ─── Factor IC table ──────────────────────────────────────────────────────────

function FactorICTable({ factors }: { factors: FactorIC[] }) {
  const sorted = [...factors].sort((a, b) => b.ic_ir - a.ic_ir)

  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      <div className="px-4 py-3 border-b border-border-subtle">
        <div className="font-mono text-xs text-text-tertiary uppercase tracking-widest">
          Factor Information Coefficients — Out of Sample
        </div>
      </div>
      <table className="w-full">
        <thead>
          <tr className="border-b border-border-subtle">
            {['Factor', 'Mean IC', 'IC IR', 'Contribution %', 'Recommendation'].map(h => (
              <th key={h} className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map(f => {
            const badge = icirBadge(f.ic_ir)
            return (
              <tr key={f.factor} className="border-b border-border-subtle/50 hover:bg-bg-elevated transition-colors">
                <td className="px-4 py-2.5 font-mono text-xs text-text-primary">{f.factor}</td>
                <td className="px-4 py-2.5">
                  <MonoNumber value={f.mean_ic} decimals={3} colorBySign />
                </td>
                <td className="px-4 py-2.5">
                  <MonoNumber
                    value={f.ic_ir}
                    decimals={2}
                    colorBySign
                    className={clsx(
                      'font-semibold',
                      f.ic_ir > 1.0 ? 'text-accent-green'
                        : f.ic_ir >= 0.3 ? 'text-text-secondary'
                        : f.ic_ir >= 0 ? 'text-accent-amber'
                        : 'text-accent-red'
                    )}
                  />
                </td>
                <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                  {f.contribution_pct?.toFixed(1) ?? '—'}%
                </td>
                <td className="px-4 py-2.5">
                  <span className={clsx('font-mono text-[10px] px-1.5 py-0.5 rounded border', badge.color)}>
                    {badge.label}
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ─── Weight rebalancing bars ───────────────────────────────────────────────────

function WeightBars({ factors }: { factors: FactorIC[] }) {
  if (!factors.length) return null
  const totalCurrent = factors.reduce((s, f) => s + (f.current_weight ?? 0), 0) || 1
  const totalSuggested = factors.reduce((s, f) => s + (f.suggested_weight ?? 0), 0) || 1

  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-4">
      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-3">
        Suggested Reweighting
      </div>
      <div className="space-y-2.5">
        {factors.map(f => {
          const cur = ((f.current_weight ?? 0) / totalCurrent) * 100
          const sug = ((f.suggested_weight ?? 0) / totalSuggested) * 100
          const delta = sug - cur
          return (
            <div key={f.factor} className="grid items-center gap-2" style={{ gridTemplateColumns: '120px 1fr 1fr 48px' }}>
              <div className="font-mono text-[11px] text-text-secondary truncate">{f.factor}</div>
              <div className="space-y-0.5">
                <div className="h-2 bg-bg-elevated rounded overflow-hidden">
                  <div style={{ width: `${cur}%` }} className="h-full bg-text-tertiary/50 rounded" />
                </div>
              </div>
              <div className="space-y-0.5">
                <div className="h-2 bg-bg-elevated rounded overflow-hidden">
                  <div
                    style={{ width: `${sug}%` }}
                    className={clsx('h-full rounded', delta > 1 ? 'bg-accent-green' : delta < -1 ? 'bg-accent-red' : 'bg-text-tertiary/40')}
                  />
                </div>
              </div>
              <div className={clsx(
                'font-mono text-[10px] text-right',
                delta > 1 ? 'text-accent-green' : delta < -1 ? 'text-accent-red' : 'text-text-tertiary'
              )}>
                {delta > 0 ? '+' : ''}{delta.toFixed(1)}%
              </div>
            </div>
          )
        })}
        <div className="grid gap-2 pt-1 border-t border-border-subtle/50" style={{ gridTemplateColumns: '120px 1fr 1fr 48px' }}>
          <div className="font-mono text-[10px] text-text-tertiary">Legend</div>
          <div className="flex items-center gap-1">
            <div className="w-6 h-2 bg-text-tertiary/50 rounded" />
            <span className="font-mono text-[9px] text-text-tertiary">Current</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-6 h-2 bg-accent-blue/70 rounded" />
            <span className="font-mono text-[9px] text-text-tertiary">Suggested</span>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Window detail side panel ─────────────────────────────────────────────────

function WindowDetail({ window: w, onClose }: { window: BacktestResult; onClose: () => void }) {
  return (
    <div className="bg-bg-surface border border-border-active rounded p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="font-mono text-xs text-text-tertiary uppercase tracking-widest">Window Detail</div>
        <button onClick={onClose} className="text-text-tertiary hover:text-text-secondary font-mono text-xs">✕</button>
      </div>
      <div className="font-mono text-xs text-text-secondary">
        {w.period_start} → {w.period_end}
      </div>
      <div className="space-y-2">
        {[
          { label: 'Return', value: w.total_return_pct, suffix: '%' },
          { label: 'Sharpe', value: w.sharpe, suffix: '' },
          { label: 'Max DD', value: w.max_drawdown_pct, suffix: '%' },
          { label: 'Hit Rate', value: w.hit_rate_pct, suffix: '%' },
          { label: 'Trades', value: w.n_trades, suffix: '' },
        ].map(({ label, value, suffix }) => (
          <div key={label} className="flex justify-between">
            <span className="font-mono text-[10px] text-text-tertiary uppercase">{label}</span>
            <span className={clsx(
              'font-mono text-xs font-semibold',
              value > 0 ? 'text-accent-green' : value < 0 ? 'text-accent-red' : 'text-text-secondary'
            )}>
              {value > 0 && label !== 'Trades' && label !== 'Hit Rate' ? '+' : ''}{typeof value === 'number' ? value.toFixed(2) : value}{suffix}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Page ──────────────────────────────────────────────────────────────────────

export function BacktestPage() {
  const [selectedWindow, setSelectedWindow] = useState<BacktestResult | null>(null)

  const { data: windows, isLoading: wLoading } = useQuery({
    queryKey: ['backtest', 'results'],
    queryFn: api.backtestResults,
  })

  const { data: summary, isLoading: sLoading } = useQuery({
    queryKey: ['backtest', 'summary'],
    queryFn: api.backtestSummaryFull,
  })

  const isLoading = wLoading || sLoading
  const noData = !isLoading && (!windows || windows.length === 0)

  if (noData) {
    return (
      <Shell title="Walk-Forward Backtest">
        <EmptyState
          message="No backtest results found"
          command="python backtest.py"
        />
      </Shell>
    )
  }

  const oosSharpe = summary?.oos_sharpe
  const spySharpe = summary?.spy_sharpe
  const worstDD = summary?.worst_drawdown_window
  const turnover = summary?.annual_turnover_pct
  const costBps = summary?.cost_bps
  const factorICs = summary?.factor_ics ?? []

  return (
    <Shell title="Walk-Forward Backtest">
      <div className="space-y-5">
        {/* Top metrics */}
        {isLoading ? (
          <LoadingSkeleton rows={3} />
        ) : (
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-bg-surface border border-border-subtle rounded p-4">
              <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
                OOS Sharpe Ratio
              </div>
              <div className={clsx(
                'font-mono text-[32px] font-semibold leading-none',
                (oosSharpe ?? 0) > 0.5 ? 'text-accent-green' : (oosSharpe ?? 0) > 0 ? 'text-accent-amber' : 'text-accent-red'
              )}>
                {oosSharpe !== undefined ? oosSharpe.toFixed(2) : '—'}
              </div>
            </div>
            <div className="bg-bg-surface border border-border-subtle rounded p-4">
              <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
                vs SPY Sharpe
              </div>
              <div className={clsx(
                'font-mono text-[32px] font-semibold leading-none',
                (spySharpe ?? 0) > 0.5 ? 'text-accent-green' : (spySharpe ?? 0) > 0 ? 'text-accent-amber' : 'text-accent-red'
              )}>
                {spySharpe !== undefined ? spySharpe.toFixed(2) : '—'}
              </div>
            </div>
            <div className="bg-bg-surface border border-border-subtle rounded p-4">
              <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
                Worst Drawdown Window
              </div>
              {worstDD ? (
                <>
                  <div className="font-mono text-[28px] font-semibold leading-none text-accent-red">
                    {worstDD.drawdown_pct.toFixed(1)}%
                  </div>
                  <div className="font-mono text-[10px] text-text-tertiary mt-1">
                    {worstDD.start} → {worstDD.end}
                  </div>
                </>
              ) : (
                <div className="font-mono text-[28px] font-semibold leading-none text-text-tertiary">—</div>
              )}
            </div>
          </div>
        )}

        {/* Walk-forward timeline */}
        <div className="bg-bg-surface border border-border-subtle rounded p-4">
          <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-4">
            Walk-Forward Windows — Sharpe by Period
          </div>
          {isLoading ? (
            <LoadingSkeleton className="h-48" />
          ) : (
            <div className="flex gap-4">
              <div className="flex-1">
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart
                    data={windows}
                    layout="horizontal"
                    margin={{ top: 4, right: 8, left: 0, bottom: 4 }}
                    onClick={(e: unknown) => {
                      const ev = e as { activePayload?: Array<{ payload: BacktestResult }> }
                      if (ev?.activePayload?.[0]?.payload) {
                        setSelectedWindow(ev.activePayload[0].payload)
                      }
                    }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
                    <XAxis
                      dataKey="period_start"
                      tick={{ fill: '#52525b', fontSize: 10, fontFamily: 'IBM Plex Mono' }}
                      tickFormatter={v => {
                        try { return format(new Date(v), 'MMM yy') } catch { return v }
                      }}
                      tickLine={false}
                      axisLine={{ stroke: '#27272a' }}
                    />
                    <YAxis
                      tick={{ fill: '#52525b', fontSize: 10, fontFamily: 'IBM Plex Mono' }}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={v => v.toFixed(1)}
                    />
                    <Tooltip
                      contentStyle={{
                        background: '#18181b',
                        border: '1px solid #3f3f46',
                        borderRadius: 4,
                        fontFamily: 'IBM Plex Mono',
                        fontSize: 11,
                      }}
                      formatter={(v: unknown) => [(v as number).toFixed(2), 'Sharpe']}
                      labelFormatter={v => {
                        try { return format(new Date(v as string), 'MMM d, yyyy') } catch { return v as string }
                      }}
                    />
                    <Bar dataKey="sharpe" radius={[2, 2, 0, 0]} cursor="pointer">
                      {(windows ?? []).map((w, i) => (
                        <Cell key={i} fill={barColor(w.sharpe)} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
                <div className="flex items-center gap-4 mt-2">
                  {[
                    { color: '#22c55e', label: 'Sharpe > 0.5' },
                    { color: '#f59e0b', label: 'Sharpe 0–0.5' },
                    { color: '#ef4444', label: 'Sharpe < 0' },
                  ].map(({ color, label }) => (
                    <div key={label} className="flex items-center gap-1.5">
                      <div className="w-3 h-3 rounded-sm" style={{ background: color }} />
                      <span className="font-mono text-[10px] text-text-tertiary">{label}</span>
                    </div>
                  ))}
                  <span className="ml-auto font-mono text-[10px] text-text-tertiary">Click bar for details</span>
                </div>
              </div>

              {/* Side panel */}
              {selectedWindow && (
                <div className="w-48 flex-shrink-0">
                  <WindowDetail window={selectedWindow} onClose={() => setSelectedWindow(null)} />
                </div>
              )}
            </div>
          )}
        </div>

        {/* Factor IC table */}
        {isLoading ? (
          <LoadingSkeleton rows={6} />
        ) : factorICs.length > 0 ? (
          <>
            <FactorICTable factors={factorICs} />
            <WeightBars factors={factorICs} />
          </>
        ) : (
          <div className="bg-bg-surface border border-border-subtle rounded p-4 font-mono text-sm text-text-tertiary text-center">
            Factor IC data not yet available — run walk-forward backtest first
          </div>
        )}

        {/* Transaction cost */}
        {(turnover !== undefined || costBps !== undefined) && (
          <div className="bg-bg-surface border border-border-subtle rounded p-4">
            <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-3">
              Transaction Cost Analysis
            </div>
            <div className="flex gap-8">
              {turnover !== undefined && (
                <div>
                  <div className="font-mono text-[10px] text-text-tertiary uppercase mb-1">Annual Turnover</div>
                  <MonoNumber value={turnover} suffix="%" />
                </div>
              )}
              {costBps !== undefined && (
                <div>
                  <div className="font-mono text-[10px] text-text-tertiary uppercase mb-1">Cost (bps, one-way)</div>
                  <MonoNumber value={costBps} decimals={1} />
                </div>
              )}
              {costBps !== undefined && turnover !== undefined && (
                <div>
                  <div className="font-mono text-[10px] text-text-tertiary uppercase mb-1">Est. Annual Drag</div>
                  <MonoNumber
                    value={(costBps / 10000) * turnover}
                    suffix="%"
                    decimals={2}
                    className="text-accent-amber"
                  />
                </div>
              )}
            </div>
          </div>
        )}

        {/* Periods table */}
        <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
          <div className="px-4 py-3 border-b border-border-subtle">
            <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
              All Walk-Forward Periods
            </span>
          </div>
          {isLoading ? (
            <div className="p-4"><LoadingSkeleton rows={6} /></div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-border-subtle">
                  {['Period', 'Return', 'Sharpe', 'Max DD', 'Hit Rate', 'Trades'].map(h => (
                    <th key={h} className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(windows ?? []).map((row, i) => (
                  <tr
                    key={i}
                    onClick={() => setSelectedWindow(row)}
                    className="border-b border-border-subtle/50 hover:bg-bg-elevated cursor-pointer transition-colors"
                  >
                    <td className="px-4 py-3 font-mono text-xs text-text-secondary">
                      {row.period_start} → {row.period_end}
                    </td>
                    <td className="px-4 py-3">
                      <MonoNumber value={row.total_return_pct} suffix="%" colorBySign />
                    </td>
                    <td className="px-4 py-3">
                      <MonoNumber value={row.sharpe} decimals={2} colorBySign />
                    </td>
                    <td className="px-4 py-3">
                      <MonoNumber value={row.max_drawdown_pct} suffix="%" colorBySign />
                    </td>
                    <td className="px-4 py-3">
                      <MonoNumber value={row.hit_rate_pct} suffix="%" />
                    </td>
                    <td className="px-4 py-3 font-mono text-sm text-text-secondary">{row.n_trades}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </Shell>
  )
}
