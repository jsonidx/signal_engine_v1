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
}

const SVG_WIDTH = 320
const LEFT_MARGIN = 70  // space for labels
const RIGHT_MARGIN = 10
const CHART_WIDTH = SVG_WIDTH - LEFT_MARGIN - RIGHT_MARGIN
const V_PAD_FRAC = 0.12  // padding fraction above/below

function toY(price: number, minP: number, maxP: number, svgH: number): number {
  const range = maxP - minP || 1
  // Invert: higher price → smaller Y
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
}: PriceLadderProps) {
  const svgH = height

  // Collect all defined prices for scale
  const allPrices = [
    currentPrice,
    target1,
    target2,
    entryLow,
    entryHigh,
    stopLoss,
    poc,
    vwap,
    maxPain,
  ].filter((p): p is number => p !== undefined && p > 0)

  if (allPrices.length === 0) return null

  const minP = Math.min(...allPrices)
  const maxP = Math.max(...allPrices)

  const y = (p: number) => toY(p, minP, maxP, svgH)

  const fmt = (p: number) => `$${p.toFixed(2)}`

  interface LineSpec {
    price: number
    label: string
    color: string
    strokeW: number
    dashed: boolean
    textColor: string
  }

  const lines: LineSpec[] = []

  if (target2 !== undefined)
    lines.push({ price: target2, label: 'Target 2', color: '#22c55e', strokeW: 1, dashed: true, textColor: '#22c55e' })
  if (target1 !== undefined)
    lines.push({ price: target1, label: 'Target 1', color: '#22c55e', strokeW: 1.5, dashed: false, textColor: '#22c55e' })
  if (stopLoss !== undefined)
    lines.push({ price: stopLoss, label: 'Stop', color: '#ef4444', strokeW: 1, dashed: true, textColor: '#ef4444' })

  // Reference lines (ticks)
  const ticks: LineSpec[] = []
  if (poc !== undefined)
    ticks.push({ price: poc, label: 'POC', color: '#a855f7', strokeW: 1, dashed: true, textColor: '#a855f7' })
  if (vwap !== undefined)
    ticks.push({ price: vwap, label: 'VWAP', color: '#3b82f6', strokeW: 1, dashed: true, textColor: '#3b82f6' })
  if (maxPain !== undefined)
    ticks.push({ price: maxPain, label: 'MaxPain', color: '#f59e0b', strokeW: 1, dashed: true, textColor: '#f59e0b' })

  const currentY = y(currentPrice)
  const hasEntryZone = entryLow !== undefined && entryHigh !== undefined
  const entryY1 = entryHigh !== undefined ? y(entryHigh) : 0
  const entryY2 = entryLow !== undefined ? y(entryLow) : 0

  return (
    <svg width={SVG_WIDTH} height={svgH} style={{ fontFamily: 'IBM Plex Mono, monospace' }}>
      {/* Entry zone shaded band */}
      {hasEntryZone && (
        <rect
          x={LEFT_MARGIN}
          y={entryY1}
          width={CHART_WIDTH}
          height={entryY2 - entryY1}
          fill="#3b82f620"
          stroke="#3b82f640"
          strokeWidth={0.5}
        />
      )}
      {hasEntryZone && (
        <>
          <text x={LEFT_MARGIN - 4} y={entryY1 + 4} fill="#3b82f6" fontSize={8} textAnchor="end">
            Entry
          </text>
          <text x={LEFT_MARGIN - 4} y={entryY2 + 4} fill="#3b82f6" fontSize={8} textAnchor="end">
            {fmt(entryLow!)}
          </text>
          <text x={SVG_WIDTH - RIGHT_MARGIN} y={entryY1 + 4} fill="#3b82f6" fontSize={8} textAnchor="end">
            {fmt(entryHigh!)}
          </text>
        </>
      )}

      {/* Horizontal price lines */}
      {lines.map(l => {
        const yPos = y(l.price)
        return (
          <g key={l.label}>
            <line
              x1={LEFT_MARGIN}
              y1={yPos}
              x2={SVG_WIDTH - RIGHT_MARGIN}
              y2={yPos}
              stroke={l.color}
              strokeWidth={l.strokeW}
              strokeDasharray={l.dashed ? '4 3' : undefined}
            />
            <text x={LEFT_MARGIN - 4} y={yPos + 4} fill={l.textColor} fontSize={9} textAnchor="end">
              {l.label}
            </text>
            <text x={SVG_WIDTH - RIGHT_MARGIN} y={yPos - 3} fill={l.textColor} fontSize={9} textAnchor="end">
              {fmt(l.price)}
            </text>
          </g>
        )
      })}

      {/* Reference ticks */}
      {ticks.map(t => {
        const yPos = y(t.price)
        return (
          <g key={t.label}>
            <line
              x1={LEFT_MARGIN + CHART_WIDTH * 0.6}
              y1={yPos}
              x2={SVG_WIDTH - RIGHT_MARGIN}
              y2={yPos}
              stroke={t.color}
              strokeWidth={0.8}
              strokeDasharray="3 2"
              opacity={0.7}
            />
            <text
              x={LEFT_MARGIN + CHART_WIDTH * 0.6 - 4}
              y={yPos + 3}
              fill={t.textColor}
              fontSize={8}
              textAnchor="end"
              opacity={0.8}
            >
              {t.label} {fmt(t.price)}
            </text>
          </g>
        )
      })}

      {/* Current price — bold white line */}
      <line
        x1={LEFT_MARGIN}
        y1={currentY}
        x2={SVG_WIDTH - RIGHT_MARGIN}
        y2={currentY}
        stroke="#fafafa"
        strokeWidth={2}
      />
      <text x={LEFT_MARGIN - 4} y={currentY + 4} fill="#fafafa" fontSize={9} fontWeight="bold" textAnchor="end">
        Price
      </text>
      <text x={SVG_WIDTH - RIGHT_MARGIN} y={currentY - 4} fill="#fafafa" fontSize={9} fontWeight="bold" textAnchor="end">
        {fmt(currentPrice)}
      </text>

      {/* Price axis (right edge) */}
      <line
        x1={LEFT_MARGIN}
        y1={svgH * V_PAD_FRAC}
        x2={LEFT_MARGIN}
        y2={svgH * (1 - V_PAD_FRAC)}
        stroke="#27272a"
        strokeWidth={1}
      />
    </svg>
  )
}
