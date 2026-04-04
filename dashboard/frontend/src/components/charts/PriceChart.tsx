/**
 * PriceChart — TradingView Lightweight Charts v5 with advanced overlays.
 *
 * Phase 3 enhancements (Phase 1 + Phase 2 are LOCKED — other components untouched):
 *   1. Lightweight Charts replaces pure-SVG rendering for TradingView-quality candlesticks
 *   2. Automatic trendlines (bullish support ─ green / bearish resistance ─ red)
 *      with breakout detection labels
 *   3. Echo Chamber pattern overlay (orange match + purple dotted projection)
 *      — only rendered when similarity > 75%
 *
 * Data source : GET /api/ticker/{symbol}/ohlcv?period=<1M|3M|6M|1Y>
 * All existing props / level overlays are preserved unchanged (zero breaking changes).
 *
 * Lightweight Charts v5 API notes:
 *   chart.addSeries(CandlestickSeries, opts)   — replaces addCandlestickSeries()
 *   chart.addSeries(LineSeries, opts)           — replaces addLineSeries()
 *   chart.addSeries(HistogramSeries, opts)      — replaces addHistogramSeries()
 */

import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  createChart,
  ColorType,
  CrosshairMode,
  LineStyle,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type SeriesType,
  type IPriceLine,
  type Time,
} from 'lightweight-charts'
import { clsx } from 'clsx'
import { api } from '../../lib/api'
import type { OHLCVBar, OHLCVPeriod } from '../../lib/api'

// ─── Constants ────────────────────────────────────────────────────────────────

const PERIODS: OHLCVPeriod[] = ['1M', '3M', '6M', '1Y']
const CHART_HEIGHT = 380   // px — same visual footprint as the old SVG

// ─── Chart theme (dark, matches app palette) ──────────────────────────────────

const BASE_CHART_OPTIONS = {
  layout: {
    background:  { type: ColorType.Solid, color: '#0a0a0a' },
    textColor:   '#52525b',
    fontFamily:  'IBM Plex Mono, monospace',
    fontSize:    10,
  },
  grid: {
    vertLines: { color: '#1c1c1e' },
    horzLines: { color: '#27272a' },
  },
  crosshair: {
    mode:     CrosshairMode.Normal,
    vertLine: { color: '#52525b', labelBackgroundColor: '#27272a' },
    horzLine: { color: '#52525b', labelBackgroundColor: '#27272a' },
  },
  rightPriceScale: { borderColor: '#27272a' },
  timeScale:       { borderColor: '#27272a', timeVisible: false, secondsVisible: false },
  handleScroll:    true,
  handleScale:     true,
} as const

// ─── Trendline types + helpers ────────────────────────────────────────────────

interface SwingPoint { idx: number; price: number; time: string }

interface TrendlineData {
  startTime:  string
  startPrice: number
  endTime:    string
  endPrice:   number
  slope:      number   // price change per bar index
  p2:         SwingPoint
}

/**
 * Find the most-recent `maxSwings` swing lows within the last `lookback` bars.
 *
 * A swing low: bar.low is strictly the lowest point among every bar within
 * `win` candles on each side (win=5 keeps detection tight; avoids noisy pivots).
 *
 * We constrain the search to recent history so that old, high-price bars from
 * an earlier uptrend don't pollute the support line on a downtrending ticker.
 */
function findSwingLows(
  bars:      OHLCVBar[],
  win        = 5,
  lookback   = 40,
  maxSwings  = 2,
): SwingPoint[] {
  const result: SwingPoint[] = []
  // Only inspect the most recent `lookback` bars; leave `win` bars of room on each side
  const searchStart = Math.max(win, bars.length - lookback)
  const searchEnd   = bars.length - win

  for (let i = searchStart; i < searchEnd; i++) {
    const lo = bars[i].low
    let isSwing = true
    for (let j = i - win; j <= i + win; j++) {
      if (j !== i && bars[j].low < lo) { isSwing = false; break }
    }
    if (isSwing) result.push({ idx: i, price: lo, time: bars[i].date })
  }

  // Return only the most-recent pivots
  return result.slice(-maxSwings)
}

/**
 * Find the most-recent `maxSwings` swing highs within the last `lookback` bars.
 *
 * A swing high: bar.high is strictly the highest point among every bar within
 * `win` candles on each side.
 */
function findSwingHighs(
  bars:      OHLCVBar[],
  win        = 5,
  lookback   = 40,
  maxSwings  = 2,
): SwingPoint[] {
  const result: SwingPoint[] = []
  const searchStart = Math.max(win, bars.length - lookback)
  const searchEnd   = bars.length - win

  for (let i = searchStart; i < searchEnd; i++) {
    const hi = bars[i].high
    let isSwing = true
    for (let j = i - win; j <= i + win; j++) {
      if (j !== i && bars[j].high > hi) { isSwing = false; break }
    }
    if (isSwing) result.push({ idx: i, price: hi, time: bars[i].date })
  }

  return result.slice(-maxSwings)
}

/**
 * Build a trendline through the two most-recent swing points.
 * Extended `extendBars` trading-day equivalents into the future.
 */
function buildTrendline(
  swings: SwingPoint[],
  bars:   OHLCVBar[],
  extendBars = 30,
): TrendlineData | null {
  if (swings.length < 2) return null
  const [p1, p2] = swings.slice(-2)
  const slope    = (p2.price - p1.price) / (p2.idx - p1.idx)

  // Calendar days to future: ~1.4 cal days per trading day
  const lastDate  = new Date(bars[bars.length - 1].date)
  const futureDate = new Date(lastDate)
  futureDate.setDate(futureDate.getDate() + Math.round(extendBars * 1.4))
  const endDateStr = futureDate.toISOString().slice(0, 10)
  const endPrice   = p2.price + slope * (bars.length - 1 + extendBars - p2.idx)

  return { startTime: p1.time, startPrice: p1.price, endTime: endDateStr, endPrice, slope, p2 }
}

type BreakoutKind = 'approaching' | 'bullish_breakout' | 'bearish_breakdown' | null

/**
 * Detect if price is approaching or has just broken through a trendline.
 * type='support'    → looks for bearish breakdown (price falls below line)
 * type='resistance' → looks for bullish breakout  (price rises above line)
 */
function detectBreakout(
  bars: OHLCVBar[],
  tl:   TrendlineData,
  type: 'support' | 'resistance',
): BreakoutKind {
  if (bars.length < 5) return null
  const n            = bars.length - 1
  const tlAtN        = tl.p2.price + tl.slope * (n - tl.p2.idx)
  const currentClose = bars[n].close
  const pct          = Math.abs(currentClose - tlAtN) / tlAtN

  if (pct < 0.015) return 'approaching'   // within 1.5% of the line

  // Check cross in last 3 bars
  for (let i = Math.max(0, n - 3); i < n; i++) {
    const prevClose = bars[i].close
    const tlAtI     = tl.p2.price + tl.slope * (i - tl.p2.idx)
    if (type === 'support'    && prevClose >= tlAtI && currentClose < tlAtN) return 'bearish_breakdown'
    if (type === 'resistance' && prevClose <= tlAtI && currentClose > tlAtN) return 'bullish_breakout'
  }
  return null
}

// ─── Echo Chamber types + helpers ────────────────────────────────────────────

interface EchoMatch {
  similarity:  number    // 0-1
  matchedBars: OHLCVBar[]
  afterBars:   OHLCVBar[]
  direction:   'bullish' | 'bearish'
}

/** Log-return series. */
function logReturns(prices: number[]): number[] {
  return prices.slice(1).map((p, i) => Math.log(p / prices[i]))
}

/** Pearson correlation — returns value in [−1, 1]. */
function pearson(a: number[], b: number[]): number {
  const n = a.length
  if (n === 0) return 0
  const ma = a.reduce((s, v) => s + v, 0) / n
  const mb = b.reduce((s, v) => s + v, 0) / n
  let num = 0, da = 0, db = 0
  for (let i = 0; i < n; i++) {
    num += (a[i] - ma) * (b[i] - mb)
    da  += (a[i] - ma) ** 2
    db  += (b[i] - mb) ** 2
  }
  const denom = Math.sqrt(da * db)
  return denom === 0 ? 0 : num / denom
}

/**
 * Slide a window over historical bars and find the best pattern match for
 * the most recent `windowSize` bars. Returns null if best score < 0.75.
 */
function detectEchoChamber(
  bars:       OHLCVBar[],
  windowSize = 20,
  afterSize  = 10,
): EchoMatch | null {
  if (bars.length < windowSize * 2 + afterSize) return null

  const currentReturns = logReturns(bars.slice(-windowSize).map(b => b.close))

  let bestSim    = 0
  let bestStart  = -1

  // Exclude the current window itself from the search
  const searchEnd = bars.length - windowSize - afterSize
  for (let start = 0; start <= searchEnd - windowSize; start++) {
    const histReturns = logReturns(bars.slice(start, start + windowSize).map(b => b.close))
    const sim         = (pearson(currentReturns, histReturns) + 1) / 2  // normalize → [0,1]
    if (sim > bestSim) { bestSim = sim; bestStart = start }
  }

  // Threshold: 75% similarity
  if (bestSim < 0.75 || bestStart < 0) return null

  const matchedBars = bars.slice(bestStart, bestStart + windowSize)
  const afterBars   = bars.slice(bestStart + windowSize, bestStart + windowSize + afterSize)
  const direction: 'bullish' | 'bearish' =
    afterBars.length > 0 && afterBars[afterBars.length - 1].close > afterBars[0].close
      ? 'bullish' : 'bearish'

  return { similarity: bestSim, matchedBars, afterBars, direction }
}

/** Scale historical prices so the first bar anchors to the current window's first close. */
function scaleEchoOverlay(historicalBars: OHLCVBar[], currentBars: OHLCVBar[]): number[] {
  if (!historicalBars.length || !currentBars.length) return []
  const scale = currentBars[0].close / historicalBars[0].close
  return historicalBars.map(b => b.close * scale)
}

/** Add N calendar days to a YYYY-MM-DD string. */
function addDays(dateStr: string, days: number): string {
  const d = new Date(dateStr)
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

// ─── Heikin Ashi ──────────────────────────────────────────────────────────────

const HA_STORAGE_KEY = 'priceChart:heikinAshiEnabled'

/**
 * Convert standard OHLCV bars to Heikin Ashi candles.
 *
 * Formulas (standard definition):
 *   HA_Close = (Open + High + Low + Close) / 4
 *   HA_Open  = (prev HA_Open + prev HA_Close) / 2
 *              first bar: (Open + Close) / 2
 *   HA_High  = max(High, HA_Open, HA_Close)
 *   HA_Low   = min(Low,  HA_Open, HA_Close)
 *
 * Volume is kept as-is (real volume displayed under HA candles).
 *
 * IMPORTANT: only the CandlestickSeries ever receives HA values.
 * Trendlines, price lines, echo chamber, and breakout detection always
 * operate on the original `bars` array (real prices), never on HA output.
 */
function toHeikinAshi(bars: OHLCVBar[]): OHLCVBar[] {
  const ha: OHLCVBar[] = []
  for (let i = 0; i < bars.length; i++) {
    const b       = bars[i]
    const haClose = (b.open + b.high + b.low + b.close) / 4
    const haOpen  = i === 0
      ? (b.open + b.close) / 2
      : (ha[i - 1].open + ha[i - 1].close) / 2
    const haHigh  = Math.max(b.high, haOpen, haClose)
    const haLow   = Math.min(b.low,  haOpen, haClose)
    ha.push({ date: b.date, open: haOpen, high: haHigh, low: haLow, close: haClose, volume: b.volume })
  }
  return ha
}

// ─── Props (identical to Phase 1/2 interface — zero breaking changes) ─────────

interface PriceChartProps {
  symbol: string
  // AI thesis levels
  aiEntryLow?:   number | null
  aiEntryHigh?:  number | null
  aiTarget1?:    number | null
  aiTarget2?:    number | null
  aiStop?:       number | null
  // Market structure
  vwap?:         number | null
  maxPain?:      number | null
  // Live action-zone levels
  azBuyLow?:     number | null
  azBuyHigh?:    number | null
  azTarget1?:    number | null
  azTarget2?:    number | null
  azStop?:       number | null
  // Current price
  currentPrice?: number | null
}

// ─── Main component ───────────────────────────────────────────────────────────

export function PriceChart({
  symbol,
  aiEntryLow, aiEntryHigh, aiTarget1, aiTarget2, aiStop,
  vwap, maxPain,
  azBuyLow, azBuyHigh, azTarget1, azTarget2, azStop,
  currentPrice,
}: PriceChartProps) {
  const [period, setPeriod] = useState<OHLCVPeriod>('3M')

  // ── Heikin Ashi toggle — persisted across sessions ───────────────────────────
  const [haEnabled, setHaEnabled] = useState<boolean>(() => {
    try { const v = localStorage.getItem(HA_STORAGE_KEY); return v === null ? true : v === 'true' } catch { return true }
  })
  const toggleHA = () =>
    setHaEnabled(prev => {
      const next = !prev
      try { localStorage.setItem(HA_STORAGE_KEY, String(next)) } catch {}
      return next
    })

  // ── Handbook open/closed ─────────────────────────────────────────────────────
  const [handbookOpen, setHandbookOpen] = useState(false)

  // ── Analysis state (fed by bar-update effect, drives overlay labels) ────────
  const [echoMatch,       setEchoMatch]       = useState<EchoMatch | null>(null)
  const [supportBreakout, setSupportBreakout] = useState<BreakoutKind>(null)
  const [resistBreakout,  setResistBreakout]  = useState<BreakoutKind>(null)

  // ── Fetch OHLCV ─────────────────────────────────────────────────────────────
  const { data: ohlcv, isLoading, isError } = useQuery({
    queryKey:  ['ohlcv', symbol, period],
    queryFn:   () => api.tickerOHLCV(symbol, period),
    staleTime: 15 * 60 * 1000,
    enabled:   !!symbol,
  })
  const bars = ohlcv?.data ?? []

  // ── Imperative chart refs ────────────────────────────────────────────────────
  const containerRef  = useRef<HTMLDivElement>(null)
  const chartRef      = useRef<IChartApi | null>(null)
  const candlesRef    = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef     = useRef<ISeriesApi<'Histogram'>   | null>(null)
  // Dynamic overlay series (trendlines + echo chamber) — rebuilt on each data update
  const overlayRef    = useRef<ISeriesApi<SeriesType>[]>([])
  // Price lines (horizontal levels) — rebuilt when level props change
  const priceLinesRef = useRef<IPriceLine[]>([])

  // ── Create chart + base series once on mount ─────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      ...BASE_CHART_OPTIONS,
      width:  containerRef.current.clientWidth || 600,
      height: CHART_HEIGHT,
    })

    // Main candlestick series
    const candles = chart.addSeries(CandlestickSeries, {
      upColor:         '#22c55e',
      downColor:       '#ef4444',
      borderUpColor:   '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor:     '#22c55e80',
      wickDownColor:   '#ef444480',
    })

    // Volume histogram — occupies the bottom ~18% of the pane
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat:  { type: 'volume' },
      priceScaleId: 'vol',
    })
    chart.priceScale('vol').applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    })

    chartRef.current   = chart
    candlesRef.current = candles
    volumeRef.current  = volume

    // Responsive width via ResizeObserver
    const ro = new ResizeObserver(entries => {
      const w = entries[0].contentRect.width
      if (w > 0 && chartRef.current) chartRef.current.applyOptions({ width: w })
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      overlayRef.current    = []
      priceLinesRef.current = []
      chart.remove()
      chartRef.current   = null
      candlesRef.current = null
      volumeRef.current  = null
    }
  }, [])  // run once on mount

  // ── Update OHLCV data + trendlines + echo chamber when bars / period change ──
  useEffect(() => {
    const chart   = chartRef.current
    const candles = candlesRef.current
    const volume  = volumeRef.current
    if (!chart || !candles || !volume || bars.length === 0) return

    // Feed candlestick data — Heikin Ashi when toggled, standard otherwise.
    // All other overlays (trendlines, price lines, echo chamber) always use
    // the real `bars` array below, never the HA-converted values.
    const displayBars = haEnabled ? toHeikinAshi(bars) : bars
    candles.setData(displayBars.map(b => ({
      time:  b.date as Time,
      open:  b.open,
      high:  b.high,
      low:   b.low,
      close: b.close,
    })))

    // Feed volume data (colour by direction)
    volume.setData(bars.map(b => ({
      time:  b.date as Time,
      value: b.volume,
      color: b.close >= b.open ? '#22c55e30' : '#ef444430',
    })))

    chart.timeScale().fitContent()

    // Remove any previous overlay series (trendlines + echo)
    for (const s of overlayRef.current) {
      try { chart.removeSeries(s) } catch (_) { /* already removed */ }
    }
    overlayRef.current = []

    // ── Trendline detection ──────────────────────────────────────────────────
    //
    // APPROACH: compute two candidate lines (one from swing lows, one from swing
    // highs), then FORCE-ASSIGN by vertical position at the current candle:
    //   • The LOWER candidate  → Support TL  (green)
    //   • The UPPER candidate  → Resistance TL (red)
    //
    // This swap guarantee means color/role are always correct regardless of which
    // type of pivot each line was built from.  On a downtrending ticker the
    // "swing-low" line can sit above price; the forced assignment catches that and
    // swaps automatically instead of drawing a misleading green line at the top.
    //
    // Lookback scales with timeframe (focus on recent price action only):
    //   1M → 15 bars   3M → 25 bars   6M → 35 bars   1Y → 50 bars
    const swingLookback = period === '1M' ? 15 : period === '3M' ? 25 : period === '6M' ? 35 : 50
    const currentClose  = bars[bars.length - 1].close
    const lastBarIdx    = bars.length - 1

    // Evaluate a trendline at any bar index via its stored slope + anchor point
    const evalTl = (tl: TrendlineData, idx: number) =>
      tl.p2.price + tl.slope * (idx - tl.p2.idx)

    // Build two raw candidates — one anchored to swing lows, one to swing highs
    const tlFromLows  = buildTrendline(findSwingLows (bars, 5, swingLookback), bars)
    const tlFromHighs = buildTrendline(findSwingHighs(bars, 5, swingLookback), bars)

    // Evaluate each candidate's price level at the current (last) candle
    const valLows  = tlFromLows  ? evalTl(tlFromLows,  lastBarIdx) : null
    const valHighs = tlFromHighs ? evalTl(tlFromHighs, lastBarIdx) : null

    // ── Forced assignment: lower value → Support (green), higher → Resistance (red) ──
    let finalSupportTl: TrendlineData | null = null
    let finalResistTl:  TrendlineData | null = null

    if (tlFromLows && tlFromHighs && valLows !== null && valHighs !== null) {
      // Both candidates present — assign strictly by which sits lower at current bar
      if (valLows <= valHighs) {
        finalSupportTl = tlFromLows   // normal case: swing-low line is lower
        finalResistTl  = tlFromHighs
      } else {
        // Swap: the swing-low line ended up above the swing-high line (e.g. downtrend)
        finalSupportTl = tlFromHighs
        finalResistTl  = tlFromLows
      }
    } else if (tlFromLows && valLows !== null) {
      // Only one candidate from lows — place it in the correct role
      if (valLows <= currentClose) finalSupportTl = tlFromLows
      else                         finalResistTl  = tlFromLows
    } else if (tlFromHighs && valHighs !== null) {
      // Only one candidate from highs
      if (valHighs >= currentClose) finalResistTl  = tlFromHighs
      else                          finalSupportTl = tlFromHighs
    }

    // Final guard: if a line is still on the wrong side of price (>3% tolerance),
    // drop it rather than render something misleading
    if (finalSupportTl && evalTl(finalSupportTl, lastBarIdx) > currentClose * 1.03) finalSupportTl = null
    if (finalResistTl  && evalTl(finalResistTl,  lastBarIdx) < currentClose * 0.97) finalResistTl  = null

    const addTrendlineSeries = (tl: TrendlineData | null, color: string) => {
      if (!tl) return
      const s = chart.addSeries(LineSeries, {
        color,
        lineWidth:              2,
        lineStyle:              LineStyle.Solid,
        crosshairMarkerVisible: false,
        lastValueVisible:       false,
        priceLineVisible:       false,
      })
      s.setData([
        { time: tl.startTime as Time, value: tl.startPrice },
        { time: tl.endTime   as Time, value: tl.endPrice   },
      ])
      overlayRef.current.push(s)
    }

    addTrendlineSeries(finalSupportTl, '#22c55e')   // Support TL  — always green (lower line)
    addTrendlineSeries(finalResistTl,  '#ef4444')   // Resistance TL — always red (upper line)

    // Breakout detection uses the correctly-assigned final lines
    setSupportBreakout(finalSupportTl ? detectBreakout(bars, finalSupportTl, 'support')    : null)
    setResistBreakout( finalResistTl  ? detectBreakout(bars, finalResistTl,  'resistance') : null)

    // ── Echo Chamber detection ───────────────────────────────────────────────
    // Window size scales with period so the pattern length is meaningful
    const echoWindow = period === '1M' ? 10 : period === '3M' ? 20 : period === '6M' ? 25 : 30
    const echo = detectEchoChamber(bars, echoWindow)
    setEchoMatch(echo)

    if (echo) {
      const currentWindow = bars.slice(-echoWindow)
      const scaledPrices  = scaleEchoOverlay(echo.matchedBars, currentWindow)

      // Orange overlay — historical matched path rescaled onto current dates
      const echoSeries = chart.addSeries(LineSeries, {
        color:                  '#f97316',
        lineWidth:              2,
        lineStyle:              LineStyle.Solid,
        crosshairMarkerVisible: false,
        lastValueVisible:       false,
        priceLineVisible:       false,
      })
      echoSeries.setData(currentWindow.map((bar, i) => ({
        time:  bar.date as Time,
        value: scaledPrices[i] ?? bar.close,
      })))
      overlayRef.current.push(echoSeries)

      // Dotted purple projection — what happened AFTER the historical match
      if (echo.afterBars.length > 0) {
        const lastBar    = bars[bars.length - 1]
        const lastScaled = scaledPrices[scaledPrices.length - 1] ?? lastBar.close
        const projScale  = lastScaled / echo.afterBars[0].close

        const projSeries = chart.addSeries(LineSeries, {
          color:                  '#a855f7',
          lineWidth:              1,
          lineStyle:              LineStyle.Dashed,
          crosshairMarkerVisible: false,
          lastValueVisible:       false,
          priceLineVisible:       false,
        })
        // Space projection points 2 calendar days apart to avoid date collisions
        projSeries.setData(echo.afterBars.map((bar, i) => ({
          time:  addDays(lastBar.date, (i + 1) * 2) as Time,
          value: bar.close * projScale,
        })))
        overlayRef.current.push(projSeries)
      }
    }
  }, [bars, period, haEnabled])

  // ── Update horizontal price-line overlays whenever level props change ────────
  useEffect(() => {
    const candles = candlesRef.current
    if (!candles) return

    // Remove previous price lines
    for (const pl of priceLinesRef.current) {
      try { candles.removePriceLine(pl) } catch (_) {}
    }
    priceLinesRef.current = []

    const addPL = (
      price: number | null | undefined,
      title: string,
      color: string,
      style: LineStyle = LineStyle.Solid,
      width: 1 | 2    = 1,
    ) => {
      if (!price || price <= 0) return
      const pl = candles.createPriceLine({ price, color, lineWidth: width, lineStyle: style, axisLabelVisible: true, title })
      priceLinesRef.current.push(pl)
    }

    // Phase 1 + Phase 2 levels (all preserved)
    addPL(aiStop,      'SL',        '#ef4444', LineStyle.Solid,  2)
    addPL(aiEntryLow,  'AI Entry▼', '#3b82f6', LineStyle.Dashed, 1)
    addPL(aiEntryHigh, 'AI Entry▲', '#3b82f6', LineStyle.Dashed, 1)
    addPL(aiTarget1,   'T1',        '#22c55e', LineStyle.Dashed, 1)
    addPL(aiTarget2,   'T2',        '#22c55e', LineStyle.Solid,  1)
    addPL(vwap,        'VWAP',      '#3b82f6', LineStyle.Dashed, 1)
    addPL(maxPain,     'MaxPain',   '#f59e0b', LineStyle.Dashed, 1)
    addPL(azStop,      'Live SL',   '#f97316', LineStyle.Dashed, 1)
    addPL(azTarget1,   'Live T1',   '#86efac', LineStyle.Dashed, 1)
    addPL(azTarget2,   'Live T2',   '#86efac', LineStyle.Dashed, 1)
    addPL(currentPrice,'▶ Price',   '#fafafa', LineStyle.Solid,  2)

    // NOTE: Entry-zone BANDS (azBuyLow/azBuyHigh + aiEntry band) are shown as
    // boundary price lines only. True filled horizontal bands require a custom
    // Lightweight Charts plugin (TODO if priority increases).
    addPL(azBuyLow,  'Buy Zone▼', '#f59e0b', LineStyle.Dashed, 1)
    addPL(azBuyHigh, 'Buy Zone▲', '#f59e0b', LineStyle.Dashed, 1)

  }, [aiStop, aiEntryLow, aiEntryHigh, aiTarget1, aiTarget2, vwap, maxPain,
      azStop, azTarget1, azTarget2, currentPrice, azBuyLow, azBuyHigh])

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <div className="bg-bg-surface border border-border-subtle rounded p-3 space-y-2">

      {/* ── Header: title + controls ──────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-widest text-text-tertiary">
          Price Chart
        </span>
        <div className="flex items-center gap-1">
          {PERIODS.map(p => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={clsx(
                'font-mono text-[9px] px-1.5 py-0.5 rounded border transition-colors',
                period === p
                  ? 'bg-accent-blue/20 text-accent-blue border-accent-blue/40'
                  : 'text-text-tertiary border-border-subtle hover:text-text-secondary',
              )}
            >
              {p}
            </button>
          ))}
          {/* Divider */}
          <span className="w-px h-3 bg-border-subtle mx-0.5 opacity-50" />
          {/* Heikin Ashi toggle */}
          <button
            onClick={toggleHA}
            title={haEnabled ? 'Switch to standard candles' : 'Switch to Heikin Ashi (smoothed trend)'}
            className={clsx(
              'font-mono text-[9px] px-1.5 py-0.5 rounded border transition-colors',
              haEnabled
                ? 'bg-amber-500/20 text-amber-400 border-amber-500/40'
                : 'text-text-tertiary border-border-subtle hover:text-text-secondary',
            )}
          >
            {haEnabled ? 'Heikin Ashi' : 'Standard'}
          </button>
          {/* Divider */}
          <span className="w-px h-3 bg-border-subtle mx-0.5 opacity-50" />
          {/* Handbook toggle */}
          <button
            onClick={() => setHandbookOpen(o => !o)}
            title="Chart Handbook – How to trade with this tool"
            className={clsx(
              'font-mono text-[9px] w-5 h-5 flex items-center justify-center rounded border transition-colors',
              handbookOpen
                ? 'bg-violet-500/20 text-violet-400 border-violet-500/40'
                : 'text-text-tertiary border-border-subtle hover:text-text-secondary',
            )}
          >
            ?
          </button>
        </div>
      </div>

      {/* ── Chart Handbook (collapsible) ──────────────────────────────────── */}
      {handbookOpen && (
        <div className="rounded border border-violet-500/20 bg-violet-500/5 p-3 space-y-3 text-[11px] font-mono">

          {/* Title */}
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="text-violet-300 font-bold text-[11px] uppercase tracking-wide">
                Chart Handbook — How to Trade with This Tool
              </p>
              <p className="text-text-tertiary text-[10px] mt-0.5">
                Deep Dive Price Chart · Retail Swing Trader Edition
              </p>
            </div>
            <button
              onClick={() => setHandbookOpen(false)}
              className="text-text-tertiary hover:text-text-secondary border border-border-subtle
                         rounded px-1.5 py-0.5 text-[9px] shrink-0"
            >
              ✕ Close
            </button>
          </div>

          {/* Default settings */}
          <div className="space-y-0.5">
            <p className="text-text-secondary font-bold">Default Settings (already active)</p>
            {[
              ['Heikin Ashi candles', 'ON by default — smooth trend, fewer whipsaws'],
              ['Timeframe',           '3M — best for weekly / monthly style'],
              ['Green line',          'Support Trendline (lower)'],
              ['Red line',            'Resistance Trendline (upper)'],
              ['Horizontal levels',   'Buy Zone, T1, T2, SL, VWAP, Max Pain — always real prices'],
            ].map(([label, desc]) => (
              <p key={label} className="text-text-tertiary">
                <span className="text-text-secondary">· {label}:</span> {desc}
              </p>
            ))}
          </div>

          {/* Core trading rules table */}
          <div className="space-y-1">
            <p className="text-text-secondary font-bold">Core Trading Rules</p>
            <div className="overflow-x-auto">
              <table className="w-full text-[10px] border-collapse">
                <thead>
                  <tr className="border-b border-border-subtle">
                    <th className="text-left py-1 pr-3 text-text-tertiary font-normal w-[38%]">What You See</th>
                    <th className="text-left py-1 pr-3 text-text-tertiary font-normal w-[24%]">What It Means</th>
                    <th className="text-left py-1 pr-3 text-text-tertiary font-normal w-[19%]">Weekly Action</th>
                    <th className="text-left py-1 text-text-tertiary font-normal w-[19%]">Monthly Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle/40">
                  {[
                    [
                      'Long green HA candles + price above green Support TL',
                      'Strong uptrend',
                      'Enter or add on pullback to green line',
                      'High-conviction hold',
                    ],
                    [
                      '"Bullish Breakout – Potential Entry" label + price breaks above red Resistance TL',
                      'Momentum shift',
                      'Take quick weekly scalp toward T1',
                      'Strong signal to enter monthly position',
                    ],
                    [
                      'Price respecting green Support TL on a pullback',
                      'Healthy dip in uptrend',
                      'Add to position',
                      'Excellent monthly entry zone',
                    ],
                    [
                      'Echo Chamber active (orange line + purple projection + "XX% Bullish")',
                      'History repeating',
                      'High-probability setup',
                      'Trust the projection for target timing',
                    ],
                    [
                      'Red Resistance TL holding + smaller HA candles',
                      'Weakness / distribution',
                      'Take partial profit at T1',
                      'Consider reducing size or tightening stop',
                    ],
                    [
                      'Price breaks below green Support TL',
                      'Trend change',
                      'Tighten stop or exit',
                      'Strong invalidation signal',
                    ],
                  ].map(([signal, meaning, weekly, monthly]) => (
                    <tr key={signal}>
                      <td className="py-1 pr-3 text-text-secondary align-top">{signal}</td>
                      <td className="py-1 pr-3 text-amber-400/80 align-top">{meaning}</td>
                      <td className="py-1 pr-3 text-green-400/80 align-top">{weekly}</td>
                      <td className="py-1 text-blue-400/80 align-top">{monthly}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Golden rules */}
          <div className="space-y-1">
            <p className="text-text-secondary font-bold">Golden Rules for Your Style</p>
            <p className="text-text-tertiary">
              <span className="text-text-secondary">· Weekly trades:</span>{' '}
              Use 1M or 3M timeframe + look for breakout labels + Echo Chamber.
            </p>
            <p className="text-text-tertiary">
              <span className="text-text-secondary">· Monthly trades:</span>{' '}
              Use 3M or 6M timeframe + focus on price respecting the green Support TL near the AI Buy Zone.
            </p>
            <p className="text-text-tertiary mt-1">
              <span className="text-text-secondary">Always cross-check with:</span>
            </p>
            {[
              ['Position Sizer',          'exact shares for 1% risk'],
              ['Risk-Reward Bar + EV',    'confirms positive expectancy'],
              ['Historical Analogs',      'shows how similar setups performed'],
              ['Earnings Reaction Model', 'critical when earnings are < 3 weeks away'],
            ].map(([tool, desc]) => (
              <p key={tool} className="text-text-tertiary pl-2">
                → <span className="text-violet-300">{tool}</span> — {desc}
              </p>
            ))}
          </div>

          {/* Pro tip */}
          <div className="rounded border border-amber-500/20 bg-amber-500/5 px-2.5 py-2">
            <p className="text-amber-400 font-bold mb-0.5">Pro Tip</p>
            <p className="text-text-tertiary">
              Toggle to <span className="text-text-secondary">"Standard"</span> candles only when you need
              precise wick levels for stop placement. Keep{' '}
              <span className="text-amber-400">Heikin Ashi</span> on for trend direction and timing.
            </p>
          </div>

          {/* Close button (bottom) */}
          <div className="flex justify-end pt-1 border-t border-border-subtle">
            <button
              onClick={() => setHandbookOpen(false)}
              className="font-mono text-[9px] px-2 py-0.5 rounded border border-border-subtle
                         text-text-tertiary hover:text-text-secondary transition-colors"
            >
              ✕ Close Handbook
            </button>
          </div>
        </div>
      )}

      {/* ── Chart body ────────────────────────────────────────────────────── */}
      <div className="relative">

        {/* Lightweight Charts canvas container — always mounted so the chart
            instance is stable. Loading/error states overlay on top. */}
        <div ref={containerRef} style={{ height: CHART_HEIGHT }} />

        {/* Loading overlay */}
        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-bg-surface/80">
            <span className="font-mono text-xs text-text-tertiary animate-pulse">
              Loading chart…
            </span>
          </div>
        )}

        {/* Error / no-data overlay */}
        {!isLoading && (isError || bars.length === 0) && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="font-mono text-xs text-text-tertiary">
              No price data available — yfinance may not cover this ticker.
            </span>
          </div>
        )}

        {/* ── Breakout zone labels (bottom-left) ────────────────────────── */}
        {bars.length > 0 && (supportBreakout || resistBreakout) && (
          <div className="absolute bottom-8 left-2 flex flex-col gap-1 pointer-events-none z-10">
            {supportBreakout === 'approaching' && (
              <span className="font-mono text-[9px] px-1.5 py-0.5 rounded
                               bg-yellow-500/20 text-yellow-400 border border-yellow-500/30">
                BREAKOUT ZONE — Support
              </span>
            )}
            {supportBreakout === 'bearish_breakdown' && (
              <span className="font-mono text-[9px] px-1.5 py-0.5 rounded
                               bg-red-500/20 text-red-400 border border-red-500/30">
                Bearish Breakdown — Exit Signal
              </span>
            )}
            {resistBreakout === 'approaching' && (
              <span className="font-mono text-[9px] px-1.5 py-0.5 rounded
                               bg-yellow-500/20 text-yellow-400 border border-yellow-500/30">
                BREAKOUT ZONE — Resistance
              </span>
            )}
            {resistBreakout === 'bullish_breakout' && (
              <span className="font-mono text-[9px] px-1.5 py-0.5 rounded
                               bg-green-500/20 text-green-400 border border-green-500/30">
                Bullish Breakout — Potential Entry
              </span>
            )}
          </div>
        )}

        {/* ── Top-right legend (trendlines + echo chamber) ───────────────── */}
        {bars.length > 0 && (
          <div className="absolute top-2 right-2 flex flex-col gap-0.5 pointer-events-none z-10
                          bg-black/50 rounded px-1.5 py-1">
            {/* HA mode badge — shown only when active */}
            {haEnabled && (
              <span className="font-mono text-[7px] text-amber-400 text-center
                               bg-amber-500/10 rounded px-1 py-0.5 mb-0.5">
                HA — smoothed trend
              </span>
            )}
            <div className="flex items-center gap-1.5">
              <span className="inline-block w-5 h-[1.5px] bg-green-500" />
              <span className="font-mono text-[8px] text-zinc-400">Support TL</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="inline-block w-5 h-[1.5px] bg-red-500" />
              <span className="font-mono text-[8px] text-zinc-400">Resistance TL</span>
            </div>
            {echoMatch && (
              <>
                <div className="flex items-center gap-1.5">
                  <span className="inline-block w-5 h-[1.5px] bg-orange-500" />
                  <span className="font-mono text-[8px] text-zinc-400">Echo Match</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span
                    className="inline-block w-5"
                    style={{ borderTop: '1.5px dashed #a855f7' }}
                  />
                  <span className="font-mono text-[8px] text-zinc-400">Echo Projection</span>
                </div>
                <span className={clsx(
                  'font-mono text-[7px] px-1 py-0.5 rounded mt-0.5 text-center',
                  echoMatch.direction === 'bullish'
                    ? 'bg-green-500/15 text-green-400'
                    : 'bg-red-500/15 text-red-400',
                )}>
                  Echo Chamber {Math.round(echoMatch.similarity * 100)}%
                  {' — '}
                  {echoMatch.direction === 'bullish' ? '▲ Bullish' : '▼ Bearish'}
                </span>
              </>
            )}
          </div>
        )}
      </div>

      {/* ── Level legend below chart (Phase 1/2 unchanged) ────────────────── */}
      {bars.length > 0 && (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 pt-1 border-t border-border-subtle">
          {[
            aiStop      != null && { color: '#ef4444', label: 'AI Stop' },
            aiTarget1   != null && { color: '#22c55e', label: 'AI T1/T2',     dashed: true },
            aiEntryLow  != null && { color: '#3b82f6', label: 'AI Entry zone' },
            vwap        != null && { color: '#3b82f6', label: 'VWAP',         dashed: true },
            maxPain     != null && { color: '#f59e0b', label: 'Max Pain',      dashed: true },
            azBuyLow    != null && { color: '#f59e0b', label: 'Live zone' },
            azTarget1   != null && { color: '#86efac', label: 'Live T1/T2',   dashed: true },
          ].filter(Boolean).map((item: any) => (
            <span key={item.label} className="flex items-center gap-1 font-mono text-[8px] text-text-tertiary">
              <span
                className="inline-block w-5 border-t"
                style={{
                  borderColor: item.color,
                  borderStyle: item.dashed ? 'dashed' : 'solid',
                  opacity:     0.8,
                }}
              />
              {item.label}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
