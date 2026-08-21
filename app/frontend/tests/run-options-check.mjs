/**
 * Runner for tests/optionsCache.check.ts.
 *
 * src/lib/options.ts imports the real axios client from './api', which reaches
 * for `window` and a live backend. This bundles the check with a resolve plugin
 * that swaps that ONE module for tests/_apiStub.ts, then runs the result in
 * Node. Everything else — the cache, the TTL, the dedupe, the projections — is
 * the real, shipped code.
 *
 * Usage:  node tests/run-options-check.mjs        (or: npm run test:options)
 * Exit code is the check's exit code, so CI and pytest can both consume it.
 */
import { build } from 'esbuild'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, resolve as pathResolve } from 'node:path'
import { rmSync } from 'node:fs'

const here = dirname(fileURLToPath(import.meta.url))
const root = pathResolve(here, '..')
const stub = pathResolve(here, '_apiStub.ts')
const outfile = pathResolve(here, '.options-check.bundle.cjs')

/** Redirect every import of src/lib/api (however it is spelled) to the stub. */
const stubApiPlugin = {
  name: 'stub-api',
  setup(b) {
    b.onResolve({ filter: /(^|\/)api$/ }, (args) => {
      const from = args.importer.replace(/\\/g, '/')
      if (from.includes('/src/lib/')) return { path: stub }
      return null
    })
  },
}

await build({
  entryPoints: [pathResolve(here, 'optionsCache.check.ts')],
  bundle: true,
  format: 'cjs',
  platform: 'node',
  target: 'node18',
  outfile,
  logLevel: 'error',
  absWorkingDir: root,
  plugins: [stubApiPlugin],
})

const res = spawnSync(process.execPath, [outfile], { stdio: 'inherit' })
try { rmSync(outfile) } catch { /* best effort */ }
process.exit(res.status === null ? 1 : res.status)
