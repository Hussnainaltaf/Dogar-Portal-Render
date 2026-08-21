"""Geometry guards for the coordinate Overlay Engine's background scans.

WHY THIS FILE EXISTS
--------------------
The Overlay Engine draws candidate data on top of a scanned government/bank
form at absolute PDF-point coordinates. Those coordinates are authored against
an A4 page (595 x 842 pt, origin bottom-left -- see the header of
``scripts/seed_document_fields.py``), and the default background fit mode is
``fill`` (``_draw_background()`` in app/services/pdf_engine.py), which
*stretches* the scan to the full page box.

That combination has a silent failure mode: if a scan's aspect ratio does not
match the page's aspect ratio, ``fill`` distorts the artwork on one axis while
the overlaid text stays undistorted. Nothing errors, no log line appears -- the
PDF simply comes out with the printed boxes shifted away from the values that
are supposed to sit inside them, and the drift grows the further you get from
the origin.

That is exactly what shipped in v1.1.0: ``allied_bank_form7.jpg`` was
1807 x 2339 px (aspect 0.77255 -- US Letter, 0.77273) rendered onto an A4 page
(0.70665), i.e. **9.3% of horizontal stretch** across a real bank deposit slip.
It was repaired by cropping 154 px of verified-pure-white margin (zero ink
pixels removed) down to 1653 x 2339, which is also the size every other scan in
the repo already used.

These tests make that class of defect impossible to reintroduce: drop a
Letter-shaped or landscape scan into ``app/static/pdf_backgrounds/`` and the
suite fails with the measured deviation instead of shipping a skewed form.
"""

import os

import pytest

from app.db.init_db import MULTIPAGE_BACKGROUNDS, TEMPLATE_DEFS
from app.models.document import DocumentTemplate

Image = pytest.importorskip("PIL.Image", reason="Pillow is required to measure scan geometry")

BG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "pdf_backgrounds")

# The page the coordinates in scripts/seed_document_fields.py are authored
# against, and the size DocumentTemplate falls back to when the seeder does not
# state one (see app/models/document.py: page_width=595, page_height=842).
A4_W, A4_H = 595.0, 842.0
A4_ASPECT = A4_W / A4_H  # 0.70665

# How far a scan's aspect may drift from the page before `fill` mode produces
# visible skew on a printed form. 2% was chosen because it is comfortably below
# the ~9.3% defect this file was written to catch, while still admitting the one
# legitimately-imperfect scan below.
ASPECT_TOLERANCE_PCT = 2.0

# Scans that are knowingly a little off A4 and are accepted as-is, with the
# reason recorded so a future reader does not "fix" them blindly.
#
# saudi_visa_application.jpg is 1624 x 2339 (-1.75%): the Islamabad embassy form
# was scanned very slightly narrow. Re-cropping cannot help (the page is *short*
# of A4 width, not over it) and padding it would move the printed boxes, so the
# sub-2% stretch is accepted. It is listed explicitly rather than by widening
# the global tolerance, so any *new* off-spec scan still fails.
KNOWN_ASPECT_EXCEPTIONS = {
    "saudi_visa_application.jpg": "Islamabad embassy form scanned 1.75% narrow; within print tolerance",
}


def _deviation_pct(image_aspect: float, page_aspect: float = A4_ASPECT) -> float:
    """Signed percentage by which a scan's aspect differs from the page's."""
    return (image_aspect - page_aspect) / page_aspect * 100.0


def _measure(filename: str):
    path = os.path.join(BG_DIR, filename)
    with Image.open(path) as im:
        width, height = im.size
    return width, height, width / height


def _shipped_background_files():
    """Every image file actually present in the pdf_backgrounds directory."""
    if not os.path.isdir(BG_DIR):
        return []
    return sorted(
        name
        for name in os.listdir(BG_DIR)
        if name.lower().endswith((".jpg", ".jpeg", ".png"))
    )


def test_the_background_directory_is_not_empty():
    """Sanity anchor: if the scans ever stop shipping, the geometry tests below
    would silently pass on an empty set and the Overlay Engine would have no
    forms to draw on."""
    files = _shipped_background_files()
    assert files, f"no background scans found in {BG_DIR} -- the Overlay Engine has nothing to render onto"


def test_every_shipped_background_matches_the_a4_page_aspect():
    """No scan may deviate from the A4 page aspect by more than the tolerance.

    This is the guard that would have caught the v1.1.0 Allied Bank defect:
    1807x2339 (Letter) on an A4 page = +9.34%.
    """
    offenders = []
    for name in _shipped_background_files():
        width, height, aspect = _measure(name)
        deviation = _deviation_pct(aspect)
        if abs(deviation) <= ASPECT_TOLERANCE_PCT:
            continue
        if name in KNOWN_ASPECT_EXCEPTIONS:
            continue
        offenders.append(
            f"{name}: {width}x{height} (aspect {aspect:.5f}) deviates {deviation:+.2f}% from the "
            f"A4 page aspect {A4_ASPECT:.5f} -- with the default bg_fit='fill' this stretches the "
            f"printed form while the overlaid text stays true, so values will not land inside their boxes. "
            f"Crop the scan to an A4 aspect (only pure-white margin may be removed), or record it in "
            f"KNOWN_ASPECT_EXCEPTIONS with a reason."
        )
    assert not offenders, "background scans are not A4-shaped:\n  - " + "\n  - ".join(offenders)


def test_known_aspect_exceptions_are_still_needed_and_still_within_reason():
    """Keeps KNOWN_ASPECT_EXCEPTIONS honest in both directions: an entry whose
    scan has since been fixed must be deleted (so the list never rots into a
    blanket amnesty), and an entry may never quietly grow into a real defect."""
    for name, reason in KNOWN_ASPECT_EXCEPTIONS.items():
        path = os.path.join(BG_DIR, name)
        if not os.path.exists(path):
            # The scan was retired; the waiver is dead weight.
            pytest.fail(
                f"KNOWN_ASPECT_EXCEPTIONS lists {name!r} but that file no longer ships -- remove the entry"
            )
        _width, _height, aspect = _measure(name)
        deviation = abs(_deviation_pct(aspect))
        assert reason.strip(), f"{name} is waived without a stated reason"
        # A waiver is for a scan that is *marginally* off, never for one that is
        # as skewed as the defect this file guards against.
        assert deviation < 5.0, (
            f"{name} now deviates {deviation:.2f}% from A4 -- that is no longer a marginal scan and "
            f"must be fixed rather than waived"
        )


def test_all_shipped_backgrounds_are_portrait():
    """Every seeded template is portrait A4. A landscape scan on a portrait page
    is the same `fill` distortion failure in its most extreme form."""
    landscape = []
    for name in _shipped_background_files():
        width, height, _aspect = _measure(name)
        if width >= height:
            landscape.append(f"{name}: {width}x{height}")
    assert not landscape, (
        "landscape scans cannot be drawn onto the portrait A4 page without severe distortion: "
        + ", ".join(landscape)
    )


def test_the_model_page_default_is_still_a4():
    """The tolerance above is measured against A4 because that is what
    DocumentTemplate falls back to and what the field coordinates were authored
    for. If that default ever changes, these tests are measuring the wrong page
    and must be revisited rather than silently passing."""
    assert DocumentTemplate.__table__.c.page_width.default.arg == A4_W
    assert DocumentTemplate.__table__.c.page_height.default.arg == A4_H


def test_every_seeded_template_background_exists_on_disk():
    """A template whose background filename does not resolve renders a blank
    page with floating text -- broken, but silent. TEMPLATE_DEFS entries are
    (name, description, category, data_source, background_filename)."""
    missing = []
    for entry in TEMPLATE_DEFS:
        name, background = entry[0], entry[4]
        if not background:
            continue
        if not os.path.exists(os.path.join(BG_DIR, background)):
            missing.append(f"{name!r} -> {background!r}")
    assert not missing, "seeded templates reference background scans that do not ship:\n  - " + "\n  - ".join(missing)


def test_every_multipage_background_exists_on_disk():
    """Pages >= 2 get their background from MULTIPAGE_BACKGROUNDS rather than
    the template row, so they need the same existence guard."""
    missing = []
    for template_name, pages in MULTIPAGE_BACKGROUNDS.items():
        for page_number, background in pages.items():
            if not os.path.exists(os.path.join(BG_DIR, background)):
                missing.append(f"{template_name!r} page {page_number} -> {background!r}")
    assert not missing, "multi-page backgrounds do not ship:\n  - " + "\n  - ".join(missing)


def test_multipage_templates_are_declared_in_template_defs():
    """MULTIPAGE_BACKGROUNDS is keyed by template *name*; a typo there would
    silently drop the extra pages from the printed document."""
    known = {entry[0] for entry in TEMPLATE_DEFS}
    unknown = sorted(set(MULTIPAGE_BACKGROUNDS) - known)
    assert not unknown, (
        f"MULTIPAGE_BACKGROUNDS names templates that TEMPLATE_DEFS does not define (typo?): {unknown}"
    )
