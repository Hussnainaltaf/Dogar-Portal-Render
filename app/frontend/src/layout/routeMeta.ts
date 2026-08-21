// ---------------------------------------------------------------------------
// Route metadata + breadcrumb resolution.
//
// The sidebar's NAV_SECTIONS already describes the app's information
// architecture (section → entry → sub-entry). Rather than maintaining a second,
// drift-prone table of page titles, this module DERIVES the breadcrumb trail
// and page title from that single source, and only hand-writes the handful of
// routes that are not reachable from the menu (record detail pages, the
// template designer, the unauthorized screen).
//
// Adding a nav entry therefore gives you a correct breadcrumb for free.
// ---------------------------------------------------------------------------
import { NAV_SECTIONS } from './navConfig'

export interface Crumb {
  label: string
  /** Omitted for the current (last) crumb. */
  to?: string
  icon?: string
}

export interface RouteMeta {
  /** Page title (also used for the document title). */
  title: string
  icon: string
  /** Section band the page belongs to ("Operations", "Administration", …). */
  section?: string
  trail: Crumb[]
}

interface NavIndexEntry {
  label: string
  icon: string
  section: string
  /** Set when the entry lives inside an expandable sidebar group. */
  parent?: { label: string; icon: string; to: string }
}

/** path → nav metadata, built once at module load. */
const NAV_INDEX: Record<string, NavIndexEntry> = (() => {
  const index: Record<string, NavIndexEntry> = {}
  for (const section of NAV_SECTIONS) {
    for (const entry of section.entries) {
      if (entry.type === 'leaf') {
        index[entry.path] = { label: entry.label, icon: entry.icon, section: section.title }
        continue
      }
      // A group is not itself navigable — its first child stands in as the
      // parent link target so the crumb is always clickable.
      const first = entry.children[0]
      for (const child of entry.children) {
        index[child.path] = {
          label: child.label,
          icon: child.icon,
          section: section.title,
          parent: first ? { label: entry.label, icon: entry.icon, to: first.path } : undefined,
        }
      }
    }
  }
  return index
})()

const HOME: Crumb = { label: 'Dashboard', to: '/dashboard', icon: 'fa-solid fa-gauge' }

/** Routes that are not in the sidebar (details, designer, error screens). */
interface DynamicRoute {
  test: RegExp
  build: (m: RegExpMatchArray) => RouteMeta
}

const DYNAMIC_ROUTES: DynamicRoute[] = [
  {
    test: /^\/demands\/(\d+)$/,
    build: (m) => ({
      title: `Demand File #${m[1]}`,
      icon: 'fa-regular fa-folder-open',
      section: 'Operations',
      trail: [HOME, { label: 'Demand Files', to: '/demands', icon: 'fa-regular fa-folder-open' }, { label: `File #${m[1]}` }],
    }),
  },
  {
    test: /^\/clients\/(\d+)$/,
    build: (m) => ({
      title: `Client #${m[1]}`,
      icon: 'fa-regular fa-building',
      section: 'Operations',
      trail: [HOME, { label: 'Clients', to: '/clients', icon: 'fa-regular fa-building' }, { label: `Client #${m[1]}` }],
    }),
  },
  {
    test: /^\/documents\/customize\/([^/]+)$/,
    build: () => ({
      title: 'Template Designer',
      icon: 'fa-solid fa-pen-ruler',
      section: 'Operations',
      trail: [HOME, { label: 'Documents', to: '/documents', icon: 'fa-regular fa-file-lines' }, { label: 'Template Designer' }],
    }),
  },
  {
    test: /^\/settings\/general$/,
    build: () => ({
      title: 'General Settings',
      icon: 'fa-solid fa-gear',
      section: 'Administration',
      trail: [HOME, { label: 'Settings', to: '/settings', icon: 'fa-solid fa-gear' }, { label: 'General' }],
    }),
  },
  {
    test: /^\/unauthorized$/,
    build: () => ({
      title: 'Access Denied',
      icon: 'fa-solid fa-lock',
      trail: [HOME, { label: 'Access Denied' }],
    }),
  },
]

/**
 * Resolve the breadcrumb trail + page title for a router pathname.
 * Always returns a usable value (never throws) so the shell can render the
 * band for any route, including ones added later.
 */
export function resolveRouteMeta(pathname: string): RouteMeta {
  const path = pathname.replace(/\/+$/, '') || '/'

  if (path === '/' || path === '/dashboard') {
    return { title: 'Dashboard', icon: 'fa-solid fa-gauge', section: 'Overview', trail: [{ label: 'Dashboard' }] }
  }

  const nav = NAV_INDEX[path]
  if (nav) {
    const trail: Crumb[] = [HOME]
    if (nav.parent) trail.push({ label: nav.parent.label, to: nav.parent.to, icon: nav.parent.icon })
    trail.push({ label: nav.label })
    return { title: nav.label, icon: nav.icon, section: nav.section, trail }
  }

  for (const route of DYNAMIC_ROUTES) {
    const m = path.match(route.test)
    if (m) return route.build(m)
  }

  // Unknown route: title-case the last segment so the band still reads well
  // instead of collapsing to an empty strip.
  const last = path.split('/').filter(Boolean).pop() || 'Page'
  const label = last.replace(/[-_]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  return { title: label, icon: 'fa-regular fa-file', trail: [HOME, { label }] }
}
