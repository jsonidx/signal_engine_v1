import { clsx } from 'clsx'

interface ConvictionDotsProps {
  conviction: number
  max?: number
}

export function ConvictionDots({ conviction, max = 5 }: ConvictionDotsProps) {
  return (
    <div className="flex items-center gap-1">
      {Array.from({ length: max }).map((_, i) => (
        <div
          key={i}
          className={clsx(
            'w-2 h-2 rounded-full',
            i < conviction ? 'bg-accent-purple' : 'bg-text-tertiary/30'
          )}
        />
      ))}
    </div>
  )
}
