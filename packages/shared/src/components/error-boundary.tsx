import { Component, type ErrorInfo, type ReactNode } from 'react'
import * as Sentry from '@sentry/react'

interface Props {
  children: ReactNode
  /** "page": one page failed, the sidebar keeps working. "app": the whole app failed. */
  scope?: 'page' | 'app'
}

interface State {
  error: Error | null
}

/**
 * Catches a crash while rendering (audit FRONT-01), so a receptionist sees a way out instead of a white
 * screen. Reported to Sentry (when configured, already PII-redacted). Give it `key={location.pathname}`
 * around the router outlet so moving to another page clears it.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    Sentry.captureException(error, { extra: { componentStack: info.componentStack } })
  }

  render() {
    if (!this.state.error) return this.props.children
    const page = this.props.scope === 'page'
    return (
      <div role="alert" className={page ? 'mx-auto max-w-lg py-16 text-center' : 'flex min-h-screen items-center justify-center p-6 text-center'}>
        <div className="space-y-3">
          <h1 className="text-lg font-semibold">{page ? "This page couldn't load" : 'Something went wrong'}</h1>
          <p className="text-sm text-muted-foreground">
            {page ? 'The rest of the dashboard still works. Try again, or open another page.' : 'Reload the page to carry on. Your data is safe.'}
          </p>
          <div className="flex justify-center gap-2">
            {page && (
              <button type="button" className="rounded-md border px-3 py-1.5 text-sm" onClick={() => this.setState({ error: null })}>
                Try again
              </button>
            )}
            <button type="button" className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
        </div>
      </div>
    )
  }
}
