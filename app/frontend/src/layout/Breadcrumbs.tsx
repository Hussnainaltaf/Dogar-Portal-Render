import { createContext, memo, ReactNode, useContext, useEffect, useMemo, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { resolveRouteMeta, Crumb } from './routeMeta'
import { TENANT_INFO } from '@/lib/api'
import { brandFullName } from '@/lib/branding'

/* ---------------------------------------------------------------------------
 * Breadcrumbs — page-hierarchy navigation for the whole admin/app flow.
 *
 * The trail is derived from the sidebar IA (see routeMeta.ts), so every page
 * gets a correct breadcrumb with zero per-page wiring. Detail pages that know
 * a better label for the LAST crumb (e.g. the real demand file number instead
 * of "File #12") can refine it at runtime with `useCrumbLabel('DTC-1023')` —
 * purely cosmetic, and safely ignored if a page never calls it.
 *
 * Mobile: the strip scrolls horizontally (CSS `.app-crumbs`) and long middle
 * crumbs are collapsed to an ellipsis so the current page always stays visible.
 * ------------------------------------------------------------------------- */

interface CrumbCtx {
  /** Overrides the label of the final crumb for the current route. */
  setLabel: (label: string | null) => void
}

const Ctx = createContext<CrumbCtx>({ setLabel: () => {} })
const LabelCtx = createContext<string | null>(null)

export function BreadcrumbProvider({ children }: { children: ReactNode }) {
  const [label, setLabel] = useState<string | null>(null)
  const { pathname } = useLocation()

  // Any navigation clears a stale override so the next page never inherits the
  // previous record's name while its own data is still loading.
  useEffect(() => { setLabel(null) }, [pathname])

  const value = useMemo(() => ({ setLabel }), [])

  return (
    <Ctx.Provider value={value}>
      <LabelCtx.Provider value={label}>{children}</LabelCtx.Provider>
    </Ctx.Provider>
  )
}

/**
 * Let a page refine the last breadcrumb with the record's real name.
 * Pass `null`/`undefined` while loading — the derived label is kept.
 */
export function useCrumbLabel(label?: string | null) {
  const { setLabel } = useContext(Ctx)
  useEffect(() => {
    if (label) setLabel(label)
    // No cleanup needed: BreadcrumbProvider resets on every route change.
  }, [label, setLabel])
}

/** Max crumbs rendered before the middle collapses to an ellipsis. */
const MAX_VISIBLE = 4

function Separator() {
  return <i className="crumb-sep fa-solid fa-chevron-right" aria-hidden="true" />
}

export default memo(function Breadcrumbs() {
  const { pathname } = useLocation()
  const override = useContext(LabelCtx)
  const meta = useMemo(() => resolveRouteMeta(pathname), [pathname])

  // Keep the browser tab title in sync with the page — small touch that makes
  // multi-tab operators (the common case here) much faster.
  // The company half of the title follows the active office (2026-08-19). It
  // was hardcoded, so a partner office's browser tab, bookmark titles, window
  // switcher entries and printed page headers all said "Dogar Trading
  // Corporation" -- the most-duplicated piece of branding in the whole UI,
  // since it is written on every route change. brandFullName() falls back to
  // the Dogar name when no tenant is resolved, so the root portal is unchanged.
  useEffect(() => {
    document.title = `${meta.title} · ${brandFullName(TENANT_INFO)}`
  }, [meta.title])

  const trail: Crumb[] = useMemo(() => {
    const t = meta.trail.map((c) => ({ ...c }))
    if (override && t.length) t[t.length - 1] = { label: override }
    if (t.length <= MAX_VISIBLE) return t
    // Collapse the middle, always keeping the root and the last two crumbs.
    return [t[0], { label: '…' }, ...t.slice(-2)]
  }, [meta.trail, override])

  return (
    <nav className="app-crumbs" aria-label="Breadcrumb">
      <ol className="flex items-center gap-1 min-w-0">
        {trail.map((crumb, i) => {
          const last = i === trail.length - 1
          return (
            <li key={`${crumb.label}-${i}`} className="flex items-center gap-1 min-w-0">
              {i > 0 && <Separator />}
              {last || !crumb.to ? (
                <span className={last ? 'crumb-current' : 'crumb-static'} aria-current={last ? 'page' : undefined}>
                  {crumb.icon && <i className={`${crumb.icon} mr-1.5 opacity-70`} aria-hidden="true" />}
                  {crumb.label}
                </span>
              ) : (
                <Link to={crumb.to}>
                  {crumb.icon && <i className={crumb.icon} aria-hidden="true" />}
                  <span>{crumb.label}</span>
                </Link>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
})
