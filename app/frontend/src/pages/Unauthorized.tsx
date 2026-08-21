import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '@/auth/AuthContext'

/** Friendly permission labels for the common gated permissions, so the page
 *  reads in plain English instead of raw "audit:view" tokens. */
const PERM_LABELS: Record<string, string> = {
  'audit:view': 'View audit log',
  'settings:view': 'View settings',
  'settings:company': 'Edit company settings',
  'settings:branding': 'Manage branding',
  'settings:tenants': 'Manage tenant portals',
  'protector:view': 'View protector letters',
  'users:view': 'Manage users',
}

export default function Unauthorized({
  requiredPerms = [],
  requiredRoles,
}: {
  requiredPerms?: string[]
  requiredRoles?: string[]
}) {
  const navigate = useNavigate()
  const location = useLocation()
  const { user } = useAuth()

  // The path the user was trying to reach. When this component is rendered by
  // a route guard, that is simply the current location.
  const attemptedPath =
    (location.state as { from?: string })?.from || location.pathname + location.search

  const prettyPerms = requiredPerms.map((p) => PERM_LABELS[p] || p)

  return (
    <section
      id="unauthorized-page"
      role="alert"
      aria-labelledby="unauthorized-title"
      className="min-h-[60vh] flex items-center justify-center px-4"
    >
      <div className="w-full max-w-lg bg-white border border-slate-200 rounded-2xl shadow-sm p-8 text-center">
        <div className="mx-auto w-16 h-16 rounded-full bg-rose-50 flex items-center justify-center mb-5">
          <i className="fa-solid fa-lock text-2xl text-rose-500" aria-hidden="true" />
        </div>

        <h1 id="unauthorized-title" className="text-xl font-bold text-slate-800 mb-2">
          Access denied
        </h1>
        <p className="text-sm text-slate-500 mb-5">
          You don&rsquo;t have permission to view this page
          {user?.role ? (
            <>
              {' '}
              (you&rsquo;re signed in as <span className="font-semibold text-slate-700 capitalize">{user.role}</span>).
            </>
          ) : (
            '.'
          )}
        </p>

        {/* Attempted path — preserved and shown so the user (and support) know
            exactly what was blocked. */}
        <div
          className="text-left bg-slate-50 border border-slate-200 rounded-lg px-4 py-3 mb-4 text-sm"
          data-testid="attempted-path"
        >
          <div className="text-[11px] uppercase tracking-wide text-slate-400 font-semibold mb-1">
            Attempted page
          </div>
          <code className="text-slate-700 break-all">{attemptedPath}</code>
        </div>

        {/* Exact required role(s) / permission(s). */}
        {(requiredRoles?.length || prettyPerms.length) ? (
          <div
            className="text-left bg-amber-50 border border-amber-200 rounded-lg px-4 py-3 mb-6 text-sm"
            data-testid="required-access"
          >
            <div className="text-[11px] uppercase tracking-wide text-amber-600 font-semibold mb-1">
              Required access
            </div>
            {requiredRoles?.length ? (
              <div className="text-amber-800">
                Role:{' '}
                {requiredRoles.map((r, i) => (
                  <span key={r} className="font-semibold">
                    {r}
                    {i < requiredRoles.length - 1 ? ', ' : ''}
                  </span>
                ))}
              </div>
            ) : null}
            {prettyPerms.length ? (
              <ul className="mt-1 list-disc list-inside text-amber-800">
                {prettyPerms.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <button
            type="button"
            onClick={() => navigate('/dashboard')}
            className="inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-lg bg-blue-600 text-white font-semibold hover:bg-blue-700 transition"
          >
            <i className="fa-solid fa-house" aria-hidden="true" /> Go to home
          </button>
          <button
            type="button"
            onClick={() => navigate(attemptedPath)}
            className="inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-lg bg-white border border-slate-300 text-slate-700 font-semibold hover:bg-slate-50 transition"
          >
            <i className="fa-solid fa-rotate-right" aria-hidden="true" /> Try again
          </button>
        </div>

        <p className="mt-5 text-xs text-slate-400">
          If you believe this is a mistake, contact your administrator to request access.
        </p>
      </div>
    </section>
  )
}
