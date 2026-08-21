// ---------------------------------------------------------------------------
// Build/runtime metadata exposed to the UI.
//
// The version string is injected by the backend into window.__TENANT_BOOT__
// (see _serve_spa_index in app/main.py) from settings.APP_VERSION, so the
// number in the sidebar footer can never drift from the number the API's
// /api/health payload reports. The literal fallback is
// only used when the SPA is opened by the Vite dev server (no backend shell).
// ---------------------------------------------------------------------------
interface BootMeta { version?: string }

const BOOT: BootMeta =
  (typeof window !== 'undefined' && (window as unknown as { __TENANT_BOOT__?: BootMeta }).__TENANT_BOOT__) || {}

export const APP_VERSION: string = BOOT.version || '1.1.0'

/** Product name used in the footer / document titles. */
export const APP_VENDOR = 'HussnainTechVertex Pvt Ltd.'
