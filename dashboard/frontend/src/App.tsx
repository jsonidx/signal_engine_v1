import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, useNavigate } from 'react-router-dom'
import { AuthProvider } from './lib/AuthContext'
import { PrivateRoute } from './components/PrivateRoute'
import { LoginPage } from './pages/LoginPage'
import { HomePage } from './pages/HomePage'
import { PortfolioPage } from './pages/PortfolioPage'
import { HeatmapPage } from './pages/HeatmapPage'
import { DeepDivePage } from './pages/DeepDivePage'
import { TickerPage } from './pages/TickerPage'
import { ScreenersPage } from './pages/ScreenersPage'
import { BacktestPage } from './pages/BacktestPage'
import { ResolutionPage } from './pages/ResolutionPage'
import { RankingsPage } from './pages/RankingsPage'
import { SettingsPage } from './pages/SettingsPage'
import { ErrorBoundary } from './components/ErrorBoundary'

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
      </AuthProvider>
    </BrowserRouter>
  )
}
