"""record whether an audited action succeeded or was denied

Revision ID: 9b5e7d3f2c81
Revises: 8a4d6c2b1e90
Create Date: 2026-08-18

WHY (measured 2026-08-18)
=========================
The operator asked to be able to find "denied or successful actions" on the
Audit Log page. That was impossible, and not because of a missing filter: the
denials were never recorded at all.

``require_permission`` / ``assert_permission`` in app/core/permissions.py raise
HTTP 403 and return. Nothing writes an audit row on that path, so a user
repeatedly probing for documents:print, candidates:delete or audit:view left
NO trace whatsoever in audit_logs. The trail contained only things that
succeeded -- which is the half of the story that matters least for a security
review. A grep for "denied" across the audit service found no hits.

Two columns are added:

  outcome        'success' | 'denied'   -- what actually happened
  permission     the permission key that was required and refused (denials)

``outcome`` is NOT NULL with a server default of 'success' so that every
pre-existing row keeps a truthful value: everything already in the table was,
by construction, an action that went through. Backfilling to 'success' is
therefore a statement of fact, not a guess.

An index on (outcome, occurred_at) backs the query the viewer actually issues
-- "denied actions, newest first, optionally within a date range" -- which
would otherwise scan the whole trail on a table that only ever grows.

SQLite is the deployment target, so this uses batch_alter_table for the ALTERs
and guards on the live column set to stay idempotent: the same tenant database
may be stamped by hand during provisioning, and a migration that explodes on
second run turns a routine deploy into an outage.
"""

import sqlalchemy as sa

from alembic import op

revision = "9b5e7d3f2c81"
down_revision = "8a4d6c2b1e90"
branch_labels = None
depends_on = None

_TABLE = "audit_logs"
_INDEX = "ix_audit_outcome_time"


def _existing_columns(bind) -> set[str]:
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def _existing_indexes(bind) -> set[str]:
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names():
        return set()
    return {i["name"] for i in insp.get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    cols = _existing_columns(bind)
    if not cols:
        # audit_logs does not exist yet in this database; the metadata create
        # path will build it with the new columns already present.
        return

    with op.batch_alter_table(_TABLE) as batch:
        if "outcome" not in cols:
            batch.add_column(
                sa.Column(
                    "outcome",
                    sa.String(16),
                    nullable=False,
                    server_default="success",
                )
            )
        if "permission" not in cols:
            batch.add_column(sa.Column("permission", sa.String(80), nullable=True))

    if _INDEX not in _existing_indexes(bind):
        op.create_index(_INDEX, _TABLE, ["outcome", "occurred_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if _INDEX in _existing_indexes(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
    cols = _existing_columns(bind)
    with op.batch_alter_table(_TABLE) as batch:
        if "permission" in cols:
            batch.drop_column("permission")
        if "outcome" in cols:
            batch.drop_column("outcome")
