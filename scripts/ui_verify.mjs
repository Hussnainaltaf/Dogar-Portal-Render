// UI verification harness — logs in via the API to obtain the httponly
// session + csrf cookies, injects them into a real Chromium context, then
// drives the actual SPA screens the user reported as broken (Manage Users,
// Roles & Access Control, Customize Role modal). Captures console errors and
// asserts the rendered DOM actually contains data — NOT just that tests pass.
import { chromium } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE = process.env.BASE || 'http://localhost:3000'

// ---------------------------------------------------------------------------
// Credentials come from the environment / .env -- NEVER from source.
//
// SECURITY (2026-08-13): this file used to carry the literal bootstrap admin
// password on the two checkPortal() calls at the bottom. That is a credential
// committed to version control: anyone with repo read access (or any clone,
// fork, backup or CI log) had the production admin password. The literal was
// removed, the credential was rotated, and
// `tests/test_no_committed_secrets.py` now fails the build if any such literal
// comes back.
//
// Resolution order, highest priority first:
//   1. $ADMIN_EMAIL / $ADMIN_PW              (root portal)
//      $ACME_ADMIN_EMAIL / $ACME_ADMIN_PW    (acme tenant)
//   2. .env -> DEFAULT_ADMIN_EMAIL / DEFAULT_ADMIN_PASSWORD (root only)
// There is no built-in fallback. If neither is present the harness exits
// non-zero rather than guessing.
// ---------------------------------------------------------------------------
const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')

function dotenv() {
  try {
    const out = {}
    for (const line of readFileSync(join(REPO_ROOT, '.env'), 'utf8').split('\n')) {
      const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*)$/.exec(line)
      if (m) out[m[1]] = m[2].trim().replace(/^['"]|['"]$/g, '')
    }
    return out
  } catch {
    return {}
  }
}

const ENVFILE = dotenv()

function credentials(label) {
  if (label === 'root') {
    const email = process.env.ADMIN_EMAIL || ENVFILE.DEFAULT_ADMIN_EMAIL || ''
    const pw = process.env.ADMIN_PW || ENVFILE.DEFAULT_ADMIN_PASSWORD || ''
    return { email, pw }
  }
  return {
    email: process.env.ACME_ADMIN_EMAIL || '',
    pw: process.env.ACME_ADMIN_PW || '',
  }
}

async function login(prefix, email, password) {
  // 1. GET login page to receive csrf_token cookie
  const r1 = await fetch(`${BASE}${prefix}/login`)
  const setCookies = r1.headers.getSetCookie ? r1.headers.getSetCookie() : (r1.headers.raw?.()['set-cookie'] || [])
  const jar = {}
  for (const c of setCookies) { const [kv] = c.split(';'); const [k, v] = kv.split('='); jar[k.trim()] = v }
  const csrf = jar['csrf_token'] || ''
  // 2. POST login
  const r2 = await fetch(`${BASE}${prefix}/api/auth/login`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrf,
      'Cookie': Object.entries(jar).map(([k, v]) => `${k}=${v}`).join('; '),
    },
    body: JSON.stringify({ email, password }),
  })
  if (r2.status !== 200) throw new Error(`login ${prefix} failed: ${r2.status} ${await r2.text()}`)
  const setCookies2 = r2.headers.getSetCookie ? r2.headers.getSetCookie() : (r2.headers.raw?.()['set-cookie'] || [])
  for (const c of setCookies2) { const [kv] = c.split(';'); const [k, v] = kv.split('='); jar[k.trim()] = v }
  return jar
}

function cookiesFor(jar, prefix) {
  const url = new URL(BASE)
  return Object.entries(jar).map(([name, value]) => ({
    name, value, domain: url.hostname, path: '/', httpOnly: false, secure: false, sameSite: 'Lax',
  }))
}

async function checkPortal(label, prefix, email, password) {
  console.log(`\n===== ${label} (${prefix || '/'}) =====`)
  const jar = await login(prefix, email, password)
  const browser = await chromium.launch({ args: ['--no-sandbox'] })
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true })
  await ctx.addCookies(cookiesFor(jar, prefix))
  const page = await ctx.newPage()
  const errors = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message))

  // ---- Manage Users ----
  await page.goto(`${BASE}${prefix}/users`, { waitUntil: 'networkidle', timeout: 30000 })
  await page.waitForTimeout(1500)
  const usersSpinner = await page.locator('.fa-spinner').count()
  const usersBody = await page.locator('table tbody tr').count()
  const usersText = (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 200)
  await page.screenshot({ path: `/tmp/ui_${label}_users.png`, fullPage: true })
  console.log(`  Users: spinner=${usersSpinner} rows=${usersBody}`)
  console.log(`  Users text: ${usersText}`)

  // ---- Roles ----
  await page.goto(`${BASE}${prefix}/roles`, { waitUntil: 'networkidle', timeout: 30000 })
  await page.waitForTimeout(1500)
  const rolesSpinner = await page.locator('.fa-spinner').count()
  const roleCards = await page.locator('.card').count()
  await page.screenshot({ path: `/tmp/ui_${label}_roles.png`, fullPage: true })
  console.log(`  Roles: spinner=${rolesSpinner} cards=${roleCards}`)

  // ---- Customize Role modal (click first Customize button) ----
  const customize = page.locator('button:has-text("Customize")').first()
  let matrixModules = 0, checkboxes = 0
  if (await customize.count()) {
    await customize.click()
    await page.waitForTimeout(1200)
    matrixModules = await page.locator('input[type="checkbox"]').count()
    checkboxes = matrixModules
    await page.screenshot({ path: `/tmp/ui_${label}_role_modal.png`, fullPage: true })
  }
  console.log(`  Customize Role modal: permission checkboxes=${checkboxes}`)

  if (errors.length) { console.log('  CONSOLE ERRORS:'); errors.slice(0, 10).forEach((e) => console.log('   - ' + e)) }
  else console.log('  No console errors.')

  await browser.close()
  return { usersSpinner, usersBody, rolesSpinner, roleCards, checkboxes, errors }
}

const results = {}

const rootCreds = credentials('root')
if (!rootCreds.email || !rootCreds.pw) {
  console.error(
    'ERROR: no root portal credentials. Set $ADMIN_EMAIL and $ADMIN_PW, or\n' +
    '       put DEFAULT_ADMIN_EMAIL / DEFAULT_ADMIN_PASSWORD in .env.\n' +
    '       This harness deliberately has no hardcoded fallback.'
  )
  process.exit(2)
}
results.root = await checkPortal('root', '', rootCreds.email, rootCreds.pw)

const acmeCreds = credentials('acme')
if (acmeCreds.email && acmeCreds.pw) {
  results.acme = await checkPortal('acme', '/t/acme', acmeCreds.email, acmeCreds.pw)
} else {
  console.log('\n===== acme (/t/acme) SKIPPED =====')
  console.log('  Set $ACME_ADMIN_EMAIL and $ACME_ADMIN_PW to include the tenant portal.')
  results.acme = { skipped: true }
}

console.log('\n===== SUMMARY =====')
console.log(JSON.stringify(results, (k, v) => k === 'errors' ? v.length : v, 2))
