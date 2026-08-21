"""Regression tests for the SPA route-gating trio + Unauthorized flow.

Covers one route at each level of the privilege ladder:
  * /admin/login-history -> Admin only      (require_admin)
  * /settings/general    -> settings:view   (Admin + Manager)
  * /protector-letter    -> protector:view  (Admin + Manager + Staff)

The Admin-only slot used to be /admin/audit-log -> audit:view. That sidebar
feature was deleted on the operator's request (2026-07-30) together with the
/api/audit router and the audit:view permission key, so the slot is now filled
by GET /api/users/login-history -- the same shape of guard (an admin-only,
read-only viewer) on a route that still exists. Asserting against a deleted
route would have been a permanently-red test; asserting that a *deleted*
permission key is absent would have been vacuously green. Neither tests
anything. See section 4.11 of PROJECT_DOCUMENTATION.md.

The React ``RequirePermission`` guard is only cosmetic — the real
security boundary is the API, which gates the same endpoints behind the
same permission keys via ``require_permission``. These tests assert the
server-side gate matches the client-side gate so a hidden nav link can
never be bypassed by hitting the API directly.

They also assert the shape the Unauthorized page + AuthContext rely on
(``/users/me/permissions`` -> permissions[] + is_wildcard).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models import User


# --------------------------------------------------------------------------
# Helpers: seed a user with a given role and mint a bearer token for it.
# --------------------------------------------------------------------------
def _make_user(role: str, email: str) -> int:
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            existing.role = role
            existing.is_active = True
            existing.is_super_admin = False
            db.commit()
            return existing.id
        u = User(
            name=f"{role.title()} User",
            email=email,
            role=role,
            password_hash=hash_password("Passw0rd!123"),
            is_active=True,
            is_super_admin=False,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _headers(role: str, email: str) -> dict:
    uid = _make_user(role, email)
    # Root/control portal token (tenant="" is the default).
    token = create_access_token(str(uid))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def staff_headers() -> dict:
    return _headers("staff", "gate_staff@test.local")


@pytest.fixture
def manager_headers() -> dict:
    return _headers("manager", "gate_manager@test.local")


# --------------------------------------------------------------------------
# /users/me/permissions — the source of truth the SPA gates against.
# --------------------------------------------------------------------------
def test_me_permissions_admin_is_wildcard(client: TestClient, auth_headers: dict):
    r = client.get("/api/users/me/permissions", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_wildcard"] is True
    assert "*" in body["permissions"]


def test_me_permissions_staff_shape(client: TestClient, staff_headers: dict):
    r = client.get("/api/users/me/permissions", headers=staff_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_wildcard"] is False
    assert "*" not in body["permissions"]
    # Staff DOES have protector:view but NOT the admin-only / manager-only keys.
    assert "protector:view" in body["permissions"]
    assert "loginhistory:view" not in body["permissions"]
    assert "settings:view" not in body["permissions"]


def test_me_permissions_manager_shape(client: TestClient, manager_headers: dict):
    r = client.get("/api/users/me/permissions", headers=manager_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "settings:view" in body["permissions"]
    assert "protector:view" in body["permissions"]
    # Manager stops short of the admin-only viewers.
    assert "loginhistory:view" not in body["permissions"]


# --------------------------------------------------------------------------
# Route 1: /admin/login-history  (Admin only)
#
# Replaces the old /admin/audit-log trio, deleted with the Audit Log sidebar
# feature on 2026-07-30. Same guard shape: an admin-only read-only viewer.
# --------------------------------------------------------------------------
def test_admin_viewer_admin_allowed(client: TestClient, auth_headers: dict):
    r = client.get("/api/users/login-history", headers=auth_headers)
    assert r.status_code == 200, r.text


def test_admin_viewer_staff_denied(client: TestClient, staff_headers: dict):
    r = client.get("/api/users/login-history", headers=staff_headers)
    assert r.status_code == 403, r.text


def test_admin_viewer_manager_denied(client: TestClient, manager_headers: dict):
    r = client.get("/api/users/login-history", headers=manager_headers)
    assert r.status_code == 403, r.text


def test_admin_viewer_anonymous_denied(client: TestClient):
    r = client.get("/api/users/login-history")
    assert r.status_code in (401, 403), r.text


def test_deleted_audit_log_api_is_really_gone(client: TestClient, auth_headers: dict):
    """The Audit Log viewer API must not come back by accident.

    The operator asked for the feature to be *deleted*, not hidden. If someone
    re-registers app/api/endpoints/audit.py this turns red, which is the point:
    a hidden nav entry with a live API is exactly the state we were asked to
    stop shipping.
    """
    r = client.get("/api/audit/", headers=auth_headers)
    assert r.status_code == 404, (
        f"/api/audit/ should be gone, got {r.status_code}: {r.text[:200]}"
    )


def test_deleted_data_migration_api_is_really_gone(client: TestClient, auth_headers: dict):
    """Same guarantee for the Data Migration endpoints.

    /api/admin/form-schemas is deliberately NOT covered here -- it survived the
    removal in its own router because it is an unrelated feature.
    """
    for path in ("/api/admin/integrity/report", "/api/admin/migration/jobs"):
        r = client.get(path, headers=auth_headers)
        assert r.status_code == 404, (
            f"{path} should be gone, got {r.status_code}: {r.text[:200]}"
        )


def test_surviving_form_schema_api_still_admin_gated(client: TestClient, auth_headers: dict, staff_headers: dict):
    """The lifted /api/admin/form-schemas router kept its admin gate.

    NOTE on the ``cookies.clear()`` below -- it is load-bearing, not cargo cult.
    ``auth_headers`` is session-scoped and obtains its token by POSTing to
    /api/auth/login, and the app also accepts auth from the ``access_token``
    HttpOnly cookie that login sets on the shared TestClient. The autouse
    ``_isolate_client_cookies`` fixture clears the jar *between* tests, but if
    this test is the one that first materialises ``auth_headers`` the login
    happens after that clear, so the cookie is still attached mid-test and the
    "anonymous" request below would come back 200. Clearing the jar here makes
    the third assertion genuinely anonymous regardless of fixture ordering.
    """
    assert client.get("/api/admin/form-schemas", headers=auth_headers).status_code == 200
    assert client.get("/api/admin/form-schemas", headers=staff_headers).status_code == 403
    client.cookies.clear()
    assert client.get("/api/admin/form-schemas").status_code == 401


# --------------------------------------------------------------------------
# Route 2: /settings/general  (settings:view — Admin + Manager)
# --------------------------------------------------------------------------
def test_settings_admin_allowed(client: TestClient, auth_headers: dict):
    r = client.get("/api/settings/general", headers=auth_headers)
    assert r.status_code == 200, r.text


def test_settings_manager_allowed(client: TestClient, manager_headers: dict):
    r = client.get("/api/settings/general", headers=manager_headers)
    assert r.status_code == 200, r.text


def test_settings_staff_denied(client: TestClient, staff_headers: dict):
    r = client.get("/api/settings/general", headers=staff_headers)
    assert r.status_code == 403, r.text


def test_settings_anonymous_denied(client: TestClient):
    r = client.get("/api/settings/general")
    assert r.status_code in (401, 403), r.text


# --------------------------------------------------------------------------
# Route 3: /protector-letter  (protector:view — Admin + Manager + Staff)
# --------------------------------------------------------------------------
def test_protector_admin_allowed(client: TestClient, auth_headers: dict):
    # No candidate seeded -> 404, but crucially NOT 403 (permission passed).
    r = client.get("/api/protector-letters/lookup/AB1234567", headers=auth_headers)
    assert r.status_code != 403, r.text
    assert r.status_code in (200, 404), r.text


def test_protector_staff_allowed(client: TestClient, staff_headers: dict):
    r = client.get("/api/protector-letters/lookup/AB1234567", headers=staff_headers)
    # Staff HAS protector:view — must not be a permission failure.
    assert r.status_code != 403, r.text
    assert r.status_code in (200, 404), r.text


def test_protector_manager_allowed(client: TestClient, manager_headers: dict):
    r = client.get("/api/protector-letters/lookup/AB1234567", headers=manager_headers)
    assert r.status_code != 403, r.text
    assert r.status_code in (200, 404), r.text


def test_protector_anonymous_denied(client: TestClient):
    r = client.get("/api/protector-letters/lookup/AB1234567")
    assert r.status_code in (401, 403), r.text
