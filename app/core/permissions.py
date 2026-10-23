"""Role-Based Access Control (RBAC) -- permission catalog + helpers.

A Role row has a ``permissions`` TEXT column storing a JSON array of
permission *keys* (e.g. ``["candidates:view", "documents:print"]``).
This module defines:

  * ``PERMISSION_CATALOG`` -- the full menu of permission keys the UI
    can offer when editing a role, grouped by module.
  * ``ROLE_PRESETS`` -- default permission sets for the three system
    roles (Admin / Manager / Staff) so first-launch is sensible.
  * ``user_permissions(user, db)`` -- resolve the *effective* permission
    set for a user (joining their role to the Role row).
  * ``user_has_permission(user, db, perm)`` -- boolean check.
  * ``require_permission(perm)`` -- FastAPI dependency factory. Use as
    ``Depends(require_permission("candidates:edit"))``. Admin and
    super-admin **always** bypass; an explicit "*" permission also
    grants everything.

Design notes
------------
* The decorator is intentionally simple -- no nested groups, no
  inheritance -- because the user explicitly asked for *granular
  per-tenant role configuration*, not a heavyweight policy engine.
* The catalog is the SOURCE OF TRUTH consumed by the role editor UI
  via ``GET /api/roles/_meta/catalog`` -- adding a new permission key
  here automatically surfaces it in the UI.
* Permission keys follow the pattern ``module:action`` so they're
  self-documenting in logs and JWT-claims-style usage.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional, Sequence

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.tenancy import get_tenant_db
from app.db.session import get_db  # noqa: F401 (kept for backward-compat imports)
from app.models import User
from app.models.lookups import Role

log = logging.getLogger("dtc.rbac")


# ---------------------------------------------------------------------
# CATALOG -- every UI module / capability the role editor can toggle
# ---------------------------------------------------------------------
PERMISSION_CATALOG: list[dict] = [
    {"module": "Dashboard", "items": [
        {"key": "dashboard:view", "label": "View dashboard"},
    ]},
    {"module": "Clients", "items": [
        {"key": "clients:view",   "label": "View clients"},
        {"key": "clients:create", "label": "Create client"},
        {"key": "clients:edit",   "label": "Edit client"},
        {"key": "clients:delete", "label": "Delete client"},
    ]},
    {"module": "Demand Files", "items": [
        {"key": "demands:view",   "label": "View demand files"},
        {"key": "demands:create", "label": "Create demand"},
        {"key": "demands:edit",   "label": "Edit demand"},
        {"key": "demands:delete", "label": "Delete demand"},
        {"key": "demands:print",  "label": "Print demand documents"},
    ]},
    {"module": "Candidates", "items": [
        {"key": "candidates:view",        "label": "View candidates"},
        {"key": "candidates:create",      "label": "Create candidate"},
        {"key": "candidates:edit",        "label": "Edit candidate"},
        {"key": "candidates:delete",      "label": "Delete candidate"},
        {"key": "candidates:stage",       "label": "Change workflow stage"},
        {"key": "candidates:assign",      "label": "Assign to demand"},
        {"key": "candidates:documents",   "label": "Upload / view candidate documents"},
        {"key": "candidates:print",       "label": "Print candidate profile / barcode"},
        {"key": "candidates:export",      "label": "Export candidates"},
    ]},
    {"module": "Documents", "items": [
        {"key": "documents:view",    "label": "View document templates"},
        {"key": "documents:edit",    "label": "Edit document templates"},
        {"key": "documents:designer","label": "Use coordinate designer"},
        {"key": "documents:print",   "label": "Print generated documents"},
    ]},
    {"module": "Protector Letters", "items": [
        {"key": "protector:view",  "label": "View protector workflow"},
        {"key": "protector:print", "label": "Print protector packet"},
        {"key": "protector:edit",  "label": "Edit protector data (E-number etc.)"},
    ]},
    {"module": "Receipts & Finance", "items": [
        {"key": "receipts:view",   "label": "View receipts"},
        {"key": "receipts:create", "label": "Issue receipt"},
        {"key": "receipts:print",  "label": "Print receipt"},
        {"key": "depositors:view", "label": "View depositors"},
        {"key": "depositors:edit", "label": "Manage depositors"},
    ]},
    {"module": "Users & Roles", "items": [
        {"key": "users:view",   "label": "View users"},
        {"key": "users:create", "label": "Create user"},
        {"key": "users:edit",   "label": "Edit user"},
        {"key": "users:delete", "label": "Delete user"},
        {"key": "roles:view",   "label": "View roles"},
        {"key": "roles:edit",   "label": "Create / edit roles"},
    ]},
    {"module": "Settings", "items": [
        {"key": "settings:view",     "label": "View settings"},
        {"key": "settings:company",  "label": "Edit company settings"},
        {"key": "settings:lookups",  "label": "Edit lookups (cities, embassies…)"},
        {"key": "settings:tenants",  "label": "Manage tenant portals (Copy Portal)"},
        {"key": "settings:branding", "label": "Manage per-tenant branding"},
        # Dedicated, high-trust permission for the whole-database export.
        # Previously the backup list/download endpoints were gated only by the
        # broad "settings:view" read permission — anyone who could open the
        # settings screen could pull a ZIP containing every table (users,
        # login_history, password_reset_tokens, support_messages) plus assets.
        # (M-audit 2026-07-24)
        {"key": "settings:backup",   "label": "Create / download full portal backups"},
    ]},
    {"module": "Audit & Security", "items": [
        {"key": "loginhistory:view",  "label": "View login history"},
        # RESTORED 2026-08-15. "audit:view" was removed on 2026-07-30 because
        # the viewer it gated had been deleted, which was correct at the time:
        # a checkbox granting access to nothing is a lie to the operator.
        # The operator has now explicitly required "an admin audit log that
        # records login history AND every role-protected document action
        # (view/edit/print/disable) with timestamps and user IDs", so the
        # viewer is back -- GET /api/users/audit-log -- and the key is real
        # again. tests/test_rbac_enforcement.py proves it is read by a code
        # path, and tests/test_audit_trail.py proves the route it guards
        # returns 403 without it.
        {"key": "audit:view",         "label": "View the admin audit log"},
    ]},
    # REMOVED 2026-07-30 (operator request): "agents:view", "agents:edit",
    # "reports:view", "reports:export" (whole Reports module).
    # The features they gated -- Sub-Agents, Reports and Data Migration --
    # were deleted, so keeping the permissions would
    # have left tick-boxes in the role editor that grant access to nothing.
    # Custom roles that still carry the old keys are unaffected: resolution
    # ignores unknown keys, so they degrade to no-ops rather than erroring.
]


def all_permission_keys() -> list[str]:
    """Flat list of every permission key in the catalog."""
    out: list[str] = []
    for group in PERMISSION_CATALOG:
        for item in group["items"]:
            out.append(item["key"])
    return out


_RESERVED_ROLE_NAMES = frozenset({"admin", "manager", "staff", "agent"})


def validate_role_name(name: str) -> str:
    """Normalize and validate a role name before it becomes an authority."""
    normalized = str(name or "").strip().lower()
    if not normalized or not re.fullmatch(r"[a-z0-9][a-z0-9 _-]{0,49}", normalized):
        raise ValueError("Role name must contain only letters, numbers, spaces, '-' or '_'")
    if normalized in _RESERVED_ROLE_NAMES:
        raise ValueError("That role name is reserved")
    return normalized


def validate_role_permissions(
    role_name: str, permissions: Sequence[str], *, is_system: bool = False
) -> list[str]:
    """Validate permissions at the write boundary, not only at read time."""
    values = list(dict.fromkeys(str(p).strip() for p in (permissions or [])))
    if "*" in values and (role_name.strip().lower() != "admin" or not is_system):
        raise ValueError("Wildcard permission is reserved for the system Admin role")
    allowed = set(all_permission_keys())
    unknown = [p for p in values if p != "*" and p not in allowed]
    if unknown:
        raise ValueError(f"Unknown permission key(s): {', '.join(unknown)}")
    if role_name.strip().lower() == "admin" and is_system and values != ["*"]:
        raise ValueError("System Admin role must keep exactly the wildcard permission")
    return values


# ---------------------------------------------------------------------
# PRESETS -- sensible defaults for the three system roles
# ---------------------------------------------------------------------
ROLE_PRESETS: dict[str, list[str]] = {
    # Admin -- everything (the asterisk wildcard is preferred so new
    # permissions added later automatically apply to admins).
    "admin": ["*"],

    # Manager -- full operational access but no user/role management,
    # no tenant management, no audit log.
    "manager": [
        "dashboard:view",
        "clients:view", "clients:create", "clients:edit",
        "demands:view", "demands:create", "demands:edit", "demands:print",
        "candidates:view", "candidates:create", "candidates:edit",
        "candidates:stage", "candidates:assign",
        "candidates:documents", "candidates:print", "candidates:export",
        "documents:view", "documents:edit", "documents:designer", "documents:print",
        "protector:view", "protector:print", "protector:edit",
        "receipts:view", "receipts:create", "receipts:print",
        "depositors:view", "depositors:edit",
        "settings:view", "settings:lookups",
    ],

    # Staff -- day-to-day data entry. Can view + create + edit own
    # work but cannot delete, change company settings, or print
    # financial documents.
    "staff": [
        "dashboard:view",
        "clients:view",
        "demands:view",
        "candidates:view", "candidates:create", "candidates:edit",
        "candidates:documents", "candidates:print",
        "documents:view", "documents:print",
        "protector:view",
        "receipts:view",
    ],
}


# ---------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------
def _parse_perms(raw: Optional[str]) -> list[str]:
    """Safely parse the Role.permissions JSON column."""
    if not raw:
        return []
    try:
        out = json.loads(raw)
        if isinstance(out, list):
            return [str(x) for x in out]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return []


def user_permissions(user: User, db: Session) -> set[str]:
    """Return the effective permission set for ``user``.

    Resolution order:
      1. Super-admin / role='admin' → ``{"*"}`` (wildcard).
      2. Otherwise look up the Role row whose ``name`` matches
         ``user.role`` (case-insensitive) and parse its
         ``permissions`` JSON list.
      3. If no Role row matches, fall back to ``ROLE_PRESETS`` for
         the role name (so a brand-new DB without seeded Role rows
         still has sane defaults).
      4. If still empty, return ``set()`` (deny everything).
    """
    if getattr(user, "is_super_admin", False):
        return {"*"}
    role_name = (user.role or "").strip().lower()
    if role_name == "admin":
        return {"*"}

    # Look up the Role row
    row = (db.query(Role)
             .filter(Role.name.ilike(role_name))
             .first())
    if row is not None:
        perms = _parse_perms(row.permissions)
        if perms:
            return set(perms)

    # Fallback to presets
    preset = ROLE_PRESETS.get(role_name)
    if preset:
        return set(preset)

    return set()


def user_has_permission(user: User, db: Session, perm: str) -> bool:
    """Cheap convenience wrapper."""
    perms = user_permissions(user, db)
    return "*" in perms or perm in perms


# ---------------------------------------------------------------------
# FastAPI dependency factory
# ---------------------------------------------------------------------
def require_permission(*perms: str, mode: str = "any"):
    """Return a FastAPI dependency that 403's if the user lacks any
    of the requested permissions.

    Parameters
    ----------
    perms : str
        One or more permission keys. With ``mode="any"`` (the default)
        the user needs **any one** of them; with ``mode="all"`` the
        user needs **all** of them.

    Notes
    -----
    * Admin / super-admin / wildcard (``"*"``) always pass.
    * Failures return **403** with a human-readable detail so the
      frontend can surface the missing permission directly.
    """
    if mode not in ("any", "all"):
        raise ValueError("mode must be 'any' or 'all'")
    required: Sequence[str] = tuple(perms)

    def _dep(request: Request,
             user: User = Depends(get_current_user),
             db: Session = Depends(get_tenant_db)) -> User:
        # Permissions/roles are stored per-tenant, so resolve them against
        # the tenant DB (yields control DB on the root host).
        granted = user_permissions(user, db)
        if "*" in granted:
            return user
        if mode == "any":
            ok = any(p in granted for p in required)
        else:
            ok = all(p in granted for p in required)
        if not ok:
            # AUDIT THE DENIAL (2026-08-18). This used to raise and write
            # nothing, so the audit trail contained only actions that SUCCEEDED
            # and a user probing for privileges left no trace at all.
            #
            # Guarded HERE as well as inside _audit_denial. That is not
            # redundant paranoia: this is the AUTHORIZATION boundary, and its
            # correctness must not depend on the internal correctness of an
            # observability helper. Without this outer try, anything the audit
            # path raises -- a future edit that moves code above the helper's
            # own try, a stale import, a monkeypatched logger in a test harness
            # -- converts a correct 403 into a 500. tests/test_audit_denials.py
            # ::test_a_broken_denial_write_still_returns_403_not_500 caught
            # exactly that, and it is the worse failure of the two: the request
            # SHOULD be refused, and a 500 both hides that it was refused and
            # looks like a portal fault.
            try:
                _audit_denial(db, user, required, mode, request)
            except Exception as exc:  # noqa: BLE001 -- never break the 403
                log.warning("denial audit raised past its own guard: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=("Permission denied -- requires "
                        + (" or ".join(required) if mode == "any"
                           else " + ".join(required))),
            )
        return user

    return _dep


def _audit_denial(db: Session, user: Optional[User], perms: Sequence[str],
                  mode: str, request: Optional[Request]) -> None:
    """Record a refused permission in the audit trail.

    Imported lazily. app.services.audit pulls in the model layer, and this
    module is imported by essentially every endpoint module, so a top-level
    import would create a cycle.

    The key logged is the FULL requirement, not just the first entry: with
    mode='all' a user may hold one of two required keys, and recording only
    ``perms[0]`` would make the trail claim they were refused something they
    actually had. Joined with " or " / " + " so the row states the real
    condition that failed.

    Best-effort by contract. This runs on the way to a 403 and must never turn
    correct authorization behaviour into a server error, so every failure is
    swallowed and logged rather than raised.
    """
    try:
        from app.services.audit import log_permission_denied

        key = (" or ".join(perms) if mode == "any" else " + ".join(perms))
        log_permission_denied(db, user=user, permission=key, request=request)
    except Exception as exc:  # noqa: BLE001 -- auditing must not break the 403
        log.warning(
            "could not audit permission denial (perms=%r mode=%r): %s",
            list(perms), mode, exc,
        )


def assert_permission(user: User, db: Session, *perms: str,
                      request: Optional[Request] = None) -> None:
    """Imperative (in-handler) counterpart to :func:`require_permission`.

    Some capabilities in this codebase are *field-level*, not route-level:
    a single ``PUT /api/candidates/{id}`` can change a plain demographic
    column, advance the workflow stage, or overwrite the Protector
    E-number -- three different privileges arriving on one route.  A
    ``Depends()`` guard cannot distinguish them, so those routes carry the
    coarse key (``candidates:edit``) as a dependency and call this helper
    for the finer keys once the payload is known.

    Raises 403 unless the user holds **any** of ``perms`` (admin /
    super-admin pass via the ``"*"`` wildcard, exactly as with
    :func:`require_permission`).
    """
    granted = user_permissions(user, db)
    if "*" in granted or any(p in granted for p in perms):
        return
    # Field-level denials are audited exactly like route-level ones. Leaving
    # this path unaudited would have produced a trail that silently depended on
    # WHICH guard refused -- a coarse route guard logged, a fine field guard
    # invisible -- which is worse than no auditing, because it looks complete.
    #
    # Same outer guard as in _dep, for the same reason: the 403 must not depend
    # on the audit path succeeding.
    try:
        _audit_denial(db, user, tuple(perms), "any", request)
    except Exception as exc:  # noqa: BLE001 -- never break the 403
        log.warning("denial audit raised past its own guard: %s", exc)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Permission denied -- requires " + " or ".join(perms),
    )
