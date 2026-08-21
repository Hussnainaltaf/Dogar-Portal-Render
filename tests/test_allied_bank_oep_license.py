"""Guards for the Allied Bank form's "O.E.P. License Number" field placement.

WHY THIS FILE EXISTS
--------------------
The ``customer_oep_license`` field on the *Allied Bank Deposit Form* template
sat at y=1004.1 on a 792pt page. In the Overlay Engine's coordinate system
(ReportLab: origin bottom-left, a field's ``y`` is the *bottom* of its box) that
puts the whole box 212pt above the top edge of the paper, so the value was never
drawn on a single document. ReportLab clips silently; the engine logs an
off-page warning and deliberately does not clamp, because moving a field to a
coordinate nobody chose is worse than an obvious blank.

Nothing in the suite noticed, because this template's live geometry exists only
in the database -- it was hand-tuned in the React designer, and
``scripts/seed_document_fields.py`` does not even contain this field. Revision
``5d9f3a1c7e24`` therefore repositions it as data.

WHAT IS BEING PINNED
--------------------
Two different things, deliberately:

  1. **The coordinate is semantically right.** Not merely "on the page" -- it
     must land inside the form's own printed "O.E.P. License Number" value
     cell, clear of the printed label, and aligned with the sibling field it was
     authored alongside. The cell bounds below were measured on the *rendered*
     PDF at 300dpi, which is the only measurement that accounts for this
     template's +9.34% background stretch.

     The first attempt at this fix used x=403.93 -- a rule that does not exist,
     picked up from an adjacent row when the raw scan was profiled -- and
     rendered the license number straight through the words "License Number".
     Static analysis said it was fine; rasterising the PDF said otherwise. That
     is why the geometry assertions here are written against measured cell
     bounds rather than against a remembered number.

  2. **The migration actually does it.** The tests execute the migration's own
     ``UPGRADE_SQL`` / ``DOWNGRADE_SQL`` constants against a throwaway database
     rather than a re-typed copy, so editing the migration cannot leave a green
     test behind.

REVERT CHECK
------------
Setting NEW_X back to 403.93, or NEW_Y back to 1004.1, or replacing the
off-page guard with a literal-coordinate match, each fails at least one test
in this module.
"""

import importlib.util
import os

import pytest
import sqlalchemy as sa

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, "alembic", "versions",
    "5d9f3a1c7e24_reposition_allied_bank_oep_license.py",
)

# ---------------------------------------------------------------------------
# Geometry measured on the RENDERED page (300dpi rasterisation of a real
# generated PDF), by locating the table rules from a PER-ROW pixel darkness
# profile. Per-row matters: profiling a whole band lets rules from neighbouring
# rows bleed in, which is exactly how the first attempt went wrong.
#
#   O.E.P. License row verticals : 12.6, 145.44, 300.12, 427.08, 609.72
#   C-Name row verticals         : 12.6, 145.44,                 609.72
#   horizontal rules             : 547.32, 517.56, 503.28, 489.24, 474.72
# ---------------------------------------------------------------------------
CELL_LEFT = 427.08          # left rule of the value cell (= right rule of label)
CELL_RIGHT = 609.72         # right rule of the value cell
ROW_BOTTOM = 474.72         # bottom rule of the O.E.P. License row
ROW_TOP = 489.24            # top rule of the O.E.P. License row
LABEL_CELL_LEFT = 300.12    # left rule of the printed "O.E.P. License Number"

SIBLING_CELL_LEFT = 145.44  # left rule of the sibling's value cell
SIBLING_ROW_TOP = 517.56    # top rule of the sibling's row

# The sibling field, as authored in the designer. The point of the fix is that
# Section C's two filled lines should sit identically within their rows.
SIBLING_X = 146.1
SIBLING_Y = 499.5
SIBLING_HEIGHT = 18.0

PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0
FIELD_WIDTH = 180.0
FIELD_HEIGHT = 18.0

# Rules are 2-3px wide at 300dpi (0.5-0.7pt) and the profile reports their
# centre, so agreement closer than this is not physically meaningful.
RULE_TOLERANCE_PT = 0.75


@pytest.fixture(scope="module")
def migration():
    """Import the revision file directly.

    Alembic version files are not importable as a package -- they are loaded by
    filename and their module names are not valid identifiers -- so the module
    is loaded from its path. Importing the real file, rather than copying its
    constants, is what makes these tests fail when it changes.
    """
    assert os.path.isfile(MIGRATION_PATH), (
        f"migration file is missing: {MIGRATION_PATH}. If the revision was "
        f"renamed, update MIGRATION_PATH here too."
    )
    spec = importlib.util.spec_from_file_location(
        "_rev_5d9f3a1c7e24", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ===========================================================================
# 1. The coordinate is semantically correct
# ===========================================================================

def test_the_field_is_on_the_page_at_all(migration):
    """The original defect, stated directly: the box must be on the paper."""
    box_top = migration.NEW_Y + FIELD_HEIGHT
    assert migration.NEW_Y >= 0, f"y={migration.NEW_Y} is below the page"
    assert box_top <= PAGE_HEIGHT, (
        f"box top {box_top} exceeds the {PAGE_HEIGHT}pt page -- this is the "
        f"original defect (y was 1004.1, box top 1022.1)"
    )
    assert migration.NEW_X >= 0
    assert migration.NEW_X + FIELD_WIDTH <= PAGE_WIDTH, (
        f"box right {migration.NEW_X + FIELD_WIDTH} exceeds the "
        f"{PAGE_WIDTH}pt page width"
    )


def test_the_value_starts_inside_its_own_cell(migration):
    """x must be within the value cell, not in the printed label's cell."""
    assert migration.NEW_X >= CELL_LEFT, (
        f"x={migration.NEW_X} starts left of the cell rule at {CELL_LEFT}, "
        f"i.e. on top of the printed 'O.E.P. License Number' label"
    )
    assert migration.NEW_X < CELL_RIGHT, (
        f"x={migration.NEW_X} is past the cell's right rule at {CELL_RIGHT}"
    )


def test_the_value_does_not_overlap_the_printed_label(migration):
    """Explicit guard for the exact failure the first attempt produced."""
    assert migration.NEW_X > LABEL_CELL_LEFT, (
        f"x={migration.NEW_X} falls inside the printed label cell "
        f"({LABEL_CELL_LEFT}..{CELL_LEFT}); the rendered PDF would read "
        f"'O.E.P. License Numb<value>'"
    )


def test_the_box_fits_within_the_cell_width(migration):
    """A 180pt box has to fit in the 182.6pt cell without crossing the rule."""
    box_right = migration.NEW_X + FIELD_WIDTH
    assert box_right <= CELL_RIGHT, (
        f"box right {box_right} crosses the cell's right rule at {CELL_RIGHT}"
    )


def test_the_box_sits_within_the_rows_horizontal_rules(migration):
    """The text must be in the O.E.P. License row, not the row above/below."""
    box_top = migration.NEW_Y + FIELD_HEIGHT
    assert box_top <= ROW_TOP + RULE_TOLERANCE_PT, (
        f"box top {box_top} is above the row's top rule at {ROW_TOP}"
    )
    # The engine flows text down from the top of the box, so the box bottom may
    # hang below the row's bottom rule; what must not happen is the box
    # starting below the row entirely.
    assert box_top > ROW_BOTTOM, (
        f"box top {box_top} is at or below the row's bottom rule {ROW_BOTTOM}"
    )


def test_placement_mirrors_the_sibling_field(migration):
    """Section C's two filled lines must be inset identically.

    ``customer_emigrant_office`` was authored in the same designer session with
    the same width, height and font size. Matching its offsets is what makes
    the two lines look like one form rather than two separate edits.
    """
    sibling_inset = SIBLING_X - SIBLING_CELL_LEFT
    our_inset = migration.NEW_X - CELL_LEFT
    assert abs(our_inset - sibling_inset) <= RULE_TOLERANCE_PT, (
        f"inset from the cell's left rule is {our_inset:.2f}pt but the sibling "
        f"field uses {sibling_inset:.2f}pt"
    )

    sibling_top_gap = SIBLING_ROW_TOP - (SIBLING_Y + SIBLING_HEIGHT)
    our_top_gap = ROW_TOP - (migration.NEW_Y + FIELD_HEIGHT)
    assert abs(our_top_gap - sibling_top_gap) <= RULE_TOLERANCE_PT, (
        f"gap below the row's top rule is {our_top_gap:.2f}pt but the sibling "
        f"field uses {sibling_top_gap:.2f}pt"
    )


def test_the_downgrade_target_is_the_historic_broken_value(migration):
    """downgrade() must restore exactly what the previous revision described."""
    assert (migration.OLD_X, migration.OLD_Y) == (147.8, 1004.1)


# ===========================================================================
# 2. The migration's own SQL does what the coordinates say
# ===========================================================================

_SCHEMA = """
CREATE TABLE document_templates (
    id INTEGER PRIMARY KEY, name TEXT, page_width FLOAT, page_height FLOAT
);
CREATE TABLE document_fields (
    id INTEGER PRIMARY KEY, template_id INTEGER, field_key TEXT,
    x FLOAT, y FLOAT, width FLOAT, height FLOAT, font_bold BOOLEAN
);
"""


def _scratch_engine(tmp_path, *, x=147.8, y=1004.1, bold=0,
                    name="Allied Bank Deposit Form", page_height=792.0):
    """A two-table stand-in for a real installation.

    Only the columns the migration touches are created: the point is to
    exercise the statement, not to reproduce the full schema (which
    tests/test_release_acceptance.py already builds with `alembic upgrade
    head`). SQLAlchemy is used rather than raw sqlite3 so the named bind
    parameters run through the same layer Alembic uses.
    """
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'scratch.db'}")
    with engine.begin() as conn:
        for statement in _SCHEMA.strip().split(";"):
            if statement.strip():
                conn.execute(sa.text(statement))
        conn.execute(
            sa.text(
                "INSERT INTO document_templates VALUES"
                " (1, :n, 612.0, :ph)"
            ),
            {"n": name, "ph": page_height},
        )
        conn.execute(
            sa.text(
                "INSERT INTO document_fields VALUES"
                " (1, 1, 'customer_oep_license', :x, :y, 180.0, 18.0, :b)"
            ),
            {"x": x, "y": y, "b": bold},
        )
        # A decoy on another template, to prove the name filter does work.
        conn.execute(
            sa.text(
                "INSERT INTO document_templates VALUES"
                " (2, 'Some Other Form', 612.0, 792.0)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO document_fields VALUES"
                " (2, 2, 'customer_oep_license', 50.0, 1004.1,"
                " 180.0, 18.0, 0)"
            )
        )
    return engine


def _apply(engine, sql, params):
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


def _field(engine, field_id=1):
    with engine.connect() as conn:
        return tuple(
            conn.execute(
                sa.text(
                    "SELECT x, y, font_bold FROM document_fields"
                    " WHERE id = :i"
                ),
                {"i": field_id},
            ).one()
        )


def _up_params(migration):
    return {
        "new_x": migration.NEW_X, "new_y": migration.NEW_Y,
        "field_key": migration.FIELD_KEY, "tpl": migration.TEMPLATE_NAME,
    }


def _down_params(migration):
    return {
        "old_x": migration.OLD_X, "old_y": migration.OLD_Y,
        "new_x": migration.NEW_X, "new_y": migration.NEW_Y,
        "field_key": migration.FIELD_KEY, "tpl": migration.TEMPLATE_NAME,
    }


def test_upgrade_sql_moves_the_broken_field(migration, tmp_path):
    engine = _scratch_engine(tmp_path)
    _apply(engine, migration.UPGRADE_SQL, _up_params(migration))
    assert _field(engine) == (migration.NEW_X, migration.NEW_Y, 1)


def test_upgrade_sql_also_normalises_the_weight(migration, tmp_path):
    """Every other field on this template is bold; this one row was not."""
    engine = _scratch_engine(tmp_path, bold=0)
    _apply(engine, migration.UPGRADE_SQL, _up_params(migration))
    assert _field(engine)[2] == 1


def test_upgrade_sql_is_idempotent(migration, tmp_path):
    """Re-running the statement must not move an already-corrected field."""
    engine = _scratch_engine(tmp_path)
    _apply(engine, migration.UPGRADE_SQL, _up_params(migration))
    first = _field(engine)
    _apply(engine, migration.UPGRADE_SQL, _up_params(migration))
    assert _field(engine) == first


def test_upgrade_sql_leaves_an_on_page_field_alone(migration, tmp_path):
    """An operator who has already dragged the field somewhere must win.

    This is the off-page guard. Without it the migration would stomp on a
    hand-placed coordinate every time it ran.
    """
    engine = _scratch_engine(tmp_path, x=300.0, y=200.0, bold=1)
    _apply(engine, migration.UPGRADE_SQL, _up_params(migration))
    assert _field(engine) == (300.0, 200.0, 1)


def test_upgrade_sql_only_touches_the_allied_bank_template(
    migration, tmp_path
):
    """The decoy row on 'Some Other Form' must be untouched."""
    engine = _scratch_engine(tmp_path)
    _apply(engine, migration.UPGRADE_SQL, _up_params(migration))
    assert _field(engine, field_id=2) == (50.0, 1004.1, 0)


def test_upgrade_sql_is_a_no_op_when_the_field_does_not_exist(
    migration, tmp_path
):
    """Fresh installs never create this field; the migration must not error."""
    engine = _scratch_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM document_fields"))
    _apply(engine, migration.UPGRADE_SQL, _up_params(migration))
    with engine.connect() as conn:
        remaining = conn.execute(
            sa.text("SELECT COUNT(*) FROM document_fields")
        ).scalar()
    assert remaining == 0


def test_downgrade_sql_restores_the_previous_state(migration, tmp_path):
    engine = _scratch_engine(tmp_path)
    _apply(engine, migration.UPGRADE_SQL, _up_params(migration))
    _apply(engine, migration.DOWNGRADE_SQL, _down_params(migration))
    assert _field(engine) == (migration.OLD_X, migration.OLD_Y, 0)


def test_downgrade_sql_leaves_a_hand_moved_field_alone(migration, tmp_path):
    engine = _scratch_engine(tmp_path, x=333.0, y=222.0, bold=1)
    _apply(engine, migration.DOWNGRADE_SQL, _down_params(migration))
    assert _field(engine) == (333.0, 222.0, 1)


# ===========================================================================
# 3. The migration is wired into the chain and guards on the right condition
# ===========================================================================

def test_migration_extends_the_previous_head(migration):
    assert migration.revision == "5d9f3a1c7e24"
    assert migration.down_revision == "4c8e1f2a6b93", (
        "this revision must sit directly on top of the schema-drift repair; "
        "if a newer revision was added, re-point that one rather than this"
    )


def test_migration_guards_on_the_page_height(migration):
    """The off-page condition must be present in the statement itself.

    Asserted textually as well as behaviourally: the behavioural tests above
    could all be satisfied by a narrower ``WHERE y = 1004.1``, which would
    silently skip any tenant whose field had drifted to a *different* off-page
    coordinate.
    """
    sql = " ".join(migration.UPGRADE_SQL.split())
    assert "y + COALESCE(height, 0) >" in sql
    assert "page_height" in sql
    assert "1004.1" not in sql, (
        "the guard must be 'is the box off the page', not 'is it at this one "
        "historic coordinate'"
    )
