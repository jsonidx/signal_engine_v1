import { Component, type ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'

interface Props {
  pageName?: string
  children: ReactNode
}

interface State {
  hasError: boolean
  error?: Error
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: undefined })
  }

  render() {
    if (this.state.hasError) {
      const { pageName = 'this page' } = this.props
      return (
        <div className="flex items-center justify-center h-full min-h-[300px]">
          <div className="bg-bg-surface border border-accent-red/30 rounded p-6 max-w-md w-full space-y-4">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-accent-red/10 flex items-center justify-center flex-shrink-0">
                <AlertTriangle size={16} className="text-accent-red" />
              </div>
              <div>
                <div className="font-mono text-sm font-semibold text-text-primary">
                  signal engine could not load {pageName}
                </div>
                <div className="font-mono text-xs text-text-tertiary mt-0.5">
                  a runtime error occurred
                </div>
              </div>
            </div>
            {this.state.error && (
              <div className="bg-bg-elevated rounded p-3 font-mono text-xs text-accent-red/80 break-all">
                {this.state.error.message}
              </div>
            )}
            <button
              onClick={this.handleRetry}
              className="w-full px-4 py-2 text-xs font-mono bg-bg-elevated border border-border-subtle hover:border-border-active text-text-secondary hover:text-text-primary rounded transition-colors"
            >
              Retry
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
