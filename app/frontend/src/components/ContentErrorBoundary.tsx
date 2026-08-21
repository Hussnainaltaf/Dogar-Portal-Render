import { Component, type ErrorInfo, type ReactNode } from 'react'

// ---------------------------------------------------------------------------
// FIX 6 (part 2 of 2) — Route-scoped React error boundary.
//
// Bug: A single component crash inside <Outlet /> propagated up to the root
// and blanked the ENTIRE SPA — sidebar, topbar, chatbot all unmounted. The
// user was left with a white screen and had to hard-refresh the browser.
//
// This class component wraps the routed content area ONLY. When a descendant
// throws during render, it catches the error, logs it to the console (so it
// still shows up in dev tools + any production error-reporting hook), and
// renders a recovery card. The surrounding shell (Sidebar, Topbar, Footer,
// Chatbot) stays mounted and interactive — the user can navigate to another
// page via the sidebar to escape the broken screen, or click "Try again" to
// re-mount the crashed subtree in place.
//
// Class component is required: React error boundaries are still class-only
// as of React 19 (no hook equivalent to componentDidCatch exists).
// ---------------------------------------------------------------------------

interface Props {
  children: ReactNode
  /** Optional key that, when it changes, resets the error state. In practice
   *  we pass `pathname` — so a user navigating to a different route via the
   *  sidebar automatically clears the caught error, without them having to
   *  click "Try again". */
  resetKey?: string
}

interface State {
  error: Error | null
}

export default class ContentErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Log to console so the stack shows up in DevTools; if a production
    // error-tracking service (Sentry, LogRocket, etc.) is later wired in,
    // this is the place to forward it.
    // eslint-disable-next-line no-console
    console.error('[ContentErrorBoundary] caught render error:', error, info.componentStack)
  }

  componentDidUpdate(prevProps: Props): void {
    // Auto-reset when the caller-supplied resetKey changes (typically the
    // current pathname) — so switching pages clears a caught error even
    // without an explicit "Try again" click.
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  reset = () => this.setState({ error: null })

  render() {
    if (!this.state.error) return this.props.children

    const msg = this.state.error.message || String(this.state.error)

    return (
      <div className="flex items-center justify-center py-16" role="alert" aria-live="assertive">
        <div className="max-w-lg w-full bg-white border border-rose-200 rounded-xl shadow-sm p-6 text-center">
          <div className="w-14 h-14 mx-auto rounded-full bg-rose-50 text-rose-500 flex items-center justify-center mb-3">
            <i className="fa-solid fa-triangle-exclamation text-2xl" aria-hidden="true" />
          </div>
          <h2 className="text-lg font-bold text-slate-800 mb-1">Something went wrong on this page</h2>
          <p className="text-sm text-slate-500 mb-4">
            The rest of the app is still working — you can navigate elsewhere from the sidebar, or try again below.
          </p>
          <details className="text-left text-[12px] text-slate-500 bg-slate-50 border border-slate-200 rounded-lg p-3 mb-4">
            <summary className="cursor-pointer font-semibold text-slate-600">Technical details</summary>
            <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-[11px] leading-snug">{msg}</pre>
          </details>
          <div className="flex items-center justify-center gap-2">
            <button
              onClick={this.reset}
              className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold px-4 py-2 rounded-lg transition"
            >
              <i className="fa-solid fa-rotate-right" aria-hidden="true" /> Try again
            </button>
            <button
              onClick={() => window.location.reload()}
              className="inline-flex items-center gap-2 border border-slate-200 text-slate-700 hover:bg-slate-50 text-sm font-semibold px-4 py-2 rounded-lg transition"
            >
              <i className="fa-solid fa-arrows-rotate" aria-hidden="true" /> Reload page
            </button>
          </div>
        </div>
      </div>
    )
  }
}
