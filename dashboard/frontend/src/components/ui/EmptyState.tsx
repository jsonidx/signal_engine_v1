import { Terminal } from 'lucide-react'

interface EmptyStateProps {
  message?: string
  command?: string
}

export function EmptyState({ message = 'No data available', command }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4">
      <div className="w-12 h-12 rounded-full bg-bg-elevated flex items-center justify-center">
        <Terminal size={20} className="text-text-tertiary" />
      </div>
      <div className="text-center space-y-1">
        <p className="font-mono text-sm text-text-tertiary">{message}</p>
        {command && (
          <p className="font-mono text-xs text-text-tertiary/60">
            Run{' '}
            <code className="px-1.5 py-0.5 bg-bg-elevated rounded text-accent-amber">
              {command}
            </code>{' '}
            to generate data
          </p>
        )}
      </div>
    </div>
  )
}
