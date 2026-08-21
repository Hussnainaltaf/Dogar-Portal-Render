"""Dogar Trading Corporation Portal -- Main FastAPI Application."""
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.endpoints import (
    auth,
    backups,
    candidates,
    chatbot,
    clients,
    dashboard,
    demands,
    documents,
    form_schemas,
    lookups,
    messaging,
    ocr,
    ocr_validate,
    protector_letters,
    realtime,
    reminders,
    users,
)
from app.api.endpoints import settings as settings_api
from app.api.endpoints import tenants as tenants_api
from app.core.config import settings
from app.core.csp_nonce import CspNonceMiddleware
from app.core.limiter import RateLimitExceeded, limiter
from app.core.logging import RequestLoggingMiddleware, configure_logging
from app.core.metrics import init_metrics
from app.core.request_id import RequestIdMiddleware
from app.core.sentry import init_sentry

# AUDIT 2026-07-29 (Low): every static/template directory used to be a
# CWD-relative string ("app/static", "data/tenants"), so the
# app only worked when launched from the project root. Starting it from anywhere
# else -- a systemd unit without WorkingDirectory, a cron job, `uvicorn` run
# from /, a container with a different WORKDIR -- made StaticFiles raise at
# import time or, worse, silently serve nothing.
# Anchoring on __file__ makes the process location-independent.
#   APP_DIR      = <project>/app
#   PROJECT_ROOT = <project>
_APP_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _APP_DIR.parent

# HIGH-01 FIX: Removed automatic Base.metadata.create_all() at IMPORT time.
#
# Schema management uses the checked-in Alembic configuration for deployment
# migrations and the idempotent compatibility migration below for legacy
# installations that pre-date Alembic. Runtime startup never invents schema.
#   1. _on_startup() → run_schema_migrations()  — idempotent, allow-listed
#      ALTER TABLE column-adds for existing DBs (app/db/init_db.py).
#   2. _on_startup() → idempotent admin / roles / document template seeders.
# docker-entrypoint.sh runs `alembic upgrade head` before the ASGI process.

# C-04: Disable interactive API docs when running in production.
# Swagger UI and ReDoc expose the full API surface (all endpoints, field names,
# auth flows) to unauthenticated users. They should only be reachable in
# dev/staging environments.
_docs_url    = "/api/docs"           if not settings.is_production else None
_redoc_url   = "/api/redoc"          if not settings.is_production else None
_openapi_url = "/api/openapi.json"   if not settings.is_production else None

# FIX-02: _lifespan defined HERE (before app=FastAPI) to fix NameError forward reference.
# ---------------------------------------------------------------------------
# LIFESPAN  (M-06: replaces deprecated @app.on_event("startup"))
# ---------------------------------------------------------------------------
# FastAPI >= 0.93 recommends the lifespan context manager instead of
# @app.on_event("startup"). The old decorator generates a DeprecationWarning
# on every boot and will be removed in a future FastAPI release.
@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Structured JSON logging + Sentry (idempotent -- safe to call twice)
    configure_logging()
    init_sentry()
    _on_startup()
    yield
    # Shutdown logic can be added here


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Full-stack portal for overseas employment management, visa processing, and PDF document generation.",
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
    lifespan=_lifespan,  # M-06: replaces deprecated @app.on_event("startup")
)

# Attach slowapi limiter to app state for use in endpoint decorators
app.state.limiter = limiter
def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded", "retry_after": 60},
        headers={"Retry-After": "60"},
    )
app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
# C-05: Disable rate limiting in automated tests to avoid flaky test failures
if os.environ.get("TESTING") != "true":
    app.add_middleware(SlowAPIMiddleware)

# NOTE (Audit fix -- dead-code removal):
# _apply_bundled_sql_dump() was removed in this release. It referenced
# migrations/dogar_full_backup.sql(.gz), migrations/legacy_backup.sql, and
# ../backup-18-05-2026.sql.gz -- all of which were deleted for PDPA 2023
# compliance (C-02). The function logged a WARNING on every boot because the
# files were absent. It is now removed entirely.
#
# To restore data from a backup, use the operator procedure in DEPLOY.md §11:
#   sqlite3 ./data/dogar_trading.db < /path/to/your-backup.sql


def _on_startup():
    import logging
    log = logging.getLogger("dtc.startup")
    log.info("=== STARTUP DIAGNOSTICS ===")
    log.info("Working directory: %s", os.getcwd())

    # ------------------------------------------------------------------
    # Defense-in-depth: re-check production safety at process start.
    # assert_production_safe() already runs inside get_settings() at import
    # time (config.py L388), but an explicit call here makes it grep-
    # discoverable and guards against environments that hot-reload the
    # Settings object without re-importing the module (e.g. some ASGI
    # servers with --reload or test harnesses that patch os.environ).
    # ------------------------------------------------------------------
    try:
        settings.assert_production_safe()
    except ValueError as exc:
        if settings.is_production:
            log.critical("STARTUP: PRODUCTION SAFETY CHECK FAILED -- %s", exc)
            raise RuntimeError(
                "Production safety check failed at startup; refusing to serve "
                f"traffic with weak configuration. Reason: {exc}"
            ) from exc
        log.warning("STARTUP: Production safety check issue (non-production, continuing): %s", exc)

    from app.db.session import SessionLocal
    from app.models.candidate import Candidate

    # ------------------------------------------------------------------
    # 0) Idempotent compatibility migrations after deployment Alembic migrations.
    #    Adds any columns that newer model versions introduced so an
    #    existing SQLite DB never falls behind the ORM mappings (e.g.
    #    document_templates.page_size / orientation for the page-size
    #    engine). Safe to run on every boot -- each ALTER is a no-op if
    #    the column already exists.
    # ------------------------------------------------------------------
    try:
        from app.db.init_db import run_schema_migrations
        run_schema_migrations()
        log.info("STARTUP: schema migrations applied (idempotent).")
    except Exception as exc:                                  # noqa: BLE001
        # In production a failed migration means the running process may be
        # serving traffic against a partially-upgraded schema -- fail fast so
        # the operator (or orchestrator health check) sees the boot abort
        # instead of silently continuing. In development we keep the previous
        # best-effort behaviour for convenience.
        if settings.is_production:
            log.critical("STARTUP: schema migration FAILED in production -- aborting boot: %s", exc)
            raise RuntimeError(
                "Schema migration failed during production startup; refusing to "
                "serve traffic with a partially-upgraded schema."
            ) from exc
        log.warning("STARTUP: schema migration step failed (development, continuing): %s", exc)

    # ------------------------------------------------------------------
    # 0b) TENANT schema check.
    #
    # MEASURED 2026-08-19: run_schema_migrations() above upgrades the CONTROL
    # database only. Tenant DBs are separate files/databases and are migrated
    # by docker-entrypoint.sh step 2 -- which does NOT run when the app is
    # started any other way (uvicorn/PM2 directly, a dev box, a systemd unit).
    # The result measured on this very deployment: the control DB reached
    # a1c6e8b4d072 on boot while tenant_acme.db and tenant_beta.db stayed at
    # 9b5e7d3f2c81, and the tenant audit page died with
    #
    #     sqlite3.OperationalError: no such column: audit_logs.doc_category
    #
    # i.e. a 500 that looks like a portal fault, on a route that works
    # perfectly for the default office. We do NOT auto-migrate tenants here:
    # migrating N databases inside the boot path would make startup time and
    # failure modes scale with tenant count, and a half-finished tenant
    # upgrade during boot is worse than a clear warning. Instead we DETECT the
    # drift and say exactly what to run, so the condition is never silent.
    # ------------------------------------------------------------------
    try:
        from app.db.tenant_schema_check import warn_if_tenants_behind
        warn_if_tenants_behind()
    except Exception as exc:                                  # noqa: BLE001
        # This is an observability step. It must never be the reason a portal
        # fails to boot -- that would make a diagnostic more dangerous than
        # the problem it reports.
        log.warning("STARTUP: tenant schema check could not run: %s", exc)

    # ------------------------------------------------------------------
    # 1) Data bootstrap -- only if the DB is empty
    # ------------------------------------------------------------------
    db = SessionLocal()
    try:
        try:
            cand_count = db.query(Candidate).count()
        except Exception as exc:                              # noqa: BLE001 -- empty/new schema
            log.warning("Candidate count query failed (likely fresh DB): %s", exc)
            cand_count = 0

        if cand_count == 0:
            # Audit fix: _apply_bundled_sql_dump() removed -- backup files were
            # deleted for PDPA compliance (C-02). A fresh DB starts empty and
            # is populated by the idempotent seeders below (admin, roles, lookups,
            # document templates). Operators restore data via DEPLOY.md §11.
            log.info("STARTUP: Empty database detected -- seeders will create admin user, roles, and lookup data.")
        else:
            log.info("STARTUP: Existing database (%d candidates) -- kept as-is.", cand_count)
    finally:
        db.close()

    # ------------------------------------------------------------------
    # 2) Always re-seed admin + document templates (idempotent)
    # ------------------------------------------------------------------
    from app.db.init_db import (
        seed_admin_user,
        seed_document_templates,
        seed_roles,
        seed_super_admin,
    )
    db = SessionLocal()
    try:
        try:
            seed_admin_user(db)
            log.info("AUTO-SEEDER: admin user verified.")
        except Exception as exc:                              # noqa: BLE001
            db.rollback()
            log.exception("AUTO-SEEDER: admin seed failed: %s", exc)
            if settings.is_production:
                raise RuntimeError("Required admin seed failed") from exc

        # Permanent super-admin (developer account) -- idempotent.
        # Re-asserts is_super_admin=True / role=admin / is_active=True
        # on every boot so the account can NEVER be permanently locked
        # out or demoted via accidental edits in another tab.
        try:
            seed_super_admin(db)
            log.info("AUTO-SEEDER: super-admin (developer) verified.")
        except Exception as exc:                              # noqa: BLE001
            db.rollback()
            log.exception("AUTO-SEEDER: super-admin seed failed: %s", exc)
            if settings.is_production:
                raise RuntimeError("Required super-admin seed failed") from exc

        try:
            seed_document_templates(db)
            log.info("AUTO-SEEDER: document templates verified.")
        except Exception as exc:                              # noqa: BLE001
            db.rollback()
            log.exception("AUTO-SEEDER: template seed failed: %s", exc)
            if settings.is_production:
                raise RuntimeError("Required document template seed failed") from exc

        # RBAC system roles -- re-seed on every boot so newly added
        # permission keys flow through to existing system roles.
        try:
            seed_roles(db)
            log.info("AUTO-SEEDER: system roles verified.")
        except Exception as exc:                              # noqa: BLE001
            db.rollback()
            log.exception("AUTO-SEEDER: roles seed failed: %s", exc)
            if settings.is_production:
                raise RuntimeError("Required role seed failed") from exc
    finally:
        db.close()

    log.info("=== END STARTUP DIAGNOSTICS ===")


# ---------------------------------------------------------------------------
# CSRF PROTECTION MIDDLEWARE (Double-Submit Cookie pattern)
# ---------------------------------------------------------------------------
# On every GET page request (non-API) we set a `csrf_token` cookie (non-HttpOnly
# so JavaScript can read it). On every state-changing API request (POST/PUT/
# DELETE/PATCH) we verify that the `X-CSRF-Token` request header matches the
# cookie value. Requests that already carry an Authorization: Bearer header are
# still checked — Defence in Depth.
#
# Exemptions (login/auth/file-upload endpoints don't have the cookie yet):
#   POST /api/auth/login, /api/auth/register, /api/auth/forgot-password,
#   /api/auth/reset-password/*, /api/ocr/* (multipart form uploads)
#
# The React SPA reads the cookie and injects it as
# axios.defaults.headers.common['X-CSRF-Token'] on every page load.
_CSRF_EXEMPT_PATHS = frozenset({
    "/api/auth/login",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
})
_CSRF_EXEMPT_PREFIXES: tuple[str, ...] = ()
_CSRF_STATE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}
_CSRF_COOKIE_NAME = "csrf_token"
_CSRF_HEADER_NAME = "x-csrf-token"

class CsrfMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF protection for all state-changing API calls."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 1. On GET requests for HTML pages (not /api/, not /static/) set the
        #    CSRF cookie so the client JS can read and replay it.
        is_page = (
            request.method == "GET"
            and not path.startswith("/api/")
            and not path.startswith("/static/")
            and not path.startswith("/tenant-assets/")
        )
        if is_page and not request.cookies.get(_CSRF_COOKIE_NAME):
            token = secrets.token_urlsafe(32)
            response = await call_next(request)
            # AUDIT L-1 (2026-07-10): comment/behaviour now agree. SameSite=Lax
            # (not Strict) is intentional and correct for a *double-submit* CSRF
            # token cookie: the security guarantee comes from matching this
            # non-HttpOnly cookie against the X-CSRF-Token header (an attacker's
            # cross-site page cannot read the cookie to forge the header). Lax
            # additionally ensures the token cookie IS present after a top-level
            # navigation from an external link (Strict would withhold it on that
            # first cross-site GET, leaving the SPA without a token until reload).
            # NOT HttpOnly: the SPA's axios interceptor must read this value.
            response.set_cookie(
                _CSRF_COOKIE_NAME, token,
                max_age=86400, path="/",
                samesite="lax", httponly=False,
                secure=settings.is_production,
            )
            return response

        # 2. Check CSRF token on state-changing API requests
        if request.method in _CSRF_STATE_METHODS and path.startswith("/api/"):
            # Skip CSRF in automated test runs to avoid session-cookie contamination.
            if os.environ.get("TESTING") == "true":
                return await call_next(request)
            exempt = (
                path in _CSRF_EXEMPT_PATHS
                or any(path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES)
            )
            if not exempt:
                cookie_token = request.cookies.get(_CSRF_COOKIE_NAME, "")
                header_token = request.headers.get(_CSRF_HEADER_NAME, "")
                cookie_authenticated = bool(request.cookies.get("access_token"))
                # Browser sessions authenticated by the HttpOnly cookie MUST
                # present the matching double-submit token. Pure API clients
                # using Authorization: Bearer remain backward compatible.
                valid = bool(cookie_token and header_token) and secrets.compare_digest(
                    cookie_token, header_token
                )
                if cookie_authenticated and not valid:
                    from fastapi.responses import JSONResponse
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "CSRF token mismatch — please refresh the page and try again."},
                    )

        return await call_next(request)


# ---------------------------------------------------------------------------
# PERFORMANCE MIDDLEWARE
# ---------------------------------------------------------------------------
# Gzip compression for everything > 500 bytes – cuts payloads ~70% for JSON lists
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)


class CacheHeadersMiddleware(BaseHTTPMiddleware):
    """Long-lived caching for /static/, no-cache for HTML pages,
    short cache for read-only API endpoints (so the candidate/demand
    list snaps back instantly on second visit).

    H-07: Also injects security response headers on every response to protect
    against clickjacking, XSS script injection, HTTPS downgrade, and Referer
    leakage.
    """
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/spa/assets/"):
            # Vite emits content-hashed filenames (e.g. index-d0h5K9PV.js), so
            # these bundles are safe to cache for a full year — a new build ships
            # new filenames. This makes repeat navigations effectively instant.
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        elif path.startswith("/static/"):
            # Non-hashed static assets (logos, background images, fonts) can be
            # replaced IN PLACE under the same URL, so they must NOT be marked
            # `immutable` — that promises the bytes never change and lets a
            # browser serve a stale logo for the full max-age without ever
            # revalidating (L-audit 2026-07-24). `must-revalidate` lets the
            # cache reuse the file while fresh but forces a conditional
            # re-check (ETag / Last-Modified) once it expires.
            response.headers.setdefault("Cache-Control", "public, max-age=86400, must-revalidate")
        elif path.startswith("/api/") and request.method == "GET":
            # Authenticated API reads must ALWAYS be revalidated.
            #
            # This used to be `private, max-age=30`, justified by the comment
            # "we always invalidate on POST/PUT/DELETE". That is not something
            # a server can do: `max-age` is a promise to the BROWSER's own HTTP
            # cache, and a later PUT on a different URL cannot reach into it and
            # evict anything. So for 30 seconds after any successful save, a
            # reload was served the PRE-SAVE body without the request ever
            # leaving the machine.
            #
            # Found by driving the tenant typeface control in a real browser:
            # the select was changed to `mono`, the PUT went out with
            # {"font_key":"mono"}, the API returned 200 and the registry row
            # read 'mono' -- and reopening the drawer still showed 'serif'.
            # Every layer was correct and the operator was still shown a lie,
            # which is the worst shape a bug can take: it looks like the save
            # silently failed, so the natural response is to save again.
            #
            # `no-cache` does NOT mean "never store". It means "store, but
            # revalidate before reuse", so the cheap 304 path is preserved
            # wherever a validator exists while a stale body can no longer be
            # served. The snappy-list benefit the old header was reaching for is
            # kept by the SPA's own in-memory state, which the app CAN
            # invalidate on write -- unlike the browser's HTTP cache.
            if not any(seg in path for seg in ("/auth/", "/me", "/login", "/logout", "/chatbot/", "/forgot-password", "/reset-password", "/contact-support")):
                response.headers.setdefault("Cache-Control", "private, no-cache")
        else:
            response.headers.setdefault("Cache-Control", "no-cache")

        # ── Security headers (H-07) ─────────────────────────────────────────
        # X-Content-Type-Options: prevents MIME sniffing of scripts/styles
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # X-Frame-Options: SAMEORIGIN (not DENY) in PRODUCTION only.
        # The portal embeds its OWN pages in same-origin iframes for core UX:
        #   * the candidate Details / Edit flyout on demand_detail.html
        #     (frame.src = /candidates?open=<id>&flyout=1)
        #   * the hidden print/preview iframes for letterhead & receipt docs
        # X-Frame-Options: DENY blocked every one of these (browser shows
        # "<host> refused to connect"), breaking view/edit/print app-wide.
        # SAMEORIGIN still prevents clickjacking from any external site.
        #
        # PREVIEW FIX: X-Frame-Options is a legacy header that CANNOT allowlist a
        # cross-origin parent (only DENY / SAMEORIGIN). Hosted preview /
        # sandbox preview embeds the app in an iframe on a DIFFERENT origin, so
        # SAMEORIGIN makes the browser refuse the frame ("refused to connect").
        # In non-production we therefore OMIT X-Frame-Options entirely and rely on
        # the CSP `frame-ancestors` allowlist (set in CspNonceMiddleware) to
        # control framing. Production keeps the strict SAMEORIGIN header.
        if settings.is_production:
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        # Referrer-Policy: do not leak full URLs to third-party origins
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # HSTS: instruct browsers to always use HTTPS for this origin (1 year)
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
        # Content-Security-Policy is now handled by CspNonceMiddleware for HTML
        # responses with per-request nonce-based allowlisting. Non-HTML responses
        # (JSON API) do not need CSP headers.
        # Permissions-Policy: disable dangerous browser APIs not needed by this portal
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        # ────────────────────────────────────────────────────────────────────
        return response


app.add_middleware(CacheHeadersMiddleware)
app.add_middleware(CsrfMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(CspNonceMiddleware)
app.add_middleware(RequestIdMiddleware)

# Multi-tenant resolver -- attaches request.state.tenant for every request.
# Adding it AFTER CacheHeadersMiddleware so cache decisions can see the tenant.
from app.core.tenancy import (  # noqa: E402
    TenantResolverMiddleware,
    require_tenant_feature,
)

app.add_middleware(TenantResolverMiddleware)

# HIGH-RISK SECURITY FIX: reject spoofed Host headers before tenant resolution.
# This prevents an attacker from choosing an arbitrary tenant subdomain through
# Host-header injection and keeps URL generation / redirects bound to known hosts.
#
# 2026-07-04 custom-domain audit: also permit any custom_domain that has been
# bound to an active tenant, so custom-domain hosting works out-of-the-box
# without operators having to remember to update ALLOWED_HOSTS each time.
# This snapshot happens once at boot -- newly-added custom domains take effect
# on the next process restart (a reverse-proxy config update is required
# anyway for TLS, so the restart is not an extra operational cost).
def _collect_allowed_hosts() -> list[str]:
    hosts = list(settings.allowed_hosts)
    import logging as _log_mod
    _boot_log = _log_mod.getLogger("dtc.startup")
    try:
        import sqlalchemy as _sa

        from app.db.session import SessionLocal as _CtrlSess
        from app.models.tenant import Tenant as _T

        _db = _CtrlSess()
        try:
            # AUDIT 2026-07-29 (Low): on a brand-new install this function used to
            # run BEFORE `alembic upgrade head` had created anything, so the query
            # raised OperationalError("no such table: tenants") and every first
            # boot logged a scary "could not load tenant custom_domains" warning.
            # Nothing was actually broken -- a virgin database has no custom
            # domains to load -- but an alarming warning on a clean install
            # teaches operators to ignore warnings, which is how real ones get
            # missed. So an absent table is now treated as the ordinary
            # pre-bootstrap state and logged at DEBUG, while genuine failures
            # (permissions, corruption, connectivity) still warn loudly below.
            _table = _T.__table__.name
            if _table not in _sa.inspect(_db.get_bind()).get_table_names():
                _boot_log.debug(
                    "control DB has no %r table yet (pre-bootstrap); "
                    "skipping the custom-domain allowlist snapshot", _table,
                )
                return hosts

            if hasattr(_T, "custom_domain"):
                rows = (
                    _db.query(_T.custom_domain)
                    .filter(_T.custom_domain.isnot(None))
                    .filter(_T.status == "active")
                    .all()
                )
                for (d,) in rows:
                    if d and d not in hosts:
                        hosts.append(d)
        finally:
            _db.close()
    except Exception as exc:                                  # noqa: BLE001
        _boot_log.warning(
            "could not load tenant custom_domains at boot: %s", exc)
    return hosts

app.add_middleware(TrustedHostMiddleware, allowed_hosts=_collect_allowed_hosts())

# Prometheus metrics (scraped by /metrics endpoint, restricted to internal IPs)
init_metrics(app)

import logging

log = logging.getLogger("dtc.exception")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Intercepts unhandled exceptions to prevent stack trace exposure in production."""
    log.exception("Unhandled Server Exception: %s", exc)
    # Use the normalized settings.is_production (which applies .lower()) rather than
    # an exact-string compare, so ENV="Production" / "PRODUCTION" / with whitespace
    # cannot accidentally fall through to development-style exception behaviour.
    if settings.is_production:
        # Check if the request accepts HTML
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            # Audit fix: replaced cdn.tailwindcss.com with self-contained inline CSS.
            # An error page that depends on an external CDN is unreliable -- the CDN
            # could be unreachable during the same outage that caused the 500. It also
            # adds an unnecessary origin to the CSP script-src and generates an
            # outbound request at exactly the moment the app is struggling.
            # CSP nonce so the inline <style> passes strict style-src-elem.
            _err_nonce = getattr(getattr(request, "state", None), "csp_nonce", None) or ""
            _err_style_open = f'<style nonce="{_err_nonce}">' if _err_nonce else "<style>"
            html_content = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Server Error -- Dogar Trading Corporation</title>
  __STYLE_OPEN__
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:#f8fafc;display:flex;align-items:center;justify-content:center;
         min-height:100vh;padding:1rem;font-family:system-ui,-apple-system,sans-serif}
    .card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;
          box-shadow:0 4px 24px rgba(0,0,0,.08);max-width:420px;width:100%;
          padding:2.5rem;text-align:center}
    .icon{width:64px;height:64px;background:#fee2e2;border-radius:50%;
          display:flex;align-items:center;justify-content:center;margin:0 auto 1.25rem}
    .icon svg{width:32px;height:32px;color:#dc2626;stroke:#dc2626}
    h1{font-size:1.2rem;font-weight:700;color:#1e293b;margin-bottom:.5rem}
    p{font-size:.9rem;color:#64748b;margin-bottom:1.75rem;line-height:1.6}
    a{display:inline-block;padding:.5rem 1.25rem;background:#2563eb;color:#fff;
      border-radius:6px;font-weight:600;text-decoration:none;font-size:.9rem}
    a:hover{background:#1d4ed8}
    .ref{font-size:.75rem;color:#94a3b8;margin-top:1.5rem}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
      </svg>
    </div>
    <h1>500 &mdash; Internal Server Error</h1>
    <p>Something went wrong on our end. The error has been logged and will be investigated.</p>
    <a href="/dashboard">Return to Dashboard</a>
    <p class="ref">Dogar Trading Corporation Portal</p>
  </div>
</body>
</html>""".replace("__STYLE_OPEN__", _err_style_open)
            return HTMLResponse(content=html_content, status_code=500)
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})
    else:
        # In development, raise it so FastAPI shows the typical debug traceback
        raise exc

# Per-tenant uploads / logos / letterheads -- served as static assets.
# Each tenant's branding lives under data/tenants/<slug>/(logo|letterhead).png.
_TENANT_ASSETS_DIR = _PROJECT_ROOT / "data" / "tenants"
os.makedirs(_TENANT_ASSETS_DIR, exist_ok=True)

# Static files (1-day public cache via CacheHeadersMiddleware)
app.mount("/static", StaticFiles(directory=str(_APP_DIR / "static")), name="static")
app.mount(
    "/tenant-assets",
    StaticFiles(directory=str(_TENANT_ASSETS_DIR), check_dir=False),
    name="tenant_assets",
)

# ── React SPA ─────────────────────────────────────────────────────────────────
# The React SPA (app/static/spa) is the ONLY frontend. All page routes serve
# its index.html; React Router handles client-side routing. The Python/FastAPI
# backend provides API endpoints exclusively.
import os as _os

# Anchored on __file__ for the same reason as the mounts above, and this one is
SPA_INDEX_PATH = str(_APP_DIR / "static" / "spa" / "index.html")
# React SPA is the ONLY frontend.
_SPA_AVAILABLE = _os.path.exists(SPA_INDEX_PATH)


def _serve_spa_index(request: Request | None = None):
    """Return the React SPA index.html with per-request CSP nonce injected."""
    from fastapi.responses import HTMLResponse as _HTMLResponse
    try:
        with open(SPA_INDEX_PATH, encoding="utf-8") as fh:
            html = fh.read()
    except OSError:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "SPA build not found"}, status_code=500)

    nonce = getattr(getattr(request, "state", None), "csp_nonce", None) if request else None

    # --- Tenant path-mode bootstrap ----------------------------------------
    # When the portal copy is opened via /t/<slug>/..., the middleware strips
    # the prefix before routing, but the BROWSER URL still carries it. Inject a
    # tiny bootstrap so React Router uses <slug> as its basename and axios
    # prefixes API calls with it (so requests route back through the tenant
    # middleware). Also expose tenant branding for an instant, flash-free brand.
    tenant_base = getattr(getattr(request, "state", None), "tenant_base", "") if request else ""
    tenant = getattr(getattr(request, "state", None), "tenant", None) if request else None
    import json as _json
    boot = {
        "base": tenant_base or "",
        "tenant": None,
        # Single source of truth for the version string the SPA shows in the
        # sidebar footer. It used to be hard-coded as "v1.0.0" in Sidebar.tsx
        # and silently drifted from settings.APP_VERSION (which the API /health
        # means the version number can never disagree with the backend.
        "version": settings.APP_VERSION,
    }
    if tenant is not None:
        boot["tenant"] = {
            "slug": tenant.slug,
            "name": tenant.company_name,
            "shortName": tenant.short_name,
            "color": tenant.primary_color,
            # The typeface KEY, not a font-family declaration. The SPA resolves
            # it against the FONT_STACKS allow-list in lib/branding.ts.
            #
            # This key is why the whole font path existed but did nothing
            # (measured 2026-08-19): applyTenantBranding() read
            # `tenant.fontKey` and this payload never contained it, so
            # resolveFontStack() fell back to the system stack for every office
            # regardless of configuration. getattr with a default keeps a
            # tenant row from an un-migrated database from 500ing the shell.
            "fontKey": (getattr(tenant, "font_key", None) or "system"),
            "logo": (f"/tenant-assets/{tenant.slug}/{tenant.logo_filename}"
                     if tenant.logo_filename else "/static/img/logo-sm.png"),
            "officeName": tenant.office_name or "",
            "subtitle": tenant.subtitle or "",
            "licenseNumber": tenant.license_number or "",
            "officeAddress": tenant.office_address or "",
            "contactPhone": tenant.contact_phone or "",
            "contactEmail": tenant.contact_email or "",
        }
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    # JSON inside a script element must neutralize HTML parser terminators.
    # IMPORTANT: replacement strings must contain *literal* backslash-u escapes.
    # In Python, "\u003c" is decoded to "<" at parse time, which is a no-op and
    # lets tenant-controlled text like "</script><img ...>" terminate this inline
    # bootstrap script. Use "\\u003c" etc. so browsers parse the value as JSON text
    # while the HTML parser never sees a raw script terminator.
    boot_json = _json.dumps(boot, ensure_ascii=False)
    boot_json = boot_json.replace("&", "\\u0026")
    boot_json = boot_json.replace("<", "\\u003c").replace(">", "\\u003e")
    boot_json = boot_json.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    boot_script = f'<script{nonce_attr}>window.__TENANT_BOOT__={boot_json};</script>'
    # Insert the bootstrap right after <head> so it runs before the app bundle.
    if "<head>" in html:
        html = html.replace("<head>", "<head>" + boot_script, 1)
    else:
        html = boot_script + html

    if nonce:
        # Stamp the nonce onto inline <script> tags that lack a src= attribute.
        # External <script src=...> tags are already allowed via 'self'/CDN.
        html = html.replace("<script>", f'<script nonce="{nonce}">')
        # Vite emits the module loader as <script type="module" crossorigin src=...>
        # which is external (allowed) — but stamp any inline type="module" too.
        html = html.replace('<script type="module">', f'<script type="module" nonce="{nonce}">')
        # Also stamp inline <style> blocks (Vite injects e.g. Vue-style <style> in
        # legacy modes, or a build-inlined <style> for critical CSS) — strict
        # style-src-elem 'self' 'nonce-…' silently drops them without this.
        html = html.replace("<style>", f'<style nonce="{nonce}">')

    return _HTMLResponse(
        content=html,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )



# API routers
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(backups.router, prefix="/api/backups", tags=["Backups"])
app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(clients.router, prefix="/api/clients", tags=["Clients"])
app.include_router(demands.router, prefix="/api/demands", tags=["Demands"])
app.include_router(candidates.router, prefix="/api/candidates", tags=["Candidates"])
app.include_router(documents.router, prefix="/api/documents", tags=["Documents"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(lookups.router, prefix="/api/lookups", tags=["Lookups"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["Settings"])
# AUDIT 2026-08-15: per-tenant feature sets are enforced HERE, at router
# registration, rather than route by route. Two reasons:
#   1. A feature is a whole capability. Gating six of a router's seven routes
#      leaves the seventh as a live hole, and that omission is invisible in
#      review. A router-level dependency covers every route it owns, including
#      routes added later by someone who never read this comment.
#   2. It keeps the flag name next to the router it disables, so the mapping
#      from `Tenant.features` keys to actual surface area is readable in one
#      place instead of scattered across endpoint files.
# `require_tenant_feature` is a no-op on the root/Dogar portal (which resolves
# to no tenant), so Dogar's workflow is byte-for-byte unchanged.
app.include_router(ocr.router, prefix="/api/ocr", tags=["OCR"],
                   dependencies=[Depends(require_tenant_feature("ai_ocr"))])
app.include_router(ocr_validate.router, prefix="/api/v1/documents", tags=["OCR Validation"],
                   dependencies=[Depends(require_tenant_feature("ai_ocr"))])
app.include_router(chatbot.router, prefix="/api/chatbot", tags=["DtcBot"],
                   dependencies=[Depends(require_tenant_feature("ai_chatbot"))])
app.include_router(tenants_api.router, prefix="/api/tenants", tags=["Tenants"])
app.include_router(protector_letters.router, prefix="/api/protector-letters", tags=["ProtectorLetters"])
app.include_router(reminders.router, prefix="/api/reminders", tags=["Reminders"])
app.include_router(messaging.router, prefix="/api/messaging", tags=["Messaging"])
app.include_router(realtime.router, prefix="/api/realtime", tags=["Realtime"])
app.include_router(form_schemas.router, prefix="/api/admin", tags=["FormSchemas"])

# REMOVED 2026-07-30 (operator request): the /api/agents, /api/audit and
# /api/admin (data-migration) routers, plus the /agents, /agents/{id}/cashbook
# and /reports page routes. Those four sidebar features were reported as
# unusable and were deleted outright rather than hidden.
#
# Audit-trail *writing* is deliberately untouched: app/services/audit.py still
# records who changed what on every mutating request, and app/models/audit.py
# still defines the table. Only the read-only viewer page and its API are gone.
# Removing the writer would silently stop recording change history, which is a
# data-integrity regression nobody asked for -- the records remain queryable
# from the database and are included in every backup.
#
# The /api/admin/form-schemas endpoints also used to live in the deleted
# admin_migration module even though they are an unrelated feature (the
# "analyse form scans -> template" workflow). They were lifted into
# app/api/endpoints/form_schemas.py rather than dropped as collateral damage.
# See section 4.11 of PROJECT_DOCUMENTATION.md.


# ===== Page Routes (React SPA only) =====
# All page routes serve the React SPA shell. The SPA handles its own routing
# client-side via React Router. There are NO Jinja2 templates — the migration
# to React/TypeScript is complete.


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    """Root — always serves the React SPA shell."""
    return _serve_spa_index(request)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return _serve_spa_index(request)


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return _serve_spa_index(request)


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str = ""):
    return _serve_spa_index(request)


@app.get("/contact-support", response_class=HTMLResponse)
def contact_support_page(request: Request):
    return _serve_spa_index(request)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    return _serve_spa_index(request)


@app.get("/clients", response_class=HTMLResponse)
def clients_page(request: Request):
    return _serve_spa_index(request)


@app.get("/clients/{client_id}", response_class=HTMLResponse)
def client_detail_page(client_id: int, request: Request):
    return _serve_spa_index(request)


@app.get("/demands", response_class=HTMLResponse)
def demands_page(request: Request):
    return _serve_spa_index(request)


@app.get("/demands/{demand_id}", response_class=HTMLResponse)
def demand_detail_page(demand_id: int, request: Request):
    return _serve_spa_index(request)


@app.get("/candidates", response_class=HTMLResponse)
def candidates_page(request: Request):
    return _serve_spa_index(request)


@app.get("/documents", response_class=HTMLResponse)
def documents_page(request: Request):
    return _serve_spa_index(request)


@app.get("/documents/builder/{tpl_id}", response_class=HTMLResponse)
def document_builder_page(tpl_id: int, request: Request):
    """Legacy builder URL → redirect to new customize route."""
    return RedirectResponse(url=f"/documents/customize/{tpl_id}", status_code=301)


@app.get("/documents/customize/{tpl_id}", response_class=HTMLResponse)
def document_customize_page(tpl_id: int, request: Request):
    """Template Designer / Document Customizer — React page."""
    return _serve_spa_index(request)


@app.get("/protector-letter", response_class=HTMLResponse)
def protector_letter_page(request: Request):
    return _serve_spa_index(request)


@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    return _serve_spa_index(request)


@app.get("/portal-admin", response_class=HTMLResponse)
def portal_admin_page(request: Request):
    return _serve_spa_index(request)


@app.get("/visa-categories", response_class=HTMLResponse)
def visa_categories_page(request: Request):
    return _serve_spa_index(request)


@app.get("/embassies", response_class=HTMLResponse)
def embassies_page(request: Request):
    return _serve_spa_index(request)


@app.get("/cities", response_class=HTMLResponse)
def cities_page(request: Request):
    return _serve_spa_index(request)


@app.get("/medical-centers", response_class=HTMLResponse)
def medical_centers_page(request: Request):
    return _serve_spa_index(request)


@app.get("/contacts", response_class=HTMLResponse)
def contacts_page(request: Request):
    return _serve_spa_index(request)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return _serve_spa_index(request)


@app.get("/settings/general", response_class=HTMLResponse)
def settings_general_page(request: Request):
    return _serve_spa_index(request)


@app.get("/depositors", response_class=HTMLResponse)
def depositors_page(request: Request):
    return _serve_spa_index(request)


@app.get("/service-charges", response_class=HTMLResponse)
def service_charges_page(request: Request):
    return _serve_spa_index(request)


@app.get("/login-history", response_class=HTMLResponse)
def login_history_page(request: Request):
    return _serve_spa_index(request)


@app.get("/roles", response_class=HTMLResponse)
def roles_page(request: Request):
    return _serve_spa_index(request)


@app.get("/change-password", response_class=HTMLResponse)
def change_password_page(request: Request):
    return _serve_spa_index(request)


# Track process start time for uptime calculation
from datetime import UTC

_PROCESS_START_TIME = datetime.now(UTC)


@app.get("/api/health")
def health():
    """Comprehensive health endpoint: status, version, DB connectivity, uptime.

    Returns 200 when healthy, 503 when the database is unreachable.
    """
    import sqlalchemy as _sa

    from app.db.session import engine
    db_status = "ok"
    try:
        with engine.connect() as conn:
            conn.execute(_sa.text("SELECT 1"))
    except Exception:
        import logging
        logging.getLogger("dtc.health").exception("Database health check failed")
        db_status = "degraded"

    uptime_seconds = int((datetime.now(UTC) - _PROCESS_START_TIME).total_seconds())

    # Audit LOW (2026-07-26): self-service password reset depends on SMTP and
    # returns 503 without it. Report that as a *capability* flag so an operator
    # or uptime monitor can see it, without letting an optional feature mark the
    # whole service unhealthy (the DB is the only thing that does that).
    password_reset = (
        "available" if settings.email_configured
        else "unavailable_smtp_not_configured"
    )

    from fastapi.responses import JSONResponse
    status_code = 200 if db_status == "ok" else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if db_status == "ok" else "degraded",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "db": db_status,
            "password_reset": password_reset,
            "uptime_seconds": uptime_seconds,
        }
    )


@app.get("/api/ping")
def ping():
    """Lightweight health-check used by the Dockerfile HEALTHCHECK command.
    FIX-04: Added to match Dockerfile: CMD curl ... http://localhost:3000/api/ping
    """
    return {"ok": True}


# ── SPA catch-all (MUST be the last route registered) ─────────────────────────
# Any GET request that did not match an /api/* route, a static mount, or an
# explicit page route above falls through here. The React SPA index.html is
# returned so client-side routing (React Router) resolves the path. This keeps
# deep links (e.g. /demands/123) working on reload.
@app.get("/{spa_path:path}", response_class=HTMLResponse)
def spa_catch_all(spa_path: str, request: Request):
    # Never hijack API or asset paths — let them 404 normally.
    if spa_path.startswith(("api/", "static/", "tenant-assets/")):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return _serve_spa_index(request)
