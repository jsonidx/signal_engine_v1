import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, useNavigate } from 'react-router-dom'
import { AuthProvider } from './lib/AuthContext'
import { PrivateRoute } from './components/PrivateRoute'
import { LoginPage } from './pages/LoginPage'
import { PortfolioPage } from './pages/PortfolioPage'
import { HeatmapPage } from './pages/HeatmapPage'
import { DeepDivePage } from './pages/DeepDivePage'
import { TickerPage } from './pages/TickerPage'
import { ScreenersPage } from './pages/ScreenersPage'
import { DarkPoolPage } from './pages/DarkPoolPage'
import { BacktestPage } from './pages/BacktestPage'
import { ResolutionPage } from './pages/ResolutionPage'
import { CryptoPage } from './pages/CryptoPage'
import { AccuracyPage } from './pages/AccuracyPage'
import { RankingsPage } from './pages/RankingsPage'
import { ErrorBoundary } from './components/ErrorBoundary'

// ─── Keyboard shortcut handler (inside router context) ────────────────────────

function KeyboardShortcuts() {
  const navigate = useNavigate()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Don't fire when typing in inputs
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (e.metaKey || e.ctrlKey || e.altKey) return

      switch (e.key) {
        case 'p': navigate('/'); break
        case 'h': navigate('/heatmap'); break
        case 't': navigate('/deepdive'); break
        case 's': navigate('/screeners'); break
        case 'd': navigate('/darkpool'); break
        case 'b': navigate('/backtest'); break
        case 'r': navigate('/resolution'); break
        case 'c': navigate('/crypto'); break
        case 'a': navigate('/accuracy'); break
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
        <Routes>
          {/* Public */}
          <Route path="/login" element={<LoginPage />} />

          {/* Protected */}
          <Route
            path="/"
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
            path="/darkpool"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Dark Pool">
                  <DarkPoolPage />
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
            path="/resolution"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Resolution Log">
                  <ResolutionPage />
                </ErrorBoundary>
              </PrivateRoute>
            }
          />
          <Route
            path="/crypto"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Crypto Signals">
                  <CryptoPage />
                </ErrorBoundary>
              </PrivateRoute>
            }
          />
          <Route
            path="/accuracy"
            element={
              <PrivateRoute>
                <ErrorBoundary pageName="Claude Accuracy">
                  <AccuracyPage />
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
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
