"""Live tenant isolation and per-tenant feature sets, over real HTTP.

Operator requirement (2026-08-15), verbatim:

    "The portal will support each recruitment office as its own isolated
     tenant with its own name, logo, branding, feature set, and strictly
     separate database/data boundary. The current Dogar portal and migrated
     records will remain only as the Dogar tenant; they will not be shared or
     mixed with another agency."

WHAT WAS ALREADY PROVEN, AND WHY IT WAS NOT ENOUGH
--------------------------------------------------
``tests/test_tenancy.py`` proves the *structural* half: every business
endpoint is bound to ``get_tenant_db`` rather than ``get_db``, and the engine
opens the same file the resolver chose. That guard is structural on purpose --
a control-DB leak is invisible in single-tenant testing, because on the root
host the control DB **is** the only DB, so a leaking route returns exactly the
right data and no behavioural assertion can tell the difference.

What it does not prove is the end-to-end claim the operator actually made:
that a request arriving for office B, over HTTP, through the middleware,
cannot see Dogar's records. That needs two real tenants and real requests --
which the ``/t/<slug>/`` path mode makes possible without DNS.

FEATURE SETS -- THE PART THAT WAS INERT
----------------------------------------
Of the five things the operator listed, four were real. ``features`` was not:
it was a column on the tenants row, editable in Portal Admin, carried onto no
request object and read by no code path. Provisioning an office with
``ai_ocr=false`` wrote ``false`` to the database and left OCR completely
usable. The tests below fail if that regresses to a cosmetic toggle.

Note the assertion that a disabled feature answers **404, not 403**. 403 says
"this exists and you may not use it", which tells one office about a
capability another office paid for. Disabled must be indistinguishable from
never installed.
"""
import json
import os
import pathlib
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import anchor_project_path
from app.core.security import hash_password
from app.core.tenancy import tenant_db_url
from app.db.session import Base, SessionLocal
from app.main import app
from app.models import Candidate, Client, Demand, Tenant, User

REPO = pathlib.Path(__file__).resolve().parents[1]


def control_db_file() -> str:
    """The control database the RUNNING app is bound to, as a filesystem path.

    Emphatically NOT ``REPO/data/dogar_trading.db``. ``tests/conftest.py``
    redirects ``DATABASE_URL`` to a fresh ``tempfile`` before the app is
    imported, precisely so the operator's real database is never touched. A
    test that hard-codes the repo path therefore probes a file that no test
    ever writes to: every "is it in the control DB?" question answers "no",
    which reads as *proof of isolation* while proving nothing, and every "did
    the root portal's own write land?" question fails for a reason that has
    nothing to do with the product. Both happened on the first attempt.

    Deriving it from ``settings`` means this stays correct if the harness moves
    the database again, and it is the same value the application resolves.
    """
    from app.core.config import settings

    url = settings.DATABASE_URL
    assert url.startswith("sqlite"), (
        f"these file-level probes assume SQLite; DATABASE_URL is {url!r}"
    )
    return url.split("sqlite:///", 1)[1]

# Marker strings. Each must be findable ONLY through its own tenant.
DOGAR_MARKER = f"DOGAR-ONLY-CLIENT-{uuid.uuid4().hex[:8]}"
ALPHA_MARKER = f"ALPHA-ONLY-CLIENT-{uuid.uuid4().hex[:8]}"


def _make_tenant_db(slug: str, marker: str, features: dict) -> str:
    """Provision a real, isolated SQLite tenant database with one client row.

    Deliberately built through the SAME url helper the application uses
    (``tenant_db_url``) rather than a hand-rolled ``sqlite:///`` string. A
    previous defect -- fixed in 3d967de -- was exactly a second, divergent
    place that built the URL itself, so the test must not reintroduce one.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    rel = f"data/tenants/tenant_{slug}.db"
    abs_path = anchor_project_path(rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(abs_path + suffix):
            os.remove(abs_path + suffix)

    engine = create_engine(
        tenant_db_url(slug, rel), connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        s.add(Client(company_name=marker))
        s.add(
            User(
                name=f"{slug} admin",
                email=f"admin@{slug}.test",
                username=f"{slug}admin",
                role="admin",
                password_hash=hash_password("Passw0rd!123"),
                is_active=True,
            )
        )
        s.commit()
    finally:
        s.close()
        engine.dispose()

    # Register the tenant in the CONTROL database.
    db = SessionLocal()
    try:
        row = db.query(Tenant).filter(Tenant.slug == slug).first()
        if row is None:
            row = Tenant(slug=slug)
            db.add(row)
        row.company_name = f"{slug.title()} Manpower Services"
        row.short_name = slug.upper()
        row.primary_color = "#c026d3"
        row.db_path = rel
        row.status = "active"
        row.features = features
        db.commit()
    finally:
        db.close()
    return rel


def _seed_candidate_and_demand(slug: str) -> dict[str, int]:
    """Seed records only in one office's operational database."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    rel = f"data/tenants/tenant_{slug}.db"
    engine = create_engine(tenant_db_url(slug, rel), connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        client = db.query(Client).first()
        assert client is not None
        candidate = Candidate(
            full_name=f"{slug.title()} Private Candidate",
            passport_no=f"{slug.upper()}-PRIVATE-PASSPORT",
            cnic=f"{slug.upper()}-PRIVATE-CNIC",
            status="pending",
        )
        db.add(candidate)
        db.flush()
        demand = Demand(
            file_number=f"{slug.upper()}-PRIVATE-DEMAND",
            demand_code=f"{slug.upper()}-PRIVATE-DEMAND",
            client_id=client.id,
            sponsor_name=f"{slug.title()} Private Sponsor",
            country="Saudi Arabia",
            status="active",
        )
        db.add(demand)
        db.commit()
        return {"candidate_id": candidate.id, "demand_id": demand.id}
    finally:
        db.close()
        engine.dispose()


@pytest.fixture(scope="module")
def two_tenants():
    """Two isolated offices: 'alpha' (OCR off) and 'bravo' (OCR on)."""
    created = []
    try:
        created.append(
            _make_tenant_db(
                "alpha",
                ALPHA_MARKER,
                {**Tenant.default_features(), "ai_ocr": False, "ai_chatbot": False},
            )
        )
        records = _seed_candidate_and_demand("alpha")
        created.append(
            _make_tenant_db("bravo", "BRAVO-CLIENT", Tenant.default_features())
        )

        # A Dogar-only record in the CONTROL database (the Dogar tenant's data).
        db = SessionLocal()
        try:
            db.add(Client(company_name=DOGAR_MARKER))
            db.commit()
        finally:
            db.close()
        yield {"alpha": "alpha", "bravo": "bravo", "private_records": records}
    finally:
        # Never leave synthetic tenant databases behind in the operator's real
        # data directory (the mistake corrected in 82f99bf).
        db = SessionLocal()
        try:
            for slug in ("alpha", "bravo"):
                row = db.query(Tenant).filter(Tenant.slug == slug).first()
                if row is not None:
                    db.delete(row)
            for c in db.query(Client).filter(Client.company_name == DOGAR_MARKER).all():
                db.delete(c)
            db.commit()
        finally:
            db.close()
        for rel in created:
            for suffix in ("", "-wal", "-shm"):
                p = anchor_project_path(rel) + suffix
                if os.path.exists(p):
                    os.remove(p)


def _login(tc: TestClient, slug: str) -> dict:
    """Authenticate against a tenant portal through its own path prefix."""
    r = tc.post(
        f"/t/{slug}/api/auth/login",
        json={"username": f"{slug}admin", "password": "Passw0rd!123"},
    )
    assert r.status_code == 200, f"login to tenant {slug} failed: {r.text[:300]}"
    token = r.json().get("access_token")
    assert token, "TESTING mode should return the raw token in the body"
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. The data boundary, end to end
# ---------------------------------------------------------------------------
def test_a_tenant_cannot_see_dogars_records(two_tenants):
    """The operator's central promise: migrated Dogar records stay Dogar's."""
    with TestClient(app) as tc:
        headers = _login(tc, "alpha")
        r = tc.get("/t/alpha/api/clients/?limit=500", headers=headers)
        assert r.status_code == 200, r.text[:300]
        body = json.dumps(r.json())
        assert DOGAR_MARKER not in body, (
            "a tenant portal returned a Dogar-only client record -- the "
            "database boundary leaked"
        )
        assert ALPHA_MARKER in body, (
            "the tenant portal did not return its OWN record either, so this "
            "test proves nothing about isolation -- the query or fixture is "
            "broken"
        )


def test_dogar_cannot_see_a_tenants_records(two_tenants):
    """Isolation is symmetric. Dogar must not absorb another agency's data."""
    with TestClient(app) as tc:
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.role == "admin", User.is_active.is_(True)).first()
            assert u is not None, "no admin user in the control DB to test with"
            from app.core.security import create_access_token

            headers = {"Authorization": f"Bearer {create_access_token(str(u.id))}"}
        finally:
            db.close()
        r = tc.get("/api/clients/?limit=500", headers=headers)
        assert r.status_code == 200, r.text[:300]
        body = json.dumps(r.json())
        assert ALPHA_MARKER not in body, (
            "the root/Dogar portal returned a tenant-only client record"
        )


def test_two_tenants_cannot_see_each_other(two_tenants):
    with TestClient(app) as tc:
        headers = _login(tc, "bravo")
        r = tc.get("/t/bravo/api/clients/?limit=500", headers=headers)
        assert r.status_code == 200, r.text[:300]
        assert ALPHA_MARKER not in json.dumps(r.json()), (
            "tenant 'bravo' can read tenant 'alpha' records"
        )


def test_a_token_minted_for_one_tenant_is_rejected_by_another(two_tenants):
    """Identity isolation, not just data isolation.

    The same numeric user id belongs to a DIFFERENT person in each database,
    so a token that crossed portals would authenticate as whoever happens to
    hold that id in the other office.
    """
    with TestClient(app) as tc:
        alpha_headers = _login(tc, "alpha")
        r = tc.get("/t/bravo/api/clients/", headers=alpha_headers)
        assert r.status_code in (401, 403), (
            f"an 'alpha' token was accepted by the 'bravo' portal "
            f"({r.status_code}) -- the tnt claim is not being enforced"
        )


# ---------------------------------------------------------------------------
# 2. Per-tenant feature sets are enforced server-side
# ---------------------------------------------------------------------------
def test_a_disabled_feature_is_refused_server_side(two_tenants):
    """Hiding a nav item in the SPA is cosmetic -- the route stays reachable
    with curl. Enforcement has to be on the server or the "feature set" is
    decoration."""
    with TestClient(app) as tc:
        headers = _login(tc, "alpha")   # alpha has ai_ocr=False
        r = tc.get("/t/alpha/api/ocr/backends", headers=headers)
        assert r.status_code == 404, (
            f"tenant 'alpha' has ai_ocr disabled but /api/ocr/backends "
            f"answered {r.status_code}"
        )


def test_a_disabled_feature_is_404_not_403(two_tenants):
    """403 would say "this exists and you may not use it" -- disclosing to one
    office what another office bought. Disabled must look uninstalled."""
    with TestClient(app) as tc:
        headers = _login(tc, "alpha")
        r = tc.get("/t/alpha/api/chatbot/welcome", headers=headers)
        assert r.status_code == 404, (
            f"expected 404 for a disabled feature, got {r.status_code}"
        )


def test_an_enabled_feature_still_works_for_another_tenant(two_tenants):
    """Positive control: the gate must not disable the feature for everyone."""
    with TestClient(app) as tc:
        headers = _login(tc, "bravo")   # bravo has ai_ocr=True
        r = tc.get("/t/bravo/api/ocr/backends", headers=headers)
        assert r.status_code == 200, (
            f"tenant 'bravo' has ai_ocr ENABLED but /api/ocr/backends answered "
            f"{r.status_code} -- the feature gate is over-refusing"
        )


def test_the_root_portal_is_never_feature_gated(two_tenants):
    """Dogar resolves to no tenant, so every capability stays on.

    This is the assertion that enforces "without replacing its current
    workflow": if the feature gate ever started applying to the root portal,
    Dogar would lose pages it has always had.
    """
    with TestClient(app) as tc:
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.role == "admin", User.is_active.is_(True)).first()
            from app.core.security import create_access_token

            headers = {"Authorization": f"Bearer {create_access_token(str(u.id))}"}
        finally:
            db.close()
        r = tc.get("/api/ocr/backends", headers=headers)
        assert r.status_code == 200, (
            f"the root/Dogar portal was feature-gated ({r.status_code})"
        )


# ---------------------------------------------------------------------------
# 3. Branding travels with the tenant
# ---------------------------------------------------------------------------
def test_each_tenant_reports_its_own_branding(two_tenants):
    with TestClient(app) as tc:
        r = tc.get("/t/alpha/api/tenants/_meta/current")
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body["is_default"] is False
        assert body["slug"] == "alpha"
        assert body["company_name"] == "Alpha Manpower Services"
        assert body["primary_color"] == "#c026d3"


def test_the_root_portal_still_reports_dogar_branding(two_tenants):
    """The branding constraint, asserted rather than assumed."""
    with TestClient(app) as tc:
        r = tc.get("/api/tenants/_meta/current")
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body["is_default"] is True
        assert body["company_name"] == "Dogar Trading Corporation"


def test_the_meta_endpoint_exposes_the_feature_map(two_tenants):
    """The SPA needs the flags to hide disabled pages; server and client must
    agree, or the operator sees a portal that contradicts itself."""
    with TestClient(app) as tc:
        r = tc.get("/t/alpha/api/tenants/_meta/current")
        assert r.status_code == 200
        feats = r.json().get("features")
        assert isinstance(feats, dict) and feats, "no feature map returned"
        assert feats["ai_ocr"] is False, "the disabled flag is not reported"
        assert feats["candidates"] is True, "a core flag went missing"

        r2 = tc.get("/api/tenants/_meta/current")
        assert r2.json()["features"]["ai_ocr"] is True, (
            "the root portal must report every capability as enabled"
        )


def test_an_unknown_feature_flag_does_not_disable_anything():
    """A flag nobody has heard of must not silently switch a page off.

    Tenant rows created before a flag existed simply lack the key; treating
    "absent" as "off" would break working portals on every deploy that adds a
    capability.
    """
    from types import SimpleNamespace

    from app.core.tenancy import tenant_feature_enabled

    rt = SimpleNamespace(features={"ai_ocr": False})
    request = SimpleNamespace(state=SimpleNamespace(tenant=rt))
    assert tenant_feature_enabled(request, "a_flag_added_next_year") is True
    assert tenant_feature_enabled(request, "ai_ocr") is False

    no_tenant = SimpleNamespace(state=SimpleNamespace(tenant=None))
    assert tenant_feature_enabled(no_tenant, "ai_ocr") is True


# ---------------------------------------------------------------------------
# 5. THE WRITE SIDE OF THE BOUNDARY
#
# WHY THIS SECTION EXISTS (2026-08-20)
# ------------------------------------
# Everything above proves that a tenant request cannot READ across the
# boundary. That is the half that gets tested, because it is the half that is
# easy to imagine. The operator's requirement -- "strictly separate
# database/data boundary" -- also covers the direction nothing here asserted:
# where a tenant's WRITES land.
#
# The asymmetry matters because the two failure modes look completely
# different from the outside:
#
#   * a read leak shows another office's data on screen -- loud, obvious, and
#     the sort of thing a user reports within a day;
#   * a WRITE leak is silent. The office creates a client, the response is
#     201, the record appears in their own list because the reading route
#     resolves the same (leaking) session -- and the row is physically sitting
#     in Dogar's database. Nobody notices until Dogar's list is polluted with
#     another agency's records, or until a backup/restore moves data across a
#     boundary that was never really there.
#
# So these tests assert on the SQLite FILES, not on API responses. An API
# response cannot distinguish "written to the right database" from "written to
# the wrong database and read back through the same wrong session" -- which is
# exactly the shape a get_db/get_tenant_db mix-up produces.
# ---------------------------------------------------------------------------
def _rows_in(db_rel_or_none: str | None, table: str, needle: str) -> int:
    """Count rows in a RAW sqlite file whose text contains `needle`.

    Deliberately raw sqlite3 rather than a SQLAlchemy session: the whole point
    is to look at the file on disk without going through the resolver that is
    the thing under test. A session obtained from the app would answer
    "whatever the resolver decided", which is the question, not the answer.

    Two properties of this helper were wrong on the first attempt and are
    called out so they are not reintroduced:

    1. It swallowed ``sqlite3.OperationalError`` and returned 0. That makes
       "this table/column does not exist" indistinguishable from "this table
       has no matching rows". A missing column would then report zero rows and
       the test would announce a leak -- or announce an absence of one -- on
       the strength of an error it had hidden. A measurement that cannot fail
       loudly is not a measurement. It now raises.
    2. It connected read/write. SQLite in WAL mode keeps recent commits in the
       ``-wal`` sidecar; a fresh connection *does* see them, but only if it can
       read the WAL, so the connection is opened read-only via a URI and the
       sidecar files are left alone. Opening read-only also removes any chance
       that the probe itself mutates the operator's real control database.
    """
    import sqlite3

    path = (
        anchor_project_path(db_rel_or_none)
        if db_rel_or_none
        else control_db_file()
    )
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cur = con.execute(f"SELECT COUNT(*) FROM {table} WHERE company_name = ?", (needle,))
        return int(cur.fetchone()[0])
    finally:
        con.close()


def test_a_tenant_write_lands_in_the_tenants_own_file_and_nowhere_else(two_tenants):
    """A POST through /t/alpha must create the row in tenant_alpha.db.

    Checked on BOTH sides -- present in alpha's file AND absent from the
    control file. Only asserting the first would pass if the write were
    duplicated, and only asserting the second would pass if the write silently
    failed and created nothing at all.
    """
    marker = f"ALPHA-WRITE-{uuid.uuid4().hex[:8]}"
    with TestClient(app) as tc:
        headers = _login(tc, "alpha")
        r = tc.post("/t/alpha/api/clients/", json={"company_name": marker},
                    headers=headers)
        assert r.status_code in (200, 201), r.text[:400]

    # Tie the file this test probes to the file the APPLICATION was told to
    # use, instead of trusting a hand-written literal. Without this the test
    # could probe an empty database that nobody writes to and report a
    # spectacular leak that does not exist -- the failure mode a stale path
    # literal produces. (The first version of this line called tenant_db_url()
    # with one argument; it takes two, and it answers a different question
    # anyway -- a URL, not the registry's recorded path.)
    db = SessionLocal()
    try:
        row = db.query(Tenant).filter(Tenant.slug == "alpha").first()
        assert row is not None, "the alpha tenant vanished from the registry mid-test"
        alpha_rel = row.db_path
    finally:
        db.close()
    assert alpha_rel == "data/tenants/tenant_alpha.db", (
        f"the registry points alpha at {alpha_rel!r}; this test probes a "
        f"different file and would be measuring the wrong database"
    )

    in_tenant = _rows_in(alpha_rel, "clients", marker)
    in_control = _rows_in(None, "clients", marker)

    assert in_tenant == 1, (
        f"a client created through the alpha portal is NOT in alpha's own "
        f"database (found {in_tenant} row(s)). The write went somewhere else."
    )
    assert in_control == 0, (
        f"a client created through the alpha portal was written into the "
        f"CONTROL database ({in_control} row(s)). This is the silent failure "
        f"mode: the office sees its own record because the read leaks the same "
        f"way, while the row physically lives in Dogar's data."
    )


def test_a_root_portal_write_does_not_reach_any_tenant_file(two_tenants):
    """The symmetric direction, which is the operator's stated priority.

    "The current Dogar portal and migrated records will remain only as the
    Dogar tenant" is a statement about Dogar's data not spreading outward, so
    it needs its own assertion rather than being inferred from the tenant case.
    """
    from app.core.security import create_access_token

    marker = f"DOGAR-WRITE-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.role == "admin", User.is_active.is_(True)).first()
        assert u is not None, "no admin user in the control DB to test with"
        headers = {"Authorization": f"Bearer {create_access_token(str(u.id))}"}
    finally:
        db.close()

    try:
        with TestClient(app) as tc:
            r = tc.post("/api/clients/", json={"company_name": marker}, headers=headers)
            assert r.status_code in (200, 201), r.text[:400]

        assert _rows_in(None, "clients", marker) == 1, (
            "the root portal's own write did not land in the control database"
        )
        for slug in ("alpha", "bravo"):
            leaked = _rows_in(f"data/tenants/tenant_{slug}.db", "clients", marker)
            assert leaked == 0, (
                f"a record created on the ROOT portal appeared in tenant "
                f"{slug!r}'s database ({leaked} row(s)) -- Dogar's data is "
                f"being mixed into another agency's"
            )
    finally:
        # This row is written into the operator's real control DB, so it must
        # be removed whatever the assertions did. Leaving test data behind in
        # a live database is its own defect (see the note in the two_tenants
        # fixture about 82f99bf).
        db = SessionLocal()
        try:
            for c in db.query(Client).filter(Client.company_name == marker).all():
                db.delete(c)
            db.commit()
        finally:
            db.close()


def test_a_tenants_audit_trail_is_written_to_its_own_database(two_tenants):
    """Audit rows are the record of WHO did WHAT in WHICH office.

    If they accumulate in the control database then the Audit Log page -- the
    feature the operator asked for filters on -- shows one office's denials and
    successes to another, and the per-office scoping in the endpoint is
    filtering a table that should never have held the other office's rows.
    """
    import sqlite3

    marker = f"ALPHA-AUDITED-{uuid.uuid4().hex[:8]}"

    def _audit_rows(path: str, needle: str) -> int:
        con = sqlite3.connect(path)
        try:
            cur = con.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE after_json LIKE ?",
                (f"%{needle}%",),
            )
            return int(cur.fetchone()[0])
        except sqlite3.OperationalError:
            return 0
        finally:
            con.close()

    control = control_db_file()
    tenant = anchor_project_path("data/tenants/tenant_alpha.db")
    before_control = _audit_rows(control, marker)

    with TestClient(app) as tc:
        headers = _login(tc, "alpha")
        r = tc.post("/t/alpha/api/clients/", json={"company_name": marker},
                    headers=headers)
        assert r.status_code in (200, 201), r.text[:400]

    assert _audit_rows(tenant, marker) >= 1, (
        "creating a client through the alpha portal wrote no audit row into "
        "alpha's own database -- either auditing is bound to the wrong session "
        "or the trail for this office does not exist"
    )
    assert _audit_rows(control, marker) == before_control, (
        "the alpha portal's audit row was written into the CONTROL database, so "
        "Dogar's audit log now contains another office's activity"
    )


# ---------------------------------------------------------------------------
# Requested cross-office record and document isolation
# ---------------------------------------------------------------------------
def test_office_cannot_read_update_or_print_another_offices_candidate(two_tenants):
    records = two_tenants["private_records"]
    with TestClient(app) as tc:
        alpha = _login(tc, "alpha")
        bravo = _login(tc, "bravo")

        own = tc.get(f"/t/alpha/api/candidates/{records['candidate_id']}", headers=alpha)
        assert own.status_code == 200, own.text[:300]
        assert "Alpha Private Candidate" in json.dumps(own.json())

        read = tc.get(f"/t/bravo/api/candidates/{records['candidate_id']}", headers=bravo)
        assert read.status_code == 404, read.text[:300]

        update = tc.put(
            f"/t/bravo/api/candidates/{records['candidate_id']}",
            headers=bravo,
            json={"full_name": "Cross-office overwrite attempt"},
        )
        assert update.status_code == 404, update.text[:300]

        printed = tc.get(
            f"/t/bravo/api/candidates/{records['candidate_id']}/print-profile",
            headers=bravo,
        )
        assert printed.status_code == 404, printed.text[:300]

        still_own = tc.get(f"/t/alpha/api/candidates/{records['candidate_id']}", headers=alpha)
        assert still_own.status_code == 200
        assert "Cross-office overwrite attempt" not in json.dumps(still_own.json())


def test_office_cannot_read_update_or_print_another_offices_demand(two_tenants):
    records = two_tenants["private_records"]
    with TestClient(app) as tc:
        alpha = _login(tc, "alpha")
        bravo = _login(tc, "bravo")

        own = tc.get(f"/t/alpha/api/demands/{records['demand_id']}", headers=alpha)
        assert own.status_code == 200, own.text[:300]
        assert "ALPHA-PRIVATE-DEMAND" in json.dumps(own.json())

        read = tc.get(f"/t/bravo/api/demands/{records['demand_id']}", headers=bravo)
        assert read.status_code == 404, read.text[:300]

        update = tc.put(
            f"/t/bravo/api/demands/{records['demand_id']}",
            headers=bravo,
            json={"sponsor_name": "Cross-office overwrite attempt"},
        )
        assert update.status_code == 404, update.text[:300]

        printed = tc.get(
            f"/t/bravo/api/protector-letters/demand/{records['demand_id']}/demand-letter",
            headers=bravo,
        )
        assert printed.status_code == 404, printed.text[:300]

        still_own = tc.get(f"/t/alpha/api/demands/{records['demand_id']}", headers=alpha)
        assert still_own.status_code == 200
        assert "Cross-office overwrite attempt" not in json.dumps(still_own.json())


def test_current_tenant_branding_get_and_put_are_scoped_to_resolved_office(two_tenants):
    with TestClient(app) as tc:
        alpha = _login(tc, "alpha")
        original = tc.get("/t/alpha/api/settings/branding", headers=alpha)
        assert original.status_code == 200, original.text[:300]
        before = original.json()
        assert before["slug"] == "alpha"

        saved = tc.put(
            "/t/alpha/api/settings/branding",
            headers=alpha,
            json={
                "short_name": "ALPHA HQ",
                "subtitle": "Alpha Recruitment",
                "primary_color": "#0f766e",
                "font_key": "lato",
                "office_name": "Alpha Overseas Office",
                "license_number": "OEP-ALPHA-1",
                "office_address": "Alpha Street",
            },
        )
        assert saved.status_code == 200, saved.text[:500]
        body = saved.json()
        assert body["slug"] == "alpha"
        assert body["shortName"] == "ALPHA HQ"
        assert body["fontKey"] == "lato"
        assert body["officeName"] == "Alpha Overseas Office"

        persisted = tc.get("/t/alpha/api/settings/branding", headers=alpha)
        assert persisted.status_code == 200
        assert persisted.json()["fontKey"] == "lato"

        # Restore fixture branding so this module remains order-independent.
        restored = tc.put(
            "/t/alpha/api/settings/branding",
            headers=alpha,
            json={
                "short_name": "ALPHA",
                "subtitle": before["subtitle"],
                "primary_color": "#c026d3",
                "font_key": "system",
                "office_name": before["officeName"],
                "license_number": before["licenseNumber"],
                "office_address": before["officeAddress"],
            },
        )
        assert restored.status_code == 200, restored.text[:300]

        bravo = _login(tc, "bravo")
        bravo_brand = tc.get("/t/bravo/api/settings/branding", headers=bravo)
        assert bravo_brand.status_code == 200
        assert bravo_brand.json()["slug"] == "bravo"
        assert bravo_brand.json()["shortName"] != "ALPHA HQ"
        assert bravo_brand.json()["fontKey"] != "lato"


def test_current_tenant_branding_rejects_unknown_font_key(two_tenants):
    with TestClient(app) as tc:
        alpha = _login(tc, "alpha")
        response = tc.put(
            "/t/alpha/api/settings/branding",
            headers=alpha,
            json={"font_key": "Comic Sans"},
        )
        assert response.status_code == 422, response.text[:500]


def test_current_tenant_branding_logo_upload_is_tenant_scoped(two_tenants):
    # A tiny valid PNG, generated in-memory, avoids shipping a fixture asset.
    import io

    from PIL import Image

    image = Image.effect_noise((128, 128), 48).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    png = buffer.getvalue()
    with TestClient(app) as tc:
        alpha = _login(tc, "alpha")
        response = tc.post(
            "/t/alpha/api/settings/branding/logo",
            headers=alpha,
            files={"file": ("alpha.png", io.BytesIO(png), "image/png")},
        )
        assert response.status_code == 200, response.text[:500]
        body = response.json()
        assert body["slug"] == "alpha"
        assert "/tenant-assets/alpha/" in body["logo"]

        bravo = _login(tc, "bravo")
        bravo_brand = tc.get("/t/bravo/api/settings/branding", headers=bravo)
        assert bravo_brand.status_code == 200
        assert "/tenant-assets/alpha/" not in bravo_brand.json()["logo"]
        assert "/tenant-assets/bravo/" not in bravo_brand.json()["logo"] or bravo_brand.json()["logo"].startswith("/static/")

        # Remove the synthetic alpha asset and restore the row through the
        # control database cleanup path used by the fixture.
        db = SessionLocal()
        try:
            row = db.query(Tenant).filter(Tenant.slug == "alpha").first()
            assert row is not None
            filename = row.logo_filename
            row.logo_filename = None
            row.logo_path = None
            db.commit()
        finally:
            db.close()
        if filename:
            asset = anchor_project_path(f"data/tenants/alpha/{filename}")
            if os.path.exists(asset):
                os.remove(asset)
