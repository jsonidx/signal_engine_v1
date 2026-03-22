import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, useNavigate } from 'react-router-dom'
import { PortfolioPage } from './pages/PortfolioPage'
import { HeatmapPage } from './pages/HeatmapPage'
import { TickerPage } from './pages/TickerPage'
import { ScreenersPage } from './pages/ScreenersPage'
import { DarkPoolPage } from './pages/DarkPoolPage'
import { BacktestPage } from './pages/BacktestPage'
import { ResolutionPage } from './pages/ResolutionPage'
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
        case 's': navigate('/screeners'); break
        case 'd': navigate('/darkpool'); break
        case 'b': navigate('/backtest'); break
        case 'r': navigate('/resolution'); break
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
      <KeyboardShortcuts />
      <Routes>
        <Route
          path="/"
          element={
            <ErrorBoundary pageName="Portfolio">
              <PortfolioPage />
            </ErrorBoundary>
          }
        />
        <Route
          path="/heatmap"
          element={
            <ErrorBoundary pageName="Signal Heatmap">
              <HeatmapPage />
            </ErrorBoundary>
          }
        />
        <Route
          path="/ticker/:symbol"
          element={
            <ErrorBoundary pageName="Ticker Deep Dive">
              <TickerPage />
            </ErrorBoundary>
          }
        />
        <Route
          path="/screeners"
          element={
            <ErrorBoundary pageName="Screeners">
              <ScreenersPage />
            </ErrorBoundary>
          }
        />
        <Route
          path="/darkpool"
          element={
            <ErrorBoundary pageName="Dark Pool">
              <DarkPoolPage />
            </ErrorBoundary>
          }
        />
        <Route
          path="/backtest"
          element={
            <ErrorBoundary pageName="Backtest">
              <BacktestPage />
            </ErrorBoundary>
          }
        />
        <Route
          path="/resolution"
          element={
            <ErrorBoundary pageName="Resolution Log">
              <ResolutionPage />
            </ErrorBoundary>
          }
        />
      </Routes>
    </BrowserRouter>
  )
}
