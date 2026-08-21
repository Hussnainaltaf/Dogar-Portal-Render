"""Print-path geometry: character cells and the phantom-page overflow.

WHY THIS FILE EXISTS
--------------------
Two defects shipped to users and were visible in every printed document, yet
the whole suite was green. Both were in the HTML print path -- the one the
portal's Print button actually uses -- while the PDF engine (well covered by
tests) did the same job correctly. The tests below pin the *printed geometry*,
not the source text, so a future refactor cannot quietly reintroduce them.

1. char_cells was never implemented in the HTML path
   ------------------------------------------------
   Grid fields (CNIC, passport no, phone, E-number) fell through to a plain
   <div>, so a 13-digit CNIC printed as one continuous string
   "3460117289185" running straight across the form's 13 pre-printed boxes
   instead of one digit centred in each. On a government form with fixed
   printed boxes that makes the document unusable -- and the PDF export of the
   very same template looked right, which is what made it so confusing to
   diagnose.

2. Every document spooled a phantom extra sheet
   --------------------------------------------
   The portal prints through an off-screen iframe pinned to `height: 297mm`
   (exactly one A4 sheet). Inside that frame the document is still in SCREEN
   media when the raster box is fixed, so the renderers' decorative
   `.sheet{margin:18px auto}` was part of the layout: measured 320.15mm of
   content in a 297.0mm frame. The 23mm overflow was faithfully printed as a
   second, near-blank page. A one-page barcode sheet reported "2 sheets of
   paper" in the print dialog.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api.endpoints.documents import _grid_slot_span

REPO_ROOT = Path(__file__).resolve().parents[1]


# ==========================================================================
# 1. Shared cell maths -- the PDF and HTML paths must agree
# ==========================================================================

def test_cell_slots_are_evenly_spaced_without_grouping():
    from app.services.pdf_engine import cell_slots

    assert cell_slots(5) == [0, 1, 2, 3, 4]


def test_cell_slots_insert_a_gap_between_printed_groups():
    """A Pakistani CNIC grid is printed 5-7-1 with wider gaps at the dashes.

    Modelling it as 13 even cells cannot work: by the last digit the
    accumulated error is two whole cells wide.
    """
    from app.services.pdf_engine import cell_slots

    slots = cell_slots(13, [5, 7, 1])
    assert slots == [0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 14]
    # One blank slot per group boundary, and no slot is ever reused.
    assert len(set(slots)) == len(slots)


@pytest.mark.parametrize(
    "raw,count,expected",
    [
        ([5, 7, 1], 13, [5, 7, 1]),   # valid
        ([5, 7], 13, None),           # does not sum to cell_count
        (["5", "7", "1"], 13, [5, 7, 1]),  # stringy JSON still coerces
        ([0, 13], 13, None),          # zero-width group
        ("5,7,1", 13, None),          # not a list
        (None, 13, None),
    ],
)
def test_malformed_group_sizes_degrade_instead_of_raising(raw, count, expected):
    """Template meta is operator-editable JSON; bad values must not 500."""
    from app.services.pdf_engine import coerce_group_sizes

    assert coerce_group_sizes(raw, count) == expected


@pytest.mark.parametrize(
    "value,fmt,expected",
    [
        ("34601-1728918-5", "digits", "3460117289185"),  # typed separators
        ("3460117289185", "", "3460117289185"),
        ("PM 5199182", "alnum", "PM5199182"),
        ("13-02-2015", "ddmmyyyy", "13022015"),
        (None, "", ""),
    ],
)
def test_cell_values_are_normalised_before_being_placed(value, fmt, expected):
    """Separators must never consume a cell that belongs to a character."""
    from app.services.pdf_engine import normalize_cell_value

    assert normalize_cell_value(value, fmt) == expected


# ==========================================================================
# 2. The HTML print path really emits one positioned character per cell
# ==========================================================================

_SPAN = re.compile(r'<span[^>]*>(?P<ch>[^<])</span>')


def _cells_div(html: str, field_key: str) -> str | None:
    m = re.search(
        rf'<div class="ov-field ov-cells"[^>]*data-field-key="{field_key}"[^>]*>(.*?)</div>',
        html, re.S,
    )
    return m.group(0) if m else None


def test_char_cells_field_emits_one_span_per_character(client, auth_headers):
    """The regression: a grid field must NOT be one continuous string.

    Renders a real template that has char_cells fields and asserts each
    character is its own element. Before the fix the CNIC arrived as the bare
    text "3460117289185" inside a single <div>.
    """
    from app.db.session import SessionLocal
    from app.models.document import DocumentField, DocumentTemplate

    db = SessionLocal()
    try:
        field = (
            db.query(DocumentField)
            .filter(DocumentField.field_type == "char_cells")
            .first()
        )
        if field is None:
            pytest.skip("no char_cells field configured in this database")
        tpl = db.query(DocumentTemplate).get(field.template_id)
        tpl_id, key = tpl.id, field.field_key
    finally:
        db.close()

    r = client.get(
        f"/api/documents/templates/{tpl_id}/print?auto=0", headers=auth_headers
    )
    assert r.status_code == 200, (r.status_code, r.text[:300])

    block = _cells_div(r.text, key)
    assert block is not None, (
        f"field {key!r} is configured as char_cells but the print HTML did not "
        "render it through the cells emitter -- it has fallen back to a plain "
        "<div>, which prints the whole value as one string across the form's "
        "pre-printed boxes"
    )
    # Each character is individually positioned (left:...px), not run together.
    assert 'position:absolute' in block or 'data-csp-h' in block, (
        "cell characters are not absolutely positioned; they will not line up "
        f"with the printed boxes. Got: {block[:200]}"
    )


def test_char_cells_never_places_characters_outside_their_field_box(client, auth_headers):
    """Self-inconsistent template meta must be clamped, not obeyed.

    Real corruption found in this database: OEP Form "phone" declared 13 cells
    of 31.47pt (=409pt) inside a 99.6pt box -- a 4x overshoot copy-pasted from
    the "address" field. Obeying it sprays digits across neighbouring fields
    and off the page edge, so the renderer falls back to a fitted pitch.
    """
    from app.api.endpoints.documents import _fitted_cell_pitch

    # 13 cells of 31.47pt cannot fit a 99.6pt box -> must clamp.
    pitch, clamped = _fitted_cell_pitch(width=99.6, count=13, gap=0.0, stored=31.47)
    assert clamped is True
    assert pitch * 13 <= 99.6 + 0.01, "clamped pitch still overflows the field"

    # A consistent template is left exactly as the designer specified.
    pitch, clamped = _fitted_cell_pitch(width=351.0, count=15, gap=0.0, stored=23.4)
    assert clamped is False
    assert pitch == pytest.approx(23.4)

    # No stored pitch at all -> derive one that exactly fills the box.
    pitch, clamped = _fitted_cell_pitch(width=100.0, count=10, gap=0.0, stored=0.0)
    assert pitch == pytest.approx(10.0)


# ==========================================================================
# 3. The phantom second sheet
# ==========================================================================

def _css_rules(src: str):
    """Yield (selector, declarations) for CSS embedded in a Python source file.

    Renderer CSS lives inside f-strings, so its literal braces are doubled;
    un-double them first so one scan works for every renderer. Python code in
    the same file also yields junk pairs -- callers filter by selector, so junk
    is harmless.
    """
    flat = src.replace("{{", "{").replace("}}", "}")
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", flat):
        yield m.group(1), m.group(2)


def _zeroes_margin_on(src: str) -> set[str]:
    """Which of html.print-frame / html.print-frame body actually get margin:0.

    Deliberately rule-scoped: an earlier version of this test matched the
    regex `html\\.print-frame[^{]*\\{[^}]*margin:\\s*0`, which kept passing
    when the critical root/body reset was deleted because the *unrelated*
    `html.print-frame .sheet{margin:0}` rule still satisfied it. A mutation
    run caught that vacuity; this helper closes it by attributing each
    margin:0 to the exact selector that carries it.
    """
    covered: set[str] = set()
    for sel, decls in _css_rules(src):
        if not re.search(r"margin:\s*0", decls):
            continue
        for part in sel.split(","):
            part = part.strip()
            if re.search(r"(?:^|\s)html\.print-frame$", part):
                covered.add("html.print-frame")
            elif re.search(r"(?:^|\s)html\.print-frame body$", part):
                covered.add("html.print-frame body")
    return covered


def test_print_frame_resets_are_present_in_every_renderer():
    """All three renderers must neutralise their screen chrome in the frame.

    The portal prints via an off-screen iframe that is still in SCREEN media
    when its raster box is fixed, so a decorative `margin:18px auto` becomes
    ~23mm of overflow -> a spooled blank page. `@media print` fires too late to
    help. Each renderer therefore ships a `.print-frame` reset.
    """
    targets = {
        "app/services/letterhead_renderer.py": "letterhead / barcode sheets",
        "app/services/receipts_renderer.py": "deposit + payment receipts",
        "app/api/endpoints/documents.py": "the coordinate/overlay engine",
    }
    for rel, what in targets.items():
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "print-frame" in src, (
            f"{rel} ({what}) has no .print-frame reset, so printing through the "
            "app's iframe will re-introduce the phantom blank page"
        )
        # The overflow that spools the extra sheet is the ROOT/BODY margin.
        # Zeroing an inner element (.sheet, .doc-page) does not remove it, so
        # demand both selectors explicitly rather than "some .print-frame rule
        # somewhere mentions margin:0".
        covered = _zeroes_margin_on(src)
        missing = {"html.print-frame", "html.print-frame body"} - covered
        assert not missing, (
            f"{rel} ({what}) defines .print-frame but does not zero the margin "
            f"on {sorted(missing)}. The document root/body margin is the actual "
            "overflow that spools an extra sheet -- resetting only inner "
            "elements such as .sheet or .doc-page does not remove it."
        )


def test_overlay_engine_disables_the_fit_scale_transform_when_printing():
    """fit-mode is a VIEWING aid; printing it rescales every coordinate.

    This engine's entire job is putting text at exact millimetre positions on
    pre-printed forms. If the fit-to-width transform survives into the print
    frame, every field lands off its box.
    """
    src = (REPO_ROOT / "app/api/endpoints/documents.py").read_text(encoding="utf-8")
    block = re.search(
        r"html\.print-frame[^{]*\.doc-page[^{]*\{(?P<body>[^}]*)\}", src, re.S
    )
    assert block, "no .print-frame rule targets .doc-page"
    assert re.search(r"transform:\s*none", block.group("body")), (
        "the fit-to-width scale transform is not reset for printing, so the "
        "printed page is scaled and every field coordinate is wrong"
    )


def test_print_iframe_is_not_pinned_to_exactly_one_sheet():
    """A rigid height:297mm frame manufactures a second page.

    Height must be free (min-height is fine) so a 1-page document stays 1 page
    and a genuinely multi-page document can still lay out. Width must stay
    rigid at 210mm or the mm-precise coordinates rescale.
    """
    src = (REPO_ROOT / "app/frontend/src/lib/print.ts").read_text(encoding="utf-8")
    style = re.search(r"Object\.assign\(iframe\.style,\s*\{(?P<b>.*?)\}\)", src, re.S)
    assert style, "could not find the print iframe style block"
    body = style.group("b")

    assert re.search(r"width:\s*'210mm'", body), (
        "the print iframe must stay exactly 210mm wide or every stored "
        "millimetre coordinate rescales"
    )
    assert not re.search(r"(?<!min)height:\s*'297mm'", body.replace("minHeight", "min_h")), (
        "the print iframe is pinned to height:297mm (exactly one A4 sheet); "
        "any overflow is spooled as a phantom extra page"
    )
    assert "minHeight" in body, "expected min-height so short documents stay one page"


def test_print_helper_marks_the_document_before_printing():
    """Printing must be UNREACHABLE without the .print-frame reset applied.

    Ordering in the source is not the guarantee that matters -- the class is
    applied inside an onload handler while print() lives in a callback. What
    matters is that no code path can reach print() with the reset unapplied.
    The original implementation had exactly that hole: if `contentDocument`
    access threw, the catch branch still fired the print, resurrecting the
    phantom blank page. So the marking is now factored into a helper that
    print() itself calls.
    """
    src = (REPO_ROOT / "app/frontend/src/lib/print.ts").read_text(encoding="utf-8")
    assert "classList.add('print-frame')" in src, (
        "print.ts never tags the framed document, so the renderers' "
        ".print-frame resets never activate"
    )

    # Isolate doPrint() and require it to mark the frame itself, so the reset
    # cannot be bypassed by any caller or error path.
    body = re.search(r"const doPrint = \(\) => \{(?P<b>.*?)\n  \}", src, re.S)
    assert body, "could not locate doPrint() in print.ts"
    marks = body.group("b").index("markPrintFrame()")
    prints = body.group("b").index("w.print()")
    assert marks < prints, (
        "doPrint() calls window.print() without first applying the "
        ".print-frame reset -- the phantom blank page comes back"
    )


# ==========================================================================
# 4. The WRITE path -- where the corruption actually came from
# ==========================================================================
#
# The clamps above are damage control at render time. This section pins the
# real fix: an impossible character grid must never be STORED in the first
# place. The corruption in this database was not shipped that way -- it was
# produced by ordinary designer use, because `meta.cell_width` is stored
# independently of the field box and nothing rescaled it when the box shrank.

def test_saving_an_impossible_cell_grid_is_refitted_not_stored():
    """A grid wider than its box must be refitted before it reaches the DB.

    Reproduces the exact corruption found in the live database: OEP Form
    "phone" carried the "address" pitch (13 cells x 31.47pt = 409pt) inside a
    99.6pt box, so the printed digits ran clean across the neighbouring
    fields of a pre-printed government form.
    """
    from app.api.endpoints.documents import _sanitize_char_cell_meta

    row = {
        "field_key": "phone", "field_type": "char_cells", "width": 99.6,
        "meta": {"cell_count": 13, "cell_width": 31.47, "cell_gap": 0},
    }
    note = _sanitize_char_cell_meta(row)

    assert note, "an impossible grid was accepted silently"
    fitted = row["meta"]["cell_width"]
    assert fitted == pytest.approx(7.66, abs=0.01), fitted
    # The refitted grid must actually fit.
    assert fitted * 13 <= 99.6 + 1.0
    # And the operator must be told what changed, by field name.
    assert "phone" in note


def test_a_grid_that_fits_is_left_exactly_as_the_designer_set_it():
    """Refitting must not "helpfully" rewrite valid, deliberate geometry.

    These numbers were measured against a scanned form; silently normalising
    them would be its own defect.
    """
    from app.api.endpoints.documents import _sanitize_char_cell_meta

    meta = {"cell_count": 15, "cell_width": 23.4, "cell_gap": 0}
    row = {"field_key": "cnic", "field_type": "char_cells",
           "width": 351.0, "meta": dict(meta)}
    assert _sanitize_char_cell_meta(row) is None
    assert row["meta"] == meta


@pytest.mark.parametrize("row", [
    {"field_key": "name", "field_type": "text", "width": 10,
     "meta": {"cell_count": 13, "cell_width": 99.0}},          # not a grid
    {"field_key": "x", "field_type": "char_cells", "width": 0,
     "meta": {"cell_count": 13, "cell_width": 99.0}},          # unknown box
    {"field_key": "x", "field_type": "char_cells", "width": 100,
     "meta": {"cell_count": 0, "cell_width": 99.0}},           # no cells
    {"field_key": "x", "field_type": "char_cells", "width": 100, "meta": {}},
    {"field_key": "x", "field_type": "char_cells", "width": 100, "meta": None},
    {"field_key": "x", "field_type": "char_cells", "width": 100,
     "meta": {"cell_count": "abc", "cell_width": "xyz"}},      # junk
])
def test_sanitizer_degrades_safely_on_incomplete_rows(row):
    """Never raise on a half-filled row -- saving must not 500."""
    from app.api.endpoints.documents import _sanitize_char_cell_meta
    assert _sanitize_char_cell_meta(row) is None


def test_every_field_write_path_enforces_the_grid_invariant():
    """Bulk save is not the only writer; the single-field routes must guard too.

    `create_field` / `update_field` are still reachable by scripts and older
    clients. If only the bulk endpoint sanitised, they would remain an open
    back door to the same corruption.
    """
    src = (REPO_ROOT / "app/api/endpoints/documents.py").read_text(encoding="utf-8")
    for func in ("def create_field(", "def update_field(", "def bulk_save_fields("):
        start = src.index(func)
        # Body = up to the next top-level def/decorator.
        nxt = re.search(r"\n@router\.", src[start:])
        body = src[start:start + (nxt.start() if nxt else len(src) - start)]
        assert "_sanitize_char_cell_meta" in body, (
            f"{func.strip('def (')} persists fields without enforcing the "
            "char_cells fit invariant -- impossible geometry can still be stored"
        )


def test_the_designer_refits_a_grid_when_its_box_is_resized():
    """Client-side mirror of the same invariant.

    The server is the authority, but if the designer lets you drag a box
    smaller and keeps drawing the old pitch, the canvas disagrees with what
    will actually print -- which is how the bad value got typed in the first
    place.
    """
    engine = (REPO_ROOT / "app/frontend/src/pages/designer/engine.ts").read_text(encoding="utf-8")
    assert "export function refitCellMeta" in engine, (
        "designer has no refitCellMeta helper"
    )

    page = (REPO_ROOT / "app/frontend/src/pages/DocumentCustomize.tsx").read_text(encoding="utf-8")
    resize = re.search(r"const beginResize = useCallback\((?P<b>.*?)\n  \}, \[", page, re.S)
    assert resize, "could not locate beginResize()"
    assert "refitCellMeta" in resize.group("b"), (
        "resizing a field does not refit its character grid, so shrinking a "
        "char_cells box leaves an impossible cell_width behind"
    )

    panel = (REPO_ROOT / "app/frontend/src/pages/designer/PropertiesPanel.tsx").read_text(encoding="utf-8")
    assert "refitCellMeta" in panel, (
        "typing a width in the properties panel bypasses the refit"
    )


def test_the_shipped_geometry_snapshot_contains_no_impossible_grid():
    """A fresh install must not be seeded with a broken character grid.

    app/data/template_geometry.json is what every NEW deployment receives (the
    operator's real DB is never shipped -- it holds candidate PII). Exporting
    the snapshot from a DB that had drifted would bake today's corruption into
    every future install, which is precisely the failure mode the snapshot was
    introduced to prevent.
    """
    import json

    snap = json.loads(
        (REPO_ROOT / "app/data/template_geometry.json").read_text(encoding="utf-8")
    )
    broken = []
    for tname, tpl in snap.items():
        for f in tpl.get("fields", []):
            if f.get("field_type") != "char_cells":
                continue
            meta = f.get("meta") or {}
            count = int(meta.get("cell_count") or 0)
            gap = float(meta.get("cell_gap") or 0)
            cw = float(meta.get("cell_width") or 0)
            width = float(f.get("width") or 0)
            if count <= 0 or cw <= 0 or width <= 0:
                continue
            # Measure by SLOTS, not characters: a grouped grid (CNIC 5-7-1)
            # also spends a blank slot on each separator. Counting characters
            # under-measures such a field by two whole cells.
            slots = _grid_slot_span(count, meta.get("group_sizes"))
            needed = cw * slots + gap * max(0, slots - 1)
            if needed > width + 1.0:
                broken.append(
                    f"{tname}/{f.get('field_key')}: {count} cells in {slots} slots "
                    f"x {cw}pt (+{gap}pt gaps) = {needed:.1f}pt in a {width:.1f}pt box"
                )

    assert not broken, (
        "the shipped geometry snapshot declares character grids that cannot "
        "fit their own field box, so every fresh install starts corrupt:\n  "
        + "\n  ".join(broken)
    )


# ==========================================================================
# 5. Grouped grids -- the CNIC 5-7-1 defect
# ==========================================================================
#
# DEFECT 4 (audit 2026-08-11, second pass)
# ----------------------------------------
# Pre-printed government forms rarely use one even run of boxes. A Pakistani
# CNIC is printed 5-7-1, with a wider gap where each dash goes, so 13 digits
# occupy 15 printed boxes. Both render engines have supported `group_sizes`
# for exactly this -- but:
#
#   * the Template Designer exposed NO control for it, so an operator could
#     not set (or repair) it, and
#   * OEP Form "cnic" therefore had no groups at all: measured against the
#     scan, 8 of its 13 digits printed into the WRONG pre-printed box.
#
# DEFECT 5: every pitch calculation assumed `cell_count` slots. On the one
# template that IS correctly grouped (Allied Bank "cnic"), that under-measured
# the grid by two whole cells -- 191.36pt computed against a true 220.80pt
# span -- making the overflow check a false negative and, worse, making the
# automatic repair actively wrong.

#: Left edges of the 15 pre-printed CNIC boxes on the OEP scan, in PDF points.
#: Measured directly off app/static/pdf_backgrounds/oep_form_p1.jpg by
#: detecting its vertical rules (1653px wide / 595pt => 2.7782 px/pt).
OEP_CNIC_BOX_EDGES = [
    155.50, 178.54, 202.29, 225.33, 249.09, 272.84, 295.88, 319.64,
    342.67, 366.07, 389.47, 413.22, 436.26, 459.66, 483.06,
]


def test_a_grouped_grid_is_measured_by_slots_not_characters():
    """13 characters in a 5-7-1 grid occupy 15 slots, not 13.

    This is the number every fit check and every repair must use.
    """
    assert _grid_slot_span(13, [5, 7, 1]) == 15
    assert _grid_slot_span(13, None) == 13          # ungrouped is unchanged
    assert _grid_slot_span(13, [9, 9]) == 13        # malformed -> even run


def test_a_correctly_grouped_grid_is_not_mistaken_for_corruption():
    """Allied Bank "cnic" fills its box EXACTLY -- it must be left alone.

    15 slots x 14.72pt = 220.80pt in a 220.8pt box. The character-counting
    maths saw only 191.36pt, so it could not detect a grouped grid
    overflowing; and had anything triggered a refit, dividing the width by 13
    instead of 15 would have walked every digit after the first dash out of
    its printed box.
    """
    from app.api.endpoints.documents import _fitted_cell_pitch

    pitch, clamped = _fitted_cell_pitch(
        width=220.8, count=13, gap=0.0, stored=14.72, group_sizes=[5, 7, 1]
    )
    assert not clamped, "a grid that exactly fills its box must not be clamped"
    assert pitch == 14.72, "a fitting pitch must be returned untouched"


def test_a_grouped_grid_that_overflows_is_still_caught():
    """The fit check must not go blind just because a grid is grouped."""
    from app.api.endpoints.documents import _fitted_cell_pitch

    # 15 slots x 14.72 = 220.8pt declared inside a 180pt box.
    pitch, clamped = _fitted_cell_pitch(
        width=180.0, count=13, gap=0.0, stored=14.72, group_sizes=[5, 7, 1]
    )
    assert clamped
    assert pitch == pytest.approx(180.0 / 15), (
        "the refit must divide the box by the SLOT count (15), not the "
        "character count (13) -- otherwise the repair itself misaligns the grid"
    )


def test_the_sanitizer_respects_groups_when_repairing():
    from app.api.endpoints.documents import _sanitize_char_cell_meta

    row = {
        "field_type": "char_cells", "field_key": "cnic", "width": 220.8,
        "meta": {"cell_count": 13, "cell_width": 14.72, "cell_gap": 0,
                 "group_sizes": [5, 7, 1]},
    }
    assert _sanitize_char_cell_meta(row) is None, (
        "a correctly-configured grouped grid must survive a save untouched"
    )
    assert row["meta"]["cell_width"] == 14.72


def test_the_oep_cnic_digits_land_in_their_printed_boxes():
    """Every CNIC digit must sit in the box the form actually printed.

    Guards the live defect: without group_sizes, digits 6..13 shift one or two
    boxes left and the last digit misses the final box entirely. Tolerance is
    a quarter of the box pitch -- past that the digit is visibly out of its box.
    """
    import json

    from app.services.pdf_engine import cell_slots

    snap = json.loads(
        (REPO_ROOT / "app/data/template_geometry.json").read_text(encoding="utf-8")
    )
    oep = next((t for n, t in snap.items() if "OEP" in n), None)
    assert oep, "the OEP Form must be present in the shipped geometry snapshot"
    fld = next(
        (f for f in oep["fields"]
         if f.get("field_key") == "cnic" and f.get("field_type") == "char_cells"),
        None,
    )
    assert fld, "the OEP Form must place the candidate's CNIC"

    meta = fld.get("meta") or {}
    groups = meta.get("group_sizes")
    assert groups == [5, 7, 1], (
        "the OEP CNIC comb is printed as 5-7-1 (13 digits across 15 boxes). "
        f"Without group_sizes the digits are evenly spread and 8 of 13 land in "
        f"the wrong printed box. Found group_sizes={groups!r}."
    )

    x0 = float(fld["x"])
    pitch = float(meta["cell_width"])
    slots = cell_slots(int(meta["cell_count"]), groups)
    tolerance = pitch / 4
    for i, slot in enumerate(slots):
        placed = x0 + slot * pitch
        printed = OEP_CNIC_BOX_EDGES[slot]
        assert abs(placed - printed) <= tolerance, (
            f"CNIC digit {i + 1} is placed at x={placed:.2f}pt but the form "
            f"prints that box at x={printed:.2f}pt "
            f"(off by {placed - printed:+.2f}pt)"
        )


def test_the_designer_can_edit_group_sizes():
    """The operator must be able to set groups without a developer.

    Both engines honoured `group_sizes`, but the properties panel had no input
    for it -- so the misaligned CNIC above was unfixable from the UI, which is
    why it survived. A rendering feature with no control is not shipped.
    """
    src = (REPO_ROOT / "app/frontend/src/pages/designer/PropertiesPanel.tsx").read_text(
        encoding="utf-8"
    )
    # A bare `"group_sizes" in src` check is not enough: it also matches a
    # renamed/disabled key such as `group_sizes_DISABLED`, and it matches a
    # mention in a comment. Require the panel to actually WRITE the exact meta
    # key through the patch callback -- that is the only thing that reaches the
    # template. (Verified by mutation: the loose check missed a rename.)
    writes = re.findall(r"onPatchMeta\(\s*'([A-Za-z_]+)'", src)
    assert "group_sizes" in writes, (
        "the Template Designer properties panel never writes the `group_sizes` "
        "meta key, so an operator cannot configure a grouped character grid "
        "(CNIC 5-7-1) or repair one that is wrong. "
        f"Keys the panel does write: {sorted(set(writes))}"
    )
    # It must also READ the current value back, or the control cannot show the
    # operator what the field is set to.
    assert re.search(r"meta\.group_sizes\b", src), (
        "the panel writes group_sizes but never reads meta.group_sizes back, so "
        "the control cannot display the grid's current grouping"
    )


# ==========================================================================
# 6. Units -- points are not pixels
# ==========================================================================
#
# DEFECT 6 (audit 2026-08-11, second pass; found by measuring the LIVE page,
# not by reading code)
# ----------------------------------------------------------------------
# Every coordinate in this project is stored in PDF points. The print page
# lays everything out in millimetres via PT_TO_MM -- field boxes, barcodes,
# backgrounds, page size. But the char_cells character spans emitted their
# offsets as `px`. CSS defines 1px = 1/96in and 1pt = 1/72in, so the grid
# rendered at exactly 0.75x its designed pitch: a 23.4pt CNIC comb came out
# at 17.55pt and the digits crept left cumulatively, the last one landing
# 11.83pt (half a box) out of place.
#
# The two engines therefore still disagreed AFTER the grid maths was shared:
# the PDF was right and the printed HTML was wrong, because only the HTML
# path had a unit conversion to get wrong. Measured in Chromium before/after:
# worst digit offset 11.83pt -> 0.46pt against the scan's printed boxes.

def test_char_cell_spans_are_positioned_in_millimetres_not_raw_points():
    """The character spans must convert points to mm like everything else.

    A raw `px` value silently shrinks every character grid to 75% of its
    designed pitch. This is invisible in the PDF export (different engine) and
    invisible in code review (the number looks right) -- it only shows up when
    you measure the rendered page, so pin it here.
    """
    src = (REPO_ROOT / "app/api/endpoints/documents.py").read_text(encoding="utf-8")

    # Isolate the char_cells span builder.
    start = src.find("_spans.append(")
    assert start != -1, "the char_cells span builder has moved or been removed"
    block = src[start:start + 700]

    assert "px;" not in block, (
        "the char_cells character spans emit raw point values as CSS `px`. "
        "1px is 1/96in but a point is 1/72in, so every character grid prints "
        "at 0.75x its designed pitch and the characters drift out of their "
        "pre-printed boxes. Convert with PT_TO_MM like the rest of the page."
    )
    assert "PT_TO_MM" in block and "mm;" in block, (
        "the char_cells spans must convert points to mm via PT_TO_MM, the same "
        "conversion the field boxes, barcodes and page size all use"
    )


def test_no_overlay_geometry_is_emitted_as_raw_pixels():
    """No layout value on the printed page may be emitted as `px`.

    Guards the whole class of bug rather than the one instance: the page is a
    physical sheet measured in mm, so any `px` in a positioning style is a
    unit error waiting to be found on paper. (Hairline borders and similar
    non-layout uses are matched explicitly and allowed.)
    """
    src = (REPO_ROOT / "app/api/endpoints/documents.py").read_text(encoding="utf-8")
    offenders = []
    for m in re.finditer(r"f?'[^']*(?:left|top|width|height):\{[^']*?px;", src):
        line = src[:m.start()].count("\n") + 1
        offenders.append(f"line {line}: {m.group(0)[:80]}")
    assert not offenders, (
        "printed-page geometry is being emitted in `px` instead of `mm`:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 7. MULTI-ROW COMBS  --  char_offset
#
# OEP form OEPE-RF-02 prints BOTH field 6 (Name) and field 12 (Address) as two
# stacked rows of boxes -- measured on the scan by /tmp/oep/recount.py and
# stable at every detection threshold: Name = 2 rows x 14 boxes, Address =
# 2 rows x 13 boxes.
#
# Until char_offset existed, every char_cells row started at character 0, so a
# second row could only ever REPEAT the beginning of the value. That is why
# only row 1 was ever placed, and why a 38-character address printed just its
# first 13 characters and silently dropped the rest.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        (13, 13),
        ("13", 13),      # meta arrives as operator-edited JSON
        (0, 0),
        (-5, 0),         # negative would slice from the END of the value
        (None, 0),
        ("", 0),
        ("abc", 0),      # malformed must degrade, never raise
        ([1, 2], 0),
    ],
)
def test_char_offset_is_coerced_defensively(raw, expected):
    """Template meta is operator-editable; a bad offset must not 500 a print."""
    from app.services.pdf_engine import coerce_char_offset

    assert coerce_char_offset(raw) == expected


def test_a_continuation_row_renders_the_rest_of_the_value_not_the_start():
    """Row 2 of a two-row comb must continue where row 1 stopped.

    This is the whole point of char_offset. If it regresses, the second row
    silently repeats row 1 and the printed form shows the first 13 characters
    twice -- which reads as plausible data and so would not be spotted.
    """
    from app.services.pdf_engine import _draw_char_cells

    drawn: list[tuple[float, str]] = []

    class _FakeCanvas:
        def setFont(self, *a, **k): pass
        def setFillColor(self, *a, **k): pass
        def setStrokeColor(self, *a, **k): pass
        def setLineWidth(self, *a, **k): pass
        def rect(self, *a, **k): pass
        def drawCentredString(self, x, y, ch): drawn.append((x, ch))

    value = "P.O KHAS MALO CHAT TEH O DISTT SIALKOT"

    drawn.clear()
    _draw_char_cells(_FakeCanvas(), value, x=155.5, y=420.0,
                     cell_count=13, cell_width=31.45, keep_spaces=True)
    row1 = "".join(ch for _, ch in drawn)

    drawn.clear()
    _draw_char_cells(_FakeCanvas(), value, x=155.5, y=400.36,
                     cell_count=13, cell_width=31.45, keep_spaces=True,
                     char_offset=13)
    row2 = "".join(ch for _, ch in drawn)

    assert row1 == "P.O KHAS MALO", row1
    assert row2 == value[13:26], row2
    assert row2 != row1, "row 2 repeated row 1 -- char_offset was ignored"


def test_the_designer_can_edit_char_offset():
    """A capability with no UI control is a capability nobody can use.

    group_sizes was honoured by both engines for months while being unsettable,
    which is exactly why OEP 'cnic' shipped with 8 of 13 digits in the wrong
    printed box. Assert the WRITE PATH, not merely the substring: a bare
    `'char_offset' in src` also matches a renamed/disabled key.
    """
    src = (REPO_ROOT / "app/frontend/src/pages/designer/PropertiesPanel.tsx").read_text(
        encoding="utf-8")
    writes = re.findall(r"onPatchMeta\(\s*'([A-Za-z_]+)'", src)
    assert "char_offset" in writes, (
        "the Template Designer has no control that writes meta.char_offset, so "
        "a two-row comb cannot be configured through the UI"
    )
    assert re.search(r"meta\.char_offset\b", src), (
        "the designer never reads back meta.char_offset, so the control cannot "
        "show the operator the current value"
    )


# ---------------------------------------------------------------------------
# 8. WORD BREAKS IN NAME / ADDRESS COMBS  --  format "text"
#
# normalize_cell_value() strips spaces, which is right for an ID grid (an
# operator typing "34601-1728918-5" must not waste a cell on punctuation) but
# wrong for a name or address comb, where the printed form expects one letter
# per box and a BLANK box between words. "MUHAMMAD IMRAN" was landing as
# "MUHAMMADIMRAN": every letter after the space shifted one box left, and the
# continuation row inherited the shift.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value,fmt,expected",
    [
        ("MUHAMMAD IMRAN", "text", "MUHAMMAD IMRAN"),
        ("P.O KHAS MALO CHAT", "text", "P.O KHAS MALO CHAT"),
        ("  A   B  ", "text", "A B"),           # runs collapse, ends trimmed
        # "text" preserves punctuation as typed. Unlike the ID formats it must
        # NOT eat hyphens: real Pakistani addresses contain them ("MOH-ALLAH
        # ABAD"), and a comb box holds whatever character the operator wrote.
        ("34601-1728918-5", "text", "34601-1728918-5"),
        (None, "text", ""),
        # the ID formats must be UNCHANGED by this addition
        ("34601-1728918-5", "digits", "3460117289185"),
        ("PM 5199182", "alnum", "PM5199182"),
        ("MUHAMMAD IMRAN", "", "MUHAMMADIMRAN"),
    ],
)
def test_only_the_text_format_keeps_word_breaks(value, fmt, expected):
    from app.services.pdf_engine import normalize_cell_value

    assert normalize_cell_value(value, fmt) == expected


def test_the_pdf_engine_does_not_re_strip_spaces_it_was_asked_to_keep():
    """The engine strips separators a SECOND time after normalisation.

    That second strip would silently undo format="text", so the fix has to be
    honoured in both places or names run together in the PDF while printing
    correctly in HTML -- the exact engine divergence that hid the px/pt bug.
    """
    from app.services.pdf_engine import _draw_char_cells

    drawn: list[str] = []

    class _FakeCanvas:
        def setFont(self, *a, **k): pass
        def setFillColor(self, *a, **k): pass
        def setStrokeColor(self, *a, **k): pass
        def setLineWidth(self, *a, **k): pass
        def rect(self, *a, **k): pass
        def drawCentredString(self, x, y, ch): drawn.append(ch)

    _draw_char_cells(_FakeCanvas(), "MUHAMMAD IMRAN", x=0, y=0,
                     cell_count=14, cell_width=29.36, keep_spaces=True)
    assert "".join(drawn) == "MUHAMMAD IMRAN"

    drawn.clear()
    _draw_char_cells(_FakeCanvas(), "MUHAMMAD IMRAN", x=0, y=0,
                     cell_count=14, cell_width=29.36)
    assert "".join(drawn) == "MUHAMMADIMRAN", "ID grids must still drop spaces"


def test_the_print_path_actually_applies_char_offset(client, auth_headers):
    """Both engines must agree about what a two-row comb means.

    MUTATION-DRIVEN (this assertion was rewritten after a mutation escaped):
    the first version merely checked that the string "_coerce_offset" appeared
    somewhere in the module, which a mutation replacing the CALL with
    `_offset = 0  # _coerce_offset disabled` still satisfied -- a vacuous test
    that passed while the print path silently printed row 1 twice.

    SECOND REWRITE (comb capacity policy, 2026-08-14): the source-text form of
    this test broke -- correctly -- when char_offset handling moved OUT of this
    module and into ``app/services/comb_capacity.resolve_comb_row()``, so one
    function decides the whole multi-row comb for all three renderers instead of
    each one slicing independently.

    Re-pointing the regex at the new spelling would keep it a grep and would
    break again on the next refactor. So this now asserts the CAPACITY CONTRACT
    behaviourally, against a value deliberately made longer than the whole comb:

      * the rows together never render more than the comb can hold, and
      * no single row renders more characters than it has cells
        (which is what stops a value bleeding over the field beside it).

    That is complementary to -- not a duplicate of --
    ``test_the_print_endpoint_really_continues_a_two_row_comb``, which seeds a
    value that FITS and checks the continuation row is not a copy of row 1.
    Here the value deliberately does NOT fit, which is the 83.4% case.
    """
    from app.db.session import SessionLocal
    from app.models.document import DocumentField, DocumentTemplate
    from app.services.comb_capacity import comb_capacity_for_key, comb_rows_from_fields

    db = SessionLocal()
    try:
        rows = (
            db.query(DocumentField)
            .filter(DocumentField.field_type == "char_cells")
            .all()
        )
        # A two-row comb on a CANDIDATE template: that is the data source this
        # test can seed deterministically (see _seed_long_value_candidate).
        target = None
        for f in rows:
            meta = f.meta if isinstance(f.meta, dict) else (f.meta or {})
            if int(meta.get("char_offset") or 0) <= 0:
                continue
            tpl = db.get(DocumentTemplate, f.template_id)
            if tpl is not None and tpl.data_source == "candidate":
                target = (f, tpl)
                break
        if target is None:
            pytest.skip("no two-row comb on a candidate template in this database")
        field, tpl = target
        tpl_id = tpl.id
        key = field.field_key
        page = int(field.page or 1)
        siblings = [
            {"field_type": o.field_type, "field_key": o.field_key,
             "page": o.page, "meta": (o.meta if isinstance(o.meta, dict) else {})}
            for o in rows if o.template_id == tpl_id
        ]
        comb_rows = comb_rows_from_fields(siblings, key, page)
        capacity = comb_capacity_for_key(comb_rows)
        # cell_count per row, so "no row exceeds its own cells" is checkable.
        row_cells = sorted(
            (off, cnt) for off, cnt in comb_rows
        )
    finally:
        db.close()

    assert capacity > 0, f"{key!r} comb reports zero capacity"

    # A record is REQUIRED: without record_id the endpoint renders EMPTY combs,
    # and comparing empty rows passes no matter what the engine does -- that is
    # exactly how the source-regex version of this test went vacuous.
    #
    # The value is deliberately DOUBLE the comb's capacity, with spaces so the
    # policy has word boundaries to cut on. Whatever the policy decides, the
    # rendered result must still fit inside the printed boxes.
    long_value = " ".join(["ABCDE"] * ((capacity // 3) + 2))
    assert len(long_value) > capacity, "fixture value is not over capacity"
    record_id = _seed_long_value_candidate(key, long_value)

    r = client.get(
        f"/api/documents/templates/{tpl_id}/print?auto=0&record_id={record_id}",
        headers=auth_headers,
    )
    assert r.status_code == 200, (r.status_code, r.text[:300])

    blocks = re.findall(
        rf'<div class="ov-field ov-cells"[^>]*?data-field-key="{re.escape(key)}"'
        rf'[^>]*?>(.*?)</div>',
        r.text, re.S,
    )
    assert len(blocks) >= 2, (
        f"the print HTML rendered {len(blocks)} comb rows for {key!r} but the "
        f"template defines {len(comb_rows)}"
    )

    def _row_text(block: str) -> str:
        return "".join(re.findall(r">([^<>])</span>", block))

    row_texts = [_row_text(b) for b in blocks]
    rendered_total = sum(len(t) for t in row_texts)

    assert any(t.strip() for t in row_texts), (
        f"a {len(long_value)}-character {key!r} rendered NOTHING in any comb "
        f"row; the value is not reaching the print path at all"
    )
    # THE CAPACITY CONTRACT: an over-long value must be reduced to something the
    # form can physically hold, not sprayed past the boxes.
    assert rendered_total <= capacity, (
        f"a {len(long_value)}-character value rendered {rendered_total} "
        f"characters into a {capacity}-character comb -- the overflow is being "
        f"printed over whatever sits beside it. Rows: {row_texts!r}"
    )
    # And per row, so one row cannot swallow another row's budget.
    assert len(row_texts) == len(row_cells), (
        f"the print HTML rendered {len(row_texts)} comb rows for {key!r} but "
        f"the template defines {len(row_cells)}; the rows and the geometry have "
        f"drifted apart"
    )
    for (offset, cells), text in zip(row_cells, row_texts, strict=True):
        assert len(text) <= cells, (
            f"the comb row at char_offset={offset} rendered {len(text)} "
            f"characters into {cells} cells: {text!r}"
        )


def test_an_overflowing_comb_value_is_reported_not_silently_cut():
    """Truncation on a government form must never be silent.

    The Address comb holds 26 characters across its two measured rows while
    real data reaches 38. Dropping the tail without a word produces a document
    that looks complete -- the worst possible failure mode.
    """
    import logging

    from app.services.pdf_engine import _draw_char_cells

    class _FakeCanvas:
        def setFont(self, *a, **k): pass
        def setFillColor(self, *a, **k): pass
        def setStrokeColor(self, *a, **k): pass
        def setLineWidth(self, *a, **k): pass
        def rect(self, *a, **k): pass
        def drawCentredString(self, x, y, ch): pass

    records: list[logging.LogRecord] = []

    class _Catch(logging.Handler):
        def emit(self, record): records.append(record)

    log = logging.getLogger("dtc.pdf_engine")
    h = _Catch()
    log.addHandler(h)
    try:
        _draw_char_cells(_FakeCanvas(), "P.O KHAS MALO CHAT TEH O DISTT SIALKOT",
                         x=0, y=0, cell_count=13, cell_width=31.45,
                         keep_spaces=True)
    finally:
        log.removeHandler(h)

    assert any("overflow" in r.getMessage() for r in records), (
        "a value too long for its comb was truncated without any warning"
    )


# ==========================================================================
# 9. BEHAVIOURAL PARITY  --  replacing four vacuous structural assertions
# ==========================================================================
#
# WHY THIS SECTION EXISTS (mutation run 2026-08-12, mutations R/S/T/W)
# --------------------------------------------------------------------
# Sections 1-8 guarded the three render paths mostly by SEARCHING THE SOURCE
# for a helper name. That is a proxy for behaviour, and mutation testing proved
# the proxy is worthless for exactly the defects this engine keeps producing:
#
#   [R] deleting  format:"text"  from the shipped snapshot          -> MISSED
#   [S] making designer refitCellMeta group-blind again             -> MISSED
#   [T] making the designer draw char_cells as one string again     -> MISSED
#   [W] dropping char_offset from the HTML print path               -> MISSED
#
# All four are real, previously-shipped defects. Every one of them left the
# helper NAME in the file, so `"refitCellMeta" in src` still matched and the
# suite stayed green while the portal printed a corrupt government form.
#
# The tests below execute the real code instead:
#   * the TypeScript is transformed with the esbuild binary already vendored in
#     app/frontend/node_modules and run under node (same technique as
#     tests/test_designer_zoom.py -- no new dependency),
#   * FieldMarker.tsx is rendered to real HTML with react-dom/server so the
#     per-cell geometry the OPERATOR sees is measured, not described,
#   * the HTML print path is driven through the real endpoint,
#   * the snapshot is checked for the meta the measured form actually requires.
#
# Structural assertions are kept alongside these (they give a much clearer
# failure message when someone deletes a helper outright); what changed is that
# they are no longer the ONLY thing standing between a regression and a green
# suite.

import json
import os
import shutil
import subprocess
import textwrap

FRONTEND = REPO_ROOT / "app" / "frontend"
DESIGNER = FRONTEND / "src" / "pages" / "designer"
ESBUILD = FRONTEND / "node_modules" / ".bin" / "esbuild"


def _require_node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    if not ESBUILD.exists():
        pytest.skip("esbuild is not installed in app/frontend/node_modules")
    return node


def _run_node(tmp_path, entry: Path, script: str, bundle: bool = False) -> dict:
    """esbuild-transform `entry`, run `script` against it, return its JSON."""
    node = _require_node()
    out = tmp_path / "mod.mjs"
    cmd = [str(ESBUILD), str(entry), "--format=esm", "--target=es2020",
           f"--outfile={out}"]
    if bundle:
        # FieldMarker imports react / sibling modules, so it must be bundled.
        # React itself stays external and is resolved by node at run time.
        cmd += ["--bundle", "--jsx=automatic",
                "--external:react", "--external:react-dom",
                "--external:react/jsx-runtime"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, f"esbuild failed:\n{proc.stderr}"

    run = tmp_path / "run.mjs"
    run.write_text(script, encoding="utf-8")
    # node must resolve `react` / `react-dom/server` from the frontend's own
    # node_modules. NODE_PATH is IGNORED by the ESM resolver, so link the real
    # node_modules next to the script and let normal upward resolution find it.
    link = tmp_path / "node_modules"
    if not link.exists():
        os.symlink(FRONTEND / "node_modules", link)
    proc = subprocess.run([node, str(run)], capture_output=True, text=True,
                          timeout=180, cwd=str(tmp_path))
    assert proc.returncode == 0, f"node failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


# --- [S] the designer's refit must stay group-aware -----------------------

def test_the_designer_refit_does_not_corrupt_a_grouped_grid(tmp_path):
    """BEHAVIOURAL replacement for a `"cellSlotSpan" in src` check.

    refitCellMeta() exists to repair an impossible grid when the operator drags
    a box smaller. Group-blind, it "repairs" a grid that was already CORRECT: a
    13-digit CNIC printed 5-7-1 occupies 15 slots, so dividing the box width by
    13 returns a pitch ~15% too wide and silently walks every digit after the
    first dash out of its printed box. That is the third copy of the same bug
    (server: _grid_slot_span, PDF: cell_slots) and it shipped twice already.

    Mutation [S] replaced `cellSlotSpan(count, meta.group_sizes)` with `count`
    and the old assertion still passed. This one executes the real function.
    """
    res = _run_node(tmp_path, DESIGNER / "engine.ts", textwrap.dedent("""
        import { refitCellMeta, cellSlotSpan } from './mod.mjs'
        const out = {}

        // A CNIC grid that EXACTLY fits its box when grouping is honoured:
        // 15 slots x 23.4pt = 351pt. Nothing needs repairing.
        const cnic = { cell_count: 13, cell_width: 23.4, cell_gap: 0,
                       group_sizes: [5, 7, 1] }
        out.span = cellSlotSpan(13, [5, 7, 1])
        out.exact_fit_untouched = refitCellMeta(cnic, 351).cell_width

        // Genuinely too small -> must refit, and the refitted pitch must fill
        // the box across 15 SLOTS, not 13 characters.
        const shrunk = refitCellMeta(cnic, 300)
        out.shrunk_pitch = shrunk.cell_width
        out.shrunk_total_slots = shrunk.cell_width * 15

        // Ungrouped grids must behave exactly as before (regression guard for
        // every other template in the portal).
        out.plain = refitCellMeta(
          { cell_count: 10, cell_width: 20, cell_gap: 0 }, 100).cell_width
        process.stdout.write(JSON.stringify(out))
    """))

    assert res["span"] == 15, (
        "cellSlotSpan no longer accounts for the blank slot each group "
        "separator occupies; a 5-7-1 CNIC spans 15 slots, not 13"
    )
    assert res["exact_fit_untouched"] == pytest.approx(23.4), (
        f"refitCellMeta 'repaired' a grid that already fit exactly "
        f"(23.4 -> {res['exact_fit_untouched']}). It divided the width by the "
        f"CHARACTER count instead of the SLOT count, so every digit after the "
        f"first dash is now drawn outside its pre-printed box."
    )
    assert res["shrunk_total_slots"] <= 300 + 0.01, (
        f"a genuinely over-wide grid was refitted to "
        f"{res['shrunk_total_slots']}pt, which still overflows its 300pt box"
    )
    assert res["shrunk_pitch"] == pytest.approx(20.0, abs=0.01), (
        "the refitted pitch must fill the box across all 15 slots (300/15=20)"
    )
    assert res["plain"] == pytest.approx(10.0), (
        "an UNGROUPED grid must refit on its character count exactly as it "
        "always did -- every other template in the portal depends on it"
    )


# --- [T] the designer canvas must show the boxes, not a string ------------

def test_the_designer_canvas_draws_one_positioned_cell_per_character(tmp_path):
    """BEHAVIOURAL replacement for a `"char_cells" in src` check.

    THE SCREENSHOT-4 DEFECT. char_cells is in TEXT_LIKE, so the canvas drew the
    grid as ONE continuous string: the operator saw "3450303592411" flowing
    left-to-right and had NO WAY to tell whether the digits would land in the
    form's pre-printed boxes. Every geometry defect in this engine was therefore
    invisible on the one screen whose entire job is to show it -- which is why
    they were only ever found by rasterising output.

    Mutation [T] disabled the whole branch (`if (false)`) and the old assertion
    still passed. This renders the real component with react-dom/server and
    measures the emitted geometry.
    """
    res = _run_node(tmp_path, DESIGNER / "FieldMarker.tsx", textwrap.dedent("""
        import { renderToStaticMarkup } from 'react-dom/server'
        import { createElement as h } from 'react'
        import FieldMarker from './mod.mjs'

        const Marker = FieldMarker.default ?? FieldMarker
        const noop = () => {}

        function render(field, preview) {
          return renderToStaticMarkup(h(Marker, {
            field, pageH: 842, selected: false, previewValue: preview,
            onPointerDown: noop, onResizeStart: noop, onDoubleClick: noop,
          }))
        }

        const out = {}

        // The real measured OEP CNIC row, verbatim from the shipped snapshot:
        // cell_count is the CHARACTER count (13); the 5-7-1 grouping makes it
        // span 15 SLOTS (13 x 23.4 = 304.2, but 15 x 23.4 = 351.0 = the box).
        out.cnic = render({
          _uid: 'a', field_key: 'cnic', field_type: 'char_cells',
          x: 155.6, y: 524.94, width: 351.0, height: 14.8, page: 1,
          font_size: 11, meta: { cell_count: 13, cell_width: 23.4,
                                 cell_gap: 0, group_sizes: [5, 7, 1] },
        }, '34601-1728918-5')

        // The measured Name comb: 14 boxes, spaces KEPT (format 'text').
        out.name = render({
          _uid: 'b', field_key: 'full_name', field_type: 'char_cells',
          x: 155.5, y: 595.73, width: 411.0, height: 14.8, page: 1,
          font_size: 11, meta: { cell_count: 14, cell_width: 29.36,
                                 cell_gap: 0, format: 'text' },
        }, 'MUHAMMAD IMRAN')

        // The continuation row of the Address comb (char_offset).
        out.addr2 = render({
          _uid: 'c', field_key: 'address', field_type: 'char_cells',
          x: 155.6, y: 400.36, width: 408.9, height: 14.8, page: 1,
          font_size: 11, meta: { cell_count: 13, cell_width: 31.45,
                                 cell_gap: 0, format: 'text', char_offset: 13 },
        }, 'P.O KHAS MALO CHAT TEH O DISTT')

        process.stdout.write(JSON.stringify(out))
    """), bundle=True)

    # Each rendered character must be its own absolutely-positioned span.
    # NOTE: React emits a bare `left:0` for zero (no unit), so `px` must be
    # OPTIONAL here -- requiring it silently dropped the first cell and made
    # the count assertion off by one.
    CELL_SPAN = r'<span[^>]*?left:\s*([0-9.]+)(?:px)?;[^>]*?>([^<]?)</span>'
    cells = re.findall(CELL_SPAN, res["cnic"])
    assert len(cells) >= 13, (
        f"the designer canvas emitted {len(cells)} positioned cells for a "
        f"13-digit CNIC grid. It is drawing the grid as one continuous string, "
        f"so the operator cannot see whether the digits land in the form's "
        f"pre-printed boxes -- the exact blindness that hid every geometry "
        f"defect in this engine.\nHTML: {res['cnic'][:400]}"
    )

    lefts = [float(x) for x, _ in cells]
    chars = [c for _, c in cells]

    # GROUPING: the dash positions must leave a blank slot, i.e. the gap
    # between digit 5 and digit 6 is TWICE the normal pitch.
    pitch = lefts[1] - lefts[0]
    assert pitch > 0, "cells are not advancing left-to-right"
    assert lefts[5] - lefts[4] == pytest.approx(2 * pitch, rel=0.02), (
        f"the canvas does not skip a slot where the CNIC's first dash is "
        f"printed (gap {lefts[5] - lefts[4]:.2f} vs pitch {pitch:.2f}); the "
        f"operator is shown an even run that does not match the printed 5-7-1 "
        f"grid"
    )
    assert lefts[12] - lefts[11] == pytest.approx(2 * pitch, rel=0.02), (
        "the canvas does not skip a slot at the CNIC's SECOND dash"
    )
    assert "".join(chars) == "3460117289185", (
        f"the separators were not stripped before the digits were placed: "
        f"{''.join(chars)!r}. A dash would consume a box that belongs to a "
        f"digit and push the national ID one cell right."
    )

    # WORD BREAKS: format 'text' must keep the space between the two names so
    # every letter stays in the box the form printed for it.
    name_chars = [c for _, c in re.findall(CELL_SPAN, res["name"])]
    assert "".join(name_chars).startswith("MUHAMMAD IMRAN"), (
        f"the canvas ran the words together ({''.join(name_chars)!r}); with the "
        f"space dropped, every letter after it is shown one box left of where "
        f"it will actually print"
    )

    # CONTINUATION: a char_offset row must show the REST of the value.
    a2 = "".join(c for _, c in re.findall(CELL_SPAN, res["addr2"]))
    assert a2.strip().startswith("CHAT"), (
        f"the continuation row shows {a2!r} -- it is repeating the START of the "
        f"address instead of continuing from character 13, so the operator "
        f"cannot see what the second printed row will contain"
    )


# --- [W] the HTML print path must apply char_offset for real -------------

def _seed_long_value_candidate(field_key: str, value: str) -> int:
    """Create a candidate whose ``field_key`` is long enough to overflow row 1.

    The test database is isolated and seeded with TEMPLATES ONLY (see
    tests/conftest.py ``_bootstrap_db``) -- there are no candidates in it. An
    earlier draft of this test searched for a record with a long enough value
    and skipped when it found none, which meant it skipped ALWAYS: a test that
    never runs is exactly as useful as the vacuous source-regex assertion it
    was written to replace. So the fixture data is created here instead, which
    also makes the expected output exact rather than dependent on whatever
    happens to be in an operator's database.

    Returns the new candidate's id.
    """
    from app.db.session import SessionLocal
    from app.models.candidate import Candidate

    db = SessionLocal()
    try:
        row = Candidate(full_name="COMB ROW OVERFLOW FIXTURE")
        setattr(row, field_key, value)
        db.add(row)
        db.commit()
        return int(row.id)
    finally:
        db.close()


def test_the_print_endpoint_really_continues_a_two_row_comb(client, auth_headers):
    """BEHAVIOURAL replacement for a regex-on-source check.

    The Print button uses the HTML path in documents.py, NOT pdf_engine.py.
    Those two engines disagreeing about what a template means is the bug class
    that hid the px/pt defect, so parity is asserted against real output.

    Mutation [W] neutered the slice (`if False:`) while leaving every symbol in
    place; the source-regex assertion still passed. Here the real endpoint is
    rendered and the two rows are compared.
    """
    from app.db.session import SessionLocal
    from app.models.document import DocumentField, DocumentTemplate

    db = SessionLocal()
    try:
        rows = (
            db.query(DocumentField)
            .filter(DocumentField.field_type == "char_cells")
            .all()
        )
        combs = []
        for f in rows:
            off = int((f.meta or {}).get("char_offset") or 0)
            if not off:
                continue
            primary = next(
                (o for o in rows
                 if o.template_id == f.template_id
                 and o.field_key == f.field_key
                 and o.page == f.page
                 and not int((o.meta or {}).get("char_offset") or 0)),
                None,
            )
            if primary is not None:
                combs.append((f.template_id, f.field_key, off,
                              int((primary.meta or {}).get("cell_count") or 0)))
        if not combs:
            pytest.fail(
                "no two-row comb is configured at all. OEP form OEPE-RF-02 "
                "prints Name (field 6) and Address (field 12) as two stacked "
                "rows of cells; without a continuation row a long value prints "
                "only its first row and the rest is silently dropped."
            )
        # Prefer a comb on a candidate template -- that is the data source this
        # test can seed deterministically.
        combs = [
            c for c in combs
            if db.query(DocumentTemplate).get(c[0]).data_source == "candidate"
        ] or combs
        tpl_id, key, offset, cells = combs[0]
    finally:
        db.close()

    # A record is REQUIRED: without record_id the endpoint renders EMPTY combs,
    # and comparing two empty rows passes no matter what the engine does (that
    # is precisely how the source-regex version of this test went vacuous).
    # The value is 2 characters longer than the first row's capacity plus the
    # offset, so the continuation row is guaranteed to have something to show.
    long_value = "".join(
        chr(ord("A") + (i % 26)) for i in range(offset + max(cells, 1) - 2)
    )
    record_id = _seed_long_value_candidate(key, long_value)

    r = client.get(
        f"/api/documents/templates/{tpl_id}/print"
        f"?auto=0&record_id={record_id}",
        headers=auth_headers,
    )
    assert r.status_code == 200, (r.status_code, r.text[:300])

    # Non-greedy up to the FIRST closing tag: the comb div contains only spans.
    blocks = re.findall(
        rf'<div class="ov-field ov-cells"[^>]*?data-field-key="{key}"'
        rf'[^>]*?>(.*?)</div>',
        r.text, re.S,
    )
    assert len(blocks) >= 2, (
        f"the print HTML rendered {len(blocks)} comb rows for {key!r}, but the "
        f"template defines a primary row and a continuation row "
        f"(char_offset={offset}). A missing row means a long value prints only "
        f"its first {cells} characters."
    )

    def text_of(block: str) -> str:
        return "".join(re.findall(r'>([^<>])</span>', block))

    rendered = [text_of(b) for b in blocks]
    nonempty = [t for t in rendered if t.strip()]
    assert len(nonempty) >= 2, (
        f"record {record_id} has a {key!r} long enough for two printed rows, "
        f"but the print HTML filled only {len(nonempty)} of them "
        f"({rendered!r}). Everything past character {offset} is not printing."
    )
    assert nonempty[0] != nonempty[1], (
        f"both printed rows of the {key!r} comb rendered the SAME text "
        f"({nonempty[0]!r}). char_offset is not being applied by the HTML "
        f"print path, so the continuation row reprints the start of the value "
        f"and everything past character {offset} never prints at all."
    )


# --- [R] the shipped snapshot must carry the measured comb meta -----------

def test_the_shipped_snapshot_keeps_the_measured_comb_meta():
    """BEHAVIOURAL replacement for "the code supports format:'text'".

    Supporting a feature and SHIPPING it are different things. Every new
    deployment is seeded from app/data/template_geometry.json, so a snapshot
    exported from a DB that lost this meta bakes the defect into every future
    install -- with the engine code still perfectly correct.

    Mutation [R] set format:"text" -> "" in the snapshot; nothing failed. These
    are the empirically measured facts of OEP form OEPE-RF-02:
      * Name (field 6) and Address (field 12) are TWO stacked rows of cells,
        so each needs a continuation row with char_offset;
      * both hold human text with word breaks, so a space must occupy a box
        (format 'text'); without it every letter after a space shifts one box
        left for the rest of the row.
    """
    snap = json.loads(
        (REPO_ROOT / "app/data/template_geometry.json").read_text("utf-8")
    )
    templates = snap.get("templates", snap)
    if isinstance(templates, dict):
        oep = templates.get("OEP Form")
    else:
        oep = next((t for t in templates if t.get("name") == "OEP Form"), None)
    assert oep, "the shipped snapshot has no OEP Form"

    for key in ("full_name", "address"):
        rows = [
            f for f in oep["fields"]
            if f["field_key"] == key and int(f.get("page", 1)) == 1
            and f.get("field_type") == "char_cells"
        ]
        assert len(rows) == 2, (
            f"the shipped snapshot has {len(rows)} page-1 {key!r} comb rows; "
            f"the printed form has TWO rows of cells for it, so a fresh "
            f"install would print only the first "
            f"{rows[0]['meta'].get('cell_count') if rows else '?'} characters "
            f"of every value"
        )
        offsets = sorted(int((f.get("meta") or {}).get("char_offset") or 0)
                         for f in rows)
        assert offsets[0] == 0 and offsets[1] > 0, (
            f"the two {key!r} comb rows have char_offset {offsets}; without an "
            f"offset the second row can only reprint the start of the value"
        )
        for f in rows:
            fmt = str((f.get("meta") or {}).get("format") or "").lower()
            assert fmt == "text", (
                f"the {key!r} comb row at y={f['y']} ships format={fmt!r}. "
                f"{key} holds human text with word breaks: without "
                f"format='text' the space is stripped and every character "
                f"after it is drawn one box left of its printed cell for the "
                f"rest of the row."
            )
        # The continuation row must start where the first row runs out --
        # an offset smaller than the first row's capacity reprints characters,
        # a larger one drops them.
        primary = next(f for f in rows
                       if not int((f.get("meta") or {}).get("char_offset") or 0))
        cont = next(f for f in rows
                    if int((f.get("meta") or {}).get("char_offset") or 0))
        assert int(cont["meta"]["char_offset"]) == int(
            primary["meta"]["cell_count"]
        ), (
            f"the {key!r} continuation row starts at character "
            f"{cont['meta']['char_offset']} but the first row holds "
            f"{primary['meta']['cell_count']} cells -- the rows either "
            f"duplicate or skip characters at the seam"
        )


# ==========================================================================
# 6. The pitch clamp must apply in the PDF engine too (audit 2026-08-14)
# ==========================================================================
#
# `_fitted_cell_pitch` has clamped an impossible `cell_width` to the field box
# since 2026-08-11 -- but ONLY in the HTML print path. The PDF engine read
# `meta["cell_width"]` raw and handed it straight to `_draw_char_cells`, so for
# the real corruption found in this database (OEP Form `phone`: 13 cells x
# 31.47pt = 409.11pt of cells inside a 99.60pt box) the two renderers of ONE
# template disagreed by 4.1x. HTML kept every digit inside the box; the PDF
# sprayed them 309pt past its right edge and off the page.
#
# The whole suite was green before and after that fix, which is the point of
# the tests below: nothing measured the PDF's cell pitch at all.


def _draw_comb_and_collect_x(meta: dict, box_width: float, value: str,
                             field_key: str = "phone") -> list[float]:
    """Render ONE char_cells field through the real PDF path.

    Returns the x-coordinate of every glyph actually drawn, in draw order.

    Asserting on the drawn coordinates rather than on the pitch variable is
    deliberate: a test that re-computed the expected pitch itself would pass
    even if the engine never applied it (the lesson of mutation BN, recorded in
    PROJECT_DOCUMENTATION.md section 4.10.3).
    """
    import io
    import types

    from reportlab.pdfgen import canvas as _canvas

    from app.services import pdf_engine

    xs: list[float] = []
    c = _canvas.Canvas(io.BytesIO())
    real = c.drawCentredString

    def _spy(x, y, text, *a, **kw):
        xs.append(float(x))
        return real(x, y, text, *a, **kw)

    c.drawCentredString = _spy

    # A minimal stand-in for the ORM row. `template` is None so the capacity
    # policy falls back to this row's own cell_count -- this test is about the
    # PITCH, and pulling in a real template would couple it to seed data.
    field = types.SimpleNamespace(
        field_type="char_cells", field_key=field_key, page=1,
        x=0.0, y=100.0, width=box_width, height=14.0,
        font_size=9.0, font_bold=False, font_italic=False, color="#000000",
        align="left", static_value=None, meta=dict(meta), template=None,
        line_height=None, font_family=None, rotation=0,
    )
    pdf_engine._render_field(c, field, value, None, None, 595.0, 842.0)
    return xs


def test_the_pdf_engine_clamps_an_impossible_cell_pitch_to_the_field_box():
    """The exact corruption found in this database, measured in the PDF.

    OEP Form `phone`: 13 cells x 31.47pt inside a 99.60pt box. Obeying the
    stored pitch puts the last digit ~390pt right of a box that is 99.6pt wide.
    """
    box = 99.6
    meta = {"cell_count": 13, "cell_width": 31.47, "cell_gap": 0, "format": "digits"}
    xs = _draw_comb_and_collect_x(meta, box, "0323740642")
    assert xs, "no glyphs were drawn at all -- the fixture no longer renders"

    assert max(xs) <= box + 0.01, (
        f"the PDF drew a character at x={max(xs):.2f}pt in a {box:.2f}pt field "
        f"box -- the stored cell_width was obeyed instead of clamped, so the "
        f"value prints across whatever sits beside it. Glyph xs: "
        f"{[round(v, 2) for v in xs]}"
    )
    assert min(xs) >= -0.01, (
        f"a character was drawn at a negative x ({min(xs):.2f}pt), i.e. off the "
        f"left edge of the page"
    )


def test_the_pdf_and_html_paths_agree_on_the_clamped_pitch():
    """One implementation, so the two renderers cannot drift apart again."""
    from app.api.endpoints.documents import _fitted_cell_pitch, _grid_slot_span
    from app.services.pdf_engine import fitted_cell_pitch, grid_slot_span

    cases = [
        (99.6, 13, 0.0, 31.47, None),        # the real corruption -> clamps
        (220.8, 13, 0.0, 14.72, [5, 7, 1]),  # grouped CNIC, fits exactly
        (351.0, 15, 0.0, 23.4, None),        # consistent -> untouched
        (100.0, 10, 0.0, 0.0, None),         # no stored pitch -> derive
        (0.0, 13, 0.0, 31.47, None),         # no box width -> cannot judge
        (100.0, 13, 1.5, 31.47, None),       # with a gap
    ]
    for width, count, gap, stored, groups in cases:
        engine = fitted_cell_pitch(width, count, gap, stored, groups)
        html = _fitted_cell_pitch(width, count, gap, stored, groups)
        assert engine == html, (
            f"the PDF engine and the HTML print path disagree on the cell pitch "
            f"for box={width} cells={count} gap={gap} stored={stored} "
            f"groups={groups}: engine={engine} html={html}. One template must "
            f"not render two different geometries."
        )
        assert grid_slot_span(count, groups) == _grid_slot_span(count, groups), (
            f"the two paths measure the grid width differently for count={count} "
            f"groups={groups}"
        )


def test_a_grouped_grid_that_fits_is_not_clamped_in_the_pdf():
    """A correctly-configured grouped grid must keep the designer's spacing.

    Allied Bank `cnic` is the one shipped template with group_sizes=[5,7,1]:
    15 slots x 14.72pt = 220.80pt in a 220.8pt box. Measuring it by its 13
    CHARACTERS instead of its 15 SLOTS says 191.36pt, which looks like slack
    and would refit the field by dividing the box by 13 -- pushing every digit
    after the first dash out of its printed cell.
    """
    box = 220.8
    meta = {"cell_count": 13, "cell_width": 14.72, "cell_gap": 0,
            "group_sizes": [5, 7, 1], "format": "digits"}
    xs = _draw_comb_and_collect_x(meta, box, "3460117289185", field_key="cnic")
    assert len(xs) == 13, f"expected 13 digits drawn, got {len(xs)}"

    # Deliberately NOT strict=True: pairing a list with its own tail always
    # yields one fewer element, so strict would raise by construction.
    deltas = [round(xs[i + 1] - xs[i], 2) for i in range(len(xs) - 1)]
    within = [d for d in deltas if d < 20]
    assert within, f"could not recover a within-group pitch from {deltas}"
    assert all(abs(d - 14.72) < 0.05 for d in within), (
        f"the designer's 14.72pt pitch was not honoured for a grid that fits: "
        f"within-group deltas {within}. If these are ~14.15pt the grid was "
        f"measured by its 13 characters instead of its 15 slots and refitted."
    )
    between = [d for d in deltas if d >= 20]
    assert len(between) == 2, (
        f"a 5-7-1 grid must skip a slot at each of its TWO dashes; found "
        f"{len(between)} wide gaps in {deltas}"
    )
    assert max(xs) <= box + 0.01, (
        f"a grid that fits its box drew a glyph at x={max(xs):.2f} in a "
        f"{box:.2f}pt box"
    )
