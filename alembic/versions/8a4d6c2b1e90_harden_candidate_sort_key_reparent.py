"""harden candidate sort key when a trade is re-parented

Revision ID: 8a4d6c2b1e90
Revises: 7f1b5c3e9a48
Create Date: 2026-08-04

Migration 3b7d2e4f8a15 denormalised candidates.latest_demand_id and kept it
correct for candidate_assignment inserts/updates/deletes. The remaining manual
edge case was moving an existing job_category to another demand. Even if that is
not exposed by the UI, support SQL or future code can do it; without this trigger
all assigned candidates would keep the old sort key until an operator ran the
verifier.

This migration closes that gap below the application layer. The trigger runs in
the same transaction as the UPDATE job_categories SET demand_id = ... statement,
recomputes only candidates assigned to the moved trade, and therefore keeps the
indexed candidate ordering deterministic for ORM, raw SQL, and bulk paths.
"""

from alembic import op

revision = "8a4d6c2b1e90"
down_revision = "7f1b5c3e9a48"
branch_labels = None
depends_on = None

_TRIGGER_NAME = "trg_cand_latest_demand_jc_reparent"
_FUNCTION_NAME = "fn_cand_latest_demand_sync_job_category_reparent"

_TRUTH_SUBQUERY = """
    SELECT MAX(d.id)
      FROM candidate_assignments ca
      JOIN job_categories jc ON jc.id = ca.job_category_id
      JOIN demands d ON d.id = jc.demand_id
     WHERE ca.candidate_id = candidates.id
"""


def _recompute_assigned_to(job_category_ref: str) -> str:
    """UPDATE candidates assigned to one job_category row."""
    return (
        "UPDATE candidates SET latest_demand_id = ("
        + _TRUTH_SUBQUERY
        + ") WHERE id IN ("
        "SELECT candidate_id FROM candidate_assignments "
        f"WHERE job_category_id = {job_category_ref});"
    )


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON job_categories")
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION {_FUNCTION_NAME}()
            RETURNS TRIGGER AS $$
            BEGIN
              IF (NEW.demand_id IS DISTINCT FROM OLD.demand_id) THEN
                UPDATE candidates SET latest_demand_id = (
                  SELECT MAX(d.id) FROM candidate_assignments ca
                    JOIN job_categories jc ON jc.id = ca.job_category_id
                    JOIN demands d ON d.id = jc.demand_id
                   WHERE ca.candidate_id = candidates.id)
                 WHERE id IN (
                   SELECT candidate_id FROM candidate_assignments
                    WHERE job_category_id = NEW.id
                 );
              END IF;
              RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            f"CREATE TRIGGER {_TRIGGER_NAME} AFTER UPDATE OF demand_id ON "
            f"job_categories FOR EACH ROW EXECUTE FUNCTION {_FUNCTION_NAME}()"
        )
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME}")
        op.execute(
            f"CREATE TRIGGER {_TRIGGER_NAME} "
            "AFTER UPDATE OF demand_id ON job_categories "
            "WHEN OLD.demand_id IS NOT NEW.demand_id BEGIN "
            + _recompute_assigned_to("NEW.id")
            + " END"
        )

    # Defensive one-time repair for any database where the documented gap was
    # exercised before this migration landed.
    op.execute("UPDATE candidates SET latest_demand_id = (" + _TRUTH_SUBQUERY + ")")
    op.execute("ANALYZE")


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON job_categories")
        op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION_NAME}()")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME}")
