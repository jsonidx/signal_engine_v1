/**
 * EarningsReactionModel — Historical post-earnings price reaction analysis.
 *
 * Shows for each of the last 8 quarters:
 *   - Day-before-close → day-of/after-close price reaction %
 *   - Whether the quarter was a beat or miss
 *   - 5-day post-earnings drift
 *
 * Summary stats: median absolute move, ±1SD, beat/miss split,
 * implied straddle move vs historical average, takeaway insight line.
 *
 * Data source: GET /api/ticker/{symbol}/earnings-reactions
 *              + existing earningsData (for next report + IV move)
 */

import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { clsx } from 'clsx'
import { useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, ComposedChart, Bar, ReferenceLine,
  XAxis, YAxis, Tooltip as RechartTooltip, Cell,
} from 'recharts'
import { api } from '../lib/api'
import type { EarningsReaction, EarningsReactionSummary, EarningsData } from '../lib/api'

// ─── Tooltip for the reaction chart ──────────────────────────────────────────

function ReactionTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const d: EarningsReaction = payload[0].payload
  return (
    <div className="bg-bg-elevated border border-border-subtle rounded px-3 py-2 font-mono text-xs space-y-1 shadow-lg">
      <div className="text-text-primary font-semibold">{d.date}</div>
      <div className={clsx('font-semibold', d.reaction_pct >= 0 ? 'text-accent-green' : 'text-accent-red')}>
        {d.reaction_pct >= 0 ? '+' : ''}{d.reaction_pct.toFixed(2)}% reaction
      </div>
      {d.beat != null && (
        <div className={d.beat ? 'text-accent-green' : 'text-accent-red'}>
          EPS {d.beat ? 'beat' : 'miss'}
          {d.eps_surprise_pct != null && (
            <span className="text-text-tertiary ml-1">
              ({d.eps_surprise_pct >= 0 ? '+' : ''}{d.eps_surprise_pct.toFixed(1)}%)
            </span>
          )}
        </div>
      )}
      {d.drift_5d_pct != null && (
        <div className="text-text-tertiary">
          5d drift: {d.drift_5d_pct >= 0 ? '+' : ''}{d.drift_5d_pct.toFixed(1)}%
        </div>
      )}
    </div>
  )
}

// ─── Summary stat cell ────────────────────────────────────────────────────────

function StatCell({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="text-center space-y-0.5">
      <div className={clsx('font-mono text-sm font-semibold', color ?? 'text-text-primary')}>
        {value}
      </div>
      <div className="font-mono text-[9px] text-text-tertiary uppercase tracking-wide whitespace-nowrap">
        {label}
      </div>
    </div>
  )
}

// ─── Distribution bar (bell-curve-like horizontal visual) ────────────────────

function MoveDistributionBar({
  median, sd, impliedMove,
}: {
  median: number
  sd: number
  impliedMove: number | null
}) {
  // Show a horizontal band from -2SD to +2SD with median and implied move marked
  const lo = -(median + sd * 1.5)
  const hi =  (median + sd * 1.5)
  const range = hi - lo || 1
  const pos = (v: number) => `${Math.max(0, Math.min(100, ((v - lo) / range) * 100)).toFixed(1)}%`

  return (
    <div className="space-y-1.5 mt-1">
      <div className="font-mono text-[9px] text-text-tertiary uppercase tracking-wide mb-1">
        Move distribution (last {8} quarters)
      </div>
      <div className="relative h-6">
        {/* Base track */}
        <div className="absolute inset-y-2 left-0 right-0 bg-bg-elevated rounded" />

        {/* ±1SD band */}
        <div
          className="absolute inset-y-1.5 bg-text-tertiary/15 rounded"
          style={{
            left: pos(-median),
            width: `${((2 * median) / range) * 100}%`,
          }}
        />

        {/* Median down move */}
        <div
          className="absolute top-0 bottom-0 w-0.5 bg-accent-red/70"
          style={{ left: pos(-median) }}
        />
        {/* Median up move */}
        <div
          className="absolute top-0 bottom-0 w-0.5 bg-accent-green/70"
          style={{ left: pos(median) }}
        />

        {/* Implied move marker */}
        {impliedMove != null && (
          <>
            <div
              className="absolute top-0 bottom-0 w-0.5 bg-accent-amber"
              style={{ left: pos(impliedMove) }}
            />
            <div
              className="absolute top-0 bottom-0 w-0.5 bg-accent-amber"
              style={{ left: pos(-impliedMove) }}
            />
          </>
        )}

        {/* Zero line */}
        <div
          className="absolute top-0 bottom-0 w-px bg-text-tertiary/40"
          style={{ left: pos(0) }}
        />
      </div>

      {/* Labels */}
      <div className="flex justify-between font-mono text-[8px] text-text-tertiary">
        <span className="text-accent-red">−{(median + sd).toFixed(1)}%</span>
        <span className="text-accent-red/70">−{median.toFixed(1)}%</span>
        <span className="text-text-tertiary/50">0</span>
        <span className="text-accent-green/70">+{median.toFixed(1)}%</span>
        <span className="text-accent-green">+{(median + sd).toFixed(1)}%</span>
      </div>

      {impliedMove != null && (
        <div className="font-mono text-[9px] text-accent-amber flex items-center gap-1">
          <span className="inline-block w-3 border-t border-accent-amber" />
          Implied straddle ±{impliedMove.toFixed(1)}%
        </div>
      )}
    </div>
  )
}

// ─── Takeaway line ────────────────────────────────────────────────────────────

function Takeaway({
  summary, impliedMove,
}: {
  summary: EarningsReactionSummary
  impliedMove: number | null
}) {
  const { median_abs_move_pct, median_beat_reaction_pct, beat_rate_pct } = summary

  const lines: { text: string; color: string }[] = []

  if (impliedMove != null && median_abs_move_pct != null) {
    const diff = impliedMove - median_abs_move_pct
    if (Math.abs(diff) > 0.5) {
      lines.push({
        text: diff > 0
          ? `Market pricing ±${impliedMove.toFixed(1)}% move — ${diff.toFixed(1)}% above historical median (${median_abs_move_pct.toFixed(1)}%). Options appear expensive.`
          : `Market pricing ±${impliedMove.toFixed(1)}% move — ${Math.abs(diff).toFixed(1)}% below historical median (${median_abs_move_pct.toFixed(1)}%). Historical moves have been larger.`,
        color: diff > 0 ? 'text-accent-amber' : 'text-accent-blue',
      })
    } else {
      lines.push({
        text: `Market pricing ±${impliedMove.toFixed(1)}% — in line with historical median of ±${median_abs_move_pct.toFixed(1)}%.`,
        color: 'text-text-secondary',
      })
    }
  }

  if (median_beat_reaction_pct != null && beat_rate_pct != null) {
    lines.push({
      text: `Beat rate ${beat_rate_pct.toFixed(0)}% · median beat reaction ${median_beat_reaction_pct >= 0 ? '+' : ''}${median_beat_reaction_pct.toFixed(1)}%`,
      color: median_beat_reaction_pct > 0 ? 'text-accent-green' : 'text-accent-red',
    })
  }

  if (lines.length === 0) return null

  return (
    <div className="space-y-1 pt-2 border-t border-border-subtle">
      {lines.map((l, i) => (
        <div key={i} className={clsx('font-mono text-[10px] leading-relaxed', l.color)}>
          ➡ {l.text}
        </div>
      ))}
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

interface EarningsReactionModelProps {
  symbol:       string
  earningsData: EarningsData | null | undefined  // already fetched on TickerPage
  impliedMove:  number | null | undefined        // signal.expected_move_pct
}

export function EarningsReactionModel({
  symbol, earningsData, impliedMove,
}: EarningsReactionModelProps) {
  const [expanded, setExpanded] = useState(true)

  const { data, isLoading } = useQuery({
    queryKey: ['earnings_reactions', symbol],
    queryFn:  () => api.tickerEarningsReactions(symbol),
    staleTime: 4 * 60 * 60 * 1000,
    enabled:  !!symbol,
  })

  // Don't render header-only if there's nothing useful to show
  if (!isLoading && !data?.data_available && !earningsData?.next_earnings) return null

  const summary = data?.summary
  const reactions = data?.data ?? []

  // Chart data: reactions in chronological order (oldest → newest)
  const chartData = reactions.map(r => ({
    ...r,
    // Short date label for x-axis
    label: r.date.slice(2, 7),  // "26-01" etc.
  }))

  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-2">
      {/* Header */}
      <button
        onClick={() => setExpanded(v => !v)}
        className="group w-full flex items-center justify-between"
      >
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
            Earnings Reaction Model
          </span>
          {earningsData?.next_earnings && (
            <span className="font-mono text-[9px] text-accent-amber border border-accent-amber/30 rounded px-1.5 py-0.5">
              next {earningsData.next_earnings}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* Teaser: show median move even when collapsed */}
          {!expanded && summary?.median_abs_move_pct != null && (
            <span className="font-mono text-xs text-text-secondary">
              ±{summary.median_abs_move_pct.toFixed(1)}% hist. median
            </span>
          )}
          <ChevronRight
            size={12}
            className={clsx('text-text-tertiary transition-transform', expanded && 'rotate-90')}
          />
        </div>
      </button>

      {expanded && (
        isLoading ? (
          <div className="font-mono text-xs text-text-tertiary py-3 text-center animate-pulse">
            Computing earnings reactions…
          </div>
        ) : (
          <div className="space-y-3">

            {/* Next earnings banner (from existing earningsData) */}
            {earningsData?.next_earnings && (
              <div className="bg-accent-amber/10 border border-accent-amber/30 rounded px-3 py-2 space-y-1">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-[9px] uppercase text-accent-amber tracking-wide">
                    Next Report
                  </span>
                  <span className="font-mono text-sm font-semibold text-accent-amber">
                    {earningsData.next_earnings}
                  </span>
                  {earningsData.next_earnings_quarter && (
                    <span className="font-mono text-xs text-accent-amber/70 border border-accent-amber/30 rounded px-1 py-0.5">
                      {earningsData.next_earnings_quarter}
                    </span>
                  )}
                </div>
                <div className="flex gap-4 flex-wrap">
                  {earningsData.next_eps?.avg != null && (
                    <span className="font-mono text-xs text-text-secondary">
                      EPS est <span className="text-text-primary font-semibold">${earningsData.next_eps.avg.toFixed(2)}</span>
                      {earningsData.next_eps.low != null && earningsData.next_eps.high != null && (
                        <span className="text-text-tertiary text-[10px] ml-1">
                          (${earningsData.next_eps.low.toFixed(2)}–${earningsData.next_eps.high.toFixed(2)})
                        </span>
                      )}
                    </span>
                  )}
                  {impliedMove != null && (
                    <span className="font-mono text-xs text-text-secondary">
                      Straddle implies <span className="text-accent-amber font-semibold">±{impliedMove.toFixed(1)}%</span>
                    </span>
                  )}
                </div>
              </div>
            )}

            {/* Summary stats row */}
            {summary && (
              <div className="grid grid-cols-5 gap-2 py-2 border-y border-border-subtle">
                <StatCell
                  label="Median ±move"
                  value={summary.median_abs_move_pct != null
                    ? `±${summary.median_abs_move_pct.toFixed(1)}%` : '—'}
                />
                <StatCell
                  label="+1SD"
                  value={summary.plus_1sd_pct != null
                    ? `±${summary.plus_1sd_pct.toFixed(1)}%` : '—'}
                  color="text-accent-amber"
                />
                <StatCell
                  label="Beat rate"
                  value={summary.beat_rate_pct != null
                    ? `${summary.beat_rate_pct.toFixed(0)}%` : '—'}
                  color={
                    summary.beat_rate_pct == null ? undefined
                    : summary.beat_rate_pct >= 60 ? 'text-accent-green'
                    : summary.beat_rate_pct >= 40 ? 'text-accent-amber'
                    : 'text-accent-red'
                  }
                />
                <StatCell
                  label="Beat react"
                  value={summary.median_beat_reaction_pct != null
                    ? `${summary.median_beat_reaction_pct >= 0 ? '+' : ''}${summary.median_beat_reaction_pct.toFixed(1)}%`
                    : '—'}
                  color={
                    summary.median_beat_reaction_pct == null ? undefined
                    : summary.median_beat_reaction_pct > 0 ? 'text-accent-green' : 'text-accent-red'
                  }
                />
                <StatCell
                  label="Miss react"
                  value={summary.median_miss_reaction_pct != null
                    ? `${summary.median_miss_reaction_pct >= 0 ? '+' : ''}${summary.median_miss_reaction_pct.toFixed(1)}%`
                    : '—'}
                  color={
                    summary.median_miss_reaction_pct == null ? undefined
                    : summary.median_miss_reaction_pct > 0 ? 'text-accent-green' : 'text-accent-red'
                  }
                />
              </div>
            )}

            {/* Distribution bar */}
            {summary?.median_abs_move_pct != null && summary.std_move_pct != null && (
              <MoveDistributionBar
                median={summary.median_abs_move_pct}
                sd={summary.std_move_pct}
                impliedMove={impliedMove ?? null}
              />
            )}

            {/* Reaction bar chart — last 8 quarters */}
            {chartData.length > 0 && (
              <div>
                <div className="font-mono text-[9px] text-text-tertiary mb-1 flex gap-3">
                  <span className="font-semibold">Post-earnings price reaction</span>
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-2 h-2 rounded-sm bg-accent-green" />Beat
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-2 h-2 rounded-sm bg-accent-red" />Miss
                  </span>
                  <span className="flex items-center gap-1 text-text-tertiary/70">
                    <span className="inline-block w-2 h-2 rounded-sm bg-text-tertiary/40" />Unknown
                  </span>
                </div>
                <ResponsiveContainer width="100%" height={100}>
                  <ComposedChart
                    data={chartData}
                    margin={{ top: 4, right: 4, bottom: 0, left: -20 }}
                  >
                    <XAxis
                      dataKey="label"
                      tick={{ fontFamily: 'monospace', fontSize: 8, fill: '#52525b' }}
                      axisLine={false} tickLine={false}
                    />
                    <YAxis
                      tick={{ fontFamily: 'monospace', fontSize: 8, fill: '#52525b' }}
                      axisLine={false} tickLine={false}
                      tickFormatter={v => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`}
                    />
                    <RechartTooltip content={<ReactionTooltip />} cursor={{ fill: '#27272a' }} />
                    <ReferenceLine y={0} stroke="#3f3f46" strokeWidth={1} />
                    {/* Implied move reference band (if available) */}
                    {impliedMove != null && (
                      <>
                        <ReferenceLine
                          y={impliedMove}
                          stroke="#f59e0b"
                          strokeDasharray="3 3"
                          strokeWidth={0.8}
                          opacity={0.6}
                        />
                        <ReferenceLine
                          y={-impliedMove}
                          stroke="#f59e0b"
                          strokeDasharray="3 3"
                          strokeWidth={0.8}
                          opacity={0.6}
                        />
                      </>
                    )}
                    <Bar dataKey="reaction_pct" radius={[2, 2, 0, 0]} maxBarSize={28}>
                      {chartData.map((entry, i) => (
                        <Cell
                          key={i}
                          fill={
                            entry.beat === true  ? '#22c55e'
                            : entry.beat === false ? '#ef4444'
                            : '#52525b'
                          }
                          fillOpacity={0.8}
                        />
                      ))}
                    </Bar>
                  </ComposedChart>
                </ResponsiveContainer>
                <div className="font-mono text-[8px] text-text-tertiary/60 mt-0.5">
                  Day-before close → day-of/after close · amber dashed = current implied straddle
                </div>
              </div>
            )}

            {/* 5-day drift table */}
            {reactions.some(r => r.drift_5d_pct != null) && (
              <div>
                <div className="font-mono text-[9px] text-text-tertiary mb-1 uppercase tracking-wide">
                  5-day post-earnings drift
                </div>
                <div className="flex gap-3 flex-wrap">
                  {reactions.slice(-6).map(r => {
                    if (r.drift_5d_pct == null) return null
                    return (
                      <div key={r.date} className="text-center space-y-0.5">
                        <div className={clsx(
                          'font-mono text-xs font-semibold',
                          r.drift_5d_pct > 0 ? 'text-accent-green' : 'text-accent-red'
                        )}>
                          {r.drift_5d_pct >= 0 ? '+' : ''}{r.drift_5d_pct.toFixed(1)}%
                        </div>
                        <div className="font-mono text-[8px] text-text-tertiary">{r.date.slice(2, 7)}</div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Takeaway insight line */}
            {summary && (
              <Takeaway summary={summary} impliedMove={impliedMove ?? null} />
            )}

            {!data?.data_available && (
              <div className="font-mono text-[10px] text-text-tertiary/60">
                No reaction history available — ticker may not report earnings or yfinance data is unavailable.
              </div>
            )}
          </div>
        )
      )}
    </div>
  )
}
