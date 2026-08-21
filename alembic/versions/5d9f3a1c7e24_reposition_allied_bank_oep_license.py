"""reposition the off-page customer_oep_license field on the Allied Bank form

Revision ID: 5d9f3a1c7e24
Revises: 4c8e1f2a6b93
Create Date: 2026-08-02

WHY
---
The "OEP License" field on the *Allied Bank Deposit Form* template was authored
at y=1004.1 on a 792pt-tall page. In the ReportLab coordinate system used by
`app/services/pdf_engine.py` the origin is bottom-left and a field's `y` is the
*bottom* of its box, so 1004.1 puts the whole box 212pt **above the top edge of
the paper**. The value was therefore never drawn on a single generated
document -- silently, because the engine deliberately logs an off-page warning
rather than clamping the coordinate (clamping would move a field to a place the
designer never chose, which is worse than an obvious blank).

It went unnoticed because the sibling field `customer_emigrant_office` -- added
in the same designer session, same width, same height, same font size -- *is*
on the page, so the pair looked half-working rather than broken.

WHERE IT BELONGS
----------------
The background scan (`allied_bank_form7.jpg`, 1653x2339px mapped onto a
612x792pt page) is "REQUEST FOR ISSUANCE OF FORM-7 (Under Emigration
Rules-1979)". Section C, "Overseas Employment Promoter (O.E.P.) Details",
contains a printed label "O.E.P. License Number" with an empty value cell to
its right. That cell is the only semantically correct home for this field: it
is the form's own slot for exactly this datum.

The cell was located by detecting the table rules from a pixel darkness profile
rather than by eye -- and, importantly, measured on the *rendered PDF page* at
300dpi rather than on the raw scan. Measuring the scan and mapping the result
onto the page is a step that can go wrong silently (this template's background
is stretched +9.34% horizontally by `bg_fit='fill'`, and a darkness profile
taken over a whole row band picks up rules bleeding in from adjacent rows).
Measuring the finished page removes both hazards, because it is the artefact
the coordinate actually has to agree with:

    O.E.P. License row   pdf_y 474.72 .. 489.24   (row height 14.52pt)
    printed label cell   pdf_x 300.12 .. 427.08   ("O.E.P. License Number")
    value cell           pdf_x 427.08 .. 609.72   (182.6pt wide)

The first attempt at this fix used 403.93 as the value cell's left edge, taken
from a profile of the raw scan. There is no rule there at all -- it was an
adjacent row bleeding into the band -- and the rendered result printed the
license number straight through the middle of the words "License Number". It
was caught by rasterising the PDF and looking at it, which is the only reason
this file carries the right number now.

The new coordinate mirrors the sibling field's placement *exactly*, so the two
lines of Section C sit identically within their rows. The sibling's own row was
re-measured the same way (its verticals are 12.6 / 145.44 / 609.72):

    sibling inset from its cell's left rule : 146.1 - 145.44 = +0.66pt
    sibling box top vs its row's top rule   : 517.5 vs 517.56 = -0.06pt

    => x = 427.08 + 0.66 = 427.7
       y = (489.24 - 0.06) - 18 (box height) = 471.2

The 180pt box then ends at 607.7, clearing the cell's right rule (609.72) with
2pt to spare, so the width does not need changing either.

Width (180), height (18) and font size (11) are deliberately left untouched --
they already match the sibling and fit inside the 205.5pt cell.

The same UPDATE also sets `font_bold = 1`. Every other field on this template,
including the sibling it was authored alongside, is bold; this one row was not,
which would have made it render in a visibly different weight from the line
directly above it the moment it became visible. That inconsistency was masked
by the field never being drawn at all.

WHY A MIGRATION AND NOT A SEED FIX
----------------------------------
`scripts/seed_document_fields.py` is *not* the source of truth for this
template: its `SPECIFIC["Allied Bank Deposit Form"]` block holds seven rough
approximations that do not match the live geometry and does not contain either
of the two `customer_*` fields. The live layout was hand-tuned in the React
designer and lives only in the database, so the correction has to travel as
data. Tenant databases pick it up through
`scripts/apply_alembic_to_tenants.py` (see `app/db/init_db.py`).

SAFETY
------
The UPDATE is guarded three ways so it cannot damage a database that is already
correct or one that was legitimately customised:

  1. it only touches templates *named* "Allied Bank Deposit Form" (never a
     hardcoded id, which differs per tenant);
  2. it only touches rows whose box is actually off the top of the page
     (`y + height > page_height`), so re-running it, or running it against an
     installation where an operator has already dragged the field somewhere
     sensible, is a no-op;
  3. it is a no-op on a fresh install, where the seed script never creates this
     field in the first place -- and on the empty database that
     `tests/test_release_acceptance.py` builds with `alembic upgrade head`.

The regression guard lives in tests/test_allied_bank_oep_license.py, which
asserts the field is on-page, inside the measured cell, and aligned with its
sibling -- and fails if the coordinate is reverted.
"""

import sqlalchemy as sa

from alembic import op

revision = "5d9f3a1c7e24"
down_revision = "4c8e1f2a6b93"
branch_labels = None
depends_on = None

TEMPLATE_NAME = "Allied Bank Deposit Form"
FIELD_KEY = "customer_oep_license"

# Measured on the rendered page at 300dpi; see the module docstring.
NEW_X = 427.7
NEW_Y = 471.2

# The historic broken values, restored by downgrade().
OLD_X = 147.8
OLD_Y = 1004.1

# The two statements are module-level constants rather than inline strings so
# tests/test_allied_bank_oep_license.py can execute *these exact statements*
# against a throwaway database. A test that re-types the SQL would keep passing
# after someone edits the migration, which would make it worse than no test.
#
# The "is the box off the page?" guard is a correlated subquery against the
# template's own page_height, so it behaves identically on SQLite (control and
# tenant databases) and PostgreSQL (the documented server deployment) with no
# user-defined functions. COALESCE(page_height, 792) stops a template with a
# NULL page height from silently disabling the guard.
UPGRADE_SQL = """
    UPDATE document_fields
       SET x = :new_x,
           y = :new_y,
           font_bold = 1
     WHERE field_key = :field_key
       AND template_id IN (
             SELECT id FROM document_templates WHERE name = :tpl
           )
       AND y + COALESCE(height, 0) > (
             SELECT COALESCE(page_height, 792)
               FROM document_templates
              WHERE document_templates.id = document_fields.template_id
           )
"""

DOWNGRADE_SQL = """
    UPDATE document_fields
       SET x = :old_x,
           y = :old_y,
           font_bold = 0
     WHERE field_key = :field_key
       AND template_id IN (
             SELECT id FROM document_templates WHERE name = :tpl
           )
       AND x = :new_x
       AND y = :new_y
"""


def _tables_present() -> bool:
    """Both tables must exist before we can touch them.

    A data migration must never assume the schema it depends on is there: this
    same revision runs against control databases, tenant databases and the
    throwaway database the release-acceptance test builds from scratch.
    """
    inspector = sa.inspect(op.get_bind())
    names = set(inspector.get_table_names())
    return {"document_templates", "document_fields"} <= names


def upgrade() -> None:
    if not _tables_present():
        return

    op.get_bind().execute(
        sa.text(UPGRADE_SQL).bindparams(
            new_x=NEW_X, new_y=NEW_Y, field_key=FIELD_KEY, tpl=TEMPLATE_NAME
        )
    )


def downgrade() -> None:
    """Restore the historic (broken) coordinate.

    Reversibility matters more than the value being sensible: an operator
    stepping back through revisions must land on the exact state the previous
    revision described. The guard on the *new* coordinate means this will not
    disturb a field that has since been moved somewhere else by hand.
    """
    if not _tables_present():
        return

    op.get_bind().execute(
        sa.text(DOWNGRADE_SQL).bindparams(
            old_x=OLD_X, old_y=OLD_Y, new_x=NEW_X, new_y=NEW_Y,
            field_key=FIELD_KEY, tpl=TEMPLATE_NAME,
        )
    )
