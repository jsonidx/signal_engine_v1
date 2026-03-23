import { useNavigate } from 'react-router-dom'
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Cell,
} from 'recharts'
import { format } from 'date-fns'
import { Shell } from '../components/layout/Shell'
import { MetricCard } from '../components/ui/MetricCard'
import { DirectionBadge } from '../components/ui/DirectionBadge'
import { ConvictionDots } from '../components/ui/ConvictionDots'
import { MonoNumber } from '../components/ui/MonoNumber'
import { SkeletonCard, LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { usePortfolioSummary, usePortfolioHistory, usePortfolioPositions } from '../hooks/usePortfolio'

function PnLTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-bg-elevated border border-border-active rounded p-3 shadow-xl">
      <div className="font-mono text-xs text-text-secondary mb-2">{label}</div>
      {payload.map((p: any) => (
        <div key={p.name} className="flex items-center gap-2 font-mono text-xs">
          <div className="w-2 h-2 rounded-full" style={{ background: p.color }} />
          <span className="text-text-secondary">{p.name}:</span>
          <span className="text-text-primary">
            {p.name === 'portfolio' ? `€${p.value?.toFixed(0)}` : `${p.value?.toFixed(2)}%`}
          </span>
        </div>
      ))}
    </div>
  )
}

function WeeklyTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const val = payload[0].value
  return (
    <div className="bg-bg-elevated border border-border-active rounded p-2 shadow-xl">
      <div className="font-mono text-xs text-text-secondary">{label}</div>
      <div className={`font-mono text-sm font-semibold ${val >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
        {val >= 0 ? '+' : ''}{val?.toFixed(2)}%
      </div>
    </div>
  )
}

export function PortfolioPage() {
  const navigate = useNavigate()
  const { data: summary, isLoading: summaryLoading } = usePortfolioSummary()
  const { data: history, isLoading: historyLoading } = usePortfolioHistory(52)
  const { data: positions, isLoading: positionsLoading } = usePortfolioPositions()

  const historyArr = Array.isArray(history) ? history : (history as any)?.data ?? []
  const last12Weeks = historyArr.slice(-12)

  const positionsArr = Array.isArray(positions) ? positions : (positions as any)?.data ?? []
  const sortedPositions = [...positionsArr].sort(
    (a, b) => Math.abs(b.unrealized_pnl_eur) - Math.abs(a.unrealized_pnl_eur)
  )

  return (
    <Shell title="Portfolio Overview">
      {/* Metric cards */}
      <div className="grid grid-cols-5 gap-3 mb-6">
        {summaryLoading ? (
          Array.from({ length: 5 }).map((_, i) => <SkeletonCard key={i} />)
        ) : (
          <>
            <MetricCard
              label="Weekly Return"
              value={summary?.weekly_return_pct ?? 0}
              unit="%"
              colorBySign
              sentiment={
                (summary?.weekly_return_pct ?? 0) > 0
                  ? 'positive'
                  : (summary?.weekly_return_pct ?? 0) < 0
                    ? 'negative'
                    : 'neutral'
              }
            />
            <MetricCard
              label="vs SPY"
              value={(summary?.weekly_return_pct ?? 0) - (summary?.spy_return_pct ?? 0)}
              unit="%"
              colorBySign
              sentiment={
                (summary?.weekly_return_pct ?? 0) > (summary?.spy_return_pct ?? 0)
                  ? 'positive'
                  : 'negative'
              }
            />
            <MetricCard
              label="Sharpe Ratio"
              value={summary?.sharpe_ratio ?? 0}
              colorBySign
              sentiment={
                (summary?.sharpe_ratio ?? 0) > 1
                  ? 'positive'
                  : (summary?.sharpe_ratio ?? 0) > 0
                    ? 'neutral'
                    : 'negative'
              }
            />
            <MetricCard
              label="Max Drawdown"
              value={summary?.max_drawdown_pct ?? 0}
              unit="%"
              colorBySign
              sentiment="negative"
            />
            <MetricCard
              label="Hit Rate"
              value={summary?.hit_rate_pct ?? 0}
              unit="%"
              sentiment={
                (summary?.hit_rate_pct ?? 0) > 50 ? 'positive' : 'negative'
              }
            />
          </>
        )}
      </div>

      {/* Charts */}
      <div className="grid grid-cols-5 gap-4 mb-6">
        {/* P&L Chart — 60% */}
        <div className="col-span-3 bg-bg-surface border border-border-subtle rounded p-4">
          <div className="font-mono text-xs text-text-tertiary uppercase tracking-widest mb-4">
            Cumulative P&L vs SPY
          </div>
          {historyLoading ? (
            <LoadingSkeleton className="h-48" />
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={historyArr}>
                <defs>
                  <linearGradient id="pnlGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.15} />
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                <XAxis
                  dataKey="week"
                  tick={{ fill: '#52525b', fontSize: 10, fontFamily: 'IBM Plex Mono' }}
                  tickFormatter={v => format(new Date(v), 'MMM d')}
                  tickLine={false}
                  axisLine={{ stroke: '#27272a' }}
                  interval={7}
                />
                <YAxis
                  tick={{ fill: '#52525b', fontSize: 10, fontFamily: 'IBM Plex Mono' }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={v => `€${v}`}
                />
                <Tooltip content={<PnLTooltip />} />
                <Area
                  type="monotone"
                  dataKey="cumulative_pnl_eur"
                  name="portfolio"
                  stroke="#3b82f6"
                  strokeWidth={2}
                  fill="url(#pnlGradient)"
                  dot={false}
                />
                <Area
                  type="monotone"
                  dataKey="spy_return_pct"
                  name="SPY"
                  stroke="#52525b"
                  strokeWidth={1.5}
                  strokeDasharray="4 2"
                  fill="none"
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Weekly returns — 40% */}
        <div className="col-span-2 bg-bg-surface border border-border-subtle rounded p-4">
          <div className="font-mono text-xs text-text-tertiary uppercase tracking-widest mb-4">
            Weekly Returns (Last 12w)
          </div>
          {historyLoading ? (
            <LoadingSkeleton className="h-48" />
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={last12Weeks}>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
                <XAxis
                  dataKey="week"
                  tick={{ fill: '#52525b', fontSize: 10, fontFamily: 'IBM Plex Mono' }}
                  tickFormatter={v => format(new Date(v), 'M/d')}
                  tickLine={false}
                  axisLine={{ stroke: '#27272a' }}
                />
                <YAxis
                  tick={{ fill: '#52525b', fontSize: 10, fontFamily: 'IBM Plex Mono' }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={v => `${v}%`}
                />
                <Tooltip content={<WeeklyTooltip />} />
                <ReferenceLine y={0} stroke="#3f3f46" />
                <Bar dataKey="pnl_eur" name="weekly return" radius={[2, 2, 0, 0]}>
                  {last12Weeks.map((entry, idx) => (
                    <Cell key={idx} fill={entry.pnl_eur >= 0 ? '#22c55e' : '#ef4444'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Positions table */}
      <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
        <div className="px-4 py-3 border-b border-border-subtle flex items-center justify-between">
          <span className="font-mono text-xs text-text-tertiary uppercase tracking-widest">
            Open Positions
          </span>
          <span className="font-mono text-xs text-text-secondary">
            {positionsArr.length} positions
          </span>
        </div>
        {positionsLoading ? (
          <div className="p-4">
            <LoadingSkeleton rows={5} />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border-subtle">
                  {['Ticker', 'Direction', 'Entry', 'Current', 'P&L (€)', 'P&L (%)', 'Size (€)', 'Days', 'Conviction'].map(h => (
                    <th
                      key={h}
                      className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sortedPositions.map(pos => (
                  <tr
                    key={pos.ticker}
                    onClick={() => navigate(`/ticker/${pos.ticker}`)}
                    className="border-b border-border-subtle/50 hover:bg-bg-elevated cursor-pointer transition-colors"
                  >
                    <td className="px-4 py-3">
                      <span className="font-mono text-sm font-semibold text-accent-blue">
                        {pos.ticker}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <DirectionBadge direction={pos.direction} size="sm" />
                    </td>
                    <td className="px-4 py-3">
                      <MonoNumber value={pos.entry_price} prefix="$" />
                    </td>
                    <td className="px-4 py-3">
                      <MonoNumber value={pos.current_price} prefix="$" />
                    </td>
                    <td className="px-4 py-3">
                      <MonoNumber value={pos.unrealized_pnl_eur} prefix="€" colorBySign />
                    </td>
                    <td className="px-4 py-3">
                      <MonoNumber value={pos.unrealized_pnl_pct} suffix="%" colorBySign />
                    </td>
                    <td className="px-4 py-3">
                      <MonoNumber value={pos.size_eur} prefix="€" />
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-mono text-sm text-text-secondary">{pos.days_held}d</span>
                    </td>
                    <td className="px-4 py-3">
                      <ConvictionDots conviction={pos.conviction} />
                    </td>
                  </tr>
                ))}
                {sortedPositions.length === 0 && (
                  <tr>
                    <td colSpan={9} className="px-4 py-8 text-center font-mono text-sm text-text-tertiary">
                      No open positions
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Shell>
  )
}
