"""store the per-tenant typeface KEY so office branding covers fonts too

Revision ID: b2d7f9a3e614
Revises: a1c6e8b4d072
Create Date: 2026-08-19

WHY (2026-08-19)
================
The operator asked for the topbar and document branding to follow the active
office automatically, naming three things: "logo/colors/fonts". Logo and colour
were already stored per tenant (``logo_filename``, ``primary_color``) and, since
the branding work, actually applied. The FONT was not stored anywhere at all --
``applyTenantBranding()`` read ``tenant.fontKey``, which no row, no boot payload
and no schema ever produced, so it resolved to the system stack for every
office forever. The code path existed; the data did not. This revision supplies
the data.

WHY A KEY AND NOT A font-family STRING
--------------------------------------
The value ends up written into a live CSS custom property (``--brand-font``).
Storing a resolved declaration would mean:

  * a stray ``}`` or ``;`` in the stored text terminates the rule and breaks the
    whole shell's typography -- a cosmetic field taking down layout; and
  * the font list would be forked between this column and
    app/frontend/src/lib/branding.ts, free to drift.

So the column holds a short token ("system" | "inter" | "roboto" | "lato" |
"serif" | "mono") that the frontend resolves against its own allow-list. An
unknown or legacy value falls back to the system stack instead of rendering an
invalid declaration.

NO INDEX, DELIBERATELY
----------------------
``font_key`` is never a query predicate -- it is read as part of the single
already-resolved tenant row. Index reconciliation in
scripts/apply_tenant_migrations.py matches BY NAME, so declaring ``index=True``
on the model would have it auto-name ``ix_tenants_font_key`` and re-add it on
every deploy. See app/models/README_INDEXES.md.

BACKFILL
--------
Existing rows are given "system" explicitly rather than left NULL. NULL and
"system" resolve identically in the frontend, but a concrete value makes the
branding form show the office what it is actually using instead of an empty
select that looks unconfigured.

DOGAR IS UNAFFECTED
-------------------
The root/control portal has no tenant row, so it takes no font from here at all;
``applyTenantBranding()`` is a no-op without a resolved tenant and Dogar keeps
the typography authored in index.css. That is a hard requirement of the brief.
"""

import sqlalchemy as sa

from alembic import op

revision = "b2d7f9a3e614"
down_revision = "a1c6e8b4d072"
branch_labels = None
depends_on = None

_TABLE = "tenants"
_COLUMN = "font_key"
_DEFAULT = "system"


def _existing_columns(bind) -> set[str]:
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    cols = _existing_columns(bind)
    if not cols:
        # `tenants` does not exist in this database (a tenant's OWN db has no
        # tenants table -- the registry lives in the control db only). The
        # metadata create path builds it with the column already present.
        return

    if _COLUMN not in cols:
        with op.batch_alter_table(_TABLE) as batch:
            batch.add_column(
                sa.Column(_COLUMN, sa.String(30), nullable=True, server_default=_DEFAULT)
            )
        # Backfill explicitly: server_default only covers rows inserted AFTER
        # the ALTER on some backends, and SQLite's ALTER fills existing rows
        # with the default while Postgres 10 and earlier does not. Doing it
        # here makes the outcome identical on both.
        op.execute(
            sa.text(f"UPDATE {_TABLE} SET {_COLUMN} = :d WHERE {_COLUMN} IS NULL").bindparams(
                d=_DEFAULT
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _existing_columns(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
