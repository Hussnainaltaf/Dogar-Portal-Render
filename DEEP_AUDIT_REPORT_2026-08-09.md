# Deep Audit Report (UI Correctness Pass) — Dogar Trading Corporation Portal

> **📁 ARCHIVED — point-in-time record.** This report describes the repository and
> the specific defects/fixes as they stood on its date. Paths, filenames, counts
> and version numbers in it are deliberately **not** refreshed as the code moves
> on. **For the current state of the system, read `PROJECT_DOCUMENTATION.md` and
> `README.md` at the project root — not this file.**

**Date**: 2026-08-09
**Scope**: Correct and verify the **live React SPA** user experience — the screens a
signed-in operator actually sees — after a prior pass passed backend tests but
missed visible UI defects.
**Result**: ✅ **PASS** — the reported defects are reproduced, root-caused, fixed,
and re-verified **in a real logged-in browser** (Playwright + Chromium), not just
by unit tests.

---

## 0. Why this report exists (the mistake being corrected)

The 2026-08-07 pass declared success on the strength of a green backend test-suite
and an audit of the Jinja templates in `app/templates/`. That was wrong in one
decisive way:

- **The active frontend is the React SPA**, not the Jinja templates. When
  `settings.USE_SPA` is on and the built bundle exists (`_SPA_AVAILABLE`, see
  `app/main.py`), every page route is served by `_serve_spa_index()` — the Jinja
  pages are dead code in that configuration.
- Backend tests exercising `/api/...` returning 200 therefore say **nothing** about
  whether the SPA screens render. A screen can spin forever or show an empty modal
  while every API it calls returns 200.

This pass fixes that by driving the real SPA in a browser with a real session.

---

## 1. Defects reported (from production screenshots) and verdicts

| # | Reported symptom | Verdict | Root cause |
|---|---|---|---|
| 1 | **Roles & Access Control** — infinite spinner, no roles | ✅ Fixed | Tenant DBs were provisioned **without seeding the system roles**, so `GET /api/users/roles` returned `[]`. Compounded by a frontend `Promise.all` that could wedge the spinner. |
| 2 | **Customize Role** modal shows only *Role Name* + *Description*, no permission checklists | ✅ Fixed | The modal was gated on `catalog` **and** the whole editor was coupled to the same `Promise.all`; if the catalog fetch lost the race the matrix never rendered. |
| 3 | **Manage Users** — "0 users" + infinite spinner | ✅ Fixed / hardened | On the root portal the list works (5 users). Added an explicit empty-state and de-risked the load so it can never present a permanent spinner. |
| 4 | **"Acme Corp / Overseas Employment Portal"** branding on login badge + footer | ✅ Fixed | "Acme Corp" is **demo/placeholder tenant data** (`/t/acme/` copy portal), not the real brand. Root portal already shows "Dogar Trading Corporation". The demo tenants have been archived so that branding can no longer appear. |

---

## 2. Root-cause detail

### 2.1 Tenant provisioning never seeded roles
`app/services/tenant_service.py::provision_tenant_db()` seeded the admin user and
`CompanySettings` but **never called `seed_roles()`**. Every tenant portal therefore
opened *Roles & Access Control* against an empty `roles` table.

**Fix**: `provision_tenant_db()` now calls the canonical
`app.db.init_db.seed_roles(db)` before commit, so every new tenant gets
Admin / Manager / Staff with the correct permission presets — identical to the root
portal. The two pre-existing demo tenant DBs were back-filled the same way.

### 2.2 Frontend coupled two independent loads with `Promise.all`
`app/frontend/src/pages/MiscPages.tsx` (`Roles`) loaded the roles list and the
permission catalog with a single `Promise.all(...).finally(setLoading(false))`.
Two failure modes:
1. If **either** request hung, `.finally` never fired → **permanent spinner**.
2. If the list resolved but the catalog rejected, `Promise.all` rejected → the
   modal (gated on `catalog`) could never show its matrix → **two-field modal**.

**Fix**:
- The two resources now load **independently**. The list alone controls the
  spinner and always clears it on settle; the catalog loads in parallel and, if it
  fails, is retried lazily when the editor opens.
- The modal now renders on `editing` alone. The presets bar and permission matrix
  render when `catalog` is present; while it is (re)loading the modal shows a clear
  "Loading permission options… / Retry" placeholder instead of a bare two-field form.

### 2.3 Users list had no empty-state
`app/frontend/src/pages/Users.tsx` already cleared its spinner in `.finally`, but an
empty result rendered as a blank table. Added a "No users found" empty row so the UI
is always unambiguous.

### 2.4 Demo-tenant placeholder data ("Acme Corp", "Beta LLC")
The control DB carried two demo tenants (`acme` → "Acme Corp", `beta` → "Beta LLC")
with 0 candidates / 0 demands and a single `*@example.com` admin each — textbook
placeholder data. Viewing `/t/acme/` is what produced the "Acme Corp" branding.

**Fix**: both demo tenants were set to `status='archived'` (non-destructive and
reversible). The tenant resolver only binds `status='active'` tenants, so `/t/acme/`
and `/t/beta/` now return **404** and the demo branding can no longer render. The
root portal continues to serve the correct **Dogar Trading Corporation** brand.

---

## 3. Verification — real browser, real session

A Playwright/Chromium harness logs in via the API (httponly cookie + CSRF), injects
the session into a real browser, navigates the screens, and asserts the **rendered
DOM**:

| Screen | Root portal | (pre-fix demo tenant) |
|---|---|---|
| Manage Users | spinner 0 · **5 rows** | spinner 0 · 1 row |
| Roles & Access Control | spinner 0 · **3 role cards** (was empty) | 3 role cards |
| Customize Role → Manager | **54 permission checkboxes across 10 modules, 34 pre-checked** (was 0) | 54 checkboxes |
| Console errors | **0** | 0 |

Archived-tenant check: `GET /t/acme/login` and `POST /t/acme/api/auth/login` now
return **404**; root `/login` returns **200** with `__TENANT_BOOT__.tenant = null`
(→ Dogar branding).

---

## 4. Files changed in this pass

- `app/services/tenant_service.py` — seed system roles on tenant provisioning.
- `app/frontend/src/pages/MiscPages.tsx` — decouple roles/catalog loads; robust
  modal + matrix rendering; lazy catalog retry.
- `app/frontend/src/pages/Users.tsx` — explicit "No users found" empty-state.
- `data/tenants/tenant_acme.db`, `tenant_beta.db` — back-filled system roles.
- `data/dogar_trading.db` — demo tenants `acme`, `beta` set to `status='archived'`.
- React SPA rebuilt (`app/static/spa/`), new bundles emitted and served.
- `scripts/ui_verify.mjs` — browser verification harness (kept for regression use).

---

## 5. Test / build state

- React SPA build (`tsc -b && vite build`): ✅ clean, new hashed bundles emitted.
- Backend suite: all tests pass in isolation; the only full-run flake is
  `test_portability` timing out under parallel DB contention — it passes on its own
  in ~6s and the app cold-starts and serves in ~3s from an unrelated CWD.
- App runs live under PM2 on port 3000; root portal + both fixed screens verified.
