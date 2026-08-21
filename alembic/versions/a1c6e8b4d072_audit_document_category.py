"""record the document type on each audited document action

Revision ID: a1c6e8b4d072
Revises: 9b5e7d3f2c81
Create Date: 2026-08-18

WHY (measured 2026-08-18)
=========================
The operator asked to filter the Audit Log "by office, user, document type and
date range". User and date range were already filterable. Document type was
not, and the reason is worth recording because the obvious fix is wrong.

The document's type IS already in the trail -- ``log_document_action`` writes
``{"category": ...}`` into ``after_json``. So it looks like no schema change is
needed: just filter on the JSON. That approach was rejected:

  * ``after_json`` is a ``sqlalchemy.JSON`` column and this codebase runs BOTH
    SQLite (one file per tenant) and PostgreSQL (one DB per tenant) -- see
    ``app.core.tenancy.is_postgres_tenants``. Reaching inside the document
    means ``json_extract(after_json, '$.category')`` on SQLite and
    ``after_json ->> 'category'`` on Postgres. A filter that only works on the
    backend the developer happened to test is worse than no filter, because it
    returns an EMPTY page rather than an error -- and an empty page reads as
    "there are no documents of that type".
  * The portable fallback -- casting the JSON to text and running LIKE -- would
    match the substring anywhere in the blob, including inside a candidate's
    name or address, so "visa" could match an unrelated row.
  * Neither form can use an index, and this is the one table in the schema
    guaranteed to grow forever.

Hence a first-class, indexable column. It is NULL for every row that is not a
document action, which is honest: those rows have no document type.

Backfill: rows written before this revision keep NULL rather than being
reconstructed from ``after_json``. A backfill would have to do exactly the
non-portable JSON reach-in described above, and getting it wrong would put
FABRICATED categories into an audit trail -- the one table where a plausible
guess is worse than a blank.
"""

import sqlalchemy as sa

from alembic import op

revision = "a1c6e8b4d072"
down_revision = "9b5e7d3f2c81"
branch_labels = None
depends_on = None

_TABLE = "audit_logs"
_INDEX = "ix_audit_doc_category"


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
        # audit_logs does not exist in this database yet; the metadata create
        # path will build it with the column already present.
        return

    if "doc_category" not in cols:
        with op.batch_alter_table(_TABLE) as batch:
            batch.add_column(sa.Column("doc_category", sa.String(60), nullable=True))

    if _INDEX not in _existing_indexes(bind):
        op.create_index(_INDEX, _TABLE, ["doc_category"])


def downgrade() -> None:
    bind = op.get_bind()
    if _INDEX in _existing_indexes(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
    if "doc_category" in _existing_columns(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column("doc_category")
