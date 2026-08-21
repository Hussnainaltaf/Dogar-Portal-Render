"""The two schema-install routes must produce the SAME schema.

WHY THIS FILE EXISTS (measured 2026-08-19)
------------------------------------------
This project brings a database up to date by two different mechanisms, and
both are load-bearing:

  1. ``alembic upgrade head`` -- the control DB (and any tenant DB that was
     provisioned with an ``alembic_version`` stamp).
  2. ``scripts/apply_tenant_migrations.py`` -- an ORM-diff reconciler that
     walks ``Base.metadata`` and issues ``CREATE TABLE`` / ``ADD COLUMN`` /
     ``CREATE INDEX`` for whatever is missing.

Route 2 cannot simply be deleted in favour of route 1. Measured against a
legacy tenant DB whose ``alembic_version`` row is absent, ``alembic upgrade
head`` fails outright::

    sqlite3.OperationalError: table agents already exists

because it replays the baseline revision against a populated database. So
both routes must exist -- and therefore both must AGREE.

They did not. Measured by running each route against its own copy of the
live ``tenant_acme.db`` after the ``doc_category`` migration:

    alembic route : ix_audit_doc_category PRESENT
    ORM route     : ix_audit_doc_category MISSING

and independently, the ``candidates`` sort index existed under two DIFFERENT
NAMES depending on which route built the database:

    migrated DB        : ix_candidates_latest_demand_sort  (composite, raw SQL
                         in migration 3b7d2e4f8a15)
    fresh create_all() : ix_candidates_latest_demand_id    (single column,
                         auto-named by ``index=True`` on the model)

None of this raised an error. Both index variants still serve the candidate
list ORDER BY as a covering scan (verified with EXPLAIN QUERY PLAN over
2,675 real rows), and a missing index never fails a query -- it just scans.
That is precisely why the drift survived: the schema was wrong in a way
nothing announced. The consequences are still real -- a query plan measured
on one install does not describe the other, and the by-name reconciler kept
re-adding an index that already existed under a different name.

WHAT THESE TESTS ACTUALLY ASSERT
--------------------------------
They compare the *live* schema of a database built by each route against
``Base.metadata`` -- the single declared source of truth -- rather than
comparing the two routes to each other. Comparing the routes to each other
would pass if both drifted identically. Comparing each to the models means
a future migration that adds an index without declaring it on the model
(or vice versa) fails here.

FAILURE-CAPABILITY (per the project's verification rule)
--------------------------------------------------------
These are not vacuous. Confirmed by reverting each fix in turn:

  * restore ``index=True`` on ``Candidate.latest_demand_id`` and drop the
    ``__table_args__`` composite ->
    ``test_migrated_schema_has_every_model_declared_index`` FAILS
    (ix_candidates_latest_demand_id absent from the migrated DB).
  * delete step 3 (index reconciliation) from
    ``scripts/apply_tenant_migrations.py`` ->
    ``test_the_orm_reconciler_creates_missing_indexes`` and
    ``test_both_routes_converge_on_the_same_index_set`` FAIL.

Both reversions are also registered as mutations in
``scripts/mutation_check.py`` (batch ``schema``).
"""
from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

import app.models  # noqa: F401 -- registers every model on Base.metadata
from app.db.session import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RECONCILER = PROJECT_ROOT / "scripts" / "apply_tenant_migrations.py"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _load_reconciler():
    """Import scripts/apply_tenant_migrations.py as a module.

    It is a script, not a package member, so it has no importable dotted
    path. Loading it by file path is deliberate: the test must exercise the
    SAME code the deploy entrypoint runs, not a re-implementation of it.
    """
    spec = importlib.util.spec_from_file_location("_atm_under_test", RECONCILER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _live_indexes(db_file: Path) -> set[str]:
    con = sqlite3.connect(str(db_file))
    try:
        return {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name IS NOT NULL"
            )
        }
    finally:
        con.close()


def _live_tables(db_file: Path) -> set[str]:
    con = sqlite3.connect(str(db_file))
    try:
        return {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        con.close()


def _declared_indexes_for(tables: set[str]) -> set[str]:
    """Every named index the models declare on tables that actually exist."""
    out: set[str] = set()
    for _, table in Base.metadata.tables.items():
        if table.name not in tables:
            continue
        for index in table.indexes:
            if index.name:
                out.add(index.name)
    return out


def _missing_declared_indexes(db_file: Path) -> set[str]:
    tables = _live_tables(db_file)
    return _declared_indexes_for(tables) - _live_indexes(db_file)


@pytest.fixture(scope="module")
def alembic_built(tmp_path_factory) -> Path:
    """A database built by ``alembic upgrade head`` from empty."""
    target = tmp_path_factory.mktemp("alembic_route") / "alembic.db"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-x",
            f"db_url=sqlite:///{target}",
            "-x",
            "is_tenant=1",
            "upgrade",
            "head",
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        pytest.fail(
            "alembic upgrade head failed while building the reference schema:\n"
            f"stdout:\n{proc.stdout[-3000:]}\n\nstderr:\n{proc.stderr[-3000:]}"
        )
    return target


@pytest.fixture(scope="module")
def create_all_built(tmp_path_factory) -> Path:
    """A database built by ``Base.metadata.create_all()`` (fresh install)."""
    target = tmp_path_factory.mktemp("create_all_route") / "fresh.db"
    engine = sa.create_engine(f"sqlite:///{target}")
    Base.metadata.create_all(bind=engine)
    engine.dispose()
    return target


# ---------------------------------------------------------------------------
# 1. each route matches the declared models
# ---------------------------------------------------------------------------
def test_migrated_schema_has_every_model_declared_index(alembic_built):
    """A DB built by alembic must carry every index the models declare.

    This is the assertion that catches "the migration created an index the
    model does not know about, under a name the model would never produce"
    -- the ix_candidates_latest_demand_sort vs _id split.
    """
    missing = _missing_declared_indexes(alembic_built)
    assert not missing, (
        "These indexes are declared on the SQLAlchemy models but are absent "
        "from a database built by `alembic upgrade head`:\n  "
        + "\n  ".join(sorted(missing))
        + "\n\nEither add a migration that creates them, or -- if a migration "
        "already creates an equivalent index under a DIFFERENT name -- change "
        "the model to declare that exact name so both install routes converge."
    )


def test_fresh_install_has_every_model_declared_index(create_all_built):
    """create_all() is the models' own output, so this is a sanity floor.

    If this ever fails, an index is declared in a way SQLAlchemy itself will
    not emit, and every other assertion in this file is untrustworthy.
    """
    missing = _missing_declared_indexes(create_all_built)
    assert not missing, (
        "Base.metadata.create_all() did not emit its own declared indexes: "
        f"{sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# 2. the ORM reconciler repairs a drifted database
# ---------------------------------------------------------------------------
def test_the_orm_reconciler_creates_missing_indexes(create_all_built, tmp_path):
    """Drop an index, run the reconciler, assert it came back.

    The index chosen is one that a real migration adds
    (``ix_audit_doc_category``), because that is the exact shape of the bug
    measured in production: a migration adds a column plus its index, the
    reconciler adds only the column, and the tenant silently ends up with an
    unindexed audit table.
    """
    victim = tmp_path / "drifted.db"
    victim.write_bytes(create_all_built.read_bytes())

    con = sqlite3.connect(str(victim))
    try:
        con.execute("DROP INDEX ix_audit_doc_category")
        con.commit()
    finally:
        con.close()

    assert "ix_audit_doc_category" not in _live_indexes(victim), (
        "test setup failed: the index was not actually dropped, so this test "
        "would pass without proving anything"
    )

    tables_created, cols_added, idx_created = _load_reconciler().upgrade_tenant_db(victim)

    assert "ix_audit_doc_category" in _live_indexes(victim), (
        "scripts/apply_tenant_migrations.py did not restore the missing index. "
        "A tenant that goes through this path therefore full-scans audit_logs "
        "-- the one table guaranteed to grow forever."
    )
    assert idx_created >= 1, (
        f"the reconciler reported {idx_created} indexes created; it must report "
        "the repair so a deploy log shows what changed"
    )
    assert tables_created == 0 and cols_added == 0, (
        "only an index was missing, so nothing else should have been altered; "
        f"got tables={tables_created} cols={cols_added}"
    )


def test_the_reconciler_is_idempotent(create_all_built, tmp_path):
    """Running it on an up-to-date DB must be a no-op, not a duplicate.

    Indexes are matched BY NAME. If the match ever regresses to matching by
    column list (or to `index=True`'s auto-generated name), the reconciler
    would add a second, differently-named index on every single deploy.
    """
    target = tmp_path / "already_current.db"
    target.write_bytes(create_all_built.read_bytes())

    before = _live_indexes(target)
    tables_created, cols_added, idx_created = _load_reconciler().upgrade_tenant_db(target)
    after = _live_indexes(target)

    assert (tables_created, cols_added, idx_created) == (0, 0, 0), (
        "the reconciler changed an already-current database: "
        f"tables={tables_created} cols={cols_added} indexes={idx_created}"
    )
    assert before == after, f"index set changed on a no-op run: {after ^ before}"


# ---------------------------------------------------------------------------
# 3. the two routes agree with each other
# ---------------------------------------------------------------------------
def test_both_routes_converge_on_the_same_index_set(alembic_built, create_all_built):
    """The deploy pipeline runs alembic THEN the reconciler.

    An operator must get one schema regardless of which route a given
    database took to get there. This compares the two routes directly, and
    is deliberately scoped to the tables both routes actually built, so a
    table that only one route knows about cannot mask a real difference.
    """
    shared_tables = _live_tables(alembic_built) & _live_tables(create_all_built)
    assert shared_tables, "the two routes built no tables in common -- test is broken"

    def scoped(db_file: Path) -> set[str]:
        con = sqlite3.connect(str(db_file))
        try:
            rows = con.execute(
                "SELECT name, tbl_name FROM sqlite_master "
                "WHERE type='index' AND name IS NOT NULL"
            ).fetchall()
        finally:
            con.close()
        # Ignore SQLite's internal auto-indexes: they are implementation
        # detail of UNIQUE constraints, not schema the project declares.
        return {
            name
            for name, tbl in rows
            if tbl in shared_tables and not name.startswith("sqlite_autoindex")
        }

    only_alembic = scoped(alembic_built) - scoped(create_all_built)
    only_fresh = scoped(create_all_built) - scoped(alembic_built)

    assert not (only_alembic or only_fresh), (
        "the two install routes produced DIFFERENT schemas.\n"
        f"  only in the alembic-migrated DB : {sorted(only_alembic)}\n"
        f"  only in the fresh create_all DB : {sorted(only_fresh)}\n\n"
        "A query plan measured on one install does not describe the other. "
        "Make the model declare the same index name the migration creates."
    )


def test_both_routes_agree_on_audit_log_columns(alembic_built, create_all_built):
    """audit_logs specifically -- it is the table this work added to.

    A column present on one route and absent on the other is not a
    performance nuance: the audit page SELECTs these columns by name, so the
    route that lacks one 500s. Measured for real on tenant_acme.db before
    the migration was rolled out to tenants:
    ``no such column: audit_logs.doc_category``.
    """
    def cols(db_file: Path) -> set[str]:
        con = sqlite3.connect(str(db_file))
        try:
            return {r[1] for r in con.execute("PRAGMA table_info(audit_logs)")}
        finally:
            con.close()

    a, f = cols(alembic_built), cols(create_all_built)
    assert a == f, (
        "audit_logs columns differ between install routes -- the audit log "
        f"page will 500 on one of them.\n  only alembic: {sorted(a - f)}\n"
        f"  only fresh  : {sorted(f - a)}"
    )
    for required in ("outcome", "permission", "doc_category"):
        assert required in a, f"{required!r} missing from the migrated audit_logs"


def test_declared_index_names_are_explicit_not_autogenerated():
    """Guard the naming convention that makes convergence possible.

    ``index=True`` auto-names an index ``ix_<table>_<column>``. That is fine
    on its own, but it is NOT fine when a migration creates an equivalent
    index under a hand-written name: the reconciler matches by name, so the
    two never recognise each other and it re-adds a duplicate forever.

    This test pins the specific columns where that collision was measured,
    so re-introducing ``index=True`` there fails loudly instead of silently
    re-opening the drift.
    """
    candidates = Base.metadata.tables["candidates"]
    names = {i.name for i in candidates.indexes}
    assert "ix_candidates_latest_demand_sort" in names, (
        "candidates must declare the composite sort index by the SAME name "
        "migration 3b7d2e4f8a15 creates (ix_candidates_latest_demand_sort)"
    )
    assert "ix_candidates_latest_demand_id" not in names, (
        "`index=True` is back on Candidate.latest_demand_id. That auto-names "
        "a SECOND single-column index which no migration creates, so migrated "
        "and fresh installs diverge again -- and the tenant reconciler will "
        "add it to every migrated tenant DB on every deploy."
    )

    audit = Base.metadata.tables["audit_logs"]
    audit_names = {i.name for i in audit.indexes}
    for expected in (
        "ix_audit_entity_time",
        "ix_audit_outcome_time",
        "ix_audit_doc_category",
    ):
        assert expected in audit_names, (
            f"{expected!r} must stay declared in audit_logs.__table_args__ so "
            "it matches the migration's index name exactly"
        )
    assert "ix_audit_logs_doc_category" not in audit_names, (
        "`index=True` is back on AuditLog.doc_category. Its auto-generated "
        "name (ix_audit_logs_doc_category) does NOT match the migration's "
        "ix_audit_doc_category, so the two install routes diverge."
    )
