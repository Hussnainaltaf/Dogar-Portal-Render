"""Guards for the Allied Bank form's CNIC digit grid (revision 6e0a4b2d8f37).

WHY THIS FILE EXISTS
--------------------
The ``cnic`` field on the *Allied Bank Deposit Form* template was wrong in
three ways at once, found by rasterising a real generated PDF while verifying
an unrelated fix:

  1. **The 13th digit was off the page.** 13 cells of 18pt starting at x=388.8
     span 234pt on a 612pt page, ending at 622.8. ReportLab clipped the last
     cell in silence, so every Allied Bank form this portal has ever produced
     carried a **twelve digit** national ID number. On a bank deposit form
     that is a data-integrity defect, not a cosmetic one.

  2. **The digits missed their printed boxes.** The form prints the standard
     Pakistani 5-7-1 grid (34102-3634966-1) with a wider gap where each dash
     goes. An even run cannot follow that; drift reached 16.1pt, more than a
     whole cell.

  3. **The engine drew a second grid over the first.** ``draw_boxes`` was true
     even though the background scan already prints the cells.

WHAT IS BEING PINNED
--------------------
Three separate things, deliberately:

  1. **Where the digits actually land.** Not "the numbers in the migration are
     the numbers I typed" -- the tests re-derive each digit's drawn centre by
     running the *engine's own* ``_cell_slots`` over the migration's own
     constants, then compare against cell bounds measured off the background.
     A change to either the engine or the migration moves these assertions.

  2. **That the old geometry really was broken.** ``test_the_old_geometry_ran
     _off_the_page`` computes the historic layout and asserts it overflows.
     A guard that cannot fail is worthless, so the defect is reproduced here
     rather than merely described.

  3. **That the migration does it, safely.** The real ``upgrade()`` and
     ``downgrade()`` bodies are executed against a throwaway database through
     an Alembic ``MigrationContext``, so ``op.get_bind()`` resolves and the
     statements under test are the shipped ones, not copies.

REVERT CHECK
------------
Each of these independently fails at least one test in this module:
reverting NEW_X to 388.8, reverting NEW_CELL_WIDTH to 18.0, deleting
GROUP_SIZES, or setting ``draw_boxes`` back to True.
"""

import importlib.util
import json
import os

import pytest
import sqlalchemy as sa

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.services.pdf_engine import _cell_slots

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, "alembic", "versions",
    "6e0a4b2d8f37_align_allied_bank_cnic_grid.py",
)

# ---------------------------------------------------------------------------
# Geometry measured on the background scan as the engine actually places it:
# stretched to the full 612x792 page, which is what bg_fit='fill' does for this
# template. The printed rules were located from a pixel darkness profile.
#
# The two wide gaps (460.80->477.72 and 578.40->595.44, ~16.9pt against a
# ~14.35pt pitch) are the dash separators. That is the measurement that proves
# the 5-7-1 grouping, rather than it being an assumption about CNIC formatting.
# ---------------------------------------------------------------------------
GRID_VERTICALS = [
    389.04, 403.20, 417.72, 432.00, 446.52, 460.80,
    477.72, 492.12, 506.40, 520.92, 535.32, 549.72,
    564.12, 578.40, 595.44, 609.84,
]

# The 13 printed cells, as (left, right) pairs. The two separator gaps are
# skipped: no digit belongs in them.
PRINTED_CELLS = [
    (389.04, 403.20), (403.20, 417.72), (417.72, 432.00),
    (432.00, 446.52), (446.52, 460.80),
    (477.72, 492.12), (492.12, 506.40), (506.40, 520.92),
    (520.92, 535.32), (535.32, 549.72), (549.72, 564.12),
    (564.12, 578.40),
    (595.44, 609.84),
]
PRINTED_CENTRES = [(left + right) / 2 for left, right in PRINTED_CELLS]

STRIP_BOTTOM = 633.40       # bottom of the printed cell strip
STRIP_TOP = 648.24          # top of the printed cell strip

PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0

CNIC_DIGITS = 13

# Half the advance width of a digit at the template's font size. Digits are
# tabular in this face, so one number covers all ten.
DIGIT_HALF_WIDTH = 3.1

# Rules are 2-3px wide at 300dpi (0.5-0.7pt) and the profile reports their
# centre; the background itself is a scan of a printed form, so sub-point
# agreement is not physically meaningful. 2.5pt is still a third of a cell.
CENTRE_TOLERANCE_PT = 2.5


@pytest.fixture(scope="module")
def migration():
    """Import the revision file directly.

    Alembic version files are not importable as a package -- they are loaded
    by filename and their module names are not valid identifiers -- so the
    module is loaded from its path. Importing the real file, rather than
    copying its constants, is what makes these tests fail when it changes.
    """
    assert os.path.isfile(MIGRATION_PATH), (
        f"migration file is missing: {MIGRATION_PATH}. If the revision was "
        f"renamed, update MIGRATION_PATH here too."
    )
    spec = importlib.util.spec_from_file_location(
        "_rev_6e0a4b2d8f37", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _drawn_centres(migration, *, cell_gap=0.0):
    """Where the engine will actually put each digit.

    Deliberately routed through the engine's own ``_cell_slots`` rather than
    re-implementing the slot maths: if the engine's grouping behaviour
    regresses, these coordinates move and the geometry tests below fail.
    """
    pitch = migration.NEW_CELL_WIDTH + cell_gap
    slots = _cell_slots(CNIC_DIGITS, migration.GROUP_SIZES)
    return [
        migration.NEW_X + slot * pitch + migration.NEW_CELL_WIDTH / 2
        for slot in slots
    ]


# ===========================================================================
# 1. The digits land where the form prints its boxes
# ===========================================================================

def test_every_digit_lands_in_its_printed_cell(migration):
    """The headline assertion: digit N is inside printed box N.

    Checked as full containment of the glyph, not just its centre, so a digit
    that merely touches the right cell does not pass.
    """
    centres = _drawn_centres(migration)
    assert len(centres) == len(PRINTED_CELLS)

    problems = []
    for i, (centre, (left, right)) in enumerate(
        zip(centres, PRINTED_CELLS, strict=True)
    ):
        if not (left <= centre - DIGIT_HALF_WIDTH
                and centre + DIGIT_HALF_WIDTH <= right):
            problems.append(
                f"  digit {i}: drawn at {centre:.2f} "
                f"(glyph {centre - DIGIT_HALF_WIDTH:.2f}.."
                f"{centre + DIGIT_HALF_WIDTH:.2f}) "
                f"escapes printed cell {left}..{right}"
            )
    assert not problems, (
        "digits do not sit inside the form's printed boxes:\n"
        + "\n".join(problems)
    )


def test_digit_centres_match_the_printed_cell_centres(migration):
    """Tighter than containment: the digits should look centred, not merely
    contained. Worst measured error at the shipped geometry is 1.78pt."""
    centres = _drawn_centres(migration)
    errors = [
        abs(drawn - printed)
        for drawn, printed in zip(centres, PRINTED_CENTRES, strict=True)
    ]
    worst = max(errors)
    assert worst <= CENTRE_TOLERANCE_PT, (
        f"worst digit is {worst:.2f}pt off its printed cell centre "
        f"(limit {CENTRE_TOLERANCE_PT}pt); per-digit errors: "
        + ", ".join(f"{e:.2f}" for e in errors)
    )


def test_the_whole_grid_stays_on_the_page(migration):
    """The original defect, stated directly."""
    centres = _drawn_centres(migration)
    rightmost = centres[-1] + DIGIT_HALF_WIDTH
    assert rightmost <= PAGE_WIDTH, (
        f"last digit ends at {rightmost:.2f} on a {PAGE_WIDTH}pt page -- "
        f"this is the original defect, which silently dropped the 13th digit"
    )
    assert migration.NEW_X >= 0
    assert migration.NEW_X + migration.NEW_WIDTH <= PAGE_WIDTH


def test_all_thirteen_digits_are_rendered(migration):
    """A CNIC is 13 digits. Twelve is a different person."""
    assert len(_drawn_centres(migration)) == CNIC_DIGITS


def test_the_old_geometry_really_was_broken(migration):
    """Reproduce the defect from the migration's own OLD_* constants.

    Without this, every assertion above could be satisfied by geometry that
    was never actually wrong, and the file would be documenting a fix that
    fixed nothing.
    """
    old_right = migration.OLD_X + CNIC_DIGITS * migration.OLD_CELL_WIDTH
    assert old_right > PAGE_WIDTH, (
        f"the historic geometry ({migration.OLD_X} + 13 x "
        f"{migration.OLD_CELL_WIDTH} = {old_right}) is expected to overflow "
        f"the {PAGE_WIDTH}pt page; if it does not, OLD_* no longer describes "
        f"the broken state and the migration's fingerprint guard is suspect"
    )

    # ...and it also missed the printed cells badly, even ignoring the overflow.
    old_centres = [
        migration.OLD_X + i * migration.OLD_CELL_WIDTH
        + migration.OLD_CELL_WIDTH / 2
        for i in range(CNIC_DIGITS)
    ]
    worst = max(
        abs(a - b)
        for a, b in zip(old_centres, PRINTED_CENTRES, strict=True)
    )
    assert worst > CENTRE_TOLERANCE_PT * 2, (
        f"the historic geometry was only {worst:.2f}pt off; the alignment "
        f"half of this fix would then be unjustified"
    )


def test_the_grouping_matches_the_measured_separator_gaps(migration):
    """The 5-7-1 split is a measurement, not an assumption.

    Derived independently here from the printed verticals: a gap materially
    wider than the modal pitch is a dash separator.
    """
    gaps = [
        round(b - a, 2)
        # ragged by construction: pairwise over a single list
        for a, b in zip(GRID_VERTICALS, GRID_VERTICALS[1:], strict=False)
    ]
    typical = min(gaps)
    separator_positions = [
        i for i, gap in enumerate(gaps) if gap > typical + 1.5
    ]
    assert len(separator_positions) == 2, (
        f"expected 2 dash separators in the printed grid, found "
        f"{len(separator_positions)} (gaps: {gaps})"
    )

    derived, previous = [], -1
    for position in separator_positions:
        derived.append(position - previous - 1)
        previous = position
    derived.append(len(gaps) - previous - 1)

    assert derived == migration.GROUP_SIZES, (
        f"the printed grid measures as {derived}, but the migration ships "
        f"{migration.GROUP_SIZES}"
    )


def test_the_grid_spans_the_measured_width(migration):
    """x + width should land on the grid's last printed vertical."""
    assert abs(migration.NEW_X - GRID_VERTICALS[0]) < 0.75
    assert abs(
        (migration.NEW_X + migration.NEW_WIDTH) - GRID_VERTICALS[-1]
    ) < 0.75, (
        f"grid ends at {migration.NEW_X + migration.NEW_WIDTH}, printed grid "
        f"ends at {GRID_VERTICALS[-1]}"
    )


def test_the_baseline_sits_inside_the_printed_strip(migration):
    """For char_cells the stored ``y`` is used directly as the text baseline
    (not the box bottom, as with every other field type). It must therefore
    fall inside the printed cell strip, low enough to leave room for the
    digits' cap height. The field previously sat at 629.0 -- below the boxes,
    so the digits hung out of the bottom of the grid."""
    assert STRIP_BOTTOM < migration.NEW_Y < STRIP_TOP, (
        f"baseline {migration.NEW_Y} is outside the printed cell strip "
        f"{STRIP_BOTTOM}..{STRIP_TOP}"
    )
    assert migration.NEW_Y - STRIP_BOTTOM >= 2.0, (
        "baseline is too close to the bottom rule; descender clearance"
    )
    assert migration.NEW_HEIGHT <= (STRIP_TOP - STRIP_BOTTOM) + 0.5, (
        f"height {migration.NEW_HEIGHT} exceeds the printed strip "
        f"({STRIP_TOP - STRIP_BOTTOM:.2f}pt)"
    )


# ===========================================================================
# 2. The migration performs the change, and only where it should
# ===========================================================================

_SCHEMA = """
CREATE TABLE document_templates (
    id INTEGER PRIMARY KEY,
    name TEXT,
    page_width FLOAT,
    page_height FLOAT
);
CREATE TABLE document_fields (
    id INTEGER PRIMARY KEY,
    template_id INTEGER,
    field_key TEXT,
    x FLOAT,
    y FLOAT,
    width FLOAT,
    height FLOAT,
    meta TEXT
);
"""

# The meta as the designer actually stored it, including the keys this
# migration must not disturb.
BROKEN_META = {
    "font_name": "DejaVu Sans",
    "cell_count": 13,
    "cell_width": 18.0,
    "cell_gap": 0,
    "format": "",
    "draw_boxes": True,
}


# Distinguishes "caller did not specify meta" from "caller wants SQL NULL",
# which the unparseable-meta test genuinely needs to express.
_UNSET = object()


def _scratch_engine(tmp_path, *, x=388.8, y=629.0, width=260.0, height=20.0,
                    meta=_UNSET, name="Allied Bank Deposit Form"):
    """A two-table stand-in for a real installation.

    Only the columns the migration touches are created: the point is to
    exercise the shipped statements, not to reproduce the full schema (which
    tests/test_release_acceptance.py already builds with `alembic upgrade
    head`).
    """
    if meta is _UNSET:
        meta = json.dumps(BROKEN_META)
    elif isinstance(meta, dict):
        meta = json.dumps(meta)

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'scratch.db'}")
    with engine.begin() as conn:
        for statement in _SCHEMA.strip().split(";"):
            if statement.strip():
                conn.execute(sa.text(statement))
        conn.execute(
            sa.text(
                "INSERT INTO document_templates VALUES (1, :n, 612.0, 792.0)"
            ),
            {"n": name},
        )
        conn.execute(
            sa.text(
                "INSERT INTO document_fields VALUES"
                " (1, 1, 'cnic', :x, :y, :w, :h, :m)"
            ),
            {"x": x, "y": y, "w": width, "h": height, "m": meta},
        )
        # A decoy carrying the identical broken geometry on a different
        # template, to prove the name filter is doing work.
        conn.execute(
            sa.text(
                "INSERT INTO document_templates VALUES"
                " (2, 'Some Other Form', 612.0, 792.0)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO document_fields VALUES"
                " (2, 2, 'cnic', 388.8, 629.0, 260.0, 20.0, :m)"
            ),
            {"m": json.dumps(BROKEN_META)},
        )
    return engine


def _run(migration, engine, direction="up"):
    """Drive the migration's real upgrade()/downgrade() body.

    Installing the Alembic operations proxy is what makes the module-level
    ``op.get_bind()`` inside the migration resolve to this scratch connection,
    so the code under test is the shipped code rather than a paraphrase.
    """
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            if direction == "up":
                migration.upgrade()
            else:
                migration.downgrade()


def _row(engine, field_id=1):
    with engine.connect() as conn:
        x, y, w, h, meta = conn.execute(
            sa.text(
                "SELECT x, y, width, height, meta FROM document_fields"
                " WHERE id = :i"
            ),
            {"i": field_id},
        ).one()
    try:
        parsed = json.loads(meta) if meta else {}
    except (TypeError, ValueError):
        # Some cases deliberately store junk; the reader must not be the thing
        # that fails, or it would mask what the migration actually did.
        parsed = {}
    return {
        "x": x, "y": y, "width": w, "height": h,
        "meta": parsed if isinstance(parsed, dict) else {},
    }


def test_upgrade_rewrites_the_geometry(migration, tmp_path):
    engine = _scratch_engine(tmp_path)
    _run(migration, engine)
    row = _row(engine)
    assert row["x"] == pytest.approx(migration.NEW_X)
    assert row["y"] == pytest.approx(migration.NEW_Y)
    assert row["width"] == pytest.approx(migration.NEW_WIDTH)
    assert row["height"] == pytest.approx(migration.NEW_HEIGHT)


def test_upgrade_rewrites_the_meta(migration, tmp_path):
    """Covers defects 2 and 3: the cell pitch and the duplicate grid."""
    engine = _scratch_engine(tmp_path)
    _run(migration, engine)
    meta = _row(engine)["meta"]
    assert meta["cell_width"] == pytest.approx(migration.NEW_CELL_WIDTH)
    assert meta["group_sizes"] == migration.GROUP_SIZES
    assert meta["draw_boxes"] is False, (
        "the background scan already prints the cells; drawing them again "
        "produces two offset grids"
    )


def test_upgrade_preserves_unrelated_meta_keys(migration, tmp_path):
    """The designer stores font and format settings in the same blob; a
    careless rewrite would drop them."""
    engine = _scratch_engine(tmp_path)
    _run(migration, engine)
    meta = _row(engine)["meta"]
    assert meta["font_name"] == "DejaVu Sans"
    assert meta["cell_count"] == 13
    assert meta["format"] == ""


def test_the_cell_gap_stays_zero(migration, tmp_path):
    """The geometry assertions in section 1 assume the pitch is exactly
    ``cell_width``. That is only true while ``cell_gap`` is 0, so it is pinned
    here rather than left as an unstated premise."""
    engine = _scratch_engine(tmp_path)
    _run(migration, engine)
    assert float(_row(engine)["meta"].get("cell_gap", 0)) == 0.0


def test_upgrade_is_idempotent(migration, tmp_path):
    engine = _scratch_engine(tmp_path)
    _run(migration, engine)
    first = _row(engine)
    _run(migration, engine)
    assert _row(engine) == first


def test_upgrade_leaves_a_hand_adjusted_grid_alone(migration, tmp_path):
    """An operator who already nudged this field must not be overruled."""
    engine = _scratch_engine(
        tmp_path, x=400.0, meta={**BROKEN_META, "cell_width": 15.5}
    )
    _run(migration, engine)
    row = _row(engine)
    assert row["x"] == pytest.approx(400.0)
    assert row["meta"]["cell_width"] == pytest.approx(15.5)
    assert "group_sizes" not in row["meta"]


def test_upgrade_only_touches_the_allied_bank_template(migration, tmp_path):
    """The decoy row on 'Some Other Form' carries identical broken geometry."""
    engine = _scratch_engine(tmp_path)
    _run(migration, engine)
    decoy = _row(engine, field_id=2)
    assert decoy["x"] == pytest.approx(migration.OLD_X)
    assert decoy["meta"]["cell_width"] == pytest.approx(
        migration.OLD_CELL_WIDTH
    )


def test_upgrade_survives_unparseable_meta(migration, tmp_path):
    """meta has been seen holding NULL, '' and junk depending on how the row
    was created. None of those should abort a deployment."""
    for index, bad in enumerate((None, "", "not json at all", "[1,2,3]")):
        # A fresh directory per case: _scratch_engine always writes
        # 'scratch.db', so they would otherwise collide.
        case_dir = tmp_path / f"case{index}"
        case_dir.mkdir()
        engine = _scratch_engine(case_dir, meta=bad)
        _run(migration, engine)          # must not raise
        # No cell_width to fingerprint against, so the row is left as found.
        assert _row(engine)["x"] == pytest.approx(migration.OLD_X)


def test_upgrade_is_a_no_op_on_a_database_without_the_tables(
    migration, tmp_path
):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    _run(migration, engine)              # must not raise


def test_downgrade_restores_the_previous_state(migration, tmp_path):
    engine = _scratch_engine(tmp_path)
    _run(migration, engine)
    _run(migration, engine, direction="down")
    row = _row(engine)
    assert row["x"] == pytest.approx(migration.OLD_X)
    assert row["y"] == pytest.approx(migration.OLD_Y)
    assert row["width"] == pytest.approx(migration.OLD_WIDTH)
    assert row["height"] == pytest.approx(migration.OLD_HEIGHT)
    assert row["meta"]["cell_width"] == pytest.approx(migration.OLD_CELL_WIDTH)
    assert row["meta"]["draw_boxes"] is True
    assert "group_sizes" not in row["meta"]
    assert row["meta"]["font_name"] == "DejaVu Sans"


def test_downgrade_leaves_rows_this_revision_did_not_create(
    migration, tmp_path
):
    """Downgrading a database that never ran the upgrade must not invent the
    broken geometry on a field that is currently fine."""
    engine = _scratch_engine(
        tmp_path, x=400.0, meta={**BROKEN_META, "cell_width": 15.5}
    )
    _run(migration, engine, direction="down")
    assert _row(engine)["x"] == pytest.approx(400.0)


# ===========================================================================
# 3. The revision is wired into the chain
# ===========================================================================

def test_migration_extends_the_previous_head(migration):
    assert migration.revision == "6e0a4b2d8f37"
    assert migration.down_revision == "5d9f3a1c7e24", (
        "this revision must sit directly on top of the O.E.P. License fix; "
        "if a newer revision was added, re-point that one rather than this"
    )


def test_the_fingerprint_guard_is_not_an_id_match(migration):
    """Template ids differ per tenant, so matching on one would apply the fix
    to an arbitrary template in some installations and to none in others."""
    sql = " ".join(migration.SELECT_SQL.split())
    assert "t.name = :tpl" in sql
    assert "template_id = 1" not in sql
