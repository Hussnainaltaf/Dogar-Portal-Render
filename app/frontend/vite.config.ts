import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// The React SPA is built into app/static/spa so FastAPI's existing
// StaticFiles mount (/static) can serve the bundle with zero backend
// logic changes. base='/static/spa/' ensures asset URLs resolve correctly.
export default defineConfig({
  plugins: [react()],
  base: '/static/spa/',
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  build: {
    outDir: path.resolve(__dirname, '../static/spa'),
    emptyOutDir: true,
    assetsDir: 'assets',
    sourcemap: false,
    // Split the long-lived vendor libs (React, Router, axios) into their own
    // cacheable chunks so app-code changes don't force a re-download of the
    // framework, and the initial parse cost is spread across parallel requests.
    //
    // v13 — the object form was WRONG here. Naming bare entry points
    // ('react', 'react-dom') only captures those exact module ids, so the real
    // React runtime (react/jsx-runtime, react-dom/client and the scheduler)
    // was pulled into whichever chunk imported it first — react-router-dom.
    // Result: a useless 0.04 kB `vendor-react` file and a 164 kB
    // `vendor-router` that invalidated the React cache on every router bump.
    // The id-matching function below assigns whole package subtrees, which is
    // what actually produces stable, independently cacheable vendor chunks.
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'vendor-react'
          if (/[\\/]node_modules[\\/](react-router|react-router-dom|@remix-run)[\\/]/.test(id)) return 'vendor-router'
          if (/[\\/]node_modules[\\/](axios|follow-redirects)[\\/]/.test(id)) return 'vendor-http'
          return undefined
        },
      },
    },
    chunkSizeWarningLimit: 700,
  },
  server: {
    port: 5173,
    host: true,
    // During dev, proxy backend routes to the running FastAPI server (:3000)
    // so cookie-based auth works on a single origin (no CORS issues).
    proxy: {
      '/api': 'http://localhost:3000',
      '/static/img': 'http://localhost:3000',
      '/static/css': 'http://localhost:3000',
      '/static/vendor': 'http://localhost:3000',
    },
  },
})
