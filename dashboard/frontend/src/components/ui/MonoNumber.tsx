import { clsx } from 'clsx'

interface MonoNumberProps {
  value: number
  decimals?: number
  prefix?: string
  suffix?: string
  colorBySign?: boolean
  className?: string
}

export function MonoNumber({ value, decimals = 2, prefix, suffix, colorBySign, className }: MonoNumberProps) {
  const isPositive = value > 0
  const isNegative = value < 0

  const colorClass = colorBySign
    ? isPositive
      ? 'text-accent-green'
      : isNegative
        ? 'text-accent-red'
        : 'text-text-primary'
    : ''

  const sign = colorBySign && isPositive ? '+' : ''

  return (
    <span className={clsx('font-mono', colorClass, className)}>
      {prefix}{sign}{value.toFixed(decimals)}{suffix}
    </span>
  )
}
