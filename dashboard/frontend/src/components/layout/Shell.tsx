import { useState, useEffect } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  Grid3x3,
  Search,
  Filter,
  Activity,
  BarChart2,
  FileText,
  RefreshCw,
  Bitcoin,
  Target,
  ListOrdered,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useRegime } from '../../hooks/useRegime'
import { RegimeBadge } from '../ui/RegimeBadge'
import { useQueryClient } from '@tanstack/react-query'
import { format } from 'date-fns'
import { usePortfolioSummary } from '../../hooks/usePortfolio'

const NAV_ITEMS = [
  { path: '/', label: 'Portfolio', icon: LayoutDashboard, exact: true, shortcut: 'p' },
  { path: '/heatmap', label: 'Signal Heatmap', icon: Grid3x3, shortcut: 'h' },
  { path: '/deepdive', label: 'Deep Dive', icon: Search, shortcut: 't' },
  { path: '/screeners', label: 'Screeners', icon: Filter, shortcut: 's' },
  { path: '/darkpool', label: 'Dark Pool', icon: Activity, shortcut: 'd' },
  { path: '/backtest', label: 'Backtest', icon: BarChart2, shortcut: 'b' },
  { path: '/resolution', label: 'Resolution Log', icon: FileText, shortcut: 'r' },
  { path: '/crypto',     label: 'Crypto',         icon: Bitcoin,  shortcut: 'c' },
  { path: '/accuracy',  label: 'Claude Accuracy', icon: Target,       shortcut: 'a' },
  { path: '/rankings',  label: 'Daily Top-20',    icon: ListOrdered,  shortcut: 'k' },
]

function ETClock() {
  const [time, setTime] = useState('')

  useEffect(() => {
    const update = () => {
      const et = new Date().toLocaleTimeString('en-US', {
        timeZone: 'America/New_York',
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
      setTime(et)
    }
    update()
    const id = setInterval(update, 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="px-3 py-2 border-b border-border-subtle">
      <div className="text-[10px] text-text-tertiary font-mono uppercase tracking-widest mb-0.5">
        Market Time (ET)
      </div>
      <div className="text-text-secondary font-mono text-sm">{time}</div>
    </div>
  )
}

interface ShellProps {
  children: React.ReactNode
  title?: string
  onRefresh?: () => void
  isRefreshing?: boolean
}

export function Shell({ children, title, onRefresh, isRefreshing }: ShellProps) {
  const { data: regime } = useRegime()
  const { data: summary } = usePortfolioSummary()
  const location = useLocation()
  const queryClient = useQueryClient()
  const [globalRefreshing, setGlobalRefreshing] = useState(false)

  const currentNavItem = NAV_ITEMS.find(item => {
    if (item.exact) return location.pathname === item.path
    return location.pathname.startsWith(item.path) && item.path !== '/'
  })
  const pageTitle = title || currentNavItem?.label || 'Signal Engine'

  const handleRefresh = async () => {
    setGlobalRefreshing(true)
    if (onRefresh) onRefresh()
    await queryClient.invalidateQueries()
    setTimeout(() => setGlobalRefreshing(false), 1000)
  }

  return (
    <div className="flex h-screen bg-bg-base overflow-hidden">
      {/* Sidebar */}
      <aside className="w-60 flex-shrink-0 bg-bg-surface border-r border-border-subtle flex flex-col">
        {/* Logo */}
        <div className="px-4 py-4 border-b border-border-subtle">
          <div className="font-mono text-sm font-semibold text-accent-blue tracking-tight">
            signal engine
          </div>
          <div className="text-[10px] text-text-tertiary font-mono mt-0.5">
            v1.0 — quant terminal
          </div>
        </div>

        {/* ET Clock */}
        <ETClock />

        {/* Regime Badge */}
        <div className="px-3 py-2.5 border-b border-border-subtle">
          <div className="text-[10px] text-text-tertiary font-mono uppercase tracking-widest mb-1.5">
            Market Regime
          </div>
          {regime ? (
            <RegimeBadge regime={regime.regime} score={regime.score} />
          ) : (
            <div className="shimmer h-6 w-28 rounded-full" />
          )}
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-2 overflow-y-auto">
          {NAV_ITEMS.map(({ path, label, icon: Icon, exact, shortcut }) => (
            <NavLink
              key={path}
              to={path}
              end={exact}
              title={shortcut ? `${label} (${shortcut})` : label}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-3 px-4 py-2.5 text-sm transition-colors',
                  isActive
                    ? 'text-text-primary bg-bg-elevated border-r-2 border-accent-blue'
                    : 'text-text-secondary hover:text-text-primary hover:bg-bg-elevated/50'
                )
              }
            >
              <Icon size={15} />
              <span className="flex-1">{label}</span>
              {shortcut && (
                <span className="font-mono text-[9px] text-text-tertiary/50 border border-text-tertiary/20 rounded px-1 py-0.5">
                  {shortcut}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Bottom status */}
        <div className="px-3 py-3 border-t border-border-subtle space-y-1.5">
          {summary?.as_of && (
            <div className="text-[10px] text-text-tertiary font-mono">
              Last run: {format(new Date(summary.as_of), 'MMM d HH:mm')}
            </div>
          )}
          <div className="flex items-center gap-1.5">
            <div className="w-1.5 h-1.5 rounded-full bg-accent-green pulse-dot" />
            <span className="text-[10px] font-mono text-text-secondary">API: live</span>
          </div>
        </div>
      </aside>

      {/* Main area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <header className="h-[52px] flex-shrink-0 bg-bg-surface border-b border-border-subtle flex items-center px-6 gap-4">
          <h1 className="flex-1 text-sm font-medium text-text-primary">{pageTitle}</h1>
          <button
            onClick={handleRefresh}
            disabled={globalRefreshing || isRefreshing}
            className="flex items-center gap-2 px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary border border-border-subtle hover:border-border-active rounded transition-colors disabled:opacity-50"
          >
            <RefreshCw
              size={12}
              className={clsx((globalRefreshing || isRefreshing) && 'animate-spin')}
            />
            Refresh
          </button>
        </header>

        {/* Content */}
        <main className="flex-1 overflow-y-auto p-6">
          {children}
        </main>
      </div>
    </div>
  )
}
