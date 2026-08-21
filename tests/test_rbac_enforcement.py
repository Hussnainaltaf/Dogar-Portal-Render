"""RBAC enforcement guards.

Two classes of defect are locked down here.

1. **Catalogue drift** -- a permission key that is declared in
   ``PERMISSION_CATALOG`` (and therefore rendered as a tickable checkbox in
   the Roles editor) but read by *no* code path.  Ticking such a box is a
   lie to the operator: it changes nothing.  On 2026-07-31 an audit found
   **22 of 44 keys** in exactly that state.

   The detection has one subtlety that made an earlier hand-check vacuous:
   the grep MUST exclude ``app/core/permissions.py`` itself, otherwise the
   catalogue trivially satisfies every key it declares and the check always
   passes.

2. **Live privilege escalation** -- routes that perform privileged writes
   behind nothing stronger than ``get_current_user``.  The same audit
   proved a Staff token could create lookup rows, create depositors, and
   *issue a financial receipt* (``RCP-2026-0001``).  The behavioural tests
   below re-run those exact escalations and require a 403.
"""
import pathlib

import pytest

from app.core.permissions import PERMISSION_CATALOG, ROLE_PRESETS
from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models import User

REPO = pathlib.Path(__file__).resolve().parents[1]
CATALOGUE_FILE = (REPO / "app" / "core" / "permissions.py").resolve()

ALL_KEYS = [item["key"] for mod in PERMISSION_CATALOG for item in mod["items"]]


def _enforcement_corpus() -> str:
    """Every source file that could *read* a permission key, minus the
    catalogue that declares them."""
    parts = []
    for pattern in ("app/**/*.py", "app/frontend/src/**/*.ts", "app/frontend/src/**/*.tsx"):
        for path in REPO.glob(pattern):
            if path.resolve() == CATALOGUE_FILE:
                continue
            parts.append(path.read_text(errors="ignore"))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 1. Catalogue drift
# ---------------------------------------------------------------------------
def test_catalogue_has_no_duplicate_keys():
    assert len(ALL_KEYS) == len(set(ALL_KEYS)), "duplicate key in PERMISSION_CATALOG"


def test_every_catalogued_permission_is_actually_enforced_somewhere():
    """Guard against catalogue drift -- see the module docstring."""
    corpus = _enforcement_corpus()
    dead = sorted(k for k in ALL_KEYS if k not in corpus)
    assert not dead, (
        "These PERMISSION_CATALOG keys are rendered as checkboxes in the Roles "
        "editor but are read by no code path, so granting them does nothing:\n  "
        + "\n  ".join(dead)
        + "\n\nEither bind each key to the route/branch that implements the "
          "capability, or delete it from PERMISSION_CATALOG (which also means "
          "updating the counts in README.md and PROJECT_DOCUMENTATION.md, "
          "asserted by tests/test_docs_integrity.py)."
    )


def test_the_drift_detector_is_not_vacuous():
    """Mutation guard: a bogus key MUST be reported as dead.

    Without this, a bug in ``_enforcement_corpus()`` (e.g. globbing nothing)
    would make the test above pass unconditionally.
    """
    corpus = _enforcement_corpus()
    assert len(corpus) > 100_000, "corpus looks empty -- the glob is broken"
    assert "nonsense:permission-that-does-not-exist" not in corpus


def test_every_enforced_permission_is_declared_in_the_catalogue():
    """The REVERSE of the drift check above -- and it was missing.

    ``test_every_catalogued_permission_is_actually_enforced_somewhere``
    catches a key that exists in the catalogue but nowhere in the code.
    Nothing caught the opposite: a key that a route *enforces* via
    ``require_permission("x:y")`` but that the catalogue never declares.

    That direction is not harmless. The Roles editor renders its checkboxes
    from ``PERMISSION_CATALOG``, so an undeclared key is a capability that
    **cannot be granted to any custom role at all** -- admins still get in
    through the ``"*"`` wildcard, so the route looks fine in testing, while
    delegating it to an office manager is quietly impossible. The operator
    would tick every box in the editor and still get a 403.

    Found 2026-08-15 by mutation GI: deleting ``audit:view`` from the
    catalogue left ``GET /api/users/audit-log`` enforcing a key no role
    could ever hold, and the entire suite stayed green.
    """
    import re

    enforced: set[str] = set()
    for path in REPO.glob("app/**/*.py"):
        if path.resolve() == CATALOGUE_FILE:
            continue
        text = path.read_text(errors="ignore")
        for m in re.finditer(r'require_permission\(\s*([^)]*)\)', text):
            enforced.update(re.findall(r'["\']([a-z_]+:[a-z_]+)["\']', m.group(1)))
        for m in re.finditer(r'assert_permission\([^)]*?["\']([a-z_]+:[a-z_]+)["\']', text):
            enforced.add(m.group(1))

    assert len(enforced) > 20, (
        f"only {len(enforced)} enforced keys discovered -- the regex is "
        f"probably broken, which would make this test vacuous"
    )
    undeclared = sorted(enforced - set(ALL_KEYS))
    assert not undeclared, (
        "These permission keys are enforced by a route but are NOT declared in "
        "PERMISSION_CATALOG, so the Roles editor renders no checkbox for them "
        "and no custom role can ever be granted the capability:\n  "
        + "\n  ".join(undeclared)
        + "\n\nAdd each to PERMISSION_CATALOG (and update the counts in "
          "README.md / PROJECT_DOCUMENTATION.md, asserted by "
          "tests/test_docs_integrity.py)."
    )


def test_role_presets_only_reference_real_keys():
    known = set(ALL_KEYS) | {"*"}
    for role, perms in ROLE_PRESETS.items():
        unknown = sorted(set(perms) - known)
        assert not unknown, f"ROLE_PRESETS[{role!r}] references unknown keys: {unknown}"


# ---------------------------------------------------------------------------
# 2. Behavioural escalation tests
# ---------------------------------------------------------------------------
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
            name=f"RBAC probe {role}",
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
    """Mint a bearer token directly -- deliberately NOT via POST /api/auth/login,
    which would set an `access_token` cookie on the shared TestClient and
    silently authenticate the anonymous assertions further down."""
    return {"Authorization": f"Bearer {create_access_token(str(_make_user(role, email)))}"}


@pytest.fixture()
def staff_headers():
    return _headers("staff", "rbac_staff@test.local")


@pytest.fixture()
def manager_headers():
    return _headers("manager", "rbac_manager@test.local")


@pytest.mark.parametrize(
    "method,path,body",
    [
        # Lookups: `settings:lookups` is a Manager key. Staff must not write.
        ("post", "/api/lookups/cities", {"name": "RBAC-Probe-City"}),
        ("post", "/api/lookups/trades", {"name": "RBAC-Probe-Trade"}),
        ("delete", "/api/lookups/cities/999999", None),
        # Depositors: `depositors:view` / `depositors:edit` are Manager keys.
        ("post", "/api/lookups/depositors", {"name": "RBAC-Probe-Depositor"}),
        # Company settings: `settings:company` is an Admin key.
        ("put", "/api/settings/general", {"company_name": "HACKED"}),
        # Financial writes: `receipts:create` is a Manager key. This exact
        # route let a Staff token issue receipt RCP-2026-0001 before the fix.
        ("post", "/api/demands/1/payments", {"amount": 1, "method": "cash"}),
        # Template-wide mutation behind an admin-named but ungated route.
        ("post", "/api/documents/admin/dedupe-all-fields", {}),
    ],
)
def test_staff_cannot_perform_privileged_writes(client, staff_headers, method, path, body):
    kwargs = {"headers": staff_headers}
    if body is not None:
        kwargs["json"] = body
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 403, (
        f"{method.upper()} {path} returned {resp.status_code} for a Staff token "
        f"-- expected 403. Body: {resp.text[:300]}"
    )


def test_staff_can_still_read_the_generic_lookups(client, staff_headers):
    """Deliberate carve-out.

    The candidate and demand forms need trades / cities / embassies to
    populate their dropdowns, Staff legitimately holds `candidates:create`,
    and there is no `lookups:view` key to gate reads with -- so generic
    lookup *reads* stay on plain authentication.  If someone later gates
    them, this test fails and forces the decision to be explicit.
    """
    for kind in ("cities", "trades", "embassies"):
        resp = client.get(f"/api/lookups/{kind}", headers=staff_headers)
        assert resp.status_code == 200, f"/api/lookups/{kind} -> {resp.status_code}"


def test_staff_cannot_list_depositors_but_manager_can(client, staff_headers, manager_headers):
    assert client.get("/api/lookups/depositors", headers=staff_headers).status_code == 403
    assert client.get("/api/lookups/depositors", headers=manager_headers).status_code == 200


def test_staff_cannot_export_the_candidate_register(client, staff_headers, manager_headers):
    """`candidates:export` is bulk data egress -- Manager and above only."""
    assert client.get("/api/candidates/export.csv", headers=staff_headers).status_code == 403
    resp = client.get("/api/candidates/export.csv", headers=manager_headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.text.splitlines()[0].startswith("ID,FULL_NAME")


def test_tenant_admin_routes_are_catalogue_driven_not_role_string_driven(client, manager_headers):
    """`_admin_only()` used to be a bare ``user.role != "admin"`` check, which
    both bypassed the catalogue and refused a super-admin whose role column
    was not literally "admin". It now resolves through ``user_permissions``."""
    assert client.get("/api/tenants", headers=manager_headers).status_code == 403
    assert client.get("/api/tenants/_meta/feature-defaults",
                      headers=manager_headers).status_code == 403


def test_anonymous_requests_are_rejected(client):
    # NOTE: load-bearing. The session-scoped `auth_headers` fixture logs in via
    # POST /api/auth/login, which sets an HttpOnly `access_token` cookie on this
    # shared TestClient (app/core/deps.py accepts it). `_isolate_client_cookies`
    # clears the jar *between* tests, so a login that happens inside this test
    # would silently authenticate the "anonymous" call below.
    client.cookies.clear()
    for path in ("/api/lookups/depositors", "/api/candidates/export.csv",
                 "/api/settings/general", "/api/tenants"):
        assert client.get(path).status_code == 401, f"{path} allowed an anonymous read"
