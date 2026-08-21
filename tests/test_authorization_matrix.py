"""Automated authorization ("RLS-equivalent") test suite.

The original brief asked for an "RLS test suite". This app does NOT use
Supabase/Postgres Row-Level Security — authorization is enforced at the
application layer via ``require_permission(...)`` / ``require_admin`` FastAPI
dependencies (see app/core/permissions.py, app/core/deps.py). This suite is the
faithful equivalent: it logs in as an **admin** and as a **non-admin (staff)**
and asserts, table by table, that:

  * write operations the role is allowed to perform return 2xx/expected-non-403
  * write operations the role must NOT perform return **403**
  * unauthenticated writes return **401**

Staff preset (app/core/permissions.py) is the reference:
  ALLOWED  : candidates:create / candidates:edit
  DENIED   : clients:create/edit/delete, demands:create/delete,
             candidates:delete, users:create/delete, any :delete

A 403 (permission denied) and a 401 (no auth) are the security-relevant
outcomes. For "allowed" writes we accept any status that is NOT 401/403 —
a 400/404/422 means the guard let us through and the request only failed on
payload/lookup, which still proves the authorization gate opened.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import User

STAFF_EMAIL = "staff.matrix@test.local"
STAFF_PASSWORD = "StaffMatrixPass123!"


@pytest.fixture(scope="module")
def staff_user():
    """Create (once) a real staff user that can log in."""
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == STAFF_EMAIL).first()
        if not existing:
            u = User(
                name="Staff Matrix",
                email=STAFF_EMAIL,
                password_hash=hash_password(STAFF_PASSWORD),
                role="staff",
                is_active=True,
                is_super_admin=False,
                must_change_password=False,
            )
            db.add(u)
            db.commit()
        yield
    finally:
        db.close()


@pytest.fixture(scope="module")
def seed_fk_rows(client: TestClient):
    """Ensure a Client row with id=1 exists so admin demand/candidate writes
    satisfy FK constraints (this suite tests the AUTH gate, not empty-DB FKs)."""
    from app.models import Client
    db = SessionLocal()
    try:
        if not db.query(Client).filter(Client.id == 1).first():
            db.add(Client(id=1, company_name="Authz Seed Co"))
            db.commit()
        yield
    finally:
        db.close()


@pytest.fixture
def staff_token(client: TestClient, staff_user) -> str:
    r = client.post(
        "/api/auth/login",
        json={"username": STAFF_EMAIL, "password": STAFF_PASSWORD},
    )
    assert r.status_code == 200, f"staff login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture
def staff_headers(staff_token: str) -> dict:
    return {"Authorization": f"Bearer {staff_token}"}


# Each case: (method, path, json_body)
# We only care about the AUTH gate, not the eventual success of the write.
# Use a high, non-existent id (999999) for delete/update targets so the auth
# gate is exercised without mutating seed rows — a 404 (row missing) still
# proves the authorization gate opened; a 403 proves it was blocked.
_WRITE_ENDPOINTS = [
    ("post",   "/api/clients/",              {"company_name": "Authz Co"}),
    ("put",    "/api/clients/999999",        {"company_name": "Renamed"}),
    ("delete", "/api/clients/999999",        None),
    ("post",   "/api/demands/",              {"client_id": 1, "country": "UAE"}),
    ("delete", "/api/demands/999999",        None),
    ("post",   "/api/candidates/",           {"full_name": "Authz Candidate"}),
    ("delete", "/api/candidates/999999",     None),
    ("post",   "/api/users/",                {"name": "X", "email": "x@x.com",
                                              "password": "Whatever123!", "role": "staff"}),
    ("delete", "/api/users/999999",          None),
]


def _call(client: TestClient, method: str, path: str, body, headers=None):
    fn = getattr(client, method)
    if method in ("post", "put", "patch"):
        return fn(path, json=(body or {}), headers=headers)
    return fn(path, headers=headers)


# --------------------------------------------------------------------------
# 1. Unauthenticated writes are always rejected (401)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("method,path,body", _WRITE_ENDPOINTS)
def test_unauthenticated_writes_denied(client: TestClient, method, path, body):
    r = _call(client, method, path, body)
    assert r.status_code == 401, (
        f"{method.upper()} {path} should require auth (401), got {r.status_code}"
    )


# --------------------------------------------------------------------------
# 2. Admin: every write passes the authorization gate (never 401/403)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("method,path,body", _WRITE_ENDPOINTS)
def test_admin_writes_allowed(client: TestClient, auth_headers, seed_fk_rows, method, path, body):
    r = _call(client, method, path, body, headers=auth_headers)
    assert r.status_code not in (401, 403), (
        f"admin {method.upper()} {path} must not be auth-blocked, got {r.status_code}: {r.text[:200]}"
    )


# --------------------------------------------------------------------------
# 3. Staff (non-admin): only permitted writes pass; the rest are 403
# --------------------------------------------------------------------------
# What the staff preset ALLOWS (gate should open -> not 403).
_STAFF_ALLOWED = [
    ("post", "/api/candidates/", {"full_name": "Authz Candidate"}),
]
# What the staff preset DENIES (must be 403).
_STAFF_DENIED = [
    ("post",   "/api/clients/",          {"company_name": "Authz Co"}),
    ("put",    "/api/clients/999999",    {"company_name": "Renamed"}),
    ("delete", "/api/clients/999999",    None),
    ("post",   "/api/demands/",          {"client_id": 1, "country": "UAE"}),
    ("delete", "/api/demands/999999",    None),
    ("delete", "/api/candidates/999999", None),
    ("post",   "/api/users/",            {"name": "X", "email": "x2@x.com",
                                          "password": "Whatever123!", "role": "staff"}),
    ("delete", "/api/users/999999",      None),
]


@pytest.mark.parametrize("method,path,body", _STAFF_ALLOWED)
def test_staff_allowed_writes_not_blocked(client: TestClient, staff_headers, method, path, body):
    r = _call(client, method, path, body, headers=staff_headers)
    assert r.status_code != 403, (
        f"staff {method.upper()} {path} should be ALLOWED, got 403: {r.text[:200]}"
    )
    assert r.status_code != 401


@pytest.mark.parametrize("method,path,body", _STAFF_DENIED)
def test_staff_denied_writes_forbidden(client: TestClient, staff_headers, method, path, body):
    r = _call(client, method, path, body, headers=staff_headers)
    assert r.status_code == 403, (
        f"staff {method.upper()} {path} must be DENIED (403), got {r.status_code}: {r.text[:200]}"
    )


# --------------------------------------------------------------------------
# 4. REMOVED 2026-07-30 (operator request)
# --------------------------------------------------------------------------
# This file used to end with three parametrized tests
# (test_admin_migration_requires_auth / _forbids_staff / _allows_admin, 9
# executed cases) covering GET /api/admin/integrity/report,
# POST /api/admin/migration/backfill-fks and GET /api/admin/migration/jobs.
#
# The operator asked for the "Data Migration" sidebar feature to be deleted
# outright, so app/api/endpoints/admin_migration.py, app/services/
# migration_jobs.py and app/services/data_integrity.py are gone and those
# routes no longer exist. Keeping the tests would assert 401/403/200 against
# paths that now 404 -- a guaranteed failure that says nothing about the
# security posture of the routes that DO exist.
#
# The surviving /api/admin/form-schemas endpoints kept their admin gate and
# are covered by tests/test_form_schema.py.
# See section 4.11 of PROJECT_DOCUMENTATION.md.


# --------------------------------------------------------------------------
# 5. Unauthenticated READS are rejected too (added 2026-08-10)
# --------------------------------------------------------------------------
# WHY THIS SECTION EXISTS
# ----------------------
# Everything above tests WRITES. A full anonymous sweep of the live route table
# found a read that answered without any credential:
#
#   GET /api/demands/config/next-file-number  ->  200
#       {"prefix": "DTC/786/80/", "next_seq": "8204", ...}
#
# It was the only demand route without a permission dependency, and it leaked
# the tenant's file-number scheme plus -- via next_seq -- the running count of
# demand files ever created (a direct read on business volume, pollable over
# time to watch that volume change). It is now guarded with `demands:create`.
#
# A hand-maintained list of paths would not have caught it, because nobody
# thought to add it. So this test does not use a list: it walks the FastAPI
# route table, calls every parameter-free GET anonymously, and requires each
# one to be either explicitly public or unauthorized. A new unguarded read
# fails here the moment it is added.

# Routes that are legitimately reachable without credentials, each with the
# reason it is safe. Anything not on this list must return 401/403/404.
_PUBLIC_GET_ROUTES = {
    "/healthz",                      # liveness probe (no app data)
    "/api/health",                   # readiness/diagnostics probe
    "/api/ping",                     # liveness probe
    "/api/tenants/_meta/current",    # pre-login branding only: company name,
                                     # colour, logo URL. No tenant config, no
                                     # counts, no PII -- the login page needs
                                     # it before any session exists.
}


def _parameterless_get_paths() -> list[str]:
    from app.main import app as fastapi_app

    paths = []
    for route in fastapi_app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if "GET" not in methods or "{" in path:
            continue
        if not path.startswith("/api/") and path not in _PUBLIC_GET_ROUTES:
            continue  # SPA page routes serve the shell, not application data
        paths.append(path)
    return sorted(set(paths))


def test_the_route_sweep_actually_finds_routes():
    """Guard the guard: an empty sweep would pass while testing nothing."""
    paths = _parameterless_get_paths()
    assert len(paths) > 20, (
        f"route sweep found only {len(paths)} GET routes -- the discovery "
        "logic is broken and this suite is not actually checking anything"
    )


# The interactive API documentation. These are mounted ONLY when
# settings.is_production is false (app/main.py sets docs_url/redoc_url/
# openapi_url to None in production, and the live production server returns 404
# for all three -- verified). The test suite deliberately runs with
# ENV=development, so they exist here; they must NOT be treated as a leak in
# this environment, but test_api_docs_are_disabled_in_production below pins the
# production behaviour so the two cannot drift apart.
_DEV_ONLY_DOC_ROUTES = {"/api/docs", "/api/redoc", "/api/openapi.json"}


def test_no_api_get_route_serves_application_data_anonymously(client: TestClient):
    """Every parameter-free GET must require auth unless explicitly public."""
    from app.core.config import settings

    leaks = []
    for path in _parameterless_get_paths():
        if path in _PUBLIC_GET_ROUTES:
            continue
        if path in _DEV_ONLY_DOC_ROUTES and not settings.is_production:
            continue
        r = client.get(path)
        if r.status_code in (200, 201):
            leaks.append(f"{path} -> {r.status_code} {r.text[:120]}")

    assert not leaks, (
        "these GET routes returned application data to an ANONYMOUS caller.\n"
        "Add a require_permission(...) dependency, or -- if the route is "
        "genuinely public -- add it to _PUBLIC_GET_ROUTES with a written "
        "justification:\n  - " + "\n  - ".join(leaks)
    )


def test_next_file_number_requires_authentication(client: TestClient):
    """Explicit regression pin for the leak that motivated this section."""
    r = client.get("/api/demands/config/next-file-number")
    assert r.status_code == 401, (
        "GET /api/demands/config/next-file-number leaked the tenant file-number "
        f"prefix and demand-volume counter anonymously (got {r.status_code})"
    )


def test_next_file_number_still_works_for_an_authorized_user(client: TestClient, auth_headers):
    """The fix must not break the Add Demand wizard that consumes it."""
    r = client.get("/api/demands/config/next-file-number", headers=auth_headers)
    assert r.status_code == 200, (r.status_code, r.text[:200])
    body = r.json()
    assert "display" in body and body["display"], body


def test_api_docs_are_disabled_in_production():
    """Swagger/ReDoc/OpenAPI must not be mounted when ENV=production.

    The anonymous sweep above tolerates these three routes because the suite
    runs with ENV=development. That tolerance is only safe if production really
    does drop them -- otherwise the full endpoint inventory, every request
    schema and every field name would be published to anonymous callers. Build
    a second app instance with the production flag flipped and assert they are
    gone, so the exemption above can never quietly become a hole.
    """
    import importlib

    from app.core.config import settings as live_settings

    original = live_settings.ENV
    try:
        live_settings.ENV = "production"
        assert live_settings.is_production, "ENV override did not take effect"

        import app.main as main_mod

        reloaded = importlib.reload(main_mod)
        try:
            doc_paths = {
                getattr(r, "path", "") for r in reloaded.app.routes
            } & _DEV_ONLY_DOC_ROUTES
            assert not doc_paths, (
                "interactive API documentation is mounted in production, "
                f"exposing the whole endpoint/schema inventory: {sorted(doc_paths)}"
            )
        finally:
            # Restore the module to its development-configured state so the
            # rest of the session keeps the app instance it started with.
            live_settings.ENV = original
            importlib.reload(main_mod)
    finally:
        live_settings.ENV = original
