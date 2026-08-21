"""Guards for Section C's "Address" row on the Allied Bank form (7f1b5c3e9a48).

WHY THIS FILE EXISTS
--------------------
Section C of the Allied Bank Deposit Form -- "Overseas Employment Promoter
(O.E.P.) Details" -- has three rows: Name, Address, and O.E.P. License Number.
Name and O.E.P. License Number were bound to company settings; **Address was
bound to nothing**, so it printed blank on every form the portal produced. A
blank mandatory field on a bank deposit form is a form the bank can reject.

Nothing caught it because a *missing* field cannot fail a geometry audit:
scripts/audit_template_geometry.py can only inspect fields that exist. That is
the interesting part of this defect class, and the reason this file asserts the
binding resolves to real data before it asserts anything about coordinates.

WHAT IS BEING PINNED
--------------------
  1. **The key resolves to a real value.** ``customer_address`` is checked by
     actually building the overlay engine's company-settings map and looking
     the key up in it -- not by grepping for a string. Renaming or dropping the
     key in the engine breaks this test rather than silently reintroducing a
     blank row.

  2. **It lands in its own cell.** Measured cell bounds from a per-row darkness
     profile of the rendered page, with the baseline derived using the engine's
     real rule (``y + height - font_size``) rather than assumed.

  3. **It lines up with its siblings.** Section C's three rows should read as a
     column, so left inset and baseline offset are compared against the Name
     and O.E.P. License rows.

  4. **The migration inserts it, once, safely.** The real ``upgrade()`` /
     ``downgrade()`` bodies run against a throwaway database.

REVERT CHECK
------------
Each of these independently fails at least one test here: deleting the INSERT,
dropping the existence guard (double-insert), moving NEW_X away from the Name
column, or moving NEW_Y out of the Address row.
"""

import importlib.util
import os

import pytest
import sqlalchemy as sa

from alembic.migration import MigrationContext
from alembic.operations import Operations

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, "alembic", "versions",
    "7f1b5c3e9a48_add_allied_bank_oep_address.py",
)

# ---------------------------------------------------------------------------
# Section C geometry, measured on the RENDERED page (300dpi) with a PER-ROW
# darkness profile. Per-row matters: a whole-band profile lets rules from
# neighbouring rows bleed in, which is how the first attempt at the O.E.P.
# License placement (revision 5d9f3a1c7e24) picked a rule that did not exist.
#
#   Section C rules    : y 547.32 (heading), 517.68, 503.28, 489.24, 474.72
#   Address value cell : x 145.32 .. 609.72, y 489.24 .. 503.28
#   Address label cell : x  12.60 .. 145.32  (tall: spans both lower rows)
# ---------------------------------------------------------------------------
CELL_LEFT = 145.32
CELL_RIGHT = 609.72
ROW_BOTTOM = 489.24
ROW_TOP = 503.28

LABEL_CELL_RIGHT = 145.32   # right rule of the printed "Address" label cell

# The Name field directly above, as authored in the designer. Section C should
# read as a single column, so the new row is compared against it.
NAME_X = 146.1
NAME_Y = 499.5
NAME_HEIGHT = 18.0
NAME_FONT_SIZE = 11.0
NAME_ROW_BOTTOM = 503.28

# The O.E.P. License row below, for the same reason. Baseline as measured in
# the rendered PDF's text layer ('1338/SKT' baseline_y=478.20).
OEP_ROW_BOTTOM = 474.72
OEP_BASELINE = 478.20

PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0

RULE_TOLERANCE_PT = 0.75

# The address seeded for this deployment, used to prove the row actually
# prints something that fits rather than merely being bound.
SEEDED_ADDRESS = "Ghalla Mandi, Circular Road, Daska District Sialkot (Pakistan)"


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
        "_rev_7f1b5c3e9a48", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _baseline(migration):
    """Where the engine will actually put the text.

    ``pdf_engine._render_field`` computes
    ``text_baseline_y = y + max(h - font_size, 0)`` for a text field.
    Reproducing that rule here -- rather than assuming the baseline is ``y`` --
    is what makes the row-containment assertions meaningful: ``y`` is the box
    *bottom*, and text flows from the box *top*.
    """
    return migration.NEW_Y + max(
        migration.NEW_HEIGHT - migration.FONT_SIZE, 0.0
    )


# ===========================================================================
# 1. The field is bound to data that actually resolves
# ===========================================================================

def test_the_bound_key_resolves_to_the_company_address(migration):
    """The whole defect was a row bound to nothing.

    A field bound to a key the engine does not produce would print just as
    blank, so this builds the engine's real company-settings map from a stub
    and looks the key up in it. Grepping the source would pass on a comment.
    """
    from app.services.overlay_engine import resolve_overlay_fields

    class _Company:
        company_name = "Test Co"
        address = SEEDED_ADDRESS
        oep_license_number = "1338/SKT"

    resolved = resolve_overlay_fields(company=_Company())

    assert migration.FIELD_KEY in resolved, (
        f"{migration.FIELD_KEY} is not a key the overlay engine produces, so "
        f"this field would render blank -- which is the exact defect this "
        f"revision fixes. Keys the engine does produce for the company: "
        f"{sorted(k for k in resolved if k.startswith('customer_'))}"
    )
    assert resolved[migration.FIELD_KEY] == SEEDED_ADDRESS, (
        f"{migration.FIELD_KEY} resolved to "
        f"{resolved[migration.FIELD_KEY]!r}, not the company's address -- the "
        f"row would print the wrong value"
    )


def test_the_key_points_at_company_settings_not_the_candidate(migration):
    """Section C describes the Overseas Employment Promoter -- the operating
    company -- so the address must come from company settings. Binding the
    candidate's own ``address`` here would print the wrong party's address,
    which is worse than printing nothing."""
    assert migration.FIELD_KEY == "customer_address"
    assert migration.FIELD_KEY != "address", (
        "'address' is the candidate's address (field id 4, Section B); "
        "Section C needs the promoter's"
    )


# ===========================================================================
# 2. The coordinate is semantically correct
# ===========================================================================

def test_the_field_is_on_the_page(migration):
    box_top = migration.NEW_Y + migration.NEW_HEIGHT
    assert migration.NEW_Y >= 0
    assert box_top <= PAGE_HEIGHT
    assert migration.NEW_X >= 0
    assert migration.NEW_X + migration.NEW_WIDTH <= PAGE_WIDTH, (
        f"box right {migration.NEW_X + migration.NEW_WIDTH} exceeds the "
        f"{PAGE_WIDTH}pt page"
    )


def test_the_text_starts_inside_the_value_cell(migration):
    assert migration.NEW_X >= CELL_LEFT - RULE_TOLERANCE_PT, (
        f"x={migration.NEW_X} starts left of the value cell ({CELL_LEFT})"
    )
    assert migration.NEW_X >= LABEL_CELL_RIGHT - RULE_TOLERANCE_PT, (
        f"x={migration.NEW_X} would overprint the form's own \"Address\" label"
    )
    assert migration.NEW_X < CELL_RIGHT


def test_the_box_fits_within_the_value_cell(migration):
    right = migration.NEW_X + migration.NEW_WIDTH
    assert right <= CELL_RIGHT + RULE_TOLERANCE_PT, (
        f"box right {right} spills past the cell's right rule {CELL_RIGHT}"
    )


def test_the_baseline_sits_within_the_address_row(migration):
    """The engine draws from the top of the box, so it is the *baseline* that
    has to be inside the row -- not ``y``, which is the box bottom and is
    allowed to hang below the row's bottom rule (both siblings do)."""
    baseline = _baseline(migration)
    assert ROW_BOTTOM < baseline < ROW_TOP, (
        f"baseline {baseline} is outside the Address row "
        f"{ROW_BOTTOM}..{ROW_TOP} -- it would print into a neighbouring row"
    )


def test_the_text_clears_the_rule_below_it(migration):
    """A cap-height's worth of clearance, so the glyphs do not sit on the
    line."""
    baseline = _baseline(migration)
    assert baseline - ROW_BOTTOM >= 2.0, (
        f"baseline {baseline} is only {baseline - ROW_BOTTOM:.2f}pt above the "
        f"row's bottom rule"
    )


# ===========================================================================
# 3. Section C reads as a column
# ===========================================================================

def test_the_left_inset_matches_the_name_row(migration):
    assert migration.NEW_X == pytest.approx(NAME_X, abs=0.01), (
        f"x={migration.NEW_X} does not line up with the Name field above it "
        f"({NAME_X}); Section C's values should form a straight column"
    )


def test_the_baseline_offset_matches_its_siblings(migration):
    """Each Section C row should sit the same distance above its own bottom
    rule, otherwise the three values look ragged even though each is 'in' its
    row."""
    name_offset = (NAME_Y + NAME_HEIGHT - NAME_FONT_SIZE) - NAME_ROW_BOTTOM
    oep_offset = OEP_BASELINE - OEP_ROW_BOTTOM
    offset = _baseline(migration) - ROW_BOTTOM

    low, high = sorted((name_offset, oep_offset))
    assert low - 0.2 <= offset <= high + 0.2, (
        f"Address baseline sits {offset:.2f}pt above its row rule; its "
        f"siblings sit {name_offset:.2f}pt (Name) and {oep_offset:.2f}pt "
        f"(O.E.P. License) above theirs"
    )


def test_the_typography_matches_its_siblings(migration):
    assert migration.NEW_HEIGHT == pytest.approx(NAME_HEIGHT)
    assert migration.FONT_SIZE == pytest.approx(NAME_FONT_SIZE)
    sql = " ".join(migration.INSERT_SQL.split())
    assert ":fs, 1, 0," in sql, (
        "the value must be bold like both siblings -- revision 5d9f3a1c7e24 "
        "already had to correct exactly this on the O.E.P. License field"
    )


def test_the_seeded_address_fits_the_cell(migration):
    """The point of the field is that it prints. A value that overruns the
    cell's right rule is a different defect, not a fix.

    Single-line is deliberate: the row is only 14pt tall, so wrapping would
    draw the second line through the O.E.P. License row beneath it.
    """
    from reportlab.pdfbase import pdfmetrics

    width = pdfmetrics.stringWidth(
        SEEDED_ADDRESS, "Helvetica-Bold", migration.FONT_SIZE
    )
    assert migration.NEW_X + width <= CELL_RIGHT, (
        f"the seeded company address renders {width:.1f}pt wide and would "
        f"overrun the cell's right rule at {CELL_RIGHT}"
    )


def test_the_field_is_not_multiline(migration):
    """Asserted explicitly because 'make it wrap' is the obvious-looking
    improvement and it would print through the row below."""
    sql = " ".join(migration.INSERT_SQL.split())
    assert "multiline" not in sql


# ===========================================================================
# 4. The migration inserts it, once, safely
# ===========================================================================

_SCHEMA = """
CREATE TABLE document_templates (
    id INTEGER PRIMARY KEY,
    name TEXT
);
CREATE TABLE document_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER,
    label TEXT,
    field_key TEXT,
    field_type TEXT,
    static_value TEXT,
    x FLOAT, y FLOAT, width FLOAT, height FLOAT,
    font_size FLOAT, font_bold INTEGER, font_italic INTEGER,
    color TEXT, align TEXT, page INTEGER, meta TEXT
);
"""


def _scratch_engine(tmp_path, *, name="Allied Bank Deposit Form",
                    with_existing=False):
    """A two-table stand-in for a real installation.

    Only the columns the migration touches are created: the point is to
    exercise the shipped statements, not to reproduce the full schema (which
    tests/test_release_acceptance.py already builds with `alembic upgrade
    head`).
    """
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'scratch.db'}")
    with engine.begin() as conn:
        for statement in _SCHEMA.strip().split(";"):
            if statement.strip():
                conn.execute(sa.text(statement))
        conn.execute(
            sa.text("INSERT INTO document_templates (id, name) VALUES (1, :n)"),
            {"n": name},
        )
        # A decoy template, to prove the name filter is doing work.
        conn.execute(
            sa.text(
                "INSERT INTO document_templates (id, name)"
                " VALUES (2, 'Some Other Form')"
            )
        )
        if with_existing:
            conn.execute(
                sa.text(
                    "INSERT INTO document_fields"
                    " (template_id, label, field_key, field_type, x, y,"
                    "  width, height, font_size, font_bold, font_italic,"
                    "  color, align, page, meta)"
                    " VALUES (1, 'Hand placed', 'customer_address', 'text',"
                    " 200.0, 300.0, 100.0, 12.0, 9.0, 0, 0, '#000000',"
                    " 'left', 1, '{}')"
                )
            )
    return engine


def _run(migration, engine, direction="up"):
    """Drive the migration's real upgrade()/downgrade() body.

    Installing the Alembic operations proxy is what makes the ``op.get_bind()``
    inside the migration resolve to this scratch connection, so the code under
    test is the shipped code rather than a paraphrase.
    """
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            if direction == "up":
                migration.upgrade()
            else:
                migration.downgrade()


def _rows(engine, template_id=1):
    with engine.connect() as conn:
        return [
            dict(r._mapping)
            for r in conn.execute(
                sa.text(
                    "SELECT * FROM document_fields"
                    " WHERE template_id = :t"
                    "   AND field_key = 'customer_address'"
                ),
                {"t": template_id},
            )
        ]


def test_upgrade_inserts_the_field(migration, tmp_path):
    engine = _scratch_engine(tmp_path)
    assert _rows(engine) == [], "fixture should start with the row absent"
    _run(migration, engine)

    rows = _rows(engine)
    assert len(rows) == 1
    row = rows[0]
    assert row["x"] == pytest.approx(migration.NEW_X)
    assert row["y"] == pytest.approx(migration.NEW_Y)
    assert row["width"] == pytest.approx(migration.NEW_WIDTH)
    assert row["height"] == pytest.approx(migration.NEW_HEIGHT)
    assert row["field_type"] == "text"
    assert row["font_bold"] == 1
    assert row["font_size"] == pytest.approx(migration.FONT_SIZE)
    assert row["align"] == "left"
    assert row["page"] == 1


def test_upgrade_is_idempotent(migration, tmp_path):
    """The guard is an existence check, not a geometry fingerprint, so a
    second run must not stack a duplicate on top of the first."""
    engine = _scratch_engine(tmp_path)
    _run(migration, engine)
    _run(migration, engine)
    assert len(_rows(engine)) == 1


def test_upgrade_leaves_a_hand_placed_field_alone(migration, tmp_path):
    engine = _scratch_engine(tmp_path, with_existing=True)
    _run(migration, engine)
    rows = _rows(engine)
    assert len(rows) == 1, "an operator's own field was duplicated"
    assert rows[0]["x"] == pytest.approx(200.0)


def test_upgrade_only_touches_the_allied_bank_template(migration, tmp_path):
    engine = _scratch_engine(tmp_path)
    _run(migration, engine)
    assert _rows(engine, template_id=2) == []


def test_upgrade_is_a_no_op_when_the_template_is_absent(migration, tmp_path):
    engine = _scratch_engine(tmp_path, name="Totally Different Form")
    _run(migration, engine)
    assert _rows(engine) == []


def test_upgrade_is_a_no_op_on_a_database_without_the_tables(
    migration, tmp_path
):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    _run(migration, engine)          # must not raise


def test_downgrade_removes_the_inserted_field(migration, tmp_path):
    engine = _scratch_engine(tmp_path)
    _run(migration, engine)
    _run(migration, engine, direction="down")
    assert _rows(engine) == []


def test_downgrade_leaves_a_repositioned_field_alone(migration, tmp_path):
    """Someone who moved the field after upgrading has expressed an intent; a
    downgrade should not silently delete their work."""
    engine = _scratch_engine(tmp_path, with_existing=True)
    _run(migration, engine, direction="down")
    assert len(_rows(engine)) == 1


# ===========================================================================
# 5. The revision is wired into the chain
# ===========================================================================

def test_migration_extends_the_previous_head(migration):
    assert migration.revision == "7f1b5c3e9a48"
    assert migration.down_revision == "6e0a4b2d8f37", (
        "this revision must sit directly on top of the CNIC grid fix; if a "
        "newer revision was added, re-point that one rather than this"
    )


def test_the_guard_is_not_an_id_match(migration):
    """Template ids differ per tenant, so matching on one would insert into an
    arbitrary template in some installations and none in others."""
    sql = " ".join(migration.SELECT_TEMPLATE_SQL.split())
    assert "t.name = :tpl" in sql
    assert "NOT EXISTS" in sql, (
        "without an existence guard, re-running the migration would stack "
        "duplicate fields on top of each other"
    )
