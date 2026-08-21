"""The page box and the field coordinates are ONE unit. Never change one alone.

WHY THIS FILE EXISTS
--------------------
On 2026-08-16 a change was proposed to "fix the aspect ratio" of the Islamabad
visa template (id 8) by giving it a custom page size, on the reasoning that its
background scan is 1624x2339 (aspect 0.694) while the template's page box is
US Letter 612x792 (aspect 0.773) -- an 11.3% vertical stretch that
``scripts/audit_template_geometry.py`` reports as ``aspect_mismatch``.

The stretch is real. The proposed fix would have broken the template.

Every field coordinate on these templates was measured **against the stretched
placement**, i.e. against the scan as ``bg_fit='fill'`` actually draws it into
the 612x792 box. ``tests/test_allied_bank_cnic_grid.py`` says so explicitly and
carries pixel-profile measurements to prove it ("Geometry measured on the
background scan as the engine actually places it: stretched to the full 612x792
page"). ``app/db/init_db.py`` says the same thing in the seeding path: "the
field coordinates were measured against THIS page size, so seeding one without
the other would misplace every field on the template."

So the page box, the scan and the coordinates form a single calibrated system:

    page 612x792  +  scan stretched to fill it  +  coords measured on that view

Changing the page height to "match the scan" while leaving the coordinates
alone would silently move every printed value off its printed box -- on real
embassy paperwork, which is worse than a visibly broken form because it looks
plausible until a visa application is rejected.

Two further facts settled the question:

*   A verified geometry snapshot for Islamabad **does** exist --
    ``app/data/template_geometry.json`` records ``page_width: 612.0``,
    ``page_height: 792.0`` and 14 fields. It was not missing.
*   Measured against **A4** (595x842, aspect 0.70665) the Islamabad scan
    deviates only **-1.75%**, which ``tests/test_background_geometry.py``
    already records as an accepted waiver in ``KNOWN_ASPECT_EXCEPTIONS``
    ("within print tolerance"). The alarming 11.3% figure is an artefact of
    comparing an A4-shaped scan against a Letter-shaped page box, not evidence
    of a newly discovered defect.

WHAT THIS FILE ENFORCES
-----------------------
Not "the aspect must match" -- that ship sailed when the coordinates were
calibrated. What it enforces is that the *pairing* cannot be broken silently:
if someone changes a template's page geometry, the shipped snapshot and the
tests that depend on it must change in the same commit, and this suite fails
until they do.
"""
from __future__ import annotations

import json
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOMETRY_FILE = os.path.join(REPO_ROOT, "app", "data", "template_geometry.json")
BG_DIR = os.path.join(REPO_ROOT, "app", "static", "pdf_backgrounds")

# The calibrated page box of every template whose field coordinates were
# measured against a STRETCHED background. Changing any number here without
# re-measuring every field on that template is a regression, not a fix.
CALIBRATED_PAGE_BOXES = {
    "Allied Bank Deposit Form": (612.0, 792.0),
    "Visa Application Form - Karachi": (612.0, 792.0),
    "Visa Application Form - Islamabad": (612.0, 792.0),
    "Passport Submission Letter - Islamabad": (595.0, 842.0),
}


def _snapshot() -> dict:
    with open(GEOMETRY_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def test_the_verified_geometry_snapshot_exists():
    """It was claimed on 2026-08-16 that no verified snapshot existed for the
    Islamabad template. It does, and it is the authority that seeds a fresh
    install."""
    assert os.path.exists(GEOMETRY_FILE), (
        f"{GEOMETRY_FILE} is missing -- a fresh install would seed templates "
        f"with default geometry and every shipped coordinate would be wrong"
    )


@pytest.mark.parametrize("name", sorted(CALIBRATED_PAGE_BOXES))
def test_the_snapshot_still_carries_its_calibrated_page_box(name):
    """The page box travels WITH the coordinates or the coordinates are junk."""
    snap = _snapshot()
    assert name in snap, f"{name!r} vanished from the verified geometry snapshot"
    entry = snap[name]
    want_w, want_h = CALIBRATED_PAGE_BOXES[name]
    got_w = float(entry.get("page_width") or 0)
    got_h = float(entry.get("page_height") or 0)
    assert (got_w, got_h) == (want_w, want_h), (
        f"{name}: page box changed from {want_w}x{want_h} to {got_w}x{got_h}.\n"
        f"Every field coordinate on this template was measured against the "
        f"OLD box (the background is stretched to fill it by bg_fit='fill'), "
        f"so changing the box alone moves every printed value off its printed "
        f"box. If this change is intentional, re-measure the fields and update "
        f"CALIBRATED_PAGE_BOXES in the same commit."
    )


@pytest.mark.parametrize("name", sorted(CALIBRATED_PAGE_BOXES))
def test_every_calibrated_template_still_has_its_fields(name):
    """A page box with no fields seeds a blank form; fields with no page box
    land nowhere. Both halves must be present."""
    entry = _snapshot()[name]
    fields = entry.get("fields") or []
    assert fields, (
        f"{name}: the snapshot carries a page box but no fields, so a fresh "
        f"install would seed an empty overlay"
    )
    for f in fields:
        for key in ("field_key", "x", "y"):
            assert key in f, f"{name}: a field is missing {key!r}"


@pytest.mark.parametrize("name", sorted(CALIBRATED_PAGE_BOXES))
def test_no_field_origin_sits_outside_its_calibrated_page(name):
    """A coordinate outside the page box never prints at all. This is the check
    that would actually catch a broken re-calibration, as opposed to catching
    the aspect mismatch (which is by design)."""
    entry = _snapshot()[name]
    page_w, page_h = CALIBRATED_PAGE_BOXES[name]
    offenders = [
        f"{f.get('field_key')} at ({f.get('x')}, {f.get('y')})"
        for f in entry.get("fields", [])
        if not (0 <= float(f.get("x", -1)) <= page_w
                and 0 <= float(f.get("y", -1)) <= page_h)
    ]
    assert not offenders, (
        f"{name}: field origins outside the {page_w}x{page_h} page box will "
        f"not print:\n  - " + "\n  - ".join(offenders)
    )


def test_the_aspect_mismatch_is_documented_rather_than_silently_tolerated():
    """The mismatch is a deliberate, recorded trade-off. If the documentation
    of *why* disappears, the next engineer to run the geometry audit will
    "fix" it and break real embassy paperwork -- which is exactly what nearly
    happened on 2026-08-16."""
    doc = os.path.join(REPO_ROOT, "PROJECT_DOCUMENTATION.md")
    with open(doc, encoding="utf-8") as fh:
        text = fh.read()
    assert "aspect" in text.lower(), "the geometry/aspect trade-off is undocumented"
    # The waiver list itself must still explain the Islamabad scan.
    bg_test = os.path.join(REPO_ROOT, "tests", "test_background_geometry.py")
    with open(bg_test, encoding="utf-8") as fh:
        bg = fh.read()
    assert "saudi_visa_application.jpg" in bg and "KNOWN_ASPECT_EXCEPTIONS" in bg, (
        "the Islamabad scan's accepted-deviation waiver has been removed from "
        "KNOWN_ASPECT_EXCEPTIONS without the scan being re-cropped"
    )


def test_the_geometry_audit_script_is_still_available():
    """It is the tool that surfaces this class of problem for a human to judge.
    It must stay shipped, but its `aspect_mismatch` findings on templates 1, 7
    and 8 are EXPECTED and must not be "fixed" by editing page sizes."""
    path = os.path.join(REPO_ROOT, "scripts", "audit_template_geometry.py")
    assert os.path.exists(path), "scripts/audit_template_geometry.py is gone"
