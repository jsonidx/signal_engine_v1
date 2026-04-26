import { useState, useMemo, useCallback, useEffect } from 'react'
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
import { RiskRewardBar } from '../components/RiskRewardBar'
import { HistoricalAnalogs } from '../components/HistoricalAnalogs'
import { PriceChart } from '../components/charts/PriceChart'
import { EarningsReactionModel } from '../components/EarningsReactionModel'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useSignalsTicker } from '../hooks/useHeatmap'
import { useDarkPoolTicker } from '../hooks/useDarkPool'
import { useHeatmap } from '../hooks/useHeatmap'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip as RechartTooltip,
  ResponsiveContainer, Cell, ReferenceLine, Legend,
} from 'recharts'
import { api } from '../lib/api'
import type { ExpectedMove, TickerDetail, SecFiling, EarningsData, EarningsQuarter, EarningsAnnual, ActionZones, AnalyzeStatus, RegimeCurrent } from '../lib/api'

// Cache TTL for regime data (1 hour, same as backend TTL_LONG)
const TTL_LONG_MS = 60 * 60 * 1000
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
  const {
    entry_low, entry_high, target_1, target_2, stop_loss, current_price,
    bull_probability, bear_probability, neutral_probability,
  } = signal

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

  const t1Pct   = target_1  != null ? pct(target_1)  : null
  const t2Pct   = target_2  != null ? pct(target_2)  : null
  const stopPct = stop_loss != null ? pct(stop_loss) : null

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

      {/* Visual Risk-Reward bar with EV calculation */}
      <RiskRewardBar
        entry={entry}
        target1={target_1}
        target2={target_2}
        stopLoss={stop_loss}
        currentPrice={current_price}
        bullPct={bull_probability}
        bearPct={bear_probability}
        neutralPct={neutral_probability}
      />
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

// ─── Module descriptions (shown in hover tooltips) ────────────────────────────

const MODULE_DESCRIPTIONS: Record<string, string> = {
  signal_engine: 'Core momentum + mean-reversion model. Combines 12-1 momentum, 5d reversal, and volatility-quality factor scores.',
  squeeze:       'Short-squeeze pressure index. Uses float short %, days-to-cover, cost-to-borrow, and recent volume surge.',
  options:       'Options flow heat score. IV rank, put/call ratio, unusual volume spikes, and expected move vs historical.',
  dark_pool:     'Dark pool accumulation/distribution signal. Off-exchange print volume trend and short-ratio direction.',
  fundamentals:  'Fundamental quality score. Revenue growth, EPS surprise history, valuation vs sector, and balance sheet strength.',
  polymarket:    'Prediction market signal. Polymarket probability vs current price implied move (where available).',
  cross_asset:   'Cross-asset divergence. Correlation of equity signal vs sector ETF, bond yield, and VIX regime.',
}

const WEIGHT_STORAGE_KEY = 'signal_engine:module_weights_v1'

const DEFAULT_WEIGHTS: Record<string, number> = {
  signal_engine: 1.0,
  squeeze:       1.0,
  options:       1.0,
  dark_pool:     1.0,
  fundamentals:  1.0,
  polymarket:    0.5,
  cross_asset:   0.75,
}

function loadWeights(): Record<string, number> {
  try {
    const raw = localStorage.getItem(WEIGHT_STORAGE_KEY)
    return raw ? { ...DEFAULT_WEIGHTS, ...JSON.parse(raw) } : { ...DEFAULT_WEIGHTS }
  } catch {
    return { ...DEFAULT_WEIGHTS }
  }
}

function saveWeights(w: Record<string, number>) {
  try {
    localStorage.setItem(WEIGHT_STORAGE_KEY, JSON.stringify(w))
  } catch {
    // localStorage unavailable — silently skip
  }
}

// ─── Module mini-heatmap with tooltips + weight editor ────────────────────────

function ModuleMiniHeatmap({ modules }: { modules: Record<string, number> }) {
  const [weights, setWeights] = useState<Record<string, number>>(loadWeights)
  const [editing, setEditing] = useState(false)

  const handleWeightChange = (key: string, val: number) => {
    const next = { ...weights, [key]: val }
    setWeights(next)
    saveWeights(next)
  }

  // Weighted agreement score — how many modules agree after applying weights
  const weightedScores = MODULE_KEYS.map(({ key }) => {
    const score = modules[key] ?? 0
    return score * (weights[key] ?? 1.0)
  })
  const bullish  = weightedScores.filter(s => s > 0.1).length
  const bearish  = weightedScores.filter(s => s < -0.1).length
  const totalW   = MODULE_KEYS.length

  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-3">
      <div className="flex items-center justify-between mb-3">
        <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
          Module Scores
          <span className="ml-2 text-accent-green">{bullish}↑</span>
          <span className="ml-1 text-accent-red">{bearish}↓</span>
          <span className="ml-1 text-text-tertiary/60">/ {totalW}</span>
        </div>
        {/* Weight editor toggle */}
        <button
          onClick={() => setEditing(v => !v)}
          title="Adjust module weights"
          className={clsx(
            'font-mono text-[9px] px-1.5 py-0.5 rounded border transition-colors',
            editing
              ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/40'
              : 'text-text-tertiary border-border-subtle hover:text-text-secondary hover:border-border-active'
          )}
        >
          {editing ? '× weights' : '⚙ weights'}
        </button>
      </div>

      {/* Score tiles */}
      <div className="flex gap-1.5 flex-wrap">
        {MODULE_KEYS.map(({ key, label }) => {
          const score = modules[key] ?? null
          const weighted = score !== null ? score * (weights[key] ?? 1.0) : null
          const color = weighted !== null ? getModuleColor(weighted) : '#27272a'
          const desc = MODULE_DESCRIPTIONS[key] ?? key
          return (
            <div
              key={key}
              className="flex flex-col items-center gap-1"
              title={`${label}: ${desc}`}
            >
              <div
                style={{
                  width: 40, height: 40,
                  background: color, borderRadius: 4,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  cursor: 'help',
                  opacity: (weights[key] ?? 1.0) < 0.5 ? 0.5 : 1,
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

      {/* Weight sliders — shown when editing */}
      {editing && (
        <div className="mt-3 pt-3 border-t border-border-subtle space-y-2">
          <div className="font-mono text-[9px] text-text-tertiary mb-1">
            Drag to adjust weight (stored locally). Affects weighted agreement count only — does not re-run AI.
          </div>
          {MODULE_KEYS.map(({ key, label }) => (
            <div key={key} className="flex items-center gap-2">
              <span className="font-mono text-[9px] text-text-tertiary w-12 flex-shrink-0">{label}</span>
              <input
                type="range"
                min={0}
                max={2}
                step={0.25}
                value={weights[key] ?? 1.0}
                onChange={e => handleWeightChange(key, parseFloat(e.target.value))}
                className="flex-1 accent-accent-blue"
              />
              <span className="font-mono text-[9px] text-text-secondary w-8 text-right">
                {(weights[key] ?? 1.0).toFixed(2)}×
              </span>
            </div>
          ))}
          <button
            onClick={() => { setWeights({ ...DEFAULT_WEIGHTS }); saveWeights({ ...DEFAULT_WEIGHTS }) }}
            className="font-mono text-[9px] text-text-tertiary hover:text-text-secondary mt-1"
          >
            reset to defaults
          </button>
        </div>
      )}
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
        const fmtPct = (v: number | null | undefined) => v == null ? '—' : (v >= 0 ? `+${v.toFixed(1)}%` : `${v.toFixed(1)}%`)
        const fmtPrice = (v: number | null | undefined) => v == null ? '—' : `$${v.toFixed(2)}`
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

function useRefreshCountdown(updatedAt: number, intervalMs: number) {
  const [secsLeft, setSecsLeft] = useState<number>(0)
  useEffect(() => {
    const tick = () => {
      const elapsed = Date.now() - updatedAt
      const remaining = Math.max(0, Math.ceil((intervalMs - elapsed) / 1000))
      setSecsLeft(remaining)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [updatedAt, intervalMs])
  return secsLeft
}

function ActionZonesCard({ zones, updatedAt }: { zones: ActionZones; updatedAt?: number }) {
  const { eur, pct, rr_t1, rr_t2, atr_pct, rsi, timing, suggested_size_eur, action, action_color, currency, fx_rate } = zones
  const fmtE = (v: number) => `€${v.toFixed(2)}`
  const fmtP = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`

  const REFRESH_MS = 15 * 60 * 1000
  const secsLeft = useRefreshCountdown(updatedAt ?? Date.now(), REFRESH_MS)
  const minsLeft = Math.floor(secsLeft / 60)
  const sLeft    = secsLeft % 60
  const lastRefresh = updatedAt ? new Date(updatedAt) : null

  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Action Zones</div>
        <div className="font-mono text-[9px] text-text-tertiary">
          ATR {fmtE(eur.atr)} ({atr_pct}%) · RSI {rsi != null ? rsi.toFixed(0) : '—'}
          {currency !== 'EUR' && <span className="ml-1 opacity-60">{currency}/{fx_rate}</span>}
        </div>
      </div>

      {/* Refresh status */}
      <div className="flex items-center justify-between border-b border-border-subtle pb-2">
        <div className="font-mono text-[9px] text-text-tertiary">
          {lastRefresh && <>Last refresh: <span className="text-text-secondary">{lastRefresh.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span></>}
        </div>
        <div className="font-mono text-[9px] text-text-tertiary">
          Next in{' '}
          <span className={clsx('tabular-nums', secsLeft < 60 ? 'text-accent-amber' : 'text-text-secondary')}>
            {minsLeft > 0 ? `${minsLeft}m ` : ''}{String(sLeft).padStart(2, '0')}s
          </span>
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
          { label: 'T1',        val: fmtE(eur.t1),        sub: `${fmtP(pct.t1)}  R:R ${rr_t1 != null ? rr_t1.toFixed(1) : '—'}`, color: 'text-accent-green' },
          { label: 'T2',        val: fmtE(eur.t2),        sub: `${fmtP(pct.t2)}  R:R ${rr_t2 != null ? rr_t2.toFixed(1) : '—'}`, color: 'text-accent-green' },
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

// ─── Prompt builder ────────────────────────────────────────────────────────────

function buildPrompt(
  signal: TickerDetail,
  actionZones: ActionZones | null | undefined,
  earningsData: EarningsData | null | undefined,
  dpLatest: any,
): string {
  const n = (v: number | null | undefined, decimals = 2) =>
    v != null ? v.toFixed(decimals) : '—'
  const pct = (v: number | null | undefined) =>
    v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(1)}%` : '—'

  const az = actionZones
  const fx = az?.fx_rate

  const usd = (v: number | null | undefined) => v != null ? `$${v.toFixed(2)}` : '—'
  const eur = (v: number | null | undefined) =>
    v != null && fx ? `€${(v / fx).toFixed(2)}` : v != null ? `$${v.toFixed(2)}` : '—'

  const today = new Date().toISOString().slice(0, 10)

  const lines: string[] = []

  lines.push(`You are an expert quantitative analyst and highly experienced trader with deep knowledge of technical analysis, options flow, dark pool activity, and fundamental valuation. Analyze the following deep dive data for ${signal.ticker} and provide:`)
  lines.push(``)
  lines.push(`1. Critical assessment of the current trade setup — does the technical price action confirm or conflict with the AI thesis?`)
  lines.push(`2. Key entry decision: is now the right time to enter, wait for a pullback, or avoid entirely?`)
  lines.push(`3. Specific price levels to watch — where would you set your entry, stop, and scale-out targets?`)
  lines.push(`4. Risk assessment — what are the top 2-3 things that could make this trade fail?`)
  lines.push(`5. Overall conviction (1–10) and your directional bias with reasoning.`)
  lines.push(``)
  lines.push(`Be direct and specific. No generic advice. Think like a prop trader with real capital at risk.`)
  lines.push(``)
  lines.push(`${'='.repeat(60)}`)
  lines.push(`DEEP DIVE: ${signal.ticker}  |  ${today}`)
  lines.push(`${'='.repeat(60)}`)
  lines.push(``)

  // ── AI Thesis ──
  lines.push(`## AI THESIS`)
  lines.push(`Direction:         ${signal.direction ?? '—'}`)
  lines.push(`Conviction:        ${signal.conviction ?? '—'}/5`)
  lines.push(`Signal Agreement:  ${signal.signal_agreement_score != null ? `${(signal.signal_agreement_score * 100).toFixed(0)}%` : '—'}`)
  if (signal.prob_combined != null) {
    lines.push(``)
    lines.push(`## PROBABILITY ASSESSMENT (pre-computed)`)
    lines.push(`prob_combined:   ${signal.prob_combined.toFixed(3)}  (${signal.data_quality ?? '—'})`)
    lines.push(`  ├─ Technical:  ${signal.prob_technical != null ? signal.prob_technical.toFixed(3) : 'N/A'}`)
    lines.push(`  ├─ Options:    ${signal.prob_options   != null ? signal.prob_options.toFixed(3)   : 'N/A'}`)
    lines.push(`  ├─ Catalyst:   ${signal.prob_catalyst  != null ? signal.prob_catalyst.toFixed(3)  : 'N/A'}`)
    lines.push(`  └─ News:       ${signal.prob_news      != null ? signal.prob_news.toFixed(3)      : 'N/A'}`)
  }
  lines.push(``)
  lines.push(`Time Horizon:      ${signal.time_horizon ?? '—'}`)
  lines.push(`Data Quality:      ${signal.data_quality ?? '—'}`)
  lines.push(`Bull Probability:  ${signal.bull_probability != null ? `${signal.bull_probability}%` : '—'}`)
  lines.push(`Bear Probability:  ${signal.bear_probability != null ? `${signal.bear_probability}%` : '—'}`)
  lines.push(``)
  if (signal.thesis) {
    lines.push(`Thesis:`)
    lines.push(signal.thesis)
    lines.push(``)
  }
  if (signal.primary_scenario) {
    lines.push(`Primary Scenario:`)
    lines.push(signal.primary_scenario)
    lines.push(``)
  }
  if (signal.bear_scenario) {
    lines.push(`Bear Scenario:`)
    lines.push(signal.bear_scenario)
    lines.push(``)
  }
  if (signal.key_invalidation) {
    lines.push(`Key Invalidation: ${signal.key_invalidation}`)
    lines.push(``)
  }
  if (signal.catalysts?.length) {
    lines.push(`Catalysts:`)
    signal.catalysts.forEach(c => lines.push(`  • ${c}`))
    lines.push(``)
  }
  if (signal.risks?.length) {
    lines.push(`Risks:`)
    signal.risks.forEach(r => lines.push(`  • ${r}`))
    lines.push(``)
  }

  // ── AI Price Levels ──
  lines.push(`## AI PRICE LEVELS`)
  lines.push(`Entry Zone:  ${usd(signal.entry_low)} – ${usd(signal.entry_high)}  (${eur(signal.entry_low)} – ${eur(signal.entry_high)})`)
  lines.push(`Target 1:    ${usd(signal.target_1)}  (${eur(signal.target_1)})`)
  lines.push(`Target 2:    ${usd(signal.target_2)}  (${eur(signal.target_2)})`)
  lines.push(`Stop Loss:   ${usd(signal.stop_loss)}  (${eur(signal.stop_loss)})`)
  if (signal.current_price != null && signal.entry_low != null && signal.entry_high != null) {
    const mid = (signal.entry_low + signal.entry_high) / 2
    const drift = ((signal.current_price - mid) / mid * 100).toFixed(1)
    lines.push(`Current vs AI Entry Mid: ${drift}%`)
  }
  lines.push(``)

  // ── Live Action Zones ──
  if (az) {
    lines.push(`## LIVE ACTION ZONES  (ATR-based, refreshed every 15 min)`)
    lines.push(`Current Price:  ${usd(az.current_price)}  (${eur(az.current_price)})`)
    lines.push(`ATR 14:         ${usd(az.atr)}  (${az.atr_pct}%)`)
    lines.push(`RSI 14:         ${n(az.rsi, 1)}`)
    lines.push(`EMA 21:         ${usd(az.ema21)}`)
    lines.push(`EMA 50:         ${usd(az.ema50)}`)
    lines.push(``)
    lines.push(`Buy Zone:       ${usd(az.buy_zone_low)} – ${usd(az.buy_zone_high)}  (${eur(az.buy_zone_low)} – ${eur(az.buy_zone_high)})`)
    lines.push(`Entry Mid:      ${usd(az.entry_mid)}  (${eur(az.entry_mid)})`)
    lines.push(`Stop Loss:      ${usd(az.stop_loss)}  (${eur(az.stop_loss)})`)
    lines.push(`Target 1:       ${usd(az.target_1)}  (${eur(az.target_1)})  R:R ${n(az.rr_t1, 1)}x`)
    lines.push(`Target 2:       ${usd(az.target_2)}  (${eur(az.target_2)})  R:R ${n(az.rr_t2, 1)}x`)
    lines.push(``)
    lines.push(`Action Signal:  ${az.action}`)
    lines.push(`Timing:         ${az.timing}`)
    lines.push(``)
  }

  // ── Module Scores ──
  if (signal.modules && Object.keys(signal.modules).length > 0) {
    lines.push(`## MODULE SCORES  (-1 bearish → +1 bullish)`)
    Object.entries(signal.modules).forEach(([k, v]) => {
      const bar = v != null ? `${v >= 0 ? '+' : ''}${(v as number).toFixed(2)}` : '—'
      lines.push(`  ${k.padEnd(16)} ${bar}`)
    })
    lines.push(``)
  }

  // ── Options Flow ──
  const hasOptions = signal.iv_rank != null || signal.put_call_ratio != null ||
    signal.expected_move_pct != null || signal.heat_score != null
  if (hasOptions) {
    lines.push(`## OPTIONS FLOW`)
    if (signal.iv_rank != null)          lines.push(`IV Rank:        ${signal.iv_rank.toFixed(0)}%`)
    if (signal.heat_score != null)       lines.push(`Heat Score:     ${signal.heat_score.toFixed(0)}/100`)
    if (signal.put_call_ratio != null)   lines.push(`Put/Call Ratio: ${signal.put_call_ratio.toFixed(2)}`)
    if (signal.expected_move_pct != null) lines.push(`Expected Move:  ±${signal.expected_move_pct.toFixed(1)}%`)
    if (signal.poc != null)              lines.push(`POC (Vol Profile): ${usd(signal.poc)}`)
    if (signal.vwap != null)             lines.push(`VWAP 20d:       ${usd(signal.vwap)}`)
    lines.push(``)
  }

  // ── Dark Pool ──
  if (dpLatest) {
    lines.push(`## DARK POOL`)
    if (dpLatest.dark_pool_score != null) lines.push(`Score:          ${dpLatest.dark_pool_score.toFixed(0)}/100`)
    if (dpLatest.signal != null)          lines.push(`Signal:         ${dpLatest.signal}`)
    if (dpLatest.short_ratio_trend != null) lines.push(`Short Ratio Trend: ${dpLatest.short_ratio_trend}`)
    lines.push(``)
  }

  // ── Squeeze ──
  if ((signal.squeeze_score ?? 0) > 0) {
    lines.push(`## SHORT SQUEEZE`)
    lines.push(`Squeeze Score:  ${signal.squeeze_score}/100`)
    if (signal.float_short_pct != null) lines.push(`Float Short:    ${signal.float_short_pct.toFixed(1)}%`)
    if (signal.days_to_cover != null)   lines.push(`Days to Cover:  ${signal.days_to_cover.toFixed(1)}`)
    if (signal.volume_surge != null)    lines.push(`Volume Surge:   ${signal.volume_surge.toFixed(1)}x`)
    lines.push(``)
  }

  // ── Earnings ──
  if (earningsData) {
    lines.push(`## EARNINGS`)
    if (earningsData.next_earnings) {
      const q = earningsData.next_earnings_quarter ? ` (${earningsData.next_earnings_quarter})` : ''
      lines.push(`Next Report:    ${earningsData.next_earnings}${q}`)
    }
    if (earningsData.eps_growth_yoy != null) lines.push(`EPS Growth YoY: ${pct(earningsData.eps_growth_yoy)}`)
    if (earningsData.next_eps?.avg != null)   lines.push(`EPS Estimate:   $${earningsData.next_eps.avg.toFixed(2)}`)
    if (earningsData.next_revenue?.avg != null) {
      const rev = earningsData.next_revenue.avg
      lines.push(`Revenue Est:    $${(rev / 1e9).toFixed(2)}B`)
    }
    const recent = (earningsData.quarterly ?? []).slice(-4)
    if (recent.length > 0) {
      lines.push(``)
      lines.push(`Recent Quarters (oldest → newest):`)
      recent.forEach(q => {
        const beat = q.beat === true ? '✓ beat' : q.beat === false ? '✗ miss' : ''
        const surp = q.surprise_pct != null ? ` (${pct(q.surprise_pct)} surprise)` : ''
        const eps = q.eps_actual != null ? `EPS $${q.eps_actual.toFixed(2)}` : ''
        const est = q.eps_estimate != null ? ` vs est $${q.eps_estimate.toFixed(2)}` : ''
        const rev = q.revenue != null ? `  Rev $${(q.revenue / 1e9).toFixed(2)}B` : ''
        lines.push(`  ${(q.label ?? '').padEnd(8)} ${eps}${est}${surp} ${beat}${rev}`)
      })
    }
    lines.push(``)
  }

  lines.push(`${'='.repeat(60)}`)
  lines.push(`END OF DATA — provide your expert quant trader analysis now.`)

  return lines.join('\n')
}

function CopyPromptButton({
  signal, actionZones, earningsData, dpLatest, compact = false,
}: {
  signal: TickerDetail
  actionZones: ActionZones | null | undefined
  earningsData: EarningsData | null | undefined
  dpLatest: any
  compact?: boolean
}) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    const prompt = buildPrompt(signal, actionZones, earningsData, dpLatest)
    await navigator.clipboard.writeText(prompt)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  // Compact mode: inline button only (used inside AI Thesis card)
  if (compact) {
    return (
      <button
        onClick={handleCopy}
        title="Copy full deep-dive prompt for ChatGPT / Grok / Claude"
        className={clsx(
          'flex items-center gap-1.5 font-mono text-[10px] px-2.5 py-1 rounded border transition-all',
          copied
            ? 'bg-accent-green/20 text-accent-green border-accent-green/40'
            : 'text-text-tertiary border-border-subtle hover:text-text-secondary hover:border-border-active'
        )}
      >
        {copied ? '✓ Copied' : '⎘ Copy Prompt'}
      </button>
    )
  }

  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-2">
      <div className="flex items-center justify-between">
        <div className="space-y-0.5">
          <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
            LLM Prompt
          </div>
          <div className="font-mono text-[9px] text-text-tertiary">
            Full deep dive data formatted for ChatGPT, Grok, Claude, etc.
          </div>
        </div>
        <button
          onClick={handleCopy}
          className={clsx(
            'flex items-center gap-1.5 font-mono text-xs px-3 py-1.5 rounded border transition-all',
            copied
              ? 'bg-accent-green/20 text-accent-green border-accent-green/40'
              : 'bg-bg-elevated text-text-secondary border-border-subtle hover:text-text-primary hover:border-border-active'
          )}
        >
          {copied ? '✓ Copied' : '⎘ Copy Prompt'}
        </button>
      </div>
      {copied && (
        <div className="font-mono text-[9px] text-accent-green/80">
          Paste into ChatGPT, Grok, Claude, or any LLM to get expert quant analysis.
        </div>
      )}
    </div>
  )
}

// ─── Model badge ──────────────────────────────────────────────────────────────

const PREMIUM_MODELS = ['grok-4.20-0309-reasoning', 'grok-4.20-0309-non-reasoning', 'grok-4-0709']

function ModelBadge({ model, cost }: { model: string; cost?: number }) {
  const isPremium = PREMIUM_MODELS.some(m => model.includes('4.20') || model.includes('4-0709'))
  const shortName = model
    .replace('claude-sonnet-', 'Sonnet-')
    .replace('claude-opus-', 'Opus-')
    .replace('claude-haiku-', 'Haiku-')
    .replace('grok-', 'G-')
    .replace('-reasoning', ' ✦')
    .replace('-non-reasoning', '')
    .replace('.20-0309', '.20')
  const costStr = cost != null ? `~$${cost < 0.01 ? cost.toFixed(4) : cost.toFixed(3)}` : null

  return (
    <span
      title={`Model: ${model}${cost != null ? ` | Cost: $${cost.toFixed(4)}` : ''} | ${isPremium ? 'Premium — used for high-conviction setups (≥85% agreement)' : 'Default — daily driver'}`}
      className={clsx(
        'inline-flex items-center gap-1 font-mono text-[9px] border rounded px-1.5 py-0.5 cursor-default',
        isPremium
          ? 'bg-accent-purple/10 border-accent-purple/40 text-accent-purple'
          : 'bg-bg-elevated border-border-subtle text-text-tertiary'
      )}
    >
      {isPremium && <span className="text-accent-purple">★</span>}
      {shortName}
      {costStr && <span className={isPremium ? 'text-accent-purple/70' : 'text-text-tertiary/70'}>{costStr}</span>}
    </span>
  )
}

// ─── Analyze button ────────────────────────────────────────────────────────────

const LLM_OPTIONS = [
  { value: 'grok',         label: 'Grok (fast)',    desc: 'xAI Grok — daily driver' },
  { value: 'grok-premium', label: 'Grok Premium',   desc: 'xAI Grok premium — 3× cost' },
  { value: 'claude',       label: 'Claude Sonnet',  desc: 'Anthropic Claude Sonnet' },
] as const

type LLMChoice = typeof LLM_OPTIONS[number]['value']

function AnalyzeButton({ symbol, hasThesis }: { symbol: string; hasThesis: boolean }) {
  const [job, setJob] = useState<AnalyzeStatus | null>(null)
  const [llm, setLlm] = useState<LLMChoice>('grok')
  const [error, setError] = useState<string | null>(null)
  const qc = useQueryClient()

  // Poll for completion when running — auto-refresh ticker data when done
  const { data: statusData } = useQuery<AnalyzeStatus>({
    queryKey: ['analyze_status', symbol],
    queryFn: () => api.tickerAnalyzeStatus(symbol),
    refetchInterval: job?.status === 'running' ? 5000 : false,
    enabled: job?.status === 'running',
  })

  useEffect(() => {
    if (statusData?.status === 'done' && job?.status === 'running') {
      setJob(statusData)
      qc.invalidateQueries({ queryKey: ['signals', 'ticker', symbol.toUpperCase()] })
      qc.invalidateQueries({ queryKey: ['ticker', symbol.toUpperCase()] })
    }
  }, [statusData?.status])

  const handleRun = async () => {
    setError(null)
    try {
      const res = await api.tickerAnalyze(symbol, llm)
      setJob(res)
    } catch (e: any) {
      const msg = e?.response?.data?.detail ?? e?.message ?? 'Failed to start analysis'
      setError(msg)
      console.error(e)
    }
  }

  const llmLabel = LLM_OPTIONS.find(o => o.value === llm)?.label ?? llm

  if (job?.status === 'running') {
    return (
      <div className="flex items-center gap-2 font-mono text-xs text-accent-amber">
        <span className="animate-pulse">⬤</span> Running AI analysis ({llmLabel}) for {symbol}…
        <span className="text-text-tertiary text-[10px]">auto-refreshes when done (~60s)</span>
      </div>
    )
  }
  if (job?.status === 'done') {
    const doneModel = job.used_model ?? job.estimated_model
    const doneCost  = job.cost_usd ?? job.estimated_cost
    return (
      <div className="flex items-center gap-2 font-mono text-xs text-accent-green">
        ✓ AI analysis complete
        {doneModel && <ModelBadge model={doneModel} cost={doneCost} />}
        {(job as any).calibration && (() => {
          const c = (job as any).calibration
          const model = (c.model ?? '').replace('claude-','Claude ').replace('grok-','Grok ')
          const t1 = c.t1_bias != null ? `T1 ${c.t1_bias > 0 ? '+' : ''}${c.t1_bias}%` : null
          const t2 = c.t2_bias != null ? `T2 ${c.t2_bias > 0 ? '+' : ''}${c.t2_bias}%` : null
          const biases = [t1, t2].filter(Boolean).join(' · ')
          return (
            <span title={`Calibrated using ${c.sample_n} resolved ${model} outcomes`}
              className="font-mono text-[10px] px-1.5 py-0.5 rounded border bg-accent-blue/10 text-accent-blue border-accent-blue/30 cursor-help">
              ⟳ calibrated via {model} ({biases})
            </span>
          )
        })()}
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2">
      {/* LLM picker */}
      <select
        value={llm}
        onChange={e => setLlm(e.target.value as LLMChoice)}
        className="font-mono text-xs px-2 py-1.5 rounded border border-border-subtle bg-bg-surface text-text-secondary hover:border-border-active focus:outline-none cursor-pointer"
        title={LLM_OPTIONS.find(o => o.value === llm)?.desc}
      >
        {LLM_OPTIONS.map(o => (
          <option key={o.value} value={o.value} title={o.desc}>{o.label}</option>
        ))}
      </select>

      {/* Run button */}
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

      {error && <span className="font-mono text-[10px] text-accent-red">{error}</span>}
    </div>
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

// ─── Position Sizer ───────────────────────────────────────────────────────────
// Calculates shares, notional, and dollar risk for a given portfolio risk %.
// Preferred risk % is persisted to localStorage.

const RISK_PCT_KEY = 'signal_engine:position_risk_pct_v1'

function loadRiskPct(): number {
  try {
    const v = localStorage.getItem(RISK_PCT_KEY)
    const n = v != null ? parseFloat(v) : NaN
    return isNaN(n) ? 1.0 : Math.min(5, Math.max(0.1, n))
  } catch {
    return 1.0
  }
}

interface PositionSizerProps {
  entry:              number        // midpoint of entry zone
  stopLoss:           number | null | undefined
  portfolioNav?:      number        // optional total portfolio size (EUR)
  regimeMultiplier?:  number | null // e.g. 0.4 for RISK_OFF, 1.0 for RISK_ON
  regimeLabel?:       string | null // e.g. "RISK_OFF"
}

function PositionSizer({ entry, stopLoss, portfolioNav, regimeMultiplier, regimeLabel }: PositionSizerProps) {
  const [riskPct, setRiskPct] = useState<number>(loadRiskPct)

  if (stopLoss == null || Math.abs(entry - stopLoss) < 0.001) return null

  const stopDist    = Math.abs(entry - stopLoss)
  const stopDistPct = (stopDist / entry) * 100

  // Apply regime multiplier to effective risk %
  const multiplier      = regimeMultiplier != null ? regimeMultiplier : 1.0
  const effectiveRiskPct = riskPct * multiplier

  // With a given portfolio risk %:
  //   dollar_risk = nav * (effective_risk_pct / 100)
  //   shares      = dollar_risk / stop_distance_per_share
  //   notional    = shares * entry
  const dollarRisk = portfolioNav != null
    ? portfolioNav * (effectiveRiskPct / 100)
    : null   // can't compute without NAV

  const shares   = dollarRisk != null ? Math.floor(dollarRisk / stopDist) : null
  const notional = shares != null ? shares * entry : null
  const pctOfNav = notional != null && portfolioNav != null
    ? (notional / portfolioNav) * 100 : null

  const handleChange = (v: number) => {
    const clamped = Math.min(5, Math.max(0.1, v))
    setRiskPct(clamped)
    try { localStorage.setItem(RISK_PCT_KEY, String(clamped)) } catch { /* ignore */ }
  }

  return (
    <div className="mt-3 pt-3 border-t border-border-subtle space-y-2">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
          Position Sizer
        </span>
        <span className="font-mono text-[9px] text-text-tertiary">
          stop dist {stopDistPct.toFixed(1)}%
        </span>
      </div>

      {/* Risk % input */}
      <div className="flex items-center gap-2">
        <span className="font-mono text-[9px] text-text-tertiary w-20 flex-shrink-0">
          Portfolio risk
        </span>
        <input
          type="number"
          min={0.1}
          max={5}
          step={0.1}
          value={riskPct}
          onChange={e => handleChange(parseFloat(e.target.value) || 1)}
          className="w-16 px-2 py-0.5 bg-bg-elevated border border-border-subtle rounded font-mono text-xs text-text-primary focus:outline-none focus:border-border-active text-center"
        />
        <span className="font-mono text-xs text-text-secondary">%</span>
        {/* Quick preset buttons */}
        {[0.5, 1, 2].map(p => (
          <button
            key={p}
            onClick={() => handleChange(p)}
            className={clsx(
              'font-mono text-[9px] px-1.5 py-0.5 rounded border transition-colors',
              riskPct === p
                ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/40'
                : 'text-text-tertiary border-border-subtle hover:text-text-secondary'
            )}
          >
            {p}%
          </button>
        ))}
      </div>

      {/* Regime multiplier line */}
      {regimeMultiplier != null && regimeMultiplier !== 1.0 && (
        <div className="font-mono text-[9px] text-text-tertiary">
          {riskPct.toFixed(1)}% × {regimeMultiplier} {regimeLabel ?? ''} ={' '}
          <span className="text-accent-amber">{effectiveRiskPct.toFixed(2)}% effective</span>
        </div>
      )}

      {/* Output grid */}
      {portfolioNav != null ? (
        <div className="grid grid-cols-4 gap-2">
          {[
            {
              label: 'Dollar Risk',
              value: dollarRisk != null ? `$${dollarRisk.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—',
              color: 'text-accent-red',
            },
            {
              label: 'Shares',
              value: shares != null ? shares.toLocaleString() : '—',
              color: 'text-text-primary',
            },
            {
              label: 'Notional',
              value: notional != null ? `$${notional.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—',
              color: 'text-text-secondary',
            },
            {
              label: '% of NAV',
              value: pctOfNav != null ? `${pctOfNav.toFixed(1)}%` : '—',
              color: pctOfNav != null && pctOfNav > 15
                ? 'text-accent-amber'  // warn if > 15% concentration
                : 'text-text-secondary',
            },
          ].map(({ label, value, color }) => (
            <div key={label} className="space-y-0.5">
              <div className="font-mono text-[9px] text-text-tertiary uppercase">{label}</div>
              <div className={clsx('font-mono text-xs font-semibold', color)}>{value}</div>
            </div>
          ))}
        </div>
      ) : (
        // No NAV — show formula only
        <div className="font-mono text-[9px] text-text-tertiary space-y-0.5">
          <div>Risk per share: <span className="text-text-secondary">${stopDist.toFixed(2)}</span></div>
          <div className="text-text-tertiary/60">
            Connect portfolio NAV to calculate shares and notional.
          </div>
        </div>
      )}
    </div>
  )
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

// ─── Earnings card ─────────────────────────────────────────────────────────────

type EarningsView = '4Q' | '8Q' | '5Y'

function fmtRevenue(v: number | null): string {
  if (v == null) return '—'
  if (Math.abs(v) >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (Math.abs(v) >= 1e9)  return `$${(v / 1e9).toFixed(1)}B`
  if (Math.abs(v) >= 1e6)  return `$${(v / 1e6).toFixed(0)}M`
  return `$${v.toLocaleString()}`
}

function fmtVol(v: number | null): string {
  if (v == null) return '—'
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000)     return `${(v / 1_000).toFixed(0)}K`
  return v.toString()
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
  const { next_earnings, next_earnings_quarter, next_eps, next_revenue, eps_growth_yoy } = data

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
      {next_earnings && (() => {
        const daysToEarnings = (() => {
          try {
            const earningsDate = new Date(next_earnings + 'T00:00:00')
            const today = new Date()
            today.setHours(0, 0, 0, 0)
            return Math.round((earningsDate.getTime() - today.getTime()) / 86400000)
          } catch { return null }
        })()
        const daysColor = daysToEarnings != null
          ? daysToEarnings <= 7  ? 'text-accent-red'
          : daysToEarnings <= 21 ? 'text-accent-amber'
          : 'text-text-tertiary'
          : 'text-text-tertiary'
        return (
        <div className="bg-accent-amber/10 border border-accent-amber/30 rounded px-3 py-2 space-y-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-[9px] uppercase text-accent-amber tracking-wide">Next</span>
            <span className="font-mono text-sm font-semibold text-accent-amber">{next_earnings}</span>
            {daysToEarnings != null && (
              <span className={clsx('font-mono text-xs font-semibold', daysColor)}>
                · {daysToEarnings > 0 ? `${daysToEarnings}d` : daysToEarnings === 0 ? 'today' : `${Math.abs(daysToEarnings)}d ago`}
              </span>
            )}
            {next_earnings_quarter && (
              <span className="font-mono text-xs text-accent-amber/70 border border-accent-amber/30 rounded px-1.5 py-0.5">
                {next_earnings_quarter}
              </span>
            )}
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
        )
      })()}

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
  const { data: secFilings = [] } = useQuery<SecFiling[]>({
    queryKey: ['sec_filings', symbol],
    queryFn: () => api.tickerSecFilings(symbol),
    staleTime: 6 * 60 * 60 * 1000,
    enabled: !!symbol,
  })
  const { data: earningsData } = useQuery<EarningsData | null>({
    queryKey: ['earnings', symbol],
    queryFn: () => api.tickerEarnings(symbol),
    staleTime: 4 * 60 * 60 * 1000,
    enabled: !!symbol,
  })
  const { data: actionZones, dataUpdatedAt: actionZonesUpdatedAt } = useQuery<ActionZones | null>({
    queryKey: ['action_zones', symbol],
    queryFn: () => api.tickerActionZones(symbol),
    staleTime:       15 * 60 * 1000,
    refetchInterval: 15 * 60 * 1000,
    enabled: !!symbol,
  })
  const { data: regimeData } = useQuery<RegimeCurrent | null>({
    queryKey: ['regime_current'],
    queryFn: () => api.regimeCurrent(),
    staleTime: TTL_LONG_MS,
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
            {signal.regime && (
              <span title={regimeData?.vix != null ? `${signal.regime} · VIX ${regimeData.vix.toFixed(1)}` : signal.regime}>
                <RegimeBadge regime={signal.regime} size="sm" />
              </span>
            )}
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

              {/* Position Sizer — inline in header card, below the R:R bar */}
              {(() => {
                const entry =
                  signal.entry_low != null && signal.entry_high != null
                    ? (signal.entry_low + signal.entry_high) / 2
                    : signal.entry_low ?? signal.entry_high ?? signal.current_price
                return entry != null ? (
                  <PositionSizer
                    entry={entry}
                    stopLoss={signal.stop_loss}
                    // TODO: wire portfolioNav from /api/portfolio/summary when needed
                    portfolioNav={undefined}
                    regimeMultiplier={regimeData?.size_multiplier}
                    regimeLabel={regimeData?.regime}
                  />
                ) : null
              })()}
            </div>

            {/* AI Thesis card — action-first layout */}
            {(() => {
              const action = deriveAction(signal)
              const thesis = signal.thesis || signal.ai_synthesis
              return (
                <div className="bg-bg-elevated border-l-2 border-accent-purple rounded-r p-4 space-y-3">
                  {/* Header */}
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">AI Thesis</span>
                      {signal.model_used && (
                        <ModelBadge model={signal.model_used} cost={signal.cost_usd} />
                      )}
                    </div>
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

                  {/* prob_combined card — calibrated multi-factor probability */}
                  {signal.prob_combined != null && (
                    <div className="bg-bg-elevated rounded p-3 space-y-2 border border-border-subtle/50">
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                          P(combined)
                        </span>
                        <span className={clsx(
                          'font-mono text-sm font-semibold',
                          signal.prob_combined >= 0.65 ? 'text-accent-green'
                            : signal.prob_combined >= 0.55 ? 'text-accent-amber'
                            : 'text-text-tertiary'
                        )}>
                          {(signal.prob_combined * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div className="h-1.5 bg-bg-surface rounded-full overflow-hidden">
                        <div
                          className={clsx(
                            'h-full rounded-full transition-all',
                            signal.prob_combined >= 0.65 ? 'bg-accent-green'
                              : signal.prob_combined >= 0.55 ? 'bg-accent-amber'
                              : 'bg-text-tertiary/50'
                          )}
                          style={{ width: `${(signal.prob_combined * 100).toFixed(1)}%` }}
                        />
                      </div>
                      <div className="flex gap-2 font-mono text-[10px] text-text-tertiary flex-wrap">
                        {signal.prob_technical != null && (
                          <span>Tech {(signal.prob_technical * 100).toFixed(0)}%</span>
                        )}
                        {signal.prob_options != null && (
                          <span>· Opts {(signal.prob_options * 100).toFixed(0)}%</span>
                        )}
                        {signal.prob_catalyst != null && (
                          <span>· Cat {(signal.prob_catalyst * 100).toFixed(0)}%</span>
                        )}
                        {signal.prob_news != null && (
                          <span>· News {(signal.prob_news * 100).toFixed(0)}%</span>
                        )}
                      </div>
                    </div>
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

                  {/* Copy Prompt — inline at bottom of AI Thesis card */}
                  <div className="border-t border-border-subtle pt-2 flex justify-end">
                    <CopyPromptButton
                      signal={signal}
                      actionZones={actionZones}
                      earningsData={earningsData}
                      dpLatest={dpLatest}
                      compact
                    />
                  </div>
                </div>
              )
            })()}

            {/* Price Ladder */}
            {signal.current_price != null && (() => {
              const aiMid = signal.entry_low != null && signal.entry_high != null
                ? (signal.entry_low + signal.entry_high) / 2 : null
              const thesisDate = signal.as_of ? new Date(signal.as_of) : null
              const daysSince = thesisDate
                ? Math.floor((Date.now() - thesisDate.getTime()) / 86400000) : null
              const priceDrift = aiMid && signal.current_price
                ? ((signal.current_price - aiMid) / aiMid * 100) : null
              const isStale = (daysSince ?? 0) > 7 && Math.abs(priceDrift ?? 0) > 10

              return (
                <div className="bg-bg-surface border border-border-subtle rounded p-4">
                  <div className="flex items-center justify-between mb-3">
                    <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                      Price Levels
                    </div>
                    {daysSince != null && (
                      <div className="font-mono text-[9px] text-text-tertiary">
                        thesis {daysSince}d ago
                      </div>
                    )}
                  </div>
                  {isStale && (
                    <div className="mb-3 flex items-start gap-2 bg-accent-amber/10 border border-accent-amber/30 rounded px-2.5 py-2">
                      <span className="text-accent-amber text-xs mt-0.5">⚠</span>
                      <div className="font-mono text-[10px] text-accent-amber leading-relaxed">
                        AI levels are {daysSince}d old — price has moved{' '}
                        <span className="font-semibold">{priceDrift! >= 0 ? '+' : ''}{priceDrift!.toFixed(1)}%</span>{' '}
                        from AI entry mid (${aiMid!.toFixed(2)}). Re-run analysis for fresh levels.
                      </div>
                    </div>
                  )}
                  {/* Legend */}
                  {actionZones && signal.entry_low != null && (
                    <div className="flex items-center gap-4 mb-2">
                      <span className="flex items-center gap-1 font-mono text-[9px] text-[#3b82f6]">
                        <span className="inline-block w-3 h-2 rounded-sm bg-[#3b82f620] border border-[#3b82f640]" />
                        AI entry
                      </span>
                      <span className="flex items-center gap-1 font-mono text-[9px] text-[#f59e0b]">
                        <span className="inline-block w-3 h-2 rounded-sm bg-[#f59e0b18] border border-[#f59e0b50]" />
                        Live zone
                      </span>
                    </div>
                  )}
                  <PriceLadder
                    currentPrice={signal.current_price}
                    target1={signal.target_1}
                    target2={signal.target_2}
                    entryLow={signal.entry_low}
                    entryHigh={signal.entry_high}
                    stopLoss={signal.stop_loss}
                    poc={signal.poc}
                    vwap={signal.vwap}
                    azBuyLow={actionZones?.buy_zone_low}
                    azBuyHigh={actionZones?.buy_zone_high}
                    azStop={actionZones?.stop_loss}
                    azTarget1={actionZones?.target_1}
                    azTarget2={actionZones?.target_2}
                    fxRate={actionZones?.fx_rate}
                  />
                </div>
              )
            })()}

            {/* Interactive candlestick chart with all key level overlays */}
            {signal.current_price != null && (
              <PriceChart
                symbol={symbol}
                aiEntryLow={signal.entry_low}
                aiEntryHigh={signal.entry_high}
                aiTarget1={signal.target_1}
                aiTarget2={signal.target_2}
                aiStop={signal.stop_loss}
                vwap={signal.vwap}
                azBuyLow={actionZones?.buy_zone_low}
                azBuyHigh={actionZones?.buy_zone_high}
                azTarget1={actionZones?.target_1}
                azTarget2={actionZones?.target_2}
                azStop={actionZones?.stop_loss}
                currentPrice={signal.current_price}
              />
            )}

            {/* Historical Analogs — similar past setups with win rate / expectancy stats */}
            <HistoricalAnalogs symbol={symbol} />

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

            {/* Module mini-heatmap — collapsed by default */}
            {signal.modules && Object.keys(signal.modules).length > 0 && (
              <Accordion.Root type="single" collapsible>
                <Accordion.Item value="modules" className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
                  <Accordion.Trigger className="group w-full flex items-center justify-between px-3 py-2.5 font-mono text-xs text-text-secondary hover:text-text-primary transition-colors">
                    <span className="uppercase tracking-widest text-[10px] text-text-tertiary">
                      Module Scores
                    </span>
                    <ChevronRight
                      size={12}
                      className="transition-transform group-data-[state=open]:rotate-90 text-text-tertiary"
                    />
                  </Accordion.Trigger>
                  <Accordion.Content className="px-3 pb-3 data-[state=open]:animate-none">
                    <ModuleMiniHeatmap modules={signal.modules} />
                  </Accordion.Content>
                </Accordion.Item>
              </Accordion.Root>
            )}
          </div>

          {/* ── RIGHT COLUMN ── */}
          <div className="space-y-4 min-w-0">
            {/* Conflict flags — shown when AI thesis and live price action disagree */}
            {(() => {
              const flags: { text: string; severity: 'red' | 'amber' }[] = []
              const { direction, stop_loss, entry_low, entry_high, current_price } = signal

              // Hard: price has blown through AI stop loss
              if (direction === 'BULL' && stop_loss != null && current_price != null && current_price < stop_loss)
                flags.push({ text: `Price $${current_price.toFixed(2)} is below AI stop $${stop_loss.toFixed(2)} — thesis invalidated by price action`, severity: 'red' })
              if (direction === 'BEAR' && stop_loss != null && current_price != null && current_price > stop_loss)
                flags.push({ text: `Price $${current_price.toFixed(2)} is above AI stop $${stop_loss.toFixed(2)} — bear thesis invalidated`, severity: 'red' })

              // Soft: live buy zone and AI entry zone diverge significantly
              const aiMid = entry_low != null && entry_high != null ? (entry_low + entry_high) / 2 : null
              const azMid = actionZones?.entry_mid
              if (aiMid != null && azMid != null) {
                const driftPct = (azMid - aiMid) / aiMid * 100
                if (Math.abs(driftPct) > 15) {
                  const dir = driftPct > 0 ? 'above' : 'below'
                  flags.push({ text: `Live buy zone ($${azMid.toFixed(2)}) is ${Math.abs(driftPct).toFixed(0)}% ${dir} AI entry ($${aiMid.toFixed(2)}) — re-run analysis for current levels`, severity: 'amber' })
                }
              }

              if (flags.length === 0) return null
              return (
                <div className="space-y-2">
                  {flags.map((f, i) => (
                    <div key={i} className={clsx(
                      'flex items-start gap-2 rounded px-2.5 py-2 border font-mono text-[10px] leading-relaxed',
                      f.severity === 'red'
                        ? 'bg-accent-red/10 border-accent-red/30 text-accent-red'
                        : 'bg-accent-amber/10 border-accent-amber/30 text-accent-amber'
                    )}>
                      <span className="mt-0.5 flex-shrink-0">{f.severity === 'red' ? '✕' : '⚠'}</span>
                      {f.text}
                    </div>
                  ))}
                </div>
              )
            })()}

            {/* Action Zones */}
            {actionZones && <ActionZonesCard zones={actionZones} updatedAt={actionZonesUpdatedAt} />}

            {/* Override flags */}
            {!!signal.override_flags?.length && (
              <OverrideCard flags={signal.override_flags} />
            )}

            {/* Squeeze details */}
            {(signal.squeeze_score ?? 0) > 30 && (
              <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                    Short Squeeze
                  </div>
                  {signal.squeeze_score != null && (
                    <div className="flex items-center gap-1">
                      <span className="font-mono text-[9px] text-text-tertiary">Score</span>
                      <span className={clsx(
                        'font-mono text-xs font-semibold',
                        signal.squeeze_score > 60 ? 'text-accent-green'
                          : signal.squeeze_score >= 40 ? 'text-accent-amber'
                          : 'text-text-tertiary'
                      )}>
                        {signal.squeeze_score.toFixed(0)}
                      </span>
                      <span className="font-mono text-[9px] text-text-tertiary">/ 100</span>
                    </div>
                  )}
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
                {signal.adv_20d != null && (
                  <div className="flex items-center justify-between font-mono text-xs text-text-tertiary">
                    <span>ADV 20d</span>
                    <span className="text-text-secondary">{fmtVol(signal.adv_20d)}</span>
                  </div>
                )}
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
            {(signal.heat_score != null || signal.iv_rank != null || signal.expected_move_pct != null || signal.put_call_ratio != null) && (
              <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-2">
                <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
                  Options Flow
                </div>
                {/* Heat score — lead metric */}
                {signal.heat_score != null && (
                  <div className="flex items-center justify-between pb-2 border-b border-border-subtle">
                    <span className="font-mono text-[10px] text-text-tertiary uppercase">Heat</span>
                    <div className="flex items-center gap-2">
                      <span className={clsx(
                        'font-mono text-sm font-semibold',
                        signal.heat_score > 60 ? 'text-accent-green'
                          : signal.heat_score >= 40 ? 'text-text-secondary'
                          : 'text-accent-red'
                      )}>
                        {signal.heat_score.toFixed(0)}
                      </span>
                      <span className="font-mono text-[10px] text-text-tertiary">/ 100</span>
                    </div>
                  </div>
                )}
                <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                  {/* IV Rank with Low/Normal/High label */}
                  {signal.iv_rank != null && (
                    <div className="flex justify-between">
                      <span className="font-mono text-[10px] text-text-tertiary uppercase">IV Rank</span>
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-xs text-text-primary">{signal.iv_rank.toFixed(0)}%</span>
                        <span className={clsx(
                          'font-mono text-[9px]',
                          signal.iv_rank > 75 ? 'text-accent-amber'
                            : signal.iv_rank >= 25 ? 'text-text-tertiary'
                            : 'text-accent-green'
                        )}>
                          {signal.iv_rank > 75 ? 'High' : signal.iv_rank >= 25 ? 'Normal' : 'Low'}
                        </span>
                      </div>
                    </div>
                  )}
                  {signal.expected_move_pct != null && (
                    <div className="flex justify-between">
                      <span className="font-mono text-[10px] text-text-tertiary uppercase">Exp Move</span>
                      <span className="font-mono text-xs text-text-primary">±{signal.expected_move_pct.toFixed(1)}%</span>
                    </div>
                  )}
                  {signal.put_call_ratio != null && (
                    <div className="flex justify-between">
                      <span className="font-mono text-[10px] text-text-tertiary uppercase">P/C Ratio</span>
                      <span className="font-mono text-xs text-text-primary">{signal.put_call_ratio.toFixed(2)}</span>
                    </div>
                  )}
                </div>
                {/* Max pain */}
                {signal.max_pain_strike != null && (
                  <div className="pt-2 border-t border-border-subtle space-y-1">
                    <div className="flex justify-between items-center">
                      <span className="font-mono text-[10px] text-text-tertiary uppercase">Max Pain</span>
                      <div className="flex items-center gap-1.5 font-mono text-xs">
                        <span className="text-text-primary font-semibold">${signal.max_pain_strike.toFixed(2)}</span>
                        {signal.max_pain_distance_pct != null && (
                          <span className={clsx(
                            signal.max_pain_distance_pct > 0 ? 'text-accent-green' : 'text-accent-red'
                          )}>
                            ({signal.max_pain_distance_pct > 0 ? '+' : ''}{signal.max_pain_distance_pct.toFixed(1)}%)
                          </span>
                        )}
                      </div>
                    </div>
                    {signal.max_pain_expiry != null && (
                      <div className="flex justify-between font-mono text-[9px] text-text-tertiary">
                        <span>OpEx {signal.max_pain_expiry}</span>
                        {signal.max_pain_days_to_expiry != null && (
                          <span>{signal.max_pain_days_to_expiry}d</span>
                        )}
                      </div>
                    )}
                  </div>
                )}
                <div className="font-mono text-[9px] text-text-tertiary">
                  iv_source: {signal.iv_source ?? 'unknown'}
                  {signal.iv_history_days != null && signal.iv_history_days > 0 && (
                    <span className="ml-2">· {signal.iv_history_days} snapshots</span>
                  )}
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

            {/* Catalysts & Risks */}
            <CatalystsAccordion catalysts={signal.catalysts} risks={signal.risks} />

            {/* Earnings */}
            {earningsData && (earningsData.quarterly?.length || earningsData.annual?.length || earningsData.next_earnings) && (
              <EarningsCard data={earningsData} />
            )}

            {/* Earnings Reaction Model — historical move distribution + implied vs actual */}
            <EarningsReactionModel
              symbol={symbol}
              earningsData={earningsData}
              impliedMove={signal.expected_move_pct}
            />

            {/* Analyst consensus */}
            {signal.target_mean != null && (
              <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-2">
                <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                  Analyst View
                </div>
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[10px] text-text-tertiary uppercase">Street Target</span>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-semibold text-text-primary">
                      ${signal.target_mean.toFixed(2)}
                    </span>
                    {signal.current_price != null && (
                      <span className={clsx(
                        'font-mono text-xs',
                        signal.target_mean > signal.current_price ? 'text-accent-green' : 'text-accent-red'
                      )}>
                        ({signal.target_mean > signal.current_price ? '+' : ''}
                        {(((signal.target_mean - signal.current_price) / signal.current_price) * 100).toFixed(1)}%)
                      </span>
                    )}
                  </div>
                </div>
                {signal.analyst_count != null && (
                  <div className="font-mono text-[9px] text-text-tertiary">
                    {signal.analyst_count} analyst{signal.analyst_count !== 1 ? 's' : ''}
                    {signal.analyst_rating != null && (
                      <span className="ml-2">· rating {signal.analyst_rating.toFixed(1)}/5</span>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* SEC Filings */}
            <SecFilingsCard filings={secFilings} />
          </div>
        </div>
      )}
    </Shell>
  )
}
