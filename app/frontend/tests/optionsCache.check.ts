/**
 * Regression guard for src/lib/options.ts — the shared reference-list cache.
 *
 * WHY A NODE CHECK AND NOT A BROWSER TEST
 * The properties that matter here are request COUNTS (did the second navigation
 * refetch? did three concurrent callers collapse into one request?). Those are
 * pure module logic. Asserting them through a real browser is slow, needs a
 * seeded database and a login, and — as this project's own perf work showed —
 * produces false failures the moment the machine is under memory pressure. This
 * runs deterministically in ~1s with no server at all.
 *
 * HOW IT RUNS
 *   npm run test:options          (from app/frontend)
 * esbuild bundles this file with tests/_apiStub.ts aliased over src/lib/api.ts,
 * then Node executes it. tests/test_frontend_options_cache.py runs the same
 * command from the Python suite so a regression fails `pytest` too.
 *
 * WHAT IT PINS
 *  1. a cold read issues exactly one request, and it carries compact=true
 *  2. repeat navigation issues zero requests (the actual perf claim)
 *  3. concurrent cold callers share a single in-flight request
 *  4. invalidateOptions() forces exactly one refetch, then caches again
 *  5. invalidating one key does not evict the others
 *  6. value semantics are preserved — ids for clients/demands, NAMES for the
 *     lookup selects (visa categories/embassies/cities). Getting this wrong
 *     would silently send the wrong filter values to the API.
 *  7. a failed load is not cached, so a dropped connection cannot leave a
 *     dropdown permanently empty
 */
import { calls } from './_apiStub'
import { getRows, getIdOptions, getNameOptions, invalidateOptions, clearOptionsCache } from '../src/lib/options'

let failures = 0
function check(cond: boolean, msg: string) {
  console.log((cond ? '  PASS  ' : '  FAIL  ') + msg)
  if (!cond) failures++
}

async function main() {
  // 1. cold fetch
  calls.length = 0
  await getRows('clients')
  check(calls.length === 1, `cold fetch issues exactly 1 request (got ${calls.length})`)
  check(calls[0].includes('"compact":true'), `clients request uses compact=true -> ${calls[0]}`)

  // 2. repeat navigation must not refetch — this IS the perf claim
  calls.length = 0
  await getRows('clients')
  await getIdOptions('clients')
  await getRows('clients')
  check(calls.length === 0, `3 repeat reads issue 0 requests (got ${calls.length})`)

  // 3. concurrent cold callers share ONE request (in-flight dedupe)
  clearOptionsCache()
  calls.length = 0
  await Promise.all([getRows('demands'), getRows('demands'), getIdOptions('demands')])
  check(calls.length === 1, `3 concurrent cold callers share 1 request (got ${calls.length})`)
  check(calls[0].includes('"compact":true'), `demands request uses compact=true -> ${calls[0]}`)

  // 4. invalidation forces exactly one refetch
  calls.length = 0
  invalidateOptions('demands')
  await getRows('demands')
  await getRows('demands')
  check(calls.length === 1, `invalidate -> exactly 1 refetch then cached again (got ${calls.length})`)

  // 5. invalidating one key must not evict the others.
  // NOTE: step 3 called clearOptionsCache(), so 'clients' must be re-warmed
  // first — otherwise this would assert against an empty cache and pass for the
  // wrong reason. (Caught by the first run of this very check.)
  await getRows('clients')          // warm
  calls.length = 0
  invalidateOptions('demands')      // evict a DIFFERENT key
  await getRows('clients')
  check(calls.length === 0, 'invalidating "demands" did not evict "clients"')

  // 6. value semantics preserved: ids for clients/demands, names for lookups
  const idOpts = await getIdOptions('clients')
  const nameOpts = await getNameOptions('visaCategories')
  check(idOpts[0].value === '1', `client option value is the id (got ${idOpts[0].value})`)
  check(idOpts[0].label === 'Acme', `client option label is company_name (got ${idOpts[0].label})`)
  check(nameOpts[0].value === 'Driver', `visa-category option value is the NAME (got ${nameOpts[0].value})`)
  check(nameOpts[0].value === nameOpts[0].label, 'visa-category value === label')

  // 7. a failed load must NOT be cached (next call retries)
  clearOptionsCache()
  const mod = require('./_apiStub')
  const good = mod.api.get
  mod.api.get = () => Promise.reject(new Error('network down'))
  let threw = false
  try { await getRows('cities') } catch { threw = true }
  check(threw, 'a network failure propagates to the caller')
  mod.api.get = good
  calls.length = 0
  const rows = await getRows('cities')
  check(rows.length === 1 && calls.length === 1, `after a failure the next call retries (requests=${calls.length})`)

  console.log(failures ? `\n${failures} FAILURE(S)` : '\nALL CACHE CHECKS PASSED')
  process.exit(failures ? 1 : 0)
}

main()
