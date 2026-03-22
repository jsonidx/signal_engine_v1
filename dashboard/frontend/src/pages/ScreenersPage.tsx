import { useState, useMemo, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import * as Tabs from '@radix-ui/react-tabs'
import { ArrowUpDown, Download } from 'lucide-react'
import { Shell } from '../components/layout/Shell'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { EmptyState } from '../components/ui/EmptyState'
import { api, type SqueezeScreenerRow, type CatalystScreenerRow, type OptionsScreenerRow } from '../lib/api'
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
    if (!rows) return []
    return [...rows].sort((a, b) => {
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

// ─── Squeeze Tab ──────────────────────────────────────────────────────────────

function SqueezeTab() {
  const navigate = useNavigate()
  const [minScore, setMinScore] = useState(40)

  const { data: raw, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ['screeners', 'squeeze', minScore],
    queryFn: () => api.screenerSqueezeRich(minScore),
    retry: 1,
  })

  const { sorted, sortKey, sortDir, toggle } = useSortedRows<SqueezeScreenerRow>(
    raw,
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
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-xs font-mono border border-border-subtle text-text-secondary hover:text-text-primary hover:border-border-active rounded transition-colors"
        >
          <Download size={11} />
          Export CSV
        </button>
        {dataUpdatedAt > 0 && (
          <span className="font-mono text-[10px] text-text-tertiary">
            Last run: {new Date(dataUpdatedAt).toLocaleTimeString()}
          </span>
        )}
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
                <SortHeader label="Float Short %" sortKey="float_short_pct" activeSortKey={String(sortKey)} sortDir={sortDir} onSort={k => toggle(k as keyof SqueezeScreenerRow)} />
                <SortHeader label="Days to Cover" sortKey="days_to_cover" activeSortKey={String(sortKey)} sortDir={sortDir} onSort={k => toggle(k as keyof SqueezeScreenerRow)} />
                <SortHeader label="Vol Surge" sortKey="volume_surge" activeSortKey={String(sortKey)} sortDir={sortDir} onSort={k => toggle(k as keyof SqueezeScreenerRow)} />
                <SortHeader label="Borrow Cost" sortKey="cost_to_borrow" activeSortKey={String(sortKey)} sortDir={sortDir} onSort={k => toggle(k as keyof SqueezeScreenerRow)} />
                <SortHeader label="EV Score" sortKey="ev_score" activeSortKey={String(sortKey)} sortDir={sortDir} onSort={k => toggle(k as keyof SqueezeScreenerRow)} />
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

  const { data: raw, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ['screeners', 'catalysts'],
    queryFn: () => api.screenerCatalystRich(4),
    retry: 1,
  })

  const { sorted, sortKey, sortDir, toggle } = useSortedRows<CatalystScreenerRow>(
    raw,
    'total_score'
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <button
          onClick={() => exportCSV(sorted, 'catalyst_screener.csv')}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-xs font-mono border border-border-subtle text-text-secondary hover:text-text-primary hover:border-border-active rounded transition-colors"
        >
          <Download size={11} />
          Export CSV
        </button>
        {dataUpdatedAt > 0 && (
          <span className="font-mono text-[10px] text-text-tertiary">
            Last run: {new Date(dataUpdatedAt).toLocaleTimeString()}
          </span>
        )}
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
                <SortHeader label="Squeeze" sortKey="squeeze_setup" activeSortKey={String(sortKey)} sortDir={sortDir} onSort={k => toggle(k as keyof CatalystScreenerRow)} />
                <SortHeader label="Vol Break" sortKey="volume_breakout" activeSortKey={String(sortKey)} sortDir={sortDir} onSort={k => toggle(k as keyof CatalystScreenerRow)} />
                <SortHeader label="Social" sortKey="social" activeSortKey={String(sortKey)} sortDir={sortDir} onSort={k => toggle(k as keyof CatalystScreenerRow)} />
                <SortHeader label="Dark Pool" sortKey="dark_pool" activeSortKey={String(sortKey)} sortDir={sortDir} onSort={k => toggle(k as keyof CatalystScreenerRow)} />
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
                    <Badge label={row.social?.toFixed(1) ?? '—'} color={row.social > 1 ? 'green' : 'gray'} />
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge label={row.dark_pool?.toFixed(1) ?? '—'} color={row.dark_pool > 1 ? 'green' : 'gray'} />
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

  const { data: raw, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ['screeners', 'options'],
    queryFn: () => api.screenerOptionsRich(40),
    retry: 1,
  })

  const sorted = useMemo(() => {
    if (!raw) return []
    return [...raw].sort((a, b) => {
      const av = a[sortKey] as number
      const bv = b[sortKey] as number
      return sortDir === 'desc' ? bv - av : av - bv
    })
  }, [raw, sortKey, sortDir])

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
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-xs font-mono border border-border-subtle text-text-secondary hover:text-text-primary hover:border-border-active rounded transition-colors"
        >
          <Download size={11} />
          Export CSV
        </button>
        {dataUpdatedAt > 0 && (
          <span className="font-mono text-[10px] text-text-tertiary">
            Last run: {new Date(dataUpdatedAt).toLocaleTimeString()}
          </span>
        )}
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
                  { label: 'Ticker', key: 'ticker' },
                  { label: 'Heat', key: 'heat_score' },
                  { label: 'IV Rank', key: 'iv_rank' },
                  { label: 'IV Source', key: 'iv_source' },
                  { label: 'Vol Spike', key: 'vol_spike' },
                  { label: 'Exp Move', key: 'exp_move_pct' },
                  { label: 'P/C Ratio', key: 'put_call_ratio' },
                  { label: 'Max Pain', key: 'max_pain' },
                  { label: 'DTE', key: 'dte' },
                ].map(({ label, key }) => (
                  <th
                    key={key}
                    className="px-4 py-2.5 text-left cursor-pointer hover:text-text-primary"
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
                    {row.max_pain !== undefined ? `$${row.max_pain.toFixed(2)}` : '—'}
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
      </Tabs.Root>
    </Shell>
  )
}
