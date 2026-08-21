# Fixes Applied — 2026-07-10

> **📁 ARCHIVED — point-in-time record.** This report describes the repository as it stood on **2026-07-10**.
> Paths, filenames, counts and version numbers in it are deliberately **not**
> refreshed as the code moves on, so the document stays an honest account of
> what was actually found and fixed then. Some assets it names have since been
> renamed or relocated. **For the current state of the system, read
> `PROJECT_DOCUMENTATION.md` and `README.md` at the project root — not this file.**


This document records the resolution of **three live runtime bugs** reported by the
operator plus the application of every finding in
`DOGAR_PORTAL_AUDIT_2026-07-10.md` (H-1, H-2, M-1…M-5, L-1, L-2, L-6).

All changes have been verified by:

- **Unit / integration tests**: `TESTING=true pytest` → **138 passed**
- **Lint**: `ruff check .` → **All checks passed**
- **Type check**: `tsc -b --noEmit` → exit 0
- **Live E2E** (Playwright, real browser against the running portal):
  `scripts/e2e_smoke.py` → **5/5 checks passed**

---

## Part A — Three live runtime bugs (BLOCKING)

### Bug #1 — Login page auto-reload loop
**Symptom:** the login page kept reloading/refreshing, never letting the user type
credentials.

**Root cause:** two compounding issues in the SPA:
1. `isOnPublicPath()` in `app/frontend/src/lib/api.ts` did not strip the tenant base
   prefix (`/t/<slug>`), so on tenant paths it never recognised `/login` as public.
2. The Axios **401 response interceptor** hard-reloaded the page
   (`window.location.href = …`) on *any* 401 — including the expected `/users/me`
   probe that `AuthProvider.refresh()` fires on mount when nobody is logged in.
   That reload re-mounted the provider, which fired `/users/me` again → 401 →
   reload → **infinite loop**.

**Fix (`app/frontend/src/lib/api.ts`):**
- `isOnPublicPath()` now strips `TENANT_BASE` before matching public prefixes.
- Added `isSilentAuthProbe()` — `/users/me` and `/users/me/permissions` are treated
  as silent probes; a 401 on them **never** triggers a redirect/reload.
- The interceptor only redirects when `!isSilentAuthProbe(url) && !isOnPublicPath()`.

**E2E proof:** `Bug#1 login no reload-loop — 0 extra main-frame navigations in 6s`
and `Bug#1 password field interactable — value='typing-probe'`.

---

### Bug #2 — Full-page reload on every click
**Symptom:** every navigation reloaded the whole portal (sidebar flashed/re-rendered)
instead of swapping only the right-hand content pane.

**Root cause:** the "Open file" action on a candidate row in
`app/frontend/src/pages/Candidates.tsx` used a raw `<a href>` (line ~548), which the
browser treats as a full document navigation.

**Fix:** replaced `<a href={…}>` with React Router `<Link to={…}>` so navigation is
client-side. (The sidebar already used `NavLink`, which was correct.)

**E2E proof:** `Bug#2 SPA nav (no full reload) — sidebar_tag_preserved=True
load_event=False` (a DOM node tagged on the sidebar survives the navigation, and no
`load` event fires).

---

### Bug #3 — Candidate detail redirect from a demand file
**Symptom:** clicking "view detail of candidate" from a demand file's Trades tab did
**not** open the detail in place; it behaved as if it first went to the candidate list.

**Root cause:** `DemandDetail.tsx` used a separate lightweight `CandidatePanel`
(and in places linked to `/candidates?open=X`), diverging from the rich drawer used
on the Candidates page.

**Fix:**
- Exported the canonical drawer: `Candidates.tsx` → `export function CandidateDrawer`.
- `DemandDetail.tsx` now imports and renders that shared `CandidateDrawer` directly,
  driven by a `viewCandidateId` state, so the detail opens **in place** over the
  demand file. Deleted the old `CandidatePanel`/`PanelSection`/`PanelRow` and the
  now-unused `createPortal` import.

**E2E proof:** `Bug#3 candidate drawer opens in place — dialog=True url_stayed=True
url=…/demands/2264` (drawer appears, URL never detours to `/candidates`).

---

## Part B — Audit findings (`DOGAR_PORTAL_AUDIT_2026-07-10.md`)

| ID  | Severity | Fix summary | Files |
|-----|----------|-------------|-------|
| H-1 | High | Path-traversal hardening for PDF background images: filename collapsed to `os.path.basename`, absolute-path escape removed, realpath boundary check against allowed roots. 5 new tests. | `app/api/endpoints/documents.py`, `app/services/pdf_engine.py`, `tests/test_bg_path_traversal.py` |
| H-2 | High | Docker persistence for PDF backgrounds: named volume `dogar-pdf-bg` mounted at `/app/app/static/pdf_backgrounds`; `Dockerfile` creates+chowns the dir. | `docker-compose.yml`, `Dockerfile`, `DEPLOY.md` |
| M-1 | Medium | JWT access-token lifetime reduced from 1440 min (24h) to **120 min**. | `app/core/config.py` |
| M-2 | Medium | Rate-limiter key function is now **proxy-aware** (mirrors `auth._client_ip` `TRUSTED_PROXY_COUNT` logic) so limits key on the real client IP. | `app/core/limiter.py` |
| M-3 | Medium | Production start now **hard-fails** if `ALLOWED_HOSTS` is empty or contains `"*"`. Test added. | `app/core/config.py`, `tests/test_config_guard.py` |
| M-4 | Medium | Docker healthcheck switched to `GET /api/ping`. | `docker-compose.yml` |
| M-5 | Medium | `DEFAULT_ADMIN_PASSWORD` default is now `""` (no baked-in `admin123`); `seed_admin_user()` **skips** creating an admin when unset. Test added. | `app/core/config.py`, `app/db/init_db.py`, `tests/test_config_guard.py` |
| L-1 | Low | CSRF cookie `samesite="lax"` documented as deliberate for the double-submit token (behaviour unchanged). | `app/main.py` |
| L-2 | Low | Production start now hard-fails if `TESTING=true`. Test added. | `app/core/config.py`, `tests/test_config_guard.py` |
| L-6 | Low | Documented the `--workers 1` SQLite single-writer constraint. | `docker-entrypoint.sh`, `DEPLOY.md` |

---

## Verification commands

```bash
# Unit / integration
TESTING=true venv/bin/pytest        # 138 passed

# Lint + types
venv/bin/ruff check .               # All checks passed
(cd app/frontend && npx tsc -b --noEmit)   # exit 0

# Live end-to-end (real browser)
venv/bin/python scripts/e2e_smoke.py \
    https://<host> admin@yourcompany.com '<password>' 2264
# → 5/5 checks passed
```

## Commits

- `1f726e2` fix(spa): kill login reload loop + full-page reloads + candidate drawer redirect
- `611708d` security(audit): apply DOGAR_PORTAL_AUDIT_2026-07-10 fixes (H-1,H-2,M-1..M-5,L-1,L-2,L-6)
- `bc29ff0` test(e2e): live Playwright smoke covering the 3 runtime bugs (5/5 pass)
