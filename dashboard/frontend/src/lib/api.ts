import axios from 'axios'

const client = axios.create({ baseURL: '/' })

// ─── Portfolio ────────────────────────────────────────────────────────────────

export interface PortfolioSummary {
  as_of: string
  nav_eur: number
  weekly_return_pct: number
  spy_return_pct: number
  sharpe_ratio: number
  max_drawdown_pct: number
  hit_rate_pct: number
  total_pnl_eur: number
  open_positions: number
}

export interface EquityScreenerRow {
  ticker: string
  rank: number | null
  composite_z: number | null
  momentum_12_1: number | null
  momentum_6_1: number | null
  mean_reversion_5d: number | null
  volatility_quality: number | null
  risk_adj_momentum: number | null
  weight_pct: number | null
  position_eur: number | null
  as_of?: string
}

export interface EquityScreenerResponse {
  data: EquityScreenerRow[]
  generated_at?: string
  as_of?: string
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

export interface RegimeComponents {
  trend: number
  volatility: number
  credit: number
  yield_curve: number
}

export interface RegimeCurrent {
  regime: 'RISK_ON' | 'TRANSITIONAL' | 'RISK_OFF' | 'UNKNOWN'
  score: number
  size_multiplier: number
  vix?: number | null
  spy_vs_200ma?: number | null
  yield_curve_spread?: number | null
  components?: RegimeComponents
  computed_at?: string | null
  sector_regimes?: Record<string, string>
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
  // Expected moves
  expected_moves?: ExpectedMove[]
  // Catalysts / risks
  catalysts?: string[]
  risks?: string[]
}

export interface ExpectedMove {
  horizon: string
  bear_pct: number
  base_pct: number
  bull_pct: number
  bear_price: number
  base_price: number
  bull_price: number
  bull_prob: number
  bear_prob: number
  neutral_prob: number
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

// ─── Crypto ───────────────────────────────────────────────────────────────────

export interface CryptoTicker {
  ticker: string
  price_usd: number
  price_eur: number
  signal_score: number
  trend: 'UP' | 'DOWN' | 'NEUTRAL'
  momentum: number
  rsi: number
  vol_pct: number
  action: 'HOLD' | 'REDUCE' | 'SELL' | 'BUY' | string
}

export interface CryptoResponse {
  generated_at: string
  btc_200ma_signal: 'CASH' | 'ACTIVE'
  tickers: CryptoTicker[]
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

// ─── Max Pain ─────────────────────────────────────────────────────────────────

export interface MaxPainExpiry {
  expiry: string
  days_to_expiry: number
  max_pain: number
  distance_pct: number
  direction: string
  call_oi: number
  put_oi: number
  total_oi: number
  pc_ratio: number | null
  signal_strength: string
}

export interface MaxPainData {
  current_price: number
  nearest_expiry: string
  nearest_max_pain: number
  nearest_distance_pct: number
  nearest_direction: string
  nearest_days_to_expiry: number
  nearest_total_oi: number
  nearest_signal_strength: string
  all_expirations: MaxPainExpiry[]
  interpretation: string
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

// ─── Thesis Accuracy ──────────────────────────────────────────────────────────

export interface ThesisOutcome {
  ticker: string
  thesis_date: string
  direction: 'BULL' | 'BEAR' | 'NEUTRAL'
  conviction: number
  entry_price: number | null
  target_1: number | null
  target_2: number | null
  stop_loss: number | null
  price_7d: number | null
  price_14d: number | null
  price_30d: number | null
  return_7d: number | null
  return_14d: number | null
  return_30d: number | null
  vs_target_1_pct: number | null
  vs_target_2_pct: number | null
  hit_target_1: number
  hit_target_2: number
  hit_stop: number
  days_to_target_1: number | null
  days_to_stop: number | null
  outcome: 'HIT_TARGET1' | 'HIT_TARGET2' | 'HIT_STOP' | 'OPEN' | 'EXPIRED'
  claude_correct: 1 | 0 | null
  was_traded: number
  last_checked: string | null
}

export interface ThesisAccuracyMonth {
  month: string
  total: number
  resolved: number
  open: number
  traded: number
  correct: number
  wrong: number
  direction_accuracy_pct: number | null
  hit_target_1: number
  hit_stop_first: number
  target_hit_rate_pct: number | null
  stop_hit_rate_pct: number | null
  avg_return_30d: number | null
  avg_vs_target_1_pct: number | null
  avg_days_to_target_1: number | null
}

export interface ThesisAccuracyAllTime {
  total: number
  resolved: number
  correct: number
  wrong: number
  direction_accuracy_pct: number | null
  hit_target_1: number
  hit_stop_first: number
  target_hit_rate_pct: number | null
  stop_hit_rate_pct: number | null
  avg_return_30d: number | null
  avg_vs_target_1_pct: number | null
}

export interface ThesisAccuracyResponse {
  data_available: boolean
  all_time: ThesisAccuracyAllTime
  by_month: ThesisAccuracyMonth[]
}

export interface ThesisOutcomesResponse {
  data_available: boolean
  days: number
  summary: ThesisAccuracyAllTime
  data: ThesisOutcome[]
}

// ─── Cash ─────────────────────────────────────────────────────────────────────

export interface CashBalance {
  cash_eur: number
  updated_at: string | null
}

export type CashAction = 'set' | 'add' | 'reduce'

// ─── Action Zones ─────────────────────────────────────────────────────────────

export interface ActionZones {
  data_available: boolean
  ticker: string
  currency: string
  fx_rate: number
  current_price: number
  atr: number
  atr_pct: number
  buy_zone_low: number
  buy_zone_high: number
  entry_mid: number
  stop_loss: number
  target_1: number
  target_2: number
  rsi: number
  ema21: number
  ema50: number
  rr_t1: number
  rr_t2: number
  timing: string
  suggested_size_eur: number
  action: string
  action_color: 'green' | 'red' | 'amber' | 'blue' | 'neutral'
  eur: {
    current: number; atr: number; buy_low: number; buy_high: number
    entry_mid: number; stop: number; t1: number; t2: number
  }
  pct: { stop: number; t1: number; t2: number; current: number }
}

export interface AnalyzeStatus {
  status: 'idle' | 'running' | 'done'
  symbol: string
  started_at?: string
  pid?: number
}

// ─── Ticker Intelligence ──────────────────────────────────────────────────────

export interface SecFiling {
  form: string
  date: string
  description: string
  url: string
}

export interface CongressTrade {
  chamber: 'House' | 'Senate'
  member: string
  date: string
  type: string
  amount: string
  asset: string
}

export interface EarningsQuarter {
  label: string              // "Q4 '24"
  period: string             // "2024-12-31"
  eps_estimate: number | null
  eps_actual: number | null
  surprise_pct: number | null
  revenue: number | null     // raw dollars
  revenue_estimate: number | null
  beat: boolean | null       // EPS beat
  revenue_beat: boolean | null
}

export interface EarningsAnnual {
  label: string          // "FY2025"
  year: number
  revenue: number | null
  eps: number | null
  net_income: number | null
}

export interface EarningsData {
  data_available: boolean
  next_earnings: string | null
  next_eps: { avg: number | null; high: number | null; low: number | null } | null
  next_revenue: { avg: number | null; high: number | null; low: number | null } | null
  eps_growth_yoy: number | null
  quarterly: EarningsQuarter[]  // oldest → newest
  annual: EarningsAnnual[]      // oldest → newest
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
    client.get('/api/portfolio/summary').then(r => {
      const d = r.data
      return {
        ...d,
        nav_eur:        d.nav_eur ?? d.total_value_eur ?? d.portfolio_value_eur ?? 50000,
        spy_return_pct: d.spy_return_pct ?? d.benchmark_return_pct ?? 0,
        total_pnl_eur:  d.total_pnl_eur  ?? d.total_value_eur  ?? 0,
      }
    }),

  portfolioHistory: (weeks: number): Promise<PortfolioHistoryPoint[]> =>
    client.get(`/api/portfolio/history?weeks=${weeks}`).then(r => r.data?.data ?? []),

  portfolioPositions: (): Promise<Position[]> =>
    client.get('/api/portfolio/positions').then(r => {
      const rows: any[] = r.data?.data ?? []
      const dirMap: Record<string, string> = { LONG: 'BULL', SHORT: 'BEAR', BULL: 'BULL', BEAR: 'BEAR', NEUTRAL: 'NEUTRAL' }
      return rows.map(p => ({
        ...p,
        size_eur:    p.size_eur    ?? p.position_size_eur ?? 0,
        conviction:  p.conviction  ?? 0,
        direction:   dirMap[p.direction] ?? 'NEUTRAL',
      }))
    }),

  // Signals
  signalsLatest: (date?: string): Promise<TickerSignal[]> =>
    client.get('/api/signals/latest', { params: date ? { date } : {} }).then(r => r.data?.data ?? []),

  signalsHeatmap: (): Promise<HeatmapRow[]> =>
    client.get('/api/signals/heatmap').then(r => r.data?.data ?? []),

  signalsTicker: (ticker: string): Promise<TickerDetail> =>
    client.get(`/api/signals/ticker/${ticker}`).then(r => r.data?.data ?? r.data),

  // Regime
  regimeCurrent: (): Promise<RegimeCurrent> =>
    client.get('/api/regime/current').then(r => {
      const d = r.data
      return { ...d, as_of: d.as_of ?? d.computed_at }
    }),

  regimeHistory: (): Promise<RegimeCurrent[]> =>
    client.get('/api/regime/history').then(r => r.data?.data ?? []),

  // Screeners (rich)
  screenerEquity: (): Promise<EquityScreenerResponse> =>
    client.get('/api/screeners/equity').then(r => ({
      data:         r.data?.data         ?? [],
      generated_at: r.data?.generated_at,
      as_of:        r.data?.as_of,
    })),

  screenerSqueezeRich: (minScore = 40): Promise<SqueezeScreenerRow[]> =>
    client.get('/api/screeners/squeeze', { params: { min_score: minScore } }).then(r => r.data?.data ?? []),

  screenerCatalystRich: (minScore = 4): Promise<CatalystScreenerRow[]> =>
    client.get('/api/screeners/catalysts', { params: { min_score: minScore } }).then(r => r.data?.data ?? []),

  screenerOptionsRich: (minHeat = 40): Promise<OptionsScreenerRow[]> =>
    client.get('/api/screeners/options', { params: { min_heat: minHeat } }).then(r => r.data?.data ?? []),

  screenerCrypto: (): Promise<CryptoResponse | null> =>
    client.get('/api/screeners/crypto').then(r => r.data).catch(() => null),

  // Dark pool
  darkpoolLatest: (): Promise<DarkPoolCard[]> =>
    client.get('/api/darkpool/top', { params: { limit: 50 } }).then(r => r.data?.data ?? []),

  darkpoolTicker: (ticker: string): Promise<DarkPoolEntry[]> =>
    client.get(`/api/darkpool/ticker/${ticker}`).then(r => r.data?.data ?? r.data),

  darkpoolTop: (signal?: string, limit = 30): Promise<DarkPoolCard[]> =>
    client.get('/api/darkpool/top', { params: { ...(signal ? { signal } : {}), limit } }).then(r => r.data?.data ?? []),

  // Max pain — live fetch (1h cache on server)
  maxPainLive: (ticker: string): Promise<MaxPainData | null> =>
    client.get(`/api/max_pain/${ticker}`).then(r => r.data?.data_available ? r.data.data : null).catch(() => null),

  // Backtest
  backtestResults: (): Promise<BacktestResult[]> =>
    client.get('/api/backtest/results').then(r => r.data?.windows ?? []),

  backtestSummaryFull: (): Promise<BacktestSummaryFull> =>
    client.get('/api/backtest/results').then(r => {
      const d = r.data ?? {}
      return {
        oos_sharpe:              d.overall_sharpe,
        spy_sharpe:              d.spy_sharpe,
        worst_drawdown_window:   d.worst_window,
        factor_ics:              d.factor_ic_table ?? [],
        weight_recommendations:  d.weight_recommendations ?? {},
        windows:                 d.windows ?? [],
      }
    }),

  // Resolution
  resolutionLog: (limit?: number): Promise<ResolutionLog[]> =>
    client.get('/api/resolution/log', { params: limit ? { limit } : {} }).then(r => r.data?.data ?? []),

  resolutionLogRich: (date?: string, limit = 100): Promise<ResolutionLogEntry[]> =>
    client.get('/api/resolution/log', { params: { ...(date ? { date } : {}), limit } }).then(r => r.data?.data ?? []),

  resolutionStats: (): Promise<ResolutionStats> =>
    client.get('/api/resolution/stats').then(r => {
      const d = r.data ?? {}
      return {
        claude_skip_rate_pct:  d.claude_skip_rate_pct  ?? d.claude_skip_rate ?? 0,
        avg_agreement_score:   d.avg_agreement_score   ?? d.module_agreement_avg ?? 0,
        most_common_override:  d.most_common_override  ?? '—',
        bear_cb_hits_30d:      d.bear_cb_hits_30d      ?? d.bear_circuit_breaker_hits ?? 0,
      }
    }),

  // Cash management
  cashGet: (): Promise<CashBalance> =>
    client.get('/api/portfolio/cash').then(r => r.data),

  cashUpdate: (action: CashAction, amount: number): Promise<CashBalance> =>
    client.post('/api/portfolio/cash', { action, amount }).then(r => r.data),

  // Thesis accuracy
  thesisAccuracy: (): Promise<ThesisAccuracyResponse> =>
    client.get('/api/signals/accuracy').then(r => r.data),

  thesisOutcomes: (days = 90): Promise<ThesisOutcomesResponse> =>
    client.get('/api/signals/outcomes', { params: { days } }).then(r => r.data),

  // Action zones (live ATR / buy zone / targets)
  tickerActionZones: (symbol: string): Promise<ActionZones | null> =>
    client.get(`/api/ticker/${symbol}/action-zones`).then(r => r.data?.data_available ? r.data : null).catch(() => null),

  tickerAnalyze: (symbol: string): Promise<AnalyzeStatus> =>
    client.post(`/api/ticker/${symbol}/analyze`).then(r => r.data),

  tickerAnalyzeStatus: (symbol: string): Promise<AnalyzeStatus> =>
    client.get(`/api/ticker/${symbol}/analyze/status`).then(r => r.data),

  // Ticker intelligence
  tickerSecFilings: (symbol: string): Promise<SecFiling[]> =>
    client.get(`/api/ticker/${symbol}/sec-filings`).then(r => r.data?.data ?? []),

  tickerCongressTrades: (symbol: string): Promise<CongressTrade[]> =>
    client.get(`/api/ticker/${symbol}/congress-trades`).then(r => r.data?.data ?? []),

  tickerEarnings: (symbol: string): Promise<EarningsData | null> =>
    client.get(`/api/ticker/${symbol}/earnings`).then(r => r.data).catch(() => null),

  // Universe
  universeStats: (): Promise<UniverseStats> =>
    client.get('/api/universe/stats').then(r => {
      const d = r.data ?? {}
      return {
        tickers: d.tickers ?? [],
        total:   d.total   ?? d.total_tickers ?? 0,
      }
    }),
}
