import { useState, useMemo } from 'react'
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
import { api } from '../lib/api'
import type { MaxPainData } from '../lib/api'
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

interface ExpectedMove {
  horizon: string
  bear_pct: number
  base_pct: number
  bull_pct: number
  bear_price: number
  base_price: number
  bull_price: number
  bull_prob: number
  bear_prob: number
  neutral_prob: number
}

const HORIZON_LABEL: Record<string, string> = {
  today: 'Today',
  week:  'This Week',
  month: 'This Month',
  year:  'This Year',
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
  const dirColor = nearest?.direction === 'UP' ? '#22c55e' : nearest?.direction === 'DOWN' ? '#ef4444' : '#a1a1aa'
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

// ─── Page ──────────────────────────────────────────────────────────────────────

export function TickerPage() {
  const { symbol = '' } = useParams()
  const { data: signal, isLoading } = useSignalsTicker(symbol)
  const { data: dpHistory } = useDarkPoolTicker(symbol)
  const { data: heatmapRows } = useHeatmap()
  const { data: maxPainLive } = useQuery({
    queryKey: ['max_pain', symbol],
    queryFn: () => api.maxPainLive(symbol),
    staleTime: 60 * 60 * 1000, // 1h — matches server TTL
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
            </div>

            {/* AI Thesis card */}
            <div className="bg-bg-elevated border-l-2 border-accent-purple rounded-r p-4 space-y-3">
              <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                AI Thesis
              </div>
              {signal.thesis ? (
                <p className="text-sm text-text-secondary leading-[1.7]">{signal.thesis}</p>
              ) : signal.ai_synthesis ? (
                <p className="text-sm text-text-secondary leading-[1.7]">{signal.ai_synthesis}</p>
              ) : (
                <p className="text-sm text-text-tertiary italic">No AI thesis available</p>
              )}
              {signal.primary_scenario && (
                <div className="font-mono text-xs text-accent-green">
                  ▲ {signal.primary_scenario}
                </div>
              )}
              {signal.bear_scenario && (
                <div className="font-mono text-xs text-accent-red">
                  ▼ {signal.bear_scenario}
                </div>
              )}
              {signal.key_invalidation && (
                <div className="font-mono text-xs text-accent-amber">
                  ⚡ {signal.key_invalidation}
                </div>
              )}
              {(signal.bull_probability !== undefined || signal.bear_probability !== undefined) && (
                <ProbBar
                  bull={signal.bull_probability}
                  bear={signal.bear_probability}
                  neutral={signal.neutral_probability}
                />
              )}
              <div className="font-mono text-[28px] font-semibold text-accent-purple">
                {Math.round(signal.signal_agreement_score * 100)}%
                <span className="text-sm font-normal text-text-tertiary ml-2">agreement</span>
              </div>
            </div>

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
            {signal.expected_moves?.length > 0 && signal.current_price != null && (
              <ExpectedMovesTable
                moves={signal.expected_moves}
                currentPrice={signal.current_price}
              />
            )}

            {/* Module mini-heatmap */}
            {signal.modules && Object.keys(signal.modules).length > 0 && (
              <ModuleMiniHeatmap modules={signal.modules} />
            )}
          </div>

          {/* ── RIGHT COLUMN ── */}
          <div className="space-y-4 min-w-0">
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
            {(signal.heat_score ?? 0) > 40 && (
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
          </div>
        </div>
      )}
    </Shell>
  )
}
