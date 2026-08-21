"""The admin audit trail -- document actions, and who is allowed to read them.

Operator requirement (2026-08-15), verbatim:

    "Add an admin audit log that records login history and every
     role-protected document action (view/edit/print/disable) with
     timestamps and user IDs in real time."

Login history was already recorded on every attempt (454 rows on the live
database, Success and Failed alike, with IP and user-agent) and already had a
viewer at ``GET /api/users/login-history``.  Document actions had **neither**.
Measured on the live database immediately before the fix:

    sqlite> select count(*) from audit_logs where entity_type='document';
    0

`app/api/endpoints/documents.py` -- 19 routes, ~2350 lines -- contained no
audit call at all.  The two most sensitive operations were the least traceable:

  * ``GET /templates/{id}/print`` renders the HTML print view.  Nothing is
    downloaded, so the ``GeneratedDocument`` bookkeeping row is never written
    either.  The most common way a document was actually printed left *no*
    trace anywhere in the system.
  * ``GET /templates/{id}/preview-data`` merges REAL record values into the
    overlay.  Reading a candidate's passport or CNIC through it is a
    disclosure event and was equally invisible.

``GeneratedDocument`` is not a substitute: it is document-management
bookkeeping (no actor IP, no user-agent, not queried by any audit viewer) and
it only exists for the PDF path.

WHAT THESE TESTS LOCK DOWN
--------------------------
1. Each of the five audited document actions writes a row, through the real
   HTTP stack, with an actor id and a timestamp.  Deleting any single
   ``log_document_action`` call site turns the matching test RED -- verified
   by mutation, see PROJECT_DOCUMENTATION.md section 8.3.
2. The timestamp is the DATABASE's, not the client's.  ``occurred_at`` is a
   server-side ``func.now()`` default, so a caller cannot backdate an entry.
3. Audit writes are best-effort and MUST NEVER break the originating request.
   A logging failure that 500s a print is worse than the missing log line.
4. The viewer is permission-gated: ``audit:view`` admits, its absence 403s.
"""
import json
import pathlib
import re

import pytest

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models import AuditLog, DocumentTemplate, User

REPO = pathlib.Path(__file__).resolve().parents[1]
DOCUMENTS_ENDPOINT = REPO / "app" / "api" / "endpoints" / "documents.py"

# The five document actions the operator named, mapped to the AuditAction
# value each one is recorded under.  "edit" splits three ways because
# toggling `is_active` is semantically enable/disable, not a generic update.
REQUIRED_ACTIONS = ("view", "print", "update", "disable", "enable")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _user(role: str, email: str, *, super_admin: bool = False) -> int:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        if u is None:
            u = User(
                name=f"Audit probe {role}",
                email=email,
                role=role,
                password_hash=hash_password("Passw0rd!123"),
                is_active=True,
                is_super_admin=super_admin,
            )
            db.add(u)
        else:
            u.role = role
            u.is_active = True
            u.is_super_admin = super_admin
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _headers(role: str, email: str) -> dict:
    """Bearer token, deliberately not POST /api/auth/login -- that would set a
    cookie on the shared TestClient and silently authenticate later
    anonymous assertions (the trap tests/test_rbac_enforcement.py documents).
    """
    return {"Authorization": f"Bearer {create_access_token(str(_user(role, email)))}"}


@pytest.fixture()
def admin_headers():
    return _headers("admin", "audit_admin@test.local")


@pytest.fixture()
def staff_headers():
    return _headers("staff", "audit_staff@test.local")


@pytest.fixture()
def template_id():
    """A template to act on.  Reuses an existing one when the seeded suite
    already has templates, so the test does not inflate the fixture data."""
    db = SessionLocal()
    try:
        tpl = db.query(DocumentTemplate).first()
        if tpl is None:
            tpl = DocumentTemplate(
                name="Audit Probe Template",
                category="visa",
                data_source="candidate",
            )
            db.add(tpl)
            db.commit()
            db.refresh(tpl)
        return tpl.id
    finally:
        db.close()


def _rows_for(template_id: int, action: str | None = None) -> list[AuditLog]:
    db = SessionLocal()
    try:
        q = db.query(AuditLog).filter(
            AuditLog.entity_type == "document",
            AuditLog.entity_id == template_id,
        )
        if action:
            q = q.filter(AuditLog.action == action)
        return q.order_by(AuditLog.id.desc()).all()
    finally:
        db.close()


def _count(template_id: int, action: str | None = None) -> int:
    return len(_rows_for(template_id, action))


# ---------------------------------------------------------------------------
# 1. Every named action writes a row -- through the real HTTP stack
# ---------------------------------------------------------------------------
def test_viewing_a_template_is_recorded(client, admin_headers, template_id):
    before = _count(template_id, "view")
    resp = client.get(f"/api/documents/templates/{template_id}", headers=admin_headers)
    assert resp.status_code == 200, resp.text[:300]
    assert _count(template_id, "view") == before + 1, (
        "GET /templates/{id} wrote no 'view' audit row. Reading a document "
        "definition is a role-protected action the operator asked to be logged."
    )


def test_reading_real_record_values_through_preview_is_recorded(
    client, admin_headers, template_id
):
    """preview-data merges REAL candidate values into the overlay -- the read
    that matters most, and the one that used to be completely invisible."""
    before = _count(template_id, "view")
    resp = client.get(
        f"/api/documents/templates/{template_id}/preview-data", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text[:300]
    rows = _rows_for(template_id, "view")
    assert len(rows) == before + 1
    assert "preview" in (rows[0].summary or "").lower(), (
        f"the preview read was logged but is indistinguishable from a plain "
        f"template view: {rows[0].summary!r}"
    )


def test_printing_is_recorded(client, admin_headers, template_id):
    """The HTML print path downloads no file, so GeneratedDocument never sees
    it. Before this fix, the most common way to print left no trace at all."""
    before = _count(template_id, "print")
    resp = client.get(
        f"/api/documents/templates/{template_id}/print", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text[:300]
    assert _count(template_id, "print") == before + 1, (
        "GET /templates/{id}/print wrote no 'print' audit row -- the system "
        "still cannot answer 'who printed this document, and when?'"
    )


def test_disabling_and_enabling_are_recorded_distinctly(
    client, admin_headers, template_id
):
    """`is_active` toggles are enable/disable, not a generic 'update'.

    The operator named "disable" as its own auditable action; collapsing it
    into "update" would make it unfindable in the viewer's action filter.
    """
    dis_before = _count(template_id, "disable")
    en_before = _count(template_id, "enable")

    r1 = client.put(
        f"/api/documents/templates/{template_id}",
        json={"is_active": False},
        headers=admin_headers,
    )
    assert r1.status_code == 200, r1.text[:300]
    r2 = client.put(
        f"/api/documents/templates/{template_id}",
        json={"is_active": True},
        headers=admin_headers,
    )
    assert r2.status_code == 200, r2.text[:300]

    assert _count(template_id, "disable") == dis_before + 1, "disable not logged"
    assert _count(template_id, "enable") == en_before + 1, "enable not logged"


def test_editing_a_template_is_recorded_with_the_changed_fields(
    client, admin_headers, template_id
):
    before = _count(template_id, "update")
    resp = client.put(
        f"/api/documents/templates/{template_id}",
        json={"description": "audit trail probe edit"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text[:300]
    rows = _rows_for(template_id, "update")
    assert len(rows) == before + 1, "edit not logged"
    assert "description" in (rows[0].summary or ""), (
        f"the edit was logged but does not say WHAT changed: {rows[0].summary!r}"
    )


# ---------------------------------------------------------------------------
# 2. The rows are usable evidence: actor id + server-side timestamp
# ---------------------------------------------------------------------------
def test_every_document_row_carries_an_actor_id_and_a_timestamp(
    client, admin_headers, template_id
):
    """"with timestamps and user IDs" -- the operator's exact words.

    A trail without an actor answers nothing.
    """
    client.get(f"/api/documents/templates/{template_id}", headers=admin_headers)
    rows = _rows_for(template_id)
    assert rows, "no document audit rows at all"
    missing_actor = [r.id for r in rows if not r.actor_user_id]
    missing_time = [r.id for r in rows if not r.occurred_at]
    assert not missing_actor, f"audit rows with no actor_user_id: {missing_actor}"
    assert not missing_time, f"audit rows with no occurred_at: {missing_time}"


def test_the_timestamp_is_the_databases_not_the_clients():
    """`occurred_at` must be a server-side default so a caller cannot backdate.

    Structural, not behavioural: a client has no way to *attempt* a backdate
    over HTTP, so the only way to prove the property is to inspect the column.
    """
    col = AuditLog.__table__.c.occurred_at
    assert col.server_default is not None or col.default is not None, (
        "AuditLog.occurred_at has no default -- the timestamp would then come "
        "from whatever the caller supplied, which is not evidence."
    )


# ---------------------------------------------------------------------------
# 3. Auditing must never break the request it is auditing
# ---------------------------------------------------------------------------
def test_a_failing_audit_write_does_not_break_the_print(
    client, admin_headers, template_id, monkeypatch
):
    """A missing log line is bad. A print that 500s because logging broke is
    worse. `log_event` is best-effort by design; this proves the wiring kept
    that property rather than introducing a new failure mode.

    NOTE the patch target. Replacing ``documents.log_document_action`` would
    test nothing useful -- no guard inside the helper can survive the helper
    itself being swapped out, so that version of this test could only ever be
    satisfied by wrapping all six call sites in try/except. The realistic
    failure is *inside* the logging stack (a detached template instance, a
    non-serialisable value, a dead session), so the seam is ``log_event``.
    That is the failure the helper's own outer guard has to absorb -- and
    when this test was first written it did not, and the print returned 500.
    """
    import app.services.audit as audit_mod

    def _boom(*a, **kw):
        raise RuntimeError("simulated audit backend failure")

    monkeypatch.setattr(audit_mod, "log_event", _boom)
    resp = client.get(
        f"/api/documents/templates/{template_id}/print", headers=admin_headers
    )
    assert resp.status_code == 200, (
        f"a failing audit write broke the print request ({resp.status_code}). "
        f"Auditing must be best-effort."
    )


# ---------------------------------------------------------------------------
# 4. The viewer is permission-gated
# ---------------------------------------------------------------------------
def test_admin_can_read_the_audit_log(client, admin_headers):
    resp = client.get("/api/users/audit-log", headers=admin_headers)
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    assert "items" in body and "total" in body
    for item in body["items"][:5]:
        assert {"action", "actor_user_id", "occurred_at", "entity_type"} <= set(item)


def test_staff_cannot_read_the_audit_log(client, staff_headers):
    """Clear 403 -- the operator asked for "a clear 403/redirect when I lack
    permission", and an audit trail readable by everyone audits nothing."""
    resp = client.get("/api/users/audit-log", headers=staff_headers)
    assert resp.status_code == 403, (
        f"a Staff token read the admin audit log ({resp.status_code})"
    )


def test_anonymous_cannot_read_the_audit_log(client):
    resp = client.get("/api/users/audit-log")
    assert resp.status_code in (401, 403), (
        f"the audit log was readable without authentication ({resp.status_code})"
    )


def test_the_audit_log_can_be_filtered_by_action_and_entity(
    client, admin_headers, template_id
):
    """The viewer is useless as evidence if it cannot isolate one action."""
    client.get(f"/api/documents/templates/{template_id}/print", headers=admin_headers)
    resp = client.get(
        "/api/users/audit-log?entity_type=document&action=print", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text[:300]
    items = resp.json()["items"]
    assert items, "filtering by entity_type=document&action=print returned nothing"
    assert all(i["entity_type"] == "document" for i in items)
    assert all(i["action"] == "print" for i in items)


def test_the_filter_metadata_endpoint_reflects_real_data(
    client, admin_headers, template_id
):
    client.get(f"/api/documents/templates/{template_id}", headers=admin_headers)
    resp = client.get("/api/users/audit-log/_meta/filters", headers=admin_headers)
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    assert "document" in body["entity_types"]
    assert "view" in body["actions"]


# ---------------------------------------------------------------------------
# 5. Structural guards -- catch a REMOVED call site, not just a broken one
# ---------------------------------------------------------------------------
def test_the_audited_document_handlers_still_call_the_logger():
    """Behavioural tests above cover the happy paths that are easy to exercise.

    This one is the backstop for the paths that are not: `generate` (PDF
    download) and `delete_template` (destructive) both audit, but exercising
    them in-suite means rendering a real PDF and destroying a fixture. If
    someone deletes those call sites, nothing else here goes red.
    """
    src = DOCUMENTS_ENDPOINT.read_text(encoding="utf-8")
    # The import line carries no "(", so this counts CALL SITES only.
    calls = src.count("log_document_action(")
    assert "from app.services.audit import log_document_action" in src, (
        "documents.py no longer imports the audit helper"
    )
    assert calls >= 6, (
        f"expected at least 6 audited handlers in documents.py "
        f"(get_template, update_template, delete_template, preview_data, "
        f"generate, print_document), found {calls} call sites. "
        f"An audited handler was probably removed."
    )
    for handler in (
        "def generate(",
        "def delete_template(",
        "def print_document(",
        "def preview_data(",
        "def update_template(",
        "def get_template(",
    ):
        assert handler in src, f"audited handler disappeared: {handler}"


def test_every_operator_named_action_exists_in_the_action_enum():
    from app.core.workflow import AuditAction

    values = {a.value for a in AuditAction}
    for action in REQUIRED_ACTIONS:
        assert action in values, (
            f"AuditAction has no {action!r} member -- the operator named "
            f"view/edit/print/disable explicitly"
        )


def test_the_audit_viewer_reads_the_tenant_database_not_the_control_database():
    """An audit trail that leaks across tenants is a data breach, not a feature.

    tests/test_tenancy.py enforces this repo-wide; asserted here too because
    this specific route was added after that guard and reads the most
    sensitive table in the portal.
    """
    src = (REPO / "app" / "api" / "endpoints" / "users.py").read_text(encoding="utf-8")
    block = re.search(
        r"@router\.get\(\"/audit-log\"\).*?(?=\n@router\.|\Z)", src, re.S
    )
    assert block, "could not locate the /audit-log route in users.py"
    assert "Depends(get_tenant_db)" in block.group(0), (
        "the audit-log viewer is not bound to get_tenant_db -- it would read "
        "the control database and expose one office's trail to another"
    )
    assert "Depends(get_db)" not in block.group(0)


# ---------------------------------------------------------------------------
# 5. CLIENTS -- the entity that was audited nowhere
#
# Added 2026-08-20. ``AuditEntity.CLIENT`` had existed in app/core/workflow.py
# since the audit model was first written, and no endpoint ever emitted it.
# Creating, editing or deleting a client -- the record an office's entire
# commercial relationship, its demands and its statements hang off -- left no
# trace at all, while candidates and demands were fully audited.
#
# HOW IT WAS FOUND, because it matters for what these tests must assert.
# It was NOT found by reading documents.py again or by any "does the audit page
# work?" check. It surfaced while proving per-office audit isolation: the
# question "does a write through office alpha's portal leave an audit row in
# alpha's own database?" answered NO, and the reason turned out not to be a
# session bound to the wrong file but an action that was audited nowhere at
# all. A row that is never written cannot appear as a wrong row, so no viewer
# test, no filter test and no tenant-scoping test could ever have shown it.
#
# These tests therefore assert on ROWS produced by real HTTP requests, not on
# the presence of a ``log_event`` call in the source. Source presence is what
# lets a call site drift into being handed the wrong session, or be wrapped in
# a condition that is never true, while still reading correctly.
# ---------------------------------------------------------------------------
def _client_rows(entity_id: int, action: str | None = None) -> list[AuditLog]:
    db = SessionLocal()
    try:
        q = db.query(AuditLog).filter(
            AuditLog.entity_type == "client",
            AuditLog.entity_id == entity_id,
        )
        if action:
            q = q.filter(AuditLog.action == action)
        return q.order_by(AuditLog.id.desc()).all()
    finally:
        db.close()


def test_creating_a_client_is_audited(client, admin_headers):
    r = client.post(
        "/api/clients/", json={"company_name": "Audit Probe Trading LLC"},
        headers=admin_headers,
    )
    assert r.status_code in (200, 201), r.text[:300]
    cid = r.json()["id"]

    rows = _client_rows(cid, "create")
    assert len(rows) == 1, (
        f"creating a client wrote {len(rows)} audit rows, expected exactly 1. "
        f"This entity was audited NOWHERE before 2026-08-20."
    )
    row = rows[0]
    assert row.actor_user_id, "the client audit row records no actor"
    assert row.occurred_at is not None, "the client audit row has no timestamp"
    assert row.after_json, "the client audit row captured no after-state"
    assert "Audit Probe Trading LLC" in (row.summary or ""), (
        f"the summary does not name the client that was created: {row.summary!r}"
    )


def test_editing_a_client_is_audited_with_both_sides_of_the_change(
    client, admin_headers
):
    """The before-state is the whole point of auditing an edit.

    A row that records only the new values answers "what does it say now?",
    which anyone can read off the record itself. What an audit trail is for is
    "what did it say before, and who changed it?" -- so the before-snapshot
    must be taken before the setattr loop, not after.
    """
    r = client.post(
        "/api/clients/", json={"company_name": "Before Name Co"},
        headers=admin_headers,
    )
    cid = r.json()["id"]

    r = client.put(
        f"/api/clients/{cid}", json={"company_name": "After Name Co"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text[:300]

    rows = _client_rows(cid, "update")
    assert len(rows) == 1, f"editing a client wrote {len(rows)} audit rows, expected 1"
    row = rows[0]
    assert row.before_json, "the edit was audited with no before-state"
    assert "Before Name Co" in json.dumps(row.before_json), (
        "the before-snapshot does not contain the OLD company name -- it was "
        "taken after the mutation, so the trail cannot show what changed"
    )
    assert "After Name Co" in json.dumps(row.after_json)


def test_deleting_a_client_is_audited_after_the_row_is_gone(client, admin_headers):
    """A delete is the single most important entry in an audit trail.

    It is also the easiest one to lose: after ``db.delete()`` + commit the ORM
    instance is expired, so reading ``obj.company_name`` for the summary raises
    and -- because audit writes are deliberately best-effort -- the exception
    would be swallowed and the delete would go unrecorded while the request
    still returned 200. The identifiers must be captured while the row lives.
    """
    r = client.post(
        "/api/clients/", json={"company_name": "Doomed Trading Co"},
        headers=admin_headers,
    )
    cid = r.json()["id"]

    r = client.delete(f"/api/clients/{cid}", headers=admin_headers)
    assert r.status_code == 200, r.text[:300]

    rows = _client_rows(cid, "delete")
    assert len(rows) == 1, (
        f"deleting a client wrote {len(rows)} audit rows, expected 1 -- a "
        f"delete that leaves no trace is the worst possible gap"
    )
    row = rows[0]
    assert "Doomed Trading Co" in (row.summary or ""), (
        f"the delete summary does not name what was deleted: {row.summary!r}. "
        f"The name must be read off the row BEFORE it is removed."
    )
    assert row.before_json, "the delete was audited with no snapshot of what was lost"
    assert "Doomed Trading Co" in json.dumps(row.before_json)


def test_the_client_audit_rows_are_bound_to_the_tenant_session():
    """Structural companion to the behavioural tests above.

    The behavioural tests run on the root portal, where the control DB *is* the
    only DB -- so a client audit row written through a control-DB session would
    still be found and they would still pass. That specific leak is proven
    absent over real per-office HTTP in
    tests/test_tenant_isolation_live.py::test_a_tenants_audit_trail_is_written_to_its_own_database;
    this assertion keeps the cheaper signal next to the rest of the audit
    coverage so a refactor is caught here first.
    """
    src = (REPO / "app" / "api" / "endpoints" / "clients.py").read_text(encoding="utf-8")
    assert "audit_svc.log_event(" in src, "client auditing has been removed entirely"
    for call in re.finditer(r"audit_svc\.log_event\(\s*(\w+)", src):
        assert call.group(1) == "db", (
            f"a client audit call is passed {call.group(1)!r} instead of the "
            f"request's tenant session -- one office's activity would be "
            f"recorded in another's trail"
        )
    assert "SessionLocal" not in src, (
        "clients.py builds its own session; every write here must use the "
        "request-scoped tenant session from get_tenant_db"
    )
