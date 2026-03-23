import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  PieChart,
  Pie,
  Cell,
  LineChart,
  Line,
  XAxis,
  YAxis,
  ReferenceLine,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import { TrendingUp, TrendingDown, Minus, X } from 'lucide-react'
import { Shell } from '../components/layout/Shell'
import { EmptyState } from '../components/ui/EmptyState'
import { api, type DarkPoolCard } from '../lib/api'
import { clsx } from 'clsx'

// ─── Signal color helpers ─────────────────────────────────────────────────────

function signalColor(signal: string): string {
  if (signal === 'ACCUMULATION') return '#22c55e'
  if (signal === 'DISTRIBUTION') return '#ef4444'
  return '#a1a1aa'
}

// ─── Donut gauge via Recharts ─────────────────────────────────────────────────

function DonutGauge({ score, signal }: { score: number; signal: string }) {
  const color = signalColor(signal)
  const pct = Math.min(100, Math.max(0, score))
  return (
    <div className="flex flex-col items-center gap-1">
      <PieChart width={80} height={80}>
        <Pie
          data={[{ value: pct }, { value: 100 - pct }]}
          innerRadius={28}
          outerRadius={38}
          startAngle={90}
          endAngle={-270}
          dataKey="value"
          stroke="none"
        >
          <Cell fill={color} />
          <Cell fill="#27272a" />
        </Pie>
      </PieChart>
      <div className="font-mono text-[10px] font-semibold" style={{ color }}>
        {signal}
      </div>
    </div>
  )
}

// ─── Short ratio mini chart ────────────────────────────────────────────────────

function ShortRatioChart({ history }: { history: DarkPoolCard['history'] }) {
  if (!history || history.length === 0) {
    return (
      <div className="h-24 flex items-center justify-center font-mono text-xs text-text-tertiary">
        No history data
      </div>
    )
  }
  const mean = history.reduce((s, p) => s + p.short_ratio, 0) / history.length

  return (
    <ResponsiveContainer width="100%" height={96}>
      <LineChart data={history} margin={{ top: 4, right: 4, bottom: 4, left: 0 }}>
        <XAxis
          dataKey="date"
          tick={{ fill: '#52525b', fontSize: 8, fontFamily: 'IBM Plex Mono' }}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis hide domain={['auto', 'auto']} />
        <ReferenceLine y={mean} stroke="#a1a1aa" strokeDasharray="3 2" strokeWidth={0.8} />
        <Tooltip
          contentStyle={{
            background: '#18181b',
            border: '1px solid #3f3f46',
            borderRadius: 4,
            fontFamily: 'IBM Plex Mono',
            fontSize: 10,
          }}
          formatter={(v: unknown) => [`${(v as number).toFixed(1)}%`, 'Short Ratio']}
        />
        <Line
          type="monotone"
          dataKey="short_ratio"
          stroke="#3b82f6"
          strokeWidth={1.5}
          dot={(props) => {
            const isLast = props.index === (history.length - 1)
            if (!isLast) return <circle key={props.index} r={0} />
            return (
              <circle
                key={props.index}
                cx={props.cx}
                cy={props.cy}
                r={3}
                fill="#3b82f6"
                stroke="#0a0a0b"
                strokeWidth={1}
              />
            )
          }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

// ─── Individual card ──────────────────────────────────────────────────────────

function DarkPoolCardView({ card }: { card: DarkPoolCard }) {
  const [expanded, setExpanded] = useState(false)

  const TrendIcon =
    card.short_ratio_trend === 'up'
      ? TrendingUp
      : card.short_ratio_trend === 'down'
        ? TrendingDown
        : Minus
  const trendColor =
    card.short_ratio_trend === 'up'
      ? 'text-accent-red'
      : card.short_ratio_trend === 'down'
        ? 'text-accent-green'
        : 'text-text-tertiary'

  return (
    <div
      className={clsx(
        'bg-bg-surface border rounded overflow-hidden cursor-pointer transition-colors',
        expanded ? 'border-border-active' : 'border-border-subtle hover:border-border-active'
      )}
      onClick={() => setExpanded(v => !v)}
    >
      <div className="p-3 space-y-3">
        {/* Header */}
        <div>
          <div className="font-mono text-sm font-semibold text-accent-blue">{card.ticker}</div>
          {card.company && (
            <div className="font-mono text-[11px] text-text-tertiary truncate">{card.company}</div>
          )}
        </div>

        {/* Donut */}
        <div className="flex justify-center">
          <DonutGauge score={card.dark_pool_score} signal={card.signal} />
        </div>

        {/* Metrics row */}
        <div className="grid grid-cols-3 gap-1 text-center">
          <div>
            <div className="font-mono text-[9px] text-text-tertiary uppercase mb-0.5">Short</div>
            <div className="font-mono text-xs text-text-primary">{card.short_ratio?.toFixed(1) ?? '—'}%</div>
          </div>
          <div>
            <div className="font-mono text-[9px] text-text-tertiary uppercase mb-0.5">Trend</div>
            <div className="flex justify-center">
              <TrendIcon size={13} className={trendColor} />
            </div>
          </div>
          <div>
            <div className="font-mono text-[9px] text-text-tertiary uppercase mb-0.5">Intensity</div>
            <div className="font-mono text-xs text-text-primary">{card.dark_pool_intensity?.toFixed(1) ?? '—'}%</div>
          </div>
        </div>
      </div>

      {/* Expanded detail — short ratio chart */}
      {expanded && (
        <div
          className="border-t border-border-subtle p-3 bg-bg-elevated"
          onClick={e => e.stopPropagation()}
        >
          <div className="flex items-center justify-between mb-2">
            <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
              Short Ratio — 20d
            </div>
            <button
              onClick={() => setExpanded(false)}
              className="text-text-tertiary hover:text-text-secondary"
            >
              <X size={12} />
            </button>
          </div>
          <ShortRatioChart history={card.history} />
          <div className="font-mono text-[9px] text-text-tertiary mt-1">
            — = 20-day mean
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Page ──────────────────────────────────────────────────────────────────────

type SignalFilter = 'ALL' | 'ACCUMULATION' | 'DISTRIBUTION'

export function DarkPoolPage() {
  const [filter, setFilter] = useState<SignalFilter>('ALL')

  const { data: rawData, isLoading } = useQuery({
    queryKey: ['darkpool', 'top', filter],
    queryFn: () => api.darkpoolTop(filter === 'ALL' ? undefined : filter, 30),
    staleTime: 15 * 60 * 1000,
    retry: 1,
  })
  const data = Array.isArray(rawData) ? rawData : (rawData as any)?.data ?? []

  return (
    <Shell title="Dark Pool Flow">
      {/* Filter buttons */}
      <div className="flex items-center gap-2 mb-5">
        {([
          { val: 'ALL', label: 'All', activeClass: 'bg-bg-elevated border-border-active text-text-primary' },
          { val: 'ACCUMULATION', label: 'Accumulation', activeClass: 'bg-accent-green/15 border-accent-green text-accent-green' },
          { val: 'DISTRIBUTION', label: 'Distribution', activeClass: 'bg-accent-red/15 border-accent-red text-accent-red' },
        ] as const).map(({ val, label, activeClass }) => (
          <button
            key={val}
            onClick={() => setFilter(val)}
            className={clsx(
              'px-4 py-2 text-xs font-mono rounded border transition-colors',
              filter === val
                ? activeClass
                : 'bg-bg-surface border-border-subtle text-text-tertiary hover:text-text-secondary hover:border-border-active'
            )}
          >
            {label}
          </button>
        ))}
        <span className="ml-auto font-mono text-xs text-text-tertiary">
          {data?.length ?? 0} tickers
        </span>
      </div>

      {isLoading ? (
        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))' }}>
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="shimmer h-52 rounded" />
          ))}
        </div>
      ) : !data || data.length === 0 ? (
        <EmptyState
          message="No dark pool data"
          command="./run_master.sh"
        />
      ) : (
        <div
          className="grid gap-3"
          style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))' }}
        >
          {data.map(card => (
            <DarkPoolCardView key={card.ticker} card={card} />
          ))}
        </div>
      )}
    </Shell>
  )
}
