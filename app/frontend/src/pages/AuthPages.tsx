import { useState } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import { api, apiError, TENANT_INFO } from '@/lib/api'
import { useToast } from '@/components/Toast'
import { useAuth } from '@/auth/AuthContext'

// FIX 2026-07-21 (CSP + UX #3) — The `.auth-page` scoped styles previously
// lived here as an inline `<style>{AUTH_CSS}</style>` block, and were a
// verbatim duplicate of Login.tsx's `LOGIN_CSS` apart from the class
// prefix. Under our strict `style-src-elem 'self' 'nonce-…'` CSP the
// nonce-less inline <style> block was BLOCKED, which broke the auth
// pages' layout identically to the login page.
//
// Both blocks are now consolidated into a single self-hosted stylesheet
// (see the "Login / Auth pages" section of `src/index.css`). That file
// is bundled by Vite into `/static/spa/assets/*.css` — same origin, so
// it satisfies `style-src-elem 'self'` without any nonce plumbing.

// Shared brand-consistent shell — split office panel on left + frosted card on right.
function AuthShell({
  title,
  subtitle,
  icon,
  eyebrow,
  cardWidth = 420,
  children,
}: {
  title: string
  subtitle?: string
  icon: string
  eyebrow?: string
  cardWidth?: number
  children: React.ReactNode
}) {
  const brandName = TENANT_INFO?.name || 'Dogar Trading Corporation'
  const brandSubtitle = TENANT_INFO?.subtitle || TENANT_INFO?.officeName || 'Overseas Employment Portal'
  const brandLogo = TENANT_INFO?.logo || '/static/img/logo.png'
  const brandLogoSm = TENANT_INFO?.logo || '/static/img/logo-sm.png'

  return (
    <div className="auth-page min-h-screen text-slate-800 flex flex-col">
      {/* Styles for .auth-page live in src/index.css (bundled to
          /static/spa/assets/*.css). See note at the top of this file. */}
      <div className="flex flex-1 min-h-[calc(100vh-44px)]">
        {/* Left panel (desktop only) — hidden below lg to keep forms full-width
            on phones/tablets and mobile browsers requesting desktop layout. */}
        <div className="hidden lg:block lg:w-1/2 panel-left">
          <div className="brand-badge">
            <img src={brandLogoSm} alt={brandName} className="h-9 w-auto object-contain" />
            <div>
              <div className="text-[13px] font-bold text-slate-800 leading-tight">{brandName}</div>
              <div className="text-[11px] text-slate-500">{brandSubtitle}</div>
            </div>
          </div>
        </div>

        {/* Right panel — frosted card */}
        <div className="w-full lg:w-1/2 panel-right flex flex-col">
          <div className="mobile-banner lg:hidden">
            <div className="logo-wrap text-center">
              <img src={brandLogo} alt={brandName} className="w-28 h-auto object-contain drop-shadow-lg mx-auto" />
              <div className="mt-2 text-sm font-bold text-white drop-shadow">{brandName}</div>
              <div className="text-[11px] text-white/80">{brandSubtitle}</div>
            </div>
          </div>

          <div className="flex-1 flex items-center justify-center px-5 sm:px-10 py-10">
            <div className="signin-card w-full px-8 py-9" style={{ maxWidth: `${cardWidth}px` }}>
              <div className="mb-7">
                <div className="flex items-center gap-2.5 mb-4">
                  <div className="w-9 h-9 rounded-xl auth-accent-tile flex items-center justify-center">
                    <i className={`${icon} text-white text-sm`} />
                  </div>
                  {eyebrow && (
                    <span className="text-[11.5px] font-semibold auth-accent tracking-wide uppercase">{eyebrow}</span>
                  )}
                </div>
                <h1 className="text-[26px] leading-tight font-bold text-slate-900">{title}</h1>
                {subtitle && <p className="text-[13.5px] text-slate-500 mt-1">{subtitle}</p>}
              </div>

              <div className="divider mb-6" />

              {children}

              <div className="divider mt-6" />
              <div className="flex items-center justify-between text-[11.5px] text-slate-400 pt-2">
                <a href="/privacy" className="hover:text-slate-600 transition-colors">Privacy Policy</a>
                <span className="inline-flex items-center gap-1">
                  <span>Built by</span>
                  <img src="/static/img/dev_logo_micro.png" alt="HussnainTechVertex" className="h-3.5 w-3.5 object-contain" />
                  <span className="font-semibold text-slate-500">HussnainTechVertex</span>
                </span>
              </div>
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

export function ForgotPassword() {
  const toast = useToast()
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [loading, setLoading] = useState(false)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setLoading(true)
    try { await api.post('/auth/forgot-password', { email }); setSent(true) }
    catch (err) { toast.show(apiError(err), 'error') } finally { setLoading(false) }
  }
  return (
    <AuthShell
      title="Forgot Password"
      subtitle="Enter your account email and we'll generate a one-time reset link."
      icon="fa-solid fa-key"
      eyebrow="Password Reset"
    >
      {sent ? (
        <div className="text-center py-4">
          <i className="fa-solid fa-circle-check text-emerald-500 text-4xl mb-3" />
          <p className="text-sm text-slate-600 mb-4">If that email exists, a reset link has been sent.</p>
          <Link to="/login" className="auth-accent hover:underline text-[13px] font-semibold">
            <i className="fa-solid fa-arrow-left mr-1" />Back to Sign In
          </Link>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-5" autoComplete="on">
          <div>
            <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">Email Address</label>
            <div className="relative">
              <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none">
                <i className="fa-regular fa-envelope text-[13px]" />
              </span>
              <input
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="field-input w-full pl-10 pr-4 py-2.5 text-sm border border-slate-200 rounded-xl"
                placeholder="you@company.com"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="btn-signin w-full py-3 font-semibold text-sm rounded-xl disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {loading ? <><i className="fa-solid fa-spinner fa-spin mr-1.5" /> Sending…</> : <><i className="fa-solid fa-paper-plane mr-1.5" /> Send Reset Link</>}
          </button>

          <div className="text-center text-[12.5px] text-slate-500">
            Remembered? <Link to="/login" className="auth-accent hover:underline font-semibold ml-0.5">Back to Sign In</Link>
          </div>
          <div className="text-center text-[12.5px] text-slate-500">
            Need help? <Link to="/contact-support" className="auth-accent hover:underline font-semibold ml-0.5">Contact Support</Link>
          </div>
        </form>
      )}
    </AuthShell>
  )
}

export function ResetPassword() {
  const toast = useToast()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [loading, setLoading] = useState(false)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (pw !== pw2) { toast.show('Passwords do not match', 'warning'); return }
    setLoading(true)
    try {
      await api.post('/auth/reset-password', { token: params.get('token'), new_password: pw })
      toast.show('Password reset. Please sign in.', 'success'); navigate('/login')
    } catch (err) { toast.show(apiError(err), 'error') } finally { setLoading(false) }
  }
  return (
    <AuthShell title="Reset Password" subtitle="Choose a new password for your account." icon="fa-solid fa-lock" eyebrow="Set New Password">
      <form onSubmit={submit} className="space-y-5" autoComplete="on">
        <div>
          <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">New Password</label>
          <div className="relative">
            <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none">
              <i className="fa-solid fa-lock text-[13px]" />
            </span>
            <input
              type="password"
              required
              minLength={12}
              autoComplete="new-password"
              value={pw}
              onChange={(e) => setPw(e.target.value)}
              className="field-input w-full pl-10 pr-4 py-2.5 text-sm border border-slate-200 rounded-xl"
              placeholder="At least 12 characters"
            />
          </div>
        </div>
        <div>
          <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">Confirm Password</label>
          <div className="relative">
            <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none">
              <i className="fa-solid fa-lock text-[13px]" />
            </span>
            <input
              type="password"
              required
              autoComplete="new-password"
              value={pw2}
              onChange={(e) => setPw2(e.target.value)}
              className="field-input w-full pl-10 pr-4 py-2.5 text-sm border border-slate-200 rounded-xl"
              placeholder="Repeat password"
            />
          </div>
        </div>
        <button type="submit" disabled={loading} className="btn-signin w-full py-3 font-semibold text-sm rounded-xl disabled:opacity-60 disabled:cursor-not-allowed">
          {loading ? <><i className="fa-solid fa-spinner fa-spin mr-1.5" /> Saving…</> : <><i className="fa-solid fa-check mr-1.5" /> Reset Password</>}
        </button>
        <div className="text-center text-[12.5px] text-slate-500">
          <Link to="/login" className="auth-accent hover:underline font-semibold">
            <i className="fa-solid fa-arrow-left mr-1" />Back to Sign In
          </Link>
        </div>
      </form>
    </AuthShell>
  )
}

export function ChangePassword() {
  const toast = useToast()
  const navigate = useNavigate()
  const { refresh } = useAuth()
  const [cur, setCur] = useState('')
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [loading, setLoading] = useState(false)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (pw !== pw2) { toast.show('Passwords do not match', 'warning'); return }
    setLoading(true)
    try {
      await api.post('/auth/change-password', { current_password: cur, new_password: pw })
      toast.show('Password changed', 'success'); await refresh(); navigate('/dashboard')
    } catch (err) { toast.show(apiError(err), 'error') } finally { setLoading(false) }
  }
  return (
    <AuthShell title="Change Password" subtitle="Set a new password to continue." icon="fa-solid fa-key" eyebrow="Update Password">
      <form onSubmit={submit} className="space-y-5" autoComplete="on">
        <div>
          <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">Current Password</label>
          <div className="relative">
            <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none">
              <i className="fa-solid fa-lock text-[13px]" />
            </span>
            <input type="password" required autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)}
              className="field-input w-full pl-10 pr-4 py-2.5 text-sm border border-slate-200 rounded-xl" placeholder="Current password" />
          </div>
        </div>
        <div>
          <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">New Password (min 12 chars)</label>
          <div className="relative">
            <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none">
              <i className="fa-solid fa-lock text-[13px]" />
            </span>
            <input type="password" required minLength={12} autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)}
              className="field-input w-full pl-10 pr-4 py-2.5 text-sm border border-slate-200 rounded-xl" placeholder="At least 12 characters" />
          </div>
        </div>
        <div>
          <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">Confirm New Password</label>
          <div className="relative">
            <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none">
              <i className="fa-solid fa-lock text-[13px]" />
            </span>
            <input type="password" required autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)}
              className="field-input w-full pl-10 pr-4 py-2.5 text-sm border border-slate-200 rounded-xl" placeholder="Repeat new password" />
          </div>
        </div>
        <button type="submit" disabled={loading} className="btn-signin w-full py-3 font-semibold text-sm rounded-xl disabled:opacity-60 disabled:cursor-not-allowed">
          {loading ? <><i className="fa-solid fa-spinner fa-spin mr-1.5" /> Saving…</> : <><i className="fa-solid fa-check mr-1.5" /> Update Password</>}
        </button>
      </form>
    </AuthShell>
  )
}

export function ContactSupport() {
  const toast = useToast()
  const [form, setForm] = useState({ name: '', email: '', phone: '', subject: '', message: '' })
  const [sent, setSent] = useState(false)
  const [loading, setLoading] = useState(false)
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setLoading(true)
    try { await api.post('/auth/support-message', form); setSent(true) }
    catch (err) { toast.show(apiError(err), 'error') } finally { setLoading(false) }
  }
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }))

  return (
    <AuthShell
      title="Contact Support"
      subtitle="Our team at HussnainTechVertex is here to help — 24/7 portal support."
      icon="fa-solid fa-headset"
      eyebrow="Support"
      cardWidth={460}
    >
      {sent ? (
        <div className="text-center py-4">
          <i className="fa-solid fa-circle-check text-emerald-500 text-4xl mb-3" />
          <p className="text-sm text-slate-600 mb-4">Thanks! Your message has been received.</p>
          <Link to="/login" className="auth-accent hover:underline text-[13px] font-semibold">
            <i className="fa-solid fa-arrow-left mr-1" />Back to Sign In
          </Link>
        </div>
      ) : (
        <>
          {/* Quick-links inside the branded card */}
          <div className="space-y-2.5 mb-6">
            <a href="tel:+923028808488" className="quick-link">
              <div className="ql-icon bg-emerald-50 text-emerald-600"><i className="fa-solid fa-phone text-[13px]" /></div>
              <div className="flex-1 min-w-0">
                <div className="text-[11px] text-slate-500 leading-none">Phone / WhatsApp</div>
                <div className="text-[13.5px] font-semibold text-slate-800 mt-0.5 truncate">+92 302 8808488</div>
              </div>
            </a>
            <a href="mailto:info@hussnaintechvertex.site" className="quick-link">
              <div className="ql-icon auth-accent-tile-soft"><i className="fa-regular fa-envelope text-[13px]" /></div>
              <div className="flex-1 min-w-0">
                <div className="text-[11px] text-slate-500 leading-none">Email · reply within 24 hours</div>
                <div className="text-[13.5px] font-semibold text-slate-800 mt-0.5 truncate">info@hussnaintechvertex.site</div>
              </div>
            </a>
            <a href="https://wa.me/923028808488" target="_blank" rel="noopener noreferrer" className="quick-link">
              <div className="ql-icon bg-emerald-50 text-emerald-600"><i className="fa-brands fa-whatsapp text-[14px]" /></div>
              <div className="flex-1 min-w-0">
                <div className="text-[11px] text-slate-500 leading-none">WhatsApp Chat · quick replies</div>
                <div className="text-[13.5px] font-semibold text-slate-800 mt-0.5 truncate">Open WhatsApp chat</div>
              </div>
            </a>
          </div>

          <div className="divider mb-6" />

          <form onSubmit={submit} className="space-y-4" autoComplete="on">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">Your Name</label>
                <div className="relative">
                  <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none">
                    <i className="fa-regular fa-user text-[13px]" />
                  </span>
                  <input required autoComplete="name" value={form.name} onChange={set('name')}
                    className="field-input w-full pl-9 pr-3 py-2.5 text-sm border border-slate-200 rounded-xl" placeholder="Full name" />
                </div>
              </div>
              <div>
                <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">Email <span className="text-red-500">*</span></label>
                <div className="relative">
                  <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none">
                    <i className="fa-regular fa-envelope text-[13px]" />
                  </span>
                  <input type="email" required autoComplete="email" value={form.email} onChange={set('email')}
                    className="field-input w-full pl-9 pr-3 py-2.5 text-sm border border-slate-200 rounded-xl" placeholder="you@company.com" />
                </div>
              </div>
              <div>
                <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">Phone</label>
                <div className="relative">
                  <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none">
                    <i className="fa-solid fa-phone text-[13px]" />
                  </span>
                  <input autoComplete="tel" value={form.phone} onChange={set('phone')}
                    className="field-input w-full pl-9 pr-3 py-2.5 text-sm border border-slate-200 rounded-xl" placeholder="+92 …" />
                </div>
              </div>
              <div>
                <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">Subject</label>
                <div className="relative">
                  <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none">
                    <i className="fa-solid fa-tag text-[13px]" />
                  </span>
                  <input value={form.subject} onChange={set('subject')}
                    className="field-input w-full pl-9 pr-3 py-2.5 text-sm border border-slate-200 rounded-xl" placeholder="How can we help?" />
                </div>
              </div>
            </div>

            <div>
              <label className="block text-[13px] font-semibold text-slate-700 mb-1.5">Message <span className="text-red-500">*</span></label>
              <textarea required rows={4} value={form.message} onChange={set('message')}
                className="field-input w-full px-3 py-2.5 text-sm border border-slate-200 rounded-xl" placeholder="Describe your issue or question in detail…" />
            </div>

            <button type="submit" disabled={loading}
              className="btn-signin w-full py-3 font-semibold text-sm rounded-xl disabled:opacity-60 disabled:cursor-not-allowed">
              {loading ? <><i className="fa-solid fa-spinner fa-spin mr-1.5" /> Sending…</> : <><i className="fa-regular fa-paper-plane mr-1.5" /> Send Message</>}
            </button>

            <div className="text-center text-[12.5px] text-slate-500">
              <Link to="/login" className="auth-accent hover:underline font-semibold">
                <i className="fa-solid fa-arrow-left mr-1" />Back to Sign In
              </Link>
            </div>
          </form>
        </>
      )}
    </AuthShell>
  )
}
