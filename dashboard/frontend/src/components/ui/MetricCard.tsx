import { clsx } from 'clsx'

interface MetricCardProps {
  label: string
  value: string | number
  unit?: string
  delta?: number
  deltaLabel?: string
  colorBySign?: boolean
  sentiment?: 'positive' | 'negative' | 'neutral'
}

export function MetricCard({ label, value, unit, delta, deltaLabel, colorBySign, sentiment }: MetricCardProps) {
  const numericValue = typeof value === 'number' ? value : parseFloat(String(value))
  const isPositive = numericValue > 0
  const isNegative = numericValue < 0

  const valueColor = colorBySign
    ? isPositive
      ? 'text-accent-green'
      : isNegative
        ? 'text-accent-red'
        : 'text-text-primary'
    : 'text-text-primary'

  const borderColor =
    sentiment === 'positive'
      ? 'border-accent-green'
      : sentiment === 'negative'
        ? 'border-accent-red'
        : 'border-border-subtle'

  return (
    <div
      className={clsx(
        'bg-bg-surface rounded border border-border-subtle border-b-2 p-4 flex flex-col gap-2',
        borderColor
      )}
    >
      <span className="font-mono text-[11px] uppercase tracking-widest text-text-tertiary">
        {label}
      </span>
      <div className="flex items-baseline gap-1">
        <span className={clsx('font-mono text-[28px] font-semibold leading-none', valueColor)}>
          {typeof value === 'number' ? value.toFixed(2) : value}
        </span>
        {unit && (
          <span className="font-mono text-sm text-text-secondary">{unit}</span>
        )}
      </div>
      {delta !== undefined && (
        <span
          className={clsx(
            'font-mono text-xs',
            delta > 0 ? 'text-accent-green' : delta < 0 ? 'text-accent-red' : 'text-text-tertiary'
          )}
        >
          {delta > 0 ? '+' : ''}{delta.toFixed(2)} {deltaLabel}
        </span>
      )}
    </div>
  )
}
