import { clsx } from 'clsx'

interface LoadingSkeletonProps {
  className?: string
  rows?: number
}

export function LoadingSkeleton({ className, rows = 1 }: LoadingSkeletonProps) {
  return (
    <div className={clsx('space-y-2', className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="shimmer h-8 rounded" />
      ))}
    </div>
  )
}

export function SkeletonCard() {
  return (
    <div className="bg-bg-surface rounded border border-border-subtle p-4 space-y-3">
      <div className="shimmer h-3 w-24 rounded" />
      <div className="shimmer h-8 w-32 rounded" />
      <div className="shimmer h-3 w-16 rounded" />
    </div>
  )
}
