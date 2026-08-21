# Dogar Trading Corporation Portal

A full-stack production-ready management portal for **Dogar Trading Corporation** — a corporate SaaS-style application for overseas employment promotion, recruitment, visa processing, financial tracking, and coordinate-based PDF document generation.

Built with **Python (FastAPI)** backend + **SQLAlchemy** ORM. The browser
front-end has been **fully migrated to React** (Vite + TypeScript + Tailwind +
React Router) while the entire Python backend — every module, function, route,
schema, database model, and piece of business logic — is preserved **100%
unchanged**. The legacy Jinja templates remain in the repo as a fall-back
(toggle via the `USE_SPA` flag).

> **Developed & maintained by** [HussnainTechVertex Pvt Ltd.](#) — branding logo embedded in every footer & login page.

---

## 🔒 Security & CSP Posture — reviewed 2026-08-05

> **External audit response (2026-07-26).** An external review raised one HIGH
> (stored XSS via unescaped `innerHTML`), one MEDIUM (`style-src 'unsafe-inline'`)
> and one LOW (password reset gated on SMTP). Current status:
>
> | # | Finding | Status |
> |---|---------|--------|
> | HIGH | Stored XSS in HTML/JS templates | ✅ **Fixed** — 62 sinks remediated (4 reported, 62 found), enforced by `tests/test_xss_sinks.py` |
> | MEDIUM | `style-src 'unsafe-inline'` | ✅ **Closed** — `script-src` and `style-src-elem` are strict; the standalone document routes now run with `style-src-attr 'none'` (2026-08-04), and the shells' remaining `-attr` exception is accepted on browser measurement. See below. |
> | LOW | Password reset needs SMTP | ✅ **Improved** — boot warning, `/api/health` field, actionable 503 UX |
> | — | **Account enumeration via reset status code** | ✅ **Fixed** — *not in the audit; found while remediating LOW.* See below. |
> | — | **SPA tenant bootstrap script terminator escaping** | ✅ **Fixed** — tenant-controlled branding JSON now emits literal `\\u003c` / `\\u003e` / `\\u0026` escapes before injection into the nonced inline bootstrap script; regression-locked in `tests/test_xss_sinks.py`. |
> | — | **React Router npm advisories** | ✅ **Fixed for shipped bundle** — `react-router-dom` upgraded and `react-router` overridden to the patched audit-clean release; Docker frontend build stage moved to Node 22 to match the patched package engine. |
> | — | **Dynamic template style injection** | ✅ **Hardened** — runtime progress widths and tenant brand colours are no longer injected as `style="..."` strings; templates now carry bounded `data-*` values and apply them via CSSOM after safe DOM insertion. |
> | — | **Candidate sort-key drift on trade re-parenting** | ✅ **Hardened** — Alembic revision `8a4d6c2b1e90` adds a DB trigger for `UPDATE job_categories SET demand_id = ...`, with focused regression coverage and verifier documentation. |
> | MEDIUM | New-tab links without `rel` on AI markdown / receipts | ✅ **Fixed** — every `target="_blank"` anchor in Jinja templates now requires `rel="noopener noreferrer"`, enforced by `tests/test_csp_policy.py`. |
>
> **⚠️ Scope note the audit could not have known:** `USE_SPA` defaults to
> **`True`** (`app/core/config.py`), so page routes serve the compiled **React
> SPA** in `app/static/spa/`. The Jinja templates the audit cited only render
> when `USE_SPA=false`. Both frontends were audited and remediated — see
> *Dual frontend* below.

**Final verdict: Production-ready with two documented, accepted low-risk caveats — not a "fully strict CSP".**

### Accepted low-risk caveats — documented 2026-08-05

- **Shell CSP style attributes:** `app/core/csp_nonce.py` intentionally keeps
  `style-src-attr 'unsafe-inline'` for the Jinja/SPA shell because legacy runtime
  DOM construction still writes style attributes. This does **not** weaken
  `script-src`, which remains nonce-based with no `unsafe-inline` or `unsafe-eval`.
  Standalone document/print routes are already tightened by
  `app/core/style_hoist.py` and run with `style-src-attr 'none'`.
- **No-Redis cache mode:** `app/services/cache.py` falls back to an in-process TTL
  cache when `REDIS_URL` is absent. That is accepted for the default
  single-worker deployment. A multi-worker deployment without Redis can serve a
  stale read from another worker for up to the TTL window, so production scaling
  beyond one worker should set `REDIS_URL`.

### Stored XSS (audit HIGH) — remediated and enforced

The audit named 4 sinks. A purpose-built static analyzer
(`scripts/scan_xss_sinks.py`) enumerated **248 interpolations reaching
`innerHTML`** across the templates and static JS, of which **62 were genuinely
unescaped**. All 62 are fixed; the scanner now reports **0** and fails CI on
regression.

Two distinct sink classes were found:

1. **Template literals** — `el.innerHTML = \`<td>${value}</td>\`` (47 sinks).
2. **String concatenation** — `el.innerHTML = '<span>' + value` (15 sinks).
   Invisible to the first analyzer pass; found only because the concatenation
   detector was added afterwards. Each pass now has its **own** mutation probe,
   because a probe that only mutates a template literal would keep passing if
   the concatenation pass were deleted.

**Remediation strategy — per-interpolation escaping, deliberately not whole-tree
sanitizing.** `safe_dom.js`'s sanitizer strips `on*` handlers, but the templates
*depend* on inline `onclick` (rebound by `csp_rebinder.js`). Routing everything
through `element.safeHTML` would therefore have broken the app. Escaping at each
interpolation preserves behaviour.

`esc()` vs `escJs()` matters and is enforced by a test: a value inside a
single-quoted JS string in an `on*=` handler **must** use `escJs()`. Using
`esc()` there emits `&#39;` for an apostrophe, which `csp_rebinder.js`'s
eval-free tokenizer cannot decode — so it is both a security hole *and* a
functional break (a client named `O'Brien & Co` would break its own row's
buttons).

> **Superseded by the React SPA.** Everything above describes the retired Jinja
> frontend, whose per-interpolation escapers lived in `app/static/js/safe_dom.js`.
> That stack — templates, `safe_dom.js` and `csp_rebinder.js` — was removed in the
> Jinja2 eradication pass, so those files no longer ship.
>
> **In the SPA the guarantee is stronger and structural:** React escapes every
> interpolation by default, so the whole "did the author remember to call
> `esc()`" bug class is gone. The only way to reach raw markup is
> `dangerouslySetInnerHTML`, and the single audited boundary for it is
> **`app/frontend/src/lib/sanitizeHtml.ts`** (strips `on*` handlers, enforces an
> attribute allowlist, rejects `javascript:`/`data:` URIs).
>
> `scripts/scan_xss_sinks.py` now scans the SPA source (`app/frontend/src`) as
> well, classifies every `dangerouslySetInnerHTML` payload with the same rules,
> and **fails if it finds nothing to scan** — so the ratchet cannot silently pass
> on an empty tree the way it briefly could after the templates were deleted.
> `tests/test_xss_sinks.py` keeps a mutation probe against a real SPA sink to
> prove the scanner still detects a reintroduced hole.

**Verified end-to-end, not just statically.** A real payload
(`<img src=x onerror=…>O'Brien & Co`) was stored via the API and every
remediated page loaded under Playwright: nothing executed, no live payload
nodes, the payload rendered as literal text, the apostrophe survived with **no
`&#39;` artefact**, inline handlers still rebound, and the edit modal still
opened. All 8 concatenation sinks were additionally driven with a hostile
stubbed API response, since those only fire on an error path.

```bash
python3 scripts/scan_xss_sinks.py       # report (exit 0 always)
python3 scripts/scan_xss_sinks.py --check   # CI mode (non-zero on findings)
python3 -m pytest tests/test_xss_sinks.py   # stored-XSS + SPA bootstrap ratchet tests
```

`tests/test_xss_sinks.py` guards the invariants *and the analyzer itself*: two
mutation probes re-inject known-bad sinks (one per class) into a temp copy of the
tree and assert the scanner still catches them — a static analyzer that has gone
blind also reports zero.

> **Suppressing a finding:** prefer escaping. If a value truly holds pre-built
> trusted markup, add a per-line `xss-sink-ok` pragma with a justification, or a
> reviewed entry in `TRUSTED_MARKUP_VARS_BY_FILE`. The pragma is deliberately
> greppable so waivers cannot quietly accumulate.

### Dual frontend — both audited

- **React SPA** (`app/static/spa/`, served when `USE_SPA=true`, the default):
  its two HTML-injection paths are safe **by construction** — the
  `dangerouslySetInnerHTML` path escapes `&`/`<`/`>` *before* applying
  markdown-ish transforms (order matters and is correct), and the document
  designer's `innerHTML` round-trip is guarded by a sanitizer with tag/attribute
  allow-lists that strips `on*` and `javascript:`/`data:`/`vbscript:` URLs.
- **Jinja templates** (`USE_SPA=false`): all 62 sinks remediated as above.

### Mobile tenant-branding and auth QA — reviewed 2026-08-04

The active tenant/customization screenshots map to the **React SPA** surfaces, not
only to the legacy Jinja templates. The latest SPA bundle includes these focused
production fixes:

- Login and shared auth shells switch from `md` split-screen to `lg` split-screen;
  mobile/tablet widths render a single full-width panel with no horizontal
  overflow. Tenant login pages read `window.__TENANT_BOOT__.tenant` for the
  tenant logo, name and subtitle.
- The dashboard topbar hides inline global search below `lg` and exposes a
  mobile/tablet search sheet trigger instead, preventing logo/search overlap.
- Portal Admin now labels the tenant launch action **Open Tenant** and explains
  that branding changes affect only the isolated `/t/<slug>/` tenant copy.
- The Tenant Branding drawer is portalled to `document.body` and behaves as a
  full-viewport mobile sheet with a sticky close button and its own scroll region.

Validated in Chromium/Playwright at `390px`, `768px`, and `1024px` for `/login`
and `/t/acme/login`; at `390px` for `/dashboard`; and authenticated at `390px`
for `/portal-admin` including the Tenant Branding drawer. All checks reported no
horizontal overflow and no unexpected console/page errors.

### Account enumeration on password reset — found during remediation

**Not reported by the external audit.** `/api/auth/forgot-password` used to raise
**500** when an SMTP send threw. That code is reachable only *after* matching an
active user, so the status code alone was an account-existence oracle:

```
registered address    -> send attempted -> throws -> 500
unregistered address  -> early return             -> 200
```

No timing analysis needed. This was easy to hit in practice: `.env.example`
ships **placeholder** SMTP values that are truthy enough to pass the config gate
but cannot authenticate, so a default-ish deployment landed squarely in the
leaking branch. Verified live before the fix (`500` vs `200`), and the
regression test was confirmed to fail against the old code.

Fixed on both axes:

- A delivery failure is a **server-side** fault that says nothing about the
  address typed, so it no longer changes the response. All branches return the
  same account-neutral `200`; the failure is escalated to the operator via an
  ERROR log, `/api/health`, and the boot warning.
- `settings.email_configured` is now the single source of truth for "can this
  server send mail?" and **rejects the `.env.example` placeholders**, so the
  honest `503` fires instead of failing later inside the send.

### Password reset / SMTP (audit LOW)

The endpoint's core logic was already right: it fails **closed** (`503`) rather
than claiming a link was sent, and the config check runs *before* user lookup so
existent and non-existent addresses behave identically. The gaps were
operational, and are closed:

- **Boot-time WARNING** when email is unconfigured — the operator learns at
  deploy time, not from the first locked-out user. Deliberately *not* a hard
  failure: many deployments reset passwords out-of-band via the admin Users
  screen, and refusing to boot over an optional feature would be worse than the
  gap it closes.
- **`GET /api/health` reports `password_reset`** (`available` /
  `unavailable_smtp_not_configured`) so monitoring can catch it. It is a
  *capability* flag — an optional feature never marks the service unhealthy;
  only the database does that.
- **Actionable 503 UX**: `/forgot-password` explains that retrying will not
  help, points to the admin and to support, and disables the submit button
  instead of inviting a pointless retry loop.

- **`script-src` is fully strict** — `'self' 'nonce-<per-request>'`, with **no**
  `'unsafe-inline'`, `'unsafe-eval'`, or `'unsafe-hashes'`. Inline `<script>`
  blocks are nonced; legacy inline event handlers (`onclick=`/`onsubmit=`/…) are
  rebound to `addEventListener` by the self-hosted, nonce'd `csp_rebinder.js`
  (proven eval-free by `tests/test_csp_rebinder.py`). This is where XSS risk
  lives, and it is closed.
- **Styles use a split, per-response policy.** The audit's MEDIUM finding cites
  `style-src 'unsafe-inline'`, which no longer describes the header: that
  directive is only a *legacy fallback*, and since 2026-08-04 the style
  directives differ per response. Live header:

  ```
  default-src 'self'
  script-src      'self' 'nonce-<per-request>'    ← strict
  style-src-elem  'self' 'nonce-<per-request>'    ← strict: no inline <style>

    # standalone document routes (style attributes hoisted) — 2026-08-04
  style-src-attr  'none'                          ← ZERO exceptions
  style-src       'self' 'nonce-<per-request>'    ← fallback, also strict

    # Jinja / SPA shell
  style-src-attr  'unsafe-inline'                 ← accepted, measured inert
  style-src       'self' 'unsafe-inline'          ← ignored by modern browsers

  object-src 'none' · base-uri 'self' · form-action 'self'
  font-src 'self' data: · img-src 'self' data: blob: · connect-src 'self'
  frame-ancestors 'self'
  ```

  Modern browsers honour the more specific `-elem`/`-attr` directives and ignore
  the `style-src` fallback, which exists only for browsers that don't understand
  the split.

- **The four standalone document routes have no style exception at all.**
  CSP provides **no nonce mechanism for style attributes** — a nonce attaches to
  an *element*, and `style="…"` is not one — so `'unsafe-inline'` cannot be
  replaced by a nonce, only *deleted*, which means the attributes themselves have
  to go. `app/core/style_hoist.py` rewrites the finished HTML, moving each
  `style="…"` into a **nonced `<style>` element** behind a `data-csp-h` hook.
  Letterhead documents, receipts, the designer print preview and the candidate
  profile print now serve under `style-src-attr 'none'`.

  Cascade fidelity is preserved by a **doubled selector**,
  `[data-csp-h="hN"][data-csp-h]` — specificity (0,2,0), high enough to outrank
  the documents' class rules, deliberately below the `!important` print overrides
  that must keep winning. Verified in Chromium rather than argued: computed-style
  comparison of original vs hoisted output showed **0 property and 0 geometry
  diffs**, and all four live routes under the enforced header reported **0 CSP
  violations** with **0** server-rendered style attributes left.

- **The shells keep the `-attr` exception, and still need it.** `safe_dom.js`
  builds nodes at runtime with `setAttribute('style', …)`, which hoisting cannot
  reach. The avoidable dynamic cases have been removed: progress widths and tenant
  brand colours are now carried as bounded `data-*` values and applied through
  CSSOM (`el.style.width` / `el.style.backgroundColor`) after safe DOM insertion.
  The remaining exception was accepted on measurement, not argument: a style
  *attribute* cannot execute script, so the worst case is UI redressing, already
  constrained by `frame-ancestors 'self'` and strict `script-src`. Closing it too
  requires finishing the legacy/sanitized shell surfaces, not weakening scripts.
  Background: [`docs/CSP_HARDENING_OPTION_B.md`](docs/CSP_HARDENING_OPTION_B.md).

  > Enforced by CI, not by memory: `tests/test_csp_policy.py` fails the build if
  > `script-src` loses its nonce or gains `unsafe-inline`/`unsafe-eval`, if
  > `style-src-elem` weakens, or if the hardened dynamic template values regress
  > to string-injected `style="..."`; `tests/test_csp_zero_exception.py` fails if a
  > hoisted response regains any `'unsafe-inline'`, if the shell silently loses
  > the exception it depends on, or if the tightening leaks across responses.

**Release packaging:** `scripts/make_release_zip.sh` excludes all secrets and
non-distributable artifacts. Only `.env.example` ships; `.env`, `.env.production`,
`.env.local`, `.env.postgres`, `.env.sqlite`, live `data/`, caches, `.git`, and
`venv` are excluded — enforced by a self-audit that **hard-fails** the build if
any forbidden file slips in (defense-in-depth: excluded at packaging **and**
denied at audit).

**Verification (this environment, 2026-08-04):** full pytest suite
**590/590 passing**; focused CSP/sort-key hardening tests **30/30 passing**;
`ruff` clean for app/tests/scripts plus the new Alembic migration; temp-SQLite
`alembic upgrade head` reaches `8a4d6c2b1e90`; `scripts/scan_xss_sinks.py --check`
reports **0** findings; frontend `tsc -b` + `vite build` succeeds and npm audit
reports **0** vulnerabilities.

📄 **Full write-up:** [`AUDIT_RESPONSE_2026-07-26.md`](AUDIT_RESPONSE_2026-07-26.md)
— per-finding remediation detail, the enumeration bug the audit did not report,
runtime verification evidence, and the one decision still owed by the owner.

---

## 🏢 Enterprise Frontend Hardening — 2026-08-01 (latest)

A front-end-only release: **no route, API, schema, permission, tenancy or
business-logic change**. Every edit is in `app/frontend/src/**`,
`app/static/css/app.css`, `vite.config.ts`, plus one additive line in
`app/main.py`. The Python backend is byte-identical apart from that line.

### Design direction — deliberately flat

An earlier iteration of this pass used multi-stop gradients on the sidebar
masthead, avatars, primary buttons and the DtcBot header. It read as consumer-app
decoration rather than business software and was **reverted**. The design system
is now built on four solid tokens:

```css
--brand-solid: #1d4ed8;  --brand-solid-hover: #1a44bd;
--brand-tint:  #eff4ff;  --brand-rule:        #dbe4f5;
```

`.theme-forest-green` overrides the same four, so the alternate theme needed no
extra rules. Identity comes from **one disciplined accent, generous whitespace
and precise typography** — not from colour effects. Concretely: the sidebar brand
block is a plain white masthead with a hairline separator; the active nav item is
a flat tint with a 3px solid rail; the breadcrumb band is white; the route
progress bar is 2px with no glow; card hover-lift and button press-bounce were
removed.

### Navigation & information architecture

- **Sidebar v2** — nav is banded into four labelled sections (OVERVIEW /
  OPERATIONS / DIRECTORY / ADMINISTRATION). **No menu item was added, removed or
  renamed.** Rows are memoized, expandable groups sync to the active route, and
  the desktop collapse-to-rail state persists. Icon alignment and 44px hit
  targets are uniform.
- **Breadcrumbs** — new `layout/routeMeta.ts` + `layout/Breadcrumbs.tsx` render a
  hierarchy band on every app route. Detail pages publish their real record name
  through the `useCrumbLabel()` hook (client company name, demand file number)
  instead of showing a raw id.
- **Global search** — the duplicated search implementation in `Topbar.tsx` was
  extracted to `layout/GlobalSearch.tsx` and is now shared by the desktop inline
  field and a new phone magnifier → portalled sheet. Below 640px the header
  search used to be `display:none`; it is reachable again.

### Mobile

- Verified at 390×844, 820×1180 and 1440×900 across
  `/dashboard /clients /candidates /demands /documents /settings /users` —
  **zero horizontal overflow** on all 21 combinations.
- Six more tables converted to the `responsive-cards` + `data-label` pattern
  (3 in `MiscPages.tsx`, 3 in `ClientDetail.tsx`) so they stack into labelled
  cards instead of scrolling sideways.
- Modals become bottom sheets under 640px (`max-height: 92dvh`, safe-area
  padding, sticky header, scroll containment).
- The mobile drawer now closes on **Escape** as well as backdrop tap.

### DtcBot on mobile

Presentation only — the bot's API calls and conversation flow are untouched.
Full-screen `100dvh` sheet under 640px, docked 380px panel on tablets; composer
pinned above the fold with safe-area padding; 44px controls; `role="dialog"` +
`role="log" aria-live="polite"`; Escape closes; `body.dtc-open` hides the FAB and
locks background scroll.

### Performance

| Change | Effect |
|---|---|
| Function-based `manualChunks` | `vendor-react` was **0.04 kB** — the object form only matched bare entry ids, so the React runtime was being bundled into `vendor-router`. Now correctly split: vendor-react **142.22 kB**, vendor-router 23.29 kB, vendor-http 45.57 kB |
| `AuthContext` boot | Two serial round-trips → one `Promise.allSettled`; a failing permissions call no longer blocks sign-in |
| Context value memoization | `AuthContext` and `Toast` values wrapped in `useMemo` — stops whole-tree rerenders on unrelated state changes |
| `Toast` timers | Tracked in a ref and cleared on unmount (previously leaked) |
| Dashboard polling | Stops while `document.hidden`, refetches on return — no background-tab traffic |
| `Unauthorized` page | Was both `lazy()` and statically imported → Rollup dual-import warning and a wasted chunk; made static |
| Page transition | `page-enter`, 140ms, opacity + 4px translate only (GPU properties, no layout work), keyed on pathname, disabled under `prefers-reduced-motion` |

### Cleanup

- Dead `/agents` quick action on the dashboard replaced with `/documents`.
- `header.topbar { overflow: hidden }` at ≤768px removed — it was clipping the
  global search results dropdown on tablets.
- Obsolete `#profileChip` and search-hide rules removed from the legacy CSS.
- New shared primitives in `components/ui.tsx`: `Skeleton`, `TableSkeleton`,
  `ErrorState`, a richer `EmptyState`, and a `Modal` with Escape + body
  scroll-lock. All existing exports remain source-compatible.
- Version drift fixed: `app/main.py` now injects `APP_VERSION` into
  `window.__TENANT_BOOT__` and the footer reads it (verified live: `v1.1.0`).

### Verification

- `tsc --noEmit` — clean.
- `vite build` — clean, **no warnings**, 6.3s.
- Playwright sweep (`3 viewports × 7 routes` + bot, drawer and search
  interaction checks) — **0 problems**; the only console output is the six
  expected pre-auth 401s on the login page.

> ⚠️ **Build note for this sandbox (985 MB RAM):** stop the app before building
> or `tsc` will OOM-hang —
> `pm2 stop dogar-portal && npm run build && pm2 start dogar-portal`.

---

## 📦 System Dependencies (host packages)

The Python side of the app depends on a handful of native libraries that must
be present on the host. On a fresh Debian / Ubuntu box:

```bash
sudo apt-get update
sudo apt-get install -y \
    tesseract-ocr tesseract-ocr-eng \
    postgresql-client libpq-dev \
    libjpeg-dev zlib1g-dev libfreetype6-dev libcairo2-dev unzip
```

On **Nix-based systems** declare the equivalent packages:

```nix
{ pkgs }: {
  deps = [
    pkgs.python311
    pkgs.python311Packages.pip
    pkgs.freetype pkgs.libjpeg pkgs.zlib pkgs.cairo pkgs.unzip
    pkgs.tesseract4      # ← required for offline passport OCR
    pkgs.postgresql      # ← required only if DATABASE_URL is Postgres
  ];
}
```

### What each package unlocks

| Package                 | Feature enabled                                                                                                    |
|-------------------------|--------------------------------------------------------------------------------------------------------------------|
| `tesseract-ocr`         | **Offline passport OCR** — the ICAO-9303 MRZ parser used by the Assign Wizard "Scan Passport" step.                |
| `tesseract-ocr-eng`     | English language pack for Tesseract. Without it MRZ extraction is empty.                                           |
| `postgresql-client` / `libpq-dev` | psycopg2's runtime + build dependency. Only needed if you point `DATABASE_URL` at Postgres (see `docs/RUN_MIGRATION.md`). |
| `libjpeg-dev` / `zlib`  | Pillow image codecs (candidate photos, background images).                                                         |
| `libfreetype6-dev`      | Font rendering for PDF barcodes.                                                                                   |
| `libcairo2-dev`         | Vector rendering used by reportlab / svg helpers.                                                                  |
| `unzip`                 | Template / background archive extraction on first run.                                                             |

### Passport OCR — graceful degradation

If Tesseract is **not** installed (`which tesseract` returns nothing) *and* the
AI-vision LLM key is not configured, the Assign Wizard's "Scan Passport" step
now **returns HTTP 200 with a machine-readable `reason: 'no_ocr_backend'`** and
a `user_message` that the SPA renders inline as:

> ⚙️ Automatic passport scanning is unavailable on this server (offline
> Tesseract not installed and AI vision not configured). Please type the
> passport details into the form below — the fields are ready for you.

The endpoint used to raise 503 in this case, which the SPA showed as a silent
global toast. The new behaviour keeps the wizard **fully usable** by hand
even on a minimal environment where the Tesseract binary hasn't been
installed yet.

You can query the current backend state at any time:

```bash
curl -s -b cookies.txt http://localhost:3000/api/ocr/backends | jq
```

Returns:

```json
{
  "tesseract": true,
  "ai_vision": false,
  "available_backends": ["tesseract"],
  "install_instructions": {
    "tesseract": { "debian": "sudo apt install tesseract-ocr tesseract-ocr-eng",
                   "nix": "add `pkgs.tesseract4` to your Nix packages",
                   "macos": "brew install tesseract" },
    "ai_vision": { "env_var": "Set OPENAI_API_KEY and restart" }
  }
}
```

---

## ✅ Fresh Production Install — 2026-07-05

This package is a **clean, fresh production install** with no legacy demo data:

- `data/dogar_trading.db` and `data/tenants/` were deleted and rebuilt from scratch.
- A new 64-character `SECRET_KEY` was generated.
- Strong bootstrap passwords were set for the admin and vendor accounts.
- The React SPA was rebuilt (`cd app/frontend && npm install && npm run build`).
- The full test suite passed: `TESTING=true pytest -q` → **88 passed, 0 failed**.
- Admin and vendor passwords were rotated after first install; `must_change_password` is `False`.
- The server is running with `ENV=production` via `pm2 start ecosystem.config.cjs`.

**Live preview (verified 2026-07-23):** https://3000-i38bilxl9287q6qts4tiu-b9b802c4.sandbox.novita.ai

**Bootstrap credentials** are **not** printed here for security reasons. On a
fresh install the accounts are created from the values you set in your **`.env`**
file (`DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD`,
`VENDOR_ADMIN_EMAIL` / `VENDOR_ADMIN_PASSWORD`). If you have lost the admin
password, reset it with:

```bash
./venv/bin/python scripts/reset_admin_password.py
```

> Never commit real credentials to `README.md`, `RUN.txt`, or any other file
> that ships in the distributable archive. If a password was ever written to a
> shipped file, rotate it immediately.

Before going fully live, replace the placeholder SMTP credentials in `.env` with real provider credentials and set `APP_BASE_URL` to your public domain.

---

## 🛠️ Critical UX Fixes & Enterprise Polish (2026-07-05 — session 6)

Root-caused and fixed the real deployed-environment bugs (verified with a real
mobile Playwright browser, not curl):

- **CSP-blocked inline handlers (portal-wide, ROOT CAUSE)**: the strict
  `script-src 'self' 'nonce-…'` CSP (no `unsafe-inline`) silently **blocked all
  49 inline `onclick=` handlers** in `base.html` — the mobile hamburger, profile
  menu, alerts bell, submenus, modals and logout all did nothing on the live
  site. Added a **nonce'd CSP-safe rebinder** that promotes every inline handler
  attribute into a real `addEventListener` listener (with a `MutationObserver`
  for dynamically-added nodes), keeping the strict CSP intact. The mobile
  sidebar/hamburger + backdrop are additionally bound **explicitly** (with
  `data-csp-skip` to avoid double-firing) and given `open/close` helpers + Esc
  support. **Verified**: hamburger opens/closes drawer, backdrop closes it.
- **Designer mobile tab control stacking vertically**: the Fields/Canvas/
  Properties segmented control relied on Tailwind `grid-cols-3` which failed to
  resolve on the CDN build → tabs stacked as big blocks. Replaced with a
  **bulletproof pure-CSS flex segmented control** (iOS-style pill, active tab
  white with ring). **Verified**: all 3 tabs on one row, switching works.
- **eBarcode**: confirmed compact (17mm × ≤70mm), crisp vector SVG, human-
  readable value embedded under the bars — professional & scannable.
- **Portal scrolling**: reference-counted `scrollLock.ts` (SPA) confirmed —
  dashboard scrolls on mobile, no stuck/frozen viewport.
- **Enterprise polish (v12 CSS layer)**: hairline borders, `:focus-visible`
  rings, sticky data-table headers, tab transitions, slim scrollbars, content-
  visibility perf, reduced-motion guards.
- Full test suite: **88 passed, 6 skipped**. SPA rebuilt (`index-Bv0GRC5j.js`).

## ✨ Portal Reliability & Designer Upgrade — 2026-07-22

- **Sidebar backups:** authorized settings users can create, list, and download
  tenant-prefixed ZIP backups without leaving the current page. Each archive
  contains a manifest, JSONL exports of every reflected database table, and
  configured upload/PDF-background assets. Routes: `GET/POST /api/backups/`
  and `GET /api/backups/{filename}`.
- **Candidate filtering:** `/candidates` keeps text search and adds searchable
  multi-select Client, Demand File, Category, and Status filters. The backend
  accepts comma-separated `clients`, `demands`, `categories`, and `statuses`
  values on `GET /api/candidates/`.
- **Demand wizard resilience:** form values, positions, active step, and
  attachment metadata are versioned in `localStorage` and synchronized across
  tabs. Structured FastAPI/Pydantic, Zod, and field-map validation responses
  render inline, focus the first invalid field, preserve the draft, and never
  become global toasts. Non-field failures produce one toast. A synchronous
  request lock and disabled loading state prevent duplicate submissions.
- **Document designer:** Text box, Checkbox, Barcode, and Trade Table primitives
  can be clicked or dragged to an exact canvas position and remain fully
  configurable. Typography controls now flow through canvas, HTML print, and
  ReportLab PDF rendering: font family, weight, italic, underline,
  strike-through, text/background color, opacity, padding, line height, letter
  spacing, alignment, and multiline wrapping.
- **Paper parity:** A4, Letter, and Legal dimensions persist on templates and are
  used by the canvas, named CSS print page size, and PDF geometry.
- **Verification (2026-07-23):** TypeScript/Vite production build passes; Ruff
  passes; the complete backend suite passes (**215 tests at that date; 279 as of
  v1.1.0 — see PROJECT_DOCUMENTATION.md §14 for the current figure**), and all
  **4 Demand Wizard Playwright tests pass**. The E2E suite in
  `app/frontend/e2e/demand-wizard.spec.ts` covers cross-tab state, attachment
  metadata, structured errors, toast rules, focus, state preservation, pending
  state, and one-request double-submit protection.

### Current functional entry URIs

- `/demands` — demand list and synchronized three-step creation wizard.
- `/candidates` — candidate list with text and multi-select filtering.
- `/documents/customize/{template_id}` — coordinate template designer.
- `/api/backups/` — backup list/create API used by the sidebar dropdown.
- `/api/backups/{filename}` — secure tenant-scoped backup download.

### Development and E2E commands

```bash
ENV=development ./venv/bin/pytest -q
./venv/bin/ruff check app tests
npm --prefix app/frontend run build
cd app/frontend
npx playwright install --only-shell chromium  # one-time browser runtime
npm run test:e2e                              # portal must run on :3000
```

### Data architecture and next steps

Backups read through the active tenant SQLAlchemy session and write portable
ZIP archives under `data/backups/`; regular portal records remain in the
configured SQL database and assets remain in configured upload directories.
Before a multi-tenant production rollout, confirm that each tenant's asset
storage paths are physically isolated. The Demand Wizard Playwright suite is
verified with Chromium in the sandbox. Recommended follow-up: run the complete
browser suite in CI and perform a visual print comparison for representative A4,
Letter, and Legal forms.

## ✨ Enhancements — 2026-07-11

Eight user-requested fixes after the NBP Fee / OEP Form video review. Full
detail in [`ENHANCEMENTS_2026-07-11.md`](ENHANCEMENTS_2026-07-11.md).

- **NBP Fee → one complete 2-page document** and **OEP Form → front + back in
  a single 2-page document**, via a new **per-page background** engine
  (`page_backgrounds` designer meta) in both the print-HTML and PDF renderers.
  The standalone "OEP Form Page 2" template is retired.
- **"HBL" → "NBP Form 32"** — corrected the mis-labelled template name
  (rename-in-place migration preserves field coordinates).
- **Professional document list** — removed the PDF icon + guided-text banner;
  clean sectioned layout with a single Print action per row.
- **Roles = real access control** — full RBAC editor with **Add New Role**,
  permission checkbox matrix (10 modules), per-module select-all, presets,
  edit & delete, wired to the `/users/roles` CRUD endpoints (Admin keeps `*`).
- **Barcode look & size** — each barcode is a labelled card (**E-Number** /
  **Passport No**) in a bordered quiet-zone box with a monospace value caption;
  both cards share an identical fixed size; crisp SVG bars.
- **Performance** — 1-year immutable cache for content-hashed SPA bundles;
  image-aware print timing (fires when barcodes/backgrounds decode, 1.2 s cap).
- **Real-time notifications** — the header bell is now a live dropdown backed
  by the reminders API (30 s unread-badge polling, mark-read / dismiss /
  mark-all-read / scan, click-through to the linked record).
- **Change Password** — added to the profile dropdown with a working modal
  (`POST /users/me/change-password`, validated).
- **DtcBot fixes** — in-chat printing now works (print payload normalized to a
  real URL) and the offline fallback welcome includes the **Add new client**
  chip; the add-client flow creates a real client record.
- Full suite: **144 passed**, ruff clean, tsc clean, SPA rebuilt.

## ✨ Enhancements — 2026-07-10 (Session 2)

Seven user-requested improvements, all verified live. Full detail in
[`ENHANCEMENTS_2026-07-10.md`](ENHANCEMENTS_2026-07-10.md).

- **Doc Coordinate Engine background fit modes** — Fill / **Contain** / **Actual size**
  (persisted as `bg_fit` in the designer metadata). Fixes the stuck auto-zoom and the
  stretched-image distortion; **barcodes stay pixel-faithful & scannable** (lossless
  PNG, aspect preserved). 6 new tests (`tests/test_bg_fit.py`).
- **Login & create-account UI** — per-field validation, polished error/toast messages,
  aria states, shake-animated alert, better spacing.
- **Image upload/preview flow** — reusable `ImageUpload` (drag-drop, crisp preview,
  consistent crop box, real progress bar) wired into tenant logo + candidate photo.
- **No full-page reloads** — `<Suspense>` moved inside `AppLayout` so lazy route loads
  swap **only** the content pane; sidebar/topbar stay mounted.
- **Reconnect refetch** — `useReconnect` hook + offline banner; on online/refocus the
  candidate list **and** open side sheet refetch, so no stale rows. New E2E
  `scripts/e2e_reconnect.py` (**7/7 pass**).

Regression: **144 pytest passed**, **ruff clean**, **E2E smoke 5/5**, **reconnect 7/7**.

---

## 🔧 Live Bug Fixes + Security Audit (2026-07-10)

Resolved three **blocking live runtime bugs** and applied every finding from
`DOGAR_PORTAL_AUDIT_2026-07-10.md`. Full detail in
[`FIXES_APPLIED_2026-07-10.md`](FIXES_APPLIED_2026-07-10.md).

**Runtime bugs (all verified fixed via live Playwright E2E — 5/5 pass):**
- **Login auto-reload loop** — the 401 interceptor no longer hard-reloads on the
  silent `/users/me` auth probe, and `isOnPublicPath()` now strips the tenant base.
  (`app/frontend/src/lib/api.ts`)
- **Full-page reload on every click** — the candidate "Open file" action now uses
  React Router `<Link>` instead of a raw `<a href>`, so navigation stays client-side.
  (`app/frontend/src/pages/Candidates.tsx`)
- **Candidate detail redirect from a demand file** — `DemandDetail` now reuses the
  shared `CandidateDrawer`, opening the detail in place (URL stays on `/demands/<id>`).
  (`app/frontend/src/pages/DemandDetail.tsx`)

**Security audit fixes:** H-1 (PDF background path-traversal hardening + 5 tests),
H-2 (Docker `dogar-pdf-bg` volume), M-1 (JWT lifetime 24h→2h), M-2 (proxy-aware
rate-limit key), M-3 (`ALLOWED_HOSTS` prod guard), M-4 (healthcheck `/api/ping`),
M-5 (no baked-in admin password), L-1/L-2/L-6 (docs + prod guards).

**Verification:** `pytest` → **138 passed** · `ruff` → clean · `tsc -b` → 0 ·
`scripts/e2e_smoke.py` → **5/5**.

## 🔧 Audit Fix & Production-Readiness (2026-07-03 — session 4)

Full codebase audit remediation — every CRITICAL/HIGH finding fixed:

- **C-2 Python toolchain**: the Nix manifest provides `python311`, `pip`,
  `setuptools/wheel` plus the system libs the wheels need (`freetype`,
  `libjpeg`, `zlib`, `cairo`, `unzip`).
- **C-3 blocked migration**: `document_templates` added to the H-08
  `_ALLOWED_TABLES` allowlist in `init_db.py` — the `page_size` /
  `orientation` column-adds now execute instead of being silently blocked.
- **H-1 psycopg2-binary**: moved to a commented optional section (project is
  SQLite-only; absent libpq on minimal hosts would break `pip install`).
- **H-2 heavy test deps**: `playwright` / `pytest-playwright` (+ pytest,
  httpx, openpyxl) moved to new **requirements-dev.txt**.
- **H-3 freetype-py**: system `freetype` lib supplied by the host packages.
- **H-4 Alembic confusion**: no alembic.ini/migrations exist — misleading
  "schema management is exclusively via Alembic" comment in `app/main.py`
  rewritten to document the real runtime path (idempotent
  `run_schema_migrations()` + `create_tables()` seeders in `_on_startup`).
- **H-5 email fallback**: `send_email(html=True)` no longer attaches raw HTML
  as the text/plain part — new `_html_to_text()` produces a proper plain-text
  fallback (links preserved as `label (url)`), plus optional `plain_body` arg.
- **H-6 token leak**: the raw password-reset token is no longer written to
  logs at ANY level when SMTP delivery fails.
- **M-4 PM2 config**: verified `ecosystem.config.cjs` targets uvicorn (not a
  nonexistent `src/index.js`); documented the no-venv variant.
- **L-1**: removed unreferenced duplicate `vendor/css/fontawesome.css`.
- **L-2**: `zipFile.zip` / `*.zip` added to `.gitignore`.
- Verified: `/api/ping` 200, `/` SPA 200, all startup seeders green, template
  table has `page_size`+`orientation`, full pytest suite passing.

## 🔧 Fixes & Enhancements (2026-07-03 — session 3)

- **Coordinate engine trimmed to the 9 official templates** (owner-approved
  scope). PROTECTOR: OEP Form front + back, NBP Deposit Slip (both pages),
  HBL Form 32, Allied Bank Deposit Form. VISA PROCESS: Visa Application Form —
  Karachi, Visa Application Form — Islamabad, Passport Submission Letter —
  Islamabad (ISB candidates only). All other templates (duplicated KHI form,
  permission/undertaking/letterhead duplicates) are **auto-deleted from the DB
  at startup**. Verified live: exactly 9 templates, all with coordinate fields.
- **Embassy-aware visa forms**: `/api/documents/templates?embassy=…` hides the
  other city's visa form server-side; a candidate's Documents tab shows only
  the form matching their demand's embassy (KHI ⇄ ISB). The E-Barcode remains a
  generated document (`/api/protector-letters/e-barcode/{id}`), not a template.
- **3-step "Add Demand" wizard** (Demand file details → Sponsor information →
  Visa positions) with auto-generated file number, searchable client select +
  inline **Add Client**, searchable visa category + inline **New Category**,
  position counters and validation gates — matches the reference design.
- **Date-string hardening**: `POST/PUT /api/demands/` now safely coerces ISO
  (`2026-06-20`) and `dd/mm/yyyy` date strings into Python dates for every
  Date column — no more 500s from the SPA wizard.
- **Protector Letters module rebuilt** (legacy `protector.formN` workflow):
  new page at `/protector-letter` — type passport numbers → **Get Data** builds
  the candidate batch table (Passport, Name, Father, Permission No & Date,
  Category, Sponsor) → green print buttons render **Registration of N Foreign
  Service Agreements** (main letter), **Undertaking**, **Undertaking-B** or the
  **Full Packet** on the company letterhead with auto-print. New iframe-friendly
  GET endpoints: `/api/protector-letters/print/{main|undertaking|undertaking-b|packet}?ids=1,2,3`.
- **Embassy column** added to the candidate list (teal = Karachi, indigo = ISB).
- **Add New Client modal** rebuilt to the reference layout (Client Type,
  Status, Email/Phone, Street/City/State/Country, Opening Balance
  amount/type/date, Notes).
- Tests: full suite green (embassy-filter ×4 + demand-wizard ×4 added).

## 🔧 Fixes & Enhancements (2026-07-02 — session 2)

- **Assign-Wizard invoice promise honoured**: the wizard's final "Charge
  Summary" step says *"An invoice will be created for this client and added to
  their statement."* — the backend now actually creates a `ClientStatement`
  **INVOICE** (debit) entry tied to the demand file whenever a candidate is
  assigned with a price > 0 (shows in the client statement *and* the demand's
  Payments tab). Reference format: `ASSIGN-<candidate_id>`.
- **Coordinate seeding fixed for all environments**: the automatic starter
  coordinate seeding in `init_db` used a raw `sqlite3.connect()` against a
  hand-parsed path + a CWD-dependent `scripts.` import — it silently failed
  when uvicorn was started from a different working directory, leaving
  templates with **zero fields**. Now reuses the already-open SQLAlchemy
  session (works on SQLite *and* PostgreSQL) and loads the layout module by
  absolute file path. Verified: 225 coordinate fields seeded.
- **In-process TTL cache**: when `REDIS_URL` is not set, list endpoints now
  use a bounded (512-entry) thread-safe LRU+TTL in-memory cache instead of the
  no-op stub — same JSON round-trip contract as Redis, tenant-scoped keys,
  disabled automatically under `TESTING`/`DISABLE_CACHE`.
- **New template**: *Visa Form Regd - Karachi* (bilingual EN/AR KHI visa form
  with Regd No) added with its HD background scan `khi_visa_regd.jpg`.
- **Duplicate page removed**: the old read-only React `DocumentBuilder` page
  duplicated the Template Designer; `/documents/builder/:id` now 301-redirects
  to `/documents/customize/:id` and the dead React route/component is deleted.
- **Tests updated for SVG barcodes** (crisp vector output): 65 passing.

## 🔧 Fixes & Enhancements (2026-07-02 — session 1)

- **UI overlap / header-override bug — FIXED (root cause)**: All slide-over
  drawers and modals (candidate detail, demand candidate panel, shared `Modal`,
  client-detail `ModalShell`, `AssignWizard`) are now rendered through
  `ReactDOM.createPortal(..., document.body)`. Previously they were mounted deep
  inside the transformed layout container, so `position: fixed` resolved against
  an ancestor stacking context instead of the viewport — causing the panel to
  overlap the table and the header to look mismatched. Overlays now anchor to the
  true viewport (full-height, right-edge slide-over) with a dimmed + blurred
  backdrop and a smooth slide-in animation.
- **Clickable references**: In the Candidates and Demands lists, the blue
  **Client** and **Demand File** cells are real links to `/clients/:id` and
  `/demands/:id` (row-click stop-propagation preserved).
- **Coordinate document engine (React, 2026-07)**: the Document Designer now
  runs as a first-class SPA page (`/documents/customize/:id`) built on a clean
  module set under `app/frontend/src/pages/designer/` — `engine.ts` is the
  single source of coordinate truth (PDF points, bottom-left origin;
  `cssY = pageH − y − height`; baseline-locked text with `ASCENT_RATIO 0.8`)
  matching the print-HTML and PDF renderers exactly, plus `types.ts`
  (Jinja-identical defaults + meta fence), `barcode.tsx` (live JsBarcode/QR
  preview), `PalettePanel`, `PropertiesPanel`, and `FieldMarker`. Full parity:
  background upload/clear with aspect-lock, grouped palette,
  drag/position/resize with persisted coordinates, barcode/QR symbology,
  multi-page tabs, zoom/fit, custom page dims (pt+mm), live data preview, and
  keyboard nudging. The Jinja designer (`document_customize.html`) remains
  available as a fallback via `?legacy=1`. QR-code support spans the 3
  pipelines (designer preview via a local dependency-free vendor lib,
  print-HTML PNG via `qrcode`+PIL, and vector PDF via reportlab).
- **HD form backgrounds**: Allied Bank Form-7, Karachi visa, and Islamabad visa
  backgrounds replaced with clean HD scans (upscaled to A4 print resolution).
- **Project cleanup**: removed the unreferenced `pdf_backgrounds_real/` directory
  (~10 MB), orphaned background images, the unused `500.html` template, a stray
  empty root `package-lock.json`, the dead one-time `migrate_from_mysql.py`, and
  redundant intermediate audit docs; purged `__pycache__`/`*.pyc`.

---

## ✅ React Front-End Migration — Pixel-Identical SPA

The complete UI was re-implemented as a single-page React application that is
**visually pixel-identical** to the original Jinja portal — same Dogar logo,
brand identity, colors, sidebar/topbar layout, components, theme switcher
(corporate-blue / forest-green), DtcBot assistant, and footer.

### Architecture
- **Stack**: Vite 5 + React 18 + TypeScript 5 + Tailwind 3 + React Router 6 + axios 1.7
- **Source**: `app/frontend/` (React app, TypeScript)
- **Build output**: `app/frontend` builds into `app/static/spa/` (committed)
- **Serving (single-origin)**: FastAPI serves the React bundle from its existing
  `/static` mount. A small, additive block in `app/main.py` registers the SPA
  page routes + a final catch-all (gated by `settings.USE_SPA`, default `True`).
  **No existing API endpoint, route handler, module, function, DB behaviour, or
  template was modified** — the SPA serving is purely additive.
- **Auth**: unchanged — JWT in an httponly `access_token` cookie. The React
  client uses `axios` with `withCredentials: true`; the browser auto-sends the
  cookie. A 401 interceptor redirects to `/login?next=…`. On mount the app calls
  `GET /api/users/me` to restore the session after a page refresh.
- **Pixel-match strategy**: the React `index.html` loads the *same* CSS stack as
  the original `base.html` — Tailwind CDN + FontAwesome 6.4.0 +
  `/static/css/app.css?v=17.0` + Inter/JetBrains Mono fonts + the same
  `tailwind.config` + the same theme-switch IIFE — so every existing utility and
  component class (`.nav-item`, `.card`, `.data-table`, `.badge`, `.btn-primary`,
  `.form-input`, …) renders identically.

### Verified (desktop + mobile QA)
- **Login** — split office-hero / sign-in-card layout, exact match
- **Dashboard** — greeting, Quick Actions, 4 stat cards (live data), stage
  badges, and Chart.js line/donut/bar charts
- **Candidates** — filters + data table with status pills + detail drawer + SMS
- **Clients / Demands** — searchable data tables
- **Users / Roles / Login history** — CRUD + expanded sidebar group
- **Settings / Depositors / Service Charges** — forms + CRUD
- **Lookups** — Visa Categories, Embassies, Cities, Medical Centers, Contacts
- **Mobile** — hamburger drawer slides in with backdrop; responsive stat grid
- **Backend regression** — full pytest suite still green (59 passed, 6 skipped)

### Front-end dev / build
```bash
cd app/frontend
npm install              # one-time
npm run build            # -> app/static/spa/ (served by FastAPI)
npm run dev              # Vite dev server :5173 (proxies /api,/static to :3000)
```

### Toggling back to legacy Jinja templates
Set `USE_SPA=0` in the environment and restart — FastAPI then serves the
original `*.html` templates instead of the React bundle.

---

## ✅ Enterprise UI/UX Refinement Pass — Clean SaaS Frontend

A focused frontend polish pass has been applied without changing the portal's
workflow, modules, backend routes, templates, or business logic. The existing
sidebar-led portal structure remains intact, while the visual system is now
cleaner and more enterprise-ready.

### Completed frontend improvements
- Added a final shared CSS layer: **`v11 ENTERPRISE SAAS REFINEMENT — LIGHT & CLEAN`** in `app/static/css/app.css`.
- Reduced the previous heavy glass/gradient feel in favor of a lighter SaaS surface system.
- Standardized sidebar navigation spacing, active states, icon rhythm, and hover treatment.
- Unified cards, dashboard sections, modal/drawer panels, dropdowns, and stat surfaces with subtle borders and soft shadows.
- Normalized button sizing, radius, typography, hover states, and restrained blue/indigo accent styling.
- Improved form inputs/selects/textareas with consistent height, border, focus ring, and label styling.
- Refined tables with cleaner headers, row padding, readable separators, hover states, and scrollbars.
- Unified badge styling across pending/processing/issued/deployed/success/error/draft states.
- Preserved the `theme-forest-green` variant while keeping it visually consistent with the enterprise-light design system.
- Added responsive refinements for tablet and mobile spacing while retaining the current sidebar/topbar behavior.

### Current functional entry URIs
- `GET /login` — sign-in screen.
- `GET /` — authenticated portal/dashboard entry.
- `GET /static/css/app.css` — shared portal styling, including the latest enterprise refinement layer.

### Verification
- CSS brace balance validated successfully.
- Local service restarted with PM2 (`dogar-portal`) on port `3000`.
- `GET /login` returned `200 OK`.
- `GET /static/css/app.css` returned `200 OK` and served the new v11 refinement layer.
- Browser console check on `/login` showed no app errors; only the existing Tailwind CDN production warning.

### Recommended next steps
- Review authenticated dashboard and module pages visually with real production data.
- If desired, convert remaining one-off Tailwind card/table patterns into shared semantic classes over time.
- Consider replacing Tailwind CDN with a compiled production CSS pipeline in a future hardening pass.

---

## ✅ Demo-OEP Parity Fix Pass — 7 Problems Resolved

All seven defects raised against the live demo OEP (`demo.oep.com.pk`) have been
fixed, verified on the running app, and committed. 59/59 tests pass.

| # | Problem | Fix | Verified |
|---|---------|-----|----------|
| 1 | Field palette showed raw DB keys (`name_arabic`) | Palette renders **clean labels only** (key kept in `data-key`/search) | Screenshot — Customer group shows only "Name", "Name (Arabic)", … |
| 2 | Agency/recruiting-office name missing on forms | `customer_*` fields resolved from `company_settings` (Dogar Trading Corporation) | API + canvas |
| 3 | "Occupation (Arabic)" + selected trade missing | Added `profession_arabic` + `selected_trade_visa_category` to palette & overlay resolver | Palette dump |
| 4 | OCR garbled (`SUFYAN KKKKSKK ALI`, wrong CNIC) | ICAO-9303 MRZ check-digit validation + name de-noising + bio-data enrichment | `SUFYAN KKKKSKK ALI`→`SUFYAN ALI` |
| 5 | Two E-Barcodes different sizes | Fixed **95 mm** barcode width + quiet-zone auto-crop | Letterhead CSS |
| 6 | Print/PDF coordinates drifted off the form | **Top-anchor** print HTML + PDF text to match the designer box model (`baseline = y + height − font_size`) | Print preview **and** generated PDF screenshots — every field lands on its form line/box |
| 7 | Phone (HEIC) photos not understood | `pillow-heif` HEIC decode + browser-side downscale + relaxed content-type | OCR pipeline |

**Problem 6 detail (coordinate fix):** the designer canvas (`.field-marker`)
places each field's text at the **top** of its box (`box top = page_h − y −
height`, `line-height 1.05`). reportlab/`drawString` and the old print HTML
instead bottom-anchored the text at the stored `y` baseline, so whenever
`height ≠ font_size` the printed/PDF text drifted `(height − font_size)`pt below
where the user positioned it. The print HTML (`.ov-field`) is now
`align-items: flex-start` and the PDF (`_render_field`) draws at
`baseline = y + height − font_size`, so **designer == print HTML == PDF**.

---

## 🛠️ Jun 2026 Fix Pass — Print, Barcode & OCR (READ THIS)

Three concrete defects were diagnosed with live evidence and fixed end-to-end:

### 1. Documents printed almost-empty (only name + barcodes, no background)
**Root cause:** the HTML print page (`/api/documents/templates/{id}/print`) loaded
its background via a network `<img>`. On Android/Chrome the print engine drops
network images **and** background graphics by default, so the printed page came
out blank with only the top text + barcodes visible.
**Fix:** the print endpoint now (a) **inlines the background as a base64 `data:` URI**
so it can never fail to load, and (b) forces `print-color-adjust: exact` on
`html/body/.doc-page/.doc-bg` so the browser keeps the background when printing.
Result: the printed page now matches the reportlab PDF exactly (full form + data).

### 2. E-Barcode rendering
Verified clean & professional: Code128, sharp bars, centered human-readable
caption (e.g. `E214226395`). Rendered by `pdf_engine.barcode_to_data_uri()` and
embedded both in the PDF and the HTML print overlay.

### 3. OCR "Scan Passport" returned "0 of 0 fields filled"
**Root cause #1:** the **`tesseract-ocr` system binary was not installed**, so the
offline OCR fallback hard-failed.
**Root cause #2:** the AI-vision path returns `401 Invalid/expired token` when the
LLM proxy key is stale.
With no working path, extraction always returned 0 fields.
**Fix:** install the system binary (`sudo apt-get install -y tesseract-ocr`) — the
offline ICAO-9303 MRZ parser then extracts 11+ fields reliably. Also fixed an
MRZ name-cleaning bug where Tesseract misread the `<` filler glyphs as a run of
`C`/`E` characters and glued them onto the name (e.g. `SAQIBECCCCCC...` → `SAQIB`).

> **⚠️ OCR system dependency:** offline passport OCR needs the Tesseract binary.
> Install it once on the host: `sudo apt-get install -y tesseract-ocr`.
> Without it (and without a valid vision LLM key) the scanner will report that
> automatic reading is unavailable and ask the user to type the fields manually.

### Field-coordinate question answered
- **"Occupation / Profession"** field key = `profession` (now exposed in the
  designer palette under the Candidate group). On templates it sits at e.g.
  Template 20 `x=375.0, y=534.4` (field id 193).
- **"Visa Category"** field key = `trade_visa_category` (Selected Trade group),
  e.g. Template 14 `x=370.7, y=367.3` (field id 3751).

---

## 🏢 Enterprise Release (Jun 2026 v6 — multi-tenant, RBAC, super-admin, side-panel redesign)

This release is the **enterprise-grade hardening pass** completing the
roadmap demanded in the v4/v5 audit. Highlights:

### 🔐 Vendor Super-Admin (support account)
> **Superseded by the C-03 security fix.** This section originally described a
> super-admin hardcoded to a personal Gmail address whose password was
> restored on every boot — i.e. a vendor backdoor the customer could not
> revoke. Both properties are gone. Current behaviour:

- Credentials come **only** from `VENDOR_ADMIN_EMAIL` / `VENDOR_ADMIN_PASSWORD`
  (see `.env.example`). **No email or password is hardcoded anywhere** in
  `app/` — verify with `grep -rn "hussnainmr07" app/` (returns nothing).
- Role: `admin` + `is_super_admin=True`.
- The account **cannot be deleted, demoted, or deactivated** via the API;
  even other admins get `403 Forbidden`.
- The password is **NOT** re-applied on later boots, so **the operator can
  change or revoke it** and the change sticks. Created with
  `must_change_password=True`.
- If `VENDOR_ADMIN_PASSWORD` is unset, seeding is **skipped entirely** in dev
  and **fails hard at boot** in production.
- Implemented via `seed_super_admin()` in `app/db/init_db.py`, wired into
  `app/main.py`'s startup hook.

### 🏬 Multi-Tenant (subdomain-based isolation)
- New module `app/core/tenancy.py`:
  - `TenantResolverMiddleware` reads the `Host` header on every
    request and attaches `request.state.tenant`.
  - `demo.oep.com.pk` → looks up `Tenant.slug='demo'` → swaps to that
    tenant's isolated SQLite DB via a thread-safe sessionmaker cache.
  - `localhost` / IP literals / `www.*` fall back to the control DB.
- Per-tenant branding (uploaded under `data/tenants/<slug>/`):
  - **Logo** — appears in navbar & on every printed document
  - **Letterhead** — A4 background for receipts, protector letters,
    candidate print profiles
  - **Office name, demand-file format, receipt template, primary color**
- New API endpoints on `/api/tenants`:
  - `POST /{id}/logo` — upload tenant logo
  - `POST /{id}/letterhead` — upload tenant letterhead
  - `PUT  /{id}/branding` — set office name / demand format / receipt template
  - `GET  /_meta/current` — current resolved tenant (public)
- Portal-admin UI (`portal_admin.html`) gains a **"Brand" drawer** per
  tenant with logo/letterhead upload widgets + identity form.

### 👮 Role-Based Access Control (RBAC) — granular permissions
- New module `app/core/permissions.py` with:
  - `PERMISSION_CATALOG` — 45 permission keys across 10 modules
    (Dashboard, Clients, Demands, Candidates, Documents, Protector,
    Receipts/Finance, Users/Roles, Settings, Audit & Security)
    <br>*Reduced from 49 keys / 11 modules on 2026-07-30, when the Sub-Agents,
    Reports, Audit Log and Data Migration screens were removed on the
    operator's request. Retired with them: `agents:view`, `agents:edit`,
    `reports:view`, `reports:export`, `audit:view`. See §4.11 of
    PROJECT_DOCUMENTATION.md.*
  - `ROLE_PRESETS` — sensible defaults for Admin/Manager/Staff
  - `user_permissions(user, db)` — resolver respecting super-admin
  - `require_permission("candidates:delete")` — FastAPI dependency factory
- API endpoints:
  - `GET /api/users/roles/_meta/catalog` — for the role-editor UI
  - `GET /api/users/me/permissions` — for frontend permission gates
- System roles are **idempotently re-seeded** on every boot using the
  latest presets, so newly added permission keys flow through.
- The `Admin` role is hard-locked to wildcard `*` — attempts to scope
  it down return `400`.
- Custom roles can be created freely via the new **roles.html** UI
  (Tailwind permission matrix with quick presets, group toggles).

### 🎨 Candidate Side-Panel — Professional Redesign
- Gradient hero header with photo / initials, name, ID & E-Number badges
- 8-stage horizontal workflow tracker (Received → Medical → E-Number →
  Visa → Clearance → Departure)
- 6 structured info cards: Personal · Identity & Passport · Contact &
  Address · Next of Kin · Linked Demand File · Visa Category
- **Print button now produces a true CV-style A4 page** on the
  letterhead (new route `GET /api/candidates/{id}/print-profile`)
- All document printing uses a **hidden-iframe direct-print pattern**
  — no new tabs, no popups, no screenshot bugs.

### 🔧 Critical Bugfix — Stage Update 500 Error
- Root cause: candidates with `Decimal` salary triggered
  `TypeError: Object of type Decimal is not JSON serializable` when
  the audit log row was committed.
- Fix: `app/services/audit.py` now coerces Decimal/date/datetime/
  bytes/lists/sets via `_json_safe()`; the commit is wrapped in
  try/except so audit failures **never break** the originating
  request.

### 📎 Demand File → Candidate Links
- In `demand_detail.html`, every assigned-candidate card now has a
  deep-link button (`<i class="fa-solid fa-arrow-up-right-from-square">`)
  that opens the Candidates page with the side-panel auto-focused on
  that candidate (`/candidates?focus=<id>`).

### 📄 E-Barcode Re-Categorized
- Moved from **Protector Documents** to under **Visa Category
  Documents** in the candidate documents drawer.

### ✅ Test Coverage
- `tests/test_super_admin.py` (6 tests) — super-admin idempotency, login,
  delete/demote protection, password restoration.
- `tests/test_audit_json_safety.py` (5 tests) — Decimal/date coercion
  + audit-doesn't-crash-request regression.
- `tests/test_rbac.py` (10 tests) — catalog integrity, preset sanity,
  user_permissions resolver, admin wildcard lock.
- `tests/test_tenancy.py` (6 tests) — subdomain parser, current-tenant
  endpoint, branding auth gates.
- **59 tests total — all passing.**

---

## 🆕 Hotfix Release (Jun 2026 v3 — letterhead-embed, clean E-Barcode, dedupe customize-save)

This patch fixes **three critical regressions** flagged in production
screenshots and ships a one-shot housekeeping cleanup:

### 1️⃣ Letterhead now ALWAYS prints (no more plain-white pages)
- **Problem**: when the printable Protector Letter Packet / Demand Packet
  HTML was opened via `blob:` URL (the `/protector-letter` workflow page
  POSTs the payload, wraps the response in a `Blob`, and `window.open()`s
  it), relative paths like `/static/pdf_backgrounds/dogar_letterhead.jpg`
  intermittently failed to resolve back to the parent origin → the
  letterhead JPG vanished in the print preview and documents printed on a
  plain white sheet.
- **Fix**: `app/services/letterhead_renderer.py` now embeds the
  letterhead as a **base64 `data:image/jpeg;base64,…` URL** (read once
  per process from `app/static/pdf_backgrounds/dogar_letterhead.jpg` and
  memo-cached). Every `<img class="bg" src="…">` is now self-contained —
  the document looks identical whether opened from a direct GET, a
  `blob:` URL, `file://`, copy-paste, or saved-then-reopened.
- **Bonus alignment fix**: content margins re-tuned to match the
  letterhead asset's true geometry (header strip = top 15 %, footer
  strip = bottom 8 %). New defaults: `top=46 mm, bottom=30 mm,
  left=22 mm, right=22 mm` (was 38/28/18/18). Body content no longer
  collides with the company header band.

### 2️⃣ E-Barcode reverted to the legacy clean-print layout
- **Problem**: the printable barcode sheet was emitting two barcodes
  with `E-Number` / `File No.` text labels above each, padded
  irrelevant numbers (the 2nd barcode used a meaningless demand File
  Number padded with leading zeros), and a chunky `Powered by
  HussnainTechVertex Pvt Ltd.` developer strip at the bottom — none of
  which exists on the legacy dogars.com print the user wants us to
  clone.
- **Fix**: `render_e_barcode()` now renders:
  1. Candidate NAME (uppercase, top-left, 12 pt)
  2. **E-Number barcode** + `*VALUE*` underneath
  3. **Passport-Number barcode** + `*VALUE*` underneath
  
  No labels, no developer footer, no double-text, and the second
  barcode encodes the **Passport Number** (e.g. `ME1855631`) — never
  the demand File Number. Falls back to `candidate.id` zero-padded to
  9 digits if both passport and the legacy `file_number` arg are
  empty. Bars are uniformly **56 mm wide × 14 mm tall** so the layout
  stays stable regardless of how many characters each value encodes.

### 3️⃣ Document Customize save → duplicate-on-save bug killed
- **Problem**: every click of the **Save** button on the Document
  Designer (`/documents/customize/{tpl_id}`) appended a NEW row for
  every field instead of updating the existing one. A single Visa
  Application Form template had grown to **3 949 field rows** for what
  should be 23 unique fields (~170× duplication) because the bulk-save
  endpoint silently ignored matching by position when the client
  failed to send the `id` back.
- **Backend fix** in `POST /api/documents/templates/{tpl_id}/fields/bulk`:
  - **Position-match fallback** — when the incoming payload omits
    `id`, match an existing row by `(field_key, page, round(x),
    round(y))` before falling through to INSERT.
  - **Per-request dedupe** — drop exact-position duplicates *within*
    a single payload.
  - **REPLACE-ALL semantics** — any DB row whose id is not represented
    in the payload is deleted at the end. The frontend already
    submits the complete canvas state on every Save, so this makes
    the canvas the single source of truth.
- **Housekeeping**: new admin endpoint `POST /api/documents/admin/dedupe-all-fields`
  collapses all existing duplicates across every template (OLDEST row
  wins). Idempotent — safe to invoke any time. Ran once on the
  production DB during this release → template #14 went from 3 949 →
  23 fields, no data loss.
- **Per-template dedupe**: `POST /api/documents/templates/{tpl_id}/fields/dedupe`
  for ad-hoc cleanup of a single template.

---

## 🆕 Previous Release (Jun 2026 — Vercel-v0-style actionable DtcBot + official receipts + barcode polish)

This release upgrades **DtcBot** from a text-guided helper into a true
**actionable compute agent** (matching the Vercel v0 / Lovable pattern) and
adds two **official printable receipts** (Demand File + Payment) with logo,
watermark, and scannable QR code.

### 🤖 DtcBot — Actionable Agent (end-to-end OCR → data entry)
- `POST /api/chatbot/upload?auto_create=true` (now the default) — the bot
  **runs OCR on the uploaded passport** *and* immediately calls
  `dtcbot_agent.create_candidate()` (or `update_candidate()` if the passport
  / CNIC already exists), then returns a **`type: "action"`** response with
  `verb`, `candidate_id`, `url`, and `navigate: true`.
- The chat widget's `renderResponse()` now handles the `action` type:
  renders a green/blue/violet success card with the entered fields + Open
  Record button, and **auto-navigates** the parent window to the candidate
  edit screen after 1.8 s. **No more guidance text — the agent does the work.**
- Natural-language commands also produce actions:
  `create candidate named ALI HASSAN, passport AB1234567` →
  `set salary of ALI HASSAN to 1500` →
  `assign ALI HASSAN to trade #12` — each returns an actionable response
  with `navigate: true` and the canonical record URL.
- Upload-progress label: typing dots now show
  *"🤖 Reading passport · running OCR · creating candidate record…"*.

### 🧾 Official Receipts (logo + QR + watermark, A4)
- **`GET /api/demands/{id}/file-receipt`** — Demand File Receipt with company
  logo header, sponsor block, recruitment-categories table, "amount in words"
  in Pakistani style (Crore / Lakh / Thousand), a **scannable QR** linking
  back to `/demands/{id}/file-receipt`, an angled CSS watermark
  (`::before` pseudo-element at –32°, 6% opacity), terms + signature row, and
  HussnainTechVertex Pvt Ltd. footer.
- **`GET /api/demands/{id}/payments/{pid}/receipt`** — Payment Receipt with
  PAID/INVOICE stamp variant, same logo + watermark, QR code linking back to
  the receipt URL.
- Renderer: `app/services/receipts_renderer.py` (≈600 lines) — uses
  `qrcode==7.4.2`, base-64 PNG embedding for logo and QR, shared
  `_company_head_html` / `_verify_row_html` / `_page_footer_html` builders.
- **UI**: green **File Receipt** button added next to *Print All (Packet)*
  on the Demand Detail page → opens the new receipt in a new tab with
  auto-print.

### 📊 Barcode polish (E-Barcode sheet)
- Second barcode is now the **Demand File Number** (numeric, e.g. `8176`)
  resolved from the candidate's assignment → job_category → demand chain,
  with leading-zero padding to match the E-Number length so both barcodes
  print at the **same visual width** (`<img class="bc-img">` is forced to
  80mm width via CSS).
- 25 mm vertical separation between blocks (was 14 mm) so they can NEVER
  visually overlap, plus 22 mm tall bars and 1.6 module width for robust
  print scanning.
- HussnainTechVertex Pvt Ltd. footer strip on every E-Barcode sheet.

### 🎨 Brand identity (HussnainTechVertex Pvt Ltd.)
- New `app/static/img/dev_logo.png` (740 × 742) + 64 px + 32 px icon
  variants generated via Pillow.
- Logo + name embedded in **base.html footer**, **login page footer**, and
  every printed receipt / barcode sheet.

### Files added / modified this release
| File | Status | Purpose |
|------|--------|---------|
| `app/services/receipts_renderer.py` | **new** | Official Demand File + Payment Receipt with logo, QR, watermark, amount-in-words |
| `app/services/letterhead_renderer.py` | edit | E-Barcode now uses File No. (numeric) as second barcode, padded for uniform width, with HussnainTechVertex footer |
| `app/services/dtcbot.py` | edit | `handle_uploaded_document()` actively creates / updates candidate from OCR (auto-execute path) |
| `app/services/dtcbot_agent.py` | edit | `_wrap_action()` now returns `verb`, `url`, `navigate: true` for frontend auto-redirect |
| `app/api/endpoints/demands.py` | edit | New `/file-receipt` endpoint + payment receipt rewritten to call `receipts_renderer` |
| `app/api/endpoints/chatbot.py` | edit | `/upload` injects DB session + `auto_create` flag → end-to-end OCR → record creation |
| `app/templates/base.html` | edit | DtcBot widget JS now renders `action` type + auto-navigates; footer carries HussnainTechVertex logo + name |
| `app/templates/login.html` | edit | Brand logo in card footer + page footer |
| `app/templates/demand_detail.html` | edit | "File Receipt" button next to "Print All (Packet)" |
| `app/templates/protector_letter.html` | edit | Emerald → blue accents |
| `app/templates/candidates.html` | edit | Drawer hidden when `flyout+wizard_mode`; instant `openWizard()` init |
| `app/main.py` | edit | `wizard_mode` template var passed when `?wizard=1` or `?edit=N` |
| `app/static/img/dev_logo*.png` | **new** | HussnainTechVertex brand asset (3 sizes) |
| `requirements.txt` | edit | + `qrcode==7.4.2` |

---

## 🗂️ Previous Changes (May 2026 — Centered-modal data entry + view-only drawer, demo-faithful lists & docs)

Reworked the candidate UI to match the **real `demo.oep.com.pk` flow**: data
entry is a **centered modal**, while the right-side panel is a **read-only view**.

- **Candidate data entry = centered modal** (`#candidateModal`, `.modal-box-wizard`,
  max-width 880px). Used for **both Add and Edit**. Step 1 leads with the photo
  upload, fields laid out in a 3-column grid, horizontal 7-step stepper at top,
  Cancel (left) / Prev / Save Draft / Next / Finalize (right). Opened via
  `openWizard(id,'edit')` or `openCandidateModal()`.
- **Right-side drawer = view-only** (`openView(id)`): read-only **Details /
  Documents / Checklist / Status / Activity** tabs with **Print** + **Edit
  Candidate** (the Edit button bridges to the centered modal via
  `editFromDrawer()`). The old in-drawer wizard was removed.
- **Status tab** now has a **workflow-stage dropdown** (populated from the
  canonical `/api/candidates/stages`) that PUTs the new stage to
  `/api/candidates/{id}` (emitting a `stage_change` audit event), plus a
  **Status History timeline** built from the `stage_change` audit entries, and
  the existing processing-details grid + assignments.
- **Demand File → Trades tab**: "Assign" opens the candidate centered modal
  **full-screen** (the flyout iframe expands so the modal is centered on the
  whole viewport, not a narrow side panel). Assigned candidates render as
  **photo cards** (photo, name, S/O father, status pill, Details / Edit /
  Unassign). On finalize the parent flyout closes, shows a toast, and reloads
  trades.
- **Document Print** (Protector Documents / Visa Process groups) is wired
  through the **coordinate-overlay engine** (`/api/documents/templates/{id}/print`)
  on real form-image backgrounds: **OEP Form p1 & p2, Allied Bank Deposit
  Form-7, NBP/HBL slips, Protector Certificate** (letterhead), and embassy-
  filtered **Visa Application (Karachi / Islamabad)**. Verified all render 200
  with field overlays + auto print dialog.
- **Demand list columns**: **File # · Visa # · Receiving Date · Sponsor ·
  Embassy · Status · Trades (assigned/total)**. The list API now returns
  `assigned_count` (single grouped query over the visible page).
- **Client → Demand Files list columns**: **File # · Date · Sponsor · Embassy ·
  Status**; the client-demands API now returns `embassy`.
- **Legacy draft upgrade**: `POST /api/candidates/wizard/from-candidate/{id}`
  now **backfills** any pre-7-step draft (5 slices) up to 7 slices and bumps
  `total_steps`, so Steps 6 & 7 hydrate correctly from the candidate row while
  preserving any in-progress edits.

---

## 🆕 Previous Changes (May 2026 — Candidate Wizard expanded to 7 steps)

The candidate-centered modal wizard now models the **full overseas-employment
lifecycle** in 7 resumable steps (was 5). Two new steps were inserted between
*Next of Kin* and *Charge Summary* to capture the real-world processing stages
that map to the canonical workflow (`medical`, `protector`, `visa_stamping`,
`e_number`, `travel_ready`, `deployed`):

- **Step 5 · Medical & Protector** — GAMCA/slip no., medical center, medical
  date, consignment no., send date, courier, plus Protector of Emigrants no. &
  date (`gamca_number`, `medical_center`, `medical_date`,
  `medical_consignment_no`, `medical_send_date`, `medical_courier_name`,
  `protector_no`, `protector_date`).
- **Step 6 · Visa & Travel** — visa stamp date, E-number, destination, flight
  no., date of departure, ticket no. (`visa_stamp_date`, `e_number`,
  `destination`, `flight_no`, `date_of_departure`, `ticket_no`).
- **Step 7 · Charge Summary** — the former Step 5 (price, allowances, ticket
  included, workflow stage) is now the final step.

All new fields already existed on the `Candidate` model, so **no DB migration
was required**. The canonical step list lives in `app/core/workflow.py`
(`WIZARD_STEPS` / `TOTAL_WIZARD_STEPS = 7`) and is consumed by both the backend
(`/api/candidates/stages`, wizard step/finalize endpoints) and the frontend
stepper/`STEP_FIELDS`. Edit-mode = 6 steps (skips Charge Summary); New/Assign =
7 steps. The Status tab's grid surfaces all the new medical/visa/travel values.

Verified end-to-end: create draft → save steps 1/5/6/7 → finalize correctly
persists every medical, protector, visa and travel field with proper date
coercion and stage transition.

---

## 🆕 Previous Changes (May 2026 — DtcBot Compute Agent + AI Vision OCR + Profile Photo)

This release upgrades **DtcBot from a guide into an offline-capable *compute
agent* that actually performs data entry**, adds an AI-vision-first passport
scanner with graceful offline fallback, and brings the candidate wizard's
**Step 1 profile photo upload** in line with the reference videos.

- **DtcBot Compute Agent (`app/services/dtcbot_agent.py`) — deterministic, offline**
  - Parses natural-language commands **and** pasted "Field: value" data blocks,
    maps human phrasing (e.g. *father*, *passport no*, *cnic*, *dob*) to real
    `Candidate` columns via `FIELD_ALIASES`, coerces types (dates, ints, money,
    gender), then performs **real DB writes** with full audit logging.
  - Supported actions (all execute against the live DB, no LLM key required):
    - **Create** — `create candidate` + a data block, or `... named ALI HASSAN`.
    - **Update / Set** — `set passport no of ALI HASSAN to AB1234567`.
    - **Assign** — `assign ALI to trade #12`.
    - **Bulk data-entry** — paste an OCR/passport block; if it has a `Full Name`
      it creates a candidate, otherwise it updates the matching passport/CNIC.
  - Wired into `dtcbot.answer()` **before** the LLM/rule fallback, so the bot
    *acts* even when the external LLM key is expired (per the user's request:
    *"data entry to dtc bot as trained compute agent, not only guided"*).
- **Direct data-entry API** — `POST /api/chatbot/data-entry`
  - Body: `{ "block": "Full Name: ...\nPassport No: ..." }` **or**
    `{ "fields": {col: value}, "candidate_ref": "<id|name|passport>" }`.
  - Creates (needs `full_name`) or updates (with `candidate_ref` / matched
    passport/CNIC). Returns the saved candidate snapshot.
- **AI Vision passport OCR (`app/services/passport_vision.py`)**
  - New primary OCR path: sends the passport image to an OpenAI-compatible
    vision model and parses strict JSON of `Candidate`-shaped fields.
  - `extract_passport_data()` now runs a **3-tier pipeline**:
    **(1) AI Vision → (2) Tesseract MRZ (ICAO 9303 TD3) → (3) text regex**,
    returning `method = "vision" | "mrz" | "text"`.
  - When neither a valid vision key nor Tesseract is present, it returns a
    clear **"type the details into the form"** message instead of failing —
    the wizard fields stay ready for manual entry.
- **Candidate Wizard · Step 1 — Profile Photo upload**
  - New photo uploader with live preview at the top of *Personal Information*.
  - Uploads via existing `POST /api/candidates/upload-photo`
    (stored at `/static/uploads/photos/<file>`); the filename is saved into the
    candidate's `photo` field and re-rendered when re-opening the wizard.
- **Dependencies** — `openai==2.38.0` and `PyYAML==6.0.3` added to
  `requirements.txt` (LLM brain + vision OCR + config loading).

> **Note on the LLM key:** the conversational LLM brain and AI-vision OCR both
> require a valid OpenAI-compatible key (env `OPENAI_API_KEY` or the local key file). If the key is
> expired, **all of the above still work**: the compute agent and rule engine
> run fully offline, and OCR degrades gracefully to manual entry.

---

## 🆕 Previous Changes (May 2026 — Client detail parity + print stylesheet)

This release fully aligns the **Client Detail** page with the reference
mobile SaaS screenshots (Profile / Contacts / **Demand Files** / Statement),
adds proper print support across the portal, and re-shapes the
`/api/clients/{id}/demands` payload for richer per-row context.

- **Client Detail · Demand Files tab — schema + UI parity with reference**
  - API `/api/clients/{id}/demands` now returns the envelope `{items, total}`
    with the columns shown in the reference screenshot:
    `file_number, receiving_date (Date), sponsor_name, visa_number, visas (count), country, permission_no, status`.
  - The `visas` column is the **aggregated trade quantity** (sum of all
    `JobCategory.quantity` for that demand) — done in one batched
    `GROUP BY` query (no N+1).
  - The HTML table now shows: **File No · Date · Sponsor · Visa No · Visas · Status**
    exactly matching the reference. Sponsor names are intelligently truncated
    with full text on hover.
  - Added inline **search box** (file / sponsor / visa) and `· N files` counter
    in the card header for fast scanning of large client portfolios.
  - Status pills are color-coded (active → green, processing → amber,
    cancelled/expired → rose) to mirror the screenshot's green "active" badge.
- **Client Detail header — Print + Delete buttons + status pill**
  - Restored the **Print** / **Edit** (now blue, primary) / **Delete** action
    cluster shown in the reference screenshot.
  - Top-right status pill mirrors the live client status.
  - Header subtitle reformatted to `Client profile · <City>, <Country>`.
  - **Delete** confirmation modal warns about cascade effects on linked
    demand files and statements.
- **Portal-wide `@media print` stylesheet** (`app/static/css/app.css`)
  - Hides sidebar, top bar, action buttons, and last-column row actions.
  - Keeps tables, stat tiles, profile cards readable on A4 paper.
  - Triggered by the **Print** button on the Client Detail page; document
    PDFs still flow through the overlay engine as before.
- **Maintenance / hardening**
  - `.gitignore` now excludes `core`, `core.*`, `*.coredump`, `server.out.log`,
    `server.err.log` (caught a 285 MB stray Playwright chromium core file
    that would otherwise have bloated the production zip).
  - All 30+ endpoints + 12 HTML pages pass the bundled smoke test
    (`api/auth/login → /api/clients/871/demands` etc.) — see `tests/`.

### Earlier (May 2026 — UI polish + Document customize fix)
This release brings the candidate list visuals fully in line with the
reference SaaS theme screenshots, and fixes a regression where the
Document Customize page failed to load background images.

- **Candidates List — visual match with reference**
  - **`+ New Candidate`** blue button restored (top-right) — matches reference exactly.
  - Removed the `Client / Demand File No. / Embassy` columns from the *list view*
    (that data is still loaded into the candidate-detail drawer header — no data loss).
  - Removed the long blue info-banner; the workflow constraint is preserved via a
    clean modal instead.
- **`+ New Candidate` modal** (workflow + UX preserved)
  - Step 1: pick Demand File (auto-loaded list).
  - Step 2: pick Trade (auto-loaded from selected demand, shows assigned/quantity).
  - On Continue → redirects to `/demands/{id}?trade_id=...&action=assign_candidate`
    where the original 5-step wizard launches with full context (client/demand/embassy
    badges shown in drawer header).
  - The backend still rejects any wizard create without `job_category_id`, so
    direct candidate creation is impossible even via API — data integrity guaranteed.
- **Document Customize page — background image fix**
  - The DB stores the background path as `/static/pdf_backgrounds/allied_bank_form7.jpg` (full),
    but the JS was double-prefixing it → `Failed to load background image`.
  - Fixed `setBackground()` to normalize: if the string already starts with `/` or
    `http`, use it as-is; otherwise prepend `/static/pdf_backgrounds/`.

### Earlier (May 2026)
- **Demand Detail** → `[Print Documents]` (was "Generate"), Embassy badge in header.
  - **Documents tab** = PERMISSION DOCUMENTS group with per-row `[Print]` button hitting the **real-image data-overlay engine** (Pillow + ReportLab, NOT generation). A `⚙` gear opens the coordinate-customise page.
  - **Payments tab** = stat tiles (Total Invoiced / Received / Outstanding) + table of payments + receipt-print on demand. Payments are stored as `ClientStatement` rows tied to a Demand File so they also appear on the client statement. Auto receipt-numbering `RCP-YYYY-NNNN`. Receipt is rendered as a printable HTML with `@media print` CSS, PAID stamp, amount-in-words (lakh/crore), signature lines.
  - **Edit Trade Modal** — supports `custom_fields` (JSON) merged into PDF templates by the overlay engine.
- **Candidate Detail (drawer)** → Embassy / Client / Demand File No. badges in the header.
  - **Documents tab** = PROTECTOR DOCUMENTS + VISA PROCESS groups (data-overlay print). Visa Application Form is **auto-filtered by embassy** (Karachi vs Islamabad).
  - **Wizard step count is conditional** — Edit mode = 4 steps (skips Charge Summary), New/Assign = 5 steps.
- **Candidate List** → Photo column removed (per user request). New columns: `Client`, `Demand File No.`, `Embassy`. Skeleton loader + 60s client-side cache + invalidation on save.
- **OCR** — Tesseract MRZ TD3 pipeline confirmed end-to-end at `POST /api/ocr/passport`. Reachable from the green **Scan Passport** button in candidate wizard Step 2 and demand-detail.
- **AI Chatbot (DtcBot)** — Upgraded from rule-based to **real LLM** (GPT-5-mini via an OpenAI-compatible endpoint) with **tool/function calling** that queries the live SQLite DB (candidates, demands, clients, payments, templates). Multi-turn memory, Markdown rendering, graceful fallback to rule-based bot when the LLM is unreachable. Source: `app/services/dtcbot_llm.py`.
- **Performance** — Full portal speed pass:
  - SQLite WAL journal + 64MB cache + mmap_io + memory temp store
  - Single-query JOIN batch-loader (eliminated N+1 in candidate-context derivation)
  - GZip middleware (~90% payload reduction on list endpoints)
  - 24h `Cache-Control: immutable` for `/static/`, 30s private cache for read-only API
  - Frontend skeleton loaders + in-memory list cache (60s TTL with invalidation on mutation)
  - Benchmark: `/api/candidates/?limit=50` went from ~100ms/88KB → **~50ms/9KB**

---

## ✨ Features

### Core Modules
- **Dashboard** — Real-time statistics, charts, recent activity
- **Clients / Companies** — Client (Foreign Sponsor) management with full profiles
- **Demand Files** — Job demand letters with job categories and quantities
- **Candidates** — Full candidate lifecycle (personal info, passport, CNIC, trade)
- **Agents** — Sub-agent / travel agent management (migrated from old data)
- **Agent Cash Book** — Debit / Credit ledger per agent (migrated from old data)
- **Visa Tracking** — Status workflow (Pending → Processing → Issued → Expired)
- **Documents & PDF Overlay Engine** — Coordinate-based document builder
- **Reports** — Financial, recruitment, visa reports
- **Users & Roles** — Multi-user with admin/staff permissions

### PDF Overlay Engine (★ Core Feature)
This is the heart of the portal — replicating the engine seen in the reference video:

1. **Upload background** — Upload any official form (visa, bank slip, government form) as JPG/PNG/PDF
2. **Place fields visually** — Drag-and-drop database fields onto the form
3. **Set coordinates** — Precise X, Y positioning with font size, color, bold/italic
4. **Save template** — Reusable document template tied to data source
5. **Generate PDF** — One-click filled PDF for any candidate / record

Supported document templates (pre-built):
- Allied Bank Deposit Slip
- NBP / NSEP Deposit Slip
- OEP Government Form (multi-page)
- Saudi / GCC Visa Application
- Demand Letter
- Permission Undertaking
- Custom (build your own!)

### Existing Data Migrated
The legacy MySQL database (`backup-18-05-2026.sql`) has been migrated:
- ✅ `agents` table → 1 agent (NEW CHAUDHARY TRAVELS) + extensible
- ✅ `agents_cash` table → 70+ transaction ledger entries
- ✅ Default office: **Dogar Trading Corporation**
- ✅ Logo embedded throughout

---

## 🚀 Quick Start

### 1. Setup environment
```bash
cd dogar_trading_portal
python -m venv venv
source venv/bin/activate          # Linux / macOS
# venv\Scripts\activate           # Windows
pip install -r requirements.txt
cp .env.example .env
```

### 2. Initialize database & seed legacy data
```bash
python -m app.db.init_db
```

### 3. Run the server
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. Login
Open **http://localhost:8000** and login:
- **Email:** the value of `DEFAULT_ADMIN_EMAIL` from your `.env` file
- **Password:** the value of `DEFAULT_ADMIN_PASSWORD` from your `.env` file

> ⚠️ **Security**: The bootstrap admin is created with `must_change_password=true`
> — on first login the UI redirects you to the password-change screen and the
> initial password cannot be re-used. In `ENV=production` mode the app refuses
> to start if the well-known weak passwords (`admin123`, `password`, …) are
> still in `.env` — see [`.env.example`](./.env.example) for guidance.

### 5. Run the tests

Test dependencies live in a **separate** `requirements-dev.txt` (pytest, httpx,
openpyxl, ruff, playwright). Installing only `requirements.txt` is **not enough**
to run the suite — you must install both:

```bash
pip install -r requirements.txt -r requirements-dev.txt
TESTING=true pytest -q          # full backend suite (SQLite, in-memory)
```

This is exactly what CI runs (`.github/workflows/ci.yml` → `python-tests`). If
`pytest` reports `ModuleNotFoundError` / missing plugin, the dev requirements
were not installed — run the `pip install` line above first. (The heavy
`playwright` E2E deps are only needed for the browser E2E suite; plain
`pytest -q` runs without them.)

---

## 🏗️ Tech Stack

| Layer        | Technology                                  |
|--------------|---------------------------------------------|
| Backend      | Python 3.10+, FastAPI 0.115                 |
| ORM          | SQLAlchemy 2.0                              |
| Database     | SQLite (default, fully supported) · PostgreSQL (partial/experimental) |
| Auth         | JWT (python-jose) + bcrypt                  |
| Templates    | Jinja2                                      |
| Frontend     | TailwindCSS (CDN), Alpine.js, Chart.js      |
| PDF Engine   | ReportLab + Pillow + pypdf                  |
| Icons        | FontAwesome 6                               |

---

## 📁 Project Structure
```
dogar_trading_portal/
├── app/
│   ├── main.py                  # FastAPI entry point
│   ├── core/                    # Config, security, deps
│   ├── db/                      # Database session, init/seed
│   ├── models/                  # SQLAlchemy ORM models
│   ├── schemas/                 # Pydantic schemas
│   ├── services/                # Business logic (PDF engine, etc.)
│   ├── api/endpoints/           # REST API routes
│   ├── templates/               # Jinja2 HTML pages
│   └── static/                  # CSS, JS, images, uploads
├── migrations/                  # Original SQL backup + migration script
├── data/                        # SQLite database (gitignored)
├── requirements.txt
├── .env.example
└── README.md
```

---

## 🌐 Main URLs

| Path                            | Purpose                              |
|---------------------------------|--------------------------------------|
| `/`                             | Login page                           |
| `/dashboard`                    | Main dashboard with stats            |
| `/clients`                      | Client / Company management          |
| `/demands`                      | Demand files list                    |
| `/candidates`                   | Candidates list                      |
| `/agents`                       | Sub-agents list                      |
| `/agents/{id}/cashbook`         | Agent ledger (debit/credit)          |
| `/documents`                    | Document templates list              |
| `/documents/builder/{id}`       | PDF coordinate builder (★)           |
| `/documents/generate/{id}`      | Generate filled PDF                  |
| `/reports`                      | Reports module                       |
| `/users`                        | User management (admin only)         |
| `/api/docs`                     | Swagger API documentation            |
| `/api/redoc`                    | ReDoc API documentation              |

---

## 🔐 Bootstrap Admin

The bootstrap admin user is created **from the values you set in `.env`**:

```bash
DEFAULT_ADMIN_EMAIL=...      # required
DEFAULT_ADMIN_PASSWORD=...   # required, ≥12 chars, not in weak list
```

On first login the user is forced to change their password (`must_change_password=true`),
so even if a weak placeholder slips through development, it cannot survive past the
first sign-in. In `ENV=production` mode the application **refuses to boot** with any
of the documented weak passwords — see `app/core/config.py::_FORBIDDEN_ADMIN_PASSWORDS`.

---

## 📦 Data Architecture

### Bundled Production Dataset

The zip ships with a **complete real-data SQL dump** at
`migrations/dogar_full_backup.sql.gz` (~650 KB compressed, ~3.8 MB
uncompressed). On first startup, if the SQLite database is empty,
`app/main.py::_apply_bundled_sql_dump()` automatically restores:

| Table                  | Rows  |
|------------------------|-------|
| `candidates`           | 2,674 |
| `demands`              | 2,260 |
| `clients`              | 1,243 |
| `job_categories`       | 2,374 |
| `candidate_assignments`| 2,671 |
| `document_templates`   | 18    |
| `document_fields`      | 202   |

No operator intervention required — a fresh server volume produces a
fully-populated portal on first boot.

### Core Entities
- **User** — Portal users with roles (admin / staff)
- **Client** — Foreign sponsor companies
- **Demand** — Job demand letters (1 client → many demands)
- **JobCategory** — Trade / profession entries inside a demand
- **Candidate** — Workers being recruited (full bio data)
- **CandidateAssignment** — Candidate ↔ JobCategory link with status
- **Agent** — Sub-agents / travel agencies
- **AgentCash** — Agent ledger transactions (debit/credit)
- **DocumentTemplate** — PDF template definition with background
- **DocumentField** — Coordinate-mapped field on a template
- **GeneratedDocument** — Log of every generated PDF

### Storage
- **SQLite** (default) — Zero-config, file-based. This is the **supported** backend
  and what the app is validated against.
- **PostgreSQL** — **Partial / experimental.** Tenant provisioning
  (`TENANT_DB_URL_TEMPLATE`) and a migration script (`scripts/migrate_to_postgres.py`)
  exist, but full Postgres support (asyncpg/psycopg2 across all code paths) is a
  roadmap item and is not yet fully validated. Do **not** point a production
  `DATABASE_URL` at Postgres until that work lands — keep the SQLite default.
- **MySQL** — **Not supported.**

---

## 🎨 Theme
Corporate **light SaaS theme** matching the reference video:
- Primary: Forest Green `#2E7D32` / Brand Blue `#1565C0`
- Sidebar: White, collapsible, icon-based navigation
- Status badges color-coded (Pending=Gray, Processing=Blue, Filled=Green, Expired=Red)
- Logo: **Dogar Trading Corporation** branding throughout

---

## 🛣️ Roadmap (Optional Enhancements)
- [ ] Multi-language (English / Urdu / Arabic)
- [ ] Email notifications (visa status changes)
- [ ] Two-Factor Authentication
- [x] **Audit log (who changed what)** — implemented (see below)
- [ ] Mobile-responsive PDA scanner integration
- [ ] Bulk import from Excel

---

## 🧭 Canonical Workflow Model

All candidate / demand / assignment status writes flow through a single source of
truth: **`app/core/workflow.py`**.

### Candidate Stages (10 canonical values)

| Value             | Display Label        | Pill Color | Meaning                                       |
| ----------------- | -------------------- | ---------- | --------------------------------------------- |
| `new`             | New                  | slate      | Candidate created, not yet assigned           |
| `docs_pending`    | Documents Pending    | amber      | Assigned to a trade, collecting docs          |
| `docs_complete`   | Documents Complete   | sky        | All passport/CNIC/photo etc. on file          |
| `protector`       | Protector            | indigo     | At Protector of Emigrants (Pakistani govt)    |
| `medical`         | Medical              | purple     | Medical examination phase                     |
| `visa_stamping`   | Visa Stamping        | blue       | Embassy / consulate visa stamping             |
| `e_number`        | E-Number             | violet     | Emigration number issued                      |
| `travel_ready`    | Travel Ready         | teal       | Ticket booked, ready to fly                   |
| `deployed`        | Deployed             | emerald    | Travelled, working overseas                   |
| `cancelled`       | Cancelled            | rose       | Terminal — withdrawn or rejected              |

The `protector` stage is intentionally retained — it represents the **Pakistani
Bureau of Emigration's Protector of Emigrants office**, a mandatory real-world
stage in the overseas-employment workflow.

State transitions are validated via `STAGE_TRANSITIONS` in `workflow.py`.
Legacy / variant status values from the migrated MySQL backup are auto-normalised
through `_LEGACY_STAGE_ALIASES` so old records keep working.

API: **`GET /api/candidates/stages`** returns the full canonical list + the
5-step wizard layout for any front-end to consume.

---

## 📝 Candidate Wizard — Resumable Multi-Step Intake

Candidate intake is a **7-step right-side drawer wizard** with **per-step
persistence**, so partially filled candidates can be resumed later from a
"Drafts" shelf at the top of `/candidates`.

| Step | Key                 | Captures                                              |
| ---- | ------------------- | ----------------------------------------------------- |
| 1    | `personal_info`     | Name (EN/Arabic), father, DOB, gender, photo, etc.    |
| 2    | `identification`    | Passport, CNIC, NADRA token, issuing authority        |
| 3    | `employment`        | Profession, permission no./date, qualification, salary|
| 4    | `next_of_kin`       | Emergency contact, relation, notes                    |
| 5    | `medical_protector` | GAMCA/medical center & dates, Protector no./date      |
| 6    | `visa_travel`       | Visa stamp date, E-number, destination, flight, ticket|
| 7    | `charge_summary`    | Charge breakdown, allowances, workflow stage          |

Each step is saved to `candidate_wizard_states.step_data` (JSON column keyed
`"1".."7"`) the moment the user clicks **Save & Next** or **Save Draft**.

### Wizard API

```
POST   /api/candidates/wizard                       Create a fresh draft
GET    /api/candidates/wizard/active                List unfinished drafts
GET    /api/candidates/wizard/{id}                  Load one draft
PATCH  /api/candidates/wizard/{id}/step/{n}         Save step n
POST   /api/candidates/wizard/{id}/finalize         Materialise into Candidate row
POST   /api/candidates/wizard/{id}/reopen           Re-open a finalised candidate
POST   /api/candidates/wizard/from-candidate/{cid}  Edit an existing candidate
DELETE /api/candidates/wizard/{id}                  Discard a draft
```

Every wizard action emits an audit event (see below).

---

## 🔁 Assign / Unassign — Cleanup + Audit

Workflow linkage between **Candidates ↔ Demand Trades** is fully transactional:

### `POST /api/demands/trades/{trade_id}/assign`
1. Inserts a `candidate_assignment` row.
2. If candidate was `new`, auto-transitions to `docs_pending`.
3. Emits `audit_log` entries: `assignment.assign` + `candidate.stage_change`.

### `DELETE /api/demands/assignments/{assignment_id}` (full cleanup)
1. Captures snapshot of the assignment.
2. Deletes the `candidate_assignment` row.
3. Clears any `GeneratedDocument.demand_id` / `.job_category_id` that referenced
   this trade for this candidate.
4. If the candidate has no remaining assignments **and** is not in a terminal
   stage (`deployed` / `cancelled`), resets `candidate.status` back to `new`.
5. Returns `{ "cleared_fields": [...] }` so the UI can show what changed.
6. Emits `audit_log` entries: `assignment.unassign` + `candidate.stage_change`
   (when status was reset).

The **Demand-file → Trades** tab now renders rich candidate cards (photo,
name + Arabic name, S/O father, status pill, passport, phone) instead of
empty placeholders. Unassign confirms with a dialog that explains the
cleanup.

---

## 📋 Audit Log

Every workflow-impacting action writes a row to `audit_logs` with:

| Field        | Purpose                                            |
| ------------ | -------------------------------------------------- |
| `entity_type`| `candidate` / `demand` / `assignment` / `wizard`   |
| `entity_id`  | PK of the affected row                             |
| `action`     | `create` / `update` / `assign` / `unassign` / `stage_change` / `wizard_step` / `wizard_finalize` / `wizard_reopen` |
| `actor_id` / `actor_email` | Who did it                           |
| `before_json` / `after_json` | Full snapshots for diffing         |
| `ip` / `ua`  | Request metadata                                   |
| `occurred_at`| UTC timestamp                                      |

API: **`GET /api/candidates/{candidate_id}/audit`** returns the full audit
trail for a candidate, surfaced in the Candidate drawer's **Activity** tab.

---

## 📜 License
Proprietary — Dogar Trading Corporation © 2026

---

**Deployment Status:** ✅ Production-ready • Last updated: 2026-05-29
**Data Status:** 2,662 candidates · 2,249 demands · 1,243 clients · 2,527 assignments (all migrated from legacy MySQL backup, status values normalised to canonical workflow)
**Tech Stack:** FastAPI · SQLAlchemy 2.0 · Jinja2 · TailwindCSS · Alpine.js · ReportLab

---

## React Frontend Migration — Status (updated)

The Jinja UI is being ported to a React SPA (Vite + React 18 + TS strict + Tailwind),
served single-origin under `/static/spa/`. The Python/FastAPI backend remains the
source of truth for all data and document rendering.

### Completed (v2-parity, QA-verified)
- **Candidates page** — list + 5-tab drawer (profile/documents/checklist/status/activity),
  ISB/KHI embassy-aware Visa document filter, E-Barcode synthetic row, print engine.
- **5-step Assign Wizard** — Personal Info → Identification (real passport OCR auto-fill
  via `/api/ocr/passport`) → Employment → Next of Kin → Charge Summary. One Assign button
  per available trade slot. Create + edit modes. Save flow verified end-to-end.
- **DtcBot assistant (production)** — global floating widget on all authenticated pages.
  Faithful port of the v2 widget: rich cards, chips, options, links, quick-replies, OCR
  passport image upload (auto-create candidate), `flow_state` round-tripping, in-app
  navigation and document printing. Wired to `/api/chatbot/welcome`, `/message`, `/upload`.

### Critical fix
- **CSRF double-submit header** — `lib/api.ts` now replays the non-HttpOnly `csrf_token`
  cookie as the `X-CSRF-Token` header on every POST/PUT/DELETE/PATCH. This unblocked ALL
  React write operations (candidate create/assign, chatbot, etc.), which previously 403'd
  from the browser.

- **Client Detail** — full v2 parity: hero + 4 stat tiles (Transactions/Debit/Credit/Balance)
  + 4 tabs (Profile / Contacts / Demand Files / Statement) + edit, add-contact and
  add-statement modals + print statement.
- **Documents page** — pinned System Templates grid + filterable templates table
  (category filter + search) with Configure-positions / Print-preview / Delete actions.
- **Template Designer** — React coordinate engine: renders the real PDF background image
  at true page dimensions (595×842pt) with each field as a draggable marker positioned at
  its stored PDF coordinates (origin bottom-left). Palette + property inspector
  (label/key/type/x/y/width/height/font/align/color) + bulk save to `/fields/bulk`.

### Login
- Log in with the **admin email** you configured in `.env` (`DEFAULT_ADMIN_EMAIL`)
  and its password (`DEFAULT_ADMIN_PASSWORD`). Log in with the **email**, not a
  plain username. Do not use weak sample passwords such as `admin123` — the app
  refuses to start in production with a known-weak `DEFAULT_ADMIN_PASSWORD`.

### Remaining
- Minor polish on legacy list pages; everything explicitly requested is complete.

### Security & maintenance audit (latest)
- **NEW-2** — `audit.py` export endpoint uses Pydantic v2 `pattern=` (removes the `regex=` deprecation warning).
- **NEW-3** — All protector-letter lookup / e-barcode / NOC-verification GETs and the bulk-lookup POST now require the `protector:view` permission (previously readable by any authenticated user).
- **NEW-4** — `GET /api/settings/general` now requires the `settings:view` permission.
- **NEW-5** — Demand status-lifecycle guard (`app/services/validation.py`) wired into `PUT /api/demands/{id}`; illegal transitions (e.g. `cancelled → filled`) return HTTP 400.
- **NEW-6** — Legacy migration script moved to `scripts/migrate_real_data.py` (path/docstring corrected).
- **NEW-7** — Tenant logo-upload docstring corrected to PNG/JPG only.
- **NEW-8 / #6** — Removed the committed Tailwind Play-CDN binary and the runtime CDN `<script>`. Tailwind is **compiled to static CSS** rather than pulled from a CDN. (At the time this entry was written that produced two outputs, one of them for the Jinja pages; after the Jinja2 eradication pass the **React SPA bundle `assets/index-*.css` is the only Tailwind output that ships**.) The blue⇄forest-green theme swap is preserved via `--blue-*` CSS variables toggled by `.theme-forest-green`. No more "cdn.tailwindcss.com should not be used in production" console warning.
- **NEW-9** — `DEPLOY.md § 10` documents applying schema changes to per-tenant SQLite DBs.
- **Tenant migrations on deploy** — `scripts/apply_tenant_migrations.py` idempotently rolls new tables/columns into every `data/tenants/tenant_*.db`; `docker-entrypoint.sh` runs it automatically (after `alembic upgrade head`) before uvicorn starts.

**Rebuilding Tailwind after SPA class changes:**
- React SPA: `cd app/frontend && npm run build`
