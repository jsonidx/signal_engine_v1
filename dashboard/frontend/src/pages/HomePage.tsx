import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Grid3x3, ListOrdered, FileText, Send, Loader2, CheckCircle, AlertTriangle, Star, X, Plus } from 'lucide-react'
import { clsx } from 'clsx'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Shell } from '../components/layout/Shell'
import { DirectionBadge } from '../components/ui/DirectionBadge'
import { RegimeBadge } from '../components/ui/RegimeBadge'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { useRegime } from '../hooks/useRegime'
import { useHeatmap } from '../hooks/useHeatmap'
import {
  usePortfolioSummary,
  usePortfolioPositions,
  usePortfolioSparklines,
  useEquityScreener,
} from '../hooks/usePortfolio'
import { api } from '../lib/api'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function regimeAlert(regime: string): { text: string; cls: string } {
  switch (regime) {
    case 'RISK_OFF':
      return { text: 'RISK-OFF — reduce sizing, tighten stops, no new longs without catalyst', cls: 'bg-accent-red/10 border-accent-red/30 text-accent-red' }
    case 'TRANSITIONAL':
      return { text: 'TRANSITIONAL — selective entries only, conviction ≥ 3/5 required', cls: 'bg-accent-amber/10 border-accent-amber/30 text-accent-amber' }
    case 'RISK_ON':
      return { text: 'RISK-ON — full deployment permitted, scale into high-conviction setups', cls: 'bg-accent-green/10 border-accent-green/30 text-accent-green' }
    default:
      return { text: 'Regime unknown — await data refresh', cls: 'bg-bg-elevated border-border-subtle text-text-tertiary' }
  }
}

function agreementColor(score: number): string {
  if (score >= 0.70) return 'text-accent-green'
  if (score >= 0.50) return 'text-accent-amber'
  return 'text-text-tertiary'
}

function estimateSizeEur(agreement: number, nav: number): number {
  // Simple heuristic: agreement drives position size within 1–8% of equity allocation (65%)
  const pct = Math.min(0.08, Math.max(0.01, agreement * 0.08))
  return Math.round(pct * nav * 0.65)
}

// ─── Mini sparkline ───────────────────────────────────────────────────────────

function MiniSparkline({ prices }: { prices: number[] }) {
  if (!prices || prices.length < 2) return <span className="text-text-tertiary text-[10px] font-mono">—</span>
  const w = 48, h = 20, pad = 1
  const min = Math.min(...prices), max = Math.max(...prices)
  const range = max - min || 1
  const pts = prices.map((p, i) => {
    const x = pad + (i / (prices.length - 1)) * (w - pad * 2)
    const y = h - pad - ((p - min) / range) * (h - pad * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const isUp = prices[prices.length - 1] >= prices[0]
  return (
    <svg width={w} height={h} className="inline-block">
      <polyline points={pts} fill="none" stroke={isUp ? '#4ade80' : '#f87171'} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

// ─── Signal card ──────────────────────────────────────────────────────────────

function SignalCard({ row, nav }: { row: any; nav: number }) {
  const navigate = useNavigate()
  const sizeEur = estimateSizeEur(row.signal_agreement_score, nav)
  return (
    <div
      onClick={() => navigate(`/ticker/${row.ticker}`)}
      className="bg-bg-surface border border-border-subtle rounded p-3 cursor-pointer hover:border-border-active hover:bg-bg-elevated transition-colors"
    >
      <div className="flex items-center justify-between mb-2">
        <span className="font-mono text-sm font-semibold text-accent-blue">{row.ticker}</span>
        <DirectionBadge direction={row.pre_resolved_direction} size="sm" />
      </div>
      <div className="flex items-center justify-between mb-1.5">
        <span className={clsx('font-mono text-xs font-semibold', agreementColor(row.signal_agreement_score))}>
          {Math.round(row.signal_agreement_score * 100)}% agree
        </span>
        <span className="font-mono text-xs text-text-secondary">≈€{sizeEur.toLocaleString()}</span>
      </div>
      {row.sector && (
        <div className="font-mono text-[10px] text-text-tertiary truncate">{row.sector}</div>
      )}
      <div className="mt-2 text-[10px] font-mono text-accent-blue/70 hover:text-accent-blue">
        Deep Dive →
      </div>
    </div>
  )
}

// ─── Portfolio mini summary ───────────────────────────────────────────────────

function PortfolioMini() {
  const navigate = useNavigate()
  const { data: summary, isLoading: sLoading } = usePortfolioSummary()
  const { data: positions } = usePortfolioPositions()
  const { data: equityScreener } = useEquityScreener()
  const { data: sparklines } = usePortfolioSparklines()

  const posArr = Array.isArray(positions) ? positions : (positions as any)?.data ?? []
  const equityArr = equityScreener?.data ?? []

  const overweightCount = posArr.filter((p: any) => {
    const eq = equityArr.find((e: any) => e.ticker === p.ticker)
    if (!eq) return false
    const ratio = eq.position_eur > 0 ? p.size_eur / eq.position_eur : 0
    return ratio > 1.15
  }).length

  const totalPnl = posArr.reduce((s: number, p: any) => s + (p.unrealized_pnl_eur ?? 0), 0)

  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Portfolio</span>
        <button
          onClick={() => navigate('/portfolio')}
          className="font-mono text-[10px] text-accent-blue hover:underline"
        >
          Full view →
        </button>
      </div>

      {sLoading ? (
        <LoadingSkeleton rows={2} />
      ) : (
        <>
          <div className="grid grid-cols-3 gap-3 mb-3">
            <div>
              <div className="font-mono text-[10px] text-text-tertiary mb-0.5">NAV</div>
              <div className="font-mono text-sm font-semibold text-text-primary">
                €{(summary?.nav_eur ?? 0).toLocaleString()}
              </div>
            </div>
            <div>
              <div className="font-mono text-[10px] text-text-tertiary mb-0.5">Weekly</div>
              <div className={clsx('font-mono text-sm font-semibold',
                (summary?.weekly_return_pct ?? 0) >= 0 ? 'text-accent-green' : 'text-accent-red'
              )}>
                {(summary?.weekly_return_pct ?? 0) >= 0 ? '+' : ''}{(summary?.weekly_return_pct ?? 0).toFixed(2)}%
              </div>
            </div>
            <div>
              <div className="font-mono text-[10px] text-text-tertiary mb-0.5">Open P&L</div>
              <div className={clsx('font-mono text-sm font-semibold', totalPnl >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                {totalPnl >= 0 ? '+' : ''}€{Math.abs(totalPnl).toFixed(0)}
              </div>
            </div>
          </div>

          {/* Position sparklines */}
          {posArr.length > 0 && (
            <div className="space-y-1.5">
              {posArr.slice(0, 5).map((pos: any) => (
                <div key={pos.ticker} className="flex items-center gap-2">
                  <span className="font-mono text-[11px] font-semibold text-accent-blue w-12 flex-shrink-0">{pos.ticker}</span>
                  <MiniSparkline prices={sparklines?.[pos.ticker] ?? []} />
                  <span className={clsx('font-mono text-[10px] ml-auto',
                    pos.unrealized_pnl_pct >= 0 ? 'text-accent-green' : 'text-accent-red'
                  )}>
                    {pos.unrealized_pnl_pct >= 0 ? '+' : ''}{pos.unrealized_pnl_pct?.toFixed(1)}%
                  </span>
                </div>
              ))}
              {posArr.length > 5 && (
                <div className="font-mono text-[10px] text-text-tertiary">+{posArr.length - 5} more</div>
              )}
            </div>
          )}

          <div className="flex items-center gap-2 mt-2 pt-2 border-t border-border-subtle/50">
            <span className="font-mono text-[10px] text-text-secondary">{posArr.length} positions</span>
            {overweightCount > 0 && (
              <span className="inline-block px-1.5 py-0.5 rounded font-mono text-[9px] uppercase tracking-wide bg-red-500/15 text-red-400 border border-red-500/30">
                {overweightCount} overweight
              </span>
            )}
          </div>
        </>
      )}
    </div>
  )
}

// ─── Telegram alert button ────────────────────────────────────────────────────

function TelegramButton() {
  const [state, setState] = useState<'idle' | 'loading' | 'done' | 'error'>('idle')
  const [output, setOutput] = useState<string | null>(null)
  const [isDryRun, setIsDryRun] = useState(true)

  const handleSend = async () => {
    setState('loading')
    setOutput(null)
    try {
      const res = await api.sendTelegramAlert(isDryRun)
      setOutput(res.output)
      setState('done')
    } catch (e: any) {
      setOutput(e?.response?.data?.detail ?? 'Request failed')
      setState('error')
    }
  }

  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-4">
      <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-3">
        Weekly Alert
      </div>
      <div className="flex items-center gap-3 mb-3">
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input
            type="checkbox"
            checked={isDryRun}
            onChange={e => setIsDryRun(e.target.checked)}
            className="accent-accent-blue"
          />
          <span className="font-mono text-xs text-text-secondary">Dry run</span>
        </label>
        <button
          onClick={handleSend}
          disabled={state === 'loading'}
          className={clsx(
            'flex items-center gap-2 px-3 py-1.5 rounded font-mono text-xs border transition-colors',
            state === 'loading'
              ? 'opacity-50 cursor-not-allowed border-border-subtle text-text-tertiary'
              : isDryRun
                ? 'bg-bg-elevated border-border-subtle text-text-secondary hover:border-border-active'
                : 'bg-accent-blue/15 border-accent-blue/40 text-accent-blue hover:bg-accent-blue/25'
          )}
        >
          {state === 'loading' ? (
            <><Loader2 size={12} className="animate-spin" /> Sending…</>
          ) : state === 'done' ? (
            <><CheckCircle size={12} className="text-accent-green" /> {isDryRun ? 'Preview' : 'Sent'}</>
          ) : (
            <><Send size={12} /> {isDryRun ? 'Preview' : 'Send Alert'}</>
          )}
        </button>
      </div>
      {output && (
        <pre className="bg-bg-base rounded p-2 font-mono text-[10px] text-text-secondary overflow-x-auto max-h-48 whitespace-pre-wrap leading-relaxed">
          {output}
        </pre>
      )}
      {state === 'error' && (
        <p className="font-mono text-[10px] text-accent-red mt-1">{output}</p>
      )}
    </div>
  )
}

// ─── Favorites panel ─────────────────────────────────────────────────────────

function FavoritesPanel() {
  const qc = useQueryClient()
  const [input, setInput] = useState('')
  const [addErr, setAddErr] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['favorites'],
    queryFn: () => api.favoritesGet(),
  })

  const addMut = useMutation({
    mutationFn: (sym: string) => api.favoriteAdd(sym),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['favorites'] })
      setInput('')
      setAddErr(null)
    },
    onError: (e: any) => setAddErr(e?.response?.data?.detail ?? 'Failed to add'),
  })

  const removeMut = useMutation({
    mutationFn: (sym: string) => api.favoriteRemove(sym),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['favorites'] }),
  })

  const handleAdd = () => {
    const sym = input.trim().toUpperCase()
    if (!sym) return
    addMut.mutate(sym)
  }

  const favs = data?.favorites ?? []

  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-4">
      <div className="flex items-center gap-1.5 mb-3">
        <Star size={11} className="text-accent-amber" />
        <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Favorites</span>
      </div>

      {/* Add bar */}
      <div className="flex gap-1.5 mb-3">
        <input
          value={input}
          onChange={e => setInput(e.target.value.toUpperCase())}
          onKeyDown={e => e.key === 'Enter' && handleAdd()}
          placeholder="TICKER"
          maxLength={10}
          className="flex-1 min-w-0 bg-bg-base border border-border-subtle rounded px-2 py-1 font-mono text-xs text-text-primary placeholder:text-text-tertiary focus:outline-none focus:border-border-active"
        />
        <button
          onClick={handleAdd}
          disabled={addMut.isPending || !input.trim()}
          className="flex items-center gap-1 px-2 py-1 rounded border border-accent-blue/40 bg-accent-blue/10 text-accent-blue font-mono text-xs hover:bg-accent-blue/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <Plus size={11} />
          Add
        </button>
      </div>
      {addErr && <p className="font-mono text-[10px] text-accent-red mb-2">{addErr}</p>}

      {/* List */}
      {isLoading ? (
        <LoadingSkeleton rows={2} />
      ) : favs.length === 0 ? (
        <p className="font-mono text-[10px] text-text-tertiary">No favorites yet — add a ticker above.</p>
      ) : (
        <div className="space-y-1">
          {favs.map(f => (
            <div key={f.symbol} className="flex items-center justify-between py-0.5">
              <span className="font-mono text-xs font-semibold text-accent-blue">{f.symbol}</span>
              <button
                onClick={() => removeMut.mutate(f.symbol)}
                disabled={removeMut.isPending}
                className="text-text-tertiary hover:text-accent-red transition-colors disabled:opacity-40"
                title="Remove"
              >
                <X size={11} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Quick nav cards ──────────────────────────────────────────────────────────

const QUICK_LINKS = [
  { path: '/heatmap',    label: 'Signal Heatmap',     icon: Grid3x3,    desc: 'Multi-factor score matrix' },
  { path: '/rankings',   label: 'Daily Top-20',        icon: ListOrdered, desc: 'Ranked by composite score' },
  { path: '/resolution', label: 'Resolution & Accuracy', icon: FileText, desc: 'Conflict log + Claude accuracy' },
]

function QuickLinks() {
  const navigate = useNavigate()
  return (
    <div className="grid grid-cols-3 gap-3">
      {QUICK_LINKS.map(({ path, label, icon: Icon, desc }) => (
        <button
          key={path}
          onClick={() => navigate(path)}
          className="bg-bg-surface border border-border-subtle rounded p-3 text-left hover:border-border-active hover:bg-bg-elevated transition-colors"
        >
          <div className="flex items-center gap-2 mb-1">
            <Icon size={13} className="text-text-tertiary" />
            <span className="font-mono text-xs text-text-primary font-medium">{label}</span>
          </div>
          <div className="font-mono text-[10px] text-text-tertiary">{desc}</div>
        </button>
      ))}
    </div>
  )
}

// ─── Page ──────────────────────────────────────────────────────────────────────

export function HomePage() {
  const { data: regime } = useRegime()
  const { data: heatmapRows, isLoading: heatmapLoading } = useHeatmap()
  const { data: summary } = usePortfolioSummary()

  const nav = summary?.nav_eur ?? 50000

  // Top 7: directional signals sorted by agreement desc, exclude NEUTRAL
  const top7 = (heatmapRows ?? [])
    .filter(r => r.pre_resolved_direction !== 'NEUTRAL')
    .sort((a, b) => b.signal_agreement_score - a.signal_agreement_score)
    .slice(0, 7)

  const alert = regime ? regimeAlert(regime.regime) : null

  return (
    <Shell title="Monday Morning Brief">
      <div className="space-y-5">

        {/* Regime banner */}
        <div className={clsx('border rounded px-4 py-3 flex items-start gap-3', alert?.cls ?? 'bg-bg-elevated border-border-subtle')}>
          <AlertTriangle size={14} className="flex-shrink-0 mt-0.5" />
          <div className="min-w-0">
            <div className="flex items-center gap-3 mb-1 flex-wrap">
              {regime && <RegimeBadge regime={regime.regime} score={regime.score} />}
              <span className="font-mono text-[10px] text-text-tertiary">
                Size multiplier: {regime?.size_multiplier != null ? `${regime.size_multiplier}×` : '—'}
              </span>
            </div>
            <p className="font-mono text-xs leading-relaxed">{alert?.text}</p>
          </div>
        </div>

        {/* Top signals + sidebar */}
        <div className="grid grid-cols-3 gap-4">
          {/* Top 7 signals — 2/3 width */}
          <div className="col-span-2">
            <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
              Top Signals by Agreement
            </div>
            {heatmapLoading ? (
              <LoadingSkeleton rows={4} />
            ) : top7.length === 0 ? (
              <div className="bg-bg-surface border border-border-subtle rounded p-6 text-center font-mono text-xs text-text-tertiary">
                No signals yet — run <code>./run_master.sh</code>
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                {top7.map(row => (
                  <SignalCard key={row.ticker} row={row} nav={nav} />
                ))}
              </div>
            )}
          </div>

          {/* Right column: portfolio + favorites + telegram */}
          <div className="space-y-3">
            <PortfolioMini />
            <FavoritesPanel />
            <TelegramButton />
          </div>
        </div>

        {/* Quick links */}
        <QuickLinks />

      </div>
    </Shell>
  )
}
