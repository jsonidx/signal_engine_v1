import { useState, useMemo, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ChevronRight, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import * as Accordion from '@radix-ui/react-accordion'
import { Shell } from '../components/layout/Shell'
import { DirectionBadge } from '../components/ui/DirectionBadge'
import { ConvictionDots } from '../components/ui/ConvictionDots'
import { MonoNumber } from '../components/ui/MonoNumber'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { RegimeBadge } from '../components/ui/RegimeBadge'
import { PriceLadder } from '../components/charts/PriceLadder'
import { useQuery } from '@tanstack/react-query'
import { useSignalsTicker } from '../hooks/useHeatmap'
import { useDarkPoolTicker } from '../hooks/useDarkPool'
import { useHeatmap } from '../hooks/useHeatmap'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip as RechartTooltip,
  ResponsiveContainer, Cell, ReferenceLine, Legend,
} from 'recharts'
import { api } from '../lib/api'
import type { MaxPainData, ExpectedMove, TickerDetail, SecFiling, CongressTrade, EarningsData, EarningsQuarter, EarningsAnnual, ActionZones, AnalyzeStatus } from '../lib/api'
import { clsx } from 'clsx'

// ─── Module score color encoding (same as heatmap) ────────────────────────────

function getModuleColor(score: number): string {
  if (score > 0.5) return '#22c55e'
  if (score > 0.1) return '#22c55e66'
  if (score >= -0.1) return '#27272a'
  if (score >= -0.5) return '#ef444466'
  return '#ef4444'
}

const MODULE_KEYS = [
  { key: 'signal_engine', label: 'SigEng' },
  { key: 'squeeze', label: 'Sqz' },
  { key: 'options', label: 'Opts' },
  { key: 'dark_pool', label: 'DkPl' },
  { key: 'fundamentals', label: 'Fund' },
  { key: 'social', label: 'Socl' },
  { key: 'polymarket', label: 'Poly' },
  { key: 'cross_asset', label: 'XAss' },
]

// ─── Ticker search input ───────────────────────────────────────────────────────

function TickerSearch({ current, tickers }: { current: string; tickers: string[] }) {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)

  const filtered = useMemo(() => {
    if (!query.trim()) return []
    const q = query.toUpperCase()
    return tickers.filter(t => t.startsWith(q)).slice(0, 8)
  }, [query, tickers])

  const go = (ticker: string) => {
    setQuery('')
    setOpen(false)
    navigate(`/ticker/${ticker}`)
  }

  return (
    <div className="relative w-48">
      <input
        value={query}
        onChange={e => { setQuery(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder={current}
        className="w-full px-3 py-1.5 bg-bg-elevated border border-border-subtle rounded font-mono text-sm text-text-primary placeholder-text-tertiary focus:outline-none focus:border-border-active"
      />
      {open && filtered.length > 0 && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-bg-surface border border-border-subtle rounded shadow-lg z-50">
          {filtered.map(t => (
            <button
              key={t}
              onMouseDown={() => go(t)}
              className={clsx(
                'w-full px-3 py-2 text-left font-mono text-sm transition-colors',
                t === current ? 'text-accent-blue' : 'text-text-secondary hover:text-text-primary hover:bg-bg-elevated'
              )}
            >
              {t}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Trade Setup Strip ────────────────────────────────────────────────────────

function TradeSetupStrip({ signal }: { signal: TickerDetail }) {
  const { entry_low, entry_high, target_1, target_2, stop_loss, current_price } = signal

  const entry =
    entry_low != null && entry_high != null
      ? (entry_low + entry_high) / 2
      : entry_low ?? entry_high ?? current_price

  if (entry == null) return null
  const hasAny = target_1 != null || target_2 != null || stop_loss != null
  if (!hasAny) return null

  const pct = (price: number) => ((price - entry!) / entry!) * 100
  const fmtPct = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`
  const fmtPrice = (v: number) => `$${v.toFixed(2)}`

  const t1Pct = target_1 != null ? pct(target_1) : null
  const t2Pct = target_2 != null ? pct(target_2) : null
  const stopPct = stop_loss != null ? pct(stop_loss) : null
  const rr =
    t1Pct != null && stopPct != null && Math.abs(stopPct) > 0
      ? Math.abs(t1Pct / stopPct)
      : null

  return (
    <div className="mt-3 pt-3 border-t border-border-subtle">
      <div className="font-mono text-[9px] uppercase tracking-widest text-text-tertiary mb-2">
        Trade Setup
      </div>
      <div className="grid grid-cols-4 gap-3">
        {/* Entry */}
        <div className="space-y-0.5">
          <div className="font-mono text-[9px] text-text-tertiary uppercase">Entry (med)</div>
          <div className="font-mono text-sm font-semibold text-text-primary">{fmtPrice(entry)}</div>
          {entry_low != null && entry_high != null && (
            <div className="font-mono text-[9px] text-text-tertiary">
              {fmtPrice(entry_low)} – {fmtPrice(entry_high)}
            </div>
          )}
        </div>
        {/* T1 */}
        <div className="space-y-0.5">
          <div className="font-mono text-[9px] text-text-tertiary uppercase">T1 Profit</div>
          {t1Pct != null ? (
            <>
              <div className={clsx('font-mono text-sm font-semibold', t1Pct >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                {fmtPct(t1Pct)}
              </div>
              <div className="font-mono text-[9px] text-text-tertiary">{fmtPrice(target_1!)}</div>
            </>
          ) : (
            <div className="font-mono text-sm text-text-tertiary">—</div>
          )}
        </div>
        {/* T2 */}
        <div className="space-y-0.5">
          <div className="font-mono text-[9px] text-text-tertiary uppercase">T2 Profit</div>
          {t2Pct != null ? (
            <>
              <div className={clsx('font-mono text-sm font-semibold', t2Pct >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                {fmtPct(t2Pct)}
              </div>
              <div className="font-mono text-[9px] text-text-tertiary">{fmtPrice(target_2!)}</div>
            </>
          ) : (
            <div className="font-mono text-sm text-text-tertiary">—</div>
          )}
        </div>
        {/* Stop / Risk */}
        <div className="space-y-0.5">
          <div className="font-mono text-[9px] text-text-tertiary uppercase">Risk (stop)</div>
          {stopPct != null ? (
            <>
              <div className="font-mono text-sm font-semibold text-accent-red">{fmtPct(stopPct)}</div>
              <div className="font-mono text-[9px] text-text-tertiary">{fmtPrice(stop_loss!)}</div>
            </>
          ) : (
            <div className="font-mono text-sm text-text-tertiary">—</div>
          )}
        </div>
      </div>
      {rr != null && (
        <div className="font-mono text-[9px] text-text-tertiary mt-2">
          R:R {rr.toFixed(1)}:1
          {rr >= 2 && <span className="text-accent-green ml-1">✓ favorable</span>}
          {rr < 1 && <span className="text-accent-amber ml-1">⚠ poor setup</span>}
        </div>
      )}
    </div>
  )
}

// ─── Bull/Bear probability bar ─────────────────────────────────────────────────

function ProbBar({
  bull = 0,
  bear = 0,
  neutral = 0,
}: {
  bull?: number
  bear?: number
  neutral?: number
}) {
  const total = bull + bear + neutral || 1
  const bp = (bull / total) * 100
  const rp = (bear / total) * 100
  const np = 100 - bp - rp

  return (
    <div className="space-y-1.5">
      <div className="font-mono text-xs text-text-tertiary">
        Bull {Math.round(bp)}% / Bear {Math.round(rp)}% / Neutral {Math.round(np)}%
      </div>
      <div className="h-3 rounded overflow-hidden flex">
        <div style={{ width: `${bp}%` }} className="bg-accent-green" />
        <div style={{ width: `${np}%` }} className="bg-text-tertiary/30" />
        <div style={{ width: `${rp}%` }} className="bg-accent-red" />
      </div>
    </div>
  )
}

// ─── Module mini-heatmap ───────────────────────────────────────────────────────

function ModuleMiniHeatmap({ modules }: { modules: Record<string, number> }) {
  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-3">
      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-3">
        Module Scores
      </div>
      <div className="flex gap-1.5">
        {MODULE_KEYS.map(({ key, label }) => {
          const score = modules[key] ?? null
          const color = score !== null ? getModuleColor(score) : '#27272a'
          return (
            <div key={key} className="flex flex-col items-center gap-1">
              <div
                style={{
                  width: 40,
                  height: 40,
                  background: color,
                  borderRadius: 4,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <span className="font-mono text-[9px] text-white/90 font-bold">
                  {score !== null ? (score > 0 ? '+' : '') + score.toFixed(2) : '—'}
                </span>
              </div>
              <span className="font-mono text-[9px] text-text-tertiary">{label}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ─── Override flags card ───────────────────────────────────────────────────────

function OverrideCard({ flags }: { flags: string[] }) {
  if (flags.length === 0) return null
  return (
    <div className="bg-accent-amber/10 border border-accent-amber/30 rounded p-3 space-y-2">
      <div className="font-mono text-[10px] uppercase tracking-widest text-accent-amber">
        Override Flags Applied
      </div>
      {flags.map(f => (
        <div key={f} className="font-mono text-xs text-accent-amber/90">
          ⚠ {f.replace(/_/g, ' ')}
        </div>
      ))}
    </div>
  )
}

// ─── Dark pool gauge ───────────────────────────────────────────────────────────

function DarkPoolGauge({ score, trend, intensity }: { score: number; trend?: string; intensity?: number }) {
  const color = score >= 65 ? '#22c55e' : score >= 35 ? '#a1a1aa' : '#ef4444'
  const label = score >= 65 ? 'ACCUMULATION' : score >= 35 ? 'NEUTRAL' : 'DISTRIBUTION'
  const pct = Math.min(100, Math.max(0, score))

  const TrendIcon = trend === 'up' ? TrendingUp : trend === 'down' ? TrendingDown : Minus
  const trendColor = trend === 'up' ? 'text-accent-green' : trend === 'down' ? 'text-accent-red' : 'text-text-tertiary'

  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-3">
      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
        Dark Pool Flow
      </div>
      <div className="space-y-1.5">
        <div className="flex justify-between font-mono text-xs">
          <span className="text-text-tertiary">Distribution</span>
          <span style={{ color }} className="font-semibold">{label}</span>
          <span className="text-text-tertiary">Accumulation</span>
        </div>
        <div className="h-3 bg-bg-elevated rounded overflow-hidden">
          <div style={{ width: `${pct}%`, background: color }} className="h-full rounded transition-all" />
        </div>
        <div className="font-mono text-[10px] text-text-tertiary text-center">{pct.toFixed(0)} / 100</div>
      </div>
      <div className="flex items-center justify-between font-mono text-xs text-text-secondary">
        <div className="flex items-center gap-1">
          <TrendIcon size={12} className={trendColor} />
          <span>{trend ?? '—'} trend</span>
        </div>
        {intensity != null && (
          <span>{intensity.toFixed(1)}% intensity</span>
        )}
      </div>
    </div>
  )
}

// ─── Social card ───────────────────────────────────────────────────────────────

function SocialCard({
  trendScore,
  interestLevel,
  bullBearRatio,
  messageCount,
}: {
  trendScore?: number
  interestLevel?: number
  bullBearRatio?: number
  messageCount?: number
}) {
  if (trendScore == null && bullBearRatio == null) return null
  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-2">
      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
        Social Signals
      </div>
      <div className="grid grid-cols-2 gap-3">
        {trendScore !== undefined && (
          <div className="space-y-1.5">
            <div className="font-mono text-[10px] text-text-tertiary uppercase">Google Trends</div>
            <div className="font-mono text-lg font-semibold text-text-primary">{trendScore}</div>
            {interestLevel !== undefined && (
              <div className="h-2 bg-bg-elevated rounded overflow-hidden">
                <div
                  style={{ width: `${Math.min(100, interestLevel)}%` }}
                  className="h-full bg-accent-blue rounded"
                />
              </div>
            )}
          </div>
        )}
        {bullBearRatio != null && (
          <div className="space-y-1.5">
            <div className="font-mono text-[10px] text-text-tertiary uppercase">StockTwits</div>
            <div className="flex items-baseline gap-1">
              <span className="font-mono text-lg font-semibold text-accent-green">
                {(bullBearRatio * 100).toFixed(0)}%
              </span>
              <span className="font-mono text-xs text-text-tertiary">bull</span>
            </div>
            {messageCount !== undefined && (
              <div className="font-mono text-xs text-text-tertiary">{messageCount} msgs</div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Catalysts & Risks accordion ──────────────────────────────────────────────

function CatalystsAccordion({
  catalysts,
  risks,
}: {
  catalysts?: string[]
  risks?: string[]
}) {
  if (!catalysts?.length && !risks?.length) return null
  return (
    <Accordion.Root type="multiple" className="space-y-1">
      {!!catalysts?.length && (
        <Accordion.Item value="catalysts" className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
          <Accordion.Trigger className="group w-full flex items-center justify-between px-3 py-2.5 font-mono text-xs text-text-secondary hover:text-text-primary transition-colors">
            <span className="uppercase tracking-widest text-[10px] text-accent-green">
              Catalysts ({catalysts.length})
            </span>
            <ChevronRight
              size={12}
              className="transition-transform group-data-[state=open]:rotate-90 text-text-tertiary"
            />
          </Accordion.Trigger>
          <Accordion.Content className="px-3 pb-3 space-y-1.5 data-[state=open]:animate-none">
            {catalysts.map((c, i) => (
              <div key={i} className="flex items-start gap-2 font-mono text-xs text-text-secondary">
                <span className="text-accent-green mt-0.5">+</span>
                <span>{c}</span>
              </div>
            ))}
          </Accordion.Content>
        </Accordion.Item>
      )}
      {!!risks?.length && (
        <Accordion.Item value="risks" className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
          <Accordion.Trigger className="group w-full flex items-center justify-between px-3 py-2.5 font-mono text-xs text-text-secondary hover:text-text-primary transition-colors">
            <span className="uppercase tracking-widest text-[10px] text-accent-red">
              Risks ({risks.length})
            </span>
            <ChevronRight
              size={12}
              className="transition-transform group-data-[state=open]:rotate-90 text-text-tertiary"
            />
          </Accordion.Trigger>
          <Accordion.Content className="px-3 pb-3 space-y-1.5 data-[state=open]:animate-none">
            {risks.map((r, i) => (
              <div key={i} className="flex items-start gap-2 font-mono text-xs text-text-secondary">
                <span className="text-accent-red mt-0.5">−</span>
                <span>{r}</span>
              </div>
            ))}
          </Accordion.Content>
        </Accordion.Item>
      )}
    </Accordion.Root>
  )
}

// ─── Expected Moves table ──────────────────────────────────────────────────────

const HORIZON_LABEL: Record<string, string> = {
  today:    'Today',
  week:     'This Week',
  month:    'This Month',
  year:     'This Year',
  straddle: 'Straddle ±',
}

function ExpectedMovesTable({ moves, currentPrice }: { moves: ExpectedMove[]; currentPrice: number }) {
  if (!moves?.length) return null
  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-4 space-y-3">
      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
        Expected Price Movement
      </div>
      {/* Header */}
      <div className="grid grid-cols-[80px_1fr_1fr_1fr_1fr] gap-x-2 font-mono text-[9px] uppercase tracking-wide text-text-tertiary border-b border-border-subtle pb-1">
        <span></span>
        <span className="text-accent-red text-center">Bear</span>
        <span className="text-text-secondary text-center">Base</span>
        <span className="text-accent-green text-center">Bull</span>
        <span className="text-center">Prob (B/N/Be)</span>
      </div>
      {moves.map((m) => {
        const fmtPct = (v: number) => (v >= 0 ? `+${v.toFixed(1)}%` : `${v.toFixed(1)}%`)
        const fmtPrice = (v: number) => `$${v.toFixed(2)}`
        const bullPct  = Math.round((m.bull_prob ?? 0) * 100)
        const neutralPct = Math.round((m.neutral_prob ?? 0) * 100)
        const bearPct  = Math.round((m.bear_prob ?? 0) * 100)
        return (
          <div
            key={m.horizon}
            className="grid grid-cols-[80px_1fr_1fr_1fr_1fr] gap-x-2 items-center"
          >
            {/* Horizon label */}
            <span className="font-mono text-[10px] text-text-tertiary">
              {HORIZON_LABEL[m.horizon] ?? m.horizon}
            </span>
            {/* Bear */}
            <div className="text-center space-y-0.5">
              <div className="font-mono text-xs font-semibold text-accent-red">
                {fmtPct(m.bear_pct)}
              </div>
              <div className="font-mono text-[9px] text-text-tertiary">
                {fmtPrice(m.bear_price)}
              </div>
            </div>
            {/* Base */}
            <div className="text-center space-y-0.5">
              <div className={clsx(
                'font-mono text-xs font-semibold',
                m.base_pct >= 0 ? 'text-accent-green' : 'text-accent-red'
              )}>
                {fmtPct(m.base_pct)}
              </div>
              <div className="font-mono text-[9px] text-text-tertiary">
                {fmtPrice(m.base_price)}
              </div>
            </div>
            {/* Bull */}
            <div className="text-center space-y-0.5">
              <div className="font-mono text-xs font-semibold text-accent-green">
                {fmtPct(m.bull_pct)}
              </div>
              <div className="font-mono text-[9px] text-text-tertiary">
                {fmtPrice(m.bull_price)}
              </div>
            </div>
            {/* Probability bar */}
            <div className="space-y-1">
              <div className="h-2 rounded overflow-hidden flex">
                <div style={{ width: `${bullPct}%` }}    className="bg-accent-green" />
                <div style={{ width: `${neutralPct}%` }} className="bg-text-tertiary/30" />
                <div style={{ width: `${bearPct}%` }}    className="bg-accent-red" />
              </div>
              <div className="font-mono text-[9px] text-text-tertiary text-center">
                {bullPct}% / {neutralPct}% / {bearPct}%
              </div>
            </div>
          </div>
        )
      })}
      <div className="font-mono text-[9px] text-text-tertiary pt-1 border-t border-border-subtle">
        Prob columns: Bull / Neutral / Bear · Prices based on current ${currentPrice.toFixed(2)}
      </div>
    </div>
  )
}

// ─── Live Max Pain card ────────────────────────────────────────────────────────

function MaxPainCard({ data }: { data: MaxPainData }) {
  const nearest = data.all_expirations[0]
  void nearest
  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-2">
      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
        Max Pain (live)
      </div>
      {/* Header row */}
      <div className="flex items-center gap-2 font-mono text-[9px] text-text-tertiary uppercase tracking-wide border-b border-border-subtle pb-1">
        <span className="w-24">Expiry</span>
        <span className="w-16 text-right">Max Pain</span>
        <span className="w-12 text-right">Dist</span>
        <span className="w-16 text-right">OI</span>
        <span className="w-10 text-right">P/C</span>
        <span className="w-12 text-right">Strength</span>
      </div>
      <div className="space-y-1.5">
        {data.all_expirations.map((e) => {
          const pcColor = e.pc_ratio == null ? '#71717a'
            : e.pc_ratio > 1.2 ? '#ef4444'
            : e.pc_ratio < 0.8 ? '#22c55e'
            : '#a1a1aa'
          return (
            <div key={e.expiry} className="flex items-center gap-2">
              <span className="font-mono text-[10px] text-text-tertiary w-24">{e.expiry}</span>
              <span className="font-mono text-xs font-semibold text-text-primary w-16 text-right">${e.max_pain.toFixed(2)}</span>
              <span className="font-mono text-[10px] w-12 text-right" style={{ color: e.direction === 'UP' ? '#22c55e' : e.direction === 'DOWN' ? '#ef4444' : '#a1a1aa' }}>
                {e.distance_pct > 0 ? '+' : ''}{e.distance_pct.toFixed(1)}%
              </span>
              <span className="font-mono text-[10px] text-text-tertiary w-16 text-right">{e.total_oi.toLocaleString()}</span>
              <span className="font-mono text-[10px] w-10 text-right font-semibold" style={{ color: pcColor }}>
                {e.pc_ratio != null ? e.pc_ratio.toFixed(2) : '—'}
              </span>
              <span className={clsx(
                'font-mono text-[9px] px-1 rounded w-12 text-center',
                e.signal_strength === 'HIGH' ? 'bg-accent-green/20 text-accent-green'
                  : e.signal_strength === 'MEDIUM' ? 'bg-accent-amber/20 text-accent-amber'
                  : 'bg-bg-elevated text-text-tertiary'
              )}>{e.signal_strength}</span>
            </div>
          )
        })}
      </div>
      <div className="font-mono text-[10px] text-text-tertiary pt-1 border-t border-border-subtle">
        {data.interpretation}
      </div>
    </div>
  )
}

// ─── Action Zones card ────────────────────────────────────────────────────────

const ACTION_STYLE: Record<string, string> = {
  green:   'bg-accent-green/10 border-accent-green/30 text-accent-green',
  red:     'bg-accent-red/10 border-accent-red/30 text-accent-red',
  amber:   'bg-accent-amber/10 border-accent-amber/30 text-accent-amber',
  blue:    'bg-accent-blue/10 border-accent-blue/30 text-accent-blue',
  neutral: 'bg-bg-surface border-border-subtle text-text-secondary',
}

function LevelBar({ zones }: { zones: ActionZones }) {
  const { stop_loss, buy_zone_low, buy_zone_high, entry_mid, current_price, target_1, target_2 } = zones
  const lo = stop_loss * 0.995
  const hi = target_2 * 1.005
  const range = hi - lo
  const p = (v: number) => `${Math.max(0, Math.min(100, (v - lo) / range * 100)).toFixed(1)}%`

  return (
    <div className="relative h-8 my-2">
      {/* Track */}
      <div className="absolute inset-y-3 left-0 right-0 bg-bg-elevated rounded" />
      {/* Buy zone fill */}
      <div
        className="absolute inset-y-2.5 bg-accent-green/20 rounded"
        style={{ left: p(buy_zone_low), width: `${((buy_zone_high - buy_zone_low) / range * 100).toFixed(1)}%` }}
      />
      {/* Stop marker */}
      <div className="absolute top-0 bottom-0 flex flex-col items-center" style={{ left: p(stop_loss), transform: 'translateX(-50%)' }}>
        <div className="w-0.5 h-full bg-accent-red" />
      </div>
      {/* Entry mid */}
      <div className="absolute top-0 bottom-0 flex flex-col items-center" style={{ left: p(entry_mid), transform: 'translateX(-50%)' }}>
        <div className="w-0.5 h-full bg-accent-green/60 border-dashed" />
      </div>
      {/* Current price dot */}
      <div
        className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-text-primary border-2 border-bg-surface shadow z-10"
        style={{ left: p(current_price), transform: 'translate(-50%, -50%)' }}
      />
      {/* T1 marker */}
      <div className="absolute top-0 bottom-0" style={{ left: p(target_1), transform: 'translateX(-50%)' }}>
        <div className="w-0.5 h-full bg-accent-green/50" />
      </div>
      {/* T2 marker */}
      <div className="absolute top-0 bottom-0" style={{ left: p(target_2), transform: 'translateX(-50%)' }}>
        <div className="w-0.5 h-full bg-accent-green" />
      </div>
      {/* Labels below */}
      <div className="absolute top-full mt-0.5 text-[8px] font-mono text-accent-red" style={{ left: p(stop_loss), transform: 'translateX(-50%)' }}>SL</div>
      <div className="absolute top-full mt-0.5 text-[8px] font-mono text-text-tertiary" style={{ left: p(current_price), transform: 'translateX(-50%)' }}>●</div>
      <div className="absolute top-full mt-0.5 text-[8px] font-mono text-accent-green/70" style={{ left: p(target_1), transform: 'translateX(-50%)' }}>T1</div>
      <div className="absolute top-full mt-0.5 text-[8px] font-mono text-accent-green" style={{ left: p(target_2), transform: 'translateX(-50%)' }}>T2</div>
    </div>
  )
}

function ActionZonesCard({ zones }: { zones: ActionZones }) {
  const { eur, pct, rr_t1, rr_t2, atr_pct, rsi, timing, suggested_size_eur, action, action_color, currency, fx_rate } = zones
  const fmtE = (v: number) => `€${v.toFixed(2)}`
  const fmtP = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`

  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Action Zones</div>
        <div className="font-mono text-[9px] text-text-tertiary">
          ATR {fmtE(eur.atr)} ({atr_pct}%) · RSI {rsi.toFixed(0)}
          {currency !== 'EUR' && <span className="ml-1 opacity-60">{currency}/{fx_rate}</span>}
        </div>
      </div>

      {/* Action chip */}
      <div className={clsx('font-mono text-xs font-semibold px-3 py-1.5 rounded border', ACTION_STYLE[action_color])}>
        ➡ {action}
      </div>

      {/* Visual level bar */}
      <LevelBar zones={zones} />

      {/* Metrics grid */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 mt-4">
        {[
          { label: 'Stop',      val: fmtE(eur.stop),      sub: fmtP(pct.stop),     color: 'text-accent-red' },
          { label: 'Buy zone',  val: `${fmtE(eur.buy_low)} – ${fmtE(eur.buy_high)}`, sub: `mid ${fmtE(eur.entry_mid)}`, color: 'text-accent-green' },
          { label: 'T1',        val: fmtE(eur.t1),        sub: `${fmtP(pct.t1)}  R:R ${rr_t1.toFixed(1)}`, color: 'text-accent-green' },
          { label: 'T2',        val: fmtE(eur.t2),        sub: `${fmtP(pct.t2)}  R:R ${rr_t2.toFixed(1)}`, color: 'text-accent-green' },
          { label: 'Current',   val: fmtE(eur.current),   sub: `${fmtP(pct.current)} vs entry`, color: pct.current >= 0 ? 'text-text-primary' : 'text-accent-amber' },
          { label: 'Size',      val: `€${suggested_size_eur.toLocaleString()}`, sub: '2% NAV', color: 'text-text-secondary' },
        ].map(({ label, val, sub, color }) => (
          <div key={label}>
            <div className="font-mono text-[9px] uppercase tracking-wide text-text-tertiary">{label}</div>
            <div className={clsx('font-mono text-xs font-semibold', color)}>{val}</div>
            <div className="font-mono text-[9px] text-text-tertiary">{sub}</div>
          </div>
        ))}
      </div>

      {/* Timing */}
      <div className="font-mono text-[10px] text-text-tertiary border-t border-border-subtle pt-2">
        ⏱ {timing}
      </div>
    </div>
  )
}

// ─── Analyze button ────────────────────────────────────────────────────────────

function AnalyzeButton({ symbol, hasThesis }: { symbol: string; hasThesis: boolean }) {
  const [job, setJob] = useState<AnalyzeStatus | null>(null)

  // Poll for completion when running
  useQuery<AnalyzeStatus>({
    queryKey: ['analyze_status', symbol],
    queryFn: () => api.tickerAnalyzeStatus(symbol),
    refetchInterval: job?.status === 'running' ? 5000 : false,
    enabled: job?.status === 'running',
    onSuccess: (data: AnalyzeStatus) => {
      if (data.status === 'done') setJob(data)
    },
  } as any)

  const handleRun = async () => {
    try {
      const res = await api.tickerAnalyze(symbol)
      setJob(res)
    } catch (e) {
      console.error(e)
    }
  }

  if (job?.status === 'running') {
    return (
      <div className="flex items-center gap-2 font-mono text-xs text-accent-amber">
        <span className="animate-pulse">⬤</span> Running AI analysis for {symbol}…
        <span className="text-text-tertiary text-[10px]">refresh in ~60s</span>
      </div>
    )
  }
  if (job?.status === 'done') {
    return (
      <div className="font-mono text-xs text-accent-green">
        ✓ Analysis complete — reload page to see thesis
      </div>
    )
  }

  return (
    <button
      onClick={handleRun}
      className={clsx(
        'font-mono text-xs px-3 py-1.5 rounded border transition-colors',
        hasThesis
          ? 'border-border-subtle text-text-tertiary hover:text-text-secondary hover:border-border-active'
          : 'bg-accent-purple/20 border-accent-purple/40 text-accent-purple hover:bg-accent-purple/30'
      )}
    >
      {hasThesis ? '↻ Re-run AI analysis' : '▶ Run AI analysis'}
    </button>
  )
}

// ─── Action derivation ────────────────────────────────────────────────────────

function deriveAction(signal: TickerDetail): { text: string; color: string } | null {
  const { current_price, entry_low, entry_high, target_1, target_2, stop_loss } = signal
  if (current_price == null) return null
  if (stop_loss != null && current_price < stop_loss)
    return { text: 'BELOW STOP — thesis invalidated', color: 'text-accent-red' }
  if (entry_low != null && entry_high != null && current_price >= entry_low && current_price <= entry_high)
    return { text: 'IN BUY ZONE — valid entry, confirm catalyst', color: 'text-accent-green' }
  if (entry_low != null && current_price < entry_low)
    return { text: 'BELOW ZONE — wait for stabilization before entry', color: 'text-accent-amber' }
  if (target_2 != null && current_price >= target_2)
    return { text: 'AT/ABOVE T2 — full target reached, exit or trail stop', color: 'text-accent-blue' }
  if (target_1 != null && current_price >= target_1)
    return { text: 'AT/ABOVE T1 — take partial profits, move stop to entry', color: 'text-accent-blue' }
  if (entry_high != null && current_price > entry_high)
    return { text: 'ABOVE ZONE — wait for pullback to buy zone', color: 'text-text-secondary' }
  return null
}

// ─── SEC Filings card ─────────────────────────────────────────────────────────

const FORM_COLOR: Record<string, string> = {
  '10-K':   'text-accent-blue',
  '10-Q':   'text-accent-blue',
  '8-K':    'text-accent-amber',
  'DEF 14A':'text-text-tertiary',
  'S-1':    'text-accent-green',
  '424B4':  'text-accent-green',
}

function SecFilingsCard({ filings }: { filings: SecFiling[] }) {
  if (!filings.length) return null
  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-2">
      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
        Recent SEC Filings
      </div>
      <div className="space-y-1.5">
        {filings.map((f, i) => (
          <a
            key={i}
            href={f.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 group hover:bg-bg-elevated rounded px-1 py-0.5 transition-colors"
          >
            <span className={clsx('font-mono text-[10px] font-bold w-14 flex-shrink-0', FORM_COLOR[f.form] ?? 'text-text-secondary')}>
              {f.form}
            </span>
            <span className="font-mono text-[10px] text-text-tertiary w-20 flex-shrink-0">{f.date}</span>
            <span className="font-mono text-[10px] text-text-secondary group-hover:text-text-primary truncate transition-colors">
              {f.description}
            </span>
            <span className="font-mono text-[9px] text-accent-blue opacity-0 group-hover:opacity-100 flex-shrink-0 transition-opacity">↗</span>
          </a>
        ))}
      </div>
    </div>
  )
}

// ─── Congress Trades card ──────────────────────────────────────────────────────

function CongressTradesCard({ trades }: { trades: CongressTrade[] }) {
  if (!trades.length) return null
  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-2">
      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
        Congress Trades ({trades.length})
      </div>
      <div className="space-y-2">
        {trades.slice(0, 10).map((t, i) => {
          const isBuy = /purchase|buy/i.test(t.type)
          const isSell = /sale|sell/i.test(t.type)
          return (
            <div key={i} className="flex items-start gap-2">
              <span className={clsx(
                'font-mono text-[9px] px-1 rounded flex-shrink-0 mt-0.5',
                isBuy  ? 'bg-accent-green/20 text-accent-green'
                  : isSell ? 'bg-accent-red/20 text-accent-red'
                  : 'bg-bg-elevated text-text-tertiary'
              )}>
                {t.type || '—'}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className={clsx(
                    'font-mono text-[9px] border rounded px-1',
                    t.chamber === 'Senate'
                      ? 'border-accent-purple/40 text-accent-purple'
                      : 'border-accent-blue/40 text-accent-blue'
                  )}>
                    {t.chamber}
                  </span>
                  <span className="font-mono text-xs text-text-primary truncate">{t.member}</span>
                </div>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="font-mono text-[9px] text-text-tertiary">{t.date}</span>
                  {t.amount && (
                    <span className="font-mono text-[9px] text-text-secondary">{t.amount}</span>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ─── Earnings card ─────────────────────────────────────────────────────────────

type EarningsView = '4Q' | '8Q' | '5Y'

function fmtRevenue(v: number | null): string {
  if (v == null) return '—'
  if (Math.abs(v) >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (Math.abs(v) >= 1e9)  return `$${(v / 1e9).toFixed(1)}B`
  if (Math.abs(v) >= 1e6)  return `$${(v / 1e6).toFixed(0)}M`
  return `$${v.toLocaleString()}`
}

function EpsTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload as EarningsQuarter
  return (
    <div className="bg-bg-elevated border border-border-subtle rounded px-3 py-2 font-mono text-xs space-y-1">
      <div className="text-text-primary font-semibold">{label}</div>
      {d.eps_estimate != null && (
        <div className="text-text-tertiary">Est: ${d.eps_estimate.toFixed(2)}</div>
      )}
      {d.eps_actual != null && (
        <div className={d.beat === true ? 'text-accent-green' : d.beat === false ? 'text-accent-red' : 'text-text-primary'}>
          Actual: ${d.eps_actual.toFixed(2)}
          {d.surprise_pct != null && (
            <span className="ml-1 text-[10px]">
              ({d.surprise_pct >= 0 ? '+' : ''}{d.surprise_pct.toFixed(1)}%)
            </span>
          )}
        </div>
      )}
      {d.revenue != null && (
        <div className="text-text-secondary">Rev: {fmtRevenue(d.revenue)}</div>
      )}
    </div>
  )
}

function RevenueTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload as EarningsQuarter
  return (
    <div className="bg-bg-elevated border border-border-subtle rounded px-3 py-2 font-mono text-xs space-y-1">
      <div className="text-text-primary font-semibold">{label}</div>
      {d.revenue_estimate != null && (
        <div className="text-text-tertiary">Est: {fmtRevenue(d.revenue_estimate)}</div>
      )}
      {d.revenue != null && (
        <div className={
          d.revenue_beat === true  ? 'text-accent-green' :
          d.revenue_beat === false ? 'text-accent-red'   : 'text-accent-blue'
        }>
          Actual: {fmtRevenue(d.revenue)}
          {d.revenue_beat === true  && <span className="ml-1 text-[10px]">✓ beat</span>}
          {d.revenue_beat === false && <span className="ml-1 text-[10px]">✗ miss</span>}
        </div>
      )}
    </div>
  )
}

function AnnualTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload as EarningsAnnual
  return (
    <div className="bg-bg-elevated border border-border-subtle rounded px-3 py-2 font-mono text-xs space-y-1">
      <div className="text-text-primary font-semibold">{label}</div>
      {d.revenue != null && <div className="text-accent-blue">Rev: {fmtRevenue(d.revenue)}</div>}
      {d.eps != null && <div className="text-accent-green">EPS: ${d.eps.toFixed(2)}</div>}
      {d.net_income != null && <div className="text-text-secondary">NI: {fmtRevenue(d.net_income)}</div>}
    </div>
  )
}

function EarningsCard({ data }: { data: EarningsData }) {
  const [view, setView] = useState<EarningsView>('4Q')

  // Hooks must be declared before any early returns
  const barFill = useCallback((entry: EarningsQuarter) => {
    if (entry.beat === true)  return '#22c55e'
    if (entry.beat === false) return '#ef4444'
    return '#52525b'
  }, [])

  const quarterly = data.quarterly ?? []
  const annual    = data.annual    ?? []
  const { next_earnings, next_eps, next_revenue, eps_growth_yoy } = data

  if (!quarterly.length && !annual.length && !next_earnings) return null

  const quarterlySlice = view === '4Q' ? quarterly.slice(-4) : quarterly
  const isAnnual = view === '5Y'

  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-3">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
          Earnings
          {eps_growth_yoy != null && (
            <span className={clsx('ml-2', eps_growth_yoy >= 0 ? 'text-accent-green' : 'text-accent-red')}>
              YoY EPS {eps_growth_yoy >= 0 ? '+' : ''}{eps_growth_yoy}%
            </span>
          )}
        </div>
        {/* View toggle */}
        <div className="flex gap-1">
          {(['4Q', '8Q', '5Y'] as EarningsView[]).map(v => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={clsx(
                'font-mono text-[9px] px-1.5 py-0.5 rounded border transition-colors',
                view === v
                  ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/40'
                  : 'text-text-tertiary border-border-subtle hover:text-text-secondary'
              )}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      {/* Next earnings banner */}
      {next_earnings && (
        <div className="bg-accent-amber/10 border border-accent-amber/30 rounded px-3 py-2 space-y-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[9px] uppercase text-accent-amber tracking-wide">Next</span>
            <span className="font-mono text-sm font-semibold text-accent-amber">{next_earnings}</span>
          </div>
          <div className="flex gap-4">
            {next_eps?.avg != null && (
              <div>
                <span className="font-mono text-[9px] text-text-tertiary">EPS est </span>
                <span className="font-mono text-xs text-text-primary font-semibold">${next_eps.avg.toFixed(2)}</span>
                {next_eps.low != null && next_eps.high != null && (
                  <span className="font-mono text-[9px] text-text-tertiary ml-1">
                    (${next_eps.low.toFixed(2)}–${next_eps.high.toFixed(2)})
                  </span>
                )}
              </div>
            )}
            {next_revenue?.avg != null && (
              <div>
                <span className="font-mono text-[9px] text-text-tertiary">Rev est </span>
                <span className="font-mono text-xs text-text-primary font-semibold">{fmtRevenue(next_revenue.avg)}</span>
                {next_revenue.low != null && next_revenue.high != null && (
                  <span className="font-mono text-[9px] text-text-tertiary ml-1">
                    ({fmtRevenue(next_revenue.low)}–{fmtRevenue(next_revenue.high)})
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Quarterly charts ── */}
      {!isAnnual && quarterlySlice.length > 0 && (
        <div className="space-y-3">

          {/* EPS beat/miss */}
          <div>
            <div className="font-mono text-[9px] text-text-tertiary mb-1 flex gap-3">
              <span className="font-semibold">EPS</span>
              <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-sm bg-accent-green" />Beat</span>
              <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-sm bg-accent-red" />Miss</span>
              <span className="flex items-center gap-1"><span className="inline-block w-6 border-t border-dashed border-accent-amber" />Est</span>
            </div>
            <ResponsiveContainer width="100%" height={130}>
              <ComposedChart data={quarterlySlice} margin={{ top: 4, right: 4, bottom: 0, left: -16 }}>
                <XAxis dataKey="label" tick={{ fontFamily: 'monospace', fontSize: 9, fill: '#71717a' }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontFamily: 'monospace', fontSize: 9, fill: '#71717a' }} axisLine={false} tickLine={false} tickFormatter={(v) => `$${v}`} />
                <RechartTooltip content={<EpsTooltip />} cursor={{ fill: '#27272a' }} />
                <ReferenceLine y={0} stroke="#3f3f46" />
                <Bar dataKey="eps_actual" radius={[2, 2, 0, 0]} maxBarSize={32}>
                  {quarterlySlice.map((entry, i) => (
                    <Cell key={i} fill={barFill(entry)} fillOpacity={0.85} />
                  ))}
                </Bar>
                <Line dataKey="eps_estimate" dot={{ r: 3, fill: '#f59e0b', stroke: 'none' }} stroke="#f59e0b" strokeDasharray="3 3" strokeWidth={1.5} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {/* Revenue beat/miss */}
          {quarterlySlice.some(q => q.revenue != null) && (
            <div>
              <div className="font-mono text-[9px] text-text-tertiary mb-1 flex gap-3">
                <span className="font-semibold">Revenue</span>
                {quarterlySlice.some(q => q.revenue_beat != null) ? (
                  <>
                    <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-sm bg-accent-green" />Beat</span>
                    <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-sm bg-accent-red" />Miss</span>
                    <span className="flex items-center gap-1"><span className="inline-block w-6 border-t border-dashed border-accent-amber" />Est</span>
                  </>
                ) : (
                  <span className="text-text-tertiary italic normal-case">actual · no consensus est available</span>
                )}
              </div>
              <ResponsiveContainer width="100%" height={110}>
                <ComposedChart data={quarterlySlice} margin={{ top: 2, right: 4, bottom: 0, left: -8 }}>
                  <XAxis dataKey="label" tick={{ fontFamily: 'monospace', fontSize: 9, fill: '#71717a' }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontFamily: 'monospace', fontSize: 9, fill: '#71717a' }} axisLine={false} tickLine={false} tickFormatter={(v) => `${(v / 1e9).toFixed(0)}B`} />
                  <RechartTooltip content={<RevenueTooltip />} cursor={{ fill: '#27272a' }} />
                  <Bar dataKey="revenue" radius={[2, 2, 0, 0]} maxBarSize={32}>
                    {quarterlySlice.map((entry, i) => (
                      <Cell
                        key={i}
                        fill={
                          entry.revenue_beat === true  ? '#22c55e' :
                          entry.revenue_beat === false ? '#ef4444' : '#3b82f6'
                        }
                        fillOpacity={0.75}
                      />
                    ))}
                  </Bar>
                  <Line dataKey="revenue_estimate" dot={{ r: 3, fill: '#f59e0b', stroke: 'none' }} stroke="#f59e0b" strokeDasharray="3 3" strokeWidth={1.5} connectNulls />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}

        </div>
      )}

      {/* ── 5-Year Annual chart ── */}
      {isAnnual && annual.length > 0 && (
        <div>
          <ResponsiveContainer width="100%" height={180}>
            <ComposedChart data={annual} margin={{ top: 4, right: 32, bottom: 0, left: -8 }}>
              <XAxis dataKey="label" tick={{ fontFamily: 'monospace', fontSize: 9, fill: '#71717a' }} axisLine={false} tickLine={false} />
              <YAxis
                yAxisId="rev"
                tick={{ fontFamily: 'monospace', fontSize: 9, fill: '#71717a' }}
                axisLine={false} tickLine={false}
                tickFormatter={(v) => `${(v / 1e9).toFixed(0)}B`}
              />
              <YAxis
                yAxisId="eps"
                orientation="right"
                tick={{ fontFamily: 'monospace', fontSize: 9, fill: '#71717a' }}
                axisLine={false} tickLine={false}
                tickFormatter={(v) => `$${v}`}
              />
              <RechartTooltip content={<AnnualTooltip />} cursor={{ fill: '#27272a' }} />
              <ReferenceLine yAxisId="rev" y={0} stroke="#3f3f46" />
              <Bar yAxisId="rev" dataKey="revenue" name="Revenue" fill="#3b82f6" fillOpacity={0.7} radius={[2, 2, 0, 0]} maxBarSize={36} />
              <Line
                yAxisId="eps"
                dataKey="eps"
                name="EPS"
                stroke="#22c55e"
                strokeWidth={2}
                dot={{ r: 3, fill: '#22c55e', stroke: 'none' }}
                connectNulls
              />
            </ComposedChart>
          </ResponsiveContainer>
          <div className="font-mono text-[9px] text-text-tertiary mt-1 flex gap-4">
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-2 rounded-sm bg-accent-blue opacity-70" />Revenue</span>
            <span className="flex items-center gap-1"><span className="inline-block w-4 border-t-2 border-accent-green" />EPS (right axis)</span>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Page ──────────────────────────────────────────────────────────────────────

export function TickerPage() {
  const { symbol = '' } = useParams()
  const { data: signal, isLoading } = useSignalsTicker(symbol)
  const { data: dpHistory } = useDarkPoolTicker(symbol)
  const { data: heatmapRows } = useHeatmap()
  const { data: maxPainLive } = useQuery({
    queryKey: ['max_pain', symbol],
    queryFn: () => api.maxPainLive(symbol),
    staleTime: 60 * 60 * 1000,
    enabled: !!symbol,
  })
  const { data: secFilings = [] } = useQuery<SecFiling[]>({
    queryKey: ['sec_filings', symbol],
    queryFn: () => api.tickerSecFilings(symbol),
    staleTime: 6 * 60 * 60 * 1000,
    enabled: !!symbol,
  })
  const { data: congressTrades = [] } = useQuery<CongressTrade[]>({
    queryKey: ['congress_trades', symbol],
    queryFn: () => api.tickerCongressTrades(symbol),
    staleTime: 60 * 60 * 1000,
    enabled: !!symbol,
  })
  const { data: earningsData } = useQuery<EarningsData | null>({
    queryKey: ['earnings', symbol],
    queryFn: () => api.tickerEarnings(symbol),
    staleTime: 4 * 60 * 60 * 1000,
    enabled: !!symbol,
  })
  const { data: actionZones } = useQuery<ActionZones | null>({
    queryKey: ['action_zones', symbol],
    queryFn: () => api.tickerActionZones(symbol),
    staleTime: 15 * 60 * 1000,
    enabled: !!symbol,
  })

  const allTickers = useMemo(() => heatmapRows?.map(r => r.ticker).sort() ?? [], [heatmapRows])

  const dpLatest = dpHistory?.[0]

  return (
    <Shell title={`${symbol} — Deep Dive`}>
      {/* Ticker search bar + header row */}
      <div className="flex items-center gap-4 mb-5">
        <TickerSearch current={symbol} tickers={allTickers} />
        {signal && (
          <div className="flex items-center gap-3">
            <DirectionBadge direction={signal.direction} />
            <ConvictionDots conviction={signal.conviction} />
            {signal.regime && <RegimeBadge regime={signal.regime} size="sm" />}
          </div>
        )}
        <div className="ml-auto">
          <AnalyzeButton symbol={symbol} hasThesis={!!(signal?.thesis || signal?.ai_synthesis)} />
        </div>
      </div>

      {isLoading ? (
        <LoadingSkeleton rows={10} />
      ) : !signal ? (
        <div className="font-mono text-sm text-text-tertiary py-12 text-center">
          No data for {symbol}. Run <code className="text-accent-amber">./run_master.sh</code> to generate signals.
        </div>
      ) : (
        <div className="grid gap-4" style={{ gridTemplateColumns: '55% 1fr' }}>
          {/* ── LEFT COLUMN ── */}
          <div className="space-y-4 min-w-0">
            {/* Header card */}
            <div className="bg-bg-surface border border-border-subtle rounded p-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="font-mono text-[32px] font-semibold leading-none text-text-primary">
                    {symbol}
                  </div>
                  {signal.company_name && (
                    <div className="font-mono text-sm text-text-secondary mt-1">
                      {signal.company_name}
                    </div>
                  )}
                  <div className="font-mono text-xs text-text-tertiary mt-1">{signal.as_of}</div>
                </div>
                <div className="text-right">
                  {signal.current_price != null && (
                    <div className="font-mono text-[28px] font-semibold leading-none text-text-primary">
                      ${signal.current_price.toFixed(2)}
                    </div>
                  )}
                  {signal.price_change_1d !== undefined && (
                    <MonoNumber
                      value={signal.price_change_1d}
                      prefix="$"
                      decimals={2}
                      colorBySign
                      className="text-sm mt-1"
                    />
                  )}
                  {signal.price_change_1d_pct !== undefined && (
                    <MonoNumber
                      value={signal.price_change_1d_pct}
                      suffix="%"
                      decimals={2}
                      colorBySign
                      className="text-xs ml-2"
                    />
                  )}
                </div>
              </div>
              <TradeSetupStrip signal={signal} />
            </div>

            {/* AI Thesis card — action-first layout */}
            {(() => {
              const action = deriveAction(signal)
              const thesis = signal.thesis || signal.ai_synthesis
              return (
                <div className="bg-bg-elevated border-l-2 border-accent-purple rounded-r p-4 space-y-3">
                  {/* Header */}
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">AI Thesis</span>
                    <div className="flex items-center gap-2">
                      {signal.time_horizon && (
                        <span className="font-mono text-[9px] text-text-tertiary border border-border-subtle rounded px-1.5 py-0.5">
                          {signal.time_horizon}
                        </span>
                      )}
                      {signal.data_quality && (
                        <span className={clsx(
                          'font-mono text-[9px] border rounded px-1.5 py-0.5',
                          signal.data_quality === 'HIGH'   ? 'border-accent-green/40 text-accent-green' :
                          signal.data_quality === 'MEDIUM' ? 'border-accent-amber/40 text-accent-amber' :
                                                             'border-accent-red/40 text-accent-red'
                        )}>
                          {signal.data_quality}
                        </span>
                      )}
                      <span className="font-mono text-sm font-semibold text-accent-purple">
                        {Math.round(signal.signal_agreement_score * 100)}%
                        <span className="text-[10px] font-normal text-text-tertiary ml-1">agree</span>
                      </span>
                    </div>
                  </div>

                  {/* ACTION — most prominent item */}
                  {action && (
                    <div className={clsx(
                      'font-mono text-xs font-semibold px-3 py-2 rounded border',
                      action.color === 'text-accent-green'  ? 'bg-accent-green/10 border-accent-green/30 text-accent-green' :
                      action.color === 'text-accent-red'    ? 'bg-accent-red/10 border-accent-red/30 text-accent-red' :
                      action.color === 'text-accent-amber'  ? 'bg-accent-amber/10 border-accent-amber/30 text-accent-amber' :
                      action.color === 'text-accent-blue'   ? 'bg-accent-blue/10 border-accent-blue/30 text-accent-blue' :
                                                              'bg-bg-surface border-border-subtle text-text-secondary'
                    )}>
                      ➡ {action.text}
                    </div>
                  )}

                  {/* Bull / Bear / Invalidation */}
                  <div className="space-y-2">
                    {signal.primary_scenario && (
                      <div className="flex items-start gap-2">
                        <span className="font-mono text-[10px] font-bold text-accent-green flex-shrink-0 mt-0.5 w-12">▲ BULL</span>
                        <span className="font-mono text-xs text-text-secondary leading-relaxed">{signal.primary_scenario}</span>
                      </div>
                    )}
                    {signal.bear_scenario && (
                      <div className="flex items-start gap-2">
                        <span className="font-mono text-[10px] font-bold text-accent-red flex-shrink-0 mt-0.5 w-12">▼ BEAR</span>
                        <span className="font-mono text-xs text-text-secondary leading-relaxed">{signal.bear_scenario}</span>
                      </div>
                    )}
                    {signal.key_invalidation && (
                      <div className="flex items-start gap-2">
                        <span className="font-mono text-[10px] font-bold text-accent-amber flex-shrink-0 mt-0.5 w-12">⚡ INVAL</span>
                        <span className="font-mono text-xs text-text-secondary leading-relaxed">{signal.key_invalidation}</span>
                      </div>
                    )}
                  </div>

                  {/* Probability bar */}
                  {(signal.bull_probability !== undefined || signal.bear_probability !== undefined) && (
                    <ProbBar
                      bull={signal.bull_probability}
                      bear={signal.bear_probability}
                      neutral={signal.neutral_probability}
                    />
                  )}

                  {/* Full thesis — collapsed by default */}
                  {thesis && (
                    <Accordion.Root type="single" collapsible>
                      <Accordion.Item value="thesis" className="border-t border-border-subtle pt-2">
                        <Accordion.Trigger className="group flex items-center gap-1 font-mono text-[10px] text-text-tertiary hover:text-text-secondary transition-colors w-full">
                          <ChevronRight size={10} className="transition-transform group-data-[state=open]:rotate-90" />
                          Full thesis
                        </Accordion.Trigger>
                        <Accordion.Content className="pt-2 data-[state=open]:animate-none">
                          <p className="font-mono text-xs text-text-tertiary leading-relaxed">{thesis}</p>
                        </Accordion.Content>
                      </Accordion.Item>
                    </Accordion.Root>
                  )}
                </div>
              )
            })()}

            {/* Price Ladder */}
            {signal.current_price != null && (
              <div className="bg-bg-surface border border-border-subtle rounded p-4">
                <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-3">
                  Price Levels
                </div>
                <PriceLadder
                  currentPrice={signal.current_price}
                  target1={signal.target_1}
                  target2={signal.target_2}
                  entryLow={signal.entry_low}
                  entryHigh={signal.entry_high}
                  stopLoss={signal.stop_loss}
                  poc={signal.poc}
                  vwap={signal.vwap}
                  maxPain={maxPainLive?.nearest_max_pain ?? signal.max_pain}
                  height={280}
                />
              </div>
            )}

            {/* Expected Moves */}
            {signal.current_price != null && (() => {
              const moves: ExpectedMove[] = (signal.expected_moves?.length ?? 0) > 0
                ? signal.expected_moves!
                : signal.expected_move_pct != null
                  ? [{
                      horizon: 'straddle',
                      bear_pct: -signal.expected_move_pct,
                      base_pct: 0,
                      bull_pct: signal.expected_move_pct,
                      bear_price: signal.current_price * (1 - signal.expected_move_pct / 100),
                      base_price: signal.current_price,
                      bull_price: signal.current_price * (1 + signal.expected_move_pct / 100),
                      bull_prob: 0.33,
                      bear_prob: 0.33,
                      neutral_prob: 0.34,
                    }]
                  : []
              return moves.length > 0 ? (
                <ExpectedMovesTable moves={moves} currentPrice={signal.current_price!} />
              ) : null
            })()}

            {/* Module mini-heatmap */}
            {signal.modules && Object.keys(signal.modules).length > 0 && (
              <ModuleMiniHeatmap modules={signal.modules} />
            )}
          </div>

          {/* ── RIGHT COLUMN ── */}
          <div className="space-y-4 min-w-0">
            {/* Action Zones */}
            {actionZones && <ActionZonesCard zones={actionZones} />}

            {/* Override flags */}
            {!!signal.override_flags?.length && (
              <OverrideCard flags={signal.override_flags} />
            )}

            {/* Squeeze details */}
            {(signal.squeeze_score ?? 0) > 30 && (
              <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-3">
                <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                  Squeeze Setup
                </div>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    { label: 'Float Short', value: signal.float_short_pct, suffix: '%' },
                    { label: 'Days to Cover', value: signal.days_to_cover, suffix: 'd' },
                    { label: 'Vol Surge', value: signal.volume_surge, suffix: 'x' },
                  ].map(({ label, value, suffix }) => (
                    <div key={label} className="text-center space-y-1">
                      <div className="font-mono text-[10px] text-text-tertiary uppercase">{label}</div>
                      <div className="font-mono text-base font-semibold text-text-primary">
                        {value != null ? `${value.toFixed(1)}${suffix}` : '—'}
                      </div>
                    </div>
                  ))}
                </div>
                {signal.ftd_shares !== undefined && signal.ftd_shares > 0 && (
                  <div className="font-mono text-xs text-text-tertiary">
                    FTD shares: {signal.ftd_shares.toLocaleString()}
                  </div>
                )}
                {signal.recent_squeeze && (
                  <div className="font-mono text-xs text-accent-amber bg-accent-amber/10 rounded px-2 py-1">
                    ⚠ Recent squeeze — guard position size
                  </div>
                )}
              </div>
            )}

            {/* Options flow */}
            {(signal.iv_rank != null || signal.expected_move_pct != null || signal.put_call_ratio != null || (maxPainLive?.nearest_max_pain ?? signal.max_pain) != null) && (
              <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-2">
                <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
                  Options Flow
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                  {[
                    { label: 'IV Rank', value: signal.iv_rank != null ? `${signal.iv_rank.toFixed(0)}%` : '—' },
                    { label: 'Exp Move', value: signal.expected_move_pct != null ? `±${signal.expected_move_pct.toFixed(1)}%` : '—' },
                    { label: 'P/C Ratio', value: signal.put_call_ratio != null ? signal.put_call_ratio.toFixed(2) : '—' },
                    { label: 'Max Pain', value: (maxPainLive?.nearest_max_pain ?? signal.max_pain) != null ? `$${(maxPainLive?.nearest_max_pain ?? signal.max_pain)!.toFixed(2)}` : '—' },
                  ].map(({ label, value }) => (
                    <div key={label} className="flex justify-between">
                      <span className="font-mono text-[10px] text-text-tertiary uppercase">{label}</span>
                      <span className="font-mono text-xs text-text-primary">{value}</span>
                    </div>
                  ))}
                </div>
                <div className="font-mono text-[9px] text-text-tertiary">
                  iv_source: {signal.iv_source ?? 'unknown'}
                </div>
              </div>
            )}

            {/* Dark pool */}
            {(signal.dark_pool_score !== undefined || dpLatest) && (
              <DarkPoolGauge
                score={signal.dark_pool_score ?? dpLatest?.score ?? 50}
                trend={signal.short_ratio_trend}
                intensity={signal.dark_pool_intensity}
              />
            )}

            {/* Social */}
            <SocialCard
              trendScore={signal.trend_score}
              interestLevel={signal.interest_level}
              bullBearRatio={signal.bull_bear_ratio}
              messageCount={signal.message_count}
            />

            {/* Dark pool history */}
            {dpHistory && dpHistory.length > 0 && (
              <div className="bg-bg-surface border border-border-subtle rounded p-3">
                <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
                  Dark Pool History
                </div>
                <div className="space-y-1.5">
                  {dpHistory.slice(0, 6).map((dp, i) => (
                    <div key={i} className="flex items-center justify-between">
                      <span className="font-mono text-[10px] text-text-tertiary">{dp.as_of}</span>
                      <span className={clsx(
                        'font-mono text-xs font-medium',
                        dp.signal === 'ACCUMULATION' ? 'text-accent-green'
                          : dp.signal === 'DISTRIBUTION' ? 'text-accent-red'
                          : 'text-text-secondary'
                      )}>
                        {dp.signal}
                      </span>
                      <MonoNumber value={dp.score} decimals={0} className="text-xs" />
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Live max pain */}
            {maxPainLive && <MaxPainCard data={maxPainLive} />}

            {/* Catalysts & Risks */}
            <CatalystsAccordion catalysts={signal.catalysts} risks={signal.risks} />

            {/* Earnings */}
            {earningsData && (earningsData.quarterly?.length || earningsData.annual?.length || earningsData.next_earnings) && (
              <EarningsCard data={earningsData} />
            )}

            {/* Congress Trades */}
            <CongressTradesCard trades={congressTrades} />

            {/* SEC Filings */}
            <SecFilingsCard filings={secFilings} />
          </div>
        </div>
      )}
    </Shell>
  )
}
