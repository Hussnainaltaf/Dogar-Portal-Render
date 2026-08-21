# Full Deep Audit Report — Dogar Trading Corporation Portal

> **📁 ARCHIVED — point-in-time record.** This report describes the repository as it stood at the time it was written.
> Paths, filenames, counts and version numbers in it are deliberately **not**
> refreshed as the code moves on, so the document stays an honest account of
> what was actually found and fixed then. **This report is SUPERSEDED by
> `DEEP_AUDIT_REPORT_2026-08-09.md`**, which corrects a critical gap: the
> 2026-08-07 pass ran only the backend test-suite and audited the (inactive)
> Jinja templates, so it MISSED live React-SPA defects (empty Roles page,
> "Customize Role" modal missing its permission matrix, demo-tenant "Acme Corp"
> branding). **For the current state of the system, read
> `DEEP_AUDIT_REPORT_2026-08-09.md`, `PROJECT_DOCUMENTATION.md` and `README.md`.**

**Date**: 2026-08-07
**Scope**: Full-codebase deep audit — extraction, module analysis, bug/import/DB/security review, fixes, build, run, live verification, packaging.
**Result**: ✅ **PASS** — app builds, imports, runs live, all ~590 tests green, real data intact, 80 of 88 dependency CVEs remediated.

---

## 1. What was audited

| Area | Method | Result |
|------|--------|--------|
| Source modules | 78 Python files under `app/` | All compile (`compileall`), all import |
| Static analysis | `ruff check --select F,E9` (undefined names, imports, syntax) | **0 issues** |
| Import graph | `from app.main import app` | OK — **232 routes** registered |
| Test suite | `pytest` (49 files, ~590 tests) | **All pass** after fixes |
| Database | schema-vs-model drift, alembic head, row counts | **0 drift**, single head matches DB |
| Security | secrets, SQL injection, eval/exec, JWT, CORS, host header | Clean (see §4) |
| Dependencies | `pip-audit` CVE scan | 88 → **8** (§4) |
| Assets/backgrounds | every template/DB asset reference resolved on disk | **0 missing** |
| Weak logic | bare-except, TODO/FIXME, mutable defaults, `assert` validation | **0 found** |
| Runtime | live boot + login + real dashboard/candidate data | **Working** |

---

## 2. Real data — preserved, not mocked

The shipped SQLite DB (`data/dogar_trading.db`, 8 MB) contains genuine production records, verified live through the API:

- **2,675 candidates**, **1,246 clients**, **2,267 demands**, 2 tenants, 5 users
- 8 document templates, 99 document fields
- Real dashboard stats (top trades: Personal Driver 665, House Worker 247, …)

All real background images under `app/static/pdf_backgrounds/` (9 bank/visa/letterhead scans) and login office images are present and serve HTTP 200. **Nothing was replaced with placeholders.**

---

## 3. Bugs found & fixed

### 3.1 Test/consistency failures (3 — all fixed)
1. **Documented table count stale** — `PROJECT_DOCUMENTATION.md` said `29 tables`; code has **31**. → Corrected to 31.
2. **Missing ARCHIVED banners** — `FULL_AUDIT_REPORT_2026-08-05.md` and `TENANT_ISOLATION_AUDIT_2026-08-05.md` (dated point-in-time reports) lacked the required ARCHIVED banner, so readers could mistake frozen paths/counts for current fact. → Added the standard banner to both.
3. **Stale geometry snapshot** — `app/data/template_geometry.json` had drifted from the source DB. → Regenerated via `scripts/export_template_geometry.py` (8 templates, 99 fields; `--check` now passes).

### 3.2 Hygiene
4. Removed stray runtime artifacts: 0-byte `control.db` and `data/perf_bench.db` (benchmark leftover). `*.db` is already gitignored.

---

## 4. Security audit

### 4.1 Dependency CVEs — the major finding (88 → 8)
`pip-audit` reported **88 known vulnerabilities in 8 packages**. Remediated by upgrading, then re-running the **full test suite (all green)** to prove no regressions:

| Package | Was | Now | Why it matters |
|---------|-----|-----|----------------|
| **PyJWT** | 2.9.0 | **2.13.0** | AUTH-CRITICAL — JWT verification CVEs (PYSEC-2026-120/175–179) |
| **python-multipart** | 0.0.12 | **0.0.31** | File-upload DoS (untrusted input) |
| **starlette** | 0.38.6 | **0.49.1** | HTTP/multipart DoS (PYSEC-2026-1941/1942/1943) |
| **fastapi** | 0.115.0 | **0.121.1** | Bumped to allow the patched Starlette |
| **jinja2** | 3.1.4 | **3.1.6** | Template/sandbox CVEs |
| **Pillow** | 12.2.0 | **≥12.3.0** | Image-parsing CVEs (untrusted photo uploads) |
| **pypdf** | 5.0.1 | **6.14.2** | 40+ PDF-parsing CVEs; 6.x API verified by tests |
| **sentry-sdk** | 2.5.1 | **2.8.0** | Env-var leak |

**Remaining 8 (accepted / deferred, documented):**
- `starlette` — the residual CVEs are only fixed in the **1.x breaking major**, which the current FastAPI line does not support. Upgrading further would destabilize the framework for diminishing returns.
- `pytest` — **dev-only** (in `requirements-dev.txt` territory), never in the production runtime.

### 4.2 Application security — already hardened (verified, no change needed)
- **Secrets**: `.env` is **not** git-tracked and is gitignored. `SECRET_KEY` hard-fails in production if placeholder/short; ephemeral in dev.
- **SQL injection**: every raw `text(f"…{table}…")` uses a **fixed allowlist + raising validation** (`_ident()`, membership check, `identifier_preparer.quote()`, `re.fullmatch`). No user input reaches raw SQL.
- **No** `eval` / `exec` / `pickle.loads` / `os.system` / `shell=True`.
- **JWT**: signature always verified with a pinned algorithm (no `alg=none`).
- **Passwords**: bcrypt via passlib; weak default-admin passwords flagged.
- **Host-header spoofing**: `TrustedHostMiddleware` + explicit `ALLOWED_HOSTS` required in prod.
- **Rate limiting**: `--forwarded-allow-ips 127.0.0.1` so `X-Forwarded-For` can't be spoofed to bypass the brute-force limiter.

---

## 5. Build / run / preview

- **Build**: `pip install -r requirements.txt` (patched pins) — clean, no conflicts.
- **Run**: `uvicorn app.main:app` under PM2 — `Application startup complete`.
- **Health**: `GET /api/health` → `{"status":"ok","db":"ok","version":"1.1.0"}`.
- **Live**: homepage 200, login works (admin), dashboard returns real data, all static assets & backgrounds 200.

### Run it yourself
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then set SECRET_KEY + ALLOWED_HOSTS + admin password
alembic upgrade head          # for a fresh DB (the shipped data/ DB is already migrated)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Generate a SECRET_KEY: `python3 -c "import secrets; print(secrets.token_hex(32))"`

---

## 6. Files changed in this audit
- `requirements.txt` — 8 security version bumps (annotated `SEC-AUDIT 2026-08-07`).
- `PROJECT_DOCUMENTATION.md` — table count 29 → 31.
- `FULL_AUDIT_REPORT_2026-08-05.md`, `TENANT_ISOLATION_AUDIT_2026-08-05.md` — ARCHIVED banners added.
- `app/data/template_geometry.json` — regenerated from DB.
- Removed: `control.db`, `data/perf_bench.db`.
- Added: this report.

## 7. Packaging note (security)
The download ZIP **excludes the real `.env`** (contains a live SECRET_KEY and admin passwords), `venv/`, `node_modules/`, caches, and `*.db-wal/-shm`. It **includes** `.env.example`, the real `data/dogar_trading.db`, and all real background images. Rotate any secret that may have previously been shared.
