#!/usr/bin/env python3
"""Re-home OEP Form page-1 fields onto their real, measured, labelled rows.

WHY THIS SCRIPT EXISTS
----------------------
The OEP Form page-1 layout shipped with coordinates that were never measured
against the scanned artwork. ``scripts/seed_document_fields.py`` places the
whole page on a lazy 30 pt ladder starting at y=735 with a constant x=150, and
its own docstring calls the numbers "best-effort approximations".

An earlier repair in this project corrected three of those rows
(``full_name``, ``father_name``, ``phone``) by checking that each baseline fell
inside *a* printed box. That check was too weak: a baseline can sit neatly
inside a box that belongs to a *different question*. Rendering the real PDF
through the live API and mapping every baseline against the measured rules
showed 11 of 13 page-1 fields printing on the wrong labelled row, e.g.

    field                 baseline  landed in
    cnic                    684.00  row 2  "Name of OEP/Agency"
    date_of_birth           684.00  row 2  "Name of OEP/Agency"
    passport_no             654.00  row 4  "Permission No."
    profession              624.00  row 4  "Permission No."
    address                 594.00  row 6  "Name" (line 1)
    email                   564.00  row 7  "Father's / Husband's Name"
    next_of_kin_name        534.00  row 8  "Emigrant CNIC No."
    full_name               703.00  *** on top of a rule, no row at all ***
    father_name             549.20  *** on top of a rule, no row at all ***

So the emigrant's CNIC was printing on the agency-name line, the address was
printing on the name line, and the e-mail was printing on the father's-name
line. Every one of those is a mandatory field on a government form.

WHAT THE FORM ACTUALLY ASKS FOR (page 1)
----------------------------------------
Rows 2/3/4 are OEP *agency* details (office use) -- the portal has no data for
them, and writing candidate data there is what produced the corruption above.
Rows 6..22 are the emigrant's details. Fields that page 1 has no row for
(``date_of_birth``, ``passport_expiry_date``, ``nationality``, ``salary``,
``country``, ``sponsor_name``) are left on page 2, which already carries them.

MEASUREMENT METHOD
------------------
The artwork ``oep_form_p1.jpg`` is rasterised at 300 dpi into a 595x842 pt page
so image rows map linearly to PDF points. Horizontal rules are found by
dark-pixel row projection restricted to the value column; vertical cell rules
by column projection restricted to each row band. Baselines are placed
PAD=3.4 pt above each row's own bottom rule, matching the clearance measured on
the Allied Bank form family (3.22 / 3.36 / 3.48).

Because the renderer flows text from the top of the box
(``baseline = y + max(h - font_size, 0)``), this script stores
``height = font_size`` so that ``y`` *is* the baseline. That keeps the stored
number identical to the measured one and removes a whole class of arithmetic
mistakes.

Rows 6 (Name), 8 (Emigrant CNIC), 10 (Cell No.) and 12 (Address) are printed as
character-cell grids, so those fields become ``char_cells`` with the measured
pitch and ``draw_boxes: False`` (the scan already prints the cells -- drawing
our own would double-rule them, a defect verified by rendering both ways
earlier in this project).

Idempotent: safe to re-run. Prints a before/after table and exits non-zero if
nothing matched, so a silent no-op cannot masquerade as success.
"""
from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

FONT = 11.0

# --------------------------------------------------------------------------
# Measured page-1 geometry. Every number below was read off the 300 dpi raster
# of app/static/pdf_backgrounds/oep_form_p1.jpg -- none is a guess.
#
# key -> (x, baseline_y, width, field_type, meta)
# ``y`` is the text baseline (height is pinned to FONT, see docstring).
# --------------------------------------------------------------------------
CELL_PITCH_ROW6 = 29.36    # row 6  Name            : 14 cells, 155.6 .. 566.7
CELL_PITCH_ROW8 = 23.40    # row 8  Emigrant CNIC    : 15 cells, 155.6 .. 506.6
CELL_PITCH_ROW10 = 31.47   # row 10 Cell No.         : 13 cells, 155.6 .. 564.7
CELL_PITCH_ROW12 = 31.47   # row 12 Address          : 13 cells, 155.6 .. 564.7
CELL_PITCH_ROW17 = 31.45   # row 17 Passport No.     : 13 cells, 155.5 .. 564.4

PAGE1_GEOMETRY: dict[str, tuple] = {
    # row 6 "Name :" -- character grid, 14 cells
    "full_name": (
        155.6, 595.73, 411.1, "char_cells",
        {"font_name": "DejaVu Sans", "cell_count": 14,
         "cell_width": CELL_PITCH_ROW6, "cell_gap": 0,
         "format": "", "draw_boxes": False},
    ),
    # row 7 "Father's / Husband's Name:" -- plain ruled line
    "father_name": (
        159.0, 553.74, 403.0, "text", None,
    ),
    # row 8 "Emigrant CNIC No:" -- character grid, 15 cells
    "cnic": (
        155.6, 524.94, 351.0, "char_cells",
        {"font_name": "DejaVu Sans", "cell_count": 15,
         "cell_width": CELL_PITCH_ROW8, "cell_gap": 0,
         "format": "", "draw_boxes": False},
    ),
    # row 10 "Cell No:" -- character grid, 13 cells
    "phone": (
        155.6, 473.11, 409.1, "char_cells",
        {"font_name": "DejaVu Sans", "cell_count": 13,
         "cell_width": CELL_PITCH_ROW10, "cell_gap": 0,
         "format": "", "draw_boxes": False},
    ),
    # row 11 "E-mail:" -- plain ruled line
    "email": (
        159.0, 450.20, 401.0, "text", None,
    ),
    # row 12 "Address: (In Pakistan)" -- character grid, 13 cells
    "address": (
        155.6, 419.96, 409.1, "char_cells",
        {"font_name": "DejaVu Sans", "cell_count": 13,
         "cell_width": CELL_PITCH_ROW12, "cell_gap": 0,
         "format": "", "draw_boxes": False},
    ),
    # ---- lower half -------------------------------------------------------
    # NOTE: THREE passes were needed for the four fields below, and the only
    # thing that ever settled them was RENDERING the page and reading the ink.
    #
    # Pass 1 ran the rule detector at a 0.55 dark-fraction threshold over the
    # full page width. That MISSED three shorter/lighter rules (374.93, 269.83,
    # 253.75), so every band boundary below y=397 was off by one row and
    # "MATRIC" printed on the Passport line. Re-measured at threshold 0.40 over
    # x 300..560 (inside the value column, clear of the left labels).
    #
    # Pass 2 matched each label to the band containing the LABEL's vertical
    # centre. On this artwork the label text sits level with the TOP of its own
    # value box, so that rule picks the band one row too high -- and the
    # "correction" of shifting everything one band down then over-shot, putting
    # "MATRIC" on Passport No. and the passport number on Name of Nominee.
    #
    # Pass 3 (this one) stopped inferring bands from label positions entirely.
    # scripts/../ /tmp overlay drew every measured rule ON TOP of the artwork so
    # the label/band pairing could be read directly. The bands below are those
    # read values; the baseline is the band's lower rule + PAD (3.4 pt, the
    # offset measured from the printed specimen on the Allied Bank form).
    #
    #   row 16 "Qualification:"    band 313.50 .. 328.86  -> baseline 316.90
    #   row 17 "Passport No:"      band 274.39 .. 294.06  -> baseline 277.79
    #   row 20 "Name of Nominee:"  band 209.60 .. 225.56  -> baseline 213.00
    #   row 22 "Relationship"      band 163.29 .. 178.89  -> baseline 166.69
    #
    # (The 143.13..163.29 band belongs to the "*Funds and Fee Details:" heading
    # that follows question 22, not to question 22's own value box -- putting
    # "FATHER" there printed it below the ruled box, level with that heading.)
    #
    # The lesson worth keeping: a baseline "inside a box" proves nothing about
    # WHICH question that box belongs to. Only the render proves that.
    # row 16 "Qualification:" -- plain ruled line
    "qualification": (
        159.0, 316.90, 401.0, "text", None,
    ),
    # row 17 "Passport No:" -- character grid, 13 cells (155.5 .. 564.4).
    # This row is a printed cell grid on the artwork, not a ruled line, so a
    # plain text run would straddle the cell rules instead of sitting in them.
    "passport_no": (
        155.5, 277.79, 409.0, "char_cells",
        {"font_name": "DejaVu Sans", "cell_count": 13,
         "cell_width": CELL_PITCH_ROW17, "cell_gap": 0,
         "format": "", "draw_boxes": False},
    ),
    # row 20 "Name of Nominee :" -- plain ruled line
    "next_of_kin_name": (
        159.0, 213.00, 401.0, "text", None,
    ),
    # row 22 "Relationship" -- plain ruled line
    "next_of_kin_relation": (
        159.0, 166.69, 401.0, "text", None,
    ),
    # row 1 "Date:" -- the printed date cells are bounded below by rule 731.98.
    # Previously this sat at y=110, i.e. inside the "Funds and Fee Details"
    # table at the very bottom of the page, overprinting the NADRA/NICOP fee
    # row. Row 1 is the form's own date question, which is where it belongs.
    "__today__": (
        255.0, 719.54, 135.0, "text", None,
    ),
}

#: Page-1 fields with NO corresponding question on page 1. Leaving them where
#: they were means they keep printing on an unrelated row, so they are removed
#: from page 1; page 2 already carries the ones the form asks for there.
PAGE1_ORPHANS = ("date_of_birth", "passport_expiry_date", "nationality", "profession")


def main() -> int:
    from app.db.session import SessionLocal
    from app.models.document import DocumentField, DocumentTemplate

    db = SessionLocal()
    changed = 0
    removed = 0
    try:
        tpl = (
            db.query(DocumentTemplate)
            .filter(DocumentTemplate.name == "OEP Form")
            .first()
        )
        if tpl is None:
            print("ERROR: OEP Form template not found", file=sys.stderr)
            return 2

        page1 = [f for f in tpl.fields if (f.page or 1) == 1]
        by_key: dict[str, DocumentField] = {}
        for f in page1:
            by_key.setdefault(f.field_key, f)

        print(f"{'field':<24}{'old x':>8}{'old y':>8} -> {'new x':>8}{'new y':>8}  type")
        for key, (x, y, w, ftype, meta) in PAGE1_GEOMETRY.items():
            f = by_key.get(key)
            if f is None:
                # The field does not exist on page 1 yet -- create it, otherwise
                # a mandatory row would simply stay blank.
                f = DocumentField(
                    template_id=tpl.id, field_key=key, page=1,
                    label=key.replace("_", " ").title(), font_size=FONT,
                )
                db.add(f)
                print(f"{key:<24}{'--':>8}{'--':>8} -> {x:>8.1f}{y:>8.2f}  {ftype} (created)")
            else:
                print(f"{key:<24}{f.x:>8.1f}{f.y:>8.1f} -> {x:>8.1f}{y:>8.2f}  {ftype}")
            f.x, f.y, f.width = x, y, w
            f.height = FONT          # so that y IS the baseline
            f.field_type = ftype
            f.font_size = FONT
            if meta is not None:
                f.meta = meta
            changed += 1

        for key in PAGE1_ORPHANS:
            f = by_key.get(key)
            if f is not None:
                print(f"{key:<24}{f.x:>8.1f}{f.y:>8.1f} -> REMOVED (no page-1 row exists)")
                db.delete(f)
                removed += 1

        db.commit()
    finally:
        db.close()

    print(f"\nupdated/created={changed} removed={removed}")
    if changed == 0:
        print("ERROR: nothing was changed -- refusing to report success",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
