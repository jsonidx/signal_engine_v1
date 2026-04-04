/**
 * RiskRewardBar — horizontal payoff diagram rendered inside TradeSetupStrip.
 *
 * Layout (left → right, sorted by price):
 *   Stop ─────────── Current ──── Entry ──── T1 ──── T2
 *
 * Red fill: stop→current (risk region)
 * Green fill: entry→T1 / T1→T2 (reward regions, fading)
 * Current price dot floats over the bar.
 */

import { clsx } from 'clsx'

interface RiskRewardBarProps {
  entry: number          // midpoint of entry zone
  target1?: number | null
  target2?: number | null
  stopLoss?: number | null
  currentPrice?: number | null
  // Bull % and Bear % (0-100) from AI thesis — used for EV display
  bullPct?: number | null
  bearPct?: number | null
  neutralPct?: number | null
}

export function RiskRewardBar({
  entry,
  target1,
  target2,
  stopLoss,
  currentPrice,
  bullPct,
  bearPct,
  neutralPct,
}: RiskRewardBarProps) {
  // Need at least one target or stop to render
  if (!target1 && !stopLoss) return null

  // ── Compute EV in R-multiples ─────────────────────────────────────────────
  // EV = (bull% × T2R) + (neutral% × T1R/2) + (bear% × -1R)
  // Uses T2 as bull payoff, T1 as neutral payoff, stop as bear (-1R by definition)
  let evR: number | null = null
  if (stopLoss != null && Math.abs(entry - stopLoss) > 0) {
    const riskR = Math.abs(entry - stopLoss)
    const t1R   = target1  != null ? (target1  - entry) / riskR : null
    const t2R   = target2  != null ? (target2  - entry) / riskR : (t1R ?? 0)

    const bPct  = (bullPct    ?? 33) / 100
    const nPct  = (neutralPct ?? 34) / 100
    const brPct = (bearPct    ?? 33) / 100

    // neutral outcome = half of T1 (partial move)
    const neutralPayoff = t1R != null ? t1R * 0.5 : 0
    evR = bPct * t2R + nPct * neutralPayoff + brPct * (-1)
    evR = Math.round(evR * 10) / 10
  }

  // ── Layout math ───────────────────────────────────────────────────────────
  const allPrices = [entry, target1, target2, stopLoss, currentPrice].filter(
    (p): p is number => p != null && p > 0
  )
  if (allPrices.length < 2) return null

  const lo = Math.min(...allPrices)
  const hi = Math.max(...allPrices)
  const range = hi - lo || 1

  // Map price → % position on bar
  const pos = (p: number) =>
    Math.max(0, Math.min(100, ((p - lo) / range) * 100))

  const entryPos  = pos(entry)
  const t1Pos     = target1  != null ? pos(target1)  : null
  const t2Pos     = target2  != null ? pos(target2)  : null
  const slPos     = stopLoss != null ? pos(stopLoss) : null
  const curPos    = currentPrice != null ? pos(currentPrice) : null

  // Fill widths: stop→entry = risk zone (red), entry→T1 = first reward (green/40),
  // T1→T2 = extended reward (green/20)
  const riskLeft  = slPos  ?? entryPos
  const riskWidth = entryPos - (slPos ?? entryPos)
  const r1Width   = t1Pos  != null ? t1Pos - entryPos : 0
  const r2Width   = t1Pos  != null && t2Pos != null ? t2Pos - t1Pos : 0

  const fmtPct = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`
  const evColor = evR == null ? '' : evR >= 1 ? 'text-accent-green' : evR >= 0 ? 'text-accent-amber' : 'text-accent-red'

  return (
    <div className="mt-3 space-y-1.5">
      {/* ── EV badge row ─────────────────────────────────────────────────── */}
      {evR != null && (
        <div className="flex items-center gap-2">
          <span className="font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
            Expected Value
          </span>
          <span className={clsx('font-mono text-xs font-semibold', evColor)}>
            {evR >= 0 ? '+' : ''}{evR.toFixed(1)}R
          </span>
          <span className="font-mono text-[9px] text-text-tertiary">
            {bullPct != null && `(${bullPct}% bull · ${neutralPct ?? 0}% ntrl · ${bearPct}% bear)`}
          </span>
        </div>
      )}

      {/* ── Horizontal bar ───────────────────────────────────────────────── */}
      <div className="relative h-5">
        {/* Base track */}
        <div className="absolute inset-y-[7px] left-0 right-0 bg-bg-elevated rounded" />

        {/* Risk fill: stop → entry */}
        {riskWidth > 0 && (
          <div
            className="absolute inset-y-[7px] bg-accent-red/25 rounded-l"
            style={{ left: `${riskLeft}%`, width: `${riskWidth}%` }}
          />
        )}

        {/* Reward fill: entry → T1 */}
        {r1Width > 0 && (
          <div
            className="absolute inset-y-[7px] bg-accent-green/30"
            style={{ left: `${entryPos}%`, width: `${r1Width}%` }}
          />
        )}

        {/* Extended reward fill: T1 → T2 */}
        {r2Width > 0 && t1Pos != null && (
          <div
            className="absolute inset-y-[7px] bg-accent-green/15 rounded-r"
            style={{ left: `${t1Pos}%`, width: `${r2Width}%` }}
          />
        )}

        {/* Stop tick */}
        {slPos != null && (
          <div className="absolute top-0 bottom-0 flex items-center" style={{ left: `${slPos}%` }}>
            <div className="w-0.5 h-4 bg-accent-red rounded" />
          </div>
        )}

        {/* Entry tick */}
        <div className="absolute top-0 bottom-0 flex items-center" style={{ left: `${entryPos}%` }}>
          <div className="w-0.5 h-4 bg-text-secondary rounded" />
        </div>

        {/* T1 tick */}
        {t1Pos != null && (
          <div className="absolute top-0 bottom-0 flex items-center" style={{ left: `${t1Pos}%` }}>
            <div className="w-0.5 h-4 bg-accent-green/70 rounded" />
          </div>
        )}

        {/* T2 tick */}
        {t2Pos != null && (
          <div className="absolute top-0 bottom-0 flex items-center" style={{ left: `${t2Pos}%` }}>
            <div className="w-0.5 h-4 bg-accent-green rounded" />
          </div>
        )}

        {/* Current price dot */}
        {curPos != null && (
          <div
            className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full bg-text-primary border-2 border-bg-surface shadow z-10"
            style={{ left: `${curPos}%`, transform: 'translate(-50%, -50%)' }}
          />
        )}
      </div>

      {/* ── Price labels below bar ────────────────────────────────────────── */}
      <div className="relative h-3.5">
        {slPos != null && stopLoss != null && (
          <span
            className="absolute font-mono text-[8px] text-accent-red -translate-x-1/2"
            style={{ left: `${slPos}%` }}
          >
            SL
          </span>
        )}
        <span
          className="absolute font-mono text-[8px] text-text-tertiary -translate-x-1/2"
          style={{ left: `${entryPos}%` }}
        >
          entry
        </span>
        {t1Pos != null && target1 != null && (
          <span
            className="absolute font-mono text-[8px] text-accent-green/80 -translate-x-1/2"
            style={{ left: `${t1Pos}%` }}
          >
            T1
          </span>
        )}
        {t2Pos != null && target2 != null && (
          <span
            className="absolute font-mono text-[8px] text-accent-green -translate-x-1/2"
            style={{ left: `${t2Pos}%` }}
          >
            T2
          </span>
        )}
      </div>

      {/* ── Pct change labels ─────────────────────────────────────────────── */}
      {target1 != null && stopLoss != null && (
        <div className="flex items-center gap-3 font-mono text-[9px] text-text-tertiary">
          <span className="text-accent-red">
            SL {fmtPct(((stopLoss - entry) / entry) * 100)}
          </span>
          <span className="text-accent-green/80">
            T1 {fmtPct(((target1 - entry) / entry) * 100)}
          </span>
          {target2 != null && (
            <span className="text-accent-green">
              T2 {fmtPct(((target2 - entry) / entry) * 100)}
            </span>
          )}
          {stopLoss != null && target1 != null && Math.abs(entry - stopLoss) > 0 && (
            <span className="text-text-tertiary ml-auto">
              R:R {(Math.abs(target1 - entry) / Math.abs(entry - stopLoss)).toFixed(1)}:1
            </span>
          )}
        </div>
      )}
    </div>
  )
}
