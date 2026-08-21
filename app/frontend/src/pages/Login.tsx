import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import { api, apiError, TENANT_INFO } from '@/lib/api'
import { useAuth } from '@/auth/AuthContext'

// FIX 2026-07-21 (CSP + UX #3) — The `.login-page` scoped styles previously
// lived here as an inline `<style>{LOGIN_CSS}</style>` block. Under our
// strict `style-src-elem 'self' 'nonce-…'` CSP, injected <style> elements
// without a nonce are BLOCKED — which is exactly what happened: the
// stylesheet dropped, the office background image + frosted-card styling
// vanished, and the naked signin card rendered as a bare white rectangle
// on the panel-right background (the reported visual bug).
//
// The rules now live in `src/index.css` (which Vite bundles into
// /static/spa/assets/*.css — same origin, covered by
// `style-src-elem 'self'`, no nonce needed). See the "Login / Auth pages"
// section of index.css. Identical rules also cover `.auth-page` so the
// two files stop duplicating each other verbatim (UX #3 resolved).

export default function Login() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { user, setUser } = useAuth()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(false)
  const [showPwd, setShowPwd] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  // Per-field validation feedback + whether the field has been "touched"
  // (blurred or submitted) so we never nag the user before they interact.
  const [fieldErr, setFieldErr] = useState<{ username?: string; password?: string }>({})
  const [touched, setTouched] = useState<{ username?: boolean; password?: boolean }>({})

  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
  const validate = (u: string, p: string) => {
    const errs: { username?: string; password?: string } = {}
    const uu = u.trim()
    if (!uu) errs.username = 'Email address is required.'
    else if (!EMAIL_RE.test(uu)) errs.username = 'Enter a valid email address (e.g. you@company.com).'
    if (!p) errs.password = 'Password is required.'
    return errs
  }

  // Already logged in → bounce to next/dashboard.
  useEffect(() => {
    if (user) navigate(params.get('next') || '/dashboard', { replace: true })
  }, [user, navigate, params])

  // Pre-fill remembered username.
  useEffect(() => {
    const remembered = localStorage.getItem('dtc_remember_username')
    if (remembered) { setUsername(remembered); setRemember(true) }
  }, [])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    // Client-side validation first — surface clear per-field messages instead
    // of a round-trip that returns a generic "invalid credentials".
    const errs = validate(username, password)
    setTouched({ username: true, password: true })
    setFieldErr(errs)
    if (errs.username || errs.password) {
      // Focus the first offending field for keyboard users.
      const sel = errs.username ? 'input[autocomplete="username"]' : 'input[type="password"]'
      ;(document.querySelector(sel) as HTMLInputElement | null)?.focus()
      return
    }
    setLoading(true)
    try {
      const { data } = await api.post('/auth/login', { username: username.trim(), password })
      if (remember) localStorage.setItem('dtc_remember_username', username)
      else localStorage.removeItem('dtc_remember_username')
      setUser(data.user)
      if (data.user?.must_change_password) navigate('/change-password', { replace: true })
      else navigate(params.get('next') || '/dashboard', { replace: true })
    } catch (err) {
      setError(apiError(err, 'Invalid username or password'))
    } finally {
      setLoading(false)
    }
  }

  const brandName = TENANT_INFO?.name || 'Dogar Trading Corporation'
  const brandSubtitle = TENANT_INFO?.subtitle || TENANT_INFO?.officeName || 'Overseas Employment Portal'
  const brandLogo = TENANT_INFO?.logo || '/static/img/logo.png'
  const brandLogoSm = TENANT_INFO?.logo || '/static/img/logo-sm.png'

  return (
    <div className="login-page min-h-screen text-slate-800 flex flex-col">
      {/* Styles for .login-page live in src/index.css (bundled to
          /static/spa/assets/*.css). See note at the top of this file. */}
      <div className="flex flex-1 min-h-[calc(100vh-44px)]">
        {/* Left panel (desktop only). Hidden below lg so tablet/mobile Chrome
            cannot squeeze a half-width login form beside the photo wall. */}
        <div className="hidden lg:block lg:w-1/2 panel-left">
          <div className="brand-badge">
            <img src={brandLogoSm} alt={brandName} className="h-9 w-auto object-contain" />
            <div>
              <div className="text-[13px] font-bold text-slate-800 leading-tight">{brandName}</div>
              <div className="text-[11px] text-slate-500">{brandSubtitle}</div>
            </div>
          </div>
        </div>

        {/* Right panel */}
        <div className="w-full lg:w-1/2 panel-right flex flex-col">
          <div className="mobile-banner lg:hidden">
            <div className="logo-wrap text-center">
              <img src={brandLogo} alt={brandName} className="w-28 h-auto object-contain drop-shadow-lg mx-auto" />
              <div className="mt-2 text-sm font-bold text-white drop-shadow">{brandName}</div>
              <div className="text-[11px] text-white/80">{brandSubtitle}</div>
            </div>
          </div>

          <div className="flex-1 flex items-center justify-center px-5 sm:px-10 py-10">
            <div className="signin-card w-full max-w-[420px] px-8 py-9">
              <div className="mb-7">
                <div className="flex items-center gap-2.5 mb-4">
                  {/* Accent surfaces go through .auth-accent* (defined in
                      index.css) rather than Tailwind's blue utilities. A
                      `bg-blue-600` class selector cannot be re-skinned by any
                      token write, which is why a #ff0000 office still had a
                      blue chip and a blue button here. */}
                  <div className="w-9 h-9 rounded-xl auth-accent-tile flex items-center justify-center">
                    <i className="fa-solid fa-right-to-bracket text-sm" />
                  </div>
                  <span className="text-[11.5px] font-semibold auth-accent tracking-wide uppercase">Secure Sign In</span>
                </div>
                <h1 className="text-[26px] leading-tight font-bold text-slate-900">Welcome back</h1>
                <p className="text-[13.5px] text-slate-500 mt-1">Enter your credentials to access your account</p>
              </div>

              <div className="divider mb-6" />

              <form className="space-y-5" autoComplete="on" onSubmit={submit}>
                <div>
                  <label htmlFor="login-email" className="block text-[13px] font-semibold text-slate-700 mb-1.5">Email Address</label>
                  <div className="relative">
                    <span className={`absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none ${touched.username && fieldErr.username ? 'text-red-400' : 'text-slate-400'}`}>
                      <i className="fa-regular fa-envelope text-[13px]" />
                    </span>
                    <input
                      id="login-email"
                      type="email"
                      required
                      autoComplete="username"
                      inputMode="email"
                      aria-invalid={!!(touched.username && fieldErr.username)}
                      aria-describedby={touched.username && fieldErr.username ? 'login-email-err' : undefined}
                      value={username}
                      onChange={(e) => { setUsername(e.target.value); if (touched.username) setFieldErr(validate(e.target.value, password)) }}
                      onBlur={() => { setTouched(t => ({ ...t, username: true })); setFieldErr(validate(username, password)) }}
                      className={`field-input w-full pl-10 pr-4 py-2.5 text-sm border rounded-xl focus:outline-none ${touched.username && fieldErr.username ? 'border-red-300 focus:border-red-500' : 'border-slate-200'}`}
                      placeholder="you@company.com" />
                  </div>
                  {touched.username && fieldErr.username && (
                    <p id="login-email-err" className="mt-1.5 text-[12px] text-red-600 flex items-center gap-1.5">
                      <i className="fa-solid fa-circle-exclamation text-[11px]" />{fieldErr.username}
                    </p>
                  )}
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-[13px] font-semibold text-slate-700">Password</label>
                    <Link to="/forgot-password" className="text-[12.5px] auth-accent hover:underline font-medium">Forgot password?</Link>
                  </div>
                  <div className="relative">
                    <span className={`absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none ${touched.password && fieldErr.password ? 'text-red-400' : 'text-slate-400'}`}>
                      <i className="fa-solid fa-lock text-[13px]" />
                    </span>
                    <input
                      id="login-password"
                      type={showPwd ? 'text' : 'password'}
                      required
                      autoComplete="current-password"
                      aria-invalid={!!(touched.password && fieldErr.password)}
                      aria-describedby={touched.password && fieldErr.password ? 'login-password-err' : undefined}
                      value={password}
                      onChange={(e) => { setPassword(e.target.value); if (touched.password) setFieldErr(validate(username, e.target.value)) }}
                      onBlur={() => { setTouched(t => ({ ...t, password: true })); setFieldErr(validate(username, password)) }}
                      className={`field-input w-full pl-10 pr-11 py-2.5 text-sm border rounded-xl focus:outline-none ${touched.password && fieldErr.password ? 'border-red-300 focus:border-red-500' : 'border-slate-200'}`}
                      placeholder="••••••••" />
                    <button type="button" tabIndex={-1} onClick={() => setShowPwd((s) => !s)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-700 p-1 rounded" title="Show / hide password" aria-label={showPwd ? 'Hide password' : 'Show password'}>
                      <i className={`fa-regular ${showPwd ? 'fa-eye-slash' : 'fa-eye'} text-[13px]`} />
                    </button>
                  </div>
                  {touched.password && fieldErr.password && (
                    <p id="login-password-err" className="mt-1.5 text-[12px] text-red-600 flex items-center gap-1.5">
                      <i className="fa-solid fa-circle-exclamation text-[11px]" />{fieldErr.password}
                    </p>
                  )}
                </div>

                <label className="flex items-center gap-2.5 text-[13px] text-slate-600 cursor-pointer select-none">
                  <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)}
                    className="w-4 h-4 rounded border-slate-300 auth-accent-input" />
                  Keep me signed in
                </label>

                {error && (
                  <div role="alert" aria-live="assertive" className="auth-alert text-[13px] text-red-700 bg-red-50 border border-red-200 rounded-xl p-3 flex items-start gap-2.5">
                    <i className="fa-solid fa-circle-exclamation mt-0.5 shrink-0 text-red-500" />
                    <div>
                      <div className="font-semibold">Sign in failed</div>
                      <div className="text-red-600/90">{error}</div>
                    </div>
                  </div>
                )}

                <button type="submit" disabled={loading}
                  className="btn-signin w-full py-3 font-semibold text-sm rounded-xl disabled:opacity-60 disabled:cursor-not-allowed">
                  {loading ? <><i className="fa-solid fa-spinner fa-spin mr-1.5" /> Signing in…</> : <><i className="fa-solid fa-right-to-bracket mr-1.5" /> Sign In</>}
                </button>

                <div className="text-center text-[12.5px] text-slate-500">
                  Need help?
                  <Link to="/contact-support" className="auth-accent hover:underline font-semibold ml-0.5">Contact Support</Link>
                </div>

                <div className="divider" />
                <div className="flex items-center justify-between text-[11.5px] text-slate-400 pt-1">
                  <a href="/privacy" className="hover:text-slate-600 transition-colors">Privacy Policy</a>
                  <span className="inline-flex items-center gap-1">
                    <span>Built by</span>
                    <img src="/static/img/dev_logo_micro.png" alt="HussnainTechVertex" className="h-3.5 w-3.5 object-contain" />
                    <span className="font-semibold text-slate-500">HussnainTechVertex</span>
                  </span>
                </div>
              </form>
            </div>
          </div>
        </div>
      </div>

      <footer className="px-6 py-3 flex items-center justify-center gap-2 text-[12px] text-slate-500 bg-white border-t border-slate-200">
        <span>&copy; {new Date().getFullYear()} {brandName} &middot; Powered by</span>
        <span className="inline-flex items-center gap-1.5">
          <img src="/static/img/dev_logo_icon.png" alt="HussnainTechVertex" className="h-5 w-5 object-contain" style={{ filter: 'drop-shadow(0 1px 1px rgba(0,0,0,0.08))' }} />
          <span className="font-semibold text-slate-700">HussnainTechVertex Pvt Ltd.</span>
        </span>
      </footer>
    </div>
  )
}
