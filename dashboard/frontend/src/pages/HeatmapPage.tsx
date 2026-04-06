import { useState, useMemo, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { clsx } from 'clsx'
import { Shell } from '../components/layout/Shell'
import { DirectionBadge } from '../components/ui/DirectionBadge'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { useHeatmap } from '../hooks/useHeatmap'
import type { HeatmapRow } from '../lib/api'

const SIGNAL_MODULES = [
  { key: 'signal_engine', label: 'SigEng' },
  { key: 'squeeze',       label: 'Sqz'    },
  { key: 'options',       label: 'Opts'   },
  { key: 'dark_pool',     label: 'DkPl'   },
  { key: 'fundamentals',  label: 'Fund'   },
] as const

function getCellStyle(score: number | undefined | null): string {
  if (score === undefined || score === null) return 'no-data-cell'
  if (score > 0.5) return 'bg-accent-green'
  if (score > 0.1) return 'bg-accent-green/40'
  if (score >= -0.1) return 'bg-bg-elevated'
  if (score >= -0.5) return 'bg-accent-red/40'
  return 'bg-accent-red'
}

function getCellTitle(score: number | undefined | null): string {
  if (score === undefined || score === null) return 'No data'
  if (score > 0.5) return `Strong BULL (${score.toFixed(3)})`
  if (score > 0.1) return `Weak BULL (${score.toFixed(3)})`
  if (score >= -0.1) return `Neutral (${score.toFixed(3)})`
  if (score >= -0.5) return `Weak BEAR (${score.toFixed(3)})`
  return `Strong BEAR (${score.toFixed(3)})`
}

function AgreementCell({ score }: { score: number }) {
  const pct = Math.round(score * 100)
  const color = pct >= 70 ? 'text-accent-green' : pct >= 50 ? 'text-accent-amber' : 'text-accent-red'
  return (
    <span className={clsx('font-mono text-xs font-semibold', color)}>{pct}%</span>
  )
}

function DarkPoolCell({ signal, zscore }: { signal?: string; zscore?: number | null }) {
  const isAccum = signal === 'ACCUMULATION'
  return (
    <div className="flex flex-col items-center leading-none gap-0.5">
      <span className={clsx('font-mono text-[9px] font-semibold uppercase tracking-wide',
        isAccum ? 'text-accent-green' : 'text-text-tertiary'
      )}>
        {isAccum ? 'ACCUM' : 'NEUT'}
      </span>
      {zscore != null && (
        <span className="font-mono text-[9px] text-text-tertiary">
          {zscore > 0 ? '+' : ''}{zscore.toFixed(1)}σ
        </span>
      )}
    </div>
  )
}

type FilterDir = 'all' | 'fifty' | 'bull' | 'bear' | 'high'

const CELL_SIZE = 28
const ROW_HEIGHT = 36
const VISIBLE_EXTRA = 10

export function HeatmapPage() {
  const navigate = useNavigate()
  const { data: rows, isLoading } = useHeatmap()
  const [filterDir, setFilterDir] = useState<FilterDir>('fifty')
  const [filterSector, setFilterSector] = useState<string>('all')
  const [sortBy, setSortBy] = useState<'agreement' | 'rank' | 'squeeze' | 'darkpool'>('agreement')
  const [expandedRow, setExpandedRow] = useState<string | null>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const containerHeight = 560

  const totalRows = rows?.length ?? 0

  const sectors = useMemo(() => {
    if (!rows) return []
    return Array.from(new Set(rows.map(r => r.sector).filter(Boolean)))
  }, [rows])

  const filtered = useMemo(() => {
    if (!rows) return []
    let result = [...rows]

    // Direction / conviction filter
    if (filterDir === 'fifty') {
      const highAgree = result.filter(r => r.signal_agreement_score >= 0.5)
      // fallback: show top 50 by agreement if fewer than 50 qualify
      result = highAgree.length >= 10 ? highAgree : result.slice(0, 50)
    } else if (filterDir === 'bull') {
      result = result.filter(r => r.pre_resolved_direction === 'BULL')
    } else if (filterDir === 'bear') {
      result = result.filter(r => r.pre_resolved_direction === 'BEAR')
    } else if (filterDir === 'high') {
      result = result.filter(r => r.signal_agreement_score >= 0.7)
    }
    // 'all' → no filter

    if (filterSector !== 'all') result = result.filter(r => r.sector === filterSector)

    result.sort((a, b) => {
      if (sortBy === 'agreement') return b.signal_agreement_score - a.signal_agreement_score
      if (sortBy === 'squeeze')   return (b.squeeze ?? 0) - (a.squeeze ?? 0)
      if (sortBy === 'darkpool')  return (b.dark_pool ?? 0) - (a.dark_pool ?? 0)
      return b.signal_agreement_score - a.signal_agreement_score
    })
    return result
  }, [rows, filterDir, filterSector, sortBy])

  const totalHeight = filtered.length * ROW_HEIGHT
  const startIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - VISIBLE_EXTRA)
  const endIdx = Math.min(
    filtered.length,
    Math.ceil((scrollTop + containerHeight) / ROW_HEIGHT) + VISIBLE_EXTRA
  )
  const visibleRows = filtered.slice(startIdx, endIdx)

  const onScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    setScrollTop(e.currentTarget.scrollTop)
  }, [])

  const handleRowClick = (row: HeatmapRow) => {
    setExpandedRow(expandedRow === row.ticker ? null : row.ticker)
  }

  return (
    <Shell title="Signal Heatmap">
      {/* Controls */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        {/* Direction / conviction filters */}
        <div className="flex items-center gap-1">
          {([
            { val: 'all',   label: 'All'                   },
            { val: 'fifty', label: '≥50% Agree'            },
            { val: 'bull',  label: 'BULL only'             },
            { val: 'bear',  label: 'BEAR only'             },
            { val: 'high',  label: 'High Conviction (≥70%)' },
          ] as { val: FilterDir; label: string }[]).map(({ val, label }) => (
            <button
              key={val}
              onClick={() => setFilterDir(val)}
              className={clsx(
                'px-3 py-1.5 text-xs font-mono rounded border transition-colors',
                filterDir === val
                  ? 'bg-accent-blue/20 border-accent-blue text-accent-blue'
                  : 'bg-bg-surface border-border-subtle text-text-secondary hover:border-border-active hover:text-text-primary'
              )}
            >
              {label}
            </button>
          ))}
        </div>

        <select
          value={filterSector}
          onChange={e => setFilterSector(e.target.value)}
          className="px-3 py-1.5 text-xs font-mono bg-bg-surface border border-border-subtle text-text-secondary rounded focus:border-border-active focus:outline-none"
        >
          <option value="all">All Sectors</option>
          {sectors.map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>

        <select
          value={sortBy}
          onChange={e => setSortBy(e.target.value as 'agreement' | 'rank' | 'squeeze' | 'darkpool')}
          className="px-3 py-1.5 text-xs font-mono bg-bg-surface border border-border-subtle text-text-secondary rounded focus:border-border-active focus:outline-none"
        >
          <option value="agreement">Sort: Agreement</option>
          <option value="rank">Sort: Rank</option>
          <option value="squeeze">Sort: Squeeze Score</option>
          <option value="darkpool">Sort: Dark Pool</option>
        </select>

        <span className="ml-auto font-mono text-xs text-text-tertiary">
          Showing {filtered.length} of {totalRows}
        </span>
      </div>

      {/* Matrix */}
      <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
        {/* Header row */}
        <div className="flex items-center border-b border-border-subtle bg-bg-base sticky top-0 z-10">
          <div className="w-24 flex-shrink-0 px-3 py-2.5">
            <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
              Ticker
            </span>
          </div>
          {SIGNAL_MODULES.map(mod => (
            <div
              key={mod.key}
              title={mod.key.replace('_', ' ')}
              className="flex-1 min-w-0 px-1 py-2.5 text-center"
              style={{ width: CELL_SIZE + 8 }}
            >
              <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                {mod.label}
              </span>
            </div>
          ))}
          {/* Divider */}
          <div className="w-px self-stretch bg-border-active mx-1" />
          <div className="w-20 px-2 py-2.5 text-center flex-shrink-0">
            <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
              Dir
            </span>
          </div>
          <div className="w-16 px-2 py-2.5 text-center flex-shrink-0">
            <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
              Agree
            </span>
          </div>
        </div>

        {isLoading ? (
          <div className="p-6">
            <LoadingSkeleton rows={12} />
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-8 text-center font-mono text-xs text-text-tertiary">
            No tickers match the current filter.
          </div>
        ) : (
          <div
            ref={containerRef}
            onScroll={onScroll}
            style={{ height: containerHeight, overflowY: 'auto', position: 'relative' }}
          >
            <div style={{ height: totalHeight, position: 'relative' }}>
              {visibleRows.map((row, relIdx) => {
                const absIdx = startIdx + relIdx
                const isExpanded = expandedRow === row.ticker
                return (
                  <div
                    key={row.ticker}
                    style={{
                      position: 'absolute',
                      top: absIdx * ROW_HEIGHT,
                      left: 0,
                      right: 0,
                    }}
                  >
                    <div
                      className={clsx(
                        'flex items-center border-b border-border-subtle/50 cursor-pointer transition-colors',
                        isExpanded ? 'bg-bg-elevated' : 'hover:bg-bg-elevated/50'
                      )}
                      style={{ height: ROW_HEIGHT }}
                      onClick={() => handleRowClick(row)}
                    >
                      <div className="w-24 flex-shrink-0 px-3">
                        <span
                          className="font-mono text-[11px] font-semibold text-accent-blue hover:underline cursor-pointer"
                          onClick={e => { e.stopPropagation(); navigate(`/ticker/${row.ticker}`) }}
                        >
                          {row.ticker}
                        </span>
                      </div>
                      {SIGNAL_MODULES.map(mod => {
                        const score = row[mod.key as keyof HeatmapRow] as number | undefined
                        return (
                          <div
                            key={mod.key}
                            className="flex-1 px-1 flex items-center justify-center"
                            style={{ width: CELL_SIZE + 8 }}
                          >
                            {mod.key === 'dark_pool' ? (
                              <DarkPoolCell signal={row.dark_pool_signal} zscore={row.dark_pool_zscore} />
                            ) : (
                              <div
                                title={getCellTitle(score)}
                                className={clsx(
                                  'rounded-sm transition-opacity',
                                  getCellStyle(score)
                                )}
                                style={{ width: CELL_SIZE, height: CELL_SIZE - 6 }}
                              />
                            )}
                          </div>
                        )
                      })}
                      <div className="w-px self-stretch bg-border-subtle mx-1" />
                      <div className="w-20 px-2 flex items-center justify-center flex-shrink-0">
                        <DirectionBadge direction={row.pre_resolved_direction} size="sm" />
                      </div>
                      <div className="w-16 px-2 flex items-center justify-center flex-shrink-0">
                        <AgreementCell score={row.signal_agreement_score} />
                      </div>
                    </div>
                    {isExpanded && (
                      <div className="bg-bg-elevated border-b border-border-active px-3 py-2 flex gap-4 flex-wrap">
                        {SIGNAL_MODULES.map(mod => {
                          const score = row[mod.key as keyof HeatmapRow] as number | undefined
                          if (mod.key === 'dark_pool') {
                            return (
                              <div key={mod.key} className="flex items-center gap-1.5">
                                <span className="font-mono text-[10px] text-text-tertiary uppercase">{mod.label}:</span>
                                <span className={clsx('font-mono text-xs font-semibold',
                                  row.dark_pool_signal === 'ACCUMULATION' ? 'text-accent-green' : 'text-text-tertiary'
                                )}>
                                  {row.dark_pool_signal ?? 'NEUTRAL'}
                                  {row.dark_pool_zscore != null && ` (${row.dark_pool_zscore > 0 ? '+' : ''}${row.dark_pool_zscore.toFixed(2)}σ)`}
                                </span>
                              </div>
                            )
                          }
                          return (
                            <div key={mod.key} className="flex items-center gap-1.5">
                              <span className="font-mono text-[10px] text-text-tertiary uppercase">
                                {mod.label}:
                              </span>
                              <span
                                className={clsx(
                                  'font-mono text-xs font-semibold',
                                  score === undefined || score === null
                                    ? 'text-text-tertiary'
                                    : score > 0
                                      ? 'text-accent-green'
                                      : score < 0
                                        ? 'text-accent-red'
                                        : 'text-text-secondary'
                                )}
                              >
                                {score !== undefined && score !== null ? (score > 0 ? '+' : '') + score.toFixed(3) : 'N/A'}
                              </span>
                            </div>
                          )
                        })}
                        <div className="flex items-center gap-1.5 ml-auto">
                          <span className="font-mono text-[10px] text-text-tertiary uppercase">Sector:</span>
                          <span className="font-mono text-xs text-text-secondary">{row.sector || '—'}</span>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </Shell>
  )
}
