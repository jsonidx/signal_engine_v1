/**
 * FundamentalScreenerTable
 *
 * Full-universe fundamental analysis screener backed by
 * GET /api/screeners/fundamentals (reads signals_output/fundamental_*.csv).
 *
 * Features:
 *  - Ticker search
 *  - Quick-filter presets (Low PE, High Growth, Strong Balance, etc.)
 *  - Sortable columns
 *  - Color-coded metrics (green = attractive, red = concerning)
 *  - Sub-score bars per category
 *  - Export CSV
 *  - Clickable rows → Ticker Deep Dive
 */

import { useState, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Download, Search, X } from 'lucide-react'
import { clsx } from 'clsx'
import { api, type FundamentalRow } from '../lib/api'
import { LoadingSkeleton } from './ui/LoadingSkeleton'
import { EmptyState } from './ui/EmptyState'

// ─── Formatting helpers ───────────────────────────────────────────────────────

function fmtPct(v: number | null, decimals = 1): string {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(decimals)}%`
}

function fmtCap(v: number | null): string {
  if (v == null) return '—'
  if (v >= 1e12) return `$${(v / 1e12).toFixed(1)}T`
  if (v >= 1e9)  return `$${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6)  return `$${(v / 1e6).toFixed(0)}M`
  return `$${v.toFixed(0)}`
}

function fmtPE(v: number | null): string {
  if (v == null) return '—'
  if (v <= 0 || v > 500) return 'N/M'
  return v.toFixed(1) + 'x'
}

function fmtAnalyst(v: number | null): string {
  if (v == null) return '—'
  // yfinance: 1.0 = Strong Buy, 5.0 = Strong Sell
  const labels: Record<number, string> = { 1: 'Str Buy', 2: 'Buy', 3: 'Hold', 4: 'Sell', 5: 'Str Sell' }
  const nearest = Math.round(v)
  return labels[nearest] ?? v.toFixed(1)
}

// ─── Color helpers ────────────────────────────────────────────────────────────

function compositeColor(v: number | null): string {
  if (v == null) return 'text-text-tertiary'
  if (v >= 55) return 'text-accent-green'
  if (v >= 35) return 'text-accent-amber'
  return 'text-accent-red'
}

function growthColor(v: number | null): string {
  if (v == null) return 'text-text-tertiary'
  if (v >= 0.15)  return 'text-accent-green'
  if (v >= 0)     return 'text-text-secondary'
  return 'text-accent-red'
}

function marginColor(v: number | null): string {
  if (v == null) return 'text-text-tertiary'
  if (v >= 0.20) return 'text-accent-green'
  if (v >= 0.05) return 'text-text-secondary'
  return 'text-accent-red'
}

function peColor(v: number | null): string {
  if (v == null || v <= 0 || v > 500) return 'text-text-tertiary'
  if (v <= 15)  return 'text-accent-green'
  if (v <= 30)  return 'text-text-secondary'
  return 'text-accent-red'
}

function analystColor(v: number | null): string {
  if (v == null) return 'text-text-tertiary'
  if (v <= 2.0) return 'text-accent-green'
  if (v <= 3.0) return 'text-text-secondary'
  return 'text-accent-red'
}

// ─── Sub-score bar (0–4 scale) ────────────────────────────────────────────────

function ScorePips({ value, label }: { value: number | null; label: string }) {
  const v = value ?? 0
  return (
    <div className="flex flex-col items-center gap-0.5 min-w-[30px]">
      <div className="flex gap-0.5">
        {[1, 2, 3, 4].map(i => (
          <div
            key={i}
            className={clsx(
              'w-1.5 h-1.5 rounded-sm',
              i <= v
                ? v >= 3 ? 'bg-accent-green' : v >= 2 ? 'bg-accent-amber' : 'bg-accent-red'
                : 'bg-bg-elevated'
            )}
          />
        ))}
      </div>
      <span className="font-mono text-[8px] text-text-tertiary">{label}</span>
    </div>
  )
}

// ─── Sort header ──────────────────────────────────────────────────────────────

type SortDir = 'asc' | 'desc'

function SortTh({
  label,
  col,
  active,
  dir,
  onSort,
  className,
}: {
  label: string
  col: string
  active: string
  dir: SortDir
  onSort: (c: string) => void
  className?: string
}) {
  const isActive = col === active
  return (
    <th
      className={clsx('px-3 py-2.5 text-left cursor-pointer select-none group', className)}
      onClick={() => onSort(col)}
    >
      <span className={clsx(
        'font-mono text-[9px] uppercase tracking-widest transition-colors',
        isActive ? 'text-accent-blue' : 'text-text-tertiary group-hover:text-text-secondary'
      )}>
        {label}{isActive ? (dir === 'desc' ? ' ↓' : ' ↑') : ''}
      </span>
    </th>
  )
}

// ─── Quick filter presets ─────────────────────────────────────────────────────

type Preset = 'all' | 'value' | 'growth' | 'quality' | 'analyst_buy'

const PRESETS: { id: Preset; label: string; desc: string }[] = [
  { id: 'all',         label: 'All',          desc: 'Show all tickers'                             },
  { id: 'value',       label: 'Value',         desc: 'Low PE (fwd ≤ 15) + composite ≥ 35'          },
  { id: 'growth',      label: 'Growth',        desc: 'Revenue growth ≥ 15% + earnings growth ≥ 0'  },
  { id: 'quality',     label: 'Quality',       desc: 'Operating margin ≥ 15% + composite ≥ 40'     },
  { id: 'analyst_buy', label: 'Analyst Buys',  desc: 'Analyst rating ≤ 2.0 (Buy or better)'        },
]

function applyPreset(rows: FundamentalRow[], preset: Preset): FundamentalRow[] {
  switch (preset) {
    case 'value':
      return rows.filter(r =>
        r.pe_forward != null && r.pe_forward > 0 && r.pe_forward <= 15 &&
        (r.composite ?? 0) >= 35
      )
    case 'growth':
      return rows.filter(r =>
        r.revenue_growth_yoy != null && r.revenue_growth_yoy >= 0.15 &&
        r.earnings_growth_yoy != null && r.earnings_growth_yoy >= 0
      )
    case 'quality':
      return rows.filter(r =>
        r.operating_margin != null && r.operating_margin >= 0.15 &&
        (r.composite ?? 0) >= 40
      )
    case 'analyst_buy':
      return rows.filter(r =>
        r.analyst_rating != null && r.analyst_rating <= 2.0 &&
        (r.analyst_count ?? 0) >= 5
      )
    default:
      return rows
  }
}

// ─── Export CSV ───────────────────────────────────────────────────────────────

function exportCSV(rows: FundamentalRow[], asOf: string | null) {
  if (!rows.length) return
  const keys: (keyof FundamentalRow)[] = [
    'ticker', 'name', 'sector', 'price', 'mkt_cap',
    'pe_forward', 'pe_trailing', 'revenue_growth_yoy', 'earnings_growth_yoy',
    'operating_margin', 'roe', 'analyst_rating', 'analyst_count', 'target_mean',
    'composite', 'score_growth', 'score_quality', 'score_balance', 'score_valuation',
  ]
  const lines = [
    keys.join(','),
    ...rows.map(r => keys.map(k => JSON.stringify(r[k] ?? '')).join(',')),
  ]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `fundamentals_${asOf ?? 'latest'}.csv`
  a.click()
}

// ─── Main component ───────────────────────────────────────────────────────────

export function FundamentalScreenerTable() {
  const navigate = useNavigate()

  const [search,  setSearch]  = useState('')
  const [preset,  setPreset]  = useState<Preset>('all')
  const [sortCol, setSortCol] = useState('composite')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const handleSort = useCallback((col: string) => {
    if (col === sortCol) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortCol(col); setSortDir('desc') }
  }, [sortCol])

  const { data, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ['screeners', 'fundamentals'],
    queryFn:  () => api.screenerFundamentals(),
    retry: 1,
    staleTime: 5 * 60 * 1000,
  })

  const allRows = data?.data ?? []

  const displayed = useMemo(() => {
    let rows = applyPreset(allRows, preset)

    if (search.trim()) {
      const q = search.trim().toUpperCase()
      rows = rows.filter(r =>
        r.ticker.toUpperCase().includes(q) ||
        r.name.toUpperCase().includes(q)
      )
    }

    return [...rows].sort((a, b) => {
      const av = (a as unknown as Record<string, unknown>)[sortCol]
      const bv = (b as unknown as Record<string, unknown>)[sortCol]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortDir === 'desc' ? bv - av : av - bv
      }
      return sortDir === 'desc'
        ? String(bv).localeCompare(String(av))
        : String(av).localeCompare(String(bv))
    })
  }, [allRows, preset, search, sortCol, sortDir])

  if (isLoading) return <LoadingSkeleton rows={10} />

  if (!data?.data_available) {
    return <EmptyState message="No fundamental data found" command="./run_master.sh" />
  }

  return (
    <div className="space-y-4">

      {/* ── Controls ──────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 flex-wrap">

        {/* Search */}
        <div className="relative flex items-center">
          <Search size={12} className="absolute left-2 text-text-tertiary pointer-events-none" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search ticker / name…"
            className="pl-7 pr-6 py-1.5 bg-bg-surface border border-border-subtle rounded font-mono text-xs text-text-primary placeholder:text-text-tertiary focus:outline-none focus:border-border-active w-48"
          />
          {search && (
            <button
              onClick={() => setSearch('')}
              className="absolute right-1.5 text-text-tertiary hover:text-text-secondary"
            >
              <X size={11} />
            </button>
          )}
        </div>

        {/* Preset pills */}
        <div className="flex items-center gap-1 flex-wrap">
          {PRESETS.map(p => (
            <button
              key={p.id}
              title={p.desc}
              onClick={() => setPreset(p.id)}
              className={clsx(
                'font-mono text-[10px] px-2.5 py-1 rounded border transition-colors',
                preset === p.id
                  ? 'bg-accent-blue/15 text-accent-blue border-accent-blue/30'
                  : 'text-text-tertiary border-border-subtle hover:text-text-secondary hover:border-border-active'
              )}
            >
              {p.label}
            </button>
          ))}
        </div>

        <button
          onClick={() => exportCSV(displayed, data.as_of)}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-xs font-mono border border-border-subtle text-text-secondary hover:text-text-primary hover:border-border-active rounded transition-colors"
        >
          <Download size={11} />
          Export CSV
        </button>

        {dataUpdatedAt > 0 && (
          <span className="font-mono text-[10px] text-text-tertiary">
            {displayed.length} of {allRows.length} · as of {data.as_of}
          </span>
        )}
      </div>

      {/* ── Table ──────────────────────────────────────────────────────── */}
      {displayed.length === 0 ? (
        <EmptyState message="No tickers match the selected filters" command="" />
      ) : (
        <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full" aria-label="Fundamental screener">
              <thead>
                <tr className="border-b border-border-subtle bg-bg-elevated/40">
                  <SortTh label="Ticker"    col="ticker"             active={sortCol} dir={sortDir} onSort={handleSort} />
                  <SortTh label="Score"     col="composite"          active={sortCol} dir={sortDir} onSort={handleSort} />
                  <th className="px-3 py-2.5 text-left font-mono text-[9px] uppercase tracking-widest text-text-tertiary hidden xl:table-cell">
                    Sub-scores
                  </th>
                  <SortTh label="Mkt Cap"   col="mkt_cap"            active={sortCol} dir={sortDir} onSort={handleSort} className="hidden md:table-cell" />
                  <SortTh label="PE Fwd"    col="pe_forward"         active={sortCol} dir={sortDir} onSort={handleSort} />
                  <SortTh label="Rev Grw"   col="revenue_growth_yoy" active={sortCol} dir={sortDir} onSort={handleSort} />
                  <SortTh label="EPS Grw"   col="earnings_growth_yoy" active={sortCol} dir={sortDir} onSort={handleSort} className="hidden lg:table-cell" />
                  <SortTh label="Op Margin" col="operating_margin"   active={sortCol} dir={sortDir} onSort={handleSort} className="hidden lg:table-cell" />
                  <SortTh label="ROE"       col="roe"                active={sortCol} dir={sortDir} onSort={handleSort} className="hidden xl:table-cell" />
                  <SortTh label="Analyst"   col="analyst_rating"     active={sortCol} dir={sortDir} onSort={handleSort} className="hidden md:table-cell" />
                  <SortTh label="Target"    col="target_mean"        active={sortCol} dir={sortDir} onSort={handleSort} className="hidden xl:table-cell" />
                </tr>
              </thead>
              <tbody>
                {displayed.map(row => {
                  const upside = row.price && row.target_mean
                    ? (row.target_mean - row.price) / row.price
                    : null
                  return (
                    <tr
                      key={row.ticker}
                      onClick={() => navigate(`/ticker/${row.ticker}`)}
                      className="border-b border-border-subtle/50 cursor-pointer hover:bg-bg-elevated transition-colors"
                    >
                      {/* Ticker + name */}
                      <td className="px-3 py-2.5">
                        <div className="font-mono text-xs font-semibold text-accent-blue">{row.ticker}</div>
                        <div className="font-mono text-[9px] text-text-tertiary truncate max-w-[100px] hidden md:block">
                          {row.sector}
                        </div>
                      </td>

                      {/* Composite score */}
                      <td className="px-3 py-2.5">
                        <div className="flex items-center gap-2">
                          <div className="w-10 h-1.5 bg-bg-elevated rounded overflow-hidden flex-shrink-0">
                            <div
                              style={{ width: `${row.composite ?? 0}%` }}
                              className={clsx(
                                'h-full rounded',
                                (row.composite ?? 0) >= 55 ? 'bg-accent-green' :
                                (row.composite ?? 0) >= 35 ? 'bg-accent-amber' : 'bg-accent-red'
                              )}
                            />
                          </div>
                          <span className={clsx('font-mono text-xs font-semibold', compositeColor(row.composite))}>
                            {row.composite?.toFixed(0) ?? '—'}
                          </span>
                        </div>
                      </td>

                      {/* Sub-score pips */}
                      <td className="px-3 py-2.5 hidden xl:table-cell">
                        <div className="flex items-end gap-2">
                          <ScorePips value={row.score_valuation}  label="Val" />
                          <ScorePips value={row.score_growth}     label="Grw" />
                          <ScorePips value={row.score_quality}    label="Qlty" />
                          <ScorePips value={row.score_balance}    label="Bal" />
                          <ScorePips value={row.score_earnings}   label="EPS" />
                          <ScorePips value={row.score_analyst}    label="Ana" />
                        </div>
                      </td>

                      {/* Market cap */}
                      <td className="px-3 py-2.5 font-mono text-xs text-text-secondary hidden md:table-cell">
                        {fmtCap(row.mkt_cap)}
                      </td>

                      {/* PE Forward */}
                      <td className="px-3 py-2.5">
                        <span className={clsx('font-mono text-xs', peColor(row.pe_forward))}>
                          {fmtPE(row.pe_forward)}
                        </span>
                      </td>

                      {/* Revenue growth */}
                      <td className="px-3 py-2.5">
                        <span className={clsx('font-mono text-xs', growthColor(row.revenue_growth_yoy))}>
                          {fmtPct(row.revenue_growth_yoy)}
                        </span>
                      </td>

                      {/* EPS growth */}
                      <td className="px-3 py-2.5 hidden lg:table-cell">
                        <span className={clsx('font-mono text-xs', growthColor(row.earnings_growth_yoy))}>
                          {fmtPct(row.earnings_growth_yoy)}
                        </span>
                      </td>

                      {/* Operating margin */}
                      <td className="px-3 py-2.5 hidden lg:table-cell">
                        <span className={clsx('font-mono text-xs', marginColor(row.operating_margin))}>
                          {row.operating_margin != null
                            ? `${(row.operating_margin * 100).toFixed(1)}%`
                            : '—'}
                        </span>
                      </td>

                      {/* ROE */}
                      <td className="px-3 py-2.5 hidden xl:table-cell">
                        <span className={clsx(
                          'font-mono text-xs',
                          row.roe != null
                            ? row.roe >= 0.15 ? 'text-accent-green' : row.roe >= 0 ? 'text-text-secondary' : 'text-accent-red'
                            : 'text-text-tertiary'
                        )}>
                          {row.roe != null ? `${(row.roe * 100).toFixed(1)}%` : '—'}
                        </span>
                      </td>

                      {/* Analyst rating */}
                      <td className="px-3 py-2.5 hidden md:table-cell">
                        <div>
                          <span className={clsx('font-mono text-xs', analystColor(row.analyst_rating))}>
                            {fmtAnalyst(row.analyst_rating)}
                          </span>
                          {row.analyst_count != null && (
                            <span className="font-mono text-[9px] text-text-tertiary ml-1">
                              ({row.analyst_count})
                            </span>
                          )}
                        </div>
                      </td>

                      {/* Analyst target + upside */}
                      <td className="px-3 py-2.5 hidden xl:table-cell">
                        {row.target_mean != null ? (
                          <div>
                            <span className="font-mono text-xs text-text-secondary">
                              ${row.target_mean.toFixed(2)}
                            </span>
                            {upside != null && (
                              <span className={clsx(
                                'font-mono text-[9px] ml-1',
                                upside >= 0 ? 'text-accent-green' : 'text-accent-red'
                              )}>
                                {upside >= 0 ? '+' : ''}{(upside * 100).toFixed(0)}%
                              </span>
                            )}
                          </div>
                        ) : (
                          <span className="font-mono text-xs text-text-tertiary">—</span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Footer */}
          <div className="px-4 py-2 border-t border-border-subtle/50 bg-bg-elevated/30">
            <p className="font-mono text-[9px] text-text-tertiary">
              Score 0–100 · Sub-scores 0–4 · PE N/M = negative or {'>'} 500x · Analyst: 1 = Strong Buy → 5 = Strong Sell
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
