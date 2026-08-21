"""fill the blank "Address" row in Section C of the Allied Bank form

Revision ID: 7f1b5c3e9a48
Revises: 6e0a4b2d8f37
Create Date: 2026-08-02

WHY
---
Found by rasterising a generated Allied Bank form while verifying the CNIC
grid fix (revision 6e0a4b2d8f37). Section C, "Overseas Employment Promoter
(O.E.P.) Details", is a three-row table:

    | Name    | Dogar Trading Corporation                        |
    | Address |                        <-- BLANK                 |
    |         |        | O.E.P. License Number | 1338/SKT       |

The "Name" and "O.E.P. License Number" rows are bound to company settings.
The "Address" row was never bound to anything, so it printed empty on every
Allied Bank form this portal has produced -- a blank mandatory field on a bank
deposit form, i.e. a form a bank can reject, not a cosmetic gap.

Nothing in the suite caught it because a *missing* field cannot fail a
geometry audit: scripts/audit_template_geometry.py can only check the fields
that exist. The data was always there and already exposed to templates --
`customer_address` has been in the overlay engine's company-settings map all
along (app/services/overlay_engine.py) -- it simply had no field on template 1.

WHAT THIS CHANGES
-----------------
Inserts one `text` field bound to `customer_address` (-> company_settings.address).

Geometry measured on the *rendered* PDF at 300dpi with a per-row darkness
profile, the same method the previous two revisions used (a whole-band profile
picks up rules bleeding in from adjacent rows, which is how an earlier attempt
at the O.E.P. License placement went wrong):

    Section C rules      y 547.32 (heading), 517.68, 503.28, 489.24, 474.72
    Address value cell   x 145.32 .. 609.72,  y 489.24 .. 503.28
    Address label cell   x  12.60 .. 145.32   (tall: spans both lower rows)

The engine draws text from the *top* of the box: for a `text` field the
baseline is `y + height - font_size` (pdf_engine._render_field). With
height=18 and font_size=11 that is y + 7, so y=485.6 puts the baseline at
492.6 -- 3.36pt above the row's bottom rule, between the two sibling rows
(Name 3.22, O.E.P. License 3.48). Left inset x=146.1 is identical to the Name
field directly above it, so the column reads straight.

    x 146.1   y 485.6   w 460.0   h 18.0   11pt bold, left, #000000

Bold matches both siblings, which is also what revision 5d9f3a1c7e24 had to
correct on the O.E.P. License field.

SAFETY
------
This revision INSERTs rather than UPDATEs, so the guard is an existence check
rather than a geometry fingerprint: if any field on this template is already
bound to `customer_address`, nothing happens. Re-running is a no-op, an
installation where someone already added the field by hand is left alone, and
matching is by template *name* (never a hardcoded id, which differs per
tenant). Tenant databases pick this up through
scripts/apply_alembic_to_tenants.py.

KNOWN LIMIT
-----------
The field is single-line, like every other text field on this form. The
seeded address renders 320.9pt into a 463.6pt cell, so it fits with room to
spare, but an unusually long tenant address would run past the cell's right
rule rather than wrapping. Enabling `multiline` would be worse, not better:
the row is only 14pt tall, so a second line would be drawn straight through
the O.E.P. License row beneath it.

The regression guards live in tests/test_allied_bank_oep_address.py.
"""

import sqlalchemy as sa

from alembic import op

revision = "7f1b5c3e9a48"
down_revision = "6e0a4b2d8f37"
branch_labels = None
depends_on = None

TEMPLATE_NAME = "Allied Bank Deposit Form"
FIELD_KEY = "customer_address"
FIELD_LABEL = "OEP Address"

# Measured on the rendered page; see the module docstring.
NEW_X = 146.1
NEW_Y = 485.6
NEW_WIDTH = 460.0
NEW_HEIGHT = 18.0
FONT_SIZE = 11.0

# Only insert when the template exists AND does not already carry this field.
SELECT_TEMPLATE_SQL = """
    SELECT t.id
      FROM document_templates t
     WHERE t.name = :tpl
       AND NOT EXISTS (
             SELECT 1 FROM document_fields f
              WHERE f.template_id = t.id
                AND f.field_key = :field_key
           )
"""

INSERT_SQL = """
    INSERT INTO document_fields (
        template_id, label, field_key, field_type, static_value,
        x, y, width, height,
        font_size, font_bold, font_italic, color, align, page, meta
    ) VALUES (
        :template_id, :label, :field_key, 'text', '',
        :x, :y, :w, :h,
        :fs, 1, 0, '#000000', 'left', 1, '{}'
    )
"""

DELETE_SQL = """
    DELETE FROM document_fields
     WHERE field_key = :field_key
       AND x = :x
       AND y = :y
       AND template_id IN (
             SELECT id FROM document_templates WHERE name = :tpl
           )
"""


def _tables_present() -> bool:
    inspector = sa.inspect(op.get_bind())
    names = set(inspector.get_table_names())
    return {"document_templates", "document_fields"} <= names


def upgrade() -> None:
    if not _tables_present():
        return

    bind = op.get_bind()
    template_ids = [
        row[0]
        for row in bind.execute(
            sa.text(SELECT_TEMPLATE_SQL),
            {"tpl": TEMPLATE_NAME, "field_key": FIELD_KEY},
        ).fetchall()
    ]

    for template_id in template_ids:
        bind.execute(
            sa.text(INSERT_SQL),
            {
                "template_id": template_id,
                "label": FIELD_LABEL,
                "field_key": FIELD_KEY,
                "x": NEW_X,
                "y": NEW_Y,
                "w": NEW_WIDTH,
                "h": NEW_HEIGHT,
                "fs": FONT_SIZE,
            },
        )


def downgrade() -> None:
    if not _tables_present():
        return

    # Matched on the exact coordinates this revision wrote, so a field that has
    # since been repositioned by hand survives the downgrade.
    op.get_bind().execute(
        sa.text(DELETE_SQL),
        {"field_key": FIELD_KEY, "x": NEW_X, "y": NEW_Y, "tpl": TEMPLATE_NAME},
    )
