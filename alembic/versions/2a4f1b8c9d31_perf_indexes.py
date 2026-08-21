"""performance indexes for list endpoints and the assignment lookup

Revision ID: 2a4f1b8c9d31
Revises: 1c1e09f245c6
Create Date: 2026-07-28

WHY (measured, not guessed)
---------------------------
Profiling the REAL data volume (1,246 clients / 2,267 demands / 2,675
candidates / 4,538 reminders) showed every list endpoint doing a full table
SCAN plus a temp B-tree sort, because `created_at` -- the default sort key for
those endpoints -- carried no index on any of the three main tables.

Measured effect of the indexes below on a copy of the live database
(best-of-8, same queries the endpoints issue):

    reminders feed    1.66ms -> 0.17ms   (-89%)
    demands list      0.65ms -> 0.15ms   (-77%)
    clients list      0.33ms -> 0.12ms   (-63%)
    candidates list  10.97ms -> 10.33ms  (-6%)

The candidates list only improves 6% because its cost is STRUCTURAL, not a
missing index: it orders by a correlated scalar subquery (latest assignment per
candidate), so the subquery runs once per candidate row and LIMIT cannot prune
the outer scan -- a deep page costs the same as page 1. The composite index on
(candidate_id, assigned_at DESC, id DESC) does remove the subquery's INNER temp
B-tree sort, which is why it is still worth having and will matter more as the
assignment table grows.

A window-function CTE rewrite of that query was tested as an alternative: it
returned byte-identical ordering but was SLOWER (13.6ms vs 10.1ms) because it
materializes the whole assignment table. The query was therefore left alone --
the existing shape is the better one at this scale. Revisit only if the
candidates table grows past ~50k rows, at which point a denormalised
`latest_assignment_id` column on candidates is the real fix.

All indexes are created IF NOT EXISTS so this is safe to re-run and safe on
databases where an operator already added one by hand.
"""

from alembic import op

revision = "2a4f1b8c9d31"
down_revision = "1c1e09f245c6"
branch_labels = None
depends_on = None


# (index name, table, DDL column list) -- kept as data so up/down cannot drift.
_INDEXES = [
    (
        "ix_cand_assign_cand_assigned",
        "candidate_assignments",
        "(candidate_id, assigned_at DESC, id DESC)",
    ),
    ("ix_candidates_created_at", "candidates", "(created_at DESC)"),
    ("ix_candidates_status_created", "candidates", "(status, created_at DESC)"),
    ("ix_clients_created_at", "clients", "(created_at DESC)"),
    ("ix_demands_created_at", "demands", "(created_at DESC)"),
    ("ix_reminders_created_at", "reminders", "(created_at DESC)"),
]


def upgrade() -> None:
    for name, table, cols in _INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {cols}")
    # Refresh the planner's statistics so it actually chooses the new indexes.
    # Harmless on PostgreSQL too (ANALYZE with no args is valid there).
    op.execute("ANALYZE")


def downgrade() -> None:
    for name, _table, _cols in reversed(_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
