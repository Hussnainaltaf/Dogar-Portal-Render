import { createContext, useContext, useState, useCallback, useEffect, useMemo, useRef, ReactNode } from 'react'

type ToastType = 'success' | 'error' | 'warning' | 'info'
interface ToastItem { id: number; type: ToastType; message: string }

// Style map ported verbatim from base.html showToast().
const STYLE: Record<ToastType, { bg: string; bd: string; ic: string; cl: string }> = {
  success: { bg: '#ecfdf5', bd: '#10b981', ic: 'fa-circle-check', cl: '#047857' },
  error:   { bg: '#fef2f2', bd: '#ef4444', ic: 'fa-circle-xmark', cl: '#b91c1c' },
  warning: { bg: '#fffbeb', bd: '#f59e0b', ic: 'fa-triangle-exclamation', cl: '#b45309' },
  info:    { bg: '#eff6ff', bd: '#3b82f6', ic: 'fa-circle-info', cl: '#1d4ed8' },
}

interface ToastCtx { show: (msg: string, type?: ToastType) => void }
const Ctx = createContext<ToastCtx>({ show: () => {} })

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  // Every auto-dismiss timer, so unmounting the provider (or a hot reload)
  // cannot leave a setState scheduled against a dead tree.
  const timers = useRef<number[]>([])

  const show = useCallback((message: string, type: ToastType = 'info') => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, type, message }])
    const handle = window.setTimeout(() => {
      setToasts((t) => t.filter((x) => x.id !== id))
      timers.current = timers.current.filter((h) => h !== handle)
    }, 4000)
    timers.current.push(handle)
  }, [])

  const dismiss = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id))
  }, [])

  useEffect(() => () => {
    timers.current.forEach(window.clearTimeout)
    timers.current = []
  }, [])

  // Stable context value — an inline `{ show }` object made every consumer of
  // useToast() re-render whenever the provider re-rendered (i.e. on every
  // toast), which cascaded through the whole app tree.
  const value = useMemo(() => ({ show }), [show])

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="toast-stack" role="region" aria-label="Notifications">
        {toasts.map((t) => {
          const s = STYLE[t.type]
          return (
            <div key={t.id} className="toast-item" role={t.type === 'error' ? 'alert' : 'status'} aria-live="polite" data-testid={`toast-${t.type}`}
              style={{ background: s.bg, borderColor: s.bd, color: s.cl }}>
              <i className={`fa-solid ${s.ic} toast-icon`} />
              <span className="min-w-0 break-words">{t.message}</span>
              <button type="button" onClick={() => dismiss(t.id)} aria-label="Dismiss notification" className="toast-close">
                <i className="fa-solid fa-xmark" />
              </button>
            </div>
          )
        })}
      </div>
    </Ctx.Provider>
  )
}

export function useToast() {
  return useContext(Ctx)
}
