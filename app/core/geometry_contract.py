"""The single source of truth for document coordinate geometry.

WHY THIS MODULE EXISTS (2026-08-18)
===================================
Three independent engines have to agree on where a field is printed:

  1. the React Template Designer canvas   -- app/frontend/src/pages/designer/engine.ts
  2. the HTML print view                  -- app/api/endpoints/documents.py
  3. the ReportLab PDF renderer           -- app/services/pdf_engine.py

They agreed only by CODE COMMENT. Each engine carried its own copy of the
shared numbers, with a comment saying "MUST match the other two". Nothing
executed that claim, so any edit to one copy silently moved printed values off
their boxes on real embassy and bank paperwork -- the exact failure mode that
is hardest to notice, because the document still renders and merely looks
slightly wrong.

TWO REAL MISMATCHES WERE MEASURED BEFORE THIS MODULE EXISTED
------------------------------------------------------------
1. The ascent ratio was duplicated as ``ASCENT_RATIO = 0.8`` in engine.ts and
   ``ASCENT = 0.800`` in documents.py, bound by nothing but prose. No test in
   the suite referenced either name (verified by grep over tests/), so a
   divergence would have shipped undetected.

2. Page-size presets genuinely disagreed. engine.ts's PAGE_SIZES table claimed
   in its own comment to "match reportlab.lib.pagesizes", but stored rounded
   integers, while pdf_engine.py's NULL fallback used reportlab's exact
   floats:

       page          designer      reportlab        delta
       A4 height     842           841.8898        +0.1102 pt
       A3 height     1191          1190.5512       +0.4488 pt
       A5 height     595           595.2756        -0.2756 pt

   Because PDF y is measured from the BOTTOM edge, a page-height disagreement
   displaces every field on the page relative to the printed artwork. The
   deltas are sub-point, which is precisely why this survived: too small to
   spot by eye on one form, systematic across every form.

   Worse, the two "A4"s lived in the same request path: the DocumentTemplate
   columns default to 595 x 842 (rounded) while pdf_engine.py fell back to
   A4 = 595.2756 x 841.8898 when the column was NULL. Same template, two
   different pages, depending only on whether a column had been written.

THE CONTRACT
------------
This module is the authority. The Python engines import from here. The
TypeScript engine cannot import Python, so tests/test_geometry_contract.py
parses engine.ts and asserts every shared constant matches these values
exactly -- a real cross-language gate that fails on divergence instead of a
comment asking nicely.

DELIBERATE DECISION -- ROUNDED PRESETS ARE CANONICAL
----------------------------------------------------
The presets below are the ROUNDED values (A4 = 595 x 842), not reportlab's
exact floats, and the Python side is aligned to them rather than the reverse.

That direction is chosen on purpose, and it is the safe one:

  * Every geometry coordinate already persisted in template_geometry.json and
    in the DocumentTemplate/DocumentField rows was authored on the designer
    canvas, i.e. against the ROUNDED page. Those coordinates are the ground
    truth for documents that are already in production use.
  * Changing the canonical page height to reportlab's float would move every
    one of those existing fields by the delta above. Aligning the renderer to
    the designer instead leaves all authored geometry exactly where the
    operator placed it.
  * The physical page is unchanged either way: 842 pt vs 841.8898 pt is
    0.0015 inch of paper, far below any printer's registration tolerance. The
    bug was never the absolute size, it was the DISAGREEMENT.

So: never "fix" this by swapping in reportlab's floats. That would silently
re-introduce the drift in the opposite direction, against live paperwork.
"""
from __future__ import annotations

from typing import Dict, Tuple

# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------
# Arial/Helvetica cap+ascent ratio. Used to convert a text BASELINE (which is
# what ReportLab's drawString takes) into the TOP of a text box (which is what
# CSS and the designer canvas position). Mirrored by ASCENT_RATIO in engine.ts.
ASCENT_RATIO: float = 0.8

# 1 PDF point == 1 CSS pixel at zoom 1.0. Mirrored by PT_TO_CSS in engine.ts.
PT_TO_CSS: float = 1.0

# Persisted coordinate precision, in decimal places. The designer rounds to
# 0.1 pt on every edit (round1 in engine.ts); the geometry snapshot exporter
# uses 2 places so that a rounding change is visible rather than absorbed.
DESIGNER_ROUND_DP: int = 1

# ---------------------------------------------------------------------------
# Box height fallback
# ---------------------------------------------------------------------------
# A field with NULL/0 height still has to produce a baseline. All three engines
# must use the SAME fallback or the baseline drifts by (h - font_size).
# Mirrored by effectiveHeight() in engine.ts and the fallback in pdf_engine.py.
HEIGHT_FALLBACK_FONT_MULTIPLE: float = 1.25
HEIGHT_FALLBACK_MINIMUM: float = 14.0

# Barcode fields have a fixed fallback height instead of a font-derived one.
BARCODE_HEIGHT_FALLBACK: float = 32.0


def effective_height(height: float | None, font_size: float) -> float:
    """The box height used for baseline math when ``height`` is unset.

    Mirrors ``effectiveHeight`` in engine.ts exactly. Kept as a function, not
    an inline expression, so the three engines share one implementation rather
    than three copies of the same arithmetic.
    """
    h = float(height or 0)
    if h > 0:
        return h
    return max(float(font_size) * HEIGHT_FALLBACK_FONT_MULTIPLE, HEIGHT_FALLBACK_MINIMUM)


def baseline_pdf_y(y: float, height: float, font_size: float) -> float:
    """PDF baseline for a field box, in points from the page BOTTOM.

    The designer draws text flowing from the TOP of the marker box, whereas
    ReportLab places the baseline at the y it is given. Drawing the baseline
    near the box top is what makes designer == print == PDF.
    """
    return float(y) + max(float(height) - float(font_size), 0.0)


def css_top_from_pdf_y(y: float, height: float, page_height: float) -> float:
    """Convert a PDF y (bottom origin) to a CSS top (top origin), in px."""
    return (float(page_height) - float(y) - float(height)) * PT_TO_CSS


# ---------------------------------------------------------------------------
# Page-size presets, in PDF points (portrait). Landscape swaps w/h.
# ---------------------------------------------------------------------------
# CANONICAL. Mirrored by PAGE_SIZES in engine.ts and asserted equal by
# tests/test_geometry_contract.py. See the module docstring for why these are
# the rounded values and must not be "corrected" to reportlab's floats.
PAGE_SIZES: Dict[str, Tuple[int, int]] = {
    "a4":     (595, 842),
    "letter": (612, 792),
    "legal":  (612, 1008),
    "a3":     (842, 1191),
    "a5":     (420, 595),
    "custom": (595, 842),
}

# The page used when a template carries no explicit size. MUST equal the
# DocumentTemplate column defaults (page_width=595, page_height=842),
# otherwise a template renders on a different page depending purely on whether
# those columns happen to have been written -- which is the second mismatch
# described in the module docstring.
DEFAULT_PAGE_WIDTH: int = PAGE_SIZES["a4"][0]
DEFAULT_PAGE_HEIGHT: int = PAGE_SIZES["a4"][1]


def resolve_page_size(width: float | None, height: float | None) -> Tuple[float, float]:
    """The page box for a template, falling back to the canonical A4.

    This replaces ``float(template.page_width or A4[0])`` in pdf_engine.py.
    The old form mixed reportlab's exact A4 into a codebase whose authored
    coordinates are all relative to the rounded A4, so a NULL column produced
    a page 0.1102 pt shorter than the one the operator designed against.
    """
    w = float(width or 0) or float(DEFAULT_PAGE_WIDTH)
    h = float(height or 0) or float(DEFAULT_PAGE_HEIGHT)
    return w, h
