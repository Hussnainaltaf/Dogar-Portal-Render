// Sidebar navigation — single source of truth for the app's information
// architecture. Consumed by:
//   • layout/Sidebar.tsx    — renders the nav
//   • layout/routeMeta.ts   — derives breadcrumb labels/icons from it
//
// FIX 10 — Removed the `active` property from every NavLeaf and every child
// of NavGroup, plus the `activeKeys` array on NavGroup. These were dead code
// left over from the old server-rendered template where each page emitted an
// `active` key that base.html cross-referenced to highlight the correct
// sidebar entry. In the SPA the Sidebar component uses React Router's
// <NavLink isActive> callback directly.
//
// v13 (enterprise IA) — entries are now grouped into labelled SECTIONS
// (Overview / Operations / Directory / Administration). Nothing was added or
// removed from the menu: the same items in the same order, just banded so a
// 15-item sidebar scans as four short lists instead of one long one. `NAV`
// is still exported (flattened) so any consumer that only needs the entry
// list keeps working unchanged.

export interface NavLeaf {
  type: 'leaf'
  label: string
  path: string
  icon: string // FontAwesome class
  /** Optional permission required to SEE this nav item (hidden otherwise). */
  perm?: string
}

export interface NavChild {
  label: string
  path: string
  icon: string
  perm?: string
}

export interface NavGroup {
  type: 'group'
  label: string
  icon: string
  key: string
  children: NavChild[]
}

export type NavEntry = NavLeaf | NavGroup

export interface NavSection {
  /** Short uppercase band label shown above the entries. */
  title: string
  entries: NavEntry[]
}

export const NAV_SECTIONS: NavSection[] = [
  {
    title: 'Overview',
    entries: [
      { type: 'leaf', label: 'Dashboard', path: '/dashboard', icon: 'fa-solid fa-gauge' },
    ],
  },
  {
    title: 'Operations',
    entries: [
      { type: 'leaf', label: 'Clients', path: '/clients', icon: 'fa-regular fa-building' },
      { type: 'leaf', label: 'Demand Files', path: '/demands', icon: 'fa-regular fa-folder-open' },
      { type: 'leaf', label: 'Candidates', path: '/candidates', icon: 'fa-solid fa-users' },
      { type: 'leaf', label: 'Documents', path: '/documents', icon: 'fa-regular fa-file-lines' },
      { type: 'leaf', label: 'Protector Letter', path: '/protector-letter', icon: 'fa-regular fa-envelope-open' },
    ],
  },
  {
    title: 'Directory',
    entries: [
      { type: 'leaf', label: 'Visa Categories', path: '/visa-categories', icon: 'fa-solid fa-tags' },
      { type: 'leaf', label: 'Embassies', path: '/embassies', icon: 'fa-solid fa-landmark' },
      { type: 'leaf', label: 'Cities', path: '/cities', icon: 'fa-solid fa-location-dot' },
      { type: 'leaf', label: 'Medical Centers', path: '/medical-centers', icon: 'fa-solid fa-stethoscope' },
      { type: 'leaf', label: 'Contacts', path: '/contacts', icon: 'fa-regular fa-address-book' },
    ],
  },
  {
    title: 'Administration',
    entries: [
      {
        type: 'group', label: 'Users', icon: 'fa-solid fa-user-group', key: 'users-sub',
        children: [
          { label: 'Manage Users', path: '/users', icon: 'fa-regular fa-user' },
          { label: 'Login history', path: '/login-history', icon: 'fa-regular fa-clock' },
          { label: 'Roles', path: '/roles', icon: 'fa-solid fa-shield-halved' },
          // RESTORED 2026-08-18 (operator request: "add filters and search to
          // the Audit Log page so I can quickly find denied or successful
          // actions by office, user, document type, and date range").
          //
          // `perm` is set so the entry HIDES for a user who cannot open it.
          // Showing a link that lands on /unauthorized is worse than showing
          // nothing: it advertises a capability and then refuses it.
          {
            label: 'Audit Log', path: '/admin/audit-log',
            icon: 'fa-solid fa-clipboard-list', perm: 'audit:view',
          },
          // STILL REMOVED 2026-07-30 (operator request): the 'Data Migration'
          // entry in this dropdown, along with the 'Reports' and 'Sub-Agents'
          // top-level entries. They were reported as unusable and have been
          // deleted rather than hidden -- see section 4.11 of
          // PROJECT_DOCUMENTATION.md for what was removed and what was kept.
        ],
      },
      { type: 'leaf', label: 'Portal Admin', path: '/portal-admin', icon: 'fa-solid fa-sitemap' },
      {
        type: 'group', label: 'Settings', icon: 'fa-solid fa-gear', key: 'settings-sub',
        children: [
          { label: 'General', path: '/settings', icon: 'fa-regular fa-building' },
          { label: 'Depositors', path: '/depositors', icon: 'fa-solid fa-user-tie' },
          { label: 'Service Charges', path: '/service-charges', icon: 'fa-solid fa-dollar-sign' },
        ],
      },
    ],
  },
]

/** Flattened entry list (back-compat + convenience for lookups). */
export const NAV: NavEntry[] = NAV_SECTIONS.flatMap((s) => s.entries)
