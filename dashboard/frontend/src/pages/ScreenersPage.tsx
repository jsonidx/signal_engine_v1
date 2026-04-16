import { useState, useMemo, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import * as Tabs from '@radix-ui/react-tabs'
import { ArrowUpDown, Download, ShieldAlert, BarChart2, RefreshCw } from 'lucide-react'
import { Shell } from '../components/layout/Shell'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { EmptyState } from '../components/ui/EmptyState'
import { api, type SqueezeScreenerRow, type CatalystScreenerRow, type OptionsScreenerRow, type EquityScreenerRow, type RedFlagRow } from '../lib/api'
import { FundamentalScreenerTable } from '../components/FundamentalScreenerTable'
import { useNavigate } from 'react-router-dom'
import { clsx } from 'clsx'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function ScoreBar({ value, max = 100 }: { value: number; max?: number }) {
  const pct = Math.min(100, (value / max) * 100)
  const color =
    pct >= 75 ? '#22c55e' : pct >= 50 ? '#22c55e99' : pct >= 25 ? '#f59e0b' : '#ef4444'
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-2 bg-bg-elevated rounded overflow-hidden flex-shrink-0">
        <div style={{ width: `${pct}%`, background: color }} className="h-full rounded" />
      </div>
      <span className="font-mono text-xs text-text-primary">{value.toFixed(1)}</span>
    </div>
  )
}

function Badge({ label, color = 'gray' }: { label: string | number; color?: string }) {
  const styles: Record<string, string> = {
    green: 'bg-accent-green/15 text-accent-green border-accent-green/30',
    red: 'bg-accent-red/15 text-accent-red border-accent-red/30',
    amber: 'bg-accent-amber/15 text-accent-amber border-accent-amber/30',
    blue: 'bg-accent-blue/15 text-accent-blue border-accent-blue/30',
    gray: 'bg-text-tertiary/10 text-text-secondary border-text-tertiary/20',
  }
  return (
    <span className={clsx('font-mono text-[10px] px-1.5 py-0.5 rounded border', styles[color] ?? styles.gray)}>
      {label}
    </span>
  )
}

function exportCSV(rows: unknown[], filename: string) {
  if (!rows.length) return
  const keys = Object.keys(rows[0] as object)
  const lines = [
    keys.join(','),
    ...rows.map(r =>
      keys.map(k => JSON.stringify((r as Record<string, unknown>)[k] ?? '')).join(',')
    ),
  ]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
}

type SortDir = 'asc' | 'desc'

function useSortedRows<T>(
  rows: T[] | undefined,
  defaultKey: keyof T,
  defaultDir: SortDir = 'desc'
) {
  const [sortKey, setSortKey] = useState<keyof T>(defaultKey)
  const [sortDir, setSortDir] = useState<SortDir>(defaultDir)

  const sorted = useMemo(() => {
    const arr = Array.isArray(rows) ? rows : (rows as any)?.data ?? []
    if (!arr.length) return []
    return [...arr].sort((a, b) => {
      const av = a[sortKey] as number
      const bv = b[sortKey] as number
      return sortDir === 'desc' ? bv - av : av - bv
    })
  }, [rows, sortKey, sortDir])

  const toggle = useCallback(
    (key: keyof T) => {
      if (key === sortKey) setSortDir(d => (d === 'desc' ? 'asc' : 'desc'))
      else { setSortKey(key); setSortDir('desc') }
    },
    [sortKey]
  )

  return { sorted, sortKey, sortDir, toggle }
}

function SortHeader({
  label,
  sortKey,
  activeSortKey,
  sortDir,
  onSort,
}: {
  label: string
  sortKey: string
  activeSortKey: string
  sortDir: SortDir
  onSort: (k: string) => void
}) {
  const active = sortKey === activeSortKey
  const dirSymbol = active ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''
  return (
    <th
      className="px-4 py-2.5 text-left cursor-pointer select-none hover:text-text-primary transition-colors"
      onClick={() => onSort(sortKey)}
    >
      <div className="flex items-center gap-1">
        <span className={clsx('font-mono text-[10px] uppercase tracking-widest', active ? 'text-accent-blue' : 'text-text-tertiary')}>
          {label}{dirSymbol}
        </span>
        {active && (
          <ArrowUpDown size={9} className="text-accent-blue" />
        )}
      </div>
    </th>
  )
}

// ─── Z-score badge ────────────────────────────────────────────────────────────

function ZBadge({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="font-mono text-xs text-text-tertiary">—</span>
  const color = v >= 0.5 ? 'green' : v < -0.5 ? 'red' : 'gray'
  return <Badge label={v.toFixed(2)} color={color} />
}

// ─── Rankings Tab ─────────────────────────────────────────────────────────────

const FACTOR_COLS: { label: string; key: keyof EquityScreenerRow }[] = [
  { label: 'Mom 12-1',  key: 'momentum_12_1'      },
  { label: 'Mom 6-1',   key: 'momentum_6_1'        },
  { label: 'Mean Rev',  key: 'mean_reversion_5d'   },
  { label: 'Vol Qual',  key: 'volatility_quality'  },
  { label: 'Risk Mom',  key: 'risk_adj_momentum'   },
]

function RankingsTable({
  rows,
  showSizing,
  ariaLabel,
  headerClass,
}: {
  rows: EquityScreenerRow[]
  showSizing: boolean
  ariaLabel: string
  headerClass: string
}) {
  const navigate = useNavigate()
  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      <table className="w-full" aria-label={ariaLabel}>
        <thead>
          <tr className={clsx('border-b border-border-subtle', headerClass)}>
            <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Rank</th>
            <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Ticker</th>
            <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary" title="Weighted composite of all factor Z-scores. Higher = stronger multi-factor signal.">Composite Z</th>
            {FACTOR_COLS.map(f => (
              <th key={f.key} className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary hidden md:table-cell" title={
                f.key === 'momentum_12_1'     ? '12-month return minus the most recent month (avoids short-term mean-reversion).' :
                f.key === 'momentum_6_1'      ? '6-month return minus the most recent month.' :
                f.key === 'mean_reversion_5d' ? 'Inverted 5-day return — high score = recent pullback from an uptrend.' :
                f.key === 'volatility_quality'? 'Inverted 63-day realized volatility. Low vol = more consistent momentum.' :
                f.key === 'risk_adj_momentum' ? 'Momentum divided by realized volatility: reward per unit of risk.' : undefined
              }>
                {f.label}
              </th>
            ))}
            {showSizing && (
              <>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Weight %</th>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Position EUR</th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr
              key={row.ticker}
              className="border-b border-border-subtle/50 hover:bg-bg-elevated cursor-pointer transition-colors"
            >
              <td className="px-4 py-2.5 font-mono text-xs text-text-tertiary">{row.rank ?? '—'}</td>
              <td className="px-4 py-2.5">
                <span
                  className="font-mono text-sm font-semibold text-accent-blue cursor-pointer hover:underline"
                  onClick={() => navigate(`/ticker/${row.ticker}`)}
                  data-testid={`ticker-${row.ticker}`}
                >
                  {row.ticker}
                </span>
              </td>
              <td className="px-4 py-2.5"><ZBadge v={row.composite_z} /></td>
              {FACTOR_COLS.map(f => (
                <td key={f.key} className="px-4 py-2.5 hidden md:table-cell">
                  <ZBadge v={row[f.key] as number | null} />
                </td>
              ))}
              {showSizing && (
                <>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                    {row.weight_pct != null ? `${row.weight_pct.toFixed(2)}%` : '—'}
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                    {row.position_eur != null ? `€${row.position_eur.toFixed(0)}` : '—'}
                  </td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function RankingsTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['screeners', 'equity'],
    queryFn:  api.screenerEquity,
    retry:    1,
  })

  const allRows = data?.data ?? []
  const generatedAt = data?.generated_at ?? data?.as_of

  const top20 = useMemo(
    () =>
      [...allRows]
        .filter(r => r.composite_z != null)
        .sort((a, b) => (b.composite_z ?? 0) - (a.composite_z ?? 0))
        .slice(0, 20),
    [allRows]
  )

  const bottom5 = useMemo(
    () =>
      [...allRows]
        .filter(r => r.composite_z != null)
        .sort((a, b) => (a.composite_z ?? 0) - (b.composite_z ?? 0))
        .slice(0, 5),
    [allRows]
  )

  if (isLoading) return <LoadingSkeleton rows={10} />
  if (!allRows.length) return <EmptyState message="No equity signals found" command="./run_master.sh" />

  return (
    <div className="space-y-6">
      {generatedAt && (
        <div className="font-mono text-[10px] text-text-tertiary">
          Last updated: {new Date(generatedAt).toLocaleString()}
        </div>
      )}

      <div>
        <div className="font-mono text-xs text-text-tertiary uppercase tracking-widest mb-2">
          Top 20 — Long Candidates
        </div>
        <RankingsTable
          rows={top20}
          showSizing={true}
          ariaLabel="Top 20 long candidates"
          headerClass="bg-accent-green/10"
        />
      </div>

      <div>
        <div className="font-mono text-xs text-text-tertiary uppercase tracking-widest mb-2">
          Bottom 5 — Weakest Composite Z
        </div>
        <RankingsTable
          rows={bottom5}
          showSizing={false}
          ariaLabel="Bottom 5 short candidates"
          headerClass="bg-accent-red/10"
        />
      </div>
    </div>
  )
}

// ─── Squeeze Tab ──────────────────────────────────────────────────────────────

function SqueezeTab() {
  const navigate = useNavigate()
  const [minScore, setMinScore] = useState(40)

  const { data: raw, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['screeners', 'squeeze', minScore],
    queryFn: () => api.screenerSqueezeRich(minScore),
    retry: 1,
  })

  const rows = raw?.data ?? []
  const { sorted, sortKey, sortDir, toggle } = useSortedRows<SqueezeScreenerRow>(
    rows,
    'final_score'
  )

  const filtered = useMemo(
    () => sorted.filter(r => r.final_score >= minScore),
    [sorted, minScore]
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs text-text-tertiary">Min score</span>
          <input
            type="range"
            min={0}
            max={100}
            value={minScore}
            onChange={e => setMinScore(Number(e.target.value))}
            className="w-28 accent-accent-blue"
          />
          <span className="font-mono text-xs text-text-primary w-6">{minScore}</span>
        </div>
        <button
          onClick={() => exportCSV(filtered, 'squeeze_screener.csv')}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-mono border border-border-subtle text-text-secondary hover:text-text-primary hover:border-border-active rounded transition-colors"
        >
          <Download size={11} />
          Export CSV
        </button>
        {raw?.as_of && (
          <span className="font-mono text-[10px] text-text-tertiary">
            Last updated: {new Date(raw.as_of).toLocaleString()}
          </span>
        )}
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="ml-auto p-1.5 rounded border border-border-subtle text-text-tertiary hover:text-text-primary transition-colors disabled:opacity-40"
          title="Refresh"
        >
          <RefreshCw size={11} className={isFetching ? 'animate-spin' : ''} />
        </button>
      </div>

      {isLoading ? (
        <LoadingSkeleton rows={8} />
      ) : filtered.length === 0 ? (
        <EmptyState message="No squeeze setups found" command="./run_master.sh" />
      ) : (
        <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-border-subtle">
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary w-8">#</th>
                <SortHeader label="Ticker" sortKey="ticker" activeSortKey={String(sortKey)} sortDir={sortDir} onSort={k => toggle(k as keyof SqueezeScreenerRow)} />
                <SortHeader label="Score" sortKey="final_score" activeSortKey={String(sortKey)} sortDir={sortDir} onSort={k => toggle(k as keyof SqueezeScreenerRow)} />
                <th className="px-4 py-2.5 text-left cursor-pointer select-none" title="% of free-float shares sold short. >20% = elevated squeeze risk." onClick={() => toggle('float_short_pct' as keyof SqueezeScreenerRow)}><span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Float Short %</span></th>
                <th className="px-4 py-2.5 text-left cursor-pointer select-none" title="Short interest divided by average daily volume. >5 days = hard to cover quickly." onClick={() => toggle('days_to_cover' as keyof SqueezeScreenerRow)}><span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Days to Cover</span></th>
                <th className="px-4 py-2.5 text-left cursor-pointer select-none" title="Recent volume vs 20-day average. >2× = unusual accumulation or panic." onClick={() => toggle('volume_surge' as keyof SqueezeScreenerRow)}><span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Vol Surge</span></th>
                <th className="px-4 py-2.5 text-left cursor-pointer select-none" title="Estimated annualized cost to borrow shares (% per year). >5% = hard-to-borrow." onClick={() => toggle('cost_to_borrow' as keyof SqueezeScreenerRow)}><span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Borrow Cost</span></th>
                <th className="px-4 py-2.5 text-left cursor-pointer select-none" title="Expected value score combining short %, borrow cost, and catalyst setup." onClick={() => toggle('ev_score' as keyof SqueezeScreenerRow)}><span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">EV Score</span></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row, i) => (
                <tr
                  key={row.ticker}
                  onClick={() => navigate(`/ticker/${row.ticker}`)}
                  className={clsx(
                    'border-b border-border-subtle/50 cursor-pointer transition-colors',
                    row.recent_squeeze ? 'opacity-60' : 'hover:bg-bg-elevated'
                  )}
                >
                  <td className="px-4 py-2.5 font-mono text-xs text-text-tertiary">{i + 1}</td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <span className={clsx(
                        'font-mono text-sm font-semibold text-accent-blue',
                        row.recent_squeeze && 'line-through'
                      )}>
                        {row.ticker}
                      </span>
                      {row.recent_squeeze && (
                        <Badge label="fired" color="amber" />
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    <ScoreBar value={row.final_score} />
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                    {row.float_short_pct?.toFixed(1) ?? '—'}%
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                    {row.days_to_cover?.toFixed(1) ?? '—'}d
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                    {row.volume_surge?.toFixed(1) ?? '—'}x
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                    {row.cost_to_borrow?.toFixed(1) ?? '—'}%
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                    {row.ev_score?.toFixed(1) ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ─── Catalysts Tab ────────────────────────────────────────────────────────────

function CatalystsTab() {
  const navigate = useNavigate()

  const { data: raw, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['screeners', 'catalysts'],
    queryFn: () => api.screenerCatalystRich(4),
    retry: 1,
  })

  const rows = raw?.data ?? []
  const { sorted, sortKey, sortDir, toggle } = useSortedRows<CatalystScreenerRow>(
    rows,
    'total_score'
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <button
          onClick={() => exportCSV(sorted, 'catalyst_screener.csv')}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-mono border border-border-subtle text-text-secondary hover:text-text-primary hover:border-border-active rounded transition-colors"
        >
          <Download size={11} />
          Export CSV
        </button>
        {raw?.as_of && (
          <span className="font-mono text-[10px] text-text-tertiary">
            Last updated: {new Date(raw.as_of).toLocaleString()}
          </span>
        )}
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="ml-auto p-1.5 rounded border border-border-subtle text-text-tertiary hover:text-text-primary transition-colors disabled:opacity-40"
          title="Refresh"
        >
          <RefreshCw size={11} className={isFetching ? 'animate-spin' : ''} />
        </button>
      </div>

      {isLoading ? (
        <LoadingSkeleton rows={8} />
      ) : sorted.length === 0 ? (
        <EmptyState message="No catalyst setups found" command="./run_master.sh" />
      ) : (
        <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-border-subtle">
                <SortHeader label="Ticker" sortKey="ticker" activeSortKey={String(sortKey)} sortDir={sortDir} onSort={k => toggle(k as keyof CatalystScreenerRow)} />
                <SortHeader label="Total" sortKey="total_score" activeSortKey={String(sortKey)} sortDir={sortDir} onSort={k => toggle(k as keyof CatalystScreenerRow)} />
                <th className="px-4 py-2.5 text-left cursor-pointer select-none" title="Squeeze setup score: combines float short %, borrow cost, and volume buildup." onClick={() => toggle('squeeze_setup' as keyof CatalystScreenerRow)}><span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Squeeze</span></th>
                <th className="px-4 py-2.5 text-left cursor-pointer select-none" title="Volume breakout score: abnormal volume vs 20-day average." onClick={() => toggle('volume_breakout' as keyof CatalystScreenerRow)}><span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Vol Break</span></th>
                <th className="px-4 py-2.5 text-left cursor-pointer select-none" title="Dark pool score: off-exchange block flow signal strength." onClick={() => toggle('dark_pool' as keyof CatalystScreenerRow)}><span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Dark Pool</span></th>
                <th className="px-4 py-2.5 text-left cursor-pointer select-none" title="Earnings proximity score (0–5). Higher = earnings sooner. Shows days to next report." onClick={() => toggle('earnings_score' as keyof CatalystScreenerRow)}><span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Earnings</span></th>
                <th className="px-4 py-2.5 text-left cursor-pointer select-none" title="Analyst upgrade clustering score (0–6). Counts upgrades/PT raises in last 7 days." onClick={() => toggle('analyst_score' as keyof CatalystScreenerRow)}><span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Analyst</span></th>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Override</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(row => (
                <tr
                  key={row.ticker}
                  onClick={() => navigate(`/ticker/${row.ticker}`)}
                  className="border-b border-border-subtle/50 hover:bg-bg-elevated cursor-pointer transition-colors"
                >
                  <td className="px-4 py-2.5">
                    <span className="font-mono text-sm font-semibold text-accent-blue">{row.ticker}</span>
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge
                      label={row.total_score?.toFixed(0) ?? '—'}
                      color={row.total_score >= 8 ? 'green' : row.total_score >= 5 ? 'blue' : 'gray'}
                    />
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge label={row.squeeze_setup?.toFixed(1) ?? '—'} color={row.squeeze_setup > 2 ? 'green' : 'gray'} />
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge label={row.volume_breakout?.toFixed(1) ?? '—'} color={row.volume_breakout > 2 ? 'blue' : 'gray'} />
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge label={row.dark_pool?.toFixed(1) ?? '—'} color={row.dark_pool > 1 ? 'green' : 'gray'} />
                  </td>
                  <td className="px-4 py-2.5">
                    {row.earnings_score > 0 ? (
                      <span title={row.days_to_earnings != null ? `${row.days_to_earnings}d to earnings` : undefined}>
                        <Badge
                          label={row.days_to_earnings != null ? `${row.days_to_earnings}d` : row.earnings_score.toFixed(0)}
                          color={row.earnings_score >= 4 ? 'amber' : row.earnings_score >= 2 ? 'blue' : 'gray'}
                        />
                      </span>
                    ) : (
                      <span className="font-mono text-xs text-text-tertiary">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    {row.analyst_score > 0 ? (
                      <span title={row.upgrades_7d ? `${row.upgrades_7d} upgrade(s) in 7 days` : undefined}>
                        <Badge
                          label={row.upgrades_7d ? `↑${row.upgrades_7d}` : row.analyst_score.toFixed(0)}
                          color={row.analyst_score >= 3 ? 'green' : row.analyst_score >= 1 ? 'blue' : 'gray'}
                        />
                      </span>
                    ) : (
                      <span className="font-mono text-xs text-text-tertiary">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    {row.override_applied ? (
                      <Badge label={row.override_flag ?? 'override'} color="amber" />
                    ) : (
                      <span className="font-mono text-xs text-text-tertiary">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ─── Options Tab ──────────────────────────────────────────────────────────────

function OptionsTab() {
  const navigate = useNavigate()
  const [sortKey, setSortKey] = useState<keyof OptionsScreenerRow>('heat_score')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const { data: raw, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['screeners', 'options'],
    queryFn: () => api.screenerOptionsRich(40),
    retry: 1,
  })

  const rows = raw?.data ?? []

  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      const av = a[sortKey] as number
      const bv = b[sortKey] as number
      return sortDir === 'desc' ? bv - av : av - bv
    })
  }, [rows, sortKey, sortDir])

  const toggle = (k: keyof OptionsScreenerRow) => {
    if (k === sortKey) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(k); setSortDir('desc') }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-xs text-text-tertiary">Sort:</span>
          {(['heat_score', 'iv_rank', 'exp_move_pct'] as const).map(k => (
            <button
              key={k}
              onClick={() => toggle(k)}
              className={clsx(
                'px-3 py-1 text-xs font-mono rounded border transition-colors',
                sortKey === k
                  ? 'bg-accent-blue/20 border-accent-blue text-accent-blue'
                  : 'bg-bg-surface border-border-subtle text-text-secondary hover:border-border-active'
              )}
            >
              {k === 'heat_score' ? 'Heat' : k === 'iv_rank' ? 'IV Rank' : 'Exp Move'}
            </button>
          ))}
        </div>
        <button
          onClick={() => exportCSV(sorted, 'options_screener.csv')}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-mono border border-border-subtle text-text-secondary hover:text-text-primary hover:border-border-active rounded transition-colors"
        >
          <Download size={11} />
          Export CSV
        </button>
        {raw?.as_of && (
          <span className="font-mono text-[10px] text-text-tertiary">
            Last updated: {new Date(raw.as_of).toLocaleString()}
          </span>
        )}
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="ml-auto p-1.5 rounded border border-border-subtle text-text-tertiary hover:text-text-primary transition-colors disabled:opacity-40"
          title="Refresh"
        >
          <RefreshCw size={11} className={isFetching ? 'animate-spin' : ''} />
        </button>
      </div>

      {isLoading ? (
        <LoadingSkeleton rows={8} />
      ) : sorted.length === 0 ? (
        <EmptyState message="No options setups found" command="./run_master.sh" />
      ) : (
        <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-border-subtle">
                {[
                  { label: 'Ticker',    key: 'ticker',         tip: undefined },
                  { label: 'Heat',      key: 'heat_score',     tip: 'Composite options heat: IV rank + volume spike + call/put flow. 0–100.' },
                  { label: 'IV Rank',   key: 'iv_rank',        tip: 'Implied volatility percentile rank over the past year (0–100). >50 = expensive options.' },
                  { label: 'IV Source', key: 'iv_source',      tip: '"true" = calculated from live options chain. "estimated" = approximated from HV.' },
                  { label: 'Vol Spike', key: 'vol_spike',      tip: 'Options volume today vs 20-day average. >2× = unusual activity.' },
                  { label: 'Exp Move',  key: 'exp_move_pct',   tip: 'Market-implied 1σ expected move to next expiry (±%). Derived from ATM straddle price.' },
                  { label: 'P/C Ratio', key: 'put_call_ratio', tip: 'Put/call open interest ratio. >1.5 = elevated hedging or bearish positioning.' },
                  { label: 'DTE',       key: 'dte',            tip: 'Days to the nearest liquid expiry used for calculations.' },
                ].map(({ label, key, tip }) => (
                  <th
                    key={key}
                    className="px-4 py-2.5 text-left cursor-pointer hover:text-text-primary"
                    title={tip}
                    onClick={() => toggle(key as keyof OptionsScreenerRow)}
                  >
                    <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
                      {label}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map(row => (
                <tr
                  key={row.ticker}
                  onClick={() => navigate(`/ticker/${row.ticker}`)}
                  className="border-b border-border-subtle/50 hover:bg-bg-elevated cursor-pointer transition-colors"
                >
                  <td className="px-4 py-2.5">
                    <span className="font-mono text-sm font-semibold text-accent-blue">{row.ticker}</span>
                  </td>
                  <td className="px-4 py-2.5">
                    <ScoreBar value={row.heat_score ?? 0} />
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                    {row.iv_rank?.toFixed(0) ?? '—'}%
                  </td>
                  <td className="px-4 py-2.5">
                    <span className={clsx(
                      'font-mono text-xs font-medium',
                      row.iv_source === 'true' ? 'text-accent-green' : 'text-accent-amber'
                    )}>
                      {row.iv_source ?? '—'}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                    {row.vol_spike?.toFixed(1) ?? '—'}x
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                    ±{row.exp_move_pct?.toFixed(1) ?? '—'}%
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                    {row.put_call_ratio?.toFixed(2) ?? '—'}
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                    {row.dte ?? '—'}d
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ─── Red Flags Tab ────────────────────────────────────────────────────────────

function SeverityBadge({ level, score }: { level: string; score: number }) {
  if (level === 'CAUTION' || score >= 25) {
    return (
      <span className="inline-flex items-center gap-1 font-mono text-[10px] px-2 py-0.5 rounded border bg-accent-red/15 text-accent-red border-accent-red/30 font-semibold">
        <ShieldAlert size={9} />
        CAUTION
      </span>
    )
  }
  if (score >= 10) {
    return (
      <span className="inline-flex items-center gap-1 font-mono text-[10px] px-2 py-0.5 rounded border bg-accent-amber/15 text-accent-amber border-accent-amber/30">
        WATCH
      </span>
    )
  }
  return (
    <span className="font-mono text-[10px] px-2 py-0.5 rounded border bg-bg-elevated text-text-tertiary border-border-subtle">
      CLEAN
    </span>
  )
}

function SubScorePip({ label, value, warn }: { label: string; value: number; warn: number }) {
  const hot = value >= warn
  return (
    <div className="flex flex-col items-center gap-0.5">
      <span className={clsx('font-mono text-xs font-semibold', hot ? 'text-accent-red' : 'text-text-secondary')}>
        {value}
      </span>
      <span className="font-mono text-[9px] text-text-tertiary">{label}</span>
    </div>
  )
}

function RedFlagsTab() {
  const navigate = useNavigate()
  const [showClean, setShowClean] = useState(false)
  const [sortKey, setSortKey] = useState<keyof RedFlagRow>('red_flag_score')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const toggle = useCallback(
    (key: keyof RedFlagRow) => {
      if (key === sortKey) setSortDir(d => (d === 'desc' ? 'asc' : 'desc'))
      else { setSortKey(key); setSortDir('desc') }
    },
    [sortKey]
  )

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['screeners', 'redflags'],
    queryFn:  () => api.screenerRedFlags(0),
    retry: 1,
  })

  const rows: RedFlagRow[] = data?.data ?? []

  const displayed = useMemo(() => {
    const base = showClean ? rows : rows.filter(r => r.red_flag_score > 0 || r.risk_level !== 'CLEAN')
    return [...base].sort((a, b) =>
      sortDir === 'desc'
        ? (b[sortKey] as number) - (a[sortKey] as number)
        : (a[sortKey] as number) - (b[sortKey] as number)
    )
  }, [rows, showClean, sortKey, sortDir])

  const cautionCount = rows.filter(r => r.risk_level === 'CAUTION').length

  return (
    <div className="space-y-4">
      {/* Description banner */}
      <div className="flex items-start gap-3 p-3 rounded border border-accent-red/20 bg-accent-red/5">
        <ShieldAlert size={14} className="text-accent-red flex-shrink-0 mt-0.5" />
        <div className="min-w-0">
          <p className="font-mono text-xs text-text-secondary leading-relaxed">
            Tickers with accounting or behavioral red flags: GAAP vs adjusted earnings gaps, elevated accruals,
            unsustainable payouts, and revenue quality issues. Use as a <span className="text-accent-amber">risk filter</span> — avoid or size down flagged names.
          </p>
        </div>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          {cautionCount > 0 && (
            <span className="inline-flex items-center gap-1 font-mono text-[10px] px-2 py-1 rounded border bg-accent-red/10 text-accent-red border-accent-red/30">
              <ShieldAlert size={10} />
              {cautionCount} flagged
            </span>
          )}
        </div>
        <label className="flex items-center gap-1.5 cursor-pointer ml-2">
          <input
            type="checkbox"
            checked={showClean}
            onChange={e => setShowClean(e.target.checked)}
            className="accent-accent-blue"
          />
          <span className="font-mono text-xs text-text-secondary">Show clean tickers</span>
        </label>
        <button
          onClick={() => exportCSV(displayed, 'red_flags.csv')}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-mono border border-border-subtle text-text-secondary hover:text-text-primary hover:border-border-active rounded transition-colors"
        >
          <Download size={11} />
          Export CSV
        </button>
        {data?.as_of && (
          <span className="font-mono text-[10px] text-text-tertiary">
            Last updated: {new Date(data.as_of).toLocaleString()}
          </span>
        )}
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="ml-auto p-1.5 rounded border border-border-subtle text-text-tertiary hover:text-text-primary transition-colors disabled:opacity-40"
          title="Refresh"
        >
          <RefreshCw size={11} className={isFetching ? 'animate-spin' : ''} />
        </button>
      </div>

      {isLoading ? (
        <LoadingSkeleton rows={8} />
      ) : displayed.length === 0 ? (
        <EmptyState message="No red flag data found" command="./run_master.sh" />
      ) : (
        <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-border-subtle bg-accent-red/5">
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary">Ticker</th>
                <SortHeader label="Score" sortKey="red_flag_score" activeSortKey={String(sortKey)} sortDir={sortDir} onSort={k => toggle(k as keyof RedFlagRow)} />
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary" title="CLEAN = no flags. WATCH = 1–2 minor flags. CAUTION = significant accounting concern.">Severity</th>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary hidden lg:table-cell" title="GAAP: earnings quality gap. Accrual: cash vs accrual earnings. Payout: dividend sustainability. RevQual: revenue quality vs sector.">Sub-scores</th>
                <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-text-tertiary" title="The single highest-severity accounting flag detected.">Top Flag</th>
              </tr>
            </thead>
            <tbody>
              {displayed.map(row => (
                <tr
                  key={row.ticker}
                  onClick={() => navigate(`/ticker/${row.ticker}`)}
                  className={clsx(
                    'border-b border-border-subtle/50 cursor-pointer transition-colors hover:bg-bg-elevated',
                    (row.risk_level === 'CAUTION' || row.red_flag_score >= 25) && 'bg-accent-red/5'
                  )}
                >
                  {/* Ticker */}
                  <td className="px-4 py-3">
                    <span className="font-mono text-sm font-semibold text-accent-blue">{row.ticker}</span>
                  </td>

                  {/* Score bar */}
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-16 h-1.5 bg-bg-elevated rounded overflow-hidden flex-shrink-0">
                        <div
                          style={{ width: `${Math.min(100, (row.red_flag_score / 50) * 100)}%` }}
                          className={clsx(
                            'h-full rounded',
                            row.red_flag_score >= 30 ? 'bg-accent-red' :
                            row.red_flag_score >= 15 ? 'bg-accent-amber' : 'bg-text-tertiary'
                          )}
                        />
                      </div>
                      <span className={clsx(
                        'font-mono text-xs font-semibold',
                        row.red_flag_score >= 30 ? 'text-accent-red' :
                        row.red_flag_score >= 15 ? 'text-accent-amber' : 'text-text-tertiary'
                      )}>
                        {row.red_flag_score}
                      </span>
                    </div>
                  </td>

                  {/* Severity badge */}
                  <td className="px-4 py-3">
                    <SeverityBadge level={row.risk_level} score={row.red_flag_score} />
                  </td>

                  {/* Sub-scores (hidden on small screens) */}
                  <td className="px-4 py-3 hidden lg:table-cell">
                    <div className="flex items-end gap-3">
                      <SubScorePip label="GAAP"    value={row.gaap_score}        warn={15} />
                      <SubScorePip label="Accrual" value={row.accruals_score}    warn={10} />
                      <SubScorePip label="Payout"  value={row.payout_score}      warn={15} />
                      <SubScorePip label="RevQual" value={row.rev_quality_score} warn={10} />
                    </div>
                  </td>

                  {/* Top flag description */}
                  <td className="px-4 py-3">
                    <span className="font-mono text-[11px] text-text-secondary leading-relaxed line-clamp-2 max-w-xs block">
                      {row.top_flag || '—'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ─── Page ──────────────────────────────────────────────────────────────────────

const TAB_STYLE =
  'px-4 py-2.5 font-mono text-xs uppercase tracking-widest border-b-2 transition-colors cursor-pointer ' +
  'data-[state=active]:border-accent-blue data-[state=active]:text-text-primary ' +
  'data-[state=inactive]:border-transparent data-[state=inactive]:text-text-tertiary data-[state=inactive]:hover:text-text-secondary'

export function ScreenersPage() {
  return (
    <Shell title="Screeners">
      <Tabs.Root defaultValue="squeeze">
        <Tabs.List className="flex border-b border-border-subtle mb-5 -mx-6 px-6">
          <Tabs.Trigger value="squeeze" className={TAB_STYLE}>
            Squeeze
          </Tabs.Trigger>
          <Tabs.Trigger value="catalysts" className={TAB_STYLE}>
            Catalysts
          </Tabs.Trigger>
          <Tabs.Trigger value="options" className={TAB_STYLE}>
            Options
          </Tabs.Trigger>
          <Tabs.Trigger value="rankings" className={TAB_STYLE}>
            Rankings
          </Tabs.Trigger>
          <Tabs.Trigger value="redflags" className={TAB_STYLE}>
            <span className="flex items-center gap-1.5">
              <ShieldAlert size={11} />
              Red Flags
            </span>
          </Tabs.Trigger>
          <Tabs.Trigger value="fundamentals" className={TAB_STYLE}>
            <span className="flex items-center gap-1.5">
              <BarChart2 size={11} />
              Fundamentals
            </span>
          </Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="squeeze">
          <SqueezeTab />
        </Tabs.Content>
        <Tabs.Content value="catalysts">
          <CatalystsTab />
        </Tabs.Content>
        <Tabs.Content value="options">
          <OptionsTab />
        </Tabs.Content>
        <Tabs.Content value="rankings">
          <RankingsTab />
        </Tabs.Content>
        <Tabs.Content value="redflags">
          <RedFlagsTab />
        </Tabs.Content>
        <Tabs.Content value="fundamentals">
          <FundamentalScreenerTable />
        </Tabs.Content>
      </Tabs.Root>
    </Shell>
  )
}
