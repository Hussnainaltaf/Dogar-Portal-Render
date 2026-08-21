import { ReactNode } from 'react'
import { useAuth } from './AuthContext'
import Unauthorized from '@/pages/Unauthorized'

/**
 * Route-level authorization gate. Renders its children only if the current
 * user holds ALL (mode="all", default) or ANY (mode="any") of `perms`. When
 * access is denied it renders the <Unauthorized> page (in place, preserving
 * the attempted URL) explaining exactly which permission(s)/role(s) are
 * required. Prevents privilege leaks on gated routes such as
 * /admin/audit-log, /settings/general and /protector-letter.
 *
 * `roles` is an optional human-readable list shown on the Unauthorized page
 * (e.g. ["Admin"]) so the user knows who to ask for access.
 */
export default function RequirePermission({
  perms,
  mode = 'all',
  roles,
  children,
}: {
  perms: string[]
  mode?: 'all' | 'any'
  roles?: string[]
  children: ReactNode
}) {
  const { can, loading } = useAuth()

  if (loading) {
    return (
      <div className="spa-loader">
        <i className="fa-solid fa-spinner fa-spin text-3xl text-blue-600" />
      </div>
    )
  }

  const list = perms || []
  const ok =
    list.length === 0 ||
    (mode === 'any' ? list.some((p) => can(p)) : list.every((p) => can(p)))

  if (!ok) {
    return <Unauthorized requiredPerms={list} requiredRoles={roles} />
  }
  return <>{children}</>
}
