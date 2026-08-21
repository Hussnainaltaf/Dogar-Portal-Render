import { createContext, useContext, useEffect, useMemo, useState, ReactNode, useCallback } from 'react'
import { api, TENANT_BASE } from '@/lib/api'

export interface CurrentUser {
  id: number
  name: string
  email: string
  username?: string
  role: string
  photo_url?: string | null
  must_change_password?: boolean
}

interface AuthState {
  user: CurrentUser | null
  loading: boolean
  /** Effective permission list from the backend ("*" = admin wildcard). */
  permissions: string[]
  /** True if the user holds `perm` (or the "*" wildcard). */
  can: (perm: string) => boolean
  refresh: () => Promise<void>
  setUser: (u: CurrentUser | null) => void
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  permissions: [],
  can: () => false,
  refresh: async () => {},
  setUser: () => {},
  logout: async () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [permissions, setPermissions] = useState<string[]>([])
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    // v13 — these two calls used to run SEQUENTIALLY, so the very first paint
    // of every session waited for two full round-trips. They are independent
    // (both are just "who am I"), so they now run in parallel and the boot
    // cost drops to a single round-trip. `allSettled` keeps the original
    // semantics: permissions are best-effort, and only a failing /users/me
    // clears the session.
    try {
      const [meRes, permRes] = await Promise.allSettled([
        api.get<CurrentUser>('/users/me'),
        api.get<{ permissions: string[] }>('/users/me/permissions'),
      ])

      if (meRes.status !== 'fulfilled') {
        setUser(null)
        setPermissions([])
        return
      }

      setUser(meRes.value.data)
      setPermissions(permRes.status === 'fulfilled' ? permRes.value.data?.permissions || [] : [])
    } finally {
      setLoading(false)
    }
  }, [])

  const logout = useCallback(async () => {
    try {
      await api.post('/auth/logout')
    } catch {
      /* ignore */
    }
    setUser(null)
    setPermissions([])
    // Keep the tenant base so a copy portal logs out to ITS OWN login.
    window.location.href = `${TENANT_BASE}/login`
  }, [])

  const can = useCallback(
    (perm: string) => {
      if (!user) return false
      if ((user.role || '').toLowerCase() === 'admin') return true
      if (permissions.includes('*')) return true
      return permissions.includes(perm)
    },
    [user, permissions],
  )

  useEffect(() => {
    void refresh()
  }, [refresh])

  // Stable identity for the context value. Without this, every AuthProvider
  // render handed consumers a brand-new object and re-rendered the entire
  // authenticated tree (sidebar, topbar, current page) even when nothing about
  // the user had actually changed.
  const value = useMemo<AuthState>(
    () => ({ user, loading, permissions, can, refresh, setUser, logout }),
    [user, loading, permissions, can, refresh, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  return useContext(AuthContext)
}
