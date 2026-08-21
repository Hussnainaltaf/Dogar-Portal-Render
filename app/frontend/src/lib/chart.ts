// Thin wrapper over the global Chart.js.
//
// AUDIT FIX (2026-07-07): Chart.js is loaded from the SELF-HOSTED vendored
// bundle, NOT from https://cdn.jsdelivr.net/npm/chart.js. The app's strict CSP
// (script-src 'self' 'nonce-…') blocks any external <script>, so the old CDN
// injection never executed and Dashboard charts silently failed. There is no
// external network dependency.
//
// PERF 2026-08-01: it is no longer a parser-blocking <script> in <head>.
//
// It used to sit in app/frontend/index.html as
//     <script src="/static/vendor/js/chart.umd.min.js"></script>
// i.e. a classic, non-deferred script in <head>. That means the browser stopped
// parsing the document, downloaded 205 KB (68 KB gzipped) and evaluated it
// BEFORE it could even discover the React module bundle — on every single route,
// including /login, and for every user who never opens the Dashboard.
//
// Chart.js is imported by exactly one page (pages/Dashboard.tsx) and always
// through ensureChart(). So the tag is gone and this module fetches it on first
// use instead:
//   * non-Dashboard routes now pay 0 bytes for charting;
//   * the Dashboard fetches it in parallel with its own data request rather than
//     ahead of the bundle, so it is no longer on the critical path either.
//
// CSP NOTE: injecting a <script src="/static/…"> at runtime is explicitly
// allowed — `script-src 'self'` governs the request by ORIGIN. The nonce only
// applies to INLINE scripts, and this is not one. (Verified against
// app/core/csp_nonce.py.)
//
// NOTE: ensureChart() resolves immediately when window.Chart is already
// defined and never injects a second copy.
declare global {
  interface Window { Chart: any }
}

/** Public path of the vendored bundle. Absolute /static/* URL served directly
 *  by FastAPI, untouched by the Vite build. */
const CHART_SRC = '/static/vendor/js/chart.umd.min.js'

/** Single in-flight loader, so two charts mounting in the same tick (the
 *  Dashboard mounts several) share one request and one <script> tag. */
let loader: Promise<any> | null = null

export function useChartGlobal() {
  return window.Chart
}

/**
 * Resolve the global Chart constructor, loading the vendored bundle on first
 * use. Resolves immediately if the page already provided it.
 */
export function ensureChart(): Promise<any> {
  if (window.Chart) return Promise.resolve(window.Chart)
  if (loader) return loader

  loader = new Promise<any>((resolve, reject) => {
    // A tag may already exist (another caller raced us, or a legacy template
    // included it) — attach to it instead of adding a duplicate 205 KB request.
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${CHART_SRC}"]`)
    const tag = existing || document.createElement('script')

    const done = () => {
      if (window.Chart) resolve(window.Chart)
      else reject(new Error(`Chart.js loaded from ${CHART_SRC} but did not define window.Chart.`))
    }
    const fail = () => {
      // Allow a later retry (e.g. the user was briefly offline) rather than
      // caching the failure for the lifetime of the page.
      loader = null
      reject(new Error(`Failed to load Chart.js from ${CHART_SRC}.`))
    }

    tag.addEventListener('load', done, { once: true })
    tag.addEventListener('error', fail, { once: true })

    if (!existing) {
      tag.src = CHART_SRC
      tag.async = true
      document.head.appendChild(tag)
    } else if (window.Chart) {
      // Already finished evaluating before we attached the listener.
      done()
    }
  })
  return loader
}
