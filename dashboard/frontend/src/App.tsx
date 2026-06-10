import { lazy, Suspense, useEffect } from 'react'
import { BrowserRouter, Routes, Route, useNavigate } from 'react-router-dom'
import { AuthProvider } from './lib/AuthContext'
import { PrivateRoute } from './components/PrivateRoute'
import { ErrorBoundary } from './components/ErrorBoundary'

const LoginPage      = lazy(() => import('./pages/LoginPage').then(m => ({ default: m.LoginPage })))
const HomePage       = lazy(() => import('./pages/HomePage').then(m => ({ default: m.HomePage })))
const PortfolioPage  = lazy(() => import('./pages/PortfolioPage').then(m => ({ default: m.PortfolioPage })))
const HeatmapPage    = lazy(() => import('./pages/HeatmapPage').then(m => ({ default: m.HeatmapPage })))
const DeepDivePage   = lazy(() => import('./pages/DeepDivePage').then(m => ({ default: m.DeepDivePage })))
const TickerPage     = lazy(() => import('./pages/TickerPage').then(m => ({ default: m.TickerPage })))
const ScreenersPage  = lazy(() => import('./pages/ScreenersPage').then(m => ({ default: m.ScreenersPage })))
const BacktestPage   = lazy(() => import('./pages/BacktestPage').then(m => ({ default: m.BacktestPage })))
const ResolutionPage = lazy(() => import('./pages/ResolutionPage').then(m => ({ default: m.ResolutionPage })))
const OptionsPage    = lazy(() => import('./pages/OptionsPage').then(m => ({ default: m.OptionsPage })))
const RankingsPage   = lazy(() => import('./pages/RankingsPage').then(m => ({ default: m.RankingsPage })))
const SettingsPage   = lazy(() => import('./pages/SettingsPage').then(m => ({ default: m.SettingsPage })))

function RouteLoadingFallback() {
  return (
    <div className="flex items-center justify-center min-h-screen bg-bg-base">
      <div className="flex flex-col items-center gap-3">
        <div className="w-5 h-5 border-2 border-accent-blue border-t-transparent rounded-full animate-spin" />
        <span className="font-mono text-xs text-text-tertiary">Loading…</span>
      </div>
    </div>
  )
}

// ─── Keyboard shortcut handler (inside router context) ────────────────────────

function KeyboardShortcuts() {
  const navigate = useNavigate()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (e.metaKey || e.ctrlKey || e.altKey) return

      switch (e.key) {
        case 'g': navigate('/'); break
        case 'p': navigate('/portfolio'); break
        case 'h': navigate('/heatmap'); break
        case 't': navigate('/deepdive'); break
        case 's': navigate('/screeners'); break
        case 'o': navigate('/options'); break
        case 'b': navigate('/backtest'); break
        case 'r': navigate('/resolution'); break
        case 'k': navigate('/rankings'); break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate])

  return null
}

// ─── App ──────────────────────────────────────────────────────────────────────

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <KeyboardShortcuts />
        <Suspense fallback={<RouteLoadingFallback />}>
        <Routes>
          {/* Public */}
          <Route path="/login" element={<LoginPage />} />

          {/* Protected */}
          <Route
            path="/"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Home">
                  <HomePage />
                </ErrorBoundary>
              </PrivateRoute>
            }
          />
          <Route
            path="/portfolio"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Portfolio">
                  <PortfolioPage />
                </ErrorBoundary>
              </PrivateRoute>
            }
          />
          <Route
            path="/heatmap"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Signal Heatmap">
                  <HeatmapPage />
                </ErrorBoundary>
              </PrivateRoute>
            }
          />
          <Route
            path="/deepdive"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Deep Dive">
                  <DeepDivePage />
                </ErrorBoundary>
              </PrivateRoute>
            }
          />
          <Route
            path="/ticker/:symbol"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Ticker Deep Dive">
                  <TickerPage />
                </ErrorBoundary>
              </PrivateRoute>
            }
          />
          <Route
            path="/screeners"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Screeners">
                  <ScreenersPage />
                </ErrorBoundary>
              </PrivateRoute>
            }
          />
          <Route
            path="/backtest"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Backtest">
                  <BacktestPage />
                </ErrorBoundary>
              </PrivateRoute>
            }
          />
          <Route
            path="/options"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Options">
                  <OptionsPage />
                </ErrorBoundary>
              </PrivateRoute>
            }
          />
          <Route
            path="/resolution"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Resolution & Accuracy">
                  <ResolutionPage />
                </ErrorBoundary>
              </PrivateRoute>
            }
          />
          <Route
            path="/rankings"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Daily Top-20">
                  <RankingsPage />
                </ErrorBoundary>
              </PrivateRoute>
            }
          />
          <Route
            path="/settings"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Settings">
                  <SettingsPage />
                </ErrorBoundary>
              </PrivateRoute>
            }
          />
        </Routes>
        </Suspense>
      </AuthProvider>
    </BrowserRouter>
  )
}
