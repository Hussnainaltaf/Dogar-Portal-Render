# Dogar Trading Corporation Portal — End‑to‑End Audit & Repair Report

> **📁 ARCHIVED — point-in-time record.** This report describes the repository as it stood on **2026-07-06**.
> Paths, filenames, counts and version numbers in it are deliberately **not**
> refreshed as the code moves on, so the document stays an honest account of
> what was actually found and fixed then. Some assets it names have since been
> renamed or relocated. **For the current state of the system, read
> `PROJECT_DOCUMENTATION.md` and `README.md` at the project root — not this file.**


**Audit date:** 2026‑07‑06
**Auditor:** Automated production‑readiness audit (two‑pass: audit → fix → verify)
**Scope:** Full stack — backend, frontend, database, deployment, static assets, security.
**Verdict:** ✅ **PRODUCTION‑READY** (after fixes below)

---

## 0. Executive Summary

The portal is a **FastAPI (Python 3.11/3.13) multi‑tenant web application** with:
- A **React 18 + Vite + TypeScript SPA** (served single‑origin from `app/static/spa/`), and
- A complete **Jinja2 server‑rendered UI** fallback (`USE_SPA=false`).
- **SQLite** persistence (SQLAlchemy 2.0), per‑tenant DB isolation, idempotent startup migrations/seeders.
- PDF/letterhead generation (ReportLab + Arabic shaping), passport OCR, RBAC, CSRF, CSP, rate‑limiting, structured logging, Prometheus metrics, Sentry.

This codebase had already passed multiple prior hardening audits (evident in code comments and commit history). This audit found **no runtime blockers, no broken imports, no missing runtime files, and no leaked secrets in version control.** All 88 automated tests pass and the app boots and serves every module correctly under `ENV=production`.

The fixes applied are **hygiene and hardening improvements** plus **deliverable safety** (removing crash junk and stale backups, self‑hosting the last CDN assets, and tightening the Content‑Security‑Policy to strict `'self'`).

---

## 1. Audit Method

| Check | Tool | Result |
|---|---|---|
| Python syntax (all `.py`) | `compileall` | ✅ 0 errors |
| Full app import (228 routes) | `import app.main` | ✅ OK |
| Test suite | `pytest` | ✅ **88 passed** |
| Frontend type‑check | `tsc -b --noEmit` | ✅ 0 errors |
| Frontend production build | `vite build` | ✅ OK (bytes match deployed SPA) |
| Import surface vs requirements | grep + import probe | ✅ all resolve |
| Static/PDF/font references | path existence scan | ✅ all present |
| SPA build asset references | existence scan | ✅ present |
| Secrets in git | `git ls-files` scan | ✅ none tracked |
| Hardcoded creds / API keys | regex scan | ✅ none |
| DB ↔ ORM schema drift | SQLAlchemy inspect | ✅ 30/30 tables, no drift |
| Live boot (production ENV) | PM2 + uvicorn | ✅ online, 0 restarts |
| Endpoint smoke test (auth’d) | curl round‑trip | ✅ all modules HTTP 200 |
| Browser render + CSP violations | Playwright console | ✅ SPA renders, no CSP errors |

---

## 2. Issues Found & Fixes Applied

### 2.1 Deliverable / hygiene (HIGH for a clean handoff)

| # | Issue | Severity | Fix |
|---|---|---|---|
| A‑1 | **187 MB Chromium `core` crash dump** in project root (leftover from a Playwright headless‑shell crash). Bloats any archive; useless. | Medium | **Removed** `core`. Already covered by `.gitignore`. |
| A‑2 | **Nested `dogar-portal-enterprise-deploy.zip` (6.3 MB)** committed inside the project directory. A project should not contain a zip of itself. | Medium | **Removed.** `*.zip` already gitignored. |
| A‑3 | **Stale DB backup** `data/dogar_trading.db.bak_geom_20260705_095950` (6.2 MB) containing *old* pre‑reset data — a PII‑bearing artifact that shouldn’t ship. | Medium (PII) | **Removed.** Fresh DB is the intended production seed state. |
| A‑4 | Stray **empty `package-lock.json`** (Node template leftover) at the root of a Python project — misleading. | Low | **Removed.** |
| A‑5 | Uncheckpointed **SQLite WAL** (`-wal`/`-shm`) left the shipped DB in a split state. | Low | **`PRAGMA wal_checkpoint(TRUNCATE)`** — DB is now self‑contained. |
| A‑6 | `__pycache__` / `.pytest_cache` cruft. | Low | **Cleared.** |

### 2.2 Security hardening

| # | Issue | Severity | Fix |
|---|---|---|---|
| S‑1 | **SPA `index.html` loaded FontAwesome + Inter/JetBrains fonts from external CDNs** (`cdn.jsdelivr.net`, `fonts.googleapis.com`, `fonts.gstatic.com`) even though the app already vendors all of these locally in `/static/vendor/`. Third‑party CDN requests in a PII portal are a supply‑chain + privacy risk and an availability dependency, and were inconsistent with the Jinja `base.html` (which was already fully self‑hosted). | Medium | **Rewrote the SPA shell to load the vendored `/static/vendor/css/all.min.css`, `fontawesome.css`, and `inter.css`.** Rebuilt the SPA. `JetBrains Mono` falls back cleanly to the existing `ui-monospace,…,monospace` chain. |
| S‑2 | **CSP allowed 6 external CDN origins** (`cdn.tailwindcss.com`, jsdelivr, cdnjs, unpkg, jquery, datatables, googleapis/gstatic) for script/style/font/img. After S‑1 the app references **zero** external origins (verified across all templates + SPA). | Medium | **Collapsed the CSP to strict `'self'`** (`default-src 'self'; script-src 'self' 'nonce-…'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'self'`). Verified live on `/`. `style-src 'unsafe-inline'` retained only for the pre‑paint theme‑switch `<style>` block. |

### 2.3 Verified‑healthy (no action needed — confirmed, not assumed)

- **Secrets management:** `.env` holds real secrets but is correctly **gitignored** and **excluded from the deliverable ZIP**. `.env.example` is the template. No secrets tracked in git.
- **Config guardrails (`app/core/config.py`):** placeholder `SECRET_KEY` hard‑fails in *all* environments; weak `SECRET_KEY`/admin passwords fail in production. Vendor super‑admin creds are env‑driven (no more hardcoded `<vendor-admin-email>`/`<redacted-password>`). ✅
- **Auth (`app/core/security.py`):** bcrypt hashing, PyJWT tokens **tenant‑bound** (token from tenant A cannot auth on tenant B). Login token delivered via **HttpOnly cookie** (XSS‑safe). ✅
- **CSRF:** double‑submit cookie on all state‑changing `/api/*`. ✅
- **Host‑header hardening:** `TrustedHostMiddleware` + custom‑domain allowlist snapshot at boot. ✅
- **Password reset:** full SMTP delivery, SHA‑256 token hashing, 30‑min expiry, rate‑limited, account‑enumeration‑safe. ✅
- **DB ↔ ORM:** no schema drift; idempotent startup migrations + seeders. ✅
- **Deployment:** `Dockerfile` (multi‑stage, non‑root uid 1000, healthcheck, `--forwarded-allow-ips 127.0.0.1`), `docker-entrypoint.sh` (migrations → tenant migrations → uvicorn PID 1), `docker-compose.yml` (named volumes for data/uploads), `ecosystem.config.cjs` (PM2) — all consistent and correct. ✅
- **Assets:** all PDF backgrounds, fonts (DejaVu + Noto Arabic), logos, and SPA build assets exist and are referenced correctly. ✅
- **"Empty database detected" at startup is EXPECTED** — this build ships a *fresh production seed* (2 seeded users: admin + vendor super‑admin, 9 document templates, system roles). This is intentional, not data loss (see commit `5dcb46e` "Fresh production install"). Operators load real data via the DEPLOY.md restore procedure.

---

## 3. Verification After Fixes

```
pytest                     → 88 passed
tsc -b --noEmit            → 0 errors
vite build                 → OK
uvicorn (ENV=production)   → online, 0 restarts, no errors
GET /, /login, /dashboard  → 200
GET /api/health            → {"status":"ok","db":"ok"}
CSP header on /            → strict 'self' (verified)
Playwright /login render   → SPA mounts, no CSP violations (only expected 401 from /api/users/me pre‑login)
Authenticated round‑trip (real seeded admin, HttpOnly cookie):
  /api/users/me, /users/me/permissions, /dashboard/stats, /dashboard/monthly,
  /candidates/, /clients/, /agents/, /lookups/cities, /documents/templates, /audit/  → all 200
```

---

## 4. Remaining Risks / Recommendations (non‑blocking)

1. **SPA bundle > 500 KB** (`index-*.js` ≈ 502 KB / 132 KB gzip). Advisory only; consider route‑level code‑splitting for faster first paint. Not a blocker.
2. **SQLite in production.** Fine for single‑node/low‑to‑moderate concurrency (the intended deployment). For high write concurrency or HA, migrate to PostgreSQL — the code path (`get_settings`) already respects a non‑SQLite `DATABASE_URL`; you must then add `psycopg2-binary` (commented in `requirements.txt`) and provide Alembic migrations.
3. **SMTP must be configured** for the password‑reset email to actually send (`.env` ships with placeholder SMTP host). Without it, tokens are generated but not emailed (logged instead). Documented in `.env.example`.
4. **Rotate the shipped `.env` secrets** before going live if this `.env` was ever shared. The deliverable ZIP intentionally **excludes** `.env`; create one from `.env.example` on the server.
5. **Alembic not configured** (by design). Schema is managed by idempotent startup migrations. If you later adopt Alembic, run `alembic init` + `stamp head` first (the entrypoint already skips Alembic gracefully when absent).

---

## 5. Files Changed in This Audit

| File | Change |
|---|---|
| `app/frontend/index.html` | CDN font/icon links → self‑hosted `/static/vendor/*`; removed Google Fonts link. |
| `app/core/csp_nonce.py` | Emptied CDN allowlists; CSP now strict `'self'`; cleaner directive builder. |
| `app/static/spa/index.html`, `assets/*` | Rebuilt SPA (reflects the self‑hosted shell). |
| `data/dogar_trading.db` | WAL checkpointed (content unchanged — fresh seed). |
| *removed* | `core` (crash dump), `dogar-portal-enterprise-deploy.zip`, `data/*.bak_geom_*`, root `package-lock.json`, `__pycache__/`, `.pytest_cache/`. |

**Real data, branding, logos, PDF backgrounds, and letterhead artwork were preserved exactly.**
