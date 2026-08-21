import { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useDialogKeys } from '../hooks/useDialogKeys'

/**
 * Shared UI primitives. These map onto the design-system classes in
 * `index.css` / `app.css` (`.card`, `.badge`, `.btn`, `.state-block`,
 * `.skeleton`) so pages stay declarative and every screen gets the same
 * loading / empty / error treatment.
 *
 * v13 additions: `Skeleton`, `TableSkeleton`, `ErrorState`, a richer
 * `EmptyState` (icon tile + optional title/action), and a `Modal` that handles
 * Escape, background scroll-lock and a mobile-friendly sheet layout.
 * All existing call signatures stay source-compatible — the new props are
 * optional — so no page had to change.
 */

export function PageHeader({ title, subtitle, icon, actions }: {
  title: string
  subtitle?: string
  icon?: string
  actions?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 mb-5">
      <div className="flex items-start gap-3 min-w-0">
        {icon && <span className="brand-tile mt-0.5" aria-hidden="true"><i className={icon} /></span>}
        <div className="min-w-0">
          <h1 className="text-xl sm:text-2xl font-bold text-slate-900 leading-tight break-words">{title}</h1>
          {subtitle && <p className="text-sm text-slate-500 mt-1 break-words">{subtitle}</p>}
        </div>
      </div>
      {/* `page-actions` lets narrow screens wrap the buttons instead of
          squeezing them off the edge (see the mobile layer in index.css). */}
      {actions && <div className="page-actions flex items-center gap-2 flex-wrap">{actions}</div>}
    </div>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`card ${className}`}>{children}</div>
}
export function CardHeader({ children }: { children: ReactNode }) {
  return <div className="card-header">{children}</div>
}
export function CardBody({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`card-body ${className}`}>{children}</div>
}

const BADGE_MAP: Record<string, string> = {
  pending: 'badge-pending', processing: 'badge-processing', issued: 'badge-issued',
  deployed: 'badge-deployed', filled: 'badge-filled', active: 'badge-active',
  inactive: 'badge-inactive', expired: 'badge-expired', cancelled: 'badge-cancelled',
  rejected: 'badge-rejected',
}
export function Badge({ status, children }: { status?: string; children: ReactNode }) {
  const cls = status ? BADGE_MAP[status.toLowerCase()] || 'badge-pending' : 'badge-pending'
  return <span className={`badge ${cls}`}>{children}</span>
}

export function Btn({ variant = 'primary', children, ...props }: {
  variant?: 'primary' | 'outline'
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const base = variant === 'primary' ? 'btn btn-primary' : 'btn btn-outline'
  return <button {...props} className={`${base} ${props.className || ''}`}>{children}</button>
}

/* ------------------------------------------------------------------ */
/* Loading / empty / error states                                      */
/* ------------------------------------------------------------------ */

/** Grey shimmer placeholder. `className` supplies the box size. */
export function Skeleton({ className = 'h-4 w-full' }: { className?: string }) {
  return <span className={`skeleton block ${className}`} aria-hidden="true" />
}

/**
 * Placeholder for a table/list that is still loading. Renders real rows so the
 * layout does not jump when data arrives, and announces itself politely rather
 * than leaving assistive tech on a blank region.
 */
export function TableSkeleton({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="p-3 sm:p-4 space-y-2.5" role="status" aria-label="Loading data">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex items-center gap-3">
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={c} className={`h-4 ${c === 0 ? 'w-1/4' : 'flex-1'}`} />
          ))}
        </div>
      ))}
    </div>
  )
}

export function EmptyState({ icon = 'fa-regular fa-folder-open', title, text, action }: {
  icon?: string
  title?: string
  text: string
  action?: ReactNode
}) {
  return (
    <div className="state-block" role="status">
      <span className="state-icon" aria-hidden="true"><i className={icon} /></span>
      {title && <p className="text-[15px] font-semibold text-slate-700">{title}</p>}
      <p className="text-sm text-slate-500 max-w-sm">{text}</p>
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}

/** Failure state with a retry affordance — the counterpart to EmptyState. */
export function ErrorState({ text = 'Something went wrong while loading this data.', onRetry }: {
  text?: string
  onRetry?: () => void
}) {
  return (
    <div className="state-block" role="alert">
      <span className="state-icon state-icon--danger" aria-hidden="true"><i className="fa-solid fa-triangle-exclamation" /></span>
      <p className="text-[15px] font-semibold text-slate-700">Couldn't load</p>
      <p className="text-sm text-slate-500 max-w-sm">{text}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn btn-outline mt-3">
          <i className="fa-solid fa-rotate-right mr-1.5" /> Try again
        </button>
      )}
    </div>
  )
}

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="state-block" role="status" aria-live="polite">
      <i className="fa-solid fa-spinner fa-spin text-2xl text-blue-600" />
      <p className="text-sm text-slate-500 mt-2">{label}</p>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Modal                                                               */
/* ------------------------------------------------------------------ */

export function Modal({ open, onClose, title, children, maxWidth = 'max-w-2xl' }: {
  open: boolean
  onClose: () => void
  title: ReactNode
  children: ReactNode
  maxWidth?: string
}) {
  // Escape, background scroll-lock, focus-in, focus trap and focus restore --
  // all shared, because six hand-rolled copies of this logic is exactly how
  // four overlays ended up with no Escape handling at all. Hooks must run
  // unconditionally, so the `open` guard lives inside the hook rather than
  // before it (the early `return null` below must stay after all hooks).
  const cardRef = useDialogKeys<HTMLDivElement>(open, onClose)

  if (!open) return null

  // Portal to <body> so the overlay is always viewport-fixed, never trapped by
  // an ancestor transform/filter stacking context (see the Topbar comment).
  return createPortal(
    <div
      className="spa-modal-backdrop fixed inset-0 z-[100] flex items-start sm:items-center justify-center p-0 sm:p-8 overflow-y-auto animate-fade-in"
      onClick={onClose}
      role="presentation"
    >
      <div
        ref={cardRef}
        className={`spa-modal-card bg-white shadow-2xl w-full ${maxWidth}`}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="spa-modal-head px-4 sm:px-5 py-4 border-b border-slate-100 flex items-center justify-between gap-3">
          <h2 className="text-base sm:text-lg font-bold text-slate-800 min-w-0 truncate">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="w-10 h-10 -mr-1.5 rounded-full hover:bg-slate-100 active:bg-slate-200 flex items-center justify-center text-slate-500 shrink-0 transition"
          >
            <i className="fa-solid fa-xmark text-lg" />
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  )
}
