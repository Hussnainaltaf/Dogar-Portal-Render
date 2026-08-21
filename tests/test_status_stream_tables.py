"""The realtime status stream must observe tables that actually exist, and must
notice in-place status transitions -- not just inserts and deletes.

WHY THIS FILE EXISTS
--------------------
An external audit of the v1.1.0 release ZIP flagged (High severity):

    Realtime status stream pointed at a non-existent physical table
    -- the logical key "documents" was mapped to "documents", but the
       ORM/database table is generated_documents
    -- document status changes were not reflected in the realtime token; the
       code silently fell back to 0:none for that table

The mis-mapping was one word. What made it survive is that ``_table_signature()``
wrapped its ``COUNT(*)`` in a bare ``except Exception`` returning a *constant*
``"0:none"``, so a missing table was indistinguishable from an empty one: the SSE
stream kept emitting a token that simply never moved, with nothing logged.

Investigating it surfaced a second, larger defect the audit did not reach. The
old code ran ``SELECT MAX(updated_at)`` against all four tables behind
``except Exception: pass``, and its comment claimed "updated_at exists on all
status tables". That was false for two of them -- ``generated_documents`` has
``generated_at``, and ``candidate_assignments`` has no timestamp column at all --
so both silently fell back to a count-only signature. Since
``candidate_assignments.status`` is the app's core workflow state, and advancing
it is an UPDATE that leaves the row count unchanged, moving a candidate through
the pipeline was exactly the case the token could not detect.

These tests therefore pin *observability*, not source text: every mapped name is
resolved against SQLAlchemy metadata, every change marker is executed for real,
and each entity is mutated to prove the token actually moves.
"""
from __future__ import annotations

import logging

import pytest
from sqlalchemy import text

from app.db.session import Base, SessionLocal
from app.services import status_stream
from app.services.status_stream import _STATUS_TABLES, status_snapshot, status_version


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _known_tables() -> set[str]:
    import app.models  # noqa: F401  (registers every mapper on Base.metadata)

    return set(Base.metadata.tables)


# --------------------------------------------------------------------------
# 1. The exact audit finding
# --------------------------------------------------------------------------

def test_every_status_table_exists_in_the_orm_metadata():
    known = _known_tables()
    missing = {
        logical: table for logical, table in _STATUS_TABLES.items()
        if table not in known
    }
    assert not missing, (
        f"status stream maps to non-existent table(s): {missing}. The signature "
        "helper swallows the error and returns a constant, so changes to these "
        "entities never wake the UI and nothing is logged."
    )


def test_documents_maps_to_generated_documents():
    assert _STATUS_TABLES["documents"] == "generated_documents", (
        "the generated-document model is backed by 'generated_documents'; "
        "mapping this key to 'documents' silently disables document realtime"
    )


# --------------------------------------------------------------------------
# 2. The follow-on defect: change markers must be real columns
# --------------------------------------------------------------------------

def test_declared_change_columns_exist_on_their_tables():
    """A declared marker column that does not exist degrades the stream silently."""
    tables = Base.metadata.tables
    import app.models  # noqa: F401

    wrong = {}
    for table, col in status_stream._TABLE_CHANGE_COLUMN.items():
        if col is None or table not in tables:
            continue
        if col not in tables[table].c:
            wrong[table] = col
    assert not wrong, (
        f"declared change column(s) absent from the schema: {wrong}. This is the "
        "bug that hid for a release: MAX(<missing column>) raised, was swallowed, "
        "and the signature quietly became count-only."
    )


def test_every_status_table_has_a_change_strategy():
    for logical, table in _STATUS_TABLES.items():
        assert table in status_stream._TABLE_CHANGE_COLUMN, (
            f"{logical} -> {table} has no declared change strategy, so it falls "
            "back to assuming 'updated_at' -- the assumption that broke"
        )


def test_change_markers_execute_without_falling_back(db):
    """Every mapped table must answer for real, not land in an except branch."""
    for logical, table in _STATUS_TABLES.items():
        marker = status_stream._change_marker(db, table)
        count = db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
        if count:
            assert marker != "none", (
                f"{logical} -> {table} has {count} row(s) but produced the "
                "fallback marker, meaning its query raised"
            )
        assert ":" in status_stream._table_signature(db, table)


# --------------------------------------------------------------------------
# 3. Behaviour: the token must move for each entity
# --------------------------------------------------------------------------

def test_document_insert_moves_the_version_token(db):
    before = status_version(db)
    db.execute(text(
        "INSERT INTO generated_documents (template_id, file_path, generated_at) "
        "VALUES (1, 'test/_token_probe.pdf', CURRENT_TIMESTAMP)"
    ))
    db.commit()
    try:
        assert status_version(db) != before, (
            "inserting a generated document did not change the status token; the "
            "SPA would never be told to refetch the documents list"
        )
        assert status_snapshot(db)["entities"]["documents"]["count"] >= 1, (
            "the snapshot reports zero documents, so the polling fallback is "
            "blind to them too"
        )
    finally:
        db.execute(text("DELETE FROM generated_documents WHERE file_path = 'test/_token_probe.pdf'"))
        db.commit()


def test_in_place_status_transition_moves_the_token(db):
    """The regression that mattered most: an UPDATE with no row-count change.

    candidate_assignments has no timestamp column, so a count-only signature
    could not see a candidate advancing through the workflow.
    """
    row = db.execute(text("SELECT id, status FROM candidate_assignments LIMIT 1")).first()
    created = False
    if row is None:
        # This table has no demand_id -- assignment is by job_category_id (checked
        # against the live schema) -- and both FKs are enforced, so use real ids
        # rather than hardcoded 1s.
        cand = db.execute(text("SELECT id FROM candidates LIMIT 1")).scalar()
        cat = db.execute(text("SELECT id FROM job_categories LIMIT 1")).scalar()
        if cand is None or cat is None:
            pytest.skip(
                "no candidate/job_category rows to build an assignment from; the "
                "strategy and change-marker tests above still guard the defect"
            )
        db.execute(
            text(
                "INSERT INTO candidate_assignments (candidate_id, job_category_id, status) "
                "VALUES (:c, :j, 'pending')"
            ),
            {"c": cand, "j": cat},
        )
        db.commit()
        row = db.execute(text(
            "SELECT id, status FROM candidate_assignments ORDER BY id DESC LIMIT 1"
        )).first()
        created = True

    assignment_id, original = row[0], row[1]
    before = status_version(db)
    try:
        new_status = "processing" if original != "processing" else "visa_issued"
        db.execute(
            text("UPDATE candidate_assignments SET status = :s WHERE id = :i"),
            {"s": new_status, "i": assignment_id},
        )
        db.commit()
        assert status_version(db) != before, (
            "advancing an assignment's status did not move the token. The row "
            "count is unchanged by an UPDATE, so a count-only signature cannot "
            "see workflow transitions -- the UI stays stale until a manual reload."
        )
    finally:
        if created:
            db.execute(text("DELETE FROM candidate_assignments WHERE id = :i"),
                       {"i": assignment_id})
        else:
            db.execute(text("UPDATE candidate_assignments SET status = :s WHERE id = :i"),
                       {"s": original, "i": assignment_id})
        db.commit()


def test_histogram_marker_detects_in_place_status_update(db):
    """Prove the no-timestamp strategy on a scratch table, independent of seeds.

    The real-table test above skips when the database has no candidate rows to
    build an assignment from, which would leave the mechanism that replaced the
    broken MAX(updated_at) assumption untested. This exercises it directly and
    also demonstrates *why* the old count-based signature could not work: the row
    count is identical before and after the UPDATE.
    """
    db.execute(text("DROP TABLE IF EXISTS _status_probe"))
    db.execute(text("CREATE TABLE _status_probe (id INTEGER PRIMARY KEY, status TEXT)"))
    db.execute(text("INSERT INTO _status_probe (id, status) VALUES (1, 'pending'), (2, 'pending')"))
    db.commit()
    status_stream._TABLE_CHANGE_COLUMN["_status_probe"] = None
    try:
        count_before = db.execute(text("SELECT COUNT(*) FROM _status_probe")).scalar()
        before = status_stream._change_marker(db, "_status_probe")

        db.execute(text("UPDATE _status_probe SET status = 'visa_issued' WHERE id = 1"))
        db.commit()

        after = status_stream._change_marker(db, "_status_probe")
        count_after = db.execute(text("SELECT COUNT(*) FROM _status_probe")).scalar()

        assert count_before == count_after, "sanity: an UPDATE must not change the count"
        assert before != after, (
            "the status histogram did not move for an in-place UPDATE, so "
            "workflow transitions remain invisible to the UI"
        )

        # And the full signature (count + marker) must move too.
        sig_after = status_stream._table_signature(db, "_status_probe")
        assert sig_after.startswith(f"{count_after}:") and after in sig_after
    finally:
        status_stream._TABLE_CHANGE_COLUMN.pop("_status_probe", None)
        db.execute(text("DROP TABLE IF EXISTS _status_probe"))
        db.commit()


# --------------------------------------------------------------------------
# 4. Degradation must stay graceful, but never silent
# --------------------------------------------------------------------------

def test_unusable_table_is_logged_not_swallowed(db):
    """Graceful degradation is fine; silent degradation is what hid the bug.

    Note: pytest's ``caplog`` cannot be used here. ``app.core.logging.setup_logging()``
    installs its own root handler at import time, which drops the capture handler
    pytest attaches, so records never reach caplog. Attaching a handler directly
    to the logger under test makes the assertion independent of root config.
    """
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):  # noqa: D102
            records.append(record)

    logger = logging.getLogger("dtc.status_stream")
    handler = _Collect(level=logging.WARNING)
    logger.addHandler(handler)
    prev_level = logger.level
    logger.setLevel(logging.WARNING)
    status_stream._SIGNATURE_FAILURES.discard("definitely_not_a_table")
    try:
        sig = status_stream._table_signature(db, "definitely_not_a_table")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)

    assert sig == "0:none", "must still degrade gracefully rather than raise"
    assert any("definitely_not_a_table" in r.getMessage() for r in records), (
        "a table the stream cannot read must produce a WARNING"
    )


def test_signature_rejects_unsafe_identifiers(db):
    with pytest.raises(ValueError):
        status_stream._table_signature(db, "candidates; DROP TABLE users")
