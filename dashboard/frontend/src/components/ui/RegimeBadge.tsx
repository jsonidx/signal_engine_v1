import { clsx } from 'clsx'

interface RegimeBadgeProps {
  regime: 'RISK_ON' | 'TRANSITIONAL' | 'RISK_OFF' | string
  score?: number
  size?: 'sm' | 'md'
}

export function RegimeBadge({ regime, score, size = 'md' }: RegimeBadgeProps) {
  const colorClass =
    regime === 'RISK_ON'
      ? 'bg-accent-green/20 text-accent-green border-accent-green/40'
      : regime === 'RISK_OFF'
        ? 'bg-accent-red/20 text-accent-red border-accent-red/40'
        : 'bg-accent-amber/20 text-accent-amber border-accent-amber/40'

  const sizeClass = size === 'sm' ? 'text-[10px] px-1.5 py-0.5' : 'text-xs px-2.5 py-1'

  const label = regime?.replace('_', ' ') ?? '—'

  return (
    <span
      className={clsx(
        'font-mono font-medium rounded-full border uppercase tracking-wide inline-flex items-center gap-1',
        colorClass,
        sizeClass
      )}
    >
      <span
        className={clsx(
          'w-1.5 h-1.5 rounded-full',
          regime === 'RISK_ON'
            ? 'bg-accent-green'
            : regime === 'RISK_OFF'
              ? 'bg-accent-red'
              : 'bg-accent-amber'
        )}
      />
      {label}
      {score !== undefined && (
        <span className="opacity-70">
          ({score > 0 ? '+' : ''}{score})
        </span>
      )}
    </span>
  )
}
