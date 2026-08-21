import axios, { AxiosError } from 'axios'

// ---------------------------------------------------------------------------
// Multi-tenant path-mode base.
// When a tenant copy portal is opened via /t/<slug>/..., the backend injects
// window.__TENANT_BOOT__ = { base: "/t/<slug>", tenant: {...} }. Every API call
// must be prefixed with that base so the request routes back through the tenant
// resolver middleware (which strips /t/<slug> and binds the tenant DB). For the
// root portal (or host/subdomain access) base is "" and behaviour is unchanged.
// ---------------------------------------------------------------------------
export interface TenantInfo {
  slug: string
  name: string
  shortName: string
  color: string
  /**
   * Typeface KEY (not a font-family declaration) -- resolved against the
   * FONT_STACKS allow-list in lib/branding.ts.
   *
   * Declared here so the compiler enforces the contract. branding.ts previously
   * had to reach for it as `(tenant as any).fontKey` precisely because it was
   * absent from this interface, and that cast is what let the field stay
   * missing from the boot payload for as long as it did without any build
   * error: tsc cannot check a property nobody declared.
   *
   * Optional: an un-migrated tenant row omits it and the resolver falls back to
   * the system stack.
   */
  fontKey?: string
  logo: string
  officeName: string
  subtitle?: string
  licenseNumber?: string
  officeAddress?: string
  contactPhone?: string
  contactEmail?: string
}
interface TenantBoot {
  base: string
  tenant: null | TenantInfo
}
const BOOT: TenantBoot = (typeof window !== 'undefined' && (window as any).__TENANT_BOOT__) || { base: '', tenant: null }
export const TENANT_BASE: string = BOOT.base || ''
export const TENANT_INFO = BOOT.tenant

// Single axios instance. The FastAPI backend issues an httponly `access_token`
// cookie (samesite=lax) on login, so we just rely on the browser sending it
// automatically — no Authorization header juggling needed.
export const api = axios.create({
  baseURL: `${TENANT_BASE}/api`,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
})

// Read a non-HttpOnly cookie value by name (used for the CSRF token).
function readCookie(name: string): string {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/([.$?*|{}()[\]\\/+^])/g, '\\$1') + '=([^;]*)'))
  return m ? decodeURIComponent(m[1]) : ''
}

// CSRF double-submit: the backend (CsrfMiddleware) sets a non-HttpOnly
// `csrf_token` cookie on page loads and enforces a matching `X-CSRF-Token`
// header on every state-changing API request (POST/PUT/DELETE/PATCH).
// Without this, those calls 403 with "CSRF token mismatch". Replay the cookie
// value as the header on every mutating request.
api.interceptors.request.use((config) => {
  const method = (config.method || 'get').toUpperCase()
  if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
    const token = readCookie('csrf_token')
    if (token) {
      config.headers = config.headers || {}
      ;(config.headers as Record<string, string>)['X-CSRF-Token'] = token
    }
  }
  return config
})

// Public routes that intentionally do NOT require authentication. If a 401
// bubbles out of an API call while the browser is on one of these pages, we
// must NOT force-redirect to /login — the page itself is public (login screen,
// password reset flow, contact support, etc.) and the 401 is an expected
// side-effect of the AuthProvider's background /users/me probe.
//
// (This was the root cause of the "blank Login/Forgot/Contact" bug on 2026-07-08:
//  visiting /forgot-password would fire /users/me → 401 → interceptor → hard
//  redirect to /login?next=/forgot-password, so the user never actually saw the
//  Forgot Password page.)
const PUBLIC_PATH_PREFIXES = [
  '/login',
  '/forgot-password',
  '/reset-password',
  '/change-password',
  '/contact-support',
]
function isOnPublicPath(): boolean {
  // CRITICAL: strip the tenant base first. On a copy portal the browser path is
  // `/t/<slug>/login`, but PUBLIC_PATH_PREFIXES are base-relative (`/login`).
  // Without stripping TENANT_BASE the login page would fail this check, the 401
  // from the AuthProvider's /users/me probe would trigger a hard redirect back
  // to /t/<slug>/login, and the whole thing would reload-loop forever — the user
  // could never type credentials. (Root portal has base="" so this is a no-op.)
  let p = window.location.pathname
  if (TENANT_BASE && (p === TENANT_BASE || p.startsWith(TENANT_BASE + '/'))) {
    p = p.slice(TENANT_BASE.length) || '/'
  }
  return PUBLIC_PATH_PREFIXES.some((prefix) => p === prefix || p.startsWith(prefix + '/'))
}

// API endpoints that are *silent auth probes* — the AuthProvider fires these on
// every mount to discover the current session. A 401 from them is normal and
// expected (it just means "not logged in yet"); it must NEVER trigger a redirect,
// or a login/public page would bounce on itself. This is the second, url-based
// guard that backs up isOnPublicPath().
const SILENT_AUTH_PROBES = ['/users/me', '/users/me/permissions']
function isSilentAuthProbe(url: string): boolean {
  return SILENT_AUTH_PROBES.some((p) => url === p || url.endsWith(p))
}

// Global 401 handling: bounce to /login when a protected page's API call
// receives a 401 (expired session, revoked token, etc.). Public pages and the
// background auth probes are exempt — they show their own UI regardless of
// session state.
api.interceptors.response.use(
  (res) => res,
  (err: AxiosError) => {
    const status = err.response?.status
    const url = err.config?.url || ''
    if (
      status === 401 &&
      !url.includes('/auth/login') &&
      !isSilentAuthProbe(url) &&
      !isOnPublicPath()
    ) {
      // Preserve where the user was trying to go. Keep the tenant base so a
      // copy portal bounces to ITS OWN login (/t/<slug>/login), not the root.
      const next = encodeURIComponent(window.location.pathname + window.location.search)
      window.location.href = `${TENANT_BASE}/login?next=${next}`
    }
    return Promise.reject(err)
  },
)

export interface ApiValidationResult {
  fieldErrors: Record<string, string>
  formError?: string
}

/** Normalize FastAPI/Pydantic and Zod-style validation payloads.
 * Field paths are reduced to the wizard's stable dot notation, e.g.
 * `body.positions.0.trade` becomes `positions.0.trade`. */
export function apiValidation(err: unknown): ApiValidationResult {
  if (!axios.isAxiosError(err)) return { fieldErrors: {} }
  const data = err.response?.data as any
  const issues = Array.isArray(data?.detail)
    ? data.detail
    : Array.isArray(data?.issues)
      ? data.issues
      : Array.isArray(data?.error?.issues)
        ? data.error.issues
        : []
  const fieldErrors: Record<string, string> = {}
  for (const issue of issues) {
    const raw = Array.isArray(issue?.loc) ? issue.loc : Array.isArray(issue?.path) ? issue.path : []
    const path = raw.map(String).filter((part: string) => !['body', 'query', 'path'].includes(part)).join('.')
    const message = String(issue?.msg || issue?.message || '').trim()
    if (path && message && !fieldErrors[path]) fieldErrors[path] = message
  }
  const nested = data?.fieldErrors || data?.error?.fieldErrors
  if (nested && typeof nested === 'object') {
    Object.entries(nested).forEach(([path, value]) => {
      const message = Array.isArray(value) ? value[0] : value
      if (message && !fieldErrors[path]) fieldErrors[path] = String(message)
    })
  }
  const stringDetail = typeof data?.detail === 'string' ? data.detail : undefined
  const formError = data?.message || data?.error?.message || stringDetail
  return { fieldErrors, formError: typeof formError === 'string' ? formError : undefined }
}

// Helper: extract a human-readable error message from an axios error.
export function apiError(err: unknown, fallback = 'Something went wrong'): string {
  if (axios.isAxiosError(err)) {
    const validation = apiValidation(err)
    if (validation.formError) return validation.formError
    const first = Object.values(validation.fieldErrors)[0]
    if (first) return first
    if (err.message) return err.message
  }
  return fallback
}

// ── Messaging (real WhatsApp / SMS via Twilio — Task 9) ────────────────────
export type MessageChannel = 'sms' | 'whatsapp'

export interface MessagingStatus {
  configured: boolean
  sms_enabled: boolean
  whatsapp_enabled: boolean
  sms_from: string | null
  whatsapp_from: string | null
  mode: 'live' | 'dry_run'
}

export interface MessageResult {
  id: number
  ok: boolean
  channel: MessageChannel
  to: string
  status: string
  provider_sid: string | null
  error: string | null
  created_at: string | null
}

export async function getMessagingStatus(): Promise<MessagingStatus> {
  const { data } = await api.get<MessagingStatus>('/messaging/status')
  return data
}

export async function sendMessage(params: {
  to: string
  body: string
  channel: MessageChannel
  candidate_id?: number
}): Promise<MessageResult> {
  const { data } = await api.post<MessageResult>('/messaging/send', params)
  return data
}

export async function getCandidateMessageLogs(candidateId: number): Promise<MessageResult[]> {
  const { data } = await api.get<MessageResult[]>(`/messaging/candidate/${candidateId}/logs`)
  return data
}
