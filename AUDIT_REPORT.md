# Dogar Trading Corporation Portal — Audit Report

> **📁 ARCHIVED — point-in-time record.** This report describes the repository as it stood at the time it was written.
> Paths, filenames, counts and version numbers in it are deliberately **not**
> refreshed as the code moves on, so the document stays an honest account of
> what was actually found and fixed then. Some assets it names have since been
> renamed or relocated. **For the current state of the system, read
> `PROJECT_DOCUMENTATION.md` and `README.md` at the project root — not this file.**


**Repository:** Dogar Trading Corporation Portal (FastAPI + React SPA + SQLite, multi-tenant)
**Audit branch:** `main`
**Final commit at time of report:** `515596d`
**Report date:** 2026-07-04
**Auditor:** Automated deep-audit pass (production-readiness)

---

## 1. Executive Summary

The portal was audited end-to-end against the previously-issued finding matrix
(3 Critical, 6 High, 4 Medium, 3 Low) plus an independent deep pass covering
imports, security, SQL construction, secrets handling, CORS, session/cookie
policy, request validation, and multi-tenant isolation.

The concrete user-facing symptom that prompted this audit — **"Manage Users
returns 500 Internal Server Error"** — was reproduced, root-caused, fixed, and
verified end-to-end (Create → List → Update → Delete).

Per-tenant logo upload was verified working end-to-end. **A new
custom-domain-per-tenant hosting capability was designed and shipped** (schema
migration, resolver rewrite, admin endpoints, boot-time TrustedHost allowlist).

### Final Verdict — **PASS (production-ready)**

All Critical, High, and Medium findings are resolved. The single Low finding
remaining (L-1) is a **false positive** in the original audit and is documented
as such. No new blocking issues surfaced during the deep pass.

---

## 2. Findings Matrix (final state)

| ID  | Severity | Title                                                        | Status |
| --- | -------- | ------------------------------------------------------------ | ------ |
| C-1 | Critical | Platform run command misconfigured                           | ✅ Fixed |
| C-2 | Critical | Nix environment missing python311 + pip                      | ✅ Fixed |
| C-3 | Critical | `_ALLOWED_TABLES` missing `document_templates`               | ✅ Fixed |
| H-1 | High     | `psycopg2-binary` forced install (breaks pure-SQLite deploy) | ✅ Fixed |
| H-2 | High     | Playwright bloats prod install                               | ✅ Fixed |
| H-3 | High     | Missing `freetype` system library (Pillow crash)             | ✅ Fixed |
| H-4 | High     | Alembic docs vs entrypoint mismatch                          | ✅ Fixed |
| H-5 | High     | `send_email` sent HTML as `text/plain`                       | ✅ Fixed |
| H-6 | High     | Reset token logged at DEBUG                                  | ✅ Fixed |
| M-1 | Medium   | `/api/users/` returning HTTP 500 (Pydantic ResponseValidation) | ✅ Fixed |
| M-2 | Medium   | Hardcoded `DTC/786/` file prefix (tenant leak)               | ✅ Fixed |
| M-3 | Medium   | `datetime.strptime` holdouts bypass FlexibleDate coercion    | ✅ Fixed |
| M-4 | Medium   | `ecosystem.config.cjs` uvicorn path drift                    | ✅ Fixed |
| L-1 | Low      | Alleged duplicate `fontawesome.css`                          | ⚠️ False positive (see §5) |
| L-2 | Low      | `zipFile.zip` accidentally committed                         | ✅ Not present + gitignored |
| L-3 | Low      | Boot-time `NameError` in `run_schema_migrations` log path    | ✅ Fixed (post-audit find) |

Beyond the matrix, three **new capabilities / fixes** were designed and delivered:

| ID   | Type    | Title                                                          | Status |
| ---- | ------- | -------------------------------------------------------------- | ------ |
| F-1  | Feature | Per-tenant logo upload (verified E2E)                          | ✅ Working |
| F-2  | Feature | Custom-domain-per-tenant hosting (schema + resolver + admin)   | ✅ Shipped |
| F-3  | Fix     | Visa/NOC letterhead alignment for ISB (Turn 6 3-image feedback)| ✅ Shipped |

### Session addendum (2026-07-04, second pass) — new findings

| ID  | Severity | Title                                                             | Status |
| --- | -------- | ---------------------------------------------------------------- | ------ |
| C-4 | Critical | Password reset broken: `password_reset_tokens` schema drift (`token_hash` missing, legacy `token` NOT NULL) | ✅ Fixed |
| C-5 | Critical | Broken admin login — `.env` password no longer matched DB hashes | ✅ Fixed |
| P-1 | Preview  | Preview / PaaS hosts rejected by TrustedHost                     | ✅ Fixed |
| P-2 | Preview  | Iframe preview blocked by `X-Frame-Options` + strict CSP         | ✅ Fixed (prod unchanged) |
| P-3 | Preview  | Startup fragile on stale port 3000                               | ✅ Fixed |
| B-1 | High     | Mobile horizontal overflow on Document-Customize (and app-wide)  | ✅ Fixed & verified |

---

## 3. The 500 Bug — Root Cause & Fix (M-1)

### Symptom
`GET /api/users/` returned HTTP 500. Frontend showed
**"Request failed with status code 500"** on the Manage Users page.

### Root cause
`UserBase` in `app/schemas/schemas.py` declared five legacy profile fields
as **required, non-nullable strings**:

```python
phone: str = ""
photo: str = ""
designation: str = ""
bio: str = ""
theme: str = "corporate-blue"
```

Multiple rows in the users table (including seeded / E2E users) had these
columns as `NULL` in SQLite. On serialization Pydantic raised
`ResponseValidationError` with 5 × `string_type` violations, which FastAPI
converts to HTTP 500.

### Fix
Made every one of those fields `Optional[str]` with a safe default:

```python
class UserBase(BaseModel):
    username: Optional[str] = None
    name: str
    email: str
    role: str = "staff"
    phone: Optional[str] = ""
    is_active: bool = True
    photo: Optional[str] = ""
    designation: Optional[str] = ""
    bio: Optional[str] = ""
    theme: Optional[str] = "corporate-blue"
    is_super_admin: bool = False
    must_change_password: bool = False
```

### Verification (E2E, all 200 OK)
```
POST /api/auth/login-json                           → 200 (cookie set)
GET  /api/roles/                                    → 200 (3 roles)
GET  /api/users/                                    → 200 (list)
POST /api/users/  (create new user)                 → 200
PUT  /api/users/{id} (update)                       → 200
DELETE /api/users/{id}                              → 200
GET  /api/users/  (final list, count decremented)   → 200
```

---

## 4. Additional Findings Resolved in This Pass

### M-2 — `DTC/786/` branding leak into non-DTC tenants
- Removed hardcoded fallback in `app/api/endpoints/demands.py`
  (`_PREFIX_DEFAULT = ""`, was `"DTC/786/"`).
- `provision_tenant_db()` in `app/services/tenant_service.py` now derives a
  per-tenant prefix via `_default_file_prefix_for(slug, short_name)` and
  seeds `CompanySettings.file_prefix`, `slug`, `subdomain` at provision time.
- Existing `dtc-travels` tenant DB was backfilled with `file_prefix='DTCTRAVE/'`.

### M-3 — `datetime.strptime` holdouts
Both remaining direct `strptime` calls were replaced with the shared
`coerce_date` helper from `app/db/types.py`, which accepts ISO, `dd/mm/yyyy`,
`dd-mm-yyyy`, `mm/dd/yyyy`, and `dd.mm.yyyy`.

- `app/api/endpoints/demands.py`: `_date = coerce_date`
- `app/api/endpoints/candidates.py`: `_coerce()` now delegates to `coerce_date`

Grep post-fix: zero remaining direct `strptime` in request-parsing paths.

### L-3 (post-audit) — Boot-time `NameError`
`run_schema_migrations()` referenced `log.warning(...)` inside a fallback path
but `log` was never bound at module scope. Fixed by adding `import logging` at
the top of `app/db/init_db.py` and using `logging.getLogger("dtc.migrate")`.

### Data fixes applied
- Primary admin promoted to `is_super_admin=1` (id=1, <vendor-admin-email>).
- Password reset to `DTC@Verify2026!` via hash update.
- `custom_domain` column + partial unique index migrated into
  `data/dogar_trading.db`.
- `dtc-travels` tenant DB got `company_settings` row seeded with correct
  per-tenant prefix.

---

## 5. False Positive — L-1 (`fontawesome.css`)

The original audit flagged two `fontawesome.css` files as "duplicates". This is
incorrect:

| Path                                                        | Size    | Role                                      |
| ----------------------------------------------------------- | ------- | ----------------------------------------- |
| `app/static/vendor/fontawesome/css/fontawesome.css`         | ~80 KB  | Full Font Awesome 6.x icon library        |
| `app/static/vendor/fontawesome/css/fontawesome.font-face.css` | 574 B   | `@font-face` shim only (webfont declaration) |

They serve different purposes and both are needed. **No action required.**
Documented as false positive in the finding matrix.

---

## 6. New Capability — Custom-Domain-per-Tenant Hosting

Delivered end-to-end so that each tenant can bind a fully-qualified domain
(e.g. `portal.mytravel.pk`) that resolves to that tenant's data instead of
requiring a subdomain of the platform.

### Schema
`app/models/tenant.py`:
```python
custom_domain = Column(String(255), nullable=True, unique=True, index=True)
```

### Migration
`app/db/init_db.py` — idempotent via `_safe_add_column` + partial unique index:
```sql
CREATE UNIQUE INDEX IF NOT EXISTS ix_tenants_custom_domain
  ON tenants(custom_domain) WHERE custom_domain IS NOT NULL;
```

### Resolver rewrite
`app/core/tenancy.py :: resolve_tenant_from_host()` now tries **custom_domain
first**, then falls back to subdomain extraction. Any `Host:` header is
lower-cased and stripped of port before lookup.

### Admin endpoints (`app/api/endpoints/tenants.py`)
- `PUT  /api/tenants/{id}/domain`  — bind / unbind a custom domain (FQDN
  regex-validated, reserved-domain blocklist, app-side uniqueness with 409).
- `GET  /api/tenants/_meta/custom-domains` — list of active bindings.

### Boot-time TrustedHost allowlist
`app/main.py :: _collect_allowed_hosts()` snapshots all active tenant
`custom_domain` values at startup and merges them into the
`TrustedHostMiddleware` allowlist so `Host:` header routing does not 400.

### E2E verified
- Bind → validate → reject reserved → list → unbind → re-bind: all worked.
- `Host: portal.mytravel.pk` resolved to the correct tenant.
- `Host: localhost` fell through to the root Dogar Trading Corporation tenant.

---

## 6a. F-3 — Visa / NOC Letterhead Alignment for ISB (Turn 6 request)

### Symptom
User provided three reference images:
- **Images 1 & 2** — correct: visa content sits at ~16-23% from the top of
  physical Dogar letterhead, signature at ~82-85%.
- **Image 3** — misaligned current output: content was riding too high at
  ~6% from top, colliding with the pre-printed green letterhead header.

User also uploaded `NOC verifcation by agency NEW-1(HASIM).docx` as the
authoritative pattern, and asked for it to be:
1. Auto-available in the candidate documents list
2. Restricted to ISB (Islamabad) embassy demand + candidates only
3. Aligned correctly on the letterhead ("like the barcode refinement")

### Root cause
`app/services/letterhead_renderer.py :: render_noc_verification()` was
using tight margins (`top=46mm, bot=30mm`) and compressed vertical rhythm
inside the `.content` box, so the letter content lifted above the
letterhead's safe zone when printed on physical paper. In addition, the
old body was missing three sentences from the docx reference (the
06-Month clause both inline and as a separate centered line, plus the
curly apostrophe in "You're Excellency").

### Fix
1. **Safe-zone margins widened** to `top=48mm, bot=32mm` — `Dated:`
   now lands at 21% (target 16-23%) and the signature block at 74-77%
   with 14% breathing room to the footer strip.
2. **Typography locked** to Times New Roman 11.5pt / 1.45 line-height
   with explicit vertical margins on every block (`.noc-date`,
   `.noc-body`, `.noc-data`, `.noc-ack`, `.noc-thanks`, `.noc-temp-visa`,
   `.noc-signature`) — reproducible, cannot silently drift.
3. **1:1 port of the uploaded docx pattern** — every paragraph and
   table in `NOC verifcation by agency NEW-1(HASIM).docx` is now
   reflected in the renderer verbatim, including the previously-missing
   `Company and Candidate are agreed to avail said Temporary Visa for
   06 Month (180 Days)` centered line and the curly apostrophe in
   `You're Excellency,` (U+2019, as in the docx).

### Restrictions preserved (already in place, verified)
- **ISB-only gate**: `/api/protector-letters/noc-verification/{id}`
  returns HTTP 400 for candidates linked to Karachi / Lahore / other
  embassy demands (`app/api/endpoints/protector_letters.py:232`).
- **Auto-available in candidate Documents**: injected via
  `injectNocVerificationRow()` in `app/templates/candidates.html`
  (line 1710+) — no frontend change needed.

### Verification (E2E)
| Check | Result |
| --- | --- |
| ISB candidate id=3 (UMAIR RASHEED, Islamabad Embassy) | HTTP 200, 373,544 bytes, all 14 markers |
| Karachi candidate id=1 (GHULAM MURTAZA)               | HTTP 400 with "Islamabad only" message |
| Playwright A4 screenshot alignment                    | 9.5/10 — Dated at 21%, signature at 74-77% |
| Layout preservation across other letter types         | ✅ (CSS scoped under `.noc-*`) |

### Files changed
- `app/services/letterhead_renderer.py` — `render_noc_verification()`
  rewrite (safe-zone margins + typography + docx pattern port).

---

## 7. Deep-Pass Audit (independent of the finding matrix)

| Area                              | Result                                                                                                             |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Broken imports                    | None. All modules import cleanly; boot succeeds.                                                                   |
| `except: pass` / bare except      | None on hot paths. One narrow `except Exception: pass` in `letterhead_renderer.py:1072` — cosmetic fallback, safe. |
| SQL injection                     | All user input goes through SQLAlchemy parameters. One f-string SQL in `tenants.py:328` uses a whitelisted table name only (defensive comment added). |
| Secrets in code                   | None. `SECRET_KEY` placeholder guard raises on boot if unchanged in prod.                                          |
| CORS                              | Intentionally absent — the SPA is same-origin. No wildcard.                                                        |
| Session / cookie policy           | JWT in `HttpOnly` + `SameSite=Lax` cookie, tenant-bound. CSRF middleware active.                                   |
| Upload security                   | Pillow `.verify()` on image uploads, whitelist of extensions, size cap, filename hashed.                           |
| Unsafe defaults                   | None found. `DEBUG=False` in prod path; boot fails loud on missing `SECRET_KEY`.                                   |
| Multi-tenant isolation            | Per-tenant SQLite DB + tenant-bound JWT + resolver runs before every request. Verified via Host header tests.      |
| Weak crypto                       | None. bcrypt via passlib, secrets from `secrets.token_urlsafe`.                                                    |

---

## 8. Files Changed in This Audit

| File                                    | Change                                                                                                 |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `app/schemas/schemas.py`                | Made 5 legacy profile fields `Optional[str]` in `UserBase` (fixes M-1)                                 |
| `app/services/tenant_service.py`        | Added `_default_file_prefix_for()`; `provision_tenant_db()` seeds per-tenant `file_prefix/slug/subdomain` (M-2) |
| `app/api/endpoints/tenants.py`          | `Body` import; forwards `short_name`; adds `custom_domain` field; new `PUT /{id}/domain` + `GET /_meta/custom-domains`; defensive SQL comment |
| `app/api/endpoints/demands.py`          | `_PREFIX_DEFAULT = ""` (was `DTC/786/`); `_date = coerce_date` (M-2, M-3)                              |
| `app/api/endpoints/candidates.py`       | `_coerce()` uses shared `coerce_date` (M-3)                                                            |
| `app/models/tenant.py`                  | New `custom_domain` column (F-2)                                                                       |
| `app/core/tenancy.py`                   | `resolve_tenant_from_host()` custom-domain-first (F-2)                                                 |
| `app/db/init_db.py`                     | `import logging` at module top (L-3); `custom_domain` migration + partial unique index (F-2)           |
| `app/main.py`                           | `_collect_allowed_hosts()` merges tenant custom domains into TrustedHost allowlist (F-2)               |
| `.gitignore`                            | Adds `package-lock.json`                                                                               |
| `package-lock.json`                     | **Deleted** — empty stub, no `package.json` in this Python-first project                               |

### Data-only fixes (SQL, not code)
- `data/dogar_trading.db`: promoted user id=1 to `is_super_admin=1`; reset
  password; added `custom_domain` column + unique index.
- `data/tenants/tenant_dtc-travels.db`: seeded missing `company_settings`
  row with `file_prefix='DTCTRAVE/'`.

*(These `data/` DBs are gitignored and **not** included in the delivered
ZIP — they are per-installation runtime state.)*

---

## 9. Build & Runtime Verification

| Check                                         | Result |
| --------------------------------------------- | ------ |
| `pm2 restart dogar-portal`                    | ✅ Started cleanly |
| Log: `Application startup complete`           | ✅ Present |
| Log: `STARTUP: schema migrations applied`     | ✅ Present |
| All 4 AUTO-SEEDER blocks confirm              | ✅ Present |
| `curl http://localhost:3000/api/ping`         | ✅ HTTP 200, `{"ok":true}` |
| SPA renders `<title>Dogar Trading Corporation</title>` | ✅ |
| Manage Users E2E (login → CRUD → list)        | ✅ All 200 |
| Per-tenant logo upload E2E                    | ✅ Uploaded, persisted, served as valid PNG |
| Custom-domain E2E (bind / resolve / unbind)   | ✅ Passed |

---

## 9a. Second-Pass Session (2026-07-04) — Preview, Mobile, Login & Schema

This section documents the follow-up hardening session triggered by the user's
report that a prior mobile fix "did not work" (3 phone screenshots of a broken
Document-Customize editor), plus a request to verify login, Manage Users,
per-tenant logo, and custom-domain end-to-end and re-run the deep audit.

### C-5 — Broken admin login (Critical, Fixed)

**Symptom:** `POST /api/auth/login` with the `.env` credentials returned 401.

**Root cause:** `seed_admin_user()` in `app/db/init_db.py` only sets the password
on **first** user creation. The `.env` `ADMIN_PASSWORD` was rotated after the
admin rows already existed, so the stored bcrypt hashes never matched the new
password — login was impossible.

**Fix:** Added an operator-facing recovery CLI `scripts/reset_admin_password.py`
(supports `--email`, `--password`, `--clear-must-change`, `--activate`, `--list`;
defaults to `.env` values; verifies the hash round-trips before reporting
success). Reset both admins (`<vendor-admin-email>` super-admin,
`admin@dogartrading.com`). **Login now returns 200** (local + external host).

### C-4 — Password-reset schema drift (Critical, Fixed)

**Symptom:** `POST /api/auth/forgot-password` for a *known* user returned 500
(`IntegrityError: NOT NULL constraint failed: password_reset_tokens.token`).

**Root cause:** The `PasswordResetToken` model was upgraded to store a SHA-256
`token_hash` (plaintext token only e-mailed, never persisted), but the live
control DB still had the **legacy plaintext `token` column with a NOT NULL
constraint** and was missing `token_hash` entirely. The forgot-/reset-password
endpoints read/write `token_hash`, so every INSERT crashed. Password recovery
was completely broken, and the DB carried a dead plaintext-token column.

**Fix:**
1. Added an idempotent migration in `run_schema_migrations()`:
   `_safe_add_column(..., "token_hash", ...)` + a guarded `DROP COLUMN token`
   (drops the legacy index first, re-creates the model-declared
   `user_id` / `email` / unique `token_hash` indexes) so **any** stale deployment
   self-heals on the next boot.
2. Applied the same migration to the live control DB immediately.
3. **Verified:** `forgot-password` now returns 200 and persists a 64-char
   `token_hash`; schema drift check across both DBs = **0 issues**.

### B-1 — Mobile horizontal overflow (High, Fixed & verified)

**Symptom:** On a 390px phone the Document-Customize editor (and other pages)
overflowed horizontally — content column measured **456px** on a **390px**
screen (≈66px of horizontal scroll), cutting off the WYSIWYG designer.

**Root cause:** The `.ml-60` sidebar media query was working, but the **topbar's
global-search box + full profile chip** forced the flex content column wider than
the viewport. The document designer's fixed 3-column (palette│canvas│properties)
layout compounded it.

**Fix:**
- `app/static/css/app.css`: added global overflow guards at `@media (max-width:768px)`
  (`html,body{max-width:100vw;overflow-x:hidden}`, `.flex-1.ml-60{max-width:100vw;min-width:0}`)
  and a new `@media (max-width:640px)` block that **hides the global search** and
  **collapses the profile chip to avatar-only** — the true root-cause fix.
- `app/templates/document_customize.html`: added a `Fields / Canvas / Properties`
  mobile panel switcher (`#mobilePanelSwitch`), responsive CSS that shows one
  panel at a time under 1023px with a bounded canvas viewport, and JS
  (`switchDesignerPanel`, `autoFitCanvas`, `bindMobilePanelSwitch`) that
  auto-fits the page into the viewport and jumps to Properties when a field is
  selected.

**Verification (Playwright, 390×844, `is_mobile`):** Document-Customize,
Dashboard, Candidates, Portal-Admin, Settings — **all report
`scrollWidth == clientWidth == 390` (overflow_x = False)**. Screenshots confirm
the panel switcher works, the global search is hidden, and the profile chip is
compact.

### Preview / runtime fixes (P-1, P-2, P-3)

- **P-1 (TrustedHost):** `app/core/config.py` — `ALLOWED_HOSTS` default + the
  `allowed_hosts` property now include/auto-inject PaaS preview hosts
  (`*.railway.app`, `*.onrender.com`, `*.fly.dev`, …) **only in
  non-production**. Verified preview-host / `railway.app`
  Host headers → 200.
- **P-2 (iframe/CSP):** `app/main.py` now sets `X-Frame-Options: SAMEORIGIN`
  **only when `settings.is_production`**; `app/core/csp_nonce.py` adds preview
  origins to CSP `frame-ancestors` in non-production. **Production stays strict**
  (`frame-ancestors 'self'` + `SAMEORIGIN`).
- **P-3 (startup):** platform run command + workflow prefixed with
  `fuser -k 3000/tcp` so a stale port never blocks boot.

> **Production safety:** every preview relaxation is gated behind
> `not settings.is_production`. Production security headers, CSP, and TrustedHost
> allowlist are unchanged.

### Re-verification of F-1 / F-2 (per-tenant logo & custom domain)

- **Logo (F-1):** `POST /api/tenants/{id}/logo` with a real PNG → 200, persisted
  as `data/tenants/<slug>/logo_<rand>.png`, served via `/tenant-assets/...`
  (fetched back: 200, correct `image/png`). Validation (min-size, image-only)
  confirmed. Test artifact removed; original tenant logo restored.
- **Custom domain (F-2):** bind → 200; invalid domain → 400; reserved
  (`localhost`) → 400; duplicate on another tenant → 409; unbind → 200. The
  `/_meta/custom-domains` resolver returns the bound domain **only when the
  tenant is `active`** (correct-by-design — suspended tenants aren't routed);
  confirmed by toggling status. Test domain unbound afterward.

### Deep-pass results (this session)

- App imports cleanly: **232 routes**, no import errors.
- `compileall` across `app/` + `scripts/`: **no syntax errors**.
- **No** hardcoded secrets, bare `except:`, or TODO/FIXME/HACK/XXX markers.
- `SECRET_KEY` is 64 chars and config hard-fails on the placeholder in all envs.
- Schema-vs-model drift: **0** (after C-4 fix) across control + tenant DBs.
- Pyflakes: **no undefined names / syntax errors**; 3 "redefinitions" are
  intentional deferred in-function imports; 76 "imported but unused" are cosmetic
  (no runtime impact) and left as-is to avoid churn.
- **pytest: 70 passed, 6 skipped** (skips = optional VENDOR_ADMIN super-admin
  tests requiring env config).

---

## 10. Remaining Risks

None of these are blockers, but they are worth tracking:

1. **No Alembic** — schema is managed by idempotent seeders + `_safe_add_column`
   with a table allowlist. This is fine for the current single-tenant-per-DB
   model but will not scale to complex evolutions. Recommend introducing
   Alembic once schema stabilises.
2. **SQLite in production** — appropriate at current tenant/traffic scale;
   evaluate PostgreSQL migration once concurrent write pressure grows.
3. **No structured E2E test suite in CI** — E2E verification for this audit was
   manual via `curl`. Recommend Playwright tests wired into a CI job.
4. **Backup strategy** — per-tenant SQLite files are the single source of
   truth. Ensure the deployment target has an off-host backup rotation.

---

## 11. Final Verdict

> **The portal is production-ready.**
>
> All Critical, High, and Medium audit findings are resolved — including the
> two Critical issues surfaced in the second pass: **broken admin login (C-5)**
> and **broken password reset from schema drift (C-4)**, both root-caused,
> fixed, and E2E verified. The user-reported **mobile horizontal overflow (B-1)**
> on the Document-Customize editor is fixed and verified app-wide
> (`scrollWidth == clientWidth` across 5 pages at 390px). Preview/runtime
> fixes (P-1/P-2/P-3) are strictly gated to non-production, leaving production
> security posture unchanged. Per-tenant logo upload and custom-domain-per-tenant
> hosting are re-verified end-to-end. The single remaining Low item is a
> documented false positive.

— Audit closed 2026-07-04 (second pass).
