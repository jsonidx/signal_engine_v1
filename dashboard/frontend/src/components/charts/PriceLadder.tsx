interface PriceLadderProps {
  currentPrice: number
  target1?: number
  target2?: number
  entryLow?: number
  entryHigh?: number
  stopLoss?: number
  poc?: number
  vwap?: number
  maxPain?: number
  height?: number
  // Live action-zone overlay (technical, ATR-based) — values in USD
  azBuyLow?: number
  azBuyHigh?: number
  azStop?: number
  azTarget1?: number
  azTarget2?: number
  // When provided: left labels show USD, right labels show EUR
  fxRate?: number
}

const SVG_WIDTH = 320
const LEFT_MARGIN = 80   // wider to fit "Label  $XXX"
const RIGHT_MARGIN = 10
const CHART_WIDTH = SVG_WIDTH - LEFT_MARGIN - RIGHT_MARGIN
const V_PAD_FRAC = 0.12

function toY(price: number, minP: number, maxP: number, svgH: number): number {
  const range = maxP - minP || 1
  return svgH - V_PAD_FRAC * svgH - ((price - minP) / range) * (svgH * (1 - 2 * V_PAD_FRAC))
}

export function PriceLadder({
  currentPrice,
  target1,
  target2,
  entryLow,
  entryHigh,
  stopLoss,
  poc,
  vwap,
  maxPain,
  height = 280,
  azBuyLow,
  azBuyHigh,
  azStop,
  azTarget1,
  azTarget2,
  fxRate,
}: PriceLadderProps) {
  const svgH = height

  const allPrices = [
    currentPrice, target1, target2, entryLow, entryHigh,
    stopLoss, poc, vwap, maxPain, azBuyLow, azBuyHigh, azStop, azTarget1, azTarget2,
  ].filter((p): p is number => p !== undefined && p > 0)

  if (allPrices.length === 0) return null

  const minP = Math.min(...allPrices)
  const maxP = Math.max(...allPrices)
  const y = (p: number) => toY(p, minP, maxP, svgH)

  // USD always shown on left; EUR shown on right when fxRate is available
  const usd = (p: number) => `$${p.toFixed(2)}`
  const eur = (p: number) => fxRate ? `€${(p / fxRate).toFixed(2)}` : `$${p.toFixed(2)}`

  const hasAzZone  = azBuyLow != null && azBuyHigh != null
  const hasAiZone  = entryLow != null && entryHigh != null
  const aiZoneLabel = hasAzZone && hasAiZone ? 'AI Entry' : 'Entry'

  interface LineSpec { price: number; label: string; color: string; strokeW: number; dashed: boolean }
  const lines: LineSpec[] = []
  if (target2   != null) lines.push({ price: target2,   label: 'AI T2',   color: '#22c55e', strokeW: 1,   dashed: true  })
  if (target1   != null) lines.push({ price: target1,   label: 'AI T1',   color: '#22c55e', strokeW: 1.5, dashed: false })
  if (azTarget2 != null) lines.push({ price: azTarget2, label: 'Live T2', color: '#86efac', strokeW: 1,   dashed: true  })
  if (azTarget1 != null) lines.push({ price: azTarget1, label: 'Live T1', color: '#86efac', strokeW: 1.5, dashed: false })
  if (stopLoss  != null) lines.push({ price: stopLoss,  label: 'AI Stop', color: '#ef4444', strokeW: 1,   dashed: true  })
  if (azStop    != null) lines.push({ price: azStop,    label: 'Live SL', color: '#f97316', strokeW: 1,   dashed: true  })

  interface TickSpec { price: number; label: string; color: string }
  const ticks: TickSpec[] = []
  if (poc     != null) ticks.push({ price: poc,     label: 'POC',     color: '#a855f7' })
  if (vwap    != null) ticks.push({ price: vwap,    label: 'VWAP',    color: '#3b82f6' })
  if (maxPain != null) ticks.push({ price: maxPain, label: 'MaxPain', color: '#f59e0b' })

  const currentY = y(currentPrice)
  const entryY1  = entryHigh  != null ? y(entryHigh)  : 0
  const entryY2  = entryLow   != null ? y(entryLow)   : 0
  const azY1     = azBuyHigh  != null ? y(azBuyHigh)  : 0
  const azY2     = azBuyLow   != null ? y(azBuyLow)   : 0

  return (
    <svg width={SVG_WIDTH} height={svgH} style={{ fontFamily: 'IBM Plex Mono, monospace' }}>
      {/* Live buy zone band — amber */}
      {hasAzZone && (
        <>
          <rect x={LEFT_MARGIN} y={azY1} width={CHART_WIDTH} height={azY2 - azY1}
            fill="#f59e0b18" stroke="#f59e0b50" strokeWidth={0.5} />
          {/* left: label + USD low */}
          <text x={LEFT_MARGIN - 4} y={azY1 + 4}  fill="#f59e0b" fontSize={7} textAnchor="end">Live</text>
          <text x={LEFT_MARGIN - 4} y={azY2 + 4}  fill="#f59e0b" fontSize={7} textAnchor="end">{usd(azBuyLow!)}</text>
          {/* right: EUR high */}
          <text x={SVG_WIDTH - RIGHT_MARGIN} y={azY1 + 4} fill="#f59e0b" fontSize={7} textAnchor="end">{eur(azBuyHigh!)}</text>
        </>
      )}

      {/* AI entry zone band — blue */}
      {hasAiZone && (
        <>
          <rect x={LEFT_MARGIN} y={entryY1} width={CHART_WIDTH} height={entryY2 - entryY1}
            fill="#3b82f620" stroke="#3b82f640" strokeWidth={0.5} />
          {/* left: label + USD low */}
          <text x={LEFT_MARGIN - 4} y={entryY1 + 4}  fill="#3b82f6" fontSize={7} textAnchor="end">{aiZoneLabel}</text>
          <text x={LEFT_MARGIN - 4} y={entryY2 + 4}  fill="#3b82f6" fontSize={7} textAnchor="end">{usd(entryLow!)}</text>
          {/* right: EUR high */}
          <text x={SVG_WIDTH - RIGHT_MARGIN} y={entryY1 + 4} fill="#3b82f6" fontSize={7} textAnchor="end">{eur(entryHigh!)}</text>
        </>
      )}

      {/* Main price lines: left = label + USD, right = EUR */}
      {lines.map(l => {
        const yPos = y(l.price)
        return (
          <g key={l.label}>
            <line x1={LEFT_MARGIN} y1={yPos} x2={SVG_WIDTH - RIGHT_MARGIN} y2={yPos}
              stroke={l.color} strokeWidth={l.strokeW} strokeDasharray={l.dashed ? '4 3' : undefined} />
            {/* left: label */}
            <text x={LEFT_MARGIN - 4} y={yPos + 2}  fill={l.color} fontSize={8} textAnchor="end">{l.label}</text>
            {/* left: USD below label */}
            <text x={LEFT_MARGIN - 4} y={yPos + 11} fill={l.color} fontSize={7} textAnchor="end" opacity={0.7}>{usd(l.price)}</text>
            {/* right: EUR */}
            <text x={SVG_WIDTH - RIGHT_MARGIN} y={yPos - 2} fill={l.color} fontSize={8} textAnchor="end">{eur(l.price)}</text>
          </g>
        )
      })}

      {/* Reference ticks (POC / VWAP / MaxPain) — short span, show both inline */}
      {ticks.map(t => {
        const yPos = y(t.price)
        return (
          <g key={t.label}>
            <line x1={LEFT_MARGIN + CHART_WIDTH * 0.55} y1={yPos} x2={SVG_WIDTH - RIGHT_MARGIN} y2={yPos}
              stroke={t.color} strokeWidth={0.8} strokeDasharray="3 2" opacity={0.7} />
            {/* left of tick: label USD */}
            <text x={LEFT_MARGIN + CHART_WIDTH * 0.55 - 4} y={yPos + 3}
              fill={t.color} fontSize={7} textAnchor="end" opacity={0.8}>
              {t.label} {usd(t.price)}
            </text>
            {/* right: EUR */}
            <text x={SVG_WIDTH - RIGHT_MARGIN} y={yPos - 2}
              fill={t.color} fontSize={7} textAnchor="end" opacity={0.8}>
              {eur(t.price)}
            </text>
          </g>
        )
      })}

      {/* Current price line — bold white: left = "Price $USD", right = "€EUR" */}
      <line x1={LEFT_MARGIN} y1={currentY} x2={SVG_WIDTH - RIGHT_MARGIN} y2={currentY}
        stroke="#fafafa" strokeWidth={2} />
      <text x={LEFT_MARGIN - 4} y={currentY + 2}  fill="#fafafa" fontSize={8} fontWeight="bold" textAnchor="end">Price</text>
      <text x={LEFT_MARGIN - 4} y={currentY + 11} fill="#fafafa" fontSize={7} textAnchor="end" opacity={0.7}>{usd(currentPrice)}</text>
      <text x={SVG_WIDTH - RIGHT_MARGIN} y={currentY - 2} fill="#fafafa" fontSize={8} fontWeight="bold" textAnchor="end">{eur(currentPrice)}</text>

      {/* Axis */}
      <line x1={LEFT_MARGIN} y1={svgH * V_PAD_FRAC} x2={LEFT_MARGIN} y2={svgH * (1 - V_PAD_FRAC)}
        stroke="#27272a" strokeWidth={1} />
    </svg>
  )
}
