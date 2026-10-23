"""Multi-tenant Copy Portal endpoints (admin-only).

POST   /api/tenants                       -- provision new tenant (creates isolated DB)
GET    /api/tenants                       -- list all tenants
GET    /api/tenants/{id}                  -- tenant detail
PUT    /api/tenants/{id}                  -- update branding / contact
PUT    /api/tenants/{id}/features         -- update feature flags
POST   /api/tenants/{id}/logo             -- upload tenant logo (image)
POST   /api/tenants/{id}/letterhead       -- upload tenant letterhead (image)
PUT    /api/tenants/{id}/branding         -- set office_name, demand_format, receipt_template
DELETE /api/tenants/{id}                  -- soft-archive tenant (moves DB to archive/)
GET    /api/tenants/_meta/current         -- info about the tenant resolved for this request
"""
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.orm import Session

from app.core.branding_contract import _DEFAULT_FONT_KEY, ALLOWED_FONT_KEYS
from app.core.deps import get_current_user
from app.core.permissions import user_permissions
from app.core.uploads import InvalidImage, normalize_image
from app.db.session import get_db
from app.models import Tenant, User
from app.services.tenant_service import (
    archive_tenant_db,
    provision_tenant_db,
    slugify,
)

router = APIRouter()


# -------- Schemas --------
class TenantCreate(BaseModel):
    company_name: str = Field(..., min_length=2, max_length=200)
    short_name: Optional[str] = None
    subtitle: Optional[str] = "Overseas Employment Promoters"
    admin_name: str = Field(..., min_length=2, max_length=150)
    admin_email: EmailStr
    admin_password: str = Field(..., min_length=12, max_length=100)
    contact_phone: Optional[str] = None
    plan: Optional[str] = "standard"
    primary_color: Optional[str] = "#2563eb"
    features: Optional[dict] = None
    notes: Optional[str] = None


class TenantUpdate(BaseModel):
    company_name: Optional[str] = None
    short_name: Optional[str] = None
    subtitle: Optional[str] = None
    primary_color: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    plan: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class FeaturesUpdate(BaseModel):
    features: dict


class BrandingUpdate(BaseModel):
    """Per-tenant enterprise branding payload -- separate from
    TenantUpdate so the UI can save 'office identity' independently
    from the partner-contact fields."""
    office_name: Optional[str] = None
    license_number: Optional[str] = None
    office_address: Optional[str] = None
    demand_format: Optional[str] = None
    receipt_template: Optional[str] = None
    subtitle: Optional[str] = None
    primary_color: Optional[str] = None
    short_name: Optional[str] = None
    font_key: Optional[str] = None

    @field_validator("font_key")
    @classmethod
    def _font_key_must_be_known(cls, v: Optional[str]) -> Optional[str]:
        """Reject any typeface key the frontend cannot resolve.

        WHY VALIDATION HERE AND NOT ONLY IN THE FRONTEND (2026-08-19)
        ------------------------------------------------------------
        `update_tenant_branding` applies this payload with a blind
        ``setattr(t, k, v)`` loop, so whatever passes this model lands in the
        column verbatim. The stored value is then written into a live CSS
        custom property (``--brand-font``), and while
        ``resolveFontStack()`` already refuses unknown keys at render time,
        relying on that alone would mean:

          * the branding form silently "saves" a value that then does nothing,
            so the office sees a saved setting and the system stack -- a
            configuration that lies about itself; and
          * the only thing standing between a stored ``}`` and a broken
            stylesheet would be one client-side function, in a codebase where
            the same value is also consumed by the server-rendered document
            letterhead.

        Rejecting at the boundary means an unknown key is a 422 the admin can
        see and correct, rather than a setting that appears to work.

        The list is duplicated from FONT_STACKS in
        app/frontend/src/lib/branding.ts, and that duplication is checked --
        see tests/test_tenant_font_branding.py, which parses the TypeScript and
        compares the two sets, so they cannot drift apart silently.
        """
        if v is None:
            return None
        key = str(v).strip().lower()
        if not key:
            # An empty select means "use the default", not "store a blank".
            return _DEFAULT_FONT_KEY
        if key not in ALLOWED_FONT_KEYS:
            raise ValueError(
                f"unknown font key {v!r}; allowed: "
                f"{', '.join(sorted(ALLOWED_FONT_KEYS))}"
            )
        return key


def _admin_only(user: User, db: Session, perm: str = "settings:tenants"):
    """Gate a tenant-administration route on the permission catalogue.

    AUDIT 2026-07-31 (High) -- this used to be a bare ``user.role != "admin"``
    check, which had two defects:

    1. It bypassed PERMISSION_CATALOG. The catalogue declares
       ``settings:tenants`` and ``settings:branding``, and the RBAC editor
       renders a checkbox for each, but nothing in the codebase ever read
       either key -- ticking them did nothing at all.
    2. It ignored ``is_super_admin``. A super-admin whose ``role`` column was
       not literally the string ``"admin"`` was refused, even though
       ``user_permissions`` grants them the ``"*"`` wildcard everywhere else.

    Routing through ``user_permissions`` fixes both: admin (``role == "admin"``)
    and super-admin still resolve to ``{"*"}`` and pass unchanged, while a
    custom role that was granted the key now actually works.

    See section 4.12 of PROJECT_DOCUMENTATION.md.
    """
    granted = user_permissions(user, db)
    if "*" in granted or perm in granted:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Requires the '{perm}' permission")


def _require_control_plane_super_admin(request: Request, user: User) -> User:
    """Refuse tenant-local identities at the control-plane boundary."""
    if getattr(request.state, "tenant", None) is not None:
        raise HTTPException(403, "Control-plane administration is only available from the root portal")
    return user


def require_control_plane_super_admin(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    return _require_control_plane_super_admin(request, user)


def _serialize(t: Tenant) -> dict:
    logo_url = None
    if t.logo_filename:
        logo_url = f"/tenant-assets/{t.slug}/{t.logo_filename}"
    letterhead_url = None
    if getattr(t, "letterhead_path", None):
        letterhead_url = f"/tenant-assets/{t.slug}/{t.letterhead_path}"
    return {
        "id": t.id,
        "slug": t.slug,
        "company_name": t.company_name,
        "short_name": t.short_name,
        "subtitle": t.subtitle,
        "primary_color": t.primary_color,
        # getattr default: a tenant row read from a database that has not yet
        # had b2d7f9a3e614 applied has no attribute here, and the tenant LIST
        # is the page an admin opens to notice that -- 500ing it would hide the
        # very problem it should surface.
        "font_key": (getattr(t, "font_key", None) or _DEFAULT_FONT_KEY),
        "logo_filename": t.logo_filename,
        "logo_url": logo_url,
        "letterhead_path": getattr(t, "letterhead_path", None),
        "letterhead_url": letterhead_url,
        "office_name": getattr(t, "office_name", None),
        "license_number": getattr(t, "license_number", None),
        "office_address": getattr(t, "office_address", None),
        "demand_format": getattr(t, "demand_format", None),
        "receipt_template": getattr(t, "receipt_template", None),
        "db_path": t.db_path,
        # Custom-domain hosting: NULL when the tenant is only reachable
        # via <slug>.<root-domain>.
        "custom_domain": getattr(t, "custom_domain", None),
        "features": t.features or {},
        "contact_name": t.contact_name,
        "contact_email": t.contact_email,
        "contact_phone": t.contact_phone,
        "status": t.status,
        "plan": t.plan,
        "admin_email": t.admin_email,
        "notes": t.notes,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


# Per-tenant asset upload helpers
_TENANT_ASSETS_ROOT = "data/tenants"
_ALLOWED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "GIF"})


def _save_tenant_asset(slug: str, file: UploadFile, kind: str) -> str:
    """Decode, normalize and save a tenant branding image.

    Only raster formats are accepted. SVG is intentionally rejected because
    active SVG served from the application origin can execute script.
    """
    try:
        file.file.seek(0)
        raw = file.file.read(16 * 1024 * 1024 + 1)
    except OSError as exc:
        raise HTTPException(400, "Could not read uploaded image") from exc
    if len(raw) > 16 * 1024 * 1024:
        raise HTTPException(400, "Asset image too large (max 16 MB)")
    if len(raw) < 256:
        raise HTTPException(400, "Asset image too small or empty")
    try:
        image = normalize_image(raw, allowed_formats=_ALLOWED_IMAGE_FORMATS)
    except InvalidImage as exc:
        raise HTTPException(400, str(exc)) from exc

    dir_path = os.path.realpath(os.path.join(_TENANT_ASSETS_ROOT, slug))
    assets_root = os.path.realpath(_TENANT_ASSETS_ROOT)
    if os.path.commonpath((assets_root, dir_path)) != assets_root:
        raise HTTPException(400, "Invalid tenant asset path")
    os.makedirs(dir_path, exist_ok=True)
    fname = f"{kind}_{uuid.uuid4().hex[:8]}.{image.extension}"
    with open(os.path.join(dir_path, fname), "wb") as fh:
        fh.write(image.data)
    return fname


@router.get("")
def list_tenants(db: Session = Depends(get_db), user: User = Depends(require_control_plane_super_admin)):
    _admin_only(user, db)
    rows = db.query(Tenant).order_by(Tenant.created_at.desc()).all()
    return {"items": [_serialize(r) for r in rows], "total": len(rows)}


@router.get("/{tenant_id}")
def get_tenant(tenant_id: int, db: Session = Depends(get_db),
               user: User = Depends(require_control_plane_super_admin)):
    _admin_only(user, db)
    t = db.query(Tenant).get(tenant_id)
    if not t:
        raise HTTPException(404, "Tenant not found")
    return _serialize(t)


@router.post("", status_code=201)
def create_tenant(payload: TenantCreate, db: Session = Depends(get_db),
                  user: User = Depends(require_control_plane_super_admin)):
    """Provision a NEW portal copy for another business partner.

    Steps:
    1. Generate a unique slug
    2. Create an isolated SQLite database file at data/tenants/tenant_<slug>.db
    3. Run schema migrations (all model DDL) into that DB
    4. Seed the requested admin user inside the new DB
    5. Insert a Tenant row in the control DB with branding + feature flags
    """
    _admin_only(user, db)

    # Unique slug
    base_slug = slugify(payload.short_name or payload.company_name)
    slug = base_slug
    n = 2
    while db.query(Tenant).filter(Tenant.slug == slug).first():
        slug = f"{base_slug}-{n}"; n += 1

    # Provision isolated DB (this is the heavy step)
    try:
        db_path = provision_tenant_db(
            slug=slug,
            admin_email=payload.admin_email,
            admin_password=payload.admin_password,
            admin_name=payload.admin_name,
            company_name=payload.company_name,
            # M-2 FIX: forward short_name so the new tenant's file_prefix is
            # derived from ITS OWN branding, not Dogar Trading's default.
            short_name=payload.short_name or "",
        )
    except Exception as exc:
        raise HTTPException(500, f"Failed to provision tenant DB: {exc}") from exc

    # Build feature flag map (start from defaults, override with payload)
    features = Tenant.default_features()
    if payload.features:
        for k, v in payload.features.items():
            features[k] = bool(v)

    # Insert Tenant row
    t = Tenant(
        slug=slug,
        company_name=payload.company_name,
        short_name=payload.short_name or payload.company_name[:40],
        subtitle=payload.subtitle or "Overseas Employment Promoters",
        primary_color=payload.primary_color or "#2563eb",
        db_path=db_path,
        features=features,
        contact_phone=payload.contact_phone,
        plan=payload.plan or "standard",
        admin_email=payload.admin_email,
        admin_password_set=True,
        status="active",
        notes=payload.notes,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _serialize(t)


@router.put("/{tenant_id}")
def update_tenant(tenant_id: int, payload: TenantUpdate,
                  db: Session = Depends(get_db),
                  user: User = Depends(require_control_plane_super_admin)):
    _admin_only(user, db)
    t = db.query(Tenant).get(tenant_id)
    if not t:
        raise HTTPException(404, "Tenant not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(t, k, v)
    db.commit()
    db.refresh(t)
    return _serialize(t)


@router.put("/{tenant_id}/features")
def update_features(tenant_id: int, payload: FeaturesUpdate,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_control_plane_super_admin)):
    _admin_only(user, db)
    t = db.query(Tenant).get(tenant_id)
    if not t:
        raise HTTPException(404, "Tenant not found")
    # Start from defaults so any missing keys keep sensible defaults
    flags = Tenant.default_features()
    flags.update(t.features or {})
    flags.update({k: bool(v) for k, v in (payload.features or {}).items()})
    t.features = flags
    db.commit()
    db.refresh(t)
    return _serialize(t)


@router.delete("/{tenant_id}")
def delete_tenant(tenant_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_control_plane_super_admin)):
    _admin_only(user, db)
    t = db.query(Tenant).get(tenant_id)
    if not t:
        raise HTTPException(404, "Tenant not found")
    archived = ""
    try:
        archived = archive_tenant_db(t.db_path)
    except OSError as exc:
        # Archiving is best-effort -- failing to move the DB file
        # should not block tenant deletion. Log so the operator can
        # archive manually.
        import logging
        logging.getLogger("dtc.tenants").exception(
            "Could not archive tenant DB %r: %s", t.db_path, exc
        )
    db.commit()
    return {"ok": True, "archived_db": archived}


@router.get("/_meta/overview")
def portal_admin_overview(db: Session = Depends(get_db),
                          user: User = Depends(require_control_plane_super_admin)):
    """Super-admin command-centre data: per-tenant DB stats + a real-time
    cross-portal activity feed (recent logins, demand-file activity and
    candidate-processing stats). The control (main) portal is included as
    the first pseudo-tenant row so the admin sees EVERY portal copy.
    """
    _admin_only(user, db)
    import os as _os
    import sqlite3 as _sq
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    def _stats_from_sqlite(path: str) -> dict:
        """Best-effort row counts straight from the tenant SQLite file."""
        out = {"clients": 0, "demands": 0, "candidates": 0, "users": 0,
               "db_size_kb": 0, "recent_logins": [], "recent_activity": [],
               "candidates_this_week": 0, "demands_this_week": 0}
        if not path or not _os.path.exists(path):
            return out
        try:
            out["db_size_kb"] = round(_os.path.getsize(path) / 1024)
            con = _sq.connect(f"file:{path}?mode=ro", uri=True)
            con.row_factory = _sq.Row
            # SAFETY: `table` values are hardcoded literals from the tuple
            # below, never user input, so the f-string interpolation cannot
            # be exploited for SQL injection. The allowlist is enforced both by
            # the tuple itself AND by the explicit membership check below, which
            # RAISES (not assert — asserts are stripped under `python -O`). Do
            # not add dynamic table names here without keeping this check.
            _COUNT_TABLES = (("clients", "clients"), ("demands", "demands"),
                             ("candidates", "candidates"), ("users", "users"))
            _ALLOWED_COUNT_TABLES = {t for _, t in _COUNT_TABLES}
            for key, table in _COUNT_TABLES:
                if table not in _ALLOWED_COUNT_TABLES:
                    raise ValueError(f"Unsafe table name: {table!r}")
                try:
                    out[key] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except Exception:
                    pass
            week_ago = (_dt.utcnow() - _td(days=7)).strftime("%Y-%m-%d")
            try:
                out["candidates_this_week"] = con.execute(
                    "SELECT COUNT(*) FROM candidates WHERE created_at >= ?", (week_ago,)).fetchone()[0]
                out["demands_this_week"] = con.execute(
                    "SELECT COUNT(*) FROM demands WHERE created_at >= ?", (week_ago,)).fetchone()[0]
            except Exception:
                pass
            try:
                out["recent_logins"] = [dict(r) for r in con.execute(
                    "SELECT email, status, ip_address, occurred_at FROM login_history "
                    "ORDER BY id DESC LIMIT 5").fetchall()]
            except Exception:
                pass
            try:
                out["recent_activity"] = [dict(r) for r in con.execute(
                    "SELECT entity_type, action, actor_name, summary, occurred_at "
                    "FROM audit_logs ORDER BY id DESC LIMIT 8").fetchall()]
            except Exception:
                pass
            con.close()
        except Exception:
            pass
        return out

    # Control portal (main DB) as pseudo-tenant row
    from app.core.config import settings as _settings
    main_db = "data/dogar_trading.db"
    portals = [{
        "id": 0, "slug": "main", "company_name": _settings.COMPANY_NAME,
        "short_name": "MAIN", "status": "active", "plan": "control",
        "is_control": True, "stats": _stats_from_sqlite(main_db),
    }]
    for t in db.query(Tenant).order_by(Tenant.created_at.desc()).all():
        row = _serialize(t)
        row["is_control"] = False
        row["stats"] = _stats_from_sqlite(t.db_path or "")
        portals.append(row)

    totals = {
        "portals": len(portals),
        "clients": sum(p["stats"]["clients"] for p in portals),
        "demands": sum(p["stats"]["demands"] for p in portals),
        "candidates": sum(p["stats"]["candidates"] for p in portals),
        "candidates_this_week": sum(p["stats"]["candidates_this_week"] for p in portals),
        "demands_this_week": sum(p["stats"]["demands_this_week"] for p in portals),
    }
    # Merge activity feeds (newest first) with the portal tag
    feed = []
    for p in portals:
        for l in p["stats"]["recent_logins"]:
            feed.append({"portal": p["short_name"], "kind": "login",
                         "when": l.get("occurred_at"), "text": f"{l.get('email')} — {l.get('status')} ({l.get('ip_address') or 'n/a'})"})
        for a in p["stats"]["recent_activity"]:
            feed.append({"portal": p["short_name"], "kind": (a.get("entity_type") or "").lower(),
                         "when": a.get("occurred_at"), "text": f"{a.get('actor_name') or 'system'}: {a.get('summary') or a.get('action')}"})
    feed.sort(key=lambda x: x.get("when") or "", reverse=True)
    return {"portals": portals, "totals": totals, "activity": feed[:40]}


@router.get("/_meta/feature-defaults")
def feature_defaults(db: Session = Depends(get_db), user: User = Depends(require_control_plane_super_admin)):
    _admin_only(user, db)
    return Tenant.default_features()


# ----------------------------------------------------------------------
# Enterprise branding -- per-tenant logo / letterhead / office name
# ----------------------------------------------------------------------
@router.post("/{tenant_id}/logo")
def upload_tenant_logo(tenant_id: int,
                       file: UploadFile = File(...),
                       db: Session = Depends(get_db),
                       user: User = Depends(require_control_plane_super_admin)):
    """Upload the tenant's company logo (PNG/JPG only).

    Stored as ``data/tenants/<slug>/logo_<rand>.<ext>`` and served via
    the ``/tenant-assets`` static mount.  The filename is persisted
    on the Tenant row in ``logo_filename`` so we keep the asset
    history-friendly (uploading a new logo doesn't break the old URL).
    """
    _admin_only(user, db, "settings:branding")
    t = db.query(Tenant).get(tenant_id)
    if not t:
        raise HTTPException(404, "Tenant not found")
    fname = _save_tenant_asset(t.slug, file, kind="logo")
    t.logo_filename = fname
    # DEAD WRITE REMOVED 2026-08-20. This used to also assign ``t.logo_path``
    # "for callers that read logo_path instead of logo_filename". There are no
    # such callers: a repo-wide search finds ``logo_path`` in exactly three
    # places -- the model column, the baseline migration that created it, and
    # the comment that claimed it was needed. Nothing in the API, the
    # letterhead/document renderers, the boot payload or the SPA bundle reads
    # it. Two independent columns holding the same value with only one of them
    # maintained is how the two drift and how "the logo is wrong on one screen
    # only" bugs are born.
    #
    # The assignment was also wrapped in `try/except Exception: pass`, which
    # would have swallowed a genuine database error on a real write.
    #
    # The COLUMN is deliberately left in place: dropping it needs a migration,
    # and any pre-existing row that has logo_path set but logo_filename empty
    # would lose its logo. It is documented as legacy-unused on the model.
    db.commit()
    db.refresh(t)
    return _serialize(t)


# ----------------------------------------------------------------------
# Custom-domain hosting (2026-07-04 audit)
# ----------------------------------------------------------------------
import re as _re_domain

_DOMAIN_RE = _re_domain.compile(
    # Fully-qualified domain, lowercase letters/digits/hyphen, 2+ labels.
    # No trailing dot, no scheme, no port. Max total length 253 (RFC 1035).
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

# Domains that must never be bindable as a tenant custom domain
# (they belong to the platform / reverse proxy layer).
_RESERVED_DOMAINS = {
    "localhost", "127.0.0.1", "0.0.0.0",
}


def _normalize_domain(raw: str) -> str:
    """Lower-case, strip scheme/path/port. Empty string on failure."""
    if not raw:
        return ""
    d = raw.strip().lower()
    if "://" in d:
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0].split(":", 1)[0].rstrip(".")
    return d


@router.put("/{tenant_id}/domain")
def set_tenant_custom_domain(
    tenant_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_control_plane_super_admin),
):
    """Bind or unbind a custom domain to a tenant.

    Request body:  ``{"custom_domain": "portal.mytravel.pk"}``
    or             ``{"custom_domain": null}``  to unbind.

    Rules:
      * Only admins can call this.
      * Domain must be a valid FQDN (2+ labels, RFC-1035 char set, ≤253 chars).
      * Domain must not clash with any existing tenant (partial unique index).
      * Reserved platform domains (localhost, IP literals) are rejected.

    Response: the full tenant record with the updated ``custom_domain``.

    Note: this endpoint only records the intent in the DB. The reverse
    proxy / DNS provisioning (adding an A record, issuing TLS) happens
    at the infrastructure layer -- see AUDIT_REPORT.md 'Custom-domain
    hosting deployment guide' for the operational runbook.
    """
    _admin_only(user, db)
    t = db.query(Tenant).get(tenant_id)
    if not t:
        raise HTTPException(404, "Tenant not found")

    raw = payload.get("custom_domain")
    if raw in (None, "", "null"):
        # Unbind
        if hasattr(t, "custom_domain"):
            t.custom_domain = None
        db.commit()
        db.refresh(t)
        return _serialize(t)

    domain = _normalize_domain(str(raw))
    if not domain:
        raise HTTPException(400, "custom_domain is empty after normalisation")
    if domain in _RESERVED_DOMAINS:
        raise HTTPException(400, f"'{domain}' is reserved and cannot be bound to a tenant")
    if not _DOMAIN_RE.match(domain):
        raise HTTPException(400,
            f"'{domain}' is not a valid fully-qualified domain (need at least "
            f"two labels, lowercase, digits, or hyphens only)")

    # Uniqueness check (also enforced by partial unique index at DB level)
    if hasattr(Tenant, "custom_domain"):
        clash = (
            db.query(Tenant)
            .filter(Tenant.custom_domain == domain)
            .filter(Tenant.id != tenant_id)
            .first()
        )
        if clash:
            raise HTTPException(409,
                f"Domain '{domain}' is already bound to tenant #{clash.id} ({clash.slug})")
        t.custom_domain = domain

    db.commit()
    db.refresh(t)
    return _serialize(t)


@router.get("/_meta/custom-domains")
def list_active_custom_domains(
    db: Session = Depends(get_db),
    user: User = Depends(require_control_plane_super_admin),
):
    """Return every active tenant's custom domain.

    Used by the reverse-proxy sync tool + TrustedHostMiddleware at boot
    time so the app allows those domains through TrustedHost enforcement.
    """
    _admin_only(user, db)
    if not hasattr(Tenant, "custom_domain"):
        return {"domains": []}
    rows = (
        db.query(Tenant.custom_domain, Tenant.slug)
        .filter(Tenant.custom_domain.isnot(None))
        .filter(Tenant.status == "active")
        .all()
    )
    return {"domains": [{"custom_domain": d, "slug": s} for d, s in rows if d]}


@router.post("/{tenant_id}/letterhead")
def upload_tenant_letterhead(tenant_id: int,
                             file: UploadFile = File(...),
                             db: Session = Depends(get_db),
                             user: User = Depends(require_control_plane_super_admin)):
    """Upload the tenant's letterhead background image.

    The letterhead is used by the per-tenant document renderer
    (receipts, protector letters, demand letters, candidate
    print-profile) so every printed page carries the *tenant's* own
    company header instead of Dogar's default.
    """
    _admin_only(user, db, "settings:branding")
    t = db.query(Tenant).get(tenant_id)
    if not t:
        raise HTTPException(404, "Tenant not found")
    fname = _save_tenant_asset(t.slug, file, kind="letterhead")
    t.letterhead_path = fname
    db.commit()
    db.refresh(t)
    # Letterhead bytes are cached per-process -- wipe the cache so the
    # new asset is picked up on the next render without a restart.
    try:
        from app.services import letterhead_renderer as _lh
        if hasattr(_lh, "_tenant_letterhead_data_url"):
            _lh._tenant_letterhead_data_url.cache_clear()
    except Exception:
        pass
    return _serialize(t)


@router.put("/{tenant_id}/branding")
def update_tenant_branding(tenant_id: int, payload: BrandingUpdate,
                           db: Session = Depends(get_db),
                           user: User = Depends(require_control_plane_super_admin)):
    """Set the per-tenant enterprise identity (office name, demand
    file format, receipt template, subtitle, primary color)."""
    _admin_only(user, db, "settings:branding")
    t = db.query(Tenant).get(tenant_id)
    if not t:
        raise HTTPException(404, "Tenant not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        if v is not None:
            setattr(t, k, v)
    db.commit()
    db.refresh(t)
    return _serialize(t)


@router.get("/_meta/current")
def current_tenant_meta(request: Request):
    """Return the ResolvedTenant info attached by the middleware for
    THIS request (host-based subdomain resolution).

    Public endpoint -- frontend templates use this to render the
    correct logo / office name / brand color in the navbar without
    leaking any sensitive tenant configuration.
    """
    rt = getattr(request.state, "tenant", None)
    if rt is None:
        # Default (control) tenant -- return the Dogar defaults so the
        # UI still has *something* to render.
        #
        # `features` is deliberately the FULL default map here rather than {}.
        # The root portal has every capability, and a SPA that reads
        # `features[x]` would otherwise see undefined and could reasonably
        # treat it as "off" -- silently hiding Dogar's own pages. The server
        # already refuses to gate anything on the root portal
        # (tenant_feature_enabled returns True when no tenant is resolved), so
        # this just keeps the client's view consistent with the server's.
        return {
            "is_default": True,
            "slug": None,
            "company_name": "Dogar Trading Corporation",
            "short_name": "DTC",
            "primary_color": "#2563eb",
            "logo_url": None,
            "letterhead_url": None,
            "office_name": "",
            "demand_format": "",
            "features": Tenant.default_features(),
        }
    logo_url = None
    if rt.logo_filename:
        logo_url = f"/tenant-assets/{rt.slug}/{rt.logo_filename}"
    letterhead_url = None
    if rt.letterhead_path:
        letterhead_url = f"/tenant-assets/{rt.slug}/{rt.letterhead_path}"
    return {
        "is_default": False,
        "id": rt.id,
        "slug": rt.slug,
        "company_name": rt.company_name,
        "short_name": rt.short_name,
        "primary_color": rt.primary_color,
        "logo_url": logo_url,
        "letterhead_url": letterhead_url,
        "office_name": rt.office_name or "",
        "demand_format": rt.demand_format or "",
        # Merged over the defaults so a tenant row that predates a newly added
        # flag reports the new capability as ON rather than missing -- matching
        # `tenant_feature_enabled`, which treats an unknown flag as enabled.
        # The two MUST agree: if the SPA hid a page the API still serves, or
        # showed one the API 404s, the operator would see a portal that
        # contradicts itself.
        "features": {**Tenant.default_features(), **(dict(rt.features or {}))},
    }
