import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import * as Tabs from '@radix-ui/react-tabs'
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
import { AlertTriangle, Wallet, Plus, Minus, RefreshCw, ChevronDown, ChevronRight, X } from 'lucide-react'
import { Shell } from '../components/layout/Shell'
import { MetricCard } from '../components/ui/MetricCard'
import { DirectionBadge } from '../components/ui/DirectionBadge'
import { ConvictionDots } from '../components/ui/ConvictionDots'
import { MonoNumber } from '../components/ui/MonoNumber'
import { SkeletonCard, LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { RegimeBadge } from '../components/ui/RegimeBadge'
import {
  usePortfolioSummary,
  usePortfolioHistory,
  usePortfolioPositions,
  usePortfolioSparklines,
  useEquityScreener,
  useCash,
  useCashUpdate,
  useAddPosition,
  useSellPosition,
  useClosePosition,
  useTrades,
} from '../hooks/usePortfolio'
import { useRegime } from '../hooks/useRegime'

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

// ─── Regime Panel ─────────────────────────────────────────────────────────────

const COMPONENT_META = [
  { key: 'trend'       as const, label: 'Trend',          min: -1, max: 2, weight: 25 },
  { key: 'volatility'  as const, label: 'Volatility',      min: -2, max: 2, weight: 33 },
  { key: 'credit'      as const, label: 'Credit Spread',   min: -1, max: 1, weight: 17 },
  { key: 'yield_curve' as const, label: 'Yield Curve',     min: -2, max: 1, weight: 25 },
]

function normalizeScore(val: number, min: number, max: number): number {
  return Math.round(((val - min) / (max - min)) * 100)
}

function signalDir(val: number): { symbol: string; label: string; cls: string } {
  if (val > 0) return { symbol: '↑', label: 'UP',      cls: 'text-accent-green' }
  if (val < 0) return { symbol: '↓', label: 'DOWN',    cls: 'text-accent-red' }
  return             { symbol: '—', label: 'NEUTRAL', cls: 'text-text-tertiary' }
}

interface RegimePanelProps {
  open: boolean
  onToggle: () => void
  regime: string
  score: number
  sizeMultiplier: number
  components?: Record<string, number>
  sectorRegimes?: Record<string, string>
  computedAt?: string | null
}

function RegimePanel({
  open, onToggle, regime, score, sizeMultiplier, components, sectorRegimes, computedAt,
}: RegimePanelProps) {
  const bullSectors = sectorRegimes
    ? Object.values(sectorRegimes).filter(v => v === 'BULL').length
    : null
  const bearSectors = sectorRegimes
    ? Object.values(sectorRegimes).filter(v => v === 'BEAR').length
    : null
  const showSectorBreadth = bullSectors !== null && bearSectors !== null

  return (
    <div
      data-testid="regime-panel"
      className="bg-bg-surface border border-border-subtle rounded overflow-hidden mb-6"
    >
      {/* Header / toggle */}
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="w-full px-4 py-3 flex items-center gap-3 border-b border-border-subtle hover:bg-bg-elevated transition-colors text-left"
      >
        {open
          ? <ChevronDown className="w-3.5 h-3.5 text-text-tertiary flex-shrink-0" />
          : <ChevronRight className="w-3.5 h-3.5 text-text-tertiary flex-shrink-0" />
        }
        <span className="font-mono text-xs text-text-tertiary uppercase tracking-widest">
          Macro Regime
        </span>
        <RegimeBadge regime={regime as any} score={score} size="sm" />
      </button>

      {open && (
        <div className="px-4 py-4 space-y-5">

          {/* Section A — Size Multiplier */}
          <div>
            <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-1">
              Position size multiplier
            </div>
            <div className="font-mono text-4xl font-semibold text-text-primary leading-none">
              {sizeMultiplier.toFixed(1)}×
            </div>
          </div>

          {/* Section B — Component Breakdown */}
          <div>
            <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
              Component Breakdown
            </div>
            <table className="w-full" aria-label="Regime component breakdown">
              <thead>
                <tr className="border-b border-border-subtle">
                  {['Component', 'Score (0–100)', 'Signal', 'Weight'].map(h => (
                    <th
                      key={h}
                      className="pb-2 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary pr-6"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {COMPONENT_META.map(({ key, label, min, max, weight }) => {
                  const rawVal = components?.[key]
                  const hasVal = rawVal !== undefined && rawVal !== null
                  const normalized = hasVal ? normalizeScore(rawVal!, min, max) : null
                  const dir = hasVal ? signalDir(rawVal!) : null
                  return (
                    <tr key={key} className="border-b border-border-subtle/40">
                      <td className="py-2 pr-6 font-mono text-sm text-text-primary">{label}</td>
                      <td className="py-2 pr-6 font-mono text-sm text-text-secondary">
                        {normalized !== null ? normalized : 'N/A'}
                      </td>
                      <td className="py-2 pr-6">
                        {dir
                          ? (
                            <span className={`font-mono text-sm ${dir.cls}`}>
                              {dir.symbol} {dir.label}
                            </span>
                          )
                          : <span className="font-mono text-sm text-text-tertiary">N/A</span>
                        }
                      </td>
                      <td className="py-2 font-mono text-sm text-text-tertiary">{weight}%</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Section C — Sector Breadth */}
          {showSectorBreadth && (
            <div className="font-mono text-sm">
              <span className="text-accent-green">Bull sectors: {bullSectors}</span>
              <span className="text-text-tertiary mx-2">|</span>
              <span className="text-accent-red">Bear sectors: {bearSectors}</span>
            </div>
          )}

          {/* Last updated */}
          {computedAt && (
            <div className="font-mono text-[10px] text-text-tertiary">
              Last updated: {new Date(computedAt).toLocaleString()}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Sizing status ────────────────────────────────────────────────────────────

type SizingStatus = 'OVERWEIGHT' | 'ALIGNED' | 'UNDERWEIGHT' | 'NOT_HELD'

export function getSizingStatus(currentEur: number, recommendedEur: number): SizingStatus {
  if (currentEur <= 0) return 'NOT_HELD'
  if (currentEur > recommendedEur * 1.2) return 'OVERWEIGHT'
  if (currentEur < recommendedEur * 0.8) return 'UNDERWEIGHT'
  return 'ALIGNED'
}

const STATUS_STYLES: Record<SizingStatus, string> = {
  OVERWEIGHT:  'bg-red-500/15 text-red-400 border border-red-500/30',
  ALIGNED:     'bg-green-500/15 text-green-400 border border-green-500/30',
  UNDERWEIGHT: 'bg-amber-500/15 text-amber-400 border border-amber-500/30',
  NOT_HELD:    'bg-zinc-700/40 text-zinc-500 border border-zinc-700/50',
}

function StatusBadge({ status }: { status: SizingStatus }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded font-mono text-[10px] uppercase tracking-wide ${STATUS_STYLES[status]}`}>
      {status.replace('_', ' ')}
    </span>
  )
}

// ─── Position sparkline ───────────────────────────────────────────────────────

function MiniSparkline({ prices }: { prices: number[] }) {
  if (!prices || prices.length < 2) {
    return <span className="font-mono text-[10px] text-text-tertiary">—</span>
  }
  const w = 56, h = 24, pad = 2
  const min = Math.min(...prices)
  const max = Math.max(...prices)
  const range = max - min || 1
  const pts = prices.map((p, i) => {
    const x = pad + (i / (prices.length - 1)) * (w - pad * 2)
    const y = h - pad - ((p - min) / range) * (h - pad * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const isUp = prices[prices.length - 1] >= prices[0]
  const color = isUp ? '#4ade80' : '#f87171'  // accent-green / accent-red
  return (
    <svg width={w} height={h} className="inline-block">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

const TAB_STYLE =
  'px-4 py-2.5 font-mono text-xs uppercase tracking-widest border-b-2 transition-colors cursor-pointer ' +
  'data-[state=active]:border-accent-blue data-[state=active]:text-text-primary ' +
  'data-[state=inactive]:border-transparent data-[state=inactive]:text-text-tertiary data-[state=inactive]:hover:text-text-secondary'

type StatusFilter = 'ALL' | SizingStatus | 'ZERO'

const STATUS_FILTER_LABELS: { value: StatusFilter; label: string }[] = [
  { value: 'ALL',         label: 'All' },
  { value: 'OVERWEIGHT',  label: 'Overweight' },
  { value: 'ALIGNED',     label: 'Aligned' },
  { value: 'UNDERWEIGHT', label: 'Underweight' },
  { value: 'NOT_HELD',    label: 'Not Held' },
  { value: 'ZERO',        label: 'Zero' },
]

export function PortfolioPage() {
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState('overview')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('ALL')
  const [cashInput, setCashInput] = useState('')
  const [cashError, setCashError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const { data: summary, isLoading: summaryLoading } = usePortfolioSummary()
  const { data: history, isLoading: historyLoading } = usePortfolioHistory(52)
  const { data: positions, isLoading: positionsLoading } = usePortfolioPositions()
  const { data: equityScreener, isLoading: equityLoading } = useEquityScreener()
  const { data: cashData, isLoading: cashLoading } = useCash()
  const cashMutation = useCashUpdate()
  const addPositionMutation = useAddPosition()
  const sellPositionMutation = useSellPosition()
  const closePositionMutation = useClosePosition()
  const { data: tradesData, isLoading: tradesLoading } = useTrades()
  const { data: regimeData } = useRegime()
  const { data: sparklines } = usePortfolioSparklines()
  const [regimePanelOpen, setRegimePanelOpen] = useState(true)

  // Add-position form state
  const [showAddForm, setShowAddForm] = useState(false)
  const [addForm, setAddForm] = useState({
    ticker: '', direction: 'LONG' as 'LONG' | 'SHORT', currency: 'USD' as 'EUR' | 'USD',
    entry_price: '', size_eur: '', conviction: '', stop_loss: '', target_1: '',
  })
  const [addFormError, setAddFormError] = useState<string | null>(null)

  // Sell form state
  const [sellingTicker, setSellingTicker] = useState<string | null>(null)
  const [sellForm, setSellForm] = useState({ price: '', currency: 'USD' as 'EUR' | 'USD' })
  const [sellError, setSellError] = useState<string | null>(null)
  const [lastPnl, setLastPnl] = useState<{ ticker: string; pnl: number } | null>(null)

  function handleAddPosition() {
    const ticker = addForm.ticker.trim().toUpperCase()
    const entry_price = parseFloat(addForm.entry_price)
    const size_eur = parseFloat(addForm.size_eur)
    if (!ticker) return setAddFormError('Ticker is required')
    if (isNaN(entry_price) || entry_price <= 0) return setAddFormError('Entry price must be > 0')
    if (isNaN(size_eur) || size_eur <= 0) return setAddFormError('Size must be > 0')
    setAddFormError(null)
    addPositionMutation.mutate(
      {
        ticker, direction: addForm.direction, currency: addForm.currency, entry_price, size_eur,
        conviction: addForm.conviction ? parseFloat(addForm.conviction) : undefined,
        stop_loss:  addForm.stop_loss  ? parseFloat(addForm.stop_loss)  : undefined,
        target_1:   addForm.target_1   ? parseFloat(addForm.target_1)   : undefined,
      },
      {
        onSuccess: () => {
          setAddForm({ ticker: '', direction: 'LONG', currency: 'USD', entry_price: '', size_eur: '', conviction: '', stop_loss: '', target_1: '' })
          setShowAddForm(false)
        },
        onError: () => setAddFormError('Failed to save — check API connection'),
      }
    )
  }

  function handleSell(ticker: string) {
    const price = parseFloat(sellForm.price)
    if (isNaN(price) || price <= 0) return setSellError('Sell price must be > 0')
    setSellError(null)
    sellPositionMutation.mutate(
      { ticker, payload: { sell_price: price, currency: sellForm.currency } },
      {
        onSuccess: (res) => {
          setLastPnl({ ticker, pnl: res.pnl_eur })
          setSellingTicker(null)
          setSellForm({ price: '', currency: 'USD' })
        },
        onError: () => setSellError('Failed to sell — check API connection'),
      }
    )
  }

  // Only treat cash as "set" if it has actually been saved (updated_at is present)
  const cashUpdatedAt = cashData?.updated_at ?? null
  const savedCash = cashUpdatedAt !== null ? (cashData?.cash_eur ?? null) : null

  function handleCashAction(action: 'set' | 'add' | 'reduce') {
    const amount = parseFloat(cashInput.replace(/[,€\s]/g, ''))
    if (isNaN(amount) || amount < 0) {
      setCashError('Enter a valid positive number')
      return
    }
    setCashError(null)
    cashMutation.mutate(
      { action, amount },
      {
        onSuccess: () => {
          setCashInput('')
          inputRef.current?.focus()
        },
        onError: () => setCashError('Failed to save — check API connection'),
      }
    )
  }

  const historyArr = Array.isArray(history) ? history : (history as any)?.data ?? []
  const last12Weeks = historyArr.slice(-12)

  const positionsArr = Array.isArray(positions) ? positions : (positions as any)?.data ?? []
  const sortedPositions = [...positionsArr].sort(
    (a, b) => Math.abs(b.unrealized_pnl_eur) - Math.abs(a.unrealized_pnl_eur)
  )

  // Position Sizes section data
  const equityArr = equityScreener?.data ?? []
  const overweightCount = positionsArr.filter((p: any) => {
    const eq = equityArr.find((e: any) => e.ticker === p.ticker)
    return eq && getSizingStatus(p.size_eur, eq.position_eur ?? 0) === 'OVERWEIGHT'
  }).length
  const positionMap = new Map<string, number>(positionsArr.map((p: any) => [p.ticker, p.size_eur as number]))

  const isZeroRow = (r: any) =>
    (r.weight_pct ?? 0) === 0 &&
    (r.position_eur ?? 0) === 0 &&
    (positionMap.get(r.ticker) ?? 0) === 0

  const sortedEquity = [...equityArr].sort((a: any, b: any) => {
    const aZero = isZeroRow(a) ? 1 : 0
    const bZero = isZeroRow(b) ? 1 : 0
    if (aZero !== bZero) return aZero - bZero
    return (b.weight_pct ?? 0) - (a.weight_pct ?? 0)
  })

  const filteredEquity = sortedEquity.filter((row: any) => {
    if (statusFilter === 'ZERO') return isZeroRow(row)
    if (isZeroRow(row)) return statusFilter === 'ALL' ? true : false
    if (statusFilter === 'ALL') return true
    const currentEur = positionMap.get(row.ticker) ?? 0
    return getSizingStatus(currentEur, row.position_eur ?? 0) === statusFilter
  })

  const totalRecommendedEur = sortedEquity.reduce((s, r) => s + (r.position_eur ?? 0), 0)
  const totalCurrentEur = positionsArr.reduce((s: number, p: any) => s + (p.size_eur ?? 0), 0)
  const remainingCash = savedCash !== null ? savedCash : (summary?.nav_eur ?? 0) - totalCurrentEur
  // NAV = cash on hand + deployed positions (dynamic when cash is set manually)
  const nav = savedCash !== null ? savedCash + totalCurrentEur : (summary?.nav_eur ?? 0)

  const overConcentrated = sortedEquity.filter(r => (r.weight_pct ?? 0) > 8)

  return (
    <Shell title="Portfolio Overview">
      <Tabs.Root value={activeTab} onValueChange={setActiveTab}>
        <Tabs.List className="flex border-b border-border-subtle mb-5 -mx-6 px-6">
          <Tabs.Trigger value="overview" className={TAB_STYLE}>
            Overview
          </Tabs.Trigger>
          <Tabs.Trigger value="position-sizes" className={TAB_STYLE}>
            Position Sizes
          </Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="overview">
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

      {/* Regime Panel */}
      {regimeData?.regime && (
        <RegimePanel
          open={regimePanelOpen}
          onToggle={() => setRegimePanelOpen(v => !v)}
          regime={regimeData.regime}
          score={regimeData.score}
          sizeMultiplier={regimeData.size_multiplier ?? 0.7}
          components={regimeData.components as unknown as Record<string, number> | undefined}
          sectorRegimes={regimeData.sector_regimes}
          computedAt={regimeData.computed_at}
        />
      )}

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

      {/* Open Positions table */}
      <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden mb-6">
        <div className="px-4 py-3 border-b border-border-subtle flex items-center justify-between">
          <span className="font-mono text-xs text-text-tertiary uppercase tracking-widest">
            Open Positions
          </span>
          <div className="flex items-center gap-3">
            <span className="font-mono text-xs text-text-secondary">
              {positionsArr.length} positions
            </span>
            {overweightCount > 0 && (
              <span className="inline-block px-2 py-0.5 rounded font-mono text-[10px] uppercase tracking-wide bg-red-500/15 text-red-400 border border-red-500/30">
                {overweightCount} overweight
              </span>
            )}
            <button
              onClick={() => { setShowAddForm(v => !v); setAddFormError(null) }}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded font-mono text-xs bg-accent-blue/15 text-accent-blue border border-accent-blue/30 hover:bg-accent-blue/25 transition-colors"
            >
              <Plus className="w-3 h-3" />
              Add Position
            </button>
          </div>
        </div>

        {/* Inline add-position form */}
        {showAddForm && (
          <div className="px-4 py-4 border-b border-border-subtle bg-bg-elevated">
            <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-3">
              New Position
            </div>
            <div className="flex flex-wrap gap-2 items-end">
              {/* Ticker */}
              <div className="flex flex-col gap-1">
                <label className="font-mono text-[10px] text-text-tertiary uppercase tracking-wide">Ticker</label>
                <input
                  type="text"
                  value={addForm.ticker}
                  onChange={e => setAddForm(f => ({ ...f, ticker: e.target.value.toUpperCase() }))}
                  onKeyDown={e => e.key === 'Enter' && handleAddPosition()}
                  placeholder="AAPL"
                  className="w-24 bg-bg-surface border border-border-subtle rounded px-2 h-8 font-mono text-sm text-text-primary outline-none focus:border-accent-blue placeholder:text-text-tertiary uppercase"
                />
              </div>
              {/* Direction */}
              <div className="flex flex-col gap-1">
                <label className="font-mono text-[10px] text-text-tertiary uppercase tracking-wide">Direction</label>
                <select
                  value={addForm.direction}
                  onChange={e => setAddForm(f => ({ ...f, direction: e.target.value as 'LONG' | 'SHORT' }))}
                  className="w-24 bg-bg-surface border border-border-subtle rounded px-2 h-8 font-mono text-sm text-text-primary outline-none focus:border-accent-blue"
                >
                  <option value="LONG">LONG</option>
                  <option value="SHORT">SHORT</option>
                </select>
              </div>
              {/* Currency */}
              <div className="flex flex-col gap-1">
                <label className="font-mono text-[10px] text-text-tertiary uppercase tracking-wide">Currency</label>
                <select
                  value={addForm.currency}
                  onChange={e => setAddForm(f => ({ ...f, currency: e.target.value as 'EUR' | 'USD' }))}
                  className="w-20 bg-bg-surface border border-border-subtle rounded px-2 h-8 font-mono text-sm text-text-primary outline-none focus:border-accent-blue"
                >
                  <option value="USD">USD $</option>
                  <option value="EUR">EUR €</option>
                </select>
              </div>
              {/* Entry Price */}
              <div className="flex flex-col gap-1">
                <label className="font-mono text-[10px] text-text-tertiary uppercase tracking-wide">
                  Entry ({addForm.currency === 'EUR' ? '€' : '$'})
                </label>
                <input
                  type="number" min="0" step="0.01"
                  value={addForm.entry_price}
                  onChange={e => setAddForm(f => ({ ...f, entry_price: e.target.value }))}
                  onKeyDown={e => e.key === 'Enter' && handleAddPosition()}
                  placeholder="0.00"
                  className="w-28 bg-bg-surface border border-border-subtle rounded px-2 h-8 font-mono text-sm text-text-primary outline-none focus:border-accent-blue placeholder:text-text-tertiary [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
                />
              </div>
              {/* Size EUR */}
              <div className="flex flex-col gap-1">
                <label className="font-mono text-[10px] text-text-tertiary uppercase tracking-wide">Size (€)</label>
                <input
                  type="number" min="0" step="100"
                  value={addForm.size_eur}
                  onChange={e => setAddForm(f => ({ ...f, size_eur: e.target.value }))}
                  onKeyDown={e => e.key === 'Enter' && handleAddPosition()}
                  placeholder="0"
                  className="w-28 bg-bg-surface border border-border-subtle rounded px-2 h-8 font-mono text-sm text-text-primary outline-none focus:border-accent-blue placeholder:text-text-tertiary [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
                />
              </div>
              {/* Stop Loss */}
              <div className="flex flex-col gap-1">
                <label className="font-mono text-[10px] text-text-tertiary uppercase tracking-wide">Stop ($)</label>
                <input
                  type="number" min="0" step="0.01"
                  value={addForm.stop_loss}
                  onChange={e => setAddForm(f => ({ ...f, stop_loss: e.target.value }))}
                  onKeyDown={e => e.key === 'Enter' && handleAddPosition()}
                  placeholder="optional"
                  className="w-28 bg-bg-surface border border-border-subtle rounded px-2 h-8 font-mono text-sm text-text-primary outline-none focus:border-accent-blue placeholder:text-text-tertiary [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
                />
              </div>
              {/* Target 1 */}
              <div className="flex flex-col gap-1">
                <label className="font-mono text-[10px] text-text-tertiary uppercase tracking-wide">Target ($)</label>
                <input
                  type="number" min="0" step="0.01"
                  value={addForm.target_1}
                  onChange={e => setAddForm(f => ({ ...f, target_1: e.target.value }))}
                  onKeyDown={e => e.key === 'Enter' && handleAddPosition()}
                  placeholder="optional"
                  className="w-28 bg-bg-surface border border-border-subtle rounded px-2 h-8 font-mono text-sm text-text-primary outline-none focus:border-accent-blue placeholder:text-text-tertiary [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
                />
              </div>
              {/* Conviction */}
              <div className="flex flex-col gap-1">
                <label className="font-mono text-[10px] text-text-tertiary uppercase tracking-wide">Conv (0–5)</label>
                <input
                  type="number" min="0" max="5" step="0.5"
                  value={addForm.conviction}
                  onChange={e => setAddForm(f => ({ ...f, conviction: e.target.value }))}
                  onKeyDown={e => e.key === 'Enter' && handleAddPosition()}
                  placeholder="optional"
                  className="w-28 bg-bg-surface border border-border-subtle rounded px-2 h-8 font-mono text-sm text-text-primary outline-none focus:border-accent-blue placeholder:text-text-tertiary [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
                />
              </div>
              {/* Actions */}
              <div className="flex gap-2 mb-0">
                <button
                  onClick={handleAddPosition}
                  disabled={addPositionMutation.isPending}
                  className="flex items-center gap-1.5 px-3 h-8 rounded font-mono text-xs bg-accent-green/15 text-accent-green border border-accent-green/30 hover:bg-accent-green/25 disabled:opacity-40 transition-colors"
                >
                  <Plus className="w-3 h-3" />
                  {addPositionMutation.isPending ? 'Saving…' : 'Save'}
                </button>
                <button
                  onClick={() => { setShowAddForm(false); setAddFormError(null) }}
                  className="flex items-center gap-1.5 px-3 h-8 rounded font-mono text-xs text-text-tertiary border border-border-subtle hover:text-text-secondary hover:border-border-active transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
            {addFormError && (
              <p className="mt-2 font-mono text-xs text-accent-red">{addFormError}</p>
            )}
          </div>
        )}
        {positionsLoading ? (
          <div className="p-4">
            <LoadingSkeleton rows={5} />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border-subtle">
                  {['Ticker', 'Direction', 'Entry', 'Current', 'P&L (€)', 'P&L (%)', 'Size (€)', '5d', 'Days', 'Conviction', ''].map(h => (
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
                  <>
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
                      <MiniSparkline prices={sparklines?.[pos.ticker] ?? []} />
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-mono text-sm text-text-secondary">{pos.days_held}d</span>
                    </td>
                    <td className="px-4 py-3">
                      <ConvictionDots conviction={pos.conviction} />
                    </td>
                    <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                      <button
                        onClick={() => {
                          if (sellingTicker === pos.ticker) { setSellingTicker(null); setSellError(null) }
                          else { setSellingTicker(pos.ticker); setSellForm({ price: '', currency: 'USD' }); setSellError(null) }
                        }}
                        className={`px-2.5 py-1 rounded font-mono text-[10px] border transition-colors ${
                          sellingTicker === pos.ticker
                            ? 'bg-accent-red/20 text-accent-red border-accent-red/40'
                            : 'bg-transparent text-text-tertiary border-border-subtle hover:text-accent-red hover:border-accent-red/40'
                        }`}
                      >
                        Sell
                      </button>
                    </td>
                  </tr>
                  {sellingTicker === pos.ticker && (
                    <tr key={`${pos.ticker}-sell`} className="bg-bg-elevated border-b border-border-subtle/50">
                      <td colSpan={11} className="px-4 py-3">
                        <div className="flex flex-wrap items-end gap-3">
                          <span className="font-mono text-xs text-text-tertiary uppercase tracking-wide">
                            Sell {pos.ticker}
                          </span>
                          {/* Sell currency */}
                          <div className="flex flex-col gap-1">
                            <label className="font-mono text-[10px] text-text-tertiary uppercase tracking-wide">Currency</label>
                            <select
                              value={sellForm.currency}
                              onChange={e => setSellForm(f => ({ ...f, currency: e.target.value as 'EUR' | 'USD' }))}
                              className="w-20 bg-bg-surface border border-border-subtle rounded px-2 h-8 font-mono text-sm text-text-primary outline-none focus:border-accent-red"
                            >
                              <option value="USD">USD $</option>
                              <option value="EUR">EUR €</option>
                            </select>
                          </div>
                          {/* Sell price */}
                          <div className="flex flex-col gap-1">
                            <label className="font-mono text-[10px] text-text-tertiary uppercase tracking-wide">
                              Sell Price ({sellForm.currency === 'EUR' ? '€' : '$'})
                            </label>
                            <input
                              type="number" min="0" step="0.01" autoFocus
                              value={sellForm.price}
                              onChange={e => setSellForm(f => ({ ...f, price: e.target.value }))}
                              onKeyDown={e => e.key === 'Enter' && handleSell(pos.ticker)}
                              placeholder="0.00"
                              className="w-32 bg-bg-surface border border-border-subtle rounded px-2 h-8 font-mono text-sm text-text-primary outline-none focus:border-accent-red placeholder:text-text-tertiary [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
                            />
                          </div>
                          <button
                            onClick={() => handleSell(pos.ticker)}
                            disabled={sellPositionMutation.isPending}
                            className="flex items-center gap-1.5 px-3 h-8 rounded font-mono text-xs bg-accent-red/15 text-accent-red border border-accent-red/30 hover:bg-accent-red/25 disabled:opacity-40 transition-colors"
                          >
                            {sellPositionMutation.isPending ? 'Selling…' : 'Confirm Sell'}
                          </button>
                          <button
                            onClick={() => { setSellingTicker(null); setSellError(null) }}
                            className="px-3 h-8 rounded font-mono text-xs text-text-tertiary border border-border-subtle hover:text-text-secondary hover:border-border-active transition-colors"
                          >
                            Cancel
                          </button>
                          {sellError && <span className="font-mono text-xs text-accent-red">{sellError}</span>}
                        </div>
                      </td>
                    </tr>
                  )}
                  </>
                ))}
                {sortedPositions.length === 0 && (
                  <tr>
                    <td colSpan={11} className="px-4 py-8 text-center font-mono text-sm text-text-tertiary">
                      No open positions
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── P&L / Trade History ─────────────────────────────────────────── */}
      {(() => {
        const trades = tradesData ?? []
        const closed = trades.filter((t: any) => t.status === 'closed' && t.pnl_eur != null)
        const totalPnl = closed.reduce((s: number, t: any) => s + (t.pnl_eur ?? 0), 0)
        const wins = closed.filter((t: any) => (t.pnl_eur ?? 0) > 0)
        const losses = closed.filter((t: any) => (t.pnl_eur ?? 0) <= 0)
        const winRate = closed.length > 0 ? (wins.length / closed.length) * 100 : 0
        const avgWin = wins.length > 0 ? wins.reduce((s: number, t: any) => s + t.pnl_eur, 0) / wins.length : 0
        const avgLoss = losses.length > 0 ? losses.reduce((s: number, t: any) => s + t.pnl_eur, 0) / losses.length : 0

        // Cumulative P&L over time
        const byDate = [...closed].sort((a: any, b: any) => (a.close_date ?? '').localeCompare(b.close_date ?? ''))
        let cum = 0
        const cumulativeData = byDate.map((t: any) => {
          cum += t.pnl_eur ?? 0
          return { date: t.close_date, ticker: t.ticker, pnl: t.pnl_eur, cumulative: cum }
        })

        return (
          <div className="mt-2">
            {/* last sell flash */}
            {lastPnl && (
              <div className={`mb-4 px-4 py-2.5 rounded border font-mono text-sm flex items-center justify-between ${lastPnl.pnl >= 0 ? 'bg-accent-green/10 border-accent-green/30 text-accent-green' : 'bg-accent-red/10 border-accent-red/30 text-accent-red'}`}>
                <span>{lastPnl.ticker} sold — P&L: {lastPnl.pnl >= 0 ? '+' : ''}€{lastPnl.pnl.toFixed(2)}</span>
                <button onClick={() => setLastPnl(null)} className="opacity-60 hover:opacity-100"><X className="w-3.5 h-3.5" /></button>
              </div>
            )}

            <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
              <div className="px-4 py-3 border-b border-border-subtle">
                <span className="font-mono text-xs text-text-tertiary uppercase tracking-widest">Realized P&L</span>
              </div>

              {tradesLoading ? (
                <div className="p-4"><LoadingSkeleton rows={3} /></div>
              ) : closed.length === 0 ? (
                <div className="px-4 py-8 text-center font-mono text-sm text-text-tertiary">No closed trades yet — sell a position to track P&L</div>
              ) : (
                <>
                  {/* Summary cards */}
                  <div className="grid grid-cols-4 divide-x divide-border-subtle border-b border-border-subtle">
                    {[
                      { label: 'Total Realized', value: `€${totalPnl.toFixed(2)}`, color: totalPnl >= 0 ? 'text-accent-green' : 'text-accent-red' },
                      { label: 'Win Rate', value: `${winRate.toFixed(0)}%  (${wins.length}W / ${losses.length}L)`, color: winRate >= 50 ? 'text-accent-green' : 'text-accent-red' },
                      { label: 'Avg Win', value: avgWin > 0 ? `+€${avgWin.toFixed(2)}` : '—', color: 'text-accent-green' },
                      { label: 'Avg Loss', value: avgLoss < 0 ? `€${avgLoss.toFixed(2)}` : '—', color: 'text-accent-red' },
                    ].map(({ label, value, color }) => (
                      <div key={label} className="px-4 py-3">
                        <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-1">{label}</div>
                        <div className={`font-mono text-lg font-semibold ${color}`}>{value}</div>
                      </div>
                    ))}
                  </div>

                  {/* Charts */}
                  <div className="grid grid-cols-2 gap-0 divide-x divide-border-subtle border-b border-border-subtle">
                    {/* Cumulative P&L */}
                    <div className="p-4">
                      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-3">Cumulative P&L</div>
                      <ResponsiveContainer width="100%" height={160}>
                        <AreaChart data={cumulativeData}>
                          <defs>
                            <linearGradient id="cPnlGrad" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="5%" stopColor={totalPnl >= 0 ? '#22c55e' : '#ef4444'} stopOpacity={0.2} />
                              <stop offset="95%" stopColor={totalPnl >= 0 ? '#22c55e' : '#ef4444'} stopOpacity={0} />
                            </linearGradient>
                          </defs>
                          <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                          <XAxis dataKey="date" tick={{ fill: '#52525b', fontSize: 9, fontFamily: 'IBM Plex Mono' }} tickLine={false} axisLine={{ stroke: '#27272a' }} />
                          <YAxis tick={{ fill: '#52525b', fontSize: 9, fontFamily: 'IBM Plex Mono' }} tickLine={false} axisLine={false} tickFormatter={v => `€${v}`} />
                          <Tooltip formatter={(v: any) => [`€${Number(v).toFixed(2)}`, 'Cumulative']} contentStyle={{ background: '#18181b', border: '1px solid #3f3f46', fontFamily: 'IBM Plex Mono', fontSize: 11 }} />
                          <ReferenceLine y={0} stroke="#3f3f46" />
                          <Area type="monotone" dataKey="cumulative" stroke={totalPnl >= 0 ? '#22c55e' : '#ef4444'} strokeWidth={2} fill="url(#cPnlGrad)" dot={false} />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                    {/* Per-trade P&L bars */}
                    <div className="p-4">
                      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-3">P&L per Trade</div>
                      <ResponsiveContainer width="100%" height={160}>
                        <BarChart data={byDate}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
                          <XAxis dataKey="ticker" tick={{ fill: '#52525b', fontSize: 9, fontFamily: 'IBM Plex Mono' }} tickLine={false} axisLine={{ stroke: '#27272a' }} />
                          <YAxis tick={{ fill: '#52525b', fontSize: 9, fontFamily: 'IBM Plex Mono' }} tickLine={false} axisLine={false} tickFormatter={v => `€${v}`} />
                          <Tooltip formatter={(v: any) => [`€${Number(v).toFixed(2)}`, 'P&L']} contentStyle={{ background: '#18181b', border: '1px solid #3f3f46', fontFamily: 'IBM Plex Mono', fontSize: 11 }} />
                          <ReferenceLine y={0} stroke="#3f3f46" />
                          <Bar dataKey="pnl_eur" radius={[2, 2, 0, 0]}>
                            {byDate.map((t: any, i: number) => (
                              <Cell key={i} fill={(t.pnl_eur ?? 0) >= 0 ? '#22c55e' : '#ef4444'} />
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>

                  {/* Trade history table */}
                  <div className="overflow-x-auto">
                    <table className="w-full">
                      <thead>
                        <tr className="border-b border-border-subtle">
                          {['Ticker', 'Dir', 'Buy Date', 'Buy Price', 'Sell Date', 'Sell Price', 'Size (€)', 'P&L (€)', 'P&L %'].map(h => (
                            <th key={h} className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {[...trades].sort((a: any, b: any) => (b.close_date ?? b.date ?? '').localeCompare(a.close_date ?? a.date ?? '')).map((t: any) => {
                          const pnlPct = t.pnl_eur != null && t.size_eur > 0 ? (t.pnl_eur / t.size_eur) * 100 : null
                          return (
                            <tr key={t.id} className="border-b border-border-subtle/40 hover:bg-bg-elevated transition-colors">
                              <td className="px-4 py-2.5">
                                <span className="font-mono text-sm font-semibold text-accent-blue cursor-pointer hover:underline" onClick={() => navigate(`/ticker/${t.ticker}`)}>{t.ticker}</span>
                              </td>
                              <td className="px-4 py-2.5"><DirectionBadge direction={t.direction === 'LONG' ? 'BULL' : 'BEAR'} size="sm" /></td>
                              <td className="px-4 py-2.5"><span className="font-mono text-xs text-text-secondary">{t.date}</span></td>
                              <td className="px-4 py-2.5">
                                <span className="font-mono text-xs text-text-primary">{t.currency === 'EUR' ? '€' : '$'}{t.entry_price?.toFixed(2)}</span>
                              </td>
                              <td className="px-4 py-2.5"><span className="font-mono text-xs text-text-secondary">{t.close_date ?? '—'}</span></td>
                              <td className="px-4 py-2.5">
                                {t.close_price != null
                                  ? <span className="font-mono text-xs text-text-primary">{t.close_currency === 'EUR' ? '€' : '$'}{t.close_price?.toFixed(2)}</span>
                                  : <span className="font-mono text-xs text-text-tertiary">open</span>
                                }
                              </td>
                              <td className="px-4 py-2.5"><MonoNumber value={t.size_eur} prefix="€" /></td>
                              <td className="px-4 py-2.5">
                                {t.pnl_eur != null
                                  ? <MonoNumber value={t.pnl_eur} prefix="€" colorBySign />
                                  : <span className="font-mono text-xs text-text-tertiary">—</span>
                                }
                              </td>
                              <td className="px-4 py-2.5">
                                {pnlPct != null
                                  ? <MonoNumber value={pnlPct} suffix="%" colorBySign />
                                  : <span className="font-mono text-xs text-text-tertiary">—</span>
                                }
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          </div>
        )
      })()}

        </Tabs.Content>

        <Tabs.Content value="position-sizes">
      {/* ── POSITION SIZES SECTION ── */}
      <div>

        {/* Cash management widget */}
        <div className="mb-4 bg-bg-surface border border-border-subtle rounded overflow-hidden">
          <div className="px-4 py-3 border-b border-border-subtle flex items-center gap-2">
            <Wallet className="w-3.5 h-3.5 text-accent-green" />
            <span className="font-mono text-xs text-text-tertiary uppercase tracking-widest">
              Cash Balance
            </span>
            {cashUpdatedAt && (
              <span className="ml-auto font-mono text-[10px] text-text-tertiary">
                saved {new Date(cashUpdatedAt).toLocaleString()}
              </span>
            )}
          </div>
          <div className="px-4 py-4">
            <div className="flex items-end gap-6 mb-4">
              {/* Current cash display */}
              <div>
                <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-1">
                  {savedCash !== null ? 'Saved Cash' : 'Computed Cash (NAV − deployed)'}
                </div>
                {cashLoading ? (
                  <div className="h-8 w-32 bg-bg-elevated rounded animate-pulse" />
                ) : (
                  <div className={`font-mono text-2xl font-semibold ${remainingCash >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
                    €{remainingCash.toLocaleString('en-IE', { maximumFractionDigits: 0 })}
                  </div>
                )}
              </div>
              {savedCash !== null && (
                <div className="mb-0.5">
                  <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-1">% of NAV</div>
                  <div className="font-mono text-sm text-text-secondary">
                    {nav > 0 ? ((savedCash / nav) * 100).toFixed(1) : '—'}%
                  </div>
                </div>
              )}
            </div>

            {/* Input + action buttons */}
            <div className="flex items-center gap-2 flex-wrap">
              <div className="flex items-center gap-1 bg-bg-elevated border border-border-subtle rounded px-3 h-9 flex-1 min-w-[160px] max-w-[240px]">
                <span className="font-mono text-sm text-text-tertiary">€</span>
                <input
                  ref={inputRef}
                  type="number"
                  min="0"
                  step="100"
                  value={cashInput}
                  onChange={e => { setCashInput(e.target.value); setCashError(null) }}
                  onKeyDown={e => e.key === 'Enter' && handleCashAction('set')}
                  placeholder="0"
                  className="flex-1 bg-transparent font-mono text-sm text-text-primary outline-none placeholder:text-text-tertiary [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
                />
              </div>

              <button
                onClick={() => handleCashAction('set')}
                disabled={cashMutation.isPending || !cashInput}
                title="Set cash to this exact amount"
                className="flex items-center gap-1.5 px-3 h-9 rounded font-mono text-xs bg-accent-blue/15 text-accent-blue border border-accent-blue/30 hover:bg-accent-blue/25 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <RefreshCw className="w-3 h-3" />
                Set
              </button>

              <button
                onClick={() => handleCashAction('add')}
                disabled={cashMutation.isPending || !cashInput}
                title="Add amount to current cash"
                className="flex items-center gap-1.5 px-3 h-9 rounded font-mono text-xs bg-green-500/15 text-green-400 border border-green-500/30 hover:bg-green-500/25 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <Plus className="w-3 h-3" />
                Add
              </button>

              <button
                onClick={() => handleCashAction('reduce')}
                disabled={cashMutation.isPending || !cashInput}
                title="Subtract amount from current cash"
                className="flex items-center gap-1.5 px-3 h-9 rounded font-mono text-xs bg-red-500/15 text-red-400 border border-red-500/30 hover:bg-red-500/25 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <Minus className="w-3 h-3" />
                Reduce
              </button>

              {cashMutation.isPending && (
                <span className="font-mono text-xs text-text-tertiary">Saving…</span>
              )}
              {cashMutation.isSuccess && !cashMutation.isPending && (
                <span className="font-mono text-xs text-accent-green">Saved</span>
              )}
            </div>

            {cashError && (
              <p className="mt-2 font-mono text-xs text-accent-red">{cashError}</p>
            )}
          </div>
        </div>

        {/* Concentration warnings */}
        {overConcentrated.map(r => (
          <div
            key={r.ticker}
            role="alert"
            className="flex items-center gap-3 mb-3 px-4 py-3 rounded border border-red-500/40 bg-red-500/10 font-mono text-xs text-red-400"
          >
            <AlertTriangle className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
            <span>
              ⚠ <span className="font-semibold">{r.ticker}</span> recommended at{' '}
              <span className="font-semibold">{(r.weight_pct ?? 0).toFixed(1)}%</span> — exceeds 8% max concentration rule.
            </span>
          </div>
        ))}

        {/* Summary panel */}
        {!equityLoading && !summaryLoading && (() => {
          const deployedPct  = nav > 0 ? (totalCurrentEur / nav) * 100 : 0
          const cashPct      = nav > 0 ? (Math.max(0, remainingCash) / nav) * 100 : 0
          const recPct       = nav > 0 ? (totalRecommendedEur / nav) * 100 : 0
          const gapEur       = totalCurrentEur - totalRecommendedEur
          const overDeployed = gapEur > 0
          return (
            <div className="mb-4 bg-bg-surface border border-border-subtle rounded overflow-hidden">
              {/* Three stat columns */}
              <div className="grid grid-cols-3 divide-x divide-border-subtle">
                {([
                  {
                    label:  'Signal Recommended',
                    eur:    totalRecommendedEur,
                    pct:    recPct,
                    color:  'text-text-primary',
                  },
                  {
                    label:  'Currently Deployed',
                    eur:    totalCurrentEur,
                    pct:    deployedPct,
                    color:  'text-accent-blue',
                  },
                  {
                    label:  'Cash',
                    eur:    remainingCash,
                    pct:    cashPct,
                    color:  remainingCash >= 0 ? 'text-accent-green' : 'text-accent-red',
                  },
                ] as const).map(({ label, eur, pct, color }) => (
                  <div key={label} className="px-4 py-4">
                    <div className="font-mono text-[11px] uppercase tracking-widest text-text-tertiary mb-2">
                      {label}
                    </div>
                    <div className={`font-mono text-[28px] font-semibold leading-none ${color}`}>
                      €{eur.toLocaleString('en-IE', { maximumFractionDigits: 0 })}
                    </div>
                    <div className="font-mono text-xs text-text-tertiary mt-2">
                      {pct.toFixed(1)}% of NAV
                    </div>
                  </div>
                ))}
              </div>

              {/* NAV allocation bar + legend */}
              <div className="px-4 py-2.5 border-t border-border-subtle">
                <div className="flex gap-px h-1.5 rounded overflow-hidden mb-2 bg-bg-elevated">
                  <div
                    style={{ width: `${Math.min(100, deployedPct)}%` }}
                    className="bg-accent-blue h-full transition-all"
                  />
                  <div
                    style={{ width: `${Math.min(100, cashPct)}%` }}
                    className="bg-zinc-700 h-full transition-all"
                  />
                </div>
                <div className="flex items-center font-mono text-[10px] text-text-tertiary">
                  <span className="flex items-center gap-1 mr-4">
                    <span className="inline-block w-2 h-1.5 rounded-sm bg-accent-blue" />
                    Deployed {deployedPct.toFixed(0)}%
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="inline-block w-2 h-1.5 rounded-sm bg-zinc-700" />
                    Cash {cashPct.toFixed(0)}%
                  </span>
                  <span className={`ml-auto font-semibold ${overDeployed ? 'text-amber-400' : 'text-text-tertiary'}`}>
                    {overDeployed
                      ? `↑ €${gapEur.toLocaleString('en-IE', { maximumFractionDigits: 0 })} more deployed than signal recommends`
                      : `↓ €${Math.abs(gapEur).toLocaleString('en-IE', { maximumFractionDigits: 0 })} less deployed than signal recommends`
                    }
                  </span>
                </div>
              </div>
            </div>
          )
        })()}

        {/* Sizes table */}
        <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
          <div className="px-4 py-3 border-b border-border-subtle flex items-center justify-between gap-4">
            <span className="font-mono text-xs text-text-tertiary uppercase tracking-widest shrink-0">
              Position Sizes
            </span>
            {/* Status filter pills */}
            <div className="flex items-center gap-1.5 flex-wrap">
              {STATUS_FILTER_LABELS.map(({ value, label }) => {
                const isActive = statusFilter === value
                const count = value === 'ALL'
                  ? sortedEquity.length
                  : value === 'ZERO'
                    ? sortedEquity.filter((row: any) => isZeroRow(row)).length
                    : sortedEquity.filter((row: any) => {
                        if (isZeroRow(row)) return false
                        const cur = positionMap.get(row.ticker) ?? 0
                        return getSizingStatus(cur, row.position_eur ?? 0) === value
                      }).length
                const activeClass = value === 'ALL'
                  ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/40'
                  : value === 'ZERO'
                    ? 'bg-zinc-700/40 text-zinc-400 border-zinc-600/50'
                    : STATUS_STYLES[value as SizingStatus]
                return (
                  <button
                    key={value}
                    onClick={() => setStatusFilter(value)}
                    className={
                      `px-2.5 py-1 rounded font-mono text-[10px] uppercase tracking-wide border transition-colors ` +
                      (isActive
                        ? activeClass
                        : 'bg-transparent text-text-tertiary border-border-subtle hover:text-text-secondary hover:border-border-active')
                    }
                  >
                    {label}
                    <span className="ml-1 opacity-60">({count})</span>
                  </button>
                )
              })}
            </div>
            <span className="font-mono text-xs text-text-secondary shrink-0">
              {filteredEquity.length}/{sortedEquity.length} · {savedCash !== null ? 'Real' : 'Paper'} NAV €{nav.toLocaleString('en-IE', { maximumFractionDigits: 0 })}
            </span>
          </div>
          {equityLoading || positionsLoading ? (
            <div className="p-4">
              <LoadingSkeleton rows={8} />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full" aria-label="Position Sizes">
                <thead>
                  <tr className="border-b border-border-subtle">
                    {['Ticker', 'Rec. Weight %', 'Rec. Size EUR', 'Current EUR', 'Delta EUR', 'Status'].map(h => (
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
                  {filteredEquity.map((row: any) => {
                    const currentEur = positionMap.get(row.ticker) ?? 0
                    const status = getSizingStatus(currentEur, row.position_eur ?? 0)
                    const deltaEur = currentEur - (row.position_eur ?? 0)
                    const zeroRow = isZeroRow(row)
                    return (
                      <tr
                        key={row.ticker}
                        className={`border-b border-border-subtle/50 hover:bg-bg-elevated transition-colors ${zeroRow ? 'opacity-35' : ''}`}
                      >
                        <td className="px-4 py-3">
                          <span
                            className="font-mono text-sm font-semibold text-accent-blue cursor-pointer hover:underline"
                            onClick={() => navigate(`/ticker/${row.ticker}`)}
                          >
                            {row.ticker}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <span className={`font-mono text-sm font-semibold ${(row.weight_pct ?? 0) > 8 ? 'text-accent-red' : 'text-text-primary'}`}>
                            {(row.weight_pct ?? 0).toFixed(2)}%
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <MonoNumber value={row.position_eur ?? 0} prefix="€" />
                        </td>
                        <td className="px-4 py-3">
                          {currentEur > 0
                            ? <MonoNumber value={currentEur} prefix="€" />
                            : <span className="font-mono text-sm text-text-tertiary">—</span>
                          }
                        </td>
                        <td className="px-4 py-3">
                          {currentEur > 0
                            ? <MonoNumber value={deltaEur} prefix="€" colorBySign />
                            : <span className="font-mono text-sm text-text-tertiary">—</span>
                          }
                        </td>
                        <td className="px-4 py-3">
                          <StatusBadge status={status} />
                        </td>
                      </tr>
                    )
                  })}
                  {filteredEquity.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-4 py-8 text-center font-mono text-sm text-text-tertiary">
                        {sortedEquity.length === 0
                          ? 'No equity sizing data — run signal_engine.py first'
                          : statusFilter === 'ZERO'
                            ? 'No zero-value tickers'
                            : `No ${statusFilter === 'ALL' ? '' : statusFilter.toLowerCase().replace('_', ' ') + ' '}positions`
                        }
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          {/* Footnote */}
          <div className="px-4 py-3 border-t border-border-subtle">
            <span className="font-mono text-[10px] text-text-tertiary">
              Position sizes are Quarter-Kelly estimates. Not financial advice.
            </span>
          </div>
        </div>
      </div>
        </Tabs.Content>
      </Tabs.Root>
    </Shell>
  )
}
