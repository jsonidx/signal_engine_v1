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
  const num = value ?? 0
  const isPositive = num > 0
  const isNegative = num < 0

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
      {prefix}{sign}{num.toFixed(decimals)}{suffix}
    </span>
  )
}
