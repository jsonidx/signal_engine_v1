/**
 * Top20RankingTable
 *
 * Displays the daily Top-20 stock ranking from /api/rankings/latest.
 * Click any row to open a side panel with a 30/60/90-day rank-history chart
 * powered by Recharts.
 *
 * Usage:
 *   import { Top20RankingTable } from '../components/Top20RankingTable'
 *   <Top20RankingTable />
 */

import { useState, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts'
import { X, RefreshCw, TrendingUp, TrendingDown, Minus, Download } from 'lucide-react'
import { clsx } from 'clsx'
import { api, type Top20RankingRow } from '../lib/api'
import { LoadingSkeleton } from './ui/LoadingSkeleton'

// ─── Formatting helpers ───────────────────────────────────────────────────────

function fmtAdv(v: number | null): string {
  if (v == null) return '—'
  if (v >= 1_000_000_000) return `$${(v / 1_000_000_000).toFixed(1)}B`
  if (v >= 1_000_000)     return `$${(v / 1_000_000).toFixed(1)}M`
  return `$${(v / 1_000).toFixed(0)}K`
}

function fmtPct(v: number | null): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function fmtScore(v: number | null): string {
  if (v == null) return '—'
  return v.toFixed(2)
}

function fmtPrice(v: number | null): string {
  if (v == null) return '—'
  return `$${v.toFixed(2)}`
}

function fmtProb(v: number | null): string {
  if (v == null || v === 0) return '—'
  return `${(v * 100).toFixed(0)}%`
}

function DirectionBadge({ direction }: { direction: string }) {
  if (direction === 'BULL') return (
    <span className="inline-flex items-center font-mono text-[10px] px-1.5 py-0.5 rounded border bg-accent-green/15 text-accent-green border-accent-green/30">
      ▲ BULL
    </span>
  )
  if (direction === 'BEAR') return (
    <span className="inline-flex items-center font-mono text-[10px] px-1.5 py-0.5 rounded border bg-accent-red/15 text-accent-red border-accent-red/30">
      ▼ BEAR
    </span>
  )
  return <span className="font-mono text-[10px] text-text-tertiary">—</span>
}

function ProbBar({ value }: { value: number | null }) {
  if (!value) return <span className="font-mono text-xs text-text-tertiary">—</span>
  const pct = Math.round(value * 100)
  const color = pct >= 60 ? '#22c55e' : pct >= 40 ? '#f59e0b' : '#ef4444'
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-12 h-1.5 bg-bg-elevated rounded-full overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="font-mono text-xs text-text-secondary">{pct}%</span>
    </div>
  )
}

function fmtDate(iso: string): string {
  // "2026-04-03" → "Apr 3"
  const d = new Date(iso + 'T00:00:00')
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function exportCSV(rows: Top20RankingRow[], asOf: string | null) {
  if (!rows.length) return
  const keys: (keyof Top20RankingRow)[] = [
    'rank', 'ticker', 'priority_score', 'weight', 'raw_weight', 'cap_hit',
    'sector', 'hist_vol_60d', 'adv_20d', 'rank_change', 'rank_yesterday',
  ]
  const lines = [
    keys.join(','),
    ...rows.map(r => keys.map(k => JSON.stringify(r[k] ?? '')).join(',')),
  ]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `top20_${asOf ?? 'latest'}.csv`
  a.click()
}

// ─── Rank-change pill ─────────────────────────────────────────────────────────

function RankChangePill({ value }: { value: string }) {
  if (value === 'NEW') {
    return (
      <span className="inline-flex items-center gap-0.5 font-mono text-[10px] px-1.5 py-0.5 rounded border bg-accent-blue/15 text-accent-blue border-accent-blue/30">
        NEW
      </span>
    )
  }
  if (value === '—' || !value) {
    return <Minus size={12} className="text-text-tertiary" />
  }
  const delta = parseInt(value, 10)
  if (isNaN(delta)) return <span className="font-mono text-xs text-text-tertiary">{value}</span>

  if (delta > 0) {
    return (
      <span className="inline-flex items-center gap-0.5 font-mono text-[10px] px-1.5 py-0.5 rounded border bg-accent-green/15 text-accent-green border-accent-green/30">
        <TrendingUp size={9} />
        {value}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-0.5 font-mono text-[10px] px-1.5 py-0.5 rounded border bg-accent-red/15 text-accent-red border-accent-red/30">
      <TrendingDown size={9} />
      {value}
    </span>
  )
}

// ─── Weight cell (tooltip for raw_weight, CAP badge) ─────────────────────────

function WeightCell({ row }: { row: Top20RankingRow }) {
  const [hovered, setHovered] = useState(false)
  const pct = row.weight != null ? `${(row.weight * 100).toFixed(2)}%` : '—'

  return (
    <div
      className="relative flex items-center gap-1"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <span className="font-mono text-xs text-text-primary">{pct}</span>
      {row.cap_hit && (
        <span className="font-mono text-[9px] px-1 py-0.5 rounded border bg-accent-amber/15 text-accent-amber border-accent-amber/30">
          CAP
        </span>
      )}
      {/* Tooltip showing raw weight */}
      {hovered && row.raw_weight != null && (
        <div className="absolute bottom-full left-0 mb-1 z-20 whitespace-nowrap rounded bg-bg-elevated border border-border-subtle px-2 py-1 text-[10px] text-text-secondary shadow-lg">
          raw: {(row.raw_weight * 100).toFixed(2)}%
        </div>
      )}
    </div>
  )
}

// ─── Rank-history chart (shown in the side panel) ─────────────────────────────

function RankHistoryChart({
  ticker,
  days,
}: {
  ticker: string
  days: number
}) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['rankings', 'history', ticker, days],
    queryFn:  () => api.rankingsHistory(ticker, days),
    staleTime: 5 * 60 * 1000,
  })

  if (isLoading) return <LoadingSkeleton rows={6} className="mt-4" />
  if (isError || !data?.data_available) {
    return (
      <p className="mt-4 text-xs text-text-tertiary text-center">
        No rank history found for {ticker}.
      </p>
    )
  }

  // Sort chronologically for the chart (data comes DESC from API)
  const chartData = [...data.data]
    .sort((a, b) => a.run_date.localeCompare(b.run_date))
    .map(r => ({ date: r.run_date, rank: r.rank }))

  if (!chartData.length) {
    return (
      <p className="mt-4 text-xs text-text-tertiary text-center">
        No rank history found for {ticker}.
      </p>
    )
  }

  return (
    <div className="mt-4">
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
          <XAxis
            dataKey="date"
            tickFormatter={fmtDate}
            tick={{ fontSize: 10, fill: '#52525b', fontFamily: 'IBM Plex Mono' }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            reversed                          // rank 1 at the top
            domain={[1, 20]}
            ticks={[1, 5, 10, 15, 20]}
            tick={{ fontSize: 10, fill: '#52525b', fontFamily: 'IBM Plex Mono' }}
            tickLine={false}
            axisLine={false}
            width={22}
          />
          <Tooltip
            contentStyle={{
              background: '#18181b',
              border: '1px solid #27272a',
              borderRadius: 6,
            }}
            labelStyle={{ color: '#a1a1aa', fontSize: 11, fontFamily: 'IBM Plex Mono' }}
            itemStyle={{ color: '#fafafa', fontSize: 11, fontFamily: 'IBM Plex Mono' }}
            labelFormatter={fmtDate}
            formatter={(value: string | number | (string | number)[]) => [`#${value}`, 'Rank']}
          />
          {/* Highlight top-5 band */}
          <ReferenceLine y={5} stroke="#22c55e" strokeDasharray="3 3" strokeOpacity={0.4} />
          <Line
            type="monotone"
            dataKey="rank"
            stroke="#3b82f6"
            strokeWidth={2}
            dot={{ r: 3, fill: '#3b82f6', strokeWidth: 0 }}
            activeDot={{ r: 5 }}
          />
        </LineChart>
      </ResponsiveContainer>
      <p className="mt-1 text-[10px] text-text-tertiary text-center">
        Rank over the last {days} days · lower = better · dashed line = top 5
      </p>
    </div>
  )
}

// ─── Side panel (ticker detail) ───────────────────────────────────────────────

type HistoryWindow = 30 | 60 | 90

function TickerPanel({
  row,
  onClose,
}: {
  row: Top20RankingRow
  onClose: () => void
}) {
  const [chartDays, setChartDays] = useState<HistoryWindow>(30)

  return (
    // Backdrop
    <div
      className="fixed inset-0 z-40 flex justify-end"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      {/* Panel */}
      <div className="relative z-50 w-full max-w-md bg-bg-surface border-l border-border-subtle shadow-2xl flex flex-col overflow-y-auto">
        {/* Header */}
        <div className="flex items-start justify-between p-5 border-b border-border-subtle sticky top-0 bg-bg-surface z-10">
          <div>
            <h2 className="font-mono text-lg font-semibold text-text-primary">{row.ticker}</h2>
            <p className="text-xs text-text-tertiary mt-0.5">{row.sector} · Rank #{row.rank}</p>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-bg-elevated text-text-tertiary hover:text-text-primary transition-colors"
            aria-label="Close panel"
          >
            <X size={16} />
          </button>
        </div>

        {/* Swing trade targets */}
        {row.direction !== 'NEUTRAL' && row.t1_price != null && (
          <div className="p-5 border-b border-border-subtle">
            <p className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-3">
              Swing Targets · <DirectionBadge direction={row.direction} />
            </p>
            <div className="grid grid-cols-3 gap-2 mb-3">
              <div className="bg-accent-green/10 border border-accent-green/20 rounded p-2.5">
                <p className="font-mono text-[9px] uppercase text-accent-green/70">T1</p>
                <p className="font-mono text-sm font-semibold text-accent-green mt-0.5">{fmtPrice(row.t1_price)}</p>
                <p className="font-mono text-[10px] text-accent-green/60 mt-0.5">{fmtProb(row.prob_t1)}</p>
              </div>
              <div className="bg-accent-blue/10 border border-accent-blue/20 rounded p-2.5">
                <p className="font-mono text-[9px] uppercase text-accent-blue/70">T2</p>
                <p className="font-mono text-sm font-semibold text-accent-blue mt-0.5">{fmtPrice(row.t2_price)}</p>
                <p className="font-mono text-[10px] text-accent-blue/60 mt-0.5">{fmtProb(row.prob_t2)}</p>
              </div>
              <div className="bg-accent-red/10 border border-accent-red/20 rounded p-2.5">
                <p className="font-mono text-[9px] uppercase text-accent-red/70">Stop</p>
                <p className="font-mono text-sm font-semibold text-accent-red mt-0.5">{fmtPrice(row.stop_price)}</p>
                <p className="font-mono text-[10px] text-accent-red/60 mt-0.5">
                  {row.hold_days != null ? `~${row.hold_days}d hold` : ''}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Metric summary */}
        <div className="grid grid-cols-2 gap-3 p-5 border-b border-border-subtle">
          {[
            { label: 'Priority Score',  value: fmtScore(row.priority_score) },
            { label: 'Agreement',       value: row.agreement_score != null ? `${(row.agreement_score * 100).toFixed(0)}%` : '—' },
            { label: 'Weight',          value: row.weight != null ? `${(row.weight * 100).toFixed(2)}%` : '—' },
            { label: 'Hist Vol 60d',    value: fmtPct(row.hist_vol_60d)     },
            { label: 'ADV 20d',         value: fmtAdv(row.adv_20d)          },
            { label: 'Sector',          value: row.sector                   },
          ].map(({ label, value }) => (
            <div key={label} className="bg-bg-elevated rounded p-3">
              <p className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">{label}</p>
              <p className="font-mono text-sm text-text-primary mt-1">{value}</p>
            </div>
          ))}
        </div>

        {/* Rank history chart */}
        <div className="p-5">
          <div className="flex items-center justify-between mb-1">
            <h3 className="font-mono text-xs uppercase tracking-widest text-text-tertiary">
              Rank History
            </h3>
            {/* Window toggle */}
            <div className="flex gap-1">
              {([30, 60, 90] as HistoryWindow[]).map(w => (
                <button
                  key={w}
                  onClick={() => setChartDays(w)}
                  className={clsx(
                    'font-mono text-[10px] px-2 py-0.5 rounded border transition-colors',
                    chartDays === w
                      ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/40'
                      : 'text-text-tertiary border-border-subtle hover:text-text-secondary'
                  )}
                >
                  {w}d
                </button>
              ))}
            </div>
          </div>
          <RankHistoryChart ticker={row.ticker} days={chartDays} />
        </div>
      </div>
    </div>
  )
}

// ─── Table skeleton ───────────────────────────────────────────────────────────

function TableSkeleton() {
  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border-subtle">
        <div className="shimmer h-3 w-48 rounded" />
      </div>
      {Array.from({ length: 10 }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 px-4 py-2.5 border-b border-border-subtle/50">
          <div className="shimmer h-3 w-6 rounded" />
          <div className="shimmer h-3 w-16 rounded" />
          <div className="shimmer h-3 w-32 rounded flex-1" />
          <div className="shimmer h-3 w-12 rounded" />
          <div className="shimmer h-3 w-12 rounded" />
        </div>
      ))}
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export function Top20RankingTable() {
  const [selectedRow, setSelectedRow] = useState<Top20RankingRow | null>(null)

  const {
    data,
    isLoading,
    isError,
    refetch,
    isFetching,
    dataUpdatedAt,
  } = useQuery({
    queryKey:       ['rankings', 'latest'],
    queryFn:        api.rankingsLatest,
    staleTime:      5  * 60 * 1000,   // 5 min — data updates once per day
    refetchInterval: 15 * 60 * 1000,  // background refresh every 15 min
    retry: 2,
  })

  const handleRowClick = useCallback((row: Top20RankingRow) => {
    setSelectedRow(prev => prev?.ticker === row.ticker ? null : row)
  }, [])

  const lastUpdated = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
    : null

  // ── Loading state ─────────────────────────────────────────────────────────
  if (isLoading) return <TableSkeleton />

  // ── Error state ───────────────────────────────────────────────────────────
  if (isError || !data?.data_available) {
    return (
      <div className="bg-bg-surface border border-border-subtle rounded p-8 text-center">
        <p className="text-text-secondary text-sm mb-3">
          {isError ? 'Failed to load daily rankings.' : 'No ranking data available yet.'}
        </p>
        <button
          onClick={() => refetch()}
          className="inline-flex items-center gap-1.5 font-mono text-xs px-3 py-1.5 rounded border border-border-subtle text-text-secondary hover:text-text-primary hover:border-text-tertiary transition-colors"
        >
          <RefreshCw size={12} />
          Retry
        </button>
      </div>
    )
  }

  const rows = data.data ?? []

  return (
    <>
      {/* ── Table card ─────────────────────────────────────────────────── */}
      <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">

        {/* Card header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
          <div>
            <h2 className="font-mono text-sm font-semibold text-text-primary">
              Daily Top-20 Ranking
            </h2>
            {data.as_of && (
              <p className="font-mono text-[10px] text-text-tertiary mt-0.5">
                as of {data.as_of}
              </p>
            )}
          </div>
          <div className="flex items-center gap-3">
            {lastUpdated && (
              <span className="font-mono text-[10px] text-text-tertiary hidden sm:block">
                updated {lastUpdated}
              </span>
            )}
            <button
              onClick={() => exportCSV(rows, data.as_of)}
              className="p-1.5 rounded border border-border-subtle text-text-tertiary hover:text-text-primary hover:border-text-tertiary transition-colors"
              aria-label="Export CSV"
              title="Export CSV"
            >
              <Download size={13} />
            </button>
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="p-1.5 rounded border border-border-subtle text-text-tertiary hover:text-text-primary hover:border-text-tertiary transition-colors disabled:opacity-40"
              aria-label="Refresh ranking"
            >
              <RefreshCw size={13} className={isFetching ? 'animate-spin' : ''} />
            </button>
          </div>
        </div>

        {/* Scrollable table */}
        <div className="overflow-x-auto">
          <table className="w-full" aria-label="Daily Top-20 ranking">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-elevated/40">
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary w-12">
                  Rank
                </th>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                  Ticker
                </th>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                  Dir
                </th>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                  P(T1)
                </th>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary hidden md:table-cell">
                  P(T2)
                </th>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary hidden md:table-cell">
                  T1
                </th>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary hidden md:table-cell">
                  T2
                </th>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary hidden lg:table-cell">
                  Stop
                </th>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary hidden lg:table-cell">
                  Hold
                </th>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary hidden lg:table-cell">
                  Sector
                </th>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                  Change
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                const isSelected = selectedRow?.ticker === row.ticker
                return (
                  <tr
                    key={row.ticker}
                    onClick={() => handleRowClick(row)}
                    className={clsx(
                      'border-b border-border-subtle/50 cursor-pointer transition-colors',
                      isSelected
                        ? 'bg-accent-blue/10'
                        : 'hover:bg-bg-elevated'
                    )}
                  >
                    {/* Rank number */}
                    <td className="px-4 py-2.5">
                      <span className={clsx(
                        'font-mono text-xs font-semibold',
                        row.rank <= 5 ? 'text-accent-green' : 'text-text-tertiary'
                      )}>
                        #{row.rank}
                      </span>
                    </td>

                    {/* Ticker */}
                    <td className="px-4 py-2.5">
                      <span className="font-mono text-xs font-semibold text-text-primary">
                        {row.ticker}
                      </span>
                    </td>

                    {/* Direction */}
                    <td className="px-4 py-2.5">
                      <DirectionBadge direction={row.direction} />
                    </td>

                    {/* P(T1) probability bar */}
                    <td className="px-4 py-2.5">
                      <ProbBar value={row.prob_t1} />
                    </td>

                    {/* P(T2) */}
                    <td className="px-4 py-2.5 font-mono text-xs text-text-secondary hidden md:table-cell">
                      {fmtProb(row.prob_t2)}
                    </td>

                    {/* T1 price */}
                    <td className="px-4 py-2.5 font-mono text-xs text-accent-green hidden md:table-cell">
                      {fmtPrice(row.t1_price)}
                    </td>

                    {/* T2 price */}
                    <td className="px-4 py-2.5 font-mono text-xs text-accent-blue hidden md:table-cell">
                      {fmtPrice(row.t2_price)}
                    </td>

                    {/* Stop price */}
                    <td className="px-4 py-2.5 font-mono text-xs text-accent-red hidden lg:table-cell">
                      {fmtPrice(row.stop_price)}
                    </td>

                    {/* Hold days */}
                    <td className="px-4 py-2.5 font-mono text-xs text-text-secondary hidden lg:table-cell">
                      {row.hold_days != null ? `${row.hold_days}d` : '—'}
                    </td>

                    {/* Sector */}
                    <td className="px-4 py-2.5 hidden lg:table-cell">
                      <span className="block font-mono text-xs text-text-tertiary truncate max-w-[100px]">
                        {row.sector}
                      </span>
                    </td>

                    {/* Rank change pill */}
                    <td className="px-4 py-2.5">
                      <RankChangePill value={row.rank_change} />
                    </td>
                  </tr>
                )
              })}

              {rows.length === 0 && (
                <tr>
                  <td colSpan={11} className="px-4 py-8 text-center text-xs text-text-tertiary">
                    No ranking data for today yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Ticker detail side panel ───────────────────────────────────── */}
      {selectedRow && (
        <TickerPanel
          row={selectedRow}
          onClose={() => setSelectedRow(null)}
        />
      )}
    </>
  )
}
