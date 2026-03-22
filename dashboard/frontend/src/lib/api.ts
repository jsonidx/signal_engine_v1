import axios from 'axios'

const client = axios.create({ baseURL: '/' })

// ─── Portfolio ────────────────────────────────────────────────────────────────

export interface PortfolioSummary {
  as_of: string
  weekly_return_pct: number
  spy_return_pct: number
  sharpe_ratio: number
  max_drawdown_pct: number
  hit_rate_pct: number
  total_pnl_eur: number
  open_positions: number
}

export interface PortfolioHistoryPoint {
  week: string
  pnl_eur: number
  spy_return_pct: number
  cumulative_pnl_eur: number
}

export interface Position {
  ticker: string
  direction: 'BULL' | 'BEAR' | 'NEUTRAL'
  entry_price: number
  current_price: number
  unrealized_pnl_eur: number
  unrealized_pnl_pct: number
  size_eur: number
  days_held: number
  conviction: number
}

// ─── Signals / Heatmap ────────────────────────────────────────────────────────

export interface HeatmapRow {
  ticker: string
  sector: string
  signal_engine: number
  squeeze: number
  options: number
  dark_pool: number
  fundamentals: number
  social: number
  polymarket: number
  cross_asset: number
  pre_resolved_direction: string
  signal_agreement_score: number
}

export interface RegimeCurrent {
  regime: 'RISK_ON' | 'TRANSITIONAL' | 'RISK_OFF'
  score: number
  as_of: string
}

// Basic ticker signal (kept for backward compat)
export interface TickerSignal {
  ticker: string
  direction: string
  conviction: number
  signal_agreement_score: number
  modules: Record<string, number>
  ai_synthesis: string
  as_of: string
}

// Rich ticker detail (superset of TickerSignal)
export interface TickerDetail extends TickerSignal {
  company_name?: string
  current_price?: number
  price_change_1d?: number
  price_change_1d_pct?: number
  sector?: string
  regime?: string
  // AI thesis fields
  thesis?: string
  primary_scenario?: string
  bear_scenario?: string
  key_invalidation?: string
  bull_probability?: number
  bear_probability?: number
  neutral_probability?: number
  // Price levels
  target_1?: number
  target_2?: number
  entry_low?: number
  entry_high?: number
  stop_loss?: number
  poc?: number
  vwap?: number
  max_pain?: number
  // Override flags
  override_flags?: string[]
  // Squeeze
  squeeze_score?: number
  float_short_pct?: number
  days_to_cover?: number
  volume_surge?: number
  recent_squeeze?: boolean
  ftd_shares?: number
  // Options
  heat_score?: number
  iv_rank?: number
  iv_source?: string
  expected_move_pct?: number
  put_call_ratio?: number
  // Dark pool
  dark_pool_score?: number
  short_ratio_trend?: string
  dark_pool_intensity?: number
  // Social
  trend_score?: number
  interest_level?: number
  bull_bear_ratio?: number
  message_count?: number
  // Catalysts / risks
  catalysts?: string[]
  risks?: string[]
}

// ─── Screeners ────────────────────────────────────────────────────────────────

export interface ScreenerResult {
  ticker: string
  sector: string
  score: number
  direction: string
  catalyst: string
}

export interface SqueezeScreenerRow {
  ticker: string
  final_score: number
  float_short_pct: number
  days_to_cover: number
  volume_surge: number
  cost_to_borrow: number
  ev_score: number
  recent_squeeze: boolean
}

export interface CatalystScreenerRow {
  ticker: string
  total_score: number
  squeeze_setup: number
  volume_breakout: number
  social: number
  dark_pool: number
  override_applied: boolean
  override_flag?: string
}

export interface OptionsScreenerRow {
  ticker: string
  heat_score: number
  iv_rank: number
  iv_source: 'true' | 'estimated'
  vol_spike: number
  exp_move_pct: number
  put_call_ratio: number
  max_pain: number
  dte: number
}

// ─── Dark Pool ────────────────────────────────────────────────────────────────

export interface DarkPoolEntry {
  ticker: string
  signal: string
  score: number
  short_ratio: number
  off_exchange_pct: number
  as_of: string
}

export interface DarkPoolCard {
  ticker: string
  company?: string
  signal: 'ACCUMULATION' | 'DISTRIBUTION' | 'NEUTRAL'
  dark_pool_score: number
  short_ratio: number
  short_ratio_trend: 'up' | 'down' | 'flat'
  dark_pool_intensity: number
  history?: Array<{ date: string; short_ratio: number }>
}

// ─── Backtest ─────────────────────────────────────────────────────────────────

export interface BacktestResult {
  period_start: string
  period_end: string
  total_return_pct: number
  sharpe: number
  max_drawdown_pct: number
  hit_rate_pct: number
  n_trades: number
}

export interface FactorIC {
  factor: string
  mean_ic: number
  ic_ir: number
  contribution_pct: number
  current_weight: number
  suggested_weight: number
}

export interface BacktestSummaryFull {
  oos_sharpe?: number
  spy_sharpe?: number
  worst_drawdown_window?: { start: string; end: string; drawdown_pct: number }
  annual_turnover_pct?: number
  cost_bps?: number
  factor_ics?: FactorIC[]
  weight_recommendations?: Record<string, number>
  windows?: BacktestResult[]
}

// ─── Resolution ───────────────────────────────────────────────────────────────

export interface ResolutionLog {
  ticker: string
  timestamp: string
  input_direction: string
  resolved_direction: string
  skip_claude: boolean
  override_reason: string | null
  module_votes: Record<string, number>
}

export interface ResolutionLogEntry {
  ticker: string
  timestamp: string
  pre_resolved: string
  confidence: number
  bull_weight: number
  bear_weight: number
  overrides: string[]
  skip_claude: boolean
}

export interface ResolutionStats {
  claude_skip_rate_pct: number
  avg_agreement_score: number
  most_common_override: string
  bear_cb_hits_30d: number
}

// ─── Universe ─────────────────────────────────────────────────────────────────

export interface UniverseStats {
  tickers: string[]
  total: number
}

// ─── API client ───────────────────────────────────────────────────────────────

export const api = {
  // Portfolio
  portfolioSummary: (): Promise<PortfolioSummary> =>
    client.get('/api/portfolio/summary').then(r => r.data),

  portfolioHistory: (weeks: number): Promise<PortfolioHistoryPoint[]> =>
    client.get(`/api/portfolio/history?weeks=${weeks}`).then(r => r.data),

  portfolioPositions: (): Promise<Position[]> =>
    client.get('/api/portfolio/positions').then(r => r.data),

  // Signals
  signalsLatest: (date?: string): Promise<TickerSignal[]> =>
    client.get('/api/signals/latest', { params: date ? { date } : {} }).then(r => r.data),

  signalsHeatmap: (): Promise<HeatmapRow[]> =>
    client.get('/api/signals/heatmap').then(r => r.data),

  signalsTicker: (ticker: string): Promise<TickerDetail> =>
    client.get(`/api/signals/ticker/${ticker}`).then(r => r.data),

  // Regime
  regimeCurrent: (): Promise<RegimeCurrent> =>
    client.get('/api/regime/current').then(r => r.data),

  regimeHistory: (): Promise<RegimeCurrent[]> =>
    client.get('/api/regime/history').then(r => r.data),

  // Screeners (legacy minimal)
  screenerCatalyst: (): Promise<ScreenerResult[]> =>
    client.get('/api/screener/catalyst').then(r => r.data),

  screenerSqueeze: (): Promise<ScreenerResult[]> =>
    client.get('/api/screener/squeeze').then(r => r.data),

  screenerOptions: (): Promise<ScreenerResult[]> =>
    client.get('/api/screener/options').then(r => r.data),

  // Screeners (rich)
  screenerSqueezeRich: (minScore = 40): Promise<SqueezeScreenerRow[]> =>
    client.get('/api/screeners/squeeze', { params: { min_score: minScore } }).then(r => r.data),

  screenerCatalystRich: (minScore = 4): Promise<CatalystScreenerRow[]> =>
    client.get('/api/screeners/catalysts', { params: { min_score: minScore } }).then(r => r.data),

  screenerOptionsRich: (minHeat = 40): Promise<OptionsScreenerRow[]> =>
    client.get('/api/screeners/options', { params: { min_heat: minHeat } }).then(r => r.data),

  // Dark pool
  darkpoolLatest: (): Promise<DarkPoolEntry[]> =>
    client.get('/api/darkpool/latest').then(r => r.data),

  darkpoolTicker: (ticker: string): Promise<DarkPoolEntry[]> =>
    client.get(`/api/darkpool/ticker/${ticker}`).then(r => r.data),

  darkpoolTop: (signal?: string, limit = 30): Promise<DarkPoolCard[]> =>
    client.get('/api/darkpool/top', { params: { ...(signal ? { signal } : {}), limit } }).then(r => r.data),

  // Backtest
  backtestResults: (): Promise<BacktestResult[]> =>
    client.get('/api/backtest/results').then(r => r.data),

  backtestSummary: (): Promise<Record<string, unknown>> =>
    client.get('/api/backtest/summary').then(r => r.data),

  backtestSummaryFull: (): Promise<BacktestSummaryFull> =>
    client.get('/api/backtest/summary').then(r => r.data),

  // Resolution
  resolutionLog: (limit?: number): Promise<ResolutionLog[]> =>
    client.get('/api/resolution/log', { params: limit ? { limit } : {} }).then(r => r.data),

  resolutionLogRich: (date?: string, limit = 100): Promise<ResolutionLogEntry[]> =>
    client.get('/api/resolution/log', { params: { ...(date ? { date } : {}), limit } }).then(r => r.data),

  resolutionStats: (): Promise<ResolutionStats> =>
    client.get('/api/resolution/stats').then(r => r.data),

  // Universe
  universeStats: (): Promise<UniverseStats> =>
    client.get('/api/universe/stats').then(r => r.data),
}
