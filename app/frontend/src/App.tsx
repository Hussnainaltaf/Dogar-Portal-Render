import { Routes, Route, Navigate } from 'react-router-dom'
import { lazy, Suspense } from 'react'
import RequireAuth from './auth/RequireAuth'
import RequirePermission from './auth/RequirePermission'
import AppLayout from './layout/AppLayout'

// Login stays eagerly imported so the first paint (the auth gate) is instant.
import Login from './pages/Login'
// Unauthorized is imported STATICALLY on purpose. RequirePermission renders it
// inline on every denied route, so it is already in the main graph; declaring
// it lazily here as well made Rollup warn ("dynamically imported ... but also
// statically imported") and silently merge the chunk anyway. One import path,
// no warning, no duplicate module instance.
import Unauthorized from './pages/Unauthorized'

// ---------------------------------------------------------------------------
// Route-level code-splitting (performance).
// Every authenticated page is lazy-loaded so the initial bundle only carries
// the shell + login. Vite emits a separate chunk per page and prefetches on
// navigation, cutting first-load JS dramatically. A lightweight Suspense
// spinner covers the (usually sub-100ms) chunk fetch.
// ---------------------------------------------------------------------------
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Candidates = lazy(() => import('./pages/Candidates'))
const Clients = lazy(() => import('./pages/Clients'))
const Demands = lazy(() => import('./pages/Demands'))
const Users = lazy(() => import('./pages/Users'))
const Depositors = lazy(() => import('./pages/Depositors'))
const DemandDetail = lazy(() => import('./pages/DemandDetail'))

const ForgotPassword = lazy(() => import('./pages/AuthPages').then((m) => ({ default: m.ForgotPassword })))
const ResetPassword = lazy(() => import('./pages/AuthPages').then((m) => ({ default: m.ResetPassword })))
const ChangePassword = lazy(() => import('./pages/AuthPages').then((m) => ({ default: m.ChangePassword })))
const ContactSupport = lazy(() => import('./pages/AuthPages').then((m) => ({ default: m.ContactSupport })))

const VisaCategoriesPage = lazy(() => import('./pages/lookupPages').then((m) => ({ default: m.VisaCategoriesPage })))
const EmbassiesPage = lazy(() => import('./pages/lookupPages').then((m) => ({ default: m.EmbassiesPage })))
const CitiesPage = lazy(() => import('./pages/lookupPages').then((m) => ({ default: m.CitiesPage })))
const MedicalCentersPage = lazy(() => import('./pages/lookupPages').then((m) => ({ default: m.MedicalCentersPage })))
const ContactsPage = lazy(() => import('./pages/lookupPages').then((m) => ({ default: m.ContactsPage })))
const ServiceChargesPage = lazy(() => import('./pages/lookupPages').then((m) => ({ default: m.ServiceChargesPage })))

const AuditLog = lazy(() => import('./pages/AuditLog'))
const LoginHistory = lazy(() => import('./pages/MiscPages').then((m) => ({ default: m.LoginHistory })))
const Roles = lazy(() => import('./pages/MiscPages').then((m) => ({ default: m.Roles })))
const Documents = lazy(() => import('./pages/MiscPages').then((m) => ({ default: m.Documents })))
const DocumentCustomize = lazy(() => import('./pages/DocumentCustomize'))
const Settings = lazy(() => import('./pages/MiscPages').then((m) => ({ default: m.Settings })))
const ProtectorLetter = lazy(() => import('./pages/MiscPages').then((m) => ({ default: m.ProtectorLetter })))
const PortalAdmin = lazy(() => import('./pages/MiscPages').then((m) => ({ default: m.PortalAdmin })))

const ClientDetail = lazy(() => import('./pages/DetailPages').then((m) => ({ default: m.ClientDetail })))

/** Suspense fallback shown while a route chunk streams in. */
function RouteFallback() {
  return (
    <div className="spa-loader" role="status" aria-label="Loading">
      <i className="fa-solid fa-spinner fa-spin text-xl" style={{ color: 'var(--brand)' }} />
    </div>
  )
}

export default function App() {
  return (
    <Suspense fallback={<RouteFallback />}>
    <Routes>
      {/* ---- Public routes ---- */}
      <Route path="/login" element={<Login />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/change-password" element={<ChangePassword />} />
      <Route path="/contact-support" element={<ContactSupport />} />

      {/* ---- Guarded routes under the app shell ---- */}
      <Route
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />

        {/* Clients */}
        <Route path="/clients" element={<Clients />} />
        <Route path="/clients/:clientId" element={<ClientDetail />} />

        {/* Demands */}
        <Route path="/demands" element={<Demands />} />
        <Route path="/demands/:demandId" element={<DemandDetail />} />

        {/* Candidates */}
        <Route path="/candidates" element={<Candidates />} />

        {/* Documents */}
        <Route path="/documents" element={<Documents />} />
        {/* Template Designer (coordinate engine) — a real React page rendered
            in-place via client-side routing (NO full reload / white flash). */}
        <Route path="/documents/customize/:tplId" element={<DocumentCustomize />} />

        {/* Protector / Portal */}
        <Route
          path="/protector-letter"
          element={
            <RequirePermission perms={['protector:view']} roles={['Admin', 'Manager', 'Staff']}>
              <ProtectorLetter />
            </RequirePermission>
          }
        />
        <Route path="/portal-admin" element={<PortalAdmin />} />

        {/* REMOVED 2026-07-30 (operator request): /reports, /agents,
            /agents/:agentId/cashbook and /admin/data-migration. These pages
            were reported as unusable and were deleted, not hidden -- their
            components, API routers and the Agent/AgentCash models are gone
            too. Any bookmarked URL now falls through to the catch-all below
            and lands on the dashboard. See section 4.11 of
            PROJECT_DOCUMENTATION.md.

            /admin/audit-log was in that removal list and is now BACK (2026-08-18,
            operator request: "add filters and search to the Audit Log page so I
            can quickly find denied or successful actions"). It is a new page --
            pages/AuditLog.tsx -- not the resurrected one, and it is backed by a
            trail that now actually records denials. Gated on audit:view, the
            same key the API route enforces: gating the ROUTE on a different
            (or no) permission would render a page whose every request 403s. */}
        <Route
          path="/admin/audit-log"
          element={
            <RequirePermission perms={['audit:view']} roles={['Admin', 'Manager']}>
              <AuditLog />
            </RequirePermission>
          }
        />

        {/* Unauthorized (also reachable directly) */}
        <Route path="/unauthorized" element={<Unauthorized />} />

        {/* Users group */}
        <Route path="/users" element={<Users />} />
        <Route path="/login-history" element={<LoginHistory />} />
        <Route path="/roles" element={<Roles />} />

        {/* Settings group */}
        <Route
          path="/settings"
          element={
            <RequirePermission perms={['settings:view']} roles={['Admin', 'Manager']}>
              <Settings />
            </RequirePermission>
          }
        />
        {/* Alias: /settings/general → General settings (gated identically). */}
        <Route
          path="/settings/general"
          element={
            <RequirePermission perms={['settings:view']} roles={['Admin', 'Manager']}>
              <Settings />
            </RequirePermission>
          }
        />
        <Route path="/depositors" element={<Depositors />} />
        <Route path="/service-charges" element={<ServiceChargesPage />} />

        {/* Lookups */}
        <Route path="/visa-categories" element={<VisaCategoriesPage />} />
        <Route path="/embassies" element={<EmbassiesPage />} />
        <Route path="/cities" element={<CitiesPage />} />
        <Route path="/medical-centers" element={<MedicalCentersPage />} />
        <Route path="/contacts" element={<ContactsPage />} />
      </Route>

      {/* ---- Fallback ---- */}
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
    </Suspense>
  )
}
