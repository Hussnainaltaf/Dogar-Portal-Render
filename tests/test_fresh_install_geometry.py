"""Guards: a FRESH INSTALL must receive the verified PDF field geometry.

WHY THIS FILE EXISTS
--------------------
Defect found 2026-08-03 during the deep audit. It is the most severe found in
this whole effort, and every prior test passed straight through it.

The measured, print-verified field coordinates lived in exactly one place that
survived: the operator's own ``data/dogar_trading.db``. That file is
deliberately never shipped (scripts/make_release_zip.sh excludes it because it
holds real candidate PII) and ``data/`` is gitignored. So a fresh deployment
seeded its coordinates from ``scripts/seed_document_fields.py``, whose own
docstring calls the numbers "best-effort approximations".

Measured on a fresh install BEFORE the fix:

  * **13 fields did not exist at all** -- including the three Section C fields
    repaired in revisions 5d9f3a1c7e24 / 7f1b5c3e9a48
    (``customer_address``, ``customer_oep_license``,
    ``customer_emigrant_office``) and 10 fields on the Karachi visa form
    (``visa_number``, ``e_number``, ``bataka_number``,
    ``sponsor_name_arabic``, ...).
  * **32 fields sat at the wrong coordinates**, drifting up to 426 pt --
    most of a page.
  * **Templates 1/7/8 came up A4** instead of the verified Letter, so the
    background scan scaled differently than every measurement assumed.
  * **``cnic`` came up plain ``text``** instead of ``char_cells``, discarding
    the digit-grid geometry fixed in revision 6e0a4b2d8f37.

Why Alembic did not save us: all three geometry revisions are ``UPDATE`` or
guarded ``INSERT`` statements against rows the *seeder* creates. On a fresh DB
they correctly match zero rows. So every geometry fix made to date would have
been invisible to a new deployment -- the customer would print blank and
misaligned mandatory fields, which is precisely the defect class those
revisions existed to eliminate.

WHY THE EXISTING SUITE MISSED IT
--------------------------------
Every geometry test (``test_allied_bank_*``, ``test_background_geometry``) and
``scripts/audit_template_geometry.py`` reads whatever database it is pointed
at. Under pytest that is a freshly-seeded temp DB; run by hand it is the live
DB. The live DB was correct, so manual runs were green. The tests asserted
about migration *constants*, not about what the seeder actually produces --
so nothing compared the two.

WHAT IS PINNED HERE
-------------------
  1. The shipped snapshot exists, parses, and covers every shipped template.
  2. A fresh install reproduces the snapshot EXACTLY -- field for field,
     including field_type, meta, page and page size.
  3. The specific fields the three revisions repaired are present on a fresh
     install with their verified coordinates.
  4. The snapshot is not stale with respect to the DB it was exported from.

These assertions are deliberately about the *seeder's real output*, not about
constants, because "the constants are right but nobody applies them" was the
entire defect.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

SNAPSHOT_PATH = os.path.join(REPO_ROOT, "app", "data", "template_geometry.json")

#: Every template the product ships. Named explicitly so that silently
#: dropping one from TEMPLATE_DEFS is a test failure rather than a quiet
#: reduction in coverage.
SHIPPED_TEMPLATES = (
    "Allied Bank Deposit Form",
    "NBP Form 32",
    "NBP Deposit Slip",
    "NBP Deposit Slip - New",
    "OEP Form",
    "Visa Application Form - Karachi",
    "Visa Application Form - Islamabad",
    "Passport Submission Letter - Islamabad",
)

#: The three Section C repairs, with the geometry verified in a rendered PDF
#: (see tests/test_allied_bank_*.py for how each number was measured).
#: (field_key, x, y, font_size, font_bold)
ALLIED_SECTION_C = (
    ("customer_emigrant_office", 146.1, 499.5, 11.0, True),
    ("customer_address",         146.1, 485.6, 11.0, True),
    ("customer_oep_license",     427.7, 471.2, 11.0, True),
)

#: The CNIC digit grid fixed in revision 6e0a4b2d8f37. A fresh install that
#: loses ``char_cells`` prints 13 digits as one run of text, which overflows
#: the printed boxes and clips the 13th digit off a 612 pt page.
CNIC_EXPECTED = {
    "field_type": "char_cells",
    "x": 389.04,
    "y": 636.9,
    "width": 220.8,
    "height": 14.8,
}
CNIC_EXPECTED_META = {
    "cell_count": 13,
    "cell_width": 14.72,
    "cell_gap": 0,
    "draw_boxes": False,
    "group_sizes": [5, 7, 1],
}

#: OEP Form page-1 rows whose coordinates were re-verified on 2026-08-03.
#:
#: These three were the ONLY fields in the entire shipped set still carrying the
#: legacy "best-effort approximation" numbers from
#: ``scripts/seed_document_fields.py`` (x=150, y in a lazy 30 pt ladder:
#: 735 / 705 / 555). The original snapshot export inherited them verbatim,
#: because the control DB itself had never had them corrected.
#:
#: Measured against the printed rules of ``oep_form_p1.jpg`` (595x842 page,
#: 1653x2339 artwork, text baseline = ``y + max(height - font_size, 0)``):
#:
#:   field         source     baseline   enclosing printed box   verdict
#:   full_name     legacy      743.00     699.81 .. 767.48       43.2pt above the
#:                                                              rule -- lands in
#:                                                              the "Computerized
#:                                                              Registration #"
#:                                                              row, not row 2
#:   full_name     verified    702.00     699.81 .. 767.48       2.2pt clearance,
#:                                                              inside row 2
#:   father_name   legacy      714.00     699.81 .. 767.48       collides with
#:                                                              full_name; row 7
#:                                                              printed blank
#:   father_name   verified    549.20     541.23 .. 550.59       inside the 9.36pt
#:                                                              row 7 box
#:   phone         legacy      564.00     550.59 .. 566.07       one row too high
#:                                                              (row 8 CNIC strip)
#:   phone         verified    474.50     469.78 .. 489.22       inside row 10
#:                                                              "Cell No."
#:
#: Confirmed by rendering both variants and reading the rasterised page: with the
#: legacy numbers the name sits in the office-use header, the father's-name row
#: prints EMPTY and the value collides with the CNIC strip; with these numbers
#: every value sits in its own labelled box.
#:
#: Pinned individually so a future re-export from a mis-edited database is
#: reported as a named regression rather than an anonymous count change.
#: (field_key, x, y)
#:
#: SUPERSEDED 2026-08-03 by a third, stricter pass. The values above only ever
#: proved a baseline fell inside *a* printed box -- never inside the box
#: belonging to its own question. Rendering the live PDF and overlaying every
#: measured rule on the artwork showed 11 of 13 page-1 fields printing on a
#: semantically WRONG row: the emigrant CNIC on the agency-name line, the
#: address on the name line, the e-mail on the father's-name line, and the
#: generation date inside the "Funds and Fee Details" table at the foot of the
#: page. ``scripts/fix_oep_page1_geometry.py`` re-homes all of them and carries
#: the full measurement table plus the two pitfalls that cost two extra passes
#: (a rule detector that missed three lighter rules, and labels that sit level
#: with the TOP of their own value box rather than its centre).
#:
#: The numbers below are the post-fix baselines, each confirmed by reading the
#: rasterised PDF -- not by static analysis.
OEP_VERIFIED_PAGE1 = (
    ("full_name",            155.6, 595.73),   # row 6  Name (14-cell grid)
    ("father_name",          159.0, 553.74),   # row 7  Father's / Husband's Name
    ("cnic",                 155.6, 524.94),   # row 8  Emigrant CNIC (15-cell grid)
    ("phone",                155.6, 473.11),   # row 10 Cell No. (13-cell grid)
    ("email",                159.0, 450.20),   # row 11 E-mail
    ("address",              155.6, 419.96),   # row 12 Address (13-cell grid)
    ("qualification",        159.0, 316.90),   # row 16 Qualification
    ("passport_no",          155.5, 277.79),   # row 17 Passport No. (13-cell grid)
    ("next_of_kin_name",     159.0, 213.00),   # row 20 Name of Nominee
    ("next_of_kin_relation", 159.0, 166.69),   # row 22 Relationship
    ("__today__",            255.0, 719.54),   # row 1  Date
)

#: How far a coordinate may sit from its verified measurement before the test
#: calls it a regression.
#:
#: This was 0.01pt, which was tighter than the system's own storage precision
#: and therefore guaranteed to fail eventually. The designer quantises every
#: coordinate to 0.1pt on save (``round1()`` in designer/engine.ts, matching
#: the persisted precision used across designer/print/PDF), so a single
#: legitimate operator save -- nudging one unrelated field -- rewrote
#: 595.73 -> 595.7 and broke five of these cases. Failing on a 0.03pt
#: (0.01mm) delta is noise, not a defect: it is roughly 1/8 of a 300dpi pixel,
#: far below what any printer can resolve.
#:
#: 0.1pt keeps the guard overwhelmingly strong, because what this test exists
#: to catch is a field jumping to the WRONG LABELLED ROW. The two closest OEP
#: rows are 22.91pt apart, so the tolerance is still ~229x smaller than the
#: smallest error that could actually mis-file a value -- while now tracking
#: the precision the application genuinely stores.
ROW_TOLERANCE_PT = 0.1


def _primary_page1_rows(fields, field_key):
    """Return the page-1 rows for ``field_key``, EXCLUDING continuation rows.

    Added 2026-08-12 with multi-row comb support. OEP form OEPE-RF-02 prints
    field 6 (Name) and field 12 (Address, in Pakistan) as TWO stacked rows of
    pre-printed character cells. The second row is a real, separate
    ``document_fields`` row carrying the SAME ``field_key`` plus
    ``meta.char_offset = N``, which makes it render characters N.. of the one
    value instead of repeating the start (before ``char_offset`` existed a
    second row could only reprint row 1, which is why a 38-character address
    printed its first 13 characters and nothing else).

    Every coordinate in ``OEP_VERIFIED_PAGE1`` was measured against the FIRST
    row, so the assertions below must select it. ``char_offset`` is the
    discriminator: absent or 0 means "starts at the beginning" -- the primary
    row. Selecting on it rather than on "the lowest y" or "the first match"
    keeps the guard honest: an accidentally CLONED field has no offset, so it
    still shows up here as an extra row and still fails.
    """
    return [
        f for f in fields
        if f["field_key"] == field_key
        and int(f.get("page", 1)) == 1
        and not int((f.get("meta") or {}).get("char_offset") or 0)
    ]


#: Page-1 fields the form has NO question for. They used to print candidate
#: data onto OEP *agency* office-use rows, so they were deleted from page 1;
#: page 2 already carries the ones the form asks for there. Guarding their
#: ABSENCE matters: re-seeding from the legacy ladder would silently bring them
#: back and re-corrupt the office-use header.
OEP_PAGE1_ORPHANS = (
    "date_of_birth", "passport_expiry_date", "nationality", "profession",
)

#: Templates whose verified page size is Letter, not the A4 default. Owner
#: instruction (2026-08-02): "Keep templates 1, 7, and 8 on Letter size unless
#: a confirmed business requirement requires A4."
LETTER_TEMPLATES = (
    "Allied Bank Deposit Form",
    "Visa Application Form - Karachi",
    "Visa Application Form - Islamabad",
)

_FIELD_IDENTITY = (
    "field_type", "x", "y", "width", "height",
    "font_size", "font_bold", "font_italic", "align", "page",
)


def _signature(field: dict) -> tuple:
    """A comparable identity for one field row.

    NOTE (bug in the first draft of this file): ``field_key`` is NOT unique
    within a template, and legitimately so -- the NBP Deposit Slip prints the
    same 6 values on 2 copies, the OEP form repeats 5 values on its back page,
    and the Karachi visa form repeats 3. Keying a dict by ``field_key`` silently
    dropped one of each pair and then reported the survivor as "misplaced",
    producing false failures (e.g. ``visa_number.x = 32.9 (verified 401.7)``
    was really the page-2 row being compared against the page-1 row).

    Comparing sorted multisets of full signatures is correct because it is
    order-independent AND duplicate-preserving.
    """
    return (
        field["field_key"],
        field.get("field_type") or "text",
        *(round(float(field[a]), 2) for a in ("x", "y", "width", "height", "font_size")),
        bool(field.get("font_bold")),
        bool(field.get("font_italic")),
        field.get("align") or "left",
        int(field.get("page", 1)),
        json.dumps(field.get("meta") or {}, sort_keys=True),
    )


@pytest.fixture(scope="module")
def snapshot() -> dict:
    with open(SNAPSHOT_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def fresh_install(tmp_path_factory) -> dict:
    """Seed a brand-new database in a SUBPROCESS and report what it contains.

    A subprocess is required, not a convenience: ``app.core.config.Settings``
    and ``app.db.session.engine`` both bind ``DATABASE_URL`` at import time,
    and conftest.py has already imported them against the shared test DB. Only
    a fresh interpreter can exercise the real first-boot code path
    (``create_tables()`` on an empty file, then ``seed_document_templates()``)
    against a database of our choosing.

    This is what makes the test honest: it runs the actual production seeder,
    not a reimplementation of it.
    """
    db_path = tmp_path_factory.mktemp("freshinstall") / "fresh.db"
    probe = r'''
import json, os, sys
from app.db.init_db import create_tables, seed_document_templates
from app.db.session import SessionLocal
create_tables()
db = SessionLocal()
seed_document_templates(db)
from app.models.document import DocumentTemplate, DocumentField
out = {}
for t in db.query(DocumentTemplate):
    out[t.name] = {
        "page_width": t.page_width, "page_height": t.page_height,
        "page_size": t.page_size, "orientation": t.orientation,
        "fields": [
            {
                "label": f.label, "field_key": f.field_key,
                "field_type": f.field_type,
                "static_value": f.static_value or "",
                "x": f.x, "y": f.y, "width": f.width, "height": f.height,
                "font_size": f.font_size,
                "font_bold": bool(f.font_bold),
                "font_italic": bool(f.font_italic),
                "color": f.color, "align": f.align, "page": f.page,
                "meta": f.meta or {},
            }
            for f in db.query(DocumentField).filter_by(template_id=t.id).order_by(DocumentField.id)
        ],
    }
db.close()
sys.stdout.write("@@JSON@@" + json.dumps(out))
'''
    env = dict(os.environ)
    env.update({
        "DATABASE_URL": f"sqlite:///{db_path}",
        "SECRET_KEY": "test-secret-key-thats-at-least-thirty-two-chars-long-yes",
        "ENV": "development",
        "TESTING": "true",
        "PYTHONPATH": REPO_ROOT,
    })
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=240,
    )
    if "@@JOSN@@" in proc.stdout or "@@JSON@@" not in proc.stdout:
        pytest.fail(
            "fresh-install probe did not report a result.\n"
            f"exit={proc.returncode}\nstdout tail:\n{proc.stdout[-2000:]}\n"
            f"stderr tail:\n{proc.stderr[-2000:]}"
        )
    return json.loads(proc.stdout.split("@@JSON@@", 1)[1])


# ===========================================================================
# 1. The shipped snapshot is present and complete
# ===========================================================================

def test_the_geometry_snapshot_ships_with_the_code():
    """If this file is missing from a release, every fresh install silently
    falls back to the approximate layout -- the original defect."""
    assert os.path.isfile(SNAPSHOT_PATH), (
        f"{SNAPSHOT_PATH} is missing. It is the ONLY shipped source of "
        f"verified field geometry; data/dogar_trading.db is never packaged."
    )


def test_the_snapshot_is_valid_json_and_non_trivial(snapshot):
    assert isinstance(snapshot, dict) and snapshot
    total = sum(len(t.get("fields", [])) for t in snapshot.values())
    assert total >= 90, (
        f"the snapshot only carries {total} fields; the verified set is ~102, "
        f"so it looks truncated or exported from the wrong database"
    )


@pytest.mark.parametrize("name", SHIPPED_TEMPLATES)
def test_every_shipped_template_is_covered_by_the_snapshot(snapshot, name):
    assert name in snapshot, (
        f"template {name!r} has no verified geometry, so a fresh install would "
        f"fall back to the approximate starter layout for it"
    )
    assert snapshot[name]["fields"], f"{name!r} is covered but has zero fields"


def _snapshot_commit_timestamp() -> int | None:
    """Unix time of the last commit that touched the shipped snapshot.

    git is the only trustworthy clock here: the snapshot's own mtime changes
    whenever anything merely rewrites it byte-identically, so the filesystem
    cannot tell "shipped on the 12th" from "regenerated 10 seconds ago".

    Returns None outside a git checkout (tarball installs, vendored copies),
    where the caller must fall back to running the check unconditionally --
    the strict behaviour.
    """
    proc = subprocess.run(
        ["git", "log", "-1", "--format=%ct", "--", SNAPSHOT_PATH],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip().isdigit():
        return None
    return int(proc.stdout.strip())


def _db_is_a_seeded_control(db_path: str) -> tuple[bool, str]:
    """Is this file actually the measured control DB, or merely a shell?

    Returns ``(True, "")`` when the database carries the geometry tables, and
    ``(False, reason)`` otherwise. Factored out so BOTH answers are directly
    testable -- a guard that can only ever answer one way is not a guard.
    """
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as probe:
            row = probe.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='document_templates'"
            ).fetchone()
    except sqlite3.Error as exc:            # unreadable / not a database at all
        return False, f"control database is not readable SQLite: {exc}"
    if row is None:
        return False, (
            "data/dogar_trading.db exists but has no document_templates table, "
            "so it is an unseeded shell (importing the app creates the file "
            "before migrations run) rather than the measured control database. "
            "Run the migrations and seeder before using it as the geometry "
            "authority."
        )
    return True, ""


def test_an_unseeded_database_shell_is_not_accepted_as_the_geometry_authority(tmp_path):
    """Pins both answers of the seeded-control guard.

    MEASURED 2026-08-18 while verifying the release ZIP. The archive ships no
    ``data/`` (correct -- it holds real candidate PII), so a fresh extract
    skips the staleness check and the module is 55 passed / 4 skipped. But
    merely IMPORTING the app in that extracted tree CREATES
    ``data/dogar_trading.db`` as an empty 4 KB file with zero tables, because
    startup opens the SQLite path before Alembic runs. A later run then saw a
    "present" database, walked past the presence check, and the exporter died
    with ``sqlite3.OperationalError: no such table: document_templates`` --
    which reads as "the shipped geometry snapshot is broken". It was not: the
    repo run was green and the package was byte-identical to the repo.

    A test whose failure message points at the wrong artefact is a trap for
    whoever reads it next, so the shell is now identified as exactly that.
    Three cases are pinned: an empty shell is rejected, a database that DOES
    carry the table is accepted, and a file that is not SQLite at all is
    rejected rather than raising.
    """
    shell = tmp_path / "shell.db"
    sqlite3.connect(str(shell)).close()
    ok, why = _db_is_a_seeded_control(str(shell))
    assert ok is False, (
        "an empty database shell was accepted as the geometry authority; the "
        "exporter will then fail with a message that blames the snapshot"
    )
    assert "document_templates" in why, (
        f"the skip reason must name the missing table, got: {why!r}"
    )

    seeded = tmp_path / "seeded.db"
    conn = sqlite3.connect(str(seeded))
    conn.execute("CREATE TABLE document_templates (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    ok, why = _db_is_a_seeded_control(str(seeded))
    assert ok is True, (
        "a database that DOES carry document_templates was rejected, so the "
        f"guard rejects everything and the staleness check never runs: {why}"
    )

    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is definitely not a sqlite database")
    ok, why = _db_is_a_seeded_control(str(junk))
    assert ok is False, "a non-SQLite file was accepted as the control database"


def _db_drifted_after_the_snapshot_shipped(db_path: str,
                                           snapshot_committed: int | None) -> bool:
    """True when the local DB was written AFTER the snapshot was committed.

    Factored out of the test below so the gate itself is unit-testable in both
    directions. A gate that can only ever return True is a blanket skip
    wearing a disguise, and nothing in a green suite would say so.
    """
    if snapshot_committed is None:
        return False
    return os.path.getmtime(db_path) > snapshot_committed


def test_the_provenance_gate_still_runs_the_check_on_a_pristine_control_db(tmp_path):
    """The gate must be a DISCRIMINATOR, not an unconditional escape hatch.

    The staleness test below skips when the untracked local ``data/`` DB has
    been written since the snapshot was committed. That skip is correct (see
    that test's docstring for the measured 50pt/103pt/28pt drift it prevented
    from being baked into shipped geometry) but it is also exactly the shape a
    disabled test takes. So both branches are pinned here:

      * DB older than the snapshot commit -> gate is False, check RUNS.
      * DB newer                          -> gate is True, check skips.
      * no git clock available            -> gate is False, check RUNS (strict).

    Previously this behaviour was proven only by a one-off manual mutation
    (backdating the real DB and corrupting a Karachi coordinate to 999.9,
    which did fail as required). A one-off proof decays the moment someone
    edits the gate; this one does not.
    """
    db = tmp_path / "control.db"
    db.write_bytes(b"not a real database, only its mtime matters here")
    committed = 1_700_000_000  # fixed point, well in the past

    os.utime(db, (committed - 3600, committed - 3600))
    assert _db_drifted_after_the_snapshot_shipped(str(db), committed) is False, (
        "a control DB older than the shipped snapshot must NOT be treated as "
        "drifted -- otherwise the staleness check never runs anywhere and the "
        "geometry regression it guards is silently unprotected"
    )

    os.utime(db, (committed + 3600, committed + 3600))
    assert _db_drifted_after_the_snapshot_shipped(str(db), committed) is True, (
        "a DB written after the snapshot was committed must be recognised as "
        "locally drifted, so a mismatch is not mistaken for a stale snapshot"
    )

    assert _db_drifted_after_the_snapshot_shipped(str(db), None) is False, (
        "with no git clock the gate must fail CLOSED (run the check), not open"
    )


def test_the_staleness_detector_actually_detects_drifted_geometry(tmp_path):
    """Proves --check is a live detector, not a no-op the gate is hiding.

    Without this, the skip above could be masking a broken comparison: if
    ``export_template_geometry.py --check`` always exited 0, every run would
    look healthy whether the snapshot matched or not.

    So: copy the shipped snapshot's own numbers into a throwaway SQLite DB,
    move ONE coordinate by 40pt, and require --check to reject it. 40pt is
    chosen deliberately -- it is real page movement, far above the 2dp
    rounding the exporter applies, and comparable to the drift measured on
    OEP Form.
    """
    with open(SNAPSHOT_PATH, encoding="utf-8") as fh:
        snap = json.load(fh)

    db = tmp_path / "drifted.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE document_templates (
            id INTEGER PRIMARY KEY, name TEXT, page_width REAL,
            page_height REAL, page_size TEXT, orientation TEXT
        );
        CREATE TABLE document_fields (
            id INTEGER PRIMARY KEY, template_id INTEGER, label TEXT,
            field_key TEXT, field_type TEXT, static_value TEXT,
            x REAL, y REAL, width REAL, height REAL, font_size REAL,
            font_bold INTEGER, font_italic INTEGER, color TEXT,
            align TEXT, page INTEGER, meta TEXT
        );
        """
    )
    moved = False
    for tid, (name, tpl) in enumerate(snap.items(), start=1):
        conn.execute(
            "INSERT INTO document_templates VALUES (?,?,?,?,?,?)",
            (tid, name, tpl["page_width"], tpl["page_height"],
             tpl["page_size"], tpl["orientation"]),
        )
        for f in tpl["fields"]:
            y = f["y"]
            if not moved:
                y = round(y + 40.0, 2)   # the single deliberate drift
                moved = True
            conn.execute(
                "INSERT INTO document_fields (template_id, label, field_key,"
                " field_type, static_value, x, y, width, height, font_size,"
                " font_bold, font_italic, color, align, page, meta)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, f["label"], f["field_key"], f["field_type"],
                 f["static_value"], f["x"], y, f["width"], f["height"],
                 f["font_size"], int(f["font_bold"]), int(f["font_italic"]),
                 f["color"], f["align"], f["page"],
                 json.dumps(f["meta"]) if f.get("meta") else None),
            )
    conn.commit()
    conn.close()
    assert moved, "the shipped snapshot has no fields at all"

    proc = subprocess.run(
        [sys.executable, os.path.join("scripts", "export_template_geometry.py"),
         "--check", "--db", str(db)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode != 0, (
        "export_template_geometry.py --check accepted a database whose "
        "geometry was deliberately moved 40pt -- the staleness detector is "
        "not detecting anything, so the provenance skip would be hiding a "
        "dead check.\n" + combined
    )
    assert "STALE" in combined, (
        "the detector rejected the drifted DB but said nothing about why; a "
        "silent non-zero exit is indistinguishable from a crash.\n" + combined
    )


def test_the_staleness_detector_accepts_the_database_it_was_measured_from(tmp_path):
    """Control for the test above: --check must not reject EVERYTHING.

    A detector that fails on all inputs would satisfy the drift test while
    being just as useless as one that passes everything. Same construction,
    no deliberate drift, must exit 0.
    """
    with open(SNAPSHOT_PATH, encoding="utf-8") as fh:
        snap = json.load(fh)

    db = tmp_path / "faithful.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE document_templates (
            id INTEGER PRIMARY KEY, name TEXT, page_width REAL,
            page_height REAL, page_size TEXT, orientation TEXT
        );
        CREATE TABLE document_fields (
            id INTEGER PRIMARY KEY, template_id INTEGER, label TEXT,
            field_key TEXT, field_type TEXT, static_value TEXT,
            x REAL, y REAL, width REAL, height REAL, font_size REAL,
            font_bold INTEGER, font_italic INTEGER, color TEXT,
            align TEXT, page INTEGER, meta TEXT
        );
        """
    )
    for tid, (name, tpl) in enumerate(snap.items(), start=1):
        conn.execute(
            "INSERT INTO document_templates VALUES (?,?,?,?,?,?)",
            (tid, name, tpl["page_width"], tpl["page_height"],
             tpl["page_size"], tpl["orientation"]),
        )
        for f in tpl["fields"]:
            conn.execute(
                "INSERT INTO document_fields (template_id, label, field_key,"
                " field_type, static_value, x, y, width, height, font_size,"
                " font_bold, font_italic, color, align, page, meta)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, f["label"], f["field_key"], f["field_type"],
                 f["static_value"], f["x"], f["y"], f["width"], f["height"],
                 f["font_size"], int(f["font_bold"]), int(f["font_italic"]),
                 f["color"], f["align"], f["page"],
                 json.dumps(f["meta"]) if f.get("meta") else None),
            )
    conn.commit()
    conn.close()

    proc = subprocess.run(
        [sys.executable, os.path.join("scripts", "export_template_geometry.py"),
         "--check", "--db", str(db)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        "--check rejected a database rebuilt FROM the shipped snapshot, so it "
        "rejects everything and the drift test above proves nothing.\n"
        f"{proc.stdout}\n{proc.stderr}"
    )


def test_the_snapshot_is_not_stale_relative_to_its_source_database():
    """Runs the real export in --check mode.

    Skipped rather than failed when the control DB is absent: a CI checkout
    legitimately has no ``data/`` directory, and failing there would train
    people to ignore this test.

    ALSO skipped when the local DB has been WRITTEN SINCE the snapshot was
    committed, because then the two disagree for a reason this test cannot
    speak to. Measured case (2026-08-17): this test failed reporting the
    snapshot "STALE", but the drift was confined to a single template --
    `OEP Form`, whose `document_templates.updated_at` was 2026-08-17 16:56,
    i.e. minutes earlier, during this very test session. The other seven
    templates were byte-identical. `data/` is untracked local state, and the
    shipped snapshot was committed 2026-08-12.

    So the DB had drifted from the snapshot, not the snapshot from the DB.
    Regenerating to make the test green would have written the mutated
    coordinates into the shipped file -- and the diff was not rounding noise:
    field 19 y 735.11 -> 684.8 (50pt), field 20 y 719.28 -> 822.6 (103pt),
    field 20 x 156.58 -> 128.9 (28pt), plus three font_bold flips. On real
    embassy and bank paperwork that silently moves printed values off their
    printed boxes.

    The staleness check is still enforced whenever the DB is genuinely the
    pristine control (the case the test was written for); it simply refuses to
    treat a locally mutated DB as the authority.
    """
    db = os.path.join(REPO_ROOT, "data", "dogar_trading.db")
    if not os.path.isfile(db):
        pytest.skip("control database not present in this checkout")

    # A file at that path is not the same thing as a usable control database.
    #
    # MEASURED 2026-08-18, while verifying the release ZIP: the archive ships
    # no data/ at all (correct -- it holds real candidate PII), so a fresh
    # extract skips on the branch above and the module is 55 passed /
    # 4 skipped. But merely IMPORTING the app in that extracted tree creates
    # data/dogar_trading.db as an empty 4 KB file with zero tables, because
    # startup opens the SQLite path before Alembic has run. The next test run
    # then found a "present" DB, sailed past the presence check, and the
    # exporter died with `sqlite3.OperationalError: no such table:
    # document_templates` -- surfacing as a geometry FAILURE that looked, for
    # a moment, like the shipped snapshot was broken.
    #
    # It was not: the repo run was green and the package was intact. But a
    # test whose failure message points at the wrong artefact is a trap for
    # whoever reads it next, so an unseeded DB is now identified as exactly
    # that.
    ok, why = _db_is_a_seeded_control(db)
    if not ok:
        pytest.skip(why)

    # Provenance gate. Both of its branches, and the detector it gates, are
    # pinned by the three tests directly above -- so this skip can never
    # quietly widen into "never check anything".
    if _db_drifted_after_the_snapshot_shipped(db, _snapshot_commit_timestamp()):
        pytest.skip(
            "data/dogar_trading.db was written after the snapshot was "
            "committed, so a mismatch cannot distinguish a stale snapshot "
            "from locally edited template geometry. Verify deliberate "
            "geometry changes by re-running "
            "scripts/export_template_geometry.py and reviewing the diff."
        )

    proc = subprocess.run(
        [sys.executable, os.path.join("scripts", "export_template_geometry.py"), "--check"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        "the shipped geometry snapshot has drifted from the database it was "
        "measured against -- re-run scripts/export_template_geometry.py.\n"
        f"{proc.stdout}\n{proc.stderr}"
    )


# ===========================================================================
# 2. A fresh install actually reproduces the snapshot
# ===========================================================================

def test_a_fresh_install_creates_every_verified_field(fresh_install, snapshot):
    """The headline regression test.

    Before the fix this reported 13 missing fields across 2 templates. Counts
    are compared per (field_key, page) so a form that repeats a value on its
    back page must still receive BOTH rows.
    """
    from collections import Counter

    missing = []
    for name, tpl in snapshot.items():
        want = Counter((f["field_key"], int(f.get("page", 1))) for f in tpl["fields"])
        got = Counter(
            (f["field_key"], int(f.get("page", 1)))
            for f in fresh_install.get(name, {}).get("fields", [])
        )
        for key, n in want.items():
            if got.get(key, 0) < n:
                missing.append(
                    f"{name}:{key[0]} (page {key[1]}) -- "
                    f"expected {n}, got {got.get(key, 0)}"
                )
    assert not missing, (
        f"{len(missing)} verified field(s) are absent from a fresh install, so "
        f"the form would print an empty box where the receiving authority "
        f"expects a value:\n  " + "\n  ".join(sorted(missing))
    )


def test_a_fresh_install_places_every_field_at_its_verified_position(
    fresh_install, snapshot,
):
    """Before the fix this reported 32 fields adrift by up to 426 pt.

    Compares sorted multisets of full field signatures, so it is immune to row
    ordering and correct in the presence of repeated field_keys (see
    ``_signature``). Covers position, size, font, alignment, page and meta in
    one shot -- every attribute that changes what lands on the paper.
    """
    problems = []
    for name, tpl in snapshot.items():
        want = sorted(_signature(f) for f in tpl["fields"])
        got = sorted(
            _signature(f) for f in fresh_install.get(name, {}).get("fields", [])
        )
        if want == got:
            continue
        want_set, got_set = set(want), set(got)
        for sig in sorted(want_set - got_set):
            problems.append(f"{name}: verified but NOT seeded -> {sig}")
        for sig in sorted(got_set - want_set):
            problems.append(f"{name}: seeded but NOT verified -> {sig}")
        if want_set == got_set:
            problems.append(
                f"{name}: same field set but different multiplicities "
                f"({len(want)} verified vs {len(got)} seeded) -- a repeated "
                f"field (e.g. a back page or second slip copy) was dropped"
            )
    assert not problems, (
        "a fresh install does not reproduce the verified geometry:\n  "
        + "\n  ".join(problems[:40])
    )


def test_a_fresh_install_preserves_field_meta(fresh_install, snapshot):
    """``meta`` carries the char_cells digit grid, the barcode symbology, the
    designer font override... Dropping it silently degrades the field to a
    plain text render, which is how the CNIC grid was lost.

    Compared as whole multisets of (field_key, page, meta) so repeated keys on
    different pages are not conflated.
    """
    from collections import Counter

    problems = []
    for name, tpl in snapshot.items():
        def _bag(rows):
            return Counter(
                (
                    r["field_key"],
                    int(r.get("page", 1)),
                    json.dumps(r.get("meta") or {}, sort_keys=True),
                )
                for r in rows
                if (r.get("meta") or {})
            )

        want = _bag(tpl["fields"])
        got = _bag(fresh_install.get(name, {}).get("fields", []))
        for key, n in want.items():
            if got.get(key, 0) < n:
                problems.append(
                    f"{name}:{key[0]} (page {key[1]}) meta not seeded -- "
                    f"verified {key[2]}"
                )
    assert not problems, (
        "field meta lost on a fresh install:\n  " + "\n  ".join(problems)
    )


def test_no_template_has_unexpected_duplicate_rows(snapshot):
    """Pin the templates that legitimately repeat a field_key.

    Discovered while debugging a false failure in this very file: repeated keys
    are CORRECT here -- the NBP Deposit Slip prints the same 6 values on 2 slip
    copies, the OEP form repeats 5 values on its back page, and the two visa
    forms repeat a few. Pinning the set means a NEW duplicate (usually a
    double-seeded or accidentally cloned field, which prints the same value
    twice on top of itself) shows up as a failure instead of passing quietly.
    """
    from collections import Counter

    expected_dupes = {
        "NBP Deposit Slip": 6,                    # 2 slip copies x 6 values
        # Front + back page repeats. Was 5 until 2026-08-03: page 1 also
        # carried a `profession` field, but page 1 has no "profession" question
        # -- the legacy ladder printed it onto an OEP agency office-use row. It
        # was deleted from page 1 (page 2 has the real question), which removed
        # one legitimate cross-page pair. See OEP_PAGE1_ORPHANS.
        #
        # Went 4 -> 5 on 2026-08-12 with multi-row comb support: OEP form
        # OEPE-RF-02 prints field 6 (Name) and field 12 (Address) as TWO
        # stacked rows of pre-printed cells. A continuation row is a SECOND
        # row carrying the SAME field_key with meta.char_offset set, so it
        # renders characters N.. of one value instead of repeating row 1. That
        # legitimately repeats `full_name` and `address` on page 1 (address
        # was already a cross-page repeat, so only one new key is added here).
        # A continuation row is distinguished from an accidental clone by
        # meta.char_offset > 0 -- see the offset-aware row selection in
        # test_oep_page1_rows_carry_the_verified_coordinates.
        "OEP Form": 5,
        "Visa Application Form - Karachi": 3,
        "Visa Application Form - Islamabad": 1,   # barcode reuses passport_no
    }
    actual = {}
    for name, tpl in snapshot.items():
        counts = Counter(f["field_key"] for f in tpl["fields"])
        n = sum(1 for v in counts.values() if v > 1)
        if n:
            actual[name] = n
    assert actual == expected_dupes, (
        f"the set of templates with repeated field_keys changed.\n"
        f"  expected: {expected_dupes}\n  actual:   {actual}\n"
        f"A NEW duplicate usually means a field was cloned by accident and the "
        f"same value now prints twice in the same place. If the change is "
        f"intentional (e.g. a new back page), update this expectation."
    )
    # Every duplicate pair must differ in page OR position -- two identical
    # rows would overprint and are always a bug.
    for name, tpl in snapshot.items():
        seen = set()
        for f in tpl["fields"]:
            ident = (f["field_key"], int(f.get("page", 1)),
                     round(f["x"], 2), round(f["y"], 2))
            assert ident not in seen, (
                f"{name}: {f['field_key']!r} is drawn twice at the identical "
                f"position {ident[2:]} on page {ident[1]} -- the value would "
                f"print on top of itself"
            )
            seen.add(ident)


@pytest.mark.parametrize("name", LETTER_TEMPLATES)
def test_letter_sized_templates_come_up_letter_on_a_fresh_install(
    fresh_install, name,
):
    """Owner instruction: keep templates 1/7/8 on Letter.

    The page size is load-bearing, not cosmetic: every field coordinate was
    measured against a 612x792 page, and the background is drawn with
    bg_fit='fill', so seeding A4 (595x842) rescales the artwork underneath
    text that did not move.
    """
    tpl = fresh_install.get(name)
    assert tpl is not None, f"{name!r} was not created by the seeder at all"
    assert (round(tpl["page_width"]), round(tpl["page_height"])) == (612, 792), (
        f"{name!r} came up {tpl['page_width']}x{tpl['page_height']} on a fresh "
        f"install; verified geometry assumes Letter (612x792)"
    )
    assert tpl["page_size"] == "letter"


# ===========================================================================
# 3. The specific repairs survive a fresh install
# ===========================================================================

@pytest.mark.parametrize("field_key,x,y,font_size,bold", ALLIED_SECTION_C)
def test_allied_bank_section_c_is_complete_on_a_fresh_install(
    fresh_install, field_key, x, y, font_size, bold,
):
    """Section C ("Overseas Employment Promoter Details") is mandatory on the
    Allied Bank Form-7. All three of its value rows were repaired this week;
    none of the three reached a fresh install before this fix.
    """
    rows = [
        f for f in fresh_install.get("Allied Bank Deposit Form", {}).get("fields", [])
        if f["field_key"] == field_key
    ]
    # The Allied Bank form is single-page with no repeated keys (asserted by
    # test_no_template_has_unexpected_duplicate_rows), so exactly one is right.
    assert len(rows) <= 1, f"{field_key!r} unexpectedly appears {len(rows)} times"
    got = rows[0] if rows else None
    assert got is not None, (
        f"{field_key!r} is missing on a fresh install -- Section C would print "
        f"a blank mandatory row (see revisions 5d9f3a1c7e24 / 7f1b5c3e9a48)"
    )
    assert abs(got["x"] - x) < 0.01, f"{field_key} x={got['x']} (verified {x})"
    assert abs(got["y"] - y) < 0.01, f"{field_key} y={got['y']} (verified {y})"
    assert abs(got["font_size"] - font_size) < 0.01
    assert bool(got["font_bold"]) is bold


def test_the_cnic_digit_grid_survives_a_fresh_install(fresh_install):
    """Revision 6e0a4b2d8f37 fixed a 13-digit CNIC whose last digit was drawn
    at x1=616.86 on a 612 pt page -- i.e. clipped off the paper. The fix lives
    entirely in the field's type + meta, both of which the old seeder dropped.
    """
    cnics = [
        f for f in fresh_install.get("Allied Bank Deposit Form", {}).get("fields", [])
        if f["field_key"] == "cnic"
    ]
    assert len(cnics) == 1, f"expected exactly one cnic field, found {len(cnics)}"
    cnic = cnics[0]

    for attr, want in CNIC_EXPECTED.items():
        got = cnic.get(attr)
        if isinstance(want, float):
            assert abs(float(got) - want) < 0.01, (
                f"cnic.{attr} = {got} (verified {want}); a fresh install is not "
                f"receiving the corrected digit grid"
            )
        else:
            assert got == want, f"cnic.{attr} = {got!r} (verified {want!r})"

    meta = cnic.get("meta") or {}
    for key, want in CNIC_EXPECTED_META.items():
        assert meta.get(key) == want, (
            f"cnic.meta[{key!r}] = {meta.get(key)!r} (verified {want!r})"
        )


@pytest.mark.parametrize("field_key,x,y", OEP_VERIFIED_PAGE1)
def test_oep_page1_rows_carry_the_verified_coordinates(snapshot, field_key, x, y):
    """Every page-1 field must sit on the row its own question labels.

    See ``OEP_VERIFIED_PAGE1`` for the measurement table. Before this was
    pinned, the OEP form printed the emigrant's CNIC on the OEP *agency* name
    line, the address on the name line, the e-mail on the father's-name line
    and the generation date inside the "Funds and Fee Details" table -- every
    one of them a mandatory field on a government form.

    Pinned against the SNAPSHOT (not a live DB) because the snapshot is what a
    fresh install actually receives.
    """
    rows = _primary_page1_rows(snapshot["OEP Form"]["fields"], field_key)
    assert len(rows) == 1, (
        f"expected exactly one PRIMARY (char_offset unset) page-1 "
        f"{field_key!r} row on the OEP Form, found {len(rows)}. Continuation "
        f"rows of a two-row comb are excluded by meta.char_offset; more than "
        f"one row without an offset means a field was cloned by accident."
    )
    field = rows[0]
    assert abs(float(field["x"]) - x) < ROW_TOLERANCE_PT, (
        f"OEP {field_key}.x = {field['x']} (verified {x})"
    )
    assert abs(float(field["y"]) - y) < ROW_TOLERANCE_PT, (
        f"OEP {field_key}.y = {field['y']} (verified {y}); the legacy "
        f"approximate ladder (735/705/555) has come back, which prints the "
        f"value in the wrong labelled box"
    )


@pytest.mark.parametrize("field_key,_x,_y", OEP_VERIFIED_PAGE1)
def test_oep_page1_rows_are_not_the_legacy_approximations(
    snapshot, field_key, _x, _y
):
    """The reproduction half of the guard above.

    Asserting the correct value alone cannot distinguish "repaired" from "never
    broken", so the specific broken coordinates are named and excluded. If
    someone re-exports the snapshot from a database that was seeded rather than
    corrected, this is the test that says so.
    """
    # scripts/seed_document_fields.py placed the whole page on a 30pt ladder
    # descending from y=735 at a constant x=150. Naming the ladder rather than
    # three individual y values keeps this guard meaningful as more rows get
    # pinned: ANY field landing on a multiple-of-30 offset from 735 at x=150 is
    # the approximate seeder's output, not a measured coordinate.
    # Offset-aware: a two-row comb's continuation row shares the field_key, and
    # `next()` would otherwise pick whichever row happens to come first.
    field = _primary_page1_rows(snapshot["OEP Form"]["fields"], field_key)[0]
    y_value = float(field["y"])
    on_legacy_ladder = (
        y_value <= 735.0 + 1.0
        and abs((735.0 - y_value) % 30.0) < 1.0
        and abs(float(field["x"]) - 150.0) < 1.0
    )
    assert not on_legacy_ladder, (
        f"OEP {field_key} is back on the legacy approximate ladder "
        f"(x=150, y=735-30n); it is at x={field['x']} y={y_value}. Those "
        f"coordinates were measured to land on the wrong labelled row."
    )
    assert abs(float(field["x"]) - 150.0) > 1.0, (
        f"OEP {field_key}.x is back at the legacy x=150.0 placeholder"
    )


def test_a_fresh_install_reproduces_the_verified_oep_rows(fresh_install):
    """End-to-end: the real seeder must deliver the corrected OEP geometry.

    The snapshot being right is necessary but not sufficient -- the loader in
    ``app/db/init_db.py`` has to actually apply it. This closes that gap for the
    specific rows that were wrong.
    """
    fields = fresh_install.get("OEP Form", {}).get("fields", [])
    assert fields, "a fresh install produced no OEP Form fields at all"
    for field_key, x, y in OEP_VERIFIED_PAGE1:
        # Offset-aware: a plain {(key, page): field} dict silently kept the LAST
        # row it saw, so once two-row combs landed it compared the CONTINUATION
        # row's coordinates against row 1's verified measurement.
        rows = _primary_page1_rows(fields, field_key)
        assert len(rows) == 1, (
            f"a fresh install has {len(rows)} primary page-1 {field_key!r} "
            f"rows on the OEP Form (expected exactly 1)"
        )
        field = rows[0]
        assert abs(float(field["x"]) - x) < ROW_TOLERANCE_PT, (
            f"fresh install OEP {field_key}.x = {field['x']} (verified {x})"
        )
        assert abs(float(field["y"]) - y) < ROW_TOLERANCE_PT, (
            f"fresh install OEP {field_key}.y = {field['y']} (verified {y})"
        )


@pytest.mark.parametrize("field_key", OEP_PAGE1_ORPHANS)
def test_oep_page1_carries_no_orphan_fields(snapshot, field_key):
    """Page 1 must not carry fields the printed form has no question for.

    ``date_of_birth``, ``passport_expiry_date``, ``nationality`` and
    ``profession`` have no row on page 1. While they existed there the legacy
    ladder dropped them onto the OEP *agency* office-use rows ("Name of
    OEP/Agency", "Licence No.", "Permission No."), i.e. candidate data printed
    into fields an agency official is supposed to fill in. Page 2 already
    carries the ones the form asks for.

    Guarding an ABSENCE is the point: a correct-coordinate assertion cannot
    catch a field that should not be on the page at all.
    """
    strays = [
        f for f in snapshot["OEP Form"]["fields"]
        if f["field_key"] == field_key and int(f.get("page", 1)) == 1
    ]
    assert not strays, (
        f"OEP page 1 has {len(strays)} {field_key!r} row(s); page 1 has no "
        f"question for it, so it prints onto an OEP agency office-use row"
    )


def test_oep_page1_grid_rows_do_not_double_draw_their_printed_cells(snapshot):
    """``draw_boxes`` must stay False on every page-1 character grid.

    Rows 6 (Name), 8 (Emigrant CNIC), 10 (Cell No.), 12 (Address) and 17
    (Passport No.) already have their cells printed on the scanned artwork.
    Stroking our own rectangles on top doubles every rule -- the same defect
    that was found and fixed on the Allied Bank CNIC grid, which is exactly why
    it is worth pinning on this form before it happens here.
    """
    grids = [
        f for f in snapshot["OEP Form"]["fields"]
        if int(f.get("page", 1)) == 1 and f.get("field_type") == "char_cells"
    ]
    assert grids, "expected page 1 to define character-cell grids"
    offenders = [
        f["field_key"] for f in grids
        if (f.get("meta") or {}).get("draw_boxes")
    ]
    assert not offenders, (
        f"page-1 grids {offenders} have draw_boxes=True, which strokes a second "
        f"set of cell rules on top of the ones the form already prints"
    )


def test_cnic_meta_does_not_double_draw_the_printed_boxes(snapshot):
    """``draw_boxes`` must stay False on the Allied Bank CNIC grid.

    The scanned form ALREADY prints the 13 digit cells. Setting
    ``draw_boxes: True`` makes the renderer stroke its own rectangles on top,
    which prints a visibly doubled rule around every digit -- confirmed by
    rendering both variants at 300dpi and comparing the crops.

    This is pinned because the value was found flipped to True (together with
    ``format: "digits"``) in the control database, alongside a ~2x4pt origin
    shift, i.e. someone had nudged the grid in the WYSIWYG designer. The origin
    was restored to the revision-6e0a4b2d8f37 constants and the flag to False.
    """
    cnic = next(
        f for f in snapshot["Allied Bank Deposit Form"]["fields"]
        if f["field_key"] == "cnic"
    )
    meta = cnic.get("meta") or {}
    assert meta.get("draw_boxes") is False, (
        "cnic.meta['draw_boxes'] is not False, so the renderer will stroke a "
        "second set of cell boxes over the ones already printed on the scan"
    )


def test_all_thirteen_cnic_digits_fit_on_the_page_on_a_fresh_install(
    fresh_install,
):
    """Arithmetic guard on the real seeded values -- the defect was that the
    13th digit fell off a 612 pt page, so assert the page actually contains it.
    """
    cnic = next(
        f for f in fresh_install["Allied Bank Deposit Form"]["fields"]
        if f["field_key"] == "cnic"
    )
    meta = cnic.get("meta") or {}
    page_width = fresh_install["Allied Bank Deposit Form"]["page_width"]

    groups = meta.get("group_sizes") or [meta.get("cell_count", 13)]
    cell_w = float(meta["cell_width"])
    gap = float(meta.get("cell_gap", 0) or 0)

    # Reproduce the engine's slot arithmetic: one blank slot per group break.
    slots, s = [], 0
    for gi, g in enumerate(groups):
        for _ in range(int(g)):
            slots.append(s)
            s += 1
        if gi < len(groups) - 1:
            s += 1

    assert len(slots) == 13, f"grid holds {len(slots)} digits, expected 13"
    right_edge = cnic["x"] + slots[-1] * (cell_w + gap) + cell_w
    assert right_edge <= page_width, (
        f"the 13th CNIC digit ends at x={right_edge:.2f} on a {page_width:.0f}pt "
        f"page -- it would be clipped off the paper (the 6e0a4b2d8f37 defect)"
    )


def test_the_old_approximate_layout_really_was_broken():
    """Reproduce the defect from the legacy layout itself.

    Without this, a reader cannot tell whether the tests above are guarding a
    real repair or merely restating the current state.
    """
    import importlib.util

    path = os.path.join(REPO_ROOT, "scripts", "seed_document_fields.py")
    spec = importlib.util.spec_from_file_location("seed_document_fields_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    legacy = mod.SPECIFIC.get("Allied Bank Deposit Form")
    assert legacy, "the legacy layout no longer defines the Allied Bank form"
    legacy_keys = {row[1] for row in legacy}

    for field_key, *_ in ALLIED_SECTION_C:
        assert field_key not in legacy_keys, (
            f"{field_key!r} is now in the legacy approximate layout too; if the "
            f"layout has been repaired, this reproduction test should be "
            f"retired deliberately rather than left asserting the wrong thing"
        )

    # And the coordinates it did define were far from the verified ones.
    with open(SNAPSHOT_PATH, encoding="utf-8") as fh:
        verified = {
            f["field_key"]: f
            for f in json.load(fh)["Allied Bank Deposit Form"]["fields"]
        }
    drifts = [
        abs(row[2] - verified[row[1]]["x"]) + abs(row[3] - verified[row[1]]["y"])
        for row in legacy if row[1] in verified
    ]
    assert max(drifts) > 50, (
        f"the legacy layout is within {max(drifts):.1f}pt of verified, so it "
        f"was not the source of the defect this file guards"
    )


# ---------------------------------------------------------------------------
# The snapshot must actually SHIP.
#
# Everything above verifies that the snapshot is *correct*. None of it verifies
# that the snapshot is *delivered*. That gap was a real defect: .gitignore
# carried an unanchored ``data/`` pattern, and git applies such a pattern to
# every directory named ``data`` at any depth -- so ``app/data/`` was excluded
# along with the repository-root ``data/`` that genuinely holds candidate PII.
#
# The consequence was total: the snapshot was never committed, so it was never
# in a clone, a release tarball, or a deployment. ``_verified_template_geometry()``
# in app/db/init_db.py would find no file, fall back to the legacy approximate
# layout, and every fresh install would reproduce the exact 13-missing /
# 32-misplaced / 3-wrong-page-size defect this module exists to prevent. The
# fix would have been perfectly correct and perfectly undelivered.
#
# The two assertions below are opposites on purpose: the snapshot MUST be
# shippable and the live database MUST NOT be, and a careless re-anchoring of
# the pattern can break either one.
# ---------------------------------------------------------------------------

def _git_is_ignored(relpath: str) -> bool:
    """Ask git itself, rather than reimplementing gitignore matching.

    gitignore precedence (anchoring, negation, directory-only patterns,
    later-pattern-wins) is subtle enough that a hand-rolled matcher would be a
    second thing to get wrong. ``git check-ignore`` exits 0 when the path is
    ignored and 1 when it is not.

    ``--no-index`` is load-bearing. By default git check-ignore reports a
    *tracked* path as "not ignored" no matter what .gitignore says, because
    ignore rules only ever apply to untracked files. Without this flag, the
    moment the snapshot was ``git add``-ed the check below would pass
    unconditionally -- it would answer "is this file in the index?" instead of
    the question actually being asked, which is "would this pattern exclude
    this path?". Verified: with an unanchored ``data/`` pattern restored, the
    default invocation returned rc=1 (not ignored) while ``--no-index``
    correctly returned rc=0 and named .gitignore line 17.
    """
    proc = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "--", relpath],
        cwd=REPO_ROOT, capture_output=True,
    )
    if proc.returncode not in (0, 1):
        pytest.skip(f"git check-ignore unavailable: rc={proc.returncode}")
    return proc.returncode == 0


def test_the_verified_geometry_snapshot_is_not_gitignored():
    """A snapshot that cannot be committed cannot reach any deployment."""
    rel = os.path.relpath(SNAPSHOT_PATH, REPO_ROOT)
    assert not _git_is_ignored(rel), (
        f"{rel} is excluded by .gitignore, so it will never be committed and "
        f"never reach a fresh install. app/db/init_db.py would silently fall "
        f"back to the legacy approximate layout, reintroducing the entire "
        f"defect this module guards. Anchor the database pattern as '/data/' "
        f"so it only matches the repository-root directory."
    )


def test_the_verified_geometry_snapshot_is_tracked_by_git():
    """Not ignored is necessary but not sufficient -- it must also be added."""
    proc = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--",
         os.path.relpath(SNAPSHOT_PATH, REPO_ROOT)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0 and "not a git repository" in (proc.stderr or "").lower():
        pytest.skip("not a git working tree")
    assert proc.returncode == 0, (
        "the verified geometry snapshot exists on disk but is untracked, so a "
        "clone or release archive built from git would not contain it. Run "
        "`git add app/data/template_geometry.json`."
    )


def test_the_live_database_is_still_gitignored():
    """The counterweight: fixing the pattern must not start shipping real PII.

    ``data/dogar_trading.db`` holds live candidate records. Re-anchoring the
    gitignore pattern is exactly the kind of edit that could expose it, so the
    protection is pinned here rather than left to reviewer vigilance.
    """
    for sensitive in ("data/dogar_trading.db", "data/tenants", ".env"):
        target = os.path.join(REPO_ROOT, sensitive)
        if not os.path.exists(target):
            continue
        assert _git_is_ignored(sensitive), (
            f"{sensitive} is NOT gitignored. It contains live candidate or "
            f"tenant data and must never be committed."
        )
