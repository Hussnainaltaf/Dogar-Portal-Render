"""repair schema drift: tenants branding columns + message_logs table

Revision ID: 4c8e1f2a6b93
Revises: 3b7d2e4f8a15
Create Date: 2026-07-29

WHY
---
Found by installing the release ZIP from scratch and driving it over HTTP, not
by the unit suite. A customer's schema is built by `alembic upgrade head`; the
test suite's schema is built by `Base.metadata.create_all()`. Those two had
silently drifted, so these defects were invisible to 275 passing tests:

  * `tenants.license_number` and `tenants.office_address` are declared on the
    Tenant model but were never added to the baseline migration. Every request
    logged `no such column: tenants.license_number` because the tenant
    resolver's `custom_domain` lookup SELECTs all mapped columns. The lookup is
    wrapped in a try/except, so the app kept serving -- it just logged an error
    on *every* request and could never match a custom domain. That is exactly
    the kind of failure that hides for months.

  * The `message_logs` table (MessageLog, the Twilio SMS/WhatsApp audit trail)
    was missing from the migrations entirely. `app/db/init_db.py` papers over
    this at startup with `MessageLog.__table__.create(checkfirst=True)`, which
    is why the messaging endpoints appeared to work -- but a table created
    outside Alembic is invisible to migration history and would not exist at
    all on a database provisioned by migrations alone (e.g. the PostgreSQL
    deployment path, where init_db's create is not part of the documented
    procedure).

This migration is deliberately idempotent: the dev SQLite databases already
have these objects (create_all / init_db added them), so it must be safe to
run against a database where they are already present as well as a virgin one.

The regression guard lives in tests/test_release_acceptance.py, which builds a
database with `alembic upgrade head` and diffs it against the model metadata --
covering every table, so the next drift of this kind fails in CI instead of
being found by a customer.
"""

import sqlalchemy as sa

from alembic import op

revision = "4c8e1f2a6b93"
down_revision = "3b7d2e4f8a15"
branch_labels = None
depends_on = None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ---- tenants: the two letterhead/branding columns -----------------------
    # Added with a server_default of '' so existing rows get a usable value and
    # the app's `default=""` stays consistent. They are nullable, matching the
    # model, so no backfill is required beyond the default.
    existing = _existing_columns("tenants")
    if "tenants" in inspector.get_table_names():
        with op.batch_alter_table("tenants", schema=None) as batch_op:
            if "license_number" not in existing:
                batch_op.add_column(
                    sa.Column(
                        "license_number", sa.String(length=100),
                        nullable=True, server_default="",
                    )
                )
            if "office_address" not in existing:
                batch_op.add_column(
                    sa.Column(
                        "office_address", sa.String(length=400),
                        nullable=True, server_default="",
                    )
                )

    # ---- message_logs: the outbound SMS / WhatsApp audit trail --------------
    if "message_logs" not in inspector.get_table_names():
        op.create_table(
            "message_logs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("channel", sa.String(length=20), nullable=True),
            sa.Column("to_number", sa.String(length=50), nullable=True),
            sa.Column("body", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=True),
            sa.Column("provider_sid", sa.String(length=80), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("candidate_id", sa.Integer(), nullable=True),
            sa.Column("sent_by", sa.Integer(), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(),
                server_default=sa.func.now(), nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("message_logs", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_message_logs_id"), ["id"], unique=False
            )
            batch_op.create_index(
                batch_op.f("ix_message_logs_to_number"),
                ["to_number"], unique=False,
            )
            batch_op.create_index(
                batch_op.f("ix_message_logs_status"), ["status"], unique=False
            )
            batch_op.create_index(
                batch_op.f("ix_message_logs_candidate_id"),
                ["candidate_id"], unique=False,
            )
            batch_op.create_index(
                batch_op.f("ix_message_logs_sent_by"),
                ["sent_by"], unique=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "message_logs" in inspector.get_table_names():
        op.drop_table("message_logs")

    existing = _existing_columns("tenants")
    if "tenants" in inspector.get_table_names():
        with op.batch_alter_table("tenants", schema=None) as batch_op:
            if "office_address" in existing:
                batch_op.drop_column("office_address")
            if "license_number" in existing:
                batch_op.drop_column("license_number")
