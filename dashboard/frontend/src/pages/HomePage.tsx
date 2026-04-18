import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Grid3x3, ListOrdered, FileText, Send, Loader2, CheckCircle, AlertTriangle, Star, X, Plus, Brain, Activity, ChevronDown, ChevronRight, Download, Circle, CheckCircle2, XCircle, Clock, Cpu, Copy, Zap, RefreshCw } from 'lucide-react'
import { clsx } from 'clsx'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import axios from 'axios'
import { Shell } from '../components/layout/Shell'
import { AiSelectionTable } from '../components/AiSelectionTable'
import { CandidateSnapshotsTable } from '../components/CandidateSnapshotsTable'
import { DirectionBadge } from '../components/ui/DirectionBadge'
import { RegimeBadge } from '../components/ui/RegimeBadge'
import { ConvictionDots } from '../components/ui/ConvictionDots'
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

// ─── Hot Entry Panel ──────────────────────────────────────────────────────────

function RankChangePill({ value }: { value: string }) {
  if (value === 'NEW') return (
    <span className="font-mono text-[9px] px-1 py-0.5 rounded border bg-accent-blue/15 text-accent-blue border-accent-blue/30">NEW</span>
  )
  if (!value || value === '—') return <span className="font-mono text-[10px] text-text-tertiary/40">—</span>
  const delta = parseInt(value, 10)
  if (isNaN(delta)) return <span className="font-mono text-[10px] text-text-tertiary">{value}</span>
  if (delta > 0) return (
    <span className="font-mono text-[9px] px-1 py-0.5 rounded border bg-accent-green/15 text-accent-green border-accent-green/30">▲{value}</span>
  )
  return (
    <span className="font-mono text-[9px] px-1 py-0.5 rounded border bg-accent-red/15 text-accent-red border-accent-red/30">▼{value}</span>
  )
}

function HotEntryPanel() {
  const navigate = useNavigate()
  const [capital, setCapital] = useState(2000)

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['hot-entry', 'rankings'],
    queryFn: api.hotEntryRankings,
    staleTime: 5 * 60 * 1000,
    refetchInterval: 15 * 60 * 1000,
  })

  const rows = data?.data ?? []
  const hasHot  = rows.some(r => r.is_hot)
  const hasRows = rows.length > 0

  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
        <div className="flex items-center gap-2">
          <Zap size={13} className={hasHot ? 'text-accent-green' : hasRows ? 'text-accent-amber' : 'text-text-tertiary'} />
          <div>
            <h2 className="font-mono text-xs font-semibold text-text-primary">Hot Entry — Buy Today</h2>
            <p className="font-mono text-[10px] text-text-tertiary mt-0.5">
              {isLoading ? 'Loading…'
                : !hasRows ? 'No tickers in entry zone right now'
                : hasHot
                  ? `${rows.filter(r => r.is_hot).length} HOT · ${rows.filter(r => !r.is_hot).length} in zone · scored by EV + R:R + conviction`
                  : `${rows.length} ticker${rows.length !== 1 ? 's' : ''} in AI entry zone · scored by EV + R:R + conviction`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {/* Capital input */}
          <div className="flex items-center gap-1.5">
            <span className="font-mono text-[10px] text-text-tertiary">Capital €</span>
            <input
              type="number"
              value={capital}
              onChange={e => setCapital(Math.max(1, Number(e.target.value) || 2000))}
              className="w-20 px-2 py-0.5 font-mono text-xs bg-bg-elevated border border-border-subtle rounded text-text-primary text-right focus:outline-none focus:border-accent-blue"
            />
          </div>
          <button onClick={() => navigate('/deepdive')} className="font-mono text-[10px] text-accent-blue hover:underline">
            Full Deep Dive →
          </button>
          <button onClick={() => refetch()} disabled={isFetching} className="p-1.5 rounded border border-border-subtle text-text-tertiary hover:text-text-primary transition-colors disabled:opacity-40">
            <RefreshCw size={11} className={isFetching ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Body */}
      {isLoading ? (
        <div className="px-4 py-3"><LoadingSkeleton rows={3} /></div>
      ) : !hasRows ? (
        <div className="px-4 py-6 text-center font-mono text-xs text-text-tertiary">
          No tickers in entry zone right now — check back after market open or run Deep Dive.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left" aria-label="Hot entry candidates">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-elevated/30">
                <th className="px-3 py-2 text-center font-mono text-[9px] uppercase tracking-widest text-text-tertiary w-12">Rank</th>
                <th className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary">Ticker</th>
                <th className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary">Conv</th>
                <th className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary">Price / Zone</th>
                <th className="px-3 py-2 text-center font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
                  <div>T1 target</div>
                  <div className="text-text-tertiary/50 normal-case">% · median · P · profit</div>
                </th>
                <th className="px-3 py-2 text-center font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
                  <div>T2 target</div>
                  <div className="text-text-tertiary/50 normal-case">% · median · P · profit</div>
                </th>
                <th className="px-3 py-2 text-center font-mono text-[9px] uppercase tracking-widest text-text-tertiary">R:R</th>
                <th className="px-3 py-2 text-center font-mono text-[9px] uppercase tracking-widest text-text-tertiary">Score</th>
                <th className="px-3 py-2 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                const isBear = row.direction === 'BEAR'
                const entry = row.entry_low != null && row.entry_high != null
                  ? (row.entry_low + row.entry_high) / 2 : null
                const priceDelta = entry != null && row.current_price != null
                  ? ((row.current_price - entry) / entry) * 100 : null
                // For BEAR trades, profit is made when price falls — flip sign for display
                const dirMult = isBear ? -1 : 1

                return (
                  <tr
                    key={row.ticker}
                    onClick={() => navigate(`/ticker/${row.ticker}`)}
                    className={clsx(
                      'border-b border-border-subtle/50 last:border-0 cursor-pointer transition-colors',
                      row.is_hot ? 'hover:bg-accent-green/5' : 'hover:bg-bg-elevated'
                    )}
                  >
                    {/* Rank + change */}
                    <td className="px-3 py-3 text-center">
                      <div className={clsx(
                        'font-mono text-sm font-bold',
                        row.rank === 1 ? 'text-accent-green' : row.rank <= 3 ? 'text-text-primary' : 'text-text-secondary'
                      )}>
                        #{row.rank}
                      </div>
                      <div className="mt-0.5">
                        <RankChangePill value={row.rank_change} />
                      </div>
                    </td>

                    {/* Ticker */}
                    <td className="px-3 py-3">
                      <div className="font-mono text-sm font-semibold text-text-primary">{row.ticker}</div>
                      {row.equity_rank != null && (
                        <div className="font-mono text-[10px] text-text-tertiary">eq #{row.equity_rank}</div>
                      )}
                    </td>

                    {/* Conviction */}
                    <td className="px-3 py-3">
                      <ConvictionDots conviction={row.conviction ?? 0} />
                    </td>

                    {/* Price / Zone */}
                    <td className="px-3 py-3">
                      <div className="font-mono text-xs text-text-primary">
                        ${row.current_price?.toFixed(2) ?? '—'}
                      </div>
                      {row.entry_low != null && row.entry_high != null && (
                        <div className="font-mono text-[10px] text-text-tertiary whitespace-nowrap">
                          {row.entry_low}–{row.entry_high}
                          {priceDelta != null && (
                            <span className={clsx('ml-1', Math.abs(priceDelta) < 1 ? 'text-accent-green' : 'text-accent-amber')}>
                              ({priceDelta >= 0 ? '+' : ''}{priceDelta.toFixed(1)}%)
                            </span>
                          )}
                        </div>
                      )}
                    </td>

                    {/* T1 */}
                    <td className="px-3 py-3 text-center">
                      {row.t1_upside_pct != null ? (
                        <div className="space-y-0.5">
                          <div className={clsx('font-mono text-xs font-semibold', row.t1_upside_pct >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                            {row.t1_upside_pct >= 0 ? '+' : ''}{row.t1_upside_pct.toFixed(1)}%
                          </div>
                          {row.t1_median != null && <div className="font-mono text-[10px] text-text-tertiary">${row.t1_median.toFixed(2)}</div>}
                          {row.prob_t1 != null && (
                            <div className={clsx('font-mono text-[10px]', row.prob_t1 >= 0.65 ? 'text-accent-green' : row.prob_t1 >= 0.5 ? 'text-accent-amber' : 'text-text-tertiary')}>
                              {Math.round(row.prob_t1 * 100)}% hit
                            </div>
                          )}
                          {(() => {
                            const rawProfit = capital * row.t1_upside_pct / 100 * dirMult
                            const prob = row.prob_t1 ?? (row.conviction != null ? 0.1 + row.conviction * 0.14 : null)
                            const isEstimated = row.prob_t1 == null && prob != null
                            const ev = prob != null ? rawProfit * prob : null
                            return (
                              <div className={clsx('font-mono text-[10px] font-semibold border-t border-border-subtle/40 pt-0.5 mt-0.5', rawProfit >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                                {rawProfit >= 0 ? '+' : ''}€{Math.round(rawProfit)}
                                {ev != null && (
                                  <span className="text-text-tertiary font-normal ml-1">
                                    (EV €{Math.round(ev)}{isEstimated ? '*' : ''})
                                  </span>
                                )}
                              </div>
                            )
                          })()}
                        </div>
                      ) : <span className="font-mono text-xs text-text-tertiary">—</span>}
                    </td>

                    {/* T2 */}
                    <td className="px-3 py-3 text-center">
                      {row.t2_upside_pct != null ? (
                        <div className="space-y-0.5">
                          <div className={clsx('font-mono text-xs font-semibold', row.t2_upside_pct >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                            {row.t2_upside_pct >= 0 ? '+' : ''}{row.t2_upside_pct.toFixed(1)}%
                          </div>
                          {row.t2_median != null && <div className="font-mono text-[10px] text-text-tertiary">${row.t2_median.toFixed(2)}</div>}
                          {row.prob_t2 != null && (
                            <div className={clsx('font-mono text-[10px]', row.prob_t2 >= 0.5 ? 'text-accent-green' : row.prob_t2 >= 0.35 ? 'text-accent-amber' : 'text-text-tertiary')}>
                              {Math.round(row.prob_t2 * 100)}% hit
                            </div>
                          )}
                          {(() => {
                            const rawProfit = capital * row.t2_upside_pct / 100 * dirMult
                            const prob = row.prob_t2 ?? (row.conviction != null ? (0.1 + row.conviction * 0.14) * 0.6 : null)
                            const isEstimated = row.prob_t2 == null && prob != null
                            const ev = prob != null ? rawProfit * prob : null
                            return (
                              <div className={clsx('font-mono text-[10px] font-semibold border-t border-border-subtle/40 pt-0.5 mt-0.5', rawProfit >= 0 ? 'text-accent-green' : 'text-accent-red')}>
                                {rawProfit >= 0 ? '+' : ''}€{Math.round(rawProfit)}
                                {ev != null && (
                                  <span className="text-text-tertiary font-normal ml-1">
                                    (EV €{Math.round(ev)}{isEstimated ? '*' : ''})
                                  </span>
                                )}
                              </div>
                            )
                          })()}
                        </div>
                      ) : <span className="font-mono text-xs text-text-tertiary">—</span>}
                    </td>

                    {/* R:R */}
                    <td className="px-3 py-3 text-center">
                      {row.rr != null ? (
                        <span className={clsx('font-mono text-xs', row.rr >= 2 ? 'text-accent-green font-semibold' : row.rr >= 1 ? 'text-text-secondary' : 'text-accent-amber')}>
                          {row.rr.toFixed(1)}R
                        </span>
                      ) : <span className="font-mono text-xs text-text-tertiary">—</span>}
                    </td>

                    {/* Hot score */}
                    <td className="px-3 py-3 text-center">
                      <span className="font-mono text-xs text-text-tertiary">{row.hot_score.toFixed(0)}</span>
                    </td>

                    {/* Status badge */}
                    <td className="px-3 py-3">
                      {row.is_hot ? (
                        <span className="inline-flex items-center gap-1 font-mono text-[9px] px-1.5 py-0.5 rounded border bg-accent-green/15 text-accent-green border-accent-green/30 font-semibold whitespace-nowrap">
                          <Zap size={8} /> HOT
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 font-mono text-[9px] px-1.5 py-0.5 rounded border bg-accent-amber/10 text-accent-amber border-accent-amber/20 whitespace-nowrap">
                          IN ZONE
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <div className="px-4 py-2 border-t border-border-subtle/50 font-mono text-[9px] text-text-tertiary">
            EV = capital × upside × hit probability · * estimated from conviction (no pipeline prob available)
          </div>
        </div>
      )}
    </div>
  )
}

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

// ─── Pipeline Status card ─────────────────────────────────────────────────────

function PipelineStatusCard() {
  const { data, isLoading } = useQuery({
    queryKey: ['status', 'pipeline'],
    queryFn:  api.pipelineStatus,
    staleTime:       60 * 1000,
    refetchInterval: 5 * 60 * 1000,
    retry: 1,
  })

  if (isLoading || !data?.pipeline?.last_run) return null

  const p = data.pipeline
  const lastRun = p.last_run ? new Date(p.last_run) : null
  const isRecent = lastRun ? (Date.now() - lastRun.getTime()) < 25 * 3600 * 1000 : false
  const runtimeMins = p.total_runtime_secs != null ? Math.floor(p.total_runtime_secs / 60) : null
  const runtimeSecs = p.total_runtime_secs != null ? p.total_runtime_secs % 60 : null

  return (
    <div className="bg-bg-surface border border-border-subtle rounded px-4 py-2.5 flex items-center gap-5 flex-wrap">
      <div className="flex items-center gap-1.5 flex-shrink-0">
        <Activity size={11} className={isRecent ? 'text-accent-green' : 'text-accent-amber'} />
        <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Pipeline</span>
      </div>

      {lastRun && (
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[10px] text-text-tertiary">Last run</span>
          <span className="font-mono text-[10px] text-text-secondary">{lastRun.toLocaleString()}</span>
        </div>
      )}

      {runtimeMins != null && (
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[10px] text-text-tertiary">Runtime</span>
          <span className="font-mono text-[10px] text-text-secondary">
            {runtimeMins}m {String(runtimeSecs).padStart(2, '0')}s
          </span>
        </div>
      )}

      <div className="flex items-center gap-1.5">
        <span className="font-mono text-[10px] text-text-tertiary">Mode</span>
        <span className={clsx('font-mono text-[10px] font-medium', p.skip_ai ? 'text-accent-amber' : 'text-accent-green')}>
          {p.skip_ai ? 'data-only' : 'full run'}
        </span>
      </div>

      {p.cost_estimate && (
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[10px] text-text-tertiary">Cost</span>
          <span className="font-mono text-[10px] text-text-secondary">{p.cost_estimate}</span>
        </div>
      )}

      <div className="flex items-center gap-1.5">
        <span className="font-mono text-[10px] text-text-tertiary">Cache</span>
        <span className="font-mono text-[10px] text-text-secondary">{data.cache.warm_keys} warm keys</span>
      </div>
    </div>
  )
}

// ─── Copy report prompt button ────────────────────────────────────────────────

function buildReportPrompt(reportContent: string, runLabel?: string): string {
  const today = new Date().toISOString().split('T')[0]
  const lines: string[] = []
  lines.push(`You are an expert swing trader and quantitative analyst.`)
  lines.push(`Below is the full output from my signal engine pipeline run as of ${today}${runLabel ? ` (${runLabel})` : ''}.`)
  lines.push(``)
  lines.push(`TASK: Analyze this report and identify the TOP 3–5 candidates for swing trading THIS WEEK (holding period: 2–7 days).`)
  lines.push(``)
  lines.push(`For each candidate provide:`)
  lines.push(`1. Ticker & direction (LONG / SHORT)`)
  lines.push(`2. Why it stands out this week (catalyst, signal strength, regime alignment)`)
  lines.push(`3. Ideal entry zone / trigger condition`)
  lines.push(`4. Price target and stop-loss level`)
  lines.push(`5. Estimated risk/reward ratio`)
  lines.push(`6. Conviction rating (1–5) with brief justification`)
  lines.push(``)
  lines.push(`Also flag any RISK-OFF or regime warnings from the report that should affect position sizing.`)
  lines.push(``)
  lines.push(`--- PIPELINE REPORT START ---`)
  lines.push(reportContent.trim())
  lines.push(`--- PIPELINE REPORT END ---`)
  return lines.join('\n')
}

function CopyReportPromptButton() {
  const [state, setState] = useState<'idle' | 'loading' | 'copied' | 'error'>('idle')

  const handleCopy = async () => {
    setState('loading')
    try {
      const res = await api.workflowReportText()
      const prompt = buildReportPrompt(res.content, res.label)
      await navigator.clipboard.writeText(prompt)
      setState('copied')
      setTimeout(() => setState('idle'), 2500)
    } catch {
      setState('error')
      setTimeout(() => setState('idle'), 2500)
    }
  }

  return (
    <button
      onClick={e => { e.stopPropagation(); handleCopy() }}
      title="Copy LLM prompt: paste into ChatGPT / Claude / Grok to get top swing trade candidates"
      className={clsx(
        'flex items-center gap-1.5 font-mono text-[10px] px-2 py-1 rounded border transition-all flex-shrink-0',
        state === 'copied'
          ? 'bg-accent-green/20 text-accent-green border-accent-green/40'
          : state === 'error'
            ? 'bg-accent-red/10 text-accent-red border-accent-red/30'
            : state === 'loading'
              ? 'opacity-60 cursor-not-allowed border-border-subtle text-text-tertiary'
              : 'bg-bg-elevated border-border-subtle text-text-secondary hover:text-text-primary hover:border-border-active'
      )}
      disabled={state === 'loading'}
    >
      {state === 'loading' ? (
        <><Loader2 size={10} className="animate-spin" /> Building…</>
      ) : state === 'copied' ? (
        <>✓ Copied</>
      ) : state === 'error' ? (
        <>✗ Failed</>
      ) : (
        <><Copy size={10} /> Copy Prompt</>
      )}
    </button>
  )
}

// ─── Workflows panel ──────────────────────────────────────────────────────────

function workflowStatusIcon(status: string, conclusion: string | null) {
  if (status === 'in_progress') return <Loader2 size={12} className="text-accent-amber animate-spin" />
  if (status === 'queued')      return <Clock size={12} className="text-text-tertiary" />
  if (conclusion === 'success') return <CheckCircle2 size={12} className="text-accent-green" />
  if (conclusion === 'failure') return <XCircle size={12} className="text-accent-red" />
  return <Circle size={12} className="text-text-tertiary" />
}

function workflowStatusLabel(status: string, conclusion: string | null): string {
  if (status === 'in_progress') return 'running'
  if (status === 'queued')      return 'queued'
  if (conclusion === 'success') return 'success'
  if (conclusion === 'failure') return 'failed'
  if (conclusion === 'cancelled') return 'cancelled'
  return conclusion ?? status
}

function formatDuration(secs: number | null): string {
  if (secs == null) return '—'
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return m > 0 ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`
}

function WorkflowRunRow({ run }: { run: import('../lib/api').WorkflowRun }) {
  const [open, setOpen] = useState(false)
  const createdAt = new Date(run.created_at)

  return (
    <div className="border-b border-border-subtle last:border-0">
      {/* Row header — clickable */}
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-3 px-3 py-2 hover:bg-bg-elevated transition-colors text-left"
      >
        {open ? <ChevronDown size={11} className="text-text-tertiary flex-shrink-0" /> : <ChevronRight size={11} className="text-text-tertiary flex-shrink-0" />}
        {workflowStatusIcon(run.status, run.conclusion)}
        <span className="font-mono text-[11px] text-text-primary font-medium flex-1 truncate">{run.label}</span>
        <span className={clsx('font-mono text-[10px] px-1.5 py-0.5 rounded flex-shrink-0',
          run.conclusion === 'success' ? 'bg-accent-green/10 text-accent-green' :
          run.conclusion === 'failure' ? 'bg-accent-red/10 text-accent-red' :
          run.status === 'in_progress' ? 'bg-accent-amber/10 text-accent-amber' :
          'bg-bg-elevated text-text-tertiary'
        )}>
          {workflowStatusLabel(run.status, run.conclusion)}
        </span>
        <span className="font-mono text-[10px] text-text-tertiary flex-shrink-0 w-28 text-right">
          {createdAt.toLocaleDateString()} {createdAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      </button>

      {/* Expanded detail */}
      {open && (
        <div className="px-6 pb-3 pt-1 space-y-2">
          <div className="flex flex-wrap gap-x-5 gap-y-1">
            {run.event && (
              <span className="font-mono text-[10px] text-text-tertiary">
                Trigger: <span className="text-text-secondary">{run.event === 'schedule' ? 'Scheduled' : 'Manual'}</span>
              </span>
            )}
            {run.duration_secs != null && (
              <span className="font-mono text-[10px] text-text-tertiary">
                Duration: <span className="text-text-secondary">{formatDuration(run.duration_secs)}</span>
              </span>
            )}
            {run.run_number != null && (
              <span className="font-mono text-[10px] text-text-tertiary">
                Run: <span className="text-text-secondary">#{run.run_number}</span>
              </span>
            )}
          </div>

          {/* AI cost row */}
          <div className="flex items-center gap-2">
            <Cpu size={10} className="text-text-tertiary flex-shrink-0" />
            {run.has_ai === true && (
              <span className="font-mono text-[10px]">
                <span className="text-text-tertiary">AI synthesis: </span>
                <span className="text-accent-amber font-medium">included</span>
                {run.cost && <span className="text-text-tertiary ml-1">({run.cost})</span>}
              </span>
            )}
            {run.has_ai === false && (
              <span className="font-mono text-[10px]">
                <span className="text-text-tertiary">AI synthesis: </span>
                <span className="text-accent-green font-medium">skipped</span>
                <span className="text-text-tertiary ml-1">(€0.00)</span>
              </span>
            )}
            {run.has_ai === null && (
              <span className="font-mono text-[10px] text-text-tertiary">AI cost unknown</span>
            )}
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2 pt-0.5">
            {run.html_url && (
              <a
                href={run.html_url}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-[10px] text-text-tertiary hover:text-text-primary underline underline-offset-2"
              >
                View on GitHub
              </a>
            )}
            <a
              href="/api/workflows/report"
              download="0_run-pipeline.txt"
              className="flex items-center gap-1 font-mono text-[10px] bg-bg-elevated border border-border-subtle hover:border-border-active rounded px-2 py-1 text-text-secondary hover:text-text-primary transition-colors"
            >
              <Download size={10} />
              Download report
            </a>
          </div>
        </div>
      )}
    </div>
  )
}

function WorkflowsPanel() {
  const [collapsed, setCollapsed] = useState(true)
  const { data, isLoading } = useQuery({
    queryKey: ['workflows', 'runs'],
    queryFn: api.workflowRuns,
    staleTime: 2 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
    enabled: !collapsed,
  })

  const runs = data?.runs ?? []

  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      {/* Panel header */}
      <button
        onClick={() => setCollapsed(c => !c)}
        className="w-full flex items-center gap-2 px-4 py-2.5 hover:bg-bg-elevated transition-colors text-left"
      >
        {collapsed ? <ChevronRight size={12} className="text-text-tertiary" /> : <ChevronDown size={12} className="text-text-tertiary" />}
        <Activity size={11} className="text-text-tertiary" />
        <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary flex-1">Workflows Overview</span>
        {!collapsed && runs.length > 0 && (
          <span className="font-mono text-[10px] text-text-tertiary mr-2">{runs.length} runs</span>
        )}
        <CopyReportPromptButton />
      </button>

      {/* Panel body */}
      {!collapsed && (
        <div className="border-t border-border-subtle">
          {isLoading ? (
            <div className="px-4 py-3 flex items-center gap-2">
              <Loader2 size={12} className="animate-spin text-text-tertiary" />
              <span className="font-mono text-[10px] text-text-tertiary">Fetching workflow runs…</span>
            </div>
          ) : data?.error ? (
            <div className="px-4 py-3 font-mono text-[10px] text-accent-amber">{data.error}</div>
          ) : runs.length === 0 ? (
            <div className="px-4 py-3 font-mono text-[10px] text-text-tertiary">No workflow runs found</div>
          ) : (
            <div>
              {runs.map(run => (
                <WorkflowRunRow key={run.id} run={run} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Quick nav cards ──────────────────────────────────────────────────────────

const QUICK_LINKS = [
  { path: '/heatmap',    label: 'Signal Heatmap',       icon: Grid3x3,    desc: 'Multi-factor score matrix' },
  { path: '/rankings',   label: 'Daily Top-20',          icon: ListOrdered, desc: 'Ranked by composite score' },
  { path: '/resolution', label: 'Resolution & Accuracy', icon: FileText,   desc: 'Conflict log + Claude accuracy' },
  { path: '/deepdive',   label: 'AI Deep Dive',          icon: Brain,      desc: 'Claude thesis + scenarios' },
]

function QuickLinks() {
  const navigate = useNavigate()
  return (
    <div className="grid grid-cols-4 gap-3">
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

  const nav = summary?.nav_eur ?? 0

  // Top 7: all signals sorted by agreement desc (NEUTRAL included)
  const top7 = (heatmapRows ?? [])
    .filter(r => (r.signal_agreement_score ?? 0) > 0)
    .sort((a, b) => (b.signal_agreement_score ?? 0) - (a.signal_agreement_score ?? 0))
    .slice(0, 7)

  const alert = regime ? regimeAlert(regime.regime) : null

  return (
    <Shell title="Monday Morning Brief">
      <div className="space-y-5">

        {/* Pipeline status */}
        <PipelineStatusCard />

        {/* Workflows overview — collapsible */}
        <WorkflowsPanel />

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

        {/* Hot Entry — buy today */}
        <HotEntryPanel />

        {/* Candidate Pool — full width so Reason column has room */}
        <CandidateSnapshotsTable />

        {/* AI Quant Selection + Top signals + sidebar */}
        <div className="grid grid-cols-4 gap-4">
          {/* AI Selection */}
          <div className="col-span-1">
            <AiSelectionTable />
          </div>

          {/* Top 7 signals — 2 cols wide so signal cards stay readable */}
          <div className="col-span-2">
            <div className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary mb-2">
              Top Signals by Agreement
            </div>
            {heatmapLoading ? (
              <LoadingSkeleton rows={4} />
            ) : top7.length === 0 ? (
              <div className="bg-bg-surface border border-border-subtle rounded p-6 text-center font-mono text-xs text-text-tertiary">
                No heatmap data — run <code>./run_master.sh</code> to generate signals
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
