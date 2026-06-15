import { useState, useMemo, useCallback, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Shell } from '../components/layout/Shell'
import { DirectionBadge } from '../components/ui/DirectionBadge'
import { ConvictionDots } from '../components/ui/ConvictionDots'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { api, type AnalyzeStatus, fetchHedgeFunds, fetchHedgeFundPositions } from '../lib/api'
import { clsx } from 'clsx'

interface DeepDiveTicker {
  ticker: string
  has_thesis: boolean
  name: string
  sector: string
  current_price: number | null
  price_source: 'live' | 'close' | null  // live = prepost 1-min bar, close = last daily bar
  date: string | null
  created_at: string | null
  direction: string | null
  conviction: number | null
  signal_agreement_score: number | null
  time_horizon: string | null
  data_quality: string | null
  thesis_short: string | null
  bull_probability: number | null
  bear_probability: number | null
  prob_combined: number | null
  entry_low: number | null
  entry_high: number | null
  target_1: number | null
  target_2: number | null
  stop_loss: number | null
}

/** Returns 'PM', 'AH', or null based on US Eastern time. */
function priceSessionLabel(): 'PM' | 'AH' | null {
  const etOffset = -5 // EST; DST handled approximately
  const utcH = new Date().getUTCHours() + new Date().getUTCMinutes() / 60
  const etH = ((utcH + etOffset) % 24 + 24) % 24
  if (etH >= 4 && etH < 9.5)  return 'PM'
  if (etH >= 16 && etH < 20)  return 'AH'
  return null
}

type DirectionFilter = 'ALL' | 'BULL' | 'BEAR' | 'NEUTRAL' | 'ANALYZED' | 'HIGH_RR' | 'IN_ENTRY' | 'ZONE_OVERLAP' | 'IDEAL_BEAR'
type DeepDivePreset = 'NONE' | 'PM_REGIME'
const LLM_OPTIONS = [
  { value: 'grok-4.3',          label: 'Grok 4.3',           desc: 'xAI Grok 4.3' },
  { value: 'grok-4.20',         label: 'Grok 4.20',          desc: 'xAI Grok 4.20' },
  { value: 'gpt-5.1',           label: 'GPT-5.1',            desc: 'OpenAI GPT-5.1' },
  { value: 'gpt-5.5',           label: 'GPT-5.5',            desc: 'OpenAI GPT-5.5' },
  { value: 'gpt-5.5-pro',       label: 'GPT-5.5 Pro',        desc: 'OpenAI GPT-5.5 Pro' },
  { value: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6',  desc: 'Anthropic Claude Sonnet 4.6' },
  { value: 'claude-opus-4-8',   label: 'Claude Opus 4.8',    desc: 'Anthropic Claude Opus 4.8' },
] as const
type LLMChoice = typeof LLM_OPTIONS[number]['value']

interface LiveZoneMap {
  [ticker: string]: { buy_zone_low: number; buy_zone_high: number }
}
type SortMode = 'direction' | 'rr' | 't1'

function fmtDateTime(iso: string | null): { date: string; time: string } | null {
  if (!iso) return null
  const d = new Date(iso)
  if (isNaN(d.getTime())) return null
  return {
    date: d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }),
    time: d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
  }
}

function computeRR(t: DeepDiveTicker): number | null {
  const entry =
    t.entry_low != null && t.entry_high != null
      ? (t.entry_low + t.entry_high) / 2
      : t.entry_low ?? t.entry_high
  if (entry == null || t.target_1 == null || t.stop_loss == null) return null
  const t1p = ((t.target_1 - entry) / entry) * 100
  const sp  = ((t.stop_loss - entry) / entry) * 100
  if (Math.abs(sp) === 0) return null
  return Math.abs(t1p / sp)
}

function useDeepDiveTickers() {
  return useQuery({
    queryKey: ['deepdive', 'tickers'],
    queryFn: () => api.deepdiveTickers<DeepDiveTicker>(),
    staleTime: 2 * 60 * 1000, // 2-min stale — server caches live prices for 5 min
  })
}

function useDeepDiveLiveZones() {
  return useQuery({
    queryKey: ['deepdive', 'live-zones'],
    queryFn: () => api.deepdiveLiveZones<LiveZoneMap>(),
    staleTime: 5 * 60 * 1000,
  })
}

function useOpenPositionTickers() {
  return useQuery({
    queryKey: ['portfolio', 'positions'],
    queryFn: () =>
      api.portfolioPositions().then(rows =>
        Array.from(new Set(rows.map((r: { ticker: string }) => r.ticker)))
      ) as Promise<string[]>,
    staleTime: 5 * 60 * 1000,
  })
}

function useBlacklist() {
  const queryClient = useQueryClient()
  const { data: blacklistData } = useQuery({
    queryKey: ['blacklist'],
    queryFn: () => api.blacklistGet(),
    staleTime: 5 * 60 * 1000,
  })
  const blacklistSet = useMemo(
    () => new Set((blacklistData ?? []).map(b => b.ticker)),
    [blacklistData],
  )
  const toggle = useCallback(async (ticker: string) => {
    try {
      if (blacklistSet.has(ticker)) {
        await api.blacklistRemove(ticker)
      } else {
        await api.blacklistAdd(ticker)
      }
    } catch (err) {
      console.error(`[blacklist] ${ticker} failed:`, err)
      throw err
    } finally {
      queryClient.invalidateQueries({ queryKey: ['blacklist'] })
      queryClient.invalidateQueries({ queryKey: ['deepdive'] })
    }
  }, [blacklistSet, queryClient])
  return { blacklistSet, toggle }
}

function useHedgeFundFilter(slug: string | null) {
  const funds = useQuery({
    queryKey: ['hedge-funds'],
    queryFn: fetchHedgeFunds,
    staleTime: 10 * 60 * 1000,
  })
  const positions = useQuery({
    queryKey: ['hedge-fund-positions', slug],
    queryFn: () => fetchHedgeFundPositions(slug!, {}),
    enabled: slug != null,
    staleTime: 10 * 60 * 1000,
  })
  const tickerSet = useMemo(() => {
    if (!slug || !positions.data) return null
    const tickers = positions.data.positions
      .filter(p => p.ticker && (!p.put_call) && p.change_type !== 'closed')
      .map(p => p.ticker as string)
    return new Set(tickers)
  }, [slug, positions.data])

  return { funds: funds.data ?? [], tickerSet, loadingPositions: positions.isLoading }
}

function BlacklistButton({
  ticker,
  isBlacklisted,
  onToggle,
  className = '',
}: {
  ticker: string
  isBlacklisted: boolean
  onToggle: (ticker: string) => Promise<void>
  className?: string
}) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(false)

  const handleClick = async (e: React.MouseEvent) => {
    e.stopPropagation()
    setPending(true)
    setError(false)
    try {
      await onToggle(ticker)
    } catch {
      setError(true)
      setTimeout(() => setError(false), 2000)
    } finally {
      setPending(false)
    }
  }

  return (
    <button
      onClick={handleClick}
      disabled={pending}
      title={isBlacklisted ? 'Remove from blacklist' : 'Blacklist — skip AI refresh'}
      className={clsx(
        'opacity-0 group-hover:opacity-100 transition-opacity font-mono text-[10px] px-2 py-0.5 rounded border flex-shrink-0',
        pending && 'opacity-50 cursor-wait',
        error
          ? 'border-accent-red text-accent-red opacity-100'
          : isBlacklisted
            ? 'border-accent-green/40 text-accent-green/70 hover:text-accent-green hover:border-accent-green/70'
            : 'border-accent-red/30 text-accent-red/60 hover:text-accent-red hover:border-accent-red/60',
        className,
      )}
    >
      {pending ? '…' : error ? 'err' : isBlacklisted ? 'unban' : '⊘'}
    </button>
  )
}

// Pure comparators — no tiebreak, so they can be chained
const DIRECTION_ORDER: Record<string, number> = { BULL: 0, NEUTRAL: 1, BEAR: 2 }
function cmpDirection(a: DeepDiveTicker, b: DeepDiveTicker): number {
  const da = DIRECTION_ORDER[a.direction ?? 'NEUTRAL'] ?? 1
  const db = DIRECTION_ORDER[b.direction ?? 'NEUTRAL'] ?? 1
  return da - db
}
function cmpRR(a: DeepDiveTicker, b: DeepDiveTicker): number {
  const ra = computeRR(a)
  const rb = computeRR(b)
  if (ra === null && rb === null) return 0
  if (ra === null) return 1
  if (rb === null) return -1
  return rb - ra
}
function t1Pct(t: DeepDiveTicker): number | null {
  if (t.target_1 == null) return null
  const entry = t.entry_low != null && t.entry_high != null
    ? (t.entry_low + t.entry_high) / 2
    : t.entry_low ?? t.entry_high
  if (entry == null || entry === 0) return null
  return (t.target_1 - entry) / entry
}
function cmpT1(a: DeepDiveTicker, b: DeepDiveTicker): number {
  const pa = t1Pct(a) ?? -Infinity
  const pb = t1Pct(b) ?? -Infinity
  return pb - pa
}
const CMP: Record<SortMode, (a: DeepDiveTicker, b: DeepDiveTicker) => number> = {
  direction: cmpDirection, rr: cmpRR, t1: cmpT1,
}
function buildSorter(modes: SortMode[]) {
  return (a: DeepDiveTicker, b: DeepDiveTicker): number => {
    for (const mode of modes) {
      const r = CMP[mode](a, b)
      if (r !== 0) return r
    }
    return (b.conviction ?? 0) - (a.conviction ?? 0)
  }
}

const IDEAL_BEAR_HORIZONS = new Set(['1-2 weeks', '2-4 weeks'])
function isIdealBear(t: DeepDiveTicker): boolean {
  return (
    t.direction === 'BEAR' &&
    (t.conviction ?? 0) >= 3 &&
    (t.bear_probability ?? 0) >= 0.50 &&
    IDEAL_BEAR_HORIZONS.has(t.time_horizon ?? '')
  )
}

function isInAiEntryZone(t: DeepDiveTicker): boolean {
  if (t.current_price == null || t.entry_low == null || t.entry_high == null) return false
  return t.current_price >= t.entry_low && t.current_price <= t.entry_high
}

function hasZoneOverlap(t: DeepDiveTicker, liveZones: LiveZoneMap): boolean {
  if (t.current_price == null || t.entry_low == null || t.entry_high == null) return false
  const lz = liveZones[t.ticker]
  if (!lz) return false
  // price must be inside BOTH the AI entry zone and the live buy zone
  const inAi   = t.current_price >= t.entry_low && t.current_price <= t.entry_high
  const inLive = t.current_price >= lz.buy_zone_low && t.current_price <= lz.buy_zone_high
  return inAi && inLive
}

function t1DistancePct(t: DeepDiveTicker): number | null {
  if (t.target_1 == null) return null
  const entry =
    t.entry_low != null && t.entry_high != null
      ? (t.entry_low + t.entry_high) / 2
      : t.entry_low ?? t.entry_high
  if (entry == null || entry === 0) return null
  return Math.abs((t.target_1 - entry) / entry)
}

function matchesPreset(t: DeepDiveTicker, preset: DeepDivePreset): boolean {
  if (preset === 'NONE') return true

  return (
    t.direction === 'BULL' &&
    (t.conviction ?? 0) >= 3 &&
    (t.signal_agreement_score ?? 0) >= 0.5 &&
    (t.prob_combined ?? 0) >= 0.55
  )
}

function AgreementBar({ score }: { score: number | null }) {
  if (score == null) return <span className="font-mono text-xs text-text-tertiary">—</span>
  const pct = Math.round(score * 100)
  const color = pct >= 70 ? '#22c55e' : pct >= 40 ? '#f59e0b' : '#ef4444'
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 bg-bg-elevated rounded overflow-hidden">
        <div style={{ width: `${pct}%`, background: color }} className="h-full rounded" />
      </div>
      <span className="font-mono text-xs text-text-secondary">{pct}%</span>
    </div>
  )
}

function TradeSetupCells({ t }: { t: DeepDiveTicker }) {
  const entry =
    t.entry_low != null && t.entry_high != null
      ? (t.entry_low + t.entry_high) / 2
      : t.entry_low ?? t.entry_high

  if (entry == null) {
    return (
      <>
        <div className="w-20 text-center font-mono text-xs text-text-tertiary">—</div>
        <div className="w-20 text-center font-mono text-xs text-text-tertiary">—</div>
        <div className="w-20 text-center font-mono text-xs text-text-tertiary">—</div>
        <div className="w-20 text-center font-mono text-xs text-text-tertiary">—</div>
      </>
    )
  }

  const pct = (price: number) => ((price - entry) / entry) * 100
  const fmt = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`

  const t1p = t.target_1 != null ? pct(t.target_1) : null
  const t2p = t.target_2 != null ? pct(t.target_2) : null
  const sp  = t.stop_loss != null ? pct(t.stop_loss) : null
  const rr  = t1p != null && sp != null && Math.abs(sp) > 0 ? Math.abs(t1p / sp) : null

  return (
    <>
      {/* Entry */}
      <div className="w-20 text-center space-y-0.5">
        <div className="font-mono text-xs text-text-primary">${entry.toFixed(2)}</div>
        {t.entry_low != null && t.entry_high != null && (
          <div className="font-mono text-[9px] text-text-tertiary">{t.entry_low}–{t.entry_high}</div>
        )}
      </div>
      {/* T1 + P(T1) */}
      <div className="w-20 text-center space-y-0.5">
        {t1p != null ? (
          <>
            <div className={clsx('font-mono text-xs font-semibold', t1p >= 0 ? 'text-accent-green' : 'text-accent-red')}>{fmt(t1p)}</div>
            <div className="font-mono text-[9px] text-text-tertiary">${t.target_1!.toFixed(2)}</div>
            {t.prob_combined != null && (
              <div className={clsx('font-mono text-[9px]',
                t.prob_combined >= 0.65 ? 'text-accent-green'
                  : t.prob_combined >= 0.45 ? 'text-accent-amber'
                  : 'text-accent-red'
              )}>{Math.round(t.prob_combined * 100)}% hit</div>
            )}
          </>
        ) : <div className="font-mono text-xs text-text-tertiary">—</div>}
      </div>
      {/* T2 */}
      <div className="w-20 text-center space-y-0.5">
        {t2p != null ? (
          <>
            <div className={clsx('font-mono text-xs font-semibold', t2p >= 0 ? 'text-accent-green' : 'text-accent-red')}>{fmt(t2p)}</div>
            <div className="font-mono text-[9px] text-text-tertiary">${t.target_2!.toFixed(2)}</div>
          </>
        ) : <div className="font-mono text-xs text-text-tertiary">—</div>}
      </div>
      {/* Risk + R:R */}
      <div className="w-20 text-center space-y-0.5">
        {sp != null ? (
          <>
            <div className="font-mono text-xs font-semibold text-accent-red">{fmt(sp)}</div>
            {rr != null && (
              <div className={clsx('font-mono text-[9px]', rr >= 2 ? 'text-accent-green' : rr < 1 ? 'text-accent-amber' : 'text-text-tertiary')}>
                R:R {rr.toFixed(1)}
              </div>
            )}
          </>
        ) : <div className="font-mono text-xs text-text-tertiary">—</div>}
      </div>
    </>
  )
}

function TickerRow({
  t,
  isOpen,
  isBlacklisted,
  onToggleBlacklist,
}: {
  t: DeepDiveTicker
  isOpen: boolean
  isBlacklisted: boolean
  onToggleBlacklist: (ticker: string) => Promise<void>
}) {
  const navigate = useNavigate()

  if (!t.has_thesis) {
    // Simplified row for unanalyzed universe tickers
    return (
      <div
        role="button"
        tabIndex={0}
        onClick={() => navigate(`/ticker/${t.ticker}`)}
        onKeyDown={e => e.key === 'Enter' && navigate(`/ticker/${t.ticker}`)}
        className="w-full text-left bg-bg-surface/60 border border-border-subtle hover:border-border-active rounded p-3 transition-colors group opacity-70 hover:opacity-100 cursor-pointer"
      >
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-3 w-44 flex-shrink-0">
            <span className="font-mono text-base font-semibold text-text-secondary group-hover:text-accent-blue transition-colors">
              {t.ticker}
            </span>
            {isOpen && (
              <span className="font-mono text-[9px] uppercase tracking-widest text-accent-amber border border-accent-amber/40 rounded px-1 py-0.5">
                open
              </span>
            )}
          </div>
          <div className="flex-1 min-w-0 flex items-center gap-3">
            {t.name && (
              <span className="font-mono text-xs text-text-tertiary truncate">{t.name}</span>
            )}
            {t.sector && (
              <span className="font-mono text-[10px] text-text-tertiary/60 truncate hidden sm:block">{t.sector}</span>
            )}
          </div>
          {t.current_price != null && (
            <span className="font-mono text-xs text-text-secondary flex-shrink-0 flex items-center gap-1">
              ${t.current_price.toFixed(2)}
              {t.price_source === 'live' && priceSessionLabel() && (
                <span className="font-mono text-[9px] text-accent-amber/80 border border-accent-amber/30 rounded px-0.5">
                  {priceSessionLabel()}
                </span>
              )}
            </span>
          )}
          <div className="font-mono text-[10px] text-text-tertiary/60 flex-shrink-0 flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-text-tertiary/40 inline-block" />
            no analysis — click to run
          </div>
          <BlacklistButton ticker={t.ticker} isBlacklisted={isBlacklisted} onToggle={onToggleBlacklist} />
        </div>
      </div>
    )
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => navigate(`/ticker/${t.ticker}`)}
      onKeyDown={e => e.key === 'Enter' && navigate(`/ticker/${t.ticker}`)}
      className="w-full text-left bg-bg-surface border border-border-subtle hover:border-border-active rounded p-4 transition-colors group cursor-pointer"
    >
      <div className="flex items-center gap-4">
        {/* Ticker + badges */}
        <div className="flex items-center gap-3 w-44 flex-shrink-0">
          <span className="font-mono text-lg font-semibold text-text-primary group-hover:text-accent-blue transition-colors">
            {t.ticker}
          </span>
          <DirectionBadge direction={t.direction ?? 'NEUTRAL'} size="sm" />
          {isOpen && (
            <span className="font-mono text-[9px] uppercase tracking-widest text-accent-amber border border-accent-amber/40 rounded px-1 py-0.5">
              open
            </span>
          )}
        </div>

        {/* Conviction + agreement */}
        <div className="flex items-center gap-6 w-44 flex-shrink-0">
          <ConvictionDots conviction={t.conviction ?? 0} />
          <AgreementBar score={t.signal_agreement_score} />
        </div>

        {/* Trade setup: entry / T1 / T2 / risk */}
        <div className="flex items-center gap-1 flex-shrink-0">
          <TradeSetupCells t={t} />
        </div>

        {/* Meta */}
        <div className="text-right flex-shrink-0 space-y-1 w-28">
          {t.current_price != null && (
            <div className="font-mono text-xs font-semibold text-text-primary flex items-center justify-end gap-1">
              ${t.current_price.toFixed(2)}
              {t.price_source === 'live' && priceSessionLabel() && (
                <span className="font-mono text-[9px] text-accent-amber/80 border border-accent-amber/30 rounded px-0.5">
                  {priceSessionLabel()}
                </span>
              )}
            </div>
          )}
          {(() => {
            const dt = fmtDateTime(t.created_at)
            return dt ? (
              <div>
                <div className="font-mono text-[10px] text-text-tertiary">{dt.date}</div>
                <div className="font-mono text-[10px] text-text-tertiary/60">{dt.time}</div>
              </div>
            ) : (
              <div className="font-mono text-[10px] text-text-tertiary">{t.date}</div>
            )
          })()}
          {t.time_horizon && (
            <div className="font-mono text-[10px] text-text-tertiary">{t.time_horizon}</div>
          )}
          {t.data_quality && (
            <div className={clsx(
              'font-mono text-[10px]',
              t.data_quality === 'HIGH' ? 'text-accent-green'
                : t.data_quality === 'MEDIUM' ? 'text-accent-amber'
                : 'text-accent-red'
            )}>
              {t.data_quality}
            </div>
          )}
          <BlacklistButton ticker={t.ticker} isBlacklisted={isBlacklisted} onToggle={onToggleBlacklist} className="mt-1" />
        </div>
      </div>
    </div>
  )
}

function BlacklistedRow({
  t,
  onUnban,
}: {
  t: DeepDiveTicker
  onUnban: (ticker: string) => Promise<void>
}) {
  const navigate = useNavigate()
  const [pending, setPending] = useState(false)

  const handleUnban = async (e: React.MouseEvent) => {
    e.stopPropagation()
    setPending(true)
    try {
      await onUnban(t.ticker)
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="flex items-center gap-4 bg-bg-surface/40 border border-accent-red/10 rounded px-4 py-2.5 opacity-60 hover:opacity-90 transition-opacity group">
      {/* Ticker + name */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => navigate(`/ticker/${t.ticker}`)}
        onKeyDown={e => e.key === 'Enter' && navigate(`/ticker/${t.ticker}`)}
        className="flex items-center gap-3 w-36 flex-shrink-0 cursor-pointer"
      >
        <span className="font-mono text-sm font-semibold text-text-secondary group-hover:text-text-primary transition-colors">
          {t.ticker}
        </span>
        {t.direction && (
          <DirectionBadge direction={t.direction} size="sm" />
        )}
      </div>

      {/* Name + sector */}
      <div className="flex-1 min-w-0">
        {t.name && (
          <span className="font-mono text-xs text-text-tertiary truncate block">{t.name}</span>
        )}
      </div>

      {/* Last thesis date */}
      <div className="font-mono text-[10px] text-text-tertiary/60 flex-shrink-0">
        {t.date ? `last thesis ${t.date}` : 'no thesis'}
      </div>

      {/* Unban button */}
      <button
        onClick={handleUnban}
        disabled={pending}
        className="flex-shrink-0 font-mono text-[10px] px-3 py-1 rounded border border-accent-green/30 text-accent-green/60 hover:text-accent-green hover:border-accent-green/60 transition-colors disabled:opacity-40"
      >
        {pending ? '…' : '↩ restore'}
      </button>
    </div>
  )
}

function Section({
  label,
  rows,
  openTickers,
  blacklistSet,
  onToggleBlacklist,
  showHeaders = true,
}: {
  label: string
  rows: DeepDiveTicker[]
  openTickers: Set<string>
  blacklistSet: Set<string>
  onToggleBlacklist: (ticker: string) => Promise<void>
  showHeaders?: boolean
}) {
  if (!rows.length) return null
  return (
    <div className="space-y-2">
      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary pt-2 pb-1 border-b border-border-subtle">
        {label} — {rows.length}
      </div>
      {showHeaders && (
        <div className="flex items-center gap-4 px-4 pb-1">
          <div className="w-44 flex-shrink-0" />
          <div className="w-44 flex-shrink-0" />
          <div className="flex items-center gap-1 flex-shrink-0">
            {(['Entry', 'T1 +%', 'T2 +%', 'Risk %'] as const).map(h => (
              <div key={h} className="w-20 text-center font-mono text-[9px] uppercase tracking-wide text-text-tertiary">{h}</div>
            ))}
          </div>
          <div className="flex-1" />
        </div>
      )}
      {rows.map(t => (
        <TickerRow
          key={t.ticker}
          t={t}
          isOpen={openTickers.has(t.ticker)}
          isBlacklisted={blacklistSet.has(t.ticker)}
          onToggleBlacklist={onToggleBlacklist}
        />
      ))}
    </div>
  )
}

const FILTER_OPTIONS: { value: DirectionFilter; label: string; title?: string }[] = [
  { value: 'ALL',          label: 'All' },
  { value: 'ANALYZED',     label: 'Analyzed' },
  { value: 'BULL',         label: 'Bull' },
  { value: 'BEAR',         label: 'Bear' },
  { value: 'NEUTRAL',      label: 'Neutral' },
  { value: 'HIGH_RR',      label: 'R:R ≥2' },
  { value: 'IN_ENTRY',     label: 'In Entry Zone' },
  { value: 'ZONE_OVERLAP', label: 'Hot Entry' },
  { value: 'IDEAL_BEAR',   label: 'Ideal Bear', title: 'Bear · conviction ≥3 · bear_prob ≥50% · explicit time horizon — 52% win rate historically' },
]

// Strip injected LLM instructions from user-supplied text (prompt injection defense)
function sanitizeThesis(text: string | null | undefined): string {
  if (!text) return ''
  return text
    .replace(/\bCRITICAL\s*:/gi, '[REDACTED]:')
    .replace(/\bIMPORTANT\s*:/gi, '[REDACTED]:')
    .replace(/respond with text only/gi, '[redacted]')
    .replace(/do not call any tools/gi, '[redacted]')
    .replace(/your task is to/gi, '[redacted]')
    .replace(/ignore (previous|prior|all|above) instructions?/gi, '[redacted]')
    .replace(/disregard (previous|prior|all|above) instructions?/gi, '[redacted]')
    .trim()
}

function buildLLMPrompt(
  tickers: DeepDiveTicker[],
  bullOnly = false,
  premarketPrices: Record<string, number> = {},
): string {
  let analyzed = tickers.filter(t => t.has_thesis && t.direction)
  if (bullOnly) analyzed = analyzed.filter(t => t.direction === 'BULL' && (t.conviction ?? 0) >= 3)
  const today = new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })
  const hasPremarket = Object.keys(premarketPrices).length > 0

  const lines: string[] = [
    `You are a quantitative equity analyst. Today is ${today}.`,
    ``,
    `Below are ${analyzed.length} tickers from my signal engine with AI-generated theses${bullOnly ? ' (BULL direction, conviction ≥ 3 only)' : ''}. Each has:`,
    `- Direction & conviction (1–5)`,
    `- AI entry zone, T1/T2 targets, stop loss`,
    `- T1 upside %, Risk:Reward, probability of hitting T1`,
    `- Time horizon, signal agreement score (0–100%), sector`,
    `- Price: ${hasPremarket ? 'live pre-market price fetched at prompt generation time' : 'last close price'}`,
    ``,
    `YOUR TASK:`,
    `1. Identify the TOP 3 best buys for TODAY (intraday / next 1–2 days)`,
    `2. Identify the TOP 3 best buys for THIS WEEK (3–5 day hold)`,
    `3. Flag any tickers to AVOID right now and why`,
    `4. Give a short overall market read based on what you see across sectors`,
    ``,
    `Prioritise: high P(T1) × good R:R × price near entry zone × short time horizon.`,
    `Penalise: low conviction, wide stop, R:R < 1, NEUTRAL direction.`,
    ``,
    `─────────────────────────────────────────────────────`,
    `TICKER DATA`,
    `─────────────────────────────────────────────────────`,
  ]

  for (const t of analyzed) {
    const entry = t.entry_low != null && t.entry_high != null
      ? (t.entry_low + t.entry_high) / 2
      : t.entry_low ?? t.entry_high

    const fmt = (v: number | null, prefix = '$') =>
      v != null ? `${prefix}${v.toFixed(2)}` : '—'

    const pct = (price: number | null) =>
      entry && price != null
        ? `${((price - entry) / entry * 100) >= 0 ? '+' : ''}${((price - entry) / entry * 100).toFixed(1)}%`
        : '—'

    const rr = (() => {
      if (!entry || t.target_1 == null || t.stop_loss == null) return null
      const risk = Math.abs(entry - t.stop_loss)
      const reward = Math.abs(t.target_1 - entry)
      return risk > 0 ? (reward / risk).toFixed(1) : null
    })()

    const pmPrice   = premarketPrices[t.ticker] ?? null
    const dispPrice = pmPrice ?? t.current_price
    const inEntry   = dispPrice != null && t.entry_low != null && t.entry_high != null
      && dispPrice >= t.entry_low && dispPrice <= t.entry_high

    lines.push(``)
    lines.push(`${t.ticker}${t.name ? ` — ${t.name}` : ''}${t.sector ? ` (${t.sector})` : ''}`)
    lines.push(`  Direction:  ${t.direction} | Conviction: ${'●'.repeat(t.conviction ?? 0)}${'○'.repeat(5 - (t.conviction ?? 0))} (${t.conviction}/5)`)
    lines.push(`  Price:      ${fmt(dispPrice)}${pmPrice != null ? ' (pre-market)' : ''}${inEntry ? ' ← IN ENTRY ZONE' : ''}`)
    lines.push(`  Entry zone: ${fmt(t.entry_low)} – ${fmt(t.entry_high)}`)
    lines.push(`  T1:         ${fmt(t.target_1)} (${pct(t.target_1)}) | T2: ${fmt(t.target_2)} (${pct(t.target_2)})`)
    lines.push(`  Stop:       ${fmt(t.stop_loss)} (${pct(t.stop_loss)}) | R:R: ${rr ?? '—'}`)
    lines.push(`  P(T1):      ${t.prob_combined != null ? `${Math.round(t.prob_combined * 100)}%` : '—'} | Time horizon: ${t.time_horizon ?? '—'}`)
    lines.push(`  Agreement:  ${t.signal_agreement_score != null ? `${Math.round(t.signal_agreement_score * 100)}%` : '—'} | Data quality: ${t.data_quality ?? '—'}`)
    const thesisSafe = sanitizeThesis(t.thesis_short)
    if (thesisSafe) lines.push(`  Thesis:     ${thesisSafe}`)
  }

  lines.push(``)
  lines.push(`─────────────────────────────────────────────────────`)
  lines.push(`Now give your analysis. Be concise and actionable.`)

  return lines.join('\n')
}

export function DeepDivePage() {
  const [filter, setFilter] = useState<DirectionFilter>('ALL')
  const [preset, setPreset] = useState<DeepDivePreset>('NONE')
  const [hedgeFundSlug, setHedgeFundSlug] = useState<string | null>(null)
  const [sortModes, setSortModes] = useState<SortMode[]>(['direction'])
  const [copied, setCopied] = useState(false)
  const [copyFetching, setCopyFetching] = useState(false)
  const [bullOnlyCopy, setBullOnlyCopy] = useState(false)
  const [bulkLlm, setBulkLlm] = useState<LLMChoice>('grok-4.3')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [bulkRun, setBulkRun] = useState<{
    status: 'idle' | 'running' | 'polling' | 'done'
    total: number
    started: number
    completed: number
    failed: number
    llm: string
    tickers: string[]
    results: AnalyzeStatus[]
    failures: string[]
  }>({
    status: 'idle',
    total: 0,
    started: 0,
    completed: 0,
    failed: 0,
    llm: 'grok-4.3',
    tickers: [],
    results: [],
    failures: [],
  })
  const toggleSort = (mode: SortMode) =>
    setSortModes(prev =>
      prev.includes(mode)
        ? prev.filter(m => m !== mode).length ? prev.filter(m => m !== mode) : ['direction']
        : [...prev, mode]
    )
  const { data: tickers, isLoading: loadingTickers, isError, error, refetch } = useDeepDiveTickers()
  const { data: openTickers = [] } = useOpenPositionTickers()
  const { data: liveZones = {} } = useDeepDiveLiveZones()
  const { blacklistSet, toggle: toggleBlacklist } = useBlacklist()
  const { funds, tickerSet: hfTickerSet, loadingPositions: hfLoading } = useHedgeFundFilter(hedgeFundSlug)
  const qc = useQueryClient()

  const openSet = useMemo(() => new Set(openTickers), [openTickers])

  const { analyzedRows, universeRows, blacklistedRows, presetMatchCount } = useMemo(() => {
    const all = (tickers ?? []).filter(t => hfTickerSet == null || hfTickerSet.has(t.ticker))

    // Separate blacklisted tickers first
    const blacklisted = all.filter(t => blacklistSet.has(t.ticker))

    // Split non-blacklisted into analyzed and unanalyzed
    const active = all.filter(t => !blacklistSet.has(t.ticker))
    const analyzed = active.filter(t => t.has_thesis)
    const universe = active.filter(t => !t.has_thesis)

    // Apply direction/quality filter to analyzed tickers
    const baseFilteredAnalyzed =
      filter === 'ALL' || filter === 'ANALYZED'
        ? analyzed
        : filter === 'HIGH_RR'
          ? analyzed.filter(t => (computeRR(t) ?? 0) >= 2)
          : filter === 'IN_ENTRY'
            ? analyzed.filter(isInAiEntryZone)
            : filter === 'ZONE_OVERLAP'
              ? analyzed.filter(t => hasZoneOverlap(t, liveZones))
              : filter === 'IDEAL_BEAR'
                ? analyzed.filter(isIdealBear)
                : analyzed.filter(t => t.direction === filter)

    const filteredAnalyzed = baseFilteredAnalyzed.filter(t => matchesPreset(t, preset))
    const presetMatches = analyzed.filter(t => matchesPreset(t, preset)).length

    // Universe tickers: show when filter is ALL only and no preset is active
    const filteredUniverse = filter === 'ALL' && preset === 'NONE' ? universe : []

    // Sort analyzed section
    const sorter = buildSorter(sortModes)
    const sortedAnalyzed = [...filteredAnalyzed].sort(sorter)

    // Sort universe: open positions first, then alphabetical
    const sortedUniverse = [...filteredUniverse].sort((a, b) => {
      const aOpen = openSet.has(a.ticker) ? 0 : 1
      const bOpen = openSet.has(b.ticker) ? 0 : 1
      if (aOpen !== bOpen) return aOpen - bOpen
      return a.ticker.localeCompare(b.ticker)
    })

    return {
      analyzedRows: sortedAnalyzed,
      universeRows: sortedUniverse,
      blacklistedRows: blacklisted,
      presetMatchCount: presetMatches,
    }
  }, [tickers, filter, sortModes, openSet, liveZones, blacklistSet, preset, hfTickerSet])

  const totalShown = analyzedRows.length + universeRows.length
  const rerunRows = useMemo(
    () => [...analyzedRows, ...universeRows],
    [analyzedRows, universeRows],
  )
  const rerunTickers = useMemo(
    () => rerunRows.map(t => t.ticker),
    [rerunRows],
  )
  const bulkLlmLabel = LLM_OPTIONS.find(o => o.value === bulkLlm)?.label ?? bulkLlm
  const confirmPreview = rerunTickers.slice(0, 8)

  useEffect(() => {
    if (bulkRun.status !== 'polling' || bulkRun.tickers.length === 0) return

    let cancelled = false
    const poll = async () => {
      try {
        const statuses = await Promise.all(
          bulkRun.tickers.map((ticker) => api.tickerAnalyzeStatus(ticker)),
        )
        if (cancelled) return

        const completed = statuses.filter((s) => s.status !== 'running').length
        if (completed === statuses.length) {
          qc.invalidateQueries({ queryKey: ['deepdive', 'tickers'] })
          qc.invalidateQueries({ queryKey: ['deepdive', 'live-zones'] })
          setBulkRun(prev => ({
            ...prev,
            status: 'done',
            completed,
          }))
          return
        }

        setBulkRun(prev => ({
          ...prev,
          completed,
        }))
      } catch {
        // Keep polling; transient status failures should not abort the batch.
      }
    }

    poll()
    const id = window.setInterval(poll, 5000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [bulkRun.status, bulkRun.tickers, qc])

  const runBulkRerun = async () => {
    const uniqueTickers = Array.from(new Set(rerunTickers))
    if (!uniqueTickers.length) {
      setConfirmOpen(false)
      return
    }

    setConfirmOpen(false)
    setBulkRun({
      status: 'running',
      total: uniqueTickers.length,
      started: 0,
      completed: 0,
      failed: 0,
      llm: bulkLlm,
      tickers: [],
      results: [],
      failures: [],
    })

    const settled = await Promise.allSettled(
      uniqueTickers.map(async (ticker) => api.tickerAnalyze(ticker, bulkLlm)),
    )

    const results: AnalyzeStatus[] = []
    const startedTickers: string[] = []
    const failures: string[] = []

    for (let i = 0; i < settled.length; i += 1) {
      const item = settled[i]
      if (item.status === 'fulfilled') {
        results.push(item.value)
        startedTickers.push(uniqueTickers[i])
      } else {
        const err = item.reason as { response?: { data?: { detail?: string } }; message?: string } | undefined
        const reason = err?.response?.data?.detail ?? err?.message ?? 'launch failed'
        failures.push(`${uniqueTickers[i]}: ${reason}`)
      }
    }

    setBulkRun({
      status: startedTickers.length > 0 ? 'polling' : 'done',
      total: uniqueTickers.length,
      started: results.length,
      completed: 0,
      failed: failures.length,
      llm: bulkLlm,
      tickers: startedTickers,
      results,
      failures,
    })
  }

  return (
    <Shell title="Deep Dive">
      {confirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
          <div className="w-full max-w-xl rounded-lg border border-border-active bg-bg-surface p-5 shadow-2xl">
            <div className="space-y-3">
              <div>
                <div className="font-mono text-[10px] uppercase tracking-widest text-accent-amber">
                  Confirm Bulk LLM Re-run
                </div>
                <div className="mt-2 font-mono text-sm text-text-primary leading-relaxed">
                  Re-run AI analysis for <span className="text-accent-amber">{rerunTickers.length}</span> ticker{rerunTickers.length !== 1 ? 's' : ''} currently shown in Deep Dive using <span className="text-text-primary">{bulkLlmLabel}</span>?
                </div>
              </div>

              <div className="font-mono text-[11px] text-text-tertiary leading-relaxed">
                Filter: {filter} · Preset: {preset === 'NONE' ? 'none' : 'PM Regime'}
              </div>

              {confirmPreview.length > 0 && (
                <div className="rounded border border-border-subtle bg-bg-elevated p-3">
                  <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
                    Tickers
                  </div>
                  <div className="font-mono text-xs text-text-secondary leading-relaxed">
                    {confirmPreview.join(', ')}
                    {rerunTickers.length > confirmPreview.length && ` … +${rerunTickers.length - confirmPreview.length} more`}
                  </div>
                </div>
              )}

              <div className="flex items-center justify-end gap-2 pt-2">
                <button
                  onClick={() => setConfirmOpen(false)}
                  className="font-mono text-xs px-3 py-1.5 rounded border border-border-subtle text-text-tertiary hover:text-text-secondary hover:border-border-active transition-colors"
                >
                  No
                </button>
                <button
                  onClick={runBulkRerun}
                  className="font-mono text-xs px-3 py-1.5 rounded border bg-accent-amber/20 border-accent-amber/40 text-accent-amber hover:bg-accent-amber/30 transition-colors"
                >
                  Yes
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Filter + sort bar */}
      <div className="flex items-center gap-2 mb-5 flex-wrap">
        {/* Hedge fund portfolio filter */}
        <div className="flex items-center gap-1.5 mr-1">
          <span className="font-mono text-[10px] text-text-tertiary">hf:</span>
          <select
            value={hedgeFundSlug ?? ''}
            onChange={e => setHedgeFundSlug(e.target.value || null)}
            className={clsx(
              'font-mono text-xs px-2 py-1.5 rounded border bg-bg-surface focus:outline-none cursor-pointer transition-colors',
              hedgeFundSlug
                ? 'border-accent-blue/50 text-accent-blue'
                : 'border-border-subtle text-text-secondary hover:border-border-active',
            )}
            title="Filter to a hedge fund's long equity portfolio"
          >
            <option value="">All Tickers</option>
            {funds.map(f => (
              <option key={f.slug} value={f.slug}>{f.name}</option>
            ))}
          </select>
          {hfLoading && (
            <span className="font-mono text-[10px] text-text-tertiary animate-pulse">loading…</span>
          )}
        </div>

        <div className="w-px h-4 bg-border-subtle mx-1" />

        <div className="flex items-center gap-2 mr-2">
          <span className="font-mono text-[10px] text-text-tertiary">preset:</span>
          <button
            onClick={() => setPreset('NONE')}
            className={clsx(
              'font-mono text-xs px-3 py-1.5 rounded border transition-colors',
              preset === 'NONE'
                ? 'bg-bg-elevated text-text-primary border-border-active'
                : 'text-text-tertiary border-border-subtle hover:text-text-secondary hover:border-border-active'
            )}
          >
            None
          </button>
          <button
            onClick={() => {
              setPreset('PM_REGIME')
              setFilter('ANALYZED')
            }}
            title="BULL, conviction ≥ 3, agreement ≥ 50%, prob ≥ 55%"
            className={clsx(
              'font-mono text-xs px-3 py-1.5 rounded border transition-colors',
              preset === 'PM_REGIME'
                ? 'bg-accent-amber/20 text-accent-amber border-accent-amber/40'
                : 'text-accent-amber/60 border-accent-amber/20 hover:text-accent-amber hover:border-accent-amber/40'
            )}
          >
            PM Regime
          </button>
          {preset === 'PM_REGIME' && (
            <span className="font-mono text-[10px] text-accent-amber/80">
              {presetMatchCount} match{presetMatchCount !== 1 ? 'es' : ''}
            </span>
          )}
        </div>

        <div className="w-px h-4 bg-border-subtle mx-1" />

        {FILTER_OPTIONS.map(({ value, label, title }) => (
          <button
            key={value}
            onClick={() => setFilter(value)}
            title={title}
            className={clsx(
              'font-mono text-xs px-3 py-1.5 rounded border transition-colors',
              filter === value
                ? value === 'BULL'
                  ? 'bg-accent-green/20 text-accent-green border-accent-green/40'
                  : value === 'BEAR' || value === 'IDEAL_BEAR'
                    ? 'bg-accent-red/20 text-accent-red border-accent-red/40'
                    : value === 'NEUTRAL'
                      ? 'bg-text-tertiary/20 text-text-secondary border-text-tertiary/30'
                      : value === 'HIGH_RR' || value === 'IN_ENTRY'
                        ? 'bg-accent-green/20 text-accent-green border-accent-green/40'
                        : value === 'ZONE_OVERLAP'
                          ? 'bg-accent-amber/20 text-accent-amber border-accent-amber/40'
                          : 'bg-bg-elevated text-text-primary border-border-active'
                : value === 'ZONE_OVERLAP'
                  ? 'text-accent-amber/60 border-accent-amber/20 hover:text-accent-amber hover:border-accent-amber/40'
                  : value === 'IDEAL_BEAR'
                    ? 'text-accent-red/60 border-accent-red/20 hover:text-accent-red hover:border-accent-red/40'
                    : 'text-text-tertiary border-border-subtle hover:text-text-secondary hover:border-border-active'
            )}
          >
            {label}
          </button>
        ))}

        {/* Divider */}
        <div className="w-px h-4 bg-border-subtle mx-1" />

        {/* Sort toggle — multi-select, order matters */}
        <div className="flex items-center gap-1">
          <span className="font-mono text-[10px] text-text-tertiary mr-1">sort:</span>
          {([['direction', 'Direction'], ['rr', 'R:R ↓'], ['t1', 'T1 ↓']] as const).map(([mode, label]) => {
            const idx = sortModes.indexOf(mode)
            const active = idx !== -1
            return (
              <button
                key={mode}
                onClick={() => toggleSort(mode)}
                className={clsx(
                  'font-mono text-xs px-2.5 py-1.5 rounded border transition-colors flex items-center gap-1',
                  active
                    ? 'bg-bg-elevated text-text-primary border-border-active'
                    : 'text-text-tertiary border-border-subtle hover:text-text-secondary hover:border-border-active'
                )}
              >
                {label}
                {active && sortModes.length > 1 && (
                  <span className="font-mono text-[9px] text-text-tertiary">{idx + 1}</span>
                )}
              </button>
            )
          })}
        </div>

        <span className="font-mono text-[10px] text-text-tertiary ml-1">
          {totalShown} ticker{totalShown !== 1 ? 's' : ''}
        </span>

        {preset === 'PM_REGIME' && (
          <span className="font-mono text-[10px] text-text-tertiary">
            Hint: T1 above 6% has had lower hit rates so far. Keep 6% in mind as the strongest hit-rate zone, not a hard filter.
          </span>
        )}

        {/* LLM prompt copy controls */}
        <div className="ml-auto flex items-center gap-1.5 flex-shrink-0">
          <select
            value={bulkLlm}
            onChange={e => setBulkLlm(e.target.value as LLMChoice)}
            className="font-mono text-xs px-2 py-1.5 rounded border border-border-subtle bg-bg-surface text-text-secondary hover:border-border-active focus:outline-none cursor-pointer"
            title={LLM_OPTIONS.find(o => o.value === bulkLlm)?.desc}
          >
            {LLM_OPTIONS.map(o => (
              <option key={o.value} value={o.value} title={o.desc}>{o.label}</option>
            ))}
          </select>
          <button
            onClick={() => setConfirmOpen(true)}
            disabled={rerunTickers.length === 0 || bulkRun.status === 'running'}
            className={clsx(
              'font-mono text-xs px-3 py-1.5 rounded border transition-colors',
              rerunTickers.length === 0 || bulkRun.status === 'running'
                ? 'border-border-subtle text-text-tertiary opacity-50 cursor-not-allowed'
                : 'border-accent-amber/30 text-accent-amber/80 hover:text-accent-amber hover:border-accent-amber/50'
            )}
            title={
              rerunTickers.length === 0
                ? 'No tickers currently shown'
                : `Re-run ${rerunTickers.length} shown ticker${rerunTickers.length !== 1 ? 's' : ''} with ${bulkLlmLabel}`
            }
          >
            ↻ Re-run shown tickers
          </button>
          <button
            onClick={() => setBullOnlyCopy(v => !v)}
            title="Filter copied prompt to BULL tickers with conviction ≥ 3"
            className={clsx(
              'font-mono text-[10px] px-2 py-1.5 rounded border transition-colors',
              bullOnlyCopy
                ? 'bg-accent-green/20 text-accent-green border-accent-green/40'
                : 'text-text-tertiary border-border-subtle hover:border-border-active hover:text-text-secondary'
            )}
          >
            BULL conv≥3
          </button>
          <button
            disabled={copyFetching}
            onClick={async () => {
              setCopyFetching(true)
              let premarketPrices: Record<string, number> = {}
              try {
                premarketPrices = await api.deepdivePremarketPrices()
              } catch {
                // silently fall back to close prices
              }
              const prompt = buildLLMPrompt(tickers ?? [], bullOnlyCopy, premarketPrices)
              setCopyFetching(false)
              navigator.clipboard.writeText(prompt).then(() => {
                setCopied(true)
                setTimeout(() => setCopied(false), 2000)
              })
            }}
            className={clsx(
              'font-mono text-xs px-3 py-1.5 rounded border transition-colors',
              copied
                ? 'border-accent-green/60 text-accent-green'
                : copyFetching
                  ? 'border-border-subtle text-text-tertiary opacity-60 cursor-wait'
                  : 'border-border-subtle text-text-tertiary hover:border-border-active hover:text-text-secondary'
            )}
          >
            {copied ? '✓ Copied' : copyFetching ? '⏳ Fetching…' : '⎘ Copy LLM Prompt'}
          </button>
        </div>
      </div>

      {bulkRun.status === 'running' && (
        <div className="mb-4 font-mono text-xs text-accent-amber flex items-center gap-2">
          <span className="animate-pulse">⬤</span>
          Starting AI analysis for {bulkRun.total} ticker{bulkRun.total !== 1 ? 's' : ''} with {bulkLlmLabel}…
        </div>
      )}

      {bulkRun.status === 'polling' && (
        <div className="mb-4 font-mono text-xs text-accent-amber flex items-center gap-2">
          <span className="animate-pulse">⬤</span>
          Re-run in progress: {bulkRun.completed}/{bulkRun.started} finished · auto-refreshing Deep Dive when complete
        </div>
      )}

      {bulkRun.status === 'done' && (
        <div className="mb-4 rounded border border-border-subtle bg-bg-elevated px-3 py-2">
          <div className="font-mono text-xs text-text-secondary">
            Bulk re-run finished: <span className="text-accent-green">{bulkRun.started} started</span>
            {bulkRun.started > 0 && (
              <span className="text-text-tertiary"> · {bulkRun.completed || bulkRun.started} completed</span>
            )}
            {bulkRun.failed > 0 && (
              <span className="text-accent-red"> · {bulkRun.failed} failed</span>
            )}
            <span className="text-text-tertiary"> · model {LLM_OPTIONS.find(o => o.value === bulkRun.llm)?.label ?? bulkRun.llm}</span>
          </div>
          {bulkRun.failed > 0 && (
            <div className="mt-1 space-y-0.5">
              {bulkRun.failures.slice(0, 8).map((f, idx) => (
                <div key={idx} className="font-mono text-[10px] text-accent-red/80">{f}</div>
              ))}
              {bulkRun.failures.length > 8 && (
                <div className="font-mono text-[10px] text-accent-red/60">… +{bulkRun.failures.length - 8} more</div>
              )}
            </div>
          )}
        </div>
      )}

      {loadingTickers ? (
        <LoadingSkeleton rows={8} />
      ) : isError ? (
        <div className="font-mono text-sm py-12 text-center space-y-3">
          <div className="text-accent-red">Failed to load tickers.</div>
          <div className="text-text-tertiary text-xs">{String(error)}</div>
          <button
            onClick={() => refetch()}
            className="mt-2 px-4 py-1.5 text-xs border border-border-subtle hover:border-border-active text-text-secondary hover:text-text-primary rounded transition-colors"
          >
            Retry
          </button>
        </div>
      ) : totalShown === 0 ? (
        <div className="font-mono text-sm text-text-tertiary py-12 text-center">
          {(tickers?.length ?? 0) === 0
            ? 'No tickers yet. Run signal_engine.py to populate the universe.'
            : `No ${filter.toLowerCase()} tickers.`}
        </div>
      ) : (
        <div className="space-y-6">
          {/* Analyzed tickers with full thesis data */}
          {analyzedRows.length > 0 && (
            <Section label="Watchlist (Analyzed)" rows={analyzedRows} openTickers={openSet} blacklistSet={blacklistSet} onToggleBlacklist={toggleBlacklist} />
          )}

          {/* Universe tickers without AI analysis */}
          {universeRows.length > 0 && (
            <Section
              label="Universe — no AI analysis yet"
              rows={universeRows}
              openTickers={openSet}
              blacklistSet={blacklistSet}
              onToggleBlacklist={toggleBlacklist}
              showHeaders={false}
            />
          )}

          {/* Blacklisted tickers */}
          {blacklistedRows.length > 0 && (
            <div className="space-y-2">
              <div className="font-mono text-[10px] uppercase tracking-widest text-accent-red/60 pt-2 pb-1 border-b border-accent-red/20">
                Blacklisted — {blacklistedRows.length} (skipped by AI refresh)
              </div>
              {blacklistedRows.map(t => (
                <BlacklistedRow key={t.ticker} t={t} onUnban={toggleBlacklist} />
              ))}
            </div>
          )}
        </div>
      )}
    </Shell>
  )
}
