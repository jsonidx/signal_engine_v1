import { clsx } from 'clsx'

interface DirectionBadgeProps {
  direction: 'BULL' | 'BEAR' | 'NEUTRAL' | string
  size?: 'sm' | 'md'
}

export function DirectionBadge({ direction, size = 'md' }: DirectionBadgeProps) {
  const colorClass =
    direction === 'BULL'
      ? 'bg-accent-green/20 text-accent-green border-accent-green/30'
      : direction === 'BEAR'
        ? 'bg-accent-red/20 text-accent-red border-accent-red/30'
        : 'bg-text-tertiary/20 text-text-secondary border-text-tertiary/30'

  const sizeClass = size === 'sm' ? 'text-[10px] px-1.5 py-0.5' : 'text-xs px-2 py-1'

  return (
    <span
      className={clsx(
        'font-mono font-medium rounded border uppercase tracking-wide',
        colorClass,
        sizeClass
      )}
    >
      {direction}
    </span>
  )
}
