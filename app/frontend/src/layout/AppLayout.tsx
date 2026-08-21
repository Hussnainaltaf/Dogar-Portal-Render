import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import Topbar from './Topbar'
import Breadcrumbs, { BreadcrumbProvider } from './Breadcrumbs'
import ContentErrorBoundary from '@/components/ContentErrorBoundary'
import { lockBodyScroll, unlockBodyScroll } from '@/lib/scrollLock'
import { APP_VENDOR } from '@/lib/appMeta'
import { TENANT_INFO } from '@/lib/api'
import { brandFullName } from '@/lib/branding'

/* The DtcBot is a self-contained widget that is NOT needed for first paint.
   Lazy-loading it keeps its transcript renderer, card views and OCR upload
   plumbing out of the entry chunk; it streams in right after the shell is
   interactive. `fallback={null}` means nothing flashes while it arrives. */
const Chatbot = lazy(() => import('@/components/Chatbot'))

/** Content-pane loader shown while a lazy route chunk streams in. Sized to sit
 *  INSIDE the main content area so the sidebar + topbar stay mounted — the page
 *  never blanks out on navigation (no "full reload" feeling). */
function ContentFallback() {
  return (
    <div className="flex items-center justify-center py-24 text-slate-400" role="status" aria-label="Loading page">
      <i className="fa-solid fa-spinner fa-spin text-2xl" style={{ color: 'var(--brand)' }} />
    </div>
  )
}

// Master shell — fixed sidebar + sticky topbar + breadcrumb band + page
// content + footer + DtcBot. On ≤1023px the sidebar becomes a slide-in drawer
// (CSS in index.css handles the transform; we toggle .show + backdrop).
export default function AppLayout() {
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [routeBusy, setRouteBusy] = useState(false)
  const [tenant, setTenant] = useState(TENANT_INFO)
  const { pathname } = useLocation()

  useEffect(() => {
    const onBrandingUpdated = (event: Event) => {
      const detail = (event as CustomEvent<typeof TENANT_INFO>).detail
      if (detail) setTenant(detail)
    }
    window.addEventListener('tenant-branding-updated', onBrandingUpdated)
    return () => window.removeEventListener('tenant-branding-updated', onBrandingUpdated)
  }, [])

  // TENANT BRANDING IS NOT APPLIED HERE ANY MORE (moved 2026-08-19 to
  // src/main.tsx). It used to be a `useEffect` in this component, which meant
  // it only ran once the user was already past <RequireAuth> -- so the tenant's
  // own login screen, and every screen shown after a 401 kicked the session
  // out, rendered in Dogar's colours and typeface. Branding a portal is not an
  // authenticated concern; it is a property of which office the URL belongs to,
  // which the server has already told us in the boot payload. Doing it at boot
  // also means the tokens are set before the first paint rather than one effect
  // after it. Do not re-add a call here: two writers of the same tokens is how
  // the next drift starts.

  // Route-transition progress bar: flash a thin top bar on every navigation so
  // the user gets immediate feedback that the tap registered (perceived speed).
  useEffect(() => {
    setRouteBusy(true)
    const t = window.setTimeout(() => setRouteBusy(false), 350)
    return () => window.clearTimeout(t)
  }, [pathname])

  // Keep the #sidebar .show class in sync with React state, and lock body
  // scroll while the mobile drawer is open so the page behind doesn't move.
  useEffect(() => {
    const el = document.getElementById('sidebar')
    if (el) el.classList.toggle('show', drawerOpen)
    if (!drawerOpen) return

    // Escape closes the drawer. Found in QA: with the drawer open the overlay
    // covered the header, and because there was no keyboard dismissal the only
    // way out was tapping the backdrop — a dead end for keyboard users and a
    // trap whenever the backdrop was itself covered.
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setDrawerOpen(false) }
    document.addEventListener('keydown', onKey)

    // Reference-counted lock so overlapping drawers/modals can't leave the page
    // permanently unscrollable.
    lockBodyScroll()
    return () => {
      document.removeEventListener('keydown', onKey)
      unlockBodyScroll()
    }
  }, [drawerOpen])

  // On navigation: close the mobile drawer and return the content pane to the
  // top. Without the scroll reset, opening a record from halfway down a long
  // list dropped the user into the middle of the next page.
  useEffect(() => {
    setDrawerOpen(false)
    window.scrollTo({ top: 0, behavior: 'auto' })
  }, [pathname])

  const toggleSidebar = useCallback(() => setDrawerOpen((o) => !o), [])
  const closeSidebar = useCallback(() => setDrawerOpen(false), [])

  return (
    <BreadcrumbProvider>
      <div className="flex min-h-screen">
        <Sidebar onNavigate={closeSidebar} />

        {/* Mobile backdrop (below lg breakpoint) */}
        <div
          className={`spa-backdrop lg:hidden ${drawerOpen ? 'show' : ''}`}
          onClick={closeSidebar}
          aria-hidden="true"
        />

        {/* Route-transition progress bar */}
        <div aria-hidden="true" className={`route-progress ${routeBusy ? 'route-progress--busy' : ''}`} role="progressbar" />

        <div className="flex-1 flex flex-col min-h-screen min-w-0 spa-main">
          <Topbar onToggleSidebar={toggleSidebar} sidebarOpen={drawerOpen} />

          {/* Breadcrumb band — brand gradient hairline + page hierarchy. Sits
              directly under the sticky topbar so orientation is always visible
              without stealing vertical space from the content. */}
          <div className="app-band">
            <div className="mx-auto w-full max-w-[1600px]">
              <Breadcrumbs />
            </div>
          </div>

          <main className="flex-1 p-6 spa-main-content">
            {/* `key={pathname}` restarts the page-enter transition on every
                navigation. The animation is a 140ms opacity/translate settle
                defined in index.css (and disabled under
                prefers-reduced-motion) — enough to make routing feel
                deliberate rather than a hard cut, short enough that it never
                delays interaction. */}
            <div key={pathname} className="page-enter mx-auto w-full max-w-[1600px]">
              {/* Route-scoped error boundary: a crash inside a single lazy route
                  no longer blanks the whole SPA; the shell stays mounted so the
                  user can navigate away. Passing `pathname` as the resetKey
                  means switching pages auto-clears a caught error. */}
              <ContentErrorBoundary resetKey={pathname}>
                {/* Suspense INSIDE the shell: only this pane shows the loader
                    while a lazy route chunk loads, so the sidebar/topbar never
                    unmount. */}
                <Suspense fallback={<ContentFallback />}>
                  <Outlet />
                </Suspense>
              </ContentErrorBoundary>
            </div>
          </main>

          {/* The copyright holder is the OFFICE that owns this portal, not the
              root company (2026-08-19). Hardcoding it put "Dogar Trading
              Corporation" in the footer of every partner office's portal, on
              every page. "Powered by <vendor>" is deliberately NOT tenant-aware:
              that is the software vendor's attribution and is the same for
              everyone. brandFullName() falls back to the Dogar name when no
              tenant is resolved, so the root portal is unchanged. */}
          <footer className="bg-white border-t border-slate-200 px-6 py-3 flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-[12px] text-slate-500 text-center">
            <span>&copy; {new Date().getFullYear()} {brandFullName(tenant)} &middot; Powered by</span>
            {/* A <button> (not href="#") — an empty hash is an in-page
                navigation that scrolls the page back to the top on click. */}
            <button
              type="button"
              className="inline-flex items-center gap-1.5 hover:opacity-80 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 rounded transition-opacity bg-transparent border-0 p-0 cursor-default"
              title={`Designed & developed by ${APP_VENDOR}`}
              aria-label={`Designed and developed by ${APP_VENDOR}`}
            >
              <img
                src="/static/img/dev_logo_icon.png"
                alt=""
                width={20}
                height={20}
                className="h-5 w-5 object-contain"
                style={{ filter: 'drop-shadow(0 1px 1px rgba(0,0,0,0.08))' }}
              />
              <span className="font-semibold text-slate-700 hidden sm:inline">{APP_VENDOR}</span>
            </button>
          </footer>
        </div>

        <Suspense fallback={null}>
          <Chatbot />
        </Suspense>
      </div>
    </BreadcrumbProvider>
  )
}
