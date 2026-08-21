"""Multi-tenant subdomain resolver + per-tenant DB session swap.

Strategy
--------
The control DB (``data/dogar_trading.db``) holds the ``tenants`` table.
Each *operational* portal -- clients, demands, candidates, documents,
agents, users -- lives in its OWN SQLite file under
``data/tenants/tenant_<slug>.db`` (already provisioned by
``app.services.tenant_service``).

When a request comes in we:

1.  Parse the ``Host`` header and extract the leftmost label
    (``demo.oep.com.pk`` → ``demo``, ``acme.oep.com.pk`` → ``acme``).
    The ``DEFAULT_TENANT_SLUG`` setting (or the value ``dogar``) is
    treated as the *root* portal and we keep using the main DB so
    legacy single-tenant behaviour is unchanged.

2.  Look the slug up in the control DB. If a matching active tenant
    is found we store a small ``ResolvedTenant`` snapshot on
    ``request.state.tenant`` (id, slug, company_name, db_path,
    primary_color, logo_filename, letterhead_path, office_name).

3.  ``get_db()`` (in ``app.db.session``) is unchanged -- it stays bound
    to the control DB. For tenant-scoped queries the endpoints
    inject the new ``get_tenant_db()`` dependency which:
        • returns the control DB if no tenant was resolved
          (single-tenant / "root" deployment), OR
        • opens a connection to the tenant's SQLite file and yields a
          session bound to a cached per-tenant engine.

This means existing endpoints can OPT IN to tenant scoping by
swapping ``Session = Depends(get_db)`` → ``Session = Depends(get_tenant_db)``
in a single line, without rewriting any query logic.

The middleware is a no-op when ``Host`` cannot be parsed (e.g. direct
IP access, ``localhost``, sandbox preview URLs) so local dev keeps
working exactly as before.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Mapping, Optional

from fastapi import HTTPException, Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import anchor_project_path, settings
from app.db.session import SessionLocal as ControlSessionLocal

log = logging.getLogger("dtc.tenancy")


def tenant_db_url(slug: str, sqlite_path: str) -> str:
    """Resolve the SQLAlchemy URL for a tenant.

    Two deployment modes:

    * SQLite (default, dev): one file per tenant at
      ``data/tenants/tenant_<slug>.db``. The ``sqlite_path`` argument
      (typically the ``tenants.db_path`` column value) is returned as
      ``sqlite:///<path>``.
    * Postgres (production): one database per tenant. When
      ``settings.TENANT_DB_URL_TEMPLATE`` is set it MUST contain the
      ``{slug}`` placeholder, which is replaced with the tenant slug to
      form the final URL. In that mode the ``sqlite_path`` argument is
      ignored -- the file-per-tenant path becomes an artifact of the
      previous storage layer that we no longer touch.

    This is the ONLY place URL construction happens; every caller
    (tenancy engine cache, tenant_service provisioning, migration
    script) routes through here so the SQLite → Postgres switch is a
    single-setting change.
    """
    template = (settings.TENANT_DB_URL_TEMPLATE or "").strip()
    if template:
        if "{slug}" not in template:
            raise ValueError(
                "TENANT_DB_URL_TEMPLATE must contain the '{slug}' placeholder"
            )
        return template.format(slug=slug)
    # SQLite fallback -- historical behaviour.
    #
    # AUDIT 2026-08-01 (Medium, same class as the control-DB finding): the
    # `tenants.db_path` column stores CWD-relative values ("data/tenants/
    # tenant_acme.db"), which SQLAlchemy resolves against the process working
    # directory. Launched from anywhere but the project root, every tenant file
    # appears to be missing -- and a missing tenant DB is deliberately a hard 503
    # (see TenantDatabaseMissing below, which must NOT fall back to the control
    # DB), so the entire multi-tenant surface goes down rather than degrading.
    # Anchoring here because this function documents itself as the ONLY place
    # tenant URL construction happens, so every caller inherits the fix.
    # Absolute stored paths are untouched.
    return f"sqlite:///{anchor_project_path(sqlite_path)}" if sqlite_path else f"sqlite:///{sqlite_path}"


def is_postgres_tenants() -> bool:
    """True when tenants live in Postgres, False for the SQLite default."""
    return bool((settings.TENANT_DB_URL_TEMPLATE or "").strip())


class TenantDatabaseMissing(Exception):
    """Raised when a tenant resolves from the control DB but its own SQLite
    file is missing on disk. We deliberately DO NOT fall back to the control
    DB in that case (that would leak control/root data under a tenant host),
    so the middleware turns this into a hard 503 for the request."""

    def __init__(self, slug: str, db_path: str):
        self.slug = slug
        self.db_path = db_path
        super().__init__(f"tenant {slug!r} DB missing at {db_path!r}")


# ---------------------------------------------------------------------------
# Reserved subdomains that always map to the root / control portal.
# ---------------------------------------------------------------------------
ROOT_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0",
    "dogar", "www", "app", "portal", "admin",
}

# Top-level domain parts we ignore when looking for the tenant label --
# anything with two or fewer labels (``oep.com``, ``dogar.local``) is
# treated as a root host.
MIN_LABELS_FOR_SUBDOMAIN = 3


@dataclass(frozen=True)
class ResolvedTenant:
    """Snapshot of a tenant attached to ``request.state.tenant``.

    A frozen dataclass keeps the value immutable for the lifetime of
    the request and makes it cheap to pass into render helpers.
    """
    id: int
    slug: str
    company_name: str
    short_name: str
    primary_color: str
    logo_filename: Optional[str]
    db_path: str
    office_name: Optional[str] = None
    letterhead_path: Optional[str] = None
    receipt_template: Optional[str] = None
    demand_format: Optional[str] = None
    license_number: Optional[str] = None
    office_address: Optional[str] = None
    subtitle: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    # The office's typeface KEY (not a font-family declaration) -- resolved via
    # app.core.branding_contract.
    #
    # 2026-08-19: carried on the snapshot for the same reason `features` had to
    # be (see the note below). The document/letterhead renderer receives ONLY
    # this snapshot -- never the ORM row -- so a branding value that is not on
    # it is a value no printed document can ever read. The tenants table would
    # hold the office's chosen font and every letter would still print in
    # Dogar's, with nothing failing anywhere.
    font_key: Optional[str] = None
    # AUDIT 2026-08-15: `features` was stored on the tenants row and editable
    # in Portal Admin, but it was NEVER carried onto this snapshot -- so no
    # request could read it, and nothing in the application could enforce it.
    # A tenant provisioned with ai_ocr=false still had full OCR access. The
    # toggle changed a database column and nothing else, which is the same
    # class of defect as a permission checkbox that grants nothing.
    # `field(default_factory=...)` because a mutable default on a dataclass is
    # a shared-state bug; the dataclass is frozen, so the dict is only ever
    # replaced, never mutated in place.
    features: Mapping[str, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Per-tenant SQLAlchemy engine cache (one engine per tenant, lazily created)
# ---------------------------------------------------------------------------
_ENGINES: dict[str, sessionmaker] = {}
_ENGINES_LOCK = threading.Lock()


def _make_tenant_sessionmaker(db_path: str, slug: str | None = None):
    """Build (or reuse) a sessionmaker bound to the given tenant DB.

    Works transparently for both storage backends:

    * SQLite: opens ``sqlite:///<db_path>`` and applies the WAL /
      synchronous=NORMAL / foreign-keys PRAGMAs on every connect so
      tenant DBs perform identically to the control DB.
    * Postgres: derives ``postgresql://…/tenant_<slug>`` from
      ``settings.TENANT_DB_URL_TEMPLATE`` (see ``tenant_db_url``) and
      uses a real connection pool. ``slug`` MUST be provided; a missing
      slug fails closed rather than crossing storage boundaries.

    The engine is cached by ``db_path`` so subsequent requests for the
    same tenant reuse it. When the deployment is Postgres and ``slug``
    is provided, we cache by slug instead so a stale historical
    ``db_path`` value doesn't cause two engines for the same tenant.
    """
    postgres = is_postgres_tenants()
    cache_key = f"pg:{slug}" if postgres and slug else db_path

    with _ENGINES_LOCK:
        if cache_key in _ENGINES:
            return _ENGINES[cache_key]

        if postgres:
            if not slug:
                raise ValueError(
                    "Tenant slug is required when PostgreSQL tenant routing is enabled"
                )
            url = tenant_db_url(slug, db_path)
            engine = create_engine(
                url,
                connect_args={"connect_timeout": 10},
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=10,
                pool_recycle=1800,
            )
        else:
            # AUDIT 2026-08-15 (High): this branch used to build the URL
            # itself as f"sqlite:///{db_path}", silently BYPASSING
            # tenant_db_url() -- the function that documents itself as "the
            # ONLY place URL construction happens" and that carries the
            # project-root anchoring fix for CWD-relative db_path values.
            #
            # The result was that the anchoring fix was real but unreachable
            # for the engine: launched from any CWD other than the project
            # root (systemd WorkingDirectory=/, a Docker entrypoint that cds,
            # a cron job), tenant_db_url() returned the correct absolute path
            # while the engine opened a *relative* one. SQLite does not error
            # on a missing file -- it CREATES it. So instead of failing, each
            # tenant silently got a brand-new EMPTY database at
            # <cwd>/data/tenants/tenant_<slug>.db, and the operator saw a
            # working login screen backed by no data.
            #
            # Routing through tenant_db_url() makes the single-source-of-truth
            # claim true for both the Postgres and SQLite branches.
            url = tenant_db_url(slug or "", db_path)
            engine = create_engine(
                url,
                connect_args={"check_same_thread": False, "timeout": 30},
                pool_pre_ping=True,
            )

        if url.startswith("sqlite"):
            @event.listens_for(engine, "connect")
            def _pragmas(dbapi_conn, _):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL;")
                cur.execute("PRAGMA synchronous=NORMAL;")
                cur.execute("PRAGMA foreign_keys=ON;")
                cur.execute("PRAGMA busy_timeout=30000;")
                cur.close()

        # NOTE: the historical create_all(...) idempotent-schema call has been
        # REMOVED here (audit 2026-07-08 PG-migration). Schema is now the sole
        # responsibility of Alembic (`alembic upgrade head`). Silently running
        # create_all on every process boot masks migration drift and was one of
        # the sources of the `_safe_add_column` band-aid we retired (and, on
        # 2026-07-31, deleted outright -- it had no callers left). If a
        # tenant DB is missing its schema, provisioning / migration must have
        # failed -- fail loudly at first query instead of silently patching it.
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        _ENGINES[cache_key] = SessionLocal
        return SessionLocal


def reset_tenant_engine(db_path: str, slug: str | None = None) -> None:
    """Drop the cached engine for a tenant -- used after archive/restore."""
    keys = [db_path]
    if slug:
        keys.append(f"pg:{slug}")
    with _ENGINES_LOCK:
        for k in keys:
            SessionLocal = _ENGINES.pop(k, None)
            if SessionLocal is not None:
                try:
                    SessionLocal.kw["bind"].dispose()
                except Exception:                             # noqa: BLE001
                    pass


# ---------------------------------------------------------------------------
# Host parsing
# ---------------------------------------------------------------------------
def extract_subdomain(host: str) -> Optional[str]:
    """Extract the tenant subdomain from a ``Host`` header.

    Returns ``None`` when the host has no usable subdomain
    (``localhost``, ``oep.com.pk``, IP literals, sandbox URLs).
    """
    if not host:
        return None
    host = host.split(":", 1)[0].lower().strip()
    if not host or host[0].isdigit():
        return None
    labels = host.split(".")
    if len(labels) < MIN_LABELS_FOR_SUBDOMAIN:
        return None
    first = labels[0]
    if first in ROOT_HOSTS:
        return None
    # Reject ``www`` style prefixes even when nested deeper
    if first in {"www", "app"}:
        return None
    return first


def _row_to_resolved(row) -> ResolvedTenant:
    """Map a Tenant ORM row to the immutable ResolvedTenant snapshot.

    Centralised so both the host-based and slug/path-based resolvers build the
    snapshot identically (and both apply the same tenant-DB-missing hard-fail).
    """
    slug = row.slug
    # AUDIT 2026-08-15 (High): os.path.exists() on the RAW db_path resolves
    # against the process CWD, while the engine (via tenant_db_url) resolves
    # against the project root. Launched from a different CWD the two
    # disagreed, so this guard reported "missing" for a tenant DB that exists
    # -- turning every tenant request into a hard 503 -- or, worse, reported
    # "present" for a stray file while the engine opened a different one.
    # Anchor both through the same helper so the check tests the file the
    # engine will actually open.
    if not is_postgres_tenants() and not os.path.exists(anchor_project_path(row.db_path)):
        log.error(
            "tenant %r resolved but db_path %r (resolved to %r) is missing -- "
            "refusing to fall back to control DB",
            slug, row.db_path, anchor_project_path(row.db_path),
        )
        raise TenantDatabaseMissing(slug=slug, db_path=row.db_path)
    return ResolvedTenant(
        id=row.id,
        slug=row.slug,
        company_name=row.company_name or row.short_name or slug,
        short_name=row.short_name or slug,
        primary_color=row.primary_color or "#2563eb",
        logo_filename=row.logo_filename,
        db_path=row.db_path,
        office_name=getattr(row, "office_name", None),
        letterhead_path=getattr(row, "letterhead_path", None),
        receipt_template=getattr(row, "receipt_template", None),
        demand_format=getattr(row, "demand_format", None),
        license_number=getattr(row, "license_number", None),
        office_address=getattr(row, "office_address", None),
        subtitle=getattr(row, "subtitle", None),
        contact_phone=getattr(row, "contact_phone", None),
        contact_email=getattr(row, "contact_email", None),
        # getattr default: a control DB that has not yet had b2d7f9a3e614
        # applied has no such column, and tenant RESOLUTION is on the critical
        # path for every single request -- an AttributeError here would take the
        # whole portal down over a cosmetic field.
        font_key=getattr(row, "font_key", None),
        features=dict(getattr(row, "features", None) or {}),
    )


def resolve_tenant_by_slug(slug: str) -> Optional[ResolvedTenant]:
    """Resolve an active tenant directly by its slug.

    This powers the **path-based tenant access mode** (``/t/<slug>/...``) which
    lets a tenant copy portal be opened and used WITHOUT owning a real domain
    or configuring DNS -- essential for sandbox/preview and for the "Open copy
    portal" button in Portal Admin. Production custom-domain / subdomain access
    keeps working unchanged.
    """
    if not slug:
        return None
    slug = slug.strip().lower()
    db = ControlSessionLocal()
    try:
        from app.models.tenant import Tenant  # local import -- model registered

        row = (
            db.query(Tenant)
            .filter(Tenant.slug == slug)
            .filter(Tenant.status == "active")
            .first()
        )
        if not row:
            return None
        return _row_to_resolved(row)
    except TenantDatabaseMissing:
        raise
    except Exception as exc:                                  # noqa: BLE001
        log.exception("tenant slug resolver failed: %s", exc)
        return None
    finally:
        db.close()


def resolve_tenant_from_host(host: str) -> Optional[ResolvedTenant]:
    """Look up the active tenant matching the request host.

    Resolution order (2026-07-04 custom-domain audit):
      1. Exact-match ``custom_domain`` (case-insensitive, port stripped).
         Enables tenants to bind their own domain (e.g. portal.mytravel.pk).
      2. Fallback: ``<slug>.<root-domain>`` subdomain.

    Returns ``None`` for the root deployment or when no matching
    active tenant exists. Failures are logged and swallowed --
    tenancy errors must never break the request.
    """
    if not host:
        return None
    bare_host = host.split(":", 1)[0].lower().strip()

    db = ControlSessionLocal()
    try:
        from app.models.tenant import Tenant  # local import -- model registered

        # 1. Custom-domain lookup (only if the column exists on the DB, which
        #    it does after run_schema_migrations() -- but be defensive so a
        #    partially-migrated DB doesn't 500 every request).
        row = None
        if bare_host and hasattr(Tenant, "custom_domain"):
            try:
                row = (
                    db.query(Tenant)
                    .filter(Tenant.custom_domain == bare_host)
                    .filter(Tenant.status == "active")
                    .first()
                )
            except Exception as exc:                          # noqa: BLE001
                log.warning("custom_domain lookup failed: %s", exc)
                row = None

        # 2. Subdomain fallback
        if row is None:
            slug = extract_subdomain(host)
            if not slug:
                return None
            row = (
                db.query(Tenant)
                .filter(Tenant.slug == slug)
                .filter(Tenant.status == "active")
                .first()
            )
        if not row:
            return None
        # Delegate snapshot construction (and the tenant-DB-missing hard-fail)
        # to the shared helper so host-based and slug-based resolution stay in
        # lockstep (same fields, same isolation guard).
        return _row_to_resolved(row)
    except TenantDatabaseMissing:
        # Deliberate hard-fail -- MUST propagate so the middleware can turn it
        # into a 503. Never let the broad handler below swallow it into a
        # control-DB fallback (that would be the tenant-isolation breach we are
        # guarding against).
        raise
    except Exception as exc:                                  # noqa: BLE001
        log.exception("tenant resolver failed: %s", exc)
        return None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Starlette middleware
# ---------------------------------------------------------------------------
class TenantResolverMiddleware(BaseHTTPMiddleware):
    """Attach a ``ResolvedTenant`` (or ``None``) to ``request.state``.

    Resolution order:
      1. **Path prefix ``/t/<slug>/...``** -- the portable, DNS-free access mode
         used by sandbox/preview and the "Open copy portal" button. When matched
         we STRIP the ``/t/<slug>`` prefix from the request path so every
         downstream route ("/dashboard", "/api/...") works transparently, and
         we record the base path on ``request.state.tenant_base`` so responses
         can build tenant-scoped links / cookies.
      2. **Host header** (custom_domain or ``<slug>.<root>`` subdomain) -- the
         production access mode.
    """

    async def dispatch(self, request, call_next):
        host = request.headers.get("host", "")
        tenant_base = ""
        try:
            # --- 1. Path-based tenant mode: /t/<slug>/... -------------------
            raw_path = request.scope.get("path", "") or request.url.path
            slug = None
            if raw_path == "/t" or raw_path.startswith("/t/"):
                remainder = raw_path[len("/t"):]            # "" or "/<slug>/..."
                parts = remainder.lstrip("/").split("/", 1)
                slug = parts[0] if parts and parts[0] else None
                if slug:
                    resolved = resolve_tenant_by_slug(slug)
                    if resolved is not None:
                        request.state.tenant = resolved
                        tenant_base = f"/t/{slug}"
                        # Rewrite the path so the rest of the app is unaware of
                        # the /t/<slug> prefix (routes, static, API all match).
                        rest = parts[1] if len(parts) > 1 else ""
                        new_path = "/" + rest
                        request.scope["path"] = new_path
                        request.scope["raw_path"] = new_path.encode("utf-8")
                    else:
                        # Unknown/suspended tenant slug -> attach nothing; the
                        # rewritten path would 404 anyway. Serve a clear 404.
                        from starlette.responses import JSONResponse
                        request.state.tenant = None
                        request.state.tenant_base = ""
                        request.state.tenant_host = host
                        return JSONResponse(
                            status_code=404,
                            content={"detail": f"Portal copy '{slug}' not found or not active."},
                        )
            # --- 2. Host-based tenant mode (production) ---------------------
            if not tenant_base:
                request.state.tenant = resolve_tenant_from_host(host)
            else:
                pass  # already set above
        except TenantDatabaseMissing as exc:
            # Hard-fail: a tenant was matched but its DB file is gone. Refuse
            # the request rather than silently serving control-DB data.
            from starlette.responses import JSONResponse
            return JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "This portal is temporarily unavailable "
                        "(tenant storage not found). Please contact support."
                    ),
                    "tenant": exc.slug,
                },
            )
        request.state.tenant_base = tenant_base
        request.state.tenant_host = host
        # Publish the resolved tenant through a ContextVar as well as on
        # request.state (2026-08-19).
        #
        # WHY BOTH. request.state is the right home for anything that has a
        # `request` in scope. The DOCUMENT renderer does not: every letter is
        # wrapped by letterhead_renderer._wrap_document(), which has thirteen
        # call sites and no request parameter, so per-office document branding
        # was unreachable from there -- twelve of those thirteen document types
        # could not know which office they were printing for. Threading the
        # tenant through all thirteen signatures is thirteen chances to miss
        # one, and a missed one prints a partner office's letter in Dogar's
        # branding with no error to notice.
        #
        # This mirrors exactly what CspNonceMiddleware already does for the same
        # renderers and the same reason (see app/core/csp_nonce.py). Set on the
        # PARENT context here -- before call_next -- because Starlette's
        # BaseHTTPMiddleware runs the downstream app in a child context: a value
        # set here is visible to the child, whereas a value set inside the child
        # would not be visible out here. Direction matters and it is the safe
        # direction.
        #
        # Not reset afterwards: each request runs in its own context copy, so
        # there is nothing to leak into the next one, and resetting would
        # require holding a token across the await for no benefit.
        try:
            from app.core.branding_contract import current_tenant
            current_tenant.set(getattr(request.state, "tenant", None))
        except Exception:  # noqa: BLE001
            # A branding import failure must never take down request routing.
            log.debug("could not publish tenant to the branding contextvar", exc_info=True)
        return await call_next(request)


# ---------------------------------------------------------------------------
# FastAPI dependency -- yields the right SQLAlchemy session for the request
# ---------------------------------------------------------------------------
def get_tenant_db(request: Request) -> Session:                 # pragma: no cover -- exercised at request time
    """Return a SQLAlchemy session bound to the right DB for this request.

    • When ``request.state.tenant`` is ``None`` we yield the control DB
      session -- i.e. legacy single-tenant behaviour is preserved.
    • When a tenant is attached we open a session against that
      tenant's isolated SQLite file (engine cached per-process).

    Endpoints that want tenant scoping just declare:
        ``db: Session = Depends(get_tenant_db)``
    """
    tenant: Optional[ResolvedTenant] = getattr(request.state, "tenant", None)
    if tenant is None:
        db = ControlSessionLocal()
    else:
        SessionLocal = _make_tenant_sessionmaker(tenant.db_path, tenant.slug)
        db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_tenant(request: Request) -> Optional[ResolvedTenant]:
    """Convenience FastAPI dependency for getting the resolved tenant."""
    return getattr(request.state, "tenant", None)


def current_tenant_base(request: Request) -> str:
    """Return the URL prefix for the current tenant path-mode ("/t/<slug>").

    Empty string for host-based access or the root portal. Used by the SPA
    bootstrap + link builders so all in-app navigation stays inside the
    tenant copy when opened via ``/t/<slug>/...``.
    """
    return getattr(request.state, "tenant_base", "") or ""


# ---------------------------------------------------------------------------
# Per-tenant feature sets
# ---------------------------------------------------------------------------
# AUDIT 2026-08-15. The operator's requirement is that each recruitment office
# gets "its own name, logo, branding, FEATURE SET, and strictly separate
# database/data boundary". Name, logo, branding and the data boundary were all
# real. The feature set was not: `features` was a column on the tenants row,
# editable in Portal Admin, carried nowhere and read by nothing. Provisioning
# an office with ai_ocr=false wrote `false` to the database and left OCR fully
# usable -- a toggle that lies to the operator, the same defect class as an
# RBAC checkbox that grants nothing.
#
# Enforcement is deliberately SERVER-side. Hiding a nav item in the SPA is a
# cosmetic change: the API route stays open and is reachable with curl, so a
# disabled feature would still be a live attack surface.
#
# The root/Dogar portal resolves to no tenant, so `tenant_feature_enabled`
# returns True for everything there. That is what keeps this change from
# altering Dogar's current workflow -- the constraint the operator restated
# twice ("without replacing its current workflow or branding").
def tenant_feature_enabled(request: Request, feature: str) -> bool:
    """Is ``feature`` enabled for the tenant serving this request?

    Unknown features and the root portal both answer True: a flag nobody has
    heard of must not silently disable a working page, and Dogar is not a
    tenant.
    """
    rt = getattr(request, "state", None)
    rt = getattr(rt, "tenant", None) if rt is not None else None
    if rt is None:
        return True
    flags = getattr(rt, "features", None) or {}
    if feature not in flags:
        return True
    return bool(flags[feature])


def require_tenant_feature(feature: str):
    """Dependency factory: 404 when ``feature`` is disabled for this tenant.

    404 rather than 403 is deliberate. 403 says "this exists and you may not
    use it", which tells one office about a capability another office bought.
    A disabled feature should be indistinguishable from a feature that was
    never installed.
    """
    def _dep(request: Request):
        if not tenant_feature_enabled(request, feature):
            raise HTTPException(
                status_code=404,
                detail=f"The '{feature}' feature is not enabled for this portal.",
            )
        return True

    return _dep
