interface PriceLadderProps {
  currentPrice: number
  // AI thesis levels (USD) — labeled on LEFT
  target1?: number
  target2?: number
  entryLow?: number
  entryHigh?: number
  stopLoss?: number
  // Market structure — center ticks
  poc?: number
  vwap?: number
  maxPain?: number
  // Live action-zone levels (USD) — labeled on RIGHT
  azBuyLow?: number
  azBuyHigh?: number
  azStop?: number
  azTarget1?: number
  azTarget2?: number
  // EUR conversion for right-side labels
  fxRate?: number
}

const SVG_WIDTH    = 490
const LEFT_MARGIN  = 112
const RIGHT_MARGIN = 112
const CHART_W      = SVG_WIDTH - LEFT_MARGIN - RIGHT_MARGIN
const V_PAD        = 0.06
const MIN_GAP      = 22
const MIN_H_PER_LABEL = 25

function toY(price: number, minP: number, maxP: number, h: number): number {
  const range = maxP - minP || 1
  return h - V_PAD * h - ((price - minP) / range) * (h * (1 - 2 * V_PAD))
}

/** Push labels apart so none are closer than minGap px.
 *  Returns a map id → adjusted labelY. Lines are drawn at the real Y. */
function resolveOverlaps(
  items: { id: string; y: number }[],
  minGap: number,
): Record<string, number> {
  const sorted = [...items].sort((a, b) => a.y - b.y)
  const adj: Record<string, number> = {}
  let lastY = -Infinity
  for (const item of sorted) {
    const y = Math.max(item.y, lastY + minGap)
    adj[item.id] = y
    lastY = y
  }
  return adj
}

export function PriceLadder({
  currentPrice,
  target1, target2, entryLow, entryHigh, stopLoss,
  poc, vwap, maxPain,
  azBuyLow, azBuyHigh, azStop, azTarget1, azTarget2,
  fxRate,
}: PriceLadderProps) {
  // Count distinct label rows to determine minimum height
  const leftLabelCount  = [target1, target2, stopLoss, currentPrice].filter(p => p != null).length
  const rightLabelCount = [azTarget1, azTarget2, azStop, currentPrice].filter(p => p != null).length
  const maxLabels = Math.max(leftLabelCount, rightLabelCount)
  const svgH = Math.max(235, maxLabels * MIN_H_PER_LABEL + 60)

  const allPrices = [
    currentPrice, target1, target2, entryLow, entryHigh, stopLoss,
    poc, vwap, maxPain, azBuyLow, azBuyHigh, azStop, azTarget1, azTarget2,
  ].filter((p): p is number => p !== undefined && p > 0)

  if (allPrices.length === 0) return null

  const minP = Math.min(...allPrices)
  const maxP = Math.max(...allPrices)
  const y    = (p: number) => toY(p, minP, maxP, svgH)

  const fmtUsd = (p: number) => `$${p.toFixed(2)}`
  const fmtEur = (p: number) => fxRate ? `€${(p / fxRate).toFixed(2)}` : `$${p.toFixed(2)}`

  const x0 = LEFT_MARGIN
  const x1 = SVG_WIDTH - RIGHT_MARGIN

  // ── Collect left labels (AI) ───────────────────────────────────────────────
  interface LabelSpec { id: string; lineY: number; line1: string; line2: string; color: string; strokeW: number; dashed: boolean }

  const leftItems: LabelSpec[] = []
  if (target2  != null) leftItems.push({ id: 'ai-t2',   lineY: y(target2),  line1: 'AI T2',   line2: fmtUsd(target2),  color: '#22c55e', strokeW: 1,   dashed: true  })
  if (target1  != null) leftItems.push({ id: 'ai-t1',   lineY: y(target1),  line1: 'AI T1',   line2: fmtUsd(target1),  color: '#22c55e', strokeW: 1.5, dashed: false })
  if (stopLoss != null) leftItems.push({ id: 'ai-stop', lineY: y(stopLoss), line1: 'AI Stop', line2: fmtUsd(stopLoss), color: '#ef4444', strokeW: 1,   dashed: true  })
  // current price also needs a left label
  leftItems.push({ id: 'price', lineY: y(currentPrice), line1: 'Price', line2: fmtUsd(currentPrice), color: '#fafafa', strokeW: 2, dashed: false })

  // ── Collect right labels (Live AZ) ─────────────────────────────────────────
  const rightItems: LabelSpec[] = []
  if (azTarget2 != null) rightItems.push({ id: 'az-t2',   lineY: y(azTarget2), line1: 'Live T2', line2: fmtEur(azTarget2), color: '#86efac', strokeW: 1,   dashed: true  })
  if (azTarget1 != null) rightItems.push({ id: 'az-t1',   lineY: y(azTarget1), line1: 'Live T1', line2: fmtEur(azTarget1), color: '#86efac', strokeW: 1.5, dashed: false })
  if (azStop    != null) rightItems.push({ id: 'az-stop', lineY: y(azStop),    line1: 'Live SL', line2: fmtEur(azStop),    color: '#f97316', strokeW: 1,   dashed: true  })
  // current price right label (EUR)
  rightItems.push({ id: 'price-r', lineY: y(currentPrice), line1: fmtEur(currentPrice), line2: '', color: '#fafafa', strokeW: 2, dashed: false })

  // Run overlap resolution
  const leftAdj  = resolveOverlaps(leftItems.map(i => ({ id: i.id, y: i.lineY })),  MIN_GAP)
  const rightAdj = resolveOverlaps(rightItems.map(i => ({ id: i.id, y: i.lineY })), MIN_GAP)

  // Zone geometry
  const hasAiZone = entryLow  != null && entryHigh  != null
  const hasAzZone = azBuyLow  != null && azBuyHigh  != null
  const aiZoneLabel = hasAzZone && hasAiZone ? 'AI Entry' : 'Entry'
  const aiY1  = entryHigh != null ? y(entryHigh) : 0
  const aiY2  = entryLow  != null ? y(entryLow)  : 0
  const azY1  = azBuyHigh != null ? y(azBuyHigh) : 0
  const azY2  = azBuyLow  != null ? y(azBuyLow)  : 0

  return (
    <svg viewBox={`0 0 ${SVG_WIDTH} ${svgH}`} width="100%" style={{ fontFamily: 'IBM Plex Mono, monospace', display: 'block' }}>

      {/* Column headers */}
      <text x={x0 - 4} y={10} fill="#52525b" fontSize={9} textAnchor="end"   fontWeight="bold">AI (USD)</text>
      <text x={x1 + 4} y={10} fill="#52525b" fontSize={9} textAnchor="start" fontWeight="bold">LIVE (EUR)</text>

      {/* Axis rails */}
      <line x1={x0} y1={svgH * V_PAD} x2={x0} y2={svgH * (1 - V_PAD)} stroke="#27272a" strokeWidth={1} />
      <line x1={x1} y1={svgH * V_PAD} x2={x1} y2={svgH * (1 - V_PAD)} stroke="#27272a" strokeWidth={1} />

      {/* Live buy zone band — amber */}
      {hasAzZone && (
        <>
          <rect x={x0} y={azY1} width={CHART_W} height={azY2 - azY1}
            fill="#f59e0b18" stroke="#f59e0b40" strokeWidth={0.5} />
          {/* Right zone labels — run through resolveOverlaps via rightItems if needed */}
          {/* Simple: use midpoint label on right */}
          <text x={x1 + 4} y={(azY1 + azY2) / 2 - 4}  fill="#f59e0b" fontSize={9} textAnchor="start">Live Zone</text>
          <text x={x1 + 4} y={(azY1 + azY2) / 2 + 6}  fill="#f59e0b" fontSize={8} textAnchor="start" opacity={0.8}>{fmtEur(azBuyHigh!)}–{fmtEur(azBuyLow!)}</text>
        </>
      )}

      {/* AI entry zone band — blue */}
      {hasAiZone && (
        <>
          <rect x={x0} y={aiY1} width={CHART_W} height={aiY2 - aiY1}
            fill="#3b82f620" stroke="#3b82f640" strokeWidth={0.5} />
          {/* Left zone labels — midpoint */}
          <text x={x0 - 4} y={(aiY1 + aiY2) / 2 - 4}  fill="#3b82f6" fontSize={9} textAnchor="end">{aiZoneLabel}</text>
          <text x={x0 - 4} y={(aiY1 + aiY2) / 2 + 6}  fill="#3b82f6" fontSize={8} textAnchor="end" opacity={0.8}>{fmtUsd(entryLow!)}–{fmtUsd(entryHigh!)}</text>
        </>
      )}

      {/* All price lines — drawn at actual Y */}
      {[...leftItems.filter(i => i.id !== 'price'), ...rightItems.filter(i => i.id !== 'price-r')].map(l => (
        <line key={`line-${l.id}`}
          x1={x0} y1={l.lineY} x2={x1} y2={l.lineY}
          stroke={l.color} strokeWidth={l.strokeW}
          strokeDasharray={l.dashed ? '4 3' : undefined} />
      ))}

      {/* Current price line */}
      <line x1={x0} y1={y(currentPrice)} x2={x1} y2={y(currentPrice)} stroke="#fafafa" strokeWidth={2} />

      {/* Left labels at adjusted Y — with tick connector when offset */}
      {leftItems.map(l => {
        const ly = leftAdj[l.id]
        const offset = Math.abs(ly - l.lineY)
        return (
          <g key={`left-${l.id}`}>
            {offset > 3 && (
              <line x1={x0 - 2} y1={l.lineY} x2={x0 - 2} y2={ly}
                stroke={l.color} strokeWidth={0.5} opacity={0.4} />
            )}
            <text x={x0 - 4} y={ly}      fill={l.color} fontSize={10} textAnchor="end" fontWeight={l.id === 'price' ? 'bold' : 'normal'}>{l.line1}</text>
            {l.line2 && <text x={x0 - 4} y={ly + 10} fill={l.color} fontSize={9}  textAnchor="end" opacity={0.7}>{l.line2}</text>}
          </g>
        )
      })}

      {/* Right labels at adjusted Y — with tick connector when offset */}
      {rightItems.map(l => {
        const ry = rightAdj[l.id]
        const offset = Math.abs(ry - l.lineY)
        return (
          <g key={`right-${l.id}`}>
            {offset > 3 && (
              <line x1={x1 + 2} y1={l.lineY} x2={x1 + 2} y2={ry}
                stroke={l.color} strokeWidth={0.5} opacity={0.4} />
            )}
            <text x={x1 + 4} y={ry}      fill={l.color} fontSize={10} textAnchor="start" fontWeight={l.id === 'price-r' ? 'bold' : 'normal'}>{l.line1}</text>
            {l.line2 && <text x={x1 + 4} y={ry + 10} fill={l.color} fontSize={9}  textAnchor="start" opacity={0.7}>{l.line2}</text>}
          </g>
        )
      })}

      {/* Market structure ticks — center, short span */}
      {[
        poc     != null && { price: poc,     label: 'POC',     color: '#a855f7' },
        vwap    != null && { price: vwap,    label: 'VWAP',    color: '#3b82f6' },
        maxPain != null && { price: maxPain, label: 'MaxPain', color: '#f59e0b' },
      ].filter(Boolean).map((t: any) => {
        const yp = y(t.price)
        const cx = x0 + CHART_W * 0.5
        return (
          <g key={t.label}>
            <line x1={cx - 20} y1={yp} x2={cx + 20} y2={yp}
              stroke={t.color} strokeWidth={1} strokeDasharray="4 3" opacity={0.7} />
            <text x={cx} y={yp - 3} fill={t.color} fontSize={8} textAnchor="middle" opacity={0.8}>
              {t.label} {fmtUsd(t.price)}
            </text>
          </g>
        )
      })}

    </svg>
  )
}
