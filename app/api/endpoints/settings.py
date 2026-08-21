import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

# NOTE: this module used to import `get_current_user`, `require_admin` and
# `get_db` behind blanket `# noqa: F401` markers. All three became genuinely
# unused once both routes moved onto `require_permission` (audit 2026-07-31),
# and a `noqa` on an unused import is exactly how dead references survive a
# lint gate -- so they are gone rather than silenced.
from app.api.endpoints.tenants import BrandingUpdate, _save_tenant_asset
from app.core.permissions import require_permission
from app.core.tenancy import get_tenant_db
from app.db.session import SessionLocal as ControlSessionLocal
from app.models import Tenant, User
from app.models.lookups import CompanySettings

router = APIRouter()
log = logging.getLogger("dtc.settings")


def _current_tenant_or_404(request: Request, db: Session) -> Tenant:
    resolved = getattr(request.state, "tenant", None)
    if resolved is None:
        raise HTTPException(status_code=400, detail="Tenant branding is only available inside an office portal")
    tenant = (
        db.query(Tenant)
        .filter(Tenant.id == resolved.id, Tenant.slug == resolved.slug, Tenant.status == "active")
        .first()
    )
    if tenant is None:
        raise HTTPException(status_code=404, detail="Current tenant not found")
    return tenant


def _branding_dict(tenant: Tenant) -> dict:
    return {
        "id": tenant.id,
        "slug": tenant.slug,
        "name": tenant.company_name,
        "shortName": tenant.short_name,
        "color": tenant.primary_color or "#2563eb",
        "fontKey": getattr(tenant, "font_key", None) or "system",
        "logo": (f"/tenant-assets/{tenant.slug}/{tenant.logo_filename}"
                 if tenant.logo_filename else "/static/img/logo-sm.png"),
        "officeName": getattr(tenant, "office_name", None) or "",
        "subtitle": tenant.subtitle or "Overseas Employment Promoters",
        "licenseNumber": getattr(tenant, "license_number", None) or "",
        "officeAddress": getattr(tenant, "office_address", None) or "",
        "contactPhone": tenant.contact_phone or "",
        "contactEmail": tenant.contact_email or "",
    }


def _to_dict(s: CompanySettings):
    return {col.name: (getattr(s, col.name).isoformat() if hasattr(getattr(s, col.name), "isoformat") else getattr(s, col.name))
            for col in s.__table__.columns}


@router.get("/branding")
def get_current_branding(
    request: Request,
    tenant_db: Session = Depends(get_tenant_db),
    user: User = Depends(require_permission("settings:branding")),
):
    """Return branding for the office resolved by this request."""
    control_db = ControlSessionLocal()
    try:
        tenant = _current_tenant_or_404(request, control_db)
        return _branding_dict(tenant)
    finally:
        control_db.close()


@router.put("/branding")
def update_current_branding(
    payload: BrandingUpdate,
    request: Request,
    tenant_db: Session = Depends(get_tenant_db),
    user: User = Depends(require_permission("settings:branding")),
):
    """Upsert the current office branding in one validated transaction."""
    control_db = ControlSessionLocal()
    try:
        tenant = _current_tenant_or_404(request, control_db)
        for key, value in payload.model_dump(exclude_unset=True).items():
            if value is not None:
                setattr(tenant, key, value)
        control_db.commit()
        control_db.refresh(tenant)
        return _branding_dict(tenant)
    finally:
        control_db.close()


@router.post("/branding/logo")
def upload_current_branding_logo(
    request: Request,
    file: UploadFile = File(...),
    tenant_db: Session = Depends(get_tenant_db),
    user: User = Depends(require_permission("settings:branding")),
):
    """Upload a raster logo directly for the resolved office tenant."""
    control_db = ControlSessionLocal()
    try:
        tenant = _current_tenant_or_404(request, control_db)
        filename = _save_tenant_asset(tenant.slug, file, kind="logo")
        tenant.logo_filename = filename
        tenant.logo_path = filename
        control_db.commit()
        control_db.refresh(tenant)
        return _branding_dict(tenant)
    finally:
        control_db.close()


@router.get("/general")
def get_general(db: Session = Depends(get_tenant_db), user: User = Depends(require_permission("settings:view"))):
    s = db.query(CompanySettings).first()
    if not s:
        s = CompanySettings()
        db.add(s); db.commit(); db.refresh(s)
    return _to_dict(s)


# AUDIT 2026-07-31 (High) -- this route was guarded by `require_admin`, a hard
# `user.role != "admin"` check that bypasses PERMISSION_CATALOG entirely. The
# catalogue declares a `settings:company` key and the RBAC editor renders a
# checkbox for it, but nothing in the codebase ever read it: ticking the box did
# nothing, and a custom role created specifically to let a trusted manager
# maintain the agency's own letterhead/name/logo was silently powerless.
# `require_permission` still admits admin and super-admin via the "*" wildcard,
# so built-in Admin behaviour is unchanged -- the key simply works now.
@router.put("/general")
def update_general(payload: dict, db: Session = Depends(get_tenant_db),
                  user: User = Depends(require_permission("settings:company"))):
    s = db.query(CompanySettings).first()
    if not s:
        s = CompanySettings()
        db.add(s); db.flush()
    allowed = {c.name for c in CompanySettings.__table__.columns} - {"id", "updated_at"}
    for k, v in payload.items():
        if k in allowed:
            setattr(s, k, v)
    db.commit(); db.refresh(s)
    # Flush the process-level TTL cache so the next PDF/print request
    # immediately sees the updated agency settings (logo, name, etc.)
    # without waiting for the 60-second TTL to expire.
    try:
        from app.services.overlay_engine import invalidate_company_settings_cache
        invalidate_company_settings_cache()
    except Exception as exc:  # noqa: BLE001 -- best-effort cache flush
        # Deliberately non-fatal: the settings ARE saved by this point, and the
        # stale cache self-heals after the 60s TTL. But a bare `pass` made a
        # permanent failure (e.g. a renamed helper) invisible, so operators
        # would see "settings saved" while prints kept the old logo for reasons
        # nothing in the logs explained.
        log.warning("company-settings cache flush failed (stale for <=60s): %s", exc)
    return _to_dict(s)
