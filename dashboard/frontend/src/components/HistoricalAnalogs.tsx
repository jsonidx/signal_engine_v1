/**
 * HistoricalAnalogs — shows resolved same-direction setups from thesis_outcomes.
 *
 * Summary row: N setups · Win T1 · Win T2 · Stopped · Avg Hold · Expectancy
 * Table: up to 12 rows — Date | Ticker | Conv | Agr | T1✓ | T2✓ | Stop | 30d | Outcome
 *
 * Data source: GET /api/ticker/{symbol}/analogs
 * Falls back gracefully if thesis_outcomes is empty.
 */

import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { clsx } from 'clsx'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { HistoricalAnalog, AnalogSummary } from '../lib/api'

// ─── Summary stat pill ────────────────────────────────────────────────────────

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex flex-col items-center gap-0.5 min-w-0">
      <span className={clsx('font-mono text-sm font-semibold', color ?? 'text-text-primary')}>
        {value}
      </span>
      <span className="font-mono text-[9px] text-text-tertiary uppercase tracking-wide whitespace-nowrap">
        {label}
      </span>
    </div>
  )
}

// ─── Summary bar ─────────────────────────────────────────────────────────────

function SummaryBar({ s }: { s: AnalogSummary }) {
  const evColor =
    s.expectancy_r == null ? 'text-text-tertiary'
    : s.expectancy_r >= 1  ? 'text-accent-green'
    : s.expectancy_r >= 0  ? 'text-accent-amber'
    : 'text-accent-red'

  const winColor =
    s.win_rate_t1_pct == null  ? 'text-text-tertiary'
    : s.win_rate_t1_pct >= 60  ? 'text-accent-green'
    : s.win_rate_t1_pct >= 40  ? 'text-accent-amber'
    : 'text-accent-red'

  return (
    <div className="flex items-start gap-5 py-2 px-1 border-b border-border-subtle mb-3">
      <Stat label="setups" value={String(s.total)} />
      <Stat
        label="win T1"
        value={s.win_rate_t1_pct != null ? `${s.win_rate_t1_pct}%` : '—'}
        color={winColor}
      />
      <Stat
        label="win T2"
        value={s.win_rate_t2_pct != null ? `${s.win_rate_t2_pct}%` : '—'}
        color="text-accent-green"
      />
      <Stat
        label="stopped"
        value={s.stop_rate_pct != null ? `${s.stop_rate_pct}%` : '—'}
        color="text-accent-red"
      />
      <Stat
        label="avg hold"
        value={s.avg_hold_days != null ? `${s.avg_hold_days}d` : '—'}
      />
      <Stat
        label="avg T1 R"
        value={s.avg_t1_r != null ? `${s.avg_t1_r}R` : '—'}
        color="text-accent-green"
      />
      {/* Expectancy — the key edge metric */}
      <div className="flex flex-col items-center gap-0.5 ml-auto border-l border-border-subtle pl-4">
        <span className={clsx('font-mono text-lg font-semibold', evColor)}>
          {s.expectancy_r != null
            ? `${s.expectancy_r >= 0 ? '+' : ''}${s.expectancy_r}R`
            : '—'}
        </span>
        <span className="font-mono text-[9px] text-text-tertiary uppercase tracking-wide">
          expectancy
        </span>
      </div>
    </div>
  )
}

// ─── Outcome badge ────────────────────────────────────────────────────────────

function OutcomeBadge({ outcome }: { outcome: string }) {
  const map: Record<string, string> = {
    HIT_TARGET1: 'bg-accent-green/20 text-accent-green',
    HIT_TARGET2: 'bg-accent-green/30 text-accent-green',
    HIT_STOP:    'bg-accent-red/20 text-accent-red',
    OPEN:        'bg-bg-elevated text-text-tertiary',
    EXPIRED:     'bg-bg-elevated text-text-tertiary',
  }
  const label: Record<string, string> = {
    HIT_TARGET1: 'T1 ✓',
    HIT_TARGET2: 'T2 ✓',
    HIT_STOP:    'SL ✗',
    OPEN:        'open',
    EXPIRED:     'exp',
  }
  return (
    <span className={clsx('font-mono text-[9px] px-1.5 py-0.5 rounded', map[outcome] ?? 'bg-bg-elevated text-text-tertiary')}>
      {label[outcome] ?? outcome}
    </span>
  )
}

// ─── Analog row ───────────────────────────────────────────────────────────────

function AnalogRow({ a }: { a: HistoricalAnalog }) {
  const retColor =
    a.return_30d == null ? 'text-text-tertiary'
    : a.return_30d > 0   ? 'text-accent-green'
    : a.return_30d < 0   ? 'text-accent-red'
    : 'text-text-secondary'

  return (
    <div className="grid gap-1 items-center py-1.5 border-b border-border-subtle/50 last:border-0"
      style={{ gridTemplateColumns: '70px 52px 28px 36px 22px 22px 22px 50px 52px' }}>
      {/* Date */}
      <span className="font-mono text-[9px] text-text-tertiary">{a.date?.slice(0, 10) ?? '—'}</span>
      {/* Ticker */}
      <span className="font-mono text-[10px] font-semibold text-text-secondary">{a.ticker}</span>
      {/* Conviction */}
      <span className="font-mono text-[9px] text-text-tertiary text-center">{a.conviction ?? '—'}/5</span>
      {/* Signal agreement */}
      <span className="font-mono text-[9px] text-text-tertiary text-center">
        {a.signal_agreement != null ? `${Math.round(a.signal_agreement * 100)}%` : '—'}
      </span>
      {/* T1 hit */}
      <span className={clsx('font-mono text-[10px] text-center', a.hit_t1 ? 'text-accent-green' : 'text-text-tertiary/40')}>
        {a.hit_t1 ? '✓' : '·'}
      </span>
      {/* T2 hit */}
      <span className={clsx('font-mono text-[10px] text-center', a.hit_t2 ? 'text-accent-green' : 'text-text-tertiary/40')}>
        {a.hit_t2 ? '✓' : '·'}
      </span>
      {/* Stop hit */}
      <span className={clsx('font-mono text-[10px] text-center', a.hit_stop ? 'text-accent-red' : 'text-text-tertiary/40')}>
        {a.hit_stop ? '✗' : '·'}
      </span>
      {/* 30d return */}
      <span className={clsx('font-mono text-[9px] text-right', retColor)}>
        {a.return_30d != null
          ? `${a.return_30d >= 0 ? '+' : ''}${a.return_30d.toFixed(1)}%`
          : '—'}
      </span>
      {/* Outcome badge */}
      <div className="flex justify-end">
        <OutcomeBadge outcome={a.outcome} />
      </div>
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export function HistoricalAnalogs({ symbol }: { symbol: string }) {
  const [expanded, setExpanded] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['analogs', symbol],
    queryFn: () => api.tickerAnalogs(symbol),
    staleTime: 15 * 60 * 1000,
    enabled: !!symbol,
  })

  // Don't render if no data (table not populated yet)
  if (!isLoading && (!data?.data_available || !data.data.length)) return null

  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-1">
      {/* Header — always visible */}
      <button
        onClick={() => setExpanded(v => !v)}
        className="group w-full flex items-center justify-between"
      >
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
            Historical Analogs
          </span>
          {data?.summary && (
            <span className="font-mono text-[9px] text-text-tertiary">
              ({data.summary.total} {data.summary.direction?.toLowerCase()} setups)
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* Expectancy teaser even when collapsed */}
          {!expanded && data?.summary?.expectancy_r != null && (
            <span className={clsx(
              'font-mono text-xs font-semibold',
              data.summary.expectancy_r >= 1  ? 'text-accent-green'
              : data.summary.expectancy_r >= 0 ? 'text-accent-amber'
              : 'text-accent-red'
            )}>
              {data.summary.expectancy_r >= 0 ? '+' : ''}{data.summary.expectancy_r}R EV
            </span>
          )}
          <ChevronRight
            size={12}
            className={clsx(
              'text-text-tertiary transition-transform',
              expanded && 'rotate-90'
            )}
          />
        </div>
      </button>

      {/* Expanded content */}
      {expanded && (
        isLoading ? (
          <div className="font-mono text-xs text-text-tertiary py-4 text-center animate-pulse">
            Loading analogs…
          </div>
        ) : data?.data_available ? (
          <div>
            <SummaryBar s={data.summary} />

            {/* Column headers */}
            <div className="grid gap-1 pb-1"
              style={{ gridTemplateColumns: '70px 52px 28px 36px 22px 22px 22px 50px 52px' }}>
              {['Date', 'Ticker', 'Conv', 'Agr%', 'T1', 'T2', 'SL', '30d%', 'Outcome'].map(h => (
                <span key={h} className="font-mono text-[9px] uppercase tracking-wide text-text-tertiary text-center first:text-left">
                  {h}
                </span>
              ))}
            </div>

            {/* Rows */}
            {data.data.map((a, i) => <AnalogRow key={i} a={a} />)}

            {/* Footer note */}
            <div className="font-mono text-[8px] text-text-tertiary/60 pt-2">
              Universe-wide {data.summary.direction} setups · resolved theses only ·
              powered by thesis_checker.py
            </div>
          </div>
        ) : (
          <div className="font-mono text-xs text-text-tertiary py-4 text-center">
            No resolved analogs yet — run thesis_checker.py to populate.
          </div>
        )
      )}
    </div>
  )
}
