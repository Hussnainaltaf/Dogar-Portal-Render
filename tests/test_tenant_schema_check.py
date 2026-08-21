"""The startup tenant-schema drift detector must actually detect drift.

WHY THIS FILE EXISTS (measured 2026-08-19)
------------------------------------------
``app.main._lifespan`` runs ``alembic upgrade head`` against the CONTROL
database on every boot. Tenant databases are separate and are NOT upgraded
there -- they are rolled forward by ``docker-entrypoint.sh`` step 2, which
only runs inside the container image. Start the app any other way (uvicorn,
PM2, systemd, a dev box) and the control DB races ahead of every tenant.

Measured on the live deployment:

    control DB      -> a1c6e8b4d072   (upgraded on boot)
    tenant_acme.db  -> 9b5e7d3f2c81   (untouched)
    tenant_beta.db  -> 9b5e7d3f2c81   (untouched)

and, with the real ORM query the audit endpoint issues against acme:

    sqlite3.OperationalError: no such column: audit_logs.doc_category

-- an HTTP 500 on a page that works perfectly for the default office, with
nothing in the log explaining the difference.

``app.db.tenant_schema_check.warn_if_tenants_behind()`` closes that
observability gap. These tests exist because a diagnostic that silently
reports "all good" is worse than no diagnostic: it actively suppresses
suspicion. So the important assertion here is not "it returns []" -- it is
that it RETURNS THE LAGGING TENANT when one is genuinely behind.

FAILURE-CAPABILITY
------------------
``test_a_tenant_behind_head_is_reported`` fails if the comparison is
stubbed out (e.g. ``if rev != head`` changed to ``if False``), and
``test_the_warning_names_the_tenant_and_the_fix`` fails if the warning stops
naming the tenant or the remediation command. Both reversions are registered
as mutations in ``scripts/mutation_check.py`` (batch ``schema``).
"""
from __future__ import annotations

import logging

import pytest
import sqlalchemy as sa

from app.db import tenant_schema_check as tsc


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _make_tenant_db(path, revision: str | None) -> str:
    """A minimal SQLite DB stamped at ``revision`` (or unstamped if None)."""
    url = f"sqlite:///{path}"
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        if revision is not None:
            conn.execute(sa.text(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
            ))
            conn.execute(
                sa.text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": revision},
            )
        else:
            # Unstamped: a real table exists so the file is not empty, but
            # there is no alembic_version at all (the legacy-tenant shape).
            conn.execute(sa.text("CREATE TABLE agents (id INTEGER PRIMARY KEY)"))
    engine.dispose()
    return url


@pytest.fixture
def fake_tenants(monkeypatch):
    """Drive warn_if_tenants_behind() off a controllable tenant list.

    The real function reads the control DB's ``tenants`` table. Patching the
    two seams it depends on (the tenant listing and the URL builder) keeps
    these tests independent of whatever tenants happen to exist locally --
    otherwise the suite's result would depend on developer machine state.
    """
    rows: list[tuple[str, str, str]] = []
    urls: dict[str, str] = {}

    class _FakeQuery:
        def all(self):
            return [
                type("T", (), {"slug": s, "db_path": p, "status": st})()
                for s, p, st in rows
            ]

    class _FakeSession:
        def query(self, _model):
            return _FakeQuery()

        def close(self):
            pass

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(
        "app.core.tenancy.tenant_db_url",
        lambda slug, db_path: urls.get(slug, f"sqlite:///{db_path}"),
    )

    def register(slug: str, url: str, status: str = "active"):
        rows.append((slug, url.replace("sqlite:///", ""), status))
        urls[slug] = url

    return register


# ---------------------------------------------------------------------------
# the detection itself
# ---------------------------------------------------------------------------
def test_a_tenant_behind_head_is_reported(tmp_path, fake_tenants, monkeypatch):
    """The exact production condition: tenant stamped one revision back."""
    monkeypatch.setattr(tsc, "_head_revision", lambda: "a1c6e8b4d072")
    fake_tenants("acme", _make_tenant_db(tmp_path / "acme.db", "9b5e7d3f2c81"))

    behind = tsc.warn_if_tenants_behind()

    assert behind == [("acme", "9b5e7d3f2c81")], (
        "the drift detector did not report a tenant that is genuinely one "
        f"revision behind head; got {behind!r}. A detector that reports "
        "'all good' while a tenant 500s is worse than no detector."
    )


def test_a_tenant_at_head_is_not_reported(tmp_path, fake_tenants, monkeypatch):
    """No false positives -- otherwise the warning gets ignored as noise."""
    monkeypatch.setattr(tsc, "_head_revision", lambda: "a1c6e8b4d072")
    fake_tenants("acme", _make_tenant_db(tmp_path / "acme.db", "a1c6e8b4d072"))

    assert tsc.warn_if_tenants_behind() == []


def test_an_unstamped_tenant_is_reported(tmp_path, fake_tenants, monkeypatch):
    """A legacy tenant with no alembic_version row must not slip through.

    This is the shape that ``alembic upgrade head`` cannot repair at all
    (measured: "table agents already exists"), so it is the MOST important
    one to surface, not the easiest to skip.
    """
    monkeypatch.setattr(tsc, "_head_revision", lambda: "a1c6e8b4d072")
    fake_tenants("legacy", _make_tenant_db(tmp_path / "legacy.db", None))

    behind = tsc.warn_if_tenants_behind()

    assert behind == [("legacy", None)], (
        f"an unstamped legacy tenant DB was not reported as behind: {behind!r}"
    )


def test_only_the_lagging_tenant_is_named(tmp_path, fake_tenants, monkeypatch):
    """Mixed fleet: report the one that is behind, not the whole fleet."""
    monkeypatch.setattr(tsc, "_head_revision", lambda: "a1c6e8b4d072")
    fake_tenants("current", _make_tenant_db(tmp_path / "cur.db", "a1c6e8b4d072"))
    fake_tenants("stale", _make_tenant_db(tmp_path / "stale.db", "9b5e7d3f2c81"))

    behind = tsc.warn_if_tenants_behind()

    assert [slug for slug, _ in behind] == ["stale"], (
        f"expected only 'stale' to be flagged, got {behind!r}"
    )


def test_a_missing_sqlite_file_is_not_flagged_or_created(tmp_path, fake_tenants, monkeypatch):
    """An archived tenant's file is gone -- do not flag it, and do not create it.

    SQLite never errors on a missing file: merely CONNECTING would create an
    empty database at that path, leaving a bogus zero-table shell behind.
    A diagnostic must not mutate the thing it inspects.
    """
    monkeypatch.setattr(tsc, "_head_revision", lambda: "a1c6e8b4d072")
    ghost = tmp_path / "archived_gone.db"
    fake_tenants("archived", f"sqlite:///{ghost}", status="archived")

    behind = tsc.warn_if_tenants_behind()

    assert behind == [], f"a tenant with no database file was flagged: {behind!r}"
    assert not ghost.exists(), (
        "the schema check CREATED a database file for a tenant whose file was "
        "archived -- an inspection must never mutate what it inspects"
    )


# ---------------------------------------------------------------------------
# the warning has to be actionable
# ---------------------------------------------------------------------------
def test_the_warning_names_the_tenant_and_the_fix(tmp_path, fake_tenants, monkeypatch, caplog):
    """A warning that does not say WHICH tenant or WHAT to run is useless."""
    monkeypatch.setattr(tsc, "_head_revision", lambda: "a1c6e8b4d072")
    fake_tenants("acme", _make_tenant_db(tmp_path / "acme.db", "9b5e7d3f2c81"))

    with caplog.at_level(logging.WARNING, logger="dtc.migrate"):
        tsc.warn_if_tenants_behind()

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "acme" in text, f"the warning did not name the lagging tenant: {text!r}"
    assert "9b5e7d3f2c81" in text, "the warning did not report the tenant's revision"
    assert "a1c6e8b4d072" in text, "the warning did not report the expected head"
    assert "apply_alembic_to_tenants.py" in text, (
        "the warning did not tell the operator how to fix it"
    )


def test_nothing_is_logged_at_warning_when_the_fleet_is_current(tmp_path, fake_tenants, monkeypatch, caplog):
    """Otherwise operators learn to ignore the message."""
    monkeypatch.setattr(tsc, "_head_revision", lambda: "a1c6e8b4d072")
    fake_tenants("acme", _make_tenant_db(tmp_path / "acme.db", "a1c6e8b4d072"))

    with caplog.at_level(logging.WARNING, logger="dtc.migrate"):
        tsc.warn_if_tenants_behind()

    drift = [r for r in caplog.records if "TENANT SCHEMA DRIFT" in r.getMessage()]
    assert not drift, f"a drift warning was emitted for an up-to-date fleet: {drift}"


# ---------------------------------------------------------------------------
# it must never break boot
# ---------------------------------------------------------------------------
def test_it_returns_empty_instead_of_raising_when_alembic_is_absent(monkeypatch):
    """No alembic in the build -> skip quietly, do not explode on startup."""
    monkeypatch.setattr(tsc, "_head_revision", lambda: None)
    assert tsc.warn_if_tenants_behind() == []


def test_an_unreadable_tenant_db_does_not_raise(tmp_path, fake_tenants, monkeypatch):
    """A corrupt tenant file must be reported as drift, never crash boot.

    Reading its revision fails, which is indistinguishable from 'unstamped'
    -- and 'flag it for the operator' is the safe interpretation of both.
    """
    monkeypatch.setattr(tsc, "_head_revision", lambda: "a1c6e8b4d072")
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"this is definitely not a sqlite database")
    fake_tenants("corrupt", f"sqlite:///{corrupt}")

    behind = tsc.warn_if_tenants_behind()  # must not raise

    assert behind == [("corrupt", None)], (
        f"a corrupt tenant DB should be flagged for the operator; got {behind!r}"
    )
