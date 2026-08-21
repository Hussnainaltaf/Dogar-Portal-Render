"""Per-capability route guards on the document engine.

Operator requirement (2026-08-15), verbatim:

    "Verify and harden role-based access by enforcing route guards for view,
     edit, disable, and print actions per document type, returning a clear
     403/redirect when I lack permission."

WHY A SEPARATE MODULE FROM test_rbac_enforcement.py
---------------------------------------------------
That module answers "is this key enforced *anywhere*?" -- a corpus grep, plus
a handful of behavioural escalation probes on lookups / settings / receipts.
It does not answer the question the operator actually asked, which is
per-capability and per-route:

    Does holding `documents:view` -- and only that -- let me edit a template?
    Does holding `documents:print` let me run the designer?
    Does a Staff token get a *clear 403*, or a 200 with empty data, or a 500?

The distinction matters because all four document keys resolve through the
same ``require_permission`` factory, so a copy-paste slip (guarding an edit
route with ``documents:view``) is invisible to a corpus grep: both keys are
"enforced somewhere". Only a matrix that exercises each route with a token
that deliberately lacks the specific key can catch it.

THE MATRIX
----------
Every route is probed with a role that holds *neither* the required key nor
the wildcard, and must return exactly 403 -- never 200, never 401 (which
would imply the request was rejected as unauthenticated rather than
unauthorised), never 500 (a guard that crashes is not a guard).

Roles used, from ROLE_PRESETS:
  * staff   -> documents:view, documents:print      (no edit, no designer)
  * manager -> all four document keys               (positive control)
  * a custom role with an EMPTY permission list     (holds nothing at all)

The empty-permission role is the important one. Staff happens to hold view
and print, so Staff alone cannot prove that the *view* and *print* guards
work -- it would pass them legitimately. Without a zero-permission subject,
those two capabilities would be entirely untested, which is exactly the kind
of gap that makes a green suite meaningless.
"""
import json

import pytest

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models import DocumentTemplate, User
from app.models.lookups import Role

# (method, path_template, required_key)
# path_template takes {tpl} / {field}. Bodies are supplied per-method below.
DOCUMENT_ROUTES = [
    # ---- view -------------------------------------------------------------
    ("get",    "/api/documents/templates",                      "documents:view"),
    ("get",    "/api/documents/templates/{tpl}",                "documents:view"),
    ("get",    "/api/documents/templates/{tpl}/fields",         "documents:view"),
    ("get",    "/api/documents/templates/{tpl}/preview-data",   "documents:view"),
    ("get",    "/api/documents/available-fields",               "documents:view"),
    ("get",    "/api/documents/designer/palette",               "documents:view"),
    ("get",    "/api/documents/designer/backgrounds",           "documents:view"),
    # ---- edit (includes disable: is_active is toggled through PUT) --------
    ("post",   "/api/documents/templates",                      "documents:edit"),
    ("put",    "/api/documents/templates/{tpl}",                "documents:edit"),
    ("delete", "/api/documents/templates/{tpl}",                "documents:edit"),
    ("post",   "/api/documents/templates/{tpl}/fields",         "documents:edit"),
    ("post",   "/api/documents/templates/{tpl}/fields/bulk",    "documents:edit"),
    ("put",    "/api/documents/fields/{field}",                 "documents:edit"),
    ("delete", "/api/documents/fields/{field}",                 "documents:edit"),
    # ---- designer ---------------------------------------------------------
    ("post",   "/api/documents/templates/{tpl}/fields/dedupe",  "documents:designer"),
    ("post",   "/api/documents/admin/dedupe-all-fields",        "documents:designer"),
    # ---- print ------------------------------------------------------------
    ("get",    "/api/documents/templates/{tpl}/generate",       "documents:print"),
    ("get",    "/api/documents/templates/{tpl}/print",          "documents:print"),
]

BODIES = {
    "post": {"name": "RBAC probe", "category": "visa", "data_source": "candidate"},
    "put": {"description": "RBAC probe"},
}

NOBODY_ROLE = "rbacnobody"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _zero_permission_role():
    """A real Role row carrying an EMPTY permission list.

    ``user_permissions`` falls back to ROLE_PRESETS when no Role row matches,
    and to ``set()`` only when both miss -- so an unknown role name would also
    yield "no permissions". A persisted empty role is the honest subject: it
    is what an operator actually creates in the Roles editor when they revoke
    everything, and it exercises the ``_parse_perms`` path rather than the
    fallback.
    """
    db = SessionLocal()
    try:
        row = db.query(Role).filter(Role.name.ilike(NOBODY_ROLE)).first()
        if row is None:
            row = Role(name=NOBODY_ROLE, permissions=json.dumps([]))
            db.add(row)
        else:
            row.permissions = json.dumps([])
        db.commit()
    finally:
        db.close()
    yield


def _user(role: str, email: str) -> int:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        if u is None:
            u = User(
                name=f"Docguard {role}",
                email=email,
                role=role,
                password_hash=hash_password("Passw0rd!123"),
                is_active=True,
                is_super_admin=False,
            )
            db.add(u)
        else:
            u.role = role
            u.is_active = True
            u.is_super_admin = False
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _headers(role: str, email: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(_user(role, email)))}"}


@pytest.fixture()
def nobody_headers():
    return _headers(NOBODY_ROLE, "docguard_nobody@test.local")


@pytest.fixture()
def staff_headers():
    return _headers("staff", "docguard_staff@test.local")


@pytest.fixture()
def manager_headers():
    return _headers("manager", "docguard_manager@test.local")


@pytest.fixture(scope="module")
def ids():
    """A template plus one of its fields to aim the probes at."""
    db = SessionLocal()
    try:
        tpl = db.query(DocumentTemplate).first()
        if tpl is None:
            tpl = DocumentTemplate(
                name="Docguard Template", category="visa", data_source="candidate"
            )
            db.add(tpl)
            db.commit()
            db.refresh(tpl)
        field = tpl.fields[0] if tpl.fields else None
        return {"tpl": tpl.id, "field": field.id if field else 999999}
    finally:
        db.close()


def _call(client, method, path, headers):
    kwargs = {"headers": headers}
    if method in BODIES:
        kwargs["json"] = BODIES[method]
    return getattr(client, method)(path, **kwargs)


# ---------------------------------------------------------------------------
# 1. A token holding NOTHING is refused on every document route
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method,path,key", DOCUMENT_ROUTES)
def test_a_role_with_no_permissions_is_refused(
    client, nobody_headers, ids, method, path, key
):
    resp = _call(client, method, path.format(**ids), nobody_headers)
    assert resp.status_code == 403, (
        f"{method.upper()} {path} returned {resp.status_code} for a role holding "
        f"NO permissions -- expected 403 (guard: {key}). Body: {resp.text[:200]}"
    )


# ---------------------------------------------------------------------------
# 2. Holding view+print does NOT confer edit or designer
# ---------------------------------------------------------------------------
STAFF_MUST_BE_DENIED = [
    (m, p, k) for (m, p, k) in DOCUMENT_ROUTES
    if k in ("documents:edit", "documents:designer")
]


@pytest.mark.parametrize("method,path,key", STAFF_MUST_BE_DENIED)
def test_view_and_print_do_not_confer_edit_or_designer(
    client, staff_headers, ids, method, path, key
):
    """The copy-paste slip this catches: guarding an edit route with
    ``documents:view``. Staff holds view, so such a route would answer 200 and
    a corpus grep would still call the key "enforced"."""
    resp = _call(client, method, path.format(**ids), staff_headers)
    assert resp.status_code == 403, (
        f"{method.upper()} {path} returned {resp.status_code} for a Staff token "
        f"(holds documents:view + documents:print, NOT {key})."
        f" Body: {resp.text[:200]}"
    )


# ---------------------------------------------------------------------------
# 3. Positive control -- the guards are not simply refusing everyone
# ---------------------------------------------------------------------------
READ_ROUTES = [(m, p, k) for (m, p, k) in DOCUMENT_ROUTES if m == "get"]


@pytest.mark.parametrize("method,path,key", READ_ROUTES)
def test_a_manager_holding_the_key_is_admitted(
    client, manager_headers, ids, method, path, key
):
    """Without this, a guard that returns 403 unconditionally would pass every
    assertion above while breaking the portal for legitimate users."""
    resp = _call(client, method, path.format(**ids), manager_headers)
    assert resp.status_code == 200, (
        f"{method.upper()} {path} returned {resp.status_code} for a Manager who "
        f"DOES hold {key} -- the guard is over-refusing. Body: {resp.text[:200]}"
    )


# ---------------------------------------------------------------------------
# 4. The refusal is a clear 403, not a 401 and not a crash
# ---------------------------------------------------------------------------
def test_refusals_are_403_not_401_and_not_500(client, nobody_headers, ids):
    """"returning a clear 403/redirect when I lack permission".

    401 would tell the SPA "your session expired" and bounce the user to the
    login screen -- they would log in again, succeed, and land back on the
    same refusal, with no indication that the real answer is "your role does
    not allow this". 500 would mean the guard itself crashed.
    """
    bad = []
    for method, path, key in DOCUMENT_ROUTES:
        resp = _call(client, method, path.format(**ids), nobody_headers)
        if resp.status_code != 403:
            bad.append(f"{method.upper()} {path} -> {resp.status_code} ({key})")
    assert not bad, "routes that did not answer a clean 403:\n  " + "\n  ".join(bad)


def test_an_anonymous_request_is_rejected(client, ids):
    """No token at all must not reach the handler either."""
    bad = []
    for method, path, _key in DOCUMENT_ROUTES:
        resp = _call(client, method, path.format(**ids), {})
        if resp.status_code not in (401, 403):
            bad.append(f"{method.upper()} {path} -> {resp.status_code}")
    assert not bad, "document routes reachable anonymously:\n  " + "\n  ".join(bad)


# ---------------------------------------------------------------------------
# 5. Disable is genuinely gated -- it is an edit, not a view
# ---------------------------------------------------------------------------
def test_disabling_a_template_requires_edit_not_view(client, staff_headers, ids):
    """The operator named "disable" as one of the four protected actions.

    It has no route of its own -- it is ``PUT {"is_active": false}`` -- so it
    is only as protected as the PUT. Asserted explicitly so that if the toggle
    is ever split onto its own endpoint, the guard has to be decided again
    rather than defaulting to open.
    """
    resp = client.put(
        f"/api/documents/templates/{ids['tpl']}",
        json={"is_active": False},
        headers=staff_headers,
    )
    assert resp.status_code == 403, (
        f"a Staff token disabled a document template ({resp.status_code}) -- "
        f"disable must require documents:edit"
    )

    db = SessionLocal()
    try:
        tpl = db.query(DocumentTemplate).get(ids["tpl"])
        assert tpl.is_active is not False, (
            "the template was actually disabled despite the refusal -- the "
            "guard ran after the mutation"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 6. Structural: no document route may be left ungated
# ---------------------------------------------------------------------------
def test_every_document_route_declares_a_permission_guard():
    """A new route added to documents.py without a guard is the failure mode
    the matrix above cannot see -- it only probes routes it already knows.
    """
    import pathlib
    import re

    src = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "api" / "endpoints" / "documents.py"
    ).read_text(encoding="utf-8")

    # Each @router.<verb> decorator plus the signature that follows it.
    blocks = re.findall(
        r'@router\.(get|post|put|delete|patch)\("([^"]+)"[^\n]*\n(.*?)(?=\n(?:@router\.|def |\Z))',
        src,
        re.S,
    )
    assert len(blocks) >= 15, (
        f"only {len(blocks)} routes parsed out of documents.py -- the regex is "
        f"stale, which would make this test vacuous"
    )
    ungated = [
        f"{verb.upper()} {path}"
        for verb, path, body in blocks
        if "require_permission(" not in body and "assert_permission(" not in body
    ]
    assert not ungated, (
        "document routes with no permission guard in their signature:\n  "
        + "\n  ".join(ungated)
        + "\n\nEvery document route must declare require_permission(...) with "
          "the capability it implements (view / edit / designer / print)."
    )
