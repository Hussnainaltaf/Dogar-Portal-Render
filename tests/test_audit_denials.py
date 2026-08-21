"""Permission denials must land in the audit trail, and be findable.

WHY THIS MODULE EXISTS (measured 2026-08-18)
============================================
The operator asked to "find denied or successful actions" on the Audit Log
page. That was impossible -- and the reason was NOT a missing filter. The
denials were never recorded at all:

    $ grep -rn "denied" app/services/audit.py
    (no hits)

``require_permission`` and ``assert_permission`` in app/core/permissions.py
raised HTTP 403 and returned. Nothing wrote a row. So a user probing for
documents:print, candidates:delete or audit:view left NO trace whatsoever, and
the trail contained only actions that had SUCCEEDED -- the half of the story
that matters least to a security review.

Every test below is written so that removing the protection it describes makes
it FAIL, per the project's verification rule. Specifically:

  * The recording tests assert on the DATABASE ROW, not on the 403. Asserting
    the 403 would pass even with the audit call deleted, since the 403 was
    always correct -- that is exactly how this gap survived.
  * The "findable" tests go through the HTTP endpoint with the filters the UI
    sends, so a filter that is accepted-but-ignored is caught.
  * The fail-open tests prove a broken audit write cannot turn a correct 403
    into a 500, because an observability feature must not invent a new failure.

Guarded by scripts/mutation_check.py batch "denials".
"""
from __future__ import annotations

import uuid

import pytest

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models import AuditLog, User


def _make_staff(email: str) -> int:
    """A real, active, NON-admin user -- the one who gets refused."""
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            existing.role = "staff"
            existing.is_active = True
            existing.is_super_admin = False
            db.commit()
            return existing.id
        u = User(
            name="Denial probe",
            email=email,
            role="staff",
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


@pytest.fixture()
def staff_headers() -> dict:
    uid = _make_staff("denial_probe@test.local")
    # Minted directly rather than via POST /api/auth/login: the login route
    # carries @limiter.limit("10/minute") and would rate-limit this module in a
    # full-suite run, and it would also set a cookie on the shared TestClient.
    return {"Authorization": f"Bearer {create_access_token(str(uid))}"}


def _denials(permission: str | None = None) -> list[AuditLog]:
    db = SessionLocal()
    try:
        q = db.query(AuditLog).filter(AuditLog.outcome == "denied")
        if permission:
            q = q.filter(AuditLog.permission == permission)
        return q.order_by(AuditLog.id.desc()).all()
    finally:
        db.close()


def _clear_denials() -> None:
    db = SessionLocal()
    try:
        db.query(AuditLog).filter(AuditLog.outcome == "denied").delete()
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. The denial is actually WRITTEN
# ---------------------------------------------------------------------------
def test_a_refused_route_writes_a_denied_audit_row(client, staff_headers):
    """The row is the assertion. A 403 alone proves nothing -- it was always
    correct, which is why the missing audit write went unnoticed for so long."""
    _clear_denials()
    r = client.get("/api/users/audit-log", headers=staff_headers)
    assert r.status_code == 403, r.text

    rows = _denials()
    assert rows, (
        "a permission denial produced a 403 but NO audit row. The trail can "
        "only show successes, so 'find denied actions' is impossible."
    )


def test_the_denial_row_names_the_permission_that_was_refused(client, staff_headers):
    """Without the key, a denial row cannot answer 'refused to do WHAT?'."""
    _clear_denials()
    client.get("/api/users/audit-log", headers=staff_headers)
    rows = _denials()
    assert rows, "no denial recorded at all"
    keys = {r.permission for r in rows}
    assert "audit:view" in keys, (
        f"denial recorded, but not against the permission that was refused: {keys}"
    )


def test_the_denial_row_identifies_the_user_who_was_refused(client, staff_headers):
    """'Someone was refused' is not an audit trail."""
    _clear_denials()
    client.get("/api/users/audit-log", headers=staff_headers)
    rows = _denials()
    assert rows
    row = rows[0]
    assert row.actor_user_id, "denial row has no actor_user_id"
    assert (row.actor_name or "").strip(), "denial row has no actor_name"


def test_the_denial_row_records_the_attempted_request(client, staff_headers):
    """Which endpoint was probed is the whole point of the record."""
    _clear_denials()
    client.get("/api/users/audit-log", headers=staff_headers)
    rows = _denials()
    assert rows
    summary = rows[0].summary or ""
    assert "audit:view" in summary, f"summary does not name the permission: {summary!r}"
    assert "/api/users/audit-log" in summary, (
        f"summary does not record the path that was refused: {summary!r}"
    )


def test_a_successful_action_is_not_labelled_denied(client, auth_headers):
    """The outcome column must discriminate, not just exist.

    If everything were written as 'denied' -- or the column defaulted wrongly
    -- the filter would be useless in the other direction.
    """
    r = client.get("/api/users/audit-log", headers=auth_headers)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        # Any pre-existing row in this fresh test DB comes from a successful
        # operation; none of them may claim to be a denial.
        successes = db.query(AuditLog).filter(AuditLog.outcome == "success").count()
        mislabelled = (
            db.query(AuditLog)
            .filter(AuditLog.outcome == "denied", AuditLog.permission.is_(None))
            .count()
        )
    finally:
        db.close()
    assert successes >= 0
    assert mislabelled == 0, (
        f"{mislabelled} row(s) are marked denied but name no refused permission"
    )


# ---------------------------------------------------------------------------
# 2. The denial is FINDABLE through the API the page uses
# ---------------------------------------------------------------------------
def test_the_outcome_filter_returns_only_denials(client, auth_headers, staff_headers):
    _clear_denials()
    client.get("/api/users/audit-log", headers=staff_headers)   # generate one

    r = client.get("/api/users/audit-log?outcome=denied", headers=auth_headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items, "outcome=denied returned nothing even though a denial exists"
    assert all(i["outcome"] == "denied" for i in items), (
        "outcome=denied returned rows that are not denials"
    )


def test_the_outcome_filter_can_also_isolate_successes(client, auth_headers, staff_headers):
    """A filter that only ever returns denials is half a filter."""
    client.get("/api/users/audit-log", headers=staff_headers)
    r = client.get("/api/users/audit-log?outcome=success", headers=auth_headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert all(i["outcome"] == "success" for i in items), (
        "outcome=success leaked denied rows into the result"
    )


def test_the_listing_reports_each_row_s_outcome(client, auth_headers, staff_headers):
    """Filtering for denials is useless if the response cannot express which
    rows are denials -- the first cut of this feature had exactly that gap."""
    client.get("/api/users/audit-log", headers=staff_headers)
    r = client.get("/api/users/audit-log", headers=auth_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "trail is empty; cannot check the payload shape"
    for key in ("outcome", "permission", "doc_category"):
        assert key in items[0], f"audit row payload is missing {key!r}"


def test_all_is_treated_as_no_constraint_not_as_a_literal(client, auth_headers, staff_headers):
    """The SPA sends the "all" sentinel rather than omitting the parameter.
    Matching it literally would return an EMPTY page, which reads as 'there
    are no denied actions' -- a false all-clear."""
    client.get("/api/users/audit-log", headers=staff_headers)
    unfiltered = client.get("/api/users/audit-log", headers=auth_headers).json()["total"]
    sentinel = client.get(
        "/api/users/audit-log?outcome=all", headers=auth_headers
    ).json()["total"]
    assert sentinel == unfiltered, (
        f"outcome=all changed the result set ({sentinel} vs {unfiltered}); "
        "the sentinel is being matched as a literal value"
    )


def test_free_text_search_finds_a_denial_by_its_permission_key(client, auth_headers, staff_headers):
    """An operator types "audit:view" into the search box and must get the row.

    NOTE ON WHAT THIS DOES *NOT* PROVE. Mutation NG (narrow the search back to
    the summary column alone) ESCAPED this assertion, and the escape was
    correct: measured against a real denial row, the summary is

        'DENIED audit:view on GET /api/users/audit-log for probe@test.local'

    -- the permission key is ALREADY inside the summary text, so this passes
    with or without the widened search. Keeping it as a plain user-facing
    guarantee, with the mutation-sensitive coverage moved to the test below
    rather than left falsely resting here.
    """
    _clear_denials()
    client.get("/api/users/audit-log", headers=staff_headers)
    r = client.get("/api/users/audit-log?q=audit:view", headers=auth_headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items, "searching for the refused permission key found nothing"


def test_free_text_search_spans_the_columns_the_summary_does_not_contain(
    client, auth_headers, staff_headers
):
    """Search must cover actor and entity, not just the summary sentence.

    Chosen by MEASUREMENT, not by guesswork. Against a real denial row:

        summary     'DENIED audit:view on GET /api/users/audit-log for <email>'
        actor_name  'Denial probe'      <- NOT a substring of summary
        entity_type 'security'          <- NOT a substring of summary

    So these two needles are reachable ONLY if the search really does span the
    extra columns. Searching by WHO was refused is the first thing an operator
    does after seeing a denial, and before this change it returned nothing
    because the summary carries the actor's email while the grid shows a name.
    """
    _clear_denials()
    client.get("/api/users/audit-log", headers=staff_headers)

    by_actor = client.get("/api/users/audit-log?q=Denial probe", headers=auth_headers)
    assert by_actor.status_code == 200, by_actor.text
    assert by_actor.json()["items"], (
        "searching by the actor's NAME found nothing -- the summary stores the "
        "email, so name search only works if actor_name is searched too"
    )

    by_entity = client.get("/api/users/audit-log?q=security", headers=auth_headers)
    assert by_entity.status_code == 200, by_entity.text
    assert by_entity.json()["items"], (
        "searching by entity_type found nothing -- the search is not spanning it"
    )


def test_the_permission_filter_narrows_to_one_key(client, auth_headers, staff_headers):
    _clear_denials()
    client.get("/api/users/audit-log", headers=staff_headers)
    r = client.get(
        "/api/users/audit-log?outcome=denied&permission=audit:view",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items, "permission=audit:view matched no denial"
    assert all(i["permission"] == "audit:view" for i in items)


def test_an_unmatched_permission_returns_an_empty_page_not_everything(
    client, auth_headers, staff_headers
):
    """Guards against a filter that is silently dropped: a bogus key must
    narrow to nothing rather than fall through to the full trail."""
    client.get("/api/users/audit-log", headers=staff_headers)
    bogus = f"nope:{uuid.uuid4().hex[:8]}"
    r = client.get(f"/api/users/audit-log?permission={bogus}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [], "an unmatched permission filter was ignored"


def test_the_date_range_filter_excludes_the_past(client, auth_headers, staff_headers):
    """date_from/date_to already existed; this pins that they still work
    alongside the new outcome filter rather than being overridden by it."""
    _clear_denials()
    client.get("/api/users/audit-log", headers=staff_headers)
    # Everything just written is from today, so a window that ends yesterday
    # must be empty.
    r = client.get(
        "/api/users/audit-log?outcome=denied&date_to=2001-01-01", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [], "date_to did not constrain the denial listing"

    r2 = client.get(
        "/api/users/audit-log?outcome=denied&date_from=2001-01-01", headers=auth_headers
    )
    assert r2.json()["items"], "date_from=2001 wrongly excluded today's denial"


def test_the_filter_metadata_always_offers_denied_as_a_choice(client, auth_headers):
    """Data-driven dropdowns would HIDE 'denied' on a portal with no denials
    yet -- i.e. exactly where an operator wants to confirm there are none.
    Its absence would read as 'this portal cannot show denials'."""
    _clear_denials()
    r = client.get("/api/users/audit-log/_meta/filters", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "denied" in body["outcomes"], (
        "the outcome chooser omits 'denied' when the trail has none, so the "
        "operator cannot ask the question at all"
    )
    assert "success" in body["outcomes"]
    assert "outcome_counts" in body
    assert body["outcome_counts"]["denied"] == 0


# ---------------------------------------------------------------------------
# 3. Auditing must not change the outcome of the request
# ---------------------------------------------------------------------------
def test_a_broken_denial_write_still_returns_403_not_500(client, staff_headers, monkeypatch):
    """An observability feature must never invent a new failure mode.

    If the audit write explodes, the correct behaviour (refuse with 403) has
    to survive; turning it into a 500 would be a REGRESSION caused by logging.
    """
    import app.core.permissions as perms

    def boom(*a, **k):
        raise RuntimeError("audit backend down")

    monkeypatch.setattr(perms, "_audit_denial", boom)
    r = client.get("/api/users/audit-log", headers=staff_headers)
    assert r.status_code == 403, (
        f"a failing audit write changed the response to {r.status_code}; "
        "logging must not break the guard"
    )


def test_a_failing_denial_write_is_logged_loudly(client, staff_headers, monkeypatch, caplog):
    """Failing open is acceptable; failing SILENTLY is not -- a denial that is
    missing from the trail must leave evidence somewhere."""
    import logging

    import app.services.audit as audit_svc

    def boom(*a, **k):
        raise RuntimeError("audit backend down")

    monkeypatch.setattr(audit_svc, "log_event", boom)
    with caplog.at_level(logging.WARNING):
        r = client.get("/api/users/audit-log", headers=staff_headers)
    assert r.status_code == 403
    assert any(
        rec.levelno >= logging.WARNING for rec in caplog.records
    ), "the audit write failed without emitting a warning"


# ---------------------------------------------------------------------------
# 4. Reading the trail is itself privileged
# ---------------------------------------------------------------------------
def test_the_audit_log_still_requires_the_permission(client, staff_headers):
    """The new filters must not have widened access to the page."""
    for path in (
        "/api/users/audit-log",
        "/api/users/audit-log?outcome=denied",
        "/api/users/audit-log/_meta/filters",
    ):
        r = client.get(path, headers=staff_headers)
        assert r.status_code == 403, f"{path} answered {r.status_code} for a staff user"


def test_the_audit_log_rejects_anonymous_callers(client):
    for path in ("/api/users/audit-log", "/api/users/audit-log/_meta/filters"):
        r = client.get(path)
        assert r.status_code in (401, 403), f"{path} answered {r.status_code} anonymously"


# --------------------------------------------------------------------------
# An impossible outcome must be REFUSED, not answered with an empty page.
#
# Found 2026-08-21 while verifying the audit filters against the packaged
# release candidate: `?outcome=banana` returned 200 with `total=0`. Every
# other layer was fine, which is why it survived -- but on this particular
# screen "no results" is a claim about whether anything was denied, and a
# typo must not be able to make that claim.
# --------------------------------------------------------------------------


def test_an_unknown_outcome_is_refused_rather_than_answered_with_nothing(
    client, auth_headers, staff_headers
):
    """A misspelled outcome must 400, not report a clean office.

    `outcome` is a CLOSED set, so an unrecognised value can only ever match
    zero rows. Returning 200 + [] would tell an operator who asked for the
    refused actions that there were none -- indistinguishable from a genuinely
    clean trail, and the natural conclusion is to stop looking.
    """
    # Guarantee there IS a denial to find, so a passing assertion cannot be
    # explained away by an empty trail.
    client.get("/api/users/audit-log", headers=staff_headers)

    r = client.get("/api/users/audit-log?outcome=denide", headers=auth_headers)
    assert r.status_code == 400, (
        f"a misspelled outcome answered {r.status_code} "
        f"(body: {r.text[:200]}) instead of refusing -- an operator hunting "
        "denials would be shown an empty page and read it as 'none'"
    )
    # The message has to be actionable: name the permitted values.
    detail = str(r.json().get("detail", "")).lower()
    assert "denied" in detail and "success" in detail, (
        f"the refusal does not say what IS allowed: {detail!r}"
    )


@pytest.mark.parametrize("value", ["denied", "success", "all", "ALL", "Denied", ""])
def test_every_outcome_the_ui_can_send_is_still_accepted(
    client, auth_headers, value
):
    """The new guard must not have narrowed the legitimate inputs.

    These are exactly the values AuditLog.tsx can put on the wire: the two
    real outcomes, the 'all' sentinel, and the empty string. Case folding is
    included because the filter itself lowercases before comparing.
    """
    r = client.get(f"/api/users/audit-log?outcome={value}", headers=auth_headers)
    assert r.status_code == 200, (
        f"outcome={value!r} was refused with {r.status_code}: {r.text[:200]}"
    )


def test_an_unmatched_open_keyspace_filter_still_returns_an_empty_page(
    client, auth_headers
):
    """The refusal above must NOT have been generalised to every filter.

    `permission` and `doc_category` are open key spaces: a key nobody has
    used yet legitimately matches nothing, and 400 there would be wrong.
    This pins the distinction that makes the outcome guard correct rather
    than merely strict.
    """
    bogus = f"nope:{uuid.uuid4().hex[:8]}"
    r = client.get(f"/api/users/audit-log?permission={bogus}", headers=auth_headers)
    assert r.status_code == 200, (
        "an unused permission key was refused; the closed-set rule for "
        f"`outcome` leaked into an open key space: {r.text[:200]}"
    )
    assert r.json()["items"] == []


def test_the_accepted_outcomes_are_derived_from_the_audit_writer(client):
    """Guards the drift the validator was written to avoid.

    If someone adds a third outcome to app.services.audit, the endpoint must
    accept it automatically. A hand-copied literal here would pass this file
    while refusing a value the application itself writes.
    """
    from app.api.endpoints.users import _KNOWN_OUTCOMES
    from app.services.audit import DENIED, SUCCESS

    assert _KNOWN_OUTCOMES == frozenset({DENIED, SUCCESS}), (
        f"the endpoint's accepted outcomes {set(_KNOWN_OUTCOMES)} have drifted "
        f"from what the writer emits {{{DENIED!r}, {SUCCESS!r}}}"
    )
