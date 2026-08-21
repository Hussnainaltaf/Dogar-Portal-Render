import { memo, useCallback, useEffect, useMemo, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { NAV_SECTIONS, NavChild, NavEntry, NavGroup, NavLeaf, NavSection } from './navConfig'
import { useAuth } from '@/auth/AuthContext'
import { api, apiError, TENANT_BASE, TENANT_INFO } from '@/lib/api'
import { brandFullName } from '@/lib/branding'
import { useToast } from '@/components/Toast'
import { APP_VERSION } from '@/lib/appMeta'

/* ---------------------------------------------------------------------------
 * Enterprise sidebar (v13).
 *
 * What changed vs. v12 and WHY:
 *  • Sections — the flat 15-item list is banded into Overview / Operations /
 *    Directory / Administration (see navConfig). Same items, same order; the
 *    labels just give the eye anchors so scanning is O(section) not O(list).
 *  • Gradient brand block — the logo header now carries the brand gradient so
 *    the product reads as one identity with the page band and the DtcBot.
 *  • Desktop collapse — a persisted icon-rail mode (76px) driven purely by the
 *    `--sidebar-w` custom property + an `html.sb-collapsed` class, so toggling
 *    it costs one class write and re-renders nothing but this component.
 *  • Perf — the permission-filtered section tree is memoised, and Leaf/Group
 *    are React.memo'd, so a parent re-render (route change, auth refresh) no
 *    longer re-creates every nav row.
 * ------------------------------------------------------------------------- */

const COLLAPSE_KEY = 'sidebar:collapsed'

/** Read the persisted desktop-collapse preference (SSR/first-paint safe). */
function readCollapsed(): boolean {
  try { return localStorage.getItem(COLLAPSE_KEY) === '1' } catch { return false }
}

export default function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { user, can } = useAuth()
  const { pathname } = useLocation()
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [tenant, setTenant] = useState(TENANT_INFO)

  useEffect(() => {
    const onBrandingUpdated = (event: Event) => {
      const detail = (event as CustomEvent<typeof TENANT_INFO>).detail
      if (detail) setTenant(detail)
    }
    window.addEventListener('tenant-branding-updated', onBrandingUpdated)
    return () => window.removeEventListener('tenant-branding-updated', onBrandingUpdated)
  }, [])

  // Reflect the collapse state on <html> so pure CSS can resize the rail and
  // shift the content column (no layout-thrashing React work per frame).
  useEffect(() => {
    document.documentElement.classList.toggle('sb-collapsed', collapsed)
    try { localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0') } catch { /* private mode */ }
  }, [collapsed])

  // Permission-filtered nav tree. Items whose `perm` the user lacks are hidden
  // entirely (no privilege leak, no dead links) and an empty group disappears.
  const sections = useMemo<NavSection[]>(() => {
    return NAV_SECTIONS.map((section) => ({
      title: section.title,
      // The explicit `NavEntry[]` return annotation matters: without it TS
      // widens the spread `{ ...entry, children }` to a structural object type
      // that no longer matches the NavGroup branch of the union.
      entries: section.entries.flatMap((entry): NavEntry[] => {
        if (entry.type === 'group') {
          const children = entry.children.filter((c) => !c.perm || can(c.perm))
          return children.length ? [{ ...entry, children }] : []
        }
        return entry.perm && !can(entry.perm) ? [] : [entry]
      }),
    })).filter((s) => s.entries.length > 0)
  }, [can])

  // Auto-open the group that owns the current route (and keep it in sync as the
  // user navigates — the old implementation only computed this once at mount,
  // so landing on /roles from a link left the Users group collapsed).
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({})
  useEffect(() => {
    setOpenGroups((prev) => {
      let changed = false
      const next = { ...prev }
      for (const section of NAV_SECTIONS) {
        for (const entry of section.entries) {
          if (entry.type !== 'group') continue
          if (entry.children.some((c) => pathname === c.path || pathname.startsWith(c.path + '/'))) {
            if (!next[entry.key]) { next[entry.key] = true; changed = true }
          }
        }
      }
      return changed ? next : prev
    })
  }, [pathname])

  const toggleGroup = useCallback((key: string) => {
    // In the collapsed icon-rail the children are hidden by CSS, so opening a
    // group implies expanding the rail (otherwise the click would do nothing).
    setCollapsed(false)
    setOpenGroups((s) => ({ ...s, [key]: !s[key] }))
  }, [])

  const brandName = (tenant?.shortName || tenant?.name || 'DOGAR TRADING').toUpperCase()
  const brandSub = tenant?.subtitle || tenant?.officeName || 'Overseas Employment Promoters'

  return (
    <aside
      id="sidebar"
      aria-label="Main navigation"
      className="bg-white border-r border-slate-200 fixed inset-y-0 left-0 h-screen z-30 flex flex-col transition-transform duration-300"
    >
      {/* Brand block — gradient, pinned (does not scroll with the nav).
          In a tenant copy portal (TENANT_INFO set) this shows THAT office's
          brand (name, subtitle, logo) so the copy is unmistakably its own
          recruiting office. */}
      <div className="sb-brand shrink-0">
                  <span className="sb-brand-logo">
          <img src={tenant?.logo || '/static/img/logo-sm.png'} alt="" width={26} height={26} />

        </span>
        <div className="sb-brand-text">
          <div className="sb-brand-name" title={brandFullName(tenant)}>{brandName}</div>
          <div className="sb-brand-sub">{brandSub}</div>
        </div>
      </div>

      {/* Nav — the only scrollable region */}
      <nav className="flex-1 min-h-0 overflow-y-auto px-2.5 pb-3 space-y-0.5">
        {sections.map((section) => (
          <div key={section.title}>
            <div className="sb-section" aria-hidden="true">{section.title}</div>
            {section.entries.map((entry) =>
              entry.type === 'group' ? (
                <Group
                  key={entry.key}
                  group={entry}
                  open={!!openGroups[entry.key]}
                  toggle={toggleGroup}
                  onNavigate={onNavigate}
                  pathname={pathname}
                />
              ) : (
                <Leaf key={entry.path} leaf={entry} onNavigate={onNavigate} />
              ),
            )}
          </div>
        ))}
      </nav>

      {can('settings:backup') && <BackupDropdown />}

      {/* Desktop-only collapse toggle (the mobile drawer has its own close
          affordances: backdrop tap, hamburger, and auto-close on navigate). */}
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="sb-collapse-btn hidden lg:flex shrink-0"
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        <i className={`fa-solid ${collapsed ? 'fa-angles-right' : 'fa-angles-left'}`} />
        <span className="sb-label">Collapse</span>
      </button>

      {/* Footer mini powered-by — pinned */}
      <div className="sb-foot shrink-0">
        <span className="flex items-center gap-1.5 min-w-0">
          <img src="/static/img/dev_logo_icon.png" alt="" className="h-4 w-4 object-contain shrink-0" width={16} height={16} />
          <span className="sb-foot-text truncate">HussnainTechVertex</span>
        </span>
        {user && <span className="sb-foot-text shrink-0 font-semibold text-slate-400">v{APP_VERSION}</span>}
      </div>
    </aside>
  )
}

/* ------------------------------------------------------------------ */
/* Nav rows                                                            */
/* ------------------------------------------------------------------ */

const Leaf = memo(function Leaf({ leaf, onNavigate }: { leaf: NavLeaf; onNavigate?: () => void }) {
  return (
    <NavLink
      to={leaf.path}
      onClick={onNavigate}
      title={leaf.label}
      className={({ isActive }) => `nav-item ${isActive ? 'nav-active' : ''}`}
    >
      <i className={`${leaf.icon} w-5 text-center`} aria-hidden="true" />
      <span className="sb-label truncate">{leaf.label}</span>
    </NavLink>
  )
})

const Group = memo(function Group({ group, open, toggle, onNavigate, pathname }: {
  group: NavGroup
  open: boolean
  toggle: (key: string) => void
  onNavigate?: () => void
  pathname: string
}) {
  const parentActive = group.children.some((c) => pathname === c.path || pathname.startsWith(c.path + '/'))
  return (
    <div className="nav-group">
      <button
        type="button"
        onClick={() => toggle(group.key)}
        title={group.label}
        aria-expanded={open}
        className={`nav-item w-full justify-between ${parentActive ? 'nav-active-parent' : ''}`}
      >
        <span className="flex items-center gap-2.5 min-w-0">
          <i className={`${group.icon} w-5 text-center`} aria-hidden="true" />
          <span className="sb-label truncate">{group.label}</span>
        </span>
        <i className={`sb-chevron fa-solid fa-chevron-right text-[9px] transition-transform ${open ? 'rotate-90' : ''}`} aria-hidden="true" />
      </button>
      {open && (
        <div className="nav-group-children ml-3.5 mt-0.5 space-y-0.5 border-l border-slate-200/80 pl-2">
          {group.children.map((c) => <SubLink key={c.path} child={c} onNavigate={onNavigate} />)}
        </div>
      )}
    </div>
  )
})

const SubLink = memo(function SubLink({ child, onNavigate }: { child: NavChild; onNavigate?: () => void }) {
  return (
    <NavLink
      to={child.path}
      onClick={onNavigate}
      className={({ isActive }) => `nav-sub ${isActive ? 'nav-sub-active' : ''}`}
    >
      <i className={`${child.icon} w-4 text-center`} aria-hidden="true" />
      <span className="sb-label truncate">{child.label}</span>
    </NavLink>
  )
})

/* ------------------------------------------------------------------ */
/* Backups panel                                                       */
/* ------------------------------------------------------------------ */

interface BackupItem { filename: string; size: number; created_at: string }

function BackupDropdown() {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [items, setItems] = useState<BackupItem[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    try { const { data } = await api.get('/backups/'); setItems(data.items || []) }
    catch (error) { toast.show(apiError(error, 'Could not load backups'), 'error') }
    finally { setLoading(false) }
  }, [toast])

  const toggle = () => { const next = !open; setOpen(next); if (next) void load() }

  const create = async () => {
    if (loading) return
    setLoading(true)
    try {
      const { data } = await api.post('/backups/')
      toast.show('Portal backup created', 'success')
      setItems((current) => [data, ...current.filter((x) => x.filename !== data.filename)])
    } catch (error) { toast.show(apiError(error, 'Backup failed'), 'error') }
    finally { setLoading(false) }
  }

  const download = (filename: string) => {
    window.location.href = `${TENANT_BASE}/api/backups/${encodeURIComponent(filename)}`
  }

  return (
    <section className="shrink-0 border-t border-slate-100 px-2.5 py-2" data-testid="backup-dropdown">
      <button type="button" onClick={toggle} className="nav-item w-full justify-between" aria-expanded={open} title="Backups">
        <span className="flex items-center gap-2.5 min-w-0">
          <i className="fa-solid fa-database w-5 text-center" aria-hidden="true" />
          <span className="sb-label">Backups</span>
        </span>
        <i className={`sb-chevron fa-solid fa-chevron-right text-[9px] transition-transform ${open ? 'rotate-90' : ''}`} aria-hidden="true" />
      </button>
      {open && (
        <div className="nav-group-children mt-2 rounded-xl border border-slate-200 bg-slate-50/70 p-2">
          <button
            type="button"
            data-testid="create-backup"
            disabled={loading}
            onClick={create}
            className="flex w-full items-center justify-center gap-2 rounded-lg px-3 py-2 text-[11.5px] font-bold text-white disabled:opacity-50 brand-gradient"
          >
            <i className={`fa-solid ${loading ? 'fa-spinner fa-spin' : 'fa-box-archive'}`} />
            {loading ? 'Creating backup…' : 'Create full backup'}
          </button>
          <div className="mt-2 max-h-40 space-y-1 overflow-y-auto">
            {!loading && !items.length && <p className="px-2 py-2 text-center text-[11px] text-slate-400">No backups yet</p>}
            {items.slice(0, 5).map((item) => (
              <button
                type="button"
                key={item.filename}
                onClick={() => download(item.filename)}
                className="flex w-full items-center gap-2 rounded-lg bg-white px-2 py-2 text-left hover:bg-blue-50 border border-slate-200/70"
                title={`Download ${item.filename}`}
              >
                <i className="fa-solid fa-download text-blue-600" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[11px] font-semibold text-slate-700">{new Date(item.created_at).toLocaleString()}</span>
                  <span className="text-[10px] text-slate-400">{(item.size / 1024 / 1024).toFixed(1)} MB</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
