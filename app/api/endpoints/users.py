import json
import os
import uuid
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user, require_admin  # noqa: F401  (see note below)
from app.core.permissions import require_permission

# AUDIT 2026-07-31 (High) -- the nine administrative routes below were guarded by
# `require_admin`, a hard `user.role != "admin"` check. Nothing was *over*-
# exposed (they were admin-locked, which is stricter than the catalogue), but
# PERMISSION_CATALOG declares users:view|create|edit|delete, roles:view|edit and
# loginhistory:view, and the RBAC editor renders a checkbox for each. None of
# those seven keys was ever read by any code path, so building a custom role to
# delegate, say, user administration to an office manager produced a role that
# looked correct in the UI and did nothing.
#
# `require_permission` admits admin and super-admin through the "*" wildcard, so
# built-in Admin behaviour is byte-for-byte unchanged; the keys simply work now.
# `require_admin` is intentionally still imported: it remains the right tool for
# routes that must never be delegatable, and keeping the import documents that
# the switch below was a decision rather than an oversight.
# See section 4.12 of PROJECT_DOCUMENTATION.md.
from app.core.security import hash_password, verify_password
from app.core.tenancy import get_tenant_db
from app.models import User
from app.models.lookups import LoginHistory, Role
from app.schemas.schemas import (
    PasswordChange,
    ProfileUpdate,
    UserCreate,
    UserOut,
    UserUpdate,
)

router = APIRouter()


# ============================================================================
# Self-service profile endpoints -- the logged-in user editing their own
# account. NO admin permission required.
# ============================================================================
@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.put("/me", response_model=UserOut)
def update_my_profile(
    payload: ProfileUpdate,
    db: Session = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """Update the currently-logged-in user's own profile.
    Role / is_active / email / password are intentionally NOT editable here --
    they must go through the admin user-management screen, or via the
    `/change-password` endpoint for password.
    """
    obj = db.query(User).filter(User.id == user.id).first()
    if not obj:
        raise HTTPException(404, "User not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        if v is not None:
            setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/me/photo", response_model=UserOut)
async def upload_my_photo(
    file: UploadFile = File(...),
    db: Session = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """Upload a new profile photo for the current user. Stored under
    /static/uploads/avatars/<userid>_<uuid>.<ext> and the URL is saved to
    `users.photo`.
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")

    # Read file bytes first so we can validate them before touching disk
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:    # 5 MB limit
        raise HTTPException(400, "Image too large (max 5MB)")

    # H-09: Derive extension from actual image bytes via Pillow, NOT from the
    # client-supplied filename. A malicious user could upload a PHP/SHTML script
    # with a faked image/jpeg content-type and a .php filename extension.
    # Pillow's verify() raises on any non-image data before we accept the file.
    import io

    from PIL import Image as _PilImage
    try:
        img = _PilImage.open(io.BytesIO(contents))
        img.verify()                      # raises on corrupted / non-image data
        img = _PilImage.open(io.BytesIO(contents))  # re-open after verify() (verify exhausts it)
        pil_format = img.format or "JPEG"
    except Exception as exc:
        raise HTTPException(400, "Invalid image file -- upload a valid JPEG, PNG, WEBP, or GIF") from exc
    ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}.get(pil_format, "jpg")
    # H-09: intentionally ignore file.filename -- extension comes from bytes only

    # Save file
    avatar_dir = os.path.join(settings.UPLOAD_DIR, "avatars")
    os.makedirs(avatar_dir, exist_ok=True)
    fname = f"{user.id}_{uuid.uuid4().hex[:8]}.{ext}"
    fpath = os.path.join(avatar_dir, fname)
    with open(fpath, "wb") as f:
        f.write(contents)

    # Update user
    obj = db.query(User).filter(User.id == user.id).first()
    if not obj:
        raise HTTPException(404, "User not found")
    obj.photo = f"/static/uploads/avatars/{fname}"
    db.commit()
    db.refresh(obj)
    return obj


@router.post("/me/change-password")
def change_my_password(
    payload: PasswordChange,
    db: Session = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """Change the current user's password. Requires the current password."""
    obj = db.query(User).filter(User.id == user.id).first()
    if not obj:
        raise HTTPException(404, "User not found")
    if not verify_password(payload.current_password, obj.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    if not payload.new_password or len(payload.new_password) < 6:
        raise HTTPException(400, "New password must be at least 6 characters")
    obj.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True, "message": "Password changed successfully"}


@router.get("/", response_model=List[UserOut])
def list_users(db: Session = Depends(get_tenant_db),
               admin: User = Depends(require_permission("users:view"))):
    return db.query(User).order_by(User.id.desc()).all()


@router.post("/", response_model=UserOut)
def create_user(payload: UserCreate, db: Session = Depends(get_tenant_db),
                admin: User = Depends(require_permission("users:create"))):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(400, "Email already registered")
    data = payload.model_dump()
    pwd = data.pop("password")
    obj = User(**data, password_hash=hash_password(pwd))
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_tenant_db),
                admin: User = Depends(require_permission("users:edit"))):
    obj = db.query(User).filter(User.id == user_id).first()
    if not obj:
        raise HTTPException(404, "User not found")

    # Super-admin protection: only the super-admin themself may modify a
    # super-admin row, and even then they cannot drop their own role or
    # super-admin flag.
    if obj.is_super_admin and not admin.is_super_admin:
        raise HTTPException(403, "Cannot modify the permanent super-admin account")

    data = payload.model_dump(exclude_unset=True)
    if "password" in data and data["password"]:
        obj.password_hash = hash_password(data.pop("password"))
    elif "password" in data:
        data.pop("password")

    # Even the super-admin cannot demote themselves or disable themselves --
    # otherwise a confused click could lock the vendor out of the system.
    if obj.is_super_admin:
        data.pop("role", None)
        data.pop("is_active", None)
        data.pop("is_super_admin", None)

    for k, v in data.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_tenant_db),
                admin: User = Depends(require_permission("users:delete"))):
    if user_id == admin.id:
        raise HTTPException(400, "Cannot delete yourself")
    obj = db.query(User).filter(User.id == user_id).first()
    if not obj:
        raise HTTPException(404, "User not found")
    # Permanent super-admin can never be deleted, full stop.
    if obj.is_super_admin:
        raise HTTPException(403, "The permanent super-admin account cannot be deleted")
    db.delete(obj)
    db.commit()
    return {"ok": True}


# ===== Login History =====
@router.get("/login-history")
def login_history(
    db: Session = Depends(get_tenant_db),
    admin: User = Depends(require_permission("loginhistory:view")),
    status: Optional[str] = Query(None, description="Success | Failed | All"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    q: Optional[str] = Query(None, description="search email"),
    skip: int = 0,
    limit: int = 100,
):
    query = db.query(LoginHistory)
    if status and status.lower() not in ("all", ""):
        query = query.filter(LoginHistory.status == status)
    if date_from:
        query = query.filter(LoginHistory.occurred_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(LoginHistory.occurred_at <= datetime.combine(date_to, datetime.max.time()))
    if q:
        query = query.filter(LoginHistory.email.ilike(f"%{q}%"))
    total = query.count()
    rows = query.order_by(LoginHistory.occurred_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "email": r.email,
                "status": r.status,
                "ip_address": r.ip_address,
                "user_agent": r.user_agent,
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            }
            for r in rows
        ],
    }


# ===== Admin Audit Log =====
# ADDED 2026-08-15 (operator requirement): "an admin audit log that records
# login history AND every role-protected document action (view/edit/print/
# disable) with timestamps and user IDs in real time".
#
# Login history already had a viewer (above). Document actions did not: an
# audit-log viewer had existed but was deleted on 2026-07-30 along with the
# `audit:view` permission, and `AuditLog` rows -- which the candidate timeline
# still writes -- became readable ONLY through /candidates/{id}/audit. There
# was no way to answer "who printed this document, and when?" across the
# portal. Measured on the live DB before this change:
#     select count(*) from audit_logs where entity_type='document';  -> 0
#
# Two properties this endpoint must preserve, both enforced by tests:
#   * It reads from get_tenant_db, so one office can never read another
#     office's trail (the control-DB leak guarded by tests/test_tenancy.py).
#   * `occurred_at` is a server-side default on the model, so the timestamp
#     shown here is the database's, not a client-supplied value.
# THE "OFFICE" DIMENSION (2026-08-18)
#
# The operator asked to filter this page "by office". That request cannot be
# satisfied with a WHERE clause, and pretending otherwise would break the
# isolation guarantee this portal is built on: each recruitment office has its
# OWN DATABASE (app/core/tenancy._make_tenant_sessionmaker), so there is no
# single audit_logs table containing more than one office's rows. A join is not
# available -- and it must not become available, because "one office can never
# read another office's trail" is the property tests/test_tenancy.py protects.
#
# So "by office" is implemented as SCOPE, not as a filter: the office being
# viewed is whichever tenant the request resolved to, and `office` is reported
# back in the response so the UI can label the page truthfully instead of
# implying the list spans every office. A portal-level operator switches office
# by opening that office's portal (/t/<slug>/...), which routes the whole
# request -- session, permissions and trail -- into that tenant's database.
#
# The `office` query parameter therefore exists ONLY to be validated: if a
# caller passes an office that is not the one this request resolved to, it is
# refused with 400 rather than silently ignored. Silently ignoring it is the
# dangerous option: an operator who filtered to "Karachi" would be shown
# Islamabad's denials under a Karachi heading and could clear an office of
# wrongdoing on another office's data.
# The complete set of outcomes the audit writer can ever record. DERIVED from
# the writer's own constants rather than spelled out here, so that adding a
# third outcome cannot leave this validator refusing a value the app itself
# writes -- a drift that would be invisible until an operator hit it.
def _known_outcomes() -> frozenset[str]:
    from app.services.audit import DENIED, SUCCESS

    return frozenset({DENIED, SUCCESS})


_KNOWN_OUTCOMES = _known_outcomes()


def _assert_office_matches_request(request: Request, office: Optional[str]) -> dict:
    """Resolve the office this request is scoped to, and police ``office``.

    Returns a small descriptor for the response payload. Raises 400 when the
    caller asks for an office other than the one it is actually reading.
    """
    tenant = getattr(request.state, "tenant", None)
    slug = getattr(tenant, "slug", None) or ""
    name = (
        getattr(tenant, "office_name", None)
        or getattr(tenant, "company_name", None)
        or settings.COMPANY_NAME
    )
    current = {"slug": slug, "name": name, "is_default": tenant is None}
    if office and office.lower() not in ("all", ""):
        # "all" is refused too -- see below. It is a sentinel the other filters
        # use to mean "no constraint", but for office it would be a LIE: this
        # response can only ever contain one office's rows.
        if office != slug:
            raise HTTPException(
                400,
                (
                    f"This audit trail belongs to office {slug or 'main'!r}. "
                    f"Cannot filter it by office {office!r}: each office's audit "
                    "log lives in its own database. Open that office's portal to "
                    "read its trail."
                ),
            )
    return current


@router.get("/audit-log")
def audit_log(
    request: Request,
    db: Session = Depends(get_tenant_db),
    admin: User = Depends(require_permission("audit:view")),
    entity_type: Optional[str] = Query(None, description="document | candidate | demand | …"),
    action: Optional[str] = Query(None, description="view | print | update | disable | …"),
    actor_user_id: Optional[int] = Query(None),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    q: Optional[str] = Query(None, description="search the summary text"),
    outcome: Optional[str] = Query(
        None, description="success | denied | all -- find refused actions",
    ),
    permission: Optional[str] = Query(
        None, description="the permission key that was refused (denials only)",
    ),
    doc_category: Optional[str] = Query(
        None, description="document type, e.g. visa | protector | bank",
    ),
    office: Optional[str] = Query(
        None, description="tenant slug -- must match the office being viewed",
    ),
    skip: int = 0,
    limit: int = Query(100, le=500),
):
    from app.models import AuditLog

    current_office = _assert_office_matches_request(request, office)

    query = db.query(AuditLog)
    if entity_type and entity_type.lower() not in ("all", ""):
        query = query.filter(AuditLog.entity_type == entity_type)
    if action and action.lower() not in ("all", ""):
        query = query.filter(AuditLog.action == action)
    if actor_user_id:
        query = query.filter(AuditLog.actor_user_id == actor_user_id)
    # OUTCOME FILTER (2026-08-18). The operator asked to find "denied or
    # successful actions". Note the guard on "all"/"" mirrors the existing
    # entity_type/action handling: the SPA sends the sentinel rather than
    # omitting the parameter, and treating "all" as a literal value to match
    # would return an empty page and read as "there are no denied actions".
    if outcome and outcome.lower() not in ("all", ""):
        # UNKNOWN OUTCOME IS REFUSED, NOT ANSWERED (2026-08-21). Unlike
        # `permission` and `doc_category` -- open key spaces where "no rows"
        # is a truthful answer for a key nobody has used yet -- `outcome` is a
        # CLOSED set: app/services/audit.py only ever writes SUCCESS or
        # DENIED. So `?outcome=denide` (or a stale client sending an outcome
        # this version no longer emits) could only ever match zero rows, and
        # the endpoint would answer 200 with an empty page.
        #
        # On a security-review surface that is the dangerous failure: the
        # operator asked "show me the refused actions", and an authoritative
        # "none" is indistinguishable from "nothing was refused". They would
        # reasonably conclude the office is clean. This is the identical
        # failure mode the comment below the `("all", "")` guard already
        # condemns; it just was not enforced for values outside the set.
        #
        # 400 with the permitted values, matching the precedent set by
        # _assert_office_matches_request above: an impossible filter is a bad
        # request, not an empty result.
        if outcome.lower() not in _KNOWN_OUTCOMES:
            raise HTTPException(
                400,
                (
                    f"Unknown outcome {outcome!r}. Every recorded action is "
                    f"either {' or '.join(sorted(_KNOWN_OUTCOMES))}; pass 'all' "
                    "for both. Refusing rather than returning an empty page, "
                    "because 'no results' here would read as 'nothing was "
                    "denied'."
                ),
            )
        query = query.filter(AuditLog.outcome == outcome.lower())
    if permission and permission.lower() not in ("all", ""):
        query = query.filter(AuditLog.permission == permission)
    if doc_category and doc_category.lower() not in ("all", ""):
        # Indexed column, NOT a reach into after_json -- see alembic
        # a1c6e8b4d072 for why the JSON route was rejected as non-portable.
        query = query.filter(AuditLog.doc_category == doc_category)
    if date_from:
        query = query.filter(AuditLog.occurred_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(AuditLog.occurred_at <= datetime.combine(date_to, datetime.max.time()))
    if q:
        # Free-text search spans the summary, the actor and the refused
        # permission key. Searching only the summary meant an operator looking
        # for "documents:print" found nothing, because on a denial that key
        # lives in its own column.
        needle = f"%{q}%"
        query = query.filter(
            or_(
                AuditLog.summary.ilike(needle),
                AuditLog.actor_name.ilike(needle),
                AuditLog.permission.ilike(needle),
                AuditLog.entity_type.ilike(needle),
            )
        )
    total = query.count()
    rows = query.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        # The office these rows belong to, so the page can be labelled with the
        # office it is actually showing. See _assert_office_matches_request.
        "office": current_office,
        "items": [
            {
                "id": r.id,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "action": r.action,
                "actor_user_id": r.actor_user_id,
                "actor_name": r.actor_name,
                "summary": r.summary,
                "ip_address": r.ip_address,
                "user_agent": r.user_agent,
                # OUTCOME IN THE PAYLOAD (2026-08-18). The first cut of this
                # change added the outcome FILTER but left the response shape
                # alone -- so a client could ask for denials and then have no
                # way to tell a denial from a success in the result, and a
                # mixed ("all") listing was indistinguishable from the old
                # success-only one. A filter whose result you cannot read is
                # not a working filter.
                "outcome": r.outcome or "success",
                "permission": r.permission,
                "doc_category": r.doc_category,
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            }
            for r in rows
        ],
    }


@router.get("/audit-log/_meta/filters")
def audit_log_filters(
    request: Request,
    db: Session = Depends(get_tenant_db),
    admin: User = Depends(require_permission("audit:view")),
):
    """Distinct entity types / actions / actors actually present in the trail.

    Populates the viewer's dropdowns from the data rather than a hardcoded
    list, so a newly-audited action shows up without a frontend change.

    ``outcomes`` is the ONE list here that is NOT purely data-driven. It always
    offers both 'success' and 'denied', even when the trail happens to contain
    no denials yet. Deriving it from the data would hide the "denied" option on
    exactly the healthy portal where an operator most wants to check for
    denials -- and its absence would read as "this portal cannot show denials",
    which is the bug that was just fixed. The counts alongside tell the truth
    about what is actually there.
    """
    from app.models import AuditLog

    types = [r[0] for r in db.query(AuditLog.entity_type).distinct().all() if r[0]]
    actions = [r[0] for r in db.query(AuditLog.action).distinct().all() if r[0]]
    actors = [
        {"id": r[0], "name": r[1]}
        for r in db.query(AuditLog.actor_user_id, AuditLog.actor_name).distinct().all()
        if r[0]
    ]
    permissions = [
        r[0] for r in db.query(AuditLog.permission).distinct().all() if r[0]
    ]
    doc_categories = [
        r[0] for r in db.query(AuditLog.doc_category).distinct().all() if r[0]
    ]
    counts = {
        "denied": db.query(AuditLog).filter(AuditLog.outcome == "denied").count(),
        "success": db.query(AuditLog).filter(AuditLog.outcome == "success").count(),
    }
    tenant = getattr(request.state, "tenant", None)
    return {
        "entity_types": sorted(types),
        "actions": sorted(actions),
        "actors": sorted(actors, key=lambda a: (a["name"] or "").lower()),
        "outcomes": ["success", "denied"],
        "outcome_counts": counts,
        "permissions": sorted(permissions),
        "doc_categories": sorted(doc_categories),
        # Single-element by design: this trail spans exactly one office. The UI
        # renders it as a fixed label rather than a chooser, so the page cannot
        # imply it can show another office's rows.
        "office": {
            "slug": getattr(tenant, "slug", None) or "",
            "name": (
                getattr(tenant, "office_name", None)
                or getattr(tenant, "company_name", None)
                or settings.COMPANY_NAME
            ),
            "is_default": tenant is None,
        },
    }


# ===== Roles =====
@router.get("/roles")
def list_roles(db: Session = Depends(get_tenant_db),
               admin: User = Depends(require_permission("roles:view"))):
    rows = db.query(Role).order_by(Role.id.asc()).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "is_system": r.is_system,
            "permissions": json.loads(r.permissions or "[]"),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/roles")
def create_role(payload: dict, db: Session = Depends(get_tenant_db),
                admin: User = Depends(require_permission("roles:edit"))):
    if not payload.get("name"):
        raise HTTPException(400, "Name is required")
    if db.query(Role).filter(Role.name == payload["name"]).first():
        raise HTTPException(400, "Role name already exists")
    role = Role(
        name=payload["name"],
        description=payload.get("description", ""),
        is_system=False,
        permissions=json.dumps(payload.get("permissions", [])),
    )
    db.add(role); db.commit(); db.refresh(role)
    return {"id": role.id, "name": role.name}


@router.put("/roles/{role_id}")
def update_role(role_id: int, payload: dict, db: Session = Depends(get_tenant_db),
                admin: User = Depends(require_permission("roles:edit"))):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role: raise HTTPException(404, "Role not found")
    # System roles (Admin/Manager/Staff) -- only permissions and
    # description are editable, name is locked so we don't break the
    # FK-style join in user.role.
    is_admin_role = (role.name or "").strip().lower() == "admin"
    if role.is_system:
        if "name" in payload:
            raise HTTPException(400, "System role name cannot be changed")
        if is_admin_role and "permissions" in payload:
            # Admin role MUST always be the wildcard -- refuse to weaken it
            perms = payload.get("permissions") or []
            if "*" not in perms:
                raise HTTPException(
                    400, "Admin role must keep the wildcard '*' permission. "
                         "Create a custom role instead if you need to scope down."
                )
    if "name" in payload and not role.is_system:
        role.name = payload["name"]
    if "description" in payload:
        role.description = payload["description"]
    if "permissions" in payload:
        role.permissions = json.dumps(payload["permissions"])
    db.commit(); return {"ok": True}


@router.delete("/roles/{role_id}")
def delete_role(role_id: int, db: Session = Depends(get_tenant_db),
                admin: User = Depends(require_permission("roles:edit"))):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role: raise HTTPException(404, "Role not found")
    if role.is_system:
        raise HTTPException(400, "Cannot delete system roles")
    db.delete(role); db.commit(); return {"ok": True}


# ===== RBAC meta endpoints =====
@router.get("/roles/_meta/catalog")
def roles_catalog(user: User = Depends(get_current_user)):
    """Public to authenticated users -- returns the permission catalog
    so the role editor UI can render its checkbox matrix.

    Also returns the default presets so the UI can offer a
    "Reset to Manager default" / "Reset to Staff default" button.
    """
    from app.core.permissions import PERMISSION_CATALOG, ROLE_PRESETS, all_permission_keys
    return {
        "catalog": PERMISSION_CATALOG,
        "presets": ROLE_PRESETS,
        "all_keys": all_permission_keys(),
    }


@router.get("/me/permissions")
def my_permissions(db: Session = Depends(get_tenant_db),
                   user: User = Depends(get_current_user)):
    """Frontend permission gate -- returns the effective permission set
    for the currently signed-in user. UI elements use this to hide /
    disable controls the user can't action."""
    from app.core.permissions import user_permissions
    perms = user_permissions(user, db)
    return {
        "user_id": user.id,
        "role": user.role,
        "is_super_admin": bool(getattr(user, "is_super_admin", False)),
        "permissions": sorted(perms),
        "is_wildcard": "*" in perms,
    }
