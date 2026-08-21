"""align the Allied Bank CNIC digit grid with the form's printed boxes

Revision ID: 6e0a4b2d8f37
Revises: 5d9f3a1c7e24
Create Date: 2026-08-02

WHY
---
Found while rasterising a generated Allied Bank form to verify the O.E.P.
License fix (revision 5d9f3a1c7e24). The CNIC on that form was wrong in three
separate ways at once, and all three are visible the moment you look at the
printed page:

  1. **The 13th digit was off the page.** The field was configured as 13 cells
     of 18pt starting at x=388.8, i.e. 234pt of grid on a 612pt page ending at
     622.8. ReportLab clipped the last cell, so every Allied Bank form this
     portal has ever produced carried a *twelve digit* national ID number. On a
     bank deposit form that is a data-integrity defect, not a cosmetic one.

  2. **The digits did not line up with the printed boxes.** The form prints the
     standard Pakistani 5-7-1 CNIC grid (34102-3634966-1) with a wider gap
     where each dash goes. An even 18pt run cannot follow that: measured digit
     centres drifted from +1.7pt on the first digit to +16.1pt on the fifth --
     more than a whole cell -- before the printed separator gap accidentally
     pulled them back into rough agreement, then drifting out again.

  3. **The engine drew a second grid on top of the first.** `draw_boxes` was
     true even though the background scan already prints the cells, so the
     output had two overlapping grids: the form's dark boxes and the engine's
     grey ones, offset from each other.

The field also sat ~8pt too low: the printed cell strip occupies y 633.4-648.24
and the baseline was at 629.0, so the digits hung below their boxes.

WHAT THIS CHANGES
-----------------
Geometry measured on the background as the engine actually places it (the scan
stretched to the full 612x792 page, which is what `bg_fit='fill'` does), by
locating the printed rules from a pixel darkness profile:

    printed cell strip   y 633.40 .. 648.24        (14.84pt tall)
    grid verticals       389.04, 403.20, 417.72, 432.00, 446.52, 460.80,
                         477.72, 492.12, 506.40, 520.92, 535.32, 549.72,
                         564.12, 578.40, 595.44, 609.84

The two wider gaps (460.80->477.72 and 578.40->595.44, ~16.9pt against a
~14.35pt pitch) are the dash separators, confirming the 5-7-1 grouping. Treated
as 15 evenly-pitched slots -- 13 digits plus 2 separators -- across
389.04..609.84 the pitch is 220.8/15 = 14.72pt, and every digit lands within
1.7pt of its printed cell centre.

    x           388.8  -> 389.04
    y           629.0  -> 636.9    (baseline centred in the 14.84pt strip)
    width       260.0  -> 220.8    (grid now ends at 609.84, on the page)
    height       20.0  -> 14.8
    cell_width   18.0  -> 14.72
    draw_boxes   true  -> false    (the form prints its own boxes)
    group_sizes    --  -> [5,7,1]  (new; see _cell_slots in pdf_engine)

`group_sizes` is a new optional meta key. `_draw_char_cells` falls back to the
previous even-run behaviour whenever it is absent or malformed, so no other
template changes.

SAFETY
------
Guarded the same way as the previous revision: matched by template *name* (never
a hardcoded id, which differs per tenant) and only applied when the row still
carries the exact broken geometry (x=388.8 and cell_width=18). An installation
where an operator has already adjusted the grid is left alone, re-running is a
no-op, and a fresh install has nothing to match.

Tenant databases pick this up through scripts/apply_alembic_to_tenants.py.
The regression guards live in tests/test_allied_bank_cnic_grid.py and
tests/test_char_cell_groups.py.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "6e0a4b2d8f37"
down_revision = "5d9f3a1c7e24"
branch_labels = None
depends_on = None

TEMPLATE_NAME = "Allied Bank Deposit Form"
FIELD_KEY = "cnic"

# Measured on the background as placed on the page; see the module docstring.
NEW_X = 389.04
NEW_Y = 636.9
NEW_WIDTH = 220.8
NEW_HEIGHT = 14.8
NEW_CELL_WIDTH = 14.72
GROUP_SIZES = [5, 7, 1]

# The broken values this revision replaces. Also the fingerprint the guard
# matches on, so the migration cannot disturb a hand-adjusted grid.
OLD_X = 388.8
OLD_Y = 629.0
OLD_WIDTH = 260.0
OLD_HEIGHT = 20.0
OLD_CELL_WIDTH = 18.0

# Selected by fingerprint rather than by id: `x` and the meta's `cell_width`
# together identify the untouched broken row on any installation.
SELECT_SQL = """
    SELECT f.id, f.x, f.meta
      FROM document_fields f
      JOIN document_templates t ON t.id = f.template_id
     WHERE f.field_key = :field_key
       AND t.name = :tpl
"""

UPDATE_SQL = """
    UPDATE document_fields
       SET x = :x, y = :y, width = :w, height = :h, meta = :meta
     WHERE id = :row_id
"""


def _tables_present() -> bool:
    inspector = sa.inspect(op.get_bind())
    names = set(inspector.get_table_names())
    return {"document_templates", "document_fields"} <= names


def _load_meta(raw) -> dict:
    """Template meta is a JSON column that has been seen holding NULL, '' and
    a dict depending on how the row was created (seeder vs designer vs API).
    Anything unparseable is treated as empty rather than aborting the upgrade.
    """
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _matches_broken_geometry(row_x, meta: dict) -> bool:
    try:
        cell_width = float(meta.get("cell_width"))
    except (TypeError, ValueError):
        return False
    return (
        abs(float(row_x) - OLD_X) < 0.01
        and abs(cell_width - OLD_CELL_WIDTH) < 0.01
    )


def upgrade() -> None:
    if not _tables_present():
        return

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(SELECT_SQL),
        {"field_key": FIELD_KEY, "tpl": TEMPLATE_NAME},
    ).fetchall()

    for row_id, row_x, raw_meta in rows:
        meta = _load_meta(raw_meta)
        if not _matches_broken_geometry(row_x, meta):
            continue

        meta["cell_width"] = NEW_CELL_WIDTH
        meta["draw_boxes"] = False
        meta["group_sizes"] = GROUP_SIZES

        bind.execute(
            sa.text(UPDATE_SQL),
            {
                "row_id": row_id, "x": NEW_X, "y": NEW_Y,
                "w": NEW_WIDTH, "h": NEW_HEIGHT,
                "meta": json.dumps(meta),
            },
        )


def downgrade() -> None:
    if not _tables_present():
        return

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(SELECT_SQL),
        {"field_key": FIELD_KEY, "tpl": TEMPLATE_NAME},
    ).fetchall()

    for row_id, _row_x, raw_meta in rows:
        meta = _load_meta(raw_meta)
        # Only step back rows this revision actually produced.
        if meta.get("group_sizes") != GROUP_SIZES:
            continue

        meta["cell_width"] = OLD_CELL_WIDTH
        meta["draw_boxes"] = True
        meta.pop("group_sizes", None)

        bind.execute(
            sa.text(UPDATE_SQL),
            {
                "row_id": row_id, "x": OLD_X, "y": OLD_Y,
                "w": OLD_WIDTH, "h": OLD_HEIGHT,
                "meta": json.dumps(meta),
            },
        )
