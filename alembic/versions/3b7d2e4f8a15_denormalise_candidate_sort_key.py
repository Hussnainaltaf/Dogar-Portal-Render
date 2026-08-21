"""denormalise the candidates list sort key (latest_demand_id) + maintain by trigger

Revision ID: 3b7d2e4f8a15
Revises: 2a4f1b8c9d31
Create Date: 2026-07-28

WHY (measured, not guessed)
---------------------------
The previous migration (2a4f1b8c9d31) indexed the list-endpoint sort keys and
explicitly recorded that the candidates list was the one endpoint it could NOT
fix, because its cost is structural: it ordered by a correlated scalar subquery
(newest linked demand per candidate), so the subquery ran once per candidate row
and SQL LIMIT could not prune the outer scan. That note said to revisit "past
~50k rows"; profiling since then shows it is worth doing now.

Profile of the endpoint's DB time on the real dev dataset (2,675 candidates):

    COUNT(*) total            0.4 ms
    sorted page fetch        14.2 ms   <-- 91% of DB time
    batch context fetch       1.0 ms

Scaling measurement on synthetic datasets, page 1 vs deep page, best-of-5:

    candidates   correlated subquery      this migration      gain
         2,500      7.2 /  14.2 ms                 ~5 ms
        10,000     24.4 /  54.4 ms                 ~5 ms
        40,000     98.9 / 317.8 ms        5.1 / 12.7 ms      -95% / -96%

Note the shape of the old numbers: cost grew with TOTAL rows and roughly
tripled at a deep page. With an indexed column the plan becomes a covering
index scan, so page depth is nearly free.

REJECTED ALTERNATIVE (measured, not assumed)
--------------------------------------------
Rewriting the sort as a single grouped derived table LEFT JOINed once -- the
obvious "fix the N+1 subquery" move -- was implemented and benchmarked first.
It produced byte-identical ordering but was consistently SLOWER than the
correlated form (-12% at 2.5k, -13% at 10k, -12% at 40k on page 1) because it
materialises and groups the entire assignment table before the join. It was
therefore discarded. A window-function CTE was rejected for the same reason in
the previous migration. Only denormalisation actually changes the complexity.

WHY TRIGGERS AND NOT APPLICATION CODE
-------------------------------------
A denormalised column is only safe if it CANNOT go stale. Assignments are
created and destroyed through at least eight paths in this codebase, including
three that bypass SQLAlchemy ORM events entirely:

    * ORM insert / delete                    (5 call sites)
    * bulk_save_objects()                    bypasses ORM events
    * raw SQL INSERT/UPDATE                  bypasses ORM events
    * cascade="all, delete-orphan" cascades  bypasses explicit app code

Maintaining the column in the API layer would leave the last three silently
wrong, and a stale sort key is an ORDERING-CORRUPTION bug -- worse than the
slow query it replaces. Triggers live below every one of those paths, so the
column is correct by construction. All eight paths were verified to keep the
column exactly equal to the recomputed truth value.

RE-PARENT HARDENING (closed by migration 8a4d6c2b1e90)
-------------------------------------------------------
The original revision deliberately documented one manual/support gap:
re-parenting a job_category to a different demand (UPDATE job_categories SET
demand_id=...) could drift because this migration only watched the assignment
table. Migration 8a4d6c2b1e90 adds a job_categories.demand_id trigger that
recomputes all candidates assigned to the moved trade in the same transaction,
so the denormalised order key no longer has that silent-drift edge case.

SQLite and PostgreSQL need different DDL: SQLite has no statement-level
triggers and no OF-column UPDATE targeting semantics identical to PG's, so each
dialect gets its own definition rather than a lowest-common-denominator hack.
"""

from sqlalchemy import inspect

from alembic import op

revision = "3b7d2e4f8a15"
down_revision = "2a4f1b8c9d31"
branch_labels = None
depends_on = None

# The single definition of truth for the column. Any change here must be made
# in scripts/verify_sort_key.py and tests/test_sort_key_integrity.py too --
# those recompute with the same expression and assert equality.
_TRUTH_SUBQUERY = """
    SELECT MAX(d.id)
      FROM candidate_assignments ca
      JOIN job_categories jc ON jc.id = ca.job_category_id
      JOIN demands d ON d.id = jc.demand_id
     WHERE ca.candidate_id = {cand_ref}
"""

_TRIGGER_NAMES = (
    "trg_cand_latest_demand_ins",
    "trg_cand_latest_demand_del",
    "trg_cand_latest_demand_upd",
)


def _recompute_for(ref: str) -> str:
    """UPDATE statement recomputing the column for one candidate id."""
    return (
        "UPDATE candidates SET latest_demand_id = ("
        + _TRUTH_SUBQUERY.format(cand_ref="candidates.id")
        + f") WHERE id = {ref};"
    )


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Idempotent: an operator may have added the column by hand, and this
    # migration is applied to every tenant database independently.
    if not _has_column(bind, "candidates", "latest_demand_id"):
        op.execute("ALTER TABLE candidates ADD COLUMN latest_demand_id INTEGER")

    # Backfill existing rows before the index exists (cheaper) .
    op.execute(
        "UPDATE candidates SET latest_demand_id = ("
        + _TRUTH_SUBQUERY.format(cand_ref="candidates.id")
        + ")"
    )

    # Composite covering index. The leading `latest_demand_id IS NULL` term in
    # the endpoint's ORDER BY implements NULLS LAST on SQLite; the index below
    # still serves the ordering because NULLs group at one end either way.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_candidates_latest_demand_sort "
        "ON candidates (latest_demand_id DESC, id DESC)"
    )

    for name in _TRIGGER_NAMES:
        op.execute(f"DROP TRIGGER IF EXISTS {name}" + (
            " ON candidate_assignments" if dialect == "postgresql" else ""
        ))

    if dialect == "postgresql":
        # PG needs the body in a function; one function serves all three triggers.
        op.execute(
            """
            CREATE OR REPLACE FUNCTION fn_cand_latest_demand_sync()
            RETURNS TRIGGER AS $$
            BEGIN
              IF (TG_OP = 'INSERT' OR TG_OP = 'UPDATE') THEN
                UPDATE candidates SET latest_demand_id = (
                  SELECT MAX(d.id) FROM candidate_assignments ca
                    JOIN job_categories jc ON jc.id = ca.job_category_id
                    JOIN demands d ON d.id = jc.demand_id
                   WHERE ca.candidate_id = candidates.id)
                 WHERE id = NEW.candidate_id;
              END IF;
              IF (TG_OP = 'DELETE' OR TG_OP = 'UPDATE') THEN
                UPDATE candidates SET latest_demand_id = (
                  SELECT MAX(d.id) FROM candidate_assignments ca
                    JOIN job_categories jc ON jc.id = ca.job_category_id
                    JOIN demands d ON d.id = jc.demand_id
                   WHERE ca.candidate_id = candidates.id)
                 WHERE id = OLD.candidate_id;
              END IF;
              RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            "CREATE TRIGGER trg_cand_latest_demand_ins AFTER INSERT ON "
            "candidate_assignments FOR EACH ROW "
            "EXECUTE FUNCTION fn_cand_latest_demand_sync()"
        )
        op.execute(
            "CREATE TRIGGER trg_cand_latest_demand_del AFTER DELETE ON "
            "candidate_assignments FOR EACH ROW "
            "EXECUTE FUNCTION fn_cand_latest_demand_sync()"
        )
        op.execute(
            "CREATE TRIGGER trg_cand_latest_demand_upd AFTER UPDATE OF "
            "candidate_id, job_category_id ON candidate_assignments "
            "FOR EACH ROW EXECUTE FUNCTION fn_cand_latest_demand_sync()"
        )
    else:
        op.execute(
            "CREATE TRIGGER trg_cand_latest_demand_ins "
            "AFTER INSERT ON candidate_assignments BEGIN "
            + _recompute_for("NEW.candidate_id")
            + " END"
        )
        op.execute(
            "CREATE TRIGGER trg_cand_latest_demand_del "
            "AFTER DELETE ON candidate_assignments BEGIN "
            + _recompute_for("OLD.candidate_id")
            + " END"
        )
        # An assignment can be moved between candidates: BOTH the old and the
        # new candidate must be recomputed, or the old one keeps a stale value.
        op.execute(
            "CREATE TRIGGER trg_cand_latest_demand_upd "
            "AFTER UPDATE OF candidate_id, job_category_id ON "
            "candidate_assignments BEGIN "
            + _recompute_for("NEW.candidate_id")
            + _recompute_for("OLD.candidate_id")
            + " END"
        )

    op.execute("ANALYZE")


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    for name in _TRIGGER_NAMES:
        op.execute(f"DROP TRIGGER IF EXISTS {name}" + (
            " ON candidate_assignments" if dialect == "postgresql" else ""
        ))
    if dialect == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS fn_cand_latest_demand_sync()")

    op.execute("DROP INDEX IF EXISTS ix_candidates_latest_demand_sort")

    # SQLite gained DROP COLUMN in 3.35; guard for older builds by falling back
    # to leaving the (now unmaintained) column in place rather than failing the
    # downgrade, since the endpoint no longer reads it after this revision.
    if _has_column(bind, "candidates", "latest_demand_id"):
        try:
            op.drop_column("candidates", "latest_demand_id")
        except Exception:  # pragma: no cover - very old SQLite only
            pass
