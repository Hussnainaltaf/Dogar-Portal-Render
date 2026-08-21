import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { AuthProvider } from './auth/AuthContext'
import { ToastProvider } from './components/Toast'
import { TENANT_BASE, TENANT_INFO } from './lib/api'
import { applyTenantBranding, brandFullName } from './lib/branding'
import './index.css'

// TENANT BRANDING IS APPLIED HERE, AT BOOT — NOT INSIDE THE SHELL.
//
// MEASURED DEFECT (2026-08-19). The only call site was AppLayout's
// `useEffect`, and AppLayout is mounted *inside* `<RequireAuth>`. Consequences,
// both confirmed in a real browser:
//
//   1. A tenant's LOGIN PAGE — the very first screen their staff ever see, and
//      the one carrying the company name and logo — rendered in Dogar blue with
//      Dogar's typeface. So did /forgot-password, /reset-password,
//      /change-password and /contact-support: every unauthenticated route.
//   2. Any 401 (expired cookie, tenant-bound token replayed at the wrong
//      portal) redirects to /login, which unmounts AppLayout. The office's
//      branding therefore *disappeared* exactly when the user was most likely
//      to be looking at a branding-heavy screen.
//
// The brief is "customize the topbar and document branding automatically based
// on the active office tenant". Branding that requires a successful login first
// is not automatic. TENANT_INFO comes from the boot payload the server injected
// into the HTML, so it is available before React renders and cannot change
// without a full page load — there is nothing to wait for.
//
// Applying it before `createRoot` also removes a first-paint flash: the tokens
// are already on <html> when the first frame is composited, instead of being
// re-written one effect later.
//
// On the Dogar root portal `TENANT_INFO` is null and this is a deliberate
// NO-OP, so the root portal's authored branding is untouched.
applyTenantBranding(TENANT_INFO)

// The BROWSER TAB TITLE, for the same reason and at the same moment.
//
// index.html ships a static `<title>Dogar Trading Corporation</title>`, and the
// only thing that ever rewrote it was Breadcrumbs — which, like the old
// branding call, lives inside the authenticated shell. So a partner office's
// login tab, and any bookmark made from it, was labelled with the ROOT
// company's name. Measured in Chromium: /t/acme/login had document.title ===
// "Dogar Trading Corporation" even once the colours and typeface were correct.
//
// Only the UNAUTHENTICATED case is handled here; once the shell mounts,
// Breadcrumbs takes over and appends the per-page prefix ("Dashboard · Acme
// Corp"). Guarding on TENANT_INFO keeps this a genuine no-op on the root
// portal, where brandFullName(null) would return the exact string already in
// the HTML — "Dogar unchanged" enforced by construction rather than by luck.
if (TENANT_INFO) document.title = brandFullName(TENANT_INFO)

// Path-mode multi-tenancy: when opened via /t/<slug>/..., React Router must use
// that prefix as its basename so every <Link>/navigate() stays inside the copy
// portal. Empty for the root portal (host/subdomain access) — no behaviour change.
ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <BrowserRouter basename={TENANT_BASE || undefined}>
      <ToastProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
