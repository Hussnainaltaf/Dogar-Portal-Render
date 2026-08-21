"""The document TYPE must be recorded on document audit rows, and filterable.

WHY THIS MODULE EXISTS (measured 2026-08-19)
============================================
The operator asked to find actions "by ... document type". The filter and the
``doc_category`` column were added (alembic a1c6e8b4d072) and the endpoint
accepted the parameter -- but nothing proved the column was ever POPULATED.
Measured on the live DB immediately after the migration:

    GET /api/users/audit-log/_meta/filters  ->  "doc_categories": []

An empty dropdown is indistinguishable from "no documents have been touched",
so the feature looked finished while being unusable. The rows already in the
table were written BEFORE the column existed; every one of them had
``doc_category = NULL`` even though the category was sitting in ``after_json``:

    id 14  action print  doc_category=None  after_json.category='Protector Documents'

That is the exact shape of a half-migrated feature: the data is present in an
unqueryable place, the indexed column is empty, and the UI silently offers
nothing to filter by.

WHY THESE TESTS ARE WRITTEN THE WAY THEY ARE
--------------------------------------------
  * The recording tests assert on the COLUMN of the database row, not on the
    HTTP status and not on ``after_json``. Asserting the 200 would pass with
    ``doc_category=`` deleted from the log call, because the response never
    contained it. Asserting ``after_json['category']`` would pass too -- that
    value was already correct and is precisely why the gap was invisible.
  * The filter tests drive the HTTP endpoint and require that a NON-matching
    category returns FEWER rows than no filter at all. A filter that is
    accepted and ignored returns everything, and "everything" contains the row
    you were looking for, so a naive assertion passes.
  * One test pins the promoted column against ``after_json`` so a future
    refactor cannot quietly go back to reading the JSON blob (rejected as
    non-portable and unindexable -- see the migration).

Guarded by scripts/mutation_check.py batch "denials".
"""
from __future__ import annotations

import pytest

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models import AuditLog, DocumentTemplate, User

# A category string that no shipped template uses, so a matching row can only
# come from the template this module creates.
PROBE_CATEGORY = "ZZ Probe Category"


def _admin_headers_for(email: str = "doccat_admin@test.local") -> dict:
    """A real admin. Minted directly: POST /api/auth/login carries
    @limiter.limit("10/minute") and would rate-limit a full-suite run."""
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        if u is None:
            u = User(
                name="Doc-category probe admin",
                email=email,
                role="admin",
                password_hash=hash_password("Passw0rd!123"),
                is_active=True,
                is_super_admin=True,
            )
            db.add(u)
        else:
            u.role = "admin"
            u.is_active = True
            u.is_super_admin = True
        db.commit()
        db.refresh(u)
        return {"Authorization": f"Bearer {create_access_token(str(u.id))}"}
    finally:
        db.close()


@pytest.fixture()
def admin_hdrs() -> dict:
    return _admin_headers_for()


@pytest.fixture()
def probe_template() -> int:
    """A template whose category is unique to this module."""
    db = SessionLocal()
    try:
        tpl = (
            db.query(DocumentTemplate)
            .filter(DocumentTemplate.name == "Doc-category probe template")
            .first()
        )
        if tpl is None:
            tpl = DocumentTemplate(
                name="Doc-category probe template",
                category=PROBE_CATEGORY,
            )
            db.add(tpl)
        else:
            tpl.category = PROBE_CATEGORY
        db.commit()
        db.refresh(tpl)
        return tpl.id
    finally:
        db.close()


def _rows_for(category: str) -> list[AuditLog]:
    db = SessionLocal()
    try:
        return (
            db.query(AuditLog)
            .filter(AuditLog.doc_category == category)
            .order_by(AuditLog.id.desc())
            .all()
        )
    finally:
        db.close()


def _clear_probe_rows() -> None:
    db = SessionLocal()
    try:
        db.query(AuditLog).filter(AuditLog.doc_category == PROBE_CATEGORY).delete()
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. The category is actually WRITTEN to the indexed column
# ---------------------------------------------------------------------------
def test_a_document_action_records_the_document_category(client, admin_hdrs, probe_template):
    """The COLUMN is the assertion.

    A 200 proves nothing here: the response body never carried the category, so
    the endpoint answered 200 for months while the column stayed NULL.
    """
    _clear_probe_rows()
    r = client.get(f"/api/documents/templates/{probe_template}", headers=admin_hdrs)
    assert r.status_code == 200, r.text

    rows = _rows_for(PROBE_CATEGORY)
    assert rows, (
        "a document action was audited but doc_category was left NULL. The "
        "'document type' filter can only ever offer categories that were "
        "actually recorded, so an unpopulated column makes the filter useless "
        "while the page still looks healthy."
    )


def test_the_recorded_category_is_the_templates_own_category(client, admin_hdrs, probe_template):
    """Not a constant, not the entity type -- the template's category."""
    _clear_probe_rows()
    client.get(f"/api/documents/templates/{probe_template}", headers=admin_hdrs)

    rows = _rows_for(PROBE_CATEGORY)
    assert rows, "no row carried the probe category"
    assert rows[0].doc_category == PROBE_CATEGORY
    assert rows[0].entity_type == "document"


def test_the_promoted_column_agrees_with_the_json_payload(client, admin_hdrs, probe_template):
    """The column must mirror after_json['category'].

    The JSON blob was the ONLY place the category lived before the column
    existed. If these two ever disagree, one of the two readers is lying about
    the same event, and the indexed one is the one the UI trusts.
    """
    _clear_probe_rows()
    client.get(f"/api/documents/templates/{probe_template}", headers=admin_hdrs)

    rows = _rows_for(PROBE_CATEGORY)
    assert rows, "no row carried the probe category"
    payload = rows[0].after_json
    # after_json is a JSON column: SQLAlchemy hands back a dict, not a string.
    assert isinstance(payload, dict), f"after_json was {type(payload).__name__}"
    assert payload.get("category") == rows[0].doc_category, (
        f"column says {rows[0].doc_category!r} but after_json says "
        f"{payload.get('category')!r} for the same event"
    )


def test_a_permission_denial_carries_no_document_category(client, admin_hdrs):
    """NULL must keep meaning "no document type", not "unknown".

    A denial on a non-document route has no document type. Inventing an empty
    string would put a blank entry in the operator's dropdown.
    """
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "doccat_staff@test.local").first()
        if u is None:
            u = User(
                name="Doc-category probe staff",
                email="doccat_staff@test.local",
                role="staff",
                password_hash=hash_password("Passw0rd!123"),
                is_active=True,
                is_super_admin=False,
            )
            db.add(u)
        else:
            u.role, u.is_active, u.is_super_admin = "staff", True, False
        db.commit()
        db.refresh(u)
        staff = {"Authorization": f"Bearer {create_access_token(str(u.id))}"}
    finally:
        db.close()

    r = client.get("/api/users/audit-log", headers=staff)
    assert r.status_code == 403, r.text

    db = SessionLocal()
    try:
        row = (
            db.query(AuditLog)
            .filter(AuditLog.outcome == "denied")
            .order_by(AuditLog.id.desc())
            .first()
        )
    finally:
        db.close()
    assert row is not None, "the denial was not recorded at all"
    assert row.doc_category is None, (
        f"a non-document denial recorded doc_category={row.doc_category!r}; "
        "NULL must mean 'no document type'"
    )


# ---------------------------------------------------------------------------
# 2. The category is FINDABLE through the endpoint the UI calls
# ---------------------------------------------------------------------------
def test_the_doc_category_filter_narrows_the_listing(client, admin_hdrs, probe_template):
    """The comparison against the UNFILTERED total is the point.

    A filter that is accepted and then ignored returns every row -- including
    the one being looked for -- so asserting only "my row is present" passes
    with the WHERE clause deleted.
    """
    _clear_probe_rows()
    client.get(f"/api/documents/templates/{probe_template}", headers=admin_hdrs)

    unfiltered = client.get("/api/users/audit-log?limit=500", headers=admin_hdrs)
    assert unfiltered.status_code == 200, unfiltered.text
    total_all = unfiltered.json()["total"]

    filtered = client.get(
        "/api/users/audit-log", headers=admin_hdrs,
        params={"doc_category": PROBE_CATEGORY, "limit": 500},
    )
    assert filtered.status_code == 200, filtered.text
    body = filtered.json()

    assert body["total"] >= 1, "the row just written was not returned by its own category"
    assert body["total"] < total_all, (
        f"the doc_category filter returned {body['total']} of {total_all} rows -- "
        "it was accepted but not applied"
    )
    assert {i["doc_category"] for i in body["items"]} == {PROBE_CATEGORY}, (
        "the filtered page contained other document types"
    )


def test_an_unmatched_category_returns_an_empty_page_not_everything(client, admin_hdrs, probe_template):
    _clear_probe_rows()
    client.get(f"/api/documents/templates/{probe_template}", headers=admin_hdrs)

    r = client.get(
        "/api/users/audit-log", headers=admin_hdrs,
        params={"doc_category": "No Such Category At All", "limit": 50},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0, (
        "an unmatched document type returned rows; the filter is being ignored"
    )


def test_all_is_no_constraint_for_doc_category(client, admin_hdrs, probe_template):
    """The SPA sends the "all" sentinel rather than omitting the parameter.

    Treating it as a literal value to match would return an empty page and read
    as "no documents have ever been touched".
    """
    client.get(f"/api/documents/templates/{probe_template}", headers=admin_hdrs)

    sentinel = client.get(
        "/api/users/audit-log", headers=admin_hdrs,
        params={"doc_category": "all", "limit": 500},
    )
    omitted = client.get("/api/users/audit-log?limit=500", headers=admin_hdrs)
    assert sentinel.status_code == omitted.status_code == 200
    assert sentinel.json()["total"] == omitted.json()["total"], (
        "doc_category=all was matched literally instead of meaning 'no constraint'"
    )


def test_the_listing_reports_each_rows_document_category(client, admin_hdrs, probe_template):
    """A filter whose result you cannot read is not a working filter."""
    _clear_probe_rows()
    client.get(f"/api/documents/templates/{probe_template}", headers=admin_hdrs)

    r = client.get(
        "/api/users/audit-log", headers=admin_hdrs,
        params={"doc_category": PROBE_CATEGORY, "limit": 10},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items, "no rows returned"
    for it in items:
        assert "doc_category" in it, "the payload omits doc_category"
        assert it["doc_category"] == PROBE_CATEGORY


# ---------------------------------------------------------------------------
# 3. The filter METADATA offers the category, so the operator can pick it
# ---------------------------------------------------------------------------
def test_the_filter_metadata_offers_a_category_that_was_used(client, admin_hdrs, probe_template):
    """This is the assertion that would have caught the empty dropdown."""
    _clear_probe_rows()
    client.get(f"/api/documents/templates/{probe_template}", headers=admin_hdrs)

    r = client.get("/api/users/audit-log/_meta/filters", headers=admin_hdrs)
    assert r.status_code == 200, r.text
    cats = r.json()["doc_categories"]
    assert PROBE_CATEGORY in cats, (
        f"a document of type {PROBE_CATEGORY!r} was audited but the filter "
        f"metadata offers {cats!r}. The operator cannot choose a value the "
        "endpoint never advertises."
    )


def test_the_metadata_does_not_offer_a_blank_category(client, admin_hdrs, probe_template):
    """NULL rows (denials, non-document events) must not become a blank option."""
    client.get(f"/api/documents/templates/{probe_template}", headers=admin_hdrs)
    r = client.get("/api/users/audit-log/_meta/filters", headers=admin_hdrs)
    assert r.status_code == 200, r.text
    cats = r.json()["doc_categories"]
    assert None not in cats, "NULL leaked into the document-type dropdown"
    assert "" not in cats, "an empty string leaked into the document-type dropdown"


def test_document_type_and_outcome_filters_compose(client, admin_hdrs, probe_template):
    """The operator's request was one sentence: denied OR successful actions BY
    office, user, document type AND date range. Combining them must AND, not
    replace one another."""
    _clear_probe_rows()
    client.get(f"/api/documents/templates/{probe_template}", headers=admin_hdrs)

    both = client.get(
        "/api/users/audit-log", headers=admin_hdrs,
        params={"doc_category": PROBE_CATEGORY, "outcome": "success", "limit": 50},
    )
    assert both.status_code == 200, both.text
    assert both.json()["total"] >= 1

    # A document view is a SUCCESS, so asking for denials of that type must be
    # empty -- if outcome were dropped once doc_category was supplied, this
    # would come back non-empty.
    contradiction = client.get(
        "/api/users/audit-log", headers=admin_hdrs,
        params={"doc_category": PROBE_CATEGORY, "outcome": "denied", "limit": 50},
    )
    assert contradiction.status_code == 200, contradiction.text
    assert contradiction.json()["total"] == 0, (
        "doc_category + outcome=denied returned rows; the two filters are not "
        "being ANDed together"
    )


# ---------------------------------------------------------------------------
# 4. A template with NO category must record NULL, never an empty string
#
# MEASURED GAP 2026-08-19 -- why this section exists.
#
# The NM mutation (`doc_category=category or None` -> `doc_category=category
# or ""`) ESCAPED the whole 30-test audit suite. Nothing noticed, because
# every existing test drove a template that HAS a category, and the one test
# that checks for blanks in the dropdown reads
# `/audit-log/_meta/filters`, whose builder is
#
#     [r[0] for r in db.query(AuditLog.doc_category).distinct() if r[0]]
#
# -- the trailing `if r[0]` discards "" as well as None, so the metadata looks
# identical either way. The empty string only becomes visible where it does
# damage:
#
#   * `doc_category=""` in a query string is the endpoint's "no constraint"
#     sentinel, so a row stored as "" can NEVER be selected by the filter it
#     claims to be filterable on -- it is silently unreachable data.
#   * NULL means "this event has no document type" (denials, logins). "" means
#     the same thing with a different value, so two encodings of one fact
#     coexist and `doc_category IS NULL` stops finding half of them.
#
# These tests therefore drive a template whose category is genuinely absent
# and assert on the stored value itself.
# ---------------------------------------------------------------------------
@pytest.fixture()
def uncategorised_template() -> int:
    """A template with NO category at all.

    ``category`` carries default="custom", so leaving the argument out would
    silently give us a categorised template and the test would prove nothing.
    It is set to "" explicitly -- the state a legacy or blanked row is in.
    """
    db = SessionLocal()
    try:
        tpl = (
            db.query(DocumentTemplate)
            .filter(DocumentTemplate.name == "Doc-category blank probe")
            .first()
        )
        if tpl is None:
            tpl = DocumentTemplate(name="Doc-category blank probe", category="")
            db.add(tpl)
        else:
            tpl.category = ""
        db.commit()
        db.refresh(tpl)
        assert not tpl.category, "fixture failed to produce an uncategorised template"
        return tpl.id
    finally:
        db.close()


def _latest_row_for_template(tpl_id: int) -> AuditLog | None:
    db = SessionLocal()
    try:
        return (
            db.query(AuditLog)
            .filter(AuditLog.entity_type == "document", AuditLog.entity_id == tpl_id)
            .order_by(AuditLog.id.desc())
            .first()
        )
    finally:
        db.close()


def test_an_uncategorised_document_records_null_not_an_empty_string(
    client, admin_hdrs, uncategorised_template
):
    """The exact defect NM introduced.

    `is None` -- not falsiness. `assert not row.doc_category` would pass for
    both NULL and "", which is precisely the distinction under test.
    """
    r = client.get(
        f"/api/documents/templates/{uncategorised_template}", headers=admin_hdrs
    )
    assert r.status_code == 200, r.text

    row = _latest_row_for_template(uncategorised_template)
    assert row is not None, "the document action was not audited at all"
    assert row.doc_category is None, (
        f"an uncategorised document recorded doc_category={row.doc_category!r}. "
        "An empty string is not NULL: '' is the endpoint's own \"no constraint\" "
        "sentinel, so this row can never be reached by the document-type "
        "filter, and `doc_category IS NULL` no longer finds every event that "
        "has no document type."
    )


def test_no_audit_row_anywhere_stores_a_blank_document_category(
    client, admin_hdrs, uncategorised_template, probe_template
):
    """Whole-column invariant, not just the row we happened to write.

    Checked across the entire trail so any other writer that starts inventing
    "" is caught here too, not only the call site NM mutates.
    """
    client.get(f"/api/documents/templates/{uncategorised_template}", headers=admin_hdrs)
    client.get(f"/api/documents/templates/{probe_template}", headers=admin_hdrs)

    db = SessionLocal()
    try:
        blanks = db.query(AuditLog).filter(AuditLog.doc_category == "").count()
    finally:
        db.close()
    assert blanks == 0, (
        f"{blanks} audit row(s) store doc_category='' -- two encodings of "
        "\"no document type\" now coexist in one indexed column"
    )


def test_a_null_category_row_is_still_reachable_by_the_is_null_query(
    client, admin_hdrs, uncategorised_template
):
    """NULL has to stay queryable as a group.

    This is the operator-facing consequence: "show me events with no document
    type". With "" stored instead, the row drops out of this set while also
    being unreachable via `doc_category=`, i.e. reachable by nothing.
    """
    client.get(f"/api/documents/templates/{uncategorised_template}", headers=admin_hdrs)

    db = SessionLocal()
    try:
        found = (
            db.query(AuditLog)
            .filter(
                AuditLog.entity_id == uncategorised_template,
                AuditLog.entity_type == "document",
                AuditLog.doc_category.is_(None),
            )
            .count()
        )
    finally:
        db.close()
    assert found >= 1, (
        "the uncategorised document's audit row is not matched by "
        "`doc_category IS NULL`, so it belongs to no document-type group at all"
    )
