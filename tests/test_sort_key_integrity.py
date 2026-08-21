"""
Integrity tests for the denormalised ``candidates.latest_demand_id`` sort key.

WHY THIS FILE EXISTS
--------------------
Migration 3b7d2e4f8a15 replaced the candidates-list correlated subquery sort
with a denormalised column maintained by DATABASE TRIGGERS (91% of the
endpoint's DB time; 318ms -> 13ms at 40k rows). Denormalisation trades a slow
query for a correctness risk: if the column goes stale, the list silently
paginates in the WRONG ORDER instead of failing loudly. That is a worse bug
than the slowness it replaced, so it needs a permanent guard.

Triggers were chosen over application-level maintenance precisely because
assignments are written through paths that bypass SQLAlchemy's ORM events --
``bulk_save_objects()``, raw SQL, and ``cascade="all, delete-orphan"``. These
tests drive every one of those paths and assert the column still equals the
recomputed truth value.

They also assert the property that actually matters to a user: the ordering
produced by the new indexed column is IDENTICAL to the ordering the old
correlated subquery produced, and paginating through it yields every row
exactly once (no gaps, no duplicates). A separate trigger also covers the
manual/support re-parent path (`UPDATE job_categories SET demand_id = ...`) so
moving a trade cannot leave assigned candidates sorted under the old demand.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  -- register every mapper
from app.db.session import Base
from app.models.candidate import Candidate, CandidateAssignment
from app.models.client import Client
from app.models.demand import Demand, JobCategory

# Same expression as migration 3b7d2e4f8a15 and scripts/verify_sort_key.py.
_TRUTH_FOR_ONE = """
    SELECT MAX(d.id)
      FROM candidate_assignments ca
      JOIN job_categories jc ON jc.id = ca.job_category_id
      JOIN demands d ON d.id = jc.demand_id
     WHERE ca.candidate_id = :cid
"""

_RECOMPUTE = """
 UPDATE candidates SET latest_demand_id = (
   SELECT MAX(d.id) FROM candidate_assignments ca
   JOIN job_categories jc ON jc.id = ca.job_category_id
   JOIN demands d ON d.id = jc.demand_id
   WHERE ca.candidate_id = candidates.id)
 WHERE id = %s;
"""

_RECOMPUTE_ASSIGNED_TO_TRADE = """
 UPDATE candidates SET latest_demand_id = (
   SELECT MAX(d.id) FROM candidate_assignments ca
   JOIN job_categories jc ON jc.id = ca.job_category_id
   JOIN demands d ON d.id = jc.demand_id
   WHERE ca.candidate_id = candidates.id)
 WHERE id IN (
   SELECT candidate_id FROM candidate_assignments WHERE job_category_id = %s
 );
"""


@pytest.fixture()
def db(tmp_path):
    """A fresh SQLite DB with the column + triggers the migration installs.

    The triggers are created here with the same DDL as the migration rather
    than by running alembic, so this test stays fast and independent of the
    migration chain -- but the DDL must stay in sync with the migration.
    """
    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    # Candidate.latest_demand_id is declared on the model, so create_all()
    # already made the column. Add only the index + triggers.
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_candidates_latest_demand_sort "
        "ON candidates (latest_demand_id DESC, id DESC)"
    ))
    session.execute(text(
        "CREATE TRIGGER trg_cand_latest_demand_ins AFTER INSERT ON "
        "candidate_assignments BEGIN " + _RECOMPUTE % "NEW.candidate_id" + " END"
    ))
    session.execute(text(
        "CREATE TRIGGER trg_cand_latest_demand_del AFTER DELETE ON "
        "candidate_assignments BEGIN " + _RECOMPUTE % "OLD.candidate_id" + " END"
    ))
    session.execute(text(
        "CREATE TRIGGER trg_cand_latest_demand_upd AFTER UPDATE OF "
        "candidate_id, job_category_id ON candidate_assignments BEGIN "
        + _RECOMPUTE % "NEW.candidate_id"
        + _RECOMPUTE % "OLD.candidate_id"
        + " END"
    ))
    session.execute(text(
        "CREATE TRIGGER trg_cand_latest_demand_jc_reparent "
        "AFTER UPDATE OF demand_id ON job_categories "
        "WHEN OLD.demand_id IS NOT NEW.demand_id BEGIN "
        + _RECOMPUTE_ASSIGNED_TO_TRADE % "NEW.id"
        + " END"
    ))

    # Seed: 1 client, 5 demands each with 1 job category, 10 candidates.
    session.add(Client(company_name="ACME"))
    session.flush()
    for i in range(1, 6):
        session.add(Demand(file_number=f"F{i}", client_id=1))
    session.flush()
    for i in range(1, 6):
        session.add(JobCategory(demand_id=i, trade="Mason"))
    for i in range(1, 11):
        session.add(Candidate(full_name=f"Worker {i}", passport_no=f"P{i:05d}"))
    session.commit()
    yield session
    session.close()


def _truth(db, cid: int):
    return db.execute(text(_TRUTH_FOR_ONE), {"cid": cid}).scalar()


def _stored(db, cid: int):
    return db.execute(
        text("SELECT latest_demand_id FROM candidates WHERE id = :cid"), {"cid": cid}
    ).scalar()


def _assert_no_drift(db, msg: str):
    ids = [r[0] for r in db.execute(text("SELECT id FROM candidates")).fetchall()]
    bad = [(c, _stored(db, c), _truth(db, c)) for c in ids if _stored(db, c) != _truth(db, c)]
    assert not bad, f"{msg}: latest_demand_id drifted (id, stored, truth) -> {bad}"


# ── The eight mutation paths ────────────────────────────────────────────────

def test_orm_insert_sets_sort_key(db):
    db.add(CandidateAssignment(candidate_id=1, job_category_id=3))
    db.commit()
    assert _stored(db, 1) == 3
    _assert_no_drift(db, "after ORM insert")


def test_higher_demand_wins(db):
    db.add(CandidateAssignment(candidate_id=1, job_category_id=2))
    db.commit()
    db.add(CandidateAssignment(candidate_id=1, job_category_id=5))
    db.commit()
    assert _stored(db, 1) == 5, "newest linked demand must win"
    _assert_no_drift(db, "after second ORM insert")


def test_orm_delete_falls_back_to_next_newest(db):
    db.add(CandidateAssignment(candidate_id=1, job_category_id=2))
    db.add(CandidateAssignment(candidate_id=1, job_category_id=5))
    db.commit()
    assert _stored(db, 1) == 5
    victim = (
        db.query(CandidateAssignment)
        .filter_by(candidate_id=1, job_category_id=5)
        .one()
    )
    db.delete(victim)
    db.commit()
    assert _stored(db, 1) == 2, "must fall back, not keep the deleted demand"
    _assert_no_drift(db, "after ORM delete")


def test_last_assignment_removed_returns_to_null(db):
    db.add(CandidateAssignment(candidate_id=4, job_category_id=1))
    db.commit()
    assert _stored(db, 4) == 1
    db.delete(db.query(CandidateAssignment).filter_by(candidate_id=4).one())
    db.commit()
    assert _stored(db, 4) is None, "no assignments => NULL, so the row sorts last"
    _assert_no_drift(db, "after removing the only assignment")


def test_bulk_save_objects_bypasses_orm_events_but_not_triggers(db):
    """bulk_save_objects skips ORM event hooks -- the reason triggers were used."""
    db.bulk_save_objects([CandidateAssignment(candidate_id=2, job_category_id=4)])
    db.commit()
    assert _stored(db, 2) == 4
    _assert_no_drift(db, "after bulk_save_objects")


def test_raw_sql_insert_maintains_sort_key(db):
    db.execute(text(
        "INSERT INTO candidate_assignments (candidate_id, job_category_id) VALUES (3, 2)"
    ))
    db.commit()
    assert _stored(db, 3) == 2
    _assert_no_drift(db, "after raw SQL insert")


def test_raw_sql_reassignment_updates_both_candidates(db):
    """Moving an assignment must fix the OLD owner as well as the new one."""
    db.add(CandidateAssignment(candidate_id=5, job_category_id=4))
    db.commit()
    assert _stored(db, 5) == 4
    assert _stored(db, 6) is None
    db.execute(text("UPDATE candidate_assignments SET candidate_id = 6 WHERE candidate_id = 5"))
    db.commit()
    assert _stored(db, 6) == 4, "new owner must gain the key"
    assert _stored(db, 5) is None, "old owner must LOSE it (stale value bug)"
    _assert_no_drift(db, "after reassignment")


def test_cascade_delete_of_job_category_maintains_sort_key(db):
    """delete-orphan cascade deletes assignments without explicit app code."""
    db.add(CandidateAssignment(candidate_id=7, job_category_id=2))
    db.add(CandidateAssignment(candidate_id=7, job_category_id=1))
    db.commit()
    assert _stored(db, 7) == 2
    db.delete(db.get(JobCategory, 2))
    db.commit()
    assert _stored(db, 7) == 1, "must fall back after cascade delete"
    _assert_no_drift(db, "after JobCategory cascade delete")


def test_cascade_delete_of_candidate_leaves_no_drift(db):
    db.add(CandidateAssignment(candidate_id=8, job_category_id=3))
    db.commit()
    db.delete(db.get(Candidate, 8))
    db.commit()
    _assert_no_drift(db, "after Candidate cascade delete")


def test_raw_sql_trade_reparent_recomputes_assigned_candidates_atomically(db):
    """Manual/support re-parenting cannot leave candidates sorted under the old demand."""
    db.add(CandidateAssignment(candidate_id=1, job_category_id=2))
    db.add(CandidateAssignment(candidate_id=2, job_category_id=2))
    db.add(CandidateAssignment(candidate_id=1, job_category_id=5))
    db.commit()
    assert _stored(db, 1) == 5
    assert _stored(db, 2) == 2

    db.execute(text("UPDATE job_categories SET demand_id = 4 WHERE id = 2"))
    # The trigger runs inside the same UPDATE statement/transaction; before commit
    # the new deterministic truth is already visible on the same connection.
    assert _stored(db, 2) == 4
    assert _stored(db, 1) == 5, "candidate with a newer separate demand must keep it"
    db.commit()
    _assert_no_drift(db, "after job_category re-parent")


# ── The property that matters to the user ───────────────────────────────────

def _legacy_order(db):
    """Ordering as produced by the ORIGINAL correlated-subquery implementation."""
    sub = (
        db.query(func.max(Demand.id))
        .select_from(CandidateAssignment)
        .join(JobCategory, JobCategory.id == CandidateAssignment.job_category_id)
        .join(Demand, Demand.id == JobCategory.demand_id)
        .filter(CandidateAssignment.candidate_id == Candidate.id)
        .correlate(Candidate)
        .scalar_subquery()
    )
    return [
        c.id
        for c in db.query(Candidate)
        .order_by(sub.desc().nullslast(), Candidate.id.desc())
        .all()
    ]


def _new_order(db, skip=0, limit=None):
    q = db.query(Candidate).order_by(
        Candidate.latest_demand_id.desc().nullslast(), Candidate.id.desc()
    )
    if limit is not None:
        q = q.offset(skip).limit(limit)
    return [c.id for c in q.all()]


def test_new_ordering_is_identical_to_the_legacy_subquery(db):
    """The optimisation must not change what the user sees, only its cost."""
    for cid, jc in ((1, 5), (1, 2), (2, 4), (3, 1), (5, 3), (9, 5), (10, 1)):
        db.add(CandidateAssignment(candidate_id=cid, job_category_id=jc))
    db.commit()
    legacy, new = _legacy_order(db), _new_order(db)
    assert new == legacy, f"ordering changed!\nlegacy={legacy}\nnew   ={new}"


def test_pagination_covers_every_row_exactly_once(db):
    """A correlated-subquery sort once caused short/overlapping pages."""
    for cid, jc in ((1, 5), (2, 4), (3, 1), (4, 4), (5, 3), (6, 2)):
        db.add(CandidateAssignment(candidate_id=cid, job_category_id=jc))
    # a candidate with SEVERAL assignments is the case that broke naive joins
    db.add(CandidateAssignment(candidate_id=1, job_category_id=2))
    db.add(CandidateAssignment(candidate_id=1, job_category_id=3))
    db.commit()

    full = _new_order(db)
    paged: list[int] = []
    for skip in range(0, len(full), 3):
        paged += _new_order(db, skip=skip, limit=3)
    assert paged == full, "paged traversal diverged from the full ordering"
    assert len(set(paged)) == len(paged), f"duplicate rows across pages: {paged}"
    assert len(paged) == 10, "every candidate must appear exactly once"


def test_candidates_without_assignments_sort_last(db):
    db.add(CandidateAssignment(candidate_id=9, job_category_id=1))
    db.commit()
    order = _new_order(db)
    assert order[0] == 9, "the only assigned candidate must sort first"
    assert _stored(db, 9) == 1
    unassigned = [c for c in order[1:]]
    assert all(_stored(db, c) is None for c in unassigned)
