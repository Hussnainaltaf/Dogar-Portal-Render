import { memo, useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { api, apiError, TENANT_INFO } from '@/lib/api'
import { brandFullName, brandLabel } from '@/lib/branding'
import { useAuth } from '@/auth/AuthContext'
import { useToast } from '@/components/Toast'
import { greeting } from '@/lib/format'
import { useDialogKeys } from '@/hooks/useDialogKeys'
import NotificationBell from './NotificationBell'
import GlobalSearch from './GlobalSearch'

/**
 * Application top bar.
 *
 * v13 — the global candidate search was extracted into `GlobalSearch` so the
 * SAME behaviour can be presented two ways:
 *   • inline  — the desktop/tablet search bar (self-hides below `sm`).
 *   • sheet   — a full-width overlay opened by the phone-only magnifier
 *               button. Before this change the header search was simply
 *               `display:none` under 640px, so phone users had no search at
 *               all. All search state/handlers now live in that component;
 *               Topbar only owns the chrome (hamburger, greeting, bell,
 *               profile menu and its two modals).
 *
 * Escape closes the profile menu and the mobile search sheet. The profile menu
 * is portalled to <body> with its own backdrop because `backdrop-filter` on
 * `header.topbar` makes the header a containing block for fixed descendants.
 */
function Topbar({ onToggleSidebar, sidebarOpen = false }: { onToggleSidebar: () => void; sidebarOpen?: boolean }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const toast = useToast()
  const firstName = (user?.name || '').trim().split(/\s+/)[0] || 'there'

  const [showChangePw, setShowChangePw] = useState(false)
  const [showAccount, setShowAccount] = useState(false)
  const [showProfile, setShowProfile] = useState(false)
  const [showMobileSearch, setShowMobileSearch] = useState(false)
  const [tenant, setTenant] = useState(TENANT_INFO)

  useEffect(() => {
    const onBrandingUpdated = (event: Event) => {
      const detail = (event as CustomEvent<typeof TENANT_INFO>).detail
      if (detail) setTenant(detail)
    }
    window.addEventListener('tenant-branding-updated', onBrandingUpdated)
    return () => window.removeEventListener('tenant-branding-updated', onBrandingUpdated)
  }, [])

  // Escape closes the transient chrome. Only bound while something is open so
  // we are not holding a document listener for the whole session.
  useEffect(() => {
    if (!showProfile && !showMobileSearch) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setShowProfile(false)
      setShowMobileSearch(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [showProfile, showMobileSearch])

  const closeMobileSearch = useCallback(() => setShowMobileSearch(false), [])

  // The mobile search sheet is `aria-modal="true"`, so it owes the keyboard the
  // same contract as any other dialog: focus moves in, Tab cycles inside it,
  // and the page behind it cannot scroll. Before 2026-08-16 it had none of
  // that -- only the shared Escape handler above. `autoFocus` is left on so the
  // search field takes focus, which is the whole point of opening the sheet.
  const mobileSearchRef = useDialogKeys<HTMLDivElement>(showMobileSearch, closeMobileSearch)
  const goSettings = useCallback(() => { setShowProfile(false); navigate('/settings') }, [navigate])

  const initial = user?.name?.[0]?.toUpperCase() || 'U'
  const roleLabel = user?.role ? user.role[0].toUpperCase() + user.role.slice(1) : ''

  return (
    <header className="topbar bg-white/95 backdrop-blur border-b border-slate-200/80 shadow-[0_1px_2px_rgba(15,23,42,0.04)] px-3 sm:px-4 md:px-6 h-16 flex items-center justify-between gap-2 sm:gap-3 sticky top-0 z-20">
      {/* Hamburger — mobile sidebar toggle. 44px touch target, aria-expanded
          reflects drawer state, click stopped from bubbling so the toggle is
          never swallowed by an outside-close listener. */}
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onToggleSidebar() }}
        aria-label={sidebarOpen ? 'Close menu' : 'Open menu'}
        aria-expanded={sidebarOpen}
        aria-controls="sidebar"
        className="lg:hidden min-w-[44px] min-h-[44px] w-11 h-11 -ml-1 rounded-lg flex items-center justify-center text-slate-600 hover:bg-slate-100 active:bg-slate-200 transition shrink-0 touch-manipulation"
      >
        <i className={`fa-solid ${sidebarOpen ? 'fa-xmark' : 'fa-bars'} text-lg`} />
      </button>

      {/* Compact brand lockup — below desktop, where the sidebar (and therefore
          the brand block) is hidden behind the drawer. Inline search is also
          hidden below lg so this lockup cannot collide with the search field. */}
      {/* TENANT BRANDING FIX (2026-08-18). Both the monogram and the label used
          to be hard-coded ("D" and the literal "Dogar"), so a partner office's
          staff saw ANOTHER company's name in their own portal chrome, while the
          Sidebar right next to it correctly rendered their own. The backend had
          already resolved this tenant's branding into window.__TENANT_BOOT__;
          nothing consumed it here. Falls back to Dogar when no tenant is
          resolved, so the root portal is unchanged. */}
      <div className="flex lg:hidden items-center gap-2 min-w-0 shrink">
        {tenant?.logo
          ? <img src={tenant.logo} alt="" width={26} height={26}
                 className="rounded-md shrink-0 object-contain" />
          : <span className="brand-tile brand-tile--sm text-[13px] font-black" aria-hidden="true">
              {brandLabel(tenant).charAt(0).toUpperCase()}
            </span>}
        <span className="font-extrabold text-[15px] tracking-tight text-slate-800 truncate"
              title={brandFullName(tenant)}>{brandLabel(tenant)}</span>
      </div>

      {/* Greeting — desktop only, where there is room beside the search */}
      <div className="hidden xl:block shrink-0 mr-4 leading-tight">
        <div className="text-[15px] font-bold text-slate-800">{greeting()}, {firstName}</div>
        <div className="text-[11px] text-slate-400">Here's what's happening today</div>
      </div>

      {/* Spacer keeps the right cluster pinned when the inline search is hidden */}
      <div className="flex-1 min-w-0 flex items-center justify-end sm:justify-start">
        <GlobalSearch />
      </div>

      {/* Right cluster */}
      <div className="flex items-center gap-1.5 sm:gap-3 relative shrink-0">
        {/* Mobile/tablet search trigger; inline search is desktop-only to avoid
            the logo/search collision visible on narrow mobile screenshots. */}
        <button
          type="button"
          onClick={() => setShowMobileSearch(true)}
          aria-label="Search candidates"
          className="lg:hidden min-w-[44px] min-h-[44px] w-11 h-11 rounded-lg flex items-center justify-center text-slate-600 hover:bg-slate-100 active:bg-slate-200 transition touch-manipulation"
        >
          <i className="fa-solid fa-magnifying-glass text-base" />
        </button>

        <NotificationBell />

        {/* Profile chip */}
        <div className="relative">
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setShowProfile((s) => !s) }}
            aria-haspopup="menu"
            aria-expanded={showProfile}
            aria-label="Account menu"
            className="flex items-center gap-2.5 cursor-pointer hover:bg-slate-50 active:bg-slate-100 rounded-full pl-1 pr-1.5 sm:pr-3 py-1 min-h-[44px] transition-colors touch-manipulation"
          >
            <span className="brand-gradient w-8 h-8 rounded-full text-white flex items-center justify-center font-semibold text-sm overflow-hidden shrink-0">
              {user?.photo_url ? <img src={user.photo_url} alt="" className="w-full h-full object-cover" /> : initial}
            </span>
            <span className="text-[13px] leading-tight text-left hidden md:block max-w-[160px] truncate">
              <span className="block font-semibold text-slate-800 truncate">{user?.name || 'Guest'}</span>
              <span className="block text-[11px] text-slate-500">{roleLabel}</span>
            </span>
            <i className="fa-solid fa-chevron-down text-[10px] text-slate-400 ml-0.5 hidden sm:block" />
          </button>

          {showProfile && createPortal(
            <>
              <div className="fixed inset-0 z-[90]" aria-hidden="true" onClick={() => setShowProfile(false)} />
              <div className="fixed inset-x-3 top-[4.5rem] sm:left-auto sm:right-4 sm:w-64 bg-white border border-slate-200 rounded-xl shadow-2xl z-[95] overflow-hidden" role="menu" aria-label="Account menu">
                <div className="px-4 py-3 border-b border-slate-100 brand-gradient-soft">
                  <div className="flex items-center gap-3">
                    <span className="brand-gradient w-12 h-12 rounded-full text-white flex items-center justify-center font-bold text-lg overflow-hidden shrink-0">
                      {user?.photo_url ? <img src={user.photo_url} alt="" className="w-full h-full object-cover" /> : initial}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="font-semibold text-slate-800 text-sm truncate">{user?.name || 'Guest'}</div>
                      <div className="text-[11px] text-slate-500 truncate">{user?.email || ''}</div>
                      {roleLabel && <div className="text-[10px] text-blue-700 font-semibold mt-0.5">{roleLabel}</div>}
                    </div>
                  </div>
                </div>
                <div className="py-1.5">
                  <MenuItem icon="fa-regular fa-user" label="My Profile" onClick={() => { setShowProfile(false); setShowAccount(true) }} />
                  <MenuItem icon="fa-solid fa-gear" label="Settings" onClick={goSettings} />
                  <MenuItem icon="fa-solid fa-key" label="Change Password" onClick={() => { setShowProfile(false); setShowChangePw(true) }} />
                  <div className="border-t border-slate-100 my-1" />
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => logout()}
                    className="w-full px-4 py-2.5 text-left text-sm text-red-600 hover:bg-red-50 flex items-center gap-3 min-h-[44px]"
                  >
                    <i className="fa-solid fa-right-from-bracket text-red-500 w-4" /> Sign out
                  </button>
                </div>
              </div>
            </>,
            document.body,
          )}
        </div>
      </div>

      {/* Mobile search sheet — portalled for the same backdrop-filter reason as
          the modals below. */}
      {showMobileSearch && createPortal(
        <div ref={mobileSearchRef} className="fixed inset-0 z-[100] lg:hidden" role="dialog" aria-modal="true" aria-label="Search">
          <div className="absolute inset-0 bg-slate-900/45" onClick={closeMobileSearch} aria-hidden="true" />
          <div className="relative bg-white border-b border-slate-200 shadow-xl px-3 pt-3 pb-4 pt-[max(0.75rem,env(safe-area-inset-top))]">
            <div className="flex items-center gap-2">
              <div className="flex-1 min-w-0">
                <GlobalSearch variant="sheet" onClose={closeMobileSearch} />
              </div>
              <button
                type="button"
                onClick={closeMobileSearch}
                aria-label="Close search"
                className="min-w-[44px] min-h-[44px] rounded-lg flex items-center justify-center text-slate-500 hover:bg-slate-100 shrink-0"
              >
                <i className="fa-solid fa-xmark text-lg" />
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}

      {showChangePw && (
        <ChangePasswordModal
          onClose={() => setShowChangePw(false)}
          onSuccess={() => { setShowChangePw(false); toast.show('Password changed successfully', 'success') }}
          onError={(m) => toast.show(m, 'error')}
        />
      )}

      {showAccount && (
        <AccountModal
          name={user?.name || 'User'}
          email={user?.email || ''}
          role={roleLabel}
          initial={initial}
          photoUrl={user?.photo_url || undefined}
          onClose={() => setShowAccount(false)}
          onChangePassword={() => { setShowAccount(false); setShowChangePw(true) }}
        />
      )}
    </header>
  )
}

export default memo(Topbar)

function MenuItem({ icon, label, onClick }: { icon: string; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className="w-full px-4 py-2.5 text-left text-sm text-slate-700 hover:bg-slate-50 flex items-center gap-3 min-h-[44px]"
    >
      <i className={`${icon} text-slate-400 w-4`} /> {label}
    </button>
  )
}

function ChangePasswordModal({ onClose, onSuccess, onError }: {
  onClose: () => void
  onSuccess: () => void
  onError: (msg: string) => void
}) {
  // AUDIT 2026-08-16: this dialog declared `aria-modal="true"` while having no
  // focus trap and no background scroll lock -- Tab walked straight out into
  // the page behind it and the body kept scrolling. The aria attribute was
  // therefore a lie to assistive technology. Escape was handled elsewhere in
  // this file, which is exactly the kind of partial, hand-rolled coverage the
  // shared hook exists to prevent.
  const cardRef = useDialogKeys<HTMLDivElement>(true, onClose)
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [saving, setSaving] = useState(false)
  const [show, setShow] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!current || !next) { onError('Please fill in all fields'); return }
    if (next.length < 12) { onError('New password must be at least 12 characters'); return }
    if (next !== confirm) { onError('New password and confirmation do not match'); return }
    if (next === current) { onError('New password must be different from the current one'); return }
    setSaving(true)
    try {
      await api.post('/users/me/change-password', { current_password: current, new_password: next })
      onSuccess()
    } catch (err) {
      onError(apiError(err, 'Could not change password'))
    } finally {
      setSaving(false)
    }
  }

  // PORTAL REQUIRED: Topbar's <header> uses `backdrop-blur`. `backdrop-filter`
  // turns the header into the CONTAINING BLOCK for position:fixed descendants
  // (CSS spec), so without a portal this modal's `fixed inset-0` collapses to
  // the 64px header strip. Rendering into document.body escapes that trap.
  return createPortal(
    <div className="fixed inset-0 z-[90] flex items-start sm:items-center justify-center bg-black/40 p-4 overflow-y-auto" onClick={onClose}>
      <div ref={cardRef} className="bg-white rounded-2xl shadow-2xl w-full max-w-md my-8 sm:my-0 max-h-[90vh] flex flex-col" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Change password">
        <div className="flex items-center justify-between px-5 sm:px-6 py-4 border-b border-slate-200 shrink-0">
          <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
            <i className="fa-solid fa-key text-blue-600" /> Change Password
          </h2>
          <button onClick={onClose} aria-label="Close" className="min-w-[40px] min-h-[40px] -mr-2 flex items-center justify-center text-slate-400 hover:text-slate-700"><i className="fa-solid fa-xmark text-xl" /></button>
        </div>
        <form onSubmit={submit} className="px-5 sm:px-6 py-5 space-y-4 overflow-y-auto">
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1" htmlFor="cpw-current">Current Password</label>
            <input id="cpw-current" type={show ? 'text' : 'password'} value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password"
              className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500" />
          </div>
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1" htmlFor="cpw-next">New Password</label>
            <input id="cpw-next" type={show ? 'text' : 'password'} value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password"
              className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500" />
            <p className="text-[11px] text-slate-400 mt-1">At least 12 characters.</p>
          </div>
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1" htmlFor="cpw-confirm">Confirm New Password</label>
            <input id="cpw-confirm" type={show ? 'text' : 'password'} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password"
              className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500" />
          </div>
          <label className="flex items-center gap-2 text-[12px] text-slate-500 cursor-pointer">
            <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} className="rounded border-slate-300 text-blue-600" />
            Show passwords
          </label>
          <div className="flex items-center justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2.5 text-sm font-semibold text-slate-600 hover:text-slate-800">Cancel</button>
            <button type="submit" disabled={saving} className="px-4 py-2.5 text-sm font-semibold rounded-lg brand-gradient text-white hover:opacity-95 disabled:opacity-60 inline-flex items-center gap-2">
              {saving ? <i className="fa-solid fa-spinner fa-spin" /> : <i className="fa-solid fa-check" />} Update Password
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}

function AccountModal({ name, email, role, initial, photoUrl, onClose, onChangePassword }: {
  name: string
  email: string
  role: string
  initial: string
  photoUrl?: string
  onClose: () => void
  onChangePassword: () => void
}) {
  // Same backdrop-filter containing-block trap as ChangePasswordModal — must
  // portal out of the <header> (see comment there).
  // Focus trap / scroll lock via the shared hook (AUDIT 2026-08-16, see the
  // note in ChangePasswordModal).
  const cardRef = useDialogKeys<HTMLDivElement>(true, onClose)
  return createPortal(
    <div className="fixed inset-0 z-[90] flex items-start sm:items-center justify-center bg-black/40 p-4 overflow-y-auto" onClick={onClose}>
      <div ref={cardRef} className="bg-white rounded-2xl shadow-2xl w-full max-w-md my-8 sm:my-0" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="My profile">
        <div className="flex items-center justify-between px-5 sm:px-6 py-4 border-b border-slate-200">
          <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
            <i className="fa-regular fa-user text-blue-600" /> My Profile
          </h2>
          <button onClick={onClose} aria-label="Close" className="min-w-[40px] min-h-[40px] -mr-2 flex items-center justify-center text-slate-400 hover:text-slate-700"><i className="fa-solid fa-xmark text-xl" /></button>
        </div>
        <div className="px-5 sm:px-6 py-6">
          <div className="flex items-center gap-4">
            <span className="brand-gradient w-16 h-16 rounded-full text-white flex items-center justify-center font-bold text-2xl overflow-hidden shrink-0">
              {photoUrl ? <img src={photoUrl} alt="" className="w-full h-full object-cover" /> : initial}
            </span>
            <div className="min-w-0">
              <div className="text-lg font-bold text-slate-900 truncate">{name}</div>
              {role && <div className="text-xs font-semibold text-blue-700 mt-0.5">{role}</div>}
            </div>
          </div>
          <dl className="mt-5 space-y-3 text-sm">
            <div className="flex items-start gap-3">
              <dt className="w-24 text-slate-400 font-medium shrink-0"><i className="fa-regular fa-envelope mr-1.5" />Email</dt>
              <dd className="text-slate-800 min-w-0 break-words">{email || '—'}</dd>
            </div>
            <div className="flex items-start gap-3">
              <dt className="w-24 text-slate-400 font-medium shrink-0"><i className="fa-solid fa-user-shield mr-1.5" />Role</dt>
              <dd className="text-slate-800">{role || '—'}</dd>
            </div>
          </dl>
        </div>
        <div className="flex items-center justify-end gap-3 px-5 sm:px-6 py-4 border-t border-slate-100 bg-slate-50 rounded-b-2xl">
          <button type="button" onClick={onClose} className="px-4 py-2.5 text-sm font-semibold text-slate-600 hover:text-slate-800">Close</button>
          <button type="button" onClick={onChangePassword} className="px-4 py-2.5 text-sm font-semibold rounded-lg brand-gradient text-white hover:opacity-95 inline-flex items-center gap-2">
            <i className="fa-solid fa-key" /> Change Password
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
